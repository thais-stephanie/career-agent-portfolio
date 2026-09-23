"""The local HTTP server. Loopback only, single user, no authentication.

`ThreadingHTTPServer` from the standard library, an explicit route table, and
static files served from `web/static`. Roughly the smallest thing that can put
a real interface in front of the corpus.

Three decisions are enforced here rather than documented and hoped for:

**It binds 127.0.0.1.** Not `0.0.0.0`, not the hostname, not a configurable
interface. `bind_host` exists as a parameter only so a test can prove the
refusal; passing anything that is not loopback raises. There is no
authentication because there is no network exposure, and the second half of
that sentence has to stay true for the first half to be acceptable.

**One SQLite connection per request.** SQLite connections are not shareable
across threads, and `ThreadingHTTPServer` gives every request its own. Opening
per request is the boring correct answer; WAL mode (set by `storage.db.connect`)
makes concurrent readers free.

**Nothing on a GET path can reach a model.** Enrichment is a POST, triggered by
a button, and it is the only route in the table that may open a socket, to
localhost. A page load can never cause inference.
"""

from __future__ import annotations

import errno
import ipaddress
import json
import mimetypes
import re
import socket
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

STATIC_ROOT = Path(__file__).parent / "static"

#: Types the platform's own table does not carry, registered rather than hoped
#: for.
#:
#: `mimetypes.guess_type` answers None for `.woff2` on this machine, and this
#: server sends `X-Content-Type-Options: nosniff` -- correctly, since it also
#: serves employer text. Together those two facts mean a self-hosted font would
#: be fetched, refused for its type, and silently replaced by the fallback
#: stack, with the page looking almost right and nothing saying why.
#:
#: Registering here rather than relying on the OS keeps the answer the same on
#: a machine whose registry says something else.
for _extension, _mime in (
    (".woff2", "font/woff2"),
    (".woff", "font/woff"),
    (".ttf", "font/ttf"),
    (".otf", "font/otf"),
):
    mimetypes.add_type(_mime, _extension)

#: Route patterns are matched in order. Kept as an explicit list because a
#: framework's decorator magic would hide the one thing worth seeing at a
#: glance: exactly which paths exist and which of them mutate.
_JOB_ID = r"(?P<job_id>[A-Za-z0-9_-]{1,64})"


class ApiError(Exception):
    """An error with an HTTP status and a message for the interface.

    ``for_reader`` says WHO THE MESSAGE IS ADDRESSED TO, and it defaults to
    False on the same principle every other check in this file follows:
    absence is never permission. Most messages raised here are written for
    whoever is reading the code -- ``applied_at must be YYYY-MM-DD``,
    ``changes must be an object with at least one field``, ``method not
    allowed``. They are precise, they are useful in a terminal, and a person
    who came to look at job postings should never see one: a message shaped
    like a contract violation tells her something is broken and gives her
    nothing to do about it.

    So the browser prints the server's own sentence only when this flag says
    the sentence was written FOR HER -- "Where you live is a two-letter
    country code, such as BR", "a rescore is already running" -- and prints
    its own plain-language sentence for everything else, with the server's
    words kept in the payload for the console. A message added later and not
    marked degrades to the friendly copy, which is the safe direction: the
    reader loses some precision and never sees a stack-trace fragment.
    """

    def __init__(self, status: int, message: str, *, for_reader: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.for_reader = for_reader


@dataclass(frozen=True)
class ServerConfig:
    db_path: Path
    config_dir: Path
    host: str = "127.0.0.1"
    port: int = 8765


def assert_loopback(host: str) -> None:
    """Refuse to bind anywhere a second machine could reach.

    The server has no authentication. Binding it to a routable interface would
    publish one person's job search, their notes and their profile-derived
    preferences to the local network. That is not a configuration choice, so it
    is not configurable.
    """
    if host == "localhost":
        return
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValueError(
            f"refusing to bind to {host!r}: the local server binds loopback only"
        ) from exc
    if not address.is_loopback:
        raise ValueError(f"refusing to bind to {host!r}: the local server binds loopback only")


def safe_static_path(url_path: str) -> Path | None:
    """Resolve a URL path inside `static/`, or return None.

    `Path.resolve()` then a containment check, rather than string filtering:
    `..%2f` and symlinks both defeat the string approach and neither defeats
    this one.
    """
    relative = unquote(url_path).lstrip("/")
    if not relative:
        relative = "index.html"
    try:
        candidate = (STATIC_ROOT / relative).resolve()
        root = STATIC_ROOT.resolve()
        if candidate == root or root in candidate.parents:
            return candidate if candidate.is_file() else None
    except (ValueError, OSError):
        # An embedded NUL, a name the platform rejects, a path too long. All
        # of them mean "no such file", and none of them should reach the
        # handler as a 500 that prints a stack trace at the user.
        return None
    return None


#: What an ordinary request body may weigh. A filter, a status change and an
#: imported posting are all small, and a megabyte of JSON on a personal tool is
#: already a mistake somewhere.
MAX_BODY_BYTES = 2_000_000

#: What an UPLOAD may weigh. `cv.extract.MAX_BYTES` is 25 MB and base64 costs a
#: third more, so a CV at the extractor's limit needs this much to arrive at
#: all. Refusing it here instead would mean the person is told "too large" for
#: a file the reader would have accepted.
#:
#: This is deliberately a per-ROUTE limit rather than a raised global one. The
#: routes that take a body from a page are the ones a hostile page would aim
#: at, and there is no reason for `/api/import` to accept 34 MB because CV
#: intake does.
MAX_UPLOAD_BYTES = 34 * 1024 * 1024

#: The routes allowed the upload limit. An exact set, not a prefix: a prefix
#: rule grows quietly, and the next route under `/api/cv/` should have to say
#: out loud that it takes a file.
#:
#: `/api/intake` is the second, and it says so here. It takes SEVERAL documents
#: in one body -- a CV and a LinkedIn export together -- which is exactly the
#: kind of route a prefix rule would have admitted without anybody deciding.
UPLOAD_PATHS = frozenset({"/api/cv/import", "/api/intake"})


#: A Content-Length this server will frame a body by. ASCII only: `str.isdigit`
#: also accepts characters such as "²" that `int` then refuses.
_DECIMAL = re.compile(r"[0-9]+")


def body_limit(path: str) -> int:
    """How large a body this path may carry."""
    return MAX_UPLOAD_BYTES if path in UPLOAD_PATHS else MAX_BODY_BYTES


class _Handler(BaseHTTPRequestHandler):
    server_version = "CareerAgentLocal/1.0"
    #: Suppress the default reverse DNS lookup on every request. It is a
    #: network call, it is slow on Windows, and this server only ever talks to
    #: 127.0.0.1.
    protocol_version = "HTTP/1.1"

    app: Any  # set on the server instance; see `build_server`

    # -- logging ---------------------------------------------------------
    def log_message(self, fmt: str, *args: Any) -> None:
        # One quiet line per request. The default writes to stderr with a
        # timestamp format that is hard to read in a PowerShell window.
        self.app.log(
            f"{self.command} {self.path.split('?')[0]} -> {args[1] if len(args) > 1 else ''}"
        )

    def address_string(self) -> str:
        return self.client_address[0]

    # -- framing -----------------------------------------------------------
    # Keep-alive means the bytes after a request are read as the NEXT request.
    # A refusal sent before the body was read -- a 403 from `_check_origin`, a
    # 405, a 413 -- used to leave that body on the socket, and a body that was
    # itself a well-formed request, with our own Host and a JSON content type,
    # was then served as if nobody had refused anything. A cross-site page can
    # send such a body as text/plain without a preflight. So: a body this
    # server did not read ends the connection, and a body it cannot frame
    # (chunked, two lengths, a length that is not a number) is refused whole.
    _body_consumed = False
    _status_code = 0

    def send_response_only(self, code: int, message: str | None = None) -> None:
        # Every status line passes through here, including the interim
        # `100 Continue` that `handle_expect_100` sends BEFORE the body is
        # read. That one must not close the connection it is inviting a body on.
        self._status_code = code
        super().send_response_only(code, message)

    def parse_request(self) -> bool:
        self._body_consumed = False
        if not super().parse_request():
            return False
        lengths = self.headers.get_all("Content-Length") or []
        if (
            "Transfer-Encoding" in self.headers
            or len(lengths) > 1
            or (lengths and not _DECIMAL.fullmatch(lengths[0].strip()))
        ):
            self.close_connection = True
            self._send_json(400, {"error": "unsupported request framing"})
            return False
        return True

    def _unread_body(self) -> bool:
        """True when the request declared a body that was not read off the socket."""
        headers = getattr(self, "headers", None)
        if headers is None or self._body_consumed:
            return False
        if "Transfer-Encoding" in headers:
            return True
        length = (headers.get("Content-Length") or "0").strip()
        return not _DECIMAL.fullmatch(length) or int(length) > 0

    def end_headers(self) -> None:
        if self._status_code >= 200 and self._unread_body():
            # `send_header` sets `close_connection` when it sees this header.
            self.send_header("Connection", "close")
            self.close_connection = True
        super().end_headers()

    # -- verbs -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802  (stdlib naming)
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    # -- plumbing ---------------------------------------------------------
    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/resume-tailor" and method == "GET":
                self._check_origin(method)
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.app.config.port + 1}/")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path.startswith("/api/"):
                self._check_origin(method)
                query = parse_qs(parsed.query, keep_blank_values=False)
                body = self._read_body(body_limit(path)) if method in ("POST", "PATCH") else {}
                payload = self.app.handle_api(method, path, query, body)
                self._send_json(200, payload)
                return
            if method != "GET":
                raise ApiError(405, "method not allowed")
            self._send_static(path)
        except ApiError as exc:
            # `for_reader` rides along only when it is TRUE. A key present on
            # every error payload would make the flag look like a property of
            # errors rather than the exception it is, and it would rewrite
            # every recorded response in the test suite for no gain.
            body = {"error": exc.message}
            if exc.for_reader:
                body["for_reader"] = True
            self._send_json(exc.status, body)
        except Exception as exc:  # noqa: BLE001  -- the server must not die
            # The detail goes to the operator's terminal, not to the page.
            # sqlite3 and OSError messages carry absolute filesystem paths and
            # SQL fragments, and the client has no use for either.
            self.app.log(f"unhandled: {type(exc).__name__}: {exc}")
            # NOT ADDRESSED TO ANYBODY IN PARTICULAR, and deliberately not to
            # the reader: no `for_reader`, so the page prints its own sentence
            # and this one goes to the console. It used to say "see the
            # terminal running `serve`" ON SCREEN, which asks somebody who
            # pressed a button in a browser to go and find a window she may
            # never have opened -- `career-agent start` is how this runs.
            self._send_json(500, {"error": "unhandled server fault; see the server log"})

    def _check_origin(self, method: str) -> None:
        """Refuse requests that another website made on the user's behalf.

        `serve` has no authentication because it has no network exposure. That
        argument covers a machine on the LAN. It does NOT cover the browser
        already running on this machine, which will happily send a request to
        127.0.0.1 on behalf of any page the user has open -- so a site visited
        while the app is running could POST fabricated postings into the
        corpus, or drive the local GPU through /enrich.

        Three checks, because each closes something the others do not:

        **Host** must be our own address. This is what closes DNS rebinding:
        an attacker-controlled name that resolves to 127.0.0.1 arrives with
        its own hostname in this header, and nothing else notices.

        **Origin**, when present, must be our own. Browsers attach it to every
        cross-origin state-changing request and cannot be talked out of it.

        **Content-Type** must be JSON on a body. A cross-origin form can only
        send text/plain, multipart or urlencoded without a preflight, so
        requiring JSON turns every forged POST into a preflight -- which this
        server answers with 501, because it implements no do_OPTIONS.

        An earlier version of this method read a MISSING header as permission:
        `if host and not is_own_host(host)`. An HTTP/1.0 request carrying no
        Host, no Origin and no Content-Type therefore satisfied all three
        checks by supplying none of them, and a header-free POST reached
        `/api/import` and committed a row. That is invariant 2 of this project
        -- absence is never permission -- broken inside the guard written to
        enforce a boundary. Host and, on a body, Content-Type are now REQUIRED.
        Origin stays optional-but-checked because same-origin GETs legitimately
        omit it and no browser omits it when it matters.
        """
        host = (self.headers.get("Host") or "").strip()
        if not host or not self.app.is_own_host(host):
            raise ApiError(403, "missing or unexpected Host header")

        origin = (self.headers.get("Origin") or "").strip()
        if origin and origin.lower() not in self.app.allowed_origins():
            raise ApiError(403, "cross-origin requests are refused")

        if method in ("POST", "PATCH"):
            content_type = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if content_type != "application/json":
                raise ApiError(415, "request body must be application/json")

    def _read_body(self, limit: int = MAX_BODY_BYTES) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > limit:
            raise ApiError(413, "request body too large")
        raw = self.rfile.read(length)
        self._body_consumed = True
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "request body must be JSON") from exc
        if not isinstance(parsed, dict):
            raise ApiError(400, "request body must be a JSON object")
        return parsed

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, path: str) -> None:
        resolved = safe_static_path(path)
        if resolved is None:
            self._send_json(404, {"error": "not found"})
            return
        data = resolved.read_bytes()
        ctype, _ = mimetypes.guess_type(resolved.name)
        ctype = ctype or "application/octet-stream"
        # `charset` describes an encoding, and a font has none. Appending it to
        # every type was harmless while everything served here was text; it is
        # simply wrong on a binary one.
        if ctype.startswith("text/") or ctype in ("application/javascript", "image/svg+xml"):
            ctype = f"{ctype}; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(data)

    def _send_security_headers(self) -> None:
        # Job descriptions are third-party text. Even though the frontend
        # never assigns them to innerHTML, a CSP that forbids inline script
        # and every remote origin means a mistake there cannot become an
        # exfiltration. `connect-src 'self'` in particular is what stops a
        # hypothetical injected script from phoning home.
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; form-action 'none'; frame-ancestors 'none'; "
            "base-uri 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")


Route = tuple[str, re.Pattern[str], Callable[..., Any]]


class closing:
    """`contextlib.closing` without the import, and it commits nothing.

    Every mutation in the route modules runs inside `storage.db.transaction`,
    which the repositories open themselves. This only guarantees the connection
    is released when the request ends.

    It lives here rather than beside either set of routes because both need it
    and neither may import the other: `api.py` imports `workspace_api.py`, so
    the reverse would be a cycle.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def __enter__(self) -> sqlite3.Connection:
        return self.conn

    def __exit__(self, *exc: object) -> None:
        self.conn.close()


class LocalApp:
    """Route table, database access and the JSON handlers.

    Separated from the HTTP plumbing so every route can be tested by calling
    `handle_api` directly, with no socket involved.
    """

    def __init__(self, config: ServerConfig, *, quiet: bool = False) -> None:
        assert_loopback(config.host)
        self.config = config
        self.quiet = quiet
        self._lock = threading.Lock()
        self._routes: list[Route] = []
        self._ollama_state: dict[str, Any] = {
            "configured": False,
            "reachable": False,
            "model": None,
            "checked_at": None,
        }

    # -- overridden by `api.py`, which owns the handlers ------------------
    def register(self, method: str, pattern: str, handler: Callable[..., Any]) -> None:
        self._routes.append((method, re.compile(f"^{pattern}$"), handler))

    def handle_api(self, method: str, path: str, query: dict, body: dict) -> Any:
        """Dispatch, checking the METHOD across every route that matches the path.

        This used to raise 405 on the first pattern that matched the path,
        whatever method it carried -- so a path registered for two methods only
        ever answered the first one registered. `/api/preferences` is the first
        route to need both, and `PATCH` was unreachable behind the `GET`: the
        editor could read the phrases and could not save them.

        Every path-matching route is now considered before giving up, and the
        405 is raised only when none of them accepts this method -- which is
        also what makes the 405 true when it does fire.
        """
        path_matched = False
        for route_method, pattern, handler in self._routes:
            match = pattern.match(path)
            if match is None:
                continue
            path_matched = True
            if route_method != method:
                continue
            return handler(query=query, body=body, **match.groupdict())
        if path_matched:
            raise ApiError(405, f"{method} not allowed on {path}")
        raise ApiError(404, f"no such endpoint: {path}")

    def is_own_host(self, host_header: str) -> str | bool:
        """Is this `Host` one of the names this server actually answers to?"""
        name = host_header.rsplit(":", 1)[0].strip("[]").casefold()
        port = host_header.rsplit(":", 1)[1] if ":" in host_header else str(self.config.port)
        if port != str(self.config.port):
            return False
        return name in {"localhost", "127.0.0.1", "::1", self.config.host.casefold()}

    def allowed_origins(self) -> frozenset[str]:
        """The only origins allowed to make a state-changing request."""
        port = self.config.port
        return frozenset(
            {
                f"http://127.0.0.1:{port}",
                f"http://localhost:{port}",
                f"http://[::1]:{port}",
                f"http://{self.config.host.casefold()}:{port}",
            }
        )

    def connect(self) -> sqlite3.Connection:
        from career_agent.storage.db import connect

        return connect(self.config.db_path)

    def log(self, message: str) -> None:
        if not self.quiet:
            print(f"[career-agent] {message}")


class PortInUse(OSError):
    """The address is already being served, and by something else.

    Its own type because the caller has something useful to say about it and
    nothing useful to say about `OSError` in general.
    """

    def __init__(self, host: str, port: int) -> None:
        super().__init__(f"{host}:{port} is already being served by another process")
        self.host = host
        self.port = port


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """One process owns the port, or the bind fails.

    **THE DEFECT THIS EXISTS FOR, AND IT COST A MORNING.**

    `SO_REUSEADDR` means opposite things on the two platforms this runs on.
    On POSIX it permits reusing an address left in `TIME_WAIT` by a socket that
    has already closed, which is what you want and is why
    `socketserver.TCPServer` sets `allow_reuse_address` by default. On Windows
    it permits binding an address **another process is actively serving**.

    Measured on this machine, 2026-09-08: two `ThreadingHTTPServer` instances
    bound `127.0.0.1` on the same port, the second raised nothing at all, and
    every subsequent request was answered by the FIRST one.

    So Career Agent printed a complete and entirely truthful banner -- new
    process, configuration v4, 19,469 scores -- while the browser talked to a
    server started the previous day, running older code against an older
    configuration. That is not a confusing error message; it is the product
    describing a runtime the reader is not using. It produced 404s on routes
    that exist, 500s on routes that work, and a Jobs list reporting nothing
    scored beside a header counting 19,469 scores.

    `SO_EXCLUSIVEADDRUSE` is Windows's own answer and it must be set BEFORE
    the bind. On POSIX there is no such flag and none is needed: `SO_REUSEADDR`
    there cannot steal a live socket, so the default stays.
    """

    #: Windows only, and False rather than absent: with `SO_EXCLUSIVEADDRUSE`
    #: set, leaving `SO_REUSEADDR` on as well is contradictory.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")

    def server_bind(self) -> None:
        exclusive = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if exclusive is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, exclusive, 1)
        super().server_bind()


def build_server(app: LocalApp) -> ThreadingHTTPServer:
    """Bind the socket, exclusively, before anything claims to be ready.

    Raises `ValueError` before binding if the host is not loopback, and
    `PortInUse` if something else is already serving the address. Both happen
    HERE, which is the point: a caller that has not got a server object back
    has nothing to announce.
    """
    assert_loopback(app.config.host)

    handler = type("_BoundHandler", (_Handler,), {"app": app})
    try:
        httpd = ExclusiveHTTPServer((app.config.host, app.config.port), handler)
    except OSError as exc:
        # WSAEADDRINUSE (10048) on Windows, EADDRINUSE (98/48) elsewhere.
        # Matched by errno rather than by message, which is localised.
        if exc.errno in (errno.EADDRINUSE, getattr(errno, "WSAEADDRINUSE", errno.EADDRINUSE)):
            raise PortInUse(app.config.host, app.config.port) from exc
        raise
    httpd.daemon_threads = True
    return httpd


#: `job_id` is a ULID from our own database, but it arrives as a URL segment,
#: so it is validated against this before it reaches SQL. Every query uses
#: bound parameters as well; this is the belt to that pair of braces.
JOB_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_job_id(job_id: str) -> str:
    if not JOB_ID_PATTERN.match(job_id):
        raise ApiError(400, "invalid job id")
    return job_id

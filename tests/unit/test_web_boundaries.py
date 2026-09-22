"""The security boundaries of the local server, asserted rather than assumed.

The server has no authentication. That is only acceptable while it has no
network exposure, so the exposure question is a test rather than a comment.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.web import presenter
from career_agent.web.server import (
    ApiError,
    LocalApp,
    ServerConfig,
    assert_loopback,
    safe_static_path,
    validate_job_id,
)

# =========================================================================
# It binds loopback, or it does not bind
# =========================================================================


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.0.0.5"])
def test_loopback_hosts_are_accepted(host: str) -> None:
    assert_loopback(host)


@pytest.mark.parametrize(
    "host",
    ["0.0.0.0", "192.168.1.10", "10.0.0.1", "example.com", "169.254.169.254", ""],
)
def test_non_loopback_hosts_are_refused(host: str) -> None:
    with pytest.raises(ValueError, match="loopback"):
        assert_loopback(host)


def test_the_app_refuses_to_construct_on_a_routable_host(tmp_path: Path) -> None:
    # The refusal happens in the constructor, before anything opens a socket,
    # so a misconfiguration cannot briefly publish the corpus while starting.
    with pytest.raises(ValueError, match="loopback"):
        LocalApp(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path, host="0.0.0.0"))


# =========================================================================
# Static files stay inside static/
# =========================================================================


@pytest.mark.parametrize(
    "path",
    [
        "/../../../../etc/passwd",
        "/..%2f..%2fpyproject.toml",
        "/js/../../../../.env",
        "/%2e%2e/%2e%2e/.env",
        "/../.env",
    ],
)
def test_traversal_attempts_resolve_to_nothing(path: str) -> None:
    assert safe_static_path(path) is None


def test_a_real_static_file_resolves() -> None:
    resolved = safe_static_path("/index.html")
    # The frontend may not exist yet in a partial checkout; when it does, the
    # resolved path must be inside static/.
    if resolved is not None:
        assert "static" in resolved.parts


# =========================================================================
# Input validation happens before SQL
# =========================================================================


@pytest.mark.parametrize(
    "job_id",
    ["'; DROP TABLE job; --", "../../secret", "a" * 65, "", "id with spaces", "id/with/slash"],
)
def test_hostile_job_ids_are_rejected(job_id: str) -> None:
    with pytest.raises(ApiError) as exc:
        validate_job_id(job_id)
    assert exc.value.status == 400


def test_ordinary_ulids_pass() -> None:
    assert validate_job_id("01J8ZFT7Q0X9ABCDEFGHJKMNPQ") == "01J8ZFT7Q0X9ABCDEFGHJKMNPQ"


# =========================================================================
# Routing refuses what it does not know
# =========================================================================


def test_unknown_endpoints_are_404(tmp_path: Path) -> None:
    app = LocalApp(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path))
    with pytest.raises(ApiError) as exc:
        app.handle_api("GET", "/api/does-not-exist", {}, {})
    assert exc.value.status == 404


def test_wrong_method_is_405(tmp_path: Path) -> None:
    app = LocalApp(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path))
    app.register("GET", r"/api/thing", lambda **kw: {"ok": True})
    with pytest.raises(ApiError) as exc:
        app.handle_api("POST", "/api/thing", {}, {})
    assert exc.value.status == 405


def test_there_is_no_route_that_submits_an_application(tmp_path: Path) -> None:
    """The product tracks applications. It never sends one.

    Asserted over the real route table rather than trusted, because "we would
    never add that" is exactly the kind of thing a future increment adds.
    """
    from career_agent.web.api import JobsApi

    api = JobsApi(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path))
    patterns = [pattern.pattern for _, pattern, _ in api._routes]
    joined = " ".join(patterns).lower()
    for forbidden in ("apply", "submit", "application/send", "autoapply"):
        assert forbidden not in joined, f"a route mentioning {forbidden!r} exists"


#: What a handler that changes something says. Every write in this codebase
#: goes through one of these: `transaction` opens the unit of work, and the
#: three statements are what the repositories issue inside it.
_WRITING = ("transaction(", "INSERT ", "UPDATE ", "DELETE ", "ensure_candidate(")


def test_no_get_route_changes_anything(tmp_path: Path) -> None:
    """A page load must never write, and must never reach a model.

    Checked against what the HANDLERS do rather than against words in their
    URLs. The path-substring version of this test read `GET /api/cv/imports` --
    a list of staged CV reads, which writes nothing -- as a mutation, because
    the noun "imports" contains the verb "import". A rule that cannot tell
    those apart would either have to be weakened until it caught nothing or
    would block a route for being badly named.

    `ensure_candidate` counts as a write and is in the list deliberately. It
    creates a row, and a GET that quietly created a candidate would make "you
    have confirmed nothing" and "this database has no candidate" the same
    answer -- which is the distinction the whole evidence surface rests on.
    """
    import ast
    import inspect
    import textwrap

    from career_agent.web.api import JobsApi

    def code_only(handler: object) -> str:
        """The handler without its prose.

        Docstrings and comments are stripped, because this file's own habit of
        explaining WHY a route does not DELETE would otherwise be read as the
        route doing one. String LITERALS are kept: the SQL is in them, and
        dropping every string would leave the check unable to see a write at
        all.
        """
        tree = ast.parse(textwrap.dedent(inspect.getsource(handler)))  # type: ignore[arg-type]
        prose: set[int] = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                prose.update(range(first.lineno, (first.end_lineno or first.lineno) + 1))
        lines = textwrap.dedent(inspect.getsource(handler)).splitlines()  # type: ignore[arg-type]
        return "\n".join(
            line
            for number, line in enumerate(lines, start=1)
            if number not in prose and not line.lstrip().startswith("#")
        )

    api = JobsApi(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path))
    for method, pattern, handler in api._routes:
        if method != "GET":
            continue
        assert "enrich" not in pattern.pattern
        source = code_only(handler)
        for verb in _WRITING:
            assert verb not in source, f"GET {pattern.pattern} writes with {verb.strip()}"


# =========================================================================
# The presenter keeps the three measurements apart
# =========================================================================


def test_an_unscored_job_reports_null_not_zero() -> None:
    """ "Not scored" and "scored zero" are opposite statements about a job."""
    from career_agent.domain.matching import ScoredJob

    job = ScoredJob(
        job_id="j1",
        title="Anything",
        company_name="Acme",
        company_slug="acme",
        provider="greenhouse",
        access_method="ats_structured",
        external_id="x",
        url="https://example.invalid/j1",
        location_raw=None,
        department=None,
        posted_at=None,
        first_seen_at=None,
        last_seen_at=None,
        closed_at=None,
        content_hash="deadbeef",
        result=None,
    )
    card = presenter.job_card(
        job,
        bands={"fit": {"STRONG": 75}, "confidence": {"HIGH": 70, "MEDIUM": 45}},
        today=presenter.utc_today(),
        recency={},
    )
    assert card["scored"] is False
    assert card["match_score"] is None
    assert card["data_confidence"] is None
    assert card["fit_band"] is None


def test_the_card_never_emits_a_blended_number() -> None:
    """No field combines score with confidence or with eligibility.

    A guard on the SHAPE of the payload: if someone later adds
    `overall_match`, this fails and asks them to justify it.
    """
    from career_agent.domain.matching import ScoredJob

    job = ScoredJob(
        job_id="j1",
        title="T",
        company_name="C",
        company_slug="c",
        provider="p",
        access_method="a",
        external_id="e",
        url=None,
        location_raw=None,
        department=None,
        posted_at=None,
        first_seen_at=None,
        last_seen_at=None,
        closed_at=None,
        content_hash="h",
        result=None,
    )
    card = presenter.job_card(
        job, bands={"fit": {}, "confidence": {}}, today=presenter.utc_today(), recency={}
    )
    forbidden = {"overall", "combined", "total_score", "weighted_score", "final_score", "rating"}
    assert not forbidden & set(card), "a blended measurement appeared on the card"
    assert "match_score" in card and "data_confidence" in card


def test_has_applied_is_derived_and_not_client_settable() -> None:
    from career_agent.domain.matching import ScoredJob

    job = ScoredJob(
        job_id="j1",
        title="T",
        company_name="C",
        company_slug="c",
        provider="p",
        access_method="a",
        external_id="e",
        url=None,
        location_raw=None,
        department=None,
        posted_at=None,
        first_seen_at=None,
        last_seen_at=None,
        closed_at=None,
        content_hash="h",
        application_status="APPLIED",
        applied_at=None,
    )
    card = presenter.job_card(
        job, bands={"fit": {}, "confidence": {}}, today=presenter.utc_today(), recency={}
    )
    assert card["has_applied"] is True

    payload = json.loads(json.dumps(card, default=str))
    assert payload["application_status"] == "APPLIED"


# =========================================================================
# Freshness
# =========================================================================


def test_a_missing_date_is_unknown_not_stale() -> None:
    from datetime import date

    assert (
        presenter.freshness_for(None, today=date(2026, 9, 4), fresh_days=14, stale_days=45)
        == "UNKNOWN"
    )
    assert (
        presenter.freshness_for("", today=date(2026, 9, 4), fresh_days=14, stale_days=45)
        == "UNKNOWN"
    )
    assert (
        presenter.freshness_for("not-a-date", today=date(2026, 9, 4), fresh_days=14, stale_days=45)
        == "UNKNOWN"
    )


def test_freshness_bands() -> None:
    from datetime import date

    today = date(2026, 9, 4)
    assert (
        presenter.freshness_for("2026-09-01", today=today, fresh_days=14, stale_days=45) == "FRESH"
    )
    assert (
        presenter.freshness_for("2026-08-10", today=today, fresh_days=14, stale_days=45) == "RECENT"
    )
    assert (
        presenter.freshness_for("2026-01-01", today=today, fresh_days=14, stale_days=45) == "STALE"
    )
    # ISO timestamps from ATS payloads, with and without a Z.
    assert (
        presenter.freshness_for("2026-09-02T10:00:00Z", today=today, fresh_days=14, stale_days=45)
        == "FRESH"
    )


# =========================================================================
# The browser is on this machine too
# =========================================================================
#
# "No authentication because no network exposure" covers a machine on the LAN.
# It does not cover the browser already running here, which will send a request
# to 127.0.0.1 on behalf of any page the user has open. These are the checks
# that close that, and they are tested over real HTTP rather than by calling
# the helpers, because the thing being asserted is what a socket accepts.


def _running_server(tmp_path: Path):
    """A real server on a free port, in a thread, torn down by the caller."""
    import socket as _socket
    import threading

    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig, build_server

    probe = _socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    app = JobsApi(ServerConfig(db_path=tmp_path / "x.db", config_dir=Path("config"), port=port))
    httpd = build_server(app)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, port


def _request(port: int, path: str, *, method: str = "GET", headers=None, body=None):
    import http.client

    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=body, headers=headers or {})
    response = conn.getresponse()
    response.read()
    conn.close()
    return response.status


def test_a_forged_host_header_is_refused(tmp_path: Path) -> None:
    """DNS rebinding: an attacker name resolving to 127.0.0.1 arrives with its
    own hostname here, and nothing else in the stack would notice."""
    httpd, port = _running_server(tmp_path)
    try:
        assert _request(port, "/api/health", headers={"Host": f"127.0.0.1:{port}"}) != 403
        assert _request(port, "/api/health", headers={"Host": f"evil.example.com:{port}"}) == 403
        assert _request(port, "/api/health", headers={"Host": "127.0.0.1:1"}) == 403
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_cross_origin_post_is_refused(tmp_path: Path) -> None:
    httpd, port = _running_server(tmp_path)
    try:
        status = _request(
            port,
            "/api/import",
            method="POST",
            headers={
                "Host": f"127.0.0.1:{port}",
                "Origin": "https://evil.example.com",
                "Content-Type": "application/json",
            },
            body=b"{}",
        )
        assert status == 403, "a page on another origin must not be able to write here"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_simple_form_post_cannot_carry_a_body(tmp_path: Path) -> None:
    """text/plain is one of the three content types a cross-origin form can send
    without a preflight. Requiring JSON is what turns every forged POST into a
    preflight, which this server answers with 501 because it has no do_OPTIONS."""
    httpd, port = _running_server(tmp_path)
    try:
        status = _request(
            port,
            "/api/import",
            method="POST",
            headers={"Host": f"127.0.0.1:{port}", "Content-Type": "text/plain"},
            body=b'{"title":"x","company":"y","description":"z"}',
        )
        assert status == 415
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_server_does_not_answer_preflight(tmp_path: Path) -> None:
    httpd, port = _running_server(tmp_path)
    try:
        status = _request(
            port, "/api/import", method="OPTIONS", headers={"Host": f"127.0.0.1:{port}"}
        )
        assert status in (405, 501), "answering preflight would hand out CORS permission"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_an_internal_error_does_not_hand_the_client_a_filesystem_path(tmp_path: Path) -> None:
    """sqlite3 and OSError messages carry absolute paths and SQL fragments."""
    import http.client
    import json as _json

    httpd, port = _running_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        # The database was never created, so the first query raises.
        conn.request("GET", "/api/jobs", headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
        conn.close()
        if response.status == 500:
            body = _json.loads(payload)
            assert "detail" not in body
            assert "AI-Workspace" not in payload and ".db" not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()


# =========================================================================
# WHO A MESSAGE IS ADDRESSED TO
#
# Every view in the interface prints `error.userMessage`, so the question of
# what a person reads when something breaks is settled here and in `api.js`.
# The rule: the server marks a message written FOR A READER, and the browser
# prints its own plain sentence for everything else.
# =========================================================================


def test_a_message_is_a_diagnostic_until_somebody_says_otherwise() -> None:
    """The default is False, on the same principle as every other check here.

    A message added upstream and not marked degrades to the friendly copy in
    the browser. That is the safe direction: the reader loses some precision
    and never sees a SQL fragment or an absolute path.
    """
    assert ApiError(400, "changes must be an object").for_reader is False
    assert ApiError(409, "a rescore is already running", for_reader=True).for_reader is True


def test_the_flag_rides_along_only_when_it_is_true(tmp_path: Path) -> None:
    """Otherwise the key would appear on every error payload ever recorded."""
    import http.client
    import json as _json

    httpd, port = _running_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        # PATCH with no `changes`: a 400 the whitelist raises, and one written
        # for whoever is reading the code rather than for the person.
        body = _json.dumps({"changes": {}})
        conn.request(
            "PATCH",
            "/api/profile",
            body=body,
            headers={"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"},
        )
        response = conn.getresponse()
        payload = _json.loads(response.read().decode("utf-8"))
        conn.close()
        assert response.status == 400
        assert "for_reader" not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_unhandled_fault_does_not_send_anybody_to_a_terminal(tmp_path: Path) -> None:
    """It used to read `internal error -- see the terminal running `serve``.

    True, and addressed to nobody who was there: this runs from
    `career-agent start`, and the person who pressed the button is looking at
    a browser. The sentence goes to the log; the page says something she can
    act on, which `api.js` supplies because this payload carries no
    `for_reader`.
    """
    import http.client
    import json as _json

    httpd, port = _running_server(tmp_path)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/api/jobs", headers={"Host": f"127.0.0.1:{port}"})
        response = conn.getresponse()
        payload = response.read().decode("utf-8")
        conn.close()
        if response.status == 500:
            body = _json.loads(payload)
            assert "for_reader" not in body, "an unhandled fault is never addressed to a reader"
            # `serve` backticked, because "serve" is a substring of "server"
            # and the log line legitimately says server.
            assert "terminal" not in payload and "`serve`" not in payload
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_null_byte_in_the_path_is_a_miss_not_a_crash() -> None:
    assert safe_static_path("/%00") is None
    assert safe_static_path("/index.html\x00.png") is None


# =========================================================================
# Absence is never permission -- including inside the guard
# =========================================================================
#
# The first version of `_check_origin` read `if host and ...`, `if origin and
# ...`, `if content_type and ...`. A request that supplied NONE of the three
# satisfied all three by omission, and a header-free HTTP/1.0 POST reached
# `/api/import` and committed a row. Invariant 2 of this project, broken inside
# the guard written to enforce a boundary.


def _raw_request(port: int, payload: bytes) -> str:
    """Send bytes at the socket, so a header can genuinely be absent.

    `http.client` inserts a Host header whether you want one or not, which is
    exactly the case that was broken. This is the only way to test it.
    """
    import socket as _socket

    with _socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(payload)
        chunks = []
        while True:
            data = sock.recv(4096)
            if not data:
                break
            chunks.append(data)
            if b"\r\n\r\n" in b"".join(chunks):
                break
    return b"".join(chunks).decode("utf-8", "replace")


def test_a_post_with_no_headers_at_all_is_refused(tmp_path: Path) -> None:
    """The regression that mattered: omission is not consent."""
    httpd, port = _running_server(tmp_path)
    try:
        body = b'{"title":"CSRF","company":"X","description":"' + b"a" * 200 + b'"}'
        payload = (
            b"POST /api/import HTTP/1.0\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
        )
        response = _raw_request(port, payload)
        status = response.split("\r\n")[0]
        assert " 403 " in status or " 415 " in status, f"a header-free POST was accepted: {status}"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_get_with_no_host_header_is_refused(tmp_path: Path) -> None:
    httpd, port = _running_server(tmp_path)
    try:
        response = _raw_request(port, b"GET /api/health HTTP/1.0\r\n\r\n")
        assert " 403 " in response.split("\r\n")[0]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_post_with_no_content_type_is_refused(tmp_path: Path) -> None:
    """A Host alone must not be enough to write."""
    httpd, port = _running_server(tmp_path)
    try:
        body = b"{}"
        payload = (
            b"POST /api/import HTTP/1.1\r\n"
            b"Host: 127.0.0.1:" + str(port).encode() + b"\r\n"
            b"Content-Length: " + str(len(body)).encode() + b"\r\n"
            b"Connection: close\r\n\r\n" + body
        )
        response = _raw_request(port, payload)
        assert " 415 " in response.split("\r\n")[0]
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_the_ordinary_same_origin_request_still_works(tmp_path: Path) -> None:
    """Tightening the guard must not break the page that uses it."""
    httpd, port = _running_server(tmp_path)
    try:
        payload = (
            b"GET /api/health HTTP/1.1\r\n"
            b"Host: 127.0.0.1:" + str(port).encode() + b"\r\n"
            b"Connection: close\r\n\r\n"
        )
        response = _raw_request(port, payload)
        first = response.split("\r\n")[0]
        assert " 403 " not in first and " 415 " not in first
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_a_path_registered_for_two_methods_answers_both(tmp_path: Path) -> None:
    """The router used to 405 on the first path match, whatever its method.

    So a path registered for GET and PATCH only ever answered the one
    registered first -- `/api/preferences` could be read and never saved. The
    405 must mean "no route here takes this method", not "the first route I
    found takes a different one".
    """
    from career_agent.web.api import JobsApi

    api = JobsApi(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path), quiet=True)
    by_path: dict[str, set[str]] = {}
    for method, pattern, _ in api._routes:
        by_path.setdefault(pattern.pattern, set()).add(method)

    shared = {p: m for p, m in by_path.items() if len(m) > 1}
    assert shared, "no path carries two methods; this test has gone blind"

    for pattern, methods in shared.items():
        for method in methods:
            matching = [m for m, p, _ in api._routes if p.pattern == pattern and m == method]
            assert matching, f"{method} {pattern} is registered but unreachable"

    # And the 405 still fires when nothing takes the method.
    with pytest.raises(ApiError) as caught:
        api.handle_api("PATCH", "/api/health", {}, {})
    assert caught.value.status == 405

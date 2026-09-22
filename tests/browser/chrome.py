"""A Chrome DevTools Protocol client made of nothing but the standard library.

WHY this file exists rather than `pip install playwright`: the frontend ships
with no bundler, no npm tree and no test runner of its own, and that is a
recorded decision. Installing a browser automation stack -- and the browser
build it downloads -- to prove thirteen assertions would be the largest
dependency decision in the repository, taken to verify the part of the product
that was deliberately kept dependency-free.

Underneath Playwright and Selenium the protocol is the same: a WebSocket
carrying JSON-RPC to a Chrome that is already on this machine. So the honest
cost of an automated browser pass is one file: RFC 6455 client framing, a
reader thread, and the eight CDP calls the tests actually make.

Nothing here reaches the network. The socket is to 127.0.0.1, and the page it
drives is served by the fixture in `conftest.py`.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import TracebackType
from typing import Any

#: RFC 6455 section 1.3. The server echoes sha1(key + this) so both ends can
#: prove they spoke the handshake rather than stumbled into a byte stream.
_WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

#: Every wait in this module is generous on purpose. A slow first paint is not
#: a failing assertion, and a test that races the browser is worse than no test.
DEFAULT_TIMEOUT = 30.0

LAUNCH_FLAGS = (
    "--headless=new",
    "--remote-debugging-port=0",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-gpu",
)


def find_chrome() -> Path | None:
    """The installed Chrome, or None. Absence is a skip, never a failure."""
    candidates: list[Path] = []
    for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        base = os.environ.get(variable)
        if base:
            candidates.append(Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe")
    for name in ("chrome", "chromium", "google-chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


class _Connection:
    """One WebSocket to one CDP target, with a reader thread behind it.

    A reader thread rather than request/response over a bare socket because CDP
    interleaves unsolicited events with replies: a console error arriving while
    an evaluate is in flight must not be mistaken for that evaluate's answer.
    """

    def __init__(self, ws_url: str) -> None:
        parsed = urllib.parse.urlsplit(ws_url)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 9222
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")

        self._sock = socket.create_connection((host, port), timeout=DEFAULT_TIMEOUT)
        # A one-second recv timeout is what lets the reader thread notice that
        # close() happened instead of blocking on a socket nobody will feed.
        self._sock.settimeout(1.0)
        self._handshake(host, port, path)

        self._send_lock = threading.Lock()
        self._state = threading.Condition()
        self._replies: dict[int, dict[str, Any]] = {}
        self._events: list[dict[str, Any]] = []
        self._next_id = 0
        self._closed = False
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    # -- handshake ---------------------------------------------------------
    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        buffer = b""
        while b"\r\n\r\n" not in buffer:
            block = self._sock.recv(4096)
            if not block:
                raise ConnectionError("Chrome closed the socket during the WebSocket handshake")
            buffer += block
        status = buffer.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise ConnectionError(f"Chrome refused the WebSocket upgrade: {status!r}")
        # Proving the accept token is what separates "we upgraded" from "we are
        # talking to something that answered 101 for its own reasons".
        expected = base64.b64encode(
            hashlib.sha1((key + _WS_GUID).encode("ascii")).digest()  # noqa: S324 -- RFC 6455
        ).decode("ascii")
        if expected.encode("ascii") not in buffer:
            raise ConnectionError("the WebSocket accept token did not match the key we sent")

    # -- framing -----------------------------------------------------------
    def _read_exact(self, count: int) -> bytes:
        chunks: list[bytes] = []
        got = 0
        while got < count:
            try:
                block = self._sock.recv(count - got)
            except TimeoutError:
                if self._closed:
                    raise ConnectionError("connection closed while reading a frame") from None
                continue
            except OSError as error:
                raise ConnectionError(str(error)) from error
            if not block:
                raise ConnectionError("Chrome closed the socket")
            chunks.append(block)
            got += len(block)
        return b"".join(chunks)

    def _read_frame(self) -> tuple[bool, int, bytes]:
        header = self._read_exact(2)
        final = bool(header[0] & 0x80)
        opcode = header[0] & 0x0F
        length = header[1] & 0x7F
        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        # A server frame is never masked, but honouring the bit costs one line
        # and makes this readable against the RFC rather than against Chrome.
        mask = self._read_exact(4) if header[1] & 0x80 else b""
        payload = self._read_exact(length) if length else b""
        if mask:
            payload = bytes(byte ^ mask[i % 4] for i, byte in enumerate(payload))
        return final, opcode, payload

    def _send(self, text: str, opcode: int = 0x1) -> None:
        payload = text.encode("utf-8")
        header = bytearray([0x80 | opcode])
        size = len(payload)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header.append(0x80 | 126)
            header += size.to_bytes(2, "big")
        else:
            header.append(0x80 | 127)
            header += size.to_bytes(8, "big")
        # Client frames MUST be masked (RFC 6455 5.3). Chrome drops the
        # connection without explanation if they are not.
        key = os.urandom(4)
        header += key
        masked = bytes(byte ^ key[i % 4] for i, byte in enumerate(payload))
        with self._send_lock:
            self._sock.sendall(bytes(header) + masked)

    def _read_loop(self) -> None:
        buffer = b""
        while not self._closed:
            try:
                final, opcode, payload = self._read_frame()
            except (ConnectionError, OSError):
                break
            if opcode == 0x9:  # ping -- answer it so Chrome keeps the socket
                with self._send_lock:
                    self._sock.sendall(bytes([0x8A, 0x80]) + os.urandom(4))
                continue
            if opcode == 0x8:  # close
                break
            if opcode in (0x0, 0x1, 0x2):
                buffer += payload
                if not final:
                    continue
                message, buffer = buffer, b""
                self._dispatch(message)
        with self._state:
            self._closed = True
            self._state.notify_all()

    def _dispatch(self, raw: bytes) -> None:
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        with self._state:
            if "id" in message:
                self._replies[int(message["id"])] = message
            else:
                self._events.append(message)
            self._state.notify_all()

    # -- RPC ---------------------------------------------------------------
    def call(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = DEFAULT_TIMEOUT
    ) -> dict[str, Any]:
        with self._state:
            self._next_id += 1
            ident = self._next_id
        self._send(json.dumps({"id": ident, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        with self._state:
            while ident not in self._replies:
                if self._closed:
                    raise ConnectionError(f"connection closed while waiting for {method}")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{method} did not answer within {timeout}s")
                self._state.wait(min(0.2, remaining))
            reply = self._replies.pop(ident)
        if "error" in reply:
            raise RuntimeError(f"{method} failed: {reply['error']}")
        result = reply.get("result", {})
        return result if isinstance(result, dict) else {}

    def drain_events(self) -> list[dict[str, Any]]:
        with self._state:
            events, self._events = self._events, []
        return events

    def close(self) -> None:
        self._closed = True
        with contextlib.suppress(OSError):
            self._send("", opcode=0x8)
        with contextlib.suppress(OSError):
            self._sock.close()
        self._reader.join(timeout=2)


class Chrome:
    """A launched headless Chrome and the page target inside it."""

    def __init__(self, process: subprocess.Popen[bytes], connection: _Connection) -> None:
        self._process = process
        self._cdp = connection
        self._errors: list[dict[str, str]] = []
        #: Every emulated media feature currently in force, by name.
        #:
        #: One dict rather than one call site each, because
        #: `Emulation.setEmulatedMedia` REPLACES the whole feature list rather
        #: than merging into it. Two helpers writing that slot independently
        #: silently undo each other: setting reduced motion cleared the
        #: emulated colour scheme, so a test that photographed the light
        #: palette got whatever headless Chrome answers by default -- dark --
        #: with nothing failing to say so.
        self._media: dict[str, str] = {}
        for domain in ("Page", "Runtime", "Log"):
            self._cdp.call(f"{domain}.enable")

    # -- lifecycle ---------------------------------------------------------
    @classmethod
    def launch(cls, binary: Path, user_data_dir: Path) -> Chrome:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen(
            [str(binary), *LAUNCH_FLAGS, f"--user-data-dir={user_data_dir}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            port = _await_debug_port(user_data_dir, process)
            ws_url = _await_page_target(port)
            return cls(process, _Connection(ws_url))
        except Exception:
            process.kill()
            raise

    def close(self) -> None:
        self._cdp.close()
        self._process.terminate()
        try:
            self._process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._process.kill()

    def __enter__(self) -> Chrome:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # -- driving -----------------------------------------------------------
    def navigate(self, url: str) -> None:
        result = self._cdp.call("Page.navigate", {"url": url})
        if result.get("errorText"):
            raise RuntimeError(f"navigation to {url} failed: {result['errorText']}")
        self.wait_for("document.readyState === 'complete'", message=f"{url} to finish loading")

    def reload(self) -> None:
        """A real `location.reload()`, started from a task so this call's own
        reply is not racing the teardown of the context it was evaluated in.
        The sentinel is how we tell the new document from the old one:
        `readyState` is already 'complete' on the page we are leaving."""
        self.evaluate("window.__reloaded = 1; setTimeout(() => location.reload(), 0); true")
        self.wait_for(
            "document.readyState === 'complete' && window.__reloaded === undefined",
            message="the page to come back from location.reload()",
        )

    def evaluate(self, expression: str) -> Any:
        result = self._cdp.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
                "userGesture": True,
            },
        )
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = detail.get("exception", {}).get("description") or detail.get("text")
            raise RuntimeError(f"evaluate failed: {text}")
        return result.get("result", {}).get("value")

    def wait_for(self, predicate: str, timeout: float = DEFAULT_TIMEOUT, message: str = "") -> None:
        """Poll a JS predicate. Never a fixed sleep: a sleep that is long
        enough on this machine is a flake on the next one."""
        deadline = time.monotonic() + timeout
        last_error = ""
        last_value: Any = None
        while time.monotonic() < deadline:
            try:
                last_value = self.evaluate(f"Boolean({predicate})")
                if last_value:
                    return
            except RuntimeError as error:
                # A predicate evaluated mid-navigation can lose its context.
                # That is "not yet", but keep the text for the failure message.
                last_error = str(error)
            time.sleep(0.05)
        described = message or predicate
        tail = f" (last error: {last_error})" if last_error else f" (last value: {last_value!r})"
        raise AssertionError(f"timed out after {timeout}s waiting for {described}{tail}")

    def set_viewport(self, width: int, height: int, *, mobile: bool = False) -> None:
        self._cdp.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": width,
                "height": height,
                "deviceScaleFactor": 1,
                "mobile": mobile,
            },
        )

    #: Keys this harness can press, with the codes the protocol wants.
    #:
    #: A short list on purpose. Everything here is a NAVIGATION key, because
    #: typing text is already done by setting a value and dispatching `input`,
    #: and the thing that cannot be faked that way is moving focus.
    _KEYS: dict[str, dict[str, Any]] = {
        "Tab": {"windowsVirtualKeyCode": 9, "code": "Tab", "key": "Tab", "text": "\t"},
        "Enter": {"windowsVirtualKeyCode": 13, "code": "Enter", "key": "Enter", "text": "\r"},
        "Escape": {"windowsVirtualKeyCode": 27, "code": "Escape", "key": "Escape"},
        "ArrowRight": {"windowsVirtualKeyCode": 39, "code": "ArrowRight", "key": "ArrowRight"},
        "ArrowLeft": {"windowsVirtualKeyCode": 37, "code": "ArrowLeft", "key": "ArrowLeft"},
        "Home": {"windowsVirtualKeyCode": 36, "code": "Home", "key": "Home"},
        "End": {"windowsVirtualKeyCode": 35, "code": "End", "key": "End"},
    }

    def press(self, key: str, *, shift: bool = False) -> None:
        """Press a key the way a person does.

        REAL key events, not `element.focus()`. The difference is the whole
        reason this exists: `:focus-visible` is a heuristic about how focus
        ARRIVED, and a programmatic `focus()` does not satisfy it on a button.
        A test that focused elements in a loop and read back the outline
        therefore reported every control on the page as having no focus ring,
        which was the test being wrong rather than the stylesheet.
        """
        if key not in self._KEYS:
            raise ValueError(f"{key!r} is not one of {sorted(self._KEYS)}")
        modifiers = 8 if shift else 0
        for event in ("rawKeyDown", "keyUp"):
            payload = {"type": event, "modifiers": modifiers, **self._KEYS[key]}
            if event == "keyUp":
                payload.pop("text", None)
            self._cdp.call("Input.dispatchKeyEvent", payload)

    def set_color_scheme(self, scheme: str | None) -> None:
        """Emulate `prefers-color-scheme`, or clear the override with `None`.

        The interface has no theme control -- `app.css` answers the media query
        and nothing else -- so this is the only way to photograph the light
        palette. Headless Chrome answers `dark` by default, which is why the
        committed evidence was entirely dark and the light theme, which is what
        most people will actually see, had no reproducible capture at all.
        """
        self._emulate_media("prefers-color-scheme", scheme)

    def set_reduced_motion(self, reduce: bool) -> None:
        """Emulate `prefers-reduced-motion`, the way `set_color_scheme` does.

        The product answers this by asking for a DIFFERENT FILE -- the still
        star instead of the twinkling one -- so it is a claim about a request,
        not about a CSS property, and the only honest way to check it is to
        set the preference and see which file the page fetches.
        """
        self._emulate_media("prefers-reduced-motion", "reduce" if reduce else "no-preference")

    def _emulate_media(self, name: str, value: str | None) -> None:
        """Set one emulated media feature, and re-send all of them.

        `None` removes the feature rather than emulating a neutral value for
        it, which are different things: `prefers-color-scheme: light` is an
        answer, and no override at all lets the browser give its own.
        """
        if value is None:
            self._media.pop(name, None)
        else:
            self._media[name] = value
        self._cdp.call(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": k, "value": v} for k, v in sorted(self._media.items())]},
        )

    def screenshot(self, path: Path) -> Path:
        result = self._cdp.call("Page.captureScreenshot", {"format": "png"})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(result["data"]))
        return path

    # -- console -----------------------------------------------------------
    def console_errors(self) -> list[dict[str, str]]:
        """Errors seen since the last `clear_console()`.

        `source` is the whole point: a browser-emitted resource error and an
        exception thrown by our own modules both land here, and only the second
        kind is this application's defect.
        """
        for event in self._cdp.drain_events():
            method = event.get("method")
            params = event.get("params", {})
            if method == "Runtime.consoleAPICalled" and params.get("type") == "error":
                text = " ".join(
                    str(arg.get("value", arg.get("description", "")))
                    for arg in params.get("args", [])
                )
                self._errors.append({"source": "console", "text": text, "url": ""})
            elif method == "Runtime.exceptionThrown":
                detail = params.get("exceptionDetails", {})
                text = detail.get("exception", {}).get("description") or detail.get("text", "")
                self._errors.append(
                    {"source": "exception", "text": text, "url": detail.get("url", "")}
                )
            elif method == "Log.entryAdded":
                entry = params.get("entry", {})
                if entry.get("level") == "error":
                    self._errors.append(
                        {
                            "source": str(entry.get("source", "unknown")),
                            "text": str(entry.get("text", "")),
                            "url": str(entry.get("url", "")),
                        }
                    )
        return list(self._errors)

    def clear_console(self) -> None:
        self._cdp.drain_events()
        self._errors.clear()


def _await_debug_port(user_data_dir: Path, process: subprocess.Popen[bytes]) -> int:
    """`--remote-debugging-port=0` means "pick one", and Chrome writes the one
    it picked into this file. Reading it is how the tests avoid guessing a port
    that some other process already holds."""
    marker = user_data_dir / "DevToolsActivePort"
    deadline = time.monotonic() + DEFAULT_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Chrome exited with {process.returncode} before it opened a port")
        if marker.is_file():
            try:
                lines = marker.read_text(encoding="utf-8").splitlines()
            except (PermissionError, FileNotFoundError):
                # On Windows the marker can exist while Chrome still owns its
                # write handle. Keep the same bounded startup deadline.
                lines = []
            if lines and lines[0].strip().isdigit():
                return int(lines[0].strip())
        time.sleep(0.05)
    raise TimeoutError(f"Chrome never wrote {marker}")


def _await_page_target(port: int) -> str:
    deadline = time.monotonic() + DEFAULT_TIMEOUT
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as response:
                targets = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            time.sleep(0.05)
            continue
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return str(target["webSocketDebuggerUrl"])
        time.sleep(0.05)
    raise TimeoutError(f"no page target appeared on the debug port {port}")

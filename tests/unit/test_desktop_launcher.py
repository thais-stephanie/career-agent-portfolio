"""The Windows desktop launcher (scripts/desktop.py) and its icon.

The launcher decides from what answers on the port: this installation's Career
Agent, something else, or nothing. These tests serve each answer on a free
local port; nothing real is started and no browser opens.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")


def _load(name: str):  # noqa: ANN202
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    module = _load("desktop")
    shown: list[str] = []
    opened: list[str] = []
    monkeypatch.setattr(module, "message", shown.append)
    monkeypatch.setattr(module, "open_window", lambda: opened.append("window") or True)
    module.shown, module.opened = shown, opened
    return module


def _serve(desktop, monkeypatch, status: int, payload: object) -> Iterator[None]:  # noqa: ANN001
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            body = json.dumps(payload).encode() if not isinstance(payload, bytes) else payload
            self.send_response(status)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{server.server_address[1]}/")
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


def ours(mode: str = "PERSONAL") -> dict:
    from career_agent import install_id

    return {"app": "career-agent", "install": install_id(ROOT), "mode": mode, "can_quit": True}


def test_this_installation_is_recognised(desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in _serve(desktop, monkeypatch, 200, ours()):
        assert desktop.probe()["mode"] == "PERSONAL"


@pytest.mark.parametrize(
    ("status", "payload"),
    [
        (200, {"app": "career-agent", "install": "another-folder", "mode": "PERSONAL"}),
        (200, {"app": "something-else"}),
        (404, {"error": "no such route"}),
        (200, b"<html>a different program</html>"),
        (200, ["career-agent"]),
    ],
)
def test_anything_else_on_the_port_is_not_career_agent(
    desktop, monkeypatch: pytest.MonkeyPatch, status: int, payload: object
) -> None:
    for _ in _serve(desktop, monkeypatch, status, payload):
        assert desktop.probe() == "other"


def test_a_listener_that_does_not_speak_http_is_not_career_agent(
    desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    stop = threading.Event()

    def accept_and_close() -> None:
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            conn.close()

    threading.Thread(target=accept_and_close, daemon=True).start()
    monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{listener.getsockname()[1]}/")
    try:
        assert desktop.probe() == "other"
    finally:
        stop.set()
        listener.close()


def test_nothing_listening_reads_as_nothing(desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setattr(desktop, "URL", f"http://127.0.0.1:{port}/")
    assert desktop.probe() is None


def test_a_running_career_agent_gets_a_window_and_no_second_server(
    desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[object] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: started.append(a))
    for _ in _serve(desktop, monkeypatch, 200, ours()):
        assert desktop.run() == 0
    assert desktop.opened == ["window"] and not started and not desktop.shown


def test_an_unrelated_service_is_neither_opened_nor_stopped(
    desktop, monkeypatch: pytest.MonkeyPatch
) -> None:
    started: list[object] = []
    monkeypatch.setattr(desktop.subprocess, "Popen", lambda *a, **k: started.append(a))
    for _ in _serve(desktop, monkeypatch, 200, {"app": "something-else"}):
        assert desktop.run() == 1
    assert desktop.shown == [desktop.PORT_BUSY]
    assert not desktop.opened and not started


def test_the_demo_on_the_port_is_named_not_opened(desktop, monkeypatch: pytest.MonkeyPatch) -> None:
    for _ in _serve(desktop, monkeypatch, 200, ours("DEMO")):
        assert desktop.run() == 1
    assert desktop.shown == [desktop.DEMO_OPEN] and not desktop.opened


def test_without_edge_the_default_browser_opens_the_app(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("desktop")
    opened: list[str] = []
    monkeypatch.setattr(module, "edge", lambda: None)
    import webbrowser

    monkeypatch.setattr(webbrowser, "open", opened.append)
    assert module.open_window() is False
    assert opened == [module.URL]


def test_edge_opens_an_app_window_on_its_own_browser_data(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load("desktop")
    calls: list[list[str]] = []
    monkeypatch.setattr(module, "edge", lambda: "C:/Edge/msedge.exe")
    monkeypatch.setattr(module.subprocess, "Popen", lambda args, **k: calls.append(args))
    assert module.open_window() is True
    args = calls[0]
    assert args[0] == "C:/Edge/msedge.exe"
    assert f"--app={module.URL}" in args
    assert f"--user-data-dir={ROOT / 'data' / 'app-window'}" in args


def test_the_shipped_icon_is_the_star_sprite() -> None:
    star = _load("make_star")
    shipped = (ROOT / "src" / "career_agent" / "web" / "static" / "career-agent.ico").read_bytes()
    assert shipped == star.icon(star.shade(star.SHAPE)), (
        "career-agent.ico is generated: run `uv run python scripts/make_star.py`"
    )

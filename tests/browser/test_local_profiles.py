"""The profile switcher in the side rail: always visible, creates, switches.

A live server on a temporary installation with a profile host, as the
launcher builds it (without Resume Tailor, which is not under test here).
"""

from __future__ import annotations

import shutil
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.runtime.profiles import ensure_registry, load_registry
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.profiles import ProfileHost
from career_agent.web.server import build_server

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def live(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    root = tmp_path / "install"
    shutil.copytree(REPO / "config", root / "config", ignore=shutil.ignore_patterns("*.local.*"))
    db = root / "data" / "personal.db"
    db.parent.mkdir(parents=True)
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "profiles")
    finally:
        conn.close()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    host = ProfileHost(root, port=port)
    first = ensure_registry(root).current
    api = host.open(first)
    httpd = build_server(api)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    host.server = httpd
    host.serve(first, api)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}", root
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)
        host.close()


def _name(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.lprof__name')?.innerText || ''"))


def test_the_active_profile_is_always_shown_and_can_be_switched(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, root = live
    page.navigate(base)
    page.wait_for("document.querySelector('.lprof__name')", message="the profile chip")
    assert _name(page) == "My profile"
    explain = str(page.evaluate("document.querySelector('.lprof').innerText")).casefold()
    assert (
        "not accounts" in explain
        or "not accounts"
        in str(page.evaluate("document.querySelector('.lprof__panel').textContent")).casefold()
    )

    page.evaluate("document.querySelector('.lprof').open = true")
    page.evaluate(
        "(() => { const box = document.getElementById('lprof-new'); box.value = 'Synthetic B';"
        " box.closest('form').requestSubmit(); return true; })()"
    )
    page.wait_for(
        "document.querySelector('.lprof__switch')",
        message="a switch button for the new profile",
    )
    assert _name(page) == "My profile", "creating a profile never switches"

    page.evaluate("document.querySelector('.lprof__switch').click()")
    page.wait_for(
        "document.querySelector('.lprof__name')?.innerText === 'Synthetic B'",
        message="the page to reload on the new profile",
    )
    assert load_registry(root).current.label == "Synthetic B"


def test_a_tab_from_another_profile_is_refused_and_told_to_reload(
    live: tuple[str, Path],
) -> None:
    import json
    import urllib.error
    import urllib.request

    base, root = live
    served = load_registry(root).current.id

    def call(claimed: str) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{base}/api/role-anchors", headers={"X-Local-Profile": claimed}
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    assert call(served)[0] == 200
    status, body = call("prof-01AAAAAAAAAAAAAAAAAAAAAAAA")
    assert status == 409 and body["code"] == "profile_changed"

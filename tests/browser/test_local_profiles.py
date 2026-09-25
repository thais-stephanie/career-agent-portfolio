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


def _menu_open(page: Chrome) -> bool:
    return bool(page.evaluate("document.getElementById('lprof-menu') !== null"))


def _key(page: Chrome, target: str, key: str) -> None:
    page.evaluate(
        f"document.querySelector({target!r}).dispatchEvent(new KeyboardEvent('keydown',"
        f" {{ key: {key!r}, bubbles: true, cancelable: true }}))"
    )


def test_the_profile_menu_floats_lists_every_profile_and_reveals_forms_on_demand(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, root = live
    page.navigate(base)
    page.wait_for("document.querySelector('.lprof__name')", message="the profile control")
    assert _name(page) == "My profile"
    # The rail holds one compact control and no form.
    assert page.evaluate("document.querySelectorAll('#local-profiles-host input').length") == 0
    nav_top = page.evaluate("document.querySelector('.sidenav__nav').getBoundingClientRect().top")

    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for("document.getElementById('lprof-menu')", message="the profile menu")
    # A menu over the navigation, not a panel that pushes it down.
    assert (
        page.evaluate("getComputedStyle(document.getElementById('lprof-menu')).position") == "fixed"
    )
    assert (
        page.evaluate("document.querySelector('.sidenav__nav').getBoundingClientRect().top")
        == nav_top
    ), "opening the menu moved the navigation"
    assert (
        page.evaluate("document.getElementById('lprof-trigger').getAttribute('aria-expanded')")
        == "true"
    )
    menu = str(page.evaluate("document.getElementById('lprof-menu').innerText")).casefold()
    assert "not accounts" in menu and "open now" in menu
    # No field until one is asked for; delete is not in the menu at all.
    assert page.evaluate("document.querySelectorAll('#lprof-menu input').length") == 0
    assert page.evaluate(
        "document.getElementById('lprof-delete-which') === null || "
        "!document.getElementById('lprof-menu').contains("
        "document.getElementById('lprof-delete-which'))"
    )

    # Rename: prefilled and selected; Esc cancels the panel, not the menu.
    page.evaluate("document.getElementById('lprof-action-rename').click()")
    page.wait_for("document.getElementById('lprof-rename')", message="the rename field")
    assert page.evaluate("document.activeElement.id") == "lprof-rename"
    assert page.evaluate("document.getElementById('lprof-rename').value") == "My profile"
    # Opening New profile closes Rename: never both.
    page.evaluate("document.getElementById('lprof-action-create').click()")
    page.wait_for("document.getElementById('lprof-new')", message="the new-profile field")
    assert page.evaluate("document.getElementById('lprof-rename') === null")
    assert page.evaluate("document.getElementById('lprof-create').disabled") is True
    _key(page, "#lprof-new", "Escape")
    page.wait_for("document.getElementById('lprof-new') === null", message="Esc to cancel")
    assert _menu_open(page)
    assert page.evaluate("document.activeElement.id") == "lprof-action-create"
    _key(page, "#lprof-menu", "Escape")
    page.wait_for("document.getElementById('lprof-menu') === null", message="Esc to close")
    assert page.evaluate("document.activeElement.id") == "lprof-trigger"

    # An outside click closes it too.
    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for("document.getElementById('lprof-menu')", message="the menu again")
    page.evaluate(
        "document.querySelector('.pagehead')"
        ".dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }))"
    )
    page.wait_for("document.getElementById('lprof-menu') === null", message="outside click")

    # Rename with Enter.
    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for("document.getElementById('lprof-action-rename')", message="the rename action")
    page.evaluate("document.getElementById('lprof-action-rename').click()")
    page.wait_for("document.getElementById('lprof-rename')", message="the rename field")
    page.evaluate("document.getElementById('lprof-rename').value = 'Synthetic A'")
    _key(page, "#lprof-rename", "Enter")
    page.wait_for(
        "document.querySelector('.lprof__name')?.innerText === 'Synthetic A'", message="rename"
    )
    assert load_registry(root).current.label == "Synthetic A"
    assert _menu_open(page), "renaming keeps the menu open"
    _key(page, "#lprof-menu", "Escape")

    # Create with Enter: it switches to the new, empty profile and lands on Home.
    page.navigate(f"{base}/#settings")
    page.wait_for("document.getElementById('lprof-trigger')", message="the control")
    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for(
        "document.getElementById('lprof-action-create')", message="the new-profile action"
    )
    page.evaluate("document.getElementById('lprof-action-create').click()")
    page.wait_for("document.getElementById('lprof-new')", message="the new-profile field")
    page.evaluate(
        "(() => { const box = document.getElementById('lprof-new'); box.value = 'Synthetic B';"
        " box.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
    )
    assert page.evaluate("document.getElementById('lprof-create').disabled") is False
    _key(page, "#lprof-new", "Enter")
    page.wait_for(
        "document.querySelector('.lprof__name')?.innerText === 'Synthetic B'",
        message="the page to reload on the new profile",
    )
    assert page.evaluate("location.hash") == "", "a switch lands on Home"
    assert load_registry(root).current.label == "Synthetic B"

    # Switching back is one click.
    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for("document.querySelector('.lprof__switch')", message="the other profile's row")
    assert "Synthetic A" in str(page.evaluate("document.getElementById('lprof-menu').innerText"))
    page.evaluate("document.querySelector('.lprof__switch').click()")
    page.wait_for(
        "document.querySelector('.lprof__name')?.innerText === 'Synthetic A'",
        message="one click to switch",
    )
    assert load_registry(root).current.label == "Synthetic A"


def test_deleting_a_profile_lives_in_settings_behind_its_name(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, root = live
    from career_agent.runtime.profiles import create_profile

    create_profile(root, "Temporary")
    page.navigate(f"{base}/#settings")
    page.wait_for("document.getElementById('lprof-delete-which')", message="the delete form")
    assert page.evaluate("document.getElementById('settings-profiles-block').hidden") is False
    options = page.evaluate(
        "[...document.querySelectorAll('#lprof-delete-which option')].map((o) => o.textContent)"
    )
    assert options == ["Temporary"], "the open profile and the original are never offered"
    page.evaluate(
        "(() => { document.getElementById('lprof-delete-confirm').value = 'Temporary';"
        " document.querySelector('.lprof__delete').requestSubmit(); return true; })()"
    )
    page.wait_for(
        "document.getElementById('lprof-delete-which') === null",
        message="the profile to be deleted",
    )
    assert [p.label for p in load_registry(root).profiles] == ["My profile"]


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

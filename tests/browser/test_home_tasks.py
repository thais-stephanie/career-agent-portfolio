"""Home's "Small things to do", in a real browser: with tasks, while a
recalculation runs, once it finishes or fails, and with nothing left.
Synthetic corpus, own profile per test, no network and no model.
"""

from __future__ import annotations

import dataclasses
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.home_helpers import open_home_past_setup
from tests.integration.test_home_tasks import _app

from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import build_server

TODOS = "document.querySelector('.home__todos')"
TODO_TEXT = f"({TODOS} ? {TODOS}.innerText : '')"
RECALC = "document.querySelector('.home__todo--recalc')"
RECALC_HELP = f"({RECALC} ? {RECALC}.querySelector('.home__todohelp').innerText : '')"
NEUTRAL = {
    "authorized_countries": [],
    "not_authorized_countries": [],
    "sponsorship_needed": [],
    "contractor": None,
    "contractor_abroad": None,
}


def _serve(app: JobsApi) -> Iterator[str]:
    port = _free_port()
    app.config = dataclasses.replace(app.config, port=port)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture
def pending(tmp_path: Path) -> Iterator[tuple[str, JobsApi]]:
    app = _app(tmp_path / "pending", **NEUTRAL)
    for base in _serve(app):
        yield base, app


def _todo_button(text: str) -> str:
    return (
        "Array.from(document.querySelectorAll('.home__todo'))"
        f".find((n) => n.innerText.includes({text!r}))"
    )


def _hold(
    app: JobsApi, release: threading.Event, *, done: int, total: int, fail: bool = False
) -> None:
    """A recalculation that reports `done` of `total` and waits, then ends
    well or badly. It scores nothing."""

    def work(state, cancel) -> None:
        state.boards_total, state.boards_done = total, done
        release.wait(30)
        if fail:
            raise RuntimeError("held run failed on purpose")

    app.rescore.start(work, "held-run")


def test_tasks_are_whole_rows_with_one_amber_line(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    payload = app.handle_api("GET", "/api/home", {}, {})
    outdated = payload["scores"]["outdated"]
    assert outdated > 0

    open_home_past_setup(page, base)
    page.wait_for(f"{TODO_TEXT}.includes('{outdated} jobs are showing old scores.')")
    text = str(page.evaluate(TODO_TEXT))
    assert "A few things to keep your search up to date." in text
    assert "Update your match scores" in text
    assert "Where can you work?" in text
    questions = len(payload["declarations_pending"])
    assert f"{questions} quick questions. “I don’t know” is fine." in text
    assert "Nothing to do right now" not in text and "Each one takes" not in text
    assert "market" not in text.lower(), "preferred markets are optional, never a task"
    rows = int(page.evaluate("document.querySelectorAll('.home__todo').length"))
    assert page.evaluate("document.querySelector('.home__todocount').innerText") == f"{rows} left"
    # Each row is ONE button with nothing inside to press; Recalculate first.
    assert page.evaluate(
        "Array.from(document.querySelectorAll('.home__todo')).every((b) => b.tagName === 'BUTTON'"
        " && !b.querySelector('button, a, .home__tododot'))"
    )
    assert page.evaluate(f"document.querySelector('.home__todo') === {RECALC}")
    # Its name is the title; the line under it describes it.
    assert (
        page.evaluate(
            f"document.getElementById({RECALC}.getAttribute('aria-describedby')).innerText"
        )
        == f"{outdated} jobs are showing old scores."
    )
    # Amber is the recalculation's line, and only that line.
    colours = page.evaluate(
        "Array.from(document.querySelectorAll('.home__todohelp'))"
        ".map((n) => getComputedStyle(n).color)"
    )
    assert colours[0] not in colours[1:], colours
    assert len(set(colours[1:])) == 1, colours
    if page.evaluate("Boolean(document.querySelector('.home__sec--best'))"):
        stale = "document.querySelector('.home__sec--best .home__stale').innerText"
        note = str(page.evaluate(stale))
        assert "Update them in Small things to do." in note

    # Keyboard: Tab reaches the row and the focus ring shows.
    for _ in range(80):
        page.press("Tab")
        if page.evaluate(f"document.activeElement === {RECALC}"):
            break
    else:
        raise AssertionError("Tab never reached the recalculation row")
    assert page.evaluate(f"getComputedStyle({RECALC}).outlineStyle") == "solid"

    # The rights row opens the profile AT the question, where "I don't know" is offered.
    page.evaluate(f"{_todo_button('Where can you work?')}.click()")
    chip = "document.getElementById('profile-field-authorized_countries-unsure')"
    page.wait_for(f"{chip} && !document.getElementById('page-profile').hidden")
    assert page.evaluate(
        f"(() => {{ const r = {chip}.getBoundingClientRect();"
        " return r.top >= 0 && r.bottom <= window.innerHeight; })()"
    ), "the profile did not open at the question"
    page.evaluate(f"{chip}.click()")
    page.wait_for(f"{chip}.getAttribute('aria-pressed') === 'true'")
    deadline = time.monotonic() + 10
    while "authorization" in app.handle_api("GET", "/api/home", {}, {})["declarations_pending"]:
        assert time.monotonic() < deadline, "I don't know was not recorded"
        time.sleep(0.2)


def test_the_tasks_read_in_portuguese(page: Chrome, pending: tuple[str, JobsApi]) -> None:
    base, app = pending
    payload = app.handle_api("GET", "/api/home", {}, {})
    outdated = payload["scores"]["outdated"]
    page.navigate(base)
    page.evaluate("window.localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    try:
        open_home_past_setup(page, base)
        page.wait_for(f"{TODO_TEXT}.includes('{outdated} vagas estão com o match desatualizado.')")
        text = str(page.evaluate(TODO_TEXT))
        assert "Algumas ações para manter sua busca em dia." in text
        assert "Atualize o match das vagas" in text
        assert "Onde você pode trabalhar?" in text
        assert f"{len(payload['declarations_pending'])} perguntas rápidas." in text
        assert "“Não sei” vale como resposta." in text
        count = str(page.evaluate("document.querySelector('.home__todocount').innerText"))
        assert count.startswith("faltam "), count
    finally:
        page.evaluate("window.localStorage.removeItem('careerAgent.locale.v1')")


def test_a_running_recalculation_shows_progress_and_is_not_started_twice(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    release = threading.Event()
    _hold(app, release, done=5, total=20)
    try:
        open_home_past_setup(page, base)
        page.wait_for(
            f"{RECALC_HELP}.includes('Updating 5 of 20 · you can keep browsing')",
            message="the live progress line",
        )
        assert page.evaluate(f"{RECALC}.getAttribute('aria-busy')") == "true"
        assert (
            page.evaluate(f"getComputedStyle({RECALC}.querySelector('.home__todochev')).visibility")
            == "hidden"
        )
        page.wait_for(
            f"Math.abs({RECALC}.querySelector('.home__todobar').getBoundingClientRect().width"
            f" - {RECALC}.getBoundingClientRect().width / 4) < 3",
            message="a bar a quarter of the row wide",
        )
        before = app.rescore.snapshot()
        page.evaluate(f"{RECALC}.click()")  # busy: starts nothing
        time.sleep(0.5)
        assert app.rescore.running and app.rescore.snapshot()["run_id"] == before["run_id"]
        # The other rows stay usable while it runs, and the run survives them.
        rights = _todo_button("Where can you work?")
        assert page.evaluate(f"!{rights}.disabled && {rights}.getAttribute('aria-busy') !== 'true'")
        page.evaluate(f"{rights}.click()")
        page.wait_for("!document.getElementById('page-profile').hidden", message="the profile")
        open_home_past_setup(page, base)
        page.wait_for(
            f"{RECALC_HELP}.includes('Updating 5 of 20')", message="the run, still followed"
        )
    finally:
        release.set()
        app.rescore.join(10)


def test_a_finished_recalculation_leaves_only_once_confirmed(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    open_home_past_setup(page, base)
    page.wait_for(RECALC, message="the recalculation row")
    rows = int(page.evaluate("document.querySelectorAll('.home__todo').length"))
    page.evaluate(f"{RECALC}.click()")  # a real pass, over this synthetic corpus only
    page.wait_for(f"!{RECALC}", timeout=60, message="the row gone after a confirmed read")
    assert app.handle_api("GET", "/api/home", {}, {})["scores"]["outdated"] == 0
    page.wait_for(f"{TODO_TEXT}.includes('✓ Match scores updated')", message="the done line")
    count = page.evaluate("document.querySelector('.home__todocount').innerText")
    assert count == f"{rows - 1} left"
    page.wait_for(
        "!document.querySelector('.home__todofoot--ok')", timeout=6, message="the line to go"
    )


def test_a_failed_recalculation_keeps_its_row(page: Chrome, pending: tuple[str, JobsApi]) -> None:
    base, app = pending
    release = threading.Event()
    _hold(app, release, done=3, total=20, fail=True)
    try:
        open_home_past_setup(page, base)
        page.wait_for(f"{RECALC_HELP}.includes('Updating 3 of 20')")
    finally:
        release.set()
        app.rescore.join(10)
    page.wait_for(
        f"{RECALC_HELP}.includes('The update stopped before finishing. Try again.')",
        message="the failure line",
    )
    assert page.evaluate(f"{RECALC}.getAttribute('aria-busy')") == "false"
    assert not page.evaluate("Boolean(document.querySelector('.home__todofoot--ok'))")


def test_all_set_only_when_nothing_is_pending(page: Chrome, tmp_path: Path) -> None:
    app = _app(
        tmp_path / "settled",
        authorized_countries=["BR"],
        contractor=True,
        contractor_abroad=False,
    )
    app.handle_api("PATCH", "/api/profile", {}, {"changes": {"compensation_target": 5000}})
    app.handle_api(
        "POST", "/api/evidence", {}, {"text": "Ran month-end reporting.", "claim_type": "SKILL"}
    )
    with connect(app.config.db_path) as conn:
        rescore(conn, app.search_config())
    payload = app.handle_api("GET", "/api/home", {}, {})
    assert payload["scores"]["outdated"] == 0 and payload["declarations_pending"] == []
    assert payload["profile_gaps"] == [], payload["profile_gaps"]
    for base in _serve(app):
        open_home_past_setup(page, base)
        page.wait_for(f"{TODO_TEXT}.includes('New tasks show up here when something needs you.')")
        assert "You’re all set." in str(page.evaluate(TODO_TEXT))
        assert not page.evaluate("Boolean(document.querySelector('.home__todo, .home__todocount'))")
        assert not page.evaluate("Boolean(document.querySelector('.home__stale'))")

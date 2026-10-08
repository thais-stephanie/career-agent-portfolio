"""Home's "Small things to do", in a real browser: with tasks, while a
recalculation runs, and with nothing left. Synthetic corpus, own profile
per test, no network and no model.
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


def test_tasks_name_the_scores_and_the_open_questions(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    outdated = app.handle_api("GET", "/api/home", {}, {})["scores"]["outdated"]
    assert outdated > 0

    open_home_past_setup(page, base)
    page.wait_for(f"{TODO_TEXT}.includes('Recalculate the match for {outdated} jobs')")
    text = str(page.evaluate(TODO_TEXT))
    assert "Answer what you know about your right to work" in text
    for question in ("where you may be employed", "for clients where you live", "abroad"):
        assert question in text, text
    assert "“I don’t know” is a fine answer" in text
    assert "Nothing to do right now" not in text
    assert "market" not in text.lower(), "preferred markets are optional, never a task"
    if page.evaluate("Boolean(document.querySelector('.home__sec--best'))"):
        assert page.evaluate("Boolean(document.querySelector('.home__sec--best .home__stale'))")

    # "Answer" opens the profile AT the question, where "I don't know" is offered.
    page.evaluate(f"{_todo_button('right to work')}.click()")
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
    outdated = app.handle_api("GET", "/api/home", {}, {})["scores"]["outdated"]
    page.navigate(base)
    page.evaluate("window.localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    open_home_past_setup(page, base)
    page.wait_for(f"{TODO_TEXT}.includes('Recalcular o match de {outdated} vagas')")
    text = str(page.evaluate(TODO_TEXT))
    assert "Responda o que você sabe sobre seu direito de trabalhar" in text
    assert "“Não sei” é uma resposta válida" in text
    page.evaluate("window.localStorage.removeItem('careerAgent.locale.v1')")


def test_a_running_recalculation_is_followed_and_not_started_twice(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    release = threading.Event()
    app.rescore.start(lambda state, cancel: release.wait(30), "held-run")
    try:
        open_home_past_setup(page, base)
        button = "document.querySelector('.home__todobtn')"
        page.wait_for(
            f"{button} && {button}.disabled && {button}.innerText.includes('Recalculating')"
        )
        page.evaluate(f"{button}.click()")  # disabled: starts nothing
        assert app.rescore.running
    finally:
        release.set()
        app.rescore.join(10)
    page.wait_for(f"!{button} || !{button}.disabled", message="the control after the run ended")


def test_nothing_to_do_only_when_nothing_is_pending(page: Chrome, tmp_path: Path) -> None:
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
        page.wait_for(f"{TODO_TEXT}.includes('Nothing to do right now')")
        assert not page.evaluate("Boolean(document.querySelector('.home__stale'))")

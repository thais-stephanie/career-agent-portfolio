"""Home's "Fix job sites" task follows the sources' status, which arrives on
its own after Home: the card alone is redrawn, never Home's read, and a task
that appears late, or leaves because another profile answered, is not said to
be done. Synthetic profile; the sources' answer is shaped by the test.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_home_tasks import (
    NEUTRAL,
    RECALC,
    RECALC_HELP,
    TODO_TEXT,
    _hold,
    _serve,
    _todo_button,
)
from tests.integration.test_home_tasks import _app

from career_agent.web.api import JobsApi

TOAST = "document.querySelector('.home__todofoot--ok')"
SITES = "document.querySelector('.home__todo[data-task=\"sites\"]')"


class Shaped:
    """What Home and the sources' status say, controlled by the test."""

    def __init__(self, app: JobsApi) -> None:
        self.failing = 0
        self.home_profile: str | None = "one"
        self.sources_profile: str | None = "one"
        self.clear_questions = False
        self.home_reads = 0
        self.sources_gate = threading.Event()
        self.sources_gate.set()
        real: Callable[..., Any] = app.handle_api

        def handle(method: str, path: str, query: dict, body: dict) -> Any:
            if method == "GET" and path == "/api/sources":
                self.sources_gate.wait(30)
                payload = real(method, path, query, body)
                rows = [dict(row, needs_attention=False) for row in payload.get("refresh") or []]
                missing = max(0, self.failing - len(rows))
                rows += [{"needs_attention": False} for _ in range(missing)]
                for row in rows[: self.failing]:
                    row["needs_attention"] = True
                return {**payload, "refresh": rows, "profile": self.sources_profile}
            payload = real(method, path, query, body)
            if method == "GET" and path == "/api/home":
                self.home_reads += 1
                payload = {**payload, "profile": self.home_profile}
                if self.clear_questions:
                    payload["declarations_pending"] = []
            return payload

        app.handle_api = handle  # type: ignore[method-assign]


@pytest.fixture
def shaped(tmp_path: Path) -> Iterator[tuple[str, JobsApi, Shaped]]:
    app = _app(tmp_path / "sites", **NEUTRAL)
    shape = Shaped(app)
    for base in _serve(app):
        yield base, app, shape


def _hold_collection(app: JobsApi) -> threading.Event:
    """A collection run that collects nothing and ends when released."""
    release = threading.Event()
    app.retrieval.start(lambda state, cancel: release.wait(30), "held-collection")
    return release


def test_the_sites_row_arrives_after_home_and_redraws_the_card_alone(
    page: Chrome, shaped: tuple[str, JobsApi, Shaped]
) -> None:
    base, _app_, shape = shaped
    shape.failing = 2
    shape.sources_gate.clear()
    open_home_past_setup(page, base)
    page.wait_for("document.querySelector('.home__todo--recalc')", message="Home's tasks")
    assert not page.evaluate(f"Boolean({SITES})"), "no sites row before the status is known"
    rows = int(page.evaluate("document.querySelectorAll('.home__todo').length"))
    reads = shape.home_reads

    shape.sources_gate.set()
    page.wait_for(f"{TODO_TEXT}.includes('Fix 2 job sites')", message="the late sites row")
    assert shape.home_reads == reads, "the card was redrawn by reading Home again"
    count = page.evaluate("document.querySelector('.home__todocount').innerText")
    assert count == f"{rows + 1} left"
    assert not page.evaluate(f"Boolean({TOAST})"), "a task that appears is not a task done"

    page.evaluate(f"{SITES}.click()")
    page.wait_for(
        "!document.getElementById('page-settings').hidden"
        " && document.getElementById('page-settings').dataset.tab === 'sites'",
        message="Settings at the job sites",
    )


def test_a_new_count_updates_the_row_without_calling_it_done(
    page: Chrome, shaped: tuple[str, JobsApi, Shaped]
) -> None:
    base, app, shape = shaped
    shape.failing = 2
    release = _hold_collection(app)
    try:
        open_home_past_setup(page, base)
        page.wait_for(f"{TODO_TEXT}.includes('Fix 2 job sites')")
        shape.failing = 1
    finally:
        release.set()
        app.retrieval.join(10)
    page.wait_for(f"{TODO_TEXT}.includes('Fix a job site')", message="the new count")
    assert not page.evaluate(f"Boolean({TOAST})")


def test_sites_working_again_is_said_once_confirmed(
    page: Chrome, shaped: tuple[str, JobsApi, Shaped]
) -> None:
    base, app, shape = shaped
    shape.failing = 1
    release = _hold_collection(app)
    try:
        open_home_past_setup(page, base)
        page.wait_for(f"{TODO_TEXT}.includes('Fix a job site')")
        shape.failing = 0
    finally:
        release.set()
        app.retrieval.join(10)
    page.wait_for(f"!{SITES}", message="the row gone once the status says so")
    page.wait_for(f"{TODO_TEXT}.includes('✓ Job sites working again')", message="the done line")


def test_another_profiles_answers_never_show_or_close_a_task(
    page: Chrome, shaped: tuple[str, JobsApi, Shaped]
) -> None:
    base, _app_, shape = shaped
    # The sources' status of another profile does not reach this Home.
    shape.failing = 2
    shape.sources_profile = "other"
    open_home_past_setup(page, base)
    page.wait_for(f"{TODO_TEXT}.includes('Where can you work?')")
    page.wait_for(
        "!document.getElementById('health-summary').hidden",
        message="the rail's status, so the sources have answered",
    )
    assert not page.evaluate(f"Boolean({SITES})")

    # Home answered by another profile: its tasks differ, and none is done.
    page.evaluate(f"{_todo_button('Where can you work?')}.click()")
    page.wait_for("!document.getElementById('page-profile').hidden", message="the profile")
    shape.home_profile = "two"
    shape.clear_questions = True
    page.evaluate("document.querySelector('.topnav__link[data-page=\"home\"]').click()")
    page.wait_for(
        "document.querySelector('.home[data-view=dashboard]')"
        f" && !{TODO_TEXT}.includes('Where can you work?')",
        message="Home read for the other profile",
    )
    assert not page.evaluate(f"Boolean({TOAST})"), "a profile switch is not a task done"


def test_a_redraw_keeps_the_recalculation_it_follows(
    page: Chrome, shaped: tuple[str, JobsApi, Shaped]
) -> None:
    base, app, shape = shaped
    shape.failing = 1
    shape.sources_gate.clear()
    release = threading.Event()
    _hold(app, release, done=5, total=20)
    try:
        open_home_past_setup(page, base)
        page.wait_for(f"{RECALC_HELP}.includes('Updating 5 of 20')", message="the progress")
        page.evaluate(f"{RECALC}.dataset.mark = 'kept'")
        shape.sources_gate.set()
        page.wait_for(f"{TODO_TEXT}.includes('Fix a job site')", message="the late sites row")
        assert page.evaluate(f"{RECALC}.dataset.mark") == "kept", "the running row was rebuilt"
        assert "Updating 5 of 20" in str(page.evaluate(RECALC_HELP))
        assert page.evaluate(f"{RECALC}.getAttribute('aria-busy')") == "true"
    finally:
        release.set()
        app.rescore.join(10)

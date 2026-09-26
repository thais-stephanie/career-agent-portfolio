"""A status change is confirmed before anything relies on it.

Changing a job's status and immediately opening Applications used to race: the
board's list request could reach the server before the status save had been
committed, the board was built without the job, and the save's late answer
could only update jobs already on screen -- so the board stayed wrong. It
showed up as an intermittent failure of the persona journeys under load.

These tests make the race deterministic instead of hoping for it: the server's
status route is slowed down (or made to fail) on the test side, which is the
only way to hold the window open, and every assertion then waits on a real
product condition -- the page's own "saving" signal clearing, or the server's
recorded status -- never on elapsed time.
"""

from __future__ import annotations

import json
import socket
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome

from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig, build_server


@dataclass
class Slowed:
    base: str
    db: Path
    #: Seconds each PATCH .../status waits before it runs.
    delay: float = 0.0
    #: When True, every status save is refused instead of made.
    fail: bool = False
    saves: list[str] = field(default_factory=list)


@pytest.fixture
def slowed(tmp_path: Path, demo_db: Path, committed_config: Path) -> Iterator[Slowed]:
    """A private copy of the demo database, served with a controllable status route."""
    db = tmp_path / "status.db"
    source = sqlite3.connect(demo_db)
    target = sqlite3.connect(db)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    state = Slowed(f"http://127.0.0.1:{port}", db)
    original = app.handle_api

    def controlled(method: str, path: str, query: dict, body: dict):
        if method == "PATCH" and path.endswith("/status"):
            # Held on the server, where the real save is: this is what a slow
            # disk or a busy machine does to the same request.
            time.sleep(state.delay)
            if state.fail:
                raise ApiError(500, "synthetic status failure")
            result = original(method, path, query, body)
            state.saves.append(str(body.get("status")))
            return result
        return original(method, path, query, body)

    app.handle_api = controlled  # type: ignore[method-assign]
    httpd = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _open_discover(page: Chrome, base: str) -> str:
    page.navigate(base + "/#jobs")
    page.wait_for("document.querySelector('.card:not(.card--skeleton) .select--status')")
    return str(page.evaluate("document.querySelector('.card:not(.card--skeleton)').dataset.jobId"))


def _set_status(page: Chrome, job_id: str, status: str) -> None:
    page.evaluate(
        f"(() => {{ const s = document.querySelector('.card[data-job-id={json.dumps(job_id)}]"
        f" .select--status'); s.value = {json.dumps(status)};"
        " s.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )


def _server_status(db: Path, job_id: str) -> str | None:
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT status FROM job_application WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    return row[0] if row else None


#: The page's own signal: present while any status save is in flight.
SETTLED = "!document.documentElement.hasAttribute('data-saving')"


def test_opening_applications_mid_save_shows_the_job(page: Chrome, slowed: Slowed) -> None:
    """The fast real user: change a status and click Applications at once."""
    job_id = _open_discover(page, slowed.base)
    slowed.delay = 0.8
    _set_status(page, job_id, "SHORTLISTED")
    assert page.evaluate("document.documentElement.getAttribute('data-saving')") == "status"
    page.evaluate("document.querySelector('.topnav__link[data-page=\"applications\"]').click()")
    page.wait_for(SETTLED)
    page.wait_for(f"document.querySelector('.kcard[data-job-id={json.dumps(job_id)}]')")
    assert _server_status(slowed.db, job_id) == "SHORTLISTED"


def test_rapid_changes_settle_on_the_last_one(page: Chrome, slowed: Slowed) -> None:
    job_id = _open_discover(page, slowed.base)
    slowed.delay = 0.3
    for status in ("SHORTLISTED", "INTERVIEW", "APPLIED"):
        _set_status(page, job_id, status)
    page.wait_for(SETTLED)
    assert slowed.saves == ["SHORTLISTED", "INTERVIEW", "APPLIED"], (
        "saves reached the server out of order"
    )
    assert _server_status(slowed.db, job_id) == "APPLIED"
    shown = page.evaluate(
        f"document.querySelector('.card[data-job-id={json.dumps(job_id)}] .select--status').value"
    )
    assert shown == "APPLIED"


def test_a_failed_save_puts_the_confirmed_status_back(page: Chrome, slowed: Slowed) -> None:
    job_id = _open_discover(page, slowed.base)
    before = page.evaluate(
        f"document.querySelector('.card[data-job-id={json.dumps(job_id)}] .select--status').value"
    )
    slowed.fail = True
    _set_status(page, job_id, "SHORTLISTED")
    page.wait_for(SETTLED)
    shown = page.evaluate(
        f"document.querySelector('.card[data-job-id={json.dumps(job_id)}] .select--status').value"
    )
    assert shown == before, "the control kept a status the server refused"
    assert _server_status(slowed.db, job_id) in (None, before)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"applications\"]').click()")
    page.wait_for("document.querySelector('#list.kanban')")
    assert not page.evaluate(
        f"Boolean(document.querySelector('.kcard[data-job-id={json.dumps(job_id)}]'))"
    )

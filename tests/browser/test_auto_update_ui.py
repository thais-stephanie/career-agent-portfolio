"""The choice "Update jobs when I open the app": off until chosen, asked once
per opening, never replacing what is being read, and visible/stoppable.

The maintenance engine is replaced by a stub that takes a moment and records a
collection that added three jobs, so nothing reaches the network.
"""

# ruff: noqa: F811  (the `live` fixture is imported, then used as an argument)
from __future__ import annotations

import time
from pathlib import Path

from tests.browser.chrome import Chrome
from tests.browser.test_local_profiles import live  # noqa: F401  (the fixture)

SPY = """(() => {
  window.__calls = [];
  const real = window.fetch;
  window.fetch = (u, o) => {
    window.__calls.push(((o && o.method) || 'GET').toUpperCase() + ' ' + String(u));
    return real(u, o);
  };
})()"""


def _calls(page: Chrome, needle: str) -> int:
    return int(page.evaluate(f"window.__calls.filter((c) => c.includes({needle!r})).length"))


def test_off_by_default_then_asked_once_when_the_app_opens_and_it_never_replaces_the_list(
    page: Chrome,
    live: tuple[str, Path],
    monkeypatch,  # noqa: F811
) -> None:
    base, _root = live

    def stub(conn, *, on_progress, **kwargs):
        time.sleep(1.5)
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES ('collect-stub', 'collect', '2026-10-11T00:00:00Z', '2026-10-11T00:00:01Z',"
            " 'OK', '{\"jobs_new\": 3}')"
        )
        conn.commit()
        receipt = {"selected": ["feed:avlis"], "results": [], "active": None}
        return {**receipt, "results": [{"key": "feed:avlis", "outcome": "OK", "attempted": True}]}

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", stub)
    page._cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SPY})
    page.navigate(f"{base}/#settings")
    page.wait_for("document.querySelector('#settab-sites')", message="Settings tabs")
    page.evaluate("document.querySelector('#settab-sites').click()")
    page.wait_for("document.querySelector('.au .dr-switch')", message="the choice")
    assert (
        page.evaluate("document.querySelector('.au .dr-switch').getAttribute('aria-checked')")
        == "false"
    )
    text = str(page.evaluate("document.querySelector('.au').innerText"))
    assert "3 days" in text and "10 minutes" in text and "Off until you turn it on" in text
    assert _calls(page, "/auto-update/run") == 0, "nothing runs because the choice is off"

    page.evaluate("document.querySelector('.au .dr-switch').click()")
    page.wait_for(
        "document.querySelector('.au .dr-switch').getAttribute('aria-checked') === 'true'",
        message="switched on",
    )
    assert _calls(page, "PATCH") >= 1 and _calls(page, "/auto-update/run") == 0, (
        "choosing it starts nothing: it runs the next time the app opens"
    )

    # Opening the app again: asked once. Moving between pages asks nothing.
    page.navigate(f"{base}/#home")
    page.evaluate("location.reload()")
    page.wait_for("window.__calls && window.__calls.length > 0", message="reload")
    page.wait_for("window.__calls.some((c) => c.includes('/auto-update/run'))", message="asked")
    for destination in ("jobs", "applications", "settings", "home"):
        page.evaluate(f"location.hash = {destination!r}")
    assert _calls(page, "/auto-update/run") == 1

    # Visible while it runs, and the list is not replaced when it ends.
    page.wait_for("!document.getElementById('collectbar')?.hidden", message="the progress bar")
    assert "background" in str(page.evaluate("document.getElementById('collectbar').innerText"))
    page.evaluate("location.hash = 'jobs'")
    page.wait_for("document.querySelector('.topnav__link[aria-current=page]')", message="Jobs")
    before = _calls(page, "/api/jobs?")
    page.wait_for(
        "document.querySelector('.toast') && !document.querySelector('.toast').hidden",
        timeout=40,
        message="notice",
    )
    notice = str(page.evaluate("document.querySelector('.toast').innerText"))
    assert "3 new jobs are available" in notice and "Update the list" in notice
    assert _calls(page, "/api/jobs?") == before, "an automatic update never reloads the list itself"
    page.evaluate(
        "Array.from(document.querySelectorAll('.toast button'))"
        ".find((b) => /Update the list/.test(b.innerText)).click()"
    )
    page.wait_for(
        f"window.__calls.filter((c) => c.includes('/api/jobs?')).length > {before}",
        message="list updated on request",
    )


def _put_run(root: Path, stage: str, stats: dict, finished: str = "now") -> None:
    import json
    from datetime import UTC, datetime, timedelta

    from career_agent.clock import new_id
    from career_agent.storage.db import connect, transaction

    moment = datetime.now(UTC) if finished == "now" else datetime.now(UTC) - timedelta(days=4)
    stamp = moment.isoformat(timespec="seconds").replace("+00:00", "Z")
    conn = connect(root / "data" / "personal.db")
    try:
        with transaction(conn):
            conn.execute(
                "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
                " VALUES (?, ?, ?, ?, 'OK', ?)",
                (new_id(), stage, stamp, stamp, json.dumps(stats)),
            )
    finally:
        conn.close()


def test_a_workable_read_that_keeps_its_place_says_so_and_offers_to_continue(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, root = live
    _put_run(
        root,
        "collect-workable",
        {
            "deferred_reason": "TIME_BUDGET",
            "continues": True,
            "postings_seen": 50,
            "jobs_new": 10,
            "jobs_unchanged": 40,
        },
    )
    page.navigate(f"{base}/#settings")
    page.wait_for("document.querySelector('#settab-sites')", message="Settings tabs")
    page.evaluate("document.querySelector('#settab-sites').click()")
    page.wait_for("document.querySelector('.src__site[data-source=workable]')", message="the card")
    card = str(
        page.evaluate("document.querySelector('.src__site[data-source=workable]').textContent")
    )
    assert "read in part" in card and "without downloading the file again" in card
    assert "Continue updating" in card
    assert "Partly updated" in card
    assert "deferred_reason" not in card and "TIME_BUDGET" not in card, "no codes as words"


def test_the_summary_of_an_automatic_update_names_what_workable_did(
    page: Chrome, live: tuple[str, Path], monkeypatch
) -> None:
    base, _root = live

    def stub(conn, *, provider=None, **kwargs):
        if provider == "workable":
            return {
                "selected": ["feed:workable"],
                "results": [
                    {
                        "key": "feed:workable",
                        "outcome": "DEFERRED",
                        "reason": "TIME_BUDGET",
                        "attempted": True,
                        "jobs_new": 7,
                        "jobs_changed": 2,
                        "jobs_unchanged": 90,
                    }
                ],
            }
        return {"selected": [], "results": []}

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", stub)
    page.navigate(f"{base}/#settings")
    page.wait_for("document.querySelector('#settab-sites')", message="Settings tabs")
    page.evaluate("document.querySelector('#settab-sites').click()")
    page.wait_for("document.querySelector('.au .dr-switch')", message="the choice")
    page.evaluate("document.querySelector('.au .dr-switch').click()")
    page.wait_for(
        "document.querySelector('.au .dr-switch').getAttribute('aria-checked') === 'true'",
        message="switched on",
    )
    page.evaluate("location.reload()")
    page.wait_for("document.querySelector('#settab-sites')", message="Settings tabs")
    deadline = time.monotonic() + 40
    text = ""
    while time.monotonic() < deadline and "Workable" not in text:
        time.sleep(1)
        page.evaluate("document.querySelector('#settab-search').click()")
        page.evaluate("document.querySelector('#settab-sites').click()")
        text = str(page.evaluate("document.querySelector('.au__last')?.textContent || ''"))
    assert "Workable: read in part (7 new and 2 changed so far)" in text, text

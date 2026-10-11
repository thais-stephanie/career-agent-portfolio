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

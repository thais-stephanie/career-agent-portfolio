"""Tailor V2 in a real browser (PR 8): the job drawer, the progress screen,
the job panel, Make it better, and manual versus tailored versions.

A synthetic senior profile (`tests.support_tailor`) and the invented demo jobs.
No model is called; nothing here reads anybody's real resume.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import leave_cleanly  # noqa: F401
from tests.integration.test_resume_master import profile
from tests.support_tailor import senior

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

DEMO = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"
AD = (
    "Responsibilities\n- Build workflow automation across HubSpot and Salesforce\n"
    "- Own pipeline reporting for sales leadership\n\nRequirements\n- Experience with HubSpot\n"
    "- Salesforce Apex is a must\n- Experience building integrations with n8n\n"
)


@pytest.fixture
def tailor_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    conn = profile(tmp_path, "p")
    senior(conn)
    seed_demo(conn, load_search_config(committed_config)[0], source=DEMO)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}"}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def open_drawer(page: Chrome, url: str, width: int = 1440) -> None:
    page.set_viewport(width, 960, mobile=width < 500)
    page.navigate(url + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for(
        "document.querySelector('.d-tailor')?.dataset.tailor === 'ready'", message="step 3"
    )


def panel(page: Chrome) -> str:
    page.wait_for("document.querySelector('.rvj__job') !== null", message="the job panel")
    return str(page.evaluate("document.querySelector('.rvj__panel').innerText"))


def test_tailor_from_the_drawer_opens_the_version_and_says_why(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    open_drawer(page, tailor_server["url"])
    step = page.evaluate("document.querySelector('.d-tailor').innerText")
    assert "Tailor from Master" in step and "Create a version for this job" in step
    assert "Built from your confirmed experience without AI." in step
    page.evaluate("document.getElementById('drawer-tailor').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="editor")
    assert page.evaluate("document.querySelector('.rve .rvw__kind').textContent") == (
        "For a job · V1"
    )
    text = panel(page)
    assert "Built from your confirmed experience without AI." in text
    assert "asks have confirmed support" in text or "ask has confirmed support" in text
    assert "Set this as preferred for this job" in text  # offered, never chosen for her
    for internal in ("SHOWN_IN_MASTER", "NO_EVIDENCE", "requirement_id", "k-", "DETERMINISTIC"):
        assert internal not in text, internal
    # The step in the drawer now lists the version, tailored, and offers both acts again.
    page.navigate(tailor_server["url"] + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for("document.querySelector('.d-tailor')?.dataset.tailor === 'versions'")
    step = page.evaluate("document.querySelector('.d-tailor').innerText")
    assert "Tailored" in step and "Tailor another version" in step
    assert "Make another manual version" in step
    assert page.console_errors() == []


def _pasted(page: Chrome, url: str, width: int = 1366) -> None:
    page.set_viewport(width, 900, mobile=width < 500)
    page.navigate(url)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.querySelector('.rvt__paste') !== null", message="the tailor card")
    page.evaluate("document.querySelector('.rvt__paste summary').click()")
    page.evaluate("document.getElementById('rvt-title').value = 'Director of Revenue Operations'")
    page.evaluate(f"document.getElementById('rvt-text').value = {AD!r}")
    page.evaluate("document.querySelector('.rvt__form button[type=\"submit\"]').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="editor")


def test_a_pasted_ad_shows_added_lines_gaps_and_applies_a_suggestion(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _pasted(page, tailor_server["url"])
    text = panel(page)
    assert "Added from your confirmed experience" in text
    assert "Built n8n integrations between HubSpot and the billing system." in text
    assert "Source: your confirmed experience as Revenue Operations Analyst" in text
    assert "Before you apply" in text and "Salesforce Apex is a must" in text
    assert "I couldn’t find these in your confirmed experience." in text
    # Hide the added line: Make it better offers it back, and Apply shows it.
    line = (
        "[...document.querySelectorAll('.rve__line')]"
        ".find((l) => l.querySelector('textarea')?.value.startsWith('Built n8n'))"
    )
    page.evaluate(f"({line}).querySelector('.rve__toggle input').click()")
    show = "document.querySelector('.rvj__suggestion[data-kind=\"UNSHOWN_EVIDENCE\"]')"
    page.wait_for(f"{show} !== null", message="the suggestion")
    assert "Built n8n" in page.evaluate(f"{show}.innerText")
    page.evaluate(f"{show}.querySelector('.btn--primary').click()")
    page.wait_for(f"({line}).dataset.hidden === 'false' && {show} === null", message="applied")
    # A gap has no Apply; Dismiss sets it aside for this version.
    gap = "document.querySelector('.rvj__suggestion[data-kind=\"NO_EVIDENCE\"]')"
    assert page.evaluate(f"{gap}.querySelector('.btn--primary')") is None
    before = page.evaluate("document.querySelectorAll('.rvj__suggestion').length")
    page.evaluate(
        f"[...{gap}.querySelectorAll('button')].find((b) => b.textContent === 'Dismiss').click()"
    )
    page.wait_for(
        f"document.querySelectorAll('.rvj__suggestion').length === {before - 1}",
        message="dismissed",
    )


def test_manual_and_tailored_versions_are_told_apart(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    open_drawer(page, tailor_server["url"])
    page.evaluate("document.getElementById('drawer-open-tailor').click()")  # by hand first
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'")
    page.navigate(tailor_server["url"] + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for("document.getElementById('drawer-tailor') !== null")
    page.evaluate("document.getElementById('drawer-tailor').click()")
    page.wait_for("document.querySelector('.rve .rvw__kind')?.textContent.endsWith('V2')")
    page.evaluate("document.querySelector('.rvw__tab[aria-controls=\"rvw-view-list\"]').click()")
    page.wait_for("document.querySelector('#rvw-view-list .rvl__group') !== null")
    rows = page.evaluate(
        "[...document.querySelectorAll('#rvw-view-list .rvl__group .rvl__row')]"
        ".map((r) => r.querySelector('.rvl__name').innerText)"
    )
    assert len(rows) == 2 and "Tailored" in rows[0] and "Tailored" not in rows[1]


def test_the_job_panel_on_a_phone(page: Chrome, tailor_server: dict[str, Any]) -> None:
    _pasted(page, tailor_server["url"], width=390)
    panel(page)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    labels = page.evaluate(
        "[...document.querySelectorAll('.rvj__suggestion button')]"
        ".map((b) => b.getAttribute('aria-label'))"
    )
    assert labels and all(labels)  # every Apply, Add evidence and Dismiss names what it acts on

# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""Resume Tailor, the direct way in from the sidebar.

With a Master resume: choose a job and adapt it, without leaving the sidebar's
logic. Without one: make it in Resumes, review it, and come back to the SAME
job. Opening the page generates nothing and calls no AI.
"""

from __future__ import annotations

from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.test_first_run_flow import click_button
from tests.browser.test_resume_tailor import tailor_server  # noqa: F401  (a profile with a Master)

SPY = """(() => {
  window.__writes = [];
  const real = window.fetch;
  window.fetch = (u, o) => {
    const m = ((o && o.method) || 'GET').toUpperCase();
    if (m !== 'GET') window.__writes.push(m + ' ' + String(u));
    return real(u, o);
  };
})()"""


def _go(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/#tailor")
    page.wait_for("!document.getElementById('page-tailor').hidden", message="the Tailor page")
    page.wait_for(
        "['pick', 'job', 'needs-master']"
        ".includes(document.getElementById('page-tailor').dataset.state)",
        message="the page drew",
    )


def test_the_sidebar_has_resume_tailor_beside_resumes_and_it_changes_nothing_by_itself(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    page._cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SPY})
    _go(page, tailor_server["url"])
    labels = page.evaluate(
        "[...document.querySelectorAll('.topnav__label')].map((n) => n.innerText)"
    )
    assert labels.index("Resume Tailor") == labels.index("Resumes") - 1
    assert page.evaluate("document.getElementById('pagehead-title').innerText") == "Resume Tailor"
    assert page.evaluate("document.getElementById('page-tailor').dataset.state") == "pick"
    text = str(page.evaluate("document.getElementById('page-tailor').innerText"))
    assert "Which job is it for?" in text and "Best matches" in text
    assert page.evaluate("window.__writes") == [], "opening the page makes and sends nothing"
    assert page.console_errors() == []


def test_choose_a_job_and_adapt_the_master_to_it(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    page.evaluate("document.querySelector('.tp__job').click()")
    page.wait_for("document.getElementById('page-tailor').dataset.state === 'job'", message="job")
    text = str(page.evaluate("document.getElementById('page-tailor').innerText"))
    assert "Adapt resume for this job" in text and "Adapt with AI (asks first)" in text
    assert "no AI" in text
    page.evaluate("document.getElementById('tp-adapt').click()")
    page.wait_for(
        "document.querySelector('.rvw')?.dataset.view === 'editor'"
        " && document.querySelector('.rve .rvw__kind')?.textContent.startsWith('For a job')",
        message="the job's version in the Editor",
    )


def test_without_a_master_make_it_and_come_back_to_the_same_job(
    page: Chrome, pristine_server: str
) -> None:
    page.set_viewport(1440, 960)
    page.navigate(pristine_server)
    page.evaluate(
        "fetch('/api/evidence', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({claim_type: 'SKILL', text: 'Invented spreadsheet modelling'})})"
        ".then(r => r.status)"
    )
    page.navigate(pristine_server + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20, message="jobs")
    title = str(
        page.evaluate(
            "document.querySelector('#list [data-job-id] .card__title,"
            " #list [data-job-id] h3')?.innerText || ''"
        )
    )
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for(
        "document.querySelector('.d-tailor')?.dataset.tailor === 'needs-master'", message="step"
    )
    page.evaluate("document.getElementById('drawer-to-resumes').click()")
    page.wait_for("!document.getElementById('page-resume').hidden", message="Resumes")
    page.wait_for("!document.getElementById('tailor-return').hidden", message="the way back")
    bar = str(page.evaluate("document.getElementById('tailor-return').innerText"))
    assert "Master resume" in bar and "go back" in bar
    click_button(page, "Build from My Profile")
    click_button(page, "Build my Master")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="the Master")
    page.evaluate("document.getElementById('tailor-return-go').click()")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'job'", message="same job"
    )
    assert "Adapt resume for this job" in str(
        page.evaluate("document.getElementById('page-tailor').innerText")
    )
    assert title == "" or title in str(
        page.evaluate("document.getElementById('page-tailor').innerText")
    )

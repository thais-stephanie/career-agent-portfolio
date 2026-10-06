"""Resumes is a page of this app, and the only resume tool (PR 12).

The sidebar opens Resumes (Home, My resumes, Editor, Analyze) in this window,
with no query string. The retired Resume Helper is reachable from nowhere:
not the sidebar, not Settings, not its old address.
"""

from tests.browser.test_browser_acceptance import click, open_list


def test_the_sidebar_opens_resumes_as_a_page_of_this_app(page, pristine_server):
    page.navigate(pristine_server)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    assert page.evaluate("document.querySelector('.topnav__link--tailor').tagName") == "BUTTON", (
        "the sidebar item is not a link to another address"
    )
    assert "debug" not in page.evaluate("window.location.search")
    click(page, "document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.wait_for(
        "!document.getElementById('page-resume').hidden"
        " && document.querySelector('#page-resume .rvw')?.dataset.view === 'home'",
        message="Resumes, at its Home",
    )
    page.wait_for("window.location.hash === '#resume'", message="the address names the page")
    assert page.evaluate("document.getElementById('pagehead-title').textContent") == "Resumes"
    tabs = page.evaluate(
        "[...document.querySelectorAll('#page-resume .rvw__tab')].map(t => t.textContent)"
    )
    assert tabs == ["Home", "My resumes", "Editor", "Analyze"]
    text = page.evaluate("document.getElementById('page-resume').innerText")
    # The old helper's tabs are not here; Analyze is this page's own tab (PR 11).
    assert "Tailor" not in text and text.count("Analyze") == 1
    # The old helper is not a peer in the navigation.
    legacy_links = "document.querySelectorAll('.topnav__link[data-page=\"resume-legacy\"]').length"
    assert page.evaluate(legacy_links) == 0
    assert page.console_errors() == []


def test_the_retired_helper_is_reachable_from_nowhere(page, pristine_server):
    """Settings offers no old helper, and the old helper's own page address
    opens Resumes. With nothing left to move, Settings says nothing about it."""
    page.navigate(pristine_server + "/#settings")
    page.wait_for("!document.getElementById('page-settings').hidden", message="Settings")
    assert page.evaluate("document.getElementById('settings-legacy-open')") is None
    assert page.evaluate("document.getElementById('page-resume-legacy')") is None
    page.wait_for("document.getElementById('settings-legacy-host').hidden")
    text = page.evaluate("document.getElementById('page-settings').innerText")
    assert "Legacy Resume Helper" not in text and "Resume Helper" not in text
    page.navigate(pristine_server + "/#resume-legacy")
    page.evaluate("window.location.reload()")  # a hash alone opens no page; a load does
    page.wait_for(
        "!document.getElementById('page-resume').hidden",
        message="the old helper's page opens Resumes",
    )
    assert page.console_errors() == []


def test_a_jobs_resume_step_leads_to_resumes_in_this_window(page, pristine_server):
    page.navigate(pristine_server)
    page.evaluate(
        "fetch('/api/evidence', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({claim_type: 'SKILL', text: 'Invented spreadsheet modelling'})})"
        ".then(r => r.status)"
    )
    open_list(page, pristine_server)
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')")
    click(page, "document.querySelector('.card:not(.card--skeleton)')")
    click(page, "document.getElementById('drawer-tab-prepare')")
    page.wait_for("document.querySelector('#drawer-to-resumes')")
    href = "document.querySelector('#drawer-to-resumes').getAttribute('href')"
    assert page.evaluate(href) is None
    windows = page.evaluate("window.history.length")
    click(page, "document.getElementById('drawer-to-resumes')")
    page.wait_for(
        "!document.getElementById('page-resume').hidden"
        " && document.querySelector('.drawer').hidden",
        message="Resumes, in this window",
    )
    assert page.evaluate("window.history.length") <= windows + 1
    assert page.evaluate("document.querySelector('.sidenav') !== null"), "the app shell stays"

"""Resumes is a page of this app; the previous Resume Helper is its fallback.

The sidebar opens Resumes (Home, My resumes, Editor) in this window, with no
query string. The previous Resume Helper is still reachable, from Settings
only, and is named for what it is.
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
    assert "Tailor" not in text and "Analyze" not in text
    # The old helper is not a peer in the navigation.
    legacy_links = "document.querySelectorAll('.topnav__link[data-page=\"resume-legacy\"]').length"
    assert page.evaluate(legacy_links) == 0
    assert page.console_errors() == []


def test_the_previous_helper_is_a_fallback_reached_from_settings(page, pristine_server):
    page.navigate(pristine_server)
    page.wait_for("document.getElementById('settings-legacy-open')")
    assert page.evaluate("document.getElementById('settings-legacy-open').textContent") == (
        "Legacy Resume Helper"
    )
    click(page, "document.getElementById('settings-legacy-open')")
    page.wait_for(
        "!document.getElementById('page-resume-legacy').hidden"
        " && document.querySelectorAll('#page-resume-legacy [role=\"tab\"]').length === 5",
        message="the previous helper's five tabs",
    )
    # The test server runs no engine: the helper says so, in words.
    page.wait_for(
        "document.querySelector('#page-resume-legacy .rh-empty')", message="the not-running notice"
    )
    assert page.evaluate("document.getElementById('pagehead-title').textContent") == (
        "Legacy Resume Helper"
    )
    # Nothing has moved here, so the old helper does not say it only shows.
    assert page.evaluate("document.querySelector('#page-resume-legacy > .rve__notice').hidden")


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

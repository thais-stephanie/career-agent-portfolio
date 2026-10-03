"""The Resume helper handoff stays in this window and carries only a posting id."""

from tests.browser.test_browser_acceptance import click, open_list


def test_the_handoff_opens_the_helper_here_with_the_job_chosen(page, pristine_server):
    # The handoff is the next step once Career Agent holds something about the
    # person's career; before that the drawer asks for it first
    # (`test_post_merge_continuity.py`). One confirmed, invented skill.
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
    page.wait_for("document.querySelector('#drawer-open-tailor')")
    # A button of this app: no address, no new tab, no second window.
    assert page.evaluate("document.querySelector('#drawer-open-tailor').tagName") == "BUTTON"
    assert (
        page.evaluate("document.querySelector('#drawer-open-tailor').getAttribute('href')") is None
    )
    windows = page.evaluate("window.history.length")
    click(page, "document.getElementById('drawer-open-tailor')")
    page.wait_for(
        "!document.getElementById('page-resume').hidden"
        " && document.querySelector('.drawer').hidden",
        message="the Resume helper, in this window",
    )
    page.wait_for("window.location.hash === '#resume'", message="the address names the page")
    assert page.evaluate("window.history.length") <= windows + 1
    assert page.evaluate("document.querySelector('.sidenav') !== null"), "the app shell stays"
    # No copy-and-paste step: the helper reads the posting itself.
    assert not page.evaluate("document.body.innerText.includes('Copy job description')")


def test_the_sidebar_opens_the_helper_as_a_page_of_this_app(page, pristine_server):
    """No second app: the sidebar item is a page, and without the engine the
    page says so in words rather than opening an address that is not there."""
    page.navigate(pristine_server)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    assert page.evaluate("document.querySelector('.topnav__link--tailor').tagName") == "BUTTON", (
        "the sidebar item is not a link to another address"
    )
    click(page, "document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.wait_for(
        "!document.getElementById('page-resume').hidden"
        " && document.querySelectorAll('#page-resume [role=\"tab\"]').length === 5",
        message="the helper's five tabs",
    )
    # The test server runs no engine: the helper says so, in words.
    page.wait_for(
        "document.querySelector('#page-resume .rh-empty')", message="the not-running notice"
    )
    assert page.evaluate("document.getElementById('pagehead-title').textContent") == (
        "Resume helper"
    )

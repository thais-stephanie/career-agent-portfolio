"""Every empty screen says what it is for and offers one way forward.

A new person meets Discover, Applications and Career Evidence with nothing in
them. Each used to be either a wall of controls with nothing to act on or
directions to a panel somewhere else; these hold each to one sentence and one
action, on a real fresh install.
"""

from __future__ import annotations

from tests.browser.chrome import Chrome
from tests.browser.test_guided_setup import Fresh, fresh  # noqa: F401 -- the fixture

__all__ = ["fresh"]


def _past_setup(page: Chrome, base: str) -> None:
    page.set_viewport(1280, 900)
    page.navigate(base)
    page.wait_for("document.querySelector('#setup-later')")
    page.evaluate("document.querySelector('#setup-later').click()")
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 6")


def _go(page: Chrome, name: str) -> None:
    page.evaluate(f"document.querySelector('.topnav__link[data-page=\"{name}\"]').click()")


def test_empty_discover_offers_find_jobs_and_hides_the_toolbar(page: Chrome, fresh: Fresh) -> None:
    _past_setup(page, fresh.base)
    _go(page, "jobs")
    page.wait_for("document.querySelector('#empty-find-jobs')")
    toolbar = "getComputedStyle(document.querySelector('.topbar__controls')).display"
    assert page.evaluate(toolbar) == "none"
    page.evaluate("document.querySelector('#empty-find-jobs').click()")
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'ready'")


def test_empty_applications_explains_itself_and_leads_to_discover(
    page: Chrome, fresh: Fresh
) -> None:
    _past_setup(page, fresh.base)
    _go(page, "applications")
    page.wait_for(
        "document.querySelector('#boardempty') && !document.querySelector('#boardempty').hidden"
    )
    assert page.evaluate("document.querySelector('#list').getAttribute('role')") == "list"
    assert not page.evaluate("Boolean(document.querySelector('#list #boardempty'))")
    page.evaluate("document.querySelector('#board-to-discover').click()")
    page.wait_for(
        "document.querySelector('.topnav__link[data-page=\"jobs\"]')"
        ".getAttribute('aria-current') === 'page'"
    )
    assert page.evaluate("document.querySelector('#boardempty').hidden")


def test_empty_evidence_leads_with_importing_a_cv(page: Chrome, fresh: Fresh) -> None:
    _past_setup(page, fresh.base)
    _go(page, "evidence")
    page.wait_for("document.querySelector('#ev-start-import')")
    first = page.evaluate("document.querySelector('#page-evidence .ev > *').className")
    assert "ev__start" in str(first)
    assert not page.evaluate("document.querySelector('.ev__sources').open")
    page.evaluate("document.querySelector('#ev-start-import').click()")
    # Headless Chrome opens no file dialog, but the picker's section is shown.
    page.wait_for("document.querySelector('.ev__sources').open === true")
    # The worked example sits one click away instead of above the first button.
    assert page.evaluate("Boolean(document.querySelector('.career__more'))")
    assert not page.evaluate("document.querySelector('.career__more').open")

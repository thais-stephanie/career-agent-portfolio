"""Every empty screen says what it is for and offers one way forward.

A new person meets Discover, Applications and Evidence with nothing in
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
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 5")


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
    page.wait_for("document.querySelector('#page-evidence .evp-empty #evp-import')")
    # No grid of empty sections: one card that says there is nothing yet.
    assert page.evaluate("document.querySelectorAll('#page-evidence .evp-section').length") == 0
    page.evaluate("document.querySelector('#evp-import').click()")
    page.wait_for("!document.querySelector('#page-documents').hidden")
    page.wait_for("document.querySelector('#page-documents #docs-file')")
    assert page.console_errors() == []


def test_each_source_stays_open_while_a_source_is_changed(page: Chrome, fresh: Fresh) -> None:
    """Settings folds the per-source cards; changing one redraws the panel,
    and the fold must not snap shut under the control that was just used."""
    _past_setup(page, fresh.base)
    _go(page, "settings")
    page.wait_for("document.querySelector('[data-source=gupy] select')")
    page.evaluate("document.querySelector('.src__each').open = true")
    page.evaluate(
        "(() => { const s = document.querySelector('[data-source=gupy] select');"
        " s.value = 'PAUSED'; s.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )
    # The sentence only a REDRAWN card carries: the panel really was rebuilt.
    page.wait_for("document.querySelector('[data-source=gupy]').textContent.includes('by you')")
    assert page.evaluate("document.querySelector('.src__each').open")

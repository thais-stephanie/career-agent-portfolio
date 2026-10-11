"""Every new person on this computer gets the guided setup, and keeps their own.

Profiles share one browser origin, so the browser's memory (the setup a person
finished or postponed, the card they left it on) was shared too: finishing the
setup wrote one flag for everybody, and the second person opened with a Home of
stacked forms and "Welcome back" instead of their own onboarding. Browser
memory about a person is now scoped to the profile.
"""

from __future__ import annotations

from pathlib import Path

from tests.browser.chrome import Chrome
from tests.browser.test_local_profiles import live  # noqa: F401  (the fixture)

LATER = "careerAgent.setup.later.v1"
SETUP_OR_HOME = "['setup', 'dashboard'].includes(document.querySelector('.home')?.dataset.view)"


def _view(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.home')?.dataset.view || ''"))


def _active(page: Chrome) -> str:
    return str(
        page.evaluate("(async () => (await (await fetch('/api/profiles')).json()).active.id)()")
    )


def _scoped(page: Chrome) -> str:
    return str(page.evaluate(f"import('/js/profile-scope.js').then((m) => m.scopedKey({LATER!r}))"))


def _create(page: Chrome, name: str) -> None:
    page.evaluate("document.getElementById('lprof-trigger').click()")
    page.wait_for("document.getElementById('lprof-action-create')", message="new profile action")
    page.evaluate("document.getElementById('lprof-action-create').click()")
    page.wait_for("document.getElementById('lprof-new')", message="new profile field")
    page.evaluate(
        f"(() => {{ const b = document.getElementById('lprof-new'); b.value = {name!r};"
        " b.dispatchEvent(new Event('input', { bubbles: true })); return true; })()"
    )
    page.evaluate("document.getElementById('lprof-create').click()")
    page.wait_for(
        f"document.querySelector('.lprof__name')?.innerText === {name!r}",
        message="the page to reload on the new profile",
    )


def test_a_new_profile_gets_the_setup_whatever_the_first_person_did(
    page: Chrome,
    live: tuple[str, Path],  # noqa: F811
) -> None:
    base, _root = live
    page.navigate(base)
    page.wait_for(SETUP_OR_HOME, message="first Home")
    first = _active(page)
    assert _scoped(page) == f"{LATER}@{first}"

    # What every earlier build wrote for everybody when a setup was finished or
    # postponed. It must not postpone the next person, or this one.
    page.evaluate(f"localStorage.setItem({LATER!r}, '1')")
    page.navigate(base)
    page.wait_for(SETUP_OR_HOME, message="first Home again")
    assert _view(page) == "setup", "a flag written for nobody in particular postpones nobody"

    _create(page, "Synthetic B")
    page.wait_for(SETUP_OR_HOME, message="Home on profile B")
    second = _active(page)
    assert second != first and _scoped(page) == f"{LATER}@{second}"
    assert _view(page) == "setup", "the new person is offered the guided setup"
    title = str(page.evaluate("document.querySelector('.pagehead__title')?.innerText")).casefold()
    assert "welcome back" not in title

    # The first person's own answer is theirs: it does not reach the second.
    page.evaluate(f"localStorage.setItem({(LATER + '@' + first)!r}, '1')")
    page.navigate(base)
    page.wait_for(SETUP_OR_HOME, message="Home on profile B again")
    assert _view(page) == "setup"
    keys = page.evaluate("Object.keys(localStorage).filter(k => k.includes('setup.at'))")
    assert f"careerAgent.setup.at.v1@{second}" in keys and "careerAgent.setup.at.v1" not in keys
    assert all(k.endswith((f"@{first}", f"@{second}")) for k in keys), "every key names its profile"

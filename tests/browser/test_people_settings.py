# ruff: noqa: F811  (the `live` fixture is imported, then used as an argument)
"""Settings, People on this computer: everyone listed, the open profile marked.

The tab used to list only profiles that could be deleted, so a person alone on
the computer saw a paragraph and nothing else, and the explanation sat in a
narrow column of small type.
"""

from __future__ import annotations

from pathlib import Path

from tests.browser.chrome import Chrome
from tests.browser.test_local_profiles import live  # noqa: F401  (the fixture)
from tests.browser.test_profile_onboarding import _create


def _open_people(page: Chrome, base: str) -> None:
    page.navigate(f"{base}/#settings")
    page.wait_for("document.querySelector('#settab-profiles')", message="Settings tabs")
    page.evaluate("document.querySelector('#settab-profiles').click()")
    page.wait_for("document.querySelector('.lprof__srow')", message="the people list")


def _rows(page: Chrome) -> list[str]:
    return list(
        page.evaluate("[...document.querySelectorAll('.lprof__srow')].map((r) => r.innerText)")
    )


def test_one_person_sees_themselves_the_explanation_and_no_delete_form(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, _root = live
    page.set_viewport(1440, 900)
    _open_people(page, base)
    rows = _rows(page)
    assert len(rows) == 1 and "My profile" in rows[0] and "Current profile" in rows[0]
    assert "Rename" in rows[0] and "Switch" not in rows[0]
    assert page.evaluate("document.getElementById('lprof-delete-which') === null")
    text = str(page.evaluate("document.querySelector('#settings-profiles-host').innerText"))
    assert "not a security lock" in text and "no other profile that can be deleted" in text
    # Text at reading width, not squeezed into a narrow column.
    assert (
        page.evaluate("document.querySelector('.lprof__explain').getBoundingClientRect().width")
        > 560
    )
    assert (
        page.evaluate("document.querySelector('.lprof__explain').getBoundingClientRect().height")
        < 100
    )


def test_rename_in_place_then_several_people_all_listed_and_switchable(
    page: Chrome, live: tuple[str, Path]
) -> None:
    base, _root = live
    _open_people(page, base)
    page.evaluate("document.querySelector('.lprof__srow .lprof__sbtn').click()")
    page.wait_for("document.getElementById('lprof-srename')", message="the rename field")
    page.evaluate(
        "(() => { const b = document.getElementById('lprof-srename'); b.value = 'Synthetic A';"
        " b.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));"
        " return true; })()"
    )
    page.wait_for(
        "document.querySelector('.lprof__srow .lprof__rowname')?.innerText === 'Synthetic A'",
        message="renamed in place",
    )
    assert page.evaluate("document.querySelector('.lprof__name').innerText") == "Synthetic A"

    page.navigate(base)
    page.wait_for("document.getElementById('lprof-trigger')", message="the control")
    _create(page, "Synthetic B")
    _open_people(page, base)
    rows = _rows(page)
    assert len(rows) == 2
    current = [r for r in rows if "Current profile" in r]
    other = [r for r in rows if "Current profile" not in r]
    assert len(current) == 1 and "Synthetic B" in current[0]
    assert len(other) == 1 and "Synthetic A" in other[0] and "Switch" in other[0]
    # With another profile there is still only the non-original one to delete, and the
    # original is protected: the delete form offers nobody here.
    assert page.evaluate("document.getElementById('lprof-delete-which') === null")
    page.evaluate(
        "[...document.querySelectorAll('.lprof__srow .lprof__sbtn')]"
        ".find((b) => /Switch/.test(b.innerText)).click()"
    )
    page.wait_for(
        "document.querySelector('.lprof__name')?.innerText === 'Synthetic A'", message="switched"
    )

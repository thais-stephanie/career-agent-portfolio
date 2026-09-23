"""Reaching the six-step Home on a fresh install, past the guided setup.

A fresh install opens on the guided setup (`js/setup.js`). The tests that are
about the six-step list on Home press "Do this later" first, which is exactly
what a person who wants that list does; the setup itself is covered by
`test_guided_setup.py`.
"""

from __future__ import annotations

from tests.browser.chrome import Chrome


def open_home_past_setup(page: Chrome, base: str) -> None:
    page.navigate(base)
    page.wait_for(
        "document.querySelector('#setup-later')"
        " || document.querySelectorAll('.firstrun__step').length === 6",
        message="the guided setup or the six steps",
    )
    if page.evaluate("Boolean(document.querySelector('#setup-later'))"):
        page.evaluate("document.querySelector('#setup-later').click()")
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 6", message="six steps")

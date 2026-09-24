"""Reaching Home past the guided setup.

A fresh install opens on the guided setup (`js/setup.js`). The tests that are
about the setup list on Home press "Do this later" first, which is exactly
what a person who wants that list does; the setup itself is covered by
`test_guided_setup.py`.

Home lists only the setup steps still open when it is arrived at -- all six on
a fresh install, fewer once some are answered, none once all are -- so this
waits for Home itself rather than for a number of rows.
"""

from __future__ import annotations

from tests.browser.chrome import Chrome


def open_home_past_setup(page: Chrome, base: str) -> None:
    page.navigate(base)
    page.wait_for(
        "document.querySelector('#setup-later') || document.querySelector('.home__head')",
        message="the guided setup or Home",
    )
    if page.evaluate("Boolean(document.querySelector('#setup-later'))"):
        page.evaluate("document.querySelector('#setup-later').click()")
    page.wait_for("document.querySelector('.home__head')", message="Home")

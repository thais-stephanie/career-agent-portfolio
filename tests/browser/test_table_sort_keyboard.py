"""A table header sorts from the keyboard: ascending, descending, then back.

The header is a button, so Enter and Space work as they do on any button;
`aria-sort` says the direction to a screen reader, and the third press puts
the list back in its own order (best match first).
"""

from __future__ import annotations

from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import open_list

HEADER = "document.querySelectorAll('table.jobs thead th')[1]"
SORTED = f"{HEADER}.getAttribute('aria-sort')"


def _press_on_header(page: Chrome, key: str) -> None:
    page.evaluate(f"{HEADER}.querySelector('button').focus()")
    page.press(key)


def test_a_header_sorts_from_the_keyboard_and_goes_back(page: Chrome, server: str) -> None:
    open_list(page, server, "?view=table")
    page.wait_for("document.querySelector('table.jobs tbody tr')", message="the table")
    assert page.evaluate(SORTED) == "none"
    _press_on_header(page, "Enter")
    page.wait_for(f"{SORTED} === 'ascending'", message="ascending after Enter")
    assert "sort=title" in str(page.evaluate("location.search"))
    titles = page.evaluate(
        "[...document.querySelectorAll('table.jobs tbody .col--title')].map((n) => n.textContent)"
    )
    assert titles == sorted(titles, key=str.casefold)
    _press_on_header(page, "Space")
    page.wait_for(f"{SORTED} === 'descending'", message="descending after Space")
    _press_on_header(page, "Enter")
    page.wait_for(f"{SORTED} === 'none'", message="back to the list's own order")
    assert "sort=title" not in str(page.evaluate("location.search"))
    assert page.evaluate(
        "document.activeElement && document.activeElement.closest('thead') !== null"
    ), "focus stays on the header row after the list redraws"

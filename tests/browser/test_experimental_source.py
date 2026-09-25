"""The experimental LinkedIn card: warning first, a deliberate tick, and the
demo refusing to switch anything on."""

from __future__ import annotations

from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import open_list


def _open_settings(page: Chrome, server: str) -> None:
    open_list(page, server)
    page.evaluate(
        "(() => { document.querySelector('.topnav__link[data-page=\"settings\"]').click();"
        " return true; })()"
    )
    page.wait_for(
        "document.querySelector('[data-experimental=\"linkedin_br\"]')",
        message="the experimental source card",
    )


def test_the_card_warns_in_plain_words_and_needs_a_deliberate_tick(
    page: Chrome, server: str
) -> None:
    _open_settings(page, server)
    card = "document.querySelector('[data-experimental=\"linkedin_br\"]')"
    text = str(page.evaluate(f"{card}.innerText")).casefold()
    for words in (
        "Experimental, optional",
        "restricts automated collection",
        "off by default",
        "block",
        "partial",
        "never signs in",
        "password",
    ):
        assert words.casefold() in text, words
    assert "off for this profile" in text

    # Without the tick nothing is sent and the card says why.
    page.evaluate(f"{card}.querySelector('#experimental-toggle-linkedin_br').click()")
    page.wait_for(f"{card}.querySelector('[role=status]').innerText.includes('Tick the box')")

    # With it, the demo still refuses: it never collects live jobs.
    page.evaluate(f"{card}.querySelector('#experimental-ack-linkedin_br').click()")
    page.evaluate(f"{card}.querySelector('#experimental-toggle-linkedin_br').click()")
    page.wait_for(f"{card}.querySelector('[role=status]').innerText.includes('demo')")
    assert "Off for this profile" in str(page.evaluate(f"{card}.innerText"))

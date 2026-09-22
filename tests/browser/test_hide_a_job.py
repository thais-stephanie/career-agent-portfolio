"""Hiding one posting by hand, and getting it back.

The fourth reason a job is off the screen, and the only one she chose. What
these tests are about is the difference between a control that works and a
control that LOOKS like it works: the first version hid the representative of
a grouped card and promoted a sibling into the same position, so the card came
back almost identical and nothing said anything had happened.

`pristine_server` rather than the shared one, because these mutate rows the
rest of the suite counts.
"""

from __future__ import annotations

import json

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import (
    A_REAL_CARD,
    RENDERED_COUNT,
    RENDERED_IDS,
    open_list,
)

NOTICE = "document.getElementById('hiddennotice')"
FLASH = "document.getElementById('flash')"


def first_card_hide(page: Chrome) -> None:
    page.evaluate("document.querySelector('.cards .card .card__hide').click()")


def rendered(page: Chrome) -> list[str]:
    return list(page.evaluate(RENDERED_IDS))


@pytest.fixture(autouse=True)
def _list_is_drawn(page: Chrome, pristine_server: str):
    open_list(page, pristine_server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    yield


def test_hiding_a_card_removes_it_and_offers_the_way_back(
    page: Chrome, pristine_server: str
) -> None:
    before = rendered(page)
    assert before, "nothing was rendered, so this test proves nothing"

    first_card_hide(page)
    page.wait_for(
        f"{RENDERED_COUNT} > 0 && {RENDERED_IDS}[0] !== {before[0]!r}",
        message="the list to be redrawn without the hidden posting",
    )

    after = rendered(page)
    assert before[0] not in after, "the hidden posting is still on screen"
    # THE WAY BACK IS OFFERED IMMEDIATELY, not only in a rail somewhere.
    assert page.evaluate(f"{FLASH}.hidden === false"), "nothing was said"
    assert page.evaluate(f"Boolean({FLASH}.querySelector('.flash__undo'))"), (
        "hiding offered no undo, so a mis-click is only recoverable by hunting"
    )


def test_undo_puts_exactly_that_posting_back(page: Chrome, pristine_server: str) -> None:
    before = rendered(page)
    first_card_hide(page)
    page.wait_for(f"Boolean({FLASH}.querySelector('.flash__undo'))", message="the undo")

    page.evaluate(f"{FLASH}.querySelector('.flash__undo').click()")
    page.wait_for(
        f"{RENDERED_IDS}.includes({before[0]!r})",
        message="the posting to come back",
    )

    assert rendered(page) == before, "undo restored a different list"


def test_the_notice_counts_what_she_hid_apart_from_what_was_hidden_for_her(
    page: Chrome, pristine_server: str
) -> None:
    """THREE SENTENCES, NEVER ONE NUMBER.

    An employer stating a requirement, a search setting work aside and a
    person closing a card are three different facts with three different ways
    back. One line saying "5 hidden" would be a count of things she can only
    restore three separate ways.
    """
    rows_before = int(page.evaluate(f"{NOTICE}.querySelectorAll('.hidden__row').length"))

    first_card_hide(page)
    page.wait_for(
        f"{NOTICE}.querySelectorAll('.hidden__row').length === {rows_before + 1}",
        message="a third notice row",
    )

    text = str(page.evaluate(f"{NOTICE}.innerText"))
    assert "1" in text
    # And the row carries BOTH ways back: reveal them here, or look at them
    # on their own.
    last = f"{NOTICE}.querySelectorAll('.hidden__row')[{rows_before}]"
    buttons = page.evaluate(
        f"Array.from({last}.querySelectorAll('button')).map((n) => n.className)"
    )
    assert sum(1 for name in buttons if "hidden__show" in str(name)) == 2, buttons


def test_the_notice_can_be_dismissed_without_revealing_anything(
    page: Chrome, pristine_server: str
) -> None:
    """The X stops the SENTENCE, never the state.

    A dismiss that also revealed the postings would be a control whose label
    says one thing and whose effect is the opposite.
    """
    before = rendered(page)
    first_card_hide(page)
    # Other notice rows can already contain "1" before this mutation returns.
    # Wait for this hide's row and the settled population, not that shared digit.
    page.wait_for(
        f"Array.from({NOTICE}.querySelectorAll('.hidden__row'))"
        ".some((row) => row.querySelectorAll('.hidden__show').length === 2)"
        f" && {RENDERED_COUNT} > 0 && !{RENDERED_IDS}.includes({before[0]!r})",
        message="the completed hide and its own notice",
    )
    hidden_ids = rendered(page)

    page.evaluate(
        f"Array.from({NOTICE}.querySelectorAll('.hidden__row'))"
        ".find((row) => row.querySelectorAll('.hidden__show').length === 2)"
        ".querySelector('.hidden__dismiss').click()"
    )
    page.wait_for(
        f"!{NOTICE}.innerText.includes('deixou de lado')"
        f" && !{NOTICE}.innerText.includes('set them aside')"
        f" && !{NOTICE}.innerText.includes('set it aside')",
        message="the notice to go quiet",
    )

    assert rendered(page) == hidden_ids, (
        "dismissing the sentence changed the population it described"
    )


def test_the_restore_view_shows_only_what_she_set_aside(page: Chrome, pristine_server: str) -> None:
    first_card_hide(page)
    # WAIT FOR THE ROW WITH TWO BUTTONS, not for the digit 1 anywhere in the
    # notice.
    #
    # `include_user_hidden` is the only narrowing whose row carries a second
    # way out -- "see just those" -- because its population is a list of
    # individual decisions rather than a rule. Waiting on "1" used to be
    # equivalent, because hers was the only row that could say it. Since
    # demo-021 the OFF-TARGET row says "1" as well, and it says so before this
    # hide has registered, so the wait finished on somebody else's sentence and
    # `.at(-1)` was a row with one button.
    page.wait_for(
        f"Array.from({NOTICE}.querySelectorAll('.hidden__row'))"
        ".some((row) => row.querySelectorAll('.hidden__show').length === 2)",
        message="the notice about what she hid, with its restore-view button",
    )

    page.evaluate(
        f"Array.from({NOTICE}.querySelectorAll('.hidden__row'))"
        ".find((row) => row.querySelectorAll('.hidden__show').length === 2)"
        ".querySelectorAll('.hidden__show')[1].click()"
    )
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the restore view")

    # Every card in it offers to put itself back, and none offers to hide.
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.cards .card__hide')).map((n) => n.className)"
    )
    assert labels, "the restore view drew no cards"
    assert all("is-hidden" in str(name) for name in labels), labels


def test_hiding_a_grouped_card_takes_the_whole_role(page: Chrome, pristine_server: str) -> None:
    """The defect that was found by looking, not by a test.

    A grouped card stands for every posting an employer published for one
    role. Hiding one of them promoted a sibling into the same position on
    screen, and the card came back looking almost identical.
    """
    grouped = page.evaluate(
        "(() => { const card = Array.from(document.querySelectorAll('.cards .card'))"
        ".find((n) => n.querySelector('.card__group'));"
        " return card ? card.dataset.jobId : null; })()"
    )
    assert grouped, "the demo corpus drew no grouped card, so this proves nothing"
    # The whole selector as one JSON string. An unquoted attribute value is
    # not a valid selector, and a Python repr would supply the wrong quote.
    selector = json.dumps(f'[data-job-id="{grouped}"]')
    node = f"document.querySelector({selector})"
    title = str(page.evaluate(f"{node}.querySelector('.card__title').textContent"))

    page.evaluate(f"{node}.querySelector('.card__hide').click()")
    page.wait_for(
        "!Array.from(document.querySelectorAll('.cards .card__title'))"
        f".some((n) => n.textContent === {title!r})",
        message="the whole role to leave the list",
    )

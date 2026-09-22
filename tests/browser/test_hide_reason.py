"""Saying why a posting was set aside: offered, never demanded.

WHAT IT BUYS
------------
The recommendation audit on 2026-09-08 could measure that 92 of the owner's
top 100 sit in the WEAK band. It could not tell a posting that is wrong for
WHERE IT IS from one that is wrong for WHAT THE WORK IS, and those two want
completely different corrections. Only she knows which, one posting at a time.

WHAT IT MUST NOT COST
---------------------
Hiding stays one click. A control that interrogates somebody for setting one
job aside is a control they stop using, and most hides have no reason worth
recording. The offer comes AFTER the hide has already happened.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import A_REAL_CARD, RENDERED_COUNT, open_list

FLASH = "document.getElementById('flash')"
REASONS = f"{FLASH}.querySelectorAll('.flash__reason')"


@pytest.fixture
def hidden(page: Chrome, pristine_server: str):
    """One posting set aside. `pristine_server` because this mutates rows the
    rest of the suite counts."""
    open_list(page, pristine_server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    before = int(page.evaluate(RENDERED_COUNT))
    page.evaluate("document.querySelector('.cards .card .card__hide').click()")
    page.wait_for(f"{RENDERED_COUNT} === {before - 1}", message="the posting to leave")
    page.wait_for(f"Boolean({FLASH}) && !{FLASH}.hidden", message="the flash")
    yield


def test_the_posting_is_hidden_before_anything_is_asked(page: Chrome, hidden) -> None:
    """**One click.** The offer below is a follow-up, not a condition."""
    text = str(page.evaluate(f"{FLASH}.textContent"))

    assert "undo" in text.lower() or "desfazer" in text.lower(), text


def test_the_reasons_are_offered_after_the_fact(page: Chrome, hidden) -> None:
    labels = page.evaluate(f"Array.from({REASONS}).map((b) => b.textContent.trim())")

    assert labels, "no reason was offered at all"
    assert len(labels) >= 6, labels
    # The two the audit most needed and could not distinguish, first.
    assert "work" in labels[0].lower() or "trabalho" in labels[0].lower(), labels
    assert "place" in labels[1].lower() or "lugar" in labels[1].lower(), labels


def test_choosing_one_records_it_and_stops_asking(page: Chrome, hidden) -> None:
    """Repeating the offer after she has answered is asking twice."""
    page.evaluate(f"{REASONS}[0].click()")
    page.wait_for(
        f"Boolean({FLASH}) && !{FLASH}.hidden && {REASONS}.length === 0",
        message="the offer to be withdrawn once answered",
    )

    text = str(page.evaluate(f"{FLASH}.textContent"))
    assert "undo" in text.lower() or "desfazer" in text.lower(), (
        "the way back disappeared along with the question"
    )


def test_the_posting_stays_hidden_whether_or_not_she_answers(
    page: Chrome, hidden, pristine_server: str
) -> None:
    """The reason is a note about a decision already made."""
    shown = int(page.evaluate(RENDERED_COUNT))
    page.evaluate(f"{REASONS}[0].click()")
    page.wait_for(f"{REASONS}.length === 0", message="the answer to land")

    assert int(page.evaluate(RENDERED_COUNT)) == shown


def test_restoring_offers_no_reason(page: Chrome, hidden) -> None:
    """A reason attached to a posting that is no longer hidden is a note about
    a decision that was reversed, and the route refuses one."""
    page.evaluate(f"{FLASH}.querySelector('.flash__undo').click()")
    page.wait_for(
        f"{FLASH}.hidden === false && {REASONS}.length === 0",
        message="the restore flash",
    )

    text = str(page.evaluate(f"{FLASH}.textContent"))
    assert "why" not in text.lower() and "por qu" not in text.lower(), text

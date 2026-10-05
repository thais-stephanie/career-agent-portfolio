"""The AI grounding reader around a number (PR 10, phase 0).

A rewrite may change joining words, punctuation and inflection around an
amount; it may not change what the amount counts or measures, nor add a
qualifier the source does not hold. Pure function, no database.
"""

from __future__ import annotations

import pytest

from career_agent.resume_doc.tailor import grounding


def checks(source: str, text: str) -> set[str]:
    return {c for c, _ in grounding(text, source, {"hubspot"}, ai=True)}


@pytest.mark.parametrize(
    ("source", "text"),
    [
        ("Built 4 regional teams in HubSpot.", "Built 4 sales teams in HubSpot."),
        ("Supported 4 enterprise accounts.", "Supported 4 customer accounts."),
        ("Reduced processing time by 30%.", "Reduced operating costs by 30%."),
        ("Reduced processing time 30%.", "Reduced operating costs 30%."),
        ("Built a $2M pipeline.", "Built a $2M revenue."),
        ("Supported 12 clinics in Ohio.", "Supported 12 hospitals in Ohio."),
        ("Built 4 regional teams in HubSpot.", "Built 4 regional sales teams in HubSpot."),
        ("Cut data entry by 30%.", "Cut data entry for 30% of teams."),
    ],
)
def test_what_a_number_counts_cannot_change(source: str, text: str) -> None:
    assert "NUMBERS" in checks(source, text)


@pytest.mark.parametrize(
    ("source", "text"),
    [
        ("Built 4 regional teams in HubSpot.", "Built 4 teams in HubSpot."),
        ("Built 4 regional sales teams in HubSpot.", "Built 4 regional sales teams in HubSpot."),
        ("Supported 12 clinics in Ohio.", "Supported 12 clinics."),
        ("Cut manual data entry by 30%.", "Cut manual data entry by 30 percent."),
        ("Cut manual data entry by 30%.", "Cut manual data entry by 30%, using HubSpot."),
    ],
)
def test_a_number_may_keep_its_phrase_in_other_words(source: str, text: str) -> None:
    assert "NUMBERS" not in checks(source, text)

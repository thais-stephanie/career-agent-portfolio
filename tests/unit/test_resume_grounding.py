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
        # Review round 1: a bare number, a swapped measure, a cut-off partitive.
        ("Built n8n integrations for 4 teams.", "Built n8n integrations: 4."),
        ("Built a $2M pipeline.", "Revenue: $2M."),
        (
            "Reduced processing time by 3 days; tracked operating costs.",
            "Reduced operating costs by 3 days.",
        ),
        ("Led 4 teams of engineers.", "Led 4 teams of designers."),
        ("Apoiei 4 equipes de vendas.", "Apoiei 4 equipes de suporte."),
        ("Led four regional teams.", "Led four sales teams."),
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
        ("Apoiei 4 equipes regionais.", "Apoiei 4 equipes."),
        ("Reduced manual processing time by 30%.", "Reduced processing time by 30%."),
        ("Organizei as planilhas de pedidos.", "Organizei uma planilha de pedidos."),
    ],
)
def test_a_number_may_keep_its_phrase_in_other_words(source: str, text: str) -> None:
    assert "NUMBERS" not in checks(source, text)

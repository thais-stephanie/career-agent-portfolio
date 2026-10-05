"""The AI grounding reader around a number (PR 10, phase 0).

A rewrite may change joining words, punctuation and inflection around an
amount; it may not change what the amount counts or measures, nor add a
qualifier the source does not hold. Pure function, no database.
"""

from __future__ import annotations

import pytest

from career_agent.resume_doc.tailor import grounding

#: Two true statements of one role; a number may not move from one to the other.
TWO = (
    "Built lead routing in HubSpot for 4 regional teams. "
    "I built n8n integrations between HubSpot and the billing system."
)
N8N = "built n8n integrations between HubSpot and the billing system"


def checks(source: str, text: str) -> set[str]:
    return {c for c, _ in grounding(text, source, {"hubspot", "n8n"}, ai=True)}


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
        # Round 2: the head kept, an article after a partitive, another sentence.
        ("Supported 200 hospital staff.", "Supported 200 hospitals."),
        ("Led 4 regional sales team leads.", "Led 4 regional sales teams."),
        ("Treinei 200 funcionarios de hospitais.", "Treinei 200 hospitais."),
        ("Led 4 teams of engineers.", "Led 4 teams of the designers."),
        # Round 3: a number joined to another sentence's work across punctuation.
        (
            TWO,
            f"For 4 regional teams, {N8N}.",
        ),
        (
            TWO,
            f"4 regional teams: {N8N}.",
        ),
        (
            "Built lead routing for 4 regional teams. Built n8n integrations.",
            "Built n8n integrations for 4 regional teams.",
        ),
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
        (
            "Cut manual data entry by 30% in HubSpot.",
            "Cut manual data entry by 30%, using HubSpot.",
        ),
        ("Reduced manual processing time by 30%.", "Reduced processing time by 30%."),
        ("Organizei as planilhas de pedidos.", "Organizei uma planilha de pedidos."),
        ("Configured GA4 dashboards for marketing.", "Configured marketing dashboards in GA4."),
        ("Built n8n integrations with HubSpot.", "Built n8n workflows with HubSpot."),
    ],
)
def test_a_number_may_keep_its_phrase_in_other_words(source: str, text: str) -> None:
    assert "NUMBERS" not in checks(source, text)


@pytest.mark.parametrize(
    "text",
    [
        f"{N8N}. For 4 regional teams.",
        f"{N8N}. Used by 4 regional teams.",
        f"{N8N}. Built for 4 regional teams.",
        f"{N8N}! 4 regional teams.",
        f"{N8N}, i.e. 4 regional teams.",
        # The price of the rule, the safe way: a true second sentence that only
        # restates the number with an allowed verb is refused too.
        f"{N8N}. Supported 4 regional teams.",
    ],
)
def test_a_number_never_stands_alone_beside_other_work(text: str) -> None:
    assert "NUMBERS" in checks(TWO, text)


def test_a_head_first_phrase_may_not_lose_its_qualifier_yet() -> None:
    """Known limit, the safe way: the head is read as the last word (or the
    one before a partitive), so Portuguese "4 equipes regionais" cannot drop
    "regionais". Refused, never wrongly accepted."""
    assert "NUMBERS" in checks("Apoiei 4 equipes regionais.", "Apoiei 4 equipes.")

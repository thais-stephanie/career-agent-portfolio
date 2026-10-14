"""Section headings as CONTEXT, in English and Portuguese, however written.

A heading says what the sentences under it are for. It is evidence of
context and never, on its own, of a core duty (owner's decision,
2026-10-14): the section that promotes a hit is still the configured one.
"""

from __future__ import annotations

import pytest

from career_agent.matching_v2.context import Context, heading_context, sentences


@pytest.mark.parametrize(
    ("line", "kind"),
    [
        ("## Responsibilities", Context.RESPONSIBILITY),
        ("**What you'll do:**", Context.RESPONSIBILITY),
        ("What You’ll Be Doing", Context.RESPONSIBILITY),
        ("Responsibilities and Duties", Context.RESPONSIBILITY),
        ("Your role", Context.RESPONSIBILITY),
        ("RESPONSABILIDADES E ATRIBUIÇÕES", Context.RESPONSIBILITY),
        ("O que você vai fazer?", None),  # a question mark ends a sentence
        ("O que você vai fazer", Context.RESPONSIBILITY),
        ("Principais atividades:", Context.RESPONSIBILITY),
        ("<h3>Requisitos</h3>", Context.REQUIREMENT),
        ("Requirements for this role", Context.REQUIREMENT),
        ("Requisitos e qualificações", Context.REQUIREMENT),
        ("Nice to have", Context.REQUIREMENT),
        ("Diferenciais", Context.REQUIREMENT),
        ("1. Benefits", Context.BENEFIT),
        ("• Benefícios", Context.BENEFIT),
        ("Por que trabalhar conosco", Context.BENEFIT),
        ("About Us", Context.COMPANY_DESCRIPTION),
        ("Sobre a empresa", Context.COMPANY_DESCRIPTION),
        ("Tech stack", Context.TOOL),
        ("Faixa salarial", Context.COMPENSATION),
        ("Local de trabalho:", Context.LOCATION),
        ("Processo seletivo", Context.CONDITION),
        # Bullets that start with a heading word are sentences, not headings.
        ("Experience with SQL and dashboards", None),
        ("- Experience with SQL", None),
        ("Skills in negotiation are a plus", None),
        ("Benefits administration for 300 employees", None),
    ],
)
def test_headings_are_read_however_they_are_written(line: str, kind: Context | None) -> None:
    assert heading_context(line) == kind


def test_an_inline_heading_sets_the_context_of_its_own_text() -> None:
    text = "Responsibilities: manage the warehouse.\nBenefits: health insurance.\n"
    got = [(text[s.start : s.end], s.context) for s in sentences(text)]
    assert got == [
        ("manage the warehouse.", Context.RESPONSIBILITY),
        ("health insurance.", Context.BENEFIT),
    ]


def test_markdown_and_html_headings_cut_an_advert() -> None:
    text = (
        "<h2>About us</h2>\nWe build tools for clinics.\n"
        "## What you'll do\n- Manage the inventory.\n"
        "**Requisitos:**\n- Experiência com estoque.\n"
        "### Perks\n- Gym pass.\n"
    )
    got = [s.context for s in sentences(text)]
    assert got == [
        Context.COMPANY_DESCRIPTION,
        Context.RESPONSIBILITY,
        Context.REQUIREMENT,
        Context.BENEFIT,
    ]


def test_without_headings_a_sentence_takes_its_own_cue_or_nothing() -> None:
    text = "We are a logistics company founded in 2010. Manage the inventory. Gym pass included."
    got = [s.context for s in sentences(text)]
    assert got[0] is Context.COMPANY_DESCRIPTION
    assert got[1] is Context.OTHER, "no heading and no cue: unknown, never guessed"

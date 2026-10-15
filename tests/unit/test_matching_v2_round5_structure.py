"""Four defects of round 5, fixed on NEW development material (2026-10-15).

Invented sentences throughout, written for the shape each rule reads; none
is taken from an advert or from a labelled package.

* teaching: a school level or a qualification is an education, the company
  teaching the hire is not the hire teaching, a programme in capitals is a
  name;
* sales as a team or a tool: a list of functions closed by a unit noun, the
  word naming what another noun is for;
* recruitment notices: the employer recruiting for this posting, its own
  process, the applicant's personal data;
* a phrase composed from unrelated words: a participle with an agent, the
  second half of another hyphenated compound; and the tightest window wins.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2.context import Context, sentences

BASE, _ = load_search_config(committed_config_dir())


def _answer(phrase: str, sentence: str, language: str, heading: str = "Requirements") -> str:
    return ev.answer(BASE, phrase, sentence, heading, language)


@pytest.mark.parametrize(
    ("phrase", "sentence", "language"),
    [
        # a school level, a degree, the company as the teacher, a programme
        ("ensino", "Ensino médio completo.", "pt"),
        ("ensino", "Formação: ensino superior completo em administração.", "pt"),
        ("enseñanza", "Enseñanza media completa.", "es"),
        ("sales", "Bachelor's degree in marketing or sales.", "en"),
        ("teaching", "We'll teach you the rest during onboarding.", "en"),
        ("teaching", "What we can't teach is curiosity.", "en"),
        ("teaching", "We pay for the program and support TEACH.", "en"),
        # a unit of the organisation, a tool
        ("sales", "Link customers, sales, product, and engineering teams.", "en"),
        # Experience with a field's tools is TOOL_EXPERIENCE since 2026-10-16
        # (weak, counted): `test_matching_v2_semantic_evidence.py`.
    ],
)
def test_one_word_that_names_something_else_is_not_the_work(
    phrase: str, sentence: str, language: str
) -> None:
    assert _answer(phrase, sentence, language) != ev.MATCH


@pytest.mark.parametrize(
    ("phrase", "sentence", "language"),
    [
        ("teaching", "Teaching mathematics to secondary students.", "en"),
        ("teaching", "Previous teaching experience is preferred.", "en"),
        ("ensino", "Experiência com ensino de matemática para adultos.", "pt"),
        ("sales", "Own the full sales cycle from prospecting to close.", "en"),
        ("sales", "Lead consultative sales for mid-market accounts.", "en"),
        ("vendas", "Experiência em vendas no varejo.", "pt"),
        ("suporte", "Prestar suporte técnico aos clientes da região.", "pt"),
        ("suporte", "Garantir suporte básico de tecnologia aos usuários.", "pt"),
        ("operations", "Bachelor's degree and 6 years of Quality or Operations experience.", "en"),
    ],
)
def test_the_work_itself_still_counts(phrase: str, sentence: str, language: str) -> None:
    assert _answer(phrase, sentence, language, heading="Responsibilities") == ev.MATCH


def _context(sentence: str) -> Context:
    return sentences(f"Responsibilities\n{sentence}\n")[0].context


@pytest.mark.parametrize(
    "notice",
    [
        "We are currently recruiting for a legal assistant for our office.",
        "Please review how we process your personal data during the application.",
        "We will not be considering recruitment agencies for this role.",
        "We are committed to a fair process in our recruitment.",
        "Conheça nosso programa de recrutamento interno.",
    ],
)
def test_a_notice_about_this_hiring_proves_no_work(notice: str) -> None:
    assert _context(notice) is Context.CONDITION


def test_a_recruiters_own_work_is_untouched() -> None:
    duty = "Own the full-cycle recruiting process for open sales roles."
    assert _context(duty) is Context.RESPONSIBILITY
    assert _answer("recruiting", duty, "en", heading="Responsibilities") == ev.MATCH


def test_a_unit_without_breaks_is_not_one_notice() -> None:
    # A whole section run together (a source that drops line breaks): one
    # notice inside it must not set aside the duties around it.
    duties = "; ".join(f"Conduzir processos de recrutamento para a unidade {i}" for i in range(12))
    text = f"Responsabilidades\n{duties}; programa de recrutamento interno.\n"
    assert sentences(text)[0].context is not Context.CONDITION


@pytest.mark.parametrize(
    ("sentence", "language"),
    [
        ("Contact warm inbound leads generated through digital campaigns.", "en"),
        ("Follow up on campaign-generated leads within a day.", "en"),
        ("Lead the next-generation platform roadmap.", "en"),
        ("Acompanhar os leads gerados pela equipe de marketing.", "pt"),
    ],
)
def test_lead_generation_is_not_composed_from_unrelated_words(sentence: str, language: str) -> None:
    assert _answer("lead generation", sentence, language, heading="Responsibilities") != ev.MATCH


@pytest.mark.parametrize(
    ("sentence", "language"),
    [
        ("Own lead generation for the region.", "en"),
        ("Generate qualified leads through outbound prospecting.", "en"),
        ("Build and manage lead-generation campaigns.", "en"),
        ("Responsável pela geração de leads qualificados.", "pt"),
        ("Generar leads calificados para el equipo comercial.", "es"),
    ],
)
def test_lead_generation_still_reads(sentence: str, language: str) -> None:
    assert _answer("lead generation", sentence, language, heading="Responsibilities") == ev.MATCH


def test_the_tightest_window_is_not_shadowed_by_a_loose_one() -> None:
    # A loose window (`Management, Customer Experience, Inventory`) used to
    # consume the words of the phrase written whole right after it.
    sentence = "Skills: Performance Management, Customer Experience, Inventory Management."
    assert _answer("inventory management", sentence, "en", heading="Requirements") == ev.MATCH

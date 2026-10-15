"""A benefits heading is evidence about what a passage is FOR; it does not
overrule a sentence that itself assigns work to the person hired (owner's
decision, 2026-10-15). Invented sentences, written for the categories of the
decision, never taken from an advert or from a labelled package.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2.context import Context, sentences

BASE, _ = load_search_config(committed_config_dir())

# (phrase, sentence under a benefits heading, language)
NOT_WORK = [
    # a benefit that mentions work only in passing
    ("data", "You will receive support from our data team during onboarding.", "en"),
    ("payroll", "Benefits include payroll assistance and tax guidance.", "en"),
    ("training", "You'll have access to a yearly training budget.", "en"),
    ("mentoring", "You will get mentoring from senior colleagues.", "en"),
    ("coaching", "You'll also enjoy wellness coaching and gym access.", "en"),
    ("support", "We offer mental health support for you and your family.", "en"),
    ("treinamento", "Você vai contar com treinamento contínuo e plano de carreira.", "pt"),
    ("suporte", "Você terá suporte do time de dados desde o primeiro dia.", "pt"),
    ("mentoria", "Programa de mentoria com lideranças da empresa.", "pt"),
    ("formación", "Vas a tener acceso a formación continua y clases de idiomas.", "es"),
    ("soporte", "Soporte psicológico y programa de bienestar.", "es"),
]
WORK = [
    # the sentence itself gives the work to the role
    ("workshops", "Professional development: you will teach quarterly customer workshops.", "en"),
    ("training sessions", "In our learning program you will lead training sessions.", "en"),
    ("onboarding", "You will be responsible for onboarding new customers.", "en"),
    ("treinamentos", "Você vai ministrar treinamentos internos para novos clientes.", "pt"),
    ("atendimento ao cliente", "Você será responsável pelo atendimento ao cliente.", "pt"),
    ("capacitaciones", "Vas a liderar capacitaciones para el equipo comercial.", "es"),
    ("inventario", "Serás responsable de controlar el inventario del almacén.", "es"),
]


def _benefit(sentence: str) -> Context:
    return sentences(f"Benefits\n{sentence}\n")[0].context


@pytest.mark.parametrize(("phrase", "sentence", "language"), NOT_WORK)
def test_a_benefit_proves_no_work(phrase: str, sentence: str, language: str) -> None:
    assert _benefit(sentence) is Context.BENEFIT
    assert ev.answer(BASE, phrase, sentence, "Benefits", language) != ev.MATCH


@pytest.mark.parametrize(("phrase", "sentence", "language"), WORK)
def test_a_duty_the_sentence_assigns_counts_under_benefits(
    phrase: str, sentence: str, language: str
) -> None:
    assert _benefit(sentence) is Context.OTHER
    assert ev.answer(BASE, phrase, sentence, "Benefits", language) == ev.MATCH


def test_the_role_voice_still_reads_under_a_company_heading() -> None:
    # Out of the decision's scope, and unchanged: an About-us heading the
    # advert never closed, followed by the role.
    text = "About us\nWe are a logistics company.\nYou will have to plan routes daily.\n"
    assert [s.context for s in sentences(text)] == [Context.COMPANY_DESCRIPTION, Context.OTHER]

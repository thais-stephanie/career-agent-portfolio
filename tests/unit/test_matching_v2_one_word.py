"""One-word preferences need the sentence to show the work (2026-10-14).

`recruitment`, `support`, `sales`, `data`, `teaching` name a kind of work and
also name teams, compounds and actions. A one-word preference stays a match
in a duty that does the work, and is held UNCERTAIN (shown, worth nothing)
when its word names a team the role works with, is the second half of a
hyphenated compound, or is an English verb. Every case is an invented
sentence; the professions are deliberately unrelated to one another.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2 import structure

BASE, _ = load_search_config(committed_config_dir())

#: (preference, sentence, language, expected state)
CASES = [
    # the work itself
    ("support", "Provide customer support by chat and email.", "en", ev.MATCH),
    ("sales", "Exceed quarterly sales quotas across your territory.", "en", ev.MATCH),
    ("teaching", "Teaching mathematics to secondary students.", "en", ev.MATCH),
    ("recruitment", "Own full-cycle recruitment for engineering roles.", "en", ev.MATCH),
    ("data", "Build and maintain data pipelines for reporting.", "en", ev.MATCH),
    ("enfermagem", "Prestar assistencia de enfermagem aos pacientes internados.", "pt",
     ev.MATCH),
    ("logistics", "Plan inbound logistics for three warehouses.", "en", ev.MATCH),
    # a team the role works with
    ("sales", "Partner closely with the sales team on renewals.", "en", ev.UNCERTAIN),
    ("sales", "Partner with Legal, Sales and Product to build programs.", "en",
     ev.UNCERTAIN),
    ("enfermagem", "Atuar junto com a equipe de enfermagem na rotina da clinica.", "pt",
     ev.UNCERTAIN),
    # JOINING the team is doing the work: the words of working together must
    # be near the team, not anywhere in a long sentence.
    ("soporte", "Buscamos un profesional con formacion en sistemas o carrera afin para "
     "integrarse a nuestro equipo de soporte operativo.", "es", ev.MATCH),
    # a TOOL is a name: listed after a preposition, or after `to`, it is
    # still the tool (found in the owner's shadow run, 2026-10-14)
    ("HubSpot", "Experience with CRM systems (HubSpot, Salesforce, or similar).", "en",
     ev.MATCH),
    ("Codex", "Experiencia com GitHub Copilot, Codex, Claude Code ou similares.", "pt",
     ev.MATCH),
    ("Zapier", "Build reports and connect workflows to Zapier for automation.", "en",
     ev.MATCH),
    # a compound naming another activity
    ("sales", "Run pre-sales demonstrations for enterprise prospects.", "en", ev.UNCERTAIN),
    # an English verb
    ("support", "Support the launch of new regional offices.", "en", ev.UNCERTAIN),
    ("support", "Build pipelines to support safe software delivery.", "en", ev.UNCERTAIN),
    ("support", "Support month-end invoicing processes.", "en", ev.UNCERTAIN),
]  # fmt: skip


@pytest.mark.parametrize(("phrase", "sentence", "language", "expected"), CASES)
def test_one_word_preferences(phrase: str, sentence: str, language: str, expected: str) -> None:
    assert ev.answer(BASE, phrase, sentence, "Responsibilities", language) == expected


def test_a_list_of_skills_after_a_preposition_is_not_a_team() -> None:
    """`com` and `with` also introduce experience: `Experiencia com gestao de
    metadados, catalogo de dados` is data work, not the data team."""
    text = "Experiencia com gestao de metadados, catalogo de dados e linhagem."
    at = text.index("dados")
    assert structure.one_word(text, at, at + 5, 0, len(text), "pt") is None


def test_two_word_phrases_are_not_read_by_the_one_word_rules() -> None:
    sentence = "Partner closely with the customer support team on escalations."
    assert ev.answer(BASE, "customer support", sentence, "Responsibilities", "en") == ev.MATCH

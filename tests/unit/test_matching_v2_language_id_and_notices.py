"""Two corrections developed on a NEW development set (2026-10-14).

Language: French, Italian, German, Dutch, Polish and Indonesian adverts were
detected as Spanish, Portuguese or English, because they share `de`, `la`,
`con`, `una`; the grammar and one-word readings of those three were then
applied to them. Hiring notices: the recruitment of THIS posting (what the
data is used for, who will write, scams, equal treatment) is a condition of
applying and proves no recruiting work. Invented sentences throughout.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2.context import Context, sentences
from career_agent.matching_v2.language import detect_language

BASE, _ = load_search_config(committed_config_dir())

TEXTS = {
    "fr": "Nous recherchons une personne pour rejoindre notre équipe et vous serez responsable"
    " des projets avec les clients dans la région.",
    "it": "Siamo alla ricerca di una persona per il nostro team che si occuperà della gestione"
    " delle attività per i clienti.",
    "de": "Wir suchen eine Person für unser Team, die mit den Kunden auf der Plattform arbeitet"
    " und Projekte von Anfang an begleitet.",
    "nl": "Wij zoeken een collega voor ons team die met klanten werkt en van het begin bij de"
    " projecten betrokken is.",
    "pl": "Szukamy osoby, która jest odpowiedzialna za projekty oraz dla klientów w regionie,"
    " ze wsparciem przez zespół lub partnerów.",
    "id": "Kami mencari orang yang berpengalaman untuk bekerja dengan tim dalam proyek dan"
    " akan membantu pelanggan dari berbagai daerah.",
    "pt": "Estamos buscando uma pessoa para atuar com atendimento ao cliente e você será"
    " responsável pelo suporte aos nossos clientes na empresa.",
    "es": "Buscamos una persona para trabajar con clientes y que tenga experiencia en ventas"
    " para el equipo de la empresa.",
    "en": "We are looking for a person to join our team and you will be responsible for the"
    " projects with our customers in the region.",
}


@pytest.mark.parametrize("language", sorted(TEXTS))
def test_each_language_is_recognised(language: str) -> None:
    assert detect_language(TEXTS[language]) == language


def _context(sentence: str) -> Context:
    return sentences(f"Responsibilities\n{sentence}\n")[0].context


@pytest.mark.parametrize(
    "notice",
    [
        "Your personal data will be used for recruitment purposes only.",
        "A member of our recruitment team will reach out to schedule a call.",
        "All recruiting communication comes from our own domain.",
        "We are committed to equal treatment in all aspects of employment.",
        "Estamos a recrutar um técnico para a nossa oficina.",
        "Garantizamos la igualdad de trato durante el proceso de reclutamiento.",
    ],
)
def test_a_hiring_notice_proves_no_work(notice: str) -> None:
    assert _context(notice) is Context.CONDITION
    assert ev.answer(BASE, "recruitment", notice, "Responsibilities", "en") != ev.MATCH


@pytest.mark.parametrize(
    "duty",
    [
        "Manage the full recruitment process for engineering roles.",
        "Own full-cycle recruiting from sourcing to offer.",
        "Gestão de processos de recrutamento de ponta a ponta.",
    ],
)
def test_recruiting_duties_still_count(duty: str) -> None:
    assert _context(duty) is Context.RESPONSIBILITY

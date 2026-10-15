"""The semantic evidence prototype (2026-10-16): text structure, the frame's
dimensions, the profile's policy, and the regression fixtures named by the
package 3 review (one invented sentence per subgroup; none is taken from an
advert or from a labelled package).
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import LexiconSignal, load_search_config
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import evidence_policy as pol
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2 import text_structure as ts
from career_agent.matching_v2.context import Context, sentences
from career_agent.matching_v2.profile_match import observe_from_features

BASE, _ = load_search_config(committed_config_dir())
HEADINGS = (tuple(BASE.prominence.primary_headings), tuple(BASE.prominence.secondary_headings))


def _frames(phrase: str, sentence: str, language: str, heading: str = "Responsibilities",
            config=None) -> list:  # fmt: skip
    config = (config or BASE).model_copy(
        update={"lexicon": {"p": LexiconSignal(label=phrase, patterns=[phrase])}}
    )
    job = JobFacts(title="Role", description=f"{heading}\n{sentence}\n", job_id="case")
    f = feat.extract(job, "case", HEADINGS)
    feat.set_language(job, f, language)
    _, evidence = observe_from_features(config, f, job)
    return [e for e in evidence if e.frame is not None]


def _one(phrase: str, sentence: str, language: str, heading: str = "Responsibilities"):
    found = _frames(phrase, sentence, language, heading)
    assert found, (phrase, sentence)
    return found[0]


# ------------------------------------------------------------ text structure

GLUED = (
    "A Acme atua no varejo.Responsabilidades e atribuiçõesConduzir vendas consultivas."
    "Atender clientes no pós-venda.Requisitos e qualificaçõesExperiência em vendas."
    "Informações adicionais🚌 Vale transporte🍎 Vale refeição"
)


def test_a_collapsed_template_advert_gets_its_lines_back_without_rewriting() -> None:
    cuts = ts.cuts(GLUED)
    assert cuts and ts.decode(ts.encode(cuts)) == cuts
    cut = sentences(GLUED, cuts)
    by_text = {GLUED[s.start : s.end].rstrip("."): s.context for s in cut}
    assert by_text["Conduzir vendas consultivas"] is Context.RESPONSIBILITY
    assert by_text["Experiência em vendas"] is Context.REQUIREMENT
    assert any(c is Context.CONDITION for t, c in by_text.items() if "Vale" in t)
    # Every sentence is still a substring at its own offset.
    assert all(GLUED[s.start : s.end].strip() == GLUED[s.start : s.end] for s in cut)


def test_text_without_the_template_is_never_cut() -> None:
    text = "We build tools.Our team is small.Version 2.1 ships soon."
    assert ts.cuts(text) == ()
    assert ts.cuts("Visit www.Example.com or node.JS docs. Requisitos e qualificacoes") == ()


def test_urls_and_dotted_names_are_not_sentence_ends() -> None:
    text = (
        "Responsabilidades e atribuiçõesUsar Node.JS e acessar www.Acme.com.Br diariamente."
        "Requisitos e qualificaçõesInglês."
    )
    cuts = ts.cuts(text)
    assert text.index("JS") not in cuts
    assert text.index("Acme") not in cuts


def test_a_heading_inside_a_sentence_is_not_a_heading() -> None:
    text = (
        "Requisitos e qualificaçõesInglês.Responsabilidades e atribuiçõesVender."
        "For additional information, please write to us."
    )
    assert text.index("additional") not in ts.cuts(text)


# ------------------------------------------------------------ the frame


@pytest.mark.parametrize(
    ("phrase", "sentence", "language", "expected"),
    [
        ("sales", "Partner with the Sales team on regional launches.", "en", "OTHER_TEAM"),
        ("systems integration", "Our platform integrates systems for retailers.", "en", "PRODUCT"),
        ("recrutamento", "A Acme Talentos é uma consultoria de recrutamento.", "pt", "COMPANY"),
        ("ventas", "Somos referentes en ventas de seguros en la region.", "es", "COMPANY"),
        ("systems integration", "You will integrate systems for retailers.", "en", "ROLE"),
    ],
)
def test_owner(phrase: str, sentence: str, language: str, expected: str) -> None:
    assert _one(phrase, sentence, language).frame.split()[1] == expected


@pytest.mark.parametrize(
    ("phrase", "sentence", "language", "expected"),
    [
        ("teaching", "We'll teach you the rest during onboarding.", "en", "ROLE_RECEIVES"),
        ("suporte", "Você contará com suporte da liderança.", "pt", "ROLE_RECEIVES"),
        ("soporte", "Contarás con soporte del equipo regional.", "es", "ROLE_RECEIVES"),
        ("teaching", "Teaching mathematics to secondary students.", "en", "ROLE_PROVIDES"),
        ("suporte", "Prestar suporte técnico aos usuários.", "pt", "ROLE_PROVIDES"),
    ],
)
def test_direction(phrase: str, sentence: str, language: str, expected: str) -> None:
    assert _one(phrase, sentence, language).frame.split()[2] == expected


@pytest.mark.parametrize(
    ("phrase", "sentence", "language", "heading", "expected"),
    [
        ("enfermagem", "Graduação em Enfermagem.", "pt", "Requisitos", "DEGREE_IN_FIELD"),
        ("marketing", "Experience with marketing automation platforms.", "en", "Requirements",
         "TOOL_EXPERIENCE"),
        ("vendas", "Experiência com ferramentas de vendas e CRM.", "pt", "Requisitos",
         "TOOL_EXPERIENCE"),
        ("logistics", "Familiarity with logistics is a plus.", "en", "Requirements", "FAMILIARITY"),
        ("recruiting", "3+ years of recruiting for technical roles.", "en", "Requirements",
         "REQUIREMENT_EXPERIENCE"),
        ("sales", "Own the full sales cycle from prospecting to close.", "en", "Responsibilities",
         "DUTY"),
        ("payroll", "Our payroll software is used by thousands.", "en", "Responsibilities",
         "ARTIFACT_ONLY"),
    ],
)  # fmt: skip
def test_evidence_type(phrase, sentence, language, heading, expected) -> None:
    assert _one(phrase, sentence, language, heading).frame.split()[0] == expected


@pytest.mark.parametrize(
    ("phrase", "sentence", "language", "expected"),
    [
        ("vendas", "Lavar os carros disponíveis para venda.", "pt", "MERCHANDISE"),
        ("ventas", "Reponer productos en los puntos de venta.", "es", "MERCHANDISE"),
        ("sales", "Restock items for sale at the store.", "en", "MERCHANDISE"),
        ("payroll", "Convênio farmácia com desconto em folha de pagamento.", "pt", "DEDUCTION"),
        ("soporte", "Diseñar estructuras de soporte para cables.", "es", "PHYSICAL"),
        ("sales", "Conduct pre-sales demos for prospects.", "en", "PRE_SALES"),
        ("vendas", "Atendimento pós-venda aos clientes.", "pt", "POST_SALES"),
        ("vendas", "Atendimento pós-venda com foco em upsell e renovações.", "pt",
         "POST_SALES_COMMERCIAL"),
        ("ensino", "Ensino médio completo.", "pt", "EDUCATION_LEVEL"),
    ],
)  # fmt: skip
def test_sense(phrase: str, sentence: str, language: str, expected: str) -> None:
    assert _one(phrase, sentence, language).frame.split()[3] == expected


def test_a_language_without_grammar_is_unknown_never_an_english_rule() -> None:
    e = _one("design", "Sie gestalten das Design unserer Produkte.", "de")
    assert e.frame.split()[1:3] == ["UNKNOWN", "UNKNOWN"] and e.frame.endswith("LOW")
    assert e.state == "MATCH"  # the section decides, as before; certainty is low


# ------------------------------------------------------------ the policy

CASES_COUNTED = [
    ("Own the full sales cycle from prospecting to close.", "sales", "en", True),
    ("Conduct pre-sales demos for prospects.", "sales", "en", True),
    ("Atendimento pós-venda com foco em upsell e renovações.", "vendas", "pt", True),
    ("Graduação em Enfermagem.", "enfermagem", "pt", True),
    ("Partner with the Sales team on regional launches.", "sales", "en", False),
    ("Atendimento pós-venda aos clientes.", "vendas", "pt", False),
    ("Formação superior em Administração, Economia, Contabilidade ou áreas correlatas.",
     "contabilidade", "pt", False),
    ("Bachelor's degree in physics or accounting.", "accounting", "en", False),
]  # fmt: skip


@pytest.mark.parametrize(("sentence", "phrase", "language", "counted"), CASES_COUNTED)
def test_what_counts_under_the_default_policy(sentence, phrase, language, counted) -> None:
    e = _one(phrase, sentence, language)
    assert (e.state == "MATCH") is counted, (e.frame, e.weight)


def test_weak_evidence_counts_and_is_never_a_core_duty() -> None:
    e = _one("enfermagem", "Graduação em Enfermagem.", "pt", "Requisitos")
    assert e.state == "MATCH" and e.weight < pol.DEFAULT["core_at"]


def test_a_profile_policy_changes_the_answer_and_not_the_frame() -> None:
    strict = BASE.model_copy(update={"evidence_policy": {**pol.DEFAULT, "counted_at": 0.5}})
    loose = _frames("enfermagem", "Graduação em Enfermagem.", "pt", "Requisitos")[0]
    tight = _frames("enfermagem", "Graduação em Enfermagem.", "pt", "Requisitos", strict)[0]
    assert loose.frame == tight.frame
    assert loose.state == "MATCH" and tight.state != "MATCH"


def test_explanations_name_every_dimension() -> None:
    from career_agent.matching_v2 import evidence as ev

    frame = ev.EvidenceFrame("p:sales", ev.Sense.NAMED, ev.Owner.OTHER_TEAM,
                             ev.Direction.ROLE_PROVIDES, ev.EvidenceType.COLLABORATION,
                             "RESPONSIBILITY", ev.Certainty.HIGH, "ONE_WORD_TEAM")  # fmt: skip
    text = pol.explain(frame)
    for word in ("OTHER_TEAM", "COLLABORATION", "weight 0.00", "not counted"):
        assert word in text


# ------------------------------------------------------------ components


def test_a_policy_change_never_reaches_a_shared_fact() -> None:
    stale = comp.affected({"profile.evidence_policy"})
    assert stale == {"evidence_policy", "work_match", "tools_match", "score"}
    assert not stale & set(comp.JOB_COMPONENTS)


def test_a_structure_change_reaches_sentences_and_semantics() -> None:
    stale = comp.affected({"text_structure"})
    assert {"sentence_context", "semantic_evidence_facts", "work_facts", "work_match"} <= stale
    assert "pay_facts" not in stale and "eligibility_facts" not in stale


def test_shared_facts_are_identical_for_every_profile() -> None:
    job = JobFacts(title="Role", description=GLUED, job_id="g")
    f = feat.extract(job, "g", HEADINGS)
    before = dict(f.payloads)
    for phrase in ("vendas", "suporte", "payroll"):
        config = BASE.model_copy(
            update={"lexicon": {"p": LexiconSignal(label=phrase, patterns=[phrase])}}
        )
        observe_from_features(config, f, job)
    assert f.payloads == before

"""Matching Architecture v2: the graph, the language layer, sentence context
and the balanced synthetic corpus (ADR-0037). No network, no person's data."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.match.phrase_forms import root
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import evaluation as ev
from career_agent.matching_v2.context import Context, sentences
from career_agent.matching_v2.language import (
    AUTOMATIC,
    Relation,
    concept_readings,
    detect_language,
    load_table,
    morphology_examples,
    tokens,
)

CORPUS = Path(__file__).resolve().parents[1] / "fixtures" / "matching_v2" / "language_corpus.yaml"
CONFIG, _ = load_search_config(committed_config_dir())

# --------------------------------------------------------------- the graph


def test_a_language_change_invalidates_work_only() -> None:
    stale = comp.affected({"language"})
    assert stale == {"language", "work_facts", "work_match", "tools_match", "score"}
    for untouched in ("pay_facts", "location_facts", "seniority_facts", "engagement_facts",
                      "eligibility", "compensation_match", "seniority_match"):  # fmt: skip
        assert untouched not in stale


def test_a_salary_preference_touches_compensation_and_the_score() -> None:
    assert comp.affected({"profile.salary"}) == {"compensation_match", "score"}


def test_work_preferences_never_touch_location_or_pay() -> None:
    stale = comp.affected({"profile.lexicon"})
    assert stale == {"work_match", "tools_match", "score"}
    assert not stale & set(comp.JOB_COMPONENTS)


def test_metadata_moves_location_pay_and_engagement_not_the_words() -> None:
    stale = comp.affected({"job.metadata"})
    assert {"location_facts", "pay_facts", "engagement_facts"} <= stale
    assert not {"language", "sentence_context", "work_facts", "work_match"} & stale


def test_new_text_makes_everything_stale() -> None:
    assert comp.affected({"job.text"}) == set(comp.REGISTRY)


def test_a_stamp_reads_only_the_inputs_of_its_lineage() -> None:
    inputs = {"job.text": "t", "job.metadata": "m", "machinery.headings": "h",
              "profile.salary": "s1", "profile.lexicon": "w"}  # fmt: skip
    a = comp.stamp("work_match", inputs)
    assert a == comp.stamp("work_match", {**inputs, "profile.salary": "s2"})
    assert a != comp.stamp("work_match", {**inputs, "profile.lexicon": "w2"})
    with pytest.raises(KeyError):
        comp.stamp("compensation_match", {"job.text": "t"})


# ------------------------------------------------------------ the language


def test_every_morphology_rule_is_proven_by_its_examples() -> None:
    table = load_table()
    for a, b in morphology_examples():
        assert table.canonical(root(a), a)[0] == table.canonical(root(b), b)[0], (a, b)


def test_non_automatic_relations_never_join_two_words() -> None:
    table = load_table()
    assert table.non_automatic, "the reasons for not matching are written down"
    for rel in table.non_automatic:
        assert rel.relation not in AUTOMATIC
        assert concept_readings(rel.a) != concept_readings(rel.b), rel


def test_a_root_two_concepts_share_is_never_mapped() -> None:
    table = load_table()
    assert table.ambiguous == ("negoci",)
    # The listed forms still tell the two apart.
    assert concept_readings("negociacao") == concept_readings("negotiation")
    assert concept_readings("negocio") == concept_readings("business")


def test_tokens_keep_offsets_into_the_original_text() -> None:
    text = "Conduzir o levantamento de requisitos e a integração de sistemas."
    for t in tokens(text):
        assert text[t.start : t.end].strip(), t
    fixed = [t for t in tokens(text) if t.relation is Relation.FIXED_EXPRESSION]
    assert {t.root for t in fixed} == {"requir", "gather"}


def test_language_is_told_by_function_words_or_not_at_all() -> None:
    assert (
        detect_language("Você vai liderar o time de vendas com a nossa equipe e os clientes")
        == "pt"
    )
    assert detect_language("You will lead the sales team with our people and the customers") == "en"
    assert detect_language("CRM HubSpot n8n") == "und"


# ---------------------------------------------------------------- context


def test_a_sentence_takes_its_heading_or_its_own_cue_or_other() -> None:
    text = (
        "About us\nWe automate workflows for banks.\n\nResponsibilities\n- Automate workflows.\n\n"
        "Benefits\nHealth insurance.\n\nWe ship weekly."
    )
    got = [(text[s.start : s.end], s.context) for s in sentences(text)]
    assert got[0] == ("We automate workflows for banks.", Context.COMPANY_DESCRIPTION)
    assert got[1] == ("- Automate workflows.", Context.RESPONSIBILITY)
    assert got[2][1] is Context.BENEFIT
    assert got[3][1] is Context.BENEFIT, "a context holds until the next heading"


# ----------------------------------------------------------------- corpus


@pytest.fixture(scope="module")
def corpus_report() -> dict:
    return ev.run(CONFIG, ev.load_corpus(CORPUS))


def test_the_corpus_is_balanced(corpus_report) -> None:
    cases = ev.load_corpus(CORPUS)
    assert len(cases) >= 110
    assert len({c.field for c in cases}) >= 9
    assert {c.lang for c in cases} == {"en", "pt", "es"}
    assert {c.cat for c in cases} == {"T", "X", "F", "S", "P", "N", "C"}


def test_design_guarantees_hold_on_the_corpus(corpus_report) -> None:
    """Never: a related concept, unrelated text or a company/benefit sentence
    proving the work. These are rules of the design, not rates."""
    by = corpus_report["by_cat"]
    for cat in ("S", "N", "C"):
        assert by[cat].get("MATCH", 0) == 0, (cat, by[cat])


def test_translation_inflection_and_fixed_expressions_are_found(corpus_report) -> None:
    by = corpus_report["by_cat"]
    for cat in ("T", "X", "F"):
        assert by[cat]["correct"] == by[cat]["n"], (cat, corpus_report["misses"])


def test_words_in_another_sense_are_uncertain_by_structure(corpus_report) -> None:
    """Words of a phrase present in another sense (`Data on staff migration`).
    Six of seven matched under proximity alone (2026-10-14). The structure of
    the sentence holds all but one UNCERTAIN: `dados cadastrais ... para
    analise`, which only the inverted-head reading caught, and that reading
    was REMOVED the same day because on real adverts it held 40 duties
    UNCERTAIN to set aside 22 (`structure.py`). Pinned so a change to it is
    a decision, not a drift."""
    assert corpus_report["by_cat"]["P"].get("MATCH", 0) == 1


ADVERSARIAL_DEV = CORPUS.with_name("adversarial_dev.yaml")


def test_adversarial_development_half() -> None:
    """Sentences written to attack the structural reading, each beside a
    legitimate sentence of the same shape. Every legitimate one still
    matches. Twenty invented other-sense sentences of 34 still do: they need
    a reading (a colon, a comma list, a compound's order, a determiner-less
    subject, an inverted head) that real postings showed costs more
    legitimate matches than it saves (`structure.py`, measured on development
    sets of real adverts; the inverted head, removed 2026-10-14, caught 14 of
    these)."""
    report = ev.run(CONFIG, ev.load_corpus(ADVERSARIAL_DEV))
    by = report["by_cat"]
    for cat in ("T", "X", "F"):
        if cat in by:
            assert by[cat]["correct"] == by[cat]["n"], (cat, report["misses"])
    for cat in ("S", "N", "C"):
        assert by[cat].get("MATCH", 0) == 0, (cat, by[cat])
    assert by["P"].get("MATCH", 0) <= 20, report["misses"]


def test_the_engine_names_no_profession_and_no_product() -> None:
    """Vocabulary of a field is data (`*_data.yaml`), never a branch in code."""
    package = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "matching_v2"
    forbidden = ("crm", "hubspot", "salesforce", "revops", "n8n", "zapier", "nurse",
                 "enfermag", "marketing", "teacher", "professor")  # fmt: skip
    for module in package.glob("*.py"):
        text = module.read_text(encoding="utf-8").lower()
        found = [w for w in forbidden if w in text]
        assert not found, f"{module.name} names {found}"


def test_a_one_word_preference_matches_its_word_never_a_shared_root() -> None:
    """`workato` reduces to the root `work`; on real postings it fired on 257 of
    300 (2026-10-14 parity run). One word matches itself or a listed form."""
    assert (
        ev.answer(CONFIG, "Workato", "You will work closely with partners.", "Responsibilities")
        == ev.NONE
    )
    assert ev.answer(CONFIG, "Workato", "Build recipes in Workato.", "Responsibilities") == "MATCH"
    assert (
        ev.answer(CONFIG, "payroll", "Processar a folha de pagamento.", "Responsabilidades")
        == "MATCH"
    )
    assert (
        ev.answer(CONFIG, "recruitment", "Conduzir o recrutamento.", "Responsabilidades") == "MATCH"
    )

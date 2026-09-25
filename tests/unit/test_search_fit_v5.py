"""Search Fit v5: the deterministic fallback and the semantic merge.

Every rule here is one sentence of docs/SEMANTIC_MATCHING.md, asserted:

* an UNCONFIGURED phrase component leaves the denominator; an UNMATCHED one
  stays 0 of its max;
* each phrase component pays its strongest few distinct signals, once each;
* one sentence pays for at most half of a component;
* tools without any of the desired work earn at most half the tools bucket;
* seniority follows the person's preferred levels;
* a soft penalty is a magnitude that is subtracted, even when written as -3;
* a semantic finding and a lexical hit for the same intent item never both
  pay, and missing information never becomes points.

The configuration is synthetic: the committed worked example with its phrase
weights replaced by a handful of invented phrases.
"""

from __future__ import annotations

import copy

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig
from career_agent.domain.enums import AnalysisConfidence, Seniority, SenioritySource
from career_agent.domain.matching import (
    DEFAULT_SENIORITY,
    SemanticEvidence,
    SemanticMatch,
    SeniorityReading,
)
from career_agent.match.employment import read_employment
from career_agent.match.engine import JobFacts
from career_agent.match.lexicon import body_only, observe
from career_agent.match.score import (
    COUNTED_SIGNALS,
    match_score_from,
    score_components,
    soft_penalties,
)
from career_agent.yaml_io import safe_load

BASE = safe_load((committed_config_dir() / "search.local.yaml").read_text(encoding="utf-8"))
WORK = {
    "w_invoices": "reconcile invoices",
    "w_payroll": "run payroll",
    "w_vendors": "onboard vendors",
    "w_audits": "prepare audits",
    "w_budget": "track budgets",
    "w_close": "month end close",
}
TOOLS = {"t_ledger": "ledgerbook", "t_sheet": "gridsheet", "t_bank": "bankfeed", "t_tax": "taxpal"}


def config_with(
    *,
    work: dict[str, str] | None = None,
    tools: dict[str, str] | None = None,
    penalties: dict[str, float] | None = None,
    preferred: list[str] | None = None,
    excluded: list[str] | None = None,
) -> SearchConfig:
    data = copy.deepcopy(BASE)
    work = WORK if work is None else work
    tools = TOOLS if tools is None else tools
    for signal_id, phrase in {**work, **tools, "p_cold": "cold calls"}.items():
        data["lexicon"][signal_id] = {"label": phrase.title(), "patterns": [phrase]}
    components = data["scoring"]["components"]
    components["responsibilities"]["weights"] = dict.fromkeys(work, 5.0)
    components["responsibilities"]["setup_weights"] = {}
    components["technologies"]["weights"] = dict.fromkeys(tools, 5.0)
    components["technologies"]["setup_weights"] = {}
    components["automation_integration"]["weights"] = {}
    components["automation_integration"]["setup_weights"] = {}
    data["scoring"]["soft_penalties"]["weights"] = penalties or {}
    data["preferences"]["seniority"]["preferred"] = preferred or []
    data["preferences"]["seniority"]["excluded"] = excluded or []
    return SearchConfig.model_validate(data)


def scored(
    config: SearchConfig,
    body: str,
    *,
    level: Seniority | None = None,
    semantic: SemanticEvidence | None = None,
):
    job = JobFacts(title="Analyst", description=body)
    observed = body_only(config, observe(config, "Analyst", body))
    reading = (
        SeniorityReading(
            value=level,
            source=SenioritySource.TITLE_GRADE,
            confidence=AnalysisConfidence.HIGH,
            evidence="Analyst",
        )
        if level
        else DEFAULT_SENIORITY
    )
    components = score_components(
        config,
        observed_body=observed,
        seniority=reading,
        employment=read_employment(body),
        job_facts=job,
        semantic=semantic,
    )
    penalties = soft_penalties(config, observed)
    return (
        {c.component_id: c for c in components},
        penalties,
        match_score_from(components, penalties),
    )


def responsibilities(*lines: str) -> str:
    return "Responsibilities\n" + "".join(f"- {line}\n" for line in lines)


def evidence(*matches: SemanticMatch) -> SemanticEvidence:
    return SemanticEvidence(
        evaluation_id="sem_test",
        provider="fake",
        model="fake-1",
        contract="semantic-test",
        intent_digest="digest",
        matches=matches,
    )


# =========================================================================
# unconfigured is not unmatched
# =========================================================================


def test_an_unconfigured_component_leaves_the_denominator() -> None:
    config = config_with()
    components, _, _ = scored(config, responsibilities("Nothing relevant here."))
    other = components["automation_integration"]
    assert other.configured is False
    assert other.max_points == 0 and other.points == 0


def test_an_unmatched_configured_component_stays_zero_of_its_max() -> None:
    config = config_with()
    components, _, score = scored(config, responsibilities("Nothing relevant here."))
    work = components["responsibilities"]
    assert work.configured is True
    assert work.points == 0 and work.max_points == 25
    assert score < 35, "missing work evidence must never read as fit"


def test_a_search_with_no_tools_is_not_penalised_for_having_none() -> None:
    """A nurse or a teacher may state no tools at all: the component leaves
    the denominator instead of sitting at 0 of 20 on every posting."""
    with_tools = config_with()
    without = config_with(tools={})
    body = responsibilities("You will reconcile invoices", "You will run payroll")
    _, _, score_with = scored(with_tools, body)
    _, _, score_without = scored(without, body)
    assert score_without > score_with


# =========================================================================
# top-N, and what one sentence can pay for
# =========================================================================


def test_only_the_strongest_few_distinct_signals_pay() -> None:
    config = config_with()
    body = responsibilities(
        "You will reconcile invoices",
        "You will run payroll",
        "You will onboard vendors",
        "You will prepare audits",
        "You will track budgets",
        "You will own the month end close",
    )
    components, _, _ = scored(config, body)
    work = components["responsibilities"]
    counted = [c for c in work.contributions if c.counted]
    extra = [c for c in work.contributions if not c.counted]
    assert len(counted) == COUNTED_SIGNALS["responsibilities"] == 4
    assert len(extra) == 2
    assert all(c.points == 0 and c.uncounted_reason for c in extra)
    assert work.points == pytest.approx(25.0)


def test_four_central_matches_fill_the_work_component() -> None:
    config = config_with()
    body = responsibilities(
        "You will reconcile invoices",
        "You will run payroll",
        "You will onboard vendors",
        "You will prepare audits",
    )
    components, _, _ = scored(config, body)
    assert components["responsibilities"].points == pytest.approx(25.0)


def test_a_compound_bullet_pays_for_both_of_its_activities() -> None:
    config = config_with()
    body = responsibilities("You will reconcile invoices and run payroll every week")
    components, _, _ = scored(config, body)
    work = components["responsibilities"]
    assert [c.counted for c in work.contributions] == [True, True]
    assert work.points == pytest.approx(2 * 25.0 / 4)


def test_one_sentence_never_fills_a_component_on_its_own() -> None:
    """The stuffed line: every desired activity in one sentence."""
    config = config_with()
    body = responsibilities(
        "You will reconcile invoices, run payroll, onboard vendors, prepare audits,"
        " track budgets and own the month end close"
    )
    components, _, _ = scored(config, body)
    work = components["responsibilities"]
    assert [c.counted for c in work.contributions].count(True) == 2
    assert work.points == pytest.approx(25.0 / 2)
    assert all(c.uncounted_reason for c in work.contributions if not c.counted)


def test_repeating_a_phrase_buys_nothing() -> None:
    config = config_with()
    once = responsibilities("You will reconcile invoices")
    many = responsibilities(*["You will reconcile invoices"] * 12)
    assert scored(config, once)[2] == scored(config, many)[2]


# =========================================================================
# the tools guard
# =========================================================================


def test_tools_without_any_desired_work_earn_at_most_half() -> None:
    config = config_with()
    body = responsibilities("Use ledgerbook", "Use gridsheet", "Use bankfeed", "Use taxpal")
    components, _, _ = scored(config, body)
    tools = components["technologies"]
    assert tools.points == pytest.approx(10.0)
    assert tools.capped and tools.note and "half" in tools.note


def test_one_piece_of_desired_work_lifts_the_tools_guard() -> None:
    config = config_with()
    body = responsibilities(
        "You will reconcile invoices",
        "Use ledgerbook",
        "Use gridsheet",
        "Use bankfeed",
    )
    components, _, _ = scored(config, body)
    assert components["technologies"].points == pytest.approx(20.0)


def test_the_guard_never_fires_when_no_work_was_asked_for() -> None:
    config = config_with(work={})
    body = responsibilities("Use ledgerbook", "Use gridsheet", "Use bankfeed")
    components, _, _ = scored(config, body)
    assert components["responsibilities"].configured is False
    assert components["technologies"].points == pytest.approx(20.0)


# =========================================================================
# seniority follows the person's preference
# =========================================================================


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (Seniority.SENIOR, 10.0),  # preferred
        (Seniority.MID, 10.0),  # preferred
        (Seniority.JUNIOR, 5.0),  # one step below MID
        (Seniority.LEAD, 5.0),  # one step above SENIOR
        (Seniority.PRINCIPAL, 2.0),  # further away
        (Seniority.INTERN, 0.0),  # excluded: hidden, and worth nothing
    ],
)
def test_seniority_points_follow_the_preferred_levels(level: Seniority, expected: float) -> None:
    config = config_with(preferred=["MID", "SENIOR"], excluded=["INTERN"])
    components, _, _ = scored(config, responsibilities("x"), level=level)
    assert components["seniority"].points == pytest.approx(expected)


def test_an_unstated_level_earns_nothing_whatever_is_preferred() -> None:
    config = config_with(preferred=["MID"])
    components, _, _ = scored(config, responsibilities("x"))
    assert components["seniority"].points == 0


def test_without_preferred_levels_the_configured_table_decides() -> None:
    config = config_with()
    components, _, _ = scored(config, responsibilities("x"), level=Seniority.SENIOR)
    assert components["seniority"].points == config.scoring.components.seniority.points_for(
        Seniority.SENIOR
    )


# =========================================================================
# soft penalties subtract
# =========================================================================


@pytest.mark.parametrize("weight", [3.0, -3.0])
def test_a_soft_penalty_lowers_the_score_however_its_sign_was_written(weight: float) -> None:
    """Setup used to write -3, and subtracting -3 ADDED points."""
    config = config_with(penalties={"p_cold": weight})
    assert config.scoring.soft_penalties.weights["p_cold"] == 3.0
    clean = responsibilities("You will reconcile invoices")
    dirty = responsibilities("You will reconcile invoices", "You will make cold calls")
    _, penalties, penalised = scored(config, dirty)
    _, _, baseline = scored(config, clean)
    assert penalties and all(p.points > 0 for p in penalties)
    assert penalised < baseline


# =========================================================================
# the semantic merge
# =========================================================================


def test_a_semantic_finding_pays_for_work_the_phrases_missed() -> None:
    config = config_with()
    body = responsibilities("Keep the books balanced against supplier bills each week.")
    found = SemanticMatch(
        "responsibilities", "w_invoices", "W3", "strong", "Keep the books balanced"
    )
    without, _, low = scored(config, body)
    with_it, _, high = scored(config, body, semantic=evidence(found))
    assert without["responsibilities"].points == 0
    contribution = with_it["responsibilities"].contributions[0]
    assert contribution.source == "semantic" and contribution.quote == "Keep the books balanced"
    assert high > low


def test_a_lexical_hit_and_a_finding_for_the_same_item_pay_once() -> None:
    config = config_with()
    body = responsibilities("You will reconcile invoices")
    found = SemanticMatch("responsibilities", "w_invoices", "W3", "strong", "reconcile invoices")
    lexical, _, _ = scored(config, body)
    both, _, _ = scored(config, body, semantic=evidence(found))
    assert both["responsibilities"].points == lexical["responsibilities"].points
    assert len(both["responsibilities"].contributions) == 1


def test_a_partial_finding_earns_less_than_a_strong_one() -> None:
    config = config_with()
    body = responsibilities("Keep the books balanced.")
    strong = SemanticMatch("responsibilities", "w_invoices", "W1", "strong", "Keep the books")
    partial = SemanticMatch("responsibilities", "w_invoices", "W1", "partial", "Keep the books")
    a, _, _ = scored(config, body, semantic=evidence(strong))
    b, _, _ = scored(config, body, semantic=evidence(partial))
    assert a["responsibilities"].points > b["responsibilities"].points > 0


def test_a_finding_for_an_unweighted_signal_pays_nothing() -> None:
    config = config_with()
    stray = SemanticMatch("responsibilities", "not_in_intent", "W9", "strong", "Keep the books")
    components, _, _ = scored(config, responsibilities("Keep the books."), semantic=evidence(stray))
    assert components["responsibilities"].points == 0


def test_an_empty_evaluation_is_no_evidence_not_negative_evidence() -> None:
    config = config_with()
    body = responsibilities("You will reconcile invoices")
    _, _, alone = scored(config, body)
    _, _, evaluated = scored(config, body, semantic=evidence())
    assert alone == evaluated


def test_the_same_inputs_always_give_the_same_score() -> None:
    config = config_with()
    body = responsibilities("You will reconcile invoices", "Use ledgerbook")
    found = SemanticMatch("responsibilities", "w_payroll", "W2", "partial", "reconcile")
    runs = {scored(config, body, semantic=evidence(found))[2] for _ in range(5)}
    assert len(runs) == 1


def test_one_sentence_pays_for_only_one_semantic_finding() -> None:
    """Providers cite one sentence for several items far more loosely than a
    posting states them; an interpretation of a sentence pays once."""
    config = config_with()
    body = responsibilities("Keep the books balanced and the team paid.")
    quote = "Keep the books balanced and the team paid."
    found = evidence(
        SemanticMatch("responsibilities", "w_invoices", "W1", "strong", quote),
        SemanticMatch("responsibilities", "w_payroll", "W2", "strong", quote),
    )
    components, _, _ = scored(config, body, semantic=found)
    work = components["responsibilities"]
    assert [c.counted for c in work.contributions].count(True) == 1
    assert work.points == pytest.approx(25.0 / 4)

"""Search Fit v5: the deterministic fallback and the semantic merge.

Every rule here is one sentence of docs/SEMANTIC_MATCHING.md, asserted:

* an UNCONFIGURED phrase component leaves the denominator; an UNMATCHED one
  stays 0 of its max;
* each phrase component pays its strongest few distinct signals, once each;
* one sentence pays for at most half of a component;
* an optional preference only adds: a fixed share per match, capped at the
  component's maximum, and naming more never lowers a posting;
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
# an optional preference only adds (readers-11, ADR-0036)
# =========================================================================

WORK_LINE = "You will reconcile invoices"
TOOL_LINES = ("Use ledgerbook", "Use gridsheet", "Use bankfeed", "Use taxpal")


def test_a_preference_not_mentioned_is_listed_and_costs_nothing() -> None:
    config = config_with()
    tools = scored(config, responsibilities(WORK_LINE, "Use ledgerbook"))[0]["technologies"]
    assert set(tools.not_found) == {"Gridsheet", "Bankfeed", "Taxpal"}
    assert tools.note and "takes nothing away" in tools.note
    assert all(c.points >= 0 for c in tools.contributions)


def test_naming_more_preferences_never_lowers_a_posting() -> None:
    """Every phrase removed, one at a time, from a full search: the score of
    each posting below never rises (removing a phrase is the reverse of
    adding one the posting may not mention)."""
    bodies = (
        responsibilities(WORK_LINE, *TOOL_LINES),
        responsibilities("You will reconcile invoices and run payroll", "Use ledgerbook"),
        responsibilities(*TOOL_LINES),
        "We sometimes reconcile invoices.\n" + responsibilities("Use gridsheet"),
    )
    full = config_with()
    for body in bodies:
        before = scored(full, body)[2]
        for kind, phrases in (("work", WORK), ("tools", TOOLS)):
            for removed in phrases:
                fewer = {k: v for k, v in phrases.items() if k != removed}
                config = config_with(**{kind: fewer})
                assert scored(config, body)[2] <= before, (removed, body)


def test_a_heavier_preference_does_not_lower_the_others() -> None:
    """Weights order which evidence counts first; they never scale it."""
    body = responsibilities(WORK_LINE, "Use ledgerbook")
    plain = config_with(tools={"t_ledger": "ledgerbook"})
    data = plain.model_dump()
    data["lexicon"]["t_sheet"] = {"label": "Gridsheet", "patterns": ["gridsheet"]}
    data["scoring"]["components"]["technologies"]["weights"] = {"t_ledger": 1.0, "t_sheet": 9.0}
    heavier = SearchConfig.model_validate(data)
    assert scored(heavier, body)[0]["technologies"].points == pytest.approx(
        scored(plain, body)[0]["technologies"].points
    )


def test_a_small_selection_fills_its_part_when_the_posting_shows_enough() -> None:
    """ONE wanted activity, a duty in four bullets of the role section, fills
    the work component; said once, it is one piece of four."""
    config = config_with(work={"w_invoices": "reconcile invoices"})
    four = responsibilities(
        "Reconcile invoices for the US entity",
        "Reconcile invoices for the EU entity",
        "Reconcile invoices against bank feeds",
        "Reconcile invoices at month end",
    )
    assert scored(config, four)[0]["responsibilities"].points == pytest.approx(25.0)
    once = scored(config, responsibilities(WORK_LINE))[0]["responsibilities"]
    assert once.points == pytest.approx(25.0 / COUNTED_SIGNALS["responsibilities"])
    assert once.fills_at == COUNTED_SIGNALS["responsibilities"]


def test_a_passing_mention_repeated_is_still_one_piece() -> None:
    """Only a DUTY repeated in the role's own section counts again; the same
    phrase scattered through company text does not."""
    config = config_with(work={"w_invoices": "reconcile invoices"})
    scattered = (
        "Our teams reconcile invoices together.\n\n"
        "Clients love how we reconcile invoices.\n\n"
        "We reconcile invoices in many currencies."
    )
    work = scored(config, scattered)[0]["responsibilities"]
    assert work.points <= 25.0 / COUNTED_SIGNALS["responsibilities"] + 1e-9


def test_the_component_maximum_is_the_explicit_limit() -> None:
    config = config_with()
    body = responsibilities(WORK_LINE, *TOOL_LINES, "Then use ledgerbook")
    components, _, score = scored(config, body)
    assert components["technologies"].points == pytest.approx(20.0)
    assert 0 <= score <= 100


def test_an_incidental_mention_is_a_partial_correspondence() -> None:
    config = config_with()
    central = scored(config, responsibilities(WORK_LINE, "Use ledgerbook"))[0]["technologies"]
    incidental = scored(config, WORK_LINE + ". We sometimes use ledgerbook.")[0]["technologies"]
    assert 0 < incidental.points < central.points


# =========================================================================
# tools read where the work is: the 2026-09-27 false positives
# =========================================================================
# Synthetic analogues of the benchmark's shapes (its postings are private):
# the tools named for other work, a generic line read as a partial finding,
# a passing mention of the work. Absence of a preference is not divergence.


def test_tools_without_the_wanted_work_as_a_duty_are_held_at_half() -> None:
    config = config_with()
    body = responsibilities(*TOOL_LINES)
    tools = scored(config, body)[0]["technologies"]
    assert tools.points == pytest.approx(10.0)
    assert tools.guarded and tools.note and "not a central duty" in tools.note


def test_tools_beside_the_wanted_work_count_in_full() -> None:
    config = config_with()
    tools = scored(config, responsibilities(WORK_LINE, *TOOL_LINES))[0]["technologies"]
    assert tools.points == pytest.approx(20.0) and not tools.guarded


def test_no_work_named_is_not_work_missing() -> None:
    """A person who named no work is not told the work was not found: the
    tools count in full. Absence of a preference is not divergent work."""
    config = config_with(work={})
    components = scored(config, responsibilities(*TOOL_LINES))[0]
    assert components["responsibilities"].configured is False
    assert components["technologies"].points == pytest.approx(20.0)


def test_a_tool_named_for_work_you_avoid_is_not_evidence() -> None:
    """Explicit divergent work: the tool sits in a sentence about something
    the person asked to avoid, and earns nothing there."""
    config = config_with(penalties={"p_cold": 3.0})
    body = responsibilities(WORK_LINE, "Make cold calls with ledgerbook", "Use gridsheet")
    tools = scored(config, body)[0]["technologies"]
    ledger = next(c for c in tools.contributions if c.signal_id == "t_ledger")
    assert not ledger.counted and "avoid" in (ledger.uncounted_reason or "")
    sheet = next(c for c in tools.contributions if c.signal_id == "t_sheet")
    assert sheet.counted


GENERIC = "Build scalable solutions with ledgerbook, gridsheet and bankfeed."
TOOLS_ONLY_BODY = responsibilities(GENERIC, "Use taxpal")


def test_a_partial_finding_on_a_generic_line_does_not_count_as_the_work() -> None:
    config = config_with()
    partial = SemanticMatch("responsibilities", "w_invoices", "W1", "partial", GENERIC)
    components, _, _ = scored(config, TOOLS_ONLY_BODY, semantic=evidence(partial))
    work, tools = components["responsibilities"], components["technologies"]
    assert work.points > 0 and work.contributions[0].counted
    assert tools.guarded and tools.points == pytest.approx(10.0)


def test_a_strong_finding_in_other_words_is_the_work() -> None:
    config = config_with()
    quote = "Match every supplier bill against the ledger before payment."
    strong = SemanticMatch("responsibilities", "w_invoices", "W1", "strong", quote)
    components, _, _ = scored(
        config, responsibilities(quote, GENERIC, "Use taxpal"), semantic=evidence(strong)
    )
    assert components["technologies"].points > 10.0 and not components["technologies"].guarded


def test_an_incidental_mention_of_the_work_does_not_lift_the_hold() -> None:
    config = config_with()
    body = "We sometimes reconcile invoices.\n" + TOOLS_ONLY_BODY
    components, _, _ = scored(config, body)
    assert components["responsibilities"].contributions[0].prominence.value == "INCIDENTAL"
    assert components["technologies"].points == pytest.approx(10.0)


def test_the_held_rows_add_up_to_what_the_component_scored() -> None:
    config = config_with()
    tools = scored(config, responsibilities(*TOOL_LINES))[0]["technologies"]
    paid = sum(c.points for c in tools.contributions if c.counted)
    assert paid == pytest.approx(tools.points)


def test_no_work_evidence_stays_unknown_not_a_mismatch() -> None:
    """No finding is no evidence: work scores 0 of its max and nothing is subtracted."""
    config = config_with()
    components, penalties, _ = scored(config, responsibilities("Use ledgerbook"))
    assert components["responsibilities"].points == 0 and not penalties
    assert components["technologies"].points == pytest.approx(20.0 / 3)


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


@pytest.mark.parametrize(
    ("preferred", "expected"),
    [(["MID"], 10.0), (["SENIOR"], 5.0), (["STAFF"], 2.0)],
)
def test_an_unstated_level_is_scored_as_mid(preferred: list[str], expected: float) -> None:
    """The MID fallback earns what a stated MID would, under each preference."""
    config = config_with(preferred=preferred)
    components, _, _ = scored(config, responsibilities("x"))
    assert components["seniority"].points == pytest.approx(expected)


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


def test_two_fragments_of_one_sentence_are_one_sentence() -> None:
    """The per-sentence rule compares SENTENCES, not whatever fragment a
    provider chose to quote."""
    config = config_with()
    sentence = "Keep the books balanced and the team paid."
    found = evidence(
        SemanticMatch("responsibilities", "w_invoices", "W1", "strong", "Keep the books", sentence),
        SemanticMatch("responsibilities", "w_payroll", "W2", "strong", "the team paid", sentence),
    )
    components, _, _ = scored(config, responsibilities(sentence), semantic=found)
    assert [c.counted for c in components["responsibilities"].contributions].count(True) == 1


def test_a_phrase_found_again_later_pays_from_its_own_sentence() -> None:
    config = config_with()
    body = responsibilities(
        "You will reconcile invoices and run payroll and onboard vendors",
        "Every Friday you will onboard vendors",
    )
    components, _, _ = scored(config, body)
    work = components["responsibilities"]
    assert [c.counted for c in work.contributions].count(True) == 3
    vendors = next(c for c in work.contributions if c.signal_id == "w_vendors")
    assert vendors.counted and "Friday" in (vendors.quote or "")


def test_capped_means_the_component_overflowed_not_that_a_rule_refused() -> None:
    config = config_with()
    body = responsibilities("You will reconcile invoices and run payroll and onboard vendors")
    components, _, _ = scored(config, body)
    work = components["responsibilities"]
    assert work.points < work.max_points and work.capped is False


def test_a_finding_cannot_pay_in_a_sentence_the_phrases_already_used() -> None:
    """The gate cuts sentences with the lexicon's own rule, so a semantic
    finding and a phrase hit in one sentence are one sentence."""
    from career_agent.match.text import sentence_at

    config = config_with()
    line = "You will reconcile invoices and keep the team paid"
    body = responsibilities(line)
    at = body.index("keep the team paid")
    sentence = sentence_at(body, at, at + len("keep the team paid"))
    found = evidence(
        SemanticMatch(
            "responsibilities", "w_payroll", "W2", "strong", "keep the team paid", sentence
        )
    )
    components, _, _ = scored(config, body, semantic=found)
    work = components["responsibilities"]
    payroll = next(c for c in work.contributions if c.signal_id == "w_payroll")
    assert payroll.counted is False


def test_a_partial_finding_pays_like_an_incidental_mention() -> None:
    """A partial finding is a secondary duty or a close neighbour; it pays at
    the INCIDENTAL multiplier, never at the secondary-section one."""
    from career_agent.domain.enums import Prominence
    from career_agent.match.score import SEMANTIC_PROMINENCE

    assert SEMANTIC_PROMINENCE == {"strong": Prominence.PRIMARY, "partial": Prominence.INCIDENTAL}
    config = config_with()
    partial = SemanticMatch("responsibilities", "w_invoices", "W1", "partial", "Keep the books")
    components, _, _ = scored(
        config, responsibilities("Keep the books."), semantic=evidence(partial)
    )
    incidental = config.scoring.components.responsibilities.prominence_multipliers[
        Prominence.INCIDENTAL
    ]
    assert components["responsibilities"].points == pytest.approx(25.0 / 4 * incidental)

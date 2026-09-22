"""The behavioural proofs, read from the corpus that carries them.

`evaluation/demo/demo_postings.yaml` holds nineteen invented postings, each
with an `expects` block naming what the matcher must conclude about it. This
file turns every one of those into a named, individually-reported test.

Writing the expectations beside the postings rather than in here is what makes
them maintainable: adding a hard case is one YAML entry, and when a lexicon
change breaks a judgement the failure says which posting and which judgement
rather than "assert 55 >= 60".

Several postings come in PAIRS that differ only in the body -- same shape of
title, opposite correct answer. A matcher that reads titles would pass a corpus
of easy cases and fail every pair here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import GateResult, Prominence
from career_agent.domain.matching import MatchResult, TitleAdjustment, TitleClass
from career_agent.match.engine import JobFacts, match_job
from career_agent.pipeline.demo_seed import load_demo_postings
from career_agent.pipeline.facts import job_facts

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
#: The committed configuration, copied without the owner's private
#: overrides. A test that read `search.local.yaml` would pass or fail on
#: what is in one person's gitignored file. See `tests/support.py`.
CONFIG_DIR = committed_config_dir()

_PROMINENCE_RANK = {Prominence.INCIDENTAL: 1, Prominence.SECONDARY: 2, Prominence.PRIMARY: 3}


def _config():
    config, _ = load_search_config(CONFIG_DIR)
    return config


def _facts(posting: dict[str, Any], default_provider: str) -> JobFacts:
    """The same derivation the seeder and a rescore use, and nothing else.

    This used to build its own, reading `employment_type` and a `salary` block
    straight out of the corpus file. Those keys are gone: a demo posting may
    only assert what the provider it imitates can publish, so the facts come
    from the archived payload through that provider's own reader.

    Building them here a third way would have put this file's judgement of a
    posting out of step with the database the browser tests drive, which is
    the exact shape of the defect this rule exists to end.
    """
    return job_facts(
        job_id=posting["external_id"],
        title=posting["title"],
        description=posting["description"],
        location_raw=posting.get("location_raw"),
        posted_at=posting.get("posted_at"),
        provider=str(posting.get("imitates_provider") or default_provider),
        payload=posting.get("payload"),
    )


def _score_all() -> dict[str, MatchResult]:
    config = _config()
    postings, default_provider = load_demo_postings(DEMO_FILE)
    return {
        p["external_id"]: match_job(
            config, _facts(p, default_provider), computed_at="2026-09-04T00:00:00Z"
        )
        for p in postings
    }


# Scored once. The matcher is a pure function, so there is nothing to isolate
# between tests and twenty re-runs would only be slower.
RESULTS: dict[str, MatchResult] = _score_all()
POSTINGS, DEFAULT_PROVIDER = load_demo_postings(DEMO_FILE)
#: Typed for the readers who look here first rather than at the loader.
POSTINGS: list[dict[str, Any]]
CASES = [(p["external_id"], p) for p in POSTINGS if p.get("expects")]


def _ids(case: tuple[str, dict]) -> str:
    return case[0]


# =========================================================================
# THE DECLARED EXPECTATIONS
# =========================================================================


@pytest.mark.parametrize("external_id,posting", CASES, ids=[c[0] for c in CASES])
def test_declared_expectations_hold(external_id: str, posting: dict[str, Any]) -> None:
    """Every `expects` key on every demo posting."""
    result = RESULTS[external_id]
    expects = posting["expects"]
    where = f"{external_id} ({posting['title']!r})"

    if "title_class" in expects:
        assert result.title is not None
        assert result.title.resolved_class == TitleClass(expects["title_class"]), (
            f"{where}: title resolved to {result.title.resolved_class}, "
            f"expected {expects['title_class']}. Reason given: {result.title.reason}"
        )

    if "title_adjustment" in expects:
        assert result.title is not None
        assert result.title.adjustment == TitleAdjustment(expects["title_adjustment"]), (
            f"{where}: adjustment was {result.title.adjustment}, "
            f"expected {expects['title_adjustment']}"
        )

    if "eligibility_status" in expects:
        assert str(result.eligibility_status) == expects["eligibility_status"], (
            f"{where}: eligibility is {result.eligibility_status}, "
            f"expected {expects['eligibility_status']}. Gates: "
            f"{[(g.gate, str(g.result)) for g in result.gates]}"
        )

    if "screening_state" in expects:
        assert str(result.screening_state) == expects["screening_state"], where

    if "gate_fail" in expects:
        failed = {g.gate for g in result.blockers}
        assert expects["gate_fail"] in failed, (
            f"{where}: expected the {expects['gate_fail']} gate to FAIL, "
            f"failing gates were {failed or 'none'}"
        )

    if "min_score" in expects:
        assert result.match_score >= expects["min_score"], (
            f"{where}: scored {result.match_score}, expected at least {expects['min_score']}"
        )

    if "max_score" in expects:
        assert result.match_score <= expects["max_score"], (
            f"{where}: scored {result.match_score}, expected at most {expects['max_score']}"
        )

    if "min_confidence" in expects:
        assert result.data_confidence >= expects["min_confidence"], where

    if "max_confidence" in expects:
        assert result.data_confidence <= expects["max_confidence"], (
            f"{where}: confidence {result.data_confidence}, "
            f"expected at most {expects['max_confidence']}"
        )

    # -- what the posting asked of a newcomer (migration 0027) -------------
    #
    # Asserted as three separate keys because they are three separate facts.
    # `experience_min_years` in particular is checked with `in expects` rather
    # than truthiness: 0 is a real answer and means the employer said none is
    # needed, and `if expects.get(...)` would silently skip exactly the case
    # this reading exists for.
    if "experience_requirement" in expects:
        assert str(result.experience.requirement) == expects["experience_requirement"], (
            f"{where}: experience reads {result.experience.requirement}, "
            f"expected {expects['experience_requirement']}. "
            f"Quote: {result.experience.quote!r}"
        )

    if "experience_min_years" in expects:
        assert result.experience.min_years == expects["experience_min_years"], (
            f"{where}: minimum reads {result.experience.min_years}, "
            f"expected {expects['experience_min_years']}"
        )

    if "entry_signals" in expects:
        # An EXACT set, not a subset. A posting that gains an invitation nobody
        # wrote is as wrong as one that loses an invitation it did write, and
        # `must_fire` below cannot catch the first kind.
        found = sorted(str(signal) for signal in result.experience.entry_signals)
        assert found == sorted(expects["entry_signals"]), (
            f"{where}: entry signals are {found}, expected {sorted(expects['entry_signals'])}"
        )

    fired = {s.signal_id for s in result.signals if s.fired}
    for signal_id in expects.get("must_fire", []):
        assert signal_id in fired, f"{where}: signal {signal_id!r} did not fire"

    penalised = {p.signal_id for p in result.penalties}
    for signal_id in expects.get("no_penalty", []):
        applied = [(pen.signal_id, pen.points) for pen in result.penalties]
        assert signal_id not in penalised, f"{where}: {signal_id!r} penalised. All: {applied}"
    for signal_id in expects.get("has_penalty", []):
        assert signal_id in penalised, f"{where}: expected a {signal_id!r} penalty, got none"

    if "negated_signal" in expects:
        target = expects["negated_signal"]
        observed = {s.signal_id: s for s in result.signals}
        assert target in observed, f"{where}: {target!r} was not observed at all"
        assert observed[target].negated_hits, (
            f"{where}: expected a NEGATED hit for {target!r}; the posting says it is not required"
        )
        assert not observed[target].hits, f"{where}: {target!r} also fired un-negated"

    if "scores_below" in expects:
        other = RESULTS[expects["scores_below"]]
        assert result.match_score < other.match_score, (
            f"{where}: scored {result.match_score}, expected below "
            f"{expects['scores_below']} at {other.match_score}"
        )

    if "same_score_as" in expects:
        # A DELIBERATE tie, not a coincidence. The duplicate trio scores
        # identically by construction -- only prose that fires no signal
        # differs between the three -- which is what pushes the representative
        # election onto its tie-break, `MIN(j.id)`. If a lexicon change makes
        # one sibling score higher, the card a reviewer sees changes identity
        # and every screenshot silently stops being about demo-001.
        other = RESULTS[expects["same_score_as"]]
        assert result.match_score == other.match_score, (
            f"{where}: scored {result.match_score}, expected the same as "
            f"{expects['same_score_as']} at {other.match_score}. The tie is what "
            "makes the representative election fall through to the lowest job id."
        )

    if expects.get("not_excluded"):
        assert result.title is not None
        assert result.title.resolved_class is not TitleClass.EXCLUDED, where

    if expects.get("salary_absent_not_penalised"):
        component = next(c for c in result.components if c.component_id == "compensation_contract")
        assert component.points > 0, (
            f"{where}: a posting with no salary scored 0 on compensation. "
            "Silence about pay is not a reason to reject a job."
        )


# =========================================================================
# CROSS-CUTTING PROOFS
# =========================================================================


def test_every_quote_is_a_real_substring_of_the_posting() -> None:
    """ADR-0002 over the whole corpus, not one posting.

    Patterns are matched against a casefolded, accent-stripped copy of the
    text. If the offset map were wrong anywhere, a quote would drift -- and
    Portuguese postings, where folding changes character counts, are exactly
    where it would drift first.
    """
    for posting in POSTINGS:
        result = RESULTS[posting["external_id"]]
        haystack = posting["title"] + "\n" + posting["description"]
        for signal in result.signals:
            for hit in list(signal.hits) + list(signal.negated_hits):
                if hit.quote.endswith("…"):
                    continue  # truncated by design
                assert hit.quote in haystack, (
                    f"{posting['external_id']}: quote {hit.quote!r} from {hit.signal_id} "
                    "is not in the posting"
                )
        for gate in result.gates:
            if gate.quote and not gate.quote.endswith("…"):
                assert gate.quote in haystack, (
                    f"{posting['external_id']}: gate {gate.gate} quotes text that is not there"
                )


def test_a_matching_title_never_overrides_an_eligibility_blocker() -> None:
    """demo-009 is a PRIMARY title that scores well and is still not eligible."""
    result = RESULTS["demo-009"]
    assert result.title is not None
    assert result.title.resolved_class is TitleClass.PRIMARY
    assert result.match_score >= 50, "the title and body genuinely are on target"
    assert str(result.eligibility_status) == "VERIFIED_NOT_ELIGIBLE"
    assert any(g.gate == "geography" and g.result is GateResult.FAIL for g in result.gates)


def test_incidental_salesforce_scores_positively_and_a_salesforce_title_does_not() -> None:
    """The same word, weighted by where it sits. principles.md section 4."""
    incidental = RESULTS["demo-011"]
    centred = RESULTS["demo-010"]

    assert "salesforce_centred" not in {p.signal_id for p in incidental.penalties}
    assert "tool_stack" in {s.signal_id for s in incidental.signals if s.fired}

    assert centred.title is not None
    assert centred.title.resolved_class is TitleClass.EXCLUDED
    # The title-only penalty is gone. What separates these two is now entirely
    # what their BODIES describe, which is the stronger version of the same
    # claim: one posting mentions Salesforce among six tools while doing the
    # work, the other is an administration role for it.
    assert "salesforce_centred" not in {p.signal_id for p in centred.penalties}
    assert incidental.match_score > centred.match_score + 40


def test_the_same_title_resolves_two_ways_on_the_body_alone() -> None:
    """Three pairs, each differing only in the description."""
    pairs = [("demo-002", "demo-003"), ("demo-007", "demo-008"), ("demo-004", "demo-005")]
    for on_target, off_target in pairs:
        good, bad = RESULTS[on_target], RESULTS[off_target]
        assert good.match_score > bad.match_score, (
            f"{on_target} ({good.match_score}) should outscore "
            f"{off_target} ({bad.match_score}); their titles are near-identical"
        )


def test_the_duplicate_trio_is_one_role_told_three_ways() -> None:
    """demo-001/018/019 are groupable by `(company, title)` and NOT by text.

    The corpus makes this point because the product depends on it: only 8 of
    31 surplus rows in the real corpus were byte-identical, so a grouping key
    built on `content_hash` would find almost none of them. Three demo bodies
    that happened to be identical would quietly agree with the wrong design.
    """
    trio = [p for p in POSTINGS if p["external_id"] in {"demo-001", "demo-018", "demo-019"}]
    assert len(trio) == 3

    assert len({p["company"]["slug"] for p in trio}) == 1, "the grouping key needs one company"
    assert len({p["title"] for p in trio}) == 1, "the grouping key needs one title"
    assert len({p["description"] for p in trio}) == 3, "identical bodies would prove nothing"
    assert len({p["location_raw"] for p in trio}) == 3, "each listing names its own place"

    # And the work described really is the same, so the tie is earned rather
    # than arranged: same fired signals, same score, same eligibility answer.
    results = [RESULTS[p["external_id"]] for p in trio]
    fired = [frozenset(s.signal_id for s in r.signals if s.fired) for r in results]
    assert len(set(fired)) == 1, f"the three listings observe different work: {fired}"
    assert len({r.match_score for r in results}) == 1
    assert len({str(r.eligibility_status) for r in results}) == 1


def test_matching_is_pure() -> None:
    """The same posting scored twice is the same result, field for field."""
    config = _config()
    posting = POSTINGS[0]
    first = match_job(config, _facts(posting, DEFAULT_PROVIDER), computed_at="2026-09-04T00:00:00Z")
    second = match_job(
        config, _facts(posting, DEFAULT_PROVIDER), computed_at="2026-09-04T00:00:00Z"
    )
    assert first == second


def test_no_component_exceeds_its_maximum_and_the_total_is_bounded() -> None:
    for external_id, result in RESULTS.items():
        assert 0 <= result.match_score <= 100, external_id
        assert 0 <= result.data_confidence <= 100, external_id
        for component in result.components:
            assert component.points <= component.max_points + 1e-9, (
                f"{external_id}: {component.component_id} scored "
                f"{component.points} of {component.max_points}"
            )


def test_a_thin_posting_loses_confidence_without_losing_score() -> None:
    """The two measurements move independently, which is the whole point."""
    thin = RESULTS["demo-017"]
    rich = RESULTS["demo-001"]
    assert thin.data_confidence < 45, "almost nothing is known about demo-017"
    assert thin.match_score > 25, "and what IS known is on target"
    assert rich.data_confidence > thin.data_confidence + 40
    assert thin.unknowns, "the posting's silences must be named, not implied"


def test_unresolved_gates_are_reported_rather_than_assumed_away() -> None:
    unresolved = RESULTS["demo-017"]
    assert str(unresolved.eligibility_status) == "UNRESOLVED"
    assert any(g.result is GateResult.UNRESOLVED for g in unresolved.gates)
    assert any("hire" in g.reason.lower() or "state" in g.reason.lower() for g in unresolved.gates)


def test_portuguese_postings_are_first_class() -> None:
    """demo-012 is written in Portuguese, with accents, and must score."""
    result = RESULTS["demo-012"]
    fired = {s.signal_id for s in result.signals if s.fired}
    assert {"workflow_automation", "system_integration", "api_integration"} <= fired
    assert result.match_score >= 50


def test_prominence_is_measured_and_not_flat() -> None:
    """If every signal came back INCIDENTAL, the multipliers would be theatre."""
    seen: set[Prominence] = set()
    for result in RESULTS.values():
        seen.update(s.prominence for s in result.signals if s.fired)
    assert len(seen) >= 2, f"only {seen} ever observed; prominence is not discriminating"
    assert max(_PROMINENCE_RANK[p] for p in seen) == _PROMINENCE_RANK[Prominence.PRIMARY]

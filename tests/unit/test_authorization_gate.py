"""Explicit eligibility declarations against what a posting requires.

Synthetic postings. Each requirement is read from the posting and answered
by what the candidate DECLARED: compatible, incompatible, or pending when she
has not said. Where she lives, or a nationality, answers nothing.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.domain.enums import (
    AnalysisConfidence,
    EligibilityStatus,
    EmploymentRelationship,
    EmploymentSource,
    GateResult,
    LocalContractRegime,
)
from career_agent.domain.matching import EmploymentReading
from career_agent.match.gates import eligibility_status_from, evaluate_gates
from career_agent.match.places import resolve_place

US_AUTH = "Candidates must be authorized to work in the United States."
#: A quote is the employer's sentence as cut from the text, its full stop left out.
QUOTE = US_AUTH.rstrip(".")
NO_SPONSOR = "Please note that we do not sponsor visas for this role."
CITIZEN = "US citizenship required for this position."


def config(**declared: Any) -> SearchConfig:
    """The committed configuration with no blockers and these declarations."""
    base, _ = load_search_config(committed_config_dir())
    data = base.model_dump()
    data["eligibility"] = {**data["eligibility"], "blockers": [], "eligible_countries": [],
                           "candidate_country": "BR", **declared}  # fmt: skip
    return SearchConfig.model_validate(data)


def gate(cfg: SearchConfig, text: str, location: str | None = None, employment=None):
    gates = evaluate_gates(cfg, {}, "Analyst", text, place=resolve_place(location),
                           location_raw=location, employment=employment)  # fmt: skip
    return next(g for g in gates if g.gate == "work_authorization"), gates


def test_a_stated_requirement_nobody_answered_is_pending_and_quoted() -> None:
    outcome, gates = gate(config(), f"Remote role. {US_AUTH}")
    assert outcome.result is GateResult.UNRESOLVED
    assert outcome.quote == QUOTE and "have not said" in outcome.reason
    assert eligibility_status_from(gates) is EligibilityStatus.UNRESOLVED


def test_where_the_candidate_lives_answers_nothing_about_authorization() -> None:
    lives_there = config(candidate_country="US")
    outcome, _ = gate(lives_there, US_AUTH)
    assert outcome.result is GateResult.UNRESOLVED, "residence is not a work authorization"


@pytest.mark.parametrize(
    ("declared", "result"),
    [
        ({"eligible_countries": ["US"]}, GateResult.PASS),
        ({"not_eligible_countries": ["US"]}, GateResult.FAIL),
    ],
)
def test_a_declared_authorization_answers_it(declared: dict, result: GateResult) -> None:
    outcome, gates = gate(config(**declared), US_AUTH)
    assert outcome.result is result and outcome.quote == QUOTE
    if result is GateResult.FAIL:
        assert eligibility_status_from(gates) is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_no_sponsorship_against_a_declared_need_in_the_postings_country() -> None:
    needs = config(sponsorship_needed=["US"])
    outcome, _ = gate(needs, NO_SPONSOR, location="New York, NY, United States")
    assert outcome.result is GateResult.FAIL and "sponsorship" in outcome.reason
    authorized = config(eligible_countries=["US"])
    assert gate(authorized, NO_SPONSOR, "New York, NY, United States")[0].result is GateResult.PASS
    assert gate(config(), NO_SPONSOR, "New York, NY, United States")[0].result is (
        GateResult.UNRESOLVED
    )


def test_a_sponsorship_sentence_with_no_country_stays_pending() -> None:
    outcome, _ = gate(config(sponsorship_needed=["US"]), NO_SPONSOR)
    assert outcome.result is GateResult.UNRESOLVED and "which country" in outcome.reason


def test_citizenship_is_never_answered_by_an_authorization() -> None:
    assert gate(config(eligible_countries=["US"]), CITIZEN)[0].result is GateResult.UNRESOLVED
    assert gate(config(not_eligible_countries=["US"]), CITIZEN)[0].result is GateResult.FAIL


def test_a_place_that_is_not_a_country_is_not_a_requirement() -> None:
    outcome, _ = gate(config(), "You will be eligible to work in our hybrid model from day one.")
    assert outcome.result is GateResult.UNRESOLVED
    assert outcome.reason == "The posting does not state whether work authorization is required."


def test_portuguese_requirements_are_read_too() -> None:
    text = "Requisito: autorização para trabalhar no Brasil."
    assert gate(config(eligible_countries=["BR"]), text)[0].result is GateResult.PASS
    assert gate(config(), text)[0].result is GateResult.UNRESOLVED


def _contractor(source: EmploymentSource) -> EmploymentReading:
    return EmploymentReading(
        relationship=EmploymentRelationship.CONTRACTOR_B2B,
        regime=LocalContractRegime.UNRESOLVED,
        source=source,
        confidence=AnalysisConfidence.HIGH,
        evidence="This is an independent contractor engagement.",
    )


@pytest.mark.parametrize(
    ("answer", "result"),
    [(False, GateResult.FAIL), (True, GateResult.PASS), (None, GateResult.UNRESOLVED)],
)
def test_an_explicit_contractor_engagement_is_answered_by_the_declaration(
    answer: bool | None, result: GateResult
) -> None:
    reading = _contractor(EmploymentSource.EXPLICIT_STATEMENT)
    outcome, _ = gate(config(contractor=answer), "Analyst role.", employment=reading)
    assert outcome.result is result


def test_a_likely_engagement_is_never_a_requirement() -> None:
    reading = _contractor(EmploymentSource.BENEFIT_CONTEXT)
    outcome, _ = gate(config(contractor=False), "Analyst role.", employment=reading)
    assert outcome.result is GateResult.UNRESOLVED
    assert outcome.reason == "The posting does not state whether work authorization is required."


def test_a_configured_blocker_still_decides_first() -> None:
    base, _ = load_search_config(committed_config_dir())  # the worked example's US blocker
    data = base.model_dump()
    data["eligibility"] = {**data["eligibility"], "eligible_countries": ["BR", "US"]}
    declared = SearchConfig.model_validate(data)
    outcome, _ = gate(declared, US_AUTH)
    assert outcome.result is GateResult.FAIL
    assert outcome.blocker_id == "us_work_authorization_required"


@pytest.mark.parametrize(
    "declared",
    [
        {"eligible_countries": ["US"], "not_eligible_countries": ["US"]},
        {"eligible_countries": ["US"], "sponsorship_needed": ["us"]},
    ],
)
def test_contradictory_declarations_are_refused(declared: dict) -> None:
    with pytest.raises(ValueError, match="declared both"):
        config(**declared)

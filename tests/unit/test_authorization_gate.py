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
    """The committed configuration with no blockers, no declarations of its
    own (the worked example answers some), and these."""
    base, _ = load_search_config(committed_config_dir())
    data = base.model_dump()
    neutral = {"blockers": [], "eligible_countries": [], "candidate_country": "BR",
               "authorized_countries": [], "not_authorized_countries": [],
               "sponsorship_needed": [], "contractor": None, "contractor_abroad": None}  # fmt: skip
    data["eligibility"] = {**data["eligibility"], **neutral, **declared}
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


def test_an_old_profile_where_employers_may_hire_from_is_not_an_authorization() -> None:
    """A search written before the declarations, saying US employers may hire
    this person (`eligible_countries`, a location fact read by the geography
    gate), declares nothing about a right to be EMPLOYED in the US."""
    import yaml

    old = yaml.safe_load(
        (committed_config_dir() / "search.worked-example.yaml").read_text(encoding="utf-8")
    )
    for key in ("authorized_countries", "not_authorized_countries", "sponsorship_needed",
                "contractor", "contractor_abroad"):  # fmt: skip
        old["eligibility"].pop(key, None)
    old["eligibility"].update(blockers=[], eligible_countries=["BR", "US"])
    cfg = SearchConfig.model_validate(old)
    outcome, gates = gate(cfg, US_AUTH)
    assert outcome.result is GateResult.UNRESOLVED and "have not said" in outcome.reason
    assert eligibility_status_from(gates) is EligibilityStatus.UNRESOLVED
    sponsor, _ = gate(cfg, NO_SPONSOR, "New York, NY, United States")
    assert sponsor.result is GateResult.UNRESOLVED
    # The location fact still does what it always did: a US-only hiring scope
    # admits this person on geography.
    scoped = evaluate_gates(cfg, {}, "Analyst", "Remote role.", declared_scope="United States")
    assert next(g for g in scoped if g.gate == "geography").result is GateResult.PASS


@pytest.mark.parametrize(
    ("declared", "result"),
    [
        ({"authorized_countries": ["US"]}, GateResult.PASS),
        ({"not_authorized_countries": ["US"]}, GateResult.FAIL),
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
    authorized = config(authorized_countries=["US"])
    assert gate(authorized, NO_SPONSOR, "New York, NY, United States")[0].result is GateResult.PASS
    assert gate(config(), NO_SPONSOR, "New York, NY, United States")[0].result is (
        GateResult.UNRESOLVED
    )


def test_a_sponsorship_sentence_with_no_country_stays_pending() -> None:
    outcome, _ = gate(config(sponsorship_needed=["US"]), NO_SPONSOR)
    assert outcome.result is GateResult.UNRESOLVED and "which country" in outcome.reason


def test_citizenship_is_never_answered_by_an_authorization() -> None:
    assert gate(config(authorized_countries=["US"]), CITIZEN)[0].result is GateResult.UNRESOLVED
    assert gate(config(not_authorized_countries=["US"]), CITIZEN)[0].result is GateResult.FAIL


def test_a_place_that_is_not_a_country_is_not_a_requirement() -> None:
    outcome, _ = gate(config(), "You will be eligible to work in our hybrid model from day one.")
    assert outcome.result is GateResult.UNRESOLVED
    assert outcome.reason == "The posting does not state whether work authorization is required."


def test_portuguese_requirements_are_read_too() -> None:
    text = "Requisito: autorização para trabalhar no Brasil."
    assert gate(config(authorized_countries=["BR"]), text)[0].result is GateResult.PASS
    assert gate(config(), text)[0].result is GateResult.UNRESOLVED


def _contractor(source: EmploymentSource) -> EmploymentReading:
    return EmploymentReading(
        relationship=EmploymentRelationship.CONTRACTOR_B2B,
        regime=LocalContractRegime.UNRESOLVED,
        source=source,
        confidence=AnalysisConfidence.HIGH,
        evidence="This is an independent contractor engagement.",
    )


BRAZIL, ABROAD = "Sao Paulo, Brazil", "New York, NY, United States"


@pytest.mark.parametrize(
    ("declared", "location", "result"),
    [
        # Cannot work as a contractor at all: refused wherever the client is.
        ({"contractor": False}, None, GateResult.FAIL),
        ({"contractor": False}, ABROAD, GateResult.FAIL),
        # Available as a contractor, client country not stated: the context
        # that decides home from abroad is missing, so it waits.
        ({"contractor": True}, None, GateResult.UNRESOLVED),
        ({"contractor": True, "contractor_abroad": True}, None, GateResult.UNRESOLVED),
        # A client where she lives: the home answer counts.
        ({"contractor": True}, BRAZIL, GateResult.PASS),
        ({}, BRAZIL, GateResult.UNRESOLVED),
        # A client abroad: availability at home does not answer it.
        ({"contractor": True}, ABROAD, GateResult.UNRESOLVED),
        ({"contractor": True, "contractor_abroad": True}, ABROAD, GateResult.PASS),
        ({"contractor": True, "contractor_abroad": False}, ABROAD, GateResult.FAIL),
        # Where she lives not said: home or abroad is not known.
        ({"contractor": True, "contractor_abroad": True, "candidate_country": ""}, ABROAD,
         GateResult.UNRESOLVED),
    ],
)  # fmt: skip
def test_a_contractor_engagement_is_answered_in_the_clients_context(
    declared: dict, location: str | None, result: GateResult
) -> None:
    reading = _contractor(EmploymentSource.EXPLICIT_STATEMENT)
    outcome, _ = gate(config(**declared), "Analyst role.", location, employment=reading)
    assert outcome.result is result, outcome.reason


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
        {"authorized_countries": ["US"], "not_authorized_countries": ["US"]},
        {"authorized_countries": ["US"], "sponsorship_needed": ["us"]},
        {"contractor": False, "contractor_abroad": True},
    ],
)
def test_contradictory_declarations_are_refused(declared: dict) -> None:
    with pytest.raises(ValueError, match="declared both"):
        config(**declared)

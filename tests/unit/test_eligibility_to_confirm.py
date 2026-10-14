"""High fit never outranks eligibility, and a contradiction is shown, not resolved.

Synthetic analogues of three postings shown to a candidate in Brazil as
eligible on 2026-10-13 (readers-12):

* a role published as `United States`, whose only "worldwide" was
  "Work from anywhere" among the perks, with a security clearance the role
  "may require" and export-controlled information (EAR/ITAR);
* two roles whose adverts said "should be based in Brazil" and, under
  Location, "can be executed from anywhere in Colombia" (or Mexico).

Each case has a control beside it, so no rule passes by refusing everything.
"""

from __future__ import annotations

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.match.engine import JobFacts, match_job
from career_agent.match.gates import pending_gates

CONFIG, _ = load_search_config(committed_config_dir())

PERKS = (
    "Why join us\n\n"
    "- Remote-first culture: Work from anywhere with a globally distributed team\n"
    "- Open source at the core\n"
)
SECURITY = (
    "U.S. Eligibility & Compliance\n\n"
    "This role may require obtaining and maintaining a U.S. government security clearance. "
    "Candidates must meet federal eligibility requirements to be considered.\n\n"
    "Applicants must meet eligibility requirements for access to export-controlled information "
    "as defined by U.S. export control laws, including EAR and ITAR.\n"
)


def run(body: str, **facts: object):
    result = match_job(
        CONFIG, JobFacts(title="Senior Engineer", description=body, **facts), computed_at="frozen"
    )
    for gate in result.gates:
        if gate.quote:
            assert gate.quote in body, "a quote is a substring that exists (ADR-0002)"
        for line in gate.conflict:
            assert line in body, "both conflicting lines are the posting's own words"
    return result


def gate(result, name: str):
    return next(g for g in result.gates if g.gate == name)


def test_perks_prose_does_not_certify_a_role_published_elsewhere() -> None:
    result = run("Build the platform in Go and React.\n\n" + PERKS, location_raw="United States")
    geography = gate(result, "geography")
    assert geography.result is GateResult.UNRESOLVED
    assert geography.code == "PUBLISHED_LOCATION_ELSEWHERE"
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED
    # Control: the same perks with no published place still admit.
    assert run("Build the platform.\n\n" + PERKS).eligibility_status is (
        EligibilityStatus.VERIFIED_ELIGIBLE
    )


def test_a_clearance_the_role_may_need_is_a_requirement_to_confirm_never_a_refusal() -> None:
    result = run("We hire globally.\n\n" + SECURITY)
    clearance = gate(result, "clearance")
    assert clearance.result is GateResult.UNRESOLVED
    assert clearance.code == "US_SECURITY_REQUIREMENTS"
    assert clearance.quote and "security clearance" in clearance.quote
    assert "U.S." in clearance.quote, "an abbreviation does not end the quoted sentence"
    assert clearance in pending_gates(result.gates)
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED
    assert not any(g.result is GateResult.FAIL for g in result.gates), (
        "EAR/ITAR or 'may require clearance' alone do not prove citizenship is required"
    )


def test_the_whole_mattermost_shape_is_to_confirm_and_keeps_its_score() -> None:
    body = "Build features in Go and React.\n\n" + PERKS + "\n" + SECURITY
    result = run(body, location_raw="United States")
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED
    codes = {g.code for g in result.gates if g.code}
    assert codes == {"PUBLISHED_LOCATION_ELSEWHERE", "US_SECURITY_REQUIREMENTS"}


def test_security_words_without_a_requirement_say_nothing() -> None:
    result = run("We build secure clearance workflows for defence customers. We hire globally.")
    assert gate(result, "clearance").code is None
    assert result.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_conflicting_explicit_locations_are_both_quoted() -> None:
    body = (
        "This role is remote but the successful candidate should be based in Brazil.\n\n"
        "Location\n\nThis role is remote-first, so it can be executed from anywhere in "
        "Colombia, with a preference of Bogota. Expect regular travel."
    )
    result = run(body, location_raw="Colombia")
    geography = gate(result, "geography")
    assert geography.result is GateResult.UNRESOLVED
    assert geography.code == "CONFLICTING_LOCATIONS"
    assert len(geography.conflict) == 2
    assert "based in Brazil" in geography.conflict[0]
    assert "anywhere in Colombia" in geography.conflict[1]
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED


def test_where_the_work_is_done_is_read_alone_too() -> None:
    body = "Location\n\nThis role can be executed from anywhere in Mexico."
    assert run(body).eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    brazil = "Location\n\nThis role can be executed from anywhere in Brazil."
    assert run(brazil).eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_an_explicit_brazil_or_latam_statement_still_admits() -> None:
    assert (
        run("Candidates must be based in Brazil.", location_raw="Brazil").eligibility_status
        is EligibilityStatus.VERIFIED_ELIGIBLE
    )
    assert (
        run("This role is open to candidates based in Latin America.").eligibility_status
        is EligibilityStatus.VERIFIED_ELIGIBLE
    )


def test_a_time_zone_is_not_a_place_and_sponsorship_offered_is_not_a_refusal() -> None:
    body = (
        "We hire globally. You must overlap four hours with US Eastern time. "
        "We offer visa sponsorship where needed."
    )
    result = run(body)
    assert result.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE
    assert not any(g.result is GateResult.FAIL for g in result.gates)


def test_silence_on_authorization_is_not_left_to_confirm() -> None:
    result = run("We hire globally. Build integrations.")
    assert gate(result, "work_authorization") not in pending_gates(result.gates)
    assert gate(result, "clearance") not in pending_gates(result.gates)

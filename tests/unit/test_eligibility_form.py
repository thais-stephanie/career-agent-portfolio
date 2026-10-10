"""The eligibility form (2026-10-09, ADR-0034): what each answer does.

Where she lives, how she wants to work, which markets come first and where she
KNOWS she can be employed are four different facts. These tests pin what each
one may decide, for two invented candidates, so a later change cannot let a
market preference become a permission or a published location become a
refusal. Nothing here is anybody's real configuration: both candidates are
built from the committed worked example.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus, GateResult
from career_agent.home import declarations_pending, profile_gaps
from career_agent.match import JobFacts, match_job

ROOT = Path(__file__).resolve().parents[2]
BODY = "You will build integrations between internal systems and own their data quality."


@pytest.fixture(scope="module")
def base():
    directory = Path(tempfile.mkdtemp())
    shutil.copy(ROOT / "config" / "search.worked-example.yaml", directory)
    shutil.copy(ROOT / "config" / "places.yaml", directory)
    loaded, _ = load_search_config(directory, use_example=True)
    return loaded


def person(base, *, lives, authorized=(), markets=(), legacy=(), scopes=(), **more):
    eligibility = base.eligibility.model_copy(
        update={
            "candidate_country": lives,
            "candidate_country_label": lives,
            "authorized_countries": list(authorized),
            "eligible_countries": list(legacy),
            "eligible_scopes": list(scopes),
            "not_authorized_countries": [],
            "sponsorship_needed": [],
            # The worked example's own hard exclusions (one names US work
            # authorization) are a different, configured rule: off here, so
            # these tests read only what the form's answers decide.
            "blockers": [],
            **more,
        }
    )
    regions = base.regions.model_copy(update={"target": list(markets)})
    return base.model_copy(update={"eligibility": eligibility, "regions": regions})


def gates(config, *, location, workplace="REMOTE", body=""):
    result = match_job(
        config,
        JobFacts(
            title="Integration Engineer",
            description=f"{BODY} {body}".strip(),
            location_raw=location,
            workplace_type=workplace,
        ),
        computed_at="2026-10-09T00:00:00Z",
    )
    return result.eligibility_status, {g.gate: g for g in result.gates}


@pytest.fixture(scope="module")
def owner_example(base):
    """The validation example: lives in Brazil, knows she can be employed in
    Brazil, wants the United States, Europe and Canada first."""
    return person(base, lives="BR", authorized=["BR"], markets=["NORTH_AMERICA", "EUROPE"])


@pytest.fixture(scope="module")
def other(base):
    """A different profile: lives in Portugal, can be employed in Portugal and
    Spain, wants Latin America first, and is open to moving."""
    return person(base, lives="PT", authorized=["PT", "ES"], markets=["LATAM"])


# =========================================================================
# 1. WHAT EACH ANSWER DOES, FOR THE VALIDATION EXAMPLE
# =========================================================================


@pytest.mark.parametrize("location", ["Worldwide", "Remote - LATAM", "Remote - Brazil"])
def test_where_she_lives_and_is_authorized_opens_remote_roles_that_include_it(
    owner_example, location
) -> None:
    status, found = gates(owner_example, location=location)
    assert found["geography"].result is GateResult.PASS, found["geography"].reason


@pytest.mark.parametrize("location", ["Remote U.S.", "Remote - Europe", "Remote (Canada)"])
def test_a_foreign_published_location_is_to_confirm_never_a_refusal(owner_example, location):
    """Her preferred markets do not open these either: a market is an order of
    search, not an authorization."""
    status, found = gates(owner_example, location=location)
    assert found["geography"].result is GateResult.UNRESOLVED, found["geography"].reason
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_an_explicit_residence_requirement_refuses_with_its_sentence(owner_example) -> None:
    status, found = gates(
        owner_example, location="Remote", body="Candidates must reside in the EU."
    )
    assert found["geography"].result is GateResult.FAIL
    assert "reside in the EU" in (found["geography"].quote or "")
    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_an_explicit_authorization_she_did_not_declare_is_left_to_confirm(owner_example) -> None:
    status, found = gates(
        owner_example,
        location="Remote",
        body="You must be authorized to work in the United States.",
    )
    gate = found["work_authorization"]
    assert gate.result is GateResult.UNRESOLVED
    assert "authorized to work in the United States" in (gate.quote or "")
    assert status is not EligibilityStatus.VERIFIED_ELIGIBLE


def test_an_authorization_she_declared_answers_it(owner_example) -> None:
    _, found = gates(
        owner_example, location="Remote", body="You must be authorized to work in Brazil."
    )
    assert found["work_authorization"].result is GateResult.PASS


def test_markets_change_no_verdict(base) -> None:
    """The same person with and without preferred markets reads every job the
    same way (ADR-0032)."""
    with_markets = person(base, lives="BR", authorized=["BR"], markets=["NORTH_AMERICA", "EUROPE"])
    without = person(base, lives="BR", authorized=["BR"])
    for location in ("Remote U.S.", "Worldwide", "Lisbon, Portugal", "Remote - Europe"):
        for workplace in ("REMOTE", "ONSITE"):
            assert gates(with_markets, location=location, workplace=workplace) == gates(
                without, location=location, workplace=workplace
            ), (location, workplace)


# =========================================================================
# 2. ANOTHER PROFILE: ON-SITE AND HYBRID, AUTHORIZED ELSEWHERE
# =========================================================================


def test_an_onsite_job_where_she_is_authorized_is_not_refused(other) -> None:
    """Madrid: she does not live there, but said she can be employed in Spain.
    Whether she moves is hers to decide, not ours to refuse."""
    _, found = gates(other, location="Madrid, Spain", workplace="ONSITE")
    assert found["geography"].result is not GateResult.FAIL


def test_a_hybrid_job_where_she_is_not_authorized_and_does_not_live_is_refused(other) -> None:
    _, found = gates(other, location="Berlin, Germany", workplace="HYBRID")
    assert found["geography"].result is GateResult.FAIL
    assert "Berlin" in (found["geography"].reason or "")


def test_an_onsite_job_where_she_lives_is_not_refused(other) -> None:
    _, found = gates(other, location="Lisbon, Portugal", workplace="ONSITE")
    assert found["geography"].result is not GateResult.FAIL


def test_authorization_elsewhere_does_not_open_a_remote_role_there(other) -> None:
    """Authorized in Spain, living in Portugal: a remote role open in Spain may
    still require living there. To confirm, not open."""
    _, found = gates(other, location="Remote - Spain")
    assert found["geography"].result is GateResult.UNRESOLVED


# =========================================================================
# 3. EMPTY ANSWERS, "I DON'T KNOW", AND THE RETIRED QUESTION
# =========================================================================


def test_residence_alone_never_proves_she_can_be_hired(base) -> None:
    """Nothing is inferred from where she lives: Worldwide stays to confirm
    until she says she can be employed there."""
    config = person(base, lives="BR")
    _, found = gates(config, location="Worldwide")
    assert found["geography"].result is GateResult.UNRESOLVED
    _, found = gates(config, location="Remote", body="You must be authorized to work in Brazil.")
    assert found["work_authorization"].result is GateResult.UNRESOLVED


def test_nothing_answered_is_unresolved_never_a_refusal(base) -> None:
    config = person(base, lives="")
    status, found = gates(config, location="Remote U.S.")
    assert found["geography"].result is GateResult.UNRESOLVED
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_i_dont_know_closes_the_home_task_and_keeps_the_job_pending(base) -> None:
    config = person(base, lives="BR")
    assert "authorization" in declarations_pending(config, set())
    assert "authorization" not in declarations_pending(config, {"authorization"})
    _, found = gates(config, location="Remote", body="You must be authorized to work in Brazil.")
    assert found["work_authorization"].result is GateResult.UNRESOLVED


def test_the_retired_answer_is_kept_and_still_only_admits(base) -> None:
    """`eligible_countries` from before 2026-10-09: never rewritten into an
    authorization, never a refusal, still a place she said she can be hired."""
    legacy = person(base, lives="BR", legacy=["BR"])
    assert legacy.eligibility.authorized_countries == []
    _, found = gates(legacy, location="Worldwide")
    assert found["geography"].result is GateResult.PASS
    _, found = gates(legacy, location="Remote", body="You must be authorized to work in Brazil.")
    assert found["work_authorization"].result is GateResult.UNRESOLVED


def test_home_no_longer_asks_the_retired_question(base) -> None:
    class NoConn:
        def execute(self, *args):
            class Row:
                def fetchone(self):
                    return (1,)

            return Row()

    config = person(base, lives="BR")
    assert "hiring_scopes" not in profile_gaps(NoConn(), config)  # type: ignore[arg-type]


def test_an_aggregators_worldwide_does_not_open_what_the_published_location_leaves_out(
    owner_example,
) -> None:
    """The TripleTen shape: the employer publishes `Remote U.S.`; an aggregator
    republishes the vacancy as Worldwide. To confirm, never a pass."""
    result = match_job(
        owner_example,
        JobFacts(
            title="Integration Engineer",
            description=BODY,
            location_raw="Remote U.S.",
            workplace_type="REMOTE",
            declared_hiring_scope="Worldwide",
        ),
        computed_at="2026-10-09T00:00:00Z",
    )
    gate = next(g for g in result.gates if g.gate == "geography")
    assert gate.result is GateResult.UNRESOLVED, gate.reason


# =========================================================================
# 4. NOT AUTHORIZED IN THE US: COMPANY COUNTRY, WHERE THE WORK IS, AND WHAT
#    THE POSTING EXPLICITLY REQUIRES ARE THREE DIFFERENT FACTS
# =========================================================================

US_COMPANY = "We are a US company headquartered in San Francisco."


@pytest.fixture(scope="module")
def no_us(base):
    """Lives in Brazil, authorized there, knows she is not authorized in the US."""
    return person(base, lives="BR", authorized=["BR"], not_authorized_countries=["US"])


@pytest.fixture(scope="module")
def no_us_needs_visa(base):
    return person(
        base, lives="BR", authorized=["BR"],
        not_authorized_countries=["US"], sponsorship_needed=["US"],
    )  # fmt: skip


@pytest.mark.parametrize("location", ["Remote - Brazil", "Worldwide", "Remote - LATAM"])
@pytest.mark.parametrize(
    "body",
    [
        US_COMPANY,
        f"{US_COMPANY} We do not sponsor visas.",
        f"{US_COMPANY} Candidates must be legally authorized to work.",
    ],
)
def test_a_us_company_hiring_work_done_from_brazil_is_not_refused(
    no_us_needs_visa, location, body
) -> None:
    """The company's country is not where the work is, and a sentence that
    names no country takes none from a remote posting."""
    status, found = gates(no_us_needs_visa, location=location, body=body)
    assert found["geography"].result is GateResult.PASS, found["geography"].reason
    assert "work_authorization" not in found or (
        found["work_authorization"].result is not GateResult.FAIL
    ), found["work_authorization"].reason
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_a_remote_role_whose_published_location_is_the_us_office_is_to_confirm(
    no_us_needs_visa,
) -> None:
    """`San Francisco` on a remote role may be the office. Neither it nor a
    no-sponsorship sentence about the US refuses work done from Brazil."""
    status, found = gates(
        no_us_needs_visa,
        location="San Francisco, CA",
        body="We do not sponsor visas in the United States. Candidates must be authorized to work.",
    )
    assert found["work_authorization"].result is GateResult.UNRESOLVED
    assert "does not say it must" in found["work_authorization"].reason
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_remote_restricted_to_the_us_refuses_only_on_the_explicit_requirement(no_us) -> None:
    status, found = gates(no_us, location="Remote U.S.")
    assert found["geography"].result is GateResult.UNRESOLVED
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    status, found = gates(
        no_us, location="Remote U.S.", body="You must be authorized to work in the United States."
    )
    gate = found["work_authorization"]
    assert gate.result is GateResult.FAIL
    assert "authorized to work in the United States" in (gate.quote or "")
    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_onsite_in_the_us_without_sponsorship_refuses_who_needs_a_visa(
    no_us, no_us_needs_visa
) -> None:
    body = "We do not sponsor visas."
    _, found = gates(no_us_needs_visa, location="New York, NY", workplace="ONSITE", body=body)
    assert found["work_authorization"].result is GateResult.FAIL
    assert "sponsor" in found["work_authorization"].reason
    # Sponsorship need not answered: to confirm, never a refusal on that gate.
    _, found = gates(no_us, location="New York, NY", workplace="ONSITE", body=body)
    assert found["work_authorization"].result is GateResult.UNRESOLVED


def test_onsite_in_the_us_with_sponsorship_is_not_refused_for_sponsorship(
    no_us_needs_visa,
) -> None:
    """The work_authorization gate has nothing to refuse. The geography gate
    still reads an on-site job in a country she is not authorized in as out
    of reach (moving countries is not a question this form asks yet)."""
    _, found = gates(
        no_us_needs_visa,
        location="New York, NY",
        workplace="ONSITE",
        body="Visa sponsorship is available for this role.",
    )
    assert "work_authorization" not in found or (
        found["work_authorization"].result is not GateResult.FAIL
    )
    assert found["geography"].result is GateResult.FAIL


def test_an_onsite_requirement_that_names_no_country_takes_the_jobs_country(no_us) -> None:
    _, found = gates(
        no_us,
        location="New York, NY",
        workplace="ONSITE",
        body="Candidates must be legally authorized to work.",
    )
    assert found["work_authorization"].result is GateResult.FAIL


def test_nothing_declared_leaves_the_explicit_us_requirement_pending(base) -> None:
    config = person(base, lives="BR", authorized=["BR"])
    _, found = gates(
        config, location="Remote", body="You must be authorized to work in the United States."
    )
    assert found["work_authorization"].result is GateResult.UNRESOLVED


# An EXPLICIT territory, residence or authorization restriction still refuses
# on a remote role; only a published location that may be an office does not.
US_RESTRICTIONS = [
    "This role is open to US residents only.",
    "Candidates must reside in the United States.",
    "You must be authorized to work in the United States.",
]


@pytest.mark.parametrize("sentence", US_RESTRICTIONS)
@pytest.mark.parametrize("location", ["Remote", "San Francisco, CA", "Remote U.S."])
def test_an_explicit_us_restriction_refuses_a_remote_role(no_us, sentence, location) -> None:
    status, found = gates(no_us, location=location, body=f"{US_COMPANY} {sentence}")
    failed = [g for g in found.values() if g.result is GateResult.FAIL]
    assert failed, {k: (g.result, g.reason) for k, g in found.items()}
    # Quoted from the employer's own sentence, never composed.
    assert any(
        g.quote and (g.quote.strip(" .") in sentence or sentence.rstrip(".") in g.quote)
        for g in failed
    ), [g.quote for g in failed]
    assert status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_a_declared_us_only_hiring_scope_refuses_a_remote_role(no_us) -> None:
    """The employer's own scope field: the WWR `USA Only` shape."""
    result = match_job(
        no_us,
        JobFacts(
            title="Integration Engineer",
            description=f"{BODY} {US_COMPANY}",
            location_raw="San Francisco, CA",
            workplace_type="REMOTE",
            declared_hiring_scope="USA Only",
        ),
        computed_at="2026-10-09T00:00:00Z",
    )
    gate = next(g for g in result.gates if g.gate == "geography")
    assert gate.result is GateResult.FAIL, gate.reason
    assert result.eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


@pytest.mark.parametrize("location", ["San Francisco, CA", "Remote U.S."])
def test_an_ambiguous_published_location_alone_is_to_confirm(no_us, location) -> None:
    """The same remote role with no restriction written: the location may be
    the office. To confirm, never a refusal."""
    status, found = gates(no_us, location=location, body=US_COMPANY)
    assert found["geography"].result is GateResult.UNRESOLVED, found["geography"].reason
    assert not any(g.result is GateResult.FAIL for g in found.values())
    assert status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE

"""Spryker-shaped conflict: an explicit remote location versus generic prose."""

from dataclasses import replace

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.match.engine import JobFacts, match_job


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Germany - Berlin; Remote - Europe", "UNRESOLVED"),
        ("Remote - Unknown Place", "UNRESOLVED"),
        ("Remote - Brazil", "VERIFIED_ELIGIBLE"),
        ("Remote - Europe; Remote - Brazil", "VERIFIED_ELIGIBLE"),
        ("Berlin, Germany", "VERIFIED_ELIGIBLE"),
        ("Remote", "VERIFIED_ELIGIBLE"),
    ],
)
def test_remote_location_conflict(location, expected):
    config, _ = load_search_config(committed_config_dir())
    job = JobFacts(
        title="Systems specialist",
        description="Work from anywhere with fully flexible hours and unlimited vacation days.",
        location_raw=location,
    )
    result = match_job(config, job, computed_at="2026-09-13T00:00:00Z")
    assert str(result.eligibility_status) == expected
    # Geography changes no Search Fit arithmetic.
    control = match_job(
        config, replace(job, location_raw="Remote"), computed_at="2026-09-13T00:00:00Z"
    )
    assert result.match_score == control.match_score


def test_remote_location_alone_never_grants_permission():
    config, _ = load_search_config(committed_config_dir())
    result = match_job(
        config,
        JobFacts(
            title="Systems specialist",
            description="Build internal tools.",
            location_raw="Remote - Brazil",
        ),
        computed_at="2026-09-13T00:00:00Z",
    )
    assert str(result.eligibility_status) == "UNRESOLVED"


def test_the_same_remote_europe_role_can_admit_a_german_candidate():
    config, _ = load_search_config(committed_config_dir())
    config = config.model_copy(
        update={
            "eligibility": config.eligibility.model_copy(
                update={
                    "candidate_country": "DE",
                    "eligible_countries": ["DE"],
                    "eligible_scopes": ["EMEA", "WORLDWIDE"],
                }
            )
        }
    )
    result = match_job(
        config,
        JobFacts(
            title="Systems specialist",
            description="Work from anywhere.",
            location_raw="Germany - Berlin; Remote - Europe",
        ),
        computed_at="2026-09-13T00:00:00Z",
    )
    assert str(result.eligibility_status) == "VERIFIED_ELIGIBLE"


@pytest.mark.parametrize(
    "location,expected",
    [
        ("Odessa, FL, US / Tampa, FL, US", "VERIFIED_NOT_ELIGIBLE"),
        ("Berlin, Germany", "VERIFIED_NOT_ELIGIBLE"),
        ("Unknown Place", "UNRESOLVED"),
        ("Brazil", "UNRESOLVED"),
    ],
)
def test_explicit_attendance_overrides_remote_benefits(location, expected):
    config, _ = load_search_config(committed_config_dir())
    description = (
        "This role is deliberately hybrid and requires employees to work from the company’s "
        "Headquarters office in Odessa, Florida a minimum of one day per week. "
        "We are a 'work from anywhere' in the U.S. SaaS company."
    )
    job = JobFacts(title="Corporate systems", description=description, location_raw=location)
    result = match_job(config, job, computed_at="2026-09-13T00:00:00Z")
    assert str(result.eligibility_status) == expected
    gate = next(g for g in result.gates if g.gate == "worksite")
    assert gate.quote in description
    assert description[gate.char_start : gate.char_end] in gate.quote
    assert (
        result.match_score
        == match_job(
            config, replace(job, location_raw="Brazil"), computed_at="2026-09-13T00:00:00Z"
        ).match_score
    )


@pytest.mark.parametrize(
    "text",
    [
        "We do not require employees to work from the office.",
        "You may work from the office if you prefer.",
        "We provide home office equipment.",
        "You must work from your home office.",
    ],
)
def test_optional_negated_or_home_office_is_not_mandatory_attendance(text):
    config, _ = load_search_config(committed_config_dir())
    result = match_job(
        config,
        JobFacts(
            title="Systems", description=text + " Work from anywhere.", location_raw="Germany"
        ),
        computed_at="2026-09-13T00:00:00Z",
    )
    assert str(result.eligibility_status) == "VERIFIED_ELIGIBLE"

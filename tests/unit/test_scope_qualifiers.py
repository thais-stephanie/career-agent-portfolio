"""A restrictive geography qualifier must survive its positive prefix."""

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus
from career_agent.match.engine import JobFacts, match_job

CONFIG, _ = load_search_config(committed_config_dir())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Work from anywhere in Canada and USA.", "VERIFIED_NOT_ELIGIBLE"),
        ("Work from anywhere in LATAM.", "VERIFIED_ELIGIBLE"),
        ("Work from anywhere in LATAM and USA.", "VERIFIED_ELIGIBLE"),
        ("Work from anywhere in Brazil.", "VERIFIED_ELIGIBLE"),
        ("Work from anywhere.", "VERIFIED_ELIGIBLE"),
        ("Remote worldwide.", "VERIFIED_ELIGIBLE"),
        ("Work from anywhere, but only in Canada.", "VERIFIED_NOT_ELIGIBLE"),
        ("Work from anywhere in Canada and U.S.A.", "VERIFIED_NOT_ELIGIBLE"),
        ("Remote worldwide (US only).", "VERIFIED_NOT_ELIGIBLE"),
        ("Work from anywhere, but restricted to Canada.", "VERIFIED_NOT_ELIGIBLE"),
        ("Can work from anywhere from Canada or USA.", "VERIFIED_NOT_ELIGIBLE"),
        ("Work from anywhere in an unspecified jurisdiction.", "UNRESOLVED"),
        # Directive F, 2026-09-11: an exclusion naming the candidate's own
        # country is a refusal, not an open question.
        ("Work from anywhere except Brazil.", "VERIFIED_NOT_ELIGIBLE"),
        # An explicit residence requirement outranks generic remote language.
        (
            "Work from anywhere. Applicants must reside in Canada or the United States.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        ("Remote worldwide. Candidates must be based in Brazil.", "VERIFIED_ELIGIBLE"),
        ("Work from anywhere in the world for 30 days per year.", "UNRESOLVED"),
        ("Our product helps customers work from anywhere in the world.", "UNRESOLVED"),
        ("Our collaboration tools letting you work from anywhere are easy to use.", "UNRESOLVED"),
        (
            '3 weeks of "passport working," offering the flexibility to work from anywhere.',
            "UNRESOLVED",
        ),
        ("6 week remote working (work from anywhere) policy.", "UNRESOLVED"),
        (
            "Work from Anywhere: podés trabajar desde donde quieras durante un periodo de 3 meses.",
            "UNRESOLVED",
        ),
    ],
)
def test_qualified_scope(body, expected) -> None:
    result = match_job(CONFIG, JobFacts(title="Engineer", description=body), computed_at="frozen")
    assert result.eligibility_status is EligibilityStatus(expected)
    for gate in result.gates:
        if gate.quote:
            assert gate.quote in body
            assert gate.char_start is not None and gate.char_end is not None
            assert body[gate.char_start : gate.char_end] in gate.quote


def test_country_list_is_not_widened_to_its_implied_region() -> None:
    result = match_job(
        CONFIG,
        JobFacts(title="Engineer", description="Work from anywhere in Mexico."),
        computed_at="frozen",
    )
    assert result.eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE


def test_unknown_provider_scope_is_not_overridden_by_general_remote_prose() -> None:
    result = match_job(
        CONFIG,
        JobFacts(
            title="Engineer", description="Work from anywhere.", declared_hiring_scope="Jabalpur"
        ),
        computed_at="frozen",
    )
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED

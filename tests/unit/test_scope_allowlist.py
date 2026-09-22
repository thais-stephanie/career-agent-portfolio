"""A regional label is not a country list, and a country list is the answer.

The class of false eligibility corrected on 2026-09-11: a posting said LATAM
in a hiring sentence and, elsewhere, listed the countries it meant -- without
Brazil. The region opened the gate and the list was never read.

Rules A to I below are the accepted behaviour, quoted from the directive that
named them. Each is a fixture with a positive control beside it so a rule
cannot pass by refusing everything.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EligibilityStatus
from career_agent.match.engine import JobFacts, match_job
from career_agent.match.places import region_contains, resolve_place

CONFIG, _ = load_search_config(committed_config_dir())


def _status(body: str, **facts: object) -> EligibilityStatus:
    result = match_job(
        CONFIG, JobFacts(title="Engineer", description=body, **facts), computed_at="frozen"
    )
    haystack = body + " " + str(facts.get("declared_hiring_scope") or "")
    for gate in result.gates:
        if gate.quote:
            assert gate.quote in haystack, "a quote is a substring that exists (ADR-0002)"
    return result.eligibility_status


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        # A. A regional label with no narrower restriction admits.
        ("This role is open to candidates based in Latin America.", "VERIFIED_ELIGIBLE"),
        ("We hire across LATAM.", "VERIFIED_ELIGIBLE"),
        # B. The same label plus an explicit allowlist without Brazil refuses.
        (
            "We hire across Latin America. Eligible countries: Argentina, Chile, Colombia, Mexico.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        (
            "Remote - Latin America. Open to candidates based in Argentina, Chile and Colombia.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        # C. A list that names Brazil admits.
        ("We are hiring in LATAM (Brazil, Argentina, Colombia).", "VERIFIED_ELIGIBLE"),
        ("Open to candidates located in Brazil, Argentina or Mexico.", "VERIFIED_ELIGIBLE"),
        # D. A list attached to the label that omits Brazil refuses.
        ("We are hiring in LATAM (Argentina, Colombia).", "VERIFIED_NOT_ELIGIBLE"),
        ("We hire in Latin America: Argentina, Colombia, Peru.", "VERIFIED_NOT_ELIGIBLE"),
        # E. Examples are not an allowlist. The region decides.
        ("We hire across LATAM, including Argentina and Colombia.", "VERIFIED_ELIGIBLE"),
        ("We hire across LATAM (e.g. Argentina, Colombia).", "VERIFIED_ELIGIBLE"),
        ("We hire in Latin America, such as Mexico and Chile.", "VERIFIED_ELIGIBLE"),
        # F. An exclusion naming Brazil refuses.
        ("We hire anywhere in South America except Brazil.", "VERIFIED_NOT_ELIGIBLE"),
        ("We hire across LATAM but cannot hire in Brazil.", "VERIFIED_NOT_ELIGIBLE"),
        ("We hire anywhere in South America except Venezuela.", "VERIFIED_ELIGIBLE"),
        # G. A hemisphere label plus a US/Canada list refuses.
        (
            "Remote across the Americas. Candidates must be based in the US or Canada.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        ("We can only hire in the United States.", "VERIFIED_NOT_ELIGIBLE"),
        # A region with "only" that does not contain Brazil refuses; one that does admits.
        ("This is a remote role, Europe only.", "VERIFIED_NOT_ELIGIBLE"),
        ("We hire remotely, but only in North America.", "VERIFIED_NOT_ELIGIBLE"),
        ("Remote role, LATAM only.", "VERIFIED_ELIGIBLE"),
        # A product that lets its customers hire anywhere is not an employer
        # hiring anywhere (Rippling's company boilerplate, 283 postings).
        (
            "With Rippling, you can hire a new employee anywhere in the world and set up payroll.",
            "UNRESOLVED",
        ),
        ("At Rippling, we enable companies to hire anywhere in the world.", "UNRESOLVED"),
        ("We hire anywhere in the world.", "VERIFIED_ELIGIBLE"),
        # I. Remote is not worldwide, and an office list is not a hiring scope.
        ("This is a fully remote role.", "UNRESOLVED"),
        ("We have offices in Argentina and Colombia.", "UNRESOLVED"),
        ("Our customers are in Europe only.", "UNRESOLVED"),
        ("Fluent in English only.", "UNRESOLVED"),
        # Two explicit statements that disagree are a contradiction, not a pass.
        (
            "Open to candidates based in Brazil. Applicants must reside in Canada.",
            "UNRESOLVED",
        ),
    ],
)
def test_allowlist_precedence(body: str, expected: str) -> None:
    assert _status(body) is EligibilityStatus(expected)


@pytest.mark.parametrize(
    ("declared", "expected"),
    [
        # H. A structured hiring-scope field is an exhaustive list.
        ("Argentina, Colombia, Mexico", "VERIFIED_NOT_ELIGIBLE"),
        ("Brazil, Portugal", "VERIFIED_ELIGIBLE"),
        ("North America, Europe", "VERIFIED_NOT_ELIGIBLE"),
        ("Anywhere", "VERIFIED_ELIGIBLE"),
        ("Worldwide", "VERIFIED_ELIGIBLE"),
        ("Jabalpur", "UNRESOLVED"),
    ],
)
def test_declared_scope_is_read_as_a_list(declared: str, expected: str) -> None:
    # The body claims LATAM and must not overrule the field (rule H).
    body = "We hire across LATAM."
    assert _status(body, declared_hiring_scope=declared) is EligibilityStatus(expected)


@pytest.mark.parametrize(
    ("location_raw", "expected"),
    [
        ("Remote (United States | Canada)", "VERIFIED_NOT_ELIGIBLE"),
        ("Argentina, Colombia", "VERIFIED_NOT_ELIGIBLE"),
        ("Brazil", "VERIFIED_ELIGIBLE"),
        ("LATAM", "VERIFIED_ELIGIBLE"),
        ("Fully remote", "UNRESOLVED"),
        ("Home based", "UNRESOLVED"),
    ],
)
def test_structured_remote_location_reads_only_what_was_named(
    location_raw: str, expected: str
) -> None:
    assert _status("Great job.", location_raw=location_raw, workplace_type="REMOTE") is (
        EligibilityStatus(expected)
    )


def test_a_country_list_keeps_its_implied_region_for_filters_only() -> None:
    place = resolve_place("Argentina, Colombia, Mexico")
    assert place.regions == ("LATAM",), "the facet still files these under Latin America"
    assert place.stated_regions == (), "and the gate is told nobody wrote the word"
    assert resolve_place("LATAM - Argentina").stated_regions == ("LATAM",)


def test_region_membership() -> None:
    assert region_contains("LATAM", "BR") is True
    assert region_contains("AMERICAS", "BR") is True
    assert region_contains("WORLDWIDE", "BR") is True
    assert region_contains("NORTH_AMERICA", "BR") is False
    assert region_contains("EMEA", "BR") is False
    assert region_contains("EMEA", "XX") is None


def _candidate(country: str, scopes: tuple[str, ...]):
    """The committed configuration re-pointed at another candidate."""
    eligibility = CONFIG.eligibility.model_copy(
        update={
            "candidate_country": country,
            "candidate_country_label": country,
            "eligible_countries": [country],
            "eligible_scopes": list(scopes),
        }
    )
    return CONFIG.model_copy(update={"eligibility": eligibility})


@pytest.mark.parametrize(
    ("country", "scopes", "body", "expected"),
    [
        # A Brazil candidate who ticked NORTH_AMERICA by mistake is still not
        # in North America.
        (
            "BR",
            ("WORLDWIDE", "LATAM", "NORTH_AMERICA", "EMEA"),
            "Open to candidates based in the United States or Canada.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        (
            "BR",
            ("WORLDWIDE", "LATAM", "NORTH_AMERICA"),
            "Work from anywhere in North America.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        # A US candidate reads the same postings the other way round.
        (
            "US",
            ("WORLDWIDE", "NORTH_AMERICA"),
            "Open to candidates based in the United States or Canada.",
            "VERIFIED_ELIGIBLE",
        ),
        (
            "US",
            ("WORLDWIDE", "NORTH_AMERICA"),
            "We hire across Latin America. Eligible countries: Argentina, Chile.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        (
            "US",
            ("WORLDWIDE", "NORTH_AMERICA"),
            "Work from anywhere in North America.",
            "VERIFIED_ELIGIBLE",
        ),
        # A European candidate.
        ("DE", ("WORLDWIDE", "EMEA"), "Work from anywhere in Europe.", "VERIFIED_ELIGIBLE"),
        ("DE", ("WORLDWIDE", "EMEA"), "This is a remote role, Europe only.", "VERIFIED_ELIGIBLE"),
        (
            "DE",
            ("WORLDWIDE", "EMEA"),
            "Remote across the Americas. Candidates must be based in the US or Canada.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
        (
            "DE",
            ("WORLDWIDE", "EMEA"),
            "Work from anywhere except Germany.",
            "VERIFIED_NOT_ELIGIBLE",
        ),
    ],
)
def test_three_personas(country: str, scopes: tuple[str, ...], body: str, expected: str) -> None:
    config = _candidate(country, scopes)
    result = match_job(config, JobFacts(title="Engineer", description=body), computed_at="frozen")
    assert result.eligibility_status is EligibilityStatus(expected)

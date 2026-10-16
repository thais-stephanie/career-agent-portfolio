"""The contractor default and the pay period defaults (readers-11, 2026-10-13).

The owner's rules, asserted:

* an American employer hiring in Latin America that states no engagement is
  "Probably Contractor"; a stated engagement of any kind wins; a working
  pattern is not an engagement; a currency or a language is not evidence of
  an American employer; the default never reaches a gate;
* a USD figure with no period, on a posting hiring in Latin America, is a
  month's pay under 7,000 and a year's over 30,000, unknown from one to the
  other (both included) and for a band that crosses either; another currency
  and an hourly rate get no default; a period the advert states wins.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import EmploymentRelationship, GateResult
from career_agent.domain.matching import GateOutcome
from career_agent.match.engagement import (
    REGISTRY_US,
    latam_hiring_evidence,
    us_company_evidence,
)
from career_agent.match.engine import JobFacts, match_job
from career_agent.match.pay import default_period

CONFIG, _ = load_search_config(committed_config_dir())
LATAM = "Remote - LATAM"
NOW = "2026-10-13T00:00:00Z"


def matched(description: str, **facts):
    facts.setdefault("declared_hiring_scope", LATAM)
    return match_job(
        CONFIG, JobFacts(title="Analyst", description=description, **facts), computed_at=NOW
    )


# -------------------------------------------------------------- evidence


@pytest.mark.parametrize(
    "text",
    [
        "We are a US-based startup building payroll tools.",
        "Join a fast-growing U.S. company.",
        "Our client is an American company in fintech.",
        "Headquartered in San Francisco, we sell software.",
        "Somos uma empresa norte-americana de tecnologia.",
    ],
)
def test_an_american_employer_is_read_from_what_the_ad_says(text: str) -> None:
    quote = us_company_evidence(text, None)
    assert quote and quote in text, "the basis is a quote that exists (ADR-0002)"


@pytest.mark.parametrize(
    "text",
    [
        "Salary paid in USD every month.",
        "Let us know if you have questions.",
        "We offer a 401(k) and dental.",
        "Help us grow our business in the United Kingdom.",
    ],
)
def test_a_currency_a_pronoun_or_a_benefit_is_not_an_american_employer(text: str) -> None:
    assert us_company_evidence(text, None) is None


def test_the_registry_headquarters_is_a_basis() -> None:
    assert us_company_evidence("Nothing about where we are.", "us") == REGISTRY_US
    assert us_company_evidence("Nothing about where we are.", "CA") is None


def test_hiring_in_latin_america_is_a_stated_scope_naming_it_or_a_country_in_it() -> None:
    quoted = GateOutcome(
        "geography", GateResult.PASS, "x", quote="We hire in Brazil, Argentina and Mexico"
    )
    assert latam_hiring_evidence(None, (quoted,)) == quoted.quote
    assert latam_hiring_evidence("LATAM", ()) == "LATAM"
    welcome = GateOutcome(
        "geography", GateResult.PASS, "x", quote="Candidates in Latin America are welcome"
    )
    assert latam_hiring_evidence(None, (welcome,)) == welcome.quote
    assert latam_hiring_evidence("Remote (US only)", ()) is None
    assert latam_hiring_evidence(None, ()) is None


# -------------------------------------------------------------- the default


AMERICAN = "We are a US-based startup. You will build reports and dashboards for the team."


def test_an_american_employer_hiring_in_latin_america_is_probably_a_contractor() -> None:
    result = matched(AMERICAN)
    employment = result.employment
    assert employment.relationship is EmploymentRelationship.UNRESOLVED, (
        "the stated reading is kept"
    )
    assert employment.effective_relationship is EmploymentRelationship.CONTRACTOR_B2B
    assert employment.is_default
    assert employment.default_us_evidence == "a US-based startup"
    assert employment.default_latam_evidence == LATAM


def test_a_stated_engagement_wins_over_the_default() -> None:
    result = matched(AMERICAN + " This is a full-time employee position (W-2).")
    if result.employment.is_explicit:
        assert not result.employment.is_default
    declared = matched(AMERICAN, declared_relationship="EMPLOYEE")
    assert declared.employment.effective_relationship is EmploymentRelationship.EMPLOYEE
    assert not declared.employment.is_default


def test_full_time_is_a_working_pattern_and_does_not_block_the_default() -> None:
    result = matched(AMERICAN, employment_type="FULL_TIME")
    assert result.employment.is_default
    assert result.employment.effective_relationship is EmploymentRelationship.CONTRACTOR_B2B


def test_without_evidence_of_an_american_employer_nothing_is_assumed() -> None:
    result = matched("You will build reports and dashboards. Salary in USD.")
    assert not result.employment.is_default
    assert result.employment.effective_relationship is EmploymentRelationship.UNRESOLVED


def test_without_hiring_in_latin_america_nothing_is_assumed() -> None:
    result = matched(AMERICAN, declared_hiring_scope="United States")
    assert not result.employment.is_default


def test_the_default_never_reaches_a_gate() -> None:
    plain = matched("You will build reports and dashboards for the team.")
    default = matched(AMERICAN)
    gates = lambda r: {g.gate: g.result for g in r.gates}  # noqa: E731
    assert gates(default) == gates(plain)


# -------------------------------------------------------------- pay periods


@pytest.mark.parametrize(
    ("low", "high", "expected"),
    [
        (3000, 3000, "MONTH"),
        (2500, 6999, "MONTH"),
        (7000, 7000, None),
        (5000, 9000, None),
        (30000, 30000, None),
        (29000, 45000, None),
        (30001, 50000, "YEAR"),
        (160000, 220000, "YEAR"),
        (None, 4000, "MONTH"),
        (0, 4000, None),
    ],
)
def test_the_usd_defaults_and_their_limits(low, high, expected) -> None:
    assert default_period(low, high, "USD", hires_in_latam=True) == expected


def test_no_default_without_latin_america_or_in_another_currency() -> None:
    assert default_period(3000, 3000, "USD", hires_in_latam=False) is None
    assert default_period(3000, 3000, "BRL", hires_in_latam=True) is None
    assert default_period(3000, 3000, "EUR", hires_in_latam=True) is None


def _pay(result):
    return result.posting_facts["salary"]


def test_an_assumed_period_is_marked_and_the_stated_figure_kept() -> None:
    result = matched(AMERICAN, salary_min=3000.0, salary_max=3000.0, salary_currency="USD")
    salary = _pay(result)
    assert salary["period"] == "MONTH" and salary["period_inferred"] is True
    assert salary["min"] == 3000.0 and salary["max"] == 3000.0


def test_a_period_the_ad_states_wins() -> None:
    text = AMERICAN + " The salary is USD 3,000 per year."
    result = matched(text, salary_min=3000.0, salary_max=3000.0, salary_currency="USD")
    assert _pay(result)["period"] == "YEAR"
    assert "period_inferred" not in _pay(result)


def test_an_hourly_rate_stays_hourly() -> None:
    result = matched(
        AMERICAN, salary_min=40.0, salary_max=85.0, salary_currency="USD", salary_period="HOUR"
    )
    assert _pay(result)["period"] == "HOUR"
    assert "period_inferred" not in _pay(result)


# -------------------------------------------------------------- hours apart


def _filed(result) -> tuple[str | None, str | None]:
    """(hours as the board gave them, engagement the Contract filter reads)."""
    from career_agent.storage.mvp_repo import FACET_COLUMN_NAMES, facet_columns

    row = facet_columns(result)
    return (
        row[FACET_COLUMN_NAMES.index("employment_type")],
        row[FACET_COLUMN_NAMES.index("engagement")],
    )


@pytest.mark.parametrize("hours", ["FULL_TIME", "PART_TIME"])
def test_hours_alone_leave_the_default_in_place_and_stay_hours(hours: str) -> None:
    result = matched(AMERICAN, employment_type=hours)
    assert result.employment.is_default
    assert _filed(result) == (hours, "CONTRACTOR_B2B")


def test_a_stated_employee_wins_over_the_default_in_the_filter() -> None:
    result = matched(AMERICAN, declared_relationship="EMPLOYEE", employment_type="FULL_TIME")
    assert not result.employment.is_default
    assert _filed(result) == ("FULL_TIME", "EMPLOYEE")


def test_hours_and_a_stated_contract_are_both_kept() -> None:
    result = matched(AMERICAN, declared_relationship="CONTRACTOR_B2B", employment_type="PART_TIME")
    assert result.employment.is_explicit and not result.employment.is_default
    assert _filed(result) == ("PART_TIME", "CONTRACTOR_B2B")


def test_no_default_and_no_statement_files_no_engagement() -> None:
    result = matched("You will build reports.", employment_type="FULL_TIME")
    assert _filed(result) == ("FULL_TIME", None)


ASSEMBLY = (
    "Our salary ranges are based on paying competitively for our size and stage. "
    "The provided range is the expected salary for candidates in the U.S. "
    "Salary range: $160,000 to $220,000\n\nWe are a global team spread across time zones."
)


def _pay_facts(**extra) -> dict:
    facts = dict(
        title="Senior GTM AI Engineer",
        description=ASSEMBLY,
        salary_min=160000,
        salary_max=220000,
        salary_currency="USD",
    )
    facts.update(extra)
    result = match_job(CONFIG, JobFacts(**facts), computed_at="frozen")
    return result.posting_facts["salary"]


def test_a_stated_worldwide_hiring_scope_takes_the_usd_period_default() -> None:
    """AssemblyAI (2026-10-13): "Anywhere in the World" in the board's scope
    field includes Latin America, so the band takes the yearly default."""
    salary = _pay_facts(declared_hiring_scope="Anywhere in the World")
    assert salary["period"] == "YEAR" and salary["period_inferred"] is True
    assert salary["min"] == 160000 and salary["max"] == 220000, "the stated figures stay"


def test_global_culture_prose_does_not_widen_where_a_posting_hires() -> None:
    """ "A global team" is about the company, not about hiring: the Latin
    America default does not apply. The band's own text calls it a salary
    range, so its period may still be ASSUMED from its size (2026-10-16),
    and the facts say which rule assumed it."""
    salary = _pay_facts()
    assert salary.get("period_basis") != "LATAM_DEFAULT"
    if salary.get("period") is not None:
        assert salary["period_inferred"] and salary["period_basis"] == "SALARY_HEURISTIC"


def test_a_band_said_to_be_the_us_one_keeps_its_sentence() -> None:
    salary = _pay_facts(declared_hiring_scope="Anywhere in the World")
    assert salary["us_only"] == (
        "The provided range is the expected salary for candidates in the U.S."
    )
    assert "us_only" not in _pay_facts(description="Salary range: $160,000 to $220,000.")


def test_an_hourly_rate_never_becomes_monthly() -> None:
    result = match_job(
        CONFIG,
        JobFacts(
            title="Analyst",
            description="We hire across Latin America. Pay: $40 per hour.",
            salary_min=40,
            salary_max=40,
            salary_currency="USD",
            salary_period="HOUR",
        ),
        computed_at="frozen",
    )
    salary = result.posting_facts["salary"]
    assert salary["period"] == "HOUR" and not salary.get("period_inferred")

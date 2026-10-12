"""Facts an ad states in words, read once and shown with the employer's sentence.

Synthetic ads shaped like the two that exposed these defects (a remote ad with
a "career growth" list that names Senior, and a talent-pool ad that lists
Junior, Mid and Senior with an hourly rate and part-time hours). No employer
text is copied; the shapes are what matter.

* A level named in a career-growth list or about a colleague is not the level
  of the job; an ad that LISTS several levels keeps them all.
* An hourly rate is pay. "40 hours per week" is a workload, never a pay period.
* Part-time says nothing about employee or contractor, a talent pool is not an
  opening, and a time-zone requirement is not a place to live.
"""

from __future__ import annotations

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import Seniority, SenioritySource
from career_agent.match import JobFacts, match_job
from career_agent.match.explicit import read_schedule, read_talent_pool, read_timezone
from career_agent.match.pay import read_pay
from career_agent.match.seniority import adopt_level, read_seniority

CONFIG, _ = load_search_config(committed_config_dir())

GROWTH_AD = """Automation Specialist - Remote

About the Role
We are hiring an Automation Specialist to build and maintain workflows.

Why This Role Stands Out
Clear career growth into:

Senior Automation Engineer

Automation Architect

Business Systems Lead
"""

POOL_AD = """Teaching Experts

We are building a talent pool of practitioners. We are looking for specialists across
a range of seniority levels:

Junior (Instructor) - 1-3 years designing or supporting workflows.

Mid (Author) - 3-6 years implementing automation across operations.

Senior (Subject Matter Expert) - 6+ years in solution architecture.

These are Talent Pool positions. There may not be an immediate opening at the time you apply.
You can join us on a part-time basis (10-15 hours per week) as an instructor.
Availability to collaborate within European time zones.
Competitive hourly rate of $40-$85 USD for flexible part-time collaboration.
"""


# ---------------------------------------------------------------- the level


def test_an_ad_with_no_level_is_mid_by_app_default_and_says_so() -> None:
    reading = read_seniority("Automation Specialist", "Build workflows with APIs.")
    assert reading.value is Seniority.MID
    assert reading.source is SenioritySource.DEFAULT
    assert not reading.is_evidence and reading.levels == ()
    assert "does not state a level" in reading.sentence


def test_a_level_named_in_a_career_growth_list_is_not_the_level_of_the_job() -> None:
    reading = read_seniority("AI Automation Specialist", GROWTH_AD)
    assert reading.source is SenioritySource.DEFAULT
    assert reading.value is Seniority.MID


def test_an_ad_that_lists_three_levels_keeps_all_three_and_adopts_mid() -> None:
    reading = read_seniority("Teaching Experts", POOL_AD)
    assert reading.source is SenioritySource.DECLARED_RANGE
    assert reading.levels == (Seniority.JUNIOR, Seniority.MID, Seniority.SENIOR)
    assert reading.value is Seniority.MID  # neither the default nor only "6+ years"
    assert reading.evidence and reading.evidence in POOL_AD
    assert reading.is_evidence


def test_an_inline_range_is_read_only_in_a_sentence_about_hiring() -> None:
    hiring = "We are hiring Junior, Mid and Senior developers for this team."
    assert read_seniority("Developer", hiring).levels == (
        Seniority.JUNIOR,
        Seniority.MID,
        Seniority.SENIOR,
    )
    about_colleagues = "You will mentor junior engineers and work with senior leaders."
    assert read_seniority("Developer", about_colleagues).source is SenioritySource.DEFAULT
    assert read_seniority("Developer", "Mid-market customers; lead our staff.").levels == ()


def test_a_title_grade_still_outranks_a_range_in_the_body() -> None:
    reading = read_seniority("Senior Engineer", POOL_AD)
    assert reading.value is Seniority.SENIOR
    assert reading.source is SenioritySource.TITLE_GRADE


def test_the_adopted_level_follows_what_the_person_said() -> None:
    levels = (Seniority.JUNIOR, Seniority.MID, Seniority.SENIOR)
    assert adopt_level(levels) is Seniority.MID
    assert adopt_level(levels, preferred=(Seniority.SENIOR,)) is Seniority.SENIOR
    assert adopt_level(levels, excluded=(Seniority.MID,)) is Seniority.JUNIOR
    assert adopt_level(levels, excluded=levels) is Seniority.MID  # nothing open: the list rules


# -------------------------------------------------------------------- pay


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "Competitive hourly rate of $40-$85 USD for flexible part-time work.",
            (40.0, 85.0, "USD", "HOUR"),
        ),
        ("Hourly rate: EUR 25 - 30", (25.0, 30.0, "EUR", "HOUR")),
        ("Taxa horaria: R$ 80 - R$ 120", (80.0, 120.0, "BRL", "HOUR")),
        ("Salary: $80,000 per year, 40 hours per week", (80000.0, 80000.0, "USD", "YEAR")),
        ("Pay: R$ 4.500 por mes. 20 horas por semana", (4500.0, 4500.0, "BRL", "MONTH")),
    ],
)
def test_pay_is_read_with_its_own_period_and_a_workload_is_not_a_period(text, expected) -> None:
    found = read_pay(text)
    assert found is not None
    assert (found.min_value, found.max_value, found.currency, found.period) == expected
    assert found.raw in text


@pytest.mark.parametrize(
    "text",
    [
        "Our interest rate is 5%",
        "We need 5-10 years of experience and 10-15 hours of training",
        "We have surpassed $400M in ARR and offer a 401(k) match",
        "Competitive pay and great benefits",
    ],
)
def test_numbers_that_are_not_pay_are_not_read_as_pay(text) -> None:
    assert read_pay(text) is None


def test_the_quote_includes_the_currency_code_the_ad_wrote() -> None:
    found = read_pay("Competitive hourly rate of $40-$85 USD for flexible part-time work.")
    assert found is not None and found.raw.endswith("USD")


# ------------------------------------------------------- schedule and others


def test_part_time_and_weekly_hours_are_read_and_no_engagement_is_implied() -> None:
    reading = read_schedule(POOL_AD)
    assert reading is not None and reading.part_time
    assert (reading.hours_min, reading.hours_max) == (10, 15)
    assert reading.quote in POOL_AD
    assert not hasattr(reading, "engagement") and not hasattr(reading, "relationship")


def test_a_forty_hour_week_is_not_part_time_and_both_is_not_stated() -> None:
    full = read_schedule("This is a full-time role, 40 hours per week.")
    assert full is not None and not full.part_time and full.hours_max == 40
    assert read_schedule("Full-time or part-time arrangements are possible.") is None


def test_a_talent_pool_is_read_with_the_sentence_that_says_there_may_be_no_opening() -> None:
    pool = read_talent_pool(POOL_AD)
    assert pool is not None and "immediate opening" in pool.quote
    assert read_talent_pool("We are hiring now for a team lead.") is None


def test_a_time_zone_requirement_is_a_quote_and_not_a_place() -> None:
    zone = read_timezone(POOL_AD)
    assert zone is not None and "European time zones" in zone.quote
    assert read_timezone("The office has many windows.") is None


# ------------------------------------------------- through the matcher


def test_the_matcher_carries_the_readings_beside_the_posting_it_scored() -> None:
    from career_agent.pipeline.facts import job_facts

    facts = job_facts(
        job_id="j1",
        title="Teaching Experts",
        description=POOL_AD,
        location_raw="Remote",
        posted_at=None,
        provider=None,
        payload={},
    )
    assert facts.salary_source == "TEXT" and facts.salary_raw
    result = match_job(CONFIG, facts, computed_at="2026-10-12T00:00:00Z")
    carried = result.posting_facts
    assert carried["salary"]["period"] == "HOUR"  # type: ignore[index]
    assert carried["salary"]["source"] == "TEXT"  # type: ignore[index]
    assert carried["schedule"]["hours_max"] == 15  # type: ignore[index]
    assert "talent_pool" in carried and "timezone" in carried
    assert result.seniority.levels == (Seniority.JUNIOR, Seniority.MID, Seniority.SENIOR)


def test_a_time_zone_does_not_make_the_posting_eligible_for_a_region() -> None:
    plain = JobFacts(title="Teaching Experts", description=POOL_AD.replace("European", "Brazilian"))
    zone = JobFacts(title="Teaching Experts", description=POOL_AD)
    a = match_job(CONFIG, plain, computed_at="2026-10-12T00:00:00Z")
    b = match_job(CONFIG, zone, computed_at="2026-10-12T00:00:00Z")
    assert a.eligibility_status == b.eligibility_status

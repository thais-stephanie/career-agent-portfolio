"""Employment context is a reading. Eligibility is a verdict. Never the same.

`domestic.context` has been computed on every posting since V1.2 and was
unreachable from SQL until migration 0020, so the only place it appeared was a
sentence inside one drawer. Giving it a column is what lets the digest offer a
section and the rail offer a filter -- and it is also the moment the reading
could quietly become a rejection list, which is what this file exists to stop.

The distinction, in the words the product uses:

    LIKELY_US_DOMESTIC   this posting offers something that usually accompanies
                         employment inside the United States, and says nothing
                         about hiring elsewhere. Worth knowing. Not a refusal.

    VERIFIED_NOT_ELIGIBLE the employer STATED something the candidate cannot
                         meet, and the gate quotes the sentence.

Measured on the owner's corpus at the time this was written: of 3,335 postings
reading LIKELY_US_DOMESTIC, 3,103 are UNRESOLVED on eligibility and **13 are
VERIFIED_ELIGIBLE**. The populations cross in both directions, which is the
strongest possible evidence that one may not stand in for the other.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import DomesticContext, EligibilityStatus
from career_agent.match.engine import JobFacts, match_job
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import (
    FACET_COLUMN_NAMES,
    JobFilter,
    ScoredJobQuery,
    facet_columns,
)
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
WHEN = "2026-09-06T00:00:00Z"

#: A posting that offers a benefit which usually accompanies United States
#: employment and says NOTHING about where it hires. Invented for this file.
US_CONTEXT_BODY = """
We are hiring a Business Systems Analyst for our internal platforms team.

You will own our CRM architecture, build workflow automation across our
billing systems, and maintain REST API integrations between them.

Benefits
- 401(k) Matching: contribution matching to help invest in your future
- Comprehensive medical, dental and vision coverage
"""

#: The same work, from an employer that DID say where it hires.
INTERNATIONAL_BODY = (
    US_CONTEXT_BODY
    + """
We hire globally and work from anywhere in the world.
"""
)


@pytest.fixture(scope="module")
def config():
    loaded, _ = load_search_config(CONFIG_DIR)
    return loaded


@pytest.fixture
def api() -> Iterator[JobsApi]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="employment-context")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(CONFIG_DIR)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR, port=0), quiet=True)


def read(config, body: str):
    return match_job(
        config, JobFacts(title="Business Systems Analyst", description=body), computed_at=WHEN
    )


# =========================================================================
# 1. THE READING IS NOT A VERDICT
# =========================================================================


def test_a_us_context_posting_is_not_ineligible(config) -> None:
    """The whole point. A 401(k) and silence is context; the employer has not
    said no, so nothing may report that it did."""
    result = read(config, US_CONTEXT_BODY)
    assert result.domestic.context is DomesticContext.LIKELY_US_DOMESTIC
    assert result.eligibility_status is not EligibilityStatus.VERIFIED_NOT_ELIGIBLE
    assert result.eligibility_status is EligibilityStatus.UNRESOLVED


def test_the_reading_carries_the_employer_sentence_that_produced_it(config) -> None:
    """A reading with no quote behind it is an assertion asking to be trusted."""
    result = read(config, US_CONTEXT_BODY)
    assert result.domestic.evidence
    assert result.domestic.evidence in US_CONTEXT_BODY
    assert result.domestic.signal


def test_saying_where_you_hire_changes_the_reading_and_not_the_refusal(config) -> None:
    domestic = read(config, US_CONTEXT_BODY)
    international = read(config, INTERNATIONAL_BODY)
    assert domestic.domestic.context is DomesticContext.LIKELY_US_DOMESTIC
    assert international.domestic.context is DomesticContext.INTERNATIONAL_STATED
    # And the second one is now positively eligible, from a stated scope.
    assert international.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE


def test_no_domestic_reading_can_produce_a_refusal(config) -> None:
    """Structural. `DomesticContext` has three members and none of them is a
    verdict; a fourth that was would be caught here rather than in a screenshot."""
    for member in DomesticContext:
        assert "ELIGIB" not in member.value, member
        assert "REJECT" not in member.value, member


# =========================================================================
# 2. THE COLUMN IS WRITTEN, AND FROM THE READING
# =========================================================================


def facet(result, name: str) -> object:
    """One facet column, BY NAME.

    These were read as `columns[-1]` and `columns[-2]`, which is fine right up
    until a thirteenth column is added -- and then both assertions quietly
    describe a different column. `FACET_COLUMN_NAMES` exists so neither a
    reader nor a test has to count.
    """
    return facet_columns(result)[FACET_COLUMN_NAMES.index(name)]


def test_the_facet_column_carries_the_reading(config) -> None:
    """Not derived a second time, and not parsed out of `result_json`. The
    column is the same object the drawer explains."""
    result = read(config, US_CONTEXT_BODY)
    assert facet(result, "employment_context") == DomesticContext.LIKELY_US_DOMESTIC.value


def test_a_regime_nobody_stated_is_null_rather_than_the_word(config) -> None:
    """`UNRESOLVED` is the honest answer for almost every posting, and a column
    holding eighteen thousand copies of "we could not tell" is a column whose
    index answers nothing."""
    assert facet(read(config, US_CONTEXT_BODY), "contract_regime") is None


# =========================================================================
# 3. THE FILTER ASKS ONE QUESTION AND CANNOT ANSWER THE OTHER
# =========================================================================


def test_the_two_filters_are_separate_parameters(api: JobsApi) -> None:
    """A query for context can never return an eligibility verdict, because
    they are different columns behind different query parameters."""
    both = api.handle_api(
        "GET",
        "/api/jobs",
        {"employment_context": ["LIKELY_US_DOMESTIC"], "eligibility": ["UNRESOLVED"]},
        {},
    )
    assert "items" in both


def test_an_invented_context_value_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("GET", "/api/jobs", {"employment_context": ["PROBABLY_AMERICAN"]}, {})
    assert caught.value.status == 400


def test_eligibility_is_not_accepted_as_a_context_value(api: JobsApi) -> None:
    """The vocabularies are disjoint on purpose, so a caller confusing them
    gets an error rather than a plausible empty list."""
    with pytest.raises(ApiError) as caught:
        api.handle_api("GET", "/api/jobs", {"employment_context": ["VERIFIED_NOT_ELIGIBLE"]}, {})
    assert caught.value.status == 400


def test_filtering_by_context_returns_only_that_context(api: JobsApi) -> None:
    db = api.config.db_path
    conn = connect(db)
    try:
        repo = ScoredJobQuery(conn)
        identity = api._identity()
        for value in ("LIKELY_US_DOMESTIC", "INTERNATIONAL_STATED", "UNRESOLVED"):
            rows = repo.page(
                *identity,
                JobFilter(employment_context=(value,), include_ineligible=True, limit=200),
            )
            for job in rows:
                assert job.result.domestic.context.value == value
    finally:
        conn.close()


# =========================================================================
# 4. THE DIGEST SECTION
# =========================================================================


def test_the_digest_offers_the_section_and_calls_it_verify(api: JobsApi) -> None:
    """The heading says verify, never skip. Every posting in it has UNRESOLVED
    eligibility -- the employer has said nothing about who it may hire."""
    from career_agent.digest import sections

    built = {section.key: section for section in sections(api.search_config())}
    assert "us_context" in built
    section = built["us_context"]
    assert "verify" in section.title.lower()
    assert section.job_filter.employment_context == ("LIKELY_US_DOMESTIC",)
    assert section.job_filter.eligibility == ("UNRESOLVED",)


def test_the_cli_and_the_web_read_the_same_section(api: JobsApi) -> None:
    """One definition. Two copies of a section list is how a terminal and a
    screen start disagreeing about what a word means."""
    from career_agent.digest import sections

    expected = [section.key for section in sections(api.search_config())]
    served = [s["key"] for s in api.handle_api("GET", "/api/daily", {}, {})["sections"]]
    assert served == expected


def test_the_section_does_not_hide_anything_from_the_ordinary_list(api: JobsApi) -> None:
    """Section 13: likely-US-domestic postings are NOT hidden by default. For a
    Brazil-resident candidate they stay visible, with context."""
    everything = api.handle_api("GET", "/api/jobs", {"limit": ["100"]}, {})
    ids = {item["job_id"] for item in everything["items"]}

    conn = connect(api.config.db_path)
    try:
        rows = ScoredJobQuery(conn).page(
            *api._identity(),
            JobFilter(employment_context=("LIKELY_US_DOMESTIC",), limit=100),
        )
    finally:
        conn.close()
    for job in rows:
        assert job.job_id in ids, "a likely-US-domestic posting was hidden by default"

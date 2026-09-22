"""The MVP storage layer against a small but real database.

Not a repeat of the unit tests: what is exercised here is the seam between
them. Rows are fabricated through the collection repositories, exactly as the
collector writes them, then scored, then edited by a simulated human -- and the
three claims that matter are asserted end to end.

  * one filter, one job-id set, whichever reader asks;
  * a status edit is on DISK, not in a connection's memory;
  * the facet counts add up to the population they were computed over.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.domain.application import ApplicationStatus, has_applied
from career_agent.domain.enums import (
    AnalysisConfidence,
    EligibilityStatus,
    FitBand,
    Seniority,
    SenioritySource,
)
from career_agent.domain.matching import MatchResult, SeniorityReading
from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import (
    SET_VALUED_FACETS,
    ApplicationRepo,
    JobFilter,
    MatchRepo,
    ScoredJobQuery,
)
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

CONFIG_ID = "demo_candidate_v1"
CONFIG_VERSION = 2

#: Three companies, eight postings. Small enough to reason about by hand, wide
#: enough that every filter below has both a hit and a miss. Band and
#: eligibility are written as their vocabulary strings and converted on the way
#: in, so one posting reads as one line.
CorpusRow = tuple[str, str, str, str, int, str, str]

CORPUS: tuple[CorpusRow, ...] = (
    (
        "acme",
        "greenhouse",
        "Revenue Operations Engineer",
        "Remote - Brazil",
        92,
        "STRONG",
        "VERIFIED_ELIGIBLE",
    ),
    (
        "acme",
        "greenhouse",
        "Salesforce Administrator",
        "Remote - LATAM",
        78,
        "GOOD",
        "LIKELY_ELIGIBLE",
    ),
    ("acme", "greenhouse", "Engineering Manager", "Sao Paulo, Brazil", 24, "WEAK", "UNRESOLVED"),
    (
        "beta",
        "lever",
        "Business Systems Analyst",
        "Remote - Worldwide",
        66,
        "GOOD",
        "LIKELY_ELIGIBLE",
    ),
    (
        "beta",
        "lever",
        "Data Platform Engineer",
        "Berlin, Germany",
        41,
        "MODERATE",
        "VERIFIED_NOT_ELIGIBLE",
    ),
    ("beta", "lever", "Solutions Architect", "Remote - EMEA", 71, "GOOD", "UNRESOLVED"),
    ("gamma", "ashby", "GTM Systems Lead", "Remote - Americas", 84, "STRONG", "LIKELY_ELIGIBLE"),
    ("gamma", "ashby", "Technical Support Engineer", "Lisbon, Portugal", 33, "WEAK", "UNRESOLVED"),
)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "career.db"


#: `MatchRepo.store` now REQUIRES the configuration bytes a score was
#: computed from. A default let a caller silently record "UNRECORDED",
#: which defeats the drift check the digest exists for.
DIGEST = "a" * 64


def build_corpus(conn: sqlite3.Connection) -> list[str]:
    """Write the eight postings and their scores. Returns the job ids in order.

    Everything goes through the production repositories: a fixture that
    hand-writes INSERTs can pass against a schema the real write path would be
    rejected by, which is the failure this test exists to catch.
    """
    companies = CompanyRepo(conn)
    boards = SourceBoardRepo(conn)
    raws = JobRawRepo(conn)
    jobs = JobRepo(conn)
    matches = MatchRepo(conn)

    job_ids: list[str] = []
    for index, (slug, provider, title, location, score, band, eligibility) in enumerate(CORPUS):
        company_id = companies.upsert(CompanyRecord(slug=slug, name=slug.title()))
        board_id = boards.upsert(
            SourceBoardRecord(
                company_id=company_id, provider=provider, board_identifier=f"{slug}-{provider}"
            )
        )
        content_hash = raws.put(f"{title} at {slug}. Own the systems that run revenue.")
        job_id = jobs.upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=provider,
                external_id=f"{slug}-{index}",
                url=f"https://example.test/{slug}/{index}",
                title=title,
                location_raw=location,
                posted_at="2026-09-01T00:00:00Z",
                content_hash=content_hash,
            )
        )
        matches.store(
            job_id,
            content_hash,
            MatchResult(
                config_id=CONFIG_ID,
                config_version=CONFIG_VERSION,
                match_score=score,
                data_confidence=min(100, score + 5),
                eligibility_status=EligibilityStatus(eligibility),
                fit_band=FitBand(band),
                seniority=SeniorityReading(
                    value=Seniority.SENIOR,
                    source=SenioritySource.TITLE_GRADE,
                    confidence=AnalysisConfidence.HIGH,
                    evidence="Senior Systems Analyst",
                ),
                computed_at="2026-09-04T12:00:00Z",
            ),
            config_digest=DIGEST,
        )
        job_ids.append(job_id)
    return job_ids


@pytest.fixture
def corpus(db_path: Path) -> Iterator[tuple[sqlite3.Connection, list[str]]]:
    conn = connect(db_path)
    migrate(conn)
    ids = build_corpus(conn)
    yield conn, ids
    conn.close()


# --- one filter, one answer -------------------------------------------------


FILTERS = [
    JobFilter(limit=100),
    JobFilter(min_score=70, limit=100),
    JobFilter(companies=("acme", "gamma"), limit=100),
    JobFilter(providers=("lever",), limit=100),
    JobFilter(eligibility=("LIKELY_ELIGIBLE", "VERIFIED_ELIGIBLE"), limit=100),
    JobFilter(fit_bands=("GOOD",), min_confidence=70, limit=100),
    JobFilter(remote_only=True, limit=100),
    JobFilter(search="engineer", limit=100),
    JobFilter(min_score=60, max_score=80, sort="company", direction="asc", limit=100),
]


@pytest.mark.parametrize("job_filter", FILTERS, ids=[f"filter-{i}" for i in range(len(FILTERS))])
def test_count_and_page_describe_the_same_rows(
    corpus: tuple[sqlite3.Connection, list[str]], job_filter: JobFilter
) -> None:
    conn, _ = corpus
    query = ScoredJobQuery(conn)
    listed = query.page(CONFIG_ID, CONFIG_VERSION, job_filter)

    assert query.count(CONFIG_ID, CONFIG_VERSION, job_filter) == len(listed)
    assert len({row.job_id for row in listed}) == len(listed), "no row appears twice"


def test_the_filters_are_not_all_the_same_query(
    corpus: tuple[sqlite3.Connection, list[str]],
) -> None:
    """count == len(page) proves nothing if every filter matches everything."""
    conn, _ = corpus
    query = ScoredJobQuery(conn)
    sizes = {query.count(CONFIG_ID, CONFIG_VERSION, f) for f in FILTERS}

    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter()) == len(CORPUS)
    assert len(sizes) > 3
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(providers=("lever",))) == 3
    assert query.count(CONFIG_ID, CONFIG_VERSION, JobFilter(remote_only=True)) == 5


def test_the_same_job_ids_come_back_however_the_page_is_ordered(
    corpus: tuple[sqlite3.Connection, list[str]],
) -> None:
    """Sorting changes the sequence, never the membership. Cards sort by score
    and Table by whatever column was clicked; they must still be showing the
    same jobs."""
    conn, _ = corpus
    query = ScoredJobQuery(conn)
    by_score = query.page(CONFIG_ID, CONFIG_VERSION, JobFilter(min_score=60, limit=100))
    by_title = query.page(
        CONFIG_ID, CONFIG_VERSION, JobFilter(min_score=60, sort="title", direction="asc", limit=100)
    )

    assert {row.job_id for row in by_score} == {row.job_id for row in by_title}
    assert [row.job_id for row in by_score] != [row.job_id for row in by_title]


# --- persistence ------------------------------------------------------------


def test_a_status_edit_survives_closing_and_reopening_the_database(db_path: Path) -> None:
    """The edit is on disk. This is the difference between a workflow tool and
    a session's worth of clicking."""
    first = connect(db_path)
    migrate(first)
    job_ids = build_corpus(first)
    target = job_ids[0]
    ApplicationRepo(first).set_status(
        target,
        ApplicationStatus.APPLIED,
        note="applied via the careers page",
        now="2026-09-03T09:00:00Z",
    )
    ApplicationRepo(first).set_saved(target, True, now="2026-09-03T09:05:00Z")
    first.close()

    second = connect(db_path)
    try:
        repo = ApplicationRepo(second)
        state = repo.get(target)
        assert state is not None
        status, applied_at, saved, _notes = state
        assert (status, applied_at, saved) == (ApplicationStatus.APPLIED, "2026-09-03", True)
        assert has_applied(status, applied_at) is True

        history = repo.history(target)
        assert [(e["from_status"], e["to_status"]) for e in history] == [(None, "APPLIED")]
        assert history[0]["note"] == "applied via the careers page"

        listed = ScoredJobQuery(second).get_one(target, CONFIG_ID, CONFIG_VERSION)
        assert listed is not None
        assert listed.application_status == "APPLIED"
        assert listed.saved is True
    finally:
        second.close()


def test_re_scoring_a_version_leaves_the_human_edits_alone(
    corpus: tuple[sqlite3.Connection, list[str]],
) -> None:
    """`delete_for` is the re-score path. It must not be able to reach the one
    table a person wrote to."""
    conn, job_ids = corpus
    ApplicationRepo(conn).set_status(
        job_ids[1], ApplicationStatus.INTERVIEW, now="2026-09-03T09:00:00Z"
    )

    assert MatchRepo(conn).delete_for(CONFIG_ID, CONFIG_VERSION) == len(CORPUS)
    assert MatchRepo(conn).count_for(CONFIG_ID, CONFIG_VERSION) == 0
    assert ApplicationRepo(conn).get(job_ids[1]) == (
        ApplicationStatus.INTERVIEW,
        "2026-09-03",
        False,
        None,
    )
    assert len(ApplicationRepo(conn).history(job_ids[1])) == 1


# --- facets -----------------------------------------------------------------


#: Facets whose buckets partition the result set, so they must sum to
#: `count()`. That property is what lets the interface print a count beside a
#: chip and promise it.
#:
#: `country`, `region`, `signal` and `technology` are SETS: one posting can be
#: in two countries and fire six signals, so their buckets overlap by
#: construction and summing them is meaningless rather than wrong. They are
#: checked by a different property -- every bucket equals what its own filter
#: returns -- in `tests/integration/test_filter_contract.py`.


def test_facets_total_the_unfiltered_count(corpus: tuple[sqlite3.Connection, list[str]]) -> None:
    conn, job_ids = corpus
    ApplicationRepo(conn).set_status(
        job_ids[0], ApplicationStatus.APPLIED, now="2026-09-03T09:00:00Z"
    )
    query = ScoredJobQuery(conn)

    total = query.count(CONFIG_ID, CONFIG_VERSION, JobFilter())
    facets = query.facets(CONFIG_ID, CONFIG_VERSION, JobFilter())

    assert total == len(CORPUS)
    for dimension, buckets in facets.items():
        if dimension in SET_VALUED_FACETS:
            continue
        assert sum(buckets.values()) == total, dimension
    assert facets["company"] == {"acme": 3, "beta": 3, "gamma": 2}
    assert facets["provider"] == {"greenhouse": 3, "lever": 3, "ashby": 2}
    assert facets["status"] == {"DISCOVERED": 7, "APPLIED": 1}


def test_facets_follow_the_filter_they_were_given(
    corpus: tuple[sqlite3.Connection, list[str]],
) -> None:
    conn, _ = corpus
    query = ScoredJobQuery(conn)
    narrowed = JobFilter(providers=("lever",))

    facets = query.facets(CONFIG_ID, CONFIG_VERSION, narrowed)
    total = query.count(CONFIG_ID, CONFIG_VERSION, narrowed)

    assert total == 3
    assert facets["company"] == {"beta": 3}
    for dimension, buckets in facets.items():
        if dimension in SET_VALUED_FACETS:
            continue
        assert sum(buckets.values()) == total, dimension

"""What a source DID, kept apart from what the catalogue says it IS.

The distinction this module exists for is the one a person needs on a Tuesday
morning: a source nobody has run is not a source that is broken. Reading one as
the other is how a health report teaches its reader to skip it.
"""

from __future__ import annotations

import sqlite3

import pytest

from career_agent.sources.health import SourceHealth, _stage_provider, health, summarise

SCHEMA = """
CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, first_seen_at TEXT,
                  company_id TEXT, content_hash TEXT, closed_at TEXT,
                  collection_status TEXT, location_raw TEXT);
CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT, company_id TEXT,
                           last_collected_at TEXT, last_error TEXT);
CREATE TABLE pipeline_run (id TEXT PRIMARY KEY, stage TEXT, started_at TEXT,
                           finished_at TEXT, status TEXT, stats_json TEXT, error TEXT);
CREATE TABLE job_match (id TEXT PRIMARY KEY, job_id TEXT, config_id TEXT,
                        config_version INTEGER, schema_version INTEGER);
"""


@pytest.fixture
def db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def entry(entries: list[SourceHealth], source_id: str) -> SourceHealth:
    return next(e for e in entries if e.source.id == source_id)


# =========================================================================
# 1. NEVER RUN IS NOT BROKEN
# =========================================================================


def test_a_source_nobody_has_run_says_so_without_alarming_anyone(db) -> None:
    note = entry(health(db), "weworkremotely").note
    assert "Never collected" in note
    assert "error" not in note.lower()
    assert "fail" in note.lower(), "the sentence must say plainly that nothing failed"


def test_a_blocked_source_says_why_rather_than_that_it_failed(db) -> None:
    note = entry(health(db), "vagas").note
    assert "terms" in note or "robots" in note
    assert "Never collected" not in note, "a policy answer is not a missing run"


@pytest.mark.parametrize(
    ("source_id", "expected"),
    [
        ("jooble", "DISABLED_QUOTA"),
        ("elite_software_automation", "BLOCKED_PROVIDER"),
        ("jobbol", "BLOCKED_PROVIDER"),
        ("lemon_io", "NOTHING_PUBLISHED"),
    ],
)
def test_source_health_preserves_the_matrix_blocker(db, source_id, expected) -> None:
    observed = entry(health(db), source_id)
    assert observed.state == expected
    assert "Never collected" not in observed.note
    if expected == "BLOCKED_PROVIDER":
        assert observed.as_dict()["collection_blocker"] == observed.source.collection_blocker


def test_an_undocumented_source_is_not_reported_as_permitted() -> None:
    """`UNDOCUMENTED` means the vendor published no terms. It is not a quieter
    yes, and the sentence must not read as one.

    THIS TEST HAS NOW LOST ITS EXAMPLE TWICE, both times to the same decision.
    It asked about Gupy until 2026-09-09, when ADR-0018 decided an undeclared
    endpoint may be collected and Gupy stopped being undocumented; it moved to
    Workday, whose row was reclassified for exactly the same reason hours
    later. There is no undocumented row left in the catalogue, and
    `test_no_row_needs_the_undeclared_permission_any_more` records that.

    The RULE did not change either time, so it is asserted here of a
    constructed row rather than of whichever source happens to be undocumented
    this week. A test that has to be re-pointed every time a classification
    moves is a test about the catalogue; this one is about the sentence.
    """
    from career_agent.sources.catalogue import Coverage, Source, SourceStatus

    undocumented = SourceHealth(
        source=Source(
            id="example",
            name="Example",
            region="global",
            status=SourceStatus.UNAVAILABLE,
            authorization="the vendor has published nothing about this endpoint",
            coverage=Coverage.UNDOCUMENTED,
        ),
    )
    assert "no terms" in undocumented.note
    assert "Not collected" in undocumented.note


# =========================================================================
# 2. ONE BAD BOARD IS NOT A BROKEN SOURCE
# =========================================================================


def test_three_failing_boards_out_of_a_hundred_are_reported_as_a_fraction(db) -> None:
    """The defect the first version of this module shipped with.

    Greenhouse reaches 96 boards, 3 of them record `NOT_FOUND` because a
    company took its board down, and the report said "The last attempt recorded
    an error" over a source holding 11,763 postings.
    """
    for index in range(96):
        error = "NOT_FOUND: board or resource not found" if index < 3 else None
        db.execute(
            "INSERT INTO source_board VALUES (?, 'greenhouse', 'c1', '2026-09-04T00:00:00Z', ?)",
            (f"b{index}", error),
        )
    for index in range(11763):
        db.execute(
            "INSERT INTO job VALUES (?, 'greenhouse', '2026-09-04T00:00:00Z',"
            " 'c1', 'h', NULL, 'NORMALISED', 'Remote')",
            (f"j{index}",),
        )
    db.commit()

    found = entry(health(db), "greenhouse")
    assert found.postings == 11763
    assert found.boards_with_errors == 3
    note = found.note
    assert note.startswith("11763 postings"), f"the headline is the wrong fact: {note}"
    assert "3 of 96 boards" in note


def test_a_source_that_ran_and_stored_nothing_leads_with_the_error(db) -> None:
    """The other side. No postings AND an error is the case where the error IS
    the news, and it must not be buried behind a count of zero."""
    db.execute(
        "INSERT INTO source_board VALUES ('b1','greenhouse','c1','2026-09-04T00:00:00Z','boom')"
    )
    db.commit()
    note = entry(health(db), "greenhouse").note
    assert "boom" in note


# =========================================================================
# 3. RUNS ARE ATTRIBUTED, OR NOT AT ALL
# =========================================================================


@pytest.mark.parametrize(
    ("stage", "expected"),
    [
        ("collect:wwr", "wwr"),
        ("collect-speedrun", "speedrun"),
        ("collect", None),
        ("retrieval", None),
        ("rescore", None),
    ],
)
def test_a_run_names_its_provider_or_names_nobody(stage: str, expected: str | None) -> None:
    """A run credited to the wrong source is worse than one credited to none.

    `rescore` is deliberately not a collection: it reads the corpus and writes
    scores, so counting it would report every source as freshly collected
    every time preferences changed.
    """
    assert _stage_provider(stage) == expected


def test_a_rescore_is_not_a_collection(db) -> None:
    db.execute(
        "INSERT INTO pipeline_run VALUES ('r1','rescore','2026-09-06T00:00:00Z',"
        "'2026-09-06T00:05:00Z','OK','{}',NULL)"
    )
    db.commit()
    assert "Never collected" in entry(health(db), "greenhouse").note


# =========================================================================
# 4. THE SUMMARY COUNTS WHAT WAS VERIFIED
# =========================================================================


def test_the_summary_counts_resolved_coverage_not_declared(db) -> None:
    """`resolve` downgrades a source that claims to be operational and has
    persisted nothing. The summary must count the downgraded answer, or it
    would report a source as working on the strength of its own file."""
    counts = summarise(health(db))
    assert counts.get("OPERATIONAL", 0) == 0, (
        "a source with an empty corpus was summarised as operational"
    )
    assert sum(counts.values()) > 0


def test_a_permitted_source_with_nothing_to_collect_says_so() -> None:
    """`NOTHING_PUBLISHED` needs a sentence of its own.

    Its two neighbours in that branch are about PERMISSION and this one is not.
    Without a line for it, a permitted source with no openings fell through to
    "Never collected. Nothing has failed; nothing has run" -- which reads as a
    job somebody forgot to do rather than as a company that publishes no
    openings.
    """
    from career_agent.sources.catalogue import Coverage, Source, SourceStatus

    entry = SourceHealth(
        source=Source(
            id="example",
            name="Example",
            region="global",
            status=SourceStatus.SEARCH_LINK,
            authorization="their robots file names this reader and allows it",
            coverage=Coverage.NOTHING_PUBLISHED,
        ),
    )
    assert "nothing to collect" in entry.note
    assert "allow us" in entry.note
    assert "failed" not in entry.note

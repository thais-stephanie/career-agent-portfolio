"""A site is "updated in part" only if THIS run read valid postings.

Jobs kept from earlier runs stay available. They are not what a failed update
obtained, and counting them would turn "we read nothing" into "partly updated".
Three situations are kept apart, from the run's own record:

* failed, no new valid data, old jobs preserved  -> failed
* current run partly succeeded                   -> partial
* current run succeeded                          -> complete
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from career_agent.clock import new_id
from career_agent.sources.progress import RefreshState, obtained_data, read_progress
from career_agent.sources.public_health import COMPLETE, FAILED, PARTIAL, outcome_of, record
from career_agent.storage.db import connect, migrate, transaction

STAGE = "collect-remotive"


@pytest.fixture
def conn(tmp_path: Path):
    db = connect(tmp_path / "ledger.db")
    migrate(db)
    yield db
    db.close()


def run_row(conn, *, status: str, stats: dict, started: str, finished: str | None) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), STAGE, started, finished, status, json.dumps(stats)),
        )


def progress(conn):
    (row,) = read_progress(conn, stage_for={"remotive": STAGE})
    return row


EARLIER = {"jobs_new": 40, "postings_seen": 40}


def test_a_failure_that_read_nothing_is_failed_even_with_old_jobs_kept(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats=EARLIER,
        started="2026-10-09T10:00:00Z",
        finished="2026-10-09T10:05:00Z",
    )
    run_row(
        conn,
        status="FAILED",
        stats={"jobs_new": 0, "postings_seen": 0, "failures": ["HTTP 503"]},
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:00:30Z",
    )
    row = progress(conn)
    assert row.state is RefreshState.FAILED
    assert row.last_success == "2026-10-09T10:05:00Z", "the earlier read is still what is held"


def test_a_run_that_logged_failures_and_read_nothing_is_not_a_success_either(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats={"postings_seen": 0, "failures": ["no answer"]},
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:00:30Z",
    )
    assert progress(conn).state is RefreshState.FAILED
    assert outcome_of("OK", {"postings_seen": 0, "failures": ["no answer"]}) == (FAILED, "ERROR")


def test_a_run_that_partly_succeeded_this_time_is_partial(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats=EARLIER,
        started="2026-10-09T10:00:00Z",
        finished="2026-10-09T10:05:00Z",
    )
    run_row(
        conn,
        status="FAILED",
        stats={"jobs_new": 12, "postings_seen": 30, "failures": ["transfer did not finish"]},
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:09:00Z",
    )
    assert progress(conn).state in {RefreshState.PARTIAL, RefreshState.DUE, RefreshState.STALE}
    assert outcome_of("FAILED", {"jobs_new": 12, "failures": ["x"]}) == (PARTIAL, "SOME_FAILED")


def test_a_successful_run_this_time_is_complete(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats={"jobs_new": 5, "postings_seen": 40},
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:05:00Z",
    )
    row = progress(conn)
    assert row.state in {RefreshState.COMPLETE, RefreshState.DUE, RefreshState.STALE}
    assert row.reason is None
    assert outcome_of("OK", {"jobs_new": 5, "postings_seen": 40}) == (COMPLETE, None)


def test_a_run_told_to_wait_before_reading_anything_changes_nothing(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats=EARLIER,
        started="2026-10-09T10:00:00Z",
        finished="2026-10-09T10:05:00Z",
    )
    run_row(
        conn,
        status="OK",
        stats={"deferred_reason": "TIME_BUDGET", "postings_seen": 0},
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:00:20Z",
    )
    row = progress(conn)
    assert row.state is not RefreshState.FAILED and row.last_success == "2026-10-09T10:05:00Z"
    assert not obtained_data({"deferred_reason": "TIME_BUDGET", "postings_seen": 0})


def test_a_feed_read_cut_short_after_reading_is_partial_and_says_it_continues(conn) -> None:
    run_row(
        conn,
        status="OK",
        stats=EARLIER,
        started="2026-10-09T10:00:00Z",
        finished="2026-10-09T10:05:00Z",
    )
    cut = {
        "deferred_reason": "TIME_BUDGET",
        "continues": True,
        "postings_seen": 50,
        "jobs_new": 10,
        "jobs_unchanged": 40,
    }
    run_row(
        conn,
        status="OK",
        stats=cut,
        started="2026-10-11T10:00:00Z",
        finished="2026-10-11T10:15:00Z",
    )
    row = progress(conn)
    assert row.state in {RefreshState.PARTIAL, RefreshState.DUE, RefreshState.STALE}
    assert row.last_success == "2026-10-09T10:05:00Z", "a cut read is not a finished update"
    assert outcome_of("OK", cut) == (PARTIAL, "PROGRESSIVE")


def test_the_shared_record_ignores_a_deferral_and_a_failure_that_read_nothing(conn) -> None:
    def shared(stats: dict, status: str) -> dict:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'source_health'").fetchone()
        assert row, "the shared health table exists after migrating"
        with transaction(conn):
            record(
                conn,
                stage=STAGE,
                started_at="2026-10-11T10:00:00Z",
                finished_at="2026-10-11T10:05:00Z",
                status=status,
                stats=stats,
            )
        return {
            r[0]: r[1] for r in conn.execute("SELECT source_key, last_outcome FROM source_health")
        }

    assert shared({"jobs_new": 3, "postings_seen": 3}, "OK") == {STAGE: "COMPLETE"}
    # A deferral is no news: the earlier verdict stays.
    assert shared({"deferred_reason": "CANCELLED", "postings_seen": 0}, "OK") == {STAGE: "COMPLETE"}
    # A failure that read nothing is recorded as one, whatever the corpus holds.
    assert shared({"jobs_new": 0, "failures": ["HTTP 503"]}, "FAILED") == {STAGE: "FAILED"}

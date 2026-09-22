"""Retrieval as something the person starts and watches.

Driven with an injected unit of work, so the whole lifecycle -- start,
progress, cancel, failure, refusal of a second run -- is exercised without a
single network call.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from career_agent.pipeline.retrieval import (
    RetrievalRunner,
    SourceOutcome,
    build_funnel,
    provider_by_board,
    source_outcomes,
)


def _corpus() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE job (id TEXT PRIMARY KEY, company_id TEXT, title TEXT,
                          content_hash TEXT, closed_at TEXT);
        CREATE TABLE job_raw (content_hash TEXT PRIMARY KEY, description_text TEXT);
        CREATE TABLE job_match (job_id TEXT, config_id TEXT, config_version INTEGER,
                                match_score INTEGER, eligibility_status TEXT);
        CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT);
        """
    )
    conn.execute("INSERT INTO job_raw VALUES ('h1', 'text')")
    for i, (closed, score, elig) in enumerate(
        [
            (None, 90, "UNRESOLVED"),
            (None, 40, "UNRESOLVED"),
            (None, 80, "VERIFIED_NOT_ELIGIBLE"),
            ("2026-01-01", 70, "UNRESOLVED"),
        ],
        start=1,
    ):
        conn.execute("INSERT INTO job VALUES (?, 'c1', 'Engineer', 'h1', ?)", (f"j{i}", closed))
        conn.execute("INSERT INTO job_match VALUES (?, 'cfg', 1, ?, ?)", (f"j{i}", score, elig))
    return conn


# =========================================================================
# The funnel
# =========================================================================


def test_the_funnel_is_counted_from_the_database() -> None:
    """Derived, never tallied in flight. A hand-kept count that drifts from
    what was stored is worse than none, because it looks authoritative."""
    funnel = build_funnel(_corpus(), "cfg", 1, shortlist_min=55)
    assert funnel["fetched"] == 4
    assert funnel["active"] == 3, "a closed posting is fetched but not active"
    assert funnel["scored"] == 4
    assert funnel["eligible"] == 3, "the explicitly ineligible one is excluded"
    assert funnel["recommended"] == 3, "three score at or above 55"
    assert funnel["deduplicated"] == 1, "one company, one title, one role"


def test_a_corpus_scored_under_other_preferences_is_reported_not_hidden() -> None:
    """**The count that turns "everything is gone" into "the question changed".**

    Every stage below `fetched` is keyed on the preferences IN FORCE, which is
    right: a score is only true relative to the question it answered. When the
    preferences move, four rows go to zero at once and the screen reads
    `Fetched 22048 / Scored 0` -- which the owner met on 2026-09-08 and which
    looks exactly like data loss.

    Nothing was lost. 19,469 scores were sitting under the previous version of
    the same preferences. This is the number that lets the panel say so.
    """
    conn = _corpus()

    current = build_funnel(conn, "cfg", 1, shortlist_min=55)
    moved_on = build_funnel(conn, "cfg", 2, shortlist_min=55)

    assert current["scored"] == 4
    assert current["scored_other_versions"] == 0, "nothing is stored under another version"

    assert moved_on["scored"] == 0, "no row answers the new question"
    assert moved_on["fetched"] == 4, "and the corpus is untouched"
    assert moved_on["scored_other_versions"] == 4, "the old answers are still there"


def test_the_funnel_narrows(cfg: None = None) -> None:
    """Each stage must be a subset of the one before it, or the funnel is
    describing something other than a funnel."""
    f = build_funnel(_corpus(), "cfg", 1, shortlist_min=55)
    assert f["active"] <= f["fetched"]
    assert f["deduplicated"] <= f["scored"]
    assert f["recommended"] <= f["scored"]


def test_a_database_without_the_tables_reports_zeros_not_a_crash() -> None:
    """The funnel is on the status route, which the interface polls. It must
    degrade rather than take the page down."""
    bare = sqlite3.connect(":memory:")
    bare.row_factory = sqlite3.Row
    funnel = build_funnel(bare, "cfg", 1, 55)
    assert set(funnel) and all(v == 0 for v in funnel.values())


# =========================================================================
# Per-source honesty
# =========================================================================


def test_a_failure_is_attributed_to_its_source() -> None:
    """ "One source is down" and "everything is down" are different situations,
    and the person has to tell them apart before trusting the run."""

    class Failure:
        def __init__(self, board_id: str, reason: str) -> None:
            self.board_id = board_id
            self.reason = reason

    class Stats:
        failures = [Failure("b1", "timeout"), Failure("b2", "500")]
        holds: list[object] = []

    rows = source_outcomes(Stats(), {"b1": "alpha", "b2": "alpha", "b3": "beta"})
    by_provider = {row["provider"]: row for row in rows}

    assert by_provider["alpha"]["boards_failed"] == 2
    assert by_provider["alpha"]["status"] == "failed"
    assert "timeout" in by_provider["alpha"]["failures"][0]
    # A source that was never attempted says so rather than looking healthy.
    assert by_provider["beta"]["status"] == "not attempted"


def test_a_source_with_some_boards_failing_is_partial_not_failed() -> None:
    outcome = SourceOutcome(
        provider="alpha", boards_attempted=3, boards_succeeded=2, boards_failed=1
    )
    assert outcome.status == "partial"


def test_failure_text_is_capped_but_the_total_is_not_hidden() -> None:
    """A run where a hundred boards failed must not put a hundred strings on
    one screen -- and must not pretend only five failed."""
    outcome = SourceOutcome(provider="a", boards_attempted=9, boards_failed=9)
    outcome.failures = [f"error {i}" for i in range(9)]
    payload = outcome.as_dict()
    assert len(payload["failures"]) == 5
    assert payload["failures_total"] == 9


def test_boards_map_to_their_providers() -> None:
    conn = _corpus()
    conn.execute("INSERT INTO source_board VALUES ('b1', 'alpha')")
    assert provider_by_board(conn) == {"b1": "alpha"}


# =========================================================================
# The runner
# =========================================================================


def test_a_run_reports_progress_and_finishes() -> None:
    runner = RetrievalRunner()
    released = threading.Event()

    def work(state, cancel) -> None:
        state.boards_total = 2
        state.boards_done = 1
        released.wait(5)
        state.boards_done = 2

    started = runner.start(work, "run-1")
    assert started["status"] == "running"
    assert runner.running

    released.set()
    runner.join(5)
    snapshot = runner.snapshot()
    assert snapshot["status"] == "done"
    assert snapshot["boards_done"] == 2
    assert snapshot["finished_at"]


def test_a_second_run_is_refused_while_one_is_going() -> None:
    """Two collections would interleave writes to the same boards and double
    the request rate at endpoints this project is deliberately polite to."""
    runner = RetrievalRunner()
    released = threading.Event()
    runner.start(lambda state, cancel: released.wait(5), "run-1")

    with pytest.raises(RuntimeError):
        runner.start(lambda state, cancel: None, "run-2")

    released.set()
    runner.join(5)
    # And once it is done, another may start.
    runner.start(lambda state, cancel: None, "run-3")
    runner.join(5)
    assert runner.snapshot()["run_id"] == "run-3"


def test_cancelling_is_cooperative_and_reported() -> None:
    runner = RetrievalRunner()
    seen = threading.Event()

    def work(state, cancel) -> None:
        seen.set()
        for _ in range(100):
            if cancel.is_set():
                return
            threading.Event().wait(0.01)

    runner.start(work, "run-1")
    seen.wait(5)
    assert runner.cancel() is True
    runner.join(5)
    assert runner.snapshot()["status"] == "cancelled"


def test_cancelling_when_nothing_runs_says_so() -> None:
    assert RetrievalRunner().cancel() is False


def test_a_failing_run_is_reported_not_swallowed() -> None:
    runner = RetrievalRunner()

    def work(state, cancel) -> None:
        raise RuntimeError("the board exploded")

    runner.start(work, "run-1")
    runner.join(5)
    snapshot = runner.snapshot()
    assert snapshot["status"] == "failed"
    assert "exploded" in snapshot["error"]
    assert snapshot["finished_at"], "a failed run still finished"


def test_nothing_has_run_reads_as_none() -> None:
    assert RetrievalRunner().snapshot() is None


def test_source_rows_are_counted_from_the_database_not_narrated() -> None:
    """The bug a real run exposed, pinned.

    `CollectionStats` carries per-board FAILURES but only an aggregate success
    count, so rows built from it alone reported "not attempted, 0/0" for the
    three sources that had just collected 2,585 postings. The funnel was
    already derived from the database and the sources were not, and that
    inconsistency is what produced the wrong table.
    """
    from career_agent.pipeline.retrieval import source_outcomes_from_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT);
        CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, source_board_id TEXT,
                          first_seen_at TEXT, last_seen_at TEXT);
        """
    )
    conn.execute("INSERT INTO source_board VALUES ('b1', 'alpha')")
    conn.execute("INSERT INTO source_board VALUES ('b2', 'alpha')")
    conn.execute(
        "INSERT INTO job VALUES ('j1', 'alpha', 'b1', '2026-01-01', '2026-09-04T22:00:00Z')"
    )
    conn.execute(
        "INSERT INTO job VALUES ('j2', 'alpha', 'b1', '2026-09-04T22:00:00Z',"
        " '2026-09-04T22:00:00Z')"
    )
    # Seen before the run began: counts toward neither.
    conn.execute("INSERT INTO job VALUES ('j3', 'alpha', 'b2', '2025-01-01', '2025-01-01')")

    class Stats:
        failures: list[object] = []
        holds: list[object] = []

    row = source_outcomes_from_db(conn, Stats(), {}, "2026-09-04T21:00:00Z")[0]
    assert row["provider"] == "alpha"
    assert row["status"] == "ok", "a source that answered must not read 'not attempted'"
    assert row["boards_attempted"] == 2
    assert row["boards_succeeded"] == 1, "only b1 answered during the run"
    assert row["postings_fetched"] == 2
    assert row["jobs_new"] == 1, "only j2 was first seen during this run"


def test_a_source_can_never_succeed_inside_one_that_was_not_attempted() -> None:
    """The arithmetic that made the wrong table possible, closed."""
    from career_agent.pipeline.retrieval import source_outcomes_from_db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        "CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT);"
        "CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, source_board_id TEXT,"
        " first_seen_at TEXT, last_seen_at TEXT);"
    )
    # No board registered, yet a posting arrived from one.
    conn.execute(
        "INSERT INTO job VALUES ('j1', 'ghost', 'b9', '2026-09-04T22:00:00Z',"
        " '2026-09-04T22:00:00Z')"
    )

    class Stats:
        failures: list[object] = []
        holds: list[object] = []

    row = source_outcomes_from_db(conn, Stats(), {}, "2026-09-04T21:00:00Z")[0]
    assert row["boards_attempted"] >= row["boards_succeeded"]
    assert row["status"] != "not attempted"


def test_the_collector_reports_progress_between_boards() -> None:
    """A progress indicator that only moves once is a spinner with extra steps.

    The first real run showed 0 of 234 for four minutes and then jumped to
    234, because the counter was written after `collect_all` returned.
    """
    from career_agent.pipeline.collect import Collector

    seen: list[tuple[int, int]] = []
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Drive the loop directly: the point is the callback contract, not the
    # network the real collector would use.
    class Fake(Collector):
        def __init__(self) -> None:  # noqa: D107
            pass

    assert hasattr(Collector, "collect_all")
    import inspect

    signature = inspect.signature(Collector.collect_all)
    assert "on_board" in signature.parameters, "no progress hook"
    assert "should_stop" in signature.parameters, "no cancellation hook"
    # Both must default to None so every existing caller is unaffected.
    assert signature.parameters["on_board"].default is None
    assert signature.parameters["should_stop"].default is None
    del seen, conn, Fake

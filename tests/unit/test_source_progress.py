"""A progress display may not claim completeness it cannot measure.

WHY THIS FILE EXISTS
--------------------
Gupy takes hours. The old experience was *collect everything, rescore
everything, then show jobs*, which lets the slowest source decide when ANY job
is visible -- including the 11,134 Greenhouse postings scored yesterday and
sitting in the database.

`sources/progress.py` exists so a screen can say what is happening instead of
spinning. The danger in that is obvious and is what these tests are about: the
easiest way to draw a progress bar is to invent a denominator. A bar that
reaches 90% and stays there for two hours is worse than no bar, because the
second time somebody sees one they stop believing it.

So the rules asserted here are:

  * a percentage exists only when the PROVIDER published a total;
  * an ETA exists only when a rate and a remainder are both real;
  * "stopped at our own budget" is PARTIAL, never COMPLETE;
  * a run in flight reports what it has done so far, from the heartbeat.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from career_agent.sources.progress import RefreshState, SourceProgress, read_progress

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def ledger() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE pipeline_run ("
        " id TEXT PRIMARY KEY, stage TEXT, started_at TEXT, finished_at TEXT,"
        " status TEXT, stats_json TEXT, error TEXT)"
    )
    return conn


def _run(
    conn: sqlite3.Connection,
    stage: str,
    *,
    run_id: str = "r1",
    started: datetime = NOW,
    finished: datetime | None = None,
    status: str = "RUNNING",
    stats: dict | None = None,
) -> None:
    conn.execute(
        "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            run_id,
            stage,
            started.isoformat().replace("+00:00", "Z"),
            finished.isoformat().replace("+00:00", "Z") if finished else None,
            status,
            json.dumps(stats or {}),
        ),
    )


def _one(conn: sqlite3.Connection, **kwargs) -> SourceProgress:
    rows = read_progress(conn, stage_for={"src": "collect-src"}, now=NOW, **kwargs)
    assert len(rows) == 1
    return rows[0]


# =========================================================================
# 1. NO INVENTED PERCENTAGES
# =========================================================================


def test_a_source_that_published_no_total_reports_counters_and_no_percentage() -> None:
    """Most feeds never say how big they are, and that must stay visible.

    The wrong answer here is a percentage computed against pages read, or
    against whatever the last run happened to find. Both look like knowledge and
    are arithmetic performed on a guess.
    """
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE pipeline_run (id TEXT PRIMARY KEY, stage TEXT, started_at TEXT,"
        " finished_at TEXT, status TEXT, stats_json TEXT, error TEXT)"
    )
    _run(conn, "collect-src", stats={"postings_seen": 412, "pages_read": 5, "jobs_new": 400})
    row = _one(conn)
    assert row.retrieved == 412
    assert row.units_done == 5
    assert row.expected_total is None
    assert row.measurable is False
    assert row.percent is None


def test_a_provider_total_is_what_makes_a_percentage_real(ledger: sqlite3.Connection) -> None:
    _run(ledger, "collect-src", stats={"postings_seen": 300, "claimed_total": 600})
    row = _one(ledger)
    assert row.measurable is True
    assert row.percent == 50


def test_a_percentage_never_exceeds_one_hundred(ledger: sqlite3.Connection) -> None:
    """A provider's own total is an estimate it revises while we walk it.

    A feed that said 79,000 and then served more would otherwise render 104%,
    and a bar that goes past its own end is a bar nobody trusts again.
    """
    _run(ledger, "collect-src", stats={"postings_seen": 1200, "claimed_total": 1000})
    assert _one(ledger).percent == 100


def test_a_partitioned_feed_sums_the_totals_it_measured_per_slice(
    ledger: sqlite3.Connection,
) -> None:
    """A feed that caps its own offset is walked in slices, each measured live.

    A sum of measured parts is still a measurement, which is why this counts and
    a guess would not.
    """
    _run(
        ledger,
        "collect-src",
        stats={"postings_seen": 5000, "partition_totals": {"remote": 2115, "hybrid": 5970}},
    )
    row = _one(ledger)
    assert row.expected_total == 8085
    assert row.percent == 62


# =========================================================================
# 2. THE STATES MEAN DIFFERENT THINGS
# =========================================================================


def test_a_run_in_flight_is_running_and_reports_the_heartbeat(
    ledger: sqlite3.Connection,
) -> None:
    """The whole point of `PipelineRunRepo.progress`.

    Before it, a run in flight held `stats_json = '{}'` for its entire life, so
    the only honest sentence about a three-hour Gupy refresh was "it started".
    """
    _run(ledger, "collect-src", stats={"postings_seen": 54200, "claimed_total": 79000})
    row = _one(ledger)
    assert row.state is RefreshState.RUNNING
    assert row.retrieved == 54200
    assert row.percent == 69


def test_stopping_at_our_own_budget_is_partial_and_not_complete(
    ledger: sqlite3.Connection,
) -> None:
    """ "We chose to stop" and "we reached the end" are different coverage claims.

    V1.6 settled that `stopped_early` and `ceiling_hit` are separate facts. Both
    mean the corpus does not hold everything the provider has, and reporting
    either as COMPLETE would claim coverage nobody measured.
    """
    _run(
        ledger,
        "collect-src",
        finished=NOW,
        status="OK",
        stats={"postings_seen": 5000, "stopped_early": True},
    )
    assert _one(ledger).state is RefreshState.PARTIAL


def test_walking_to_the_end_is_complete(ledger: sqlite3.Connection) -> None:
    _run(
        ledger,
        "collect-src",
        finished=NOW,
        status="OK",
        stats={"postings_seen": 99, "claimed_total": 99, "stopped_early": False},
    )
    assert _one(ledger).state is RefreshState.COMPLETE


def test_a_failed_run_says_so(ledger: sqlite3.Connection) -> None:
    _run(ledger, "collect-src", finished=NOW, status="FAILED", stats={"postings_seen": 0})
    assert _one(ledger).state is RefreshState.FAILED


def test_a_source_nobody_has_ever_run_is_not_started(ledger: sqlite3.Connection) -> None:
    assert _one(ledger).state is RefreshState.NOT_STARTED


def test_paused_and_blocked_carry_a_reason_rather_than_a_flag(
    ledger: sqlite3.Connection,
) -> None:
    """A screen that says "paused" without saying why is one somebody guesses at.

    And the two are not the same: PAUSED is this profile's own choice and is
    reversible from the screen; BLOCKED is somebody else refusing us.
    """
    _run(ledger, "collect-src", finished=NOW, status="OK", stats={"postings_seen": 10})
    paused = _one(ledger, paused={"src": "you are not looking for work in this market"})
    assert paused.state is RefreshState.PAUSED
    assert "market" in (paused.blocker or "")

    blocked = _one(ledger, blocked={"src": "the endpoint has answered 429 since 2026-09-09"})
    assert blocked.state is RefreshState.BLOCKED
    assert "429" in (blocked.blocker or "")


def test_blocked_outranks_paused(ledger: sqlite3.Connection) -> None:
    """Nothing this profile chooses changes a vendor refusing us."""
    row = _one(ledger, paused={"src": "not my market"}, blocked={"src": "quota spent"})
    assert row.state is RefreshState.BLOCKED


# =========================================================================
# 2b. A SHARED STAGE IS NOT ONE SOURCE'S RUN
# =========================================================================


def test_four_families_walked_in_one_pass_do_not_each_claim_the_whole_run(
    ledger: sqlite3.Connection,
) -> None:
    """The defect the real corpus exposed the moment this was pointed at it.

    `collect` walks every employer board in a single pass, so its
    `postings_observed` is the total across all of them. Reporting that against
    each family separately said Greenhouse, Ashby, Lever and Recruiterflow had
    EACH retrieved 20,079 postings on the same night -- four numbers that were
    each larger than the family they described, which is the loudest possible
    way for a count to be wrong.

    The run already records `by_provider`. Each family gets its own slice.
    """
    _run(
        ledger,
        "collect",
        finished=NOW,
        status="OK",
        stats={
            "postings_observed": 20079,
            "by_provider": {
                "greenhouse": {"postings_observed": 11134, "jobs_new": 40},
                "ashby": {"postings_observed": 6324},
                "lever": {"postings_observed": 2463},
            },
        },
    )
    rows = read_progress(
        ledger,
        stage_for={"greenhouse": "collect", "ashby": "collect", "lever": "collect"},
        providers={"greenhouse": "greenhouse", "ashby": "ashby", "lever": "lever"},
        now=NOW,
    )
    got = {r.source_id: r.retrieved for r in rows}
    assert got == {"greenhouse": 11134, "ashby": 6324, "lever": 2463}


def test_a_family_the_shared_run_did_not_name_withholds_its_counters(
    ledger: sqlite3.Connection,
) -> None:
    """Withheld, not zero, and not the run total.

    Zero would say the family collected nothing, which is a claim. The run
    simply did not record anything about it, and "we did not measure this" is
    the honest rendering -- the same rule `ingestion-report` follows when it
    prints `not measured` instead of 0.
    """
    _run(
        ledger,
        "collect",
        finished=NOW,
        status="OK",
        stats={
            "postings_observed": 20079,
            "by_provider": {"greenhouse": {"postings_observed": 11134}},
        },
    )
    rows = read_progress(
        ledger,
        stage_for={"ashby": "collect"},
        providers={"ashby": "ashby"},
        now=NOW,
    )
    assert rows[0].retrieved is None


def test_a_source_with_its_own_stage_is_unaffected(ledger: sqlite3.Connection) -> None:
    _run(ledger, "collect-src", finished=NOW, status="OK", stats={"postings_seen": 99})
    rows = read_progress(
        ledger, stage_for={"src": "collect-src"}, providers={"src": "src"}, now=NOW
    )
    assert rows[0].retrieved == 99


# =========================================================================
# 3. THE ETA REFUSES FAR MORE OFTEN THAN IT ANSWERS
# =========================================================================


def test_no_eta_before_a_rate_means_anything(ledger: sqlite3.Connection) -> None:
    """The first page of a feed does not predict the next eight hundred.

    An estimate that swings from four minutes to four hours inside its first
    minute teaches somebody to ignore the number for the rest of the run.
    """
    _run(
        ledger,
        "collect-src",
        started=NOW - timedelta(seconds=5),
        stats={"postings_seen": 10, "claimed_total": 10000},
    )
    assert _one(ledger).eta_seconds is None


def test_an_eta_appears_once_there_is_a_real_rate_and_a_real_remainder(
    ledger: sqlite3.Connection,
) -> None:
    _run(
        ledger,
        "collect-src",
        started=NOW - timedelta(seconds=600),
        stats={"postings_seen": 300, "claimed_total": 900},
    )
    row = _one(ledger)
    assert row.eta_seconds is not None
    # 300 in 600s is one every two seconds; 600 remain.
    assert 1100 <= row.eta_seconds <= 1300


def test_no_eta_without_a_provider_total(ledger: sqlite3.Connection) -> None:
    _run(
        ledger,
        "collect-src",
        started=NOW - timedelta(seconds=600),
        stats={"postings_seen": 300},
    )
    assert _one(ledger).eta_seconds is None


def test_a_finished_run_has_no_eta(ledger: sqlite3.Connection) -> None:
    _run(
        ledger,
        "collect-src",
        started=NOW - timedelta(seconds=600),
        finished=NOW,
        status="OK",
        stats={"postings_seen": 300, "claimed_total": 900},
    )
    assert _one(ledger).eta_seconds is None


# =========================================================================
# 4. FRESHNESS IS THE LAST RUN THAT WORKED
# =========================================================================


def test_last_success_survives_a_later_failure(ledger: sqlite3.Connection) -> None:
    """ "Fresh as of" must not be erased by tonight's failure.

    The corpus is still there and still usable; what changed is that it did not
    get any newer. Reporting no freshness at all would tell a candidate their
    jobs had vanished.
    """
    good = NOW - timedelta(hours=8)
    _run(
        ledger,
        "collect-src",
        run_id="old",
        started=good - timedelta(minutes=5),
        finished=good,
        status="OK",
        stats={"postings_seen": 500},
    )
    _run(
        ledger,
        "collect-src",
        run_id="new",
        started=NOW - timedelta(minutes=5),
        finished=NOW,
        status="FAILED",
        stats={"postings_seen": 0},
    )
    row = _one(ledger)
    assert row.state is RefreshState.FAILED
    assert row.last_success is not None
    assert row.last_success.startswith(good.date().isoformat())


def test_a_running_source_sorts_first(ledger: sqlite3.Connection) -> None:
    """What is moving is what a person came to the screen to look at."""
    _run(ledger, "collect-a", run_id="a", finished=NOW, status="OK", stats={})
    _run(ledger, "collect-z", run_id="z", stats={"postings_seen": 1})
    rows = read_progress(ledger, stage_for={"a": "collect-a", "z": "collect-z"}, now=NOW)
    assert [r.source_id for r in rows] == ["z", "a"]


def test_targeted_refresh_keeps_other_familys_previous_progress(ledger):
    _run(
        ledger,
        "collect",
        run_id="old",
        started=NOW - timedelta(hours=1),
        finished=NOW - timedelta(minutes=50),
        status="OK",
        stats={
            "by_provider": {
                "example": {"boards_attempted": 1, "boards_succeeded": 1, "postings_observed": 8},
            }
        },
    )
    _run(
        ledger,
        "collect",
        run_id="new",
        finished=NOW,
        status="OK",
        stats={
            "by_provider": {
                "another": {"boards_attempted": 1, "boards_succeeded": 1, "postings_observed": 3},
            }
        },
    )
    rows = read_progress(
        ledger,
        stage_for={"example": "collect", "unrun": "collect"},
        providers={"example": "example", "unrun": "unrun"},
        now=NOW,
    )
    by_id = {row.source_id: row for row in rows}
    assert by_id["example"].retrieved == 8
    assert by_id["example"].last_success == (NOW - timedelta(minutes=50)).isoformat().replace(
        "+00:00", "Z"
    )
    assert by_id["unrun"].state is RefreshState.NOT_STARTED

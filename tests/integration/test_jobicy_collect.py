"""Collecting Jobicy: the cadence gate, the window, and what it stores.

The gate is the part worth the most tests. It is not a politeness setting: the
first-party grant this source is read under is conditioned on no more than one
poll an hour, so a bug here is a licence breach rather than a performance
problem. Every test below runs against a mock transport and a temporary
database. Nothing opens a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.jobicy_collect import (
    MIN_SECONDS_BETWEEN_POLLS,
    RUN_NAME,
    JobicyCollector,
    describe_cooldown,
)
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobicy"
WINDOW = json.loads((FIXTURES / "window-latam.json").read_text(encoding="utf-8"))
EDGE = json.loads((FIXTURES / "edge-cases.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "jobicy.db")
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: Any,
    body: dict[str, Any] | None = None,
    seen: list[httpx.Request] | None = None,
    **kwargs: Any,
) -> JobicyCollector:
    payload = WINDOW if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return JobicyCollector(conn, fetcher, **kwargs)


# =========================================================================
# 1. THE CADENCE GATE
# =========================================================================


def test_the_first_run_is_allowed(conn) -> None:
    assert collector(conn).cooling_down(now=1_000_000.0) is None


def test_a_second_run_inside_the_hour_is_refused(conn) -> None:
    seen: list[httpx.Request] = []
    first = collector(conn, seen=seen)
    first.collect()
    assert len(seen) == 1

    second = collector(conn, seen=seen)
    stats = second.collect()

    assert stats.cooling_down_until is not None
    assert len(seen) == 1, "a refused run still opened a request"
    assert stats.postings_seen == 0
    assert stats.failures == [], "a refusal is not a failure"


def test_the_refusal_never_pushes_its_own_deadline_out(conn) -> None:
    """**A gate that tightens itself is a gate people route around.**

    A refused run writes no `pipeline_run` row, so typing the command four
    times in a row leaves the permitted moment exactly where it was rather than
    moving it an hour further out each time.
    """
    collector(conn).collect()
    first_deadline = collector(conn).cooling_down(now=0.0)

    for _ in range(4):
        collector(conn).collect()

    assert collector(conn).cooling_down(now=0.0) == first_deadline


def test_the_hour_is_measured_from_the_attempt_not_the_success(conn) -> None:
    """A request that failed still consumed one against somebody else's server.

    The run row is written BEFORE the request, so a failed window leaves the
    attempt recorded and the next poll is still an hour away.
    """
    broken = collector(conn, EDGE["contract_error"])
    stats = broken.collect()
    assert stats.failures, "the fixture is supposed to fail"

    assert collector(conn).cooling_down() is not None


def test_the_gate_survives_a_restart(conn) -> None:
    """It reads `pipeline_run` rather than holding state in the process, so a
    second terminal cannot spend the hour's request a second time."""
    collector(conn).collect()

    # A completely fresh collector, as a new process would build.
    assert collector(conn).cooling_down() is not None


def test_force_exists_and_is_an_argument_rather_than_a_setting(conn) -> None:
    """An ethical boundary should cost a reviewable argument at the call site.
    Torre's gate is kept out of the configuration for the same reason."""
    seen: list[httpx.Request] = []
    collector(conn, seen=seen).collect()
    collector(conn, seen=seen).collect(force=True)

    assert len(seen) == 2


def test_an_hour_later_it_is_allowed_again(conn) -> None:
    collector(conn).collect()
    later = collector(conn)
    deadline = later.cooling_down(now=0.0)
    assert deadline is not None

    assert later.cooling_down(now=deadline + 1) is None


def test_the_refusal_says_how_long_is_left() -> None:
    """A refusal with no number is the kind people work around."""
    said = describe_cooldown(10_000.0, now=10_000.0 - 25 * 60)

    assert "25 minute" in said
    assert "nothing failed" in said.lower()


def test_the_interval_is_the_one_the_grant_names() -> None:
    assert MIN_SECONDS_BETWEEN_POLLS == 3600


# =========================================================================
# 2. WHAT ONE WINDOW STORES
# =========================================================================


def test_it_stores_the_postings_with_their_bodies(conn) -> None:
    stats = collector(conn).collect()

    assert stats.postings_seen == 6
    assert stats.jobs_new == 6
    assert stats.descriptions_non_empty == 6
    assert stats.descriptions_empty == 0

    row = conn.execute("SELECT COUNT(*) AS n FROM job WHERE provider = 'jobicy'").fetchone()
    assert row["n"] == 6


def test_the_hiring_scope_is_stored_and_anywhere_is_not(conn) -> None:
    """The point of the source, and the line that protects it.

    Five of the six fixture rows state where the employer may hire. The sixth
    says `Anywhere`, which is the vendor's default, and it is counted in the
    other bucket and stored as NULL.
    """
    stats = collector(conn).collect()

    assert stats.with_hiring_scope == 5
    assert stats.without_hiring_scope == 1

    anywhere = conn.execute(
        "SELECT location_raw FROM job WHERE provider = 'jobicy' AND external_id = '900003'"
    ).fetchone()
    assert anywhere["location_raw"] is None

    brazil = conn.execute(
        "SELECT location_raw FROM job WHERE provider = 'jobicy' AND external_id = '900001'"
    ).fetchone()
    assert brazil["location_raw"] == "Brazil"


def test_every_employer_gets_its_own_board(conn) -> None:
    """ADR-0008 through a source whose real shape is one stream. Registering
    the whole window as one board carrying every employer is the attribution
    failure that document exists to prevent."""
    collector(conn).collect()

    boards = conn.execute(
        "SELECT COUNT(*) AS n FROM source_board WHERE provider = 'jobicy'"
    ).fetchone()
    assert boards["n"] == 6


def test_the_question_that_produced_the_rows_is_stored_beside_them(conn) -> None:
    """A count with no question attached is how a bounded read gets quoted as
    market data."""
    collector(conn).collect()

    row = conn.execute(
        "SELECT stats_json FROM pipeline_run WHERE stage = ? ORDER BY started_at DESC LIMIT 1",
        (RUN_NAME,),
    ).fetchone()
    stats = json.loads(row["stats_json"])

    assert stats["applied_filters"] == {"count": 200, "geo": "latam"}
    assert "never a census" in stats["coverage_note"]
    assert "Jobicy" in stats["attribution"]


def test_an_empty_window_stores_nothing_and_closes_nothing(conn) -> None:
    """An empty valid response is an empty window. It is not evidence that
    anything ended, and this collector has no code path that could close a
    posting on it."""
    collector(conn).collect()
    before = conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"]

    stats = collector(conn, EDGE["empty_window"]).collect(force=True)

    assert stats.postings_seen == 0
    assert stats.failures == []
    assert conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"] == before


def test_a_contract_error_is_recorded_as_a_failure_not_an_empty_window(conn) -> None:
    stats = collector(conn, EDGE["contract_error"]).collect()

    assert stats.failures
    assert stats.postings_seen == 0

    row = conn.execute(
        "SELECT status FROM pipeline_run WHERE stage = ? ORDER BY started_at DESC LIMIT 1",
        (RUN_NAME,),
    ).fetchone()
    assert row["status"] == "FAILED"


def test_running_it_twice_files_the_second_read_as_the_same_jobs(conn) -> None:
    collector(conn).collect()
    stats = collector(conn).collect(force=True)

    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == 6
    assert conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"] == 6


def test_a_row_it_cannot_address_is_counted_rather_than_invented(conn) -> None:
    stats = collector(conn, EDGE["unsafe_rows"]).collect()

    assert stats.postings_unaddressable == 4
    assert stats.jobs_new == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"] == 0

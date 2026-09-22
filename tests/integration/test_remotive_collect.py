"""Collecting Remotive: the six-hour gate, the window, and what it stores.

Mock transport, temporary database. Nothing opens a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.remotive_collect import (
    MIN_SECONDS_BETWEEN_POLLS,
    RUN_NAME,
    RemotiveCollector,
    describe_cooldown,
)
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "remotive"
WINDOW = json.loads((FIXTURES / "window.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "remotive.db")
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: Any, body: dict[str, Any] | None = None, seen: list[httpx.Request] | None = None
) -> RemotiveCollector:
    payload = WINDOW if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return RemotiveCollector(conn, fetcher)


def test_a_second_run_inside_six_hours_is_refused_without_a_request(conn) -> None:
    seen: list[httpx.Request] = []
    collector(conn, seen=seen).collect()
    assert len(seen) == 1
    stats = collector(conn, seen=seen).collect()
    assert stats.cooling_down_until is not None
    assert len(seen) == 1
    assert stats.failures == [], "a refusal is not a failure"
    runs = conn.execute(
        "SELECT COUNT(*) AS n FROM pipeline_run WHERE stage = ?", (RUN_NAME,)
    ).fetchone()
    assert runs["n"] == 1, "a refusal writes no run row, so it cannot push its own deadline out"


def test_six_hours_later_it_is_allowed_again(conn) -> None:
    first = collector(conn)
    first.collect()
    last = first.last_attempt_at()
    assert last is not None
    assert first.cooling_down(now=last + MIN_SECONDS_BETWEEN_POLLS + 1) is None
    assert first.cooling_down(now=last + 60) is not None


def test_the_refusal_says_how_long_is_left() -> None:
    text = describe_cooldown(deadline=10_000.0, now=10_000.0 - 3_600.0)
    assert "60 minute" in text
    assert "Nothing was fetched" in text


def test_it_stores_the_postings_with_bodies_and_scopes(conn) -> None:
    stats = collector(conn).collect()
    assert stats.postings_seen == 4
    assert stats.jobs_new == 4
    assert stats.descriptions_non_empty == 4
    assert stats.with_hiring_scope == 4
    assert stats.claimed_total == 4
    rows = conn.execute(
        "SELECT location_raw, url FROM job WHERE provider = 'remotive' ORDER BY external_id"
    ).fetchall()
    assert {r["location_raw"] for r in rows} == {
        "Worldwide",
        "LATAM, Europe, USA, Canada, APAC",
        "Europe",
        "USA",
    }
    assert all(r["url"].startswith("https://remotive.com/") for r in rows)


def test_the_scope_reaches_the_gate_through_facts(conn) -> None:
    """`publishes_hiring_scope` is what makes `location_raw` a declared scope."""
    from career_agent.pipeline.facts import job_facts

    collector(conn).collect()
    row = conn.execute(
        "SELECT id, title, location_raw, posted_at, provider FROM job WHERE location_raw = 'Europe'"
    ).fetchone()
    facts = job_facts(
        job_id=row["id"],
        title=row["title"],
        description="x",
        location_raw=row["location_raw"],
        posted_at=row["posted_at"],
        provider=row["provider"],
        payload=None,
    )
    assert facts.declared_hiring_scope == "Europe"


def test_every_employer_gets_its_own_board(conn) -> None:
    stats = collector(conn).collect()
    assert stats.companies_new == 4
    boards = conn.execute(
        "SELECT COUNT(*) AS n FROM source_board WHERE provider = 'remotive'"
    ).fetchone()
    assert boards["n"] == 4


def test_running_it_twice_files_the_second_read_as_the_same_jobs(conn) -> None:
    collector(conn).collect()
    second = collector(conn)
    stats = second.collect(force=True)
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == 4


def test_an_empty_window_stores_nothing_and_closes_nothing(conn) -> None:
    collector(conn).collect()
    empty = dict(WINDOW)
    empty["jobs"] = []
    empty["job-count"] = 0
    stats = collector(conn, body=empty).collect(force=True)
    assert stats.postings_seen == 0 and stats.failures == []
    open_rows = conn.execute("SELECT COUNT(*) AS n FROM job WHERE closed_at IS NULL").fetchone()
    assert open_rows["n"] == 4


def test_a_contract_error_is_a_failure_not_an_empty_window(conn) -> None:
    stats = collector(conn, body={"jobs": "nope"}).collect()
    assert stats.failures
    run = conn.execute("SELECT status FROM pipeline_run WHERE stage = ?", (RUN_NAME,)).fetchone()
    assert run["status"] == "FAILED"

"""Collecting Jobgether: slices, the three ways a slice ends, and a lead that
is scored on its scope alone. Mock transport, temporary database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.jobgether_collect import (
    RUN_NAME,
    SLICE_BUDGET,
    SLICE_CAUGHT_UP,
    SLICE_EXHAUSTED,
    JobgetherCollector,
)
from career_agent.providers.jobgether import Slice
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobgether"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "jobgether.db")
    migrate(connection)
    yield connection
    connection.close()


def _pages(rows: list[dict[str, Any]], per_page: int = 2) -> list[dict[str, Any]]:
    chunks = [rows[i : i + per_page] for i in range(0, len(rows), per_page)]
    return [
        {"jobs": chunk, "pagination": {"page": n, "limit": per_page, "hasMore": n < len(chunks)}}
        for n, chunk in enumerate(chunks, start=1)
    ]


def collector(conn: Any, seen: list[httpx.Request] | None = None) -> JobgetherCollector:
    pages = _pages(PAGE["jobs"])

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=pages[min(page, len(pages)) - 1])

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return JobgetherCollector(conn, fetcher)


def test_one_slice_walks_to_the_vendors_end_and_stores_leads(conn) -> None:
    seen: list[httpx.Request] = []
    stats = collector(conn, seen=seen).collect([Slice("anywhere")])
    assert stats.requests == 3
    assert stats.jobs_new == 5
    assert stats.with_hiring_scope == 5
    assert stats.slices[0].ended == SLICE_EXHAUSTED
    rows = conn.execute(
        "SELECT content_hash, collection_status, location_raw FROM job WHERE provider = 'jobgether'"
    ).fetchall()
    assert len(rows) == 5
    from career_agent.storage.repositories import sha256_text

    assert all(r["content_hash"] == sha256_text("") for r in rows), "stored against the empty body"
    assert all(r["collection_status"] == "FETCHED" for r in rows)
    assert {r["location_raw"] for r in rows} >= {"Anywhere", "Brazil", "Latin America"}


def test_a_second_run_catches_up_instead_of_rewalking(conn) -> None:
    collector(conn).collect([Slice("anywhere")])
    seen: list[httpx.Request] = []
    stats = collector(conn, seen=seen).collect([Slice("anywhere")])
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again > 0
    # Page 1 is always read; page 2 is entirely held, so the slice stops there.
    assert stats.slices[0].ended == SLICE_CAUGHT_UP
    assert len(seen) == 2


def test_the_budget_bounds_the_run_and_progress_survives(conn) -> None:
    stats = collector(conn).collect([Slice("anywhere"), Slice("brazil")], max_requests=1)
    assert stats.requests == 1
    assert stats.budget_exhausted is True
    assert stats.slices_walked == 1 and stats.slices[0].ended == SLICE_BUDGET
    run = conn.execute(
        "SELECT status, stats_json FROM pipeline_run WHERE stage = ?", (RUN_NAME,)
    ).fetchone()
    assert run["status"] == "OK"
    recorded = json.loads(run["stats_json"])
    assert recorded["budget_exhausted"] is True
    assert recorded["slices"][0]["key"] == "anywhere"


def test_a_lead_is_scored_on_its_declared_scope_alone(conn) -> None:
    """The point of storing metadata-only rows: `Anywhere` admits, `Indiana
    (USA), New Jersey (USA)` refuses, and both are visible with a score."""
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.rescore import rescore
    from career_agent.storage.revisions import scoreable_count

    collector(conn).collect([Slice("anywhere")])
    config, _ = load_search_config(committed_config_dir())
    stats = rescore(conn, config)
    assert stats.jobs_scored == 5
    assert stats.jobs_skipped_no_description == 0
    assert scoreable_count(conn) == 5
    statuses = {
        row["location_raw"]: row["eligibility_status"]
        for row in conn.execute(
            "SELECT j.location_raw, m.eligibility_status FROM job_match m"
            " JOIN job j ON j.id = m.job_id"
        )
    }
    assert statuses["Anywhere"] == "VERIFIED_ELIGIBLE"
    assert statuses["Brazil"] == "VERIFIED_ELIGIBLE"
    assert statuses["Latin America"] == "VERIFIED_ELIGIBLE"
    assert statuses["Indiana (USA), New Jersey (USA)"] == "VERIFIED_NOT_ELIGIBLE"
    assert statuses["North America, Europe"] == "VERIFIED_NOT_ELIGIBLE"
    completeness = {
        row[0]
        for row in conn.execute("SELECT DISTINCT content_completeness FROM job_match").fetchall()
    }
    assert completeness == {"METADATA_ONLY"}

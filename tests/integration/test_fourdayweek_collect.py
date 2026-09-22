"""Collecting 4 Day Week: a bounded walk, full bodies, and a scope that reaches
the gate. Mock transport, temporary database."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.fourdayweek_collect import FourDayWeekCollector
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "fourdayweek"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "fourdayweek.db")
    migrate(connection)
    yield connection
    connection.close()


def collector(conn, max_pages: int = 1) -> FourDayWeekCollector:
    last = dict(PAGE, has_more=False)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=last)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return FourDayWeekCollector(conn, fetcher, max_pages=max_pages)


def test_it_stores_bodies_scopes_and_offices(conn) -> None:
    stats = collector(conn).collect()
    assert stats.jobs_new == len(PAGE["data"])
    assert stats.descriptions_non_empty == len(PAGE["data"])
    assert stats.with_hiring_scope == 1 and stats.without_hiring_scope == len(PAGE["data"]) - 1
    assert stats.claimed_total == 23690 and stats.stopped_early is False
    rows = {
        r["url"]: r["location_raw"]
        for r in conn.execute("SELECT url, location_raw FROM job WHERE provider = 'fourdayweek'")
    }
    assert "Luxembourg" in rows.values()


def test_the_scope_decides_eligibility_and_the_office_refuses(conn) -> None:
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.rescore import rescore

    collector(conn).collect()
    config, _ = load_search_config(committed_config_dir())
    stats = rescore(conn, config)
    assert stats.jobs_scored == len(PAGE["data"])
    statuses = {
        row["location_raw"]: row["eligibility_status"]
        for row in conn.execute(
            "SELECT j.location_raw, m.eligibility_status FROM job_match m"
            " JOIN job j ON j.id = m.job_id"
        )
    }
    assert statuses["Luxembourg"] == "VERIFIED_NOT_ELIGIBLE", (
        "a remote role open only to Luxembourg"
    )
    onsite = next(v for k, v in statuses.items() if k and k.startswith("London"))
    assert onsite == "VERIFIED_NOT_ELIGIBLE", "an office in London, for a candidate in Brazil"


def test_each_page_is_persisted_before_the_next_is_read(conn) -> None:
    """Progress after page one must already be in the ledger and in `job`
    when page two is requested: a killed walk keeps what it read."""
    from career_agent.pipeline.fourdayweek_collect import FourDayWeekCollector

    counts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        counts.append(conn.execute("SELECT COUNT(*) FROM job").fetchone()[0])
        return httpx.Response(200, json=dict(PAGE, has_more=page < 2))

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = FourDayWeekCollector(conn, fetcher, max_pages=5).collect()
    assert stats.pages_read == 2 and stats.stopped_early is False
    assert counts[0] == 0 and counts[1] == len(PAGE["data"]), counts

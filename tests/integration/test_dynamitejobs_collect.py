"""Dynamite Jobs: sitemaps, one JSON-LD page per posting, a country allowlist
that the gate reads as one. Fixtures captured 2026-09-11; no socket opens."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.dynamitejobs_collect import DynamiteJobsCollector
from career_agent.providers.dynamitejobs import (
    DynamiteJobsProvider,
    hiring_scope,
    is_posting_url,
    job_posting,
    read_compensation,
    to_stub,
)
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "dynamitejobs"
INDEX = (FIXTURES / "index.xml").read_text(encoding="utf-8")
CATEGORY = (FIXTURES / "category.xml").read_text(encoding="utf-8")
PAGE = (FIXTURES / "posting.html").read_text(encoding="utf-8")
URL = "https://dynamitejobs.com/company/bellblytravel/remote-job/assistant-travel-coordinator"


def _handler(seen: list[str] | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        path = request.url.path
        if path.endswith("jobs-categories-index.xml"):
            return httpx.Response(200, text=INDEX)
        if path.endswith("jobs-of-category.xml"):
            return httpx.Response(200, text=CATEGORY)
        if "/remote-job/" in path:
            return httpx.Response(200, text=PAGE)
        return httpx.Response(404)

    return handler


def _fetcher(seen: list[str] | None = None) -> HttpFetcher:
    return HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(_handler(seen))))


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "dj.db")
    migrate(connection)
    yield connection
    connection.close()


def test_the_sitemaps_yield_only_posting_urls_on_the_trusted_host() -> None:
    with _fetcher() as fetcher:
        read = DynamiteJobsProvider(fetcher).read_sitemaps()
    assert read.urls == (URL,)
    assert read.sitemaps == 1 and read.stopped_early is False
    assert is_posting_url("https://evil.example/company/x/remote-job/y") is False
    assert is_posting_url("https://dynamitejobs.com/category/remote-admin-va-jobs") is False


def test_the_posting_page_carries_the_advert_the_scope_and_the_pay() -> None:
    posting = job_posting(PAGE)
    assert posting is not None
    stub = to_stub(URL, posting)
    assert stub is not None
    assert stub.external_id == "bellblytravel/assistant-travel-coordinator"
    assert "Brazil" in (stub.location_raw or "")
    assert hiring_scope(posting) == stub.location_raw
    assert stub.posted_at == "2026-09-03T18:27:34Z"
    pay = read_compensation(stub.payload)
    assert pay is not None
    assert (pay.currency, pay.period, pay.min_value, pay.max_value) == ("USD", "MONTH", 1200, 1400)


def test_a_run_is_bounded_and_a_second_run_costs_nothing_for_what_it_holds(conn) -> None:
    seen: list[str] = []
    with _fetcher(seen) as fetcher:
        stats = DynamiteJobsCollector(conn, fetcher, max_postings=5).collect()
    assert stats.postings_listed == 1 and stats.jobs_new == 1
    assert stats.with_location == 1 and stats.with_salary == 1
    assert sum(1 for u in seen if "/remote-job/" in u) == 1
    seen.clear()
    with _fetcher(seen) as fetcher:
        again = DynamiteJobsCollector(conn, fetcher, max_postings=5).collect()
    assert again.postings_already_held == 1
    assert not any("/remote-job/" in u for u in seen), "a held posting is not requested again"


def test_the_allowlist_decides_eligibility(conn) -> None:
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.rescore import rescore

    with _fetcher() as fetcher:
        DynamiteJobsCollector(conn, fetcher).collect()
    config, _ = load_search_config(committed_config_dir())
    rescore(conn, config)
    row = conn.execute("SELECT eligibility_status, content_completeness FROM job_match").fetchone()
    # The fixture's list names Brazil: eligible. Remove it and the same list
    # refuses, because it is exhaustive.
    assert row["eligibility_status"] == "VERIFIED_ELIGIBLE"
    assert row["content_completeness"] == "FULL_CONTENT"
    from career_agent.match.engine import JobFacts, match_job

    without = match_job(
        config,
        JobFacts(
            title="Assistant",
            description="Remote across Latin America.",
            declared_hiring_scope="Argentina, Aruba, Bahamas, Barbados",
            provider="dynamitejobs",
        ),
        computed_at="x",
    )
    assert without.eligibility_status.value == "VERIFIED_NOT_ELIGIBLE"

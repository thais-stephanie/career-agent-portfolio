"""Collecting Get on Board: identity, idempotence, and what a bound is not.

Everything runs against the contract-built fixture in
`tests/fixtures/providers/getonbrd/` through `httpx.MockTransport`. Nothing
opens a socket, and nothing has ever opened one to this vendor: its robots.txt
names `ClaudeBot`, and the agent that wrote this is Claude.

The tests that matter most are the negative ones. A collector's expensive
mistakes are all quiet: a second pass duplicating a first, a posting filed as a
sighting of itself, a page budget reported as a finished feed.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.getonbrd_collect import (
    PROVIDER,
    GetonbrdCollector,
    known_categories,
)
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "getonbrd"
PAGE1 = json.loads((FIXTURES / "category-programming-page1.json").read_text(encoding="utf-8"))
EMPTY = json.loads((FIXTURES / "empty-category.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="getonbrd-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn,
    body: object = PAGE1,
    *,
    status: int = 200,
    seen: list | None = None,
    max_pages: int | None = None,
):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=body)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _s: None,
    )
    return GetonbrdCollector(conn, fetcher, max_pages=max_pages)


ONE = ("programming",)


# -- a first pass ----------------------------------------------------------


def test_a_first_pass_persists_what_it_can_address(conn) -> None:
    stats = collector(conn).collect(categories=ONE)

    # Three rows in the fixture; the third names no company, so it is counted
    # rather than stored with an invented employer.
    assert stats.postings_seen == 3
    assert stats.jobs_new == 2
    assert stats.postings_without_company == 1
    assert stats.companies_new == 2


def test_every_stored_posting_carries_its_payload_verbatim(conn) -> None:
    """Provenance: later stages must be able to ask what the provider returned."""
    collector(conn).collect(categories=ONE)
    rows = conn.execute(
        "SELECT payload_json FROM job_provider_payload WHERE provider = ?", (PROVIDER,)
    ).fetchall()
    assert len(rows) == 2
    stored = [json.loads(str(r["payload_json"])) for r in rows]
    assert {s["id"] for s in stored} == {"40219", "40220"}


def test_a_stored_posting_carries_the_body_it_arrived_with(conn) -> None:
    """The composed description reaches `job_raw`, and its hash reaches `job`.

    This test used to assert the opposite, and it was right when it was
    written: the adapter declared no description existed. The owner's first
    live retrieval on 2026-09-07 showed the feed carries the employer's
    posting split across five fields, so the row now holds text.
    """
    collector(conn).collect(categories=ONE)
    rows = conn.execute(
        "SELECT j.content_hash AS h, r.description_text AS t FROM job j"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.provider = ? AND j.external_id = ?",
        (PROVIDER, "40219"),
    ).fetchone()
    assert rows["h"] is not None
    assert "Own the revenue reporting pipeline" in rows["t"]
    assert "4+ years in revenue or sales operations" in rows["t"]


def test_the_companys_own_marketing_never_reaches_the_stored_body(conn) -> None:
    """The V1.2 defect cannot re-enter through this source's `projects` field."""
    collector(conn).collect(categories=ONE)
    row = conn.execute(
        "SELECT r.description_text AS t FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.provider = ? AND j.external_id = ?",
        (PROVIDER, "40219"),
    ).fetchone()
    assert "anywhere in the world" not in row["t"]


def test_the_country_list_is_stored_and_remote_is_not_written_into_it(conn) -> None:
    collector(conn).collect(categories=ONE)
    row = conn.execute(
        "SELECT location_raw FROM job WHERE provider = ? AND external_id = ?",
        (PROVIDER, "40219"),
    ).fetchone()
    assert row["location_raw"] == "Chile, Argentina"


# -- idempotence -----------------------------------------------------------


def test_a_second_pass_files_nothing_as_a_sighting_of_itself(conn) -> None:
    """The defect both sibling collectors were built to avoid.

    Without `provider != ?` on the identity lookups, every posting matches the
    row this collector wrote last time: `jobs_seen_again` reads zero, the
    duplicate counters fill up, and `last_seen_at` freezes.
    """
    collector(conn).collect(categories=ONE)
    stats = collector(conn).collect(categories=ONE)

    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == 2
    assert stats.duplicates_total == 0


def test_a_second_pass_creates_no_extra_rows(conn) -> None:
    collector(conn).collect(categories=ONE)
    collector(conn).collect(categories=ONE)
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE provider = ?", (PROVIDER,)
    ).fetchone()["n"]
    assert count == 2


def test_a_posting_listed_under_two_categories_is_stored_once(conn) -> None:
    """One pass, two categories, the same rows. Not an ADR-0013 duplicate.

    This is the same feed handing back the same posting, so it must not be
    filed as a discovery sighting either -- that would be the source citing
    itself.
    """
    stats = collector(conn).collect(categories=("programming", "operations-management"))

    assert stats.jobs_new == 2
    assert stats.duplicates_total == 0
    count = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE provider = ?", (PROVIDER,)
    ).fetchone()["n"]
    assert count == 2


# -- bounds, and what they are not -----------------------------------------


def test_a_short_page_ends_a_category_in_one_request(conn) -> None:
    seen: list[httpx.Request] = []
    stats = collector(conn, seen=seen).collect(categories=ONE)

    assert len(seen) == 1
    assert stats.categories_read == 1
    assert stats.categories_page_limited == 0
    assert stats.complete is True


def test_stopping_at_the_page_budget_is_reported_and_is_not_completeness(conn) -> None:
    """A run that read two pages of many must never report like a whole feed."""
    full = {
        "data": [dict(PAGE1["data"][0], id=str(n)) for n in range(100)],
        "meta": {"total": 9999},
    }
    stats = collector(conn, full, max_pages=2).collect(categories=ONE)

    assert stats.categories_page_limited == 1
    assert stats.categories_truncated == 0
    assert stats.complete is False


def test_a_feed_that_stops_short_of_its_own_total_is_a_named_failure(conn) -> None:
    """Distinct from a page budget: nothing was decided, something broke."""
    pages = [
        {"data": [dict(PAGE1["data"][0], id=str(n)) for n in range(100)], "meta": {"total": 500}},
        {"data": [], "meta": {"total": 500}},
    ]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[min(calls["n"], 1)]
        calls["n"] += 1
        return httpx.Response(200, json=body)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _s: None,
    )
    stats = GetonbrdCollector(conn, fetcher, max_pages=5).collect(categories=ONE)

    assert stats.categories_truncated == 1
    assert stats.categories_page_limited == 0
    assert stats.complete is False
    assert any("stopped serving" in f for f in stats.failures)


# -- failure is not emptiness ----------------------------------------------


def test_an_empty_category_is_a_successful_read_that_closes_nothing(conn) -> None:
    collector(conn).collect(categories=ONE)
    before = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE provider = ?", (PROVIDER,)
    ).fetchone()["n"]

    stats = collector(conn, EMPTY).collect(categories=ONE)

    after = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE provider = ?", (PROVIDER,)
    ).fetchone()["n"]
    assert stats.categories_empty == 1
    assert stats.categories_failed == 0
    assert after == before, "an empty read must never remove a job"


def test_a_failing_category_is_counted_and_named_and_ends_nothing(conn) -> None:
    """A run that quietly read three of five categories looks like a thin market."""
    collector(conn).collect(categories=ONE)

    stats = collector(conn, status=503).collect(categories=ONE)

    assert stats.categories_failed == 1
    assert stats.failures
    assert stats.complete is False
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM job WHERE provider = ?", (PROVIDER,)
    ).fetchone()["n"]
    assert remaining == 2, "a failed request must never close a job"


def test_one_category_failing_does_not_end_the_pass(conn) -> None:
    """Keyed on the URL, not on a call count.

    `HttpFetcher` retries a 503, so a handler that fails only its first CALL
    lets the retry succeed and the category never fails at all. That is the
    fetcher working correctly, and it is why this fixture fails one CATEGORY
    persistently instead.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if "/categories/programming/" in str(request.url):
            return httpx.Response(503, json={})
        return httpx.Response(200, json=PAGE1)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _s: None,
    )
    stats = GetonbrdCollector(conn, fetcher).collect(
        categories=("programming", "operations-management")
    )

    assert stats.categories_failed == 1
    assert stats.categories_read == 1
    assert stats.jobs_new == 2, "the second category still collected"


# -- cross-source identity -------------------------------------------------


def test_the_default_categories_are_the_ones_the_adapter_declares(conn) -> None:
    assert known_categories()
    assert "programming" in known_categories()
    assert len(known_categories()) > 1


def test_the_run_is_recorded_with_its_own_name(conn) -> None:
    """Provider health reads these rows. A pass that logged nothing is invisible."""
    collector(conn).collect(categories=ONE)
    row = conn.execute(
        "SELECT stage, status FROM pipeline_run ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row["stage"] == "collect-getonbrd"


def test_a_failed_pass_is_recorded_as_failed(conn) -> None:
    collector(conn, status=503).collect(categories=ONE)
    row = conn.execute(
        "SELECT stage, status FROM pipeline_run ORDER BY rowid DESC LIMIT 1"
    ).fetchone()
    assert row["status"] == "FAILED"

"""Collecting Himalayas: identity, idempotence, and what a bound is not.

Everything runs against captured fixtures through `httpx.MockTransport`.
Nothing here opens a socket.

The tests that matter most are the negative ones. A collector's expensive
mistakes are all quiet: a second pass duplicating a first, a posting filed as a
sighting of itself, a page budget reported as a finished feed -- and, for this
source specifically, a hiring restriction stored as though it were an office.
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.himalayas_collect import PROVIDER, HimalayasCollector
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "himalayas"
PAGE1 = json.loads((FIXTURES / "feed-page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "feed-page2.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="himalayas-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: sqlite3.Connection, pages: list[dict[str, Any]] | None = None, max_pages: int = 5
) -> HimalayasCollector:
    bodies = pages if pages is not None else [PAGE1, PAGE2]
    order = iter(bodies)

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(200, json=next(order))
        except StopIteration:
            return httpx.Response(200, json={"jobs": [], "nextCursor": None})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return HimalayasCollector(conn, fetcher, max_pages=max_pages)


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM job WHERE provider = ? ORDER BY external_id", (PROVIDER,))
    )


# -- the pass ---------------------------------------------------------------


def test_a_pass_stores_every_addressable_posting(conn) -> None:
    stats = collector(conn).collect()

    assert stats.postings_seen == len(PAGE1["jobs"]) + len(PAGE2["jobs"])
    assert stats.jobs_new == stats.postings_seen
    assert len(_jobs(conn)) == stats.postings_seen
    assert stats.failures == []


def test_every_stored_posting_carries_its_body_and_its_hash(conn) -> None:
    """The reason this source was chosen. A row with no body would be a lead."""
    collector(conn).collect()

    rows = conn.execute(
        "SELECT j.content_hash AS h, j.collection_status AS s, r.description_text AS t"
        " FROM job j JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.provider = ?",
        (PROVIDER,),
    ).fetchall()
    assert len(rows) == len(_jobs(conn))
    assert all(row["h"] and row["s"] == "NORMALISED" and len(row["t"]) > 200 for row in rows)


def test_the_stored_location_is_the_hiring_restriction(conn) -> None:
    """The single most consequential thing this collector writes.

    For every ATS in the corpus this column holds an OFFICE and
    `publishes_hiring_scope` is False. Here it holds the employer's answer to
    where it may hire and the capability is True. The two look identical in
    the database and mean opposite things.
    """
    from career_agent.providers.registry import publishes_hiring_scope

    collector(conn).collect()

    assert publishes_hiring_scope(PROVIDER) is True
    stored = {str(row["location_raw"]) for row in _jobs(conn) if row["location_raw"]}
    declared = {
        ", ".join(job["locationRestrictions"])
        for job in PAGE1["jobs"] + PAGE2["jobs"]
        if job.get("locationRestrictions")
    }
    assert stored == declared


def test_a_posting_carries_its_payload_verbatim(conn) -> None:
    """Provenance: later stages must be able to ask what the provider returned,
    and `pipeline/facts.py` rebuilds the facts from exactly this."""
    collector(conn).collect()

    rows = conn.execute(
        "SELECT payload_json FROM job_provider_payload WHERE provider = ?", (PROVIDER,)
    ).fetchall()
    stored = [json.loads(str(r["payload_json"])) for r in rows]
    assert {s["guid"] for s in stored} == {j["guid"] for j in PAGE1["jobs"] + PAGE2["jobs"]}


def test_the_pay_a_posting_stated_survives_into_a_rescore(conn) -> None:
    """A band with a currency AND a period is what `min_salary` has always
    needed. It reaches the matcher through the provider's own reader."""
    from career_agent.pipeline.facts import compensation_from

    collector(conn).collect()
    payloads = [
        json.loads(str(r["payload_json"]))
        for r in conn.execute(
            "SELECT payload_json FROM job_provider_payload WHERE provider = ?", (PROVIDER,)
        )
    ]
    hints = [compensation_from(PROVIDER, p) for p in payloads]
    priced = [h for h in hints if h is not None]

    assert priced, "no posting in the fixture carried a usable band"
    assert all(h.currency for h in priced)


# -- the bound --------------------------------------------------------------


def test_a_page_budget_is_recorded_and_not_reported_as_the_whole_feed(conn) -> None:
    """A bounded read presented as a complete one is how a window becomes a
    market in somebody's head."""
    stats = collector(conn, max_pages=1).collect()

    assert stats.pages_read == 1
    assert stats.stopped_early is True
    assert stats.claimed_total is not None
    assert stats.claimed_total > stats.postings_seen


def test_reaching_the_end_of_the_feed_is_not_a_page_budget(conn) -> None:
    stats = collector(conn, max_pages=5).collect()

    assert stats.stopped_early is False


def test_a_failed_walk_is_a_failure_and_never_an_empty_feed(conn) -> None:
    """Conflating them is how a network blip closes a company's whole posting
    history."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = HimalayasCollector(conn, fetcher, max_pages=1).collect()

    assert stats.failures
    assert stats.jobs_new == 0
    assert _jobs(conn) == []


# -- identity and idempotence ----------------------------------------------


def test_a_second_pass_files_nothing_as_a_sighting_of_itself(conn) -> None:
    """The defect every sibling collector was built to avoid.

    Without `provider != ?` on the identity lookups, every posting matches the
    row this collector wrote last time: `jobs_seen_again` reads zero, the
    duplicate counters fill up, and `last_seen_at` freezes.
    """
    collector(conn).collect()
    before = len(_jobs(conn))

    stats = collector(conn).collect()

    assert len(_jobs(conn)) == before
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == before
    assert stats.duplicates_total == 0
    assert conn.execute("SELECT COUNT(*) c FROM job_discovery_source").fetchone()["c"] == 0


def test_a_posting_another_provider_already_holds_becomes_a_sighting(conn) -> None:
    """ADR-0013. The employer's own record outranks an aggregator's copy, and
    the way to guarantee that is to have no code path that writes over it."""
    from career_agent.domain.enums import CollectionStatus
    from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, JobRepo, SourceBoardRepo

    held = PAGE1["jobs"][0]
    company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="held", name="Held"))
    board_id = SourceBoardRepo(conn).upsert(
        SourceBoardRecord(
            company_id=company_id,
            provider="greenhouse",
            board_identifier="held",
            board_url=None,
        )
    )
    original = JobRepo(conn).upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id=held["guid"].rstrip("/").rsplit("/", 1)[-1],
            url="https://boards.greenhouse.io/held/jobs/1",
            title="Whatever the employer called it",
            department=None,
            location_raw="San Francisco, CA",
            posted_at=None,
            content_hash=None,
        ),
        status=CollectionStatus.FETCHED,
    )
    conn.commit()

    stats = collector(conn).collect()

    assert stats.duplicates.get("EXTERNAL_ID") == 1
    kept = conn.execute("SELECT * FROM job WHERE id = ?", (original,)).fetchone()
    assert kept["title"] == "Whatever the employer called it"
    assert kept["location_raw"] == "San Francisco, CA"
    assert kept["provider"] == "greenhouse"
    assert conn.execute("SELECT COUNT(*) c FROM job_discovery_source").fetchone()["c"] == 1

"""Collecting Working Nomads: identity, idempotence, and what a window is not.

Everything runs against captured fixtures through `httpx.MockTransport`.
Nothing here opens a socket.

The test that matters most is that the stored `location` never reaches the
eligibility gate. On this board it carries `Global` and `Europe, LATAM, APAC`,
which look exactly like an employer's hiring scope and are not read as one.
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
from career_agent.pipeline.workingnomads_collect import PROVIDER, WorkingNomadsCollector
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "workingnomads"
FEED = json.loads((FIXTURES / "feed.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="wn-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(conn: sqlite3.Connection, body: Any = None) -> WorkingNomadsCollector:
    payload = FEED if body is None else body

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return WorkingNomadsCollector(conn, fetcher)


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        conn.execute("SELECT * FROM job WHERE provider = ? ORDER BY external_id", (PROVIDER,))
    )


def test_a_pass_stores_every_addressable_posting(conn) -> None:
    stats = collector(conn).collect()

    assert stats.postings_seen == len(FEED)
    assert stats.jobs_new == len(FEED)
    assert len(_jobs(conn)) == len(FEED)
    assert stats.failures == []


def test_every_stored_posting_carries_its_body_and_its_hash(conn) -> None:
    collector(conn).collect()

    rows = conn.execute(
        "SELECT j.content_hash AS h, j.collection_status AS s, r.description_text AS t"
        " FROM job j JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.provider = ?",
        (PROVIDER,),
    ).fetchall()
    assert len(rows) == len(FEED)
    assert all(row["h"] and row["s"] == "NORMALISED" and len(row["t"]) > 200 for row in rows)


def test_the_stored_location_never_reaches_the_eligibility_gate(conn) -> None:
    """The whole point of `publishes_hiring_scope=False`.

    The string is stored so it can be SHOWN, and `pipeline/facts.py` refuses
    to pass it as a declared scope because the registry says this board does
    not publish one.
    """
    from career_agent.pipeline.facts import job_facts
    from career_agent.providers.registry import publishes_hiring_scope

    collector(conn).collect()
    assert publishes_hiring_scope(PROVIDER) is False

    row = _jobs(conn)[0]
    assert row["location_raw"]
    facts = job_facts(
        job_id=str(row["id"]),
        title=str(row["title"]),
        description="We build data pipelines.",
        location_raw=str(row["location_raw"]),
        posted_at=None,
        provider=PROVIDER,
        payload=None,
    )
    assert facts.declared_hiring_scope is None


def test_a_posting_carries_its_payload_verbatim(conn) -> None:
    collector(conn).collect()

    rows = conn.execute(
        "SELECT payload_json FROM job_provider_payload WHERE provider = ?", (PROVIDER,)
    ).fetchall()
    stored = [json.loads(str(r["payload_json"])) for r in rows]
    assert {s["url"] for s in stored} == {j["url"] for j in FEED}


def test_a_failed_read_is_a_failure_and_never_an_empty_window(conn) -> None:
    stats = collector(conn, body={"data": []}).collect()

    assert stats.failures
    assert stats.jobs_new == 0
    assert _jobs(conn) == []


def test_a_second_pass_files_nothing_as_a_sighting_of_itself(conn) -> None:
    collector(conn).collect()
    before = len(_jobs(conn))

    stats = collector(conn).collect()

    assert len(_jobs(conn)) == before
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == before
    assert stats.duplicates_total == 0
    assert conn.execute("SELECT COUNT(*) c FROM job_discovery_source").fetchone()["c"] == 0

"""Collecting Remote OK: the licence, the link back, and three rules not four.

Everything runs against the window captured on 2026-09-05 through
`httpx.MockTransport`. Nothing here opens a socket, and that is policy: the
vendor's robots file names `ClaudeBot` with `Disallow: /` and the agent that
maintains these tests is ClaudeBot.
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
from career_agent.pipeline.remoteok_collect import PROVIDER, RemoteOkCollector
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "remoteok"
FEED = json.loads((FIXTURES / "feed.json").read_text(encoding="utf-8"))
POSTINGS = [record for record in FEED if "legal" not in record]


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="remoteok-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(conn: sqlite3.Connection, body: object | None = None) -> RemoteOkCollector:
    served = FEED if body is None else body

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=served)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return RemoteOkCollector(conn, fetcher)


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job ORDER BY external_id").fetchall()


def test_a_pass_stores_every_posting_and_not_the_licence(conn: sqlite3.Connection) -> None:
    stats = collector(conn).collect()
    assert stats.elements_served == len(FEED)
    assert stats.jobs_new == len(POSTINGS)
    assert len(_jobs(conn)) == len(POSTINGS)
    titles = {str(row["title"]) for row in _jobs(conn)}
    assert not any("Terms of Service" in title for title in titles)


def test_the_licence_served_on_the_run_is_recorded(conn: sqlite3.Connection) -> None:
    """The grant is conditional and the vendor restates it in every response.
    A run collected under it should carry what it agreed to, rather than what a
    docstring written months earlier remembers."""
    stats = collector(conn).collect()
    assert stats.licence
    assert "link back" in stats.licence.lower()


def test_apply_opens_the_remote_ok_url_for_every_stored_posting(
    conn: sqlite3.Connection,
) -> None:
    """The link back IS the licence. If this ever stopped being true the access
    would be the thing at stake, so it is asserted over stored rows rather than
    over the adapter alone."""
    collector(conn).collect()
    urls = [str(row["url"]) for row in _jobs(conn)]
    assert urls
    assert all(url.lower().startswith("https://remoteok.com/") for url in urls)


def test_a_second_pass_adds_nothing(conn: sqlite3.Connection) -> None:
    collector(conn).collect()
    before = len(_jobs(conn))
    stats = collector(conn).collect()
    assert len(_jobs(conn)) == before
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == len(POSTINGS)


def test_a_posting_another_provider_holds_becomes_a_sighting(conn: sqlite3.Connection) -> None:
    """Rule 3. Rule 2 is unreachable here -- `apply_url` is byte-identical to
    `url`, so there is no origin to resolve against -- and this is what
    survives of cross-source identity."""
    row = POSTINGS[0]
    stamp = "2026-09-09T00:00:00+00:00"
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at)"
        " VALUES ('c1', 'held', 'Held Elsewhere', ?, ?)",
        (stamp, stamp),
    )
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES ('b1', 'c1', 'greenhouse', 'held')"
    )
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " collection_status, first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES ('j1', 'c1', 'b1', 'greenhouse', 'gh-1', ?, 'The Original Title',"
        " 'NORMALISED', ?, ?, ?, ?)",
        (row["url"], stamp, stamp, stamp, stamp),
    )
    conn.commit()

    stats = collector(conn).collect()

    assert stats.duplicates.get("CANONICAL_URL") == 1
    assert stats.jobs_new == len(POSTINGS) - 1
    kept = conn.execute("SELECT title, provider FROM job WHERE id = 'j1'").fetchone()
    assert kept["title"] == "The Original Title", "a sighting never overwrites the original"
    assert kept["provider"] == "greenhouse"


def test_a_row_with_no_place_stores_no_place(conn: sqlite3.Connection) -> None:
    """Empty is stored as nothing rather than as `Remote`. A remote posting
    with no stated place resolves to no country, which is the honest answer."""
    stats = collector(conn).collect()
    assert stats.without_location > 0, "the premise: most rows say nothing"
    blank = [row for row in _jobs(conn) if row["location_raw"] is None]
    assert blank


def test_a_failed_request_is_a_failure_and_not_an_empty_feed(conn: sqlite3.Connection) -> None:
    """Conflating them is how a network blip closes a company's whole posting
    history."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = RemoteOkCollector(conn, fetcher).collect()
    assert stats.failures
    assert _jobs(conn) == []


def test_a_response_that_is_not_a_list_is_refused(conn: sqlite3.Connection) -> None:
    """This feed is a bare JSON array. A vendor that started wrapping it would
    otherwise read as a feed that had gone empty."""
    stats = collector(conn, body={"jobs": []}).collect()
    assert stats.failures
    assert _jobs(conn) == []


def test_only_the_vendors_own_host_is_requested(conn: sqlite3.Connection) -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        return httpx.Response(200, json=FEED)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    RemoteOkCollector(conn, fetcher).collect()

    assert asked == ["https://remoteok.com/api"], "one request, one window, no paging invented"


def test_the_source_is_recorded_as_remote_ok(conn: sqlite3.Connection) -> None:
    """Crediting Remote OK is the other half of its licence, and the source a
    posting is stored under is where that credit lives."""
    collector(conn).collect()
    providers = {str(row["provider"]) for row in _jobs(conn)}
    assert providers == {PROVIDER}

"""Collecting Programathor, with the vendor's own failures counted rather than fatal.

Everything runs against captured fixtures through `httpx.MockTransport`.
Nothing here opens a socket.

The test that matters most is the one where half the postings answer 500. That
is this board's ordinary case, measured, and a collector that treated it as a
failure would abort a walk over a record the vendor simply cannot render -- or,
worse, report a source going dark as a source with nothing new.
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
from career_agent.pipeline.programathor_collect import PROVIDER, ProgramathorCollector
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "programathor"
LISTING = (FIXTURES / "listing-page1.html").read_text(encoding="utf-8")
POSTINGS = json.loads((FIXTURES / "postings.json").read_text(encoding="utf-8"))["postings"]

#: The captured postings, keyed by the path the listing links them at.
BY_PATH = {json.loads(json.dumps(p))["_url"].split("programathor.com.br")[1]: p for p in POSTINGS}


def _page(posting: dict) -> str:
    """A posting page as the vendor serves it: the block, inside a script tag.

    Serialised WITHOUT escaping the newlines the vendor leaves raw, so the
    fixture exercises the lenient parse rather than a tidied version of it.
    """
    body = json.dumps(posting, ensure_ascii=False)
    return (
        '<html><head><script type="application/ld+json">'
        '{"@type": "BreadcrumbList", "itemListElement": []}'
        "</script>"
        f'<script type="application/ld+json">{body}</script>'
        "</head><body></body></html>"
    )


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="programathor-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: sqlite3.Connection, serve: set[str] | None = None, max_pages: int = 1
) -> ProgramathorCollector:
    """A collector wired to fixtures. `serve` names the paths that answer 200.

    Everything else answers 500, which is what this vendor does to about half
    its own postings.
    """
    servable = BY_PATH.keys() if serve is None else serve

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/jobs":
            return httpx.Response(200, text=LISTING)
        if path in servable and path in BY_PATH:
            return httpx.Response(200, text=_page(BY_PATH[path]))
        return httpx.Response(500, text="Internal Server Error")

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _: None,
    )
    return ProgramathorCollector(conn, fetcher, max_pages=max_pages)


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job ORDER BY external_id").fetchall()


def test_a_pass_stores_the_postings_the_vendor_served(conn: sqlite3.Connection) -> None:
    stats = collector(conn).collect()
    assert stats.jobs_new == len(BY_PATH)
    assert stats.postings_seen == len(BY_PATH)
    assert len(_jobs(conn)) == len(BY_PATH)


def test_a_five_hundred_is_counted_and_never_ends_the_walk(conn: sqlite3.Connection) -> None:
    """The defining behaviour of this source.

    The captured listing lists fifteen postings and five are servable, which is
    close to the measured live ratio. All five must still arrive.
    """
    stats = collector(conn).collect()
    assert stats.postings_listed == 15
    assert stats.postings_unavailable == 15 - len(BY_PATH)
    assert stats.jobs_new == len(BY_PATH)
    assert stats.failures == [], "a posting the vendor will not render is not a run failure"


def test_a_source_gone_completely_dark_is_visible(conn: sqlite3.Connection) -> None:
    """A collector that swallowed these would report a dead source exactly the
    way it reports a quiet one."""
    stats = collector(conn, serve=set()).collect()
    assert stats.postings_listed == 15
    assert stats.postings_unavailable == 15
    assert stats.jobs_new == 0
    assert _jobs(conn) == []


def test_a_failed_listing_is_a_failure(conn: sqlite3.Connection) -> None:
    """The listing is different from a posting: without it there is no walk."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="down")

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _: None,
    )
    stats = ProgramathorCollector(conn, fetcher, max_pages=1).collect()
    assert stats.failures
    assert _jobs(conn) == []


def test_a_second_pass_adds_nothing(conn: sqlite3.Connection) -> None:
    collector(conn).collect()
    before = len(_jobs(conn))
    stats = collector(conn).collect()
    assert len(_jobs(conn)) == before
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == len(BY_PATH)


def test_the_employer_domain_is_stored_as_the_canonical_one(conn: sqlite3.Connection) -> None:
    """`hiringOrganization.sameAs` is the strongest identity signal this board
    carries, and `company.canonical_domain` is a column that already exists."""
    collector(conn).collect()
    domains = [
        row["canonical_domain"]
        for row in conn.execute(
            "SELECT canonical_domain FROM company WHERE discovery_source = ?", (PROVIDER,)
        ).fetchall()
    ]
    assert domains
    assert any(domain and "." in domain for domain in domains)


def test_every_stored_posting_keeps_the_employers_own_advert(conn: sqlite3.Connection) -> None:
    stats = collector(conn).collect()
    assert stats.descriptions_empty == 0
    assert stats.descriptions_non_empty == len(BY_PATH)


def test_a_refused_posting_is_retried_and_that_is_what_it_really_costs(
    conn: sqlite3.Connection,
) -> None:
    """MEASURED HERE, and it corrects this source's cost model by 2.25x.

    `HttpFetcher` retries a 5xx, which is right for a transient failure and
    exactly wrong for this vendor's deterministic ones: a posting it will never
    render is requested three times before the walk moves on. So a listing page
    does not cost 16 requests, it costs 36 -- one listing, one per servable
    posting, and three per refused one.

    The number is asserted rather than described because it is the input to
    every decision about this source's page budget, and because a change in the
    shared retry policy would silently change what a run costs the vendor.
    """
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/jobs":
            return httpx.Response(200, text=LISTING)
        if request.url.path in BY_PATH:
            return httpx.Response(200, text=_page(BY_PATH[request.url.path]))
        return httpx.Response(500, text="Internal Server Error")

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _: None,
    )
    ProgramathorCollector(conn, fetcher, max_pages=1).collect()

    servable = len(BY_PATH)
    refused = 15 - servable
    assert asked.count("/jobs") == 1, "the listing is read once, never once per posting"
    assert len(asked) == 1 + servable + refused * 3

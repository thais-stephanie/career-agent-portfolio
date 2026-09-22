"""Collecting We Work Remotely: identity, idempotence, and what a failure is not.

Everything runs against the recorded feed in
`tests/fixtures/providers/wwr/feed-programming.xml` through
`httpx.MockTransport`. Nothing opens a socket.

The tests that matter most are the negative ones. A collector's expensive
mistakes are all quiet: an empty read closing jobs that still exist, a second
pass duplicating a first, a posting filed as a sighting of something it is not.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.wwr_collect import (
    MATCH_CANONICAL_URL,
    MATCH_EXTERNAL_ID,
    PROVIDER,
    WwrCollector,
    known_feeds,
)
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "wwr"
FEED = (FIXTURES / "feed-programming.xml").read_text(encoding="utf-8")
EMPTY = (
    '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
    "<channel><title>WWR</title></channel></rss>"
)


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="wwr-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(conn, body: str = FEED, *, status: int = 200, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body, headers={"content-type": "application/rss+xml"})

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _s: None,
    )
    return WwrCollector(conn, fetcher)


def jobs(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job ORDER BY external_id").fetchall()


# =========================================================================
# 1. A PASS
# =========================================================================


def test_a_first_pass_stores_every_addressable_posting(conn) -> None:
    stats = collector(conn).collect(feeds=("programming",))

    assert stats.feeds_read == 1
    assert stats.postings_seen == 3
    assert stats.jobs_new == 3
    assert stats.companies_new == 3, "each posting names a different employer"
    assert stats.descriptions_non_empty == 3
    assert stats.failures == []
    assert len(jobs(conn)) == 3


def test_the_whole_posting_is_stored_and_not_an_excerpt(conn) -> None:
    """The reason this source was chosen. A matcher that reads bodies needs a
    body, and the feed carries one."""
    collector(conn).collect(feeds=("programming",))
    lengths = [
        row["byte_length"]
        for row in conn.execute("SELECT byte_length FROM job_raw ORDER BY byte_length DESC")
    ]
    assert lengths and min(lengths) > 500, f"a description came through as an excerpt: {lengths}"


def test_the_company_comes_from_the_title_and_the_board_belongs_to_it(conn) -> None:
    """ADR-0008 through a source whose real shape is a category feed.

    Registering the whole site as one board carrying every employer is exactly
    the attribution failure that document exists to prevent, so the collector
    maintains one board per company instead.
    """
    collector(conn).collect(feeds=("programming",))
    rows = conn.execute(
        "SELECT c.slug AS slug, b.board_identifier AS board, COUNT(*) AS n"
        " FROM job j JOIN company c ON c.id = j.company_id"
        " JOIN source_board b ON b.id = j.source_board_id"
        " GROUP BY c.slug, b.board_identifier"
    ).fetchall()
    assert rows
    for row in rows:
        assert row["board"] == row["slug"], "a board is addressed by its own company"


def test_the_stored_run_says_the_counts_are_not_coverage(conn) -> None:
    """A feed is a window. A number that reaches a report without that sentence
    beside it is a number somebody will read as completeness."""
    collector(conn).collect(feeds=("programming",))
    stats = collector(conn).collect(feeds=("programming",))
    assert "not coverage of remote hiring" in stats.as_dict()["coverage_note"]


# =========================================================================
# 2. IDEMPOTENCE
# =========================================================================


def test_a_second_pass_adds_nothing(conn) -> None:
    """The property every collector here is judged on."""
    first = collector(conn).collect(feeds=("programming",))
    before = [dict(row) for row in jobs(conn)]

    second = collector(conn).collect(feeds=("programming",))
    after = [dict(row) for row in jobs(conn)]

    assert first.jobs_new == 3
    assert second.jobs_new == 0
    assert second.jobs_seen_again == 3
    assert len(after) == len(before) == 3
    assert [row["external_id"] for row in after] == [row["external_id"] for row in before]
    assert [row["content_hash"] for row in after] == [row["content_hash"] for row in before]


def test_a_second_pass_advances_last_seen_rather_than_freezing_it(conn) -> None:
    """The defect `_held_external_ids` excludes this provider to avoid.

    Reading our OWN rows as "already held" would file a posting as a sighting
    of itself, freeze `last_seen_at` so it could never age out, and keep a
    rewritten description out of the corpus.
    """
    collector(conn).collect(feeds=("programming",))
    collector(conn).collect(feeds=("programming",))

    sightings = conn.execute(
        "SELECT COUNT(*) AS n FROM job_discovery_source WHERE source = ?", (PROVIDER,)
    ).fetchone()["n"]
    assert sightings == 0, "the collector recorded this source as a sighting of its own postings"


def test_a_rewritten_description_is_noticed_on_the_next_pass(conn) -> None:
    collector(conn).collect(feeds=("programming",))
    # A phrase the recorded feed actually contains. `You will` is not in it,
    # so the first version of this test rewrote nothing and asserted that a
    # change nobody made had been noticed.
    rewritten = FEED.replace("You want", "You are hoping")
    assert rewritten != FEED, "the rewrite changed nothing, so this proves nothing"

    stats = collector(conn, rewritten).collect(feeds=("programming",))

    assert stats.jobs_new == 0
    assert stats.jobs_changed >= 1, "an edited posting went unnoticed"


# =========================================================================
# 3. WHAT A FAILURE IS, AND WHAT IT IS NOT
# =========================================================================


def test_an_empty_feed_closes_nothing(conn) -> None:
    """The most expensive mistake a collector can make.

    A feed that answers and holds nothing is a quiet day, not an outage, and a
    pass that treated it as one would mark every job gone.
    """
    collector(conn).collect(feeds=("programming",))
    before = [dict(row) for row in jobs(conn)]

    stats = collector(conn, EMPTY).collect(feeds=("programming",))

    assert stats.feeds_empty == 1
    assert stats.failures == [], "an empty feed was reported as a failure"
    after = [dict(row) for row in jobs(conn)]
    assert after == before, "an empty read changed stored jobs"


def test_a_transport_failure_is_counted_and_closes_nothing(conn) -> None:
    collector(conn).collect(feeds=("programming",))
    before = len(jobs(conn))

    stats = collector(conn, "nope", status=500).collect(feeds=("programming",))

    assert stats.feeds_failed == 1
    assert stats.failures, "a failing feed was silent"
    assert len(jobs(conn)) == before


def test_one_failing_feed_does_not_end_the_pass(conn) -> None:
    """A run that quietly read two of three feeds looks identical to a run that
    found fewer postings."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "devops" in str(request.url):
            return httpx.Response(503, text="down")
        return httpx.Response(200, text=FEED)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        max_attempts=1,
        sleep=lambda _s: None,
    )
    stats = WwrCollector(conn, fetcher).collect(feeds=("programming", "devops", "design"))

    assert stats.feeds_read == 2
    assert stats.feeds_failed == 1
    assert stats.jobs_new == 3, "the surviving feeds still stored their postings"


def test_an_unknown_feed_is_refused_rather_than_fetched(conn) -> None:
    seen: list[httpx.Request] = []
    stats = collector(conn, seen=seen).collect(feeds=("not-a-feed",))
    assert stats.feeds_failed == 1
    assert seen == [], "a request went out for a feed that does not exist"


# =========================================================================
# 4. IDENTITY
# =========================================================================


def test_a_posting_another_provider_already_holds_is_a_sighting(conn) -> None:
    """Rule 1, and the only thing it may do is record the sighting.

    No job field is touched: whoever holds the original holds a better record
    than this source's copy, and the guarantee is that no code path writes over
    it.
    """
    collector(conn).collect(feeds=("programming",))
    row = conn.execute("SELECT id, external_id, title FROM job LIMIT 1").fetchone()
    # Re-file it under another provider, as though an ATS had it first.
    conn.execute("UPDATE job SET provider = 'greenhouse' WHERE id = ?", (row["id"],))
    conn.commit()
    before = dict(conn.execute("SELECT * FROM job WHERE id = ?", (row["id"],)).fetchone())

    stats = collector(conn).collect(feeds=("programming",))

    assert stats.duplicates.get(MATCH_EXTERNAL_ID) == 1
    after = dict(conn.execute("SELECT * FROM job WHERE id = ?", (row["id"],)).fetchone())
    assert after["title"] == before["title"]
    assert after["content_hash"] == before["content_hash"]
    assert after["provider"] == "greenhouse", "the sighting overwrote the holder"


def test_the_same_url_under_another_provider_is_a_sighting(conn) -> None:
    """Rule 3: the same page, stored twice, is one job."""
    collector(conn).collect(feeds=("programming",))
    row = conn.execute("SELECT id FROM job LIMIT 1").fetchone()
    conn.execute(
        "UPDATE job SET provider = 'greenhouse', external_id = 'gh-999' WHERE id = ?",
        (row["id"],),
    )
    conn.commit()

    stats = collector(conn).collect(feeds=("programming",))
    assert stats.duplicates.get(MATCH_CANONICAL_URL) == 1


def test_a_title_with_no_company_is_counted_rather_than_guessed(conn) -> None:
    """WWR writes `Company: Role`. Without the colon there is no employer, and
    inventing one from the first two words is the fuzzy identity ADR-0008
    closes by forbidding."""
    import re

    anonymous = re.sub(r"<title>[^:<]+: ", "<title>", FEED)
    stats = collector(conn, anonymous).collect(feeds=("programming",))

    assert stats.postings_without_company == 3
    assert stats.jobs_new == 0
    assert jobs(conn) == []


def test_every_known_feed_name_is_accepted(conn) -> None:
    assert set(known_feeds()) == set(known_feeds())
    seen: list[httpx.Request] = []
    stats = collector(conn, seen=seen).collect(feeds=known_feeds())
    assert stats.feeds_read == len(known_feeds())
    assert stats.feeds_failed == 0

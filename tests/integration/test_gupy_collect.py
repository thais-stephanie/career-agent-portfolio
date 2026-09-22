"""Collecting Gupy: identity, idempotence, and the difference between two walls.

Everything runs against captured fixtures through `httpx.MockTransport`.
Nothing here opens a socket.

The negative tests are the ones worth having. A collector's expensive mistakes
are all quiet: a second pass duplicating a first, a posting filed as a sighting
of itself, a page budget reported as a finished feed -- and, for this source
specifically, the vendor's hard `offset` ceiling reported as though this
product had chosen to stop.
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
from career_agent.pipeline.gupy_collect import (
    PROVIDER,
    GupyCollector,
    board_identifier,
    board_url,
)
from career_agent.providers.gupy import PER_PAGE
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "gupy"
REMOTE = json.loads((FIXTURES / "feed-remote-page1.json").read_text(encoding="utf-8"))
ONSITE = json.loads((FIXTURES / "feed-onsite-page1.json").read_text(encoding="utf-8"))

#: What the `limit=10` probe answers. The real total, unlike the paginated
#: response, whose `total` is the page size for this vendor.
#:
#: SMALL on purpose. The collector plans its slices from this number, and a
#: stub answering a large total for every query is a vendor nobody can
#: partition: the planner would cut to the bottom of every dimension and these
#: tests would exercise 1,134 queries against six recorded postings. Six is
#: what the fixture holds, so the plan is one slice and the tests stay about
#: the collector rather than about the planner, which has its own tests.
COUNT_BODY = {"data": [], "pagination": {"total": 6, "limit": 10, "offset": 0}}


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="gupy-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: sqlite3.Connection,
    pages: dict[str, list[dict[str, Any]]] | None = None,
    max_pages: int = 5,
    workplace_types: tuple[str, ...] = ("remote",),
) -> tuple[GupyCollector, list[str]]:
    """A collector wired to fixtures, and the list of URLs it asked for.

    The URL list is returned because two of the tests below are about which
    requests are made rather than about what came back.
    """
    served = pages if pages is not None else {"remote": [REMOTE]}
    remaining = {scope: list(bodies) for scope, bodies in served.items()}
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        asked.append(url)
        scope = request.url.params.get("workplaceType", "")
        if request.url.params.get("limit") == "10":
            return httpx.Response(200, json=COUNT_BODY)
        queue = remaining.get(scope, [])
        if queue:
            return httpx.Response(200, json=queue.pop(0))
        return httpx.Response(200, json={"data": [], "pagination": {"total": 0}})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return (
        GupyCollector(conn, fetcher, max_pages=max_pages, workplace_types=workplace_types),
        asked,
    )


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job ORDER BY external_id").fetchall()


# -- the ordinary pass -------------------------------------------------------


def test_a_pass_stores_the_recorded_rows(conn: sqlite3.Connection) -> None:
    coll, _ = collector(conn)
    stats = coll.collect()
    assert stats.jobs_new == len(REMOTE["data"])
    assert stats.postings_unaddressable == 0
    assert stats.postings_without_company == 0
    assert stats.descriptions_empty == 0
    assert len(_jobs(conn)) == len(REMOTE["data"])


def test_every_stored_posting_carries_a_place(conn: sqlite3.Connection) -> None:
    """The reason this source exists. A run that stored no locations would
    still look successful in every other counter."""
    coll, _ = collector(conn)
    stats = coll.collect()
    assert stats.without_location == 0
    assert all(row["location_raw"] for row in _jobs(conn))


def test_a_second_pass_adds_nothing(conn: sqlite3.Connection) -> None:
    """Idempotence, and the specific failure it guards: a collector that filed
    its own earlier rows as sightings of themselves would freeze `last_seen_at`
    and write discovery rows migration 0018 forbids."""
    first, _ = collector(conn)
    first.collect()
    before = len(_jobs(conn))

    second, _ = collector(conn)
    stats = second.collect()

    assert len(_jobs(conn)) == before
    assert stats.jobs_new == 0
    assert stats.jobs_seen_again == len(REMOTE["data"])
    assert stats.duplicates_total == 0


def test_no_detail_request_is_made(conn: sqlite3.Connection) -> None:
    """The economics of this source, asserted rather than described.

    One page plus one count probe. If a per-posting fetch ever appeared, a run
    would silently cost a hundred times more requests and no counter would say
    so.
    """
    coll, asked = collector(conn)
    coll.collect()
    pages = [url for url in asked if "limit=100" in url]
    probes = [url for url in asked if "limit=10&" in url]
    assert len(pages) == 1
    assert len(probes) == 1
    assert len(asked) == 2


def test_nothing_outside_the_api_host_is_requested(conn: sqlite3.Connection) -> None:
    coll, asked = collector(conn)
    coll.collect()
    assert asked
    assert all(url.startswith("https://employability-portal.gupy.io/") for url in asked)


# -- identity ----------------------------------------------------------------


def test_a_posting_another_provider_holds_becomes_a_sighting(conn: sqlite3.Connection) -> None:
    """Rule 3. Adding a source must not inflate the corpus with rows that are
    the same posting seen twice."""
    row = REMOTE["data"][0]
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at)"
        " VALUES ('c1', 'held-elsewhere', 'Held Elsewhere', '2026-09-09T00:00:00+00:00',"
        " '2026-09-09T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES ('b1', 'c1', 'greenhouse', 'held-elsewhere')"
    )
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " collection_status, first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES ('j1', 'c1', 'b1', 'greenhouse', 'gh-1', ?, 'Held Elsewhere', 'NORMALISED',"
        " '2026-09-09T00:00:00+00:00', '2026-09-09T00:00:00+00:00',"
        " '2026-09-09T00:00:00+00:00', '2026-09-09T00:00:00+00:00')",
        (row["jobUrl"],),
    )
    conn.commit()

    coll, _ = collector(conn)
    stats = coll.collect()

    assert stats.duplicates.get("CANONICAL_URL") == 1
    assert stats.jobs_new == len(REMOTE["data"]) - 1
    sightings = conn.execute(
        "SELECT COUNT(*) FROM job_discovery_source WHERE source = ?", (PROVIDER,)
    ).fetchone()[0]
    assert sightings == 1


def test_a_sighting_does_not_overwrite_the_original(conn: sqlite3.Connection) -> None:
    """Whoever holds the original holds a better record than a second sighting
    of it, and the way to keep that true is to have no code path that writes
    over it."""
    row = REMOTE["data"][0]
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at)"
        " VALUES ('c1', 'held-elsewhere', 'Held Elsewhere', '2026-09-09T00:00:00+00:00',"
        " '2026-09-09T00:00:00+00:00')"
    )
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES ('b1', 'c1', 'greenhouse', 'held-elsewhere')"
    )
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " collection_status, first_seen_at, last_seen_at, created_at, updated_at)"
        " VALUES ('j1', 'c1', 'b1', 'greenhouse', 'gh-1', ?, 'The Original Title', 'NORMALISED',"
        " '2026-09-09T00:00:00+00:00', '2026-09-09T00:00:00+00:00',"
        " '2026-09-09T00:00:00+00:00', '2026-09-09T00:00:00+00:00')",
        (row["jobUrl"],),
    )
    conn.commit()

    coll, _ = collector(conn)
    coll.collect()

    kept = conn.execute("SELECT title, provider FROM job WHERE id = 'j1'").fetchone()
    assert kept["title"] == "The Original Title"
    assert kept["provider"] == "greenhouse"


def test_a_board_is_the_vendors_employer_id_not_a_slug() -> None:
    """A slug is a normalisation that hopes: it merges two spellings that are
    two companies and separates two spellings that are one. This feed carries
    an identifier, so ADR-0008 is satisfied by one."""
    assert board_identifier({"careerPageId": 210}) == "210"
    assert board_identifier({"careerPageId": "233056"}) == "233056"
    assert board_identifier({"careerPageId": "not-a-number"}) is None
    assert board_identifier({}) is None


def test_a_board_url_is_the_origin_without_the_tracking_token() -> None:
    """`careerPageUrl` arrives with a base64 campaign marker as its path. A
    board is a place; a place with a campaign parameter on it is a link."""
    assert (
        board_url({"careerPageUrl": "https://grupoboticario.gupy.io/eyJzb3VyY2UiOiJn"})
        == "https://grupoboticario.gupy.io"
    )


def test_a_board_url_off_the_vendor_is_refused() -> None:
    assert board_url({"careerPageUrl": "https://evil.example/careers"}) is None
    assert board_url({"careerPageUrl": "http://x.gupy.io"}) is None
    assert board_url({}) is None


def test_the_stored_board_url_is_a_real_employer_page(conn: sqlite3.Connection) -> None:
    coll, _ = collector(conn)
    coll.collect()
    urls = [
        row["board_url"]
        for row in conn.execute(
            "SELECT board_url FROM source_board WHERE provider = ?", (PROVIDER,)
        ).fetchall()
    ]
    assert urls
    assert all(url and url.endswith(".gupy.io") for url in urls)


# -- the two walls, kept apart -----------------------------------------------


def _full_page() -> dict[str, Any]:
    """A page the walk cannot mistake for the end of the feed.

    `walk_feed` stops on a SHORT page, and the captured fixtures hold six rows
    against a page size of a hundred, so every fixture page is the end of a
    feed by definition. Testing a page budget needs a full one, and building it
    by repeating the recorded rows keeps the shape honest while changing only
    the count.
    """
    rows = [dict(row, id=f"{row['id']}-{index}") for index in range(17) for row in REMOTE["data"]]
    return {"data": rows[:PER_PAGE], "pagination": REMOTE["pagination"]}


def test_a_page_budget_is_reported_as_this_products_decision(conn: sqlite3.Connection) -> None:
    """`stopped_early` is this product choosing to stop, and it must not be set
    when the feed simply ended."""
    page = _full_page()
    coll, _ = collector(conn, pages={"remote": [page, page]}, max_pages=1)
    stats = coll.collect()
    assert stats.stopped_early is True
    assert stats.ceiling_hit == []


def test_reaching_the_end_of_a_feed_is_not_stopping_early(conn: sqlite3.Connection) -> None:
    coll, _ = collector(conn, max_pages=5)
    stats = coll.collect()
    assert stats.stopped_early is False


def test_our_own_page_cap_now_reaches_exactly_what_the_vendor_serves() -> None:
    """The cap is DERIVED from a measurement now, and this pins the derivation.

    It was 50 pages, which at 100 rows reached 5,000 -- half of the 10,000 at
    which this vendor starts answering 400. That was a politeness nobody asked
    for, paid in postings a candidate never saw, and reported as
    `stopped_early` so it read as a decision rather than an arbitrary constant.

    The right ceiling for "how far may one walk go" is the most a vendor is
    itself willing to serve. Equal, never greater: a cap above the vendor's own
    limit would turn a wall we know about into a 400 we discover.
    """
    from career_agent.providers.feeds import MAX_PAGES_CAP
    from career_agent.providers.gupy import MAX_OFFSET, PER_PAGE

    assert MAX_PAGES_CAP * PER_PAGE == MAX_OFFSET


def test_the_partition_total_comes_from_the_probe_not_the_page(conn: sqlite3.Connection) -> None:
    """The captured page says `total: 100`; the probe says what it really is.

    The two numbers differ in the fixture exactly as they differ live, so a
    collector that read the page would be visibly wrong here.
    """
    coll, _ = collector(conn)
    stats = coll.collect()
    assert REMOTE["pagination"]["total"] == PER_PAGE, "the premise: the page lies"
    assert stats.partition_totals["remote"] == COUNT_BODY["pagination"]["total"]
    assert stats.partition_read["remote"] == len(REMOTE["data"])


def test_a_plan_is_measured_and_reported(conn: sqlite3.Connection) -> None:
    """A run says how many queries it cut the feed into, and which of them it
    could not cut small enough. A walk that did not finish must not look like
    one that did."""
    coll, _ = collector(conn)
    stats = coll.collect()
    assert stats.slices_planned == 1, "one slice fits under the ceiling here"
    assert stats.slices_over_ceiling == []


def test_two_partitions_are_walked_separately(conn: sqlite3.Connection) -> None:
    coll, _ = collector(
        conn,
        pages={"remote": [REMOTE], "on-site": [ONSITE]},
        workplace_types=("remote", "on-site"),
    )
    stats = coll.collect()
    assert stats.partitions == ["remote", "on-site"]
    assert stats.partition_read["remote"] == len(REMOTE["data"])
    assert stats.partition_read["on-site"] == len(ONSITE["data"])


def test_one_failing_slice_does_not_discard_the_slices_already_persisted(
    conn: sqlite3.Connection,
) -> None:
    """THE REASON THIS COLLECTOR PERSISTS PER SLICE.

    It read every slice into memory and wrote at the end, which was fine for
    the three-hundred-posting feeds it was copied from. A full plan here
    reaches tens of thousands of postings carrying their whole adverts, so
    holding them costs hundreds of megabytes and one failure late in the walk
    threw away everything already fetched.

    A slice that fails is recorded and the run continues. What was collected
    stays collected.
    """
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("limit") == "10":
            return httpx.Response(200, json=COUNT_BODY)
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=REMOTE)
        return httpx.Response(503, text="the vendor gave up on this slice")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = GupyCollector(
        conn, fetcher, max_pages=5, workplace_types=("remote", "hybrid")
    ).collect()

    assert stats.failures, "a slice that failed must be reported"
    assert stats.jobs_new == len(REMOTE["data"]), "the slice that worked is still persisted"
    assert len(_jobs(conn)) == len(REMOTE["data"])


def test_a_posting_in_two_slices_is_stored_once(conn: sqlite3.Connection) -> None:
    """Slices OVERLAP on purpose: a parent is walked beside its children so
    that rows carrying no value for the cut dimension are not lost. That must
    cost requests and never rows."""
    coll, _ = collector(
        conn, pages={"remote": [REMOTE], "hybrid": [REMOTE]}, workplace_types=("remote", "hybrid")
    )
    stats = coll.collect()

    assert stats.slice_overlap == len(REMOTE["data"]), "the same postings arrived twice"
    assert stats.jobs_new == len(REMOTE["data"]), "and were stored once"
    assert len(_jobs(conn)) == len(REMOTE["data"])


def test_a_failed_walk_is_a_failure_and_not_an_empty_feed(conn: sqlite3.Connection) -> None:
    """Conflating them is how a network blip closes a company's whole posting
    history."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is down")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = GupyCollector(conn, fetcher, workplace_types=("remote",)).collect()

    assert stats.failures
    assert stats.jobs_new == 0
    assert _jobs(conn) == []

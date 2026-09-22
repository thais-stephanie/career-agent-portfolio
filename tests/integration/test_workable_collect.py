"""Collecting Workable: identity, idempotence, and a feed that stops advancing.

Everything runs against captured fixtures through `httpx.MockTransport`.
Nothing here opens a socket.

The negative tests are the ones that matter. A collector's expensive mistakes
are all quiet: a second pass duplicating a first, a posting filed as a sighting
of itself, a page budget reported as a finished index -- and, for this source
specifically, a page token that did not take, which looks like twenty-five
pages of successful work and is one page read twenty-five times.
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
from career_agent.pipeline.workable_collect import PROVIDER, WorkableCollector
from career_agent.providers.workable import WorkableProvider
from career_agent.storage.db import connect, migrate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "workable"
PAGE1 = json.loads((FIXTURES / "search-page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "search-page2.json").read_text(encoding="utf-8"))


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="workable-collect")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def collector(
    conn: sqlite3.Connection,
    pages: list[dict[str, Any]] | None = None,
    max_pages: int = 5,
    **kwargs: Any,
) -> WorkableCollector:
    bodies = pages if pages is not None else [PAGE1, PAGE2]
    order = iter(bodies)
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        try:
            return httpx.Response(200, json=next(order))
        except StopIteration:
            return httpx.Response(200, json={"jobs": [], "nextPageToken": None})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    built = WorkableCollector(conn, fetcher, max_pages=max_pages, **kwargs)
    built.provider = WorkableProvider(fetcher, max_pages=max_pages)
    built.seen_urls = seen_urls  # type: ignore[attr-defined]
    return built


def _jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM job ORDER BY external_id"))


# -- the ordinary pass -------------------------------------------------------


def test_a_first_pass_stores_every_posting_with_its_whole_advert(conn) -> None:
    stats = collector(conn).collect()

    assert stats.postings_seen == len(PAGE1["jobs"]) + len(PAGE2["jobs"])
    assert stats.jobs_new == stats.postings_seen
    assert stats.descriptions_empty == 0
    assert stats.postings_unaddressable == 0
    assert stats.postings_without_company == 0
    assert len(_jobs(conn)) == stats.postings_seen


def test_the_location_is_stored_and_it_is_a_place_of_work(conn) -> None:
    """The opposite of Himalayas in the same column, and the reason
    `publishes_hiring_scope` is False. `Houston, Texas, United States` is where
    somebody goes, not where an employer will hire."""
    collector(conn).collect()
    rows = _jobs(conn)
    assert all(row["location_raw"] for row in rows)
    assert any("United States" in (row["location_raw"] or "") for row in rows)


def test_the_employers_own_site_lands_on_the_company_and_not_on_the_job(conn) -> None:
    collector(conn).collect()
    sites = [
        row["website"]
        for row in conn.execute("SELECT website FROM company WHERE website IS NOT NULL")
    ]
    assert sites, "company.website is the closest thing to an origin pointer this feed has"
    urls = [row["url"] for row in _jobs(conn)]
    assert all("jobs.workable.com" in url for url in urls)
    assert not any(url in sites for url in urls)


def test_a_second_pass_adds_nothing_and_files_nothing_as_a_sighting_of_itself(conn) -> None:
    """The correction every sibling collector carries. Applied to this source's
    OWN rows, the external-id rule would file last week's posting as a sighting
    of itself, freeze `last_seen_at` and write a discovery row migration 0018
    forbids."""
    first = collector(conn).collect()
    second = collector(conn).collect()

    assert second.jobs_new == 0
    assert second.jobs_seen_again == first.jobs_new
    assert second.duplicates_total == 0
    assert len(_jobs(conn)) == first.jobs_new
    assert conn.execute("SELECT COUNT(*) FROM job_discovery_source").fetchone()[0] == 0


# -- the bounds --------------------------------------------------------------


def test_a_page_budget_is_recorded_as_a_choice_rather_than_an_ending(conn) -> None:
    stats = collector(conn, max_pages=1).collect()

    assert stats.pages_read == 1
    assert stats.stopped_early is True
    assert stats.repeated_itself is False
    assert stats.postings_seen == len(PAGE1["jobs"])


def test_a_feed_that_repeats_itself_is_a_different_fact_from_a_budget(conn) -> None:
    """THE PAGE-TOKEN TRAP, END TO END.

    The response field is `nextPageToken` and the request parameter is
    `pageToken`. Sending the first name returns HTTP 200 and page one, forever,
    and a walk that trusted the page count would report twenty-five pages of
    work over twenty postings. Nothing failed, so this must not read as a
    failure -- and nothing advanced, so it must not read as a completed walk
    either.
    """
    stats = collector(conn, pages=[PAGE1, PAGE1, PAGE1], max_pages=5).collect()

    assert stats.repeated_itself is True
    assert stats.stopped_early is False
    assert stats.failures == []
    assert stats.jobs_new == len(PAGE1["jobs"])
    assert stats.pages_read == 2


def test_the_index_total_is_recorded_and_is_never_what_was_collected(conn) -> None:
    stats = collector(conn, max_pages=1).collect()
    assert stats.claimed_total == PAGE1["totalSize"]
    assert stats.postings_seen < stats.claimed_total


# -- the narrowings, which must be visible afterwards -------------------------


def test_no_query_and_no_day_range_by_default(conn) -> None:
    """A search API is the easiest place in this system to fill a corpus with
    one person's preferences. The default sends neither."""
    stats = collector(conn, max_pages=1).collect()

    assert stats.query is None
    assert stats.day_range is None
    assert stats.as_dict()["query"] is None
    urls = collector(conn, max_pages=1).seen_urls  # type: ignore[attr-defined]
    del urls  # the URL assertion lives below, on a collector that ran


def test_a_narrowing_that_was_used_is_named_in_the_stored_stats(conn) -> None:
    """A narrowing nobody can see afterwards becomes an unexplained gap in the
    corpus six months later."""
    built = collector(conn, max_pages=1, query="executive assistant", day_range=7)
    stats = built.collect()

    assert stats.as_dict()["query"] == "executive assistant"
    assert stats.as_dict()["day_range"] == 7
    asked = built.seen_urls  # type: ignore[attr-defined]
    assert any("query=executive%20assistant" in url for url in asked)
    assert any("day_range=7" in url for url in asked)


# -- failure is failure ------------------------------------------------------


def test_a_broken_request_is_a_failure_and_never_an_empty_index(conn) -> None:
    """Conflating them is how a network blip closes a whole posting history."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream is unhappy")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = WorkableCollector(conn, fetcher, max_pages=2).collect()

    assert stats.failures
    assert stats.jobs_new == 0
    assert _jobs(conn) == []
    run = conn.execute(
        "SELECT status FROM pipeline_run WHERE stage = 'collect-workable' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "FAILED"


def test_a_row_with_no_employer_is_counted_and_not_invented(conn) -> None:
    """Naming the company from the title is the fuzzy identity ADR-0008
    forbids."""
    page = json.loads(json.dumps(PAGE1))
    page["jobs"] = [dict(page["jobs"][0])]
    page["jobs"][0]["company"] = {"title": "   "}
    page["nextPageToken"] = None

    stats = collector(conn, pages=[page], max_pages=1).collect()

    assert stats.postings_without_company == 1
    assert stats.jobs_new == 0
    assert _jobs(conn) == []


def test_an_unaddressable_row_is_counted_rather_than_dropped_in_silence(conn) -> None:
    """The funnel's `unaccounted` line is computed from exactly this: a posting
    read and not stored has to leave a number behind saying why."""
    page = json.loads(json.dumps(PAGE1))
    page["jobs"] = [dict(page["jobs"][0])]
    page["jobs"][0]["url"] = "https://somewhere-else.example/view/1"
    page["nextPageToken"] = None

    stats = collector(conn, pages=[page], max_pages=1).collect()

    assert stats.postings_unaddressable == 1
    assert stats.jobs_new == 0
    parsed = stats.as_dict()["postings_seen"]
    accounted = (
        stats.jobs_new
        + stats.jobs_seen_again
        + stats.postings_unaddressable
        + stats.postings_without_company
        + stats.duplicates_total
    )
    assert accounted == parsed


def test_the_run_is_recorded_under_its_own_stage_name(conn) -> None:
    collector(conn, max_pages=1).collect()
    stages = [row["stage"] for row in conn.execute("SELECT DISTINCT stage FROM pipeline_run")]
    assert stages == ["collect-workable"]
    assert PROVIDER == "workable"


# -- a failure keeps what came before it -------------------------------------


def test_a_rate_limit_on_a_later_page_keeps_the_earlier_ones(conn) -> None:
    """THE RUN THAT COST 404 SECONDS AND STORED NOTHING.

    The first production collection of this source walked for six and a half
    minutes, met an HTTP 429, and persisted ZERO postings -- because the whole
    walk was held in memory and the failure discarded it. Gupy had learned the
    same lesson at a larger scale earlier the same day.

    The run is still a FAILURE. An empty feed and a broken request are
    different outcomes and conflating them is how a network blip closes a
    company's whole posting history. What changed is that the pages before the
    failure are in the corpus, so the run is partial rather than nothing.
    """
    served = iter([PAGE1])

    def handler(_request: httpx.Request) -> httpx.Response:
        try:
            return httpx.Response(200, json=next(served))
        except StopIteration:
            return httpx.Response(429, text="slow down")

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    built = WorkableCollector(conn, fetcher, max_pages=5)
    built.provider = WorkableProvider(fetcher, max_pages=5)
    stats = built.collect()

    assert stats.failures, "the rate limit is reported"
    assert stats.jobs_new == len(PAGE1["jobs"]), "and page one is in the corpus"
    assert len(_jobs(conn)) == len(PAGE1["jobs"])

    run = conn.execute(
        "SELECT status, stats_json FROM pipeline_run WHERE stage = 'collect-workable'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert run["status"] == "FAILED"
    assert json.loads(run["stats_json"])["jobs_new"] == len(PAGE1["jobs"])


def test_a_page_is_written_before_the_next_one_is_asked_for(conn) -> None:
    """Asserted by counting rows FROM INSIDE the walk, because the property is
    invisible from the outside: a collector that holds everything and one that
    streams look identical on a run that succeeds."""
    rows_when_second_page_requested: list[int] = []
    served = [PAGE1, PAGE2]
    index = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal index
        if index > 0:
            rows_when_second_page_requested.append(
                conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
            )
        body = served[index] if index < len(served) else {"jobs": [], "nextPageToken": None}
        index += 1
        return httpx.Response(200, json=body)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    built = WorkableCollector(conn, fetcher, max_pages=3)
    built.provider = WorkableProvider(fetcher, max_pages=3)
    built.collect()

    assert rows_when_second_page_requested
    assert rows_when_second_page_requested[0] == len(PAGE1["jobs"]), (
        "page one must already be persisted when page two is requested"
    )

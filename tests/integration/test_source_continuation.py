"""Sources that continue across refreshes, instead of rereading page one.

Each keeps its place in its own run stats (`PipelineRunRepo.checkpoint`), so a
new collector over the same database -- a restarted process -- carries on from
it. What these tests hold: the place advances, nothing is skipped or read twice
by the checkpoint logic, a bad checkpoint costs a lap and never a refresh, and
the lap starts again at the end. No network: every response is served here.
"""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.storage.db import connect, migrate, transaction


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "continuation.db")
    migrate(connection)
    yield connection
    connection.close()


def _fetcher(handler) -> HttpFetcher:
    return HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        request_delay_seconds=0,
        sleep=lambda _: None,
    )


# -- Himalayas: a keyset cursor, kept between refreshes ----------------------

HIMALAYAS = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "himalayas"
TEMPLATE = json.loads((HIMALAYAS / "feed-page1.json").read_text(encoding="utf-8"))["jobs"][0]


def _himalayas_feed(pages: int, per_page: int = 2) -> dict[str | None, dict]:
    """`cursor -> body`: page n is served for cursor `c{n}` (page 0 for none)."""
    feed: dict[str | None, dict] = {}
    for n in range(pages):
        jobs = []
        for i in range(per_page):
            job = copy.deepcopy(TEMPLATE)
            ident = f"job-{n}-{i}"
            job["guid"] = f"https://himalayas.app/companies/acme/jobs/{ident}"
            job["applicationLink"] = job["guid"]
            job["title"] = f"Role {ident}"
            jobs.append(job)
        feed[None if n == 0 else f"c{n}"] = {
            "jobs": jobs,
            "totalCount": pages * per_page,
            "nextCursor": f"c{n + 1}" if n + 1 < pages else None,
        }
    return feed


def _himalayas(conn, feed, asked: list[str | None], *, refuse: set[str] = frozenset()):
    from career_agent.pipeline.himalayas_collect import HimalayasCollector

    def handler(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        asked.append(cursor)
        if cursor in refuse:
            return httpx.Response(400, json={"error": "invalid cursor"})
        return httpx.Response(200, json=feed[cursor])

    return HimalayasCollector(conn, _fetcher(handler), max_pages=1, backlog_pages=2)


def test_himalayas_moves_down_the_feed_across_restarts_without_gaps(conn):
    feed = _himalayas_feed(pages=6)
    asked: list[str | None] = []

    first = _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c1", "c2"]
    assert first.resume_cursor == "c3" and first.continues

    asked.clear()
    second = _himalayas(conn, feed, asked).collect()  # a new collector: a restart
    assert asked == [None, "c3", "c4"]
    assert second.resume_cursor == "c5"

    # The end of the feed: the next lap starts again just below the newest.
    asked.clear()
    third = _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c5"]
    assert third.resume_cursor is None and third.continues
    asked.clear()
    _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c1", "c2"]

    # Every posting was read; none twice in a run, none skipped over a lap.
    held = {r[0] for r in conn.execute("SELECT external_id FROM job WHERE provider='himalayas'")}
    assert held == {f"job-{n}-{i}" for n in range(6) for i in range(2)}


def test_himalayas_drops_a_refused_cursor_and_restarts_the_next_lap(conn):
    feed = _himalayas_feed(pages=6)
    _himalayas(conn, feed, []).collect()  # keeps c3

    asked: list[str | None] = []
    stats = _himalayas(conn, feed, asked, refuse={"c3"}).collect()

    # No second walk right after a refusal; the newest pages were still read.
    assert asked == [None, "c3"]
    assert stats.cursor_reset is True and stats.resume_cursor is None
    assert stats.jobs_new + stats.jobs_seen_again > 0, "the refresh itself went on"

    asked.clear()
    _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c1", "c2"]


def test_himalayas_keeps_its_place_through_a_blip_or_a_rate_limit(conn):
    from career_agent.pipeline.himalayas_collect import HimalayasCollector

    feed = _himalayas_feed(pages=6)
    _himalayas(conn, feed, []).collect()  # keeps c3

    for status in (429, 503):

        def handler(request: httpx.Request, status=status) -> httpx.Response:
            cursor = request.url.params.get("cursor")
            return httpx.Response(status) if cursor else httpx.Response(200, json=feed[None])

        HimalayasCollector(conn, _fetcher(handler), max_pages=1, backlog_pages=2).collect()

    asked: list[str | None] = []
    _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c3", "c4"], "a blip or a refusal must not rewind the lap"


def test_himalayas_never_puts_a_malformed_kept_cursor_in_a_url(conn):
    from career_agent.clock import new_id

    with transaction(conn):
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES (?, 'collect-himalayas', '2026-10-01T00:00:00Z',"
            " '2026-10-01T00:01:00Z', 'OK', ?)",
            (new_id(), json.dumps({"resume_cursor": "../../etc?x=1", "continues": True})),
        )
    asked: list[str | None] = []
    stats = _himalayas(conn, _himalayas_feed(pages=6), asked).collect()
    assert stats.cursor_reset is True and asked == [None, "c1", "c2"]


def test_a_failed_head_walk_keeps_the_place(conn):
    feed = _himalayas_feed(pages=6)
    _himalayas(conn, feed, []).collect()  # keeps c3

    from career_agent.pipeline.himalayas_collect import HimalayasCollector

    broken = _fetcher(lambda request: httpx.Response(503, text="down"))
    HimalayasCollector(conn, broken, max_pages=1, backlog_pages=2).collect()

    asked: list[str | None] = []
    _himalayas(conn, feed, asked).collect()
    assert asked == [None, "c3", "c4"]


# -- Speedrun: overlapping page windows under the API's own ceiling ----------


def _speedrun(conn, monkeypatch, windows: list[tuple[int, int]], last_page: int = 200):
    from career_agent.pipeline.speedrun_collect import SpeedrunCollector
    from career_agent.providers.speedrun import FeedPage, FeedWalk

    collector = SpeedrunCollector(conn, _fetcher(lambda r: httpx.Response(500)))

    def walk_feed(scope="portfolio", max_pages=None, on_page=None, should_stop=None, first_page=0):
        windows.append((first_page, max_pages))
        end = min(first_page + (max_pages or 999), last_page + 1)
        pages = tuple(
            FeedPage(
                url=f"page={n}",
                jobs=(),
                page=n,
                page_size=50,
                total=22068,
                total_pages=last_page + 1,
                echoed_source=None,
            )
            for n in range(first_page, end)
        )
        return FeedWalk(
            scope=scope,
            pages=pages,
            stopped_early=end <= last_page,
            first_page=first_page,
        )

    monkeypatch.setattr(collector.provider, "walk_feed", walk_feed)
    return collector


def test_speedrun_windows_advance_with_one_page_of_overlap_and_wrap_at_the_ceiling(
    conn, monkeypatch
):
    windows: list[tuple[int, int]] = []
    first = _speedrun(conn, monkeypatch, windows).collect(check_contract=False)
    assert windows == [(0, 2), (2, 4)]
    assert first.resume_page == 6 and first.continues

    windows.clear()
    second = _speedrun(conn, monkeypatch, windows).collect(check_contract=False)
    assert windows == [(0, 2), (5, 4)], "one page of overlap, no gap"
    assert second.resume_page == 9

    # Near the ceiling the window ends with the feed, and the lap restarts.
    windows.clear()
    third = _speedrun(conn, monkeypatch, windows, last_page=10).collect(check_contract=False)
    assert windows == [(0, 2), (8, 4)]
    assert third.resume_page == 2
    windows.clear()
    _speedrun(conn, monkeypatch, windows).collect(check_contract=False)
    assert windows == [(0, 2), (2, 4)]


def test_speedrun_reports_what_it_holds_against_two_different_totals(conn, monkeypatch):
    stats = _speedrun(conn, monkeypatch, []).collect(check_contract=False)
    # What the feed says it holds, and what its interface will ever serve.
    assert stats.claimed_total == 22068
    assert stats.servable_total == 201 * 50 < stats.claimed_total
    from career_agent.sources.progress import partial_reason

    assert partial_reason(stats.as_dict()) == "PROGRESSIVE"


# -- Programathor: a listing window, and no request for what is held ---------


def _listing(page: int, pages: int) -> str:
    if page > pages:
        return "<html></html>"
    links = "".join(f'<a href="/jobs/{page * 100 + i}-dev">x</a>' for i in range(15))
    return f"<html>{links}</html>"


def _programathor(conn, asked: list[str], pages: int = 8):
    from career_agent.pipeline.programathor_collect import ProgramathorCollector

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        asked.append(path + (f"?{request.url.query.decode()}" if request.url.query else ""))
        if path == "/jobs":
            page = int(request.url.params.get("page", "1"))
            return httpx.Response(200, text=_listing(page, pages))
        return httpx.Response(500, text="Internal Server Error")

    return ProgramathorCollector(conn, _fetcher(handler), max_pages=2, window_pages=3)


def _listing_pages(asked: list[str]) -> list[int]:
    return [
        int(m.group(1)) if (m := re.search(r"page=(\d+)", a)) else 1
        for a in asked
        if a.startswith("/jobs?") or a == "/jobs"
    ]


def _details(asked: list[str]) -> set[str]:
    return {a.split("-")[0].removeprefix("/jobs/") for a in asked if a.startswith("/jobs/")}


def test_programathor_moves_down_the_listing_and_wraps(conn):
    asked: list[str] = []
    first = _programathor(conn, asked).collect()
    assert _listing_pages(asked) == [1, 2, 3, 4, 5]
    assert first.resume_page == 6

    asked.clear()
    second = _programathor(conn, asked).collect()
    assert _listing_pages(asked) == [1, 2, 5, 6, 7]
    assert second.resume_page == 8

    asked.clear()
    third = _programathor(conn, asked).collect()
    assert _listing_pages(asked) == [1, 2, 7, 8, 9]
    assert third.resume_page == 3, "the listing ended: the next lap starts again"


def test_programathor_does_not_ask_again_for_what_it_holds_or_what_was_just_refused(conn):
    from career_agent.providers.programathor import to_stub
    from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, JobRepo, SourceBoardRepo

    # One posting on page 3, which only the continuing window reads.
    stub = to_stub("https://programathor.com.br/jobs/300-dev", {"title": "Dev"})
    assert stub is not None
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="programathor", board_identifier="acme")
        )
        JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company,
                source_board_id=board,
                provider="programathor",
                # The id exactly as the adapter writes it, not a hand-made one.
                external_id=stub.external_id,
                url=stub.url,
                title=stub.title,
            )
        )
    asked: list[str] = []
    first = _programathor(conn, asked).collect()
    assert "300" not in _details(asked), "a held posting was requested again"
    assert first.postings_known == 1
    # The newest pages are read in full, held postings included.
    assert "100" in _details(asked)
    refused = set(first.unavailable_ids)
    assert refused and "300" not in refused

    # Refused last time: skipped this time. New pages are still asked for.
    asked.clear()
    second = _programathor(conn, asked).collect()
    assert not (_details(asked) & refused)
    assert second.postings_skipped_unavailable > 0
    assert {"600", "700"} <= _details(asked)


def test_a_held_posting_only_some_other_source_holds_is_not_touched(conn):
    """`touch_seen` is this source's own rows only, and open ones only."""
    from career_agent.storage.repositories import JobRepo

    assert JobRepo(conn).touch_seen("programathor", "programathor-999") is False


# -- Dynamite Jobs: "continues" only when the frontier shrank ----------------


def test_dynamite_says_it_continues_only_when_the_batch_stored_something():
    from career_agent.pipeline.dynamitejobs_collect import DynamiteJobsStats
    from career_agent.sources.progress import partial_reason

    stored = DynamiteJobsStats(stopped_early=True, continues=True, jobs_new=500)
    stuck = DynamiteJobsStats(stopped_early=True, continues=False, jobs_new=0)
    assert partial_reason(stored.as_dict()) == "PROGRESSIVE"
    assert partial_reason(stuck.as_dict()) == "PAGE_LIMIT"


# -- a step stopped mid-run does not leave its source RUNNING for good ------


def test_a_stopped_collector_row_is_closed_and_the_source_is_due_again(tmp_path):
    from career_agent.clock import new_id
    from career_agent.sources.progress import RefreshState, read_progress
    from career_agent.web.source_refresh import _close_abandoned, _ledger_mark

    db = tmp_path / "stopped.db"
    with connect(db) as c:
        migrate(c)
    mark = _ledger_mark(db)
    with connect(db) as c, transaction(c):
        c.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, status, stats_json)"
            " VALUES (?, 'collect-wwr', '2026-10-01T00:00:00Z', 'RUNNING', '{}')",
            (new_id(),),
        )
    _close_abandoned(db, "collect-wwr", mark)
    with connect(db) as c:
        c.row_factory = __import__("sqlite3").Row
        (row,) = read_progress(c, stage_for={"wwr": "collect-wwr"})
    assert row.state is RefreshState.FAILED

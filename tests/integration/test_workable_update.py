"""Workable's feed as an update: conditional, shared, progressive, stoppable.

No network: a transport that answers like Workable did on 2026-10-11 (200 with
Last-Modified and Content-Length, no ETag, no Range support, 304 to an equal
If-Modified-Since). Nothing here touches a real database or the real feed.
"""

from __future__ import annotations

import json

import httpx
import pytest

from career_agent.net.deadline import Deadline
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline import maintenance
from career_agent.pipeline.workable_update import WorkableUpdate
from career_agent.providers.workable_feed import FeedSession
from career_agent.runtime.maintenance_lock import maintenance_lock
from career_agent.sources.maintenance import history
from career_agent.storage.db import connect, migrate

MODIFIED = "Sun, 11 Oct 2026 12:16:03 GMT"
NEWER = "Sun, 11 Oct 2026 13:16:03 GMT"


def feed_xml(count: int, *, start: int = 0, changed: tuple[int, ...] = ()) -> str:
    jobs = []
    for i in range(start, start + count):
        text = f"Role {i} duties{' (now with a new duty)' if i in changed else ''}."
        jobs.append(
            f"<job><title>Retail associate {i}</title><referencenumber>R{i}</referencenumber>"
            f"<company>Employer {i % 3}</company>"
            f"<url><![CDATA[https://apply.workable.com/j/R{i}?utm_source=x&amp;token=a%2Fb+z]]></url>"
            f"<description><![CDATA[<p>{text} Salary USD 40,000 yearly.</p>]]></description>"
            "<country>US</country><remote>false</remote><jobtype>Full-time</jobtype>"
            "<date>Mon, 05 Oct 2026 10:00:00 GMT</date></job>"
        )
    return "<source>" + "".join(jobs) + "</source>"


class Server:
    """What Workable's route does, measured: Last-Modified yes, ETag no."""

    def __init__(self, xml: str, modified: str = MODIFIED) -> None:
        self.xml, self.modified = xml, modified
        self.requests: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(dict(request.headers))
        if request.headers.get("if-modified-since") == self.modified:
            return httpx.Response(304, headers={"last-modified": self.modified})
        body = self.xml.encode("utf-8")
        return httpx.Response(
            200,
            content=body,
            headers={"last-modified": self.modified, "content-length": str(len(body))},
        )

    def fetcher(self, **kwargs) -> HttpFetcher:
        client = httpx.Client(transport=httpx.MockTransport(self.handler))
        return HttpFetcher(client=client, request_delay_seconds=0, **kwargs)

    @property
    def downloads(self) -> int:
        return len(self.requests)


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "scratch.db")
    migrate(db)
    yield db
    db.close()


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "shared" / "workable"


def run(conn, server, folder, **kwargs):
    with server.fetcher() as fetcher:
        return WorkableUpdate(
            conn, fetcher, directory=folder, free_bytes=lambda _p: 10**12, **kwargs
        ).collect()


def jobs(conn):
    return {r[0]: r[1] for r in conn.execute("SELECT external_id, url FROM job")}


def test_first_read_stores_everything_and_keeps_every_apply_url_whole(conn, folder):
    server = Server(feed_xml(5))
    stats = run(conn, server, folder)
    assert stats.jobs_new == 5 and not stats.failures and stats.deferred_reason is None
    assert (
        jobs(conn)["workable-R2"]
        == "https://apply.workable.com/j/R2?utm_source=x&amp;token=a%2Fb+z"
    )
    state = FeedSession(folder).read()
    assert state["processed_last_modified"] == MODIFIED and "pending" not in state
    assert not (folder / "feed.xml").exists() and not (folder / "stage.sqlite").exists()


def test_the_next_read_asks_if_modified_and_a_304_downloads_nothing(conn, folder):
    server = Server(feed_xml(5))
    run(conn, server, folder)
    before = conn.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0]
    again = run(conn, server, folder)
    assert server.requests[0].get("if-modified-since") is None
    assert server.requests[1]["if-modified-since"] == MODIFIED
    assert again.feed_not_modified and again.jobs_new == 0 and again.postings_seen == 0
    assert conn.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0] == before
    # A 304 is a real answer: the run counts as a successful check.
    assert any(s.key == "feed:workable" for s in history(conn))


def test_only_new_and_changed_postings_are_written_and_queued_for_a_score(conn, folder):
    run(conn, Server(feed_xml(6)), folder)
    conn.execute("DELETE FROM job_dirty")
    conn.commit()
    stable = {r[0]: r[1] for r in conn.execute("SELECT external_id, last_seen_at FROM job")}
    newer = Server(feed_xml(7, changed=(2,)), modified=NEWER)
    stats = run(conn, newer, folder)
    assert (stats.jobs_new, stats.jobs_changed, stats.jobs_unchanged) == (1, 1, 5)
    queued = {
        r[0]
        for r in conn.execute("SELECT j.external_id FROM job_dirty d JOIN job j ON j.id = d.job_id")
    }
    assert queued == {"workable-R2", "workable-R6"}
    # What did not change was not touched at all.
    now = {r[0]: r[1] for r in conn.execute("SELECT external_id, last_seen_at FROM job")}
    assert all(now[k] == v for k, v in stable.items() if k != "workable-R2")


def test_a_posting_the_feed_no_longer_lists_is_never_closed(conn, folder):
    run(conn, Server(feed_xml(4)), folder)
    run(conn, Server(feed_xml(2), modified=NEWER), folder)
    assert conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NOT NULL").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 4


def test_a_budget_cut_keeps_its_place_and_is_not_an_update(conn, folder):
    server = Server(feed_xml(450))
    seen = {"n": 0}

    def reason():
        seen["n"] += 1
        return "TIME_BUDGET" if seen["n"] > 230 else None

    cut = run(conn, server, folder, stop_reason=reason)
    assert cut.deferred_reason == "TIME_BUDGET" and 0 < cut.postings_seen < 450
    state = FeedSession(folder).read()
    assert "processed_last_modified" not in state and state["pending"]["staged"]
    assert state["pending"]["cursor"] > 0
    # Not a success for the freshness question, and nothing was closed.
    assert not any(s.key == "feed:workable" for s in history(conn))
    stored = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    assert 0 < stored < 450

    done = run(conn, server, folder)
    assert done.resumed and server.downloads == 1  # it did not download again
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 450
    state = FeedSession(folder).read()
    assert state["processed_last_modified"] == MODIFIED and "pending" not in state
    assert any(s.key == "feed:workable" for s in history(conn))


def test_stop_pressed_during_the_read_is_a_pending_update_too(conn, folder):
    server = Server(feed_xml(50))
    cut = run(conn, server, folder, stop_reason=lambda: "CANCELLED")
    assert cut.deferred_reason == "CANCELLED"
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    assert FeedSession(folder).read().get("processed_last_modified") is None


def test_an_expired_budget_during_the_download_leaves_no_partial_file(conn, folder):
    server = Server(feed_xml(600))  # several 64 KiB chunks
    ticks = iter(range(10**6))
    expiring = Deadline(ends_at=2, clock=lambda: float(next(ticks)))
    with server.fetcher(deadline=expiring) as fetcher:
        stats = WorkableUpdate(
            conn, fetcher, directory=folder, free_bytes=lambda _p: 10**12
        ).collect()
    assert stats.deferred_reason == "TIME_BUDGET"
    assert not (folder / "feed.xml").exists()
    assert FeedSession(folder).pending() is None


def test_a_truncated_download_is_a_failure_and_changes_nothing(conn, folder):
    class Short(Server):
        def handler(self, request):
            self.requests.append(dict(request.headers))
            body = self.xml.encode("utf-8")
            return httpx.Response(
                200, content=body[:-40], headers={"content-length": str(len(body))}
            )

    held = run(conn, Server(feed_xml(3)), folder)
    assert held.jobs_new == 3
    stats = run(conn, Short(feed_xml(5), modified=NEWER), folder)
    assert stats.failures and stats.deferred_reason is None
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 3
    assert FeedSession(folder).read()["processed_last_modified"] == MODIFIED
    assert (
        conn.execute(
            "SELECT status FROM pipeline_run WHERE stage='collect-workable'"
            " ORDER BY started_at DESC"
        ).fetchone()[0]
        == "FAILED"
    )


def test_one_read_for_the_whole_installation(conn, folder, tmp_path):
    server = Server(feed_xml(4))
    FeedSession(folder)  # creates the folder
    with maintenance_lock(folder / "feed.lock"):
        busy = run(conn, server, folder)
    assert busy.deferred_reason == "ALREADY_RUNNING" and server.downloads == 0

    # A second profile, later: the feed was confirmed recently, so it is not
    # downloaded again; its own ledger still records a successful check.
    run(conn, server, folder)
    assert server.downloads == 1
    other = connect(tmp_path / "other.db")
    migrate(other)
    again = run(other, server, folder, fresh_hours=72)
    assert server.downloads == 1 and again.deferred_reason is None
    assert any(s.key == "feed:workable" for s in history(other))
    other.close()


def test_a_forced_read_asks_the_server_even_when_recently_checked(conn, folder):
    server = Server(feed_xml(3))
    run(conn, server, folder)
    run(conn, server, folder, fresh_hours=72, force=True)
    assert server.downloads == 2 and server.requests[1]["if-modified-since"] == MODIFIED


def test_no_room_on_disk_defers_without_a_request(conn, folder):
    server = Server(feed_xml(3))
    with server.fetcher() as fetcher:
        stats = WorkableUpdate(conn, fetcher, directory=folder, free_bytes=lambda _p: 0).collect()
    assert stats.deferred_reason == "NOT_ENOUGH_DISK" and server.downloads == 0


def test_progress_names_each_phase(conn, folder):
    ticks = []
    run(conn, Server(feed_xml(250)), folder, progress=ticks.append)
    phases = [t["phase"] for t in ticks]
    assert phases[0] == "downloading" and "staging" in phases and phases[-1] == "reading"
    last = ticks[-1]
    assert last["unit"] == "jobs" and last["done"] <= last["total"]


def test_the_engine_runs_it_as_one_unit_and_respects_the_profile_pause(conn, folder, monkeypatch):
    server = Server(feed_xml(4))
    monkeypatch.setattr("career_agent.pipeline.workable_update.feed_directory", lambda _c: folder)

    def factory(*, deadline):
        return server.fetcher(deadline=deadline)

    paused = maintenance.execute(
        conn,
        budget_seconds=900,
        provider="workable",
        fetcher_factory=factory,
        profile_blocks={"workable": "PAUSED_BY_PROFILE"},
    )
    assert paused["results"] == [] and server.downloads == 0

    seen = []
    receipt = maintenance.execute(
        conn,
        budget_seconds=900,
        provider="workable",
        fetcher_factory=factory,
        on_detail=seen.append,
    )
    (result,) = receipt["results"]
    assert result["key"] == "feed:workable" and result["outcome"] == "OK"
    assert result["jobs_new"] == 4 and result["jobs_unchanged"] == 0
    assert seen and seen[-1] is None  # the detail is cleared when the step ends
    json.dumps(receipt)  # the receipt is a plain record

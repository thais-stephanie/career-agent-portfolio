"""Stages 0 and 1: presence and shadow decisions, on temporary databases only.

Synthetic feeds and a mock transport: rules, not rates. Nothing here reaches
the network or a real database.
"""

from __future__ import annotations

import hashlib
import random
import shutil
import threading

import httpx
import pytest
from test_workable_update import Server, feed_xml, run

from career_agent.net.deadline import BudgetExpired
from career_agent.pipeline import workable_presence as wp
from career_agent.pipeline.workable_update import WorkableUpdate
from career_agent.providers.workable_feed import FeedSession
from career_agent.storage.db import MIGRATIONS_DIR, applied_versions, connect, migrate

DAY_1 = "Sun, 01 Nov 2026 12:00:00 GMT"
DAY_1B = "Sun, 01 Nov 2026 13:00:00 GMT"
DAY_2 = "Mon, 02 Nov 2026 12:00:00 GMT"
DAY_3 = "Tue, 03 Nov 2026 12:00:00 GMT"
DAY_4 = "Wed, 04 Nov 2026 12:00:00 GMT"
TOO_MANY = "Fri, 06 Nov 2026 12:00:00 GMT"
SHADOW_TABLES = (
    "workable_feed_read",
    "workable_presence",
    "workable_shadow_event",
    "workable_audit_sample",
)


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "scratch.db")
    migrate(db)
    yield db
    db.close()


@pytest.fixture
def folder(tmp_path):
    return tmp_path / "shared" / "workable"


@pytest.fixture
def shadow(monkeypatch):
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_PRESENCE", "on")
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_SHADOW", "on")


@pytest.fixture
def audit(shadow, monkeypatch):
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_AUDIT", "on")


def ids(*numbers):
    return [f"workable-R{i}" for i in numbers]


def seed(conn, folder, count=40):
    """Open rows R0..R(count-1), written with the flags off, as production has."""
    with pytest.MonkeyPatch.context() as patch:
        for name in ("PRESENCE", "SHADOW", "AUDIT"):
            patch.delenv(f"CAREER_AGENT_WORKABLE_{name}", raising=False)
        run(conn, Server(feed_xml(count), modified="Sat, 31 Oct 2026 12:00:00 GMT"), folder)


def state(conn, number):
    return conn.execute(
        "SELECT p.* FROM workable_presence p JOIN job j ON j.id = p.job_id WHERE j.external_id = ?",
        (f"workable-R{number}",),
    ).fetchone()


def events(conn):
    return [
        (r[0].removeprefix("workable-"), r[1], r[2])
        for r in conn.execute(
            "SELECT j.external_id, e.decision, e.reason FROM workable_shadow_event e"
            " JOIN job j ON j.id = e.job_id"
            " JOIN workable_feed_read r ON r.id = e.read_id"
            " ORDER BY r.seq, j.external_id, e.decision"
        )
    ]


def outcomes(conn):
    return [r[0] for r in conn.execute("SELECT outcome FROM workable_feed_read ORDER BY seq")]


def fingerprint(conn):
    """Every table the shadow must leave alone, as one digest per table."""
    tables = (
        "job",
        "job_dirty",
        "compute_revision",
        "job_input_revision",
        "job_match",
        "job_application",
        "job_application_event",
        "review_decision",
        "candidate",
    )
    result = {}
    for table in tables:
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {table}")]
        result[table] = hashlib.sha256(repr(sorted(map(repr, rows))).encode()).hexdigest()
    return result


def private_rows(conn, job_id):
    """One row in each private table a closure could be tempted to touch."""
    stamp = "2026-10-01T00:00:00Z"
    conn.execute(
        "INSERT INTO job_application (id, job_id, status, created_at, updated_at)"
        " VALUES ('a1', ?, 'APPLIED', ?, ?)",
        (job_id, stamp, stamp),
    )
    conn.execute(
        "INSERT INTO job_application_event (id, job_id, from_status, to_status, occurred_at)"
        " VALUES ('e1', ?, 'SHORTLISTED', 'APPLIED', ?)",
        (job_id, stamp),
    )
    conn.execute(
        "INSERT INTO review_decision (job_id, decision, origin, created_at, updated_at)"
        " VALUES (?, 'KEEP', 'APP', ?, ?)",
        (job_id, stamp, stamp),
    )
    required = [
        r[1]
        for r in conn.execute("PRAGMA table_info(job_match)")
        if r[3] and r[4] is None and r[1] != "job_id"
    ]
    conn.execute("PRAGMA foreign_keys = OFF")  # the config it names is not the point here
    conn.execute("PRAGMA ignore_check_constraints = ON")
    conn.execute(
        f"INSERT INTO job_match (id, job_id, {', '.join(required)})"
        f" VALUES ('m1', ?, {', '.join('1' for _ in required)})",
        (job_id,),
    )
    conn.execute("PRAGMA ignore_check_constraints = OFF")
    conn.execute("PRAGMA foreign_keys = ON")
    for table in ("job_application", "job_application_event", "review_decision", "job_match"):
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1


def missing(*numbers, count=40):
    """A feed of the same size without these postings: new ones took their place."""
    new = [f"workable-NEW{i}" for i in numbers]
    return ids(*(i for i in range(count) if i not in numbers)) + new


# -- stage 0: the reopen fix ---------------------------------------------------


def test_reopening_works_with_every_flag_off_and_queues_only_that_row(conn, folder):
    run(conn, Server(feed_xml(3)), folder)
    conn.execute(
        "UPDATE job SET closed_at = '2026-11-01T00:00:00Z', collection_status = 'CLOSED'"
        " WHERE external_id = 'workable-R1'"
    )
    conn.execute("DELETE FROM job_dirty")
    conn.commit()
    stats = run(conn, Server(feed_xml(3), modified=DAY_1), folder)
    row = conn.execute(
        "SELECT closed_at, collection_status FROM job WHERE external_id = 'workable-R1'"
    ).fetchone()
    assert row[0] is None and row[1] != "CLOSED"
    assert stats.jobs_reopened == 1 and stats.jobs_unchanged == 2
    queued = conn.execute(
        "SELECT j.external_id FROM job_dirty d JOIN job j ON j.id = d.job_id"
    ).fetchall()
    assert [r[0] for r in queued] == ["workable-R1"]


# -- flags ---------------------------------------------------------------------


def test_with_the_flags_off_nothing_is_recorded(conn, folder):
    run(conn, Server(feed_xml(4), modified=DAY_1), folder)
    stats = run(conn, Server(feed_xml(2), modified=DAY_3), folder)
    assert stats.presence == {}
    for table in SHADOW_TABLES:
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


@pytest.mark.parametrize("value", ["", "off", "1", "true", "yes"])
def test_only_the_word_on_enables_a_flag(monkeypatch, value):
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_PRESENCE", value)
    assert not wp.presence_enabled()


def test_the_flags_depend_on_each_other(monkeypatch):
    monkeypatch.delenv("CAREER_AGENT_WORKABLE_PRESENCE", raising=False)
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_SHADOW", "on")
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_AUDIT", "on")
    assert not wp.shadow_enabled() and not wp.audit_enabled()


def test_presence_alone_counts_without_any_decision(conn, folder, monkeypatch):
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_PRESENCE", "on")
    seed(conn, folder)
    wp.observe(conn, DAY_1, missing(1))
    wp.observe(conn, DAY_2, missing(1))
    assert state(conn, 1)["misses"] == 2 and state(conn, 1)["last_decision"] is None
    assert conn.execute("SELECT COUNT(*) FROM workable_shadow_event").fetchone()[0] == 0


def test_activation_starts_no_read(conn, folder, shadow):
    """A read still fresh stays fresh: turning the flags on downloads nothing."""
    seed(conn, folder)
    server = Server(feed_xml(40), modified=DAY_1)
    run(conn, server, folder, fresh_hours=6)
    assert server.downloads == 0
    assert conn.execute("SELECT COUNT(*) FROM workable_feed_read").fetchone()[0] == 0


# -- what counts as an observation ---------------------------------------------


def test_a_304_is_not_an_observation(conn, folder, shadow):
    server = Server(feed_xml(4), modified=DAY_1)
    run(conn, server, folder)
    assert outcomes(conn) == ["BASELINE"]
    again = run(conn, server, folder)
    assert again.feed_not_modified and again.presence == {}
    assert outcomes(conn) == ["BASELINE"]


def test_the_same_version_counts_once(conn, folder, shadow):
    seed(conn, folder)
    assert wp.observe(conn, DAY_1, missing(1))["outcome"] == "BASELINE"
    before = state(conn, 1)["misses"]
    assert wp.observe(conn, DAY_1, missing(1))["outcome"] == "IGNORED_ALREADY_RECORDED"
    assert state(conn, 1)["misses"] == before == 1


def test_a_truncated_feed_counts_nothing(conn, folder, shadow):
    server = Server(feed_xml(40), modified=DAY_1)
    run(conn, server, folder)
    cut = Server(feed_xml(40)[:-400], modified=DAY_2)
    stats = run(conn, cut, folder)
    assert stats.failures and stats.presence == {}
    assert outcomes(conn) == ["BASELINE"]


@pytest.mark.parametrize("status", [404, 429, 500, 503])
def test_a_refused_or_failing_feed_counts_nothing(conn, folder, shadow, status):
    run(conn, Server(feed_xml(4), modified=DAY_1), folder)

    def refuse(_request):
        return httpx.Response(status)

    with Server("").fetcher() as fetcher:
        fetcher._client = httpx.Client(transport=httpx.MockTransport(refuse))
        fetcher.max_attempts = 1
        stats = WorkableUpdate(
            conn, fetcher, directory=folder, free_bytes=lambda _p: 10**12
        ).collect()
    assert stats.failures and outcomes(conn) == ["BASELINE"]


def test_a_stopped_read_counts_nothing_and_its_resume_counts_the_whole_feed(conn, folder, shadow):
    feed = feed_xml(450)
    run(conn, Server(feed, modified=DAY_1), folder)
    conn.execute("UPDATE job SET closed_at = NULL")
    calls = {"n": 0}

    def stop_after_a_while():
        calls["n"] += 1
        return "CANCELLED" if calls["n"] > 250 else None

    stats = run(conn, Server(feed, modified=DAY_2), folder, stop_reason=stop_after_a_while)
    assert stats.deferred_reason == "CANCELLED" and stats.presence == {}
    assert outcomes(conn) == ["BASELINE"]
    assert FeedSession(folder).pending()

    resumed = run(conn, Server(feed, modified=DAY_2), folder)
    assert resumed.resumed and resumed.postings_seen < 450
    # Ids come from the staged file, so a resume cannot see a part as the whole.
    assert resumed.presence["outcome"] == "COUNTED"
    assert resumed.presence["feed_unique"] == 450 and resumed.presence["absent_rows"] == 0


def test_a_crash_between_presence_and_finish_repeats_nothing(conn, folder, shadow, monkeypatch):
    seed(conn, folder)
    server = Server(feed_xml(39), modified=DAY_1)

    def crash(_self, _modified):
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(FeedSession, "finish", crash)
        with pytest.raises(KeyboardInterrupt):
            run(conn, server, folder)
    assert outcomes(conn) == ["BASELINE"] and state(conn, 39)["misses"] == 1

    resumed = run(conn, server, folder)
    assert resumed.presence == {"outcome": "IGNORED_ALREADY_RECORDED"}
    assert state(conn, 39)["misses"] == 1 and FeedSession(folder).pending() is None


def test_a_version_without_a_date_counts_nothing(conn, shadow):
    assert wp.observe(conn, None, ids(1))["outcome"] == "IGNORED_NO_VERSION"
    assert wp.observe(conn, "not a date", ids(1))["outcome"] == "IGNORED_NO_VERSION"
    assert outcomes(conn) == []


def test_a_failure_in_the_record_never_costs_the_read(conn, folder, shadow, monkeypatch):
    def broken(*_args):
        raise RuntimeError("boom")

    monkeypatch.setattr(wp, "observe", broken)
    stats = run(conn, Server(feed_xml(3), modified=DAY_1), folder)
    assert stats.jobs_new == 3 and not stats.failures
    assert stats.presence == {"outcome": "ERROR", "error": "RuntimeError"}
    assert FeedSession(folder).processed_last_modified() == DAY_1


def test_a_failed_observation_leaves_nothing_behind_and_can_run_again(conn, folder, shadow):
    seed(conn, folder)

    def explode():
        yield "workable-R0"
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        wp.observe(conn, DAY_1, explode())
    assert outcomes(conn) == []
    assert wp.observe(conn, DAY_1, missing(1))["outcome"] == "BASELINE"


# -- the rules -----------------------------------------------------------------


def test_one_absence_only_holds(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    result = wp.observe(conn, DAY_2, missing(1))
    assert result["outcome"] == "COUNTED" and result["would_close"] == 0
    assert state(conn, 1)["last_decision"] == "WOULD_HOLD"
    assert events(conn) == [("R1", "WOULD_HOLD", "FIRST_MISS")]


def test_two_misses_under_24_hours_only_hold(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    almost = "Tue, 03 Nov 2026 11:59:59 GMT"
    assert wp.observe(conn, almost, missing(1))["would_close"] == 0
    assert state(conn, 1)["misses"] == 2 and state(conn, 1)["last_decision"] == "WOULD_HOLD"


def test_two_misses_over_24_hours_would_close_and_close_nothing(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    assert wp.observe(conn, DAY_3, missing(1))["would_close"] == 1
    assert events(conn)[-1] == ("R1", "WOULD_CLOSE", "MISSED_REPEATEDLY")
    row = conn.execute(
        "SELECT closed_at, collection_status FROM job WHERE external_id = 'workable-R1'"
    ).fetchone()
    assert row[0] is None and row[1] != "CLOSED"


def test_a_due_posting_writes_one_decision_however_many_reads_follow(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    wp.observe(conn, DAY_3, missing(1))
    assert wp.observe(conn, DAY_4, missing(1))["would_close"] == 0
    assert [e[1] for e in events(conn)] == ["WOULD_HOLD", "WOULD_CLOSE"]


def test_a_posting_that_reappears_starts_again(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    result = wp.observe(conn, DAY_3, ids(*range(40)))
    assert result["reappeared"] == 1
    row = state(conn, 1)
    assert row["misses"] == 0 and row["first_miss_at"] is None and row["last_decision"] is None
    assert events(conn)[-1] == ("R1", "WOULD_KEEP", "REAPPEARED")
    # The clock starts over too: one more miss is a first miss, not a due posting.
    assert wp.observe(conn, DAY_4, missing(1))["would_close"] == 0


def test_a_posting_back_after_a_would_close_is_a_would_reopen(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    wp.observe(conn, DAY_3, missing(1))
    wp.observe(conn, DAY_4, ids(*range(40)))
    assert events(conn)[-1] == ("R1", "WOULD_REOPEN", "REAPPEARED")


def test_an_abrupt_drop_is_held_and_counts_no_miss(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    result = wp.observe(conn, DAY_2, ids(*range(20)))
    assert result["outcome"] == "HELD_FEED_SHRANK" and state(conn, 30)["misses"] == 0
    assert events(conn) == []


def test_an_empty_feed_is_held_even_as_the_first_read(conn, folder, shadow):
    seed(conn, folder)
    assert wp.observe(conn, DAY_1, [])["outcome"] == "HELD_FEED_EMPTY"
    assert conn.execute("SELECT COUNT(*) FROM workable_presence").fetchone()[0] == 0
    assert wp.observe(conn, DAY_2, ids(*range(40)))["outcome"] == "BASELINE"


def test_churn_with_a_stable_size_is_held(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    result = wp.observe(conn, DAY_2, missing(*range(10)))
    assert result["outcome"] == "HELD_TOO_MANY_NEW_MISSES" and state(conn, 1)["misses"] == 0


def test_a_held_read_still_resets_what_it_listed(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1))
    shrunk = wp.observe(conn, DAY_3, ids(*range(5)))
    assert shrunk["outcome"] == "HELD_FEED_SHRANK" and state(conn, 1)["misses"] == 0


def test_a_held_read_does_not_become_the_size_reference(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, ids(*range(20)))
    assert wp.observe(conn, DAY_3, ids(*range(21)))["outcome"] == "HELD_FEED_SHRANK"


def test_an_older_version_changes_nothing(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_2, missing(1))
    before = tuple(state(conn, 1))
    assert wp.observe(conn, DAY_1, ids(*range(40)))["outcome"] == "HELD_OLDER_VERSION"
    assert tuple(state(conn, 1)) == before


def test_the_backlog_is_a_baseline_and_does_not_hold_later_reads(conn, folder, shadow):
    """A quarter of the rows missing at the start, as measured on 2026-11-04."""
    seed(conn, folder)
    feed = missing(*range(10))
    assert wp.observe(conn, DAY_1, feed)["outcome"] == "BASELINE"
    assert state(conn, 1)["baseline"] == 1
    assert wp.observe(conn, DAY_2, feed)["outcome"] == "COUNTED"
    decisions = {(e[1], e[2]) for e in events(conn)}
    assert decisions == {("WOULD_HOLD", "BASELINE_ABSENT"), ("WOULD_CLOSE", "BACKLOG")}
    assert conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NOT NULL").fetchone()[0] == 0


def test_a_return_from_the_backlog_stays_out_of_the_flap_rate(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, missing(1))
    wp.observe(conn, DAY_2, ids(*range(40)))
    assert events(conn)[-1] == ("R1", "WOULD_KEEP", "REAPPEARED_FROM_BASELINE")
    report = wp.shadow_report(conn)
    assert report["first_misses"] == 0 and report["flap_rate"] is None


def test_a_departure_after_the_baseline_is_not_the_backlog(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, missing(0))
    wp.observe(conn, DAY_2, missing(0, 1))
    wp.observe(conn, DAY_3, missing(0, 1))
    assert state(conn, 0)["baseline"] == 1 and state(conn, 1)["baseline"] == 0
    assert ("R1", "WOULD_CLOSE", "MISSED_REPEATEDLY") in events(conn)


def test_only_workable_rows_are_read_and_written(conn, folder, shadow):
    seed(conn, folder, 3)
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " first_seen_at, last_seen_at, collection_status, created_at, updated_at)"
        " SELECT 'other', company_id, source_board_id, 'greenhouse', 'gh-1', url || '/o', title,"
        " first_seen_at, last_seen_at, collection_status, created_at, updated_at"
        " FROM job LIMIT 1"
    )
    before = tuple(conn.execute("SELECT * FROM job WHERE id = 'other'").fetchone())
    wp.observe(conn, DAY_1, ids(0))
    wp.observe(conn, DAY_2, ids(0))
    assert tuple(conn.execute("SELECT * FROM job WHERE id = 'other'").fetchone()) == before
    assert not conn.execute("SELECT 1 FROM workable_presence WHERE job_id = 'other'").fetchone()
    assert conn.execute("SELECT open_rows FROM workable_feed_read").fetchone()[0] == 3


# -- non-destruction -----------------------------------------------------------


def test_the_shadow_changes_no_posting_score_queue_or_private_row(conn, folder, audit):
    seed(conn, folder, 100)
    one = conn.execute("SELECT id FROM job WHERE external_id = 'workable-R1'").fetchone()[0]
    private_rows(conn, one)
    before = fingerprint(conn)
    closed = "SELECT COUNT(*) FROM job WHERE closed_at IS NOT NULL OR collection_status = 'CLOSED'"
    wp.observe(conn, DAY_1, ids(*range(100)))
    wp.observe(conn, DAY_2, missing(1, 2, count=100))
    wp.observe(conn, DAY_3, missing(1, 2, count=100))
    wp.run_audit(conn, client=Site().client(), sleep=lambda _s: None)
    wp.observe(conn, DAY_4, ids(*range(100)))
    assert {e[1] for e in events(conn)} == {"WOULD_HOLD", "WOULD_CLOSE", "WOULD_REOPEN"}
    assert conn.execute("SELECT COUNT(*) FROM workable_audit_sample").fetchone()[0] == 2
    assert fingerprint(conn) == before
    assert conn.execute(closed).fetchone()[0] == 0


def test_the_presence_writes_do_not_touch_the_population_revision(conn, folder, shadow):
    seed(conn, folder)
    revision = "SELECT revision, population_revision FROM compute_revision"
    before = conn.execute(revision).fetchone()
    wp.observe(conn, DAY_1, missing(1))
    wp.observe(conn, DAY_2, missing(1))
    assert tuple(conn.execute(revision).fetchone()) == tuple(before)


def test_a_full_update_with_the_shadow_on_writes_what_it_writes_with_it_off(tmp_path, monkeypatch):
    """The same two reads give the same rows and the same queue, flags on or off."""

    def play(name):
        db = connect(tmp_path / f"{name}.db")
        migrate(db)
        where = tmp_path / name
        run(db, Server(feed_xml(5), modified=DAY_1), where)
        db.execute("DELETE FROM job_dirty")
        db.commit()
        run(db, Server(feed_xml(4, changed=(1,)), modified=DAY_2), where)
        rows = sorted(
            tuple(r)
            for r in db.execute(
                "SELECT j.external_id, j.title, j.content_hash, j.closed_at, j.collection_status,"
                " d.job_id IS NOT NULL FROM job j LEFT JOIN job_dirty d ON d.job_id = j.id"
            )
        )
        db.close()
        return rows

    off = play("off")
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_PRESENCE", "on")
    monkeypatch.setenv("CAREER_AGENT_WORKABLE_SHADOW", "on")
    assert play("on") == off


# -- concurrency ---------------------------------------------------------------


def test_two_windows_on_one_version_count_it_once(tmp_path, folder, shadow):
    path = tmp_path / "shared.db"
    first = connect(path)
    migrate(first)
    seed(first, folder)
    results = []
    barrier = threading.Barrier(2)

    def window():
        db = connect(path)
        barrier.wait()
        results.append(wp.observe(db, DAY_1, missing(1))["outcome"])
        db.close()

    threads = [threading.Thread(target=window) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["BASELINE", "IGNORED_ALREADY_RECORDED"]
    assert state(first, 1)["misses"] == 1
    assert first.execute("SELECT COUNT(*) FROM workable_shadow_event").fetchone()[0] == 1
    first.close()


# -- the audit sample ----------------------------------------------------------


class Site:
    """Workable's two answers as measured on 2026-11-04, and the failures."""

    def __init__(self, answers=None, default="gone"):
        self.answers, self.default = answers or {}, default
        self.requests = []

    def handler(self, request):
        self.requests.append(request.url.path)
        parts = request.url.path.strip("/").split("/")
        code = parts[-1]
        kind = self.answers.get(code, self.default)
        if kind in (429, 403, 500):
            return httpx.Response(kind)
        if kind == "timeout":
            raise httpx.ReadTimeout("slow")
        if parts[0] == "j":
            if kind == "gone":
                return httpx.Response(302, headers={"location": "/oops"})
            return httpx.Response(301, headers={"location": f"/acme/j/{code}"})
        if kind == "deleted":
            return httpx.Response(404)
        if kind == "archived":
            return httpx.Response(200, json={"state": "archived"})
        if kind == "bad_json":
            return httpx.Response(200, content=b"<html>")
        return httpx.Response(200, json={"state": "published"})

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def due_postings(conn, folder, count=40, gone=3):
    seed(conn, folder, count)
    wp.observe(conn, DAY_1, ids(*range(count)))
    present = missing(*range(gone), count=count)
    wp.observe(conn, DAY_2, present)
    wp.observe(conn, DAY_3, present)


def answers(conn):
    return {
        r[0].removeprefix("workable-"): (r[1], r[2])
        for r in conn.execute(
            "SELECT j.external_id, a.answer, a.detail FROM workable_audit_sample a"
            " JOIN job j ON j.id = a.job_id"
        )
    }


def test_the_sample_needs_its_own_flag(conn, folder, shadow):
    due_postings(conn, folder)
    assert conn.execute("SELECT COUNT(*) FROM workable_audit_sample").fetchone()[0] == 0
    assert wp.run_audit(conn, client=Site().client(), sleep=lambda _s: None)["asked"] == 0


def test_the_sample_is_capped_and_seeded_by_the_version(conn, folder, audit):
    due_postings(conn, folder, count=500, gone=70)
    picked = {r[0] for r in conn.execute("SELECT job_id FROM workable_audit_sample")}
    assert len(picked) == wp.AUDIT_SAMPLE
    due = sorted(
        r[0]
        for r in conn.execute(
            "SELECT job_id FROM workable_presence WHERE last_decision = 'WOULD_CLOSE'"
        )
    )
    assert picked == set(random.Random(DAY_3).sample(due, wp.AUDIT_SAMPLE))


def test_each_answer_is_recorded_as_measured(conn, folder, audit):
    due_postings(conn, folder, count=60, gone=6)
    site = Site(
        {
            "R0": "gone",
            "R1": "deleted",
            "R2": "archived",
            "R3": "published",
            "R4": "bad_json",
            "R5": "timeout",
        }
    )
    spent = []
    summary = wp.run_audit(conn, client=site.client(), sleep=spent.append)
    assert answers(conn) == {
        "R0": ("GONE", "SHORT_LINK_OOPS"),
        "R1": ("GONE", "ACCOUNT_API_404"),
        "R2": ("GONE", "STATE_OTHER"),
        "R3": ("PUBLISHED", "STATE_PUBLISHED"),
        "R4": ("UNKNOWN", "BAD_JSON"),
        "R5": ("UNKNOWN", "ReadTimeout"),
    }
    assert summary["asked"] == 6 and set(spent) == {wp.AUDIT_SPACING_SECONDS}
    assert len(spent) == summary["requests"] == len(site.requests)


def test_a_published_answer_trips_the_breaker_and_restarts_only_that_posting(conn, folder, audit):
    due_postings(conn, folder)
    wp.run_audit(conn, client=Site({"R1": "published"}).client(), sleep=lambda _s: None)
    report = wp.shadow_report(conn)
    last = report["reads"][-1]
    assert last["sampled_published"] == 1 and last["breaker"] is True
    assert state(conn, 1)["misses"] == 0 and state(conn, 1)["last_decision"] == "WOULD_KEEP"
    assert state(conn, 0)["last_decision"] == "WOULD_CLOSE"
    assert ("R1", "WOULD_KEEP", "EMPLOYER_STILL_LISTS") in events(conn)


def test_all_gone_leaves_the_breaker_down(conn, folder, audit):
    due_postings(conn, folder)
    wp.run_audit(conn, client=Site().client(), sleep=lambda _s: None)
    assert wp.shadow_report(conn)["reads"][-1]["breaker"] is False


@pytest.mark.parametrize("status", [429, 403])
def test_a_refusal_stops_the_sample_at_once(conn, folder, audit, status):
    due_postings(conn, folder, count=60, gone=6)
    site = Site(default=status)
    summary = wp.run_audit(conn, client=site.client(), sleep=lambda _s: None)
    assert len(site.requests) == 1 and summary["stopped"] == f"HTTP_{status}"
    assert wp.shadow_report(conn)["reads"][-1]["breaker"] is True
    pending = "SELECT COUNT(*) FROM workable_audit_sample WHERE answer IS NULL"
    assert conn.execute(pending).fetchone()[0] == 5


def test_an_unknown_answer_is_asked_again_up_to_a_limit(conn, folder, audit):
    due_postings(conn, folder, gone=1)
    site = Site(default=500)
    for _ in range(wp.AUDIT_MAX_ATTEMPTS + 2):
        wp.run_audit(conn, client=site.client(), sleep=lambda _s: None)
    assert len(site.requests) == wp.AUDIT_MAX_ATTEMPTS
    assert answers(conn)["R0"] == ("UNKNOWN", "HTTP_500")


def test_the_budget_ends_the_sample_and_the_rest_stays_pending(conn, folder, audit):
    due_postings(conn, folder, count=60, gone=6)
    site = Site()
    left = {"n": 2}

    def budget():
        left["n"] -= 1
        if left["n"] < 0:
            raise BudgetExpired("TIME_BUDGET")

    summary = wp.run_audit(conn, client=site.client(), sleep=lambda _s: None, check=budget)
    assert summary["stopped"] == "TIME_BUDGET" and summary["asked"] == 2
    again = wp.run_audit(conn, client=site.client(), sleep=lambda _s: None)
    assert again["asked"] == 4 and again["stopped"] is None


def test_a_run_stays_inside_the_request_limit(conn, folder, audit):
    due_postings(conn, folder, count=500, gone=70)
    site = Site(default="published")
    summary = wp.run_audit(conn, client=site.client(), sleep=lambda _s: None)
    assert summary["asked"] == wp.AUDIT_SAMPLE
    assert summary["requests"] == wp.AUDIT_SAMPLE * 2 <= wp.AUDIT_MAX_REQUESTS


def test_a_later_read_samples_postings_not_sampled_before(conn, folder, audit):
    due_postings(conn, folder, count=500, gone=70)
    wp.observe(conn, DAY_4, missing(*range(70), count=500))
    sampled = [r[0] for r in conn.execute("SELECT job_id FROM workable_audit_sample")]
    assert len(sampled) == len(set(sampled)) == 70


def test_the_update_answers_the_sample_after_a_complete_read(conn, folder, audit):
    seed(conn, folder, 40)
    run(conn, Server(feed_xml(39), modified=DAY_1), folder)
    site = Site()
    server = Server(feed_xml(39), modified=DAY_2)
    with server.fetcher() as fetcher:
        stats = WorkableUpdate(
            conn,
            fetcher,
            directory=folder,
            free_bytes=lambda _p: 10**12,
            audit_client=site.client(),
            audit_sleep=lambda _s: None,
        ).collect()
    assert stats.presence["would_close"] == 1 and stats.presence["audit"]["asked"] == 1
    assert answers(conn) == {"R39": ("GONE", "SHORT_LINK_OOPS")}


# -- report --------------------------------------------------------------------


def test_the_report_shows_absences_returns_and_flapping(conn, folder, shadow):
    seed(conn, folder)
    wp.observe(conn, DAY_1, ids(*range(40)))
    wp.observe(conn, DAY_2, missing(1, 2))
    wp.observe(conn, DAY_3, missing(2))
    report = wp.shadow_report(conn)
    assert report["reads_counted"] == 3 and report["first_misses"] == 2
    assert report["returned_after_one_miss"] == 1 and report["flap_rate"] == 0.5
    assert report["absent_now"] == [{"misses": 2, "baseline": 0, "postings": 1}]


# -- the migration and its rollback --------------------------------------------


def test_upgrading_from_60_keeps_every_row_and_rollback_leaves_them_again(tmp_path, folder):
    older = tmp_path / "migrations"
    older.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.stem[:4]) <= 60:
            shutil.copy(path, older)
    db = connect(tmp_path / "upgrade.db")
    migrate(db, older)
    run(db, Server(feed_xml(5), modified=DAY_1), folder)
    before = fingerprint(db)

    assert [m.version for m in migrate(db)] == [61]
    assert fingerprint(db) == before
    assert not migrate(db)  # applying again does nothing

    # An older build opens the migrated database: nothing to do, nothing refused.
    assert not migrate(db, older)

    # Rollback, as in the runbook.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("CAREER_AGENT_WORKABLE_PRESENCE", "on")
        wp.observe(db, DAY_2, ids(*range(5)))
    for table in reversed(SHADOW_TABLES):
        db.execute(f"DROP TABLE {table}")
    db.execute("DELETE FROM schema_migration WHERE version = 61")
    assert fingerprint(db) == before
    assert 61 not in applied_versions(db)
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    # And it can come back.
    assert [m.version for m in migrate(db)] == [61]
    db.close()


def test_deleting_a_posting_takes_its_presence_with_it(conn, folder, shadow):
    seed(conn, folder, 1)
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " first_seen_at, last_seen_at, collection_status, created_at, updated_at)"
        " SELECT 'gone', company_id, source_board_id, provider, 'workable-X', url || '/x', title,"
        " first_seen_at, last_seen_at, collection_status, created_at, updated_at FROM job"
    )
    wp.observe(conn, DAY_1, ids(0))
    assert conn.execute("SELECT misses FROM workable_presence WHERE job_id = 'gone'").fetchone()[0]
    conn.execute("DELETE FROM job_dirty WHERE job_id = 'gone'")
    conn.execute("DELETE FROM job WHERE id = 'gone'")
    assert not conn.execute("SELECT 1 FROM workable_presence WHERE job_id = 'gone'").fetchone()

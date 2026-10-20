"""Update overdue job sites when the app opens: off until chosen, bounded,
never duplicated, and it never starts what only the person starts.

The collectors are not reached: the maintenance engine is replaced by a
recorder, so these tests are about WHEN and WITHIN WHAT it runs.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from tests.integration.test_semantic_api import build

from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import HttpFetcher
from career_agent.runtime.mode import RuntimeMode
from career_agent.storage.db import connect, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo
from career_agent.web import auto_update


def _ago(days: float) -> str:
    return (
        (datetime.now(UTC) - timedelta(days=days))
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


@pytest.fixture
def app(tmp_path):
    app = build(tmp_path, RuntimeMode.PERSONAL)
    conn = connect(app.config.db_path)
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        for ident, age in (("stale", 5), ("fresh", 1), ("never", None)):
            board = SourceBoardRepo(conn).upsert(
                SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier=ident)
            )
            conn.execute(
                "UPDATE source_board SET last_collected_at = ? WHERE id = ?",
                (_ago(age) if age is not None else None, board),
            )
    conn.close()
    return app


def call(app, method, path, body=None):
    return app.handle_api(method, path, {}, body or {})


@pytest.fixture
def recorded(monkeypatch):
    """Replace the engine; record how it was called, and take a moment."""
    calls: list[dict] = []

    def fake(conn, **kwargs):
        calls.append(kwargs)
        if kwargs.get("provider") == "workable":
            # Workable is its own step, with its own allowance.
            return {
                "selected": ["feed:workable"],
                "results": [
                    {
                        "key": "feed:workable",
                        "outcome": "DEFERRED",
                        "reason": "TIME_BUDGET",
                        "attempted": True,
                        "jobs_new": 3,
                        "jobs_changed": 1,
                        "jobs_unchanged": 40,
                    }
                ],
                "status": "FINISHED",
            }
        return {
            "selected": ["greenhouse:stale", "greenhouse:never"],
            "results": [
                {"key": "greenhouse:stale", "outcome": "OK", "attempted": True},
                {
                    "key": "greenhouse:never",
                    "outcome": "DEFERRED",
                    "reason": "TIME_BUDGET",
                    "attempted": False,
                },
            ],
            "status": "FINISHED",
        }

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", fake)
    return calls


def test_it_is_off_until_the_person_chooses_and_then_it_says_what_it_will_do(app, recorded):
    status = call(app, "GET", "/api/auto-update")
    assert status["enabled"] is False and status["available"] is True
    assert status["limits"] == {
        "stale_days": 3,
        "minutes": 10,
        "max_sites": 60,
        "max_requests": 1000,
        "min_gap_hours": 6,
        "score_limit": 5000,
        "workable_minutes": 15,
    }
    assert call(app, "POST", "/api/auto-update/run") == {"started": False, "reason": "DISABLED"}
    assert recorded == [], "nothing runs until it is chosen"
    assert call(app, "PATCH", "/api/auto-update", {"enabled": True})["enabled"] is True
    assert call(app, "PATCH", "/api/auto-update", {"enabled": False})["enabled"] is False


def _reasons(app) -> dict[str, str]:
    conn = connect(app.config.db_path)
    try:
        return {i["key"]: i["reason"] for i in auto_update.plan_now(conn)["items"]}
    finally:
        conn.close()


def test_only_sites_overdue_by_three_days_are_asked_and_a_pause_is_respected(app):
    reasons = _reasons(app)
    assert reasons["greenhouse:fresh"] == "FRESH", "updated a day ago: not overdue"
    # Overdue and never updated are eligible: the budget, not the age, decides
    # how many of them this run takes (one unmeasured site per run).
    assert reasons["greenhouse:stale"] != "FRESH" and reasons["greenhouse:never"] != "FRESH"
    assert not reasons["greenhouse:stale"].startswith("PAUSED")
    call(app, "PATCH", "/api/sources/schedule", {"source_id": "greenhouse", "mode": "PAUSED"})
    paused = _reasons(app)
    assert {v for k, v in paused.items() if k.startswith("greenhouse:")} == {"PAUSED_BY_PROFILE"}


def test_opening_starts_one_bounded_run_and_reopening_does_not_start_another(app, recorded):
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    first = call(app, "POST", "/api/auto-update/run")
    assert first["started"] is True and first["planned"] >= 1
    app.retrieval.join(30)
    second = call(app, "POST", "/api/auto-update/run")
    assert second == {"started": False, "reason": "RECENT"}
    assert len(recorded) == 2, "the quick run, then Workable as a step of its own"
    asked, workable = recorded
    assert asked["budget_seconds"] == 600 and asked["max_items"] == 60
    assert asked["stale_hours"] == 72 and "provider" not in asked
    assert asked["profile_blocks"]["workable"] == "RUNS_SEPARATELY"
    assert workable["provider"] == "workable" and workable["budget_seconds"] == 900
    assert workable["max_items"] == 1 and workable["stale_hours"] == 72
    status = call(app, "GET", "/api/auto-update")
    result = status["last_result"]
    assert result["asked"] == 2 and result["updated"] == 1 and result["left_for_later"] == 1
    assert result["workable"] == {
        "outcome": "DEFERRED",
        "reason": "TIME_BUDGET",
        "jobs_new": 3,
        "jobs_changed": 1,
        "jobs_unchanged": 40,
    }
    assert result["stopped"] == "BUDGET" and status["last_run_at"]


def test_a_second_request_while_one_is_running_is_busy_not_a_second_run(app, monkeypatch):
    release = []

    def slow(conn, **kwargs):
        while not release:
            time.sleep(0.02)
        return {"selected": [], "results": [], "status": "FINISHED"}

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", slow)
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    assert call(app, "POST", "/api/auto-update/run")["started"] is True
    snapshot = app.retrieval.snapshot()
    assert snapshot["kind"] == "auto" and call(app, "GET", "/api/auto-update")["running"] is True
    assert call(app, "POST", "/api/auto-update/run")["reason"] in {"RECENT", "BUSY"}
    release.append(1)
    app.retrieval.join(30)


def test_stop_ends_the_run_between_sites_and_nothing_is_scored_by_ai(app, monkeypatch):
    def interrupted(conn, *, on_progress, **kwargs):
        receipt = {"selected": ["greenhouse:stale"], "results": [], "active": "greenhouse:stale"}
        app.retrieval.cancel()
        try:
            on_progress(receipt)
        except KeyboardInterrupt:
            return {**receipt, "status": "INTERRUPTED"}
        raise AssertionError("progress should have stopped the run")

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", interrupted)
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    call(app, "POST", "/api/auto-update/run")
    app.retrieval.join(30)
    assert call(app, "GET", "/api/auto-update")["last_result"]["stopped"] == "CANCELLED"


def test_the_demo_never_updates_anything(tmp_path):
    demo = build(tmp_path, RuntimeMode.DEMO)
    assert call(demo, "POST", "/api/auto-update/run") == {"started": False, "reason": "DEMO"}


def test_the_request_cap_ends_work_like_an_expired_deadline():
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    fetcher = HttpFetcher(
        client=httpx.Client(transport=transport), request_cap=2, request_delay_seconds=0
    )
    fetcher.get_json("https://example.test/a", use_cache=False)
    fetcher.get_json("https://example.test/b", use_cache=False)
    with pytest.raises(BudgetExpired):
        fetcher.get_json("https://example.test/c", use_cache=False)
    assert fetcher.requests_spent == 2


@pytest.mark.parametrize(
    ("waiting", "expected"),
    [(0, "NONE"), (40, "STARTED"), (auto_update.SCORE_LIMIT + 1, "PENDING_LARGE")],
)
def test_only_what_arrived_is_scored_and_a_whole_recalculation_is_left_to_the_person(
    app, monkeypatch, waiting, expected
):
    class Plan:
        targets = ["job"] * waiting

    started = []
    monkeypatch.setattr("career_agent.pipeline.rescore.plan", lambda conn, config: Plan())
    monkeypatch.setattr(app.rescore, "start", lambda work, run_id: started.append(work))
    assert auto_update._score(app) == expected
    assert bool(started) == (expected == "STARTED")


# -- Workable: the per-site update and the progress a person sees ----------------


def _workable_feed(count: int):
    from tests.integration.test_workable_update import Server, feed_xml

    return Server(feed_xml(count))


def _use_feed(monkeypatch, server, gate=None):
    """Point the app's fetcher at an in-memory Workable (nothing leaves the machine)."""
    import httpx

    def handler(request):
        if gate is not None:
            gate.wait(20)
        return server.handler(request)

    class Local(auto_update._StoppableFetcher):
        def __init__(self, **kwargs):
            client = httpx.Client(transport=httpx.MockTransport(handler))
            super().__init__(client=client, request_delay_seconds=0, **kwargs)

    monkeypatch.setattr(auto_update, "_StoppableFetcher", Local)


def test_updating_workable_by_hand_reads_the_shared_feed_and_shows_where_it_is(app, monkeypatch):
    import threading

    gate = threading.Event()
    server = _workable_feed(30)
    _use_feed(monkeypatch, server, gate)
    started = call(app, "POST", "/api/sources/refresh", {"source_id": "workable"})
    assert started["status"] == "running"
    deadline = time.monotonic() + 10
    detail = None
    while time.monotonic() < deadline and detail is None:
        snap = app.retrieval.snapshot() or {}
        detail = snap.get("current_detail")
        time.sleep(0.02)
    assert detail and detail["phase"] == "downloading", "the bar can say what it is doing"
    gate.set()
    app.retrieval.join(30)
    assert app.retrieval.snapshot()["current_detail"] is None
    conn = connect(app.config.db_path)
    try:
        assert (
            conn.execute("SELECT COUNT(*) FROM job WHERE provider = 'workable'").fetchone()[0] == 30
        )
    finally:
        conn.close()


def test_stop_ends_a_workable_update_and_keeps_what_was_read(app, monkeypatch):
    import threading

    gate = threading.Event()
    _use_feed(monkeypatch, _workable_feed(30), gate)
    call(app, "POST", "/api/sources/refresh", {"source_id": "workable"})
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not (app.retrieval.snapshot() or {}).get(
        "current_detail"
    ):
        time.sleep(0.02)
    assert call(app, "POST", "/api/retrieval/cancel") == {"cancelled": True}
    gate.set()
    app.retrieval.join(30)
    snapshot = app.retrieval.snapshot()
    assert snapshot["status"] == "cancelled"
    conn = connect(app.config.db_path)
    try:
        run = conn.execute(
            "SELECT status, stats_json FROM pipeline_run WHERE stage = 'collect-workable'"
        ).fetchone()
    finally:
        conn.close()
    assert run[0] == "OK" and json.loads(run[1])["deferred_reason"] == "CANCELLED"


# --- Reopening the app: the gate is the last FINISHED run, never a start ----


def _state(app, key: str, value: str) -> None:
    from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate

    conn = connect(app.config.db_path)
    with transaction(conn):
        CandidateStateRepo(conn).set(ensure_candidate(conn), key, value)
    conn.close()


def test_reopening_many_times_after_a_finished_run_asks_no_site_again(app, recorded):
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    assert call(app, "POST", "/api/auto-update/run")["started"] is True
    app.retrieval.join(30)
    engine_calls = len(recorded)
    for _ in range(5):
        assert call(app, "POST", "/api/auto-update/run") == {"started": False, "reason": "RECENT"}
    assert len(recorded) == engine_calls, "five reopenings, not one more request"


def test_a_run_the_app_was_closed_during_is_not_recent(app, recorded):
    """The start is written before the run; a closed app never writes the end.
    That unfinished run must not hold the next opening back for six hours."""
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    _state(app, auto_update.KEY_LAST, _ago(1 / 24))
    first = call(app, "POST", "/api/auto-update/run")
    assert first["started"] is True
    app.retrieval.join(30)
    assert call(app, "POST", "/api/auto-update/run")["reason"] == "RECENT"


def test_a_run_that_ended_in_error_is_not_recent(app, recorded):
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    at = _ago(1 / 24)
    _state(app, auto_update.KEY_LAST, at)
    _state(app, auto_update.KEY_RESULT, json.dumps({"at": at, "stopped": "ERROR"}))
    assert call(app, "POST", "/api/auto-update/run")["started"] is True
    app.retrieval.join(30)


def test_a_stop_or_a_spent_budget_still_counts_as_a_finished_run(app, recorded):
    """Stop is the person's choice and a budget is a deliberate limit: neither
    reopens the gate, or ten openings would be ten ten-minute runs."""
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    for stopped in ("CANCELLED", "BUDGET", "DONE"):
        at = _ago(1 / 24)
        _state(app, auto_update.KEY_LAST, at)
        _state(app, auto_update.KEY_RESULT, json.dumps({"at": at, "stopped": stopped}))
        assert call(app, "POST", "/api/auto-update/run") == {"started": False, "reason": "RECENT"}
    assert recorded == []


def test_after_a_complete_refresh_last_night_this_morning_asks_nothing(app, recorded):
    """Freshness is per site, from its last SUCCESSFUL read: a full refresh
    yesterday evening leaves nothing overdue, whatever the gate says."""
    conn = connect(app.config.db_path)
    with transaction(conn):
        conn.execute("UPDATE source_board SET last_collected_at = ?", (_ago(0.5),))
    conn.close()
    reasons = _reasons(app)
    boards = {k: v for k, v in reasons.items() if k.startswith("greenhouse:")}
    assert set(boards.values()) == {"FRESH"}, boards

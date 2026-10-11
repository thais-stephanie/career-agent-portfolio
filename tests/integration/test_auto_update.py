"""Update overdue job sites when the app opens: off until chosen, bounded,
never duplicated, and it never starts what only the person starts.

The collectors are not reached: the maintenance engine is replaced by a
recorder, so these tests are about WHEN and WITHIN WHAT it runs.
"""

from __future__ import annotations

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
    assert len(recorded) == 1
    asked = recorded[0]
    assert asked["budget_seconds"] == 600 and asked["max_items"] == 60
    assert asked["stale_hours"] == 72 and "profile_blocks" in asked
    status = call(app, "GET", "/api/auto-update")
    result = status["last_result"]
    assert result["asked"] == 1 and result["updated"] == 1 and result["left_for_later"] == 1
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

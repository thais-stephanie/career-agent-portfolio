"""Source health: one model, public where it may be, due before stale.

- A finished collection writes the SHARED catalogue's record: outcome, a
  reason code, times and counts, and never a query or a failure line.
- Every profile reads that record, so a catalogue refreshed by one profile
  is fresh for another.
- DUE after a day, STALE after three, a refusal is RATE_LIMITED (never "no
  jobs") and waits out its cooldown; `needs_attention` is one rule.
- "Refresh due sources" runs only what is due, isolates a failure, and never
  asks a source that is cooling down.

Synthetic data only.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_shared_catalogue import two_profiles  # noqa: F401 -- fixture

from career_agent.domain.enums import PipelineRunStatus
from career_agent.sources import public_health
from career_agent.sources.progress import RefreshState, read_progress
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import PipelineRunRepo

PRIVATE_QUERY = "Synthetic Private Role Phrase"


def _db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "single.db")
    migrate(conn)
    return conn


def _finish(conn, stage: str, status: PipelineRunStatus, stats: dict) -> None:
    runs = PipelineRunRepo(conn)
    with transaction(conn):
        run_id = runs.start(stage)
    with transaction(conn):
        runs.finish(run_id, status, stats=stats, error=None)


def _public(conn) -> dict:
    return public_health.read(conn)


def _iso(delta_hours: float) -> str:
    moment = datetime.now(UTC) - timedelta(hours=delta_hours)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------ what is written


def test_a_finished_feed_run_writes_outcome_and_counts_only(tmp_path) -> None:
    conn = _db(tmp_path)
    _finish(
        conn,
        "collect-linkedin",
        PipelineRunStatus.OK,
        {
            "queries_rate_limited": 1,
            "stopped_reason": "rate_limited",
            "failures": [f"rate limited: {PRIVATE_QUERY}|BR"],
            "raw_results": 7,
            "jobs_new": 2,
        },
    )
    row = _public(conn)["collect-linkedin"]
    assert row.last_outcome == "RATE_LIMITED" and row.last_reason == "REFUSED"
    assert row.jobs_seen == 7 and row.jobs_new == 2
    assert row.last_success_at is None, "a refusal is not a success"
    everything = str(conn.execute("SELECT * FROM source_health").fetchall())
    assert PRIVATE_QUERY not in everything, "a private query reached the shared record"


@pytest.mark.parametrize(
    ("status", "stats", "outcome", "reason"),
    [
        (PipelineRunStatus.OK, {"postings_seen": 10}, "COMPLETE", None),
        (PipelineRunStatus.OK, {"stopped_early": True}, "PARTIAL", "PAGE_LIMIT"),
        (PipelineRunStatus.FAILED, {}, "FAILED", "ERROR"),
        (PipelineRunStatus.OK, {"stopped_reason": "rate_limited"}, "RATE_LIMITED", "REFUSED"),
    ],
)
def test_outcomes(tmp_path, status, stats, outcome, reason) -> None:
    conn = _db(tmp_path)
    _finish(conn, "collect-gupy", status, stats)
    row = _public(conn)["collect-gupy"]
    assert (row.last_outcome, row.last_reason) == (outcome, reason)


def test_the_shared_board_pass_writes_one_row_per_family(tmp_path) -> None:
    conn = _db(tmp_path)
    _finish(
        conn,
        "collect",
        PipelineRunStatus.FAILED,
        {
            "by_provider": {
                "greenhouse": {"boards_attempted": 10, "boards_succeeded": 9, "boards_failed": 1},
                "lever": {"boards_attempted": 3, "boards_succeeded": 0, "boards_failed": 3},
                "ashby": {"boards_attempted": 0},
            }
        },
    )
    rows = _public(conn)
    assert rows["collect:greenhouse"].last_outcome == "PARTIAL", "some 404s are not failure"
    assert rows["collect:lever"].last_outcome == "FAILED"
    assert "collect:ashby" not in rows, "a family the pass never tried is not recorded"


def test_a_rescore_is_not_a_collection(tmp_path) -> None:
    conn = _db(tmp_path)
    _finish(conn, "rescore", PipelineRunStatus.OK, {})
    assert _public(conn) == {}


# -------------------------------------------------------- shared vs private


def test_a_catalogue_refreshed_by_one_profile_is_fresh_for_the_other(two_profiles) -> None:  # noqa: F811
    a = connect(two_profiles["a_db"])
    try:
        _finish(a, "collect-gupy", PipelineRunStatus.OK, {"postings_seen": 5})
    finally:
        a.close()
    b = connect(two_profiles["b_db"])
    try:
        assert (
            b.execute("SELECT COUNT(*) FROM pipeline_run WHERE stage = 'collect-gupy'").fetchone()[
                0
            ]
            == 0
        ), "B never ran it itself"
        rows = read_progress(b, stage_for={"gupy": "collect-gupy"}, public=_public(b))
    finally:
        b.close()
    assert rows[0].state is RefreshState.COMPLETE, "B read the shared record"
    assert rows[0].last_success is not None


# -------------------------------------------------------------- freshness


def _with_public(tmp_path, *, outcome: str, hours_ago: float, success: bool = True):
    conn = _db(tmp_path)
    stamp = _iso(hours_ago)
    with transaction(conn):
        conn.execute(
            "INSERT INTO source_health (source_key, last_attempt_at, last_finished_at,"
            " last_success_at, last_outcome, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("collect-gupy", stamp, stamp, stamp if success else None, outcome, stamp),
        )
    return read_progress(conn, stage_for={"gupy": "collect-gupy"}, public=_public(conn))[0]


@pytest.mark.parametrize(
    ("hours", "state", "due", "attention"),
    [
        (2, RefreshState.COMPLETE, False, False),
        (25, RefreshState.DUE, True, False),
        (80, RefreshState.STALE, True, True),
    ],
)
def test_due_after_a_day_stale_after_three(tmp_path, hours, state, due, attention) -> None:
    row = _with_public(tmp_path, outcome="COMPLETE", hours_ago=hours)
    assert (row.state, row.due, row.needs_attention) == (state, due, attention)


def test_a_refusal_waits_out_its_cooldown(tmp_path) -> None:
    recent = _with_public(tmp_path / "a", outcome="RATE_LIMITED", hours_ago=2, success=False)
    assert recent.state is RefreshState.RATE_LIMITED and recent.needs_attention
    assert recent.due is False and recent.cooldown_until is not None
    older = _with_public(tmp_path / "b", outcome="RATE_LIMITED", hours_ago=30, success=False)
    assert older.due is True and older.cooldown_until is None


def test_a_failure_waits_an_hour(tmp_path) -> None:
    fresh = _with_public(tmp_path / "a", outcome="FAILED", hours_ago=0.2, success=False)
    assert fresh.state is RefreshState.FAILED and not fresh.due
    later = _with_public(tmp_path / "b", outcome="FAILED", hours_ago=2, success=False)
    assert later.due


def test_never_run_is_due_but_not_alarming(tmp_path) -> None:
    conn = _db(tmp_path)
    row = read_progress(conn, stage_for={"gupy": "collect-gupy"}, public={})[0]
    assert row.state is RefreshState.NOT_STARTED and row.due and not row.needs_attention


# ---------------------------------------------------------- refresh due


@pytest.fixture
def api(tmp_path: Path, monkeypatch):  # noqa: ANN201
    import shutil

    from tests.support import committed_config_dir

    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig

    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "health")
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)


def _rows(api, due_ids: set[str], cooling: set[str] = frozenset()):  # noqa: ANN202
    from career_agent.sources.health import health
    from career_agent.sources.progress import SourceProgress

    with connect(api.config.db_path) as conn:
        entries = health(conn, catalogue_path=api.config.config_dir / "source_catalogue.yaml")
    out = []
    for entry in entries:
        sid = entry.source.id
        if sid in cooling:
            out.append(
                SourceProgress(sid, "x", RefreshState.RATE_LIMITED, cooldown_until=_iso(-20))
            )
        elif sid in due_ids:
            out.append(SourceProgress(sid, "x", RefreshState.DUE))
        else:
            out.append(SourceProgress(sid, "x", RefreshState.COMPLETE))
    return out


def test_refresh_due_runs_only_due_sources_and_isolates_failures(api, monkeypatch) -> None:
    import threading

    from career_agent.web import source_refresh

    ran: list[str] = []
    done = threading.Event()

    def fake_feed(db_path, stage, *, config_dir=None):  # noqa: ANN001, ANN202
        def work(state, cancel):  # noqa: ANN001, ANN202
            ran.append(stage)
            if stage == "collect-remotive":
                raise RuntimeError("synthetic failure")

        return work

    monkeypatch.setattr(source_refresh, "feed_work", fake_feed)
    monkeypatch.setattr(api, "_collect_work", lambda *a, **k: lambda s, c: ran.append("boards"))
    monkeypatch.setattr(api, "_rescore_work", lambda: lambda s, c: done.set())
    monkeypatch.setattr(
        api, "_refresh_progress", lambda entries: _rows(api, {"remotive", "jobicy"})
    )
    answer = api.handle_api("POST", "/api/sources/refresh-due", {}, {})
    assert answer["started"] is True and answer["due"] == 2
    for _ in range(200):
        if not api.retrieval.running:
            break
        threading.Event().wait(0.05)
    assert sorted(ran) == ["collect-jobicy", "collect-remotive"], ran
    status = api.handle_api("GET", "/api/retrieval", {}, {})
    outcomes = {s["provider"]: s for s in (status.get("run") or {}).get("sources", [])}
    assert any(o["boards_failed"] == 1 for o in outcomes.values()), "the failure is reported"
    assert any(o["boards_succeeded"] == 1 for o in outcomes.values()), "the success is kept"


def test_nothing_due_runs_nothing(api, monkeypatch) -> None:
    monkeypatch.setattr(api, "_refresh_progress", lambda entries: _rows(api, set()))
    answer = api.handle_api("POST", "/api/sources/refresh-due", {}, {})
    assert answer == {"started": False, "due": 0, "cooling_down": []}
    assert not api.retrieval.running


def test_a_cooling_down_source_is_never_asked(api, monkeypatch) -> None:
    from career_agent.web import source_refresh

    ran: list[str] = []
    monkeypatch.setattr(
        source_refresh,
        "feed_work",
        lambda db, stage, config_dir=None: lambda s, c: ran.append(stage),
    )
    monkeypatch.setattr(api, "_rescore_work", lambda: lambda s, c: None)
    monkeypatch.setattr(
        api, "_refresh_progress", lambda entries: _rows(api, set(), cooling={"remotive"})
    )
    answer = api.handle_api("POST", "/api/sources/refresh-due", {}, {})
    assert answer["started"] is False and answer["cooling_down"] == ["remotive"]
    assert ran == []


def test_the_sidebar_and_settings_share_one_rule(api) -> None:
    payload = api.handle_api("GET", "/api/sources", {}, {})
    for row in payload["refresh"]:
        assert {"due", "needs_attention", "cooldown_until", "last_attempt"} <= set(row)
    main = Path(__file__).resolve().parents[2] / "src/career_agent/web/static/js/main.js"
    assert "row.needs_attention" in main.read_text(encoding="utf-8")

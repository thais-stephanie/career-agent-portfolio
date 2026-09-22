"""Real collectors, mock HTTP, disposable databases; no production access."""

import json
import sqlite3

import httpx
import pytest
from typer.testing import CliRunner

from career_agent.cli import app
from career_agent.net.deadline import Deadline
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.pipeline.maintenance import execute
from career_agent.pipeline.remotive_collect import RemotiveCollector
from career_agent.runtime.maintenance_lock import maintenance_lock, maintenance_running
from career_agent.sources.maintenance import freshness, history, inventory, read_only
from career_agent.sources.progress import _last_success
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    migrate(c)
    with transaction(c):
        company = CompanyRepo(c).upsert(CompanyRecord(slug="example", name="Example"))
        SourceBoardRepo(c).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="example")
        )
    yield c
    c.close()


def transport(deadline=None, handler=None):
    return HttpFetcher(
        deadline=deadline,
        request_delay_seconds=0,
        backoff_seconds=0,
        client=httpx.Client(
            transport=httpx.MockTransport(
                handler or (lambda _: httpx.Response(200, json={"jobs": []}))
            )
        ),
    )


def test_read_only_plan_creates_no_run_or_lock_file(conn, tmp_path):
    db = tmp_path / "test.db"
    before = db.read_bytes()
    result = CliRunner().invoke(app, ["refresh", "--db", str(db), "--plan", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["inventory_count"] > 0
    assert db.read_bytes() == before
    assert conn.execute("SELECT COUNT(*) FROM pipeline_run").fetchone()[0] == 0
    assert not db.with_suffix(".db.maintenance.lock").exists()
    with read_only(db) as ro, pytest.raises(sqlite3.OperationalError):
        ro.execute("DELETE FROM source_board")


def test_budget_expiry_does_not_stamp_close_or_mark_board_failed(conn):
    calls = []
    fetcher = transport(Deadline(1, lambda: 2), lambda r: calls.append(r))
    stats = Collector(conn, fetcher).collect_all()
    board = conn.execute("SELECT * FROM source_board").fetchone()
    assert board["last_collected_at"] is None
    assert board["last_error"] is None
    assert stats.boards_failed == stats.boards_attempted == stats.jobs_closed == 0
    assert stats.deferred_reason == "TIME_BUDGET"
    assert calls == []
    run = conn.execute("SELECT * FROM pipeline_run").fetchone()
    assert run["finished_at"] and run["status"] == "OK"


def test_expiry_after_response_before_persistence_keeps_board_pending(conn):
    now = [0]

    def handler(_):
        now[0] = 11
        return httpx.Response(200, json={"jobs": []})

    stats = Collector(conn, transport(Deadline(10, lambda: now[0]), handler)).collect_all()
    assert stats.deferred_reason == "TIME_BUDGET"
    assert conn.execute("SELECT last_collected_at FROM source_board").fetchone()[0] is None


def test_resume_skips_successful_board_and_records_direct_timing(conn):
    result = execute(conn, budget_seconds=180, provider="greenhouse", fetcher_factory=transport)
    assert result["results"][0]["outcome"] == "OK"
    assert conn.execute("SELECT last_collected_at FROM source_board").fetchone()[0]
    second = execute(conn, budget_seconds=180, provider="greenhouse", fetcher_factory=transport)
    assert second["selected"] == []
    run = conn.execute("SELECT stats_json FROM pipeline_run WHERE stage = 'collect'").fetchone()
    assert "elapsed_ms" in json.loads(run[0])["by_board"][0]


def test_interruption_retains_active_identity_without_claiming_source_failure(conn):
    def interrupted(_):
        raise KeyboardInterrupt

    result = execute(
        conn,
        budget_seconds=180,
        provider="greenhouse",
        fetcher_factory=lambda deadline: transport(deadline, interrupted),
    )
    assert result["status"] == "INTERRUPTED"
    assert result["active"] == "greenhouse:example"
    assert conn.execute("SELECT last_error FROM source_board").fetchone()[0] is None
    assert any(i.key == result["active"] and i.last_success is None for i in inventory(conn))


def test_feed_budget_deferral_is_not_a_successful_freshness_stamp(conn):
    result = RemotiveCollector(conn, transport(Deadline(1, lambda: 2))).collect()
    assert result.deferred_reason == "TIME_BUDGET"
    assert result.failures == []
    assert "collect-remotive" not in _last_success(conn)
    assert not [s for s in history(conn) if s.provider == "remotive"]


def test_os_lock_released_after_interruption(tmp_path):
    path = tmp_path / "test.db"
    assert not maintenance_running(path)
    with maintenance_lock(path):
        assert maintenance_running(path)
        with pytest.raises(RuntimeError), maintenance_lock(path):
            pass
    with maintenance_lock(path):
        assert maintenance_running(path)
    assert not maintenance_running(path)


def test_owner_run_feeds_never_admitted(conn):
    items = inventory(conn)
    assert all(
        i.blocked == "OWNER_RUN_ONLY" for i in items if i.provider in {"remoteok", "getonbrd"}
    )


def test_freshness_ledger_does_not_claim_process_is_alive(conn):
    conn.execute(
        "INSERT INTO pipeline_run (id,stage,started_at,status,stats_json)"
        " VALUES ('old','source-maintenance','2026-01-01T00:00:00Z','RUNNING','{}')"
    )
    assert freshness(conn)["activity"] == "UNCONFIRMED_RUNNING_OR_INTERRUPTED"


def test_completed_provider_output_equivalent_with_and_without_deadline(conn):
    payload = {
        "jobs": [
            {
                "id": 123,
                "title": "Operations coordinator",
                "absolute_url": "https://example.org/job/123",
                "location": {"name": "Remote"},
                "content": "<p>Coordinate our operations.</p>",
            }
        ]
    }

    def handler(_):
        return httpx.Response(200, json=payload)

    Collector(conn, transport(handler=handler)).collect_all(use_cache=False)
    before = [
        tuple(r)
        for r in conn.execute(
            "SELECT provider, external_id, title, content_hash, closed_at FROM job"
        )
    ]
    stats = Collector(conn, transport(Deadline(1e20), handler)).collect_all(use_cache=False)
    after = [
        tuple(r)
        for r in conn.execute(
            "SELECT provider, external_id, title, content_hash, closed_at FROM job"
        )
    ]
    assert before == after
    assert stats.jobs_seen_again == 1 and stats.jobs_new == stats.jobs_closed == 0


def test_actual_workday_adapter_stops_between_detail_requests_and_resumes(conn):
    conn.execute(
        "UPDATE source_board SET provider='workday', board_identifier='example.wd1/careers'"
    )
    now = [0.0]
    calls = []

    def handler(request):
        calls.append(request.method)
        now[0] += 1
        if request.method == "POST":
            return httpx.Response(
                200,
                json={
                    "total": 3,
                    "jobPostings": [
                        {
                            "title": f"Role {i}",
                            "externalPath": f"/job/Remote/Role_JR{i}",
                            "locationsText": "Remote",
                            "bulletFields": [f"JR{i}"],
                        }
                        for i in range(3)
                    ],
                },
            )
        return httpx.Response(
            200,
            json={
                "jobPostingInfo": {
                    "jobDescription": "<p>Coordinate operations.</p>",
                    "country": {"descriptor": "Brazil"},
                }
            },
        )

    stats = Collector(conn, transport(Deadline(2, lambda: now[0]), handler)).collect_all(False)
    assert stats.deferred_reason == "TIME_BUDGET"
    assert calls == ["POST", "GET"]
    assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 0
    assert conn.execute("SELECT last_collected_at FROM source_board").fetchone()[0] is None
    assert conn.execute("SELECT last_error FROM source_board").fetchone()[0] is None
    stats = Collector(conn, transport(Deadline(100, lambda: now[0]), handler)).collect_all(False)
    assert stats.boards_succeeded == 1 and stats.jobs_new == 3


def test_budget_deferred_family_progress_is_partial_not_failed_or_complete(conn):
    from career_agent.sources.progress import read_progress

    Collector(conn, transport(Deadline(1, lambda: 2))).collect_all()
    result = read_progress(
        conn, stage_for={"source": "collect"}, providers={"source": "greenhouse"}
    )[0]
    assert result.state.value == "PARTIAL"
    assert result.last_success is None


def test_cli_refuses_live_demo_or_unidentified_database(conn, tmp_path):
    result = CliRunner().invoke(app, ["refresh", "--db", str(tmp_path / "test.db"), "--execute"])
    assert result.exit_code != 0
    assert "personal database identity" in result.output


def test_request_refusal_stops_the_family_without_failing_deferred_boards(conn):
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="second", name="Second"))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="second")
        )
    # Establish known costs so both boards can be admitted in one session.
    Collector(conn, transport()).collect_all()
    conn.execute("UPDATE source_board SET last_collected_at='2026-01-01T00:00:00Z'")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(403)

    result = execute(
        conn,
        budget_seconds=180,
        provider="greenhouse",
        fetcher_factory=lambda deadline: transport(deadline, handler),
    )
    assert len(calls) == 1
    assert [r["outcome"] for r in result["results"]] == ["FAILED", "DEFERRED"]
    assert result["results"][1]["reason"] == "PROVIDER_REFUSAL"


def test_plan_observes_provider_cooldown_even_when_freshness_is_zero(conn):
    from career_agent.clock import now_utc
    from career_agent.sources.maintenance import plan

    conn.execute(
        "INSERT INTO pipeline_run (id,stage,started_at,status,stats_json)"
        " VALUES ('poll','collect-remotive',?,'RUNNING','{}')",
        (now_utc(),),
    )
    result = plan(inventory(conn), 900, stale_hours=0)
    feed = next(i for i in result["items"] if i["key"] == "feed:remotive")
    assert not feed["selected"] and feed["reason"] == "PROVIDER_COOLDOWN"


def test_freshness_api_is_read_only_and_does_not_gate_discover(conn, tmp_path):
    from tests.support import committed_config_dir

    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig

    api = JobsApi(
        ServerConfig(db_path=tmp_path / "test.db", config_dir=committed_config_dir()), quiet=True
    )
    status = api.handle_api("GET", "/api/source-maintenance", {}, {})
    assert status["inventory"] > 0
    assert status["maintenance_running"] is False
    assert status["app_refresh_running"] is False
    assert conn.execute("SELECT COUNT(*) FROM pipeline_run").fetchone()[0] == 0


def test_unaddressable_identity_is_refused_not_a_source_failure(conn):
    conn.execute("UPDATE source_board SET provider='workday', board_identifier='incomplete'")
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500)

    result = execute(
        conn,
        budget_seconds=180,
        provider="workday",
        fetcher_factory=lambda deadline: transport(deadline, handler),
    )
    assert calls == []
    assert result["results"][0]["outcome"] == "DEFERRED"
    assert result["results"][0]["reason"] == "BOARD_NOT_ADDRESSABLE"

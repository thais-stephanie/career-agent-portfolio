"""Scheduling controls never pass candidate preferences to collection."""

from pathlib import Path
from types import SimpleNamespace

import pytest
from tests.support import committed_config_dir

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig
from career_agent.web.source_refresh import can_refresh, feed_work, modes


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "source-controls.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.DEMO, "source controls")
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir()), quiet=True)


def test_scheduling_changes_no_jobs_scores_or_search_configuration(api):
    initial = api.handle_api("GET", "/api/sources", {}, {})
    source = next(s for s in initial["sources"] if s["id"] == "gupy")
    assert source["can_refresh"]
    version = api._identity()
    for mode in ("PAUSED", "ENABLED", "AUTO"):
        api.handle_api("PATCH", "/api/sources/schedule", {}, {"source_id": "gupy", "mode": mode})
        with connect(api.config.db_path) as conn:
            assert modes(conn)["gupy"] == mode
            assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 0
            assert conn.execute("SELECT count(*) FROM job_match").fetchone()[0] == 0
        assert api._identity() == version
        data = api.handle_api("GET", "/api/sources", {}, {})
        assert next(s for s in data["sources"] if s["id"] == "gupy")["refresh_mode"] == mode
        if mode == "PAUSED":
            assert next(s for s in data["refresh"] if s["source_id"] == "gupy")["state"] == "PAUSED"


def test_demo_and_unknown_sources_cannot_start_network_work(api):
    with pytest.raises(ApiError, match="Demo databases"):
        api.handle_api("POST", "/api/sources/refresh", {}, {"source_id": "gupy"})
    with pytest.raises(ApiError, match="existing source"):
        api.handle_api("POST", "/api/sources/refresh", {}, {"source_id": "made-up-source"})
    assert not api.retrieval.running


def test_forbidden_source_cannot_be_enabled_even_with_existing_adapter():
    entry = SimpleNamespace(
        source=SimpleNamespace(
            provider="gupy",
            collection_blocker=None,
            permission=SimpleNamespace(value="FORBIDDEN"),
        ),
        state="DISABLED",
    )
    assert not can_refresh(entry)


def test_manual_feed_reuses_only_existing_cli_defaults(monkeypatch, tmp_path):
    seen = []

    class Process:
        returncode = 0

        def __init__(self, args, **kwargs):
            seen.append((args, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def poll(self):
            return 0

    monkeypatch.setattr("career_agent.web.source_refresh.subprocess.Popen", Process)
    feed_work(tmp_path / "db.sqlite", "collect-gupy")(None, None)
    args, options = seen[0]
    assert args[1:4] == ["-m", "career_agent.cli", "collect-gupy"]
    assert args[4:] == ["--db", str(tmp_path / "db.sqlite")]
    assert not options.get("shell")
    assert not {"--workplace", "--query", "--country", "--max-pages"}.intersection(args)
    with pytest.raises(ValueError, match="existing collection"):
        feed_work(Path("unused"), "collect-new-provider")


@pytest.mark.parametrize("finished_at", [None, "2099-01-01T00:00:00+00:00"])
def test_manual_failure_without_final_pipeline_update_is_visible(api, monkeypatch, finished_at):
    from career_agent.storage.repositories import PipelineRunRepo

    with connect(api.config.db_path) as conn, transaction(conn):
        PipelineRunRepo(conn).start("collect-gupy")
    api._active_source_refresh = "gupy"
    monkeypatch.setattr(
        api.retrieval,
        "snapshot",
        lambda: {
            "status": "failed",
            "error": "Synthetic launch failure",
            "finished_at": finished_at,
        },
    )
    data = api.handle_api("GET", "/api/sources", {}, {})
    state = next(s for s in data["refresh"] if s["source_id"] == "gupy")
    assert state["state"] == "FAILED"
    assert state["blocker"] == "Synthetic launch failure"

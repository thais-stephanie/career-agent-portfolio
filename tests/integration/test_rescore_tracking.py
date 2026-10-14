"""A recalculation is followed to its real end: running, done, failed or stopped.

THE OWNER'S EVENING (2026-10-13). 171,319 scores were recalculated in 86
minutes and every one was saved; the screen meanwhile lost the progress, went
back to the starting notice with the starting count, and once said only
"Recalculating...". These tests pin the account `/api/rescore` gives, on a
synthetic corpus: what was committed survives a failure and a restart, the
next pass takes only what is still to do, a run another process is writing is
not started twice, and Home counts what is left from committed scores.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_semantic_api import build

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import PipelineRunStatus
from career_agent.pipeline import rescore as rescore_module
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime.mode import RuntimeMode
from career_agent.storage.db import connect, transaction
from career_agent.storage.repositories import PipelineRunRepo
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

DEMO_FILE = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    """A personal profile with the demo corpus, every score of it out of date."""
    app = build(tmp_path, RuntimeMode.PERSONAL)
    config, _ = load_search_config(app.config.config_dir)
    conn = connect(app.config.db_path)
    try:
        seed_demo(conn, config, source=DEMO_FILE)
        # The owner's case: scores missing for the current reader, no posting
        # marked as changed (her 171,319 were all MISSING_OR_STALE). A MARKED
        # posting is retried after a stopped pass by design (ADR-0026), and
        # `test_incremental_safety` pins that.
        with transaction(conn):
            conn.execute("DELETE FROM job_match")
            conn.execute("DELETE FROM job_dirty")
    finally:
        conn.close()
    return app


def status(app: JobsApi) -> dict:
    return app.handle_api("GET", "/api/rescore", {}, {})


def wait(app: JobsApi) -> None:
    deadline = time.monotonic() + 120
    while app.rescore.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not app.rescore.running, "the pass did not end within two minutes"


def targets(app: JobsApi) -> int:
    conn = connect(app.config.db_path)
    try:
        return len(rescore_module.plan(conn, app.search_config()).targets)
    finally:
        conn.close()


def outdated_on_home(app: JobsApi) -> int:
    return int(app.handle_api("GET", "/api/home", {}, {})["scores"]["outdated"])


def fail_after(monkeypatch: pytest.MonkeyPatch, calls: int) -> None:
    """Pages of five, and the WRITE of a page breaks once `calls` scores were
    recorded: a database error, which ends the pass (a posting that cannot be
    scored is counted and skipped instead)."""
    monkeypatch.setattr(rescore_module, "_PAGE", 5)
    real = rescore_module.invalidation.receipt
    seen = {"n": 0}

    def flaky(*args, **kwargs):
        seen["n"] += 1
        if seen["n"] >= calls:
            raise RuntimeError("synthetic failure after partial progress")
        return real(*args, **kwargs)

    monkeypatch.setattr(rescore_module.invalidation, "receipt", flaky)


def test_a_failure_after_partial_progress_keeps_what_was_saved_and_says_so(
    api: JobsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    total = targets(api)
    assert total > 10
    fail_after(monkeypatch, 12)
    api.handle_api("POST", "/api/rescore", {}, {})
    wait(api)
    state = status(api)["state"]
    assert state["phase"] == "FAILED"
    assert state["total"] == total
    # Two pages of five were committed before the eleventh-and-later page broke.
    assert state["done"] == 10
    assert "synthetic failure" in (state["error"] or "")
    # Saved, and only the rest is asked of the next pass.
    assert targets(api) == total - 10
    monkeypatch.undo()
    # A return to the page, or a restart of the app, reads the same account.
    restarted = JobsApi(
        ServerConfig(db_path=api.config.db_path, config_dir=api.config.config_dir), quiet=True
    )
    assert status(restarted)["state"]["phase"] == "FAILED"
    assert status(restarted)["state"]["done"] == 10
    # Home counts what is left from saved scores, not what was planned.
    assert outdated_on_home(restarted) == total - 10
    restarted.handle_api("POST", "/api/rescore", {}, {})
    wait(restarted)
    after = status(restarted)
    assert after["state"]["phase"] == "DONE"
    assert after["state"]["done"] == after["state"]["total"] == total - 10
    assert outdated_on_home(restarted) == 0
    assert after["rate"] is None, "too few postings to measure a rate from"


def _dead_run(app: JobsApi, *, heartbeat_age: timedelta, done: int = 7, total: int = 21) -> None:
    conn = connect(app.config.db_path)
    try:
        with transaction(conn):
            run_id = PipelineRunRepo(conn).start("rescore")
            beat = _stamp(datetime.now(UTC) - heartbeat_age)
            conn.execute(
                "UPDATE pipeline_run SET started_at = ?, stats_json = ? WHERE id = ?",
                (
                    beat,
                    json.dumps({"jobs_scored": done, "jobs_targeted": total, "heartbeat_at": beat}),
                    run_id,
                ),
            )
    finally:
        conn.close()


def test_a_run_that_stopped_with_the_app_reads_as_interrupted_with_its_count(
    api: JobsApi,
) -> None:
    _dead_run(api, heartbeat_age=timedelta(minutes=30))
    state = status(api)["state"]
    assert state["phase"] == "INTERRUPTED"
    assert (state["done"], state["total"]) == (7, 21)
    assert status(api)["running"] is False
    # Continuing is allowed, and closes the dead run with its reason.
    api.handle_api("POST", "/api/rescore", {}, {})
    wait(api)
    assert status(api)["state"]["phase"] == "DONE"


def test_a_run_another_process_is_writing_is_followed_and_never_started_twice(
    api: JobsApi,
) -> None:
    _dead_run(api, heartbeat_age=timedelta(seconds=5))
    answer = status(api)
    assert answer["state"]["phase"] == "RUNNING_ELSEWHERE"
    assert answer["running"] is True, "the page must follow it, not offer a second one"
    with pytest.raises(ApiError) as refused:
        api.handle_api("POST", "/api/rescore", {}, {})
    assert refused.value.status == 409


def test_a_second_start_in_the_same_app_is_refused_and_home_counts_from_progress(
    api: JobsApi,
) -> None:
    release = threading.Event()

    def held(state, cancel) -> None:
        state.boards_total = 40
        state.boards_done = 15
        release.wait(30)

    api.rescore.start(held, "held-run")
    try:
        answer = status(api)
        assert answer["state"]["phase"] == "RUNNING"
        assert (answer["state"]["done"], answer["state"]["total"]) == (15, 40)
        assert answer["scored"] is None, "no count of a table being written, per poll"
        with pytest.raises(ApiError) as refused:
            api.handle_api("POST", "/api/rescore", {}, {})
        assert refused.value.status == 409
        # Home does not plan against the tables being written: what is left is
        # the run's total minus what it has committed.
        assert outdated_on_home(api) == 25
    finally:
        release.set()
        wait(api)


def test_the_automatic_update_waits_while_scores_are_being_written(api: JobsApi) -> None:
    api.handle_api("PATCH", "/api/auto-update", {}, {"enabled": True})
    release = threading.Event()
    api.rescore.start(lambda state, cancel: release.wait(30), "held-run")
    try:
        answer = api.handle_api("POST", "/api/auto-update/run", {}, {})
        assert answer["started"] is False
        assert answer["reason"] in {"BUSY", "NOTHING_DUE"}
        assert not api.retrieval.running, "a collection started during a recalculation"
    finally:
        release.set()
        wait(api)


def test_a_measured_rate_is_offered_only_from_a_large_finished_run(api: JobsApi) -> None:
    conn = connect(api.config.db_path)
    try:
        with transaction(conn):
            repo = PipelineRunRepo(conn)
            small = repo.start("rescore")
            repo.finish(small, PipelineRunStatus.OK, {"jobs_scored": 300, "elapsed_ms": 9000})
        assert status(api)["rate"] is None
        with transaction(conn):
            big = repo.start("rescore")
            repo.finish(big, PipelineRunStatus.OK, {"jobs_scored": 171319, "elapsed_ms": 5_162_933})
    finally:
        conn.close()
    rate = status(api)["rate"]
    assert rate["jobs"] == 171319 and rate["seconds"] == 5163
    assert rate["seconds_per_job"] == pytest.approx(5162.933 / 171319)

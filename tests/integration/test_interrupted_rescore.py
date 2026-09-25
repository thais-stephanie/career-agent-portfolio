"""A recalculation killed part way is said to have stopped, never shown as moving.

WHAT HAPPENED, ON THE OWNER'S OWN MACHINE (2026-09-25)
------------------------------------------------------
A preference change asked for 122,876 postings to be scored again. The server
was killed when the machine ran out of memory, after 74,000. The database kept
exactly what it should: 74,000 scores for the new revision, the previous
complete revision untouched, and a `pipeline_run` row still marked RUNNING
whose last heartbeat was the moment the process died.

The screen then said "Recalculating: 74,000 of 122,876 (60%)" for ever, even
after a restart, with no way to continue: a partly scored revision was read as
one being scored, and the Recalculate button is only offered when nothing is.

These tests rebuild that exact state and pin the terminal behaviour: stopped
and resumable, and a new pass closes the dead run as FAILED with its reason.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import tempfile
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support import REAL_CONFIG_DIR

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import INTERRUPTED, rescore_in_progress
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import PipelineRunRepo
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api() -> Iterator[JobsApi]:
    config = Path(tempfile.mkdtemp(prefix="interrupted-rescore")) / "config"
    shutil.copytree(REAL_CONFIG_DIR, config)
    for stray in [*config.glob("*.local.yaml"), *config.glob("*.local.yaml.*")]:
        stray.unlink()
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="interrupted-rescore")) / "demo.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        loaded, _ = load_search_config(config)
        seed_demo(conn, loaded, source=DEMO_FILE)
    finally:
        conn.close()
    yield JobsApi(ServerConfig(db_path=db_path, config_dir=config, port=0), quiet=True)


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def killed_part_way(api: JobsApi, *, heartbeat_age: timedelta) -> tuple[int, int]:
    """The owner's database after the kill: the previous revision complete,
    the current one partly scored, and a RUNNING row that stopped beating."""
    config_id, version = api._identity()
    conn = connect(api.config.db_path)
    try:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(job_match)") if r[1] != "id"]
        listed = ", ".join(columns)
        chosen = ", ".join("config_version" if c == "config_version" else c for c in columns)
        with transaction(conn):
            # Everything the seed scored now answers the PREVIOUS preferences.
            conn.execute(
                "UPDATE job_match SET config_version = ? WHERE config_id = ? AND config_version = ?",
                (version - 1, config_id, version),
            )
            total = conn.execute(
                "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?",
                (config_id, version - 1),
            ).fetchone()[0]
            done = max(1, total * 6 // 10)
            # And the new pass got part of the way through.
            conn.execute(
                f"INSERT INTO job_match (id, {listed}) SELECT id || '-new', "
                f"{chosen.replace('config_version', '?')} FROM job_match"
                " WHERE config_id = ? AND config_version = ? ORDER BY job_id LIMIT ?",
                (version, config_id, version - 1, done),
            )
            run_id = PipelineRunRepo(conn).start("rescore")
            beat = _stamp(datetime.now(UTC) - heartbeat_age)
            conn.execute(
                "UPDATE pipeline_run SET started_at = ?, stats_json = ? WHERE id = ?",
                (beat, json.dumps({"jobs_scored": done, "heartbeat_at": beat}), run_id),
            )
    finally:
        conn.close()
    return done, total


def revision(api: JobsApi) -> dict:
    return api.handle_api("GET", "/api/jobs", {}, {})["revision"]


def test_a_pass_killed_part_way_reads_as_stopped_not_as_recalculating(api: JobsApi) -> None:
    done, total = killed_part_way(api, heartbeat_age=timedelta(minutes=20))
    state = revision(api)
    assert state["current"]["scored"] == done < total
    assert state["is_building"] is False, "a dead pass was shown as still recalculating"
    assert state["is_interrupted"] is True
    # The list still answers the previous, complete preferences.
    assert state["serving"]["config_version"] == state["current"]["config_version"] - 1


def test_a_pass_that_is_still_beating_reads_as_recalculating(api: JobsApi) -> None:
    killed_part_way(api, heartbeat_age=timedelta(seconds=10))
    state = revision(api)
    assert state["is_building"] is True
    assert state["is_interrupted"] is False


def test_continuing_finishes_the_revision_and_closes_the_dead_run(api: JobsApi) -> None:
    killed_part_way(api, heartbeat_age=timedelta(minutes=20))
    api.handle_api("POST", "/api/rescore", {}, {})
    deadline = time.monotonic() + 120
    while api.rescore.running and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not api.rescore.running
    state = revision(api)
    assert state["is_current"] and not state["is_building"] and not state["is_interrupted"]
    conn = connect(api.config.db_path)
    try:
        rows = conn.execute(
            "SELECT status, error FROM pipeline_run WHERE stage = 'rescore' ORDER BY started_at"
        ).fetchall()
        assert not rescore_in_progress(conn)
    finally:
        conn.close()
    statuses = [(r["status"], r["error"]) for r in rows]
    assert ("FAILED", INTERRUPTED) in statuses, statuses
    assert statuses[-1][0] == "OK", statuses
    assert not [s for s in statuses if s[0] == "RUNNING"], "a RUNNING row was left behind"


def test_every_heartbeat_says_when_it_beat(api: JobsApi) -> None:
    conn = connect(api.config.db_path)
    try:
        with transaction(conn):
            repo = PipelineRunRepo(conn)
            run_id = repo.start("rescore")
            repo.progress(run_id, {"jobs_scored": 5})
        stats = json.loads(repo.get(run_id)["stats_json"])
        assert stats["jobs_scored"] == 5 and stats["heartbeat_at"]
        assert rescore_in_progress(conn)
    finally:
        conn.close()

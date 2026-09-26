"""Migration 0044: "To apply" merged into "Interested", nothing else touched.

A database is built at migration 0043, given synthetic applications standing
at TO_APPLY with notes, a bookmark, dates and history, and then migrated. The
applications move to SHORTLISTED; the job, the row, the notes, the bookmark,
every date and every old history event stay exactly as they were; one event
per moved application records the merge. Then the retired word is still
understood where an old bookmark or client might send it.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.application import (
    ApplicationStatus,
    canonical_status,
    moved_forward,
)
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import MIGRATIONS_DIR, connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def before_0044(tmp_path: Path):  # noqa: ANN201
    """A seeded database at 0043 with two TO_APPLY applications and one
    SHORTLISTED control."""
    older = tmp_path / "migrations"
    older.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) <= 43:
            shutil.copy(path, older / path.name)
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn, older)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "merge")
    search, _ = load_search_config(config)
    seed_demo(conn, search, source=DEMO)
    jobs = [str(r[0]) for r in conn.execute("SELECT id FROM job ORDER BY id LIMIT 3")]
    rows = [
        ("app-a", jobs[0], "TO_APPLY", None, 1, "synthetic note A", "2026-09-01T10:00:00Z"),
        ("app-b", jobs[1], "TO_APPLY", "2026-09-02", 0, None, "2026-09-02T11:00:00Z"),
        ("app-c", jobs[2], "SHORTLISTED", None, 0, "control", "2026-09-03T12:00:00Z"),
    ]
    with transaction(conn):
        for app_id, job, status, applied, saved, notes, updated in rows:
            conn.execute(
                "INSERT INTO job_application"
                " (id, job_id, status, applied_at, saved, notes, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, '2026-08-30T09:00:00Z', ?)",
                (app_id, job, status, applied, saved, notes, updated),
            )
            conn.execute(
                "INSERT INTO job_application_event"
                " (id, job_id, from_status, to_status, applied_at, note, occurred_at)"
                " VALUES (?, ?, 'SHORTLISTED', ?, ?, NULL, ?)",
                (f"ev-{app_id}", job, status, applied, updated),
            )
    return conn, db, config, jobs


def _state(conn):  # noqa: ANN001, ANN202
    return {
        str(r["id"]): dict(r)
        for r in conn.execute("SELECT * FROM job_application ORDER BY id").fetchall()
    }


def test_to_apply_becomes_interested_and_nothing_else_moves(before_0044) -> None:
    conn, _, _, jobs = before_0044
    before = _state(conn)
    old_events = [dict(r) for r in conn.execute("SELECT * FROM job_application_event ORDER BY id")]

    applied = migrate(conn)
    # 0044 runs here; later migrations may follow it.
    assert [m.version for m in applied][0] == 44

    after = _state(conn)
    assert {k: v["status"] for k, v in after.items()} == {
        "app-a": "SHORTLISTED",
        "app-b": "SHORTLISTED",
        "app-c": "SHORTLISTED",
    }
    for app_id, row in before.items():
        moved = dict(row, status="SHORTLISTED")
        assert after[app_id] == moved, f"{app_id}: something besides the status changed"

    events = [dict(r) for r in conn.execute("SELECT * FROM job_application_event ORDER BY id")]
    assert [e for e in events if e["id"].startswith("ev-")] == old_events, "history was rewritten"
    merged = [e for e in events if e["id"].startswith("m0044-")]
    assert sorted(e["job_id"] for e in merged) == sorted([jobs[0], jobs[1]])
    for event in merged:
        assert (event["from_status"], event["to_status"]) == ("TO_APPLY", "SHORTLISTED")
        assert "Interested" in event["note"]
    by_job = {e["job_id"]: e for e in merged}
    assert by_job[jobs[0]]["occurred_at"] == before["app-a"]["updated_at"]
    assert by_job[jobs[1]]["applied_at"] == "2026-09-02", "the applied date travels"

    # Idempotent: a second run applies nothing and adds no event.
    assert migrate(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM job_application_event").fetchone()[0] == len(events)
    conn.close()


def test_the_retired_word_is_still_understood(before_0044) -> None:
    conn, db, config, jobs = before_0044
    migrate(conn)
    conn.close()
    api = JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)

    # An old bookmark filtering on TO_APPLY finds the Interested jobs.
    listed = api.handle_api("GET", "/api/jobs", {"status": ["TO_APPLY"]}, {})
    ids = {item["job_id"] for item in listed["items"]}
    assert {jobs[0], jobs[1], jobs[2]} <= ids

    # An old client setting TO_APPLY sets Interested.
    saved = api.handle_api("PATCH", f"/api/jobs/{jobs[0]}/status", {}, {"status": "TO_APPLY"})
    assert saved["application_status"] == "SHORTLISTED"

    # The history still reads, including the events that say TO_APPLY.
    detail = api.handle_api("GET", f"/api/jobs/{jobs[0]}", {}, {})
    assert any(e["to_status"] == "SHORTLISTED" for e in detail["history"])

    assert "TO_APPLY" not in {status.value for status in ApplicationStatus}
    assert canonical_status("TO_APPLY") == "SHORTLISTED"
    assert moved_forward("TO_APPLY", "APPLIED") is True
    assert moved_forward("TO_APPLY", "SHORTLISTED") is False, "the merge is not progress"


def test_the_integrity_check_accepts_a_migrated_database(before_0044) -> None:
    from career_agent.storage.integrity import check_unknown_application_status

    conn, *_ = before_0044
    migrate(conn)
    assert check_unknown_application_status(conn) is None
    conn.close()

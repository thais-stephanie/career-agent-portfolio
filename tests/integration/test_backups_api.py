"""Settings, Backup lists only what the app made, and says the policy."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.integration.test_semantic_api import build

from career_agent.runtime.mode import RuntimeMode
from career_agent.storage import auto_backup


def test_it_lists_the_automatic_copies_and_nothing_else(tmp_path):
    app = build(tmp_path, RuntimeMode.PERSONAL)
    data = app.config.db_path.parent
    (data / "backups").mkdir(parents=True, exist_ok=True)
    (data / "backups" / "pre-something-20261010.zip").write_bytes(b"an operator's copy")
    empty = app.handle_api("GET", "/api/backups", {}, {})
    assert empty["count"] == 0 and empty["entries"] == []
    assert empty["policy"] == {
        "keep_per_profile": 3,
        "max_total_bytes": 2 * 1024**3,
        "when": "before-upgrade",
    }
    config = app.config.config_dir
    entry = auto_backup.backup_before_upgrade(
        data,
        profile_id="p",
        profile_label="Synthetic",
        db=app.config.db_path,
        config_dir=config,
        pending=["60"],
        schema_now=59,
        now=datetime(2026, 10, 11, tzinfo=UTC),
    )
    assert entry
    shown = app.handle_api("GET", "/api/backups", {}, {})
    assert shown["count"] == 1 and shown["entries"][0]["created_at"] == "2026-10-11T00:00:00Z"
    assert shown["total_bytes"] == shown["entries"][0]["bytes"]
    assert "pre-something" not in str(shown), "files the app did not make are not listed"

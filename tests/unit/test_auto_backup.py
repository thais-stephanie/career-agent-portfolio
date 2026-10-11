"""The one automatic backup: before an upgrade, once per step, bounded, and
only ever touching what it made.

Synthetic databases only. Files an operator or the person put next to the
automatic folder (manual backups, exports, recovery copies) must survive.
"""

from __future__ import annotations

import sqlite3
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from career_agent.storage import auto_backup as ab


@pytest.fixture
def world(tmp_path: Path):
    data = tmp_path / "data"
    (data / "backups").mkdir(parents=True)
    db = data / "personal.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE job_application (job_id TEXT, notes TEXT, status TEXT, saved INT)")
    conn.execute("INSERT INTO job_application VALUES ('j1', 'a synthetic note', 'APPLIED', 1)")
    conn.commit()
    conn.close()
    config = tmp_path / "config"
    config.mkdir()
    (config / "search.local.yaml").write_text("config_version: 1\n", encoding="utf-8")
    (config / ".env").write_text("DEEPSEEK_API_KEY=synthetic-not-a-key\n", encoding="utf-8")
    return data, db, config


def make(
    world, *, step: str = "52", when: datetime | None = None, profile: str = "prof-a", now=None
):
    data, db, config = world
    return ab.backup_before_upgrade(
        data,
        profile_id=profile,
        profile_label="Synthetic",
        db=db,
        config_dir=config,
        pending=[step],
        schema_now=51,
        now=when or now,
    )


def test_a_verified_backup_is_listed_and_holds_no_credential(world):
    entry = make(world)
    assert entry and entry["verified"] and entry["bytes"] > 0 and len(entry["sha256"]) == 64
    archive = ab.folder(world[0]) / entry["file"]
    with zipfile.ZipFile(archive) as z:
        names = z.namelist()
    assert "personal.db" in names and "MANIFEST.json" in names
    assert not any(n.endswith(".env") for n in names), "a credential is never copied"
    shown = ab.listing(world[0])
    assert shown["count"] == 1 and shown["entries"][0]["schema_after"] == "52"


def test_an_equivalent_step_makes_no_second_copy(world):
    assert make(world)
    assert make(world) is None, "opening again, or retrying the same upgrade, copies nothing"
    assert ab.listing(world[0])["count"] == 1


def test_nothing_pending_or_an_empty_database_copies_nothing(world, tmp_path):
    data, db, config = world
    assert (
        ab.backup_before_upgrade(
            data,
            profile_id="p",
            profile_label="x",
            db=db,
            config_dir=config,
            pending=[],
            schema_now=51,
        )
        is None
    )
    assert (
        ab.backup_before_upgrade(
            data,
            profile_id="p",
            profile_label="x",
            db=tmp_path / "missing.db",
            config_dir=config,
            pending=["52"],
            schema_now=51,
        )
        is None
    )
    assert not ab.folder(data).exists() or ab.listing(data)["count"] == 0


def test_only_the_newest_three_per_profile_are_kept_and_nothing_else_is_touched(world):
    data, _db, _config = world
    protected = {
        data / "backups" / "pre-deep-panel-profile-20261010.zip": b"operator copy",
        data / "backups" / "career-agent-manual-20260901.zip": b"manual backup",
        data / "backups" / "auto" / "not-listed.zip": b"someone else's file in the folder",
    }
    (data / "backups" / "auto").mkdir(parents=True, exist_ok=True)
    for path, body in protected.items():
        path.write_bytes(body)
    start = datetime(2026, 10, 1, tzinfo=UTC)
    for n in range(5):
        assert make(world, step=str(52 + n), when=start + timedelta(days=n))
    shown = ab.listing(data)
    assert shown["count"] == 3
    assert [e["schema_after"] for e in shown["entries"]] == ["56", "55", "54"]
    for path, body in protected.items():
        assert path.read_bytes() == body, "a file the app did not make is never deleted"


def test_profiles_are_counted_apart(world):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    make(world, step="52", profile="prof-a", when=start)
    make(world, step="52", profile="prof-b", when=start + timedelta(days=1))
    assert ab.listing(world[0])["count"] == 2


def test_one_copy_larger_than_the_limit_is_still_made_and_kept(world, monkeypatch):
    monkeypatch.setattr(ab, "MAX_TOTAL_BYTES", 1)
    first = make(world, step="52", when=datetime(2026, 10, 1, tzinfo=UTC))
    assert first, "the limit never justifies skipping the protection"
    shown = ab.listing(world[0])
    assert shown["count"] == 1 and shown["over_limit"] is True
    second = make(world, step="53", when=datetime(2026, 10, 2, tzinfo=UTC))
    assert second
    shown = ab.listing(world[0])
    # The older one may go to make room; the newest verified one is never deleted.
    assert [e["schema_after"] for e in shown["entries"]] == ["53"]
    assert (ab.folder(world[0]) / second["file"]).is_file() and shown["over_limit"] is True


def test_the_limit_never_deletes_the_newest_verified_copy_of_any_profile(world, monkeypatch):
    start = datetime(2026, 10, 1, tzinfo=UTC)
    assert make(world, profile="prof-a", when=start)
    monkeypatch.setattr(ab, "MAX_TOTAL_BYTES", 1)
    assert make(world, profile="prof-b", when=start + timedelta(days=1))
    shown = ab.listing(world[0])
    assert {e["profile_id"] for e in shown["entries"]} == {"prof-a", "prof-b"}


def test_a_copy_that_does_not_verify_stops_the_upgrade_and_removes_nothing(world, monkeypatch):
    kept = make(world, step="52", when=datetime(2026, 10, 1, tzinfo=UTC))
    assert kept
    monkeypatch.setattr(ab, "_verified", lambda *_: False)
    with pytest.raises(ab.UpgradeBackupFailed) as stopped:
        make(world, step="53", when=datetime(2026, 10, 2, tzinfo=UTC))
    assert "nothing was changed" in str(stopped.value) and stopped.value.why == "NOT_VERIFIED"
    assert ab.read_manifest(world[0])["skipped"][-1]["why"] == "NOT_VERIFIED"
    assert (ab.folder(world[0]) / kept["file"]).is_file(), "the last valid copy is untouched"
    assert not list(ab.folder(world[0]).glob("*.partial")), "no half-written file is left"


def test_a_copy_that_cannot_be_written_stops_the_upgrade(world, monkeypatch):
    def full_disk(**_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("career_agent.storage.backup.create_backup", full_disk)
    with pytest.raises(ab.UpgradeBackupFailed) as stopped:
        make(world)
    assert stopped.value.why == "ERROR:OSError"
    assert ab.listing(world[0])["count"] == 0
    assert ab.read_manifest(world[0])["skipped"][-1]["why"] == "ERROR:OSError"


def test_only_the_explicit_recorded_override_lets_an_upgrade_go_without_a_copy(world, monkeypatch):
    monkeypatch.setattr(ab, "_verified", lambda *_: False)
    monkeypatch.setenv(ab.OVERRIDE, "1")
    assert make(world) is None
    assert ab.read_manifest(world[0])["skipped"][-1]["why"] == "OVERRIDDEN:NOT_VERIFIED"
    monkeypatch.setenv(ab.OVERRIDE, "yes")
    with pytest.raises(ab.UpgradeBackupFailed):
        make(world, step="54")

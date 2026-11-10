"""`export-jobs` and `rescore --no-migrate`: the two commands an unattended caller uses.

Every database here is synthetic: the committed demo postings, scored under the
committed worked-example search, in a temporary folder.
"""

from __future__ import annotations

import hashlib
import json
import time
import tracemalloc
from pathlib import Path

import pytest
from tests.support import career_profile
from typer.testing import CliRunner

from career_agent import jobs_export
from career_agent.cli import app
from career_agent.jobs_export import EXCLUDED_FIELDS, ExportRefused, export_jobs
from career_agent.runtime.maintenance_lock import maintenance_lock

runner = CliRunner()
#: Makes the newest migration pending again, on a synthetic database.
FORGET_LAST_MIGRATION = (
    "DELETE FROM schema_migration WHERE version = (SELECT MAX(version) FROM schema_migration)"
)


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)  # no registry here: the profile check has nothing to bind
    _, api = career_profile(tmp_path, "prof-export", "Synthetic")
    return api


def _db(api) -> Path:
    return Path(api.config.db_path)


def _config(api) -> Path:
    return Path(api.config.config_dir)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# -- the export -------------------------------------------------------------


def test_export_items_are_the_api_cards_without_private_fields(profile, tmp_path: Path) -> None:
    out = tmp_path / "out" / "jobs.json"
    summary = export_jobs(_db(profile), _config(profile), out, page_size=5)
    data = _read(out)
    screen = profile.handle_api(
        "GET", "/api/jobs", {"lane": ["shortlist"], "sort": ["deep"], "limit": ["500"]}, {}
    )
    assert data["format"] == "career-agent-jobs-export" and data["schema_version"] == 2
    assert data["database"]["profile_id"] is None  # no registry: an unprofiled install
    listed = [i for i in data["items"] if i["export_scope"] == "default_list"]
    assert [i["job_id"] for i in listed] == [i["job_id"] for i in screen["items"]]
    for exported, shown in zip(listed, screen["items"], strict=True):
        assert {k: v for k, v in exported.items() if k != "export_scope"} == {
            k: v for k, v in shown.items() if k not in EXCLUDED_FIELDS
        }
    assert not any(EXCLUDED_FIELDS & item.keys() for item in data["items"])
    completeness = data["completeness"]
    assert completeness["complete"] is True
    assert completeness["default_list_total"] == screen["total"] == len(listed)
    assert summary["completeness"] == completeness
    assert data["scope"]["include_unresolved"] is False


def test_tracked_jobs_are_exported_even_when_the_list_hides_them(profile, tmp_path: Path) -> None:
    def ids(**extra: list[str]) -> set[str]:
        query = {"lane": ["shortlist"], "limit": ["500"], **extra}
        return {i["job_id"] for i in profile.handle_api("GET", "/api/jobs", query, {})["items"]}

    listed = ids()
    # A posting that did not say where it hires: out of the default list.
    hidden = sorted(ids(include_unresolved=["true"]) - listed)[0]
    profile.handle_api("PATCH", f"/api/jobs/{hidden}/status", {}, {"status": "APPLIED"})
    profile.handle_api("PATCH", f"/api/jobs/{hidden}/notes", {}, {"notes": "Synthetic note"})
    assert hidden not in ids()
    out = tmp_path / "jobs.json"
    export_jobs(_db(profile), _config(profile), out)
    item = next(i for i in _read(out)["items"] if i["job_id"] == hidden)
    assert item["export_scope"] == "tracked" and item["application_status"] == "APPLIED"
    assert "Synthetic note" not in out.read_text(encoding="utf-8")


def test_export_never_changes_the_database(profile, tmp_path: Path) -> None:
    db = _db(profile)
    before = _digest(db)
    export_jobs(db, _config(profile), tmp_path / "jobs.json")
    assert _digest(db) == before
    # A reader of a WAL database may leave the -wal and -shm sidecars; nothing is in them.
    wal = db.with_name(db.name + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0


def test_pending_migrations_are_refused_and_nothing_is_migrated(profile, tmp_path: Path) -> None:
    import sqlite3

    db = _db(profile)
    with sqlite3.connect(db) as raw:
        raw.execute(FORGET_LAST_MIGRATION)
    before = _digest(db)
    with pytest.raises(ExportRefused) as refused:
        export_jobs(db, _config(profile), tmp_path / "jobs.json")
    assert refused.value.code == 2 and refused.value.reason == "MIGRATIONS_PENDING"
    assert _digest(db) == before
    assert not (tmp_path / "jobs.json").exists()


def test_a_demo_database_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.support import REPO_ROOT, committed_config_dir

    monkeypatch.chdir(tmp_path)
    db = tmp_path / "demo.db"
    source = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
    result = runner.invoke(
        app,
        ["seed-demo", "--db", str(db), "--config-dir", str(committed_config_dir())]
        + ["--source", str(source)],
    )
    assert result.exit_code == 0, result.output
    with pytest.raises(ExportRefused) as refused:
        export_jobs(db, committed_config_dir(), tmp_path / "jobs.json")
    assert refused.value.reason == "NOT_PERSONAL"


def test_an_interrupted_export_leaves_the_previous_file_and_no_partial(
    profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "jobs.json"
    out.write_text('{"previous": true}', encoding="utf-8")
    real = jobs_export._pages

    def broken(app, base, page_size):
        pages = real(app, base, page_size)
        yield next(pages)
        raise KeyboardInterrupt

    monkeypatch.setattr(jobs_export, "_pages", broken)
    with pytest.raises(KeyboardInterrupt):
        export_jobs(_db(profile), _config(profile), out, page_size=2)
    assert _read(out) == {"previous": True}
    assert [p.name for p in tmp_path.iterdir() if "partial" in p.name] == []


def test_a_list_that_moves_between_pages_is_refused(
    profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = jobs_export._pages

    def moving(app, base, page_size):
        for n, page in enumerate(real(app, base, page_size)):
            yield {**page, "freshness": page["freshness"] + ("x" if n else "")}

    monkeypatch.setattr(jobs_export, "_pages", moving)
    with pytest.raises(ExportRefused) as refused:
        export_jobs(_db(profile), _config(profile), tmp_path / "jobs.json", page_size=2)
    assert refused.value.code == 4 and refused.value.reason == "LIST_MOVED"
    assert not (tmp_path / "jobs.json").exists()


def test_large_export_streams_in_bounded_memory(
    profile, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """20,000 synthetic cards: memory follows the page, not the list."""
    total, card = 20_000, {"title": "Synthetic posting", "match_score": 50, "blob": "x" * 2000}
    revision = {"is_building": False, "serving": None, "is_current": True}

    def synthetic(app, base, page_size):
        if base["lane"] == ["all"]:
            yield {"items": [], "total": 0, "freshness": "f", "revision": revision}
            return
        for offset in range(0, total, page_size):
            items = [{**card, "job_id": f"job{n:06d}"} for n in range(offset, offset + page_size)]
            yield {"items": items, "total": total, "freshness": "f", "revision": revision}

    monkeypatch.setattr(jobs_export, "_pages", synthetic)
    out = tmp_path / "big.json"
    tracemalloc.start()
    started = time.perf_counter()
    summary = export_jobs(_db(profile), _config(profile), out, page_size=500)
    seconds = time.perf_counter() - started
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert summary["completeness"]["item_count"] == total
    assert len(_read(out)["items"]) == total
    # The file is ~42 MB; one page of cards is ~1 MB.
    assert out.stat().st_size > 40_000_000
    assert peak < 20_000_000, f"peak {peak / 1e6:.1f} MB"
    print(f"\n20,000 synthetic cards: {seconds:.1f} s, peak {peak / 1e6:.1f} MB")


def test_cli_export_reports_and_exits(profile, tmp_path: Path) -> None:
    out = tmp_path / "jobs.json"
    args = ["export-jobs", "--db", str(_db(profile)), "--config-dir", str(_config(profile))]
    result = runner.invoke(app, [*args, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "WRITTEN" and out.exists()
    missing = runner.invoke(
        app, ["export-jobs", "--db", str(tmp_path / "none.db"), "--out", str(tmp_path / "x.json")]
    )
    assert missing.exit_code == 2 and json.loads(missing.output)["reason"] == "NO_DATABASE"
    assert not (tmp_path / "none.db").exists()


# -- rescore --no-migrate ----------------------------------------------------


def _rescore(api, *extra: str):
    return runner.invoke(
        app,
        [
            "rescore",
            "--no-migrate",
            "--db",
            str(_db(api)),
            "--config-dir",
            str(_config(api)),
            *extra,
        ],
    )


def test_unattended_rescore_finishes_with_one_json_result(profile) -> None:
    result = _rescore(profile, "--dirty")
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "FINISHED" and payload["mode"] == "DIRTY"
    assert "jobs_scored" in payload["stats"] and "errors" in payload["stats"]


def test_unattended_rescore_refuses_pending_migrations(profile) -> None:
    import sqlite3

    db = _db(profile)
    with sqlite3.connect(db) as raw:
        raw.execute(FORGET_LAST_MIGRATION)
        ledger = raw.execute("SELECT COUNT(*) FROM schema_migration").fetchone()
    result = _rescore(profile)
    assert result.exit_code == 2, result.output
    assert json.loads(result.output)["reason"] == "MIGRATIONS_PENDING"
    with sqlite3.connect(db) as raw:
        assert raw.execute("SELECT COUNT(*) FROM schema_migration").fetchone() == ledger


def test_unattended_rescore_waits_for_nobody_while_maintenance_runs(profile) -> None:
    with maintenance_lock(_db(profile)):
        result = _rescore(profile)
    assert result.exit_code == 3, result.output
    assert json.loads(result.output) | {"message": ""} == {
        "status": "BUSY",
        "mode": "TARGETED",
        "reason": "MAINTENANCE_RUNNING",
        "message": "",
    }


def test_unattended_rescore_refuses_while_another_rescore_runs(
    profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("career_agent.pipeline.rescore.rescore_in_progress", lambda conn: True)
    result = _rescore(profile)
    assert result.exit_code == 3 and json.loads(result.output)["reason"] == "RESCORE_RUNNING"


def test_unattended_rescore_reports_an_interruption_as_such(
    profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    def stopped(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("career_agent.pipeline.rescore.rescore", stopped)
    result = _rescore(profile)
    assert result.exit_code == 130 and json.loads(result.output)["status"] == "INTERRUPTED"


def test_unattended_rescore_holds_the_lock_while_it_scores(
    profile, monkeypatch: pytest.MonkeyPatch
) -> None:
    from career_agent.runtime.maintenance_lock import maintenance_running

    seen: list[bool] = []

    def watching(conn, config, **kwargs):
        seen.append(maintenance_running(_db(profile)))
        from career_agent.pipeline.rescore import RescoreStats

        return RescoreStats()

    monkeypatch.setattr("career_agent.pipeline.rescore.rescore", watching)
    assert _rescore(profile).exit_code == 0
    assert seen == [True]

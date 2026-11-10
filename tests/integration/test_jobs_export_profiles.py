"""`export-jobs` says which local profile its rows belong to, and proves it.

Two synthetic profiles of one synthetic installation, laid out as the app lays them
out: the original one at `data/personal.db` + `config/`, a later one under
`data/profiles/<id>/`. Both hold the committed demo postings, scored under different
committed example searches, so their lists differ.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from tests.support import REPO_ROOT, committed_config_dir
from typer.testing import CliRunner

from career_agent import install_id
from career_agent.cli import app
from career_agent.jobs_export import ExportRefused, export_jobs
from career_agent.runtime.profiles import (
    bind_database,
    create_profile,
    ensure_registry,
    set_active,
)

runner = CliRunner()


def _seed(db: Path, config: Path, search: str, label: str | None) -> None:
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.demo_seed import seed_demo
    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.storage.db import connect, migrate, transaction

    shutil.copyfile(committed_config_dir() / search, config / "search.local.yaml")
    conn = connect(db)
    try:
        migrate(conn)
        if label:
            with transaction(conn):
                stamp_identity(conn, RuntimeMode.PERSONAL, label)
        seed_demo(
            conn,
            load_search_config(config)[0],
            source=REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml",
        )
    finally:
        conn.close()


def _bind(db: Path, profile_id: str) -> None:
    """What the app does when it first opens a profile."""
    from career_agent.storage.db import connect

    conn = connect(db)
    try:
        bind_database(conn, profile_id)
    finally:
        conn.close()


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CAREER_AGENT_DB", raising=False)
    shutil.copytree(
        committed_config_dir(), tmp_path / "config", ignore=shutil.ignore_patterns("*.local.*")
    )
    (tmp_path / "data").mkdir()
    _seed(tmp_path / "data" / "personal.db", tmp_path / "config", "search.worked-example.yaml", "A")
    a = ensure_registry(tmp_path).current
    _bind(tmp_path / "data" / "personal.db", a.id)
    b = create_profile(tmp_path, "Perfil B")
    _seed(tmp_path / b.db, tmp_path / b.config_dir, "search.legacy-example.yaml", None)
    return a, b


def _export(profile, out: Path) -> dict:
    export_jobs(Path(profile.db), Path(profile.config_dir), out)
    return json.loads(out.read_text(encoding="utf-8"))


def test_each_export_names_the_profile_its_database_belongs_to(install, tmp_path) -> None:
    a, b = install
    first, second = _export(a, tmp_path / "a.json"), _export(b, tmp_path / "b.json")
    assert first["schema_version"] == second["schema_version"] == 2
    assert first["database"]["profile_id"] == a.id and second["database"]["profile_id"] == b.id
    # Same program folder for both: install alone cannot tell two people apart.
    assert first["career_agent"]["install"] == second["career_agent"]["install"] == install_id()
    ids = [{i["job_id"] for i in d["items"]} for d in (first, second)]
    assert ids[0] and ids[1]
    assert "Perfil B" not in (tmp_path / "b.json").read_text(encoding="utf-8")


def test_the_id_comes_from_the_database_not_the_active_profile(install, tmp_path) -> None:
    a, b = install
    set_active(tmp_path, b.id)
    assert _export(a, tmp_path / "a.json")["database"]["profile_id"] == a.id
    set_active(tmp_path, a.id)
    assert _export(b, tmp_path / "b.json")["database"]["profile_id"] == b.id


def test_the_default_command_follows_the_original_profile_or_refuses(install, tmp_path) -> None:
    a, b = install
    out = tmp_path / "out.json"
    result = runner.invoke(app, ["export-jobs", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert json.loads(out.read_text(encoding="utf-8"))["database"]["profile_id"] == a.id
    out.unlink()
    set_active(tmp_path, b.id)
    # Without --db, a later profile active in the app is never exported as the original.
    result = runner.invoke(app, ["export-jobs", "--out", str(out)])
    assert result.exit_code == 2 and not out.exists()


def test_another_profiles_settings_are_refused(install, tmp_path) -> None:
    _, b = install
    with pytest.raises(ExportRefused) as refused:
        export_jobs(Path(b.db), Path("config"), tmp_path / "x.json")
    assert refused.value.reason == "WRONG_PROFILE_SETTINGS"
    assert not (tmp_path / "x.json").exists()


def test_a_copy_of_a_profiles_database_is_refused(install, tmp_path) -> None:
    _, b = install
    copy = tmp_path / "copy.db"
    shutil.copyfile(b.db, copy)
    with pytest.raises(ExportRefused) as refused:
        export_jobs(copy, Path(b.config_dir), tmp_path / "x.json")
    assert refused.value.reason == "WRONG_PROFILE_DATABASE"


def test_an_unstamped_database_is_refused_once_profiles_exist(install, tmp_path) -> None:
    from career_agent.storage.db import connect

    a, _ = install
    conn = connect(Path(a.db))
    with conn:
        conn.execute("UPDATE database_identity SET profile_id = NULL")
    conn.close()
    with pytest.raises(ExportRefused) as refused:
        export_jobs(Path(a.db), Path(a.config_dir), tmp_path / "x.json")
    assert refused.value.reason == "PROFILE_UNBOUND"


def test_a_profile_missing_from_the_registry_is_refused(install, tmp_path) -> None:
    a, _ = install
    registry = tmp_path / "data" / "profiles.json"
    data = json.loads(registry.read_text(encoding="utf-8"))
    data["profiles"] = [p for p in data["profiles"] if p["id"] != a.id]
    data["active"] = data["profiles"][0]["id"]
    registry.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ExportRefused) as refused:
        export_jobs(Path(a.db), Path(a.config_dir), tmp_path / "x.json")
    assert refused.value.reason == "PROFILE_UNKNOWN"


def test_a_stamped_database_without_its_registry_is_refused(install, tmp_path) -> None:
    a, _ = install
    (tmp_path / "data" / "profiles.json").unlink()
    with pytest.raises(ExportRefused) as refused:
        export_jobs(Path(a.db), Path(a.config_dir), tmp_path / "x.json")
    assert refused.value.reason == "PROFILE_UNVERIFIED"


def test_a_broken_registry_is_refused(install, tmp_path) -> None:
    a, _ = install
    (tmp_path / "data" / "profiles.json").write_text("{", encoding="utf-8")
    with pytest.raises(ExportRefused) as refused:
        export_jobs(Path(a.db), Path(a.config_dir), tmp_path / "x.json")
    assert refused.value.reason == "PROFILE_REGISTRY"

"""Local profiles: two people, one installation, nothing crossing.

Adversarial and synthetic throughout. Profile A is an installation's original
workspace, adopted in place; profile B is created empty through the API. A's
CV, confirmed evidence, role anchors, saved job, note, source choice and
Resume Tailor candidate must never be visible from B, and B's must never
reach A. Everything lives under a temporary folder; no real profile is read.
"""

from __future__ import annotations

import base64
import shutil
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.runtime.profiles import (
    ProfileError,
    ProfileMismatch,
    bind_database,
    bound_profile,
    ensure_registry,
    load_registry,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.profiles import ProfileHost, SwitchableApp, tailor_environment
from career_agent.web.server import ApiError

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
TAILOR = "http://127.0.0.1:8766"

CV_A = """Robin Example
Operations Analyst

Experience
Automated the monthly invoicing workflow for the finance team
Built the weekly revenue dashboard used by the leadership team

Skills
Process mapping, spreadsheet modelling

Tools
HubSpot, SQL
"""


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    root = tmp_path / "install"
    config = root / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = root / "data" / "personal.db"
    db.parent.mkdir(parents=True)
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "original")
        from career_agent.pipeline.demo_seed import seed_demo

        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO_FILE)
    finally:
        conn.close()
    monkeypatch.setenv("RESUME_TAILOR_HOME", str(root / "unused"))
    registry = ensure_registry(root)
    first = registry.current
    tailor_environment(root, first)
    from resume_tailor.api.app import create_app

    host = ProfileHost(root, port=0, tailor=SwitchableApp(create_app()), tailor_factory=create_app)
    api = host.open(first)
    host.server = types.SimpleNamespace(RequestHandlerClass=type("H", (), {"app": api}))
    return host


def app(host: ProfileHost):
    api = host.current()
    assert api is not None
    return api


def tailor(host: ProfileHost) -> TestClient:
    return TestClient(host.tailor, base_url=TAILOR)


def fill_profile_a(host: ProfileHost) -> dict:
    api = app(host)
    review = api.handle_api(
        "POST",
        "/api/cv/import",
        {},
        {"filename": "robin.txt", "content_base64": base64.b64encode(CV_A.encode()).decode()},
    )
    target = next(
        p for g in review["groups"] for p in g["proposals"] if "invoicing" in p["text"].lower()
    )
    api.handle_api(
        "POST",
        f"/api/cv/imports/{review['import_id']}/decide",
        {},
        {"claim_key": target["claim_key"], "decision": "ACCEPTED"},
    )
    api.handle_api("PATCH", "/api/role-anchors", {}, {"anchors": [{"text": "Revenue Analyst"}]})
    job = api.handle_api("GET", "/api/jobs", {"limit": ["1"]}, {})["items"][0]["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job}/saved", {}, {"saved": True})
    api.handle_api("PATCH", f"/api/jobs/{job}/notes", {}, {"notes": "Profile A note"})
    api.handle_api("PATCH", "/api/sources/schedule", {}, {"source_id": "gupy", "mode": "PAUSED"})
    created = tailor(host).post("/api/candidates", json={"name": "Synthetic A"})
    assert created.status_code == 200, created.text
    return {"job": job}


def snapshot(host: ProfileHost) -> dict:
    """Everything a person could see of their own data."""
    api = app(host)
    evidence = api.handle_api("GET", "/api/evidence", {}, {})
    anchors = api.handle_api("GET", "/api/role-anchors", {}, {})
    jobs = api.handle_api("GET", "/api/jobs", {"limit": ["200"]}, {})["items"]
    sources = api.handle_api("GET", "/api/sources", {}, {})["sources"]
    return {
        "confirmed": evidence["confirmed"],
        "claims": sorted(c["text"] for c in evidence["claims"]),
        "anchors": [a["text"] for a in anchors["anchors"]],
        "saved": sorted(j["job_id"] for j in jobs if j.get("saved")),
        "noted": sorted(j["job_id"] for j in jobs if j.get("notes")),
        "modes": {s["id"]: s["refresh_mode"] for s in sources if s["refresh_mode"] != "AUTO"},
        "tailor": sorted(c["name"] for c in tailor(host).get("/api/candidates").json()),
        "search_file": (Path(api.config.config_dir) / "search.local.yaml").exists(),
    }


def test_profile_b_sees_nothing_of_a_and_a_nothing_of_b(install: ProfileHost) -> None:
    host = install
    first = load_registry(host.root).current
    fill_profile_a(host)
    a_before = snapshot(host)
    assert a_before["confirmed"] == 1 and a_before["anchors"] == ["Revenue Analyst"]
    assert a_before["saved"] and a_before["noted"] and a_before["modes"] == {"gupy": "PAUSED"}
    assert a_before["tailor"] == ["Synthetic A"] and a_before["search_file"]

    created = app(host).handle_api("POST", "/api/profiles", {}, {"label": "Synthetic B"})
    second = created["created"]["id"]
    assert load_registry(host.root).active == first.id, "creating a profile never switches"
    result = app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": second})
    assert result["reload"] is True

    b = snapshot(host)
    assert b == {
        "confirmed": 0,
        "claims": [],
        "anchors": [],
        "saved": [],
        "noted": [],
        "modes": {},
        "tailor": [],
        "search_file": False,
    }
    assert app(host).handle_api("GET", "/api/profiles", {}, {})["active"]["label"] == "Synthetic B"

    # B's own data.
    app(host).handle_api("PATCH", "/api/role-anchors", {}, {"anchors": [{"text": "Nurse"}]})
    assert tailor(host).post("/api/candidates", json={"name": "Synthetic B"}).status_code == 200

    app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": first.id})
    assert snapshot(host) == a_before, "profile A changed while B was in use"

    app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": second})
    after = snapshot(host)
    assert after["anchors"] == ["Nurse"] and after["tailor"] == ["Synthetic B"]


def test_a_database_answers_only_to_its_own_profile(install: ProfileHost) -> None:
    host = install
    registry = load_registry(host.root)
    first = registry.current
    db = host.root / first.db
    conn = connect(db)
    try:
        assert bound_profile(conn) == first.id
        with pytest.raises(ProfileMismatch):
            bind_database(conn, "prof-01AAAAAAAAAAAAAAAAAAAAAAAA")
    finally:
        conn.close()


def test_switching_is_refused_while_the_current_profile_is_writing(
    install: ProfileHost, monkeypatch: pytest.MonkeyPatch
) -> None:
    host = install
    second = app(host).handle_api("POST", "/api/profiles", {}, {"label": "Other"})["created"]["id"]
    monkeypatch.setattr(type(app(host).retrieval), "running", property(lambda self: True))
    with pytest.raises(ApiError) as caught:
        app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": second})
    assert caught.value.status == 409
    assert load_registry(host.root).active != second


def test_rapid_switches_and_a_restart_keep_every_profile_whole(install: ProfileHost) -> None:
    host = install
    first = load_registry(host.root).current.id
    fill_profile_a(host)
    a_before = snapshot(host)
    second = app(host).handle_api("POST", "/api/profiles", {}, {"label": "B"})["created"]["id"]
    for target in (second, first, second, first, second, first):
        app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": target})
    assert snapshot(host) == a_before

    # A "restart": a new host reads the registry and opens the active profile.
    from resume_tailor.api.app import create_app

    app(host).handle_api("POST", "/api/profiles/switch", {}, {"profile_id": second})
    reopened = ProfileHost(
        host.root, port=0, tailor=SwitchableApp(create_app()), tailor_factory=create_app
    )
    active = load_registry(host.root).current
    assert active.id == second
    tailor_environment(host.root, active)
    reopened.tailor.inner = create_app()
    reopened.server = types.SimpleNamespace(
        RequestHandlerClass=type("H", (), {"app": reopened.open(active)})
    )
    assert snapshot(reopened)["anchors"] == []


def test_rename_and_delete_are_deliberate(install: ProfileHost) -> None:
    host = install
    api = app(host)
    first = load_registry(host.root).current
    second = api.handle_api("POST", "/api/profiles", {}, {"label": "Temp"})["created"]["id"]
    with pytest.raises(ApiError):
        api.handle_api("POST", "/api/profiles", {}, {"label": "temp"})  # same name
    api.handle_api("PATCH", f"/api/profiles/{second}", {}, {"label": "Temporary"})

    for target, confirm in ((second, "Temp"), (first.id, first.label)):
        with pytest.raises(ApiError):
            api.handle_api("POST", f"/api/profiles/{target}/delete", {}, {"confirm_label": confirm})

    folder = (host.root / load_registry(host.root).get(second).db).parent
    api.handle_api("POST", f"/api/profiles/{second}/delete", {}, {"confirm_label": "Temporary"})
    assert not folder.exists()
    assert list((host.root / "data" / "profiles" / ".trash").iterdir()), "moved, not erased"
    assert (host.root / first.db).exists(), "the original workspace is untouched"
    with pytest.raises(ProfileError):
        load_registry(host.root).get(second)


def test_without_the_launcher_profiles_are_unavailable(tmp_path: Path) -> None:
    from career_agent.web.api import JobsApi
    from career_agent.web.server import ServerConfig

    db = tmp_path / "x.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    plain = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir()), quiet=True)
    assert plain.handle_api("GET", "/api/profiles", {}, {}) == {
        "enabled": False,
        "active": None,
        "profiles": [],
    }
    with pytest.raises(ApiError) as caught:
        plain.handle_api("POST", "/api/profiles", {}, {"label": "x"})
    assert caught.value.status == 409


def test_the_first_profile_adopts_the_workspace_without_moving_it(tmp_path: Path) -> None:
    root = tmp_path / "fresh"
    (root / "config").mkdir(parents=True)
    registry = ensure_registry(root)
    first = registry.current
    assert (first.db, first.config_dir, first.tailor_home) == (
        "data/personal.db",
        "config",
        "data/tailor-personal",
    )
    assert first.label == "My profile" and first.legacy
    assert ensure_registry(root).current.id == first.id, "adoption happens once"

"""Settings & Sources -> AI & Semantic Matching, through the HTTP handlers.

Nothing here reaches a provider: every availability check is replaced. What
is asserted is what the routes accept, what they write, and that no response
ever carries a credential.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from career_agent.runtime.mode import RuntimeMode, stamp_identity
from career_agent.semantic import routing
from career_agent.semantic.providers import Availability, ProviderStatus
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

KEY = "sk-" + "9f8e7d6c" * 4


def build(tmp_path: Path, kind: RuntimeMode | None) -> JobsApi:
    config = tmp_path / "config"
    source = Path(__file__).resolve().parents[2] / "config"
    shutil.copytree(source, config, ignore=shutil.ignore_patterns("*.local.*"))
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn)
    if kind is not None:
        with transaction(conn):
            stamp_identity(conn, kind, "semantic-test")
    conn.close()
    return JobsApi(ServerConfig(db_path=tmp_path / "db.sqlite", config_dir=config), quiet=True)


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(
        routing,
        "provider_status",
        lambda provider_id, *, fresh=False: ProviderStatus(Availability.NOT_INSTALLED, "absent"),
    )
    routing.forget_status()
    return build(tmp_path, RuntimeMode.PERSONAL)


def test_the_status_says_what_is_used_and_why(api) -> None:
    status = api.handle_api("GET", "/api/semantic", {}, {})
    assert status["settings"]["mode"] == "auto"
    assert status["active_provider"] is None
    assert [p["id"] for p in status["providers"]] == ["deepseek", "codex", "claude_code", "laya"]
    assert status["deepseek_key"] == {"configured": False}
    assert status["readiness"]["state"] in {"NOT_READY", "PARTIAL", "READY"}
    assert "Career Profile" in " ".join(status["privacy"]["never_sends"])


def test_a_key_is_accepted_and_never_returned(api, monkeypatch) -> None:
    stored = api.handle_api("POST", "/api/semantic/deepseek-key", {}, {"key": KEY})
    assert stored == {"configured": True}
    status = api.handle_api("GET", "/api/semantic", {}, {})
    assert KEY not in json.dumps(status) and KEY[-6:] not in json.dumps(status)
    env = api.config.config_dir.parent / ".env"
    assert KEY in env.read_text(encoding="utf-8")
    with connect(api.config.db_path) as conn:
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for row in conn.execute(f'SELECT * FROM "{table}"'):  # noqa: S608 - test scan
                assert KEY not in json.dumps([str(v) for v in row]), table
    removed = api.handle_api("POST", "/api/semantic/deepseek-key/remove", {}, {})
    assert removed == {"configured": False}
    assert KEY not in env.read_text(encoding="utf-8")


@pytest.mark.parametrize("body", [{}, {"key": 5}, {"key": "not a key"}, {"key": KEY, "x": 1}])
def test_a_bad_key_request_writes_nothing(api, body) -> None:
    with pytest.raises(ApiError):
        api.handle_api("POST", "/api/semantic/deepseek-key", {}, body)
    assert not (api.config.config_dir.parent / ".env").exists()


def test_settings_are_validated_and_saved(api) -> None:
    saved = api.handle_api(
        "PATCH", "/api/semantic", {}, {"mode": "deterministic", "budget_per_run_usd": 0.1}
    )
    assert saved["settings"]["mode"] == "deterministic"
    assert saved["recalculation_required"] is True
    for bad in ({"mode": "gpt"}, {"budget_per_run_usd": 99}, {"secret": 1}, {}):
        with pytest.raises(ApiError):
            api.handle_api("PATCH", "/api/semantic", {}, bad)


def test_a_plan_with_no_provider_sends_nothing_and_says_so(api) -> None:
    plan = api.handle_api("POST", "/api/semantic/plan", {}, {})
    assert plan["provider"] is None and plan["expected_usd"] is None
    assert plan["reason"]


def test_demo_mode_never_uses_ai(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        routing,
        "provider_status",
        lambda provider_id, *, fresh=False: ProviderStatus(Availability.AVAILABLE, "ready"),
    )
    demo = build(tmp_path, RuntimeMode.DEMO)
    status = demo.handle_api("GET", "/api/semantic", {}, {})
    assert status["demo"] is True and status["active_provider"] is None
    for path in ("/api/semantic/run", "/api/semantic/plan"):
        with pytest.raises(ApiError) as caught:
            demo.handle_api("POST", path, {}, {})
        assert caught.value.status == 409

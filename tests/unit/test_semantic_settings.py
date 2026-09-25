"""Semantic settings, the routing policy, readiness and the one local secret."""

from __future__ import annotations

import copy

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig
from career_agent.match.readiness import Readiness, search_fit_readiness
from career_agent.semantic import routing
from career_agent.semantic.providers import Availability, ProviderStatus
from career_agent.semantic.settings import (
    Mode,
    SemanticSettings,
    SettingsError,
    env_path,
    key_state,
    load_settings,
    remove_key,
    save_settings,
    settings_path,
    store_key,
)
from career_agent.yaml_io import safe_load

KEY = "sk-" + "a1b2c3d4" * 4


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    directory = tmp_path / "config"
    directory.mkdir()
    return directory


# =========================================================================
# settings
# =========================================================================


def test_defaults_are_auto_and_bounded(config_dir) -> None:
    settings = load_settings(config_dir)
    assert settings.mode is Mode.AUTO and settings.enabled
    assert 0 < settings.budget_per_run_usd <= 5


def test_saving_round_trips_and_never_holds_a_credential(config_dir) -> None:
    save_settings(config_dir, {"mode": "claude_code", "budget_per_run_usd": 0.1})
    loaded = load_settings(config_dir)
    assert loaded.mode is Mode.CLAUDE_CODE and loaded.budget_per_run_usd == 0.1
    assert "sk-" not in settings_path(config_dir).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "change", [{"budget_per_run_usd": -1}, {"budget_per_run_usd": 50}, {"mode": "gpt"}]
)
def test_an_invalid_setting_is_refused(config_dir, change) -> None:
    with pytest.raises(SettingsError):
        save_settings(config_dir, change)


def test_an_unreadable_file_turns_semantic_matching_off(config_dir) -> None:
    settings_path(config_dir).write_text("enabled: [unclosed", encoding="utf-8")
    assert load_settings(config_dir).uses_findings is False


def test_deterministic_only_reads_no_findings() -> None:
    assert SemanticSettings(mode=Mode.DETERMINISTIC).uses_findings is False
    assert SemanticSettings(enabled=False).uses_findings is False


# =========================================================================
# the key
# =========================================================================


def test_a_key_is_stored_in_the_root_env_and_reported_only_as_configured(config_dir) -> None:
    env = env_path(config_dir)
    env.write_text("OTHER=1\nDEEPSEEK_API_KEY=sk-old000000000000000\n", encoding="utf-8")
    state = store_key(config_dir, KEY)
    assert state.configured
    text = env.read_text(encoding="utf-8")
    assert text.count("DEEPSEEK_API_KEY=") == 1 and KEY in text and "OTHER=1" in text
    assert KEY not in repr(state) and KEY not in state.source
    assert not list(env.parent.glob(".env.*")), "no backup copy of a secret"


def test_removing_the_key_removes_it(config_dir) -> None:
    store_key(config_dir, KEY)
    state = remove_key(config_dir)
    assert not state.configured
    assert "DEEPSEEK_API_KEY" not in env_path(config_dir).read_text(encoding="utf-8")
    assert not key_state(config_dir).configured


@pytest.mark.parametrize("bad", ["", "hello world", "sk-short", "Bearer sk-abc", KEY + "\nX=1"])
def test_something_that_is_not_a_key_is_never_written(config_dir, bad) -> None:
    with pytest.raises(SettingsError):
        store_key(config_dir, bad)
    assert not env_path(config_dir).exists()


# =========================================================================
# routing
# =========================================================================


def statuses(monkeypatch, **states: Availability) -> None:
    def fake(provider_id: str, *, fresh: bool = False) -> ProviderStatus:
        return ProviderStatus(states.get(provider_id, Availability.NOT_INSTALLED), provider_id)

    monkeypatch.setattr(routing, "provider_status", fake)


def test_auto_prefers_deepseek_when_it_is_available(monkeypatch) -> None:
    statuses(
        monkeypatch,
        deepseek=Availability.AVAILABLE,
        claude_code=Availability.AVAILABLE,
    )
    route = routing.resolve(SemanticSettings())
    assert route.provider is not None and route.provider.id == "deepseek"
    assert route.fallbacks == []


def test_auto_falls_back_and_says_so(monkeypatch) -> None:
    statuses(monkeypatch, codex=Availability.AVAILABLE)
    route = routing.resolve(SemanticSettings())
    assert route.provider is not None and route.provider.id == "codex"
    assert [f["preferred"] for f in route.fallbacks] == ["deepseek", "claude_code"]
    assert all(f["used"] == "codex" for f in route.fallbacks)


def test_auto_with_nothing_available_is_deterministic(monkeypatch) -> None:
    statuses(monkeypatch)
    route = routing.resolve(SemanticSettings())
    assert route.provider is None
    assert all(f["used"] == "deterministic" for f in route.fallbacks)


def test_a_chosen_provider_is_respected_and_never_swapped_for_another(monkeypatch) -> None:
    statuses(monkeypatch, deepseek=Availability.AVAILABLE, claude_code=Availability.LIMIT_OR_ERROR)
    route = routing.resolve(SemanticSettings(mode=Mode.CLAUDE_CODE))
    assert route.provider is None, "choosing Claude Code must never send postings to DeepSeek"
    assert route.fallbacks == [
        {"preferred": "claude_code", "reason": "claude_code", "used": "deterministic"}
    ]


def test_laya_is_not_in_the_auto_chain() -> None:
    assert "laya" not in routing.AUTO_ORDER


# =========================================================================
# readiness
# =========================================================================

BASE = safe_load((committed_config_dir() / "search.local.yaml").read_text(encoding="utf-8"))


def readiness_of(*, work: bool, level: bool, work_model: bool, tools: bool = True):
    data = copy.deepcopy(BASE)
    components = data["scoring"]["components"]
    if not work:
        components["responsibilities"]["weights"] = {}
    if not tools:
        components["technologies"]["weights"] = {}
    data["preferences"]["seniority"]["preferred"] = ["SENIOR"] if level else []
    remote = data["preferences"]["remote"]
    remote["accepted_work_models"] = ["REMOTE"] if work_model else []
    remote["avoided_work_models"] = []
    remote["excluded_work_models"] = []
    return search_fit_readiness(SearchConfig.model_validate(data))


def test_no_work_intent_is_not_ready() -> None:
    result = readiness_of(work=False, level=True, work_model=True)
    assert result.state is Readiness.NOT_READY and result.missing == ("work",)


def test_work_without_level_or_way_of_working_is_partial() -> None:
    result = readiness_of(work=True, level=False, work_model=False)
    assert result.state is Readiness.PARTIAL
    assert set(result.missing) == {"level", "work_model"}


def test_work_level_and_way_of_working_are_ready_without_any_tools() -> None:
    """A teacher or a nurse may state no tools. That is not missing setup."""
    result = readiness_of(work=True, level=True, work_model=True, tools=False)
    assert result.state is Readiness.READY and result.tool_phrases == 0


def test_a_saved_key_survives_a_restart_without_the_cli(config_dir, monkeypatch) -> None:
    """The launcher never loads `.env`; the key saved from Settings must still
    be there after the app starts again, and nothing else from `.env` is."""
    from career_agent.semantic.settings import load_stored_key

    env_path(config_dir).write_text(f"DEEPSEEK_API_KEY={KEY}\nOTHER_SECRET=x\n", encoding="utf-8")
    # Registered with monkeypatch first, so whatever load_stored_key writes
    # is undone after the test and never reaches the rest of the session.
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.delenv("OTHER_SECRET", raising=False)
    assert load_stored_key(config_dir) is True
    assert key_state(config_dir).configured
    import os

    assert "OTHER_SECRET" not in os.environ


def test_an_environment_key_wins_over_the_file(config_dir, monkeypatch) -> None:
    import os

    from career_agent.semantic.settings import load_stored_key

    env_path(config_dir).write_text(f"DEEPSEEK_API_KEY={KEY}\n", encoding="utf-8")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-" + "e" * 32)
    load_stored_key(config_dir)
    assert os.environ["DEEPSEEK_API_KEY"] == "sk-" + "e" * 32


def test_no_file_means_no_key(config_dir) -> None:
    from career_agent.semantic.settings import load_stored_key

    assert load_stored_key(config_dir) is False
    assert not key_state(config_dir).configured

"""Semantic providers: one contract, honest availability, no hidden billing.

No test here reaches a network or starts a real CLI. The subprocess boundary
and the HTTP client are replaced, and what is asserted is what Career Agent
SENDS and how it reads what comes back.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from career_agent.llm.client import Family, LLMRequest, ModelConfig, StructuredOutput
from career_agent.llm.vendors.openai_compatible import DEEPSEEK, build_payload
from career_agent.semantic.intent import Aspect, IntentItem, SearchIntent
from career_agent.semantic.providers import (
    PROVIDER_IDS,
    Availability,
    Billing,
    ProviderFailed,
    get_provider,
    local_cli,
)
from career_agent.semantic.providers.deepseek import DeepSeekProvider
from career_agent.semantic.providers.laya import LayaProvider

INTENT = SearchIntent(items=(IntentItem("W1", Aspect.WORK, "w", "Workflow automation"),))


def request() -> LLMRequest:
    return LLMRequest(family=Family.SEMANTIC, system="s json", user="u", schema={}, schema_name="x")


# =========================================================================
# DeepSeek
# =========================================================================


def test_deepseek_asks_for_thinking_off_by_default() -> None:
    config = ModelConfig(
        vendor=DEEPSEEK,
        identifier="deepseek-flash",
        structured_output=StructuredOutput.JSON_OBJECT,
    )
    payload = build_payload(request(), config)
    assert payload["extra_body"]["thinking"] == {"type": "disabled"}
    assert "reasoning_effort" not in payload
    assert payload["response_format"] == {"type": "json_object"}
    assert "max_tokens" in payload


def test_deepseek_thinking_is_explicit_when_asked_for() -> None:
    config = ModelConfig(
        vendor=DEEPSEEK,
        identifier="deepseek-flash",
        reasoning="low",
        structured_output=StructuredOutput.JSON_OBJECT,
    )
    payload = build_payload(request(), config)
    assert payload["extra_body"]["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "low"


def test_deepseek_without_a_key_is_key_missing_and_sends_nothing(monkeypatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    provider = DeepSeekProvider()
    assert provider.availability().state is Availability.KEY_MISSING
    with pytest.raises(ProviderFailed) as caught:
        provider.evaluate(INTENT, "t", "posting")
    assert caught.value.state is Availability.KEY_MISSING


def test_deepseek_is_metered_and_priced_at_the_recorded_peak_rate() -> None:
    provider = DeepSeekProvider()
    assert provider.capabilities().billing is Billing.METERED_API
    assert provider.estimate_cost(1_000_000, 0) == pytest.approx(0.30)
    assert provider.estimate_cost(0, 1_000_000) == pytest.approx(1.20)


def test_thinking_gets_room_to_think() -> None:
    assert (
        DeepSeekProvider(reasoning="low").max_output_tokens > DeepSeekProvider().max_output_tokens
    )


# =========================================================================
# the local CLIs
# =========================================================================


def completed(stdout: str = "", stderr: str = "", code: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr=stderr)


@pytest.mark.parametrize(
    "variable",
    [
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "CLAUDE_CODE_USE_BEDROCK",
        "ANTHROPIC_BASE_URL",
        "DEEPSEEK_API_KEY",
        "GOOGLE_API_KEY",
        "SOMETHING_NOBODY_LISTED",
    ],
)
def test_a_subscription_cli_receives_only_an_allowlisted_environment(
    monkeypatch, variable: str
) -> None:
    """Posting text is third-party input; a child that inherited this process's
    environment would be one prompt injection away from quoting a key back."""
    monkeypatch.setenv(variable, "should-not-pass")
    monkeypatch.setenv("PATH", "/usr/bin")
    env = local_cli._child_env()
    assert variable not in env
    assert env["PATH"] == "/usr/bin"


def test_claude_is_run_as_its_executable_never_through_a_batch_shim(tmp_path, monkeypatch) -> None:
    shim = tmp_path / "claude.cmd"
    shim.write_text("@echo off")
    exe = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(local_cli.shutil, "which", lambda name: str(shim))
    assert local_cli.find_claude() == str(exe)
    exe.unlink()
    assert local_cli.find_claude() is None, "a shim with no executable is not installed"


@pytest.mark.parametrize(
    ("status", "state"),
    [
        ({"loggedIn": True, "authMethod": "claude.ai"}, Availability.AVAILABLE),
        ({"loggedIn": False}, Availability.SIGN_IN_REQUIRED),
        ({"loggedIn": True, "authMethod": "api_key"}, Availability.SIGN_IN_REQUIRED),
    ],
)
def test_claude_availability_reads_the_sign_in_not_the_binary(monkeypatch, status, state) -> None:
    monkeypatch.setattr(local_cli, "find_claude", lambda: "claude")
    monkeypatch.setattr(local_cli, "_run", lambda *a, **k: completed(json.dumps(status)))
    assert local_cli.ClaudeCodeProvider().availability().state is state


def test_claude_not_installed(monkeypatch) -> None:
    monkeypatch.setattr(local_cli, "find_claude", lambda: None)
    assert local_cli.ClaudeCodeProvider().availability().state is Availability.NOT_INSTALLED


@pytest.mark.parametrize(
    ("said", "state"),
    [
        ("Logged in using ChatGPT", Availability.AVAILABLE),
        ("Logged in using an API key - sk-***", Availability.SIGN_IN_REQUIRED),
        ("Not logged in", Availability.SIGN_IN_REQUIRED),
    ],
)
def test_codex_availability_reads_the_sign_in(monkeypatch, said, state) -> None:
    monkeypatch.setattr(local_cli, "find_codex", lambda: "codex")
    monkeypatch.setattr(local_cli, "_run", lambda *a, **k: completed(said))
    assert local_cli.CodexProvider().availability().state is state


def test_claude_sends_the_prompt_on_stdin_with_no_tools_and_reads_the_structured_answer(
    monkeypatch,
) -> None:
    seen: dict = {}

    def fake_run(argv, stdin, timeout, cwd=None):
        seen["argv"], seen["stdin"], seen["cwd"] = argv, stdin, cwd
        body = {
            "is_error": False,
            "structured_output": {"work": {"verdict": "none", "matches": []}},
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 5, "output_tokens": 7},
            "modelUsage": {"claude-sonnet-x": {}},
        }
        return completed(json.dumps(body))

    monkeypatch.setattr(local_cli, "find_claude", lambda: "claude")
    monkeypatch.setattr(local_cli, "_run", fake_run)
    answer = local_cli.ClaudeCodeProvider().evaluate(INTENT, "Title", "The posting text")
    argv = seen["argv"]
    assert argv[argv.index("--tools") + 1] == ""
    assert "--strict-mcp-config" in argv and "--no-session-persistence" in argv
    assert "The posting text" in seen["stdin"] and "The posting text" not in " ".join(argv)
    assert Path(seen["cwd"]).name.startswith("ca-claude-")
    assert answer.model == "claude-sonnet-x" and answer.cost_usd is None
    assert answer.input_tokens == 15 and answer.output_tokens == 7


def test_a_plan_limit_is_reported_as_a_limit_not_as_an_answer(monkeypatch) -> None:
    body = {"is_error": True, "result": "Claude usage limit reached. Resets at 5pm."}
    monkeypatch.setattr(local_cli, "find_claude", lambda: "claude")
    monkeypatch.setattr(local_cli, "_run", lambda *a, **k: completed(json.dumps(body), code=1))
    with pytest.raises(ProviderFailed) as caught:
        local_cli.ClaudeCodeProvider().evaluate(INTENT, "t", "p")
    assert caught.value.state is Availability.LIMIT_OR_ERROR


def test_codex_reads_its_last_message_and_usage(monkeypatch) -> None:
    def fake_run(argv, stdin, timeout, cwd=None):
        out = Path(argv[argv.index("-o") + 1])
        out.write_text('{"work": {"verdict": "none", "matches": []}}', encoding="utf-8")
        assert "--sandbox" in argv and argv[argv.index("--sandbox") + 1] == "read-only"
        assert "--ephemeral" in argv and "--ignore-user-config" in argv
        disabled = {argv[i + 1] for i, arg in enumerate(argv) if arg == "--disable"}
        assert {"shell_tool", "unified_exec", "browser_use", "computer_use"} <= disabled
        events = [
            {"type": "thread.started"},
            {"type": "turn.completed", "usage": {"input_tokens": 100, "output_tokens": 9}},
        ]
        return completed("\n".join(json.dumps(e) for e in events))

    monkeypatch.setattr(local_cli, "find_codex", lambda: "codex")
    monkeypatch.setattr(local_cli, "_run", fake_run)
    answer = local_cli.CodexProvider().evaluate(INTENT, "t", "p")
    assert answer.input_tokens == 100 and answer.cost_usd is None
    assert "work" in answer.raw_text


def test_subscription_providers_never_claim_a_price() -> None:
    for provider_id in ("claude_code", "codex"):
        provider = get_provider(provider_id)
        assert provider.capabilities().billing is Billing.SUBSCRIPTION
        assert provider.estimate_cost(1000, 1000) is None


# =========================================================================
# Laya, and the registry
# =========================================================================


def test_laya_is_never_a_source_of_findings(monkeypatch) -> None:
    from career_agent.semantic.providers import laya

    provider = LayaProvider()
    assert provider.capabilities().quotes is False
    monkeypatch.setattr(laya, "installed", lambda: False)
    assert provider.availability().state is Availability.NOT_INSTALLED
    monkeypatch.setattr(laya, "installed", lambda: True)
    assert provider.availability().state is Availability.UNSUPPORTED
    with pytest.raises(ProviderFailed):
        provider.evaluate(INTENT, "t", "p")


def test_every_listed_provider_constructs_without_reaching_anything() -> None:
    for provider_id in PROVIDER_IDS:
        assert get_provider(provider_id).id == provider_id

"""Claude Code and Codex, through the person's OWN signed-in local CLI.

These are subscription providers. They spend the person's Claude or ChatGPT
plan limits, not an API key Career Agent holds, and Career Agent cannot know
the marginal price of a call, so it reports none.

SUBSCRIPTION IS NOT API BILLING
-------------------------------
Claude Code prefers `ANTHROPIC_API_KEY` over a claude.ai sign-in when both are
present, which would quietly move a subscription user onto API billing, and
Codex honours `CODEX_API_KEY` the same way. The child process receives an
allowlisted environment that contains none of them, and a CLI that is signed
in ONLY with an API key is reported as needing a subscription sign-in rather
than used. For Codex, "Logged in using ChatGPT" is the subscription; an
API-key login is not used silently.

WHAT THE CHILD CAN DO
---------------------
Nothing but answer. Claude Code runs with no tools, no MCP servers, no skills,
no settings files and no session persistence, in an empty temporary directory.
Codex runs ephemeral, read-only sandboxed, ignoring user config and rules, with
its shell, exec, browser, apps, plugins and web search disabled, in an empty
temporary directory. Both receive an ALLOWLISTED environment. The prompt
arrives on stdin, so a long posting never meets a command-line length limit.
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from career_agent.semantic.contract import SYSTEM_PROMPT, answer_schema, user_message
from career_agent.semantic.intent import SearchIntent
from career_agent.semantic.providers.base import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)

TIMEOUT_SECONDS = 180
STATUS_TIMEOUT_SECONDS = 20

#: The ONLY environment a child CLI receives: enough for the operating system,
#: the user's home (where the CLI keeps its own sign-in) and temporary files.
#: An allowlist rather than a denylist, for two reasons found in review.
#: Posting text is third-party input, and a child that inherited this
#: process's environment would hold every key `.env` loaded, one prompt
#: injection away from quoting it back. And every variable that moves a CLI
#: onto API billing (`ANTHROPIC_API_KEY`, `CODEX_API_KEY`, `OPENAI_API_KEY`,
#: `CLAUDE_CODE_USE_BEDROCK`, `ANTHROPIC_BASE_URL`, ...) is excluded by not
#: being listed, including ones nobody has named yet.
CHILD_ENVIRONMENT = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERNAME",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "OS",
        "PROCESSOR_ARCHITECTURE",
        "NUMBER_OF_PROCESSORS",
        "CODEX_HOME",
        "CLAUDE_CONFIG_DIR",
        "XDG_CONFIG_HOME",
        # How this machine reaches the internet and which certificates it
        # trusts. Without them a CLI behind a corporate proxy cannot sign in.
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)
#: Kept for readers and tests: the billing variables the allowlist excludes.
API_BILLING_VARIABLES = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "OPENAI_API_KEY",
    "CODEX_API_KEY",
)
#: Codex tools that could act on this machine or reach beyond the answer.
#: Disabled, so a posting that says "run this command" meets a model that
#: cannot (verified 2026-09-25: it answers that it could not run it).
CODEX_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "browser_use",
    "browser_use_external",
    "computer_use",
    "in_app_browser",
    "plugins",
    "remote_plugin",
    "tool_suggest",
    "sleep_tool",
)

_LIMIT_WORDS = ("limit", "quota", "rate", "429", "overloaded", "capacity", "usage")
_AUTH_WORDS = ("log in", "login", "logged in", "sign in", "authenticat", "unauthorized", "401")


def _child_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() in CHILD_ENVIRONMENT}


def _run(
    argv: list[str], stdin: str, timeout: float, cwd: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv is built here, never from input
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        cwd=cwd,
        env=_child_env(),
        check=False,
    )


def _failure_state(text: str) -> Availability:
    folded = text.casefold()
    if any(word in folded for word in _AUTH_WORDS):
        return Availability.SIGN_IN_REQUIRED
    if any(word in folded for word in _LIMIT_WORDS):
        return Availability.LIMIT_OR_ERROR
    return Availability.LIMIT_OR_ERROR


# =========================================================================
# CLAUDE CODE
# =========================================================================


def find_claude() -> str | None:
    """The Claude Code EXECUTABLE, never a batch shim.

    npm installs `claude.cmd`, and running a batch file routes every argument
    through cmd.exe, which rewrites quotes, pipes and line breaks: the system
    prompt would arrive mangled, and argument text would be interpreted by a
    shell. The shim only launches the bundled `claude.exe`, so that is what is
    run. A shim whose executable cannot be found is treated as not installed.
    """
    found = shutil.which("claude")
    if found is None:
        return None
    if Path(found).suffix.lower() not in (".cmd", ".bat"):
        return found
    bundled = Path(found).parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin"
    for name in ("claude.exe", "claude"):
        if (bundled / name).is_file():
            return str(bundled / name)
    return None


class ClaudeCodeProvider:
    id = "claude_code"
    display_name = "Claude Code"

    def __init__(self, model: str = "sonnet") -> None:
        self.model = model

    def availability(self) -> ProviderStatus:
        exe = find_claude()
        if exe is None:
            return ProviderStatus(Availability.NOT_INSTALLED, "Claude Code is not installed.")
        try:
            done = _run([exe, "auth", "status"], "", STATUS_TIMEOUT_SECONDS)
            status = json.loads(done.stdout or "{}")
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return ProviderStatus(
                Availability.LIMIT_OR_ERROR, "Claude Code did not report its sign-in state."
            )
        method = str(status.get("authMethod") or "")
        if not status.get("loggedIn"):
            return ProviderStatus(
                Availability.SIGN_IN_REQUIRED, "Open Claude Code and sign in to use it here."
            )
        if method != "claude.ai":
            return ProviderStatus(
                Availability.SIGN_IN_REQUIRED,
                "Claude Code is signed in with an API key, which bills the Anthropic API. "
                "Sign in with a Claude subscription to use it here.",
                {"auth": method or "unknown"},
            )
        return ProviderStatus(
            Availability.AVAILABLE,
            "Signed in with a Claude subscription. Uses your plan limits.",
            {"auth": "subscription"},
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.SUBSCRIPTION,
            quotes=True,
            max_concurrency=2,
            sends="Your search intent phrases and the text of each posting evaluated, "
            "through your own Claude Code sign-in.",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return None

    def _ask(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        exe = find_claude()
        if exe is None:
            raise ProviderFailed("Claude Code is not installed.", state=Availability.NOT_INSTALLED)
        argv = [
            exe,
            "-p",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(schema, separators=(",", ":")),
            "--tools",
            "",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--setting-sources",
            "",
            "--system-prompt",
            system,
            "--model",
            self.model,
        ]
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ca-claude-") as cwd:
            try:
                done = _run(argv, user, TIMEOUT_SECONDS, cwd)
            except subprocess.TimeoutExpired as exc:
                raise ProviderFailed("Claude Code did not answer in time.") from exc
            except OSError as exc:
                raise ProviderFailed(
                    "Claude Code could not be started.", state=Availability.NOT_INSTALLED
                ) from exc
        latency = int((time.monotonic() - started) * 1000)
        try:
            body = json.loads(done.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ProviderFailed("Claude Code returned unreadable output.") from exc
        if done.returncode != 0 or body.get("is_error"):
            reason = str(body.get("result") or done.stderr or "error")[:300]
            raise ProviderFailed(f"Claude Code refused: {reason}", state=_failure_state(reason))
        answer = body.get("structured_output")
        raw = json.dumps(answer) if isinstance(answer, dict) else str(body.get("result") or "")
        usage = body.get("usage") or {}
        used = list((body.get("modelUsage") or {}).keys())
        inputs = sum(
            int(usage.get(key) or 0)
            for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
        )
        return ProviderAnswer(
            raw_text=raw,
            provider=self.id,
            model=used[0] if used else self.model,
            latency_ms=latency,
            input_tokens=inputs or None,
            output_tokens=usage.get("output_tokens"),
            cost_usd=None,
        )

    def evaluate(self, intent: SearchIntent, title: str, posting: str) -> ProviderAnswer:
        return self._ask(SYSTEM_PROMPT, user_message(intent, title, posting), answer_schema())

    def healthcheck(self) -> ProviderStatus:
        status = self.availability()
        if not status.state.usable:
            return status
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        try:
            self._ask("Answer in the requested JSON only.", "Return ok true.", schema)
        except ProviderFailed as exc:
            return ProviderStatus(exc.state, str(exc))
        return ProviderStatus(Availability.CONNECTED, "Claude Code answered.")


# =========================================================================
# CODEX
# =========================================================================


def find_codex() -> str | None:
    """The Codex EXECUTABLE, never a batch shim (see `find_claude`)."""
    found = shutil.which("codex")
    if found and Path(found).suffix.lower() not in (".cmd", ".bat"):
        return found
    # The Codex desktop app on Windows ships its CLI outside PATH.
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates = glob.glob(str(Path(local) / "OpenAI" / "Codex" / "bin" / "*" / "codex.exe"))
        if candidates:
            return max(candidates, key=os.path.getmtime)
    return None


class CodexProvider:
    id = "codex"
    display_name = "Codex"

    def __init__(self, model: str = "gpt-6-sol", reasoning: str = "low") -> None:
        self.model = model
        self.reasoning = reasoning

    def availability(self) -> ProviderStatus:
        exe = find_codex()
        if exe is None:
            return ProviderStatus(Availability.NOT_INSTALLED, "Codex is not installed.")
        try:
            done = _run([exe, "login", "status"], "", STATUS_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, OSError):
            return ProviderStatus(
                Availability.LIMIT_OR_ERROR, "Codex did not report its sign-in state."
            )
        said = f"{done.stdout}\n{done.stderr}".casefold()
        if "chatgpt" in said and "logged in" in said:
            return ProviderStatus(
                Availability.AVAILABLE,
                "Signed in with ChatGPT. Uses your plan limits.",
                {"auth": "subscription"},
            )
        if "api key" in said and "logged in" in said:
            return ProviderStatus(
                Availability.SIGN_IN_REQUIRED,
                "Codex is signed in with an API key, which bills the OpenAI API. "
                "Sign in with ChatGPT to use it here.",
                {"auth": "api_key"},
            )
        return ProviderStatus(
            Availability.SIGN_IN_REQUIRED, "Open Codex and sign in with ChatGPT to use it here."
        )

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.SUBSCRIPTION,
            quotes=True,
            max_concurrency=2,
            sends="Your search intent phrases and the text of each posting evaluated, "
            "through your own Codex sign-in.",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return None

    def _ask(self, prompt: str, schema: dict) -> ProviderAnswer:
        exe = find_codex()
        if exe is None:
            raise ProviderFailed("Codex is not installed.", state=Availability.NOT_INSTALLED)
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="ca-codex-") as cwd:
            schema_path = Path(cwd) / "schema.json"
            last_path = Path(cwd) / "answer.txt"
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            argv = [
                exe,
                "exec",
                "--ephemeral",
                "--skip-git-repo-check",
                "--ignore-user-config",
                "--ignore-rules",
                "--sandbox",
                "read-only",
                "-C",
                cwd,
                "--output-schema",
                str(schema_path),
                "-o",
                str(last_path),
                "--json",
                "-m",
                self.model,
                "-c",
                f'model_reasoning_effort="{self.reasoning}"',
                "-c",
                'web_search="disabled"',
                *(arg for feature in CODEX_DISABLED_FEATURES for arg in ("--disable", feature)),
                "-",
            ]
            try:
                done = _run(argv, prompt, TIMEOUT_SECONDS, cwd)
            except subprocess.TimeoutExpired as exc:
                raise ProviderFailed("Codex did not answer in time.") from exc
            except OSError as exc:
                raise ProviderFailed(
                    "Codex could not be started.", state=Availability.NOT_INSTALLED
                ) from exc
            raw = last_path.read_text(encoding="utf-8") if last_path.exists() else ""
        latency = int((time.monotonic() - started) * 1000)
        usage: dict = {}
        failure = ""
        for line in (done.stdout or "").splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "turn.completed":
                usage = event.get("usage") or {}
            if event.get("type") in ("error", "turn.failed"):
                failure = json.dumps(event)[:300]
        if done.returncode != 0 or failure or not raw.strip():
            reason = failure or (done.stderr or "no answer")[-300:]
            raise ProviderFailed(f"Codex refused: {reason}", state=_failure_state(reason))
        return ProviderAnswer(
            raw_text=raw,
            provider=self.id,
            model=f"{self.model}@{self.reasoning}",
            latency_ms=latency,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
            cost_usd=None,
        )

    def evaluate(self, intent: SearchIntent, title: str, posting: str) -> ProviderAnswer:
        prompt = f"{SYSTEM_PROMPT}\n\nINPUT:\n{user_message(intent, title, posting)}"
        return self._ask(prompt, answer_schema())

    def healthcheck(self) -> ProviderStatus:
        status = self.availability()
        if not status.state.usable:
            return status
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["ok"],
            "properties": {"ok": {"type": "boolean"}},
        }
        try:
            self._ask("Return the JSON object with ok true.", schema)
        except ProviderFailed as exc:
            return ProviderStatus(exc.state, str(exc))
        return ProviderStatus(Availability.CONNECTED, "Codex answered.")

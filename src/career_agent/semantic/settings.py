"""AI & Semantic Matching settings, and the one local secret they need.

HOW Career Agent interprets postings, not WHAT the person wants: this lives
beside Settings & Sources, never in the Search Intent.

Settings are `config/semantic.local.yaml` (gitignored like every local file).
The DeepSeek key is `DEEPSEEK_API_KEY` in the root `.env`, the file the
project already reads its credentials from and already excludes from backups
and from git. The key is never written to the database, never returned by an
API, never logged; the API reports only whether one is configured.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from career_agent.config.candidate_writer import _write_atomically
from career_agent.semantic.providers.deepseek import CREDENTIAL
from career_agent.yaml_io import safe_load

SETTINGS_FILE = "semantic.local.yaml"


class Mode(StrEnum):
    AUTO = "auto"
    DEEPSEEK = "deepseek"
    CODEX = "codex"
    CLAUDE_CODE = "claude_code"
    LAYA = "laya"
    DETERMINISTIC = "deterministic"


class SemanticSettings(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    #: Whether validated semantic findings take part in Search Fit. Nothing is
    #: ever sent to a provider without an explicit run, whatever this says.
    enabled: bool = True
    mode: Mode = Mode.AUTO
    #: Hard stop for a metered API in one run, in USD.
    budget_per_run_usd: float = Field(default=0.25, ge=0.0, le=5.0)
    #: At most this many postings per run, for every provider.
    max_jobs_per_run: int = Field(default=300, ge=1, le=5000)
    #: Subscription providers spend the person's plan limits; a smaller cap.
    max_jobs_per_subscription_run: int = Field(default=25, ge=1, le=500)

    @property
    def uses_findings(self) -> bool:
        """Whether scoring reads stored semantic findings at all."""
        return self.enabled and self.mode is not Mode.DETERMINISTIC


class SettingsError(ValueError):
    pass


def settings_path(config_dir: Path) -> Path:
    return config_dir / SETTINGS_FILE


def load_settings(config_dir: Path) -> SemanticSettings:
    path = settings_path(config_dir)
    if not path.exists():
        return SemanticSettings()
    try:
        data = safe_load(path.read_text(encoding="utf-8")) or {}
        return SemanticSettings.model_validate(data)
    except (ValidationError, ValueError, OSError, yaml.YAMLError):
        # An unreadable file never enables anything: deterministic only.
        return SemanticSettings(enabled=False)


def save_settings(config_dir: Path, changes: dict) -> SemanticSettings:
    current = load_settings(config_dir).model_dump(mode="json")
    current.update(changes)
    try:
        settings = SemanticSettings.model_validate(current)
    except ValidationError as exc:
        raise SettingsError(f"{exc.error_count()} invalid setting(s)") from exc
    data = settings.model_dump(mode="json")
    text = (
        "# AI & Semantic Matching. Written by Career Agent; safe to edit by hand.\n"
        "# Gitignored. Holds no credential: API keys live in the root .env.\n"
        + "".join(f"{key}: {_yaml_value(value)}\n" for key, value in data.items())
    )
    _write_atomically(settings_path(config_dir), text)
    return settings


def _yaml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# =========================================================================
# THE LOCAL SECRET
# =========================================================================

#: DeepSeek keys are `sk-` followed by letters and digits. Anything else is
#: refused before it is written, so a pasted sentence never lands in .env.
_KEY_SHAPE = re.compile(r"^sk-[A-Za-z0-9]{16,128}$")
_LINE = re.compile(rf"^\s*(export\s+)?{CREDENTIAL}\s*=")


@dataclass(frozen=True)
class KeyState:
    configured: bool
    #: Where it came from, in words: never the value, never a suffix.
    source: str


def env_path(config_dir: Path) -> Path:
    """The root `.env`: the folder above `config/`."""
    return config_dir.parent / ".env"


def key_state(config_dir: Path) -> KeyState:
    if os.environ.get(CREDENTIAL, "").strip():
        return KeyState(True, "configured on this computer")
    return KeyState(False, "not configured")


def valid_key_shape(key: str) -> bool:
    return bool(_KEY_SHAPE.match(key.strip()))


def store_key(config_dir: Path, key: str) -> KeyState:
    key = key.strip()
    if not valid_key_shape(key):
        raise SettingsError("That does not look like a DeepSeek API key.")
    lines = _env_lines(config_dir)
    kept = [line for line in lines if not _LINE.match(line)]
    kept.append(f"{CREDENTIAL}={key}")
    _write_env(env_path(config_dir), kept)
    os.environ[CREDENTIAL] = key
    return key_state(config_dir)


def remove_key(config_dir: Path) -> KeyState:
    lines = _env_lines(config_dir)
    kept = [line for line in lines if not _LINE.match(line)]
    if len(kept) != len(lines):
        _write_env(env_path(config_dir), kept)
    os.environ.pop(CREDENTIAL, None)
    return key_state(config_dir)


def _env_lines(config_dir: Path) -> list[str]:
    path = env_path(config_dir)
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8").splitlines()


def _write_env(path: Path, lines: list[str]) -> None:
    """Atomic, and deliberately WITHOUT the `.backup` sibling other local
    files keep: a backup of this file is a second copy of a secret."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-env-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise

"""AI & Semantic Matching settings, and the one local secret they need.

HOW Career Agent interprets postings, not WHAT the person wants: this lives
beside Settings & Sources, never in the Search Intent.

Settings are `semantic.local.yaml` in the PROFILE's settings folder
(gitignored like every local file): whether an external AI provider is used,
and which, is each profile's own decision. A profile that never made it uses
none: Career Agent works deterministically until somebody chooses (`choice`).

The DeepSeek key is `DEEPSEEK_API_KEY` in the root `.env`, the file the
project already reads its credentials from and already excludes from backups
and from git. It is INSTALLATION-WIDE: one key on this computer, which every
profile may use only if that profile chose a provider that needs it, and
which "remove" removes for all of them. The key is never written to the
database, never returned by an API, never logged; the API reports only
whether one is configured.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

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
    #: No provider until the profile chooses one. Auto, or a named provider,
    #: is a decision a profile makes in Settings, never a default it inherits.
    mode: Mode = Mode.DETERMINISTIC
    #: Hard stop for a metered API in one run, in USD.
    budget_per_run_usd: float = Field(default=0.25, ge=0.0, le=5.0)
    #: At most this many postings per run, for every provider.
    max_jobs_per_run: int = Field(default=300, ge=1, le=5000)
    #: Subscription providers spend the person's plan limits; a smaller cap.
    max_jobs_per_subscription_run: int = Field(default=25, ge=1, le=500)
    #: The providers this profile allowed to receive its CONFIRMED evidence for
    #: the deep reading (ADR-0035). Consent per profile and per
    #: provider: empty by default, and only a signed-in subscription CLI can be
    #: named, so no API key and no fallback ever receives a person's history.
    share_evidence_with: list[Literal["claude_code", "codex"]] = Field(default_factory=list)

    @property
    def uses_findings(self) -> bool:
        """Whether scoring reads stored semantic findings at all."""
        return self.enabled and self.mode is not Mode.DETERMINISTIC


class SettingsError(ValueError):
    pass


def settings_path(config_dir: Path) -> Path:
    return config_dir / SETTINGS_FILE


#: How a profile's AI choice stands: written in its settings file; KEPT from
#: before choosing existed (the profile already used an AI provider when the
#: implicit default was Auto, so Auto stays until it chooses, and nothing it
#: relied on moves); or never made, which means deterministic.
CHOSEN, KEPT, NOT_CHOSEN = "chosen", "kept", "not_chosen"


def used_ai(conn: sqlite3.Connection) -> bool:
    """Whether this profile's database shows an AI provider already used: a
    semantic run that reached one, or a resume drafted with AI."""
    for sql in (
        "SELECT EXISTS (SELECT 1 FROM semantic_run WHERE provider IS NOT NULL)",
        "SELECT EXISTS (SELECT 1 FROM tailoring_run WHERE mode = 'AI_ASSISTED')",
    ):
        try:
            if conn.execute(sql).fetchone()[0]:
                return True
        except sqlite3.Error:  # an older or a demo database without the table
            continue
    return False


def choice(config_dir: Path, conn: sqlite3.Connection | None = None) -> str:
    if settings_path(config_dir).exists():
        return CHOSEN
    return KEPT if conn is not None and used_ai(conn) else NOT_CHOSEN


def load_settings(config_dir: Path, conn: sqlite3.Connection | None = None) -> SemanticSettings:
    """The profile's settings. With no file, deterministic, unless `conn` (the
    profile's database) shows it already used AI: then Auto is KEPT, so an
    existing installation behaves as it did until the person chooses."""
    path = settings_path(config_dir)
    if not path.exists():
        kept = conn is not None and used_ai(conn)
        return SemanticSettings(mode=Mode.AUTO) if kept else SemanticSettings()
    try:
        data = safe_load(path.read_text(encoding="utf-8")) or {}
        return SemanticSettings.model_validate(data)
    except (ValidationError, ValueError, OSError, yaml.YAMLError):
        # An unreadable file never enables anything: deterministic only.
        return SemanticSettings(enabled=False)


def save_settings(
    config_dir: Path, changes: dict, conn: sqlite3.Connection | None = None
) -> SemanticSettings:
    """Write the whole file. With `conn`, a KEPT Auto is written down as it
    stands when another setting is saved, never silently dropped."""
    current = load_settings(config_dir, conn).model_dump(mode="json")
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
    if isinstance(value, list):
        return json.dumps(value)
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
    """The installation's `.env`, never a profile's folder.

    `config/` sits in the installation root; a later local profile's settings
    live in `data/profiles/<id>/config/`, and its key must not be written
    there: the key is installation-wide and one copy is the only safe number.
    """
    from career_agent.runtime.profiles import installation_root

    return installation_root(config_dir) / ".env"


def load_stored_key(config_dir: Path) -> bool:
    """Bring a key saved in the root `.env` into this process, once, at startup.

    The launcher that opens the app never loads `.env` (only the CLI does), so
    a key saved from Settings was lost on the next start. Only THIS variable is
    read, and only when the environment does not already hold one: an explicit
    environment value wins, and nothing else in `.env` is touched.
    """
    if os.environ.get(CREDENTIAL, "").strip():
        return True
    path = env_path(config_dir)
    if not path.is_file():
        return False
    from dotenv import dotenv_values

    value = (dotenv_values(path).get(CREDENTIAL) or "").strip()
    if not value:
        return False
    os.environ[CREDENTIAL] = value
    return True


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
    # Named `.env.tmp-*` so that even a temporary copy left by a hard kill is
    # covered by the `.env.*` ignore rule and never looks committable.
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".env.tmp-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + ("\n" if lines else ""))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise

"""Preferred role anchors: optional search anchors, never limits.

"Do you have specific roles in mind?" A person may name a few roles (a nurse:
"ICU Nurse"; a salesperson: "Account Executive"; an engineer: "Platform
Engineer"). Career Agent uses them to ASK query-based sources better
questions. It still discovers relevant work under any other title through the
exploratory lane, and an anchor never hides, rules out or scores a posting.

PROVENANCE IS KEPT. What the person typed is a `user` anchor. A role suggested
from their Career Profile becomes an anchor only after they confirm it
(`confirmed_suggestion`): background is what someone HAS done, and it never
silently becomes what they want next. Aliases a planner generated are kept in a
separate list with their generator named, so the interface can always show
which words were the person's and which were a helper's.

Stored in `config/role_anchors.local.yaml` (gitignored like every local file),
not in the scoring configuration: anchors are retrieval helpers, so a change to
them must never move a score or require a recalculation.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_agent.config.candidate_writer import _write_atomically
from career_agent.yaml_io import safe_load

ANCHORS_FILE = "role_anchors.local.yaml"
MAX_ANCHORS = 8
MAX_ALIASES_PER_ANCHOR = 6
MAX_TEXT = 80
ANCHOR_SOURCES = ("user", "confirmed_suggestion")
ALIAS_SOURCES = ("generated", "rule")

_SPACE = re.compile(r"\s+")


def clean(text: str) -> str:
    return _SPACE.sub(" ", str(text or "")).strip()


def folded(text: str) -> str:
    return clean(text).casefold()


class Anchor(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=MAX_TEXT)
    source: str = "user"

    @field_validator("text")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean(value)

    @field_validator("source")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in ANCHOR_SOURCES:
            raise ValueError(f"source must be one of {ANCHOR_SOURCES}")
        return value


class Alias(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    text: str = Field(min_length=1, max_length=MAX_TEXT)
    #: The anchor text it was generated for.
    anchor: str
    source: str = "generated"
    #: `deepseek:deepseek-flash`, `rule`, ... Never empty.
    generator: str = "rule"

    @field_validator("text", "anchor")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean(value)

    @field_validator("source")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in ALIAS_SOURCES:
            raise ValueError(f"source must be one of {ALIAS_SOURCES}")
        return value


class RoleAnchors(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    anchors: tuple[Anchor, ...] = ()
    aliases: tuple[Alias, ...] = ()

    @field_validator("anchors")
    @classmethod
    def _bounded(cls, value: tuple[Anchor, ...]) -> tuple[Anchor, ...]:
        seen: dict[str, Anchor] = {}
        for anchor in value:
            seen.setdefault(folded(anchor.text), anchor)
        if len(seen) > MAX_ANCHORS:
            raise ValueError(f"at most {MAX_ANCHORS} role anchors")
        return tuple(seen.values())

    def alias_texts(self) -> tuple[str, ...]:
        """Aliases that still belong to a current anchor, deduplicated against
        the anchors themselves and each other."""
        current = {folded(a.text) for a in self.anchors}
        seen = set(current)
        out: list[str] = []
        counts: dict[str, int] = {}
        for alias in self.aliases:
            key = folded(alias.text)
            owner = folded(alias.anchor)
            if owner not in current or key in seen:
                continue
            if counts.get(owner, 0) >= MAX_ALIASES_PER_ANCHOR:
                continue
            counts[owner] = counts.get(owner, 0) + 1
            seen.add(key)
            out.append(alias.text)
        return tuple(out)


class AnchorsError(ValueError):
    pass


def anchors_path(config_dir: Path) -> Path:
    return config_dir / ANCHORS_FILE


def load_anchors(config_dir: Path) -> RoleAnchors:
    path = anchors_path(config_dir)
    if not path.exists():
        return RoleAnchors()
    try:
        data = safe_load(path.read_text(encoding="utf-8")) or {}
        return RoleAnchors.model_validate(data)
    except Exception:  # noqa: BLE001 - an unreadable file is "no anchors", never a crash
        return RoleAnchors()


def save_anchors(config_dir: Path, anchors: RoleAnchors) -> RoleAnchors:
    try:
        checked = RoleAnchors.model_validate(anchors.model_dump())
    except ValidationError as exc:
        raise AnchorsError(f"{exc.error_count()} invalid entr(y/ies)") from exc
    import yaml

    text = (
        "# Preferred role anchors. Written by Career Agent; safe to edit by hand.\n"
        "# Search anchors, not limits: they help ask job sources better questions\n"
        "# and never hide, rule out or score a posting. Gitignored.\n"
        + yaml.safe_dump(checked.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    )
    _write_atomically(anchors_path(config_dir), text)
    return checked

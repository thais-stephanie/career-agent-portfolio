"""Workspace schema versioning.

Every ``candidate.json`` (and the backup manifest) carries ``schema_version``.
Migrations run oldest-to-newest when an older workspace is opened; an unknown
NEWER version produces a human-readable error instead of misreading data, so a
future release can change the layout without users ever editing JSON by hand.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

CURRENT_SCHEMA = 1

# version -> migration that upgrades a candidate meta dict from that version to the next.
# (No historical versions exist yet; version 1 is the first shipped schema.)
_MIGRATIONS: dict[int, Callable[[dict[str, Any], Path], dict[str, Any]]] = {}


class SchemaTooNew(Exception):
    """The workspace was written by a newer Resume Tailor than this one."""


def check_schema(meta: dict[str, Any], path: Path) -> dict[str, Any]:
    """Validate and, when needed, upgrade a candidate meta dict in place."""
    version = int(meta.get("schema_version", 1))
    if version > CURRENT_SCHEMA:
        raise SchemaTooNew(
            f"This candidate was saved by a newer version of Resume Tailor "
            f"(data version {version}, this app understands up to {CURRENT_SCHEMA}). "
            f"Please update Resume Tailor to open it. Nothing was changed on disk. ({path})"
        )
    while version < CURRENT_SCHEMA:
        meta = _MIGRATIONS[version](meta, path)
        version = int(meta["schema_version"])
    return meta

"""Experimental local overrides: a source the catalogue forbids, run only
because the person using this profile explicitly chose to.

The catalogue row keeps its FORBIDDEN permission and its quoted reason; that
is a fact about what the site says and no opt-in changes it. What the row may
additionally declare is `experimental_override`: that a local, per-profile,
explicit choice can run an adapter anyway. This module holds that choice.

STORED IN THE PROFILE, NEVER SHIPPED. The choice lives in `candidate_state`
(the person's own database), keyed by source id, with when it was made. There
is no default that turns it on, no environment variable, and no way for a new
profile to inherit it.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from career_agent.clock import now_utc
from career_agent.storage.workspace_repo import (
    CandidateStateRepo,
    candidate_id_of,
    ensure_candidate,
)

PREFIX = "source_experimental."
#: The words the person agreed to, versioned: a later, materially different
#: warning must be agreed to again.
WARNING_VERSION = 1


def opted_in(conn: sqlite3.Connection, source_id: str) -> bool:
    return bool(state(conn, source_id).get("opted_in"))


def state(conn: sqlite3.Connection, source_id: str) -> dict[str, Any]:
    candidate = candidate_id_of(conn)
    if candidate is None:
        return {}
    raw = CandidateStateRepo(conn).get(candidate, PREFIX + source_id)
    try:
        value = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    # Agreeing to an older warning is not agreeing to this one.
    if value.get("warning_version") != WARNING_VERSION:
        return {**value, "opted_in": False}
    return value


def opted_in_sources(conn: sqlite3.Connection, source_ids: list[str]) -> set[str]:
    return {sid for sid in source_ids if opted_in(conn, sid)}


def set_opt_in(conn: sqlite3.Connection, source_id: str, on: bool) -> dict[str, Any]:
    """Record the choice. The caller holds the transaction."""
    value = {
        "opted_in": bool(on),
        "warning_version": WARNING_VERSION,
        "changed_at": now_utc(),
    }
    CandidateStateRepo(conn).set(ensure_candidate(conn), PREFIX + source_id, json.dumps(value))
    return value

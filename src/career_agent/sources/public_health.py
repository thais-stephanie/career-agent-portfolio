"""Public source health: how each collector last went, for every profile.

Written when a collection run finishes (`PipelineRunRepo.finish`), read by
the refresh progress model. It lives in the shared catalogue (migration
0045): a posting catalogue refreshed by one profile is fresh for all.

Only candidate-independent facts cross: outcome, a reason CODE, timestamps
and counts. Never a query, never a failure line (a LinkedIn failure line
names the query it failed on), never anything from the profile's search.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
RATE_LIMITED = "RATE_LIMITED"
FAILED = "FAILED"

#: The shared pass that walks every employer-board family at once.
SHARED_STAGE = "collect"

_SEEN = ("postings_seen", "postings_observed", "raw_results", "retrieved")
_NEW = ("jobs_new", "postings_new", "new_postings", "jobs_created")


@dataclass(frozen=True)
class PublicHealth:
    source_key: str
    last_attempt_at: str
    last_finished_at: str | None
    last_success_at: str | None
    last_useful_at: str | None
    last_outcome: str
    last_reason: str | None
    jobs_seen: int | None
    jobs_new: int | None


def _count(stats: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = stats.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def rate_limited(stats: Mapping[str, Any]) -> bool:
    """A refusal is its own outcome, never "no jobs": the collector said the
    provider refused (429, 999, 403, a sign-in wall)."""
    return bool(
        stats.get("queries_rate_limited")
        or stats.get("stopped_reason") == "rate_limited"
        or stats.get("rate_limited")
    )


def outcome_of(status: str, stats: Mapping[str, Any]) -> tuple[str, str | None]:
    """The outcome and reason code of one finished run (or one family slice)."""
    from career_agent.sources.progress import partial_reason

    if rate_limited(stats):
        return RATE_LIMITED, "REFUSED"
    if str(status).upper() not in {"OK", "SUCCESS", "COMPLETE"}:
        return FAILED, "ERROR"
    reason = partial_reason(stats)
    if reason is not None:
        return PARTIAL, reason
    return COMPLETE, None


def _table(conn: sqlite3.Connection) -> str | None:
    """Where the table is: the attached catalogue, this file, or nowhere yet
    (a database migrated by an older build)."""
    from career_agent.storage.catalogue import SCHEMA, is_attached

    for schema in ((SCHEMA,) if is_attached(conn) else ()) + ("main",):
        found = conn.execute(
            f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = 'source_health'"
        ).fetchone()
        if found:
            return f"{schema}.source_health"
    return None


def _slices(stage: str, status: str, stats: Mapping[str, Any]) -> list[tuple[str, str, Mapping]]:
    """(source key, status, stats) per collector the run speaks for."""
    if stage != SHARED_STAGE:
        return [(stage, status, stats)]
    by_provider = stats.get("by_provider")
    if not isinstance(by_provider, dict):
        return []
    out: list[tuple[str, str, Mapping]] = []
    for provider, part in by_provider.items():
        if not isinstance(part, dict) or not (
            part.get("boards_attempted", 0) > 0 or part.get("boards_deferred", 0) > 0
        ):
            continue
        attempted = part.get("boards_attempted", 0) or 0
        failed = part.get("boards_failed")
        if failed is None:
            failed = attempted - (part.get("boards_succeeded", attempted) or 0)
        # A family fails only when every board it tried failed; some boards
        # answering NOT_FOUND is PARTIAL, never the family failing.
        own = "FAILED" if attempted and failed >= attempted else "OK"
        out.append((f"{SHARED_STAGE}:{provider}", own, part))
    return out


def record(
    conn: sqlite3.Connection,
    *,
    stage: str,
    started_at: str,
    finished_at: str,
    status: str,
    stats: Mapping[str, Any],
) -> None:
    """Write what a finished collection run means for each collector it spoke for."""
    if not stage.startswith(SHARED_STAGE):
        return
    table = _table(conn)
    if table is None:
        return
    for key, own_status, part in _slices(stage, status, stats):
        outcome, reason = outcome_of(own_status, part)
        success = finished_at if outcome in (COMPLETE, PARTIAL) else None
        new = _count(part, _NEW)
        useful = finished_at if success and (new or 0) > 0 else None
        conn.execute(
            f"INSERT INTO {table} (source_key, last_attempt_at, last_finished_at,"
            " last_success_at, last_useful_at, last_outcome, last_reason, jobs_seen,"
            " jobs_new, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(source_key) DO UPDATE SET"
            " last_attempt_at = excluded.last_attempt_at,"
            " last_finished_at = excluded.last_finished_at,"
            " last_success_at = COALESCE(excluded.last_success_at, last_success_at),"
            " last_useful_at = COALESCE(excluded.last_useful_at, last_useful_at),"
            " last_outcome = excluded.last_outcome, last_reason = excluded.last_reason,"
            " jobs_seen = excluded.jobs_seen, jobs_new = excluded.jobs_new,"
            " updated_at = excluded.updated_at",
            (
                key,
                started_at,
                finished_at,
                success,
                useful,
                outcome,
                reason,
                _count(part, _SEEN),
                new,
                finished_at,
            ),
        )


def read(conn: sqlite3.Connection) -> dict[str, PublicHealth]:
    table = _table(conn)
    if table is None:
        return {}
    conn.row_factory = sqlite3.Row
    return {
        str(r["source_key"]): PublicHealth(
            source_key=str(r["source_key"]),
            last_attempt_at=str(r["last_attempt_at"]),
            last_finished_at=r["last_finished_at"],
            last_success_at=r["last_success_at"],
            last_useful_at=r["last_useful_at"],
            last_outcome=str(r["last_outcome"]),
            last_reason=r["last_reason"],
            jobs_seen=r["jobs_seen"],
            jobs_new=r["jobs_new"],
        )
        for r in conn.execute(f"SELECT * FROM {table}")
    }

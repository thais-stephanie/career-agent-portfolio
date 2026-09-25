"""A per-source funnel, read from what the database recorded. Read-only.

Written after a corpus of 122,876 postings turned out to be 88% one source,
with nine ATS families that had never collected a posting because no board of
theirs had ever reached the database. "Never run" on a screen said that; it did
not say WHY, and the number 122,876 hid it. This report makes both visible.

For every provider that has run, has boards, or holds postings:

    boards -> last attempt -> last success -> status -> requests -> raw fetched
    -> new -> updated -> deduplicated -> failed -> canonical jobs -> open jobs
    -> why partial

Every figure comes from `pipeline_run.stats_json`, `source_board` and `job`.
Nothing is estimated; a figure a run did not record is shown as unknown.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from career_agent.sources.progress import (
    STALE_AFTER_HOURS,
    RefreshState,
    _older_than,
    _state_of_slice,
    partial_reason,
)

#: The stage every board family shares.
SHARED_STAGE = "collect"

_RAW_KEYS = ("postings_seen", "postings_observed", "postings_listed")
_NEW_KEYS = ("jobs_new", "postings_new")
_UPDATED_KEYS = ("jobs_changed", "jobs_seen_again")


def _first(stats: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = stats.get(key)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _requests(stats: dict[str, Any]) -> int | None:
    http = stats.get("http")
    if isinstance(http, dict):
        for key in ("requests", "requests_made", "fetches"):
            value = http.get(key)
            if isinstance(value, int):
                return value
    value = stats.get("requests")
    return value if isinstance(value, int) else None


def _failed(stats: dict[str, Any]) -> int:
    failures = stats.get("failures")
    if isinstance(failures, list | dict):
        return len(failures)
    if isinstance(failures, int):
        return failures
    return int(stats.get("boards_failed") or 0)


@dataclass
class SourceFunnel:
    provider: str
    boards: int = 0
    boards_active: int = 0
    last_attempt: str | None = None
    last_success: str | None = None
    status: str = "NEVER_RUN"
    requests: int | None = None
    raw_fetched: int | None = None
    new: int | None = None
    updated: int | None = None
    deduplicated: int | None = None
    failed: int = 0
    canonical_jobs: int = 0
    open_jobs: int = 0
    partial_reason: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def _stats(row: sqlite3.Row | tuple[Any, ...] | None) -> dict[str, Any]:
    if row is None or not row[3]:
        return {}
    try:
        loaded = json.loads(str(row[3]))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def coverage(
    conn: sqlite3.Connection, *, families: set[str], now: datetime | None = None
) -> list[SourceFunnel]:
    moment = now or datetime.now(UTC)
    funnels: dict[str, SourceFunnel] = {}

    def get(provider: str) -> SourceFunnel:
        return funnels.setdefault(provider, SourceFunnel(provider))

    for provider, total, active in conn.execute(
        "SELECT provider, count(*), sum(active) FROM source_board GROUP BY provider"
    ):
        funnel = get(str(provider))
        funnel.boards, funnel.boards_active = int(total), int(active or 0)
    for provider, total, open_ in conn.execute(
        "SELECT provider, count(*), sum(closed_at IS NULL) FROM job GROUP BY provider"
    ):
        funnel = get(str(provider))
        funnel.canonical_jobs, funnel.open_jobs = int(total), int(open_ or 0)

    runs = conn.execute(
        "SELECT stage, started_at, finished_at, stats_json, status FROM pipeline_run"
        " WHERE stage = ? OR stage LIKE 'collect-%' ORDER BY started_at",
        (SHARED_STAGE,),
    ).fetchall()
    for row in runs:
        stage, started, finished, _, status = row
        stats = _stats(row)
        if stage == SHARED_STAGE:
            by_provider = stats.get("by_provider")
            slices = by_provider if isinstance(by_provider, dict) else {}
            for provider, piece in slices.items():
                if isinstance(piece, dict) and (piece.get("boards_attempted") or 0) > 0:
                    # A family is judged by its own slice: one family failing
                    # inside a pass does not make the others FAILED.
                    _apply(
                        get(str(provider)),
                        started,
                        finished,
                        status,
                        piece,
                        slice_state=_state_of_slice(piece),
                    )
            continue
        provider = str(stage).removeprefix("collect-")
        _apply(get(provider), started, finished, status, stats)

    for funnel in funnels.values():
        # The same rule the app applies: a success older than this is stale,
        # whatever the run that produced it said at the time.
        if funnel.status in {"COMPLETE", "PARTIAL"} and _older_than(
            funnel.last_success, STALE_AFTER_HOURS, moment
        ):
            funnel.status = "STALE"
    for provider in families:
        funnel = get(provider)
        if funnel.status == "NEVER_RUN" and funnel.boards == 0:
            funnel.notes.append("no employer board of this family is known yet")
    return sorted(funnels.values(), key=lambda f: (-f.open_jobs, f.provider))


def _apply(
    funnel: SourceFunnel,
    started: Any,
    finished: Any,
    status: Any,
    stats: dict[str, Any],
    *,
    slice_state: RefreshState | None = None,
) -> None:
    funnel.last_attempt = str(started)
    ok = str(status or "").upper() in {"OK", "SUCCESS", "COMPLETE"} and finished is not None
    if finished is None:
        funnel.status = "RUNNING"
    elif slice_state is RefreshState.FAILED or (slice_state is None and not ok):
        funnel.status = "FAILED"
    else:
        reason = partial_reason(stats)
        funnel.status = "PARTIAL" if reason else "COMPLETE"
        funnel.partial_reason = reason
        if not stats.get("deferred_reason"):
            funnel.last_success = str(finished)
    funnel.requests = _requests(stats)
    funnel.raw_fetched = _first(stats, _RAW_KEYS)
    funnel.new = _first(stats, _NEW_KEYS)
    funnel.updated = _first(stats, _UPDATED_KEYS)
    duplicates = stats.get("duplicates_total")
    funnel.deduplicated = duplicates if isinstance(duplicates, int) else None
    funnel.failed = _failed(stats)

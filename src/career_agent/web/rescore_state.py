"""What the last recalculation is doing, or did, in one vocabulary.

THE OWNER'S EVENING (2026-10-13). A recalculation of 171,319 scores ran for 86
minutes and finished well; the screen meanwhile lost its progress, went back to
the starting notice with the starting count, and once said only
"Recalculating...". The run itself was fine. What failed was the account of it:
the progress lived only in the runner's memory, every Home redraw started one
more poll loop, ten missed polls ended the watch for good, and the notices kept
the count read when the page opened.

So the account is read from two places, in this order:

* the runner of THIS process, while it runs (live counters, cancellation);
* the run's own `pipeline_run` row, which the pass updates after every page
  it commits (`rescore._heartbeat`, `heartbeat_at`), judged alive or not by
  the rule the pipeline already uses (`rescore.LIVE_WINDOW`). That row
  survives a restart of the app, so a run that stopped with the process reads
  INTERRUPTED with the count it had committed, rather than as if nothing had
  happened.

Phases: RUNNING, RUNNING_ELSEWHERE (another process, e.g. the command line,
is writing heartbeats), DONE, FAILED, CANCELLED, INTERRUPTED, IDLE (never run).
`done` always counts scores COMMITTED: the heartbeat is written after the page
it counts.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

#: Fewer postings than this make a poor rate to estimate from.
MIN_JOBS_FOR_RATE = 1000


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def last_run(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """The newest `rescore` run row, as plain values, or None."""
    row = conn.execute(
        "SELECT id, started_at, finished_at, status, stats_json, error FROM pipeline_run"
        " WHERE stage = 'rescore' ORDER BY started_at DESC, rowid DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return None
    try:
        stats = json.loads(row[4] or "{}")
    except ValueError:
        stats = {}
    return {
        "id": row[0],
        "started_at": row[1],
        "finished_at": row[2],
        "status": row[3],
        "stats": stats if isinstance(stats, dict) else {},
        "error": row[5],
    }


def measured_rate(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Seconds per posting of the newest finished run large enough to measure.

    The only basis offered for "how long will this take": a figure this
    computer produced, named with the run it came from. None when there is
    no such run, and then the interface promises no duration at all.
    """
    rows = conn.execute(
        "SELECT finished_at, stats_json FROM pipeline_run WHERE stage = 'rescore'"
        " AND status = 'OK' AND finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 20"
    ).fetchall()
    for finished_at, raw in rows:
        try:
            stats = json.loads(raw or "{}")
        except ValueError:
            continue
        scored = int(stats.get("jobs_scored") or 0)
        elapsed = int(stats.get("elapsed_ms") or 0)
        if scored >= MIN_JOBS_FOR_RATE and elapsed > 0:
            return {
                "seconds_per_job": elapsed / 1000 / scored,
                "jobs": scored,
                "seconds": round(elapsed / 1000),
                "finished_at": finished_at,
            }
    return None


def describe(
    runner_running: bool,
    runner_snapshot: dict[str, Any] | None,
    record: dict[str, Any] | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One account of the recalculation: phase, committed progress, outcome."""
    now = now or datetime.now(UTC)
    if runner_running and runner_snapshot:
        detail = runner_snapshot.get("current_detail") or {}
        return {
            "phase": "RUNNING",
            "done": int(runner_snapshot.get("boards_done") or 0),
            "total": int(runner_snapshot.get("boards_total") or 0),
            "priority": int(detail.get("priority") or 0) if isinstance(detail, dict) else 0,
            "started_at": runner_snapshot.get("started_at"),
            "finished_at": None,
            "error": None,
        }
    if record is None:
        snapshot_status = (runner_snapshot or {}).get("status")
        if snapshot_status in {"failed", "cancelled"}:
            # Failed before its run row was written (a configuration that
            # would not load, a locked database).
            return {
                "phase": snapshot_status.upper(),
                "done": int((runner_snapshot or {}).get("boards_done") or 0),
                "total": int((runner_snapshot or {}).get("boards_total") or 0),
                "started_at": (runner_snapshot or {}).get("started_at"),
                "finished_at": (runner_snapshot or {}).get("finished_at"),
                "error": (runner_snapshot or {}).get("error"),
            }
        return {
            "phase": "IDLE",
            "done": 0,
            "total": 0,
            "started_at": None,
            "finished_at": None,
            "error": None,
        }
    stats = record["stats"]
    done = int(stats.get("jobs_scored") or 0)
    total = int(stats.get("jobs_targeted") or 0)
    base = {
        "done": done,
        "total": total,
        "priority": int(stats.get("jobs_priority") or 0),
        "started_at": record["started_at"],
        "finished_at": record["finished_at"],
        "error": record["error"],
        "run_id": record["id"],
    }
    from career_agent.pipeline.rescore import INTERRUPTED, LIVE_WINDOW

    if record["finished_at"] is None:
        beat = _parse(stats.get("heartbeat_at")) or _parse(record["started_at"])
        alive = beat is not None and now - beat <= LIVE_WINDOW
        return {**base, "phase": "RUNNING_ELSEWHERE" if alive else "INTERRUPTED"}
    if record["error"] == INTERRUPTED:
        # Closed by the next pass as stopped with its process (`close_interrupted_runs`).
        return {**base, "phase": "INTERRUPTED"}
    if (runner_snapshot or {}).get("status") == "cancelled":
        return {**base, "phase": "CANCELLED"}
    return {**base, "phase": "DONE" if record["status"] == "OK" else "FAILED"}

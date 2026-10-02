"""Retrieval as something the person starts and watches, not a command.

`collect` has existed since M1 and works. What did not exist is a way to run
it from the interface and to see what it did in the terms the product cares
about: how many postings were fetched, how many survived each stage, and which
sources answered.

THIS RUNS IN A THREAD AND IS POLLED. A collection pass over 234 boards takes
minutes; doing it inside a request would hold a socket open for the duration,
give the person a spinner with no detail, and lose everything if they navigated
away. So `start` returns immediately and `snapshot` is what the interface asks
repeatedly.

CANCELLATION IS COOPERATIVE AND LEAVES THE DATABASE INTACT. The flag is checked
between boards, never inside one, because a board is a unit of work with its
own transaction -- stopping mid-board would be the only way to corrupt
anything, so it is the one thing this cannot do.

THE FUNNEL IS DERIVED, NOT NARRATED. Every number is counted from the database
after the run, not accumulated by hand while it goes. A hand-kept tally that
drifts from what was stored is worse than no tally, because it looks
authoritative.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class SourceOutcome:
    """What one source did on this run. Reported even when it failed."""

    provider: str
    boards_attempted: int = 0
    boards_succeeded: int = 0
    boards_failed: int = 0
    postings_fetched: int = 0
    jobs_new: int = 0
    jobs_updated: int = 0
    held: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.boards_attempted == 0:
            return "not attempted"
        if self.boards_failed and not self.boards_succeeded:
            return "failed"
        if self.boards_failed:
            return "partial"
        return "ok"

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "status": self.status,
            "boards_attempted": self.boards_attempted,
            "boards_succeeded": self.boards_succeeded,
            "boards_failed": self.boards_failed,
            "postings_fetched": self.postings_fetched,
            "jobs_new": self.jobs_new,
            "jobs_updated": self.jobs_updated,
            "held": self.held,
            # Capped: a run where every board failed would otherwise put a
            # hundred stack-shaped strings on one screen.
            "failures": self.failures[:5],
            "failures_total": len(self.failures),
        }


@dataclass
class RetrievalState:
    """One run, from the interface's point of view."""

    run_id: str
    started_at: str
    status: str = "running"  # running | done | failed | cancelled
    boards_total: int = 0
    boards_done: int = 0
    error: str | None = None
    finished_at: str | None = None
    funnel: dict[str, int] = field(default_factory=dict)
    sources: list[dict[str, Any]] = field(default_factory=list)
    #: The source being read right now, and since when. A long source is the
    #: normal case, not a stall, and naming it is what lets the screen say so.
    current: str | None = None
    current_started_at: str | None = None
    #: Which button started it: "due", "all" or "source".
    kind: str = ""
    #: THE PLAN, FROZEN WHEN THE RUN STARTED. `boards_total` is its length and
    #: never moves, whatever the sources' freshness says while it runs.
    plan: list[str] = field(default_factory=list)
    #: Sources holding jobs from an earlier refresh that this run did not
    #: refresh (paused, refused and cooling down, unavailable, or failed now).
    not_refreshed: list[str] = field(default_factory=list)
    #: Jobs this run added to the corpus, once it has finished.
    jobs_new: int | None = None
    #: The source being read reports its own progress here, so an employer
    #: board family can say "121 of 124 boards" beside "5 of 16 sources".
    inner: Any = None

    def as_dict(self) -> dict[str, Any]:
        # Read once: the run's thread sets it to None when a source ends.
        inner = self.inner
        return {
            "run_id": self.run_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "boards_total": self.boards_total,
            "boards_done": self.boards_done,
            "error": self.error,
            "funnel": self.funnel,
            "sources": self.sources,
            "current": self.current,
            "current_started_at": self.current_started_at,
            "kind": self.kind,
            "plan": list(self.plan),
            "not_refreshed": list(self.not_refreshed),
            "jobs_new": self.jobs_new,
            "current_boards": (
                {"done": int(inner.boards_done), "total": int(inner.boards_total)}
                if inner is not None and inner.boards_total
                else None
            ),
        }


def now_iso() -> str:
    """UTC now, to the second, in the form every timestamp here uses."""
    return _now()


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def build_funnel(
    conn: sqlite3.Connection,
    config_id: str,
    config_version: int,
    shortlist_min: int,
) -> dict[str, int]:
    """The stages, counted from the database rather than tallied in flight.

    `fetched` is every posting held, not "seen during this run": a person
    asking what their corpus contains is asking about the corpus. A run's own
    deltas are in the per-source rows, which is where they mean something.
    """

    def scalar(sql: str, params: tuple[Any, ...] = ()) -> int:
        try:
            row = conn.execute(sql, params).fetchone()
        except sqlite3.Error:
            return 0
        return int(row[0]) if row and row[0] is not None else 0

    fetched = scalar("SELECT COUNT(*) FROM job")
    active = scalar("SELECT COUNT(*) FROM job WHERE closed_at IS NULL")
    normalised = scalar(
        "SELECT COUNT(*) FROM job j JOIN job_raw jr ON jr.content_hash = j.content_hash"
        " WHERE j.closed_at IS NULL"
    )
    scored = scalar(
        "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?",
        (config_id, config_version),
    )
    grouped = scalar(
        "SELECT COUNT(*) FROM (SELECT 1 FROM job_match jm JOIN job j ON j.id = jm.job_id"
        " WHERE jm.config_id = ? AND jm.config_version = ?"
        " GROUP BY j.company_id, j.title)",
        (config_id, config_version),
    )
    eligible = scalar(
        "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?"
        " AND eligibility_status != 'VERIFIED_NOT_ELIGIBLE'",
        (config_id, config_version),
    )
    recommended = scalar(
        "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?"
        " AND match_score >= ?",
        (config_id, config_version, shortlist_min),
    )
    # **WHY `scored` CAN BE ZERO WITH A FULL CORPUS.** Every count above is
    # keyed on the configuration IN FORCE, which is right: a score is only true
    # relative to the preferences that produced it. But when that key finds
    # nothing, four rows of this funnel read zero and the screen says
    # `Fetched 22048 / Scored 0` with no hint that 19,469 scores are sitting
    # right there under an earlier version of the same preferences.
    #
    # The owner met exactly that on 2026-09-08 and it looked like data loss.
    # Nothing was lost; the question had changed. Counting the other versions
    # is what lets the screen say so.
    scored_other_versions = scalar(
        "SELECT COUNT(*) FROM job_match WHERE NOT (config_id = ? AND config_version = ?)",
        (config_id, config_version),
    )
    return {
        "fetched": fetched,
        "active": active,
        "normalised": normalised,
        "scored": scored,
        "deduplicated": grouped,
        "eligible": eligible,
        "recommended": recommended,
        "scored_other_versions": scored_other_versions,
    }


class ProfileRetired(Exception):  # noqa: N818 -- a state, not a malfunction
    """A run was asked of an app whose local profile was switched away.

    Deliberately NOT a RuntimeError, which call sites read as "a run is
    already going": the server answers this one with a reload instead."""


class RetrievalRunner:
    """Owns at most one run at a time, and says so rather than starting a second.

    Two concurrent collections would interleave writes to the same boards and
    double the request rate at endpoints this project is deliberately polite
    to. Refusing is the honest answer.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: RetrievalState | None = None
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return self._state.as_dict() if self._state else None

    @property
    def running(self) -> bool:
        with self._lock:
            return bool(self._state and self._state.status == "running")

    def cancel(self) -> bool:
        """Ask the run to stop after the board it is on. Never mid-board."""
        if not self.running:
            return False
        self._cancel.set()
        return True

    def start(self, work: Any, run_id: str) -> dict[str, Any]:
        """Begin a run. `work` is called with (state, cancel_event).

        Injected rather than constructed here so the tests can drive the whole
        lifecycle -- start, progress, cancel, failure -- without a network.
        """
        gate = getattr(self, "admit_lock", None)
        if gate is not None:
            # A local-profile switch holds this lock while it retires the old
            # profile's app: no run may start on a profile that is being left.
            with gate:
                if not self.admit():
                    raise ProfileRetired("this profile is no longer the active one")
                return self._start(work, run_id)
        return self._start(work, run_id)

    def admit(self) -> bool:
        return True

    def _start(self, work: Any, run_id: str) -> dict[str, Any]:
        with self._lock:
            if self._state and self._state.status == "running":
                raise RuntimeError("a retrieval is already running")
            self._state = RetrievalState(run_id=run_id, started_at=_now())
            self._cancel = threading.Event()
            state = self._state

        def runner() -> None:
            try:
                work(state, self._cancel)
                state.status = "cancelled" if self._cancel.is_set() else "done"
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                state.status = "failed"
                state.error = str(exc)
            finally:
                # A collection that ended without finishing its run record
                # must not keep the shared catalogue locked for this process.
                from career_agent.storage.catalogue import release_thread_holds

                release_thread_holds()
                state.current = None
                state.current_started_at = None
                state.finished_at = _now()

        self._thread = threading.Thread(target=runner, name=f"retrieval-{run_id}", daemon=True)
        self._thread.start()
        return state.as_dict()

    def join(self, timeout: float | None = None) -> None:
        """For tests and for a clean shutdown. Never called by a request."""
        if self._thread is not None:
            self._thread.join(timeout)


def provider_by_board(conn: sqlite3.Connection) -> dict[str, str]:
    """`source_board.id -> provider`, so a failure can name its source."""
    try:
        rows = conn.execute("SELECT id, provider FROM source_board").fetchall()
    except sqlite3.Error:
        return {}
    return {str(row["id"]): str(row["provider"]) for row in rows}


def default_db_hint() -> Path:
    from career_agent.runtime.mode import DEFAULT_PERSONAL_DB_PATH

    return DEFAULT_PERSONAL_DB_PATH

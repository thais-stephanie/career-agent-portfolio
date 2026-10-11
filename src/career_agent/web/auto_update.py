"""Optional update of overdue job sites when the app opens. Off until chosen.

WHAT IT IS
----------
A per-profile choice, off by default and changed only by the person
("Update jobs when I open the app, when sites have gone 3 days without an
update"). When on, opening the app serves the interface and the jobs already
held at once; a background run then asks ONLY the sites whose last successful
update is older than three days.

It reuses the source-maintenance engine (`pipeline.maintenance.execute`, ADR-0031):
its inventory of boards and light feeds, its time estimates from measured runs,
its fairness (the longest-waiting site goes first, a rotating first slot keeps
cheap, heavy and unknown work from starving each other), its cool-downs after a
failed attempt, its durable receipt in the run ledger, and its OS lock. Nothing
here collects by itself.

THE BUDGET, AND WHY THESE NUMBERS
---------------------------------
* `BUDGET_SECONDS` = 600. Ten minutes covers dozens of employer boards at the
  polite one-request-a-second pace, and is short enough to be invisible next to
  a working session. A full first collection takes 90 minutes; that is never
  done on opening.
* `MAX_ITEMS` = 60 boards or feeds in one run.
* `MAX_REQUESTS` = 1000 requests in one run, whatever the clock says.
* `MIN_GAP_HOURS` = 6 between runs started by an opening, so reopening the app
  ten times does not ask the same sites ten times.
* `SCORE_LIMIT` = 5000 postings scored after the update. Postings that arrived
  or changed are scored so they can be shown; if more than that are waiting
  (typically because the preferences changed and the whole corpus needs a
  recalculation) nothing is scored automatically and the person is told.

Work that does not fit is left for the next run, in the order of how long each
site has waited. A budget is a limit, not a promise: an update can finish with
sites still pending, and the person is told so.

WHAT IT NEVER DOES
------------------
It starts no model call, no Deep reading and no full recalculation. It does not
ask a site the person paused, one whose terms forbid it, or one that must be run
by the owner by hand. Heavy feeds that cannot honour a budget yet (Workable's
whole-catalogue download, Gupy) are updated only by hand. It does nothing when
the app is closed: there is no service.
"""

from __future__ import annotations

import json
import threading
from contextlib import closing
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from career_agent.clock import new_id
from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import HttpFetcher
from career_agent.runtime import RuntimeMode, read_identity
from career_agent.storage.db import transaction
from career_agent.storage.workspace_repo import (
    CandidateStateRepo,
    candidate_id_of,
    ensure_candidate,
)
from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi

STALE_DAYS = 3
STALE_HOURS = STALE_DAYS * 24
BUDGET_SECONDS = 600
MAX_ITEMS = 60
MAX_REQUESTS = 1000
MIN_GAP_HOURS = 6
SCORE_LIMIT = 5000

KEY_ENABLED = "auto_update.on_open"
KEY_LAST = "auto_update.last_run_at"
KEY_RESULT = "auto_update.last_result"

#: Why a run did not start, as codes the screen words itself.
NOT_STARTED = ("DEMO", "DISABLED", "RECENT", "BUSY", "NOTHING_DUE", "RETIRED")


class _StoppableFetcher(HttpFetcher):
    """The maintenance fetcher, that also stops when the person says Stop."""

    cancel: threading.Event | None = None

    def check_budget(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise BudgetExpired("TIME_BUDGET")
        super().check_budget()


def limits() -> dict[str, int]:
    return {
        "stale_days": STALE_DAYS,
        "minutes": BUDGET_SECONDS // 60,
        "max_sites": MAX_ITEMS,
        "max_requests": MAX_REQUESTS,
        "min_gap_hours": MIN_GAP_HOURS,
        "score_limit": SCORE_LIMIT,
    }


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def profile_blocks(conn: Any) -> dict[str, str]:
    """provider -> why THIS profile does not want it asked: its own pauses."""
    from career_agent.sources.catalogue import resolve
    from career_agent.web.source_refresh import modes

    paused = {source_id for source_id, mode in modes(conn).items() if mode == "PAUSED"}
    if not paused:
        return {}
    return {s.provider: "PAUSED_BY_PROFILE" for s in resolve(conn) if s.id in paused and s.provider}


def plan_now(conn: Any) -> dict[str, Any]:
    """What a run would do now, with a reason for every site: overdue by three
    days, in a cool-down, blocked by policy or by this profile's pauses, or
    left for later because of the budget. Reads only."""
    from dataclasses import replace

    from career_agent.sources.maintenance import inventory, plan, round_number

    blocks = profile_blocks(conn)
    items = [
        replace(i, blocked=i.blocked or blocks[i.provider]) if i.provider in blocks else i
        for i in inventory(conn)
    ]
    return plan(
        items,
        BUDGET_SECONDS,
        stale_hours=STALE_HOURS,
        max_items=MAX_ITEMS,
        round_number=round_number(conn),
    )


def overdue(conn: Any) -> list[str]:
    """The inventory keys a run would ask now."""
    return list(plan_now(conn)["selected"])


def register_auto_update(app: JobsApi) -> None:
    #: Starting is decided under one lock, and the start time is written
    #: before the thread runs: a second tab, window or request that arrives a
    #: moment later finds a recent start and does nothing.
    gate = threading.Lock()

    def personal() -> bool:
        with closing(app.connect()) as conn:
            identity = read_identity(conn)
        return identity is not None and identity.kind is RuntimeMode.PERSONAL

    def read(conn: Any, key: str) -> str | None:
        candidate = candidate_id_of(conn)
        return CandidateStateRepo(conn).get(candidate, key) if candidate else None

    def write(conn: Any, key: str, value: str) -> None:
        CandidateStateRepo(conn).set(ensure_candidate(conn), key, value)

    def status(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "Reading the automatic update takes no parameters.")
        with closing(app.connect()) as conn:
            enabled = read(conn, KEY_ENABLED) == "1"
            last_at = read(conn, KEY_LAST)
            raw = read(conn, KEY_RESULT)
        try:
            result = json.loads(raw) if raw else None
        except ValueError:
            result = None
        return {
            "enabled": enabled,
            "available": personal(),
            "running": bool(app.retrieval.running and _running_kind() == "auto"),
            "last_run_at": last_at,
            "last_result": result,
            "limits": limits(),
        }

    def _running_kind() -> str:
        snap = app.retrieval.snapshot() or {}
        return str(snap.get("kind") or "")

    def patch(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"enabled"} or not isinstance(body["enabled"], bool):
            raise ApiError(400, "Send `enabled` as true or false.")
        if not personal():
            raise ApiError(409, "The demo does not update job sites.", for_reader=True)
        with closing(app.connect()) as conn, transaction(conn):
            write(conn, KEY_ENABLED, "1" if body["enabled"] else "0")
        return status(query={}, body={})

    def run(*, query: dict, body: dict) -> dict:
        """Start the update if the person asked for it and a site is overdue.
        Idempotent: asking again does nothing while a run is going or recent."""
        if query or body:
            raise ApiError(400, "Starting the automatic update takes no parameters.")
        if not personal():
            return {"started": False, "reason": "DEMO"}
        with gate:
            with closing(app.connect()) as conn:
                if read(conn, KEY_ENABLED) != "1":
                    return {"started": False, "reason": "DISABLED"}
                last_at = read(conn, KEY_LAST)
                if last_at and _now() - _parse(last_at) < timedelta(hours=MIN_GAP_HOURS):
                    return {"started": False, "reason": "RECENT"}
                if _busy():
                    return {"started": False, "reason": "BUSY"}
                keys = overdue(conn)
            if not keys:
                return {"started": False, "reason": "NOTHING_DUE"}
            with closing(app.connect()) as conn, transaction(conn):
                write(conn, KEY_LAST, _stamp(_now()))
            try:
                from career_agent.pipeline.retrieval import ProfileRetired

                started = app.retrieval.start(_work(app, write), new_id())
            except RuntimeError:
                return {"started": False, "reason": "BUSY"}
            except ProfileRetired:
                return {"started": False, "reason": "RETIRED"}
            return {"started": True, "planned": len(keys), "run_id": started["run_id"]}

    def _busy() -> bool:
        from career_agent.runtime.maintenance_lock import maintenance_running

        semantic = getattr(app, "semantic_runner", None)
        return bool(
            app.retrieval.running
            or app.rescore.running
            or (semantic is not None and semantic.running)
            or maintenance_running(app.config.db_path)
        )

    app.register("GET", r"/api/auto-update", status)
    app.register("PATCH", r"/api/auto-update", patch)
    app.register("POST", r"/api/auto-update/run", run)


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _work(app: JobsApi, write: Any):
    """The background unit: the maintenance engine inside the app's one run."""

    def work(state: Any, cancel: threading.Event) -> None:
        from career_agent.pipeline.maintenance import execute
        from career_agent.pipeline.retrieval import now_iso
        from career_agent.runtime.maintenance_lock import maintenance_lock

        state.kind = "auto"
        started = _now()
        mark = _last_run_row(app)

        def factory(*, deadline: Any) -> _StoppableFetcher:
            fetcher = _StoppableFetcher(deadline=deadline, request_cap=MAX_REQUESTS)
            fetcher.cancel = cancel
            return fetcher

        def progress(receipt: dict) -> None:
            # Raised between sites, so the one in flight is never cut mid-board.
            if cancel.is_set():
                raise KeyboardInterrupt
            state.plan = list(receipt["selected"])
            state.boards_total = len(receipt["selected"])
            state.boards_done = len(receipt["results"])
            state.current = receipt.get("active")
            state.current_started_at = now_iso()

        receipt: dict = {}
        error = False
        try:
            with closing(app.connect()) as conn:
                blocks = profile_blocks(conn)
            with maintenance_lock(app.config.db_path), closing(app.connect()) as conn:
                receipt = execute(
                    conn,
                    budget_seconds=BUDGET_SECONDS,
                    stale_hours=STALE_HOURS,
                    max_items=MAX_ITEMS,
                    fetcher_factory=factory,
                    on_progress=progress,
                    profile_blocks=blocks,
                )
        except Exception:  # noqa: BLE001 - reported as a result, never as a path
            error = True
        finally:
            state.current = None
            state.current_started_at = None

        results = receipt.get("results", [])
        done = [r for r in results if r.get("attempted")]
        state.boards_done = len(done)
        new_total = _jobs_new_after(app, mark)
        state.jobs_new = new_total
        outcomes = {r.get("outcome") for r in done}
        summary = {
            "at": _stamp(started),
            "planned": len(receipt.get("selected", [])),
            "asked": len(done),
            "updated": sum(1 for r in done if r.get("outcome") == "OK"),
            "failed": sum(1 for r in done if r.get("outcome") == "FAILED"),
            "left_for_later": sum(1 for r in results if not r.get("attempted")),
            "jobs_new": new_total,
            "stopped": (
                "ERROR"
                if error
                else "CANCELLED"
                if cancel.is_set()
                else "BUDGET"
                if any(r.get("reason") == "TIME_BUDGET" for r in results)
                else "DONE"
            ),
            "scoring": "NONE",
        }
        if error:
            state.error = "The update could not finish."
        if outcomes:
            _record_run(app, state, started)
        summary["scoring"] = _score(app)
        with closing(app.connect()) as conn, transaction(conn):
            write(conn, KEY_RESULT, json.dumps(summary))

    return work


def _score(app: JobsApi) -> str:
    """Score what arrived or changed, inside SCORE_LIMIT; never the catalogue.

    `PENDING_LARGE` means more postings wait than the limit (the preferences
    moved, so the whole corpus needs the recalculation the person starts
    themselves); nothing is scored then and they keep what they had."""
    from contextlib import suppress

    from career_agent.pipeline.rescore import plan as rescore_plan

    with closing(app.connect()) as conn:
        waiting = len(rescore_plan(conn, app.search_config()).targets)
    if waiting == 0:
        return "NONE"
    if waiting > SCORE_LIMIT:
        return "PENDING_LARGE"
    if app.rescore.running:
        return "PENDING_BUSY"
    with suppress(RuntimeError):
        app.rescore.start(app._rescore_work(limit=SCORE_LIMIT), new_id())
        return "STARTED"
    return "PENDING_BUSY"


def _last_run_row(app: JobsApi) -> int:
    with closing(app.connect()) as conn:
        row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM pipeline_run").fetchone()
    return int(row[0])


def _jobs_new_after(app: JobsApi, mark: int) -> int:
    total = 0
    with closing(app.connect()) as conn:
        for (raw,) in conn.execute(
            "SELECT stats_json FROM pipeline_run WHERE rowid > ? AND stage LIKE 'collect%'",
            (mark,),
        ):
            try:
                value = json.loads(raw or "{}").get("jobs_new")
            except (ValueError, AttributeError):
                continue
            if isinstance(value, int) and not isinstance(value, bool):
                total += value
    return total


def _record_run(app: JobsApi, state: Any, started: datetime) -> None:
    """The finished run for Home's "New from your latest refresh": its own
    ledger row (`find-jobs`, never `collect%`), counts only."""
    from career_agent.pipeline.retrieval import now_iso
    from career_agent.runtime.mode import record_retrieval
    from career_agent.web.source_refresh import FIND_JOBS_STAGE

    stats = {
        "kind": "auto",
        "planned": len(state.plan),
        "finished": state.boards_done,
        "jobs_new": state.jobs_new,
        "not_refreshed": [],
    }
    with closing(app.connect()) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), FIND_JOBS_STAGE, _stamp(started), now_iso(), "OK", json.dumps(stats)),
        )
        if state.boards_done:
            record_retrieval(conn)

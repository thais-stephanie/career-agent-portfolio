"""Candidate refresh controls reuse existing collectors and their unchanged defaults."""

from __future__ import annotations

import json
import subprocess
import sys
from functools import lru_cache
from typing import TYPE_CHECKING

from career_agent.clock import new_id
from career_agent.runtime import RuntimeMode, read_identity
from career_agent.sources.health import health
from career_agent.sources.matrix import _stage_for
from career_agent.storage.db import transaction
from career_agent.storage.workspace_repo import (
    CandidateStateRepo,
    candidate_id_of,
    ensure_candidate,
)
from career_agent.web.server import ApiError, closing

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi

PREFIX = "source_refresh."
#: The ledger stage of a finished "Find jobs" / refresh run (never `collect%`).
FIND_JOBS_STAGE = "find-jobs"
#: The employer-board discovery step: part of a run, never counted as a source.
DISCOVERY = "employer-boards"
MODES = {"AUTO", "ENABLED", "PAUSED"}


def modes(conn) -> dict[str, str]:
    candidate = candidate_id_of(conn)
    return {
        row["key"][len(PREFIX) :]: row["value"]
        for row in conn.execute(
            "SELECT key, value FROM candidate_state WHERE candidate_id = ? AND key LIKE ?",
            (candidate, PREFIX + "%"),
        )
        if row["value"] in MODES
    }


@lru_cache(maxsize=1)
def commands() -> frozenset[str]:
    from career_agent.cli import app

    return frozenset(
        c.name for c in app.registered_commands if c.name and c.name.startswith("collect-")
    )


def experimental_available(provider: str) -> bool:
    """Whether an experimental adapter's library is installed here."""
    from career_agent.providers import linkedin_jobspy

    return provider != linkedin_jobspy.PROVIDER or linkedin_jobspy.available()


def opted_in(conn, entries) -> frozenset[str]:
    """Rows whose experimental override this profile explicitly switched on."""
    from career_agent.sources.experimental import opted_in_sources

    ids = [e.source.id for e in entries if e.source.experimental_provider]
    return frozenset(opted_in_sources(conn, ids)) if ids else frozenset()


def effective_provider(entry, opted: frozenset[str] = frozenset()) -> str | None:
    """The adapter a row runs: its own, or its experimental override when this
    profile opted in. A row's declared permission is never consulted here for
    the override: it stays FORBIDDEN, and the opt-in is the exception."""
    source = entry.source
    if source.provider:
        return source.provider
    if source.experimental_provider and source.id in opted:
        return source.experimental_provider
    return None


def can_refresh(entry, opted: frozenset[str] = frozenset()) -> bool:
    source = entry.source
    if not source.provider and source.experimental_provider:
        return bool(
            source.id in opted
            and not source.collection_blocker
            and experimental_available(source.experimental_provider)
            and _stage_for(source.experimental_provider) in commands()
        )
    return bool(
        source.provider
        and not source.collection_blocker
        and source.permission.value != "FORBIDDEN"
        and entry.state not in {"BLOCKED_PROVIDER", "BLOCKED", "FORBIDDEN", "DISABLED_QUOTA"}
        and (_stage_for(source.provider) == "collect" or _stage_for(source.provider) in commands())
    )


def warm_commands() -> None:
    """Read the collector list on a background thread, once per process.

    `commands()` imports the whole command-line app the first time it runs.
    Done inside the first "Find jobs" request, that import held the request
    for seconds while the button said only "Starting", which reads as frozen.
    """
    import threading

    if commands.cache_info().currsize:
        return
    threading.Thread(target=commands, name="warm-collectors", daemon=True).start()


def register_source_refresh(app: JobsApi) -> None:
    warm_commands()

    def maintenance_status(*, query: dict, body: dict) -> dict:
        from career_agent.runtime.maintenance_lock import maintenance_running
        from career_agent.sources.maintenance import freshness, read_only

        if query or body:
            raise ApiError(400, "Freshness status takes no parameters.")
        with read_only(app.config.db_path) as conn:
            result = freshness(conn, app.config.config_dir / "source_catalogue.yaml")
        result["app_refresh_running"] = app.retrieval.running
        result["maintenance_running"] = maintenance_running(app.config.db_path)
        return result

    app.register("GET", r"/api/source-maintenance", maintenance_status)

    def source(body):
        with closing(app.connect()) as conn:
            entries = health(conn, catalogue_path=app.config.config_dir / "source_catalogue.yaml")
        entry = next((e for e in entries if e.source.id == body.get("source_id")), None)
        if entry is None:
            raise ApiError(400, "Choose an existing source.", for_reader=True)
        return entry

    def schedule(*, query: dict, body: dict) -> dict:
        if (
            query
            or set(body) != {"source_id", "mode"}
            or not isinstance(body.get("mode"), str)
            or body.get("mode") not in MODES
        ):
            raise ApiError(400, "Choose automatic, enabled or paused refresh.")
        entry = source(body)
        with closing(app.connect()) as conn:
            opted = opted_in(conn, [entry])
        if not can_refresh(entry, opted):
            raise ApiError(
                409, "This source is currently unavailable for refresh.", for_reader=True
            )
        with closing(app.connect()) as conn, transaction(conn):
            CandidateStateRepo(conn).set(
                ensure_candidate(conn), PREFIX + entry.source.id, body["mode"]
            )
        return {"source_id": entry.source.id, "mode": body["mode"]}

    def _step(entry, opted):
        """One source as one step: (source id, name, work, is a board family)."""
        provider = effective_provider(entry, opted) or ""
        stage = _stage_for(provider)
        if stage == "collect":
            return entry.source.id, entry.source.name, app._collect_work(provider=provider), True
        work = feed_work(app.config.db_path, stage, config_dir=app.config.config_dir)
        return entry.source.id, entry.source.name, work, False

    def refresh(*, query: dict, body: dict) -> dict:
        """Refresh now: exactly this one source, as a one-step plan."""
        if query or set(body) != {"source_id"}:
            raise ApiError(400, "Choose one source to refresh.")
        entry = source(body)
        with closing(app.connect()) as conn:
            opted = opted_in(conn, [entry])
            identity = read_identity(conn)
        if not can_refresh(entry, opted):
            raise ApiError(
                409, "This source is currently unavailable for refresh.", for_reader=True
            )
        cooling = next(
            (
                p
                for p in app._refresh_progress([entry])
                if p.state == "RATE_LIMITED" and p.cooldown_until
            ),
            None,
        )
        if cooling is not None:
            raise ApiError(
                409,
                "This site refused the last requests. Career Agent will not ask it again"
                f" before {cooling.cooldown_until[:16].replace('T', ' ')} UTC.",
                for_reader=True,
            )
        if not identity or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(409, "Demo databases do not collect live jobs.", for_reader=True)
        if app.retrieval.running:
            raise ApiError(409, "A refresh is already running.", for_reader=True)
        from career_agent.runtime.maintenance_lock import maintenance_running

        if maintenance_running(app.config.db_path):
            raise ApiError(
                409,
                "A source refresh started from the command window is still running. "
                "Try again when it finishes.",
                for_reader=True,
            )
        source_id, name, work, _family = _step(entry, opted)
        return _run([(source_id, name, work, True)], kind="source")

    def _refuse_unless_ready(identity) -> None:
        if not identity or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(
                409,
                "The demo never collects live jobs. Start Career Agent normally to find real ones.",
                for_reader=True,
            )
        if app.retrieval.running:
            raise ApiError(
                409,
                "Career Agent is already looking for jobs. Wait for it to finish.",
                for_reader=True,
            )
        from career_agent.runtime.maintenance_lock import maintenance_running

        if maintenance_running(app.config.db_path):
            raise ApiError(
                409,
                "A source refresh started from the command window is still running. "
                "Try again when it finishes.",
                for_reader=True,
            )

    def _plan(entries, opted, rows, *, only: set[str] | None):
        """The steps for `entries`, in the order they must run, and the names of
        the sources left out that still hold jobs from an earlier refresh.

        One policy for "Find jobs", "Refresh due sources" and "Refresh all
        available sources": the sources `can_refresh` admits, minus every
        PAUSED one and every one still cooling down after a refusal; with
        `only`, just those source ids. A board family runs once whichever of
        its rows asked for it."""
        paused = {p.source_id for p in rows if p.state == "PAUSED"}
        # A wait is respected by every plan: a source the site refused, or one
        # that just failed, is not asked again before its wait is over.
        refused = {p.source_id for p in rows if p.cooldown_until}
        steps: list[tuple[str, str, object, bool]] = []
        seen: set[str] = set()
        for entry in entries:
            if only is not None and entry.source.id not in only:
                continue
            provider = effective_provider(entry, opted)
            if not provider or not can_refresh(entry, opted):
                continue
            if entry.source.id in paused or entry.source.id in refused:
                continue
            stage = _stage_for(provider)
            # Board families share one collector per provider; every other
            # source is its own `collect-*` command. Never run one twice.
            key = provider if stage == "collect" else stage
            if key in seen:
                continue
            seen.add(key)
            steps.append(_step(entry, opted))
        # Feeds first, then employer board discovery (it reads the employers
        # the feeds just brought in), then the employer boards themselves, so
        # a board found this run is collected this run. Discovery is not a
        # source and is not counted as one.
        boards = [s for s in steps if s[3]]
        steps = [s for s in steps if not s[3]]
        from career_agent.pipeline.employer_boards import FAMILIES

        probe = tuple(f for f in FAMILIES if f in seen)
        if probe:
            steps.append((DISCOVERY, "Employer job boards", employer_board_work(app, probe), False))
        steps.extend(boards)
        planned = {s[0] for s in steps}
        names = {e.source.id: e.source.name for e in entries}
        held_back = sorted(
            names.get(p.source_id, p.source_id)
            for p in rows
            if p.source_id not in planned
            and p.last_success
            and p.state not in ("COMPLETE", "PARTIAL", "RUNNING")
        )
        return steps, held_back

    def _run(steps, *, kind: str, held_back=()):
        counted = [s for s in steps if s[0] != DISCOVERY]

        def all_sources(state, cancel):
            from contextlib import suppress

            from career_agent.pipeline.retrieval import RetrievalState, SourceOutcome, now_iso

            state.kind = kind
            state.plan = [name for _id, name, _work, _family in counted]
            state.boards_total = len(counted)
            state.not_refreshed = list(held_back)
            outcomes: list[dict] = []
            new_total = 0
            # The shipped employer boards, added to a database that lacks them.
            # In the worker rather than the request: it can wait behind another
            # writer, and a registry that no longer parses changes nothing.
            from career_agent.config.registry import sync_registry_quietly

            with closing(app.connect()) as conn:
                sync_registry_quietly(conn, app.config.config_dir)
            try:
                for source_id, name, work, _family in steps:
                    if cancel.is_set():
                        break
                    state.current = name
                    state.current_started_at = now_iso()
                    app._active_source_refresh = source_id
                    # Each source's work reports into its own state, so its
                    # board counters do not overwrite "sources checked".
                    inner = RetrievalState(run_id=state.run_id, started_at=state.started_at)
                    state.inner = inner
                    outcome = SourceOutcome(provider=name, boards_attempted=1)
                    mark = _last_run_row()
                    try:
                        work(inner, cancel)  # type: ignore[operator]
                        outcome.boards_succeeded = 1
                    except Exception:  # noqa: BLE001 -- one source, reported
                        # Never the exception text: it can carry a local path.
                        outcome.boards_failed = 1
                        outcome.failures.append("This source could not be read this time.")
                    # What this source's own runs added, from their own stats:
                    # a posting another source already held is a sighting
                    # there, never a new job, so the sum counts each job once.
                    outcome.jobs_new = _jobs_new_after(mark)
                    new_total += outcome.jobs_new
                    if source_id == DISCOVERY:
                        continue
                    if outcome.boards_failed:
                        state.not_refreshed.append(name)
                    outcomes.append(outcome.as_dict())
                    state.sources = list(outcomes)
                    state.boards_done = len(outcomes)
            finally:
                state.current = None
                state.current_started_at = None
                state.inner = None
                app._active_source_refresh = None
            if not cancel.is_set():
                state.jobs_new = new_total
                _record_run(state)
            # Score what arrived. Targeted: only postings without a current
            # score, so this is proportional to what was collected. Never a
            # semantic (paid) pass: that is only ever started by the person.
            if not app.rescore.running:
                with suppress(RuntimeError):
                    app.rescore.start(app._rescore_work(), new_id())

        try:
            return app.retrieval.start(all_sources, new_id())
        except RuntimeError as exc:
            raise ApiError(
                409,
                "Career Agent is already looking for jobs. Wait for it to finish.",
                for_reader=True,
            ) from exc

    def _last_run_row() -> int:
        with closing(app.connect()) as conn:
            row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM pipeline_run").fetchone()
        return int(row[0])

    def _jobs_new_after(mark: int) -> int:
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

    def _record_run(state) -> None:
        """The finished run, for Home's "New from your latest refresh".

        Its own ledger row (stage `find-jobs`, never `collect%`, so nothing
        reads it as a collection): counts and source names only."""
        from career_agent.pipeline.retrieval import now_iso
        from career_agent.runtime.mode import record_retrieval

        read_any = any(row.get("status") == "ok" for row in state.sources)
        stats = {
            "kind": state.kind,
            "planned": len(state.plan),
            "finished": state.boards_done,
            "jobs_new": state.jobs_new,
            "not_refreshed": list(state.not_refreshed),
        }
        with closing(app.connect()) as conn, transaction(conn):
            conn.execute(
                "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    FIND_JOBS_STAGE,
                    state.started_at,
                    now_iso(),
                    "OK" if read_any else "FAILED",
                    json.dumps(stats),
                ),
            )
            if read_any:
                record_retrieval(conn)

    def _start(kind: str, *, only_due: bool) -> dict:
        with closing(app.connect()) as conn:
            identity = read_identity(conn)
            entries = health(conn, catalogue_path=app.config.config_dir / "source_catalogue.yaml")
            opted = opted_in(conn, entries)
        _refuse_unless_ready(identity)
        # THE PLAN IS DECIDED HERE, ONCE. What is due is read at the press and
        # frozen into the run; a source turning fresh or due while it runs
        # changes the next press, never this run's total.
        rows = app._refresh_progress(entries)
        due = {p.source_id for p in rows if p.due} if only_due else None
        waiting = sorted(p.source_id for p in rows if p.cooldown_until)
        steps, held_back = _plan(entries, opted, rows, only=due)
        planned = sum(1 for s in steps if s[0] != DISCOVERY)
        if not planned:
            fresh = any(p.state in ("COMPLETE", "PARTIAL") and not p.cooldown_until for p in rows)
            if only_due and fresh:
                return {"started": False, "due": 0, "cooling_down": waiting}
            if waiting:
                raise ApiError(
                    409,
                    "The sources you can use refused the last requests and are cooling down. "
                    "Try again later; Settings & Sources says when.",
                    for_reader=True,
                )
            raise ApiError(
                409,
                "No job source is switched on for the places you can work. "
                "Open Settings & Sources to turn one on.",
                for_reader=True,
            )
        run = _run(steps, kind=kind, held_back=held_back)
        return {**run, "started": True, "due": planned, "cooling_down": waiting}

    def refresh_all(*, query: dict, body: dict) -> dict:
        """Refresh all available sources: every source `can_refresh` admits,
        fresh ones included, minus paused and cooling-down ones. A deliberate
        full refresh; "Find jobs" is `refresh_due`. One source failing does not
        stop the rest; cancelling stops after the source in flight. New
        postings are then scored by the normal targeted rescore."""
        if query or body:
            raise ApiError(400, "Finding jobs takes no parameters.")
        return _start("all", only_due=False)

    def refresh_due(*, query: dict, body: dict) -> dict:
        """Find jobs, and Refresh due sources: only the sources due right now.

        Due is one rule (`SourceProgress.due`): never refreshed, older than a
        day, stale, or failed or refused and past its cooldown. Nothing paused,
        blocked, running or still cooling down; nothing switched on by this
        button. An experimental source (LinkedIn) is here only when this
        profile already opted in. Nothing runs when nothing is due.
        """
        if query or body:
            raise ApiError(400, "Refreshing due sources takes no parameters.")
        return _start("due", only_due=True)

    def experimental(*, query: dict, body: dict) -> dict:
        """Switch a row's local experimental override on or off, for this profile.

        ON requires `acknowledged: true`: the screen shows what the source
        says about automated access first, and the person confirms they read
        it. The row's own permission is not touched. OFF is always allowed.
        """
        if (
            query
            or set(body) - {"source_id", "opted_in", "acknowledged"}
            or not isinstance(body.get("opted_in"), bool)
        ):
            raise ApiError(400, "Say whether to switch the experimental source on or off.")
        entry = source(body)
        if not entry.source.experimental_provider:
            raise ApiError(400, "This source has no experimental option.", for_reader=True)
        with closing(app.connect()) as conn:
            identity = read_identity(conn)
        if not identity or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(
                409,
                "The demo never collects live jobs, so it has nothing to switch on.",
                for_reader=True,
            )
        if body["opted_in"] and body.get("acknowledged") is not True:
            raise ApiError(
                400,
                "Read the warning and confirm you understand it before switching this on.",
                for_reader=True,
            )
        from career_agent.sources.experimental import set_opt_in

        with closing(app.connect()) as conn, transaction(conn):
            value = set_opt_in(conn, entry.source.id, body["opted_in"])
        return {"source_id": entry.source.id, **value}

    app.register("PATCH", r"/api/sources/experimental", experimental)
    app.register("PATCH", r"/api/sources/schedule", schedule)
    app.register("POST", r"/api/sources/refresh", refresh)
    app.register("POST", r"/api/sources/refresh-all", refresh_all)
    app.register("POST", r"/api/sources/refresh-due", refresh_due)


def employer_board_work(app, families):
    """Find the ATS boards of employers the feeds have shown, bounded."""

    def work(state, cancel):
        from contextlib import closing as _closing

        from career_agent.domain.enums import PipelineRunStatus
        from career_agent.net.fetcher import HttpFetcher
        from career_agent.pipeline.employer_boards import discover_employer_boards
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import PipelineRunRepo

        with _closing(app.connect()) as conn, HttpFetcher() as fetcher:
            runs = PipelineRunRepo(conn)
            with transaction(conn):
                run_id = runs.start("discover-employer-boards")
            try:
                stats = discover_employer_boards(
                    conn, fetcher, should_stop=cancel.is_set, families=families
                )
            except Exception as exc:
                with transaction(conn):
                    runs.finish(
                        run_id, PipelineRunStatus.FAILED, stats={}, error=type(exc).__name__
                    )
                raise
            with transaction(conn):
                runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)

    return work


def _ledger_mark(db_path) -> int:
    import sqlite3
    from contextlib import closing as _closing

    from career_agent.storage.db import connect

    # A database the collector has not migrated yet has no ledger: nothing
    # can be left open in it.
    try:
        with _closing(connect(db_path)) as conn:
            row = conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM pipeline_run").fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row[0])


def _close_abandoned(db_path, stage: str, mark: int) -> None:
    """A collector stopped or killed mid-run leaves its ledger row open, which
    reads as RUNNING for good: never due, never listed, and every refresh
    button disabled. Close it as FAILED, so it waits its hour and is due again."""
    import sqlite3
    from contextlib import closing as _closing

    from career_agent.clock import now_utc
    from career_agent.storage.db import connect

    try:
        with _closing(connect(db_path)) as conn, transaction(conn):
            conn.execute(
                "UPDATE pipeline_run SET finished_at = ?, status = 'FAILED',"
                " error = COALESCE(error, 'stopped before it finished')"
                " WHERE stage = ? AND rowid > ? AND finished_at IS NULL",
                (now_utc(), stage, mark),
            )
    except sqlite3.OperationalError:
        return


#: Collectors that read the person's settings, and so are told where they are.
READS_CONFIG = frozenset({"collect-himalayas", "collect-linkedin"})


def feed_work(db_path, stage, *, config_dir=None):
    if stage not in commands():
        raise ValueError("No existing collection command supports this source.")

    def work(state, cancel):
        # Argument list, no shell and no candidate preference passed to ingestion.
        # CLI defaults retain their existing page budgets and collection policy.
        import tempfile

        # stderr goes to a temporary file, not to nowhere: a collector that
        # crashed used to leave "could not be read" and nothing else, while the
        # traceback that said why was discarded. Its tail now travels with the
        # failure. A file rather than a pipe, so a chatty child can never fill
        # a pipe buffer and hang.
        # Before the child starts, so its own ledger row is always after it.
        mark = _ledger_mark(db_path)
        with (
            tempfile.TemporaryFile() as errors,
            subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "career_agent.cli",
                    stage,
                    "--db",
                    str(db_path),
                    *(
                        ["--config-dir", str(config_dir)]
                        if config_dir is not None and stage in READS_CONFIG
                        else []
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=errors,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as process,
        ):
            while process.poll() is None:
                if cancel.wait(0.25):
                    process.terminate()
                    process.wait(timeout=10)
                    _close_abandoned(db_path, stage, mark)
                    return
            if process.returncode:
                # Only a child that did not end cleanly can have left its row
                # open; a clean exit closed its own.
                _close_abandoned(db_path, stage, mark)
                errors.seek(0)
                tail = errors.read()[-600:].decode("utf-8", errors="replace").strip()
                last = tail.splitlines()[-1] if tail else "no error output"
                # To the local console only: the line can carry a local path,
                # and what reaches a screen says which source, never why.
                import logging

                logging.getLogger(__name__).warning("%s failed: %s", stage, last[:300])
                raise RuntimeError("The source refresh failed.")

    return work

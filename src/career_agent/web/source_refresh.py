"""Candidate refresh controls reuse existing collectors and their unchanged defaults."""

from __future__ import annotations

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

    def refresh(*, query: dict, body: dict) -> dict:
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
        if not identity or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(409, "Demo databases do not collect live jobs.", for_reader=True)
        if app.retrieval.running:
            raise ApiError(409, "A refresh is already running.", for_reader=True)
        provider = effective_provider(entry, opted)
        stage = _stage_for(provider or "")
        if stage == "collect":
            work = app._collect_work(None, provider=provider)
        else:
            work = feed_work(app.config.db_path, stage, config_dir=app.config.config_dir)

        # The runner is shared with the existing refresh action, so a second
        # click cannot start competing collectors in this server.
        def selected_work(state, cancel):
            app._active_source_refresh = entry.source.id
            return work(state, cancel)

        try:
            return app.retrieval.start(selected_work, new_id())
        except RuntimeError as exc:
            raise ApiError(409, "A refresh is already running.", for_reader=True) from exc

    def refresh_all(*, query: dict, body: dict) -> dict:
        """Find jobs: every source a per-source button would refresh, in turn.

        A fresh install had no single way to collect anything. Each source had
        its own "Refresh now", one run at a time, so a first search meant about
        twenty clicks with a wait between each -- and the only all-at-once
        control collected just the employer boards already in the database,
        which a fresh database has none of.

        This adds no collection policy of its own. The sources are exactly the
        ones `can_refresh` admits, minus every one the person or their target
        markets have PAUSED (the same verdict `/api/sources` shows beside each
        row), and each is run through the same work its own button starts. One
        source failing does not stop the rest; cancelling stops after the source
        in flight. New postings are then scored by the normal targeted rescore.
        """
        if query or body:
            raise ApiError(400, "Finding jobs takes no parameters.")
        with closing(app.connect()) as conn:
            identity = read_identity(conn)
            entries = health(conn, catalogue_path=app.config.config_dir / "source_catalogue.yaml")
            opted = opted_in(conn, entries)
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

        paused = {p.source_id for p in app._refresh_progress(entries) if p.state == "PAUSED"}
        steps: list[tuple[str, str, object]] = []
        board_ids: set[str] = set()
        seen: set[str] = set()
        #: Sources that could run but are paused -- by the person, or because
        #: they serve none of the places they can work. Counted in the same
        #: unit as "N of M" (one per collector) and said, not silently missing.
        deferred: set[str] = set()
        for entry in entries:
            provider = effective_provider(entry, opted)
            if not provider or not can_refresh(entry, opted):
                continue
            stage = _stage_for(provider)
            # Board families share one collector per provider; every other
            # source is its own `collect-*` command. Never run one twice.
            key = provider if stage == "collect" else stage
            if entry.source.id in paused:
                deferred.add(key)
                continue
            if key in seen:
                continue
            seen.add(key)
            work = (
                app._collect_work(None, provider=provider)
                if stage == "collect"
                else feed_work(app.config.db_path, stage, config_dir=app.config.config_dir)
            )
            if stage == "collect":
                board_ids.add(entry.source.id)
            steps.append((entry.source.id, entry.source.name, work))
        if not steps:
            raise ApiError(
                409,
                "No job source is switched on for the places you can work. "
                "Open Settings & Sources to turn one on.",
                for_reader=True,
            )
        # Feeds first, then employer board discovery (it reads the employers
        # the feeds just brought in), then the employer boards themselves, so
        # a board found this run is collected this run.
        boards = [s for s in steps if s[0] in board_ids]
        steps = [s for s in steps if s[0] not in board_ids]
        # Only on the families the person has not paused, and not at all when
        # every one of them is paused or blocked.
        from career_agent.pipeline.employer_boards import FAMILIES

        probe = tuple(f for f in FAMILIES if f in seen)
        if probe:
            steps.append(
                ("employer-boards", "Employer job boards", employer_board_work(app, probe))
            )
        steps.extend(boards)

        def all_sources(state, cancel):
            from contextlib import suppress

            from career_agent.pipeline.retrieval import RetrievalState, SourceOutcome, now_iso

            state.boards_total = len(steps)
            state.skipped = len(deferred - seen)
            outcomes: list[dict] = []
            # The shipped employer boards, added to a database that lacks them.
            # In the worker rather than the request: it can wait behind another
            # writer, and a registry that no longer parses changes nothing.
            from career_agent.config.registry import sync_registry_quietly

            with closing(app.connect()) as conn:
                sync_registry_quietly(conn, app.config.config_dir)
            try:
                for done, (source_id, name, work) in enumerate(steps):
                    if cancel.is_set():
                        break
                    state.boards_done = done
                    state.current = name
                    state.current_started_at = now_iso()
                    app._active_source_refresh = source_id
                    # Each source's work reports into its own state, so its
                    # board counters do not overwrite "sources checked".
                    inner = RetrievalState(run_id=state.run_id, started_at=state.started_at)
                    outcome = SourceOutcome(provider=name, boards_attempted=1)
                    try:
                        work(inner, cancel)  # type: ignore[operator]
                        outcome.boards_succeeded = 1
                    except Exception:  # noqa: BLE001 -- one source, reported
                        # Never the exception text: it can carry a local path.
                        outcome.boards_failed = 1
                        outcome.failures.append("This source could not be read this time.")
                    outcomes.append(outcome.as_dict())
                    state.sources = list(outcomes)
                state.boards_done = len(outcomes)
            finally:
                state.current = None
                state.current_started_at = None
                app._active_source_refresh = None
            # Score what arrived. Targeted: only postings without a current
            # score, so this is proportional to what was collected.
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
                    return
            if process.returncode:
                errors.seek(0)
                tail = errors.read()[-600:].decode("utf-8", errors="replace").strip()
                last = tail.splitlines()[-1] if tail else "no error output"
                # To the local console only: the line can carry a local path,
                # and what reaches a screen says which source, never why.
                import logging

                logging.getLogger(__name__).warning("%s failed: %s", stage, last[:300])
                raise RuntimeError("The source refresh failed.")

    return work

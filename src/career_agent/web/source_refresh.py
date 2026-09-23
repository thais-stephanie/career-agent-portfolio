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


def can_refresh(entry) -> bool:
    source = entry.source
    return bool(
        source.provider
        and not source.collection_blocker
        and source.permission.value != "FORBIDDEN"
        and entry.state not in {"BLOCKED_PROVIDER", "BLOCKED", "FORBIDDEN", "DISABLED_QUOTA"}
        and (_stage_for(source.provider) == "collect" or _stage_for(source.provider) in commands())
    )


def register_source_refresh(app: JobsApi) -> None:
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
        if not can_refresh(entry):
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
        if not can_refresh(entry):
            raise ApiError(
                409, "This source is currently unavailable for refresh.", for_reader=True
            )
        with closing(app.connect()) as conn:
            identity = read_identity(conn)
        if not identity or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(409, "Demo databases do not collect live jobs.", for_reader=True)
        if app.retrieval.running:
            raise ApiError(409, "A refresh is already running.", for_reader=True)
        provider = entry.source.provider
        stage = _stage_for(provider)
        if stage == "collect":
            work = app._collect_work(None, provider=provider)
        else:
            work = feed_work(app.config.db_path, stage)

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
        seen: set[str] = set()
        for entry in entries:
            provider = entry.source.provider
            if not provider or not can_refresh(entry) or entry.source.id in paused:
                continue
            stage = _stage_for(provider)
            # Board families share one collector per provider; every other
            # source is its own `collect-*` command. Never run one twice.
            key = provider if stage == "collect" else stage
            if key in seen:
                continue
            seen.add(key)
            work = (
                app._collect_work(None, provider=provider)
                if stage == "collect"
                else feed_work(app.config.db_path, stage)
            )
            steps.append((entry.source.id, entry.source.name, work))
        if not steps:
            raise ApiError(
                409,
                "No job source is switched on for the places you can work. "
                "Open Settings & Sources to turn one on.",
                for_reader=True,
            )

        def all_sources(state, cancel):
            from contextlib import suppress

            from career_agent.pipeline.retrieval import RetrievalState, SourceOutcome

            state.boards_total = len(steps)
            outcomes: list[dict] = []
            try:
                for done, (source_id, name, work) in enumerate(steps):
                    if cancel.is_set():
                        break
                    state.boards_done = done
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

    app.register("PATCH", r"/api/sources/schedule", schedule)
    app.register("POST", r"/api/sources/refresh", refresh)
    app.register("POST", r"/api/sources/refresh-all", refresh_all)


def feed_work(db_path, stage):
    if stage not in commands():
        raise ValueError("No existing collection command supports this source.")

    def work(state, cancel):
        # Argument list, no shell and no candidate preference passed to ingestion.
        # CLI defaults retain their existing page budgets and collection policy.
        with subprocess.Popen(
            [sys.executable, "-m", "career_agent.cli", stage, "--db", str(db_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ) as process:
            while process.poll() is None:
                if cancel.wait(0.25):
                    process.terminate()
                    process.wait(timeout=10)
                    return
            if process.returncode:
                raise RuntimeError("The source refresh failed. Check its recorded source details.")

    return work

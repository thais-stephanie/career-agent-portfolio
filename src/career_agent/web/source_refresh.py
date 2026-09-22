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

    app.register("PATCH", r"/api/sources/schedule", schedule)
    app.register("POST", r"/api/sources/refresh", refresh)


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

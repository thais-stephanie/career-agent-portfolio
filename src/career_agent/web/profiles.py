"""Switching local profiles inside the one running app.

ONE PROCESS, ONE WRITER PER PROFILE. The launcher runs Career Agent and
Resume Tailor in one process. A switch builds a fresh `JobsApi` for the chosen
profile (its own database, settings and caches), checks the database belongs
to that profile, and swaps it in for the next request; Resume Tailor is
rebuilt on that profile's own workspace and swapped the same way. Nothing of
the previous profile's in-memory state survives: search settings, band
caches and background runners all belong to the old instance.

A switch is REFUSED while a collection, a scoring pass or a semantic run is in
progress: those threads write the current profile's database and must not be
cut off, and must never be left running against a profile that is no longer
on screen. The page reloads after a switch, so no screen keeps the previous
person's data in memory either.

`career-agent serve` and the demo run without a host: there, `/api/profiles`
says profiles are unavailable and nothing can be switched.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from career_agent.runtime.profiles import (
    Profile,
    ProfileError,
    bind_database,
    create_profile,
    delete_profile,
    ensure_registry,
    load_registry,
    rename_profile,
    set_active,
    sync_shipped_config,
)
from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi


def tailor_environment(root: Path, profile: Profile) -> None:
    """Point Resume Tailor at this profile's own workspace."""
    home = root / profile.tailor_home
    home.mkdir(parents=True, exist_ok=True)
    os.environ["RESUME_TAILOR_HOME"] = str(home)
    os.environ["RESUME_TAILOR_DATA"] = str(home / "runtime")


class SwitchableApp:
    """An ASGI app that forwards to whichever inner app is current."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        await self.inner(scope, receive, send)


class ProfileHost:
    def __init__(
        self,
        root: Path,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        tailor: SwitchableApp | None = None,
        tailor_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.root = root
        self.host = host
        self.port = port
        self.tailor = tailor
        self.tailor_factory = tailor_factory
        self.server: Any = None
        self._lock = threading.Lock()

    # -- building a profile's app ------------------------------------------

    def open(self, profile: Profile) -> JobsApi:
        """A `JobsApi` for `profile`, after checking its database is its own."""
        from career_agent.runtime import RuntimeMode, identity_of
        from career_agent.storage.db import connect, migrate
        from career_agent.web.api import JobsApi
        from career_agent.web.server import ServerConfig

        db, config_dir, _ = profile.paths(self.root)
        if not db.exists():
            raise ProfileError("This profile's database is missing.")
        sync_shipped_config(self.root, profile)
        conn = connect(db)
        try:
            migrate(conn)
            identity_of(conn, RuntimeMode.PERSONAL)
            bind_database(conn, profile.id)
            # The employer boards the product ships with, added to a new
            # profile's database. Additive only.
            from career_agent.config.registry import sync_registry_quietly

            sync_registry_quietly(conn, config_dir)
        finally:
            conn.close()
        api = JobsApi(
            ServerConfig(db_path=db, config_dir=config_dir, host=self.host, port=self.port)
        )
        api.profile_host = self  # type: ignore[attr-defined]
        return api

    def current(self) -> JobsApi | None:
        if self.server is None:
            return None
        return self.server.RequestHandlerClass.app

    @staticmethod
    def busy(api: JobsApi | None) -> str | None:
        if api is None:
            return None
        if api.retrieval.running:
            return "Career Agent is looking for jobs. Switch when it finishes."
        if api.rescore.running:
            return "Career Agent is updating Search Fit. Switch when it finishes."
        runner = getattr(api, "semantic_runner", None)
        if runner is not None and runner.running:
            return "Semantic matching is running. Switch when it finishes."
        return None

    def switch(self, profile_id: str) -> Profile:
        with self._lock:
            registry = load_registry(self.root) or ensure_registry(self.root)
            profile = registry.get(profile_id)
            reason = self.busy(self.current())
            if reason:
                raise ApiError(409, reason, for_reader=True)
            api = self.open(profile)
            if self.tailor is not None and self.tailor_factory is not None:
                tailor_environment(self.root, profile)
                self.tailor.inner = self.tailor_factory()
            if self.server is not None:
                self.server.RequestHandlerClass.app = api
            set_active(self.root, profile.id)
            return profile


def _host(app: JobsApi) -> ProfileHost:
    host = getattr(app, "profile_host", None)
    if host is None:
        raise ApiError(
            409,
            "Profiles are available when Career Agent is started with its launcher.",
            for_reader=True,
        )
    return host


def _listing(host: ProfileHost) -> dict[str, Any]:
    registry = load_registry(host.root) or ensure_registry(host.root)
    return {
        "enabled": True,
        "active": registry.current.public(),
        "profiles": [
            {**p.public(), "active": p.id == registry.active, "original": p.legacy}
            for p in registry.profiles
        ],
        # Said by the server, so no screen can drift from it.
        "note": (
            "Local profiles keep each person's data apart in this app. "
            "They are not accounts and not a security boundary."
        ),
    }


def register_profiles(app: JobsApi) -> None:
    def listing(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "Listing profiles takes no parameters.")
        host = getattr(app, "profile_host", None)
        if host is None:
            return {"enabled": False, "active": None, "profiles": []}
        return _listing(host)

    def create(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"label"} or not isinstance(body["label"], str):
            raise ApiError(400, "Give the new profile a name.")
        host = _host(app)
        try:
            profile = create_profile(host.root, body["label"])
        except ProfileError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        return {"created": profile.public(), **_listing(host)}

    def rename(*, profile_id: str, query: dict, body: dict) -> dict:
        if query or set(body) != {"label"} or not isinstance(body["label"], str):
            raise ApiError(400, "Give the profile a name.")
        host = _host(app)
        try:
            rename_profile(host.root, profile_id, body["label"])
        except ProfileError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        return _listing(host)

    def switch(*, query: dict, body: dict) -> dict:
        if query or set(body) != {"profile_id"} or not isinstance(body["profile_id"], str):
            raise ApiError(400, "Choose a profile.")
        host = _host(app)
        try:
            profile = host.switch(body["profile_id"])
        except ProfileError as exc:
            raise ApiError(409, str(exc), for_reader=True) from exc
        # The page reloads: nothing on screen keeps the previous profile.
        return {"switched": True, "active": profile.public(), "reload": True}

    def remove(*, profile_id: str, query: dict, body: dict) -> dict:
        if query or set(body) != {"confirm_label"} or not isinstance(body["confirm_label"], str):
            raise ApiError(400, "Type the profile's name to confirm.")
        host = _host(app)
        try:
            delete_profile(host.root, profile_id, body["confirm_label"])
        except ProfileError as exc:
            raise ApiError(409, str(exc), for_reader=True) from exc
        return {"deleted": profile_id, **_listing(host)}

    app.register("GET", r"/api/profiles", listing)
    app.register("POST", r"/api/profiles", create)
    app.register("POST", r"/api/profiles/switch", switch)
    app.register("PATCH", r"/api/profiles/(?P<profile_id>prof-[0-9A-Z]{26})", rename)
    app.register("POST", r"/api/profiles/(?P<profile_id>prof-[0-9A-Z]{26})/delete", remove)

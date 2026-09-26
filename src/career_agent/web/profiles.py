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

import contextlib
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from career_agent.runtime.profiles import (
    Profile,
    ProfileError,
    ProfileLock,
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


TAILOR_ENV = ("RESUME_TAILOR_HOME", "RESUME_TAILOR_DATA")


def tailor_environment(root: Path, profile: Profile) -> dict[str, str | None]:
    """Point Resume Tailor at this profile's own workspace. Returns what the
    variables held before, so a failed switch can put them back."""
    previous = {name: os.environ.get(name) for name in TAILOR_ENV}
    home = root / profile.tailor_home
    home.mkdir(parents=True, exist_ok=True)
    os.environ["RESUME_TAILOR_HOME"] = str(home)
    os.environ["RESUME_TAILOR_DATA"] = str(home / "runtime")
    return previous


def restore_environment(previous: dict[str, str | None]) -> None:
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


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
        tailor_factory: Callable[[Path], Any] | None = None,
    ) -> None:
        self.root = root
        self.host = host
        self.port = port
        self.tailor = tailor
        self.tailor_factory = tailor_factory
        self.server: Any = None
        #: Serialises switching, deleting and EVERY background run start of
        #: the served app (see `gate`): no run can begin on a profile that is
        #: being left, and nothing is deleted while it is being switched to.
        self._lock = threading.RLock()
        #: The profile this process serves, whatever the registry file says.
        self.active: Profile | None = None
        self._held: ProfileLock | None = None
        self._pending_lock: ProfileLock | None = None

    # -- building a profile's app ------------------------------------------

    def gate(self, api: JobsApi, runner: Any) -> None:
        """Make `runner` refuse to start once `api` has been switched away."""
        runner.admit_lock = self._lock
        runner.admit = lambda: not api.retired

    def open(self, profile: Profile, *, lock: bool = True) -> JobsApi:
        """A `JobsApi` for `profile`, after checking its database is its own.

        With `lock`, the profile's OS lock is taken for this process (and the
        previous one released by `switch`): another Career Agent window
        serving the same profile is refused."""

        db, config_dir, _ = profile.paths(self.root)
        if not db.exists():
            raise ProfileError("This profile's database is missing.")
        held = None
        if lock and not (self.active is not None and self.active.id == profile.id):
            held = ProfileLock(db)
            held.acquire()
        try:
            api = self._build(profile, db, config_dir)
        except BaseException:
            if held is not None:
                held.release()
            raise
        if held is not None:
            self._pending_lock = held
        return api

    def _build(self, profile: Profile, db: Path, config_dir: Path) -> JobsApi:
        from career_agent.runtime import RuntimeMode, identity_of
        from career_agent.storage.db import connect, migrate
        from career_agent.web.api import JobsApi
        from career_agent.web.server import ServerConfig

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
        # Every runner this app owns, the semantic one included: it is built
        # while the app is constructed, before this host is attached to it.
        for runner in (api.retrieval, api.rescore, getattr(api, "semantic_runner", None)):
            if runner is not None:
                self.gate(api, runner)
        return api

    def serve(self, profile: Profile, api: JobsApi) -> None:
        """Record that `profile` is what this process now serves."""
        previous, self._held = self._held, getattr(self, "_pending_lock", None) or self._held
        self._pending_lock = None
        if previous is not None and previous is not self._held:
            previous.release()
        self.active = profile

    def close(self) -> None:
        if self._held is not None:
            self._held.release()
            self._held = None

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
        """Serve `profile_id` from the next request on, or change nothing.

        Order, and why: retire the old app first (so no run can start on it
        while it is checked), refuse if anything is still running, open the
        new profile (its identity and its OS lock), record it in the
        registry, rebuild Resume Tailor, and only then swap. Any failure
        puts everything back as it was.
        """
        with self._lock:
            registry = load_registry(self.root) or ensure_registry(self.root)
            profile = registry.get(profile_id)
            old = self.current()
            old_active = registry.active
            if old is not None:
                old.retired = True
                # A local reading is interruptible: stop it rather than let it
                # run on against a profile that is no longer on screen.
                readings = getattr(old, "local_readings", None)
                if readings is not None:
                    readings.cancel_all()
            reason = self.busy(old)
            if reason:
                if old is not None:
                    old.retired = False
                raise ApiError(409, reason, for_reader=True)
            previous_env: dict[str, str | None] | None = None
            try:
                api = self.open(profile)
                set_active(self.root, profile.id)
                if self.tailor is not None and self.tailor_factory is not None:
                    previous_env = tailor_environment(self.root, profile)
                    self.tailor.inner = self.tailor_factory(self.root / profile.tailor_home)
            except BaseException:
                if old is not None:
                    old.retired = False
                if previous_env is not None:
                    restore_environment(previous_env)
                pending = getattr(self, "_pending_lock", None)
                if pending is not None:
                    pending.release()
                    self._pending_lock = None
                with contextlib.suppress(ProfileError):
                    set_active(self.root, old_active)
                raise
            if self.server is not None:
                self.server.RequestHandlerClass.app = api
            self.serve(profile, api)
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
    # What THIS process serves decides "active", not the registry file: a
    # second launcher may have rewritten the file since.
    served = host.active.id if host.active is not None else registry.active
    current = next((p for p in registry.profiles if p.id == served), registry.current)
    return {
        "enabled": True,
        "active": current.public(),
        "profiles": [
            {**p.public(), "active": p.id == served, "original": p.legacy}
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
        with host._lock:  # never while that profile is being switched to
            try:
                delete_profile(
                    host.root,
                    profile_id,
                    body["confirm_label"],
                    served=host.active.id if host.active is not None else None,
                )
            except ProfileError as exc:
                raise ApiError(409, str(exc), for_reader=True) from exc
        return {"deleted": profile_id, **_listing(host)}

    app.register("GET", r"/api/profiles", listing)
    app.register("POST", r"/api/profiles", create)
    app.register("POST", r"/api/profiles/switch", switch)
    app.register("PATCH", r"/api/profiles/(?P<profile_id>prof-[0-9A-Z]{26})", rename)
    app.register("POST", r"/api/profiles/(?P<profile_id>prof-[0-9A-Z]{26})/delete", remove)

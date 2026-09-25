"""Local profiles: several people, one installation, nothing mixed.

WHAT A PROFILE IS
-----------------
One person's Career Agent: their database (Career Profile, evidence, Search
Fit, semantic findings, applications, notes, source choices such as the
experimental LinkedIn opt-in), their private configuration (search intent,
role anchors, semantic settings and budget) and their Resume Tailor
workspace. A profile has a stable identifier that never changes and a label
the person can rename.

WHAT A PROFILE IS NOT
---------------------
An account. There is no login, no password and no PIN. Profiles separate
application data so that one person's CV, scores and applications never
appear in, or influence, another's. They are NOT a security boundary: anyone
using the same computer account can read every file under `data/`.

LAYOUT
------
    data/profiles.json              the registry (installation level)
    data/profiles/<id>/personal.db  a profile's database
    data/profiles/<id>/config/      its private settings, plus the shipped
                                    files, refreshed from `config/` on start
    data/profiles/<id>/tailor/      its Resume Tailor workspace
    data/profiles/.trash/           deleted profiles, moved rather than erased

The first profile ADOPTS the existing installation where it already is:
`data/personal.db`, `config/` and `data/tailor-personal/`. Nothing is moved
or copied; the database is only stamped with the profile's id.

Installation secrets (`.env`, the DeepSeek key) stay installation-wide and
are never copied into a profile.

A DATABASE BELONGS TO ONE PROFILE. `bind_database` stamps the profile id into
`database_identity` the first time a profile opens it and refuses, from then
on, to open it as any other profile.

SHARED PUBLIC CATALOGUE: NOT YET. Each profile currently holds its own copy
of the job postings it collects. docs/MULTI_PROFILE.md describes the staged
split into one shared public catalogue plus private per-profile state, and
why it is staged.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ulid import ULID

REGISTRY_FILE = Path("data") / "profiles.json"
PROFILES_DIR = Path("data") / "profiles"
TRASH_DIR = PROFILES_DIR / ".trash"
SCHEMA = 1
DEFAULT_LABEL = "My profile"
MAX_LABEL = 40
MAX_PROFILES = 12

#: Where the installation's first profile already lives.
LEGACY_DB = Path("data") / "personal.db"
LEGACY_CONFIG = Path("config")
LEGACY_TAILOR = Path("data") / "tailor-personal"

_ID = re.compile(r"^prof-[0-9A-Z]{26}$")
_SPACE = re.compile(r"\s+")
_COLORS = ("teal", "violet", "amber", "rose", "sky", "lime")


class ProfileError(ValueError):
    """A profile operation was refused, with a sentence a person can read."""


class ProfileMismatch(ProfileError):
    """A database stamped for one profile was opened as another."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def clean_label(label: object) -> str:
    text = _SPACE.sub(" ", str(label or "")).strip()
    if not text:
        raise ProfileError("Give the profile a name.")
    if len(text) > MAX_LABEL:
        raise ProfileError(f"Keep the name to {MAX_LABEL} characters.")
    return text


@dataclass(frozen=True)
class Profile:
    id: str
    label: str
    created_at: str
    #: Relative to the installation root.
    db: str
    config_dir: str
    tailor_home: str
    color: str | None = None
    #: The installation's original workspace, adopted where it lives.
    legacy: bool = False

    def paths(self, root: Path) -> tuple[Path, Path, Path]:
        return root / self.db, root / self.config_dir, root / self.tailor_home

    def public(self) -> dict[str, Any]:
        """What a screen may show: never a path."""
        return {
            "id": self.id,
            "label": self.label,
            "color": self.color,
            "created_at": self.created_at,
        }


# =========================================================================
# the registry file
# =========================================================================


@dataclass
class Registry:
    active: str
    profiles: list[Profile]

    def get(self, profile_id: str) -> Profile:
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileError("That profile does not exist.")

    @property
    def current(self) -> Profile:
        return self.get(self.active)


_lock = threading.Lock()


def registry_path(root: Path) -> Path:
    return root / REGISTRY_FILE


def load_registry(root: Path) -> Registry | None:
    path = registry_path(root)
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ProfileError("The profile registry was written by a different version.")
    profiles = [Profile(**p) for p in data.get("profiles", [])]
    if not profiles or not any(p.id == data.get("active") for p in profiles):
        raise ProfileError("The profile registry names no valid active profile.")
    for p in profiles:
        if not _ID.match(p.id):
            raise ProfileError("The profile registry holds an invalid profile id.")
    return Registry(active=str(data["active"]), profiles=profiles)


def save_registry(root: Path, registry: Registry) -> None:
    """Atomically: a registry half-written by a crash would lose every profile."""
    path = registry_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "schema": SCHEMA,
            "note": "Local profiles. Not accounts: see docs/MULTI_PROFILE.md.",
            "active": registry.active,
            "profiles": [asdict(p) for p in registry.profiles],
        },
        indent=1,
        ensure_ascii=False,
    )
    fd, tmp = tempfile.mkstemp(prefix=".profiles-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def new_id() -> str:
    return f"prof-{ULID()}"


def ensure_registry(root: Path) -> Registry:
    """The registry, created on first use by ADOPTING the existing workspace.

    The first profile points at `data/personal.db`, `config/` and
    `data/tailor-personal/` exactly where they are. Nothing is moved, copied
    or rewritten; the neutral label "My profile" is used rather than any
    name the data might suggest.
    """
    with _lock:
        existing = load_registry(root)
        if existing is not None:
            return existing
        first = Profile(
            id=new_id(),
            label=DEFAULT_LABEL,
            created_at=_now(),
            db=LEGACY_DB.as_posix(),
            config_dir=LEGACY_CONFIG.as_posix(),
            tailor_home=LEGACY_TAILOR.as_posix(),
            color=_COLORS[0],
            legacy=True,
        )
        registry = Registry(active=first.id, profiles=[first])
        save_registry(root, registry)
        return registry


# =========================================================================
# database identity
# =========================================================================


def bound_profile(conn: sqlite3.Connection) -> str | None:
    try:
        row = conn.execute(
            "SELECT profile_id FROM database_identity WHERE id = 'singleton'"
        ).fetchone()
    except sqlite3.Error:
        return None
    return str(row[0]) if row is not None and row[0] else None


def bind_database(conn: sqlite3.Connection, profile_id: str) -> None:
    """Stamp `profile_id` into this database, or refuse a different one.

    The caller holds no transaction; this commits its own. A database not yet
    stamped with any identity is left alone here: `init-personal` stamps the
    kind first.
    """
    found = bound_profile(conn)
    if found is not None and found != profile_id:
        raise ProfileMismatch(
            "This database belongs to a different local profile and was not opened."
        )
    if found is None:
        with conn:
            conn.execute(
                "UPDATE database_identity SET profile_id = ? WHERE id = 'singleton'",
                (profile_id,),
            )


# =========================================================================
# creating, renaming, deleting
# =========================================================================


#: Shipped configuration a profile folder needs next to its private files.
#: Everything else in `config/` is either private (`*.local.yaml`, backups)
#: or not read at runtime.
def _shipped(config_root: Path) -> list[Path]:
    return sorted(
        p
        for p in config_root.glob("*.yaml")
        if ".local." not in p.name and not p.name.endswith(".backup")
    )


def sync_shipped_config(root: Path, profile: Profile) -> int:
    """Refresh a profile folder's copies of the shipped configuration.

    Never touches a `*.local.yaml`: those are the person's own. The legacy
    profile reads `config/` itself and needs nothing.
    """
    if profile.legacy:
        return 0
    target = root / profile.config_dir
    target.mkdir(parents=True, exist_ok=True)
    copied = 0
    for source in _shipped(root / LEGACY_CONFIG):
        destination = target / source.name
        if not destination.exists() or destination.read_bytes() != source.read_bytes():
            shutil.copyfile(source, destination)
            copied += 1
    return copied


def create_profile(root: Path, label: str) -> Profile:
    """A new, EMPTY profile: its own folder, database, settings and Tailor
    workspace. Nothing of any other profile is copied into it."""
    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.storage.db import connect, migrate, transaction

    clean = clean_label(label)
    with _lock:
        registry = load_registry(root) or ensure_registry_unlocked(root)
        if len(registry.profiles) >= MAX_PROFILES:
            raise ProfileError(f"There can be at most {MAX_PROFILES} local profiles.")
        if any(p.label.casefold() == clean.casefold() for p in registry.profiles):
            raise ProfileError("Another profile already has that name.")
        profile_id = new_id()
        base = PROFILES_DIR / profile_id
        profile = Profile(
            id=profile_id,
            label=clean,
            created_at=_now(),
            db=(base / "personal.db").as_posix(),
            config_dir=(base / "config").as_posix(),
            tailor_home=(base / "tailor").as_posix(),
            color=_COLORS[len(registry.profiles) % len(_COLORS)],
        )
        db, config_dir, tailor = profile.paths(root)
        tailor.mkdir(parents=True, exist_ok=True)
        config_dir.mkdir(parents=True, exist_ok=True)
        sync_shipped_config(root, profile)
        conn = connect(db)
        try:
            migrate(conn)
            with transaction(conn):
                stamp_identity(conn, RuntimeMode.PERSONAL, clean)
            bind_database(conn, profile_id)
        finally:
            conn.close()
        registry.profiles.append(profile)
        save_registry(root, registry)
        return profile


def ensure_registry_unlocked(root: Path) -> Registry:
    """`ensure_registry` for a caller already holding the lock."""
    existing = load_registry(root)
    if existing is not None:
        return existing
    first = Profile(
        id=new_id(),
        label=DEFAULT_LABEL,
        created_at=_now(),
        db=LEGACY_DB.as_posix(),
        config_dir=LEGACY_CONFIG.as_posix(),
        tailor_home=LEGACY_TAILOR.as_posix(),
        color=_COLORS[0],
        legacy=True,
    )
    registry = Registry(active=first.id, profiles=[first])
    save_registry(root, registry)
    return registry


def rename_profile(root: Path, profile_id: str, label: str) -> Profile:
    clean = clean_label(label)
    with _lock:
        registry = load_registry(root)
        if registry is None:
            raise ProfileError("There are no local profiles yet.")
        target = registry.get(profile_id)
        if any(
            p.id != profile_id and p.label.casefold() == clean.casefold() for p in registry.profiles
        ):
            raise ProfileError("Another profile already has that name.")
        renamed = replace(target, label=clean)
        registry.profiles = [renamed if p.id == profile_id else p for p in registry.profiles]
        save_registry(root, registry)
        return renamed


def set_active(root: Path, profile_id: str) -> Profile:
    with _lock:
        registry = load_registry(root)
        if registry is None:
            raise ProfileError("There are no local profiles yet.")
        profile = registry.get(profile_id)
        registry.active = profile_id
        save_registry(root, registry)
        return profile


def delete_profile(root: Path, profile_id: str, confirm_label: str) -> Path:
    """Move a profile's folder to `data/profiles/.trash/` and forget it.

    Refused for the active profile (switch first), for the adopted original
    workspace (its files live in shared folders and are never moved by this),
    and unless `confirm_label` is exactly the profile's name. Moved, not
    erased: the folder can be restored by hand until the trash is emptied.
    The job catalogue of another profile is never touched.
    """
    with _lock:
        registry = load_registry(root)
        if registry is None:
            raise ProfileError("There are no local profiles yet.")
        profile = registry.get(profile_id)
        if profile.id == registry.active:
            raise ProfileError("Switch to another profile before deleting this one.")
        if profile.legacy:
            raise ProfileError(
                "This is the original profile of this installation. It cannot be deleted here."
            )
        if confirm_label != profile.label:
            raise ProfileError("Type the profile's name exactly to confirm.")
        base = (root / profile.db).parent
        if base.parent.resolve() != (root / PROFILES_DIR).resolve():
            raise ProfileError("This profile's folder is not where profiles live.")
        trash = root / TRASH_DIR
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / f"{profile.id}-{_now().replace(':', '')}"
        shutil.move(str(base), str(destination))
        registry.profiles = [p for p in registry.profiles if p.id != profile_id]
        save_registry(root, registry)
        return destination

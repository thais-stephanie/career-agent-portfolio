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
import sys
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


def installation_root(config_dir: Path) -> Path:
    """The installation a profile's settings folder belongs to.

    `config/` sits directly in the installation; a later profile's settings
    live in `data/profiles/<id>/config/`. Secrets (`.env`) are looked up
    here, never in a profile's folder.
    """
    config_dir = Path(config_dir)
    parent = config_dir.parent
    if parent.parent.name == PROFILES_DIR.name and parent.parent.parent.name == "data":
        return parent.parent.parent.parent
    return parent


def installation_data_dir(db_path: Path) -> Path:
    """Where installation-wide state next to a database belongs (quotas)."""
    db_path = Path(db_path)
    parent = db_path.parent
    if parent.parent.name == PROFILES_DIR.name and parent.parent.parent.name == "data":
        return parent.parent.parent
    return parent


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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("schema") != SCHEMA:
            raise ProfileError("The profile registry was written by a different version.")
        profiles = [Profile(**p) for p in data.get("profiles", [])]
    except ProfileError:
        raise
    except (OSError, ValueError, TypeError) as exc:
        raise ProfileError(
            f"The profile registry ({REGISTRY_FILE.as_posix()}) cannot be read. "
            "Restore it from a copy, or move it aside to have it rebuilt from the "
            "profile folders."
        ) from exc
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
        return ensure_registry_unlocked(root)


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


def _stamped(db: Path) -> tuple[str | None, str | None]:
    """(profile id, label) a database file says it belongs to, read-only."""
    if not db.exists():
        return None, None
    try:
        conn = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT profile_id, label FROM database_identity WHERE id = 'singleton'"
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None, None
    return (str(row[0]) if row and row[0] else None, str(row[1]) if row and row[1] else None)


def ensure_registry_unlocked(root: Path) -> Registry:
    """`ensure_registry` for a caller already holding the lock.

    A registry that was lost (or an installation restored from a backup) is
    REBUILT from what the databases say: the original workspace keeps the id
    already stamped in it, and every `data/profiles/<id>/` folder whose
    database carries its own id comes back under its stored name. Without
    this, a missing file would lock a person out of her own database.
    """
    existing = load_registry(root)
    if existing is not None:
        return existing
    stamped, _ = _stamped(root / LEGACY_DB)
    first = Profile(
        id=stamped if stamped and _ID.match(stamped) else new_id(),
        label=DEFAULT_LABEL,
        created_at=_now(),
        db=LEGACY_DB.as_posix(),
        config_dir=LEGACY_CONFIG.as_posix(),
        tailor_home=LEGACY_TAILOR.as_posix(),
        color=_COLORS[0],
        legacy=True,
    )
    profiles = [first]
    folder = root / PROFILES_DIR
    for base in sorted(folder.iterdir()) if folder.is_dir() else []:
        found, label = _stamped(base / "personal.db")
        if base.name.startswith(".") or found != base.name or not _ID.match(found):
            continue
        rel = (PROFILES_DIR / base.name).as_posix()
        profiles.append(
            Profile(
                id=found,
                label=label or found,
                created_at=_now(),
                db=f"{rel}/personal.db",
                config_dir=f"{rel}/config",
                tailor_home=f"{rel}/tailor",
                color=_COLORS[len(profiles) % len(_COLORS)],
            )
        )
    registry = Registry(active=first.id, profiles=profiles)
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
        # The database keeps the name too, so a registry rebuilt from the
        # databases brings the profile back under its current name.
        db = root / renamed.db
        if db.exists():
            conn = sqlite3.connect(db, timeout=5)
            try:
                with conn:
                    conn.execute(
                        "UPDATE database_identity SET label = ? WHERE id = 'singleton'", (clean,)
                    )
            except sqlite3.Error:
                pass
            finally:
                conn.close()
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


def delete_profile(
    root: Path, profile_id: str, confirm_label: str, *, served: str | None = None
) -> Path:
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
        if profile.id in (registry.active, served):
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
        # Nobody may be serving it: on Linux and macOS a rename succeeds on a
        # folder another process still writes, so the lock is asked first.
        guard = ProfileLock(root / profile.db)
        guard.acquire()
        guard.release()
        trash = root / TRASH_DIR
        trash.mkdir(parents=True, exist_ok=True)
        destination = trash / f"{profile.id}-{_now().replace(':', '')}"
        try:
            # A rename or nothing: never the copy-then-delete fallback, which
            # on Windows half-erases a folder whose database is still open.
            os.rename(base, destination)
        except OSError as exc:
            raise ProfileError(
                "That profile's files are in use (another Career Agent window or a "
                "command). Close it and try again."
            ) from exc
        registry.profiles = [p for p in registry.profiles if p.id != profile_id]
        save_registry(root, registry)
        return destination


# =========================================================================
# one process per profile
# =========================================================================


class ProfileLock:
    """An operating-system lock on a profile's database, held while a process
    serves it. Released by the OS if the process dies, so it never goes stale.
    Two Career Agent windows can therefore never write one profile at once."""

    def __init__(self, db: Path) -> None:
        self.path = Path(db).resolve().with_suffix(Path(db).suffix + ".profile.lock")
        self._handle: Any = None

    def acquire(self) -> None:
        handle = self.path.open("a+b")
        handle.seek(0)
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise ProfileError(
                "This profile is already open in another Career Agent window. "
                "Use that window, or close it first."
            ) from exc
        self._handle = handle

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            handle.close()

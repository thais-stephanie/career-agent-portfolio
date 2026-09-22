"""Personal or demo: one decision, made once, refused when ambiguous.

The defect this exists to close: the documented launch path served
`data/demo.db`, so the product opened on nineteen invented postings while the
owner's 18,549 real ones sat unused. Nobody chose that; it was the default in
a document, and nothing downstream could tell the difference.

Two rules carry the whole design.

**Personal is the default, and demo is never a fallback.** If the personal
database is missing or empty, the answer is an empty personal database and an
onboarding screen -- never demo rows. Showing invented postings to someone who
asked for their own is worse than showing nothing, because nothing is honest.

**The database says what it is.** `kind` is stored inside the file, not
inferred from its path. A path is a guess: copy `demo.db` to `personal.db` and
the name lies while the contents do not. Personal mode refuses to open a demo
database and demo mode refuses to open a personal one, so the two populations
cannot mix even by accident.

This repository has read absence as permission four times -- a missing Host
header as consent, an omitted field as "clear the date you applied", a
duplicate count over the wrong population, an unknown filter as no filter. So
an *unmarked* database is not silently adopted either: it is adopted only when
the caller states which kind it is.
"""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

#: The demo database. Fixed, so "explicitly selected" cannot drift into
#: "whatever path happened to be passed".
DEMO_DB_PATH = Path("data") / "demo.db"

#: Where the personal corpus lives today. `data/career.db` is the historical
#: default and is EMPTY; the real one is the milestone-2 collection path.
#: Named here once so the rest of the program never guesses.
DEFAULT_PERSONAL_DB_PATH = Path("data") / "m1d2" / "career.db"

#: The environment variable a person can set to move their personal database
#: without passing `--db` on every command.
PERSONAL_DB_ENV = "CAREER_AGENT_DB"


class RuntimeModeError(RuntimeError):
    """The requested mode and the database on disk disagree.

    Always a refusal, never a downgrade. The whole point is that the caller
    finds out rather than being served the other population.
    """


class RuntimeMode(StrEnum):
    PERSONAL = "PERSONAL"
    DEMO = "DEMO"

    @property
    def is_personal(self) -> bool:
        return self is RuntimeMode.PERSONAL

    @property
    def banner(self) -> str:
        """What the interface must show. Demo says so; personal says whose."""
        return "Demo data" if self is RuntimeMode.DEMO else "Personal data"


@dataclass(frozen=True, slots=True)
class DatabaseIdentity:
    """What a database file says about itself."""

    kind: RuntimeMode
    label: str
    created_at: str
    last_retrieval_at: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeIndicator:
    """Everything the interface needs to say where it is standing.

    `database_ref` is a LABEL plus a short digest, never a path. An absolute
    path on Windows carries the person's username, and this string is designed
    to survive being screenshotted for a portfolio.
    """

    mode: RuntimeMode
    banner: str
    database_ref: str
    is_personal: bool
    total_active: int
    last_retrieval_at: str | None
    #: Set by the caller that ran the query; the runtime layer cannot know it.
    displayed: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode.value,
            "banner": self.banner,
            "database_ref": self.database_ref,
            "is_personal": self.is_personal,
            "total_active": self.total_active,
            "last_retrieval_at": self.last_retrieval_at,
            "displayed": self.displayed,
        }


def database_ref(path: Path, label: str) -> str:
    """A stable, non-identifying reference to a database file.

    The label a person chose, plus six hex characters of the resolved path, so
    two databases with the same label are still distinguishable and neither
    reveals a home directory.
    """
    digest = hashlib.sha256(str(path.resolve()).encode("utf-8")).hexdigest()[:6]
    # ASCII separator on purpose. This string travels through a Windows
    # console, a JSON payload and a screenshot; a middle dot renders as a
    # replacement character in cp1252 terminals and looks like corruption.
    return f"{label}#{digest}"


def read_identity(conn: sqlite3.Connection) -> DatabaseIdentity | None:
    """What this database says it is, or None if it has never been stamped.

    Never raises on a database that predates the table: an old file is
    unmarked, which is a question for the caller, not a crash.
    """
    try:
        row = conn.execute(
            "SELECT kind, label, created_at, last_retrieval_at"
            " FROM database_identity WHERE id = 'singleton'"
        ).fetchone()
    except sqlite3.Error:
        return None
    if row is None:
        return None
    return DatabaseIdentity(
        kind=RuntimeMode(str(row["kind"])),
        label=str(row["label"]),
        created_at=str(row["created_at"]),
        last_retrieval_at=row["last_retrieval_at"],
    )


def stamp_identity(
    conn: sqlite3.Connection,
    kind: RuntimeMode,
    label: str,
    *,
    last_retrieval_at: str | None = None,
) -> DatabaseIdentity:
    """Record what this database is. Idempotent; the caller owns the transaction.

    Re-stamping a database with a DIFFERENT kind is refused. Turning a demo
    database into a personal one by running a command is exactly the mixing
    this module exists to prevent -- if someone genuinely wants that file to
    be personal, deleting it and collecting into a fresh one is the honest
    path and loses nothing, because a demo database holds only invented rows.
    """
    existing = read_identity(conn)
    if existing is not None and existing.kind is not kind:
        raise RuntimeModeError(
            f"this database is already stamped {existing.kind.value} and cannot become "
            f"{kind.value}; demo and personal records must never mix"
        )
    now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO database_identity (id, kind, label, created_at, last_retrieval_at)"
        " VALUES ('singleton', ?, ?, ?, ?)"
        " ON CONFLICT(id) DO UPDATE SET"
        " label = excluded.label,"
        " last_retrieval_at = COALESCE("
        "excluded.last_retrieval_at, database_identity.last_retrieval_at)",
        (kind.value, label, existing.created_at if existing else now, last_retrieval_at),
    )
    result = read_identity(conn)
    assert result is not None  # noqa: S101 -- just written in this transaction
    return result


def record_retrieval(conn: sqlite3.Connection, when: str | None = None) -> None:
    """Mark that a collection pass ran. Distinct from "a posting is new".

    A run that found nothing still ran, and the interface must be able to say
    "checked, nothing new" instead of showing a stale publication date.
    """
    stamp = when or datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    conn.execute(
        "UPDATE database_identity SET last_retrieval_at = ? WHERE id = 'singleton'",
        (stamp,),
    )


def identity_of(conn: sqlite3.Connection, requested: RuntimeMode) -> DatabaseIdentity:
    """The identity of an already-open database, checked against what was asked.

    Raises :class:`RuntimeModeError` when they disagree. This is the guarantee:
    personal mode cannot serve demo rows and demo mode cannot serve personal
    ones, whatever the file happens to be called.
    """
    found = read_identity(conn)
    if found is None:
        raise RuntimeModeError(
            "this database does not say whether it is personal or demo. "
            "Run `career-agent init-personal --db <path>` to claim it as personal, "
            "or `career-agent seed-demo` to build a demo database."
        )
    if found.kind is not requested:
        other = "demo" if found.kind is RuntimeMode.DEMO else "personal"
        want = "personal" if requested is RuntimeMode.PERSONAL else "demo"
        raise RuntimeModeError(
            f"refusing to open a {other} database in {want} mode. "
            f"{want.capitalize()} mode never falls back to the other population."
        )
    return found


def resolve_database(
    mode: RuntimeMode,
    explicit: Path | None = None,
    env: dict[str, str] | None = None,
) -> Path:
    """Which file this mode is allowed to open.

    Precedence for personal mode: an explicit `--db`, then the
    ``CAREER_AGENT_DB`` environment variable, then the known personal corpus.
    Demo mode ignores all of it and uses :data:`DEMO_DB_PATH`, because "demo"
    means one specific throwaway database and letting it be redirected is how
    invented rows end up somewhere real.
    """
    if mode is RuntimeMode.DEMO:
        return DEMO_DB_PATH
    if explicit is not None:
        return explicit
    from os import environ

    source = environ if env is None else env
    override = source.get(PERSONAL_DB_ENV)
    if override:
        return Path(override)
    return DEFAULT_PERSONAL_DB_PATH


def indicator(
    conn: sqlite3.Connection,
    path: Path,
    identity: DatabaseIdentity,
    *,
    displayed: int | None = None,
) -> RuntimeIndicator:
    """The runtime banner: mode, which database, how much is in it, when we looked."""
    total = int(conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0])
    return RuntimeIndicator(
        mode=identity.kind,
        banner=identity.kind.banner,
        database_ref=database_ref(path, identity.label),
        is_personal=identity.kind.is_personal,
        total_active=total,
        last_retrieval_at=identity.last_retrieval_at,
        displayed=displayed,
    )

"""The shared public job catalogue: one copy of the public job data, many profiles.

WHY
---
Local profiles (stage 1) gave each person their own database, and each
database also held every job posting that person collected. The same public
posting was therefore stored once per profile, and every profile collected it
again. Stage 2 keeps ONE catalogue of public job data for the installation and
leaves everything about a person in that person's own database:

    data/shared/catalogue.db          public: postings, descriptions, payloads,
                                      employers, boards, the full-text index
    data/personal.db                  the original profile's private state
    data/profiles/<id>/personal.db    every later profile's private state

A profile database that has been split carries a `catalogue_link` row. Opening
it (`storage.db.connect`) ATTACHes the catalogue under the schema name
`catalogue`. SQLite resolves a table name without a schema in `main` first and
then in the attached database, so every existing query keeps working unchanged:
`job_match` is found in the profile, `job` in the catalogue, and a join between
them crosses the boundary on its own. A database WITHOUT a link (the demo, the
test fixtures, a profile not converted yet) holds both kinds of table itself
and behaves exactly as before.

WHAT IS PUBLIC AND WHAT IS PRIVATE
----------------------------------
Classified from the schema, not from table names (docs/MULTI_PROFILE.md has the
reasoning per table). The test suite asserts every table is in exactly one set,
so a new table cannot arrive unclassified.

* A posting, its description, its archived payloads, its sightings, its
  employer and board, the collection slices, the full-text index, the input
  revision clock and the index refresh queue are PUBLIC: nothing in them
  depends on who is searching.
* Everything about a person is PRIVATE: the Career Profile and evidence, search
  preference snapshots, Search Fit and its receipts, semantic findings, saved,
  hidden, notes and applications, the run log (a targeted run's stats count
  what the person's own terms found), local AI enrichment (its prompt carries
  the person's capability line) and `job_retrieval_lane`: a posting is public,
  but "found because this person searched for Y" is not.

THREE CONSEQUENCES OF TWO FILES
-------------------------------
1. A trigger can only reach tables in its own file, and SQLite refuses a write
   whose foreign key names a parent table that is not in the same file. So the
   private tables that pointed at `job` or `job_raw` are rebuilt without those
   foreign keys, `integrity.catalogue_orphans` checks the references instead,
   and the profile keeps its own population counter (`profile_revision`) for
   the triggers on `job_match`.
2. A transaction that writes both files is atomic per file, not across them
   (SQLite under WAL). Every write that spans both goes public first and
   private second, and each is idempotent, so a crash between them leaves a
   public posting with no private row yet, which the next pass completes.
3. Two Career Agent windows serving two profiles may both write the catalogue.
   SQLite serialises every transaction; on top of that a COLLECTION holds an
   operating-system lock on the catalogue (`CatalogueWriteLock`), so two
   collections never interleave. The lock is released by the OS if a process
   dies, so it never goes stale.
"""

from __future__ import annotations

import re
import sqlite3
import sys
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ulid import ULID

SCHEMA = "catalogue"
CATALOGUE_DIR = "shared"
CATALOGUE_FILE = "catalogue.db"

#: Candidate-independent public job data. Lives in the catalogue.
SHARED_TABLES: frozenset[str] = frozenset(
    {
        # postings and what they say
        "job",
        "job_raw",
        "job_provider_payload",
        "job_discovery_source",
        # who posts them and where they are read from
        "company",
        "source_board",
        "board_discovery_lead",
        "source_slice_state",
        # the full-text index over them
        "job_search",
        "search_index_map",
        "search_index_state",
        # the input revision clock the triggers on the tables above advance,
        # and the queue of postings whose index row must be refreshed
        "compute_revision",
        "job_input_revision",
        "job_dirty",
        # posting-side extraction: facts read out of the posting text, with
        # quotes from it; no candidate data is sent or stored
        "fingerprint",
        "evidence",
        "fp_eligibility",
        "fp_language",
        "fp_responsibility",
        "fp_software",
        "provider_observation",
        "llm_call",
    }
)

#: Everything about one person. Lives in that profile's own database.
PRIVATE_TABLES: frozenset[str] = frozenset(
    {
        "database_identity",
        "candidate",
        "candidate_state",
        "search_profile_version",
        "verified_claim",
        "cv_import",
        "cv_entry",
        "cv_proposal",
        "career_company",
        "career_company_decision",
        "career_evidence_link",
        "career_experience",
        "career_history_event",
        "intake_package",
        "intake_claim",
        "intake_conflict_resolution",
        "job_match",
        "job_score_revision",
        "job_application",
        "job_application_event",
        "requirement_review",
        "job_retrieval_lane",
        "job_enrichment",
        "semantic_run",
        "semantic_evaluation",
        "pipeline_run",
    }
)

#: Tables every database keeps for itself.
LEDGER_TABLES: frozenset[str] = frozenset({"schema_migration"})
#: Added by the split, one per side.
LINK_TABLE = "catalogue_link"
PROFILE_REVISION_TABLE = "profile_revision"
#: A split profile's own rescore requests (a semantic finding published, a
#: forced or explicit rescore). They name postings this PERSON wants scored
#: again, so they never go to the shared queue: that would tell every other
#: profile which postings this one evaluated, and force all of them to
#: rescore work that changed nothing public.
PROFILE_REQUEST_TABLE = "profile_request"
#: `generation` is monotonic per profile, so a request made while a pass runs
#: is never mistaken for the one that pass read (a timestamp could collide).
PROFILE_REQUEST_DDL = (
    f"CREATE TABLE IF NOT EXISTS main.{PROFILE_REQUEST_TABLE} ("
    " job_id TEXT PRIMARY KEY,"
    " generation INTEGER NOT NULL,"
    " marked_at TEXT NOT NULL)"
)
IDENTITY_TABLE = "catalogue_identity"

#: Run stages that write the catalogue and therefore take its collection lock.
#: Scoring (`rescore`) refreshes index rows inside short transactions SQLite
#: already serialises, and holds no lock of its own.
UNLOCKED_STAGES: frozenset[str] = frozenset({"rescore"})


class CatalogueError(RuntimeError):
    """The shared catalogue cannot be used, with a sentence a person can read."""


class CatalogueMismatch(CatalogueError):
    """A profile database is linked to a different catalogue than the one found."""


class CatalogueBusy(CatalogueError):
    """Another process is collecting into the catalogue right now."""


# =========================================================================
# where it is, and which side a connection is on
# =========================================================================


def catalogue_path(db_path: Path) -> Path:
    """The catalogue beside a profile database's installation data folder.

    `data/personal.db` and `data/profiles/<id>/personal.db` both resolve to
    `data/shared/catalogue.db`; a database anywhere else to a `shared/` folder
    next to it. Derived rather than stored, so a moved installation still
    finds its catalogue.
    """
    db_path = Path(db_path)
    parent = db_path.parent
    if parent.parent.name == "profiles" and parent.parent.parent.name == "data":
        parent = parent.parent.parent
    return parent / CATALOGUE_DIR / CATALOGUE_FILE


def _has_table(conn: sqlite3.Connection, name: str, schema: str = "main") -> bool:
    row = conn.execute(
        f"SELECT 1 FROM {schema}.sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def role(conn: sqlite3.Connection) -> str:
    """`profile` (split, private side), `catalogue`, or `single` (both in one file)."""
    if _has_table(conn, LINK_TABLE):
        return "profile"
    if _has_table(conn, IDENTITY_TABLE):
        return "catalogue"
    return "single"


def ensure_profile_tables(conn: sqlite3.Connection) -> None:
    """Tables a split profile needs that an earlier build of the split did
    not create. Idempotent; the caller holds a write transaction or none."""
    if role(conn) == "profile":
        conn.execute(PROFILE_REQUEST_DDL)


def is_attached(conn: sqlite3.Connection) -> bool:
    return any(str(row[1]) == SCHEMA for row in conn.execute("PRAGMA database_list"))


def attached_path(conn: sqlite3.Connection) -> Path | None:
    for row in conn.execute("PRAGMA database_list"):
        if str(row[1]) == SCHEMA and row[2]:
            return Path(str(row[2]))
    return None


def catalogue_id(conn: sqlite3.Connection, schema: str = "main") -> str | None:
    if not _has_table(conn, IDENTITY_TABLE, schema):
        return None
    row = conn.execute(
        f"SELECT catalogue_id FROM {schema}.{IDENTITY_TABLE} WHERE id = 'singleton'"
    ).fetchone()
    return str(row[0]) if row else None


def linked_id(conn: sqlite3.Connection) -> str | None:
    if not _has_table(conn, LINK_TABLE):
        return None
    row = conn.execute(f"SELECT catalogue_id FROM {LINK_TABLE} WHERE id = 'singleton'").fetchone()
    return str(row[0]) if row else None


def attach(conn: sqlite3.Connection, db_path: Path) -> bool:
    """ATTACH the catalogue a split profile database is linked to.

    Returns False for any database that is not a split profile (nothing to
    do). Refuses a missing catalogue, and one whose identity is not the one
    the profile was linked to, rather than silently showing another
    installation's postings.
    """
    wanted = linked_id(conn)
    if wanted is None or is_attached(conn):
        return False
    path = catalogue_path(db_path).resolve()
    if not path.exists():
        raise CatalogueError(
            "The shared job catalogue (data/shared/catalogue.db) is missing, so this "
            "profile's jobs cannot be shown. Restore it from a catalogue backup; the "
            "profile's own data is intact."
        )
    conn.execute(f"ATTACH DATABASE ? AS {SCHEMA}", (str(path),))
    try:
        found = catalogue_id(conn, SCHEMA)
        if found != wanted:
            raise CatalogueMismatch(
                "The shared job catalogue found here is not the one this profile was "
                "linked to, so it was not opened."
            )
        conn.execute(f"PRAGMA {SCHEMA}.journal_mode = WAL")
    except BaseException:
        conn.execute(f"DETACH DATABASE {SCHEMA}")
        raise
    return True


def attach_read_only(conn: sqlite3.Connection, db_path: Path) -> bool:
    """`attach` for a read-only connection: the catalogue is opened read-only
    too. Returns False for a database that is not a split profile."""
    wanted = linked_id(conn)
    if wanted is None or is_attached(conn):
        return False
    path = catalogue_path(db_path).resolve()
    if not path.exists():
        raise CatalogueError("The shared job catalogue (data/shared/catalogue.db) is missing.")
    conn.execute(f"ATTACH DATABASE ? AS {SCHEMA}", (f"{path.as_uri()}?mode=ro",))
    if catalogue_id(conn, SCHEMA) != wanted:
        conn.execute(f"DETACH DATABASE {SCHEMA}")
        raise CatalogueMismatch("The shared job catalogue here is not this profile's.")
    return True


def open_read_only(db_path: Path) -> sqlite3.Connection:
    """A read-only connection to a database, with its catalogue if it has one."""
    conn = sqlite3.connect(f"{Path(db_path).resolve().as_uri()}?mode=ro", uri=True)
    try:
        attach_read_only(conn, db_path)
    except BaseException:
        conn.close()
        raise
    return conn


def link_new_profile(db_path: Path, *, create: bool) -> str | None:
    """Link a NEW, empty profile database to the installation's catalogue.

    With `create`, an installation that has no catalogue yet gets an empty
    one (a fresh install). Without it, a profile is only linked when the
    catalogue already exists: an installation whose original profile still
    holds its own postings keeps the stage-1 layout until that profile is
    split explicitly (`career-agent catalogue split`), because an existing
    catalogue cannot yet absorb a second profile's postings.

    Refuses a database that already holds postings: that is a conversion, not
    a link, and it has its own verified path.
    """
    path = catalogue_path(db_path)
    if not path.exists() and not create:
        return None
    raw = sqlite3.connect(db_path, isolation_level=None)
    try:
        if role(raw) != "single":
            return linked_id(raw)
        if _has_table(raw, "job") and raw.execute("SELECT 1 FROM job LIMIT 1").fetchone():
            raise CatalogueError(
                "This database already holds job postings; split it with "
                "`career-agent catalogue split` instead."
            )
        identity = ensure(db_path)
        make_profile(raw, identity)
        return identity
    finally:
        raw.close()


# =========================================================================
# revision tokens that span both files
# =========================================================================


def match_population(conn: sqlite3.Connection) -> int:
    """The counter the triggers on `job_match` advance, on whichever side it is."""
    table = (
        PROFILE_REVISION_TABLE if _has_table(conn, PROFILE_REVISION_TABLE) else "compute_revision"
    )
    return int(
        conn.execute(f"SELECT population_revision FROM {table} WHERE id = 'singleton'").fetchone()[
            0
        ]
    )


def population_token(conn: sqlite3.Connection) -> tuple[int, ...]:
    """Every counter that moves when the scored population can have changed.

    One file: the single `compute_revision`. Split: the catalogue's (postings
    inserted, updated, deleted) and the profile's (scores written).
    """
    shared = int(
        conn.execute(
            "SELECT population_revision FROM compute_revision WHERE id = 'singleton'"
        ).fetchone()[0]
    )
    if _has_table(conn, PROFILE_REVISION_TABLE):
        return (shared, match_population(conn))
    return (shared,)


# =========================================================================
# building the two sides
# =========================================================================


_FK_ACTIONS = (
    r"(?:\s+ON\s+(?:DELETE|UPDATE)\s+(?:CASCADE|SET\s+NULL|SET\s+DEFAULT|RESTRICT|NO\s+ACTION))*"
)
_DEFERRABLE = r"(?:\s+(?:NOT\s+)?DEFERRABLE(?:\s+INITIALLY\s+(?:DEFERRED|IMMEDIATE))?)?"


def _strip_shared_references(sql: str) -> str:
    """A CREATE TABLE statement without the foreign keys that name a public table."""
    # Whole names only: `job` must never match the start of `job_match`.
    names = "|".join(
        f"{name}(?![\\w])"
        for name in sorted((re.escape(t) for t in SHARED_TABLES), key=len, reverse=True)
    )
    table_level = re.compile(
        r",\s*(?:CONSTRAINT\s+\w+\s+)?FOREIGN\s+KEY\s*\([^)]*\)\s*REFERENCES\s+[\"`]?(?:"
        + names
        + r")[\"`]?\s*(?:\([^)]*\))?"
        + _FK_ACTIONS
        + _DEFERRABLE,
        re.IGNORECASE,
    )
    column_level = re.compile(
        r"\s+REFERENCES\s+[\"`]?(?:"
        + names
        + r")[\"`]?\s*(?:\([^)]*\))?"
        + _FK_ACTIONS
        + _DEFERRABLE,
        re.IGNORECASE,
    )
    return column_level.sub("", table_level.sub("", sql))


def _columns(conn: sqlite3.Connection, table: str) -> list[tuple[Any, ...]]:
    return [tuple(r)[1:] for r in conn.execute(f'PRAGMA table_info("{table}")')]


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _virtual(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
            " AND upper(sql) LIKE 'CREATE VIRTUAL TABLE%'"
        )
    ]


def _shadow(name: str, virtual: Iterable[str]) -> bool:
    """An FTS5 shadow table (`job_search_data` and friends) of a virtual table."""
    return any(name.startswith(f"{owner}_") and name != owner for owner in virtual)


@dataclass(frozen=True)
class SplitReport:
    dropped: tuple[str, ...]
    rebuilt: tuple[str, ...]
    kept: tuple[str, ...]


def _foreign_keys_off(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise CatalogueError("the split needs its own transaction")
    conn.execute("PRAGMA foreign_keys = OFF")


def make_catalogue(conn: sqlite3.Connection, *, new_id: str | None = None) -> str:
    """Turn a one-file database (a fresh one, or a copy of a profile's) into
    the catalogue: drop every private table and stamp a catalogue identity.

    `secure_delete` overwrites what the dropped tables held, so the catalogue
    file keeps no fragment of a person's scores or findings in its free pages;
    the caller VACUUMs afterwards to give the space back.
    """
    if role(conn) != "single":
        raise CatalogueError("only a one-file database can become the catalogue")
    _foreign_keys_off(conn)
    identity = new_id or f"cat-{ULID()}"
    try:
        conn.execute("PRAGMA secure_delete = ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            present = _tables(conn)
            for name in present:
                if name in PRIVATE_TABLES:
                    conn.execute(f'DROP TABLE "{name}"')
            conn.execute(
                f"CREATE TABLE {IDENTITY_TABLE} ("
                " id TEXT PRIMARY KEY CHECK (id = 'singleton'),"
                " catalogue_id TEXT NOT NULL,"
                " created_at TEXT NOT NULL)"
            )
            from career_agent.clock import now_utc

            conn.execute(
                f"INSERT INTO {IDENTITY_TABLE} VALUES ('singleton', ?, ?)", (identity, now_utc())
            )
            left = _tables(conn)
            unexpected = [
                t
                for t in left
                if t not in SHARED_TABLES
                and t not in LEDGER_TABLES
                and t != IDENTITY_TABLE
                and not _shadow(t, _virtual(conn))
            ]
            if unexpected:
                raise CatalogueError(f"unclassified tables would enter the catalogue: {unexpected}")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return identity


def make_profile(conn: sqlite3.Connection, linked_to: str) -> SplitReport:
    """Turn a one-file database into a split profile linked to `linked_to`.

    Drops every public table (their rows live in the catalogue), rebuilds the
    private tables whose foreign keys named a public table, moves the
    `job_match` population counter into `profile_revision` and records the
    link. One transaction: either the database is a complete split profile or
    it is exactly what it was.
    """
    if role(conn) != "single":
        raise CatalogueError("only a one-file database can become a split profile")
    _foreign_keys_off(conn)
    rebuilt: list[str] = []
    dropped: list[str] = []
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            present = _tables(conn)
            unexpected = [
                t
                for t in present
                if t not in SHARED_TABLES
                and t not in PRIVATE_TABLES
                and t not in LEDGER_TABLES
                and not _shadow(t, _virtual(conn))
            ]
            if unexpected:
                raise CatalogueError(f"unclassified tables: {unexpected}")
            population = 0
            if "compute_revision" in present:
                row = conn.execute(
                    "SELECT population_revision FROM compute_revision WHERE id = 'singleton'"
                ).fetchone()
                population = int(row[0]) if row else 0

            # Triggers on private tables are recreated after the rebuild; the
            # ones on public tables go with their tables.
            triggers = conn.execute(
                "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"
            ).fetchall()
            private_triggers = [
                (str(n), str(t), str(s)) for n, t, s in triggers if str(t) in PRIVATE_TABLES
            ]
            for name, _, _ in private_triggers:
                conn.execute(f'DROP TRIGGER "{name}"')

            for name in present:
                if name in SHARED_TABLES:
                    conn.execute(f'DROP TABLE IF EXISTS "{name}"')
                    dropped.append(name)

            for name in sorted(PRIVATE_TABLES & set(present)):
                parents = {str(fk[2]) for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")')}
                if not parents & SHARED_TABLES:
                    continue
                _rebuild_without_shared_keys(conn, name)
                rebuilt.append(name)

            conn.execute(
                f"CREATE TABLE {PROFILE_REVISION_TABLE} ("
                " id TEXT PRIMARY KEY CHECK (id = 'singleton'),"
                " population_revision INTEGER NOT NULL)"
            )
            conn.execute(
                f"INSERT INTO {PROFILE_REVISION_TABLE} VALUES ('singleton', ?)", (population,)
            )
            for _name, _table, sql in private_triggers:
                rewritten = re.sub(r"\bcompute_revision\b", PROFILE_REVISION_TABLE, sql)
                conn.execute(rewritten)

            conn.execute(PROFILE_REQUEST_DDL)
            conn.execute(
                f"CREATE TABLE {LINK_TABLE} ("
                " id TEXT PRIMARY KEY CHECK (id = 'singleton'),"
                " catalogue_id TEXT NOT NULL,"
                " linked_at TEXT NOT NULL)"
            )
            from career_agent.clock import now_utc

            conn.execute(
                f"INSERT INTO {LINK_TABLE} VALUES ('singleton', ?, ?)", (linked_to, now_utc())
            )
            _verify_profile_side(conn)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    kept = tuple(sorted(set(_tables(conn)) - set(dropped)))
    return SplitReport(dropped=tuple(sorted(dropped)), rebuilt=tuple(rebuilt), kept=kept)


def _rebuild_without_shared_keys(conn: sqlite3.Connection, name: str) -> None:
    """SQLite's documented table rebuild, keeping every column and every row."""
    sql = str(
        conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
        ).fetchone()[0]
    )
    indexes = [
        str(r[0])
        for r in conn.execute(
            "SELECT sql FROM sqlite_master"
            " WHERE type = 'index' AND tbl_name = ? AND sql IS NOT NULL",
            (name,),
        )
    ]
    temp = f"{name}__split"
    stripped = _strip_shared_references(sql)
    head = re.compile(
        r"^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?" + re.escape(name) + r"[\"`]?", re.I
    )
    if not head.search(stripped):
        raise CatalogueError(f"cannot read the definition of {name}")
    conn.execute(head.sub(f'CREATE TABLE "{temp}"', stripped, count=1))
    before = _columns(conn, name)
    if _columns(conn, temp) != before:
        raise CatalogueError(f"rebuilding {name} would change its columns")
    parents = {str(fk[2]) for fk in conn.execute(f'PRAGMA foreign_key_list("{temp}")')}
    if parents & SHARED_TABLES:
        raise CatalogueError(f"{name} still names a public table after the rebuild")
    count = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
    conn.execute(f'INSERT INTO "{temp}" SELECT * FROM "{name}"')
    if int(conn.execute(f'SELECT COUNT(*) FROM "{temp}"').fetchone()[0]) != count:
        raise CatalogueError(f"rebuilding {name} would lose rows")
    conn.execute(f'DROP TABLE "{name}"')
    conn.execute(f'ALTER TABLE "{temp}" RENAME TO "{name}"')
    for index_sql in indexes:
        conn.execute(index_sql)


def _verify_profile_side(conn: sqlite3.Connection) -> None:
    left = _tables(conn)
    stray = [t for t in left if t in SHARED_TABLES]
    if stray:
        raise CatalogueError(f"public tables left in the profile: {stray}")
    for name in left:
        parents = {str(fk[2]) for fk in conn.execute(f'PRAGMA foreign_key_list("{name}")')}
        if parents & SHARED_TABLES:
            raise CatalogueError(f"{name} still names a public table")
    problems = conn.execute("PRAGMA main.foreign_key_check").fetchall()
    if problems:
        raise CatalogueError(f"foreign key check failed after the split: {len(problems)} rows")


def create_empty(path: Path) -> str:
    """A new, empty catalogue at `path`, at this build's schema."""
    from career_agent.storage.db import connect, migrate

    if path.exists():
        raise CatalogueError(f"a catalogue already exists at {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        migrate(conn)
        return make_catalogue(conn)
    finally:
        conn.close()


def ensure(db_path: Path) -> str:
    """The id of the catalogue beside `db_path`, creating an empty one if absent."""
    from career_agent.storage.db import connect

    path = catalogue_path(db_path)
    if not path.exists():
        return create_empty(path)
    conn = connect(path)
    try:
        found = catalogue_id(conn)
    finally:
        conn.close()
    if found is None:
        raise CatalogueError(f"{path} is not a job catalogue")
    return found


# =========================================================================
# one collection at a time
# =========================================================================


class CatalogueWriteLock:
    """An operating-system lock on the catalogue, held while a collection
    writes it. Re-entrant inside one process (the app runs several collectors
    one after another in one pass); refused, not waited for, across processes.
    Released by the OS when a process dies."""

    _registry: dict[Path, CatalogueWriteLock] = {}
    _guard = threading.Lock()

    def __init__(self, catalogue: Path) -> None:
        self.path = Path(catalogue).resolve().with_suffix(".db.collect.lock")
        self._handle: Any = None
        self._holds: dict[str, int] = {}

    @classmethod
    def for_catalogue(cls, catalogue: Path) -> CatalogueWriteLock:
        key = Path(catalogue).resolve()
        with cls._guard:
            lock = cls._registry.get(key)
            if lock is None:
                lock = cls._registry[key] = cls(key)
            return lock

    @property
    def held(self) -> bool:
        return self._handle is not None

    def acquire(self, token: str) -> None:
        with self._guard:
            if self._handle is None:
                self._handle = self._take()
            self._holds[token] = threading.get_ident()

    def release(self, token: str) -> None:
        with self._guard:
            if self._holds.pop(token, None) is None:
                return
            if not self._holds:
                self._drop()

    def release_thread(self, ident: int | None = None) -> int:
        """Release every hold a thread still has (a run that never finished)."""
        ident = threading.get_ident() if ident is None else ident
        with self._guard:
            stale = [t for t, owner in self._holds.items() if owner == ident]
            for token in stale:
                del self._holds[token]
            if stale and not self._holds:
                self._drop()
            return len(stale)

    def _take(self) -> Any:
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            raise CatalogueBusy(
                "Another Career Agent window is collecting jobs into the shared catalogue "
                "right now. Try again when it has finished."
            ) from exc
        return handle

    def _drop(self) -> None:
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


def hold_for_run(conn: sqlite3.Connection, run_id: str, stage: str) -> None:
    """Take the collection lock for a run that writes the catalogue."""
    if stage in UNLOCKED_STAGES:
        return
    path = attached_path(conn)
    if path is None:
        return
    CatalogueWriteLock.for_catalogue(path).acquire(run_id)


def release_for_run(conn: sqlite3.Connection, run_id: str) -> None:
    path = attached_path(conn)
    if path is None:
        return
    CatalogueWriteLock.for_catalogue(path).release(run_id)


def release_thread_holds() -> int:
    """Release whatever this thread still holds, on every catalogue. Called
    when a background run ends, whatever way it ended."""
    released = 0
    for lock in list(CatalogueWriteLock._registry.values()):
        released += lock.release_thread()
    return released

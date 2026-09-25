"""SQLite connection handling and the migration runner.

Three ideas worth understanding here, because the rest of the storage layer
rests on them.

**A connection, not a server.** ``sqlite3.connect(path)`` opens a file. There
is no service to start, no port, no password. The entire Personal Alpha
database is ``data/career.db``, and it is why M0 needs no infrastructure.

**Migrations are numbered SQL files, applied once, in order.** Every schema
change is a new ``NNNN_name.sql`` file. The ``schema_migration`` table records
which have run, so migrating is idempotent and a fresh database catches up by
replaying the files. The same files port to PostgreSQL mechanically.

**A migration is identified by its version AND its name.** The number alone is
not an identity: two branches that both grow from the same ``main`` number
their next migration the same, and a database that ran one of them holds a
ledger row whose number matches the other's file. Until 2026-09-18 the runner
compared numbers only, so the second lineage's file was reported as already
applied and its schema change never happened -- silently, with a green
``migrate``. The runner now compares the name too: a row of the same number
and another name is a LINEAGE COLLISION and is refused before anything runs,
unless the file at that number declares that it stands for the foreign row
(``-- reconciles: <name>``), which only a file with no statements may do.
See ADR-0028.

**Transactions are explicit.** The connection runs in autocommit mode and every
write is wrapped by :func:`transaction`. This is not stylistic. Python's sqlite3
module, left to its defaults, opens an implicit transaction before INSERT and
friends but *not* before DDL -- so a ``CREATE TABLE`` would commit itself
immediately and a failed migration would leave half a schema behind with no
ledger row to explain it. Managing the transaction ourselves makes a migration
genuinely all-or-nothing.
"""

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from career_agent.clock import now_utc

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: How long a writer waits for the write lock before giving up.
WRITE_WAIT_SECONDS = 120.0

_TRIGGER_START = re.compile(r"^\s*CREATE\s+(TEMP\s+|TEMPORARY\s+)?TRIGGER\b", re.IGNORECASE)
_TRIGGER_END = re.compile(r"\bEND\s*$", re.IGNORECASE)


class MigrationError(RuntimeError):
    """A migration file is malformed or could not be applied."""


#: A header line naming a ledger row, written by another lineage at this
#: file's version, that this file accepts as occupying its slot. One name per
#: line; the line is a comment to SQL and a declaration to the runner.
_RECONCILES = re.compile(r"^\s*--\s*reconciles:\s*([A-Za-z0-9_]+)\s*$", re.MULTILINE)

#: Which file of a split installation a migration changes. From
#: `SCOPED_SINCE` on, every migration names one: a split profile database
#: holds only private tables and the shared catalogue only public ones, so a
#: migration written for "the database" would find half its tables missing.
#: A one-file database (the demo, tests, a profile not split yet) holds both
#: and runs every migration. The other side records the file as considered,
#: so the ledgers stay comparable. See storage/catalogue.py.
_SCOPE = re.compile(r"^\s*--\s*scope:\s*(profile|catalogue)\s*$", re.MULTILINE)
SCOPED_SINCE = 44


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path
    #: Names of foreign ledger rows this file reconciles at its version. Empty
    #: for every ordinary migration. Non-empty only for a RESERVED SLOT: a file
    #: with no statements whose number another lineage already spent in a
    #: database this build must still be able to open.
    reconciles: frozenset[str] = frozenset()
    #: `profile`, `catalogue`, or None for the migrations written before the
    #: split, which both files ran while they were still one.
    scope: str | None = None

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def occupies(self, ledger_name: str) -> bool:
        """Does a ledger row of this version and ``ledger_name`` mean this
        migration's slot is taken -- by itself, or by a row it reconciles?"""
        return ledger_name == self.name or ledger_name in self.reconciles


def connect(db_path: Path) -> sqlite3.Connection:
    """Open (or create) the database with the pragmas this project relies on."""
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level=None means autocommit: no implicit transactions at all.
    # See the module docstring for why that is deliberate.
    #
    # `timeout` is how long `BEGIN IMMEDIATE` waits for another writer. The
    # default is five seconds; a collector writing a large board's postings
    # in one transaction takes longer than that on a 15 GB file, and the
    # interface's own writes (a status, a note) were failing with "database
    # is locked" rather than waiting. Two minutes waits out any single
    # transaction this product writes in the ordinary course -- a board's
    # postings, a page of scores, a page of index rows -- with the one
    # exception of a full-text rebuild, which holds the lock for minutes and
    # happens once after migration 0034 or under `--force`. Readers never
    # wait under WAL.
    conn = sqlite3.connect(db_path, isolation_level=None, timeout=WRITE_WAIT_SECONDS)

    # A15: SQLite defaults foreign_keys to OFF, which silently permits orphan
    # rows that then block the PostgreSQL import years later. Never optional.
    conn.execute("PRAGMA foreign_keys = ON")
    # Write-ahead logging: readers do not block the writer, and an interrupted
    # run is far less likely to leave a locked file behind.
    conn.execute("PRAGMA journal_mode = WAL")
    # Rows indexable by column name instead of position.
    conn.row_factory = sqlite3.Row
    # A split profile database opens with its shared job catalogue attached.
    # Any other database (one file holding everything) is left as it is.
    from career_agent.storage.catalogue import attach

    try:
        attach(conn, db_path)
    except BaseException:
        conn.close()
        raise
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Run a block atomically. Commits on success, rolls back on any exception.

    Works for DDL as well as DML, which the sqlite3 module's implicit handling
    does not.

    ``IMMEDIATE`` takes the write lock at BEGIN rather than at the first write.
    A deferred transaction that reads before writing can lose its snapshot to
    another writer and fail with ``SQLITE_BUSY_SNAPSHOT`` -- an error SQLite
    does NOT retry through the busy handler, so `busy_timeout` never applies
    and the loser fails in milliseconds with "database is locked". With two
    processes on one file (a `rescore` and a `serve`, which is an ordinary
    thing to do) that is a lost status edit rather than a wait.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def strip_sql_comments(sql: str) -> str:
    """Remove ``--`` line comments and ``/* */`` block comments.

    Done before splitting so that an apostrophe in prose ("a company's jobs")
    cannot be mistaken for an unterminated string literal.
    """
    without_block = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return "\n".join(line.split("--", 1)[0] for line in without_block.splitlines())


def split_statements(sql: str) -> list[str]:
    """Split a migration file into individual statements.

    We deliberately avoid ``sqlite3.Connection.executescript``: it issues an
    implicit COMMIT before running, which would destroy the surrounding
    transaction and reintroduce the half-applied-schema problem.

    Splitting on ``;`` is safe for the DDL we author. The guard below makes the
    unsafe case loud rather than silent: a semicolon inside a string literal
    would leave a fragment with an odd number of quotes.
    """
    statements: list[str] = []
    trigger: list[str] = []
    for fragment in strip_sql_comments(sql).split(";"):
        statement = fragment.strip()
        if not statement:
            continue
        if statement.count("'") % 2 != 0:
            raise MigrationError(
                "statement contains an unbalanced quote, which usually means a "
                f"semicolon inside a string literal: {statement[:80]!r}"
            )
        # A trigger body is `BEGIN <statements>; END`, so splitting on `;`
        # cuts it into fragments no parser accepts. The fragments are joined
        # back until the one that closes the body. Migration 0034 is the
        # first to declare one; before it this branch never ran.
        if trigger or _TRIGGER_START.match(statement):
            trigger.append(statement)
            if _TRIGGER_END.search(statement):
                statements.append(";\n".join(trigger) + ";")
                trigger = []
            continue
        statements.append(statement)
    if trigger:
        raise MigrationError(f"CREATE TRIGGER without a closing END: {trigger[0][:80]!r}")
    return statements


def discover_migrations(directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Every NNNN_name.sql file, ordered by version, with duplicates rejected.

    A file carrying a ``-- reconciles:`` header is a reserved slot and must
    hold no statements. The restriction is the whole safety of the directive:
    on a database where the foreign row exists the file is NOT run, so any
    DDL in it would exist on a fresh install and be missing there, and the
    two would diverge by exactly what the file claimed to do.
    """
    migrations: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        prefix, _, remainder = path.stem.partition("_")
        if not prefix.isdigit() or not remainder:
            raise MigrationError(f"{path.name} does not follow the NNNN_name.sql convention")
        text = path.read_text(encoding="utf-8")
        reconciles = frozenset(_RECONCILES.findall(text))
        if reconciles and split_statements(text):
            raise MigrationError(
                f"{path.name} declares `reconciles:` and also contains statements; "
                "a reserved slot must be empty"
            )
        scopes = _SCOPE.findall(text)
        if len(scopes) > 1:
            raise MigrationError(f"{path.name} declares more than one `scope:`")
        if int(prefix) >= SCOPED_SINCE and not scopes and not reconciles:
            raise MigrationError(
                f"{path.name} must declare `-- scope: profile` or `-- scope: catalogue`: "
                "since the shared catalogue, every migration changes one file"
            )
        migrations.append(
            Migration(
                version=int(prefix),
                name=remainder,
                path=path,
                reconciles=reconciles,
                scope=scopes[0] if scopes else None,
            )
        )

    versions = [m.version for m in migrations]
    duplicates = {v for v in versions if versions.count(v) > 1}
    if duplicates:
        raise MigrationError(f"duplicate migration versions: {sorted(duplicates)}")
    return migrations


def applied_ledger(conn: sqlite3.Connection) -> dict[int, str]:
    """Every ledger row this database holds, version to name, as recorded."""
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migration'"
    ).fetchone()
    if table_exists is None:
        return {}
    rows = conn.execute("SELECT version, name FROM schema_migration").fetchall()
    return {int(row["version"]): str(row["name"]) for row in rows}


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Which migration versions this database has already run."""
    return set(applied_ledger(conn))


def pending_migrations(
    conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR
) -> list[Migration]:
    """The migrations this build must still apply, in order.

    Raises :class:`MigrationError` on a LINEAGE COLLISION: a ledger row whose
    version this build also has and whose name this build's file neither
    carries nor reconciles. That database was migrated by another lineage
    past the point where the two agree, and applying this build's file over
    it would either fail on the first statement or, worse, be skipped as
    already done. Nothing here decides which lineage is right; it refuses to
    guess, names both, and leaves the ledger as it found it.

    A ledger row whose version is BEYOND every file this build carries is not
    a collision: that is an older build opening a newer database, which reads
    fine and writes nothing new.
    """
    ledger = applied_ledger(conn)
    pending: list[Migration] = []
    for migration in discover_migrations(directory):
        recorded = ledger.get(migration.version)
        if recorded is None:
            pending.append(migration)
        elif not migration.occupies(recorded):
            raise MigrationError(
                f"lineage collision at migration {migration.version:04d}: this database "
                f"records it as {recorded!r} and this build's file is "
                f"{migration.name!r}. The database was migrated by another lineage. "
                "Nothing was applied and the ledger was not changed. Open it with the "
                "build that wrote that row, or reserve the slot (ADR-0028)."
            )
    return pending


def migrate(conn: sqlite3.Connection, directory: Path = MIGRATIONS_DIR) -> list[Migration]:
    """Apply every pending migration. Returns those actually applied.

    Each migration runs inside one transaction together with the row that
    records it: either the tables and the ledger entry both exist, or neither
    does.
    """
    from career_agent.storage.catalogue import attached_path, role

    side = role(conn)
    if side == "profile":
        # The catalogue is migrated on its own connection, where its tables
        # are `main`, before the profile's own migrations run.
        shared = attached_path(conn)
        if shared is not None:
            other = connect(shared)
            try:
                migrate(other, directory)
            finally:
                other.close()
    applied: list[Migration] = []
    for migration in pending_migrations(conn, directory):
        runs = side == "single" or migration.scope is None or migration.scope == side
        try:
            with transaction(conn):
                for statement in split_statements(migration.sql) if runs else ():
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migration (version, name, applied_at) VALUES (?, ?, ?)",
                    (migration.version, migration.name, now_utc()),
                )
        except sqlite3.Error as exc:
            raise MigrationError(f"migration {migration.path.name} failed: {exc}") from exc
        applied.append(migration)
    return applied


def schema_version(conn: sqlite3.Connection) -> int:
    """Highest applied migration version, or 0 for an empty database."""
    versions = applied_versions(conn)
    return max(versions) if versions else 0


def table_names(conn: sqlite3.Connection) -> list[str]:
    """The tables this schema declares, without the ones SQLite invented.

    An FTS5 virtual table brings a family of shadow tables with it --
    `<name>_data`, `_idx`, `_content`, `_docsize`, `_config` -- which are an
    implementation detail of the virtual table and vary by SQLite version.
    They are filtered out rather than enumerated anywhere, because listing
    them would pin the schema's own inventory to one release's internals.

    The filter is derived from the virtual tables actually present, not from a
    hard-coded list of suffixes: a table is shadow only if a virtual table
    named as its prefix exists.
    """
    rows = conn.execute(
        "SELECT name, type, sql FROM sqlite_master"
        " WHERE type IN ('table') AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    virtual = {
        str(row["name"])
        for row in rows
        if row["sql"] and "CREATE VIRTUAL TABLE" in str(row["sql"]).upper()
    }
    return [
        str(row["name"])
        for row in rows
        if not any(
            str(row["name"]).startswith(f"{owner}_") and str(row["name"]) != owner
            for owner in virtual
        )
    ]

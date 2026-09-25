"""Converting a one-file profile database into a split profile plus the shared
catalogue, and proving nothing was lost on the way.

NEVER IN PLACE. The source database is only ever READ. Both new files are
built as copies in a staging folder, verified against the source, and only
then moved into place; the source itself is moved aside (renamed, not copied,
not deleted) into `data/legacy/`, where it stays the rollback until the owner
removes it. See docs/MULTI_PROFILE.md, "Cutover and rollback".

WHAT IS VERIFIED BEFORE ANYTHING MOVES
--------------------------------------
* `PRAGMA integrity_check` and `foreign_key_check` on both new files.
* Every public table's row count in the catalogue equals the source's, and
  every private table's row count in the profile equals the source's.
* Content digests of the rows that tie the two sides together: every
  posting's identity and text hash, every score's (job, configuration,
  content hash, score, band), every saved, hidden, noted or applied row and
  every semantic finding's (job, content hash, intent digest). A copy that
  kept the counts and changed a value would fail here.
* The new profile opens through the ordinary connection path with the
  catalogue attached, and every private row that names a posting finds it.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from career_agent.storage.catalogue import (
    PRIVATE_TABLES,
    SHARED_TABLES,
    CatalogueError,
    catalogue_path,
    make_catalogue,
    make_profile,
    role,
)

#: Private tables that name a posting, and the column that does.
JOB_REFERENCES: dict[str, str] = {
    "job_match": "job_id",
    "job_score_revision": "job_id",
    "job_application": "job_id",
    "job_application_event": "job_id",
    "requirement_review": "job_id",
    "job_retrieval_lane": "job_id",
    "job_enrichment": "job_id",
    "semantic_evaluation": "job_id",
}

#: What must survive value for value, not only in number.
DIGESTS: dict[str, str] = {
    "job": "SELECT id, provider, external_id, url, title, content_hash, first_seen_at,"
    " last_seen_at, closed_at FROM {s}job ORDER BY id",
    "job_raw": "SELECT content_hash, length(description_text) FROM {s}job_raw"
    " ORDER BY content_hash",
    "job_match": "SELECT job_id, config_id, config_version, content_hash, match_score, fit_band,"
    " eligibility_status FROM {s}job_match ORDER BY job_id, config_id, config_version",
    "job_application": "SELECT job_id, status, saved, notes, hidden_at, hidden_reason, applied_at"
    " FROM {s}job_application ORDER BY job_id",
    "semantic_evaluation": "SELECT id, job_id, content_hash, intent_digest FROM"
    " {s}semantic_evaluation ORDER BY id",
    "job_retrieval_lane": "SELECT job_id, lane, source, query_key, term_origin FROM"
    " {s}job_retrieval_lane ORDER BY job_id, lane, source, query_key",
    "candidate_state": "SELECT candidate_id, key, value FROM {s}candidate_state ORDER BY 1, 2",
    "verified_claim": "SELECT id, claim_key, revision, text FROM {s}verified_claim ORDER BY id",
    "database_identity": "SELECT id, kind, label, profile_id FROM {s}database_identity",
}


@dataclass
class SplitCheck:
    source_counts: dict[str, int] = field(default_factory=dict)
    profile_counts: dict[str, int] = field(default_factory=dict)
    catalogue_counts: dict[str, int] = field(default_factory=dict)
    digests: dict[str, bool] = field(default_factory=dict)
    orphans: dict[str, int] = field(default_factory=dict)
    integrity: dict[str, str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "problems": list(self.problems),
            "integrity": dict(self.integrity),
            "digests_equal": dict(self.digests),
            "orphans": dict(self.orphans),
            "source_counts": dict(self.source_counts),
            "profile_counts": dict(self.profile_counts),
            "catalogue_counts": dict(self.catalogue_counts),
        }


def _ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)


class SourceHold:
    """The source database, held EXCLUSIVELY from before its copies are
    taken until the moment it is moved aside.

    `locking_mode = EXCLUSIVE` keeps every other connection out, readers and
    writers alike, in this process and any other: a collection, a rescore or
    a semantic run started from a terminal while the split is running is
    refused ("database is locked") instead of committing work that would
    only reach the file being retired. The write-ahead log is emptied into
    the file first, so nothing is left behind in it.
    """

    def __init__(self, source: Path) -> None:
        self.source = Path(source)
        self.conn: sqlite3.Connection | None = None

    def __enter__(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.source, isolation_level=None, timeout=5)
        try:
            conn.execute("PRAGMA locking_mode = EXCLUSIVE")
            # A write takes the exclusive lock, and under EXCLUSIVE it is kept.
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("COMMIT")
            busy, _, _ = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if busy:
                raise CatalogueError("the source database is in use; close Career Agent first")
        except sqlite3.OperationalError as exc:
            conn.close()
            raise CatalogueError(
                "the source database is in use by another program; close Career Agent and "
                "any command using it, then try again"
            ) from exc
        except BaseException:
            conn.close()
            raise
        conn.row_factory = sqlite3.Row
        self.conn = conn
        return conn

    def release(self) -> None:
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __exit__(self, *exc: object) -> None:
        self.release()


def _tables(conn: sqlite3.Connection, schema: str = "main") -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            f"SELECT name FROM {schema}.sqlite_master WHERE type = 'table'"
            " AND name NOT LIKE 'sqlite_%'"
        )
    ]


def _counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}


def _digest(conn: sqlite3.Connection, sql: str) -> str:
    h = hashlib.sha256()
    for row in conn.execute(sql):
        h.update(repr(tuple(row)).encode("utf-8"))
    return h.hexdigest()


def copy_database(source: sqlite3.Connection | Path, destination: Path) -> None:
    """A consistent, compacted copy of a database, reading it only."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise CatalogueError(f"{destination} already exists")
    conn = source if isinstance(source, sqlite3.Connection) else _ro(source)
    try:
        conn.execute("VACUUM INTO ?", (str(destination),))
    finally:
        if conn is not source:
            conn.close()


def build(
    source: Path, staging: Path, *, held: sqlite3.Connection | None = None
) -> tuple[Path, Path, str]:
    """Build `staging/personal.db` and `staging/shared/catalogue.db` from
    `source`. Returns (profile, catalogue, catalogue id). The source is only
    read, through `held` when the caller holds it (`SourceHold`)."""
    from career_agent.storage.db import MigrationError, connect, pending_migrations

    probe = held if held is not None else _ro(source)
    try:
        if role(probe) != "single":
            raise CatalogueError("this database is already split, or is a catalogue")
        probe.row_factory = sqlite3.Row
        try:
            behind = pending_migrations(probe)
        except MigrationError as exc:
            raise CatalogueError(str(exc)) from exc
        if behind:
            raise CatalogueError(
                "this database is not at this build's schema yet; start Career Agent once "
                "(or run `career-agent doctor`) to bring it up to date, then split it"
            )
    finally:
        if probe is not held:
            probe.close()
    staging.mkdir(parents=True, exist_ok=True)
    profile = staging / "personal.db"
    catalogue = staging / "shared" / "catalogue.db"
    copy_database(held if held is not None else source, catalogue)
    conn = connect(catalogue)
    try:
        identity = make_catalogue(conn)
        conn.execute("VACUUM")
    finally:
        conn.close()
    copy_database(held if held is not None else source, profile)
    conn = sqlite3.connect(profile, isolation_level=None)
    try:
        make_profile(conn, identity)
        conn.execute("VACUUM")
    finally:
        conn.close()
    return profile, catalogue, identity


def verify(
    source: Path, profile: Path, catalogue: Path, *, held: sqlite3.Connection | None = None
) -> SplitCheck:
    """Compare the two new files with the source. Reads all three only."""
    check = SplitCheck()
    src = held if held is not None else _ro(source)
    prof = _ro(profile)
    cat = _ro(catalogue)
    try:
        source_tables = _tables(src)
        check.source_counts = _counts(src, source_tables)
        check.profile_counts = _counts(prof, _tables(prof))
        check.catalogue_counts = _counts(cat, _tables(cat))
        for name, n in check.source_counts.items():
            if name in SHARED_TABLES and check.catalogue_counts.get(name) != n:
                check.problems.append(
                    f"catalogue.{name}: {check.catalogue_counts.get(name)} != {n}"
                )
            if name in PRIVATE_TABLES and check.profile_counts.get(name) != n:
                check.problems.append(f"profile.{name}: {check.profile_counts.get(name)} != {n}")
        leaked = sorted(set(_tables(cat)) & PRIVATE_TABLES)
        if leaked:
            check.problems.append(f"private tables in the catalogue: {leaked}")
        stray = sorted(set(_tables(prof)) & SHARED_TABLES)
        if stray:
            check.problems.append(f"public tables left in the profile: {stray}")
        for label, conn in (("profile", prof), ("catalogue", cat)):
            result = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            check.integrity[label] = result
            if result != "ok":
                check.problems.append(f"{label} integrity_check: {result}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                check.problems.append(f"{label} foreign_key_check reports rows")
        for table, sql in DIGESTS.items():
            if table not in check.source_counts:
                continue
            side = cat if table in SHARED_TABLES else prof
            equal = _digest(src, sql.format(s="")) == _digest(side, sql.format(s=""))
            check.digests[table] = equal
            if not equal:
                check.problems.append(f"{table}: content differs from the source")
    finally:
        if src is not held:
            src.close()
        prof.close()
        cat.close()
    check.orphans = orphans_after_split(profile, catalogue)
    for table, n in check.orphans.items():
        # Any private row naming a posting the catalogue lacks fails the
        # split. The source enforces these as foreign keys, so there it has
        # none; one here would be a posting lost on the way.
        if n:
            check.problems.append(f"{table}: {n} rows name a posting the catalogue lacks")
    return check


def orphans_after_split(profile: Path, catalogue: Path) -> dict[str, int]:
    conn = _ro(profile)
    try:
        conn.execute("ATTACH DATABASE ? AS catalogue", (f"{catalogue.resolve().as_uri()}?mode=ro",))
        present = set(_tables(conn))
        return {
            table: int(
                conn.execute(
                    f'SELECT COUNT(*) FROM "{table}" p WHERE NOT EXISTS'
                    f" (SELECT 1 FROM catalogue.job j WHERE j.id = p.{column})"
                ).fetchone()[0]
            )
            for table, column in JOB_REFERENCES.items()
            if table in present
        }
    finally:
        conn.close()


@dataclass(frozen=True)
class Installed:
    profile: Path
    catalogue: Path
    legacy: Path


def _sidecars(path: Path) -> list[Path]:
    return [path.with_name(path.name + suffix) for suffix in ("-wal", "-shm")]


def install(
    source: Path,
    staged_profile: Path,
    staged_catalogue: Path,
    *,
    hold: SourceHold | None = None,
) -> Installed:
    """Move the verified files into place; the source goes to `data/legacy/`.

    Renames only, on one volume, each recorded so that a failure at any step
    puts every file back where it was. Order: the source aside first (a
    rename Windows refuses while anything still has it open, which is the
    refusal wanted), then the new profile where it was, then the catalogue.
    `hold` is released at the last moment, just before the first rename.
    """
    source = Path(source)
    target_catalogue = catalogue_path(source)
    for path in (target_catalogue, *_sidecars(target_catalogue)):
        if path.exists():
            raise CatalogueError(f"{path} already exists; move it away before splitting")
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    legacy_dir = target_catalogue.parent.parent / "legacy"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy = legacy_dir / f"{source.stem}.pre-catalogue-{stamp}{source.suffix}"
    target_catalogue.parent.mkdir(parents=True, exist_ok=True)
    if hold is not None:
        hold.release()
    done: list[tuple[Path, Path]] = []

    def move(origin: Path, target: Path) -> None:
        os.replace(origin, target)
        done.append((origin, target))

    try:
        move(source, legacy)
        for side, aside in zip(_sidecars(source), _sidecars(legacy), strict=True):
            if side.exists():
                move(side, aside)
        move(staged_profile, source)
        move(staged_catalogue, target_catalogue)
    except OSError as exc:
        for origin, target in reversed(done):
            os.replace(target, origin)
        raise CatalogueError(
            "the files could not be moved into place (is Career Agent or a command still "
            f"using the database?); everything was put back as it was: {exc}"
        ) from exc
    return Installed(profile=source, catalogue=target_catalogue, legacy=legacy)


def rollback(profile: Path, legacy: Path) -> Path:
    """Put the pre-catalogue database back where the profile is. The split
    profile is kept beside it (`.split-rolled-back`), and the catalogue is
    left where it is: nothing is deleted."""
    profile = Path(profile)
    legacy = Path(legacy)
    aside = profile.with_name(profile.name + ".split-rolled-back")
    if aside.exists():
        raise CatalogueError(f"{aside} already exists")
    os.replace(profile, aside)
    for side, moved in zip(_sidecars(profile), _sidecars(aside), strict=True):
        if side.exists():
            os.replace(side, moved)
    os.replace(legacy, profile)
    for side, back in zip(_sidecars(legacy), _sidecars(profile), strict=True):
        if side.exists():
            os.replace(side, back)
    return aside

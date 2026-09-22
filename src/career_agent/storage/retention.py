"""Explicit retention of superseded compute artifacts (ADR-0030).

Only job_match is deleted. Its known trigger advances the population token
and removes corresponding compute receipts; receipt-bearing revisions are
therefore protected in full. No payload, raw body or user fact is a target.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass

from career_agent.storage.invalidation import read_snapshot
from career_agent.storage.revisions import resolve


class RetentionRefused(ValueError):
    """The retained population or the reviewed plan cannot be proved safe."""


@dataclass(frozen=True)
class RetentionPlan:
    config_id: str
    current: int
    serving: int
    rollback: int
    retained: tuple[int, ...]
    candidates: tuple[int, ...]
    rows: int
    result_json_bytes: int
    population_revision: int
    database_identity: str

    @property
    def token(self) -> str:
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True).encode()).hexdigest()


def plan_history(conn: sqlite3.Connection, config_id: str, current: int) -> RetentionPlan:
    """R1, including revisions referenced by successful processing receipts.

    Refuse incomplete current populations, future versions and absent complete
    rollback populations. An old incomplete revision is not protected merely
    because it exists. Other configuration IDs are never touched.
    """
    with read_snapshot(conn):
        decision = resolve(conn, config_id, current)
        if not decision.is_current or not decision.current.complete or decision.serving is None:
            raise RetentionRefused("current revision must be complete and serving")
        versions = tuple(
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT config_version FROM job_match WHERE config_id=?", (config_id,)
            )
        )
        if any(v > current for v in versions):
            raise RetentionRefused("future revisions exist; current configuration is ambiguous")
        prior = [
            r.config_version for r in decision.previous if r.config_version < current and r.complete
        ]
        if not prior:
            raise RetentionRefused("no previous complete rollback revision")
        rollback = max(prior)
        receipts = {
            int(r[0])
            for r in conn.execute(
                "SELECT DISTINCT config_version FROM job_score_revision WHERE config_id=?",
                (config_id,),
            )
        }
        if any(v > current for v in receipts):
            raise RetentionRefused("future processing receipts exist; configuration is ambiguous")
        retained = tuple(sorted({current, rollback, *receipts}))
        candidates = tuple(sorted(set(versions) - set(retained)))
        for version in (current, rollback):
            if (
                conn.execute(
                    "SELECT COUNT(DISTINCT config_digest) FROM job_match "
                    "WHERE config_id=? AND config_version=?",
                    (config_id, version),
                ).fetchone()[0]
                != 1
            ):
                raise RetentionRefused("retained revision has ambiguous configuration digests")
        # Unknown incoming references need their own retention review, even
        # if SQLite would currently cascade them or the table is empty.
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            quoted = '"' + table.replace('"', '""') + '"'
            if any(
                fk[2] == "job_match" for fk in conn.execute(f"PRAGMA foreign_key_list({quoted})")
            ):
                raise RetentionRefused(f"incoming job_match reference requires review: {table}")
        rows = size = 0
        for version in candidates:
            row = conn.execute(
                "SELECT COUNT(*),COALESCE(SUM(length(CAST(result_json AS BLOB))),0) "
                "FROM job_match WHERE config_id=? AND config_version=?",
                (config_id, version),
            ).fetchone()
            rows += int(row[0])
            size += int(row[1])
        identity = conn.execute("SELECT * FROM database_identity").fetchall()
        return RetentionPlan(
            config_id,
            current,
            decision.serving.config_version,
            rollback,
            retained,
            candidates,
            rows,
            size,
            int(
                conn.execute(
                    "SELECT population_revision FROM compute_revision WHERE id='singleton'"
                ).fetchone()[0]
            ),
            hashlib.sha256(json.dumps([tuple(r) for r in identity]).encode()).hexdigest(),
        )


def execute_history(conn: sqlite3.Connection, reviewed: RetentionPlan) -> int:
    """Revalidate the reviewed plan under the write lock; all or nothing.

    The caller owns backup verification and durable logging. No migration or
    VACUUM is run here. A failed validation rolls back all score deletions.
    """
    if conn.in_transaction:
        raise RetentionRefused("execution requires its own transaction")
    conn.execute("BEGIN IMMEDIATE")
    try:
        fresh = plan_history(conn, reviewed.config_id, reviewed.current)
        if fresh != reviewed:
            raise RetentionRefused("database changed since the reviewed plan")
        receipts_before = conn.execute("SELECT COUNT(*) FROM job_score_revision").fetchone()[0]

        def authorize(action, table, column, database, trigger):
            # A future trigger must not silently broaden the deletion scope.
            if action == sqlite3.SQLITE_DELETE:
                return (
                    sqlite3.SQLITE_OK
                    if table in {"job_match", "job_score_revision"}
                    else sqlite3.SQLITE_DENY
                )
            if action == sqlite3.SQLITE_UPDATE:
                return (
                    sqlite3.SQLITE_OK
                    if table == "compute_revision" and column == "population_revision"
                    else sqlite3.SQLITE_DENY
                )
            if action == sqlite3.SQLITE_INSERT:
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        conn.set_authorizer(authorize)
        removed = 0
        for version in fresh.candidates:
            if version in fresh.retained or version in (
                fresh.serving,
                fresh.rollback,
                fresh.current,
            ):
                raise RetentionRefused("refusing to delete a protected revision")
            removed += conn.execute(
                "DELETE FROM job_match WHERE config_id=? AND config_version=?",
                (fresh.config_id, version),
            ).rowcount
        if removed != fresh.rows:
            raise RetentionRefused("deleted row count differs from plan")
        population = conn.execute(
            "SELECT population_revision FROM compute_revision WHERE id='singleton'"
        ).fetchone()[0]
        if population != fresh.population_revision + removed:
            raise RetentionRefused("unexpected population invalidation during retention")
        if conn.execute("SELECT COUNT(*) FROM job_score_revision").fetchone()[0] != receipts_before:
            raise RetentionRefused("a processing receipt would be lost")
        for version in (fresh.current, fresh.rollback):
            decision = resolve(conn, fresh.config_id, version)
            if not decision.is_current or not decision.current.complete:
                raise RetentionRefused("retained revision no longer serves completely")
        conn.commit()
        return removed
    except BaseException:
        conn.rollback()
        # Post-delete resolution can have cached an uncommitted population.
        # A rolled-back token can be reached again by later writes.
        from career_agent.storage import revisions

        revisions._LAST.clear()
        raise
    finally:
        conn.set_authorizer(None)

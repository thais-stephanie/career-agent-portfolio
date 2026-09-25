"""Durable input revisions and candidate-specific processing receipts.

The dirty queue is an acknowledgement boundary, never the revision clock.
All mutations here require the caller's write transaction.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from career_agent.clock import now_utc
from career_agent.config.search_config import SearchConfig


@contextmanager
def read_snapshot(conn: sqlite3.Connection) -> Iterator[None]:
    """Read job, payload and sightings from one WAL snapshot without a write lock."""
    owned = not conn.in_transaction
    if owned:
        conn.execute("BEGIN")
    try:
        yield
    finally:
        if owned:
            conn.rollback()


def input_revisions(conn: sqlite3.Connection, ids: Sequence[str]) -> dict[str, int]:
    result = dict.fromkeys(ids, 0)
    for start in range(0, len(ids), 500):
        page = ids[start : start + 500]
        marks = ",".join("?" for _ in page)
        result.update(
            (str(r[0]), int(r[1]))
            for r in conn.execute(
                f"SELECT job_id, revision FROM job_input_revision WHERE job_id IN ({marks})", page
            )
        )
    return result


def request(conn: sqlite3.Connection, ids: Sequence[str]) -> None:
    """Persist explicit work before processing, so a crashed request can be retried."""
    if not conn.in_transaction:
        raise RuntimeError("request requires a write transaction")
    # A split profile keeps its own requests (storage/catalogue.py): the
    # shared queue and the shared input revisions are for public changes.
    from career_agent.storage.catalogue import ensure_profile_tables, role

    if role(conn) == "profile":
        ensure_profile_tables(conn)
        generation = int(
            conn.execute(
                "SELECT COALESCE(MAX(generation), 0) FROM main.profile_request"
            ).fetchone()[0]
        )
        conn.executemany(
            "INSERT INTO main.profile_request (job_id, generation, marked_at) VALUES (?, ?, ?)"
            " ON CONFLICT(job_id) DO UPDATE SET generation = excluded.generation,"
            " marked_at = excluded.marked_at",
            [(jid, generation + 1, now_utc()) for jid in ids],
        )
        return
    conn.execute("UPDATE compute_revision SET revision = revision + 1 WHERE id = 'singleton'")
    conn.executemany(
        "INSERT INTO job_dirty(job_id, reason, generation, marked_at)"
        " VALUES (?, 'REQUESTED',"
        " (SELECT revision FROM compute_revision WHERE id='singleton'), ?)"
        " ON CONFLICT(job_id) DO UPDATE SET reason=excluded.reason,"
        " generation=excluded.generation, marked_at=excluded.marked_at",
        [(jid, now_utc()) for jid in ids],
    )


def receipt(conn: sqlite3.Connection, job_id: str, config: SearchConfig, revision: int) -> None:
    conn.execute(
        "INSERT INTO job_score_revision VALUES (?, ?, ?, ?)"
        " ON CONFLICT(job_id, config_id, config_version) DO UPDATE SET revision=excluded.revision",
        (job_id, config.config_id, config.config_version, revision),
    )


def score_revisions(
    conn: sqlite3.Connection, ids: Sequence[str], config_id: str, config_version: int
) -> dict[str, int]:
    """The input revision each stored score was computed from, by job.

    Absent means zero, which is exactly what migration 0035 wrote down: legacy
    scores have implicit input revision 0. So a posting nothing has touched
    since the revision clock started, holding a score written before receipts
    existed, compares equal -- and one that HAS been touched has a non-zero
    input revision and compares unequal. Absence is not being read as
    permission here; it is being read as the value the migration assigned.
    """
    result = dict.fromkeys(ids, 0)
    for start in range(0, len(ids), 500):
        page = ids[start : start + 500]
        marks = ",".join("?" for _ in page)
        result.update(
            (str(r[0]), int(r[1]))
            for r in conn.execute(
                "SELECT job_id, revision FROM job_score_revision"
                f" WHERE config_id = ? AND config_version = ? AND job_id IN ({marks})",
                (config_id, config_version, *page),
            )
        )
    return result

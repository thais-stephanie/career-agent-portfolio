"""The rescore plan reads indexes, not rows. Asserted over EXPLAIN QUERY PLAN.

Migration 0034 widened `idx_job_match_population` so that "does this posting
hold a current score" is answered inside the index. If a later migration
narrows it, or a later edit adds a column to the currency test that the index
does not carry, the anti-join goes back to reading 245,000 score rows -- and
nothing but a stopwatch on the production corpus would notice. This test
notices on a fresh database in milliseconds.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from career_agent.storage.db import connect, migrate


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "plan.db")
    migrate(conn)
    yield conn
    conn.close()


def _plan(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, params).fetchall()
    return "\n".join(str(r[3]) for r in rows)


ANTI_JOIN = (
    "SELECT j.id FROM job j"
    " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL"
    " AND NOT EXISTS ("
    "   SELECT 1 FROM job_match m INDEXED BY idx_job_match_population"
    "   WHERE m.config_id = ? AND m.config_version = ? AND m.job_id = j.id"
    "     AND m.content_hash = j.content_hash"
    "     AND m.schema_version >= ? AND m.config_digest = ?)"
    " ORDER BY j.id"
)


def test_the_anti_join_is_covered_on_both_sides(db: sqlite3.Connection) -> None:
    text = _plan(db, ANTI_JOIN, ("personal-alpha", 1, 8, "d"))
    assert "COVERING INDEX idx_job_open_population" in text, text
    assert "COVERING INDEX idx_job_match_population" in text, text
    assert "SCAN job_match" not in text and "SCAN job " not in text, text


def test_the_population_index_carries_every_column_of_the_currency_test(
    db: sqlite3.Connection,
) -> None:
    columns = [str(r["name"]) for r in db.execute("PRAGMA index_info(idx_job_match_population)")]
    assert columns == [
        "config_id",
        "config_version",
        "job_id",
        "content_hash",
        "schema_version",
        "config_digest",
    ]


def test_the_durable_clock_is_a_point_lookup(db: sqlite3.Connection) -> None:
    text = _plan(db, "SELECT revision FROM compute_revision WHERE id='singleton'")
    assert "SEARCH compute_revision USING INDEX" in text, text


def test_the_six_triggers_exist_and_nothing_else_writes_the_ledger(
    db: sqlite3.Connection,
) -> None:
    names = {
        str(r["name"]) for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
    }
    assert {
        "trg_job_dirty_insert",
        "trg_job_dirty_update",
        "trg_job_dirty_payload",
        "trg_job_dirty_sighting_insert",
        "trg_job_dirty_sighting_update",
        "trg_job_dirty_company",
    } <= names
    assert "trg_input_revision_insert" in names
    assert "trg_input_revision_update" in names


def test_the_plan_reads_nothing_about_a_person() -> None:
    """Which postings need a score is a fact about the corpus and its stored
    scores (ADR-0025, rule 7; ADR-0019). The planning half of `rescore.py`
    may read the configuration's identity -- id, version, digest -- and
    nothing it says about the candidate. Asserted over the module's own
    syntax tree, the way `test_source_scheduling` guards the scheduler."""
    import ast
    import inspect

    from career_agent.pipeline import rescore as module

    source = inspect.getsource(module)
    tree = ast.parse(source)
    planning = {
        "plan",
        "_read_ledger",
        "_ledger_reasons",
        "_missing_or_stale",
        "_without_current_score",
        "_open_scoreable",
        "_open_counts",
        "_explicit_targets",
    }
    forbidden = {
        "preferences",
        "eligibility",
        "lexicon",
        "screening",
        "taxonomy",
        "scoring",
        "regions",
        "seniority",
        "occupation",
        "skills",
        "evidence",
        "claim",
    }
    offenders: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in planning:
            for inner in ast.walk(node):
                if isinstance(inner, ast.Attribute) and inner.attr in forbidden:
                    offenders.append(f"{node.name}: .{inner.attr}")
                if isinstance(inner, ast.Constant) and isinstance(inner.value, str):
                    hits = [w for w in forbidden if w in inner.value.lower()]
                    if hits:
                        offenders.append(f"{node.name}: {inner.value[:40]!r} ({hits})")
    assert not offenders, offenders

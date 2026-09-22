"""The Discover narrowings are written so the planner can SEEK the gates index,
and a chip that makes `job` the cheaper side still probes scores by id.

Measured 2026-09-12 on the 245,871-row revision of the corpus copy: with the
default narrowings written as `eligibility_status != ...` and a worksite
chip, SQLite planned the count on `idx_job_match_experience_years
(config_id, config_version)` and read every row of the revision -- 2.5 to
4.6 s, paid again by the page, the facets and four hidden counters. Written
as the COMPLEMENT over the closed vocabulary (`IN ('VERIFIED_ELIGIBLE',
'LIKELY_ELIGIBLE')`, `= 'NOT_BLOCKED'`) the same predicates are equalities on
the third and fourth columns of `idx_job_match_discovery_gates`, and the
count is 0.26 s. An earlier attempt NAMED the index with `INDEXED BY`; that
turned a provider chip, where the planner rightly drives from `job`, into a
102 s skip-scan. Both shapes are pinned here, over `EXPLAIN QUERY PLAN`.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import ELIGIBILITY_STATUSES, JobFilter, ScoredJobQuery


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "plan.db")
    migrate(conn)
    yield conn
    conn.close()


def _narrowed(**kw: object) -> JobFilter:
    base: dict[str, object] = dict(
        include_ineligible=False,
        include_unresolved=False,
        include_off_target=False,
        include_user_hidden=False,
        group_duplicates=False,
    )
    base.update(kw)
    return JobFilter(**base)  # type: ignore[arg-type]


def _count_plan(db: sqlite3.Connection, f: JobFilter) -> tuple[str, str]:
    repo = ScoredJobQuery(db)
    where, params = repo._where("personal-alpha", 1, f)
    sql = "SELECT COUNT(*)" + repo._from_for(repo._joins_needed(f), f=f) + where
    plan = "\n".join(str(r[3]) for r in db.execute("EXPLAIN QUERY PLAN " + sql, params))
    return where, plan


def test_the_vocabulary_constant_is_the_check_constraint(db: sqlite3.Connection) -> None:
    """The complement argument holds only while the list here IS the
    vocabulary the table enforces. Read it off the schema rather than trust
    a comment."""
    sql = db.execute("SELECT sql FROM sqlite_master WHERE name = 'job_match'").fetchone()[0]
    match = re.search(
        r"eligibility_status\s+TEXT\s+NOT\s+NULL\s+CHECK\s*\(\s*eligibility_status\s+IN\s*\(([^)]*)\)",
        sql,
        re.S,
    )
    assert match, sql
    declared = tuple(v.strip().strip("'") for v in match.group(1).split(","))
    assert declared == ELIGIBILITY_STATUSES


def test_both_narrowings_become_one_positive_list(db: sqlite3.Connection) -> None:
    where, plan = _count_plan(db, _narrowed())
    assert "IN ('VERIFIED_ELIGIBLE', 'LIKELY_ELIGIBLE')" in where, where
    assert "!=" not in where, where
    assert "screening_state = 'NOT_BLOCKED'" in where, where
    assert "idx_job_match_discovery_gates" in plan and "eligibility_status=?" in plan, plan


def test_hiding_only_the_ineligible_shows_the_other_three(db: sqlite3.Connection) -> None:
    where, _ = _count_plan(db, _narrowed(include_unresolved=True))
    assert "IN ('VERIFIED_ELIGIBLE', 'LIKELY_ELIGIBLE', 'UNRESOLVED')" in where, where


def test_a_narrowed_scan_with_another_chip_seeks_the_gates_index(db: sqlite3.Connection) -> None:
    _, plan = _count_plan(db, _narrowed(worksites=("REMOTE",)))
    assert "idx_job_match_discovery_gates" in plan, plan
    assert "eligibility_status=?" in plan and "screening_state=?" in plan, plan
    assert "idx_job_match_experience_years" not in plan, plan


def test_a_provider_chip_probes_scores_by_id_without_a_skip_scan(db: sqlite3.Connection) -> None:
    for grouped in (False, True):
        _, plan = _count_plan(db, _narrowed(providers=("greenhouse",), group_duplicates=grouped))
        assert "ANY(" not in plan and "SKIP-SCAN" not in plan, plan
        assert "INDEXED BY" not in plan


def test_a_grouped_read_probes_by_id(db: sqlite3.Connection) -> None:
    _, plan = _count_plan(db, _narrowed(group_duplicates=True))
    assert "ANY(" not in plan, plan
    assert plan.count("idx_job_match_discovery_gates") >= 2, plan


def test_saved_only_materializes_saved_ids_before_probing_the_population(db):
    where, plan = _count_plan(db, _narrowed(saved_only=True, exempt_tracked=True))
    assert "IN (SELECT job_id FROM job_application WHERE saved = 1)" in where
    assert "LIST SUBQUERY" in plan
    assert "job_id=?" in plan

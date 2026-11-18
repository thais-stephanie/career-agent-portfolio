"""Migrations 0062 and 0063: the receipts check reads indexes, and selects the same postings.

`rescore._missing_or_stale` compares every open posting's input revision with
its score's receipt. 0062 and 0063 put `revision` in an index on each side,
and the query names them (Phase 4 Step 3). These tests pin:

- the plan: both probes covering, no row read of either table;
- the answer: the same postings as the query as it was written before, on
  many synthetic states (empty, closed, textless, stale and current receipts,
  other configurations, moved readings, ledger marks), and the same whole
  plan (targets, order, breakdown, priority);
- the split install: each index on its own side, and the same plan there;
- the upgrade from 61, an older build opening the result, and the rollback.

Synthetic throughout: the demo postings under a temporary folder.
"""

from __future__ import annotations

import random
import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.integration.test_shared_catalogue import two_profiles  # noqa: F401
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline import rescore as rescore_mod
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import RescoreMode, plan, rescore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import (
    MIGRATIONS_DIR,
    applied_versions,
    connect,
    migrate,
    transaction,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: The receipts query exactly as it was before 0062/0063 (rescore.py at 9636ecf).
LEGACY_CHANGED = (
    "SELECT i.job_id FROM job_input_revision i JOIN job j ON j.id=i.job_id"
    " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL AND NOT EXISTS ("
    " SELECT 1 FROM job_score_revision r WHERE r.job_id=i.job_id"
    " AND r.config_id=? AND r.config_version=? AND r.revision=i.revision)"
)
CHANGED = (
    "SELECT i.job_id FROM job_input_revision i INDEXED BY idx_job_input_revision_cover"
    " JOIN job j ON j.id=i.job_id"
    " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL AND NOT EXISTS ("
    " SELECT 1 FROM job_score_revision r INDEXED BY idx_job_score_revision_cover"
    " WHERE r.job_id=i.job_id"
    " AND r.config_id=? AND r.config_version=? AND r.revision=i.revision)"
)


def _legacy_missing_or_stale(conn, config_id, config_version, config_digest):
    """`_missing_or_stale` as it was: the same first query, the old second one."""
    from career_agent.storage.mvp_repo import MATCH_SCHEMA_VERSION

    rows = conn.execute(
        "SELECT j.id FROM job j"
        " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM job_match m INDEXED BY idx_job_match_population"
        "   WHERE m.config_id = ? AND m.config_version = ? AND m.job_id = j.id"
        "     AND m.content_hash = j.content_hash"
        "     AND m.schema_version >= ? AND m.config_digest = ?)"
        " ORDER BY j.id",
        (config_id, config_version, MATCH_SCHEMA_VERSION, config_digest),
    ).fetchall()
    changed = conn.execute(LEGACY_CHANGED, (config_id, config_version)).fetchall()
    return sorted({str(r[0]) for r in rows} | {str(r[0]) for r in changed})


def _plan_text(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> str:
    return "\n".join(str(r[3]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))


def _config_dir(tmp_path: Path) -> Path:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    return config


@pytest.fixture
def seeded(tmp_path: Path):  # noqa: ANN201
    config_dir = _config_dir(tmp_path)
    config, _ = load_search_config(config_dir)
    conn = connect(tmp_path / "plan.db")
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Receipts")
    seed_demo(conn, config, source=DEMO_FILE)
    yield conn, config
    conn.close()


def _same_plan(conn: sqlite3.Connection, config, monkeypatch, **kw) -> None:
    new = plan(conn, config, **kw)
    with monkeypatch.context() as patch:
        patch.setattr(rescore_mod, "_missing_or_stale", _legacy_missing_or_stale)
        old = plan(conn, config, **kw)
    assert (new.targets, new.breakdown, new.priority, new.dirty, new.search_refresh) == (
        old.targets,
        old.breakdown,
        old.priority,
        old.dirty,
        old.search_refresh,
    )
    assert (new.candidates_examined, new.textless_open, new.closed_candidates) == (
        old.candidates_examined,
        old.textless_open,
        old.closed_candidates,
    )


# -- the plan ------------------------------------------------------------------


def test_both_probes_read_only_their_covering_index(seeded) -> None:
    conn, config = seeded
    text = _plan_text(conn, CHANGED, (config.config_id, config.config_version))
    assert "COVERING INDEX idx_job_input_revision_cover" in text, text
    assert "COVERING INDEX idx_job_score_revision_cover" in text, text
    assert "sqlite_autoindex_job_input_revision" not in text, text
    assert "sqlite_autoindex_job_score_revision" not in text, text
    # Without the names the planner keeps the primary keys and reads rows.
    legacy = _plan_text(conn, LEGACY_CHANGED, (config.config_id, config.config_version))
    assert "COVERING INDEX idx_job_score_revision_cover" not in legacy, legacy


def test_the_product_runs_this_query(seeded) -> None:
    """The SQL pinned here is the SQL `_missing_or_stale` sends."""
    conn, config = seeded
    seen: list[str] = []
    conn.set_trace_callback(seen.append)
    rescore_mod._missing_or_stale(conn, config.config_id, config.config_version, "d")
    conn.set_trace_callback(None)
    sent = [s for s in seen if "job_input_revision" in s]
    assert len(sent) == 1, seen
    assert "job_input_revision i INDEXED BY idx_job_input_revision_cover" in sent[0], sent
    assert "job_score_revision r INDEXED BY idx_job_score_revision_cover" in sent[0], sent


def test_the_indexes_carry_the_revision(seeded) -> None:
    conn, _ = seeded
    cols = lambda name: [str(r[2]) for r in conn.execute(f"PRAGMA index_info({name})")]  # noqa: E731
    assert cols("idx_job_input_revision_cover") == ["job_id", "revision"]
    assert cols("idx_job_score_revision_cover") == [
        "job_id",
        "config_id",
        "config_version",
        "revision",
    ]


def test_a_missing_index_is_a_loud_error_not_a_slow_answer(seeded) -> None:
    conn, config = seeded
    with transaction(conn):
        conn.execute("DROP INDEX idx_job_score_revision_cover")
    with pytest.raises(sqlite3.OperationalError, match="no such index"):
        rescore_mod._missing_or_stale(conn, config.config_id, config.config_version, "d")


# -- the receipts rule, pinned ---------------------------------------------------


def _changed(conn, config) -> set[str]:
    """What the PRODUCT selects (`_missing_or_stale`), for postings whose score
    is current: there the answer is the receipts query's alone."""
    current = {
        str(r[0])
        for r in conn.execute(
            "SELECT m.job_id FROM job_match m JOIN job j ON j.id = m.job_id"
            " WHERE m.config_id = ? AND m.config_version = ? AND m.content_hash = j.content_hash",
            (config.config_id, config.config_version),
        )
    }
    chosen = rescore_mod._missing_or_stale(
        conn, config.config_id, config.config_version, str(config.digest)
    )
    return set(chosen) & current


def _receipt(conn, job_id, config_id, version, revision) -> None:
    conn.execute(
        "INSERT INTO job_score_revision VALUES (?, ?, ?, ?)"
        " ON CONFLICT(job_id, config_id, config_version) DO UPDATE SET revision=excluded.revision",
        (job_id, config_id, version, revision),
    )


def test_a_receipt_certifies_only_its_own_revision_and_configuration(seeded) -> None:
    conn, config = seeded
    job_id, revision = conn.execute(
        "SELECT i.job_id, i.revision FROM job_input_revision i JOIN job j ON j.id = i.job_id"
        " JOIN job_match m ON m.job_id = j.id AND m.content_hash = j.content_hash"
        " AND m.config_id = ? AND m.config_version = ?"
        " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL ORDER BY i.job_id LIMIT 1",
        (config.config_id, config.config_version),
    ).fetchone()
    cid, ver = config.config_id, config.config_version
    with transaction(conn):
        conn.execute("DELETE FROM job_score_revision WHERE job_id = ?", (job_id,))
    assert job_id in _changed(conn, config)  # no receipt at all
    with transaction(conn):
        _receipt(conn, job_id, cid, ver, revision - 1)
    assert job_id in _changed(conn, config)  # an older revision
    with transaction(conn):
        _receipt(conn, job_id, cid, ver + 1, revision)
        _receipt(conn, job_id, "another-config", ver, revision)
    assert job_id in _changed(conn, config)  # right revision, other configuration
    with transaction(conn):
        _receipt(conn, job_id, cid, ver, revision)
    assert job_id not in _changed(conn, config)  # current
    with transaction(conn):
        conn.execute(
            "UPDATE job_input_revision SET revision = ? WHERE job_id = ?", (revision + 5, job_id)
        )
    assert job_id in _changed(conn, config)  # the input moved on
    with transaction(conn):
        conn.execute("UPDATE job SET closed_at = '2026-10-01T00:00:00Z' WHERE id = ?", (job_id,))
    assert job_id not in rescore_mod._missing_or_stale(
        conn, cid, ver, str(config.digest)
    )  # closed postings are never targets


# -- the same answer, on many states ----------------------------------------------


def _shuffle_state(conn: sqlite3.Connection, config, rnd: random.Random) -> None:
    """Move the synthetic corpus into a random but legal state."""
    cid, ver = config.config_id, config.config_version
    jobs = [str(r[0]) for r in conn.execute("SELECT id FROM job ORDER BY id")]
    hashes = [
        str(r[0]) for r in conn.execute("SELECT content_hash FROM job_raw ORDER BY content_hash")
    ]
    with transaction(conn):
        for job_id in jobs:
            roll = rnd.random()
            if roll < 0.1:
                conn.execute(
                    "UPDATE job SET closed_at = '2026-10-01T00:00:00Z' WHERE id = ?", (job_id,)
                )
            elif roll < 0.15:
                conn.execute("UPDATE job SET content_hash = NULL WHERE id = ?", (job_id,))
            if rnd.random() < 0.15:
                conn.execute(
                    "DELETE FROM job_match WHERE job_id = ? AND config_version = ?", (job_id, ver)
                )
            elif rnd.random() < 0.1:
                conn.execute(
                    "UPDATE job_match SET content_hash = ? WHERE job_id = ? AND config_version = ?",
                    (rnd.choice(hashes), job_id, ver),  # scored on some other text
                )
            elif rnd.random() < 0.1:
                conn.execute(
                    "UPDATE job_match SET input_digest = 'an-older-reader' WHERE job_id = ?"
                    " AND config_version = ?",
                    (job_id, ver),
                )
        # Receipts after the score writes (their triggers delete receipts).
        for job_id, revision in conn.execute(
            "SELECT job_id, revision FROM job_input_revision"
        ).fetchall():
            roll = rnd.random()
            if roll < 0.55:
                _receipt(conn, job_id, cid, ver, revision)
            elif roll < 0.7:
                _receipt(conn, job_id, cid, ver, revision - rnd.randint(1, 3))
            elif roll < 0.8:
                _receipt(conn, job_id, cid, ver - 1, revision)
            elif roll < 0.9:
                _receipt(conn, job_id, cid, ver + 1, revision)  # a newer version's receipt
            if rnd.random() < 0.3:
                _receipt(conn, job_id, "another-config", ver, revision)
            if rnd.random() < 0.15:
                conn.execute(
                    "UPDATE job_input_revision SET revision = revision + ? WHERE job_id = ?",
                    (rnd.randint(1, 4), job_id),
                )
        if rnd.random() < 0.5:
            conn.execute("DELETE FROM job_dirty")
        elif rnd.random() < 0.5:
            conn.execute("DELETE FROM job_input_revision WHERE rowid % 3 = 0")


@pytest.mark.parametrize("seed", range(40))
def test_the_same_postings_and_the_same_plan_as_before(seeded, seed, monkeypatch) -> None:
    conn, config = seeded
    _shuffle_state(conn, config, random.Random(seed))
    args = (conn, config.config_id, config.config_version, str(config.digest))
    assert rescore_mod._missing_or_stale(*args) == _legacy_missing_or_stale(*args)
    for version in (config.config_version, config.config_version - 1):
        assert {str(r[0]) for r in conn.execute(CHANGED, (config.config_id, version))} == {
            str(r[0]) for r in conn.execute(LEGACY_CHANGED, (config.config_id, version))
        }
    _same_plan(conn, config, monkeypatch)
    _same_plan(conn, config, monkeypatch, mode=RescoreMode.DIRTY)


def test_empty_and_all_closed_corpora(seeded, monkeypatch) -> None:
    conn, config = seeded
    _same_plan(conn, config, monkeypatch)
    with transaction(conn):
        conn.execute("UPDATE job SET closed_at = '2026-10-01T00:00:00Z'")
    assert _changed(conn, config) == set()
    _same_plan(conn, config, monkeypatch)
    with transaction(conn):
        conn.execute("DELETE FROM job_input_revision")
        conn.execute("DELETE FROM job_score_revision")
    assert _changed(conn, config) == set()
    _same_plan(conn, config, monkeypatch)


def test_a_full_pass_leaves_nothing_and_the_count_agrees(seeded, monkeypatch) -> None:
    conn, config = seeded
    rescore(conn, config)
    assert plan(conn, config).targets == []
    _same_plan(conn, config, monkeypatch)


# -- the split install -------------------------------------------------------------


def _names(path: Path) -> set[str]:
    db = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        return {
            str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'index'")
        }
    finally:
        db.close()


def test_each_index_lives_on_its_own_side(two_profiles: dict) -> None:  # noqa: F811
    catalogue = _names(two_profiles["catalogue"])
    for profile_db in (two_profiles["a_db"], two_profiles["b_db"]):
        mine = _names(profile_db)
        assert "idx_job_score_revision_cover" in mine
        assert "idx_job_input_revision_cover" not in mine
    assert "idx_job_input_revision_cover" in catalogue
    assert "idx_job_score_revision_cover" not in catalogue


def test_a_split_profile_plans_the_same_postings(two_profiles: dict, monkeypatch) -> None:  # noqa: F811
    for db_key, config_key in (("a_db", "a_config"), ("b_db", "b_config")):
        config, _ = load_search_config(two_profiles[config_key])
        conn = connect(two_profiles[db_key])
        try:
            text = _plan_text(conn, CHANGED, (config.config_id, config.config_version))
            assert "COVERING INDEX idx_job_input_revision_cover" in text, text
            assert "COVERING INDEX idx_job_score_revision_cover" in text, text
            _same_plan(conn, config, monkeypatch)
            rescore(conn, config)
            _same_plan(conn, config, monkeypatch)
        finally:
            conn.close()


# -- the upgrade ---------------------------------------------------------------------


def _rows(conn: sqlite3.Connection) -> tuple:
    return tuple(
        conn.execute(f"SELECT COUNT(*), TOTAL(revision) FROM {t}").fetchone()[:]
        for t in ("job_input_revision", "job_score_revision")
    )


def test_upgrading_from_61_moves_no_row_and_rolls_back(tmp_path: Path, monkeypatch) -> None:
    """The code needs 0062/0063 (a missing index is an error, as for the
    other INDEXED BY): a rollback reverts the code with the indexes."""
    older = tmp_path / "migrations61"
    older.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.stem[:4]) <= 61:
            shutil.copy(path, older)
    config, _ = load_search_config(_config_dir(tmp_path))
    db = connect(tmp_path / "upgrade.db")
    migrate(db, older)
    with transaction(db):
        stamp_identity(db, RuntimeMode.PERSONAL, "Upgrade")
    seed_demo(db, config, source=DEMO_FILE)
    with monkeypatch.context() as patch:  # the code of the build that is at 61
        patch.setattr(rescore_mod, "_missing_or_stale", _legacy_missing_or_stale)
        rescore(db, config)
        with transaction(db):  # something left to plan
            db.execute("UPDATE job_input_revision SET revision = revision + 1 WHERE rowid % 2 = 0")
        before, planned = _rows(db), plan(db, config).targets
    assert planned
    with pytest.raises(sqlite3.OperationalError, match="no such index"):
        plan(db, config)  # this build's code on a database it has not migrated
    assert [m.version for m in migrate(db)] == [62, 63]
    assert _rows(db) == before
    assert plan(db, config).targets == planned
    assert not migrate(db)
    assert not migrate(db, older)  # an older build opens it: nothing to do
    # Rollback: drop the indexes and their ledger rows. The older code runs.
    with transaction(db):
        db.execute("DROP INDEX idx_job_input_revision_cover")
        db.execute("DROP INDEX idx_job_score_revision_cover")
        db.execute("DELETE FROM schema_migration WHERE version IN (62, 63)")
    assert {62, 63}.isdisjoint(applied_versions(db))
    assert _rows(db) == before
    with monkeypatch.context() as patch:
        patch.setattr(rescore_mod, "_missing_or_stale", _legacy_missing_or_stale)
        assert plan(db, config).targets == planned
    assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [m.version for m in migrate(db)] == [62, 63]
    db.close()

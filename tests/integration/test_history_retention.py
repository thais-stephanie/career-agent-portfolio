"""Retention must preserve serving, rollback, receipts and all user facts."""

import sqlite3
from dataclasses import replace
from types import SimpleNamespace

import pytest
from tests.unit.test_mvp_repo import CONFIG_ID, DIGEST, a_full_result, seed_job

from career_agent.storage.db import connect, migrate
from career_agent.storage.mvp_repo import MatchRepo
from career_agent.storage.retention import RetentionRefused, execute_history, plan_history
from career_agent.storage.revisions import resolve


@pytest.fixture
def corpus(tmp_path):
    conn = connect(tmp_path / "retention.db")
    migrate(conn)
    jobs = [seed_job(conn, external_id=str(n)) for n in range(4)]
    for version, count in ((1, 4), (2, 2), (3, 4), (4, 4)):
        for job, content in jobs[:count]:
            MatchRepo(conn).store(
                job, content, a_full_result(config_version=version), config_digest=DIGEST
            )
    yield conn, jobs
    conn.close()


def test_r1_keeps_serving_and_previous_complete_and_all_other_tables(corpus):
    conn, _ = corpus
    before = {
        t: [tuple(r) for r in conn.execute(f'SELECT * FROM "{t}"')]
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        if t not in {"job_match", "compute_revision"}
    }
    plan = plan_history(conn, CONFIG_ID, 4)
    assert plan.retained == (3, 4)
    assert plan.candidates == (1, 2)  # partial history is not immortal
    assert plan.rows == 6 and plan.result_json_bytes > 0
    assert execute_history(conn, plan) == 6
    for version in (3, 4):
        result = resolve(conn, CONFIG_ID, version)
        assert result.is_current and result.current.complete
    for table, rows in before.items():
        assert [tuple(r) for r in conn.execute(f'SELECT * FROM "{table}"')] == rows
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_receipt_reference_preserves_the_whole_revision(corpus):
    conn, jobs = corpus
    conn.execute("INSERT INTO job_score_revision VALUES (?,?,?,?)", (jobs[0][0], CONFIG_ID, 1, 0))
    plan = plan_history(conn, CONFIG_ID, 4)
    assert plan.retained == (1, 3, 4)
    assert execute_history(conn, plan) == 2
    assert conn.execute("SELECT COUNT(*) FROM job_score_revision").fetchone()[0] == 1


def test_stale_or_forged_plan_is_refused_without_deletion(corpus):
    conn, jobs = corpus
    plan = plan_history(conn, CONFIG_ID, 4)
    with pytest.raises(RetentionRefused):
        execute_history(conn, replace(plan, candidates=(4,)))
    conn.execute("UPDATE job SET title='Changed' WHERE id=?", (jobs[0][0],))
    with pytest.raises(RetentionRefused, match="changed"):
        execute_history(conn, plan)
    assert conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0] == 14


def test_future_receipt_refuses_even_before_a_future_score_exists(corpus):
    conn, jobs = corpus
    conn.execute("INSERT INTO job_score_revision VALUES (?,?,?,?)", (jobs[0][0], CONFIG_ID, 5, 0))
    with pytest.raises(RetentionRefused, match="future processing receipts"):
        plan_history(conn, CONFIG_ID, 4)
    assert conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0] == 14


def test_incomplete_current_or_future_revision_refuses(corpus):
    conn, _ = corpus
    with pytest.raises(RetentionRefused):
        plan_history(conn, CONFIG_ID, 2)
    with pytest.raises(RetentionRefused, match="future"):
        plan_history(conn, CONFIG_ID, 3)
    with pytest.raises(RetentionRefused):
        plan_history(conn, CONFIG_ID, 5)


def test_no_complete_rollback_refuses(corpus):
    conn, _ = corpus
    conn.execute("DELETE FROM job_match WHERE config_version IN (1,3)")
    with pytest.raises(RetentionRefused, match="rollback"):
        plan_history(conn, CONFIG_ID, 4)


def test_unknown_reference_refuses(corpus):
    conn, _ = corpus
    conn.execute("CREATE TABLE audit_fact (match_id TEXT REFERENCES job_match(id))")
    with pytest.raises(RetentionRefused, match="reference"):
        plan_history(conn, CONFIG_ID, 4)


def test_new_trigger_cannot_delete_user_facts(corpus):
    conn, _ = corpus
    conn.execute(
        "CREATE TRIGGER unexpected AFTER DELETE ON job_match BEGIN DELETE FROM job_application; END"
    )
    plan = plan_history(conn, CONFIG_ID, 4)
    with pytest.raises(sqlite3.DatabaseError):
        execute_history(conn, plan)
    assert conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0] == 14


def test_failure_mid_delete_rolls_back_all_rows(corpus):
    conn, _ = corpus
    conn.execute(
        "CREATE TRIGGER reject_delete BEFORE DELETE ON job_match "
        "WHEN OLD.config_version=2 BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    plan = plan_history(conn, CONFIG_ID, 4)
    with pytest.raises(sqlite3.IntegrityError, match="injected"):
        execute_history(conn, plan)
    assert conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0] == 14


def test_command_defaults_to_read_only_and_requires_execution_proofs(corpus, monkeypatch, capsys):
    from scripts import prune_history

    conn, _ = corpus
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    monkeypatch.setattr(
        prune_history,
        "load_search_config",
        lambda _: (SimpleNamespace(config_id=CONFIG_ID, config_version=4, digest=DIGEST), None),
    )
    assert prune_history.main(["--db", path]) == 0
    assert '"executed": false' in capsys.readouterr().out
    with pytest.raises(RetentionRefused, match="requires"):
        prune_history.main(["--db", path, "--execute"])
    assert conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0] == 14


def test_backup_verification_requires_identical_bytes_and_empty_wal(tmp_path):
    from scripts.prune_history import verified_file_backup

    original = tmp_path / "a.db"
    backup = tmp_path / "b.db"
    original.write_bytes(b"same bytes")
    backup.write_bytes(b"same bytes")
    verified_file_backup(original, backup)
    backup.write_bytes(b"different")
    with pytest.raises(RetentionRefused, match="byte-identical"):
        verified_file_backup(original, backup)
    backup.write_bytes(b"same bytes")
    (tmp_path / "a.db-wal").write_bytes(b"pending")
    with pytest.raises(RetentionRefused, match="checkpointed"):
        verified_file_backup(original, backup)

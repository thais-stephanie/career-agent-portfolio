"""Independent audit reproducers at ab02a67, now mandatory correctness regressions."""
# ruff: noqa: F811

import importlib

from tests.integration.test_incremental_rescore import (
    _assert_incremental_equals_full,
    _full_recompute_on_a_copy,
    _snapshot,
    corpus,  # noqa: F401
)

from career_agent.pipeline.rescore import _clear_ledger, plan, rescore
from career_agent.storage.db import transaction


def test_explicit_pass_preserves_unprocessed_fact_changes(corpus, tmp_path):
    conn, config, ids, _ = corpus
    a, b = ids["seed-0"], ids["seed-2"]
    with transaction(conn):
        conn.execute(
            "UPDATE job SET title='Senior Integration Specialist' WHERE id IN (?,?)", (a, b)
        )
    rescore(conn, config, job_ids=[a])
    pending = plan(conn, config).targets
    stale = conn.execute("SELECT match_score FROM job_match WHERE job_id=?", (b,)).fetchone()[0]
    rescore(conn, config, job_ids=[b])
    fresh = conn.execute("SELECT match_score FROM job_match WHERE job_id=?", (b,)).fetchone()[0]
    assert fresh != stale, "The lost invalidation must change the actual score"
    assert b in pending
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_failed_fact_rescore_is_retried(corpus, monkeypatch, tmp_path):
    conn, config, ids, _ = corpus
    jid = ids["seed-0"]
    with transaction(conn):
        conn.execute("UPDATE job SET title='Senior Integration Specialist' WHERE id=?", (jid,))
    module = importlib.import_module("career_agent.pipeline.rescore")

    def fail(*args, **kwargs):
        raise ValueError("audit injected scoring failure")

    monkeypatch.setattr(module, "match_job", fail)
    stats = rescore(conn, config)
    assert stats.errors == 1
    assert jid in plan(conn, config).targets
    monkeypatch.undo()
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_limited_pass_preserves_unprocessed_marks(corpus, monkeypatch, tmp_path):
    conn, config, ids, _ = corpus
    module = importlib.import_module("career_agent.pipeline.rescore")
    monkeypatch.setattr(module, "_PAGE", 1)
    with transaction(conn):
        conn.execute("UPDATE job SET location_raw='Remote in Brazil'")
    result = rescore(conn, config, limit=1)
    assert result.jobs_scored == 1
    assert len(plan(conn, config).targets) == len(ids) - 1
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_old_pass_cannot_clear_a_reused_generation(corpus, tmp_path):
    conn, config, ids, _ = corpus
    jid = ids["seed-0"]
    with transaction(conn):
        conn.execute("UPDATE job SET location_raw='First change' WHERE id=?", (jid,))
    old = plan(conn, config).dirty
    # Second worker finishes, then a collector changes the same job again.
    with transaction(conn):
        _clear_ledger(conn, old)
        conn.execute("UPDATE job SET location_raw='Second change' WHERE id=?", (jid,))
        _clear_ledger(conn, old)
    assert jid in plan(conn, config).targets
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_revision_cache_tracks_population_identity(corpus, tmp_path):
    from career_agent.storage import revisions

    conn, config, ids, _ = corpus
    a, b = ids["seed-0"], ids["seed-1"]
    with transaction(conn):
        conn.execute("DELETE FROM job_match WHERE job_id=?", (b,))
        conn.execute("UPDATE job SET closed_at='2026-09-13' WHERE id=?", (b,))
    revisions._LAST.clear()
    before = revisions.resolve(conn, config.config_id, config.config_version)
    with transaction(conn):
        conn.execute("UPDATE job SET closed_at='2026-09-13' WHERE id=?", (a,))
        conn.execute("UPDATE job SET closed_at=NULL WHERE id=?", (b,))
    cached = revisions.resolve(conn, config.config_id, config.config_version)
    revisions._LAST.clear()
    fresh = revisions.resolve(conn, config.config_id, config.config_version)
    assert before.current.scoreable == fresh.current.scoreable
    assert cached == fresh
    rescore(conn, config)
    assert _snapshot(conn, config) == _full_recompute_on_a_copy(conn, config, tmp_path)

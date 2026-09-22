"""Real-process interleavings and crash recovery on disposable SQLite databases."""
# ruff: noqa: F811

import importlib
import multiprocessing
import os
from pathlib import Path

import pytest
from tests.integration.test_incremental_rescore import (
    _assert_incremental_equals_full,
    _snapshot,
    corpus,  # noqa: F401
)
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.rescore import plan, rescore
from career_agent.storage.db import connect, transaction
from career_agent.storage.records import SourceBoardRecord
from career_agent.storage.repositories import SourceBoardRepo


def _worker(path, stage, ready, release):
    """Pause or terminate at actual scoring/write/ack boundaries in another process."""
    module = importlib.import_module("career_agent.pipeline.rescore")
    conn = connect(Path(path))
    config, _ = load_search_config(committed_config_dir())
    original = module.match_job

    def score(*args, **kwargs):
        result = original(*args, **kwargs)
        if stage == "stale":
            ready.set()
            assert release.wait(30), "parent did not release the stale worker"
        return result

    module.match_job = score
    if stage == "uncommitted":
        original_store = module.MatchRepo.store

        def store(*args, **kwargs):
            original_store(*args, **kwargs)
            os._exit(31)

        module.MatchRepo.store = store
    if stage == "acknowledging":
        original_clear = module._clear_ledger

        def clear(*args, **kwargs):
            original_clear(*args, **kwargs)
            os._exit(34)

        module._clear_ledger = clear

    def progress(stats):
        if stage == "committed" and stats.jobs_scored:
            os._exit(32)
        if stage == "requested" and stats.jobs_targeted:
            os._exit(33)

    rescore(
        conn, config, progress=progress, provider="greenhouse" if stage == "requested" else None
    )
    conn.close()


def _start(conn, stage):
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    path = conn.execute("PRAGMA database_list").fetchone()[2]
    child = context.Process(target=_worker, args=(path, stage, ready, release))
    child.start()
    return child, ready, release


def test_late_process_cannot_overwrite_newer_score(corpus, tmp_path):
    conn, config, ids, _ = corpus
    jid = ids["seed-0"]
    conn.execute("UPDATE job SET title='Old worker input' WHERE id=?", (jid,))
    child, ready, release = _start(conn, "stale")
    try:
        assert ready.wait(30)
        conn.execute("UPDATE job SET title='Senior Integration Specialist' WHERE id=?", (jid,))
        rescore(conn, config)
        expected = _snapshot(conn, config)
        release.set()
        child.join(30)
        assert child.exitcode == 0
        assert _snapshot(conn, config) == expected
        assert plan(conn, config).targets == []
        _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=0)
    finally:
        release.set()
        if child.is_alive():
            child.terminate()
        child.join(5)


@pytest.mark.parametrize(
    "stage,exitcode",
    [("uncommitted", 31), ("committed", 32), ("requested", 33), ("acknowledging", 34)],
)
def test_process_crash_leaves_retryable_work(corpus, tmp_path, stage, exitcode):
    conn, config, ids, _ = corpus
    jid = ids["seed-0"]
    before = _snapshot(conn, config)
    conn.execute("UPDATE job SET title='Senior Integration Specialist' WHERE id=?", (jid,))
    child, _, _ = _start(conn, stage)
    try:
        child.join(30)
        assert child.exitcode == exitcode
        assert jid in plan(conn, config).targets
        if stage == "uncommitted":
            assert _snapshot(conn, config) == before
        _assert_incremental_equals_full(conn, config, tmp_path)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        if child.is_alive():
            child.terminate()
        child.join(5)


@pytest.mark.parametrize("limit", [0, 1, 2, 3, 4, 5])
def test_limit_is_exact_attempt_budget_without_page_rounding(corpus, tmp_path, limit):
    conn, config, ids, _ = corpus
    conn.execute("UPDATE job SET title='Senior Integration Specialist'")
    stats = rescore(conn, config, limit=limit)
    assert stats.jobs_scored == min(limit, len(ids))
    assert len(plan(conn, config).targets) == max(0, len(ids) - limit)
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_negative_limit_rejected_before_work(corpus):
    conn, config, _, _ = corpus
    with pytest.raises(ValueError, match="non-negative"):
        rescore(conn, config, limit=-1)


@pytest.mark.parametrize("selector", [{"provider": "greenhouse"}, {"board": "greenhouse:acme"}])
def test_provider_and_board_leave_unselected_pending(corpus, tmp_path, selector):
    conn, config, ids, _ = corpus
    outsider = ids["seed-3"]
    with transaction(conn):
        company = conn.execute("SELECT company_id FROM job WHERE id=?", (outsider,)).fetchone()[0]
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company,
                provider="manual",
                board_identifier="outside",
                board_url="https://example.invalid/outside",
            )
        )
        conn.execute(
            "UPDATE job SET provider='manual', source_board_id=? WHERE id=?", (board, outsider)
        )
        conn.execute("UPDATE job SET title='Senior Integration Specialist'")
    result = rescore(conn, config, **selector)
    assert result.jobs_scored == 3
    assert plan(conn, config).targets == [outsider]
    _assert_incremental_equals_full(conn, config, tmp_path)


def test_one_config_receipt_does_not_acknowledge_another(corpus, tmp_path):
    conn, config, ids, _ = corpus
    other = config.model_copy(update={"config_id": "another-candidate"})
    rescore(conn, other)
    jid = ids["seed-0"]
    conn.execute("UPDATE job SET title='Senior Integration Specialist' WHERE id=?", (jid,))
    rescore(conn, config)
    assert conn.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0] == 0
    assert plan(conn, other).targets == [jid]
    _assert_incremental_equals_full(conn, other, tmp_path, expect_scored=1)


def test_failed_score_then_retry_equals_full(corpus, tmp_path, monkeypatch):
    conn, config, ids, _ = corpus
    module = importlib.import_module("career_agent.pipeline.rescore")
    jid = ids["seed-0"]
    conn.execute("UPDATE job SET title='Senior Integration Specialist' WHERE id=?", (jid,))
    with monkeypatch.context() as patch:

        def fail(*args, **kwargs):
            raise ValueError("injected")

        patch.setattr(module, "match_job", fail)
        assert rescore(conn, config).errors == 1
    assert plan(conn, config).targets == [jid]
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)


def test_serving_cache_reuses_same_population_across_connections(corpus, monkeypatch):
    from career_agent.storage import revisions

    conn, config, _, _ = corpus
    expected = revisions.resolve(conn, config.config_id, config.config_version)
    other = connect(Path(conn.execute("PRAGMA database_list").fetchone()[2]))
    try:

        def unexpected_scan(*args, **kwargs):
            raise AssertionError("unchanged population scanned again for another connection")

        monkeypatch.setattr(revisions, "_revisions", unexpected_scan)
        assert revisions.resolve(other, config.config_id, config.config_version) == expected
    finally:
        other.close()

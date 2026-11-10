"""The write counter the read caches key on: every request but a GET moves it,
except one that SAID it wrote nothing -- and only the automatic update's
refusals, which only read, say so.

Opening the app always asks `POST /api/auto-update/run`. When the update
declined (off, recent, nothing due), that request used to count as a write,
so the job lists' totals, their "N more" counts and Home were all read again
from scratch on every opening. These tests hold both halves: a refusal keeps
what is remembered, and anything that does write still drops it.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from tests.integration.test_auto_update import app, call, recorded  # noqa: F401

from career_agent.storage.db import connect, transaction
from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate
from career_agent.web import auto_update
from career_agent.web.server import ApiError, LocalApp, ServerConfig, changed_nothing

JOBS = {"group_duplicates": ["1"], "limit": ["8"], "narrowings": ["later"]}


def freshness(app) -> str:  # noqa: F811
    conn = connect(app.config.db_path)
    try:
        return app._jobs_freshness(conn)
    finally:
        conn.close()


def _set(app, key: str, value: str) -> None:  # noqa: F811
    conn = connect(app.config.db_path)
    with transaction(conn):
        CandidateStateRepo(conn).set(ensure_candidate(conn), key, value)
    conn.close()


def _recent(app) -> None:  # noqa: F811
    """Switched on, and a finished run started an hour ago."""
    at = (datetime.now(UTC) - timedelta(hours=1)).isoformat(timespec="seconds")
    at = at.replace("+00:00", "Z")
    _set(app, auto_update.KEY_ENABLED, "1")
    _set(app, auto_update.KEY_LAST, at)
    _set(app, auto_update.KEY_RESULT, f'{{"at": "{at}", "stopped": "DONE"}}')


def test_a_refused_update_moves_neither_the_counter_nor_the_freshness(app):  # noqa: F811
    before = (app.writes, freshness(app))
    assert call(app, "POST", "/api/auto-update/run") == {"started": False, "reason": "DISABLED"}
    _recent(app)
    after_switch = (app.writes, freshness(app))
    # Writing the switch is a write (outside a request here); the refusals are not.
    assert call(app, "POST", "/api/auto-update/run") == {"started": False, "reason": "RECENT"}
    assert (app.writes, freshness(app)) == after_switch
    assert before[0] == after_switch[0], "the DISABLED refusal counted as a write"


def test_nothing_due_is_a_refusal_too(app, monkeypatch):  # noqa: F811
    _set(app, auto_update.KEY_ENABLED, "1")
    monkeypatch.setattr(auto_update, "overdue", lambda conn: [])
    writes = app.writes
    assert call(app, "POST", "/api/auto-update/run")["reason"] == "NOTHING_DUE"
    assert app.writes == writes


def test_a_real_write_still_moves_the_counter_and_the_freshness(app):  # noqa: F811
    writes, token = app.writes, freshness(app)
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    assert app.writes == writes + 1
    assert freshness(app) != token


def test_a_run_that_starts_counts_as_a_write(app, recorded):  # noqa: F811
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    writes = app.writes
    answer = call(app, "POST", "/api/auto-update/run")
    assert answer["started"] is True
    assert app.writes == writes + 1
    app.retrieval.join(30)  # the recorder ends it; nothing is left running


def test_a_refused_request_that_failed_still_counts(app):  # noqa: F811
    writes = app.writes
    with pytest.raises(ApiError):
        call(app, "POST", "/api/auto-update/run", {"unexpected": 1})
    assert app.writes == writes + 1


def test_the_lists_keep_their_totals_across_a_refusal_and_drop_them_after_a_write(
    app,  # noqa: F811
    monkeypatch,
):
    counted: list[int] = []
    real = type(app)._counts

    def spy(repo, *args):
        counted.append(1)
        return real(repo, *args)

    monkeypatch.setattr(type(app), "_counts", staticmethod(spy))
    _recent(app)
    app.handle_api("GET", "/api/jobs", JOBS, {})
    app.handle_api("GET", "/api/jobs", JOBS, {})
    assert len(counted) == 1, "the second list should reuse the remembered totals"
    call(app, "POST", "/api/auto-update/run")  # RECENT: nothing written
    app.handle_api("GET", "/api/jobs", JOBS, {})
    assert len(counted) == 1, "a refused update made the list count its totals again"
    call(app, "PATCH", "/api/auto-update", {"enabled": False})  # a real write
    app.handle_api("GET", "/api/jobs", JOBS, {})
    assert len(counted) == 2, "a real write must drop the remembered totals"


def test_home_keeps_its_answer_across_a_refusal_and_reads_again_after_a_write(app):  # noqa: F811
    _recent(app)
    first = call(app, "GET", "/api/home")
    assert call(app, "GET", "/api/home") is first
    call(app, "POST", "/api/auto-update/run")
    assert call(app, "GET", "/api/home") is first, "a refused update dropped Home's answer"
    call(app, "PATCH", "/api/auto-update", {"enabled": True})
    assert call(app, "GET", "/api/home") is not first


# -- the mechanism itself, on a bare route table ------------------------------


@pytest.fixture
def bare(tmp_path):
    return LocalApp(ServerConfig(db_path=tmp_path / "x.db", config_dir=tmp_path), quiet=True)


def test_only_the_request_that_said_so_is_left_out(bare):
    bare.register("POST", r"/api/quiet", lambda **_: (changed_nothing(), {})[1])
    bare.register("POST", r"/api/loud", lambda **_: {})
    bare.register("GET", r"/api/read", lambda **_: (changed_nothing(), {})[1])
    bare.handle_api("POST", "/api/quiet", {}, {})
    assert bare.writes == 0
    bare.handle_api("POST", "/api/loud", {}, {})
    assert bare.writes == 1
    bare.handle_api("GET", "/api/read", {}, {})
    assert bare.writes == 1


def test_a_nested_call_never_borrows_or_clears_the_outer_flag(bare):
    bare.register("GET", r"/api/inner-read", lambda **_: {})
    bare.register("POST", r"/api/inner-write", lambda **_: {})

    def outer_quiet(**_):
        bare.handle_api("GET", "/api/inner-read", {}, {})
        changed_nothing()
        return {}

    def outer_writes_inner_quiet(**_):
        # The inner route declares itself; the outer one wrote and did not.
        bare.handle_api("POST", "/api/inner-quiet", {}, {})
        return {}

    bare.register("POST", r"/api/inner-quiet", lambda **_: (changed_nothing(), {})[1])
    bare.register("POST", r"/api/outer-quiet", outer_quiet)
    bare.register("POST", r"/api/outer-loud", outer_writes_inner_quiet)
    bare.handle_api("POST", "/api/outer-quiet", {}, {})
    assert bare.writes == 0
    bare.handle_api("POST", "/api/outer-loud", {}, {})
    assert bare.writes == 1


def test_the_flag_belongs_to_one_thread(bare):
    gate = threading.Event()
    release = threading.Event()

    def slow_quiet(**_):
        gate.set()
        release.wait(5)
        changed_nothing()
        return {}

    bare.register("POST", r"/api/slow-quiet", slow_quiet)
    bare.register("POST", r"/api/loud", lambda **_: {})
    worker = threading.Thread(target=bare.handle_api, args=("POST", "/api/slow-quiet", {}, {}))
    worker.start()
    gate.wait(5)
    bare.handle_api("POST", "/api/loud", {}, {})  # meanwhile, on this thread
    release.set()
    worker.join(5)
    assert bare.writes == 1


def test_changed_nothing_outside_a_request_does_nothing():
    changed_nothing()  # no request on this thread: no error, nothing to mark

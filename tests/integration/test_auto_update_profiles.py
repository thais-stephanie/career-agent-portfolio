# ruff: noqa: F811
"""The automatic update and a profile switch: nothing crosses, nothing blocks.

An update runs for ONE profile. Switching away is refused while it (or the local
scoring it starts) is working, the person can keep using the app meanwhile, and
whatever it published (its summary, its scores, its messages) is in that profile
and in no other. The engine is replaced by a controllable stand-in: no site, no
feed and no real data is touched.
"""

from __future__ import annotations

import threading
import time

import pytest
from tests.integration.test_local_profiles import app, install

from career_agent.runtime.profiles import load_registry
from career_agent.storage.db import connect
from career_agent.web.server import ApiError

__all__ = ["install"]


def call(api, method, path, body=None, query=None):
    return api.handle_api(method, path, query or {}, body or {})


def finish(api):
    api.retrieval.join(30)
    api.rescore.join(30)


@pytest.fixture
def held_engine(monkeypatch):
    """An engine that works until it is released, then reports one updated site."""
    release = threading.Event()
    entered = threading.Event()

    def engine(conn, **kwargs):
        entered.set()
        release.wait(30)
        if kwargs.get("provider") == "workable":
            return {"selected": [], "results": [], "status": "FINISHED"}
        return {
            "selected": ["greenhouse:acme"],
            "results": [{"key": "greenhouse:acme", "outcome": "OK", "attempted": True}],
            "status": "FINISHED",
        }

    monkeypatch.setattr("career_agent.pipeline.maintenance.execute", engine)
    return release, entered


def job_scores(api) -> int:
    conn = connect(api.config.db_path)
    try:
        return conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0]
    finally:
        conn.close()


def test_the_app_stays_usable_and_a_switch_is_refused_while_an_update_runs(install, held_engine):
    release, entered = held_engine
    host = install
    first = app(host)
    other = call(first, "POST", "/api/profiles", body={"label": "Other"})["created"]["id"]
    before = load_registry(host.root).active
    call(first, "PATCH", "/api/auto-update", {"enabled": True})
    assert call(first, "POST", "/api/auto-update/run")["started"] is True
    assert entered.wait(10)

    # Navigation is not held by the run: these answer while it is still working.
    for method, path in (
        ("GET", "/api/jobs"),
        ("GET", "/api/auto-update"),
        ("GET", "/api/retrieval"),
        ("GET", "/api/rescore"),
    ):
        started = time.monotonic()
        call(first, method, path, query={"limit": ["5"]} if path == "/api/jobs" else None)
        assert time.monotonic() - started < 5, path
    assert call(first, "GET", "/api/auto-update")["running"] is True

    with pytest.raises(ApiError) as refused:
        call(first, "POST", "/api/profiles/switch", {"profile_id": other})
    assert refused.value.status == 409
    assert load_registry(host.root).active == before, "the profile did not change"

    release.set()
    finish(first)
    status = call(first, "GET", "/api/auto-update")
    assert status["running"] is False and status["last_result"]["updated"] == 1


def test_what_an_update_publishes_stays_in_the_profile_that_ran_it(install, held_engine):
    release, entered = held_engine
    host = install
    first = app(host)
    other = call(first, "POST", "/api/profiles", body={"label": "Other"})["created"]["id"]

    # Leave some of the first profile's postings without a current score, so the
    # update's local scoring has something to do.
    conn = connect(first.config.db_path)
    try:
        conn.execute("DELETE FROM job_match")
        conn.commit()
    finally:
        conn.close()
    assert job_scores(first) == 0

    call(first, "PATCH", "/api/auto-update", {"enabled": True})
    assert call(first, "POST", "/api/auto-update/run")["started"] is True
    assert entered.wait(10)
    release.set()
    first.retrieval.join(30)

    # The local scoring it started is a run of its own: switching waits for it.
    if first.rescore.running:
        with pytest.raises(ApiError) as refused:
            call(first, "POST", "/api/profiles/switch", {"profile_id": other})
        assert refused.value.status == 409
    first.rescore.join(60)
    scored = job_scores(first)
    assert scored > 0, "the update scored what was waiting, for this profile"
    result = call(first, "GET", "/api/auto-update")["last_result"]
    assert result["scoring"] in {"STARTED", "NONE"}

    call(first, "POST", "/api/profiles/switch", {"profile_id": other})
    second = app(host)
    assert second is not first
    status = call(second, "GET", "/api/auto-update")
    assert status["enabled"] is False, "the choice is the first profile's"
    assert status["last_result"] is None and status["last_run_at"] is None
    assert status["running"] is False
    assert job_scores(second) == 0, "no score was written into the profile switched to"
    snapshot = call(second, "GET", "/api/retrieval")
    assert not snapshot.get("running") and not snapshot.get("run"), "no run message crossed over"
    # And the first profile's record is intact where it was written.
    conn = connect(first.config.db_path)
    try:
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM candidate_state WHERE key = 'auto_update.last_result'"
            ).fetchone()[0]
            == 1
        )
    finally:
        conn.close()

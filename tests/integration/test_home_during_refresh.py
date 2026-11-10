"""Home while jobs are being collected.

Home is two reads. Its LISTS (the total, the open postings, the digest's
sections) go over the whole catalogue; while a collection runs they are kept
across the postings it writes, source after source, and counted once when it
ends. Everything else -- the cards, the gaps, the questions, the latest
refresh's own count -- is this person's own rows and is read on every request.
What must hold:

* sources finishing one after another count nothing again; the run's end
  counts once; a run that wrote nothing counts nothing;
* the answer says when its lists were counted and whether a collection runs,
  including one that started while they were being counted;
* anything the person does during the run (a hide, a save, an application, a
  setting) is shown at once, lists included;
* a failed or stopped run ends the same way; another profile never reuses
  this one's lists; concurrent requests count once.

Synthetic data only: the collection is a runner holding a thread open while
the test writes postings the way a source would.
"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from tests.integration.test_deep_api_provider import WORK, api, call  # noqa: F401
from tests.integration.test_score_replay import _put

from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, transaction
from career_agent.web import workspace_api


@pytest.fixture
def counted(api, monkeypatch):  # noqa: F811
    """How many times Home counted its lists over the catalogue."""
    seen: list[int] = []
    real = type(api)._home_lists_now

    def spy(self, *args, **kwargs):
        seen.append(1)
        return real(self, *args, **kwargs)

    monkeypatch.setattr(type(api), "_home_lists_now", spy)
    return seen


@contextmanager
def collection(app, *, ends: str = "done"):
    """A collection running on this profile's runner until the block ends.

    `ends`: "done", "failed" (the work raises) or "cancelled" (Stop)."""
    release = threading.Event()

    def work(state, cancel):
        release.wait(30)
        if ends == "failed":
            raise RuntimeError("synthetic source failure")

    app.retrieval.start(work, "synthetic-run")
    try:
        yield
    finally:
        if ends == "cancelled":
            app.retrieval.cancel()
        release.set()
        app.retrieval.join(10)
    assert not app.retrieval.running


_N = iter(range(1000, 10_000))


def a_source_finishes(app, postings: int = 1) -> None:
    """What a finished source leaves behind: new postings, scored."""
    conn = connect(app.config.db_path)
    with transaction(conn):
        company, board = conn.execute("SELECT company_id, id FROM source_board").fetchone()
        for _ in range(postings):
            n = next(_N)
            _put(
                conn,
                company,
                board,
                f"p-{n}",
                f"Business Systems Engineer {n}",
                f"{WORK} {n}",
                "Remote",
            )
        conn.execute("UPDATE job SET posted_at = ?", (datetime.now(UTC).date().isoformat(),))
    rescore(conn, app.search_config())
    conn.close()


def home(app) -> dict:
    return call(app, "GET", "/api/home")


def first_job(app) -> str:
    items = [i for s in home(app)["sections"] for i in s["items"]]
    return items[0]["job_id"]


# 1, 3, 11: warm Home, several sources in a row, one count at the end ---------


def test_sources_finishing_in_a_row_count_nothing_and_the_end_counts_once(api, counted):  # noqa: F811
    before = home(api)
    assert len(counted) == 1 and before["collecting"] is False and before["lists_at"]
    with collection(api):
        for _ in range(3):
            a_source_finishes(api)
            during = home(api)
            assert during["collecting"] is True
            assert during["lists_at"] == before["lists_at"]
            assert during["total"] == before["total"] and during["sections"] == before["sections"]
        assert len(counted) == 1, "a finished source made Home count its lists again"
    after = home(api)
    assert len(counted) == 2 and after["collecting"] is False
    assert after["job_count"] == before["job_count"] + 3
    assert after["total"] >= before["total"]
    home(api)
    assert len(counted) == 2, "the end of the run is counted once"


# 2: no snapshot yet ----------------------------------------------------------


def test_a_first_read_during_a_run_counts_once_and_is_kept(api, counted):  # noqa: F811
    with collection(api):
        first = home(api)
        assert len(counted) == 1 and first["collecting"] is True and first["lists_at"]
        a_source_finishes(api, postings=2)
        assert home(api)["lists_at"] == first["lists_at"]
        assert len(counted) == 1


# 4: one slow source, for minutes ---------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def monotonic(self) -> float:
        return self.now


def test_a_source_that_runs_for_minutes_keeps_the_lists_within_the_memo_bound(
    api,  # noqa: F811
    counted,
    monkeypatch,
):
    clock = Clock()
    monkeypatch.setattr(workspace_api, "time", clock)
    home(api)
    with collection(api):
        for _ in range(4):  # four minutes, a posting written each one
            clock.now += 60
            a_source_finishes(api)
            assert home(api)["collecting"] is True
        assert len(counted) == 1
        # The memo's own bound still applies: past it the lists are counted,
        # once, and said with their new time.
        clock.now += workspace_api.HOME_MEMO_SECONDS
        late = home(api)
        assert len(counted) == 2 and late["collecting"] is True
        home(api)
        assert len(counted) == 2


# 5: what the person does during the run is shown at once ---------------------


def test_a_save_an_application_and_a_hide_during_the_run_are_shown_at_once(api, counted):  # noqa: F811
    job = first_job(api)
    before = home(api)

    def card(answer: dict, key: str) -> int:
        return next(m["value"] for m in answer["metrics"] if m["key"] == key)

    with collection(api):
        a_source_finishes(api)
        assert len(counted) == 1
        call(api, "PATCH", f"/api/jobs/{job}/saved", {"saved": True})
        after_save = home(api)
        assert card(after_save, "saved") == card(before, "saved") + 1
        assert len(counted) == 2, "a save during the run did not count the lists again"
        # Counted again with the catalogue as it is now: the run's posting is in.
        assert after_save["job_count"] == before["job_count"] + 1
        assert after_save["lists_at"] >= before["lists_at"]
        call(api, "PATCH", f"/api/jobs/{job}/status", {"status": "APPLIED"})
        assert card(home(api), "applied") == card(before, "applied") + 1
        assert len(counted) == 3
        api.handle_api("PATCH", f"/api/jobs/{job}/hidden", {}, {"hidden": True})
        home(api)
        assert len(counted) == 4, "a hide during the run did not count the lists again"
        assert home(api)["collecting"] is True
        assert len(counted) == 4


# 7, 8: the run's end, with and without new jobs ------------------------------


def test_the_latest_refresh_count_is_read_from_its_own_record_even_while_lists_are_kept(
    api,  # noqa: F811
    counted,
):
    home(api)
    with collection(api):
        a_source_finishes(api, postings=3)
        conn = connect(api.config.db_path)
        with transaction(conn):
            conn.execute(
                "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
                " VALUES ('r-synthetic', 'find-jobs', ?, ?, 'OK', ?)",
                ("2026-11-10T09:00:00Z", "2026-11-10T09:05:00Z", json.dumps({"jobs_new": 3})),
            )
        conn.close()
        during = home(api)
        assert next(m["value"] for m in during["metrics"] if m["key"] == "new") == 3
        assert during["latest_refresh_run"]
        assert len(counted) == 1


def test_a_run_that_wrote_nothing_counts_nothing_when_it_ends(api, counted):  # noqa: F811
    before = home(api)
    with collection(api):
        home(api)
    after = home(api)
    assert len(counted) == 1 and after["collecting"] is False
    assert after["lists_at"] == before["lists_at"] and after["total"] == before["total"]


# 9: failed and stopped runs end the same way ---------------------------------


@pytest.mark.parametrize("ends", ["failed", "cancelled"])
def test_a_failed_or_stopped_run_counts_what_it_wrote_once(api, counted, ends):  # noqa: F811
    before = home(api)
    with collection(api, ends=ends):
        a_source_finishes(api)
        assert home(api)["lists_at"] == before["lists_at"]
    assert api.retrieval.snapshot()["status"] == ends
    after = home(api)
    assert len(counted) == 2 and after["collecting"] is False
    assert after["job_count"] == before["job_count"] + 1
    home(api)
    assert len(counted) == 2


# 6: another profile ----------------------------------------------------------


def test_another_profile_never_reuses_this_profiles_lists(tmp_path) -> None:
    from tests.support import career_profile

    _p1, first = career_profile(tmp_path, "prof-one", "One")
    _p2, second = career_profile(tmp_path, "prof-two", "Two")
    mine = home(first)
    with collection(first):
        conn = connect(second.config.db_path)
        with transaction(conn):
            conn.execute("DELETE FROM job_match")
        conn.close()
        theirs = home(second)
        assert theirs["collecting"] is False, "the other profile's run is not this one's"
        assert theirs["total"] == 0 and mine["total"] > 0
        assert home(first)["total"] == mine["total"]


# 10: concurrent requests, and a run that starts while lists are counted ------


def test_concurrent_reads_count_once_and_agree(api, counted):  # noqa: F811
    answers: list[dict] = []
    threads = [threading.Thread(target=lambda: answers.append(home(api))) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert len(answers) == 4 and len(counted) == 1
    assert all(a["lists_at"] == answers[0]["lists_at"] for a in answers)


def test_a_run_that_starts_while_the_lists_are_counted_is_said(api, monkeypatch):  # noqa: F811
    real = type(api)._home_lists_now
    release = threading.Event()

    def starts_a_run(self, *args, **kwargs):
        lists = real(self, *args, **kwargs)
        if not self.retrieval.running:
            self.retrieval.start(lambda state, cancel: release.wait(30), "late-run")
        return lists

    monkeypatch.setattr(type(api), "_home_lists_now", starts_a_run)
    try:
        assert home(api)["collecting"] is True, (
            "lists counted before a run began were shown as current"
        )
    finally:
        release.set()
        api.retrieval.join(10)


# the outdated count follows the same rule ------------------------------------


def test_the_outdated_count_is_kept_during_the_run_and_dropped_by_the_person(
    api,  # noqa: F811
    monkeypatch,
):
    from career_agent.pipeline import rescore as rescore_module

    plans: list[int] = []
    real = rescore_module.plan

    asking = threading.local()

    def counting(*args, **kwargs):
        # Only the outdated route's own plan: a source's scoring plans too.
        if getattr(asking, "now", False):
            plans.append(1)
        return real(*args, **kwargs)

    def outdated() -> None:
        asking.now = True
        try:
            call(api, "GET", "/api/home/outdated")
        finally:
            asking.now = False

    monkeypatch.setattr(rescore_module, "plan", counting)
    outdated()
    with collection(api):
        a_source_finishes(api)
        outdated()
        assert len(plans) == 1
        call(api, "PATCH", f"/api/jobs/{first_job(api)}/saved", {"saved": True})
        outdated()
        assert len(plans) == 2
        a_source_finishes(api)
        outdated()
        assert len(plans) == 2
    outdated()
    assert len(plans) == 3, "the run's end reads the plan again, once"
    outdated()
    assert len(plans) == 3

"""One press finds jobs: every refreshable, unpaused source, in turn, no network here.

`POST /api/sources/refresh-all` adds no collection policy. These tests replace
the two kinds of per-source work -- the shared board collector and the
`collect-*` commands -- with recorders, so they prove WHICH sources run and
what the run reports, without a single request leaving the machine.
"""

from __future__ import annotations

import threading

import pytest
from tests.support import committed_config_dir

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.sources.matrix import _stage_for
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


def _api(tmp_path, mode: RuntimeMode) -> JobsApi:
    db = tmp_path / f"{mode.value.lower()}.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, mode, "find jobs")
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir()), quiet=True)


@pytest.fixture
def personal(tmp_path):
    return _api(tmp_path, RuntimeMode.PERSONAL)


class Recorder:
    """Stands in for both kinds of source work and remembers what ran."""

    def __init__(self, fail: set[str] | None = None, block: threading.Event | None = None):
        self.ran: list[str] = []
        self.fail = fail or set()
        self.block = block
        self.started = threading.Event()

    def install(self, monkeypatch, api: JobsApi) -> None:
        def collect_work(board_limit, *, provider=None):
            return self._work(f"collect:{provider}")

        def feed(db_path, stage):
            return self._work(stage)

        monkeypatch.setattr(api, "_collect_work", collect_work)
        monkeypatch.setattr("career_agent.web.source_refresh.feed_work", feed)
        rescores: list[str] = []
        monkeypatch.setattr(api.rescore, "start", lambda work, run_id: rescores.append(run_id))
        self.rescores = rescores

    def _work(self, key: str):
        def work(state, cancel) -> None:
            self.ran.append(key)
            self.started.set()
            if self.block is not None:
                self.block.wait(5)
            if key in self.fail:
                raise RuntimeError(r"C:\private\path\secret.db exploded")

        return work


def _expected_keys(api: JobsApi) -> list[str]:
    """What the sources panel says could be refreshed and is not paused."""
    from career_agent.sources.health import health

    data = api.handle_api("GET", "/api/sources", {}, {})
    paused = {row["source_id"] for row in data["refresh"] if row["state"] == "PAUSED"}
    refreshable = {row["id"] for row in data["sources"] if row["can_refresh"]}
    with connect(api.config.db_path) as conn:
        entries = health(conn, catalogue_path=api.config.config_dir / "source_catalogue.yaml")
    keys: list[str] = []
    for entry in entries:
        if entry.source.id not in refreshable or entry.source.id in paused:
            continue
        provider = entry.source.provider
        stage = _stage_for(provider)
        key = f"collect:{provider}" if stage == "collect" else stage
        if key not in keys:
            keys.append(key)
    return keys


def _run(api: JobsApi) -> dict:
    api.handle_api("POST", "/api/sources/refresh-all", {}, {})
    api.retrieval.join(10)
    snapshot = api.retrieval.snapshot()
    assert snapshot is not None
    return snapshot


def test_the_demo_never_collects(tmp_path):
    api = _api(tmp_path, RuntimeMode.DEMO)
    with pytest.raises(ApiError, match="demo never collects") as caught:
        api.handle_api("POST", "/api/sources/refresh-all", {}, {})
    assert caught.value.status == 409
    assert caught.value.for_reader
    assert not api.retrieval.running


def test_it_takes_no_parameters(personal):
    with pytest.raises(ApiError) as caught:
        personal.handle_api("POST", "/api/sources/refresh-all", {}, {"source_id": "gupy"})
    assert caught.value.status == 400


def test_every_refreshable_unpaused_source_runs_once_in_turn(personal, monkeypatch):
    recorder = Recorder()
    recorder.install(monkeypatch, personal)
    expected = _expected_keys(personal)
    assert expected, "the committed catalogue offers nothing to refresh; this test is blind"

    snapshot = _run(personal)

    assert recorder.ran == expected
    assert len(set(recorder.ran)) == len(recorder.ran), "a source ran twice"
    assert snapshot["status"] == "done"
    assert snapshot["boards_total"] == len(expected)
    assert snapshot["boards_done"] == len(expected)
    assert all(row["status"] == "ok" for row in snapshot["sources"])
    # New postings are scored by the normal targeted pass, once.
    assert len(recorder.rescores) == 1


def test_a_source_paused_by_the_person_is_left_alone(personal, monkeypatch):
    recorder = Recorder()
    recorder.install(monkeypatch, personal)
    before = _expected_keys(personal)
    gupy = "collect:gupy" if _stage_for("gupy") == "collect" else _stage_for("gupy")
    assert gupy in before, "gupy is not refreshable here; pick another fixture source"

    personal.handle_api(
        "PATCH", "/api/sources/schedule", {}, {"source_id": "gupy", "mode": "PAUSED"}
    )
    _run(personal)

    assert gupy not in recorder.ran
    assert recorder.ran == _expected_keys(personal)


def test_one_failing_source_does_not_stop_the_rest_or_leak_its_error(personal, monkeypatch):
    expected = _expected_keys(personal)
    assert len(expected) >= 2
    recorder = Recorder(fail={expected[0]})
    recorder.install(monkeypatch, personal)

    snapshot = _run(personal)

    assert recorder.ran == expected, "the run stopped at the first failure"
    statuses = [row["status"] for row in snapshot["sources"]]
    assert statuses[0] == "failed"
    assert statuses[1:] == ["ok"] * (len(expected) - 1)
    rendered = repr(snapshot)
    assert "private" not in rendered and "secret" not in rendered


def test_cancelling_stops_after_the_source_in_flight(personal, monkeypatch):
    gate = threading.Event()
    recorder = Recorder(block=gate)
    recorder.install(monkeypatch, personal)
    expected = _expected_keys(personal)
    assert len(expected) >= 2

    personal.handle_api("POST", "/api/sources/refresh-all", {}, {})
    assert recorder.started.wait(5)
    with pytest.raises(ApiError, match="already looking") as caught:
        personal.handle_api("POST", "/api/sources/refresh-all", {}, {})
    assert caught.value.status == 409
    personal.retrieval.cancel()
    gate.set()
    personal.retrieval.join(10)

    assert recorder.ran == expected[:1]
    assert personal.retrieval.snapshot()["status"] == "cancelled"


def test_the_run_says_which_source_it_is_reading_and_how_many_it_skipped(personal, monkeypatch):
    """A long source is the normal case. The run names it and says since when,
    so the screen can tell a person it is still working rather than frozen."""
    gate = threading.Event()
    recorder = Recorder(block=gate)
    recorder.install(monkeypatch, personal)
    data = personal.handle_api("GET", "/api/sources", {}, {})
    paused = {row["source_id"] for row in data["refresh"] if row["state"] == "PAUSED"}
    refreshable = {row["id"] for row in data["sources"] if row["can_refresh"]}
    from career_agent.sources.health import health

    with connect(personal.config.db_path) as conn:
        entries = health(conn, catalogue_path=personal.config.config_dir / "source_catalogue.yaml")

    def key_of(provider: str) -> str:
        stage = _stage_for(provider)
        return f"collect:{provider}" if stage == "collect" else stage

    # One per collector, the unit "N of M" counts in, and none that also runs.
    skipped = {
        key_of(entry.source.provider)
        for entry in entries
        if entry.source.id in refreshable and entry.source.id in paused
    } - set(_expected_keys(personal))

    personal.handle_api("POST", "/api/sources/refresh-all", {}, {})
    assert recorder.started.wait(5)
    during = personal.retrieval.snapshot()
    assert during is not None
    assert during["current"], "the source being read is not named"
    assert during["current_started_at"]
    assert during["skipped"] == len(skipped)
    gate.set()
    personal.retrieval.join(10)

    after = personal.retrieval.snapshot()
    assert after["status"] == "done"
    assert after["current"] is None and after["current_started_at"] is None


def test_the_progress_poll_can_leave_the_funnel_out(personal):
    """The funnel is seven counts over the whole corpus. The poll that runs
    every two seconds only needs the run, the scoring and the server's clock."""
    full = personal.handle_api("GET", "/api/retrieval", {}, {})
    assert isinstance(full["funnel"], dict)
    light = personal.handle_api("GET", "/api/retrieval", {"funnel": ["false"]}, {})
    assert light["funnel"] is None
    for payload in (full, light):
        assert payload["now"].endswith("Z")
        assert set(payload["scoring"]) == {"running", "done", "total"}
    with pytest.raises(ApiError) as caught:
        personal.handle_api("GET", "/api/retrieval", {"funnel": ["sometimes"]}, {})
    assert caught.value.status == 400
    with pytest.raises(ApiError):
        personal.handle_api("GET", "/api/retrieval", {"fields": ["run"]}, {})

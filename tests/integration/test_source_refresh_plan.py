"""Find jobs, Refresh due sources and Refresh all: one plan, frozen at the press.

No network: every source's work is a recorder, and each source's freshness is
written straight into the run ledger the way its collector would write it. So
these tests prove WHICH sources a press runs, what its total counts, and what
Home says afterwards, without a request leaving the machine.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.clock import new_id
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.sources.matrix import _stage_for
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import LAST_REVIEWED_AT, CandidateStateRepo
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig


def _stamp(hours_ago: float) -> str:
    moment = datetime.now(UTC) - timedelta(hours=hours_ago)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


@pytest.fixture
def api(tmp_path) -> JobsApi:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "refresh plan")
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir()), quiet=True)


def _ledger(api: JobsApi, stage: str, *, hours_ago: float, **stats) -> None:
    """One finished run of `stage`, as its collector would have recorded it."""
    status = stats.pop("status", "OK")
    with connect(api.config.db_path) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                stage,
                _stamp(hours_ago + 0.1),
                _stamp(hours_ago),
                status,
                json.dumps(stats),
            ),
        )


def _rows(api: JobsApi) -> dict[str, dict]:
    return {
        row["source_id"]: row for row in api.handle_api("GET", "/api/sources", {}, {})["refresh"]
    }


def _providers(api: JobsApi) -> dict[str, str | None]:
    from career_agent.sources.health import health

    with connect(api.config.db_path) as conn:
        entries = health(conn, catalogue_path=api.config.config_dir / "source_catalogue.yaml")
    return {e.source.id: e.source.provider for e in entries}


def _key(provider: str) -> str:
    stage = _stage_for(provider)
    return f"collect:{provider}" if stage == "collect" else stage


class Recorder:
    """Every source's work: remembers what ran and records what it did."""

    def __init__(self, api: JobsApi, monkeypatch, *, block: threading.Event | None = None):
        self.api = api
        self.ran: list[str] = []
        self.block = block
        self.started = threading.Event()
        #: stage -> jobs_new its run records; a stage absent here records nothing
        self.new: dict[str, int] = {}
        monkeypatch.setattr(
            api, "_collect_work", lambda *, provider: self._work(f"collect:{provider}")
        )
        monkeypatch.setattr(
            "career_agent.web.source_refresh.feed_work", lambda db, stage, **_: self._work(stage)
        )
        monkeypatch.setattr(
            "career_agent.web.source_refresh.employer_board_work",
            lambda app, families: self._work("employer-boards"),
        )
        monkeypatch.setattr(api.rescore, "start", lambda work, run_id: None)

    def _work(self, key: str):
        def work(state, cancel) -> None:
            self.ran.append(key)
            self.started.set()
            if self.block is not None:
                self.block.wait(5)
            if key in self.new:
                stage = "collect" if key.startswith("collect:") else key
                stats = {"jobs_new": self.new[key]}
                if stage == "collect":
                    family = key.split(":", 1)[1]
                    stats["by_provider"] = {
                        family: {"boards_attempted": 1, "boards_succeeded": 1, "boards_failed": 0}
                    }
                _ledger(self.api, stage, hours_ago=0, **stats)

        return work


def _run(api: JobsApi, route: str) -> dict:
    answer = api.handle_api("POST", f"/api/sources/{route}", {}, {})
    if answer.get("started") is False:
        return answer
    api.retrieval.join(10)
    snapshot = api.retrieval.snapshot()
    assert snapshot is not None
    return snapshot


@pytest.fixture
def seven(api: JobsApi):
    """A fresh, B due, C stale, D paused, E cooling down, F blocked, G
    experimental and off. Every other source is made fresh, so the press can
    only differ from "B and C" by being wrong."""
    rows = _rows(api)
    sources = {s["id"]: s for s in api.handle_api("GET", "/api/sources", {}, {})["sources"]}
    providers = _providers(api)
    feeds = [
        sid
        for sid, row in rows.items()
        if row["state"] not in ("PAUSED", "BLOCKED")
        and sources[sid].get("can_refresh")
        and providers.get(sid)
        and _stage_for(providers[sid]) != "collect"
    ]
    assert len(feeds) >= 5, "the committed catalogue has too few feeds for this test"
    a, b, c, d, e = feeds[:5]
    # Everything else fresh: one recent OK run per stage, per board family.
    # (The newest run of a stage is its state, so A..E get only their own.)
    for sid in rows:
        provider = providers.get(sid)
        if not provider or sid in (a, b, c, d, e):
            continue
        stage = _stage_for(provider)
        if stage == "collect":
            family = {"boards_attempted": 1, "boards_succeeded": 1, "boards_failed": 0}
            _ledger(api, "collect", hours_ago=1, by_provider={provider: family})
        else:
            _ledger(api, stage, hours_ago=1)
    stage = {sid: _stage_for(providers[sid]) for sid in (a, b, c, d, e)}
    _ledger(api, stage[a], hours_ago=1)  # fresh
    _ledger(api, stage[b], hours_ago=30)  # due
    _ledger(api, stage[c], hours_ago=80)  # stale
    _ledger(api, stage[d], hours_ago=80)  # stale, and paused below
    api.handle_api("PATCH", "/api/sources/schedule", {}, {"source_id": d, "mode": "PAUSED"})
    _ledger(api, stage[e], hours_ago=2, status="OK", rate_limited=True)  # refused, cooling
    blocked = [sid for sid, row in rows.items() if row["state"] == "BLOCKED"]
    experimental = [sid for sid, s in sources.items() if s.get("experimental")]
    return {"A": a, "B": b, "C": c, "D": d, "E": e, "F": blocked, "G": experimental, "stage": stage}


def test_due_runs_exactly_the_due_sources_then_only_what_is_still_due(api, seven, monkeypatch):
    states = _rows(api)
    assert states[seven["A"]]["due"] is False
    assert states[seven["B"]]["due"] and states[seven["C"]]["due"]
    assert states[seven["D"]]["state"] == "PAUSED" and not states[seven["D"]]["due"]
    assert states[seven["E"]]["cooldown_until"] and not states[seven["E"]]["due"]

    recorder = Recorder(api, monkeypatch)
    recorder.new[seven["stage"][seven["B"]]] = 0  # B's run lands: it becomes fresh
    snapshot = _run(api, "refresh-due")

    assert sorted(recorder.ran) == sorted([seven["stage"][seven["B"]], seven["stage"][seven["C"]]])
    assert snapshot["kind"] == "due" and snapshot["boards_total"] == 2

    # C recorded nothing, so it is still due; B is fresh now. The next press
    # recalculates and runs C alone.
    recorder.ran.clear()
    _run(api, "refresh-due")
    assert recorder.ran == [seven["stage"][seven["C"]]]


def test_nothing_due_starts_nothing(api, seven, monkeypatch):
    recorder = Recorder(api, monkeypatch)
    _ledger(api, seven["stage"][seven["B"]], hours_ago=0)
    _ledger(api, seven["stage"][seven["C"]], hours_ago=0)

    answer = _run(api, "refresh-due")

    assert answer["started"] is False and recorder.ran == []
    assert seven["E"] in answer["cooling_down"]


def test_refresh_all_includes_fresh_sources_but_never_paused_cooling_or_off(
    api, seven, monkeypatch
):
    recorder = Recorder(api, monkeypatch)
    _run(api, "refresh-all")

    ran = set(recorder.ran)
    for name in ("A", "B", "C"):
        assert seven["stage"][seven[name]] in ran, name
    assert seven["stage"][seven["D"]] not in ran, "a paused source woke up"
    assert seven["stage"][seven["E"]] not in ran, "a cooldown was bypassed"
    sources = {s["id"]: s for s in api.handle_api("GET", "/api/sources", {}, {})["sources"]}
    for sid in seven["G"]:
        provider = sources[sid]["experimental"]["provider"]
        assert _key(provider) not in ran, "an experimental source ran without an opt-in"


def test_a_run_keeps_its_total_while_the_live_due_count_moves(api, seven, monkeypatch):
    gate = threading.Event()
    recorder = Recorder(api, monkeypatch, block=gate)
    api.handle_api("POST", "/api/sources/refresh-due", {}, {})
    assert recorder.started.wait(5)
    during = api.retrieval.snapshot()
    assert during["boards_total"] == 2 == len(during["plan"])

    # C turns fresh while the run is still on B. The live count moves; the
    # run's total does not.
    _ledger(api, seven["stage"][seven["C"]], hours_ago=0)
    summary = api.handle_api("GET", "/api/sources", {}, {})["summary"]
    # Live: B is being read (RUNNING, not due) and C is fresh, so nothing is
    # due right now; the run still says it is refreshing two.
    assert summary["due"] == 0
    assert api.retrieval.snapshot()["boards_total"] == 2
    gate.set()
    api.retrieval.join(10)
    assert api.retrieval.snapshot()["boards_total"] == 2


def test_source_board_rows_never_become_the_run_total(api, seven, monkeypatch):
    """A feed registers one `source_board` per employer for attribution. Two
    hundred of them are not two hundred sources."""
    from career_agent.storage.records import CompanyRecord, SourceBoardRecord
    from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

    with connect(api.config.db_path) as conn, transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        for i in range(200):
            SourceBoardRepo(conn).upsert(
                SourceBoardRecord(
                    company_id=company, provider="himalayas", board_identifier=f"e{i}"
                )
            )
    Recorder(api, monkeypatch)
    snapshot = _run(api, "refresh-due")
    assert snapshot["boards_total"] == 2


def test_the_summary_counts_come_from_the_catalogue(api):
    payload = api.handle_api("GET", "/api/sources", {}, {})
    summary = payload["summary"]
    sources = payload["sources"]
    assert summary["integrated"] == sum(1 for p in _providers(api).values() if p)
    experimental = [s for s in sources if s.get("experimental")]
    assert summary["experimental"] == len(experimental) and summary["experimental_enabled"] == 0
    assert summary["due"] == sum(1 for row in payload["refresh"] if row["due"])
    assert summary["available"] <= len(payload["refresh"])


# -- Home: "New from your latest refresh" ------------------------------------


def _new_card(api: JobsApi):
    home = api.handle_api("GET", "/api/home", {}, {})
    card = next(m for m in home["metrics"] if m["key"] == "new")
    assert card["kind"] == "refresh"
    return card["value"]


def _reviewed(api: JobsApi):
    from career_agent.storage.workspace_repo import candidate_id_of

    with connect(api.config.db_path) as conn:
        candidate = candidate_id_of(conn)
        return CandidateStateRepo(conn).get(candidate, LAST_REVIEWED_AT) if candidate else None


def test_home_new_has_no_number_before_any_refresh_and_opening_it_marks_nothing(api):
    assert _new_card(api) is None
    assert _reviewed(api) is None


def test_home_names_the_run_its_new_count_belongs_to(api, seven, monkeypatch):
    """The banner is dismissed for ONE run; two runs adding 3 are not one."""
    assert api.handle_api("GET", "/api/home", {}, {})["latest_refresh_at"] is None
    recorder = Recorder(api, monkeypatch)
    recorder.new = {seven["stage"][seven["B"]]: 3}
    _run(api, "refresh-due")
    first = api.handle_api("GET", "/api/home", {}, {})["latest_refresh_at"]
    assert first
    _run(api, "refresh-all")
    assert api.handle_api("GET", "/api/home", {}, {})["latest_refresh_at"] >= first


def test_home_new_is_what_the_latest_whole_run_added(api, seven, monkeypatch):
    recorder = Recorder(api, monkeypatch)
    b, c = seven["stage"][seven["B"]], seven["stage"][seven["C"]]
    recorder.new = {b: 8, c: 7}
    snapshot = _run(api, "refresh-due")
    assert snapshot["jobs_new"] == 15
    assert _new_card(api) == 15, "the whole plan, not the last source to finish"

    # The next refresh sees those again and adds 3: the card is 3, not 18.
    recorder.new = {stage: 0 for stage in recorder.new}
    recorder.new[next(iter(recorder.new))] = 3
    _run(api, "refresh-all")
    assert _new_card(api) == 3

    # A posting that changed, or was seen through a second source and filed
    # as a sighting, is no new job: its collector records no `jobs_new`.
    recorder.new = {stage: 0 for stage in recorder.new}
    _run(api, "refresh-all")
    assert _new_card(api) == 0

    # Refreshing never marks the list read.
    assert _reviewed(api) is None


def test_a_cancelled_run_is_not_the_latest_refresh(api, seven, monkeypatch):
    recorder = Recorder(api, monkeypatch)
    recorder.new = {seven["stage"][seven["B"]]: 4}
    _run(api, "refresh-due")
    assert _new_card(api) == 4

    gate = threading.Event()
    recorder.block = gate
    recorder.started.clear()
    api.handle_api("POST", "/api/sources/refresh-all", {}, {})
    assert recorder.started.wait(5)
    api.retrieval.cancel()
    gate.set()
    api.retrieval.join(10)
    assert _new_card(api) == 4


def test_refresh_all_waits_out_a_failure_too(api, seven, monkeypatch):
    """One rule for every plan, the same one the summary counts with."""
    _ledger(api, seven["stage"][seven["A"]], hours_ago=0.2, status="FAILED")
    assert _rows(api)[seven["A"]]["cooldown_until"]
    recorder = Recorder(api, monkeypatch)
    snapshot = _run(api, "refresh-all")
    assert seven["stage"][seven["A"]] not in recorder.ran
    summary = api.handle_api("GET", "/api/sources", {}, {})["summary"]
    assert snapshot["boards_total"] <= summary["available"] + 1  # +1: the board family row


def test_a_run_where_every_source_failed_is_not_the_latest_refresh(api, seven, monkeypatch):
    recorder = Recorder(api, monkeypatch)
    recorder.new = {seven["stage"][seven["B"]]: 5}
    _run(api, "refresh-due")
    assert _new_card(api) == 5

    def broken(*args, **kwargs):
        def work(state, cancel):
            raise RuntimeError("down")

        return work

    monkeypatch.setattr("career_agent.web.source_refresh.feed_work", broken)
    _run(api, "refresh-due")
    assert _new_card(api) == 5, "a run that read nothing replaced the last real one"


def test_a_source_no_button_can_refresh_is_never_due_or_available(api):
    """Jooble (a lifetime, regional quota) is never counted as due: a count no
    press can act on would promise a refresh that cannot start."""
    payload = api.handle_api("GET", "/api/sources", {}, {})
    refreshable = {s["id"] for s in payload["sources"] if s["can_refresh"]}
    for row in payload["refresh"]:
        if row["source_id"] not in refreshable:
            assert row["state"] == "BLOCKED" and not row["due"], row["source_id"]


def test_an_unavailable_upstream_keeps_its_jobs_and_leaves_every_plan(api, monkeypatch):
    """Gupy's feed stopped serving on 2026-10-02. The connector produced real
    jobs and keeps them; no press may start it, and it is never due."""
    import httpx

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.gupy_collect import GupyCollector
    from career_agent.sources import maintenance, matrix

    fixture = Path(__file__).parents[1] / "fixtures/providers/gupy/feed-remote-page1.json"
    body = json.loads(fixture.read_text(encoding="utf-8"))
    count = {"data": [], "pagination": {"total": len(body["data"]), "limit": 10, "offset": 0}}
    pages = [body]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("limit") == "10":
            return httpx.Response(200, json=count)
        return httpx.Response(200, json=pages.pop(0) if pages else {"data": []})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    with connect(api.config.db_path) as conn:
        GupyCollector(conn, fetcher, max_pages=2, workplace_types=("remote",)).collect()
    stage = _stage_for("gupy")
    _ledger(api, stage, hours_ago=80, status="FAILED")  # would be due, if it could run

    def open_gupy() -> list[tuple]:
        with connect(api.config.db_path) as conn:
            return conn.execute(
                "SELECT id, first_seen_at, last_seen_at FROM job"
                " WHERE provider = 'gupy' AND closed_at IS NULL ORDER BY id"
            ).fetchall()

    before = open_gupy()
    assert len(before) == len(body["data"])

    payload = api.handle_api("GET", "/api/sources", {}, {})
    source = next(s for s in payload["sources"] if s["id"] == "gupy")
    row = next(r for r in payload["refresh"] if r["source_id"] == "gupy")
    blocker = source["collection_blocker"]
    assert source["state"] == "BLOCKED_PROVIDER" and source["note"] == blocker
    assert source["can_refresh"] is False and source["permission"] != "FORBIDDEN"
    assert source["coverage"] not in ("BLOCKED", "UNSUPPORTED")
    assert "2026-10-02" in blocker and "still available" in blocker
    for claim in ("robots", "terms", "forbid", "prohibit", "deprecat", "documented"):
        assert claim not in blocker.lower(), claim
    assert row["state"] == "BLOCKED" and row["due"] is False and row["blocker"] == blocker
    available = [r for r in payload["refresh"] if r["state"] != "BLOCKED"]
    assert payload["summary"]["available"] <= len(available)

    # The matrix and the maintenance plan give the same answer as Settings:
    # its jobs are in production, its upstream is unavailable, not forbidden.
    catalogue = api.config.config_dir / "source_catalogue.yaml"
    with connect(api.config.db_path) as conn:
        row = next(r for r in matrix.build(conn, catalogue_path=catalogue) if r.source_id == "gupy")
        items = [i for i in maintenance.inventory(conn, catalogue) if i.provider == "gupy"]
    assert row.state is matrix.MatrixState.PRODUCTION and row.postings == len(before)
    assert row.production_enabled is False and row.blocker == blocker
    assert "recheck" in row.next_action
    assert items and all(i.blocked == "PROVIDER_UNAVAILABLE" for i in items)

    recorder = Recorder(api, monkeypatch)
    _run(api, "refresh-due")
    _run(api, "refresh-all")
    assert stage not in recorder.ran

    with pytest.raises(Exception) as refused:
        api.handle_api("POST", "/api/sources/refresh", {}, {"source_id": "gupy"})
    assert getattr(refused.value, "status", None) == 409
    assert stage not in recorder.ran
    assert open_gupy() == before, "an unavailable feed closed or touched its jobs"

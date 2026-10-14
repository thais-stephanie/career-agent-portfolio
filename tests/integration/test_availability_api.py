"""Ad availability through the app: in the background, bounded, never deleting.

Simulated HTTP and DNS only (the network guard refuses anything else). A
removed ad leaves the default search, stays one switch away, stays in the
saved list, keeps its score, and is never read again by a deep reading.
"""

from __future__ import annotations

import shutil
import threading
import time
from pathlib import Path

import httpx
import pytest
from tests.integration.test_semantic_api import build

from career_agent import availability
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime.mode import RuntimeMode
from career_agent.storage.db import connect, transaction
from career_agent.web.server import ApiError

DEMO_FILE = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    app = build(tmp_path, RuntimeMode.PERSONAL)
    # A search already described, so the main list holds postings.
    shutil.copyfile(
        app.config.config_dir / "search.worked-example.yaml",
        app.config.config_dir / "search.local.yaml",
    )
    config, _ = load_search_config(app.config.config_dir)
    conn = connect(app.config.db_path)
    try:
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    return app


def wide(app, **query) -> dict:
    base = {
        "include_ineligible": ["true"],
        "include_unresolved": ["true"],
        "include_off_target": ["true"],
        "group_duplicates": ["false"],
        "limit": ["100"],
    }
    return app.handle_api("GET", "/api/jobs", {**base, **{k: [v] for k, v in query.items()}}, {})


def simulate(monkeypatch: pytest.MonkeyPatch, gone: set[str]) -> list[str]:
    """Every request answered here: 404 for `gone`, the title for the rest."""
    asked: list[str] = []
    real = availability.run

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if any(key in str(request.url) for key in gone):
            return httpx.Response(404, text="gone")
        return httpx.Response(200, text="ANY")

    def fake_run(conn, candidates, **kwargs):
        titled = [(job_id, url, title) for job_id, url, title in candidates]

        def answer(request: httpx.Request) -> httpx.Response:
            response = handler(request)
            if response.status_code == 200:
                title = next(
                    (t for _i, u, t in titled if u and u.split("?")[0] in str(request.url)), ""
                )
                return httpx.Response(200, text=f"<h1>{title}</h1>")
            return response

        http = httpx.Client(transport=httpx.MockTransport(answer), follow_redirects=False)
        return real(
            conn,
            candidates,
            client=http,
            resolve=lambda host: ["93.184.216.34"],
            sleep=lambda s: None,
            **kwargs,
        )

    monkeypatch.setattr(availability, "run", fake_run)
    return asked


def wait(app) -> None:
    deadline = time.monotonic() + 60
    while app.availability.running and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not app.availability.running


def test_a_removed_ad_leaves_the_default_search_and_nothing_else(app, monkeypatch) -> None:
    listed = wide(app)["items"]
    target = listed[0]
    saved_id = target["job_id"]
    app.handle_api("PATCH", f"/api/jobs/{saved_id}/saved", {}, {"saved": True})
    score_before = target["match_score"]
    asked = simulate(monkeypatch, gone={target["url"].split("?")[0]} if target["url"] else set())
    app.handle_api("POST", "/api/availability/check", {}, {})
    wait(app)
    assert asked, "nothing was asked"
    assert len(asked) <= availability.RUN_LIMIT * (availability.MAX_REDIRECTS + 1)

    status = app.handle_api("GET", "/api/availability", {}, {})
    assert status["states"].get("REMOVED") == 1
    assert sum(status["states"].values()) == status["of"]

    default_ids = {i["job_id"] for i in wide(app)["items"]}
    assert saved_id not in default_ids, "a removed ad stayed in the default search"
    shown = {i["job_id"]: i for i in wide(app, include_removed="true")["items"]}
    assert shown[saved_id]["availability"]["state"] == "REMOVED"
    assert shown[saved_id]["match_score"] == score_before, "a check never moves a score"
    saved = {i["job_id"] for i in wide(app, saved_only="true")["items"]}
    assert saved_id in saved, "a saved ad must stay reachable"

    # A deep reading never reads it again.
    from career_agent.semantic.deep_run import population_rows

    conn = connect(app.config.db_path)
    try:
        rows = population_rows(conn, app.search_config())
    finally:
        conn.close()
    assert saved_id not in {str(r["id"]) for r in rows}


def test_recent_answers_are_reused_and_a_second_run_is_refused(app, monkeypatch) -> None:
    asked = simulate(monkeypatch, gone=set())
    app.handle_api("POST", "/api/availability/check", {}, {})
    wait(app)
    first = len(asked)
    app.handle_api("POST", "/api/availability/check", {}, {})
    wait(app)
    assert len(asked) == first, "postings checked minutes ago were asked again"

    app.availability.start(lambda state, cancel: cancel.wait(30), "held")
    try:
        with pytest.raises(ApiError) as refused:
            app.handle_api("POST", "/api/availability/check", {}, {})
        assert refused.value.status == 409
    finally:
        app.availability.cancel()
        wait(app)


def test_it_waits_for_a_recalculation_or_a_collection(app, monkeypatch) -> None:
    simulate(monkeypatch, gone=set())
    app.rescore.start(lambda state, cancel: cancel.wait(30), "held")
    try:
        with pytest.raises(ApiError) as refused:
            app.handle_api("POST", "/api/availability/check", {}, {})
        assert refused.value.status == 409
    finally:
        app.rescore.cancel()
        deadline = time.monotonic() + 30
        while app.rescore.running and time.monotonic() < deadline:
            time.sleep(0.05)


def test_nothing_is_written_until_asked(app) -> None:
    conn = connect(app.config.db_path)
    try:
        with transaction(conn):
            assert conn.execute("SELECT COUNT(*) FROM job_availability").fetchone()[0] == 0
    finally:
        conn.close()
    status = app.handle_api("GET", "/api/availability", {}, {})
    assert set(status["states"]) == {"NOT_CHECKED"} and not status["running"]
    assert status["states"]["NOT_CHECKED"] == status["of"] > 0


def _slow(monkeypatch: pytest.MonkeyPatch, first_asked: threading.Event) -> None:
    """Simulated pages that take a moment each, so a run can be overtaken."""
    real = availability.run

    def answer(request: httpx.Request) -> httpx.Response:
        first_asked.set()
        time.sleep(0.2)
        return httpx.Response(200, text="<h1>ANY</h1>")

    def fake_run(conn, candidates, **kwargs):
        http = httpx.Client(transport=httpx.MockTransport(answer), follow_redirects=False)
        return real(
            conn, candidates, client=http, resolve=lambda host: ["93.184.216.34"],
            sleep=lambda s: None, **kwargs,
        )  # fmt: skip

    monkeypatch.setattr(availability, "run", fake_run)


def test_a_running_check_stops_when_a_recalculation_starts_and_keeps_its_answers(
    app, monkeypatch
) -> None:
    first = threading.Event()
    _slow(monkeypatch, first)
    app.handle_api("POST", "/api/availability/check", {}, {})
    assert first.wait(30), "the check never asked a page"
    app.rescore.start(lambda state, cancel: cancel.wait(30), "held")
    try:
        wait(app)
    finally:
        app.rescore.cancel()
    funnel = app.availability.snapshot()["funnel"]
    assert funnel["stopped"] == "paused for a collection or a recalculation"
    assert 0 < funnel["checked"] < funnel["planned"]
    conn = connect(app.config.db_path)
    try:
        stored = conn.execute("SELECT COUNT(*) FROM job_availability").fetchone()[0]
    finally:
        conn.close()
    assert stored == funnel["checked"], "the answers received before the stop are kept"


def test_it_waits_for_a_recalculation_in_another_process(app, monkeypatch) -> None:
    """The command line, or a second window on the same profile."""
    import json
    from datetime import UTC, datetime

    from career_agent.storage.repositories import PipelineRunRepo

    simulate(monkeypatch, gone=set())
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn = connect(app.config.db_path)
    try:
        with transaction(conn):
            run_id = PipelineRunRepo(conn).start("rescore")
            conn.execute(
                "UPDATE pipeline_run SET started_at = ?, stats_json = ? WHERE id = ?",
                (
                    now,
                    json.dumps({"jobs_scored": 1, "jobs_targeted": 9, "heartbeat_at": now}),
                    run_id,
                ),
            )
    finally:
        conn.close()
    with pytest.raises(ApiError) as refused:
        app.handle_api("POST", "/api/availability/check", {}, {})
    assert refused.value.status == 409


def test_it_waits_for_a_collection_in_another_process(app, monkeypatch) -> None:
    from career_agent.storage import catalogue

    simulate(monkeypatch, gone=set())
    monkeypatch.setattr(catalogue, "collecting_elsewhere", lambda conn: True)
    with pytest.raises(ApiError) as refused:
        app.handle_api("POST", "/api/availability/check", {}, {})
    assert refused.value.status == 409

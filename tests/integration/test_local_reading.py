"""The local reading end to end, against a fake Ollama on loopback.

Every state a person can meet is reached through the real web API, the real
streaming transport and the real deadline: SUCCESS (stored, shown again, never
touching Search Fit), CANCELLED (the connection to Ollama is closed),
TIMEOUT, MODEL_MISSING, OLLAMA_UNAVAILABLE and ERROR. Synthetic postings only.
"""

from __future__ import annotations

import shutil
import socket
import time
from pathlib import Path

import pytest
from tests.fake_ollama import FakeOllama
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.local_ai import runner
from career_agent.pipeline import enrich as enrich_module
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "local reading")
        from career_agent.pipeline.demo_seed import seed_demo

        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
        top = conn.execute(
            "SELECT j.id, r.description_text, m.match_score FROM job_match m"
            " JOIN job j ON j.id = m.job_id JOIN job_raw r ON r.content_hash = j.content_hash"
            " ORDER BY m.match_score DESC LIMIT 1"
        ).fetchone()
        low = conn.execute(
            "SELECT job_id FROM job_match ORDER BY match_score ASC LIMIT 1"
        ).fetchone()[0]
    finally:
        conn.close()
    monkeypatch.setattr(enrich_module, "CACHE_ROOT", tmp_path / "local_ai_cache")
    api = JobsApi(ServerConfig(db_path=db, config_dir=config), quiet=True)
    description = str(top["description_text"])
    quote = next(line.strip() for line in description.splitlines() if len(line.strip()) > 30)[:80]
    return {
        "api": api,
        "job": str(top["id"]),
        "score": int(top["match_score"]),
        "low": str(low),
        "quote": quote,
        "monkeypatch": monkeypatch,
    }


def _wait(api: JobsApi, job_id: str, seconds: float = 20.0) -> dict:
    ends = time.monotonic() + seconds
    while time.monotonic() < ends:
        state = api.handle_api("GET", f"/api/jobs/{job_id}/local-reading", {}, {})
        if state["state"] in runner.FINAL:
            return state
        time.sleep(0.05)
    raise AssertionError("the reading never reached a final state")


def _score(api: JobsApi, job_id: str) -> int:
    return int(api.handle_api("GET", f"/api/jobs/{job_id}", {}, {})["match_score"])


def test_a_reading_succeeds_is_stored_and_never_changes_search_fit(world) -> None:
    api, job = world["api"], world["job"]
    before = _score(api, job)
    status = api.handle_api("GET", f"/api/jobs/{job}/local-reading", {}, {})
    assert status["state"] == runner.NOT_RUN
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        started = api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        assert started["state"] == runner.RUNNING
        final = _wait(api, job)
        assert final["state"] == runner.SUCCESS, final
        # Streamed, bounded, and asked only of the loopback fake.
        chat = fake.behaviour.chats[0]
        assert chat["stream"] is True and chat["think"] is False
        assert chat["options"]["num_predict"] > 0
    job_view = api.handle_api("GET", f"/api/jobs/{job}", {}, {})
    assert job_view["enrichment"], "the reading is stored with the posting"
    assert _score(api, job) == before, "a local reading never changes Search Fit"


def test_cancel_stops_the_model_and_says_so(world) -> None:
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.chunks = 60
        fake.behaviour.chunk_delay = 0.2
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        ends = time.monotonic() + 10
        while not fake.behaviour.chats and time.monotonic() < ends:
            time.sleep(0.05)
        time.sleep(0.5)
        api.handle_api("POST", f"/api/jobs/{job}/enrich/cancel", {}, {})
        final = _wait(api, job)
        assert final["state"] == runner.CANCELLED
        ends = time.monotonic() + 5
        while fake.behaviour.disconnects == 0 and time.monotonic() < ends:
            time.sleep(0.05)
        assert fake.behaviour.disconnects == 1, "the connection to Ollama was closed"
    assert not api.handle_api("GET", f"/api/jobs/{job}", {}, {}).get("enrichment")


def test_a_reading_that_runs_past_its_deadline_times_out(world) -> None:
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.chunks = 40
        fake.behaviour.chunk_delay = 0.25
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        world["monkeypatch"].setenv("OLLAMA_TIMEOUT_MS", "1500")
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        final = _wait(api, job)
    assert final["state"] == runner.TIMEOUT
    assert final["elapsed_s"] < 10


def test_a_missing_model_is_named(world) -> None:
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.models = ["llama3:8b"]
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        final = _wait(api, job)
        assert fake.behaviour.chats == [], "nothing was asked of a model that is not there"
    assert final["state"] == runner.MODEL_MISSING
    assert "ollama pull qwen3:4b" in final["message"]


def test_ollama_not_running_is_a_state_not_a_spinner(world) -> None:
    api, job = world["api"], world["job"]
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    world["monkeypatch"].setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{port}")
    api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
    final = _wait(api, job)
    assert final["state"] == runner.OLLAMA_UNAVAILABLE
    health = api.handle_api("GET", "/api/health", {}, {})
    assert health["ollama"]["reachable"] is False, "the status line learns from a failure"


def test_an_answer_cut_off_is_an_error_not_a_hang(world) -> None:
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.done_reason = "length"
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        final = _wait(api, job)
    assert final["state"] == runner.ERROR


def test_a_job_below_the_threshold_is_refused_with_a_reason(world) -> None:
    api = world["api"]
    with FakeOllama() as fake:
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{world['low']}/enrich", {}, {})
        final = _wait(api, world["low"])
        assert fake.behaviour.chats == []
    assert final["state"] == runner.ERROR and final["code"] == "below_threshold"


def test_the_local_path_cannot_reach_a_hosted_provider() -> None:
    """The promise "on your own computer", made checkable: the reading path
    imports no hosted-provider code, and a non-loopback address is refused."""
    import ast

    for module in (enrich_module, runner):
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        names = {
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        } | {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        assert not any(
            n.startswith(("career_agent.semantic", "career_agent.llm")) for n in names
        ), names
    from career_agent.local_ai.ollama import OllamaClient, OllamaRefused, OllamaSettings

    with pytest.raises(OllamaRefused):
        OllamaClient(OllamaSettings(base_url="https://api.example.com"))


def test_cancel_is_immediate_even_while_the_model_reads_the_prompt(world) -> None:
    """Nothing streams while the prompt is read; Cancel must not wait for it."""
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.first_delay = 30
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        ends = time.monotonic() + 10
        while not fake.behaviour.chats and time.monotonic() < ends:
            time.sleep(0.05)
        asked = time.monotonic()
        api.handle_api("POST", f"/api/jobs/{job}/enrich/cancel", {}, {})
        final = _wait(api, job, seconds=10)
        assert final["state"] == runner.CANCELLED
        assert time.monotonic() - asked < 3, "Cancel waited for the model"


def test_cancel_is_immediate_even_while_the_model_loads(world) -> None:
    """A cold load sends not even the response headers until the model is in
    memory: measured live, Cancel used to wait the whole load (47s)."""
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.header_delay = 30
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        ends = time.monotonic() + 10
        while not fake.behaviour.chats and time.monotonic() < ends:
            time.sleep(0.05)
        asked = time.monotonic()
        api.handle_api("POST", f"/api/jobs/{job}/enrich/cancel", {}, {})
        final = _wait(api, job, seconds=10)
        assert final["state"] == runner.CANCELLED
        assert time.monotonic() - asked < 3, "Cancel waited for the model to load"


def test_a_load_that_outlives_the_deadline_times_out(world) -> None:
    api, job = world["api"], world["job"]
    with FakeOllama() as fake:
        fake.behaviour.quote = world["quote"]
        fake.behaviour.header_delay = 30
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        world["monkeypatch"].setenv("OLLAMA_TIMEOUT_MS", "1500")
        api.handle_api("POST", f"/api/jobs/{job}/enrich", {}, {})
        final = _wait(api, job, seconds=15)
    assert final["state"] == runner.TIMEOUT
    assert final["elapsed_s"] < 6

"""The local reading in the drawer, against a fake Ollama on loopback.

The page used to show a spinner with no end. Every state a person can meet is
now a sentence on `#enrich-msg`, with its state on `data-state`: this drives
the real page, the real web API and the real streaming transport through
SUCCESS (the reading is drawn and shown again on reopening), CANCELLED and
OLLAMA_UNAVAILABLE. Synthetic postings only.
"""

from __future__ import annotations

import json
import shutil
import socket
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.fake_ollama import FakeOllama
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline import enrich as enrich_module
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def local_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "local reading browser")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
        # Every posting the local model may read (Search Fit at or above its
        # threshold); the drawer is opened on whichever the list shows first.
        readable = {
            str(row["id"]): str(row["description_text"])
            for row in conn.execute(
                "SELECT j.id, r.description_text FROM job_match m"
                " JOIN job j ON j.id = m.job_id JOIN job_raw r ON r.content_hash = j.content_hash"
                " WHERE m.match_score >= 60"
            )
        }
    finally:
        conn.close()
    monkeypatch.setattr(enrich_module, "CACHE_ROOT", tmp_path / "local_ai_cache")
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    assert readable, "the demo has postings above the local model's threshold"
    try:
        yield {
            "url": f"http://127.0.0.1:{port}",
            "readable": readable,
            "monkeypatch": monkeypatch,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def _open_drawer(page: Chrome, world: dict) -> str:
    """Open the first listed posting the local model may read; return its quote."""
    open_home_past_setup(page, world["url"])
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    ids = json.dumps(sorted(world["readable"]))
    card = (
        f"Array.from(document.querySelectorAll('[data-job-id]'))"
        f".find((n) => {ids}.includes(n.dataset.jobId))"
    )
    page.wait_for(card, message="a listed posting above the threshold")
    job = str(page.evaluate(f"{card}.dataset.jobId"))
    page.evaluate(f"(() => {{ {card}.click(); return true; }})()")
    page.wait_for("document.getElementById('enrich-run')", message="the local reading section")
    lines = world["readable"][job].splitlines()
    return next(line.strip() for line in lines if len(line.strip()) > 30)[:80]


def _state(page: Chrome) -> str:
    return str(page.evaluate("document.getElementById('enrich-msg').dataset.state || ''"))


def _message(page: Chrome) -> str:
    return str(page.evaluate("document.getElementById('enrich-msg').textContent"))


def test_a_reading_runs_shows_progress_and_is_shown_again(page: Chrome, local_server) -> None:
    world = local_server
    with FakeOllama() as fake:
        fake.behaviour.chunks = 20
        fake.behaviour.chunk_delay = 0.25
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        fake.behaviour.quote = _open_drawer(page, world)
        page.evaluate("document.getElementById('enrich-run').click()")
        page.wait_for(
            "document.getElementById('enrich-msg').dataset.state === 'RUNNING'"
            " && !document.getElementById('enrich-cancel').hidden",
            message="a running reading with a Cancel button",
        )
        assert _message(page).strip(), "a running reading says what it is doing"
        # The drawer redraws itself with the stored reading once it succeeds.
        page.wait_for(
            "document.querySelector('.kv--enrich')",
            timeout=30,
            message="the stored reading drawn in the drawer",
        )
        # Redrawn once, then stable: a finished reading must not reopen the
        # drawer again and again (it did, every 1.5s, while it stayed open).
        job_calls = (
            "performance.getEntriesByType('resource').filter((e) =>"
            " /\\/api\\/jobs\\/[^/?]+$/.test(new URL(e.name).pathname)).length"
        )
        page.wait_for("document.querySelector('.kv--enrich')", message="the redrawn drawer")
        import time

        time.sleep(1.0)
        settled = page.evaluate(job_calls)
        time.sleep(4.0)
        assert page.evaluate(job_calls) == settled, "the drawer kept reopening itself"
    # Reopening shows the stored reading without asking the model again.
    _open_drawer(page, world)
    page.wait_for("document.querySelector('.kv--enrich')", message="the stored reading")
    assert "again" in str(page.evaluate("document.getElementById('enrich-run').textContent"))
    import time

    time.sleep(1.0)
    settled = page.evaluate(job_calls)
    time.sleep(4.0)
    assert page.evaluate(job_calls) == settled, "a reopened drawer kept reopening itself"


def test_cancel_in_the_drawer_stops_the_reading(page: Chrome, local_server) -> None:
    world = local_server
    with FakeOllama() as fake:
        fake.behaviour.first_delay = 30
        world["monkeypatch"].setenv("OLLAMA_BASE_URL", fake.url)
        fake.behaviour.quote = _open_drawer(page, world)
        page.evaluate("document.getElementById('enrich-run').click()")
        page.wait_for(
            "!document.getElementById('enrich-cancel').hidden",
            message="the Cancel button while the model reads the prompt",
        )
        page.evaluate("document.getElementById('enrich-cancel').click()")
        page.wait_for(
            "document.getElementById('enrich-msg').dataset.state === 'CANCELLED'",
            timeout=10,
            message="the cancelled state",
        )
        assert "Cancelled" in _message(page)
        assert page.evaluate("document.getElementById('enrich-cancel').hidden")
        assert not page.evaluate("document.getElementById('enrich-run').disabled")


def test_ollama_not_running_is_said_in_the_drawer(page: Chrome, local_server) -> None:
    world = local_server
    world["monkeypatch"].setenv("OLLAMA_BASE_URL", f"http://127.0.0.1:{_free_port()}")
    _open_drawer(page, world)
    page.evaluate("document.getElementById('enrich-run').click()")
    page.wait_for(
        "document.getElementById('enrich-msg').dataset.state === 'OLLAMA_UNAVAILABLE'",
        timeout=15,
        message="the Ollama-unavailable state",
    )
    assert "not running" in _message(page)
    assert _state(page) == "OLLAMA_UNAVAILABLE"

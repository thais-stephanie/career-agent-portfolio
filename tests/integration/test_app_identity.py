"""/api/app and /api/app/quit: who answers on the port, and how it stops.

The desktop launcher trusts /api/app to tell this installation's Career Agent
from anything else on the port, and stops the servers through /api/app/quit.
Neither may say anything about the person, and the stop must be as hard to
forge from another website as every other POST.
"""

from __future__ import annotations

import http.client
import json
import shutil
import socket
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent import install_id
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web import server as web_server
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> JobsApi:
    monkeypatch.setattr(web_server, "on_quit", None)
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "identity")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config, port=0), quiet=True)


def test_it_names_the_program_and_installation_and_nothing_about_the_person(
    api: JobsApi,
) -> None:
    answer = api.handle_api("GET", "/api/app", {}, {})
    assert answer == {
        "app": "career-agent",
        "install": install_id(REPO),
        "mode": "PERSONAL",
        "can_quit": False,
    }
    assert str(REPO).casefold() not in json.dumps(answer).casefold()


def test_without_a_launcher_that_can_stop_it_quit_is_refused(api: JobsApi) -> None:
    with pytest.raises(ApiError) as caught:
        api.handle_api("POST", "/api/app/quit", {}, {})
    assert caught.value.status == 409


def _post(port: int, headers: dict[str, str]) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request("POST", "/api/app/quit", body=b"{}", headers=headers)
        return conn.getresponse().status
    finally:
        conn.close()


def test_quit_stops_only_for_a_local_request(api: JobsApi, monkeypatch: pytest.MonkeyPatch) -> None:
    stopped = threading.Event()
    monkeypatch.setattr(web_server, "on_quit", stopped.set)
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    # The Host check compares with the port the server was configured for.
    api = JobsApi(replace(api.config, port=port), quiet=True)
    httpd = build_server(api)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    own = {"Host": f"127.0.0.1:{port}", "Content-Type": "application/json"}
    try:
        assert _post(port, {**own, "Origin": "https://example.com"}) == 403
        assert _post(port, {**own, "Host": "evil.example:80"}) == 403
        assert _post(port, {"Host": own["Host"], "Content-Type": "text/plain"}) in (403, 415)
        time.sleep(0.5)
        assert not stopped.is_set(), "a forged request stopped Career Agent"
        assert api.handle_api("GET", "/api/app", {}, {})["can_quit"] is True
        assert _post(port, own) == 200
        assert stopped.wait(5), "a local quit did not stop the servers"
    finally:
        httpd.shutdown()
        httpd.server_close()


def test_quit_waits_for_a_running_collection_or_recalculation(
    api: JobsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    stopped = threading.Event()
    monkeypatch.setattr(web_server, "on_quit", stopped.set)
    monkeypatch.setattr(api, "rescore", SimpleNamespace(running=True))
    with pytest.raises(ApiError) as caught:
        api.handle_api("POST", "/api/app/quit", {}, {})
    assert caught.value.status == 409
    time.sleep(0.5)
    assert not stopped.is_set(), "a quit cut a recalculation short"

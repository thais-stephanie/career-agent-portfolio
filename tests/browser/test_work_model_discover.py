"""Discover says what a never-shown way of working set aside, and puts it back.

The demo corpus is remote work, so "never show remote" is the answer with
something to set aside. The database is seeded after the answer is written, so
its scores answer the search in force.
"""

from __future__ import annotations

import shutil
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import DEMO_FILE

from career_agent.config.candidate_writer import set_candidate_fields
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server


@pytest.fixture
def never_remote(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    config_dir = tmp_path / "config"
    shutil.copytree(committed_config, config_dir)
    set_candidate_fields(config_dir, {"excluded_work_models": ["REMOTE"]})
    db = tmp_path / "demo.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        config, _ = load_search_config(config_dir)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=port), quiet=True)
    httpd = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def test_a_never_shown_way_of_working_is_counted_and_revealable(
    page: Chrome, never_remote: str
) -> None:
    page.navigate(never_remote + "/#jobs")
    page.wait_for(
        "document.querySelector('#hiddennotice')?.innerText.includes('way of working')",
        message="the notice naming what was set aside",
    )
    assert page.evaluate("document.querySelectorAll('#list [data-job-id]').length") <= 1
    page.evaluate(
        "[...document.querySelectorAll('#hiddennotice button')]"
        ".find((b) => b.closest('[data-key=\"include_excluded_work_model\"]')"
        " || /Show those too/.test(b.textContent) && b.parentElement.innerText"
        ".includes('way of working')).click()"
    )
    page.wait_for(
        "document.querySelectorAll('#list [data-job-id]').length > 3",
        message="the remote postings back on the list",
    )
    assert page.evaluate("localStorage.getItem('careerAgent.includeExcludedWorkModel.v1')") == "1"
    assert page.console_errors() == []

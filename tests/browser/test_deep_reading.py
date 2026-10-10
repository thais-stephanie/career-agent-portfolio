"""The deep reading in the browser, with a FAKE subscription provider.

Synthetic postings, synthetic profile, no command line and no network: the
provider is replaced in-process. What must hold on the real page: opening the
panel and asking for a preview send nothing; Start stays disabled until the
preview is confirmed; a run is followed to its results; a result opens the
drawer, whose section shows the level beside the local score with the quotes;
and the whole flow reads in Portuguese.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.integration.test_deep_run import FakeCli
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.semantic import providers
from career_agent.semantic.providers import Availability, ProviderStatus
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
SHOTS = os.environ.get("DEEP_SHOTS")


class SignedInFake(FakeCli):
    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE, "", {"auth": "subscription"})


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def deep_server(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict]:
    config = tmp_path / "config"
    shutil.copytree(committed_config_dir(), config, ignore=shutil.ignore_patterns("*.local.*"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    (config / "semantic.local.yaml").write_text("enabled: true\nmode: claude_code\n", "utf-8")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "deep reading browser")
        search, _ = load_search_config(config)
        seed_demo(conn, search, source=DEMO)
    finally:
        conn.close()
    fake = SignedInFake(id="claude_code")
    monkeypatch.setattr(providers, "get_provider", lambda provider_id: fake)
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "fake": fake}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


PANEL = "document.querySelector('.deep')"


def _open_panel(page: Chrome, url: str) -> None:
    open_home_past_setup(page, url)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.getElementById('deep-open')", message="the Deep reading button")
    page.evaluate("document.getElementById('deep-open').click()")
    page.wait_for(f"{PANEL} && {PANEL}.querySelector('.deep__access')", message="the panel")


def _click_text(page: Chrome, selector: str, pattern: str) -> None:
    page.evaluate(
        f"Array.from(document.querySelectorAll({selector!r}))"
        f".find((n) => /{pattern}/i.test(n.textContent)).click()"
    )


def test_preview_sends_nothing_and_a_confirmed_run_reaches_the_drawer(
    page: Chrome, deep_server: dict
) -> None:
    fake = deep_server["fake"]
    _open_panel(page, deep_server["url"])
    text = str(page.evaluate(f"{PANEL}.innerText"))
    assert "Claude Code · Claude subscription" in text and "Signed in with a subscription" in text
    _click_text(page, ".deep button", "what would be sent")
    page.wait_for(f"/What would be sent/.test({PANEL}.innerText)", message="the preview")
    assert fake.messages == [], "a preview sent something"
    start = (
        "Array.from(document.querySelectorAll('.deep button'))"
        ".find((n) => /Start reading/.test(n.textContent))"
    )
    assert page.evaluate(f"{start}.disabled"), "Start is enabled before the preview is confirmed"
    page.evaluate(
        "Array.from(document.querySelectorAll('.deep__consent input'))"
        ".find((n) => /checked the postings/.test(n.parentNode.textContent)).click()"
    )
    if SHOTS:
        page.screenshot(Path(SHOTS) / "deep-preview.png")
    page.evaluate(f"{start}.click()")
    page.wait_for("document.querySelector('.deep__row')", timeout=30, message="results")
    assert fake.messages, "the run asked the provider"
    assert all(m["evidence"] == [] for m in fake.messages), "evidence left without consent"
    if SHOTS:
        page.screenshot(Path(SHOTS) / "deep-results.png")
    page.evaluate("document.querySelector('.deep__open').click()")
    page.wait_for(
        "Array.from(document.querySelectorAll('.d-sec__head'))"
        ".some((n) => n.textContent === 'Deep reading')"
        " && document.querySelector('.deep__levelline')",
        message="the drawer's deep reading",
    )
    section = str(
        page.evaluate("document.querySelector('.deep__levelline').closest('.d-sec').innerText")
    )
    assert "Local score" in section, section
    if SHOTS:
        page.screenshot(Path(SHOTS) / "deep-drawer.png")


def test_the_flow_reads_in_portuguese(page: Chrome, deep_server: dict) -> None:
    open_home_past_setup(page, deep_server["url"])
    page.evaluate("window.localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    _open_panel(page, deep_server["url"])
    text = str(page.evaluate(f"{PANEL}.innerText"))
    assert "Claude Code · assinatura Claude" in text
    assert "Enviar minhas habilidades e experiências confirmadas" in text
    assert "deep." not in text, "a catalogue key leaked onto the screen"
    if SHOTS:
        page.screenshot(Path(SHOTS) / "deep-pt.png")

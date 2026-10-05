"""Analyze in a real browser (PR 11): resume only, for a job, details, actions,
a fresh result after an edit, the phone width and Portuguese.

A synthetic senior profile, one tailored job version and the invented demo
jobs. No model; nobody's real resume.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import leave_cleanly  # noqa: F401
from tests.integration.test_resume_master import profile
from tests.integration.test_resume_tailor import AD
from tests.support_tailor import senior

from career_agent.clock import new_id
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore
from career_agent.resume_doc.tailor import tailor
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

DEMO = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"
LOCALE_KEY = "careerAgent.locale.v1"
DUP = '.rvan__finding[data-kind="DUPLICATE_BULLET"]'
SHOWN = '.rvan__req[data-state="SUPPORTED_IN_RESUME"]'
GAP = '.rvan__req[data-state="NOT_FOUND_IN_CONFIRMED_EXPERIENCE"]'
REPEAT = "The same line appears twice."


@pytest.fixture
def analyze_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    conn = profile(tmp_path, "p")
    senior(conn)
    seed_demo(conn, load_search_config(committed_config)[0], source=DEMO)
    store = ResumeStore(conn)
    master = store.current_master()
    data = master.working.model_dump(mode="json")
    first = data["experience"][0]["bullets"][0]
    data["experience"][0]["bullets"].append({**first, "id": new_id()})
    store.save_working_copy(
        master.id, upgrade_resume_document(data), expected_sha256=master.working_sha256
    )
    version, _ = tailor(conn, ad=AD)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}",
            "db": db,
            "master": master.id,
            "version": version.id,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def open_analyze(page: Chrome, server: dict[str, Any], width: int = 1366) -> None:
    page.set_viewport(width, 900, mobile=width < 500)
    page.navigate(server["url"])
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.getElementById('rvw-analyze') !== null", message="the Home card")
    page.evaluate("document.getElementById('rvw-analyze').click()")
    page.wait_for("document.getElementById('rvan-go') !== null", message="the Analyze form")


def choose(page: Chrome, doc_id: str) -> None:
    page.evaluate(
        f"const s = document.getElementById('rva-doc'); s.value = '{doc_id}';"
        "s.dispatchEvent(new Event('change'))"
    )
    page.wait_for(f"document.getElementById('rva-doc')?.value === '{doc_id}'")


def result(page: Chrome) -> str:
    page.wait_for("document.querySelector('#rvan-result h3') !== null", message="a result")
    return str(page.evaluate("document.getElementById('rvan-result').innerText"))


def test_resume_only_names_findings_and_never_scores(
    page: Chrome, analyze_server: dict[str, Any]
) -> None:
    open_analyze(page, analyze_server)
    choose(page, analyze_server["master"])
    page.evaluate("document.getElementById('rvan-go').click()")
    said = result(page)
    assert "Overview" in said and "not a score" in said
    assert REPEAT in said
    assert "Fact" in said and "Advice" not in said or "Fact" in said
    for word in ("score", "Score", "%", "probability", "match"):
        assert word not in said.replace("not a score", ""), word
    # The safe change: remove the exact repeat, on the server.
    page.evaluate(
        f"Array.from(document.querySelectorAll('{DUP} button'))"
        ".find(b => b.textContent === 'Remove the repeat').click()"
    )
    page.wait_for(
        f"!document.querySelector('{DUP}')",
        message="the repeat removed",
    )
    assert page.console_errors() == []


def test_for_a_job_version_every_ask_has_a_state_and_its_details(
    page: Chrome, analyze_server: dict[str, Any]
) -> None:
    open_analyze(page, analyze_server)
    choose(page, analyze_server["version"])
    page.evaluate('document.querySelector(\'input[name="rvan-mode"][value="job"]\').click()')
    page.evaluate("document.getElementById('rvan-go').click()")
    said = result(page)
    assert "This resume shows confirmed support for" in said
    assert "Before you apply" in said
    assert "Not your resume" in said, "eligibility stays apart"
    states = page.evaluate(
        "Array.from(document.querySelectorAll('.rvan__req')).map(n => n.dataset.state)"
    )
    assert "SUPPORTED_IN_RESUME" in states and "NOT_FOUND_IN_CONFIRMED_EXPERIENCE" in states
    # Details: the employer's own words, where in my resume, and no gap has Apply.
    page.evaluate(f"document.querySelector('{SHOWN} details').open = true")
    shown = page.evaluate(f"document.querySelector('{SHOWN}').innerText")
    assert "In this resume" in shown
    gaps = page.evaluate(
        'Array.from(document.querySelectorAll(\'.rvan__req[data-state="NOT_FOUND_IN_CONFIRMED_EXPERIENCE"]'
        " button')).map(b => b.textContent)"
    )
    assert "Apply" not in gaps


def test_an_edit_gives_a_new_result_of_the_new_content(
    page: Chrome, analyze_server: dict[str, Any]
) -> None:
    open_analyze(page, analyze_server)
    choose(page, analyze_server["master"])
    page.evaluate("document.getElementById('rvan-go').click()")
    assert REPEAT in result(page)
    with connect(analyze_server["db"]) as conn:
        store = ResumeStore(conn)
        doc = store.get_document(analyze_server["master"])
        data = doc.working.model_dump(mode="json")
        data["experience"][0]["bullets"] = data["experience"][0]["bullets"][:-1]
        store.save_working_copy(
            doc.id, upgrade_resume_document(data), expected_sha256=doc.working_sha256
        )
    page.evaluate("document.getElementById('rvan-go').click()")
    page.wait_for(
        f"!document.getElementById('rvan-result').innerText.includes('{REPEAT}')",
        message="the new content analyzed",
    )


def test_analyze_fits_a_phone_and_speaks_portuguese(
    page: Chrome, analyze_server: dict[str, Any]
) -> None:
    page.navigate(analyze_server["url"])
    page.evaluate(f"window.localStorage.setItem('{LOCALE_KEY}', 'pt-BR')")
    open_analyze(page, analyze_server, width=390)
    choose(page, analyze_server["version"])
    page.evaluate('document.querySelector(\'input[name="rvan-mode"][value="job"]\').click()')
    page.evaluate("document.getElementById('rvan-go').click()")
    said = result(page)
    assert "Visão geral" in said and "não uma nota" in said
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")

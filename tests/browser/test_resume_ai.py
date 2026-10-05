"""Tailor with AI in a real browser (PR 9): disclosure, progress, review, finish.

A synthetic senior profile, the invented demo jobs and a FAKE provider
(`tests.support_drafter`) standing where Career Agent's configured AI stands.
No network, no key, no paid call; nobody's real resume.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import leave_cleanly  # noqa: F401
from tests.integration.test_resume_master import profile
from tests.support_drafter import FakeDrafter, change, install
from tests.support_tailor import senior

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore
from career_agent.storage.db import connect
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

DEMO = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


def proposals(s: dict[str, Any]) -> list[dict[str, Any]]:
    """Two grounded rewrites of lines it was sent, and one invented claim."""
    lines = [(r, line) for r in s["resume"]["roles"] for line in r["lines"] if line["evidence_ids"]]
    asks = [a["id"] for a in s["job"]["asks"]]
    out = []
    for n, (_, line) in enumerate(lines[:2]):
        text = line["text"].rstrip(".")
        out.append(
            change(
                "REWRITE_BULLET",
                line["id"],
                text,
                line["evidence_ids"][:1],
                asks[:1],
                f"ok{n}",
                "Puts the relevant work first.",
            )
        )
    _, first = lines[0]
    out.append(
        change(
            "REWRITE_BULLET",
            first["id"],
            "Ran Salesforce for 400 teams worldwide.",
            first["evidence_ids"][:1],
            asks[:1],
            "bad",
        )
    )
    return out


@pytest.fixture
def ai_server(
    tmp_path: Path, committed_config: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[dict[str, Any]]:
    conn = profile(tmp_path, "p")
    senior(conn)
    seed_demo(conn, load_search_config(committed_config)[0], source=DEMO)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    # The job whose ad this profile answers best, so the fake has asks to cite.
    job = conn.execute("SELECT id FROM job WHERE title = 'Business Systems Engineer'").fetchone()[0]
    conn.close()
    fake = FakeDrafter(proposals)
    install(monkeypatch, fake)
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {
            "url": f"http://127.0.0.1:{port}",
            "job": job,
            "fake": fake,
            "db": db,
            "monkeypatch": monkeypatch,
        }
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def open_ai(page: Chrome, server: dict[str, Any], width: int = 1366) -> None:
    page.set_viewport(width, 900, mobile=width < 500)
    page.navigate(server["url"] + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.evaluate(f"document.querySelector('[data-job-id=\"{server['job']}\"]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for("document.getElementById('drawer-tailor-ai') !== null", message="AI button")
    page.evaluate("document.getElementById('drawer-tailor-ai').click()")
    page.wait_for("document.querySelector('.rva h2') !== null", message="the AI screen")


def text(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.rva').innerText"))


def documents(server: dict[str, Any]) -> int:
    with connect(server["db"]) as conn:
        return conn.execute("SELECT COUNT(*) FROM resume_document").fetchone()[0]


def test_not_set_up_says_so_and_leads_to_settings(page: Chrome, ai_server: dict[str, Any]) -> None:
    install(ai_server["monkeypatch"], None)
    open_ai(page, ai_server)
    said = text(page)
    assert "AI is not set up yet" in said and "Tailor without AI" in said
    page.evaluate("document.getElementById('rva-settings').click()")
    page.wait_for("document.body.dataset.page === 'settings'", message="Settings")
    assert ai_server["fake"].calls == []


def test_disclose_send_review_accept_edit_reject_and_create(
    page: Chrome, ai_server: dict[str, Any]
) -> None:
    before = documents(ai_server)
    open_ai(page, ai_server)
    said = text(page)
    # Said before anything is sent: which AI, what goes, what never does, one call.
    assert "Fake AI (fake-model-1)" in said and "One AI request" in said
    assert "Not sent: your contact details" in said
    assert ai_server["fake"].calls == [], "opening the screen sent nothing"
    page.evaluate("document.getElementById('rva-send').click()")
    page.wait_for("document.querySelectorAll('.rva__card').length === 2", message="two changes")
    said = text(page)
    assert "1 suggestion was left out because Career Agent couldn’t verify it." in said
    assert "Salesforce" not in said, "an unverified suggestion is never shown"
    assert "0 of 2 reviewed" in said and "Before:" in said and "Why:" in said
    assert page.evaluate("document.getElementById('rva-create').disabled")
    assert len(ai_server["fake"].calls) == 1
    # Accept the first; edit the second, first with a claim it cannot check.
    page.evaluate("document.querySelectorAll('.rva__card')[0].querySelector('button').click()")
    page.wait_for("document.querySelector('.rva__count').textContent.startsWith('1 of 2')")
    page.evaluate(
        "Array.from(document.querySelectorAll('.rva__card')[1].querySelectorAll('button'))"
        ".find(b => b.textContent === 'Edit').click()"
    )
    page.evaluate(
        "document.querySelector('.rva__card textarea').value = 'Ran Salesforce for 9 teams.'"
    )
    page.evaluate(
        "Array.from(document.querySelectorAll('.rva__card')[1].querySelectorAll('button'))"
        ".find(b => b.textContent === 'Accept my wording').click()"
    )
    page.wait_for(
        "document.querySelector('.rva__said')?.textContent.includes('couldn’t verify')"
        " || document.querySelectorAll('.rva__said')[1].textContent !== ''"
    )
    assert "couldn’t verify" in text(page)
    page.evaluate(
        "Array.from(document.querySelectorAll('.rva__card')[1].querySelectorAll('button'))"
        ".find(b => b.textContent === 'Cancel').click()"
    )
    page.evaluate(
        "Array.from(document.querySelectorAll('.rva__card')[1].querySelectorAll('button'))"
        ".find(b => b.textContent === 'Reject').click()"
    )
    page.wait_for("document.querySelector('.rva__count').textContent.startsWith('2 of 2')")
    said = text(page)
    assert "✓ Accepted" in said and "✕ Rejected" in said, "said in words, not colour"
    assert documents(ai_server) == before, "no version before Create version"
    page.evaluate("document.getElementById('rva-create').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="editor")
    page.wait_for(
        "document.querySelector('.rvj__panel .rvj__job') !== null", message="the job panel"
    )
    panel = str(page.evaluate("document.querySelector('.rvj__panel').innerText"))
    assert "Drafted with AI from your confirmed experience" in panel
    assert "Reworded with AI, reviewed by you" in panel
    for internal in ("AI_ASSISTED", "DRAFTER", "requirement_id", "k-hubspot"):
        assert internal not in panel
    assert documents(ai_server) == before + 1
    # The one expected failure: the edit the server refused (422).
    unexpected = [e for e in page.console_errors() if "/changes/" not in e.get("url", "")]
    assert unexpected == []


def test_cancel_while_ai_works_keeps_nothing(page: Chrome, ai_server: dict[str, Any]) -> None:
    release = threading.Event()
    fake = ai_server["fake"]

    def slow(s: dict[str, Any]) -> list[dict[str, Any]]:
        release.wait(20)
        return proposals(s)

    fake.answer = slow
    before = documents(ai_server)
    open_ai(page, ai_server)
    page.evaluate("document.getElementById('rva-send').click()")
    page.wait_for("document.querySelectorAll('.rvt__step').length === 4", message="progress")
    said = text(page)
    assert "Drafting suggestions with AI" in said and "Checking the suggestions" in said
    page.evaluate("document.getElementById('rva-cancel').click()")
    page.wait_for("document.querySelector('.rva [role=alert]')?.textContent === 'Cancelled.'")
    release.set()
    # The late answer reaches the server after the run ended: it keeps nothing.
    status = ""
    for _ in range(100):
        with connect(ai_server["db"]) as conn:
            row = conn.execute("SELECT status FROM tailoring_run").fetchone()
            status = row[0] if row else ""
            changes = conn.execute("SELECT COUNT(*) FROM tailoring_change").fetchone()[0]
        if status == "ERROR":
            break
        time.sleep(0.1)
    assert status == "ERROR" and changes == 0
    assert documents(ai_server) == before
    assert "Cancelled." in text(page)


def test_a_resume_changed_while_ai_worked_is_said(page: Chrome, ai_server: dict[str, Any]) -> None:
    fake = ai_server["fake"]

    def meanwhile(s: dict[str, Any]) -> list[dict[str, Any]]:
        with connect(ai_server["db"]) as conn:
            store = ResumeStore(conn)
            master = store.current_master()
            data = master.working.model_dump(mode="json")
            data["experience"][0]["bullets"][0]["text"] += " Edited meanwhile."
            store.save_working_copy(
                master.id, upgrade_resume_document(data), expected_sha256=master.working_sha256
            )
        return proposals(s)

    fake.answer = meanwhile
    before = documents(ai_server)
    open_ai(page, ai_server)
    page.evaluate("document.getElementById('rva-send').click()")
    page.wait_for("document.getElementById('rva-retry') !== null", message="the stale screen")
    assert "This resume changed while AI was working" in text(page)
    assert "Discard and try again" in text(page)
    assert documents(ai_server) == before


def test_the_review_fits_a_phone(page: Chrome, ai_server: dict[str, Any]) -> None:
    open_ai(page, ai_server, width=390)
    page.evaluate("document.getElementById('rva-send').click()")
    page.wait_for("document.querySelectorAll('.rva__card').length === 2", message="two changes")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.rva__card button'))"
        ".map(b => b.getAttribute('aria-label'))"
    )
    assert all(label and ":" in label for label in labels), "each action names its change"
    assert page.evaluate("document.querySelector('.rva__count').getAttribute('role')") == "status"

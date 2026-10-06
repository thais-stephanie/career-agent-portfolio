"""Resumes in a real browser (PR 7): Home, My resumes, versions, the old helper.

Synthetic profiles only: the synthetic rich resume, the demo corpus's
invented jobs, and a stubbed answer for the old helper's resumes. Nothing
here reads or moves anybody's real resumes, and the move itself is never
pressed: its preflight and its Cancel are.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import leave_cleanly  # noqa: F401
from tests.support_resume import confirm_cited, rich, sparse

from career_agent.clock import new_id
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"
IMPORTED = "Consulting CV"


def _serve(db: Path, config: Path) -> Iterator[dict[str, Any]]:
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "db": db}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def _profile(db: Path, config: Path) -> Any:
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic Work")
        ensure_candidate(conn)
        confirm_cited(conn, rich())
    seed_demo(conn, load_search_config(config)[0], source=DEMO)
    return conn


@pytest.fixture
def empty_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    _profile(tmp_path / "personal.db", committed_config).close()
    yield from _serve(tmp_path / "personal.db", committed_config)


@pytest.fixture
def library_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    """A Master, one imported resume and two versions for one job."""
    conn = _profile(tmp_path / "personal.db", committed_config)
    store = ResumeStore(conn)
    store.create_document(rich())
    data = sparse().model_dump(mode="json")
    data.update(id=new_id(), kind="IMPORTED", title=IMPORTED, provenance={"created_from": "IMPORT"})
    store.create_document(upgrade_resume_document(data))
    job = conn.execute("SELECT j.id, j.title FROM job j ORDER BY j.id LIMIT 1").fetchone()
    for _ in range(2):
        store.version_from_master(
            job_id=job[0], title=job[1], company="Synthetic Co", url=None, text="An invented ad."
        )
    conn.close()
    for server in _serve(tmp_path / "personal.db", committed_config):
        yield {**server, "job": job[0], "job_title": job[1]}


def open_list(page: Chrome, url: str, width: int = 1366) -> None:
    page.set_viewport(width, 900, mobile=width < 500)
    page.navigate(url)
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')", message="rail")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.querySelector('.rvw__card') !== null", message="home")
    page.evaluate("document.querySelector('.rvw__tab[aria-controls=\"rvw-view-list\"]').click()")
    page.wait_for(
        "document.querySelector('#rvw-view-list .rvl__row') !== null", message="my resumes"
    )


def row_of(name: str) -> str:
    return (
        "[...document.querySelectorAll('#rvw-view-list .rvl__row')]"
        f".find((r) => r.querySelector('.rvl__title').textContent === {name!r})"
    )


def menu(page: Chrome, row: str, action: str) -> None:
    page.evaluate(f"({row}).querySelector('summary').click()")
    page.evaluate(
        f"[...({row}).querySelectorAll('.rvl__menu button')]"
        f".find((b) => b.textContent === {action!r}).click()"
    )


def api(page: Chrome, path: str, method: str = "GET", body: Any = None) -> Any:
    init: dict[str, Any] = {"method": method}
    if method != "GET":
        init.update(headers={"Content-Type": "application/json"}, body=json.dumps(body or {}))
    url = json.dumps("/api/resume" + path)
    return page.evaluate(f"fetch({url}, {json.dumps(init)}).then((r) => r.json())")


# ---------------------------------------------------------------------- Home


def test_opening_resumes_creates_nothing_and_the_master_is_built_on_purpose(
    page: Chrome, empty_server: dict[str, Any]
) -> None:
    page.set_viewport(1366, 900)
    page.navigate(f"{empty_server['url']}/#resume")
    page.wait_for("document.querySelector('.rvw__card') !== null", message="home")
    text = page.evaluate("document.getElementById('page-resume').innerText")
    for words in ("Build from My Profile", "Import PDF or DOCX", "New blank resume"):
        assert words in text, words
    assert "null" not in text
    assert api(page, "/master")["master"] is None  # opening created nothing
    page.evaluate(
        "[...document.querySelectorAll('#page-resume button')]"
        ".find((b) => b.textContent === 'Build from My Profile').click()"
    )
    page.wait_for("document.querySelector('.rvw__explain') !== null", message="the explanation")
    explained = page.evaluate("document.querySelector('.rvw__explain').textContent")
    assert "experience you’ve already confirmed" in explained
    assert api(page, "/master")["master"] is None  # still nothing until confirmed
    page.evaluate("document.getElementById('rvw-build-confirm').click()")
    page.wait_for("document.querySelector('.rvw').dataset.view === 'editor'", message="editor")
    assert api(page, "/master")["master"] is not None
    assert page.console_errors() == []


# --------------------------------------------------------------- My resumes


def test_my_resumes_groups_the_master_imported_and_a_jobs_versions(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    open_list(page, library_server["url"])
    sections = page.evaluate(
        "[...document.querySelectorAll('.rvl__section h2')].map((h) => h.textContent)"
    )
    assert sections == ["Master resume", "Resumes for a job", "Imported and other resumes"]
    names = page.evaluate(
        "[...document.querySelectorAll('.rvl__group .rvl__title')].map((n) => n.textContent)"
    )
    assert names == ["V2", "V1"]  # newest first, the store's own numbers
    # No raw rows: no ids, hashes or internal words on screen.
    text = page.evaluate("document.getElementById('page-resume').innerText")
    for leaked in ("ResumeDocument", "sha256", "TAILORED", "SQLite", "DETERMINISTIC"):
        assert leaked not in text, leaked
    # The Master cannot be archived from its row.
    page.evaluate("document.querySelector('[data-kind=\"MASTER\"] summary').click()")
    master_actions = page.evaluate(
        "[...document.querySelectorAll('[data-kind=\"MASTER\"] .rvl__menu button')]"
        ".map((b) => b.textContent)"
    )
    assert "Archive" not in master_actions


def test_one_preferred_version_said_in_words(page: Chrome, library_server: dict[str, Any]) -> None:
    open_list(page, library_server["url"])
    for name in ("V1", "V2"):
        menu(page, row_of(name), "Set as preferred")
        page.wait_for(f"({row_of(name)})?.querySelector('.rvl__preferred') !== null", message=name)
    marks = page.evaluate(
        "[...document.querySelectorAll('.rvl__preferred')].map((m) => m.textContent)"
    )
    assert marks == ["★ Preferred"]  # a word and a sign, and only one
    assert page.evaluate(f"({row_of('V2')}).querySelector('.rvl__preferred') !== null")


def test_rename_and_archive_ask_and_keep_everything(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    open_list(page, library_server["url"])
    menu(page, row_of(IMPORTED), "Rename")
    page.wait_for("document.querySelector('.rvl__rename .input') === document.activeElement")
    page.evaluate("document.querySelector('.rvl__rename .input').value = 'US version'")
    page.evaluate(
        "document.querySelector('.rvl__rename .input')"
        ".dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'}))"
    )
    page.wait_for(f"{row_of('US version')} !== undefined", message="renamed")
    assert "Renamed" in page.evaluate("document.querySelector('.rvl__status').textContent")
    menu(page, row_of("US version"), "Archive")
    page.wait_for("document.querySelector('.cw-confirm') !== null", message="the question")
    assert page.evaluate("document.activeElement.textContent") == "Cancel"  # the safe answer
    page.evaluate("document.querySelector('.cw-confirm .btn--danger').click()")
    page.wait_for(f"{row_of('US version')} === undefined", message="archived")
    page.evaluate(
        "[...document.querySelectorAll('.rvl__filter')].find((b) => b.textContent === 'Archived')"
        ".click()"
    )
    page.wait_for(f"{row_of('US version')} !== undefined", message="under Archived")
    menu(page, row_of("US version"), "Back to My resumes")
    page.wait_for("document.querySelector('.rvl__status').textContent.includes('Back')")


def test_compare_two_versions_in_words(page: Chrome, library_server: dict[str, Any]) -> None:
    listed = api(page_ready(page, library_server), "/documents")
    v2 = listed["jobs"][0]["versions"][0]
    detail = api(page, f"/documents/{v2['id']}")
    doc = detail["document"]
    doc["experience"][0]["display_title"] = "Lead Automation Engineer"
    body = {"document": doc, "expected_sha256": detail["sha256"]}
    api(page, f"/documents/{v2['id']}/working", "PATCH", body)
    open_list(page, library_server["url"])
    page.evaluate(
        "[...document.querySelectorAll('.rvl__group > .btn')]"
        ".find((b) => b.textContent === 'Compare versions').click()"
    )
    page.wait_for("document.querySelector('.rvl__change') !== null", message="the changes")
    text = page.evaluate("document.querySelector('.rvl__compare').innerText")
    assert "From V1 to V2" in text and "Changed" in text and "Job title shown" in text
    assert "After: Lead Automation Engineer" in text
    assert "{" not in text  # never raw JSON


def page_ready(page: Chrome, server: dict[str, Any]) -> Chrome:
    page.navigate(server["url"])
    page.wait_for("document.readyState === 'complete'")
    return page


def test_restoring_a_version_is_announced(page: Chrome, library_server: dict[str, Any]) -> None:
    listed = api(page_ready(page, library_server), "/documents")
    imported = listed["others"][0]
    detail = api(page, f"/documents/{imported['id']}")
    edited = {**detail["document"], "title": "Edited title"}
    body = {"document": edited, "expected_sha256": detail["sha256"]}
    api(page, f"/documents/{imported['id']}/working", "PATCH", body)
    api(page, f"/documents/{imported['id']}/checkpoint", "POST", {"reason": "MANUAL_CHECKPOINT"})
    open_list(page, library_server["url"])
    menu(page, row_of("Edited title"), "View history")
    page.wait_for("document.querySelector('.rvl__history') !== null", message="history")
    text = page.evaluate("document.querySelector('.rvl__history').innerText")
    assert "Version point" in text and "Created" in text and "Current" in text
    assert "Not downloaded yet." in text
    page.evaluate(
        "[...document.querySelectorAll('.rvl__history button')]"
        ".find((b) => b.textContent === 'Restore').click()"
    )
    page.wait_for("document.querySelector('.rvw').dataset.view === 'editor'", message="editor")
    assert page.evaluate("document.querySelector('.rve__state').textContent") == (
        "Restored, saved as a new version"
    )
    assert page.evaluate("document.querySelector('.rve__state').getAttribute('role')") == "status"


# ------------------------------------------------- the old helper's resumes

FOUND = (
    "(() => { const f = window.fetch; window.__moved = 0; window.fetch = (u, o) => {"
    " const url = String(u);"
    " if (url.endsWith('/api/resume/legacy/migrate')) { window.__moved += 1; }"
    " if (url.endsWith('/api/resume/legacy')) return Promise.resolve(new Response(JSON.stringify("
    "{state: 'FOUND', remaining: 4, contact: true, base_resumes: 1, job_versions: 3, drafts: 1,"
    " exports: 2, unfinished: 1}), {headers: {'Content-Type': 'application/json'}}));"
    " return f(u, o); }; })()"
)


def test_old_resumes_are_offered_never_moved_by_looking(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    page.set_viewport(1366, 900)
    page.navigate(f"{library_server['url']}/#jobs")
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.evaluate(FOUND)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.querySelector('.rvl__legacy') !== null", message="the message")
    assert "We found resumes from the previous version" in page.evaluate(
        "document.querySelector('.rvl__legacy').innerText"
    )
    page.evaluate(
        "[...document.querySelectorAll('.rvl__legacy button')]"
        ".find((b) => b.textContent === 'Review and move them').click()"
    )
    text = page.evaluate("document.querySelector('.rvl__legacy').innerText")
    assert "A backup will be created first." in text
    for line in ("1 base resume", "3 resumes made for a job", "2 downloaded files"):
        assert line in text, line
    assert "candidate.json" not in text and "run.json" not in text
    page.evaluate(
        "[...document.querySelectorAll('.rvl__legacy button')]"
        ".find((b) => b.textContent === 'Cancel').click()"
    )
    page.evaluate(
        "[...document.querySelectorAll('.rvl__legacy button')]"
        ".find((b) => b.textContent === 'Not now').click()"
    )
    page.wait_for("document.querySelector('.rvl__legacy') === null", message="put away")
    assert page.evaluate("window.__moved") == 0  # nothing was asked to move


FAILS = FOUND.replace(
    " if (url.endsWith('/api/resume/legacy/migrate')) { window.__moved += 1; }",
    " if (url.endsWith('/api/resume/legacy/migrate')) { window.__moved += 1;"
    " return Promise.resolve(new Response(JSON.stringify({masters: 1, imported: 0,"
    " job_versions: 2, exports: 1, already: 0, failed: 1,"
    " failures: [{kind: 'run', name: '20260105T000000-eeeeee'}]}),"
    " {headers: {'Content-Type': 'application/json'}})); }",
)


def test_a_partial_move_says_what_stayed_and_never_points_to_the_old_helper(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    """PR 12: the old helper is gone. A move that leaves something behind
    offers to try again, names what stayed (its kind and name only) and says
    the old files were not changed."""
    page.set_viewport(1366, 900)
    page.navigate(f"{library_server['url']}/#jobs")
    page.wait_for("document.querySelector('.topnav__link[data-page=\"resume\"]')")
    page.evaluate(FAILS)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"resume\"]').click()")
    page.wait_for("document.querySelector('.rvl__legacy') !== null", message="the message")
    press = (
        "[...document.querySelectorAll('.rvl__legacy button')]"
        ".find((b) => b.textContent === {!r}).click()"
    )
    page.evaluate(press.format("Review and move them"))
    page.evaluate(press.format("Back up and move"))
    page.wait_for(
        "document.querySelector('.rvl__legacy').innerText.includes('could not be moved')",
        message="the result",
    )
    buttons = page.evaluate(
        "[...document.querySelectorAll('.rvl__legacy button')].map((b) => b.textContent)"
    )
    assert "Try again" in buttons and "Keep original files" in buttons, buttons
    assert not any("Legacy" in b or "Helper" in b for b in buttons), buttons
    text = page.evaluate("document.querySelector('.rvl__legacy').innerText")
    assert "Your old Resume Helper files were not changed." in text
    page.evaluate("document.querySelector('.rvl__details summary').click()")
    details = page.evaluate("document.querySelector('.rvl__details').innerText")
    assert "Resume made for a job: 20260105T000000-eeeeee" in details
    assert "couldn’t safely move" in details
    page.evaluate(press.format("Keep original files"))
    page.wait_for("document.querySelector('.rvl__legacy') === null", message="put away")
    assert page.evaluate("window.__moved") == 1


def test_settings_offers_the_move_only_while_old_resumes_are_left(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    page.navigate(f"{library_server['url']}/#jobs")
    page.wait_for("document.querySelector('.topnav__link[data-page=\"settings\"]')")
    page.evaluate(FOUND)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"settings\"]').click()")
    page.wait_for(
        "!document.getElementById('settings-legacy-host').hidden"
        " && document.querySelector('#settings-legacy-host .rvl__legacy') !== null",
        message="the move offered in Settings",
    )
    buttons = page.evaluate(
        "[...document.querySelectorAll('#settings-legacy-host button')].map((b) => b.textContent)"
    )
    assert buttons == ["Review and move them", "Not now"], buttons
    page.evaluate("document.querySelectorAll('#settings-legacy-host button')[1].click()")
    page.wait_for("document.getElementById('settings-legacy-host').hidden", message="put away")
    assert page.evaluate("window.__moved") == 0


# --------------------------------------------------------- phone, Portuguese


def test_my_resumes_on_a_phone_in_portuguese(page: Chrome, library_server: dict[str, Any]) -> None:
    page.navigate(library_server["url"])
    page.evaluate("window.localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    open_list(page, library_server["url"], width=390)
    with contextlib.suppress(Exception):
        page.evaluate("document.querySelector('.rvl__group .rvl__row summary').click()")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    sections = page.evaluate(
        "[...document.querySelectorAll('.rvl__section h2')].map((h) => h.textContent)"
    )
    assert sections == [
        "Currículo mestre",
        "Currículos para uma vaga",
        "Importados e outros currículos",
    ]
    assert page.evaluate("document.querySelector('.rvl__group .rvl__menu .btn').textContent") == (
        "Marcar como preferida"
    )
    page.evaluate("window.localStorage.removeItem('careerAgent.locale.v1')")


# ------------------------------------------------------ a version for a job


def test_a_version_for_a_job_is_a_copy_made_by_hand(
    page: Chrome, library_server: dict[str, Any]
) -> None:
    page.set_viewport(1440, 960)
    page.navigate(library_server["url"] + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    other = page.evaluate(
        "[...document.querySelectorAll('#list [data-job-id]')]"
        f".map((c) => c.dataset.jobId).find((id) => id !== {library_server['job']!r})"
    )
    page.evaluate(f"document.querySelector('#list [data-job-id=\"{other}\"]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for("document.querySelector('.d-tailor')?.dataset.tailor === 'ready'")
    text = page.evaluate("document.querySelector('.d-tailor').closest('.d-step').innerText")
    assert "Nothing is rewritten automatically." in text
    for claim in ("Optimized", "optimized", "Matched to this job", "AI-tailored"):
        assert claim not in text, claim
    # The copy made by hand is said apart from Tailor, and never claims tailoring.
    manual = page.evaluate("document.getElementById('drawer-open-tailor').textContent")
    assert manual == "Create a version for this job"
    page.evaluate("document.getElementById('drawer-open-tailor').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="editor")
    assert page.evaluate("document.querySelector('.rve .rvw__kind').textContent") == (
        "For a job · V1"
    )
    versions = api(page, f"/jobs/{other}")["versions"]
    assert [v["version_number"] for v in versions] == [1]
    made = api(page, f"/documents/{versions[0]['id']}")["document"]
    master = api(page, "/master")["master"]
    master_doc = api(page, f"/documents/{master['id']}")["document"]
    assert made["experience"] == master_doc["experience"]  # nothing rewritten or chosen
    assert made["provenance"]["created_from"] == "MASTER_COPY"

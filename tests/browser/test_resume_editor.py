"""The Resume Workspace V2 editor, in a real browser (internal page).

A fresh personal database: a synthetic Career Profile (one experience, two
confirmed statements and one still waiting) and the synthetic rich resume.
The editor's pure rules (history, autosave) are exercised in the page too,
through the module the page itself imports.
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
from tests.browser.conftest import DESKTOP, _free_port
from tests.integration.test_resume_master import claim, experience
from tests.support_resume import confirm_cited, rich

from career_agent.domain.enums import ClaimType
from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

CONFIRMED = ["Ran the synthetic data guild.", "Wrote the synthetic runbook."]
WAITING = "A statement still waiting for review."


@pytest.fixture(autouse=True)
def leave_cleanly(page: Chrome) -> Iterator[None]:
    """The editor rightly asks before leaving unsaved edits. A test that stops
    with edits pending must not leave that question blocking the next test."""
    yield
    with contextlib.suppress(Exception):
        page._cdp.call("Page.navigate", {"url": "about:blank"}, timeout=3)
    with contextlib.suppress(Exception):
        page._cdp.call("Page.handleJavaScriptDialog", {"accept": True}, timeout=3)


@pytest.fixture
def editor_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic Work")
        candidate = ensure_candidate(conn)
        conn.execute("UPDATE candidate SET display_name = 'You' WHERE id = ?", (candidate,))
        job = experience(
            conn,
            "Northwind Systems",
            "Principal Automation Engineer",
            "2021-04",
            None,
            current=True,
        )
        claim(conn, "k-guild", ClaimType.EMPLOYMENT, CONFIRMED[0], experience=job)
        claim(conn, "k-runbook", ClaimType.EMPLOYMENT, CONFIRMED[1], experience=job)
        claim(conn, "k-wait", ClaimType.EMPLOYMENT, WAITING, experience=job, verified=False)
        confirm_cited(conn, rich())
    ResumeStore(conn).create_document(rich())
    conn.close()
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
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


FRAME = "[...document.querySelectorAll('.rvp__frame')].find((f) => !f.hasAttribute('data-off'))"
DOC = f"({FRAME}).contentDocument"
STATE = "document.querySelector('.rve__state').dataset.state"


def stored(db: Path) -> Any:
    with connect(db) as conn:
        return ResumeStore(conn).get_document(rich().id)


def open_editor(page: Chrome, url: str, *, width: int = 1600, height: int = 900) -> None:
    page.set_viewport(width, height, mobile=width < 500)
    page.navigate(f"{url}/?debug=resume-v2#resume-v2")
    page.wait_for("document.querySelector('.rvw__card') !== null", message="home")
    page.evaluate("[...document.querySelectorAll('.rvw__tab')][1].click()")
    page.wait_for("document.querySelector('.rvw__row button') !== null", message="my resumes")
    page.evaluate("document.querySelector('.rvw__row button').click()")
    page.wait_for(
        "document.querySelector('.rvp[data-state=\"ready\"]') !== null", message="preview"
    )


def type_into(page: Chrome, ref: str, text: str, *, replace: bool = True) -> None:
    selector = f"[data-ref={json.dumps(ref)}]"
    page.evaluate(
        f"(() => {{ const n = document.querySelector({json.dumps(selector)}); n.focus();"
        f" {'n.select();' if replace else ''} }})()"
    )
    page.type_text(text)


def press(page: Chrome, text: str) -> None:
    page.evaluate(
        "[...document.querySelectorAll('.rve__bar button')]"
        f".find((b) => b.textContent === {text!r}).click()"
    )


def serial(page: Chrome) -> int:
    return int(page.evaluate("Number(document.querySelector('.rvp').dataset.serial)"))


def test_history_undo_then_redo_gives_back_the_edit(page: Chrome, editor_server: dict) -> None:
    page.navigate(f"{editor_server['url']}/?debug=resume-v2#resume-v2")
    page.wait_for("document.querySelector('.rvw') !== null", message="page")
    result = page.evaluate("""(async () => {
      const { createHistory } = await import('/js/resume_editor.js');
      let clock = 0;
      const h = createHistory({ cap: 3, now: () => clock });
      const out = {};
      h.record('start', 'f'); let doc = 'A';
      clock = 1000; h.record(doc, 'f'); doc = 'B';
      doc = h.undo(doc); out.undo = doc;
      doc = h.redo(doc); out.redo = doc;
      clock = 1100; h.record(doc, 'g'); doc = 'C';
      clock = 1200; h.record(doc, 'g'); doc = 'D';   // same burst: one step
      out.coalesced = h.undo(doc);
      h.record('x', 'k'); out.redoAfterEdit = h.canRedo;
      for (let i = 0; i < 10; i += 1) { clock += 1000; h.record(String(i), null); }
      out.capped = h.size;
      return out;
    })()""")
    assert result == {
        "undo": "A",
        "redo": "B",
        "coalesced": "B",
        "redoAfterEdit": False,
        "capped": 3,
    }


def test_autosave_is_single_flight_newest_wins_and_never_overwrites(
    page: Chrome, editor_server: dict
) -> None:
    page.navigate(f"{editor_server['url']}/?debug=resume-v2#resume-v2")
    page.wait_for("document.querySelector('.rvw') !== null", message="page")
    result = page.evaluate("""(async () => {
      const { createAutosave } = await import('/js/resume_editor.js');
      const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
      const out = {};
      // Debounce and single flight: one slow save, edits meanwhile, newest next.
      let calls = [];
      let release;
      const a = createAutosave({ sha: 's0', delay: 20, save: (doc, sha) => {
        calls.push([doc, sha]);
        if (calls.length === 1) return new Promise((r) => { release = () => r('s1'); });
        return Promise.resolve(`s${calls.length}`);
      } });
      a.schedule('d1'); a.schedule('d2'); await sleep(60);
      out.debounced = calls.length;
      a.schedule('d3'); a.schedule('d4'); await sleep(60);
      out.inFlight = calls.length;
      release(); await a.flush();
      out.calls = calls;
      out.state = a.state;
      // A stale copy stops saving and is never retried over the newer one.
      let staleCalls = 0;
      const b = createAutosave({ sha: 'x', delay: 5, retryMs: 5, save: () => {
        staleCalls += 1; return Promise.reject({ status: 409 });
      } });
      b.schedule('mine'); await sleep(40); b.schedule('more'); await sleep(40);
      out.stale = [b.state, staleCalls];
      // A failed save keeps the work and retries.
      let tries = 0;
      const c = createAutosave({ sha: 'y', delay: 5, retryMs: 10, save: (doc) => {
        tries += 1; return tries === 1 ? Promise.reject({ status: 0 }) : Promise.resolve('y2');
      } });
      c.schedule('keep'); await sleep(80);
      out.retry = [c.state, tries, c.sha];
      // Edits the page holds back stay unsaved, never reported as saved.
      const d = createAutosave({ sha: 'z', delay: 5, save: () => Promise.resolve('z2') });
      d.schedule('valid'); d.hold(); await sleep(40);
      out.held = [d.state, d.dirty, await d.flush()];
      return out;
    })()""")
    assert result["debounced"] == 1
    assert result["inFlight"] == 1, "a second save started while the first was in flight"
    assert result["calls"] == [["d2", "s0"], ["d4", "s1"]], "newest copy, chained on the new hash"
    assert result["state"] == "saved"
    assert result["stale"] == ["conflict", 1]
    assert result["retry"] == ["saved", 2, "y2"]
    assert result["held"] == ["invalid", True, False]


def test_typing_previews_saves_and_undoes(page: Chrome, editor_server: dict) -> None:
    open_editor(page, editor_server["url"])
    before = serial(page)
    headline = f"headline/{rich().headline.id}"  # type: ignore[union-attr]
    type_into(page, headline, "Systems integration lead")
    page.wait_for(f"{DOC}.body.innerText.includes('Systems integration lead')", message="preview")
    assert serial(page) > before
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    assert stored(editor_server["db"]).working.headline.text == "Systems integration lead"
    revisions_while_typing = len(_revisions(editor_server["db"]))
    assert revisions_while_typing == 1, "typing wrote a revision"
    press(page, "Undo")
    page.wait_for(f"!{DOC}.body.innerText.includes('Systems integration lead')", message="undone")
    press(page, "Redo")
    page.wait_for(f"{DOC}.body.innerText.includes('Systems integration lead')", message="redone")
    page.wait_for(f"{STATE} === 'saved'", message="saved again")
    assert stored(editor_server["db"]).working.headline.text == "Systems integration lead"


def test_an_evidence_line_keeps_its_provenance_and_evidence_is_untouched(
    page: Chrome, editor_server: dict
) -> None:
    claims_before = _claims(editor_server["db"])
    open_editor(page, editor_server["url"])
    entry = rich().experience[0]
    first, second = entry.bullets[0], entry.bullets[1]
    type_into(page, f"experience/{entry.id}/bullet/{first.id}", "Reworded by the person.")
    page.evaluate(
        f"document.querySelector('[data-ref=\"experience/{entry.id}/bullet/{second.id}\"]')"
        ".closest('li').querySelector('input[type=checkbox]').click()"
    )
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    page.wait_for(
        f"!{DOC}.body.innerText.includes({json.dumps(second.text)})", message="hidden in preview"
    )
    saved = stored(editor_server["db"]).working.experience[0]
    line = saved.bullets[0]
    assert (line.text, line.override, line.original_text) == (
        "Reworded by the person.",
        "EDITED",
        first.text,
    )
    assert line.origin == "EVIDENCE_VERBATIM" and line.evidence_ids == first.evidence_ids
    assert saved.bullets[1].hidden and saved.bullets[1].text == second.text, "hidden, never deleted"
    assert _claims(editor_server["db"]) == claims_before


def test_only_confirmed_evidence_can_be_added(page: Chrome, editor_server: dict) -> None:
    open_editor(page, editor_server["url"])
    entry = rich().experience[0]
    page.evaluate(
        f"document.querySelector('[data-ref=\"experience/{entry.id}\"]').closest('details')"
        ".querySelector('.rve__actions button:nth-child(2)').click()"
    )
    page.wait_for("document.querySelector('.cw-drawer .rve__evtext') !== null", message="drawer")
    texts = page.evaluate(
        "[...document.querySelectorAll('.cw-drawer .rve__evtext')].map((p) => p.textContent)"
    )
    assert sorted(texts) == sorted(CONFIRMED) and WAITING not in texts
    page.evaluate("document.querySelector('.cw-drawer .rve__evlist button').click()")
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    added = stored(editor_server["db"]).working.experience[0].bullets[-1]
    assert added.origin == "EVIDENCE_VERBATIM" and added.evidence_ids[0] in {"k-guild", "k-runbook"}


def test_a_click_in_the_preview_focuses_its_field(page: Chrome, editor_server: dict) -> None:
    open_editor(page, editor_server["url"])
    entry = rich().experience[1]
    ref = f"experience/{entry.id}/bullet/{entry.bullets[0].id}"
    page.evaluate(f"{DOC}.querySelector('[data-ref={json.dumps(ref)}]').click()")
    page.wait_for(f"document.activeElement.dataset.ref === {json.dumps(ref)}", message="focus")


def test_a_template_change_keeps_every_word_and_is_a_version_point(
    page: Chrome, editor_server: dict
) -> None:
    open_editor(page, editor_server["url"])
    # textContent: the words themselves, not how a template styles them.
    words = page.evaluate(f"{DOC}.body.textContent")
    page.evaluate("document.querySelector('.rve__panel').open = true")
    page.evaluate(
        "(() => { const s = document.querySelector('select[aria-label=\"Template\"]');"
        " s.value = 'modern'; s.dispatchEvent(new Event('change', { bubbles: true })); })()"
    )
    h2 = f"getComputedStyle({DOC}.documentElement).getPropertyValue('--h2-size').trim()"
    page.wait_for(f"{h2} === '1.08em'", message="modern")
    assert page.evaluate(f"{DOC}.body.textContent") == words
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    page.evaluate("new Promise((r) => setTimeout(r, 500))")
    # The version BEFORE the change is in the history; the working copy has the new one.
    assert {r.content.design.template for r in _revisions(editor_server["db"])} == {"clean"}
    assert stored(editor_server["db"]).working.design.template == "modern"


def test_a_second_window_is_never_overwritten(page: Chrome, editor_server: dict) -> None:
    open_editor(page, editor_server["url"])
    with connect(editor_server["db"]) as conn:
        store = ResumeStore(conn)
        now = store.get_document(rich().id)
        store.save_working_copy(
            now.id,
            now.working.model_copy(update={"title": "Changed elsewhere"}),
            expected_sha256=now.working_sha256,
        )
    type_into(page, "identity/phone", "+1 555 0199")
    page.wait_for(f"{STATE} === 'conflict'", message="conflict")
    assert stored(editor_server["db"]).title == "Changed elsewhere", (
        "the other window was overwritten"
    )
    page.evaluate("[...document.querySelectorAll('.rve__notice button')][0].click()")
    page.wait_for(
        "document.querySelector('.rve__titleinput').value === 'Changed elsewhere'", message="reload"
    )
    assert page.evaluate(f"{STATE}") == "saved"


def test_leaving_after_edits_writes_one_version_point(page: Chrome, editor_server: dict) -> None:
    open_editor(page, editor_server["url"])
    type_into(page, "identity/phone", "+1 555 0177")
    page.evaluate("[...document.querySelectorAll('.rve__bar button')][0].click()")
    page.wait_for("document.querySelector('.rvw').dataset.view === 'list'", message="back")
    page.evaluate("new Promise((r) => setTimeout(r, 500))")
    reasons = [r.reason for r in _revisions(editor_server["db"])]
    assert reasons == ["CREATED", "MANUAL_CHECKPOINT"]
    assert stored(editor_server["db"]).working.identity.phone == "+1 555 0177"


def test_edits_that_cannot_be_saved_yet_are_never_left_behind(
    page: Chrome, editor_server: dict
) -> None:
    open_editor(page, editor_server["url"])
    # "Add a project" leaves its required name empty: the copy cannot be saved yet.
    page.evaluate("document.querySelector('.rve__set[data-section=\"projects\"] > .btn').click()")
    page.wait_for(f"{STATE} === 'invalid'", message="held")
    page.evaluate("new Promise((r) => setTimeout(r, 900))")
    assert page.evaluate(f"{STATE}") == "invalid", "said Saved over unsaved edits"
    press(page, "Back")
    page.wait_for("document.querySelector('.rve__notice--bad') !== null", message="refused")
    assert page.evaluate("document.querySelector('.rvw').dataset.view") == "editor"
    assert len(stored(editor_server["db"]).working.projects) == 1, "nothing half-saved"
    # Naming the project makes it savable, and leaving works again.
    projects = rich().projects
    page.evaluate(
        "[...document.querySelectorAll('input[data-ref^=\"projects/\"]')]"
        ".find((x) => new RegExp('^projects/[0-9A-Z]{26}$').test(x.dataset.ref)"
        f" && !x.dataset.ref.endsWith({projects[0].id!r})).focus()"
    )
    page.type_text("Second synthetic project")
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    assert len(stored(editor_server["db"]).working.projects) == 2
    press(page, "Back")
    page.wait_for("document.querySelector('.rvw').dataset.view === 'list'", message="left")


def test_a_blank_resume_asks_for_a_name_never_you(page: Chrome, editor_server: dict) -> None:
    page.set_viewport(*DESKTOP)
    page.navigate(f"{editor_server['url']}/?debug=resume-v2#resume-v2")
    page.wait_for(
        "document.querySelector('.rvw__card .btn:not(.btn--primary)') !== null", message="home"
    )
    page.evaluate("document.querySelector('.rvw__card .btn:not(.btn--primary)').click()")
    page.wait_for(
        "document.querySelector('.rvp[data-state=\"ready\"]') !== null", message="preview"
    )
    page.wait_for(
        "document.querySelector('.rve__check').innerText.includes('Add your name')",
        message="finding",
    )
    assert page.evaluate("document.querySelector('[data-ref=\"identity/name\"]').value") == ""
    assert "You" not in page.evaluate(f"{DOC}.body.innerText").split()


@pytest.mark.parametrize(
    ("width", "height"), [(1920, 1080), (1600, 900), (1366, 768), (1200, 900), (390, 844)]
)
def test_the_editor_fits_every_width(
    page: Chrome, editor_server: dict, width: int, height: int
) -> None:
    open_editor(page, editor_server["url"], width=width, height=height)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    if width < 1100:
        assert (
            page.evaluate("getComputedStyle(document.querySelector('.rve__side')).display")
            == "none"
        )
        page.evaluate("[...document.querySelectorAll('.rve__mode')][1].click()")
        page.wait_for(
            "getComputedStyle(document.querySelector('.rve__side')).display !== 'none'",
            message="preview",
        )
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    else:
        assert (
            page.evaluate("getComputedStyle(document.querySelector('.rve__side')).display")
            != "none"
        )
    assert not page.console_errors()
    page.set_viewport(*DESKTOP)


def _revisions(db: Path) -> list[Any]:
    with connect(db) as conn:
        return ResumeStore(conn).list_revisions(rich().id)


def _claims(db: Path) -> list[tuple[Any, ...]]:
    with connect(db) as conn:
        return [tuple(r) for r in conn.execute("SELECT * FROM verified_claim ORDER BY id")]

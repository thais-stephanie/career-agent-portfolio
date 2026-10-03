"""Resume Workspace V2 PR 5 in a real browser: the PDF has the preview's pages,
and the editor's Download finishes saving first.

PREVIEW <-> PDF PARITY. For every synthetic document, template and page size,
the page count the preview's own paginator (`resume_preview.js`, over the
server's `/render`) shows must equal the page count of the PDF the exporter
prints (`print_pdf`, headless Edge). A sweep of growing documents walks
lines and headings across page bottoms. A disagreement is not waived: it
means the paginator and the print CSS read the break rules differently.
"""

from __future__ import annotations

import contextlib
import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import STATE, open_editor, type_into
from tests.support_resume import LONG_BULLET, confirm_cited, exportable, long, rich, sparse

from career_agent.clock import new_id
from career_agent.resume_doc.ats import read_pdf, squash
from career_agent.resume_doc.export import find_browser, print_pdf
from career_agent.resume_doc.models import ResumeDocument, upgrade_resume_document
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

pytestmark = pytest.mark.skipif(
    find_browser() is None, reason="no Microsoft Edge or Chrome: the PDF is NOT verified"
)


@pytest.fixture(autouse=True)
def leave_cleanly(page: Chrome) -> Iterator[None]:
    yield
    with contextlib.suppress(Exception):
        page._cdp.call("Page.navigate", {"url": "about:blank"}, timeout=3)
    with contextlib.suppress(Exception):
        page._cdp.call("Page.handleJavaScriptDialog", {"accept": True}, timeout=3)


@pytest.fixture
def server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
        confirm_cited(conn, exportable())
    ResumeStore(conn).create_document(exportable())
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


#: The preview's page count for a document, by the preview's own code.
PREVIEW_PAGES = """(async (doc) => {
  const { paginate } = await import('/js/resume_preview.js');
  const answer = await fetch('/api/resume/render', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document: doc }),
  }).then((r) => r.json());
  const frame = document.createElement('iframe');
  frame.setAttribute('sandbox', 'allow-same-origin');
  frame.style.cssText = 'position:absolute;left:-5000px;width:900px;height:600px;border:0';
  document.body.append(frame);
  await new Promise((done) => { frame.onload = done; frame.src = answer.url; });
  const result = paginate(frame.contentDocument, answer.page);
  // The first block of every page, by where the paginator put it.
  const root = frame.contentDocument.querySelector('.rv-doc');
  const base = root.getBoundingClientRect().top;
  const firsts = [];
  for (const block of root.querySelectorAll('[data-block]')) {
    const step = result.pageHeight + result.gap;
    const n = Math.floor((block.getBoundingClientRect().top - base + 1) / step);
    if (firsts[n] === undefined) firsts[n] = block.textContent;
  }
  frame.remove();
  return { firsts, breaks: result.breaks };
})"""


def _grown(n: int, **design: Any) -> ResumeDocument:
    """The rich career with `n` more lines on its first role, long and short
    in turn, so page ends fall on lines, entry headers and headings."""
    data = rich(**design).model_dump(mode="json")
    data["experience"][0]["bullets"] += [
        {
            "id": new_id(),
            "text": LONG_BULLET[: 140 + 260 * (k % 3)] if k % 2 else f"Short line {k}.",
            "origin": "USER_AUTHORED",
        }
        for k in range(n)
    ]
    return upgrade_resume_document(data)


def _cases() -> list[tuple[str, ResumeDocument]]:
    cases = []
    for template in ("clean", "modern", "compact"):
        for size in ("A4", "LETTER"):
            design = {"template": template, "page": {"size": size}}
            for name, make in (
                ("sparse", sparse),
                ("rich", rich),
                ("exportable", exportable),
                ("long", long),
            ):
                cases.append((f"{name}/{template}/{size}", make(**design)))
    for n in range(4, 40, 3):
        cases.append((f"grown{n}/clean/A4", _grown(n)))
        cases.append(
            (f"grown{n}/modern/LETTER", _grown(n, template="modern", page={"size": "LETTER"}))
        )
    return cases


def _pdf_pages(case: tuple[ResumeDocument, list[str]]) -> list[str]:
    doc, breaks = case
    data, _ = print_pdf(render_html(doc, mode="print", breaks=frozenset(breaks)).html)
    return [squash(text) for text in read_pdf(data)]


def test_every_pdf_has_exactly_the_preview_pages(page: Chrome, server: dict[str, Any]) -> None:
    cases = dict(_cases())
    page.navigate(server["url"])
    page.wait_for("document.readyState === 'complete'", message="app")
    preview = {
        label: page.evaluate(f"{PREVIEW_PAGES}({json.dumps(doc.model_dump(mode='json'))})")
        for label, doc in cases.items()
    }
    jobs = [(cases[k], preview[k]["breaks"]) for k in cases]
    with ThreadPoolExecutor(max_workers=4) as pool:
        printed = dict(zip(cases, pool.map(_pdf_pages, jobs), strict=True))
    shown = {k: v["firsts"] for k, v in preview.items()}
    differ = {
        k: (len(shown[k]), len(printed[k])) for k in cases if len(shown[k]) != len(printed[k])
    }
    assert not differ, f"preview vs PDF pages: {differ}"
    # Every page starts with the same block in both.
    moved = {
        k: n
        for k in cases
        for n, (first, text) in enumerate(zip(shown[k], printed[k], strict=True))
        if not text.startswith(squash(first)[:60])
    }
    assert not moved, f"a page starts elsewhere in the PDF: {moved}"
    counts = {len(v) for v in shown.values()}
    assert {1, 2} <= counts and max(counts) >= 3, f"1, 2 and 3+ pages, measured: {counts}"


# ---------------------------------------------------------- the editor


BOX = "document.querySelector('.rve__export')"


def download(page: Chrome, fmt: str) -> None:
    page.evaluate(
        "[...document.querySelectorAll('.rve__formats button')]"
        f".find((b) => b.textContent === {fmt!r}).click()"
    )


def _exports(db: Path) -> list[Any]:
    with connect(db) as conn:
        return ResumeStore(conn).list_exports(exportable().id)


def test_download_saves_first_and_makes_the_file_of_what_is_shown(
    page: Chrome, server: dict[str, Any], tmp_path: Path
) -> None:
    page._cdp.call(
        "Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(tmp_path)}
    )
    open_editor(page, server["url"])
    # Typed, then Download at once: well inside the autosave's 600 ms.
    type_into(page, "identity/phone", "+1 555 0142")
    download(page, "PDF")
    page.wait_for(f"{BOX}.dataset.state === 'ready'", timeout=60, message="ready")
    made = _exports(server["db"])
    assert len(made) == 1 and made[0].format == "PDF"
    with connect(server["db"]) as conn:
        store = ResumeStore(conn)
        revision = store.get_revision(made[0].revision_id)
        assert revision.content_sha256 == store.get_document(exportable().id).working_sha256
    assert revision.content.identity.phone == "+1 555 0142", "the file holds the newest edit"
    assert made[0].ats_check["verified"] and made[0].page_count == 1
    pages = next(c for c in made[0].ats_check["checks"] if c["check"] == "PAGES")
    assert pages == {"check": "PAGES", "status": "PASS", "params": {"n": 1, "preview": 1}}
    text = page.evaluate(f"{BOX}.textContent")
    assert "1 page(s), the same as the preview" in text and "%" not in text
    for _ in range(50):
        if (tmp_path / "Morgan_Conceição_Exemplo_Resume.pdf").exists():
            break
        page.evaluate("new Promise((r) => setTimeout(r, 100))")
    assert (tmp_path / "Morgan_Conceição_Exemplo_Resume.pdf").read_bytes().startswith(b"%PDF")


def test_edits_that_cannot_be_saved_refuse_the_download(
    page: Chrome, server: dict[str, Any]
) -> None:
    open_editor(page, server["url"])
    page.evaluate("document.querySelector('.rve__set[data-section=\"projects\"] > .btn').click()")
    page.wait_for(f"{STATE} === 'invalid'", message="held")
    download(page, "DOCX")
    page.wait_for(f"{BOX}.dataset.state === 'failed'", message="refused")
    assert page.evaluate(f"{BOX}.querySelector('[role=alert]') !== null")
    assert _exports(server["db"]) == []


def test_a_change_in_another_window_refuses_the_download(
    page: Chrome, server: dict[str, Any]
) -> None:
    open_editor(page, server["url"])
    with connect(server["db"]) as conn:
        store = ResumeStore(conn)
        now = store.get_document(exportable().id)
        store.save_working_copy(
            now.id,
            now.working.model_copy(update={"title": "Changed elsewhere"}),
            expected_sha256=now.working_sha256,
        )
    type_into(page, "identity/phone", "+1 555 0111")
    download(page, "JSON")
    page.wait_for(f"{BOX}.dataset.state === 'failed'", message="refused")
    assert page.evaluate(f"{STATE}") == "conflict"
    assert _exports(server["db"]) == []


def test_no_name_disables_download_and_says_why(page: Chrome, server: dict[str, Any]) -> None:
    open_editor(page, server["url"])
    type_into(page, "identity/name", " ")
    page.evaluate(
        "(() => { const n = document.querySelector('[data-ref=\"identity/name\"]');"
        " n.value = ''; n.dispatchEvent(new Event('input', { bubbles: true })); })()"
    )
    page.wait_for(
        "[...document.querySelectorAll('.rve__formats button')].every((b) => b.disabled)",
        message="disabled",
    )
    hint = page.evaluate(
        "document.getElementById(document.querySelector('.rve__formats button')"
        ".getAttribute('aria-describedby')).textContent"
    )
    assert hint == "Add your name to download."


def test_evidence_retired_meanwhile_can_be_kept_as_the_persons_own_words(
    page: Chrome, server: dict[str, Any]
) -> None:
    open_editor(page, server["url"])
    key = exportable().experience[0].bullets[0].evidence_ids[0]
    with connect(server["db"]) as conn, transaction(conn):
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import ensure_candidate

        candidate, repo = ensure_candidate(conn), ClaimRepo(conn)
        repo.supersede(candidate, repo.history(candidate, key)[-1].next_revision(verified=False))
    type_into(page, "identity/phone", "+1 555 0123")
    page.wait_for(
        "[...document.querySelectorAll('.rve__notice button')]"
        ".some((b) => b.textContent === 'Keep them as my own words')",
        message="the notice",
    )
    page.evaluate(
        "[...document.querySelectorAll('.rve__notice button')]"
        ".find((b) => b.textContent === 'Keep them as my own words').click()"
    )
    page.wait_for(f"{STATE} === 'saved'", message="saved")
    with connect(server["db"]) as conn:
        line = ResumeStore(conn).get_document(exportable().id).working.experience[0].bullets[0]
    assert line.origin == "USER_AUTHORED" and line.evidence_ids == []

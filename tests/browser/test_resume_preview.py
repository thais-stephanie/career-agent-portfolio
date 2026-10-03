"""The Resume Workspace V2 preview, in a real browser (internal page).

A fresh personal database holding three synthetic documents: a rich two-role
career, a sparse draft and a resume of three pages and more. Everything is
measured inside the preview frame, the way the paginator measures it.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import DESKTOP, _free_port
from tests.support_resume import long, rich, sparse

from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server


@pytest.fixture
def resumes(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
    store = ResumeStore(conn)
    for doc in (rich(), long(), sparse()):
        store.create_document(doc)
    conn.close()
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


FRAME = "[...document.querySelectorAll('.rvp__frame')].find((f) => !f.hasAttribute('data-off'))"
DOC = f"({FRAME}).contentDocument"

#: Every block's page, measured inside the visible frame.
LAYOUT = f"""(() => {{
  const doc = {DOC};
  const root = doc.querySelector('.rv-doc');
  const gap = 24;
  const H = Number(document.querySelector('.rvp').dataset.pageHeight);
  const base = root.getBoundingClientRect().top;
  const pageOf = (y) => Math.floor(y / (H + gap));
  return [...root.querySelectorAll('[data-block]')].map((b) => {{
    const r = b.getBoundingClientRect();
    return {{ ref: b.dataset.ref, keep: b.hasAttribute('data-keep'),
      top: pageOf(r.top - base + 0.5), bottom: pageOf(r.bottom - base - 0.5) }};
  }});
}})()"""


def open_workspace(page: Chrome, server: str, *, width: int = 1600, height: int = 900) -> None:
    page.set_viewport(width, height)
    page.navigate(f"{server}/?debug=resume-v2#resume-v2")
    page.wait_for(
        "document.querySelector('.rvp[data-state=\"ready\"]') !== null", message="preview"
    )


def choose(page: Chrome, title: str) -> None:
    serial = page.evaluate("Number(document.querySelector('.rvp').dataset.serial)")
    page.evaluate(
        "(() => { const s = document.getElementById('rvw-doc');"
        f" s.value = [...s.options].find((o) => o.text === {title!r}).value;"
        " s.dispatchEvent(new Event('change')); })()"
    )
    page.wait_for(
        f"Number(document.querySelector('.rvp').dataset.serial) > {serial}", message="re-render"
    )


def test_a_long_resume_is_three_pages_and_breaks_like_print(page: Chrome, resumes: str) -> None:
    open_workspace(page, resumes)
    choose(page, "Long")
    assert int(page.evaluate("document.querySelector('.rvp').dataset.pages")) >= 3
    assert page.evaluate("document.querySelector('.rvp').dataset.overflow") == "0"
    blocks = page.evaluate(LAYOUT)
    assert all(b["top"] == b["bottom"] for b in blocks), "a block split across pages"
    for block, after in zip(blocks, blocks[1:], strict=False):
        if block["keep"]:
            assert block["bottom"] == after["top"], f"{block['ref']} left alone at a page end"
    assert "pages" in page.evaluate("document.querySelector('.rvp__status').textContent")


def test_letter_and_a4_are_their_real_sizes(page: Chrome, resumes: str) -> None:
    open_workspace(page, resumes)
    width_a4 = page.evaluate(f"({FRAME}).style.width")
    page.evaluate(
        "(() => { const s = document.getElementById('rvw-page'); s.value = 'LETTER';"
        " s.dispatchEvent(new Event('change')); })()"
    )
    page.wait_for(f"({FRAME}).style.width !== {width_a4!r}", message="letter")
    assert abs(float(width_a4[:-2]) - 210 * 96 / 25.4) < 1
    assert abs(float(page.evaluate(f"({FRAME}).style.width")[:-2]) - 215.9 * 96 / 25.4) < 1
    # The paper never scrolls inside itself, and nothing is cut off: the
    # frame is as big as the document.
    assert page.evaluate(
        f"(() => {{ const f = {FRAME}; const d = f.contentDocument.documentElement;"
        " return d.scrollHeight <= f.clientHeight + 1 && d.scrollWidth <= f.clientWidth + 1; })()"
    )


def test_the_latest_render_wins_and_a_template_keeps_the_words(page: Chrome, resumes: str) -> None:
    open_workspace(page, resumes)
    before = page.evaluate(f"{DOC}.body.innerText")
    # The first request (modern) is held back; the second (compact) answers first.
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) => {"
        " const slow = String(url).endsWith('/resume/render') && opts.body.includes('\"modern\"');"
        " return slow ? new Promise((r) => setTimeout(() => r(real(url, opts)), 800))"
        " : real(url, opts); }; })()"
    )
    for value in ("modern", "compact"):
        page.evaluate(
            "(() => { const s = document.getElementById('rvw-template');"
            f" s.value = '{value}'; s.dispatchEvent(new Event('change')); }})()"
        )
    compact = (
        f"getComputedStyle({DOC}.documentElement).getPropertyValue('--h2-size').trim() === '0.88em'"
    )
    page.wait_for(compact, message="compact")
    page.evaluate("new Promise((r) => setTimeout(r, 1200))")
    assert page.evaluate(compact), "an old render won"
    assert page.evaluate(f"{DOC}.body.innerText") == before


def test_a_click_names_the_object_and_nothing_is_fetched(page: Chrome, resumes: str) -> None:
    open_workspace(page, resumes)
    ref = page.evaluate(f"{DOC}.querySelector('li[data-ref]').dataset.ref")
    assert "/bullet/" in ref
    page.evaluate(f"{DOC}.querySelector('li[data-ref]').click()")
    page.wait_for(
        f"document.querySelector('.rvw__clicked').textContent.includes({ref!r})", message="ref"
    )
    assert page.evaluate(f"{DOC}.defaultView.performance.getEntriesByType('resource').length") == 0
    assert page.evaluate(f"{DOC}.querySelectorAll('script, a[href]').length") == 0
    assert not page.console_errors()


@pytest.mark.parametrize("width", [1920, 1600, 1366, 1200, 390])
def test_no_page_scrolls_sideways(page: Chrome, resumes: str, width: int) -> None:
    open_workspace(page, resumes, width=width, height=900 if width > 400 else 844)
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert page.evaluate(
        "document.querySelector('.rvp__paper').getBoundingClientRect().width"
        " <= document.querySelector('.rvp__stage').clientWidth"
    )
    page.set_viewport(*DESKTOP)

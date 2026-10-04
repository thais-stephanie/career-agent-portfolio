"""Resume Workspace V2 PR 6: import in the browser.

Pick a file, review what was found (uncertain values say "Check this" in
words, with the line they came from), correct, save, and the saved document
opens in the existing Editor. Nothing is stored before Save; Cancel stores
nothing; an unreadable PDF says why. Synthetic files only.
"""

from __future__ import annotations

import base64
import contextlib
import io
import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_resume_editor import type_into
from tests.support_import import AMBIGUOUS, SIMPLE, docx

from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

STEP = "document.querySelector('.rvi') && document.querySelector('.rvi').dataset.step"


@pytest.fixture(autouse=True)
def leave_cleanly(page: Chrome) -> Iterator[None]:
    yield
    with contextlib.suppress(Exception):
        page._cdp.call("Page.navigate", {"url": "about:blank"}, timeout=3)


@pytest.fixture
def server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
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


def documents(db: Path) -> int:
    with connect(db) as conn:
        return len(ResumeStore(conn).list_documents(include_archived=True))


def start(page: Chrome, url: str, *, width: int = 1600, height: int = 900) -> None:
    page.set_viewport(width, height, mobile=width < 500)
    page.navigate(f"{url}/?debug=resume-v2#resume-v2")
    page.wait_for("document.querySelector('.rvw__card') !== null", message="home")
    click(page, "Import PDF or DOCX")
    page.wait_for(f"{STEP} === 'pick'", message="pick a file")


def click(page: Chrome, text: str) -> None:
    page.evaluate(
        f"[...document.querySelectorAll('button')].find((b) => b.textContent === {text!r}).click()"
    )


def upload(page: Chrome, data: bytes, name: str) -> None:
    """Hand the file input a file, as choosing one in the dialog does."""
    page.evaluate(
        f"""(() => {{
          const bytes = Uint8Array.from(atob({json.dumps(base64.b64encode(data).decode())}),
            (c) => c.charCodeAt(0));
          const list = new DataTransfer();
          list.items.add(new File([bytes], {json.dumps(name)}));
          const input = document.getElementById('rvi-file');
          input.files = list.files;
          input.dispatchEvent(new Event('change', {{ bubbles: true }}));
        }})()"""
    )


def test_review_then_save_then_the_editor(page: Chrome, server: dict[str, Any]) -> None:
    start(page, server["url"])
    upload(page, docx(AMBIGUOUS), "ambiguous.docx")
    page.wait_for(f"{STEP} === 'review'", message="review")
    # Uncertain values say so in words, with where they came from.
    name = page.evaluate(
        "(() => { const f = document.querySelector('[data-ref=\"identity/name\"]')"
        ".closest('.rvi__field'); return { conf: f.dataset.confidence,"
        " about: f.querySelector('.rvi__about').textContent }; })()"
    )
    assert name["conf"] == "LOW"
    assert "Check this" in name["about"] and 'Use "Jamie Sample"' in name["about"]
    assert "From paragraph 1" in name["about"]
    assert page.evaluate("document.activeElement.dataset.ref") == "identity/name", "first to check"
    assert documents(server["db"]) == 0, "reading saved nothing"
    type_into(page, "identity/name", "Taylor Q. Sample")
    click(page, "Save")
    page.wait_for(f"{STEP} === 'saved'", message="saved")
    assert documents(server["db"]) == 1
    click(page, "Open in the editor")
    page.wait_for("document.querySelector('.rvp[data-state=\"ready\"]') !== null", message="editor")
    value = page.evaluate("document.querySelector('[data-ref=\"identity/name\"]').value")
    assert value == "Taylor Q. Sample", "the reviewed value, in the existing Editor"


def test_cancel_and_an_empty_required_field_save_nothing(
    page: Chrome, server: dict[str, Any]
) -> None:
    start(page, server["url"])
    upload(page, docx(SIMPLE), "simple.docx")
    page.wait_for(f"{STEP} === 'review'", message="review")
    ref = page.evaluate(
        'document.querySelector(\'[data-section="experience"] [data-ref$="/org"]\').dataset.ref'
    )
    field = f"document.querySelector('[data-ref={json.dumps(ref)}]')"
    page.evaluate(
        f"(() => {{ const n = {field}; n.value = '';"
        " n.dispatchEvent(new Event('input', { bubbles: true })); })()"
    )
    click(page, "Save")
    page.wait_for(f"{field}.getAttribute('aria-invalid') === 'true'", message="named")
    assert page.evaluate("document.activeElement.dataset.ref") == ref
    assert documents(server["db"]) == 0
    click(page, "Cancel")
    page.wait_for("document.querySelector('.rvw').dataset.view === 'home'", message="home")
    assert documents(server["db"]) == 0


def test_an_unreadable_pdf_says_why_and_offers_ways_on(
    page: Chrome, server: dict[str, Any]
) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=595, height=842)
    out = io.BytesIO()
    writer.write(out)
    start(page, server["url"])
    upload(page, out.getvalue(), "scan.pdf")
    page.wait_for("document.querySelector('.rvi__alert p') !== null", message="refusal")
    text = page.evaluate("document.querySelector('.rvi__alert').textContent")
    assert "read text from this PDF" in text and "DOCX" in text
    assert documents(server["db"]) == 0


def test_review_fits_a_phone(page: Chrome, server: dict[str, Any]) -> None:
    start(page, server["url"], width=390, height=844)
    upload(page, docx(AMBIGUOUS), "ambiguous.docx")
    page.wait_for(f"{STEP} === 'review'", message="review")
    page.evaluate("document.querySelector('.rvi__raw').open = true")
    widths = page.evaluate(
        "({ page: document.documentElement.scrollWidth, view: window.innerWidth })"
    )
    assert widths["page"] <= widths["view"], widths

"""Export GOOD + STRONG from Table mode: offered there only, and the file
the browser saves holds every GOOD and STRONG posting of the list."""

from __future__ import annotations

from tests.browser.chrome import Chrome

WAIT_LIST = "document.querySelectorAll('[data-job-id]').length > 0"


def test_the_export_is_offered_in_table_mode_only(page: Chrome, pristine_server: str) -> None:
    page.navigate(f"{pristine_server}/?view=cards")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for(WAIT_LIST, message="the list")
    assert page.evaluate("document.getElementById('export-good-strong').hidden")
    page.evaluate("document.getElementById('view-table').click()")
    page.wait_for("!document.getElementById('export-good-strong').hidden", message="the export")
    assert page.evaluate("document.getElementById('export-good-strong').textContent") == (
        "Export GOOD + STRONG"
    )
    page.evaluate("document.getElementById('view-kanban').click()")
    page.wait_for("document.getElementById('export-good-strong').hidden", message="hidden again")


def test_the_saved_file_holds_every_good_and_strong_posting(
    page: Chrome, pristine_server: str
) -> None:
    page.navigate(f"{pristine_server}/?view=table")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("!document.getElementById('export-good-strong').hidden", message="the export")
    # Capture what the page hands the browser to save, instead of a download.
    page.evaluate(
        "window.__saved = null;"
        "const make = URL.createObjectURL;"
        "URL.createObjectURL = (blob) => { blob.arrayBuffer().then((b) => {"
        " window.__bom = Array.from(new Uint8Array(b).slice(0, 3));"
        " window.__saved = new TextDecoder('utf-8', { ignoreBOM: true }).decode(b); });"
        " return make.call(URL, blob); };"
        "HTMLAnchorElement.prototype.click = function () { window.__name = this.download; };"
    )
    page.evaluate("document.getElementById('export-good-strong').click()")
    page.wait_for("typeof window.__saved === 'string'", message="the saved file")
    expected = page.evaluate(
        "fetch('/api/jobs?' + new URLSearchParams([['fit_band','GOOD'],['fit_band','STRONG']]))"
        ".then(r => r.json()).then(p => p.total)"
    )
    text = str(page.evaluate("window.__saved"))
    assert text.startswith("﻿" + "Search Fit band,")
    import csv
    import io

    rows = list(csv.DictReader(io.StringIO(text[1:])))
    assert len(rows) == int(expected)
    assert {r["Search Fit band"] for r in rows} <= {"GOOD", "STRONG"}
    assert str(page.evaluate("window.__name")).startswith("career-agent-good-strong-")
    page.wait_for(
        f"document.body.innerText.includes('Saved {len(rows)} GOOD and STRONG postings')",
        message="the confirmation",
    )

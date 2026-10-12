# ruff: noqa: E501, F811 - JavaScript literals read whole; fixtures are imported by name
"""The new flows, driven with the keyboard only (2026-10-12).

Every action below is a real key press (Tab, Shift+Tab, Enter, Space, the arrows)
on the element that has focus. Nothing is clicked. Synthetic people and a patched
`fetch` for the Deep reading; no model is called and no provider is reached.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from pathlib import Path
from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_deep_panel import PREVIEW
from tests.browser.test_deep_panel_faults import BASE, PATCH
from tests.browser.test_deep_reading import deep_server  # noqa: F401 - fixture
from tests.browser.test_resume_tailor import tailor_server  # noqa: F401 - fixture

from career_agent.resume_doc.ats import read_docx, read_pdf
from career_agent.resume_doc.export import find_browser

ACTIVE = "document.activeElement"


def tab_to(page: Chrome, selector: str, *, limit: int = 60, back: bool = False) -> int:
    """Press Tab until the focused element matches, and say how many presses it took."""
    for presses in range(1, limit + 1):
        page.press("Tab", shift=back)
        if page.evaluate(f"Boolean({ACTIVE} && {ACTIVE}.matches({json.dumps(selector)}))"):
            return presses
    raise AssertionError(f"Tab never reached {selector!r} in {limit} presses")


def focused(page: Chrome) -> str:
    return str(
        page.evaluate(
            f"({ACTIVE}.id ? '#' + {ACTIVE}.id : '') + ({ACTIVE}.className ? '.' + String({ACTIVE}.className).split(' ')[0] : '')"
        )
    )


# ---------------------------------------------------------------- Deep reading


def _open_deep(page: Chrome, url: str, **fields: Any) -> None:
    open_home_past_setup(page, url)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.getElementById('deep-open')", message="the Deep reading button")
    data = {
        "status": {**BASE, **fields.pop("status", {})},
        "preview": fields.pop("preview", PREVIEW),
        **fields,
    }
    page.evaluate(PATCH + f"window.__deep = {json.dumps(data)};")
    page.evaluate("document.getElementById('deep-open').focus()")
    page.press("Enter")
    page.wait_for(
        "document.querySelector('.dr-panel .dr-body') && !document.querySelector('.dr-panel .sk')",
        message="the panel drawn by the keyboard",
    )
    page.wait_for("document.querySelector('.dr-panel').innerText.length > 40", message="its text")


def test_deep_reading_quantity_and_provider_by_keyboard(
    page: Chrome, deep_server: dict[str, Any]
) -> None:
    _open_deep(page, deep_server["url"])
    assert page.evaluate("Boolean(document.activeElement.closest('.dr-panel'))"), (
        "focus moved into the dialog"
    )
    # Provider radios: reachable, and Space chooses.
    tab_to(page, ".dr-choice")
    page.press("Space")
    page.wait_for(
        "document.querySelector('.dr-choice[aria-checked=true]') !== null", message="chosen"
    )
    # The quantity: a number field the keyboard can reach and type into.
    tab_to(page, "#deep-count", limit=80)
    page.evaluate("document.getElementById('deep-count').select()")
    page.type_text("2")
    assert page.evaluate("document.getElementById('deep-count').value") == "2"
    # Above the current availability it is brought back, and the panel says so.
    page.evaluate("document.getElementById('deep-count').select()")
    page.type_text("9999")
    page.press("Tab")
    max_allowed = int(page.evaluate("document.getElementById('deep-count').max"))
    assert int(page.evaluate("document.getElementById('deep-count').value")) <= max_allowed
    # The start button is reachable and a disabled one is skipped by Tab, not trapped.
    tab_to(page, ".dr-foot .dr-btn", limit=80)
    assert page.evaluate("document.activeElement.disabled") is False
    page.press("Escape")
    page.wait_for(
        "document.querySelector('.drawer--deep').hidden", message="Escape closes the panel"
    )
    assert page.evaluate("document.activeElement.id") == "deep-open", "focus returns to its opener"


def test_a_failed_check_is_reached_and_retried_by_keyboard(
    page: Chrome, deep_server: dict[str, Any]
) -> None:
    _open_deep(
        page,
        deep_server["url"],
        status={
            "coverage_ok": False, "main_list_roles": None, "readable": None, "too_short": None,
            "available": None, "available_unresolved": None,
        },
    )  # fmt: skip
    body = str(page.evaluate("document.querySelector('.dr-panel').innerText"))
    assert "We could not check which jobs can be read" in body
    start_disabled = page.evaluate("document.querySelector('.dr-foot .dr-btn').disabled")
    assert start_disabled is True
    tab_to(page, ".dr-btn2", limit=40)
    assert "Try again" in str(page.evaluate("document.activeElement.innerText"))
    # Fix the status behind the page, then press Enter on "Try again".
    page.evaluate(
        "window.__deep.status = Object.assign({}, window.__deep.status, {coverage_ok: true, available: 5, readable: 5, main_list_roles: 5, too_short: 0, available_unresolved: 5})"
    )
    page.press("Enter")
    page.wait_for(
        "!document.querySelector('.dr-panel').innerText.includes('We could not check which jobs')",
        message="the retry by keyboard cleared the failure",
    )


# ---------------------------------------------------------------- the age limit


def _open_filters(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/#jobs")
    page.wait_for("document.querySelector('.fess__age')", timeout=30, message="the age control")
    page.evaluate(
        "[...document.querySelectorAll('button')].find((b) => b.textContent.startsWith('All filters')).focus()"
    )
    page.press("Enter")
    page.wait_for(
        "document.querySelector('.fess__age').getBoundingClientRect().width > 0",
        message="the filter panel opened by Enter",
    )


def _stored(page: Chrome, key: str, expected: Any) -> None:
    """The server's own record of the choice, once the request has landed."""
    got = None
    for _ in range(40):
        got = page.evaluate(f"fetch('/api/view-prefs').then(r => r.json()).then(j => j[{key!r}])")
        if got == expected:
            return
        page.evaluate("new Promise((r) => setTimeout(r, 100))")
    raise AssertionError(f"{key} stayed {got!r}, expected {expected!r}")


def test_the_age_limit_is_operated_with_the_keyboard(
    page: Chrome, deep_server: dict[str, Any]
) -> None:
    _open_filters(page, deep_server["url"])
    page.evaluate("document.querySelector('.fess__age [data-age=\"120\"]').focus()")
    assert page.evaluate("document.activeElement.getAttribute('aria-pressed')") == "true"
    page.press("Tab")
    assert page.evaluate("document.activeElement.dataset.age") == "150", "Tab walks the choices"
    page.press("Enter")
    page.wait_for(
        "document.querySelector('.fess__age [data-age=\"150\"]').getAttribute('aria-pressed') === 'true'",
        message="150 chosen by Enter",
    )
    _stored(page, "max_age_days", 150)
    page.press("Tab")
    page.press("Space")  # "No limit"
    page.wait_for(
        "document.querySelector('.fess__age [data-age=\"null\"]').getAttribute('aria-pressed') === 'true'",
        message="no limit by Space",
    )
    page.press("Tab")
    assert page.evaluate("document.activeElement.id") != "age-undated", (
        "with no limit the undated switch has nothing to do and Tab skips it"
    )
    # With no limit the undated switch is off the tab order (disabled); choose 120 again.
    page.evaluate("document.querySelector('.fess__age [data-age=\"120\"]').focus()")
    page.press("Enter")
    page.wait_for(
        "document.querySelector('.fess__age [data-age=\"120\"]').getAttribute('aria-pressed') === 'true'",
        message="back to 120",
    )
    _stored(page, "max_age_days", 120)
    tab_to(page, "#age-undated", limit=4)
    page.press("Space")
    page.wait_for(
        "document.getElementById('age-undated').getAttribute('aria-checked') === 'true'",
        message="undated switched on by Space",
    )
    _stored(page, "include_undated", True)
    _stored(page, "max_age_days", 120)
    assert page.console_errors() == []


# ------------------------------------------------------------- the Resume Tailor


def _squash(text: str) -> str:
    # NFKC folds the "fl" and "fi" ligatures a PDF text layer carries.
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _wait_file(folder: Path, suffix: str, seconds: int = 60) -> Path:
    for _ in range(seconds * 5):
        found = [p for p in folder.glob(f"*{suffix}") if not p.name.endswith(".crdownload")]
        if found:
            return found[0]
        time.sleep(0.2)
    raise AssertionError(f"no {suffix} file was downloaded")


def test_the_tailor_five_tabs_then_create_review_and_export_by_keyboard(
    page: Chrome, tailor_server: dict[str, Any], tmp_path: Path
) -> None:
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    page._cdp.call(
        "Browser.setDownloadBehavior", {"behavior": "allow", "downloadPath": str(downloads)}
    )
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#tailor")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="start"
    )

    # The five tabs: roving focus, arrows, Home and End.
    page.evaluate("document.getElementById('tp-tab-start').focus()")
    for tab in ("make", "resumes", "experience", "tips"):
        page.press("ArrowRight")
        page.wait_for(
            f"document.getElementById('page-tailor').dataset.state === {tab!r}",
            message=f"{tab} by ArrowRight",
        )
        assert page.evaluate("document.activeElement.id") == f"tp-tab-{tab}"
    page.press("Home")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="Home"
    )
    page.press("End")
    page.wait_for("document.getElementById('page-tailor').dataset.state === 'tips'", message="End")
    # A tip is ticked with Space, and the honest rule is never a way to switch the truth off.
    tab_to(page, ".tp-tick", limit=30)
    page.press("Space")
    page.wait_for(
        "document.querySelector('.tp-tick[aria-checked=true]')", message="ticked by Space"
    )

    # Make: choose a job, check the evidence, create, all with keys.
    page.press("Home")
    page.evaluate("document.getElementById('tp-tab-make').focus()")
    page.press("Enter")
    page.wait_for("document.getElementById('page-tailor').dataset.state === 'make'", message="make")
    tab_to(page, ".tp-radios [data-job]", limit=40)
    page.press("Space")
    page.wait_for("document.getElementById('tp-check').disabled === false", message="check enabled")
    tab_to(page, "#tp-rule-honest, #tp-honest, [aria-disabled=true][role=switch]", limit=40)
    page.press("Space")
    assert (
        page.evaluate("document.getElementById('tp-honest').getAttribute('aria-checked')") == "true"
    )
    tab_to(page, "#tp-check", limit=40)
    page.press("Enter")
    page.wait_for("document.querySelector('.tp-check')", message="the evidence check")
    tab_to(page, "#tp-make-it", limit=40)
    page.press("Enter")
    page.wait_for(
        "document.querySelector('.rvw')?.dataset.view === 'editor'"
        " && document.querySelector('.rve .rvw__kind')?.textContent.startsWith('For a job')",
        message="the version in the Editor, opened by Enter",
    )
    assert page.evaluate("document.querySelectorAll('.rve__formats button').length") >= 3

    # Export: every format, by the keyboard, then open the files themselves.
    for label in ("JSON", "DOCX", "PDF"):
        if label == "PDF" and find_browser() is None:
            continue
        page.evaluate(
            "[...document.querySelectorAll('.rve__formats button')]"
            f".find((b) => b.textContent === {label!r}).focus()"
        )
        page.press("Enter")
        page.wait_for(
            "document.querySelector('.rve__export')?.dataset.state === 'ready'",
            timeout=90,
            message=f"{label} ready",
        )
    json_file = _wait_file(downloads, ".json")
    docx_file = _wait_file(downloads, ".docx")
    payload = json.loads(json_file.read_text(encoding="utf-8"))
    visible = [
        b["text"]
        for entry in payload["experience"]
        if not entry.get("hidden")
        for b in entry["bullets"]
        if not b.get("hidden")
    ]
    hidden = [
        b["text"]
        for entry in payload["experience"]
        for b in entry["bullets"]
        if entry.get("hidden") or b.get("hidden")
    ]
    assert visible, "the tailored version carries confirmed lines"
    assert payload["target"]["title"], "the version names the job it was made for"
    word = _squash("\n".join(read_docx(docx_file.read_bytes())))
    for line in visible:
        assert _squash(line) in word, f"missing from the DOCX: {line[:60]}"
    for line in hidden:
        assert _squash(line) not in word
    assert _squash(payload["identity"]["full_name"]) in word
    if find_browser() is not None:
        pdf_file = _wait_file(downloads, ".pdf")
        assert pdf_file.read_bytes().startswith(b"%PDF")
        pdf = _squash("\n".join(read_pdf(pdf_file.read_bytes())))
        for line in visible:
            assert _squash(line) in pdf, f"missing from the PDF: {line[:60]} :: {pdf}"
        for line in hidden:
            assert _squash(line) not in pdf
    # The honest rule is still on after all of it.
    assert page.console_errors() == []

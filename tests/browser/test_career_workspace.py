"""The career workspace in the browser: Profile, Evidence and Documents.

Every journey here is one a person takes: import a resume and review it
experience by experience, settle a date the documents disagree on, leave a
line for later and come back, put an import away and bring it back, open a
project, add one by hand, edit an experience, all with the keyboard, on a
phone and in Portuguese. Each runs on a fresh synthetic workspace in a
temporary directory.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_career_evidence import _serve
from tests.support_cv import load_cv

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

LOCALE_KEY = "careerAgent.locale.v1"
CV_NAME = "Riley_Invented_CV.md"


@dataclass
class Workspace:
    base: str
    db: Path
    api: JobsApi


def _call(api: JobsApi, method: str, path: str, body: dict | None = None) -> dict:
    return dict(api.handle_api(method, path, {}, body or {}))


def _upload(api: JobsApi, text: str, name: str) -> str:
    raw = base64.b64encode(text.encode()).decode()
    return str(
        _call(api, "POST", "/api/cv/import", {"filename": name, "content_base64": raw})["import_id"]
    )


def _fresh(tmp_path: Path, committed_config: Path) -> tuple[Path, JobsApi]:
    db = tmp_path / "workspace.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
    finally:
        conn.close()
    return db, JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=0), quiet=True)


@pytest.fixture
def empty(tmp_path: Path, committed_config: Path) -> Iterator[Workspace]:
    db, api = _fresh(tmp_path, committed_config)
    for base in _serve(db, committed_config):
        yield Workspace(base, db, api)


@pytest.fixture
def placed(tmp_path: Path, committed_config: Path) -> Iterator[Workspace]:
    """Three experiences in the profile from a first CV, two lines confirmed
    in each, then a second CV that moves one start date."""
    db, api = _fresh(tmp_path, committed_config)
    first = _upload(api, load_cv("markdown_complex.md"), CV_NAME)
    model = _call(api, "GET", f"/api/documents/cv/{first}")
    for entry in model["experiences"][:3]:
        _call(
            api,
            "POST",
            f"/api/documents/cv/{first}/place",
            {"entry": entry["key"], "choice": "new"},
        )
        for item in entry["items"][:2]:
            _call(
                api,
                "POST",
                f"/api/documents/cv/{first}/answer",
                {"key": item["key"], "answer": "CONFIRM"},
            )
    experiences = _call(api, "GET", "/api/career")["experiences"]
    _call(
        api,
        "POST",
        "/api/evidence",
        {
            "claim_type": "PROJECT",
            "text": "Billing Automation: Built an automated billing workflow for the finance team.",
            "tools": ["Stripe", "Python"],
            "period_start": "2025-03",
            "experience_id": experiences[0]["id"],
        },
    )
    changed = load_cv("markdown_complex.md").replace("Jan 2020", "Mar 2020", 1)
    _upload(api, changed, "Riley_CV_update.md")
    for base in _serve(db, committed_config):
        yield Workspace(base, db, api)


# =========================================================================
# helpers
# =========================================================================


def _nav(page: Chrome, name: str) -> None:
    page.wait_for(f"document.querySelector('.topnav__link[data-page=\"{name}\"]') !== null")
    page.evaluate(f"document.querySelector('.topnav__link[data-page=\"{name}\"]').click()")
    page.wait_for(f"!document.querySelector('#page-{name}').hidden")


def _open(page: Chrome, base: str, name: str, locale: str = "en") -> None:
    page.navigate(base)
    page.evaluate(f"localStorage.setItem({json.dumps(LOCALE_KEY)}, {json.dumps(locale)})")
    page.navigate(base)
    if page.evaluate("Boolean(document.querySelector('#setup-later'))"):
        page.evaluate("document.querySelector('#setup-later').click()")
    _nav(page, name)


def _press(page: Chrome, text: str, scope: str = "body") -> None:
    """Press the visible button a person would press: by its words."""
    found = page.evaluate(
        f"(() => {{ const b = [...document.querySelectorAll({json.dumps(scope + ' button')})]"
        f".find((n) => n.offsetParent !== null && n.textContent.trim() === {json.dumps(text)});"
        " if (b) { b.click(); return true; } return false; })()"
    )
    assert found, f"no visible button {text!r} in {scope}"


def _text(page: Chrome, selector: str) -> str:
    return str(page.evaluate(f"document.querySelector({json.dumps(selector)})?.textContent || ''"))


def _choose_file(page: Chrome, text: str, name: str) -> None:
    page.evaluate(
        "(() => { const box = new DataTransfer();"
        f" box.items.add(new File([{json.dumps(text)}], {json.dumps(name)},"
        " { type: 'text/plain' }));"
        " const input = document.getElementById('docs-file'); input.files = box.files;"
        " input.dispatchEvent(new Event('change', { bubbles: true })); })()"
    )


def _is(key: str, state: str) -> str:
    """A predicate: the line `key` in the review now reads as `state`."""
    found = f".imp-item[data-key={json.dumps(key)}]"
    return f"document.querySelector({json.dumps(found)})?.dataset.state === {json.dumps(state)}"


def _own_errors(page: Chrome, base: str) -> list[dict[str, str]]:
    """Console errors from this workspace, not from a server an earlier test closed."""
    return [
        e
        for e in page.console_errors()
        if not (e.get("source") == "network" and not str(e.get("url", "")).startswith(base))
    ]


def _verified(db: Path) -> int:
    conn = connect(db)
    try:
        return int(
            conn.execute("SELECT COUNT(*) FROM verified_claim WHERE verified=1").fetchone()[0]
        )
    finally:
        conn.close()


def _open_update_review(page: Chrome, ws: Workspace) -> None:
    _open(page, ws.base, "documents")
    page.wait_for("document.querySelector('.docs-card .btn--primary')")
    page.evaluate(
        "[...document.querySelectorAll('.docs-card')]"
        ".find((c) => c.textContent.includes('Riley_CV_update'))"
        ".querySelector('.btn--primary').click()"
    )
    page.wait_for("document.querySelector('.imp-card')", message="the import review")


# =========================================================================
# A, B, J, K: a fresh import, reviewed one experience and one line at a time
# =========================================================================


def test_a_fresh_resume_is_reviewed_experience_by_experience(
    page: Chrome, empty: Workspace
) -> None:
    _open(page, empty.base, "documents")
    page.wait_for("document.querySelector('#docs-file')")
    _choose_file(page, load_cv("markdown_complex.md"), CV_NAME)
    page.wait_for(
        "document.querySelector('.imp-card')", message="the review opens on the first experience"
    )

    assert "Experience 1 of" in _text(page, ".imp-counter")
    assert "New" in _text(page, ".imp-banner")
    rail = page.evaluate("document.querySelectorAll('.imp-rail__xp').length")
    assert rail >= 3, "the rail lists every experience the resume describes"
    # Nothing is confirmed by reading, and there is no way to confirm everything.
    assert _verified(empty.db) == 0
    labels = page.evaluate(
        "[...document.querySelectorAll('button')].map((b) => b.textContent.trim())"
    )
    assert not [
        label for label in labels if "all" in label.lower() and "confirm" in label.lower()
    ], labels

    _press(page, "Add to profile", ".imp-card")
    page.wait_for("!document.querySelector('.imp-place')", message="the experience placed")
    assert _verified(empty.db) == 0, "placing an experience confirmed a statement"

    first_key = page.evaluate("document.querySelector('.imp-item[data-state=waiting]').dataset.key")
    page.evaluate("document.querySelector('.imp-item[data-state=waiting] .btn--primary').click()")
    page.wait_for(_is(first_key, "confirmed"))
    assert _verified(empty.db) == 1
    # Focus moves to the next line still waiting.
    page.wait_for(
        "document.activeElement && document.activeElement.closest('.imp-item')"
        f" && document.activeElement.closest('.imp-item').dataset.key !== {json.dumps(first_key)}",
        message="focus on the next waiting line",
    )

    # "Not sure yet" leaves it open, marked, and confirms nothing.
    second_key = page.evaluate(
        "document.querySelector('.imp-item[data-state=waiting]').dataset.key"
    )
    _press(page, "Not sure yet", f'.imp-item[data-key="{second_key}"]')
    # On a CV the line stays "waiting", so the state cannot say the answer
    # landed; the review's status line does. Answers run one at a time, and a
    # click made while this one is still out would be ignored.
    page.wait_for(
        "document.querySelector('.imp-status')?.textContent === 'Left for later.'",
        message="the Not sure yet answer saved",
    )
    page.wait_for(_is(second_key, "waiting"))
    assert _verified(empty.db) == 1

    # Reject records the answer and creates nothing.
    third_key = page.evaluate(
        "[...document.querySelectorAll('.imp-item[data-state=waiting]')]"
        f".map((n) => n.dataset.key).find((k) => k !== {json.dumps(second_key)})"
    )
    _press(page, "Reject", f'.imp-item[data-key="{third_key}"]')
    page.wait_for(_is(third_key, "rejected"))
    assert _verified(empty.db) == 1

    # Close the app mid-review; every answer is still there.
    page.navigate(empty.base)
    _nav(page, "documents")
    page.wait_for("document.querySelector('.docs-card')")
    assert "Continue review" in _text(page, ".docs-card")
    _press(page, "Continue review", ".docs-card")
    page.wait_for("document.querySelector('.imp-card')")
    states = page.evaluate(
        f"[{json.dumps(first_key)}, {json.dumps(second_key)}, {json.dumps(third_key)}]"
        '.map((k) => document.querySelector(`.imp-item[data-key="${k}"]`)?.dataset.state)'
    )
    assert states == ["confirmed", "waiting", "rejected"], states
    assert _own_errors(page, empty.base) == []


# =========================================================================
# C, D, E: reconciliation against what the profile already holds
# =========================================================================


def test_a_date_conflict_shows_both_and_changes_only_what_is_chosen(
    page: Chrome, placed: Workspace
) -> None:
    _open_update_review(page, placed)
    page.evaluate(
        "[...document.querySelectorAll('.imp-rail__xp')]"
        ".find((b) => /^Solutions Consultant/.test(b.textContent)).click()"
    )
    page.wait_for("document.querySelector('.imp-dates')", message="the date question")
    assert "Check the dates" in _text(page, ".imp-banner")
    options = _text(page, ".imp-dates")
    assert "Jan 2020" in options and "Mar 2020" in options, options

    page.evaluate("document.querySelectorAll('.imp-choice')[1].click()")
    page.wait_for("!document.querySelector('.imp-dates')", message="the dates settled")
    career = page.evaluate("fetch('/api/career').then((r) => r.json())")
    starts = {e["title"]: e["period_start"] for e in career["experiences"]}
    assert starts["Solutions Consultant"] == "2020-03", starts


def test_a_line_already_in_the_profile_is_kept_not_stored_twice(
    page: Chrome, placed: Workspace
) -> None:
    _open_update_review(page, placed)
    assert "Already in your profile" in _text(page, ".imp-banner")
    before = _verified(placed.db)
    page.wait_for("document.querySelector('.imp-item .btn--primary')")
    label = page.evaluate("document.querySelector('.imp-item .btn--primary').textContent.trim()")
    assert label == "Keep the one in my profile", label
    key = page.evaluate("document.querySelector('.imp-item').dataset.key")
    page.evaluate("document.querySelector('.imp-item .btn--primary').click()")
    page.wait_for(_is(key, "rejected"))
    assert _verified(placed.db) == before


# =========================================================================
# M, N: Documents put away, brought back, deleted after saying what goes
# =========================================================================


def test_documents_archive_restore_and_delete(page: Chrome, placed: Workspace) -> None:
    _open(page, placed.base, "documents")
    page.wait_for("document.querySelectorAll('.docs-card').length === 2")
    kept = _verified(placed.db)

    page.evaluate(
        "[...document.querySelectorAll('.docs-card')]"
        ".find((c) => c.textContent.includes('Riley_CV_update'))"
        ".querySelector('[aria-label^=\"Archive\"]').click()"
    )
    page.wait_for("document.querySelectorAll('.docs > .docs-list > li').length === 1")
    page.wait_for("document.querySelector('.cw-toast')", message="the undo toast")
    _press(page, "Undo", ".cw-toast")
    page.wait_for(
        "document.querySelectorAll('.docs > .docs-list > li').length === 2",
        message="undo brought it back",
    )

    page.evaluate(
        "[...document.querySelectorAll('.docs-card')]"
        ".find((c) => c.textContent.includes('Riley_CV_update'))"
        ".querySelector('[aria-label^=\"Delete\"]').click()"
    )
    page.wait_for("document.querySelector('.cw-confirm')", message="the inline confirmation")
    said = _text(page, ".cw-confirm")
    assert "cannot be undone" in said.lower() or "for good" in said.lower(), said
    page.evaluate(
        "document.querySelector('.cw-confirm .btn--danger, .cw-confirm .btn--primary').click()"
    )
    page.wait_for("document.querySelectorAll('.docs-card').length === 1")
    assert _verified(placed.db) == kept, "deleting an import removed confirmed evidence"


# =========================================================================
# O, P: an evidence card and its drawer; evidence written by hand
# =========================================================================


def test_a_project_card_opens_a_drawer_and_focus_comes_back(
    page: Chrome, placed: Workspace
) -> None:
    _open(page, placed.base, "evidence")
    page.wait_for("document.querySelector('.evp-card')")
    assert "Billing Automation" in _text(page, ".evp-section")
    page.evaluate("document.querySelector('.evp-card .cw-action').focus()")
    page.evaluate("document.querySelector('.evp-card .cw-action').click()")
    page.wait_for("document.querySelector('.cw-drawer [role=dialog]')")
    drawer = _text(page, ".cw-drawer")
    assert "Billing Automation" in drawer and "Written by you" in drawer, drawer
    page.wait_for("document.querySelector('.cw-drawer').contains(document.activeElement)")
    page.press("Escape")
    page.wait_for("!document.querySelector('.cw-drawer')")
    page.wait_for(
        "document.activeElement && document.activeElement.closest('.evp-card') !== null",
        message="focus back on the card; it is on "
        + str(page.evaluate("document.activeElement?.outerHTML.slice(0, 120)")),
    )


def test_evidence_added_by_hand_is_never_shown_as_a_quote(page: Chrome, empty: Workspace) -> None:
    _open(page, empty.base, "evidence")
    page.wait_for("document.querySelector('#evp-import')", message="the empty state")
    _press(page, "Add one yourself", "#page-evidence")
    page.wait_for("document.querySelector('.cw-drawer input[type=text]')")
    page.evaluate(
        "(() => { const r = [...document.querySelectorAll('.evp-type')]"
        ".find((n) => n.dataset.type === 'ACHIEVEMENT'); r.click();"
        " const t = document.querySelector('.cw-drawer input[type=text]');"
        " t.value = 'Team award 2024';"
        " const d = document.querySelector('.cw-drawer textarea');"
        " d.value = 'Recognised for the onboarding redesign.'; })()"
    )
    _press(page, "Save evidence", ".cw-drawer")
    page.wait_for("document.querySelector('.evp-card')")
    card = _text(page, ".evp-card")
    assert "Team award 2024" in card and "Written by you" in card, card
    assert (
        page.evaluate("document.querySelectorAll('#page-evidence .cw-source__quote').length") == 0
    )


# =========================================================================
# H: edit an experience in place, with a year-only start and a current role
# =========================================================================


def test_edit_an_experience_in_place(page: Chrome, placed: Workspace) -> None:
    _open(page, placed.base, "profile")
    page.wait_for("document.querySelector('#page-profile .profiletab')", message="the profile tabs")
    page.evaluate(
        "[...document.querySelectorAll('#page-profile button')]"
        ".find((b) => b.textContent.trim().startsWith('Experience')).click()"
    )
    page.wait_for("document.querySelector('.xp-card')")
    page.evaluate("document.querySelector('#pagehead-actions .btn--primary').click()")
    page.wait_for("document.querySelector('.xp-banner')", message="the edit-mode banner")
    role = page.evaluate("document.querySelector('.xp-card .xp-card__role').textContent")
    page.evaluate("document.querySelector('.xp-card .cw-action').click()")
    page.wait_for("document.querySelector('.xp-editor')")
    page.evaluate(
        "(() => { const f = document.querySelector('.xp-editor');"
        " const years = f.querySelectorAll('.xp-period__year');"
        " years[0].value = '2019';"
        " f.querySelectorAll('.xp-period select')[0].value = '';"
        " const current = f.querySelector('input[type=checkbox]');"
        " if (!current.checked) current.click();"
        " f.querySelector('textarea').value = 'Led solutions work for enterprise customers.'; })()"
    )
    assert page.evaluate("document.querySelectorAll('.xp-editor .xp-period__year')[1].disabled")
    _press(page, "Save changes", ".xp-editor")
    page.wait_for("!document.querySelector('.xp-editor')")
    career = page.evaluate("fetch('/api/career').then((r) => r.json())")
    edited = next(e for e in career["experiences"] if e["title"] == role)
    assert edited["period_start"] == "2019" and edited["current_role"], edited
    assert edited["description"] == "Led solutions work for enterprise customers."
    shown = page.evaluate(
        "[...document.querySelectorAll('.xp-card')]"
        f".find((c) => c.querySelector('.xp-card__role').textContent === {json.dumps(role)})"
        "?.textContent || ''"
    )
    assert "Led solutions work" in shown, shown


# =========================================================================
# S: keyboard reaches every action; focus reveals what hover reveals
# =========================================================================


def test_card_actions_appear_when_the_keyboard_reaches_them(
    page: Chrome, placed: Workspace
) -> None:
    # A background tab has no focus, and :focus-within never matches there.
    page._cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": True})
    try:
        _card_actions_on_focus(page, placed)
    finally:
        page._cdp.call("Emulation.setFocusEmulationEnabled", {"enabled": False})


def _card_actions_on_focus(page: Chrome, placed: Workspace) -> None:
    _open(page, placed.base, "evidence")
    page.wait_for("document.querySelector('.evp-card .cw-card__actions')")
    hidden = "getComputedStyle(document.querySelector('.evp-card .cw-card__actions')).opacity"
    page.evaluate("document.body.focus()")
    assert float(page.evaluate(hidden)) < 0.5, "actions are hidden until hover or focus"
    page.evaluate("document.querySelector('.evp-card .cw-action').focus()")
    time.sleep(0.4)
    assert float(page.evaluate(hidden)) == 1.0, "focus within the card did not reveal its actions"


# =========================================================================
# T: a phone; U: Portuguese
# =========================================================================


def test_the_review_fits_a_phone(page: Chrome, placed: Workspace) -> None:
    page.set_viewport(390, 860, mobile=True)
    _open_update_review(page, placed)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the page scrolls sideways by {overflow}px"
    small = page.evaluate(
        "[...document.querySelectorAll('.imp-item__actions button')]"
        ".filter((b) => b.getBoundingClientRect().height < 44).length"
    )
    assert small == 0, "an answer button is below the 44px touch target"


def test_the_review_reads_in_portuguese(page: Chrome, placed: Workspace) -> None:
    page.navigate(placed.base)
    page.evaluate(f"localStorage.setItem({json.dumps(LOCALE_KEY)}, 'pt-BR')")
    _open_update_review_pt(page, placed)
    counter = _text(page, ".imp-counter")
    assert "Experiência 1 de" in counter, counter
    card = _text(page, ".imp-card")
    for english in ("Not sure yet", "Reject", "Already in your profile", "details found"):
        assert english not in card, f"{english!r} is still English"


def _open_update_review_pt(page: Chrome, ws: Workspace) -> None:
    _open(page, ws.base, "documents", locale="pt-BR")
    page.wait_for("document.querySelector('.docs-card .btn--primary')")
    page.evaluate(
        "[...document.querySelectorAll('.docs-card')]"
        ".find((c) => c.textContent.includes('Riley_CV_update'))"
        ".querySelector('.btn--primary').click()"
    )
    page.wait_for("document.querySelector('.imp-card')")


# =========================================================================
# Hotfix 2026-09-25: job controls belong to Discover; no legacy manager
# =========================================================================

SHOWN = (
    # Rendered at all: a hidden ancestor leaves an element no boxes.
    "(sel) => { const n = document.querySelector(sel);"
    " return !!n && n.getClientRects().length > 0; }"
)


def _shown(page: Chrome, selector: str) -> bool:
    return bool(page.evaluate(f"({SHOWN})({json.dumps(selector)})"))


def test_job_controls_belong_to_discover_alone(page: Chrome, pristine_server: str) -> None:
    from tests.browser.home_helpers import open_home_past_setup

    open_home_past_setup(page, pristine_server)
    _nav(page, "jobs")
    page.wait_for(f"({SHOWN})('.topbar')", message="the job toolbar on Discover")
    if not _shown(page, "#filterpanel"):
        page.evaluate("document.getElementById('rail-toggle').click()")
    page.wait_for(f"({SHOWN})('#filterpanel')", message="the filters, opened")
    for name in ("profile", "evidence", "documents", "home", "applications", "settings"):
        if name == "applications":
            # Applications shares Discover's container; the link says it is current.
            page.evaluate("document.querySelector('.topnav__link[data-page=applications]').click()")
            page.wait_for(
                "document.querySelector('.topnav__link[data-page=applications]')"
                ".getAttribute('aria-current') === 'page'"
            )
        else:
            _nav(page, name)
        for control in (".topbar", "#filterpanel", "#chipbar", "#rail-toggle"):
            assert not _shown(page, control), f"{control} leaked onto {name}"
    _nav(page, "jobs")
    page.wait_for(f"({SHOWN})('.topbar') && ({SHOWN})('#filterpanel')", message="filters back")
    assert _own_errors(page, pristine_server) == []


def test_no_ordinary_route_reaches_the_statement_manager(page: Chrome, placed: Workspace) -> None:
    _open(page, placed.base, "evidence")
    page.wait_for("document.querySelector('.evp-card')")
    for name in ("evidence", "documents", "profile"):
        _nav(page, name)
        time.sleep(0.5)
        words = str(page.evaluate("document.body.innerText"))
        assert "Manage all statements" not in words and "All statements" not in words, name
    assert not page.evaluate("Boolean(document.querySelector('.topnav__link[data-page=manage]'))")
    # Not even an old bookmark opens it.
    page.navigate(f"{placed.base}/#manage")
    page.wait_for("document.querySelector('.topnav__link[data-page=home]') !== null")
    time.sleep(0.5)
    assert page.evaluate("document.getElementById('page-manage').hidden")
    assert not page.evaluate("document.querySelector('#page-manage .ev__privacy')")


# =========================================================================
# Hotfix 2026-09-25: Career Profile -> Skills
# =========================================================================

CHIP_INPUT = ".xp-editor .cw-chipinput__input"


def test_skills_are_typed_one_after_another_without_the_mouse(
    page: Chrome, empty: Workspace
) -> None:
    _open(page, empty.base, "profile")
    page.wait_for("document.querySelector('#page-profile .profiletab')")
    page.evaluate(
        "[...document.querySelectorAll('#page-profile .profiletab')]"
        ".find((b) => b.textContent.trim().startsWith('Experience')).click()"
    )
    page.wait_for("document.querySelector('.xp-add')")
    page.evaluate("document.querySelector('.xp-add').click()")
    page.wait_for(f"document.querySelector({json.dumps(CHIP_INPUT)})")
    # Opening the editor focuses its first field on the next frame; let that
    # land first, as it has by the time a person reaches the skills field.
    page.wait_for(
        "document.activeElement && document.activeElement.closest('.xp-editor') !== null",
        message="the editor's own first focus",
    )
    page.evaluate(f"document.querySelector({json.dumps(CHIP_INPUT)}).focus()")
    focused = f"document.activeElement === document.querySelector({json.dumps(CHIP_INPUT)})"
    for number, name in enumerate(["HubSpot", "n8n", "Python"], start=1):
        page.type_text(name)
        page.press("Enter")
        page.wait_for(
            f"document.querySelectorAll('.xp-editor .cw-chip').length === {number}",
            message=f"chip {name}",
        )
        assert page.evaluate(focused), f"focus left the skill input after {name}: " + str(
            page.evaluate(
                "({ active: document.activeElement?.outerHTML.slice(0, 90),"
                " editor: !!document.querySelector('.xp-editor'),"
                " same: document.querySelector('.cw-chipinput__input') ?"
                " document.querySelector('.cw-chipinput__input').isConnected : null })"
            )
        )
    chips = page.evaluate(
        "[...document.querySelectorAll('.xp-editor .cw-chip span')].map((n) => n.textContent)"
    )
    assert chips == ["HubSpot", "n8n", "Python"], chips
    # Removing one puts focus back where the typing is.
    page.evaluate("document.querySelector('.xp-editor .cw-chip__remove').click()")
    page.wait_for("document.querySelectorAll('.xp-editor .cw-chip').length === 2")
    assert page.evaluate(focused), "focus was lost after removing a skill"


def test_the_skills_tab_separates_skills_certificates_and_education(
    page: Chrome, empty: Workspace
) -> None:
    for body in (
        {"claim_type": "SKILL", "text": "HubSpot"},
        {"claim_type": "SKILL", "text": "Python"},
        {
            "claim_type": "CERTIFICATION",
            "text": "Certified Administrator | Salesforce | Sep 2026 | Sep 2027 | ABC-12345",
        },
        {"claim_type": "CERTIFICATION", "text": "Course | Provider | Other text | Sep 2026"},
        {"claim_type": "EDUCATION", "text": "BSc Business Administration, Invented University"},
    ):
        _call(empty.api, "POST", "/api/evidence", body)
    _open(page, empty.base, "profile")
    page.wait_for("document.querySelector('#page-profile .profiletab')")
    page.evaluate(
        "[...document.querySelectorAll('#page-profile .profiletab')]"
        ".find((b) => b.textContent.trim().startsWith('Skills')).click()"
    )
    page.wait_for("document.querySelector('.profile__certs')", message="the certificates")
    chips = page.evaluate(
        "[...document.querySelectorAll('#page-profile .profile__chips .evchip')]"
        ".map((n) => n.textContent)"
    )
    assert sorted(chips) == ["HubSpot", "Python"], chips
    certs = page.evaluate(
        "[...document.querySelectorAll('.profile__cert')].map((n) => ({"
        " title: n.querySelector('.profile__certtitle')?.textContent,"
        " issuer: n.querySelector('.profile__certissuer')?.textContent || null,"
        " dates: n.querySelector('.profile__certdates')?.textContent || null,"
        " id: n.querySelector('.profile__certid')?.textContent || null }))"
    )
    structured = next(c for c in certs if c["title"] == "Certified Administrator")
    assert structured["issuer"] == "Salesforce"
    assert "Issued" in structured["dates"] and "Expires" in structured["dates"]
    assert "2026" in structured["dates"] and "2027" in structured["dates"]
    assert structured["id"] == "Credential ID: ABC-12345"
    # No raw pipe-delimited string where the fields could be read.
    assert "|" not in " ".join(str(v) for v in structured.values())
    # An ambiguous line is shown exactly as written.
    assert any(c["title"] == "Course | Provider | Other text | Sep 2026" for c in certs), certs
    headings = page.evaluate(
        "[...document.querySelectorAll('#page-profile .profile__heading')]"
        ".map((n) => n.textContent)"
    )
    assert "Education" in headings and "Certificates and study" in headings, headings
    assert "BSc Business Administration" in _text(page, "#page-profile")

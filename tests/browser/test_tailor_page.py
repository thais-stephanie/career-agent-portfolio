# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""Resume Tailor: the whole way from a job to a resume, in five tabs.

Start, Make a resume, My resumes, My experience and Tips. Synthetic people only
(`tests.support_tailor`); no model is called and nothing reads a real resume.
What must hold: opening a tab makes and sends nothing; the honesty rule is shown
and cannot be switched off; a pasted ad is kept as pasted; the version opens in
the existing Editor; Experience is the profile's own evidence workspace; a tip is
done only when the person says so.
"""

from __future__ import annotations

import time
from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.test_first_run_flow import click_button
from tests.browser.test_resume_tailor import tailor_server  # noqa: F401  (a profile with a Master)

SPY = """(() => {
  window.__writes = [];
  const real = window.fetch;
  window.fetch = (u, o) => {
    const m = ((o && o.method) || 'GET').toUpperCase();
    if (m !== 'GET') window.__writes.push(m + ' ' + String(u));
    return real(u, o);
  };
})()"""

TABS = ["start", "make", "resumes", "experience", "tips"]
TAB_NAMES = "[...document.querySelectorAll('#page-tailor [role=tab]')].map((b) => b.innerText)"


def _go(page: Chrome, url: str, state: str = "start", width: int = 1440) -> None:
    page.set_viewport(width, 960, mobile=width < 500)
    page.navigate(url + "/#tailor")
    page.wait_for("!document.getElementById('page-tailor').hidden", message="the Tailor page")
    page.wait_for(
        f"document.getElementById('page-tailor').dataset.state === {state!r}",
        message=f"{state} drew",
    )


def _open(page: Chrome, tab: str) -> None:
    page.evaluate(f"document.getElementById('tp-tab-{tab}').click()")
    page.wait_for(
        f"document.getElementById('page-tailor').dataset.state === {tab!r}",
        message=f"the {tab} tab",
    )


def _text(page: Chrome) -> str:
    return str(page.evaluate("document.getElementById('page-tailor').innerText"))


def test_the_sidebar_has_resume_tailor_with_five_tabs_and_opening_them_changes_nothing(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    page._cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SPY})
    _go(page, tailor_server["url"])
    labels = page.evaluate(
        "[...document.querySelectorAll('.topnav__label')].map((n) => n.innerText)"
    )
    assert labels.index("Resume Tailor") == labels.index("Resumes") - 1
    assert page.evaluate("document.getElementById('pagehead-title').innerText") == "Resume Tailor"
    tabs = page.evaluate(TAB_NAMES)
    assert [t.split("\n")[0] for t in tabs] == [
        "Start",
        "Make a resume",
        "My resumes",
        "My experience",
        "Tips",
    ]
    for tab in TABS:
        _open(page, tab)
        time.sleep(0.4)
    assert page.evaluate("window.__writes") == [], "opening the tabs makes and sends nothing"
    assert page.console_errors() == []


def test_the_tabs_move_with_the_arrow_keys_and_are_named_in_portuguese(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    page.evaluate("document.getElementById('tp-tab-start').focus()")
    page.press("ArrowRight")
    page.wait_for("document.getElementById('page-tailor').dataset.state === 'make'", message="make")
    page.wait_for("document.activeElement.id === 'tp-tab-make'", message="focus follows")
    page.press("ArrowLeft")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="back"
    )
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    page.wait_for_app()
    page.wait_for("document.getElementById('page-tailor').dataset.state === 'start'", message="pt")
    tabs = page.evaluate(TAB_NAMES)
    assert [t.split("\n")[0] for t in tabs] == [
        "Início",
        "Criar currículo",
        "Meus currículos",
        "Minha experiência",
        "Dicas",
    ]


def test_start_says_what_waits_without_inventing_anything(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    text = _text(page)
    for needle in (
        "Make a resume that fits the job",
        "Saved jobs without a resume",
        "Resumes you made",
        "Your experience",
        "confirmed",
    ):
        assert needle in text, needle
    assert "waiting for your review" in text, "the evidence still waiting is counted, never hidden"


def test_make_a_version_for_a_job_after_checking_what_will_be_used(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    _open(page, "make")
    assert page.evaluate("document.getElementById('tp-check').disabled") is True
    assert "Still needed" in _text(page)
    page.evaluate("document.querySelector('.tp-radios [data-job]').click()")
    page.wait_for("document.getElementById('tp-check').disabled === false", message="enabled")
    page.evaluate("document.getElementById('tp-check').click()")
    page.wait_for("document.querySelector('.tp-check')", message="the check")
    text = _text(page)
    assert "What will go into your resume for" in text and "Starting from: Master resume" in text
    assert "A gap stays a gap" in text
    assert "Confirmed lines that will be added" in text and "Asked by the ad and not proven" in text
    page.evaluate("document.getElementById('tp-make-it').click()")
    page.wait_for(
        "document.querySelector('.rvw')?.dataset.view === 'editor'"
        " && document.querySelector('.rve .rvw__kind')?.textContent.startsWith('For a job')",
        message="the job's version in the Editor",
    )


FILL_AD = (
    "(() => { const s = (id, v) => { const n = document.getElementById(id); n.value = v;"
    " n.dispatchEvent(new Event('input', {bubbles: true})); };"
    " s('tp-ad-title', 'Operations Analyst');"
    " s('tp-ad-text', 'Requirements\\n- Experience with HubSpot workflows and n8n\\n- Reporting');"
    " })()"
)


def test_a_pasted_ad_is_kept_as_pasted_and_asks_no_one(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    page._cdp.call("Page.addScriptToEvaluateOnNewDocument", {"source": SPY})
    _go(page, tailor_server["url"])
    _open(page, "make")
    page.evaluate(FILL_AD)
    page.wait_for("document.getElementById('tp-check').disabled === false", message="enabled")
    assert page.evaluate("document.querySelectorAll('.tp-radios [data-job].is-on').length") == 0
    page.evaluate("document.getElementById('tp-check').click()")
    page.wait_for("document.querySelector('.tp-check')", message="the check")
    page.evaluate("document.getElementById('tp-make-it').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="the editor")
    writes = page.evaluate("window.__writes")
    assert not any("collect" in w or "semantic" in w or "deep" in w for w in writes), writes
    assert any("/api/resume/tailor" in w for w in writes)
    page.evaluate("document.querySelector('.topnav__link[data-page=tailor]').click()")
    page.wait_for("!document.getElementById('page-tailor').hidden", message="the Tailor page")
    page.wait_for("document.getElementById('tp-tab-resumes')", message="the tabs")
    _open(page, "resumes")
    text = _text(page)
    assert "Operations Analyst" in text and "Ad pasted by you" in text and "Open" in text


def test_the_honesty_rule_is_shown_and_cannot_be_switched_off(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    _open(page, "make")
    rule = "document.getElementById('tp-honest')"
    assert page.evaluate(f"{rule}.getAttribute('aria-checked')") == "true"
    assert page.evaluate(f"{rule}.getAttribute('aria-disabled')") == "true"
    page.evaluate(f"{rule}.click(); {rule}.focus()")
    page.press("Space")
    page.press("Enter")
    assert page.evaluate(f"{rule}.getAttribute('aria-checked')") == "true"
    text = _text(page)
    assert "Only use what I have really done" in text and "This cannot be turned off." in text
    assert "Nothing is cut to reach it" in text, "two pages is an aim, never a licence to cut"


def test_a_version_can_start_from_another_resume_of_the_persons_own(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    page.evaluate(
        "fetch('/api/resume/master').then(r => r.json()).then(m => fetch('/api/resume/documents',"
        " {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({from: m.master.document}) })).then(r => r.status)"
    )
    time.sleep(1.0)
    _open(page, "make")
    bases = "document.querySelectorAll('[aria-labelledby=tp-make-2] .tp-radio')"
    page.wait_for(f"{bases}.length === 2", message="two bases")
    page.evaluate(f"{bases}[1].click()")
    page.evaluate("document.querySelector('.tp-radios [data-job]').click()")
    page.wait_for("document.getElementById('tp-check').disabled === false", message="enabled")
    page.evaluate("document.getElementById('tp-check').click()")
    page.wait_for("document.querySelector('.tp-check')", message="the check")
    assert "Starting from: Master resume" not in _text(page)
    assert "Starting from:" in _text(page)


def test_my_resumes_lists_the_master_and_the_versions_and_downloads_say_what_exists(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    page.evaluate(
        "fetch('/api/jobs?limit=1').then(r => r.json()).then(j => fetch("
        "'/api/resume/jobs/' + j.items[0].job_id + '/tailor', {method: 'POST',"
        " headers: {'Content-Type': 'application/json'}, body: '{}'}))"
    )
    time.sleep(2.0)
    _open(page, "resumes")
    text = _text(page)
    assert "Made for a job" in text and "Starting resumes" in text and "Your Master resume" in text
    assert "PDF and Word" in text
    page.evaluate(
        "[...document.querySelectorAll('.tp-res button')]"
        ".find((b) => /Downloads/.test(b.textContent)).click()"
    )
    shown = "document.querySelector('.tp-dl:not([hidden])')"
    page.wait_for(f"{shown}?.innerText.length > 0", message="listed")
    assert "No file made yet" in str(page.evaluate(f"{shown}.innerText"))
    page.evaluate("document.querySelector('.tp-res button').click()")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="Editor")


def test_my_experience_is_the_profiles_own_evidence_workspace(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    _open(page, "experience")
    page.wait_for("document.querySelector('#page-tailor .career')", message="the workspace")
    page.wait_for(
        "document.querySelector('#page-tailor .career h2, #page-tailor .career h3')",
        message="drawn",
    )
    text = _text(page)
    assert "same experience your profile holds" in text
    assert "Only confirmed lines go into a resume" in text
    assert "Northwind Systems" in text, "the profile's own experiences, not a second list"


def test_a_tip_is_done_only_when_the_person_says_so_and_stays_done(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _go(page, tailor_server["url"])
    _open(page, "tips")
    page.wait_for("document.querySelectorAll('.tp-tip').length === 12", message="twelve tips")
    text = _text(page)
    assert "None of them guarantees an interview or a job" in text
    assert "0 of 6 done" in text
    assert "Add one only when it is true" in text, "a note comes from the person's own data"
    page.evaluate("document.querySelector('.tp-tick').click()")
    page.wait_for("document.querySelector('.tp-tip.is-done')", message="marked")
    page.reload()
    page.wait_for_app()
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="start"
    )
    _open(page, "tips")
    page.wait_for(
        "document.querySelector('.tp-tip.is-done')", message="still marked after a reload"
    )
    assert "1 of 6 done" in _text(page)


def test_without_a_master_make_it_and_come_back_to_the_same_job(
    page: Chrome, pristine_server: str
) -> None:
    page.set_viewport(1440, 960)
    page.navigate(pristine_server)
    page.evaluate(
        "fetch('/api/evidence', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({claim_type: 'SKILL', text: 'Invented spreadsheet modelling'})})"
        ".then(r => r.status)"
    )
    page.navigate(pristine_server + "/#jobs")
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20, message="jobs")
    title = str(
        page.evaluate(
            "document.querySelector('#list [data-job-id] .card__title,"
            " #list [data-job-id] h3')?.innerText || ''"
        )
    )
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.evaluate("document.getElementById('drawer-tab-prepare').click()")
    page.wait_for(
        "document.querySelector('.d-tailor')?.dataset.tailor === 'needs-master'", message="step"
    )
    page.evaluate("document.getElementById('drawer-to-resumes').click()")
    page.wait_for("!document.getElementById('page-resume').hidden", message="Resumes")
    page.wait_for("!document.getElementById('tailor-return').hidden", message="the way back")
    bar = str(page.evaluate("document.getElementById('tailor-return').innerText"))
    assert "Master resume" in bar and "go back" in bar
    click_button(page, "Build from My Profile")
    click_button(page, "Build my Master")
    page.wait_for("document.querySelector('.rvw')?.dataset.view === 'editor'", message="the Master")
    page.evaluate("document.getElementById('tailor-return-go').click()")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'make'", message="Make, same job"
    )
    assert title == "" or title in str(
        page.evaluate("document.querySelector('.tp-radio.is-on')?.innerText || ''")
    )


def test_the_tabs_fit_a_phone_in_both_themes(page: Chrome, tailor_server: dict[str, Any]) -> None:
    _go(page, tailor_server["url"], width=390)
    for scheme in ("light", "dark"):
        page.set_color_scheme(scheme)
        for tab in TABS:
            _open(page, tab)
            time.sleep(0.3)
            overflow = page.evaluate(
                "document.documentElement.scrollWidth - document.documentElement.clientWidth"
            )
            assert overflow <= 0, f"{tab} overflows by {overflow}px in {scheme}"

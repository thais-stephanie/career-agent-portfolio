"""The first run of a new installation, after its collection (2026-10-10).

Invented data only: an invented role and two confirmed lines, the invented
demo postings, and one invented posting from an employer whose slug is
`constructor` -- the value that made every list read throw. No Master resume
exists yet, which is where a new installation starts.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.home_helpers import open_home_past_setup
from tests.integration.test_resume_master import claim, experience, profile

from career_agent.clock import now_utc
from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import ClaimType
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.manual_import import import_posting
from career_agent.storage.db import connect, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

DEMO = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"
BUTTONS = "[...document.querySelectorAll('button')]"
NAV = "document.querySelector('.topnav__link[data-page=\"{}\"]')"
FAILED = "/Could not load/.test(document.body.innerText)"
CARD = "document.querySelector('#list [data-job-id]')"
EDITOR = "document.querySelector('.rvw')?.dataset.view === 'editor'"


def has_button(pattern: str) -> str:
    return f"{BUTTONS}.some(b => /{pattern}/.test(b.textContent))"


def click_button(page: Chrome, pattern: str, **wait: Any) -> None:
    page.wait_for(has_button(pattern), **wait)
    page.evaluate(f"{BUTTONS}.find(b => /{pattern}/.test(b.textContent)).click()")


def go(page: Chrome, name: str) -> None:
    page.evaluate(f"{NAV.format(name)}.click()")


@pytest.fixture
def first_run(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    conn = profile(tmp_path, "p")
    with transaction(conn):
        role = experience(
            conn, "Invented Works", "Operations Analyst", "2022-01", None, current=True
        )
        line = "Built lead routing for four invented teams."
        claim(conn, "k-routing", ClaimType.EMPLOYMENT, line, experience=role)
        claim(conn, "k-hubspot", ClaimType.TOOL, "HubSpot")
        # Proof, a skill and a qualification: a complete profile's overview.
        claim(conn, "k-mapping", ClaimType.SKILL, "Process mapping")
        project = "Rebuilt the invented weekly report as one automated workflow."
        claim(conn, "k-report", ClaimType.PROJECT, project, experience=role)
        claim(conn, "k-cert", ClaimType.CERTIFICATION, "Invented Analytics Certificate")
    config = load_search_config(committed_config)[0]
    seed_demo(conn, config, source=DEMO)
    import_posting(
        conn,
        config,
        title="Invented Integrations Analyst",
        company="Constructor",
        description="You will build workflow automation for an invented team using HubSpot.",
        url="https://example.invalid/constructor",
        location="Remote",
        config_id=config.config_id,
        config_version=config.config_version,
        now=now_utc(),
    )
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
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


def open_app(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/")
    page.wait_for(NAV.format("jobs"))


def open_list(page: Chrome, url: str) -> None:
    open_app(page, url)
    go(page, "jobs")
    page.wait_for(f"{CARD} || {FAILED}", timeout=30)


def test_an_employer_named_like_a_built_in_property_does_not_break_the_list(
    page: Chrome, first_run: dict[str, Any]
) -> None:
    """`constructor` read back JavaScript's own Object function as its label."""
    open_list(page, first_run["url"])
    assert not page.evaluate(FAILED)
    assert page.evaluate("document.querySelectorAll('#list [data-job-id]').length") > 0
    # And the list says what its scores are made of.
    assert "no AI" in str(page.evaluate("document.querySelector('#resultcount').innerText"))


def test_a_list_failure_is_a_plain_sentence_with_its_details_folded(
    page: Chrome, first_run: dict[str, Any]
) -> None:
    open_list(page, first_run["url"])
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) =>"
        " String(url).includes('/api/jobs?')"
        " ? Promise.reject(new Error('synthetic transport error')) : real(url, opts); })()"
    )
    # A new question (a quick filter), so the list is asked again rather than
    # redrawn from the one already held.
    click_button(page, "Remote only")
    page.wait_for(FAILED, timeout=20)
    state = str(page.evaluate("document.querySelector('#list').innerText"))
    head = state.split("Technical details")[0]
    assert "synthetic transport error" not in head, "the program's words lead the message"
    assert page.evaluate("document.querySelector('#list details.src__tech') !== null")
    retry = "/Try again|Retry/.test(document.querySelector('#list').innerText)"
    assert page.evaluate(retry)


def test_from_a_job_to_the_resume_tailor_starting_without_a_master(
    page: Chrome, first_run: dict[str, Any]
) -> None:
    """The approved flow: the job asks for a Master first, then tailors."""
    open_list(page, first_run["url"])

    def prepare() -> str:
        page.evaluate(f"{CARD}.click()")
        page.wait_for("document.getElementById('drawer-tab-prepare')")
        page.evaluate("document.getElementById('drawer-tab-prepare').click()")
        state = "document.querySelector('.d-tailor')?.dataset.tailor"
        page.wait_for(f"({state} || 'loading') !== 'loading'", message="step 3")
        return str(page.evaluate(state))

    assert prepare() == "needs-master"
    page.evaluate("document.getElementById('drawer-to-resumes').click()")
    click_button(page, "Build from My Profile")
    click_button(page, "Build my Master")
    page.wait_for(EDITOR, message="the Master")
    go(page, "jobs")
    page.wait_for(CARD)
    assert prepare() == "ready"
    page.evaluate("document.getElementById('drawer-tailor').click()")
    kind = "document.querySelector('.rve .rvw__kind')?.textContent"
    page.wait_for(f"{EDITOR} && {kind} === 'For a job · V1'", message="the job's version")


def test_a_site_that_needs_her_is_named_and_opens_at_its_own_action(
    page: Chrome, first_run: dict[str, Any]
) -> None:
    stamp = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    with connect(first_run["db"]) as conn, transaction(conn):
        conn.execute(
            "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json)"
            " VALUES ('r-remotive', 'collect-remotive', ?, ?, 'FAILED', ?)",
            (stamp, stamp, json.dumps({"jobs_new": 0})),
        )
    page.set_viewport(1440, 960)
    open_home_past_setup(page, first_run["url"] + "/")
    # The sidebar alert names the source and what happened ...
    alert = "document.querySelector('#site-alert')"
    page.wait_for(f"{alert} && !{alert}.hidden", timeout=30, message="the alert")
    assert page.evaluate("document.getElementById('site-alert-title').textContent") == (
        "Remotive needs a new check"
    )
    assert "failed and read nothing" in str(
        page.evaluate("document.getElementById('site-alert-text').textContent")
    )
    # ... and opens that source's own card, at its own action.
    page.evaluate("document.getElementById('site-alert-fix').click()")
    focused = "document.querySelector('article[data-source=\"remotive\"].is-focus')"
    page.wait_for(focused, timeout=30)
    assert page.evaluate("document.querySelector('.src__each').open")
    action = str(page.evaluate("document.activeElement.textContent"))
    assert action.strip(), "focus lands on the source's own action"


HOME_WITH_A_BIG_NEW = (
    "(() => { const real = window.fetch; window.fetch = async (url, opts) => {"
    " const answer = await real(url, opts);"
    " if (!String(url).includes('/api/home')) return answer;"
    " const body = await answer.json();"
    " body.metrics = (body.metrics || [])"
    "   .map(m => m.key === 'new' ? { ...m, value: 124528 } : m);"
    " return new Response(JSON.stringify(body),"
    "   { status: 200, headers: { 'Content-Type': 'application/json' } });"
    " }; })()"
)


def test_a_six_digit_new_count_fits_its_circle(page: Chrome, first_run: dict[str, Any]) -> None:
    open_app(page, first_run["url"])
    page.evaluate(HOME_WITH_A_BIG_NEW)
    node = "[...document.querySelectorAll('.home__side .step__value')].find(n => n.title)"
    try:
        for locale, short in (("en", "125K"), ("pt-BR", "125 mil")):
            page.evaluate(f"document.querySelector('[data-locale=\"{locale}\"]').click()")
            go(page, "jobs")
            go(page, "home")
            page.wait_for(f"Boolean({node})", timeout=20)
            assert page.evaluate(f"{node}.textContent").replace(" ", " ") == short
            assert page.evaluate(f"{node}.scrollWidth <= {node}.clientWidth + 1")
            assert "124" in str(page.evaluate(f"{node}.title"))
    finally:
        page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")


def open_overview(page: Chrome, url: str, width: int, locale: str) -> None:
    page.set_viewport(width, 900, mobile=width < 500)
    page.navigate(url + "/")
    page.wait_for(NAV.format("profile"))
    page.evaluate(f"document.querySelector('[data-locale=\"{locale}\"]').click()")
    go(page, "profile")
    page.wait_for("document.querySelector('.profile__evnote')", timeout=30)


@pytest.mark.parametrize(
    ("width", "locale", "note", "action"),
    [
        (
            1440,
            "en",
            "Confirmed evidence supports the claims in your tailored resume. "
            "Confirming it does not change your match score.",
            "Review my evidence",
        ),
        (
            390,
            "pt-BR",
            "As evidências confirmadas dão suporte às afirmações do seu currículo adaptado. "
            "Confirmá-las não altera seu match.",
            "Revisar minhas evidências",
        ),
    ],
)
def test_the_overview_keeps_the_skills_under_the_counts_and_the_note_beside_its_action(
    page: Chrome, first_run: dict[str, Any], width: int, locale: str, note: str, action: str
) -> None:
    open_overview(page, first_run["url"], width, locale)
    try:
        # The skills share the counts' column, beside the latest role.
        column = ".profile__overmain .profile__metrics + .profile__glance"
        assert page.evaluate(f"Boolean(document.querySelector('{column}'))")
        text = "document.querySelector('.profile__evnotetext')"
        button = "document.querySelector('.profile__evnote > .btn')"
        assert page.evaluate(f"{text}.textContent") == note
        assert page.evaluate(f"{button}.textContent") == action
        box = page.evaluate(
            f"(() => {{ const t = {text}.getBoundingClientRect(),"
            f" b = {button}.getBoundingClientRect(),"
            " c = document.querySelector('.profile__evnote').getBoundingClientRect();"
            " return { tw: t.width, cw: c.width, gapX: b.left - t.right, gapY: b.top - t.bottom,"
            " th: t.height, bh: b.height, ch: c.height }; })()"
        )
        if width >= 1000:
            # One row: the text takes the card's width, not a 60ch column.
            assert box["tw"] > box["cw"] * 0.6, box
            assert box["gapX"] >= 12, box
        else:
            assert box["gapY"] >= 12, box
            # Sized by its content: no empty band between text and button.
            assert box["ch"] < box["th"] + box["bh"] + 80, box
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    finally:
        page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")

# ruff: noqa: E501
"""What an ad states, as the interface shows it (2026-10-12).

Synthetic ads shaped like the two that exposed the defects. In the drawer: the
level (several listed, or the app default said as a default, never a level taken
from a career-growth list), the pay (an hourly rate is pay and is shown as that),
part-time and the weekly hours, a talent pool, a time-zone request, and an ad
laid out from its own lists and headings. In the list: the profile's age limit,
with what it hides counted apart and a way to lift it. No model, no collection.
"""

from __future__ import annotations

import socket
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import CollectionStatus
from career_agent.pipeline.rescore import rescore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    SourceBoardRepo,
)
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

POOL_TEXT = """Teaching Experts

We are building a talent pool of practitioners. We are looking for specialists across
a range of seniority levels:

Junior (Instructor) - 1-3 years designing or supporting workflows.

Mid (Author) - 3-6 years implementing automation across operations.

Senior (Subject Matter Expert) - 6+ years in solution architecture.

These are Talent Pool positions. There may not be an immediate opening at the time you apply.
You can join us on a part-time basis (10-15 hours per week) as an instructor.
Availability to collaborate within European time zones.
Competitive hourly rate of $40-$85 USD for flexible part-time collaboration.
"""

POOL_HTML = (
    "<h3>Teaching Experts</h3><p>We are building a talent pool of practitioners.</p>"
    "<ul><li><b>Junior</b> (Instructor) - 1-3 years designing workflows.</li>"
    "<li><b>Mid</b> (Author) - 3-6 years implementing automation.</li>"
    "<li><b>Senior</b> (Subject Matter Expert) - 6+ years in architecture.</li></ul>"
    "<p>There may not be an immediate opening at the time you apply.</p>"
)

GROWTH_TEXT = """Automation Specialist - Remote

Position Type: Full-Time, Remote
Location: Remote (LATAM)
Working Hours: U.S. business hours

About the role
We are hiring an Automation Specialist to build and maintain workflows with Zapier and APIs.

What you will own

Analyze existing workflows and find automation opportunities.

Design scalable workflows that reduce manual effort.

Document workflows, integrations and logic.

Train internal teams on automation practices.

Clear career growth into:

Senior Automation Engineer

Automation Architect

Business Systems Lead
"""

GROWTH_HTML = (
    "<h3><strong>Automation Specialist - Remote</strong></h3>"
    "<p><strong>Position Type:</strong> Full-Time, Remote<br> <strong>Location:</strong> Remote"
    " (LATAM)<br> <strong>Working Hours:</strong> U.S. business hours</p>"
    "<h3>About the role</h3><p>We are hiring an Automation Specialist to build and maintain"
    " workflows with Zapier and APIs.</p><h3>What you will own</h3>"
    "<ul><li>Analyze existing workflows and find automation opportunities.</li>"
    "<li>Design scalable workflows that reduce manual effort.</li>"
    "<li>Document workflows, integrations and logic.</li>"
    "<li>Train internal teams on automation practices.</li></ul>"
    "<h3>Why this role stands out</h3><p>Clear career growth into:</p>"
    "<ul><li>Senior Automation Engineer</li><li>Automation Architect</li>"
    "<li>Business Systems Lead</li></ul>"
)

SIMPLE_TEXT = (
    "Operations Coordinator\n\nWe coordinate day to day operations for a remote team and are "
    "looking for someone who likes order and clear documentation.\n\nRequirements\n- Spreadsheets\n- Writing\n"
    # Past the 200 characters a posting needs to be read (ADR-0039), so these
    # stay on the shortlist rather than in the leads lane.
    "- Calm, written handovers between time zones\n"
)


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).date().isoformat() + "T00:00:00+00:00"


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.fixture
def facts_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    db = tmp_path / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "facts")
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="example", name="Example Academy"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="comeet", board_identifier="x")
        )
        ids: dict[str, str] = {}
        rows = [
            ("pool", "Teaching Experts", POOL_TEXT, POOL_HTML, days_ago(10)),
            ("growth", "Automation Specialist", GROWTH_TEXT, GROWTH_HTML, days_ago(10)),
            ("old", "Operations Coordinator Old", SIMPLE_TEXT, None, days_ago(300)),
            ("undated", "Operations Coordinator Undated", SIMPLE_TEXT + "Undated.\n", None, None),
            ("recent", "Operations Coordinator", SIMPLE_TEXT + "Recent.\n", None, days_ago(5)),
        ]
        for key, title, text, html, posted in rows:
            digest = JobRawRepo(conn).put(text, html)
            ids[key] = JobRepo(conn).upsert_seen(
                JobRecord(
                    company_id=company, source_board_id=board, provider="comeet",
                    external_id=f"fact-{key}", url=f"https://example.test/{key}", title=title,
                    location_raw="Remote", posted_at=posted, content_hash=digest,
                ),
                status=CollectionStatus.NORMALISED,
            )  # fmt: skip
        # Last listed when it was published: since ADR-0039 a posting its
        # source still lists is not hidden for its age.
        conn.execute("UPDATE job SET last_seen_at = posted_at WHERE id = ?", (ids["old"],))
    config, _ = load_search_config(committed_config)
    rescore(conn, config)
    # An OLD score: the facts the readers learned later are missing from what it stored.
    import json

    row = conn.execute(
        "SELECT id, result_json FROM job_match WHERE job_id = ?", (ids["pool"],)
    ).fetchone()
    result = json.loads(row["result_json"])
    result["posting_facts"].pop("salary", None)
    for key in ("schedule", "talent_pool", "timezone"):
        result["posting_facts"].pop(key, None)
    result["seniority"] = {
        "value": "MID",
        "source": "DEFAULT",
        "confidence": "LOW",
        "evidence": None,
    }
    conn.execute(
        "UPDATE job_match SET result_json = ?, salary_min = NULL WHERE id = ?",
        (json.dumps(result), row["id"]),
    )
    stored = conn.execute(
        "SELECT match_score FROM job_match WHERE job_id = ?", (ids["pool"],)
    ).fetchone()[0]
    conn.close()
    port = _free_port()
    app = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}", "ids": ids, "db": db, "pool_score": stored}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def _open_job(page: Chrome, base: str, job_id: str) -> None:
    page.set_viewport(1440, 1000)
    page.navigate(f"{base}/?job={job_id}#jobs")
    page.wait_for(
        "document.querySelector('.drawer__panel .d-tile')", timeout=30, message="the drawer"
    )


def _tab(page: Chrome, key: str) -> None:
    page.evaluate(f"document.getElementById('drawer-tab-{key}').click()")
    page.wait_for(
        f"!document.getElementById('drawer-panel-{key}').hidden", message=f"the {key} tab"
    )


def _drawer(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.drawer__panel').innerText"))


def _tile(page: Chrome, key: str) -> str:
    return str(page.evaluate(f"document.querySelector('.d-tile--{key}').innerText"))


def _tile_details(page: Chrome, key: str) -> str:
    """The tiles hold the fact and short qualifiers; the explanation opens in
    one area under the set (2026-10-13)."""
    page.evaluate(f"document.querySelector('.d-tile--{key} .d-tile__more').click()")
    page.wait_for("!document.querySelector('.d-tiledetail').hidden", message="the details")
    return str(page.evaluate("document.querySelector('.d-tiledetail').innerText"))


def test_an_ad_that_lists_three_levels_shows_all_three_and_the_pay_the_ad_states(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    _open_job(page, facts_server["url"], facts_server["ids"]["pool"])
    level = _tile(page, "level")
    assert "Junior" in level and "Mid-level" in level and "Senior" in level
    assert "the ad lists several levels" in _tile_details(page, "level")
    assert "Read now from the ad" in level, "this score was made before the level was read"
    pay = _tile(page, "pay")
    assert "USD" in pay and "40" in pay and "85" in pay and "hour" in pay.lower()
    assert "Read now from the ad" in pay
    assert "talent pool" in _drawer(page).lower()
    assert "may not be an immediate opening" in _drawer(page)
    _tab(page, "prepare")
    text = _drawer(page)
    assert "Part-time, 10 to 15 hours a week" in text
    assert "time zones" in text.lower() and "not where to live" in text


def test_the_stored_score_is_not_moved_by_showing_facts_read_now(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    _open_job(page, facts_server["url"], facts_server["ids"]["pool"])
    shown = page.evaluate("document.querySelector('.d-fit__pct')?.innerText || ''")
    assert str(facts_server["pool_score"]) in str(shown).replace("%", "")
    conn = connect(facts_server["db"])
    try:
        again = conn.execute(
            "SELECT match_score FROM job_match WHERE job_id = ?", (facts_server["ids"]["pool"],)
        ).fetchone()[0]
    finally:
        conn.close()
    assert again == facts_server["pool_score"], "looking at it recalculated nothing"


def test_pay_the_ad_states_is_never_called_not_stated(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    _open_job(page, facts_server["url"], facts_server["ids"]["pool"])
    page.evaluate(
        "document.querySelector('.drawer__panel [data-tab=why], #drawer-tab-why')?.click()"
    )
    text = _drawer(page)
    assert "The ad states pay (" in text or "Pay" in text
    assert "Pay\nNot stated in the posting." not in text


def test_a_level_in_a_career_growth_list_is_not_the_level_and_the_default_says_it_is_one(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    _open_job(page, facts_server["url"], facts_server["ids"]["growth"])
    level = _tile(page, "level")
    # The tile says the level and that it is the app's default; its details
    # say why. A compact card or row says the level alone (2026-10-13).
    assert "Mid" in level and "App default" in level
    assert "does not state a level" in _tile_details(page, "level")
    assert "Senior" not in level
    compact = page.evaluate(
        "[...document.querySelectorAll('.fact--terms')].map((n) => n.textContent).join('|')"
    )
    assert "default" not in str(compact).lower() and "not stated in the ad" not in str(compact)
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    page.wait_for(
        "document.querySelector('.drawer__panel .d-tile--level')", timeout=30, message="pt"
    )
    level = _tile(page, "level")
    assert "Pleno" in level and "Padrão do app" in level


def test_the_ad_is_laid_out_from_its_lists_and_headings_with_moderate_spacing(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    _open_job(page, facts_server["url"], facts_server["ids"]["growth"])
    page.wait_for("document.querySelector('.drawer__panel .ad')", message="the laid out ad")
    shape = page.evaluate(
        """(() => {
          const ad = document.querySelector('.drawer__panel .ad');
          const lists = [...ad.querySelectorAll('ul.ad__list')];
          const items = lists[0].querySelectorAll('li');
          const gap = items[1].getBoundingClientRect().top - items[0].getBoundingClientRect().bottom;
          const heading = [...ad.querySelectorAll('h4.ad__h')].pop();
          const p = ad.querySelector('p.ad__p');
          const meta = ad.querySelector('dl.ad__meta');
          return {
            lists: lists.length, items: items.length, gap: Math.round(gap),
            headingTop: parseFloat(getComputedStyle(heading).marginTop),
            paragraphBottom: parseFloat(getComputedStyle(p).marginBottom),
            lineHeight: parseFloat(getComputedStyle(p).lineHeight) / parseFloat(getComputedStyle(p).fontSize),
            width: ad.getBoundingClientRect().width,
            meta: meta ? [...meta.querySelectorAll('dt')].map((n) => n.innerText) : [],
            oldParagraphs: document.querySelectorAll('.drawer__panel p.desc__p').length,
          };
        })()"""
    )
    assert shape["lists"] >= 2 and shape["items"] == 4, "list items stay together in one list"
    assert shape["gap"] <= 12, f"items in one list sit close: {shape['gap']}px"
    assert shape["headingTop"] > shape["gap"], "sections are separated more than items"
    assert 6 <= shape["paragraphBottom"] <= 14 and shape["lineHeight"] >= 1.5
    assert shape["meta"] == ["Position Type", "Location", "Working Hours"]
    assert shape["oldParagraphs"] == 0
    assert shape["width"] <= 800, "reading width, not the whole panel"


def test_the_ad_never_runs_markup_or_loads_anything(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    conn = connect(facts_server["db"])
    try:
        hostile = (
            '<p>Visible text <a href="javascript:alert(1)">bad link</a></p>'
            '<script>window.__ran = true</script><img src="https://evil.test/x.png">'
            '<iframe src="https://evil.test"></iframe>'
        )
        conn.execute(
            "UPDATE job_raw SET description_html = ? WHERE content_hash ="
            " (SELECT content_hash FROM job WHERE id = ?)",
            (hostile, facts_server["ids"]["growth"]),
        )
    finally:
        conn.close()
    _open_job(page, facts_server["url"], facts_server["ids"]["growth"])
    page.wait_for("document.querySelector('.drawer__panel .ad')", message="the ad")
    assert page.evaluate("window.__ran === undefined")
    assert (
        page.evaluate(
            "document.querySelectorAll('.drawer__panel .ad img, .drawer__panel .ad iframe, .drawer__panel .ad script').length"
        )
        == 0
    )
    assert page.evaluate(
        "[...document.querySelectorAll('.drawer__panel .ad a')].every((a) => /^https?:|^mailto:/.test(a.href))"
    )


ALL = "include_unresolved=true&include_ineligible=true&include_off_target=true"


def _count(page: Chrome, extra: str = "") -> dict[str, Any]:
    """The list as the API answers it, counts included (the default list hides
    postings whose eligibility is unresolved, which these synthetic ones are)."""
    return page.evaluate(  # type: ignore[no-any-return]
        f"fetch('/api/jobs?{ALL}&limit=50{extra}').then(r => r.json())"
        ".then(j => ({n: (j.jobs || j.items || []).length, age: j.hidden_by_age,"
        " undated: j.hidden_undated, view: j.view}))"
    )


def test_the_age_limit_hides_old_and_undated_postings_counts_them_and_can_be_lifted(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    page.set_viewport(1440, 1000)
    page.navigate(facts_server["url"] + "/#jobs")
    page.wait_for("document.querySelector('.fess__age')", message="the age control")
    default = _count(page)
    assert default["n"] == 3, "recent postings only"
    assert default["age"] == 1 and default["undated"] == 1
    text = str(page.evaluate("document.querySelector('.fess__age').innerText"))
    assert "120" in text and "150" in text and "No limit" in text
    assert "without a date" in text
    page.evaluate(
        "fetch('/api/view-prefs', {method: 'PATCH', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({max_age_days: null})})"
    )
    assert _count(page)["n"] == 5, "no limit shows the old and the undated"


def test_a_found_on_date_is_said_apart_when_there_is_no_publication_date(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    page.set_viewport(1440, 1000)
    page.navigate(f"{facts_server['url']}/?{ALL}#jobs")
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')", message="a card")
    page.evaluate(
        "fetch('/api/view-prefs', {method: 'PATCH', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({max_age_days: null, include_undated: true})})"
    )
    page.reload()
    page.wait_for(
        "document.querySelectorAll('.card:not(.card--skeleton)').length >= 5", message="all cards"
    )
    print(
        "POSTED>>>",
        page.evaluate("[...document.querySelectorAll('.fact--posted')].map(n => n.innerText)"),
        page.evaluate("fetch('/api/view-prefs').then(r=>r.json())"),
    )
    cards = str(page.evaluate("document.body.innerText"))
    assert "Found on" in cards, "the undated posting says when it was found, not posted"


def test_a_posting_the_person_added_says_so_and_is_never_shown_as_recent(
    page: Chrome, facts_server: dict[str, Any]
) -> None:
    conn = connect(facts_server["db"])
    try:
        conn.execute(
            "UPDATE job SET provider = 'manual_import' WHERE id = ?",
            (facts_server["ids"]["undated"],),
        )
    finally:
        conn.close()
    page.set_viewport(1440, 1000)
    page.navigate(f"{facts_server['url']}/?{ALL}#jobs")
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')", message="the list")
    cards = str(page.evaluate("document.getElementById('list').innerText"))
    assert "publication date not stated" in cards and "Added by you on" in cards
    assert (
        page.evaluate(
            "[...document.querySelectorAll('.fact--posted')].filter(n => /Added by you/.test(n.innerText)).length"
        )
        == 1
    )
    page.navigate(f"{facts_server['url']}/?job={facts_server['ids']['undated']}#jobs")
    page.wait_for("document.querySelector('.drawer__panel .d-tile')", message="the drawer")
    _tab(page, "prepare")
    assert "Publication date not stated (added by you)" in _drawer(page)
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    page.wait_for("document.querySelector('.drawer__panel .d-tile')", message="the drawer in PT")
    _tab(page, "prepare")
    assert "Publicação não informada (adicionada por você)" in _drawer(page)

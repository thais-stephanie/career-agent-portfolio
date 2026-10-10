"""Brazil relevance on screen (2026-10-10). Invented data only.

The list opens on work she can do from where she lives, with one switch for
the rest; the drawer tells a met preference from a silence; a title written
twice can be shown in its English half; and her skills are edited in place.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import _free_port
from tests.browser.test_first_run_flow import CARD, DEMO, FAILED, NAV, click_button, go
from tests.integration.test_resume_master import claim, experience, profile

from career_agent.clock import now_utc
from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import ClaimType
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.manual_import import import_posting
from career_agent.storage.db import transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

BILINGUAL = "Product Manager / プロダクトマネージャー(Customer Experience)"
SHOW_UNCERTAIN = "localStorage.setItem('careerAgent.includeUnresolved.v2', '1')"


@pytest.fixture
def app(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    conn = profile(tmp_path, "p")
    with transaction(conn):
        role = experience(conn, "Invented Works", "Operations Analyst", "2022-01", None, current=True)
        claim(conn, "k-routing", ClaimType.EMPLOYMENT, "Built lead routing for invented teams.", experience=role)
        claim(conn, "k-hubspot", ClaimType.TOOL, "HubSpot")
        claim(conn, "k-mapping", ClaimType.SKILL, "Process mapping")
        claim(conn, "k-cert", ClaimType.CERTIFICATION, "Invented Analytics Certificate")
    config = load_search_config(committed_config)[0]
    seed_demo(conn, config, source=DEMO)
    import_posting(
        conn,
        config,
        title=BILINGUAL,
        company="Invented Marketplace",
        description="You will own an invented product area end to end.",
        url="https://example.invalid/bilingual",
        location="Remote",
        config_id=config.config_id,
        config_version=config.config_version,
        now=now_utc(),
    )
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    port = _free_port()
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(api)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield {"url": f"http://127.0.0.1:{port}"}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def boot(page: Chrome, url: str, *, locale: str = "en", before: str = "") -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/")
    page.wait_for(NAV.format("jobs"))
    page.evaluate(f"localStorage.setItem('careerAgent.locale.v1', {json.dumps(locale)}); {before}")
    page.navigate(url + "/")
    page.wait_for(NAV.format("jobs"))


def listed(page: Chrome) -> int:
    go(page, "jobs")
    page.wait_for(f"{CARD} || {FAILED}", timeout=30)
    assert not page.evaluate(FAILED)
    return int(page.evaluate("document.querySelectorAll('#list [data-job-id]').length"))


def api(url: str, path: str) -> Any:
    with urlopen(url + path, timeout=30) as response:  # noqa: S310 - loopback test server
        return json.loads(response.read())


# =========================================================================
# 1. the list
# =========================================================================


def test_uncertain_territory_is_one_switch_away_not_on_the_list(page: Chrome, app: dict) -> None:
    boot(page, app["url"], before="localStorage.setItem('careerAgent.includeUnresolved.v1', '1')")
    hidden = listed(page)  # a value stored under the old default does not carry over
    boot(page, app["url"], before=SHOW_UNCERTAIN)
    shown = listed(page)
    assert shown > hidden
    labels = str(page.evaluate("document.body.innerHTML"))
    assert "Include jobs whose location is still to confirm" in labels


# =========================================================================
# 4. the drawer: met, no conflict, not stated
# =========================================================================


def test_silence_on_pay_is_left_open_never_ticked(page: Chrome, app: dict) -> None:
    boot(page, app["url"], before=SHOW_UNCERTAIN)
    listed(page)
    silent = next(
        j["job_id"]
        for j in api(app["url"], "/api/jobs?include_unresolved=1&limit=200")["items"]
        if not j.get("salary")
    )
    page.evaluate(f"document.querySelector('#list [data-job-id=\"{silent}\"]').click()")
    page.wait_for("document.querySelector('[data-assessment=\"not_stated\"]')", timeout=30)
    fits = str(page.evaluate(
        "[...document.querySelectorAll('.d-row')].filter(r => r.querySelector('.d-row__mark--yes'))"
        ".map(r => r.innerText).join('|')"
    ))
    assert "Pay" not in fits and "Compensation" not in fits
    open_rows = str(page.evaluate(
        "[...document.querySelectorAll('[data-assessment]')].map(r => r.innerText).join('|')"
    ))
    assert "Pay" in open_rows and "Not stated in the posting." in open_rows


# =========================================================================
# 5. a title written twice
# =========================================================================


def test_the_english_half_of_a_title_is_a_display_choice(page: Chrome, app: dict) -> None:
    # Every narrowing off: this test is about how a title is shown, not
    # about whether an invented product role is on her list.
    everything = (
        f"{SHOW_UNCERTAIN}; localStorage.setItem('careerAgent.includeOffTarget.v1', '1');"
        " localStorage.setItem('careerAgent.includeIneligible.v1', '1')"
    )
    boot(page, app["url"], before=everything)
    page.navigate(app["url"] + "/?search=Invented%20Marketplace")
    page.wait_for(NAV.format("jobs"))
    listed(page)
    title = "[...document.querySelectorAll('#list .card__title')].map(n => [n.textContent, n.title])"
    rows = page.evaluate(title)
    assert [BILINGUAL, BILINGUAL] in rows  # as the employer wrote it, by default
    go(page, "settings")
    page.wait_for("document.getElementById('settab-look')")
    page.evaluate("document.getElementById('settab-look').click()")
    click_button(page, "The English version, when there is one")
    listed(page)
    assert ["Product Manager (Customer Experience)", BILINGUAL] in page.evaluate(title)
    query = "/api/jobs?search=Invented%20Marketplace&include_unresolved=1&include_off_target=1"
    stored = [j["title"] for j in api(app["url"], query + "&include_ineligible=1")["items"]]
    assert BILINGUAL in stored  # the data never changed


# =========================================================================
# 6. skills, edited where they are shown
# =========================================================================


def open_skills(page: Chrome) -> None:
    go(page, "profile")
    page.wait_for("document.querySelector('.profile__glance .profile__skillsedit')", timeout=30)


def chips(page: Chrome) -> list[str]:
    return list(page.evaluate(
        "[...document.querySelectorAll('.profile__glance .evchip')].map(n => n.textContent)"
    ))


def test_skills_are_added_renamed_removed_and_cancel_changes_nothing(page: Chrome, app: dict) -> None:
    boot(page, app["url"])
    open_skills(page)
    before = chips(page)
    assert {"HubSpot", "Process mapping"} <= set(before)
    ledger_before = api(app["url"], "/api/evidence")["claims"]

    # Cancel: a change typed and abandoned is not saved.
    page.evaluate("document.querySelector('.profile__glance .profile__skillsedit').click()")
    page.wait_for("document.querySelector('.skilledit__add')")
    page.evaluate(
        "(() => { const i = document.querySelector('.skilledit__add'); i.value = 'Abandoned';"
        " i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); })()"
    )
    click_button(page, "^Cancel$")
    assert chips(page) == before

    # Rename one, remove one, add one, save.
    page.evaluate("document.querySelector('.profile__glance .profile__skillsedit').click()")
    page.wait_for("document.querySelector('.skilledit__name')")
    page.evaluate(
        "(() => { const row = [...document.querySelectorAll('.skilledit__row')]"
        ".find(r => r.querySelector('input').value === 'Process mapping');"
        " const i = row.querySelector('input'); i.value = 'Process design';"
        " i.dispatchEvent(new Event('input')); })()"
    )
    page.evaluate(
        "[...document.querySelectorAll('.skilledit__row')]"
        ".find(r => r.querySelector('input').value === 'HubSpot').querySelector('button').click()"
    )
    page.evaluate(
        "(() => { const i = document.querySelector('.skilledit__add'); i.value = 'Python';"
        " i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter'})); })()"
    )
    click_button(page, "^Save$")
    page.wait_for("document.querySelector('.profile__glance .profile__skillsedit')", timeout=30)

    # Persisted: a reload reads the same.
    boot(page, app["url"])
    open_skills(page)
    after = set(chips(page))
    assert {"Process design", "Python"} <= after
    assert "HubSpot" not in after and "Process mapping" not in after

    claims = api(app["url"], "/api/evidence")["claims"]
    by_key = {c["claim_key"]: c for c in claims}
    # Removing retired the claim; it was not deleted, and nothing else moved.
    assert by_key["k-hubspot"]["verified"] is False
    assert by_key["k-mapping"]["text"] == "Process design"
    typed = [c for c in claims if c["text"] == "Python"]
    assert [(c["claim_type"], c["source"]) for c in typed] == [("SKILL", "SELF_ATTESTED")]
    kinds = lambda rows: sorted(  # noqa: E731
        c["claim_type"] for c in rows if c["claim_type"] not in ("SKILL", "TOOL")
    )
    assert kinds(claims) == kinds(ledger_before)


def test_the_skills_editor_speaks_portuguese(page: Chrome, app: dict) -> None:
    boot(page, app["url"], locale="pt-BR")
    open_skills(page)
    page.evaluate("document.querySelector('.profile__glance .profile__skillsedit').click()")
    page.wait_for("document.querySelector('.skilledit__add')")
    text = str(page.evaluate("document.querySelector('.profile__glance').innerText"))
    assert "não cria experiência" in text
    assert "Salvar" in text and "Cancelar" in text

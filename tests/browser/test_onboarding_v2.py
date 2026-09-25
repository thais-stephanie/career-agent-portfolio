"""Onboarding V2, as a person meets it: journeys A to P.

Each journey starts from a personal install with nothing answered and the
shipped configuration. Every source is replaced by an offline stand-in, and the
CV is invented. What each answer is allowed to change is in docs/ONBOARDING.md;
these tests hold the setup to it and to one more promise: the setup and
Settings write the same configuration.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome

from career_agent.config.search_config import load_search_config
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
LOCALE_KEY = "careerAgent.locale.v1"

INVENTED_CV = """Alex Example
Implementation consultant

Experience
Implementation Consultant, Contoso Health, Jan 2019 - Feb 2021
- Ran customer onboarding for twelve clinics across three regions.
- Cut handover time between sales and support by 20% with a shared checklist.
- Wrote the integration runbook the support team still uses for new accounts.
Onboarding Analyst, Northwind Retail, Apr 2017 - Dec 2018
- Configured accounts and trained new customers on the reporting tools.

Skills
Solution design, API integration, customer onboarding, spreadsheets

Education
BSc Information Systems, Example University, 2016
"""


@dataclass
class Install:
    base: str
    app: JobsApi
    config_dir: Path
    db: Path
    port: int
    log: list[tuple[float, str, str]] = field(default_factory=list)
    httpd: object = None
    thread: threading.Thread | None = None

    def start(self) -> None:
        self.app = JobsApi(
            ServerConfig(db_path=self.db, config_dir=self.config_dir, port=self.port), quiet=True
        )
        original = self.app.handle_api

        def logged(method, path, query, body):
            self.log.append((time.monotonic(), method, path))
            return original(method, path, query, body)

        self.app.handle_api = logged  # type: ignore[method-assign]
        self.app._collect_work = lambda limit, provider=None: lambda state, cancel: None  # type: ignore[method-assign]
        self.app.rescore.start = lambda work, run_id: None  # type: ignore[method-assign]
        self.httpd = build_server(self.app)
        self.httpd.handle_error = lambda request, client_address: None  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)  # type: ignore[attr-defined]
        self.thread.start()

    def stop(self) -> None:
        self.httpd.shutdown()  # type: ignore[attr-defined]
        self.httpd.server_close()  # type: ignore[attr-defined]
        if self.thread:
            self.thread.join(timeout=10)

    def config(self):
        config, _ = load_search_config(self.config_dir)
        return config

    def requests(self, since: float) -> list[tuple[str, str]]:
        return [(method, path) for at, method, path in list(self.log) if at >= since]


@pytest.fixture
def install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Install]:
    config_dir = tmp_path / "config"
    shutil.copytree(REPO / "config", config_dir, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "onboarding v2")
    finally:
        conn.close()
    monkeypatch.setattr(
        "career_agent.web.source_refresh.feed_work",
        lambda db, stage, **_: lambda state, cancel: None,
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.employer_board_work",
        lambda app, families: lambda state, cancel: None,
    )
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    live = Install(f"http://127.0.0.1:{port}", None, config_dir, db, port)  # type: ignore[arg-type]
    live.start()
    try:
        yield live
    finally:
        live.stop()


# -------------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------------


def card(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.setup__card')?.dataset.step || ''"))


def wait_card(page: Chrome, key: str) -> None:
    page.wait_for(
        f"document.querySelector('.setup__card')?.dataset.step === {json.dumps(key)}",
        message=f"the {key} card",
    )


def click(page: Chrome, selector: str) -> None:
    page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def put(page: Chrome, selector: str, value: str, event: str = "input") -> None:
    page.evaluate(
        f"(() => {{ const n = document.querySelector({json.dumps(selector)});"
        f" n.value = {json.dumps(value)};"
        f" n.dispatchEvent(new Event({json.dumps(event)}, {{bubbles: true}})); }})()"
    )


def text(page: Chrome, selector: str) -> str:
    return str(page.evaluate(f"document.querySelector({json.dumps(selector)})?.innerText || ''"))


def begin(
    page: Chrome,
    live: Install,
    *,
    locale: str = "en",
    width: int = 1280,
    height: int = 900,
    mobile: bool = False,
) -> None:
    page.set_viewport(width, height, mobile=mobile)
    page.navigate(live.base)
    page.evaluate(f"localStorage.setItem({json.dumps(LOCALE_KEY)}, {json.dumps(locale)})")
    page.navigate(live.base)
    wait_card(page, "welcome")


def open_preferences(page: Chrome) -> None:
    click(page, '.topnav__link[data-page="profile"]')
    page.wait_for(
        "document.querySelectorAll('.profiletab').length > 0"
        " || document.querySelectorAll('.profile__form input').length > 0",
        message="the profile to paint",
    )
    page.evaluate(
        "(() => { const tab = [...document.querySelectorAll('.profiletab')]"
        ".find((b) => b.textContent.trim() === 'Preferences'); if (tab) tab.click(); })()"
    )
    page.wait_for(
        "document.querySelector('#profile-field-work_models-REMOTE-prefer')",
        message="the ways-of-working control in Settings",
    )


def next_card(page: Chrome) -> None:
    click(page, "#setup-next")


def skip(page: Chrome) -> None:
    click(page, "#setup-skip")


def no_overflow(page: Chrome) -> bool:
    return bool(page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1"))


def answer_everything(page: Chrome, live: Install) -> None:
    """Journey A's answers, from Welcome to the review."""
    next_card(page)
    wait_card(page, "work")
    put(page, "#setup-work", "customer onboarding\nimplementation")
    put(page, "#setup-skills", "HubSpot")
    next_card(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brazil", "change")
    next_card(page)
    wait_card(page, "hire")
    click(page, "#setup-hire-yes")
    next_card(page)
    # Brazil confirmed: no region could add anything, so no regions card.
    wait_card(page, "workmodel")
    click(page, "#setup-workmodel-REMOTE-prefer")
    click(page, "#setup-workmodel-HYBRID-fine")
    click(page, "#setup-workmodel-ONSITE-avoid")
    next_card(page)
    wait_card(page, "arrangement")
    click(page, "#setup-arrangement-CONTRACTOR_B2B-yes")
    click(page, "#setup-arrangement-FULL_TIME_EMPLOYEE-yes")
    click(page, "#setup-arrangement-EOR-no")
    next_card(page)
    wait_card(page, "level")
    click(page, "#setup-seniority_excluded-LEAD")
    next_card(page)
    wait_card(page, "pay")
    put(page, "#setup-pay", "9000")
    put(page, "#setup-currency", "BRL", "change")
    next_card(page)
    wait_card(page, "cv")


def add_cv(page: Chrome) -> None:
    """A real file in the real picker, as far as the page can tell."""
    page.evaluate(
        "(() => { const dt = new DataTransfer();"
        f" dt.items.add(new File([{json.dumps(INVENTED_CV)}], 'alex-cv.txt',"
        " {type: 'text/plain'}));"
        " const input = document.querySelector('#setup-cv-file'); input.files = dt.files;"
        " input.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )
    click(page, "#setup-cv-read")
    page.wait_for("document.querySelector('#setup-cv-added')", message="the CV to be read")


# =========================================================================
# A, I, O, P -- everything answered, and the same configuration as Settings
# =========================================================================


def test_a_new_person_answers_everything_and_the_configuration_says_so(
    page: Chrome, install: Install
) -> None:
    begin(page, install)
    answer_everything(page, install)
    add_cv(page)
    # The exact number the server staged, not merely the sentence.
    staged = page.evaluate(
        "fetch('/api/cv/imports').then(r => r.json()).then(d => d.imports[0].total)"
    )
    assert staged > 0
    assert text(page, "#setup-cv-status").startswith(f"{staged} statements found"), text(
        page, "#setup-cv-status"
    )
    next_card(page)
    wait_card(page, "review")

    review = text(page, ".setup__summary")
    for expected in (
        "customer onboarding",
        "Brazil",
        "Prefer Remote",
        "Rather avoid On-site",
        "Works: contractor",
        "Rather not: employer of record",
        "Lead",
        "9,000 BRL",
        "CV added",
    ):
        assert expected in review, (expected, review)
    for internal in (
        "eligible_scopes",
        "LATAM",
        "CONTRACTOR_B2B",
        "config_version",
        "display_order",
    ):
        assert internal not in review, internal

    config = install.config()
    assert config.eligibility.candidate_country == "BR"
    assert config.eligibility.eligible_countries == ["BR"]
    assert config.eligibility.eligible_scopes == []
    remote = config.preferences.remote
    assert remote.accepted_work_models == ["REMOTE"]
    assert remote.avoided_work_models == ["ONSITE"]
    assert remote.excluded_work_models == []
    assert remote.require_remote is False
    assert sorted(config.preferences.contract.preferred) == ["CONTRACTOR_B2B", "FULL_TIME_EMPLOYEE"]
    assert config.preferences.contract.unwanted == ["EOR"]
    assert [level.value for level in config.preferences.seniority.excluded] == ["LEAD"]
    assert config.preferences.compensation.target_monthly_amount == 9000
    assert config.preferences.compensation.currency == "BRL"

    # O: the CV was read, and nothing was confirmed by reading it.
    with connect(install.db) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM verified_claim WHERE verified=1").fetchone()[0] == 0
        )

    next_card(page)
    wait_card(page, "ready")
    assert "What now" in text(page, "#setup-title")
    assert not page.evaluate("Boolean(document.querySelector('#setup-add-cv'))")
    click(page, "#setup-find")
    page.wait_for("document.querySelector('#setup-see')", timeout=30)

    # P: what the setup wrote is what Settings shows, in the same control.
    open_preferences(page)
    for selector in (
        "#profile-field-work_models-REMOTE-prefer",
        "#profile-field-work_models-ONSITE-avoid",
        "#profile-field-contract_preferred-CONTRACTOR_B2B-yes",
        "#profile-field-contract_preferred-EOR-no",
    ):
        assert page.evaluate(f"document.querySelector({json.dumps(selector)}).checked"), selector
    # This install's own errors. The browser is shared across tests, and a page
    # from the previous test can report its server's shutdown after the
    # console was cleared for this one.
    mine = [e for e in page.console_errors() if install.base in str(e.get("url", install.base))]
    assert mine == [], mine


def test_a_change_in_settings_is_what_the_review_shows(page: Chrome, install: Install) -> None:
    """P, the other way round: one configuration, two screens."""
    begin(page, install)
    open_preferences(page)
    click(page, "#profile-field-work_models-HYBRID-never")
    click(page, "#profile-field-work_models-ONSITE-never")
    page.evaluate("document.querySelector('.profile__actions .btn--primary').click()")
    page.wait_for("document.querySelector('.profile__save-status').classList.contains('is-ok')")
    config = install.config()
    assert config.preferences.remote.excluded_work_models == ["HYBRID", "ONSITE"]
    assert config.preferences.remote.require_remote is True, "only remote is derived on the server"

    click(page, '.topnav__link[data-page="settings"]')
    page.wait_for("document.querySelector('#settings-open-setup')")
    click(page, "#settings-open-setup")
    wait_card(page, "welcome")
    page.evaluate("localStorage.setItem('careerAgent.setup.at.v1', 'review')")
    page.reload()
    wait_card(page, "review")
    assert "Never show Hybrid · Never show On-site" in text(page, ".setup__summary")
    click(page, "#setup-edit-workmodel")
    wait_card(page, "workmodel")
    assert page.evaluate("document.querySelector('#setup-workmodel-HYBRID-never').checked")
    # Change from the review returns to the review.
    click(page, "#setup-workmodel-REMOTE-prefer")
    assert text(page, "#setup-next") == "Save and go back"
    next_card(page)
    wait_card(page, "review")
    assert "Prefer Remote" in text(page, ".setup__summary")


# =========================================================================
# B, N -- nothing answered
# =========================================================================


def test_every_question_can_be_skipped_and_nothing_is_invented(
    page: Chrome, install: Install
) -> None:
    begin(page, install)
    next_card(page)
    for key in ("work", "roles", "home", "hire", "workmodel", "arrangement", "level", "pay"):
        wait_card(page, key)
        skip(page)
    wait_card(page, "cv")
    # N: no CV, and the page says the search works without one.
    assert "without adding a CV" in text(page, ".setup__card")
    next_card(page)
    wait_card(page, "review")
    review = text(page, ".setup__summary")
    assert "Not answered" in review and "No preference" in review and "Not added yet" in review
    next_card(page)
    wait_card(page, "ready")
    assert page.evaluate("Boolean(document.querySelector('#setup-add-cv'))")
    assert "without a CV" in text(page, ".setup__card")
    assert not (install.config_dir / "search.local.yaml").exists(), "a skip wrote an answer"


# =========================================================================
# C, D, E -- persistence
# =========================================================================


def test_a_reload_halfway_comes_back_to_the_same_card_with_the_answers(
    page: Chrome, install: Install
) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    put(page, "#setup-work", "payroll administration")
    next_card(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brazil", "change")
    next_card(page)
    wait_card(page, "hire")

    page.reload()
    wait_card(page, "hire")
    click(page, "#setup-back")
    wait_card(page, "home")
    assert page.evaluate("document.querySelector('#setup-country').dataset.code") == "BR"
    click(page, "#setup-back")
    wait_card(page, "roles")
    click(page, "#setup-back")
    wait_card(page, "work")
    assert "payroll administration" in text(page, ".setup__saved")


def test_a_restart_halfway_comes_back_to_the_same_card(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    skip(page)
    wait_card(page, "hire")
    skip(page)
    wait_card(page, "workmodel")
    click(page, "#setup-workmodel-REMOTE-prefer")
    next_card(page)
    wait_card(page, "arrangement")

    install.stop()
    install.start()
    page.navigate(install.base)
    wait_card(page, "arrangement")
    click(page, "#setup-back")
    wait_card(page, "workmodel")
    assert page.evaluate("document.querySelector('#setup-workmodel-REMOTE-prefer').checked")


def test_back_all_the_way_shows_every_answer(page: Chrome, install: Install) -> None:
    begin(page, install)
    answer_everything(page, install)
    expected = {
        "pay": "document.querySelector('#setup-pay').value === '9000'",
        "level": "document.querySelector('#setup-seniority_excluded-LEAD').checked",
        "arrangement": "document.querySelector('#setup-arrangement-EOR-no').checked",
        "workmodel": "document.querySelector('#setup-workmodel-ONSITE-avoid').checked",
        "hire": "document.querySelector('#setup-hire-yes').checked",
        "home": "document.querySelector('#setup-country').dataset.code === 'BR'",
    }
    for key in ("pay", "level", "arrangement", "workmodel", "hire", "home", "roles", "work"):
        click(page, "#setup-back")
        wait_card(page, key)
        if key in expected:
            assert page.evaluate(expected[key]), key
    click(page, "#setup-back")
    wait_card(page, "welcome")


# =========================================================================
# F, G, H -- where you live and where you can be hired
# =========================================================================


def test_a_new_country_asks_the_hiring_question_again(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brazil", "change")
    next_card(page)
    wait_card(page, "hire")
    click(page, "#setup-hire-yes")
    next_card(page)
    # A: Brazil confirmed, so no redundant regions card.
    wait_card(page, "workmodel")

    click(page, "#setup-back")
    wait_card(page, "hire")
    click(page, "#setup-back")
    wait_card(page, "home")
    put(page, "#setup-country", "Portugal", "change")
    next_card(page)
    wait_card(page, "hire")
    assert "Portugal" in text(page, ".setup__legend")
    assert not page.evaluate("document.querySelector('#setup-hire-yes').checked"), (
        "Brazil's answer was carried over to Portugal"
    )
    next_card(page)
    # Living in Portugal, with Brazil still confirmed: Worldwide already admits
    # through Brazil, so only EMEA could add something.
    wait_card(page, "regions")
    assert page.evaluate(
        "[...document.querySelectorAll('[id^=setup-eligible_scopes-]')].map(n => n.value)"
    ) == ["EMEA"]
    config = install.config()
    assert config.eligibility.candidate_country == "PT"
    assert config.eligibility.eligible_countries == ["BR"]


def test_not_sure_stays_unknown(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brazil", "change")
    next_card(page)
    wait_card(page, "hire")
    click(page, "#setup-hire-unsure")
    next_card(page)
    wait_card(page, "regions")
    skip(page)
    config = install.config()
    assert config.eligibility.candidate_country == "BR"
    assert config.eligibility.eligible_countries == [], "residence became eligibility"
    page.evaluate("localStorage.setItem('careerAgent.setup.at.v1', 'review')")
    page.reload()
    wait_card(page, "review")
    assert "Not known yet" in text(page, ".setup__summary")


def test_preferring_remote_is_not_being_hireable_anywhere(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brazil", "change")
    next_card(page)
    wait_card(page, "hire")
    click(page, "#setup-hire-yes")
    next_card(page)
    wait_card(page, "workmodel")
    click(page, "#setup-workmodel-REMOTE-prefer")
    click(page, "#setup-workmodel-ONSITE-never")
    assert "not where companies can hire you" in text(page, ".setup__card")
    next_card(page)
    wait_card(page, "arrangement")
    config = install.config()
    assert config.eligibility.eligible_countries == ["BR"]
    assert "WORLDWIDE" not in config.eligibility.eligible_scopes
    assert config.preferences.remote.accepted_work_models == ["REMOTE"]
    assert config.preferences.remote.excluded_work_models == ["ONSITE"]


# =========================================================================
# J, K -- Portuguese
# =========================================================================


def english_only_strings(page: Chrome) -> list[str]:
    """Catalogue values that differ between the languages, from the page's own
    module, so a Portuguese card can be searched for leftover English."""
    return list(
        page.evaluate(
            "import('/js/i18n.js').then((m) => {"
            "  const keys = m.catalogueKeys('en')"
            "    .filter((k) => /^(setup|workModel|arrangement)\\./.test(k));"
            "  const now = m.getLocale();"
            "  m.setLocale('en'); const en = Object.fromEntries(keys.map((k) => [k, m.t(k)]));"
            "  m.setLocale('pt-BR'); const pt = Object.fromEntries(keys.map((k) => [k, m.t(k)]));"
            "  m.setLocale(now);"
            "  return keys.filter((k) => en[k] !== pt[k] && !/[{]/.test(en[k]) && en[k].length > 3)"
            "    .map((k) => en[k]); })"
        )
    )


def test_the_whole_setup_is_portuguese_from_the_start(page: Chrome, install: Install) -> None:
    begin(page, install, locale="pt-BR")
    english = english_only_strings(page)
    assert english
    seen = []
    for _ in range(14):
        step = card(page)
        seen.append(step)
        body = text(page, ".setup")
        leaked = [phrase for phrase in english if phrase in body]
        assert not leaked, (step, leaked)
        if step == "ready":
            break
        if step == "home":
            put(page, "#setup-country", "Brasil", "change")
            assert page.evaluate("document.querySelector('#setup-country').dataset.code") == "BR"
            next_card(page)
            page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'hire'")
            continue
        page.evaluate(
            "(document.querySelector('#setup-skip')"
            " || document.querySelector('#setup-next')).click()"
        )
        page.wait_for(
            f"document.querySelector('.setup__card')?.dataset.step !== {json.dumps(step)}"
        )
    assert seen[-1] == "ready", seen
    assert "regions" in seen, "the regions card was not offered for Brazil"


def test_switching_language_halfway_keeps_answers_and_unsaved_text(
    page: Chrome, install: Install
) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    put(page, "#setup-work", "gestão de folha")
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for("document.querySelector('#setup-title').innerText.includes('trabalho')")
    assert page.evaluate("document.querySelector('#setup-work').value") == "gestão de folha"
    next_card(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Brasil")
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")
    page.wait_for("document.querySelector('#setup-title').innerText.includes('live')")
    assert page.evaluate("document.querySelector('#setup-country').value") == "Brazil"


# =========================================================================
# L, M -- a phone, and a keyboard
# =========================================================================


def test_every_card_fits_a_phone(page: Chrome, install: Install) -> None:
    begin(page, install, width=390, height=844, mobile=True)
    answer_everything(page, install)
    assert no_overflow(page), "cv"
    for key in ("review", "ready"):
        next_card(page)
        wait_card(page, key)
        assert no_overflow(page), key
    for key in ("pay", "level", "arrangement", "workmodel", "hire", "home", "roles", "work"):
        page.evaluate("localStorage.setItem('careerAgent.setup.at.v1', " + json.dumps(key) + ")")
        page.reload()
        wait_card(page, key)
        assert no_overflow(page), key


def focused(page: Chrome) -> str:
    return str(page.evaluate("document.activeElement?.id || document.activeElement?.tagName"))


def tab_to(page: Chrome, element_id: str, limit: int = 30) -> None:
    for _ in range(limit):
        if focused(page) == element_id:
            return
        page.press("Tab")
    raise AssertionError(f"Tab never reached #{element_id}; stopped on {focused(page)}")


def test_the_setup_can_be_completed_with_a_keyboard(page: Chrome, install: Install) -> None:
    begin(page, install)
    tab_to(page, "setup-next")
    page.press("Space")
    wait_card(page, "work")
    assert focused(page) == "setup-title", "focus did not move to the new question"
    tab_to(page, "setup-work")
    page.type_text("customer onboarding")
    tab_to(page, "setup-next")
    page.press("Space")
    wait_card(page, "roles")
    tab_to(page, "setup-skip")
    page.press("Space")
    wait_card(page, "home")
    tab_to(page, "setup-country")
    page.type_text("Brazil")
    tab_to(page, "setup-next")
    page.press("Space")
    wait_card(page, "hire")
    tab_to(page, "setup-hire-yes")
    page.press("Space")
    tab_to(page, "setup-next")
    page.press("Space")
    wait_card(page, "workmodel")
    # A radio group per row: Tab reaches the chosen answer, arrows move it.
    # Buttons are pressed with Space: the harness sends raw key-downs, which
    # never produce the character an Enter activation waits for.
    tab_to(page, "setup-workmodel-REMOTE-fine")
    page.press("ArrowLeft")
    assert page.evaluate("document.querySelector('#setup-workmodel-REMOTE-prefer').checked")
    tab_to(page, "setup-next")
    page.press("Space")
    wait_card(page, "arrangement")
    config = install.config()
    assert config.eligibility.candidate_country == "BR"
    assert config.eligibility.eligible_countries == ["BR"]
    assert config.preferences.remote.accepted_work_models == ["REMOTE"]
    # Every field on the way was named for a screen reader.
    for selector in ("#setup-arrangement", "#setup-arrangement-EOR-no"):
        assert page.evaluate(f"Boolean(document.querySelector({json.dumps(selector)}))")
    unnamed = page.evaluate(
        "[...document.querySelectorAll("
        "'.setup__card input, .setup__card select, .setup__card textarea')]"
        ".filter((n) => !(n.labels && n.labels.length) && !n.getAttribute('aria-label'))"
        ".map((n) => n.id)"
    )
    assert unnamed == [], unnamed


def test_errors_are_announced_beside_the_field(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Atlantis")
    next_card(page)
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    assert page.evaluate("document.querySelector('#setup-error').getAttribute('role')") == "alert"
    assert (
        page.evaluate("document.querySelector('#setup-country').getAttribute('aria-invalid')")
        == "true"
    )
    assert "setup-error" in str(
        page.evaluate("document.querySelector('#setup-country').getAttribute('aria-describedby')")
    )
    assert card(page) == "home"


# =========================================================================
# performance: requests per card
# =========================================================================


def test_each_card_costs_a_bounded_number_of_requests(page: Chrome, install: Install) -> None:
    begin(page, install)
    since = time.monotonic()
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    assert install.requests(since) == [], "moving between cards asked the server"

    put(page, "#setup-country", "Brazil", "change")
    since = time.monotonic()
    next_card(page)
    wait_card(page, "hire")
    asked = install.requests(since)
    # The save, then one read-back of what the server derived.
    assert asked.count(("PATCH", "/api/profile")) == 1
    assert len(asked) <= 3, asked


def test_leaving_the_setup_by_the_navigation_does_not_bring_it_back_on_reload(
    page: Chrome, install: Install
) -> None:
    """Resume is for a reload or a restart in the middle of the setup, not a way
    for onboarding to reappear on every visit after somebody walked away."""
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    # One answer, so this is no longer a fresh install that offers the setup
    # by itself on every load.
    put(page, "#setup-work", "customer onboarding")
    next_card(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    click(page, '.topnav__link[data-page="jobs"]')
    page.reload()
    page.wait_for("document.querySelector('.home__head') || document.querySelector('#list')")
    click(page, '.topnav__link[data-page="home"]')
    page.wait_for("document.querySelector('.home__head')", message="Home, not the setup")
    assert not page.evaluate("Boolean(document.querySelector('.setup__card'))")


def test_the_ready_card_is_not_resumed(page: Chrome, install: Install) -> None:
    begin(page, install)
    page.evaluate("localStorage.setItem('careerAgent.setup.at.v1', 'review')")
    page.reload()
    wait_card(page, "review")
    next_card(page)
    wait_card(page, "ready")
    page.reload()
    # Nothing was answered, so this is still a fresh install and the setup
    # offers itself from the start -- but never back on the finished card.
    page.wait_for(
        "document.querySelector('.home__head') || document.querySelector('.setup__card')",
        message="Home or the start of the setup",
    )
    assert card(page) in ("", "welcome"), card(page)


def test_the_same_answers_in_another_order_are_not_a_change(page: Chrome, install: Install) -> None:
    """Saving them would bump the configuration version and ask for a rescore."""
    from career_agent.config.candidate_writer import set_candidate_fields

    set_candidate_fields(install.config_dir, {"work_models": ["HYBRID", "REMOTE"]})
    install.app._search_config = None
    begin(page, install)
    click(page, "#setup-later")
    page.wait_for("document.querySelector('.home__head')")
    open_preferences(page)
    click(page, "#profile-field-work_models-REMOTE-fine")
    page.wait_for("!document.querySelector('.profile__actions .btn--primary').disabled")
    click(page, "#profile-field-work_models-REMOTE-prefer")
    page.wait_for(
        "document.querySelector('.profile__actions .btn--primary').disabled",
        message="the answers back where they were, with nothing to save",
    )


# =========================================================================
# the fresh-user QA findings
# =========================================================================


def skip_to(page: Chrome, target: str) -> None:
    """Skip forward in this page (so any stub installed on it stays in place)."""
    for _ in range(12):
        if card(page) == target:
            return
        step = card(page)
        page.evaluate(
            "(document.querySelector('#setup-skip')"
            " || document.querySelector('#setup-next')).click()"
        )
        page.wait_for(
            f"document.querySelector('.setup__card')?.dataset.step !== {json.dumps(step)}"
        )
    wait_card(page, target)


def test_the_cv_card_shows_the_count_the_server_staged(page: Chrome, install: Install) -> None:
    """The server says `total: 9`; the card says 9. It said 0 when it counted a
    list that is not at the top level of the response."""
    begin(page, install)
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) => {"
        "  const answer = real(url, opts);"
        "  if (!String(url).endsWith('/api/cv/import')) return answer;"
        "  return answer.then((r) => r.json()).then((body) => new Response("
        "    JSON.stringify({ ...body, total: 9 }),"
        "    { status: 200, headers: { 'Content-Type': 'application/json' } })); }; })()"
    )
    skip_to(page, "cv")
    add_cv(page)
    assert text(page, "#setup-cv-status").startswith("9 statements found"), text(
        page, "#setup-cv-status"
    )


def test_progress_names_the_step_and_never_a_total_that_can_change(
    page: Chrome, install: Install
) -> None:
    begin(page, install)
    next_card(page)
    seen = []
    for _ in range(12):
        step = card(page)
        if step in ("review", "ready"):
            break
        count = text(page, ".setup__count")
        bar = page.evaluate(
            "(() => { const b = document.querySelector('.setup__card [role=progressbar]');"
            " return [b.getAttribute('aria-valuetext'), Number(b.getAttribute('aria-valuenow'))];"
            " })()"
        )
        assert re.fullmatch(r"Step \d+", count), count
        assert bar[0] == count, bar
        seen.append((step, int(count.split()[1]), bar[1]))
        if step == "home":
            put(page, "#setup-country", "Brazil", "change")
            next_card(page)
            page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'hire'")
            continue
        page.evaluate(
            "(document.querySelector('#setup-skip')"
            " || document.querySelector('#setup-next')).click()"
        )
        page.wait_for(
            f"document.querySelector('.setup__card')?.dataset.step !== {json.dumps(step)}"
        )
    numbers = [number for _, number, _ in seen]
    assert numbers == list(range(1, len(numbers) + 1)), seen
    assert [done for _, _, done in seen] == sorted(done for _, _, done in seen), seen
    assert "regions" in [key for key, _, _ in seen], "the conditional card was not in this walk"


def test_an_error_clears_as_soon_as_its_field_is_valid(page: Chrome, install: Install) -> None:
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Atlantis")
    next_card(page)
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    # Another invalid value keeps the error.
    put(page, "#setup-country", "Atlan")
    assert text(page, "#setup-error")
    invalid = "document.querySelector('#setup-country').getAttribute('aria-invalid')"
    assert page.evaluate(invalid) == "true"
    # A valid one clears it at once, before Continue.
    put(page, "#setup-country", "Canada")
    page.wait_for("document.querySelector('#setup-error').textContent === ''")
    assert page.evaluate(invalid) is None
    assert card(page) == "home"

    # An error about one field is not cleared by fixing another.
    skip(page)
    skip_to(page, "pay")
    put(page, "#setup-pay", "-5")
    put(page, "#setup-currency", "CAD", "change")
    next_card(page)
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    put(page, "#setup-currency", "USD", "change")
    assert text(page, "#setup-error"), "fixing another field cleared this error"
    put(page, "#setup-pay", "4000")
    page.wait_for("document.querySelector('#setup-error').textContent === ''")
    assert (
        page.evaluate("document.querySelector('#setup-pay').getAttribute('aria-invalid')") is None
    )


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_an_invalid_field_is_marked_without_relying_on_colour(
    page: Chrome, install: Install, scheme: str
) -> None:
    page.set_color_scheme(scheme)
    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    skip(page)
    wait_card(page, "roles")
    skip(page)
    wait_card(page, "home")
    put(page, "#setup-country", "Atlantis")
    next_card(page)
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    field = page.evaluate(
        "(() => { const s = getComputedStyle(document.querySelector('#setup-country'));"
        " return [s.outlineStyle, parseFloat(s.outlineWidth)]; })()"
    )
    assert field[0] == "solid" and field[1] >= 3, field
    message = page.evaluate(
        "(() => { const n = document.querySelector('#setup-error');"
        " const s = getComputedStyle(n); const mark = getComputedStyle(n, '::before');"
        " return [s.borderLeftStyle, parseFloat(s.borderLeftWidth), mark.backgroundImage,"
        "  n.getAttribute('role')]; })()"
    )
    assert message[0] == "solid" and message[1] >= 6, message
    assert "px-warn" in message[2], "the message carries no mark beside its colour"
    assert message[3] == "alert"
    assert "setup-error" in str(
        page.evaluate("document.querySelector('#setup-country').getAttribute('aria-describedby')")
    )
    page.set_color_scheme(None)


def test_roles_in_mind_are_optional_and_saved_as_the_persons_own_words(
    page: Chrome, install: Install
) -> None:
    from career_agent.discovery.anchors import load_anchors

    begin(page, install)
    next_card(page)
    wait_card(page, "work")
    put(page, "#setup-work", "customer onboarding")
    next_card(page)
    wait_card(page, "roles")
    assert "other titles" in text(page, ".setup__card")
    put(page, "#setup-roles-anchors", "Hair Stylist")
    page.evaluate(
        "document.querySelector('#setup-roles-anchors').dispatchEvent("
        "new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))"
    )
    page.wait_for("document.querySelector('.tags__pills').children.length > 0")
    next_card(page)
    wait_card(page, "home")
    saved = load_anchors(install.config_dir)
    assert [(a.text, a.source) for a in saved.anchors] == [("Hair Stylist", "user")]
    assert "Hairdresser" in {a.text for a in saved.aliases}

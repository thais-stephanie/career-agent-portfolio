"""Three invented candidates, from neutral empty state through a persisted job journey."""

from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

import pytest
from tests.browser.conftest import _free_port
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_browser_acceptance import choose_theme, open_list, set_value
from tests.browser.test_evidence_workspace import answer, open_evidence, open_next
from tests.browser.test_profile_editing import open_profile

from career_agent.clock import now_utc
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.manual_import import import_posting
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

PERSONAS = [
    (
        "A",
        "BR",
        "MID",
        "HYBRID",
        "Executive Assistant",
        "calendar management",
        "Excel",
        "Managed calendar management and meeting preparation for a regional office.",
    ),
    (
        "B",
        "BR",
        "JUNIOR",
        "ONSITE",
        "Retail Assistant",
        "customer service",
        "Point of sale",
        "Provided customer service and prepared clothing displays during an internship.",
    ),
    (
        "C",
        "US",
        "SENIOR",
        "REMOTE",
        "Customer Success Specialist",
        "customer onboarding",
        "Zendesk",
        "Led customer onboarding and coordinated support escalations for business clients.",
    ),
]


class Workspace:
    def __init__(self, root):
        self.db = root / "personal.db"
        self.config = root / "config"
        source = Path(__file__).resolve().parents[2] / "config"
        shutil.copytree(source, self.config, ignore=shutil.ignore_patterns("*.local.*"))
        with connect(self.db) as conn:
            migrate(conn)
            with transaction(conn):
                stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic release rehearsal")
        self.port = _free_port()
        self.start()

    def start(self):
        self.app = JobsApi(
            ServerConfig(db_path=self.db, config_dir=self.config, port=self.port), quiet=True
        )
        self.server = build_server(self.app)
        self.server.handle_error = lambda *args: None
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(tmp_path)
    try:
        yield ws
    finally:
        ws.stop()


def input_value(page, selector, value, event="input"):
    set_value(page, f"document.querySelector({json.dumps(selector)})", value, event)


@pytest.mark.parametrize("persona", PERSONAS, ids=["admin-br", "retail-br", "success-us"])
def test_complete_persona_journey(page, workspace, persona):
    code, country, level, model, title, phrase, tool, evidence = persona
    ws = workspace
    open_home_past_setup(page, ws.base)
    assert not (ws.config / "search.local.yaml").exists()
    assert ws.app.handle_api("GET", "/api/health", {}, {})["job_count"] == 0
    input_value(page, "#fr-work", phrase)
    input_value(page, "#fr-skills", tool)
    page.evaluate("document.querySelector('#fr-search-save').click()")
    page.wait_for("document.querySelector('[data-step=work]').classList.contains('is-done')")
    open_profile(page, ws.base)
    input_value(page, "#profile-field-candidate_country", country, "change")
    input_value(page, "#profile-field-eligible_countries", country)
    page.evaluate(
        "document.querySelector('#profile-field-eligible_countries')"
        ".dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true}))"
    )
    page.evaluate(f"document.querySelector('#profile-field-work_models-{model}-prefer').click()")
    page.evaluate(f"document.querySelector('#profile-field-seniority_preferred-{level}').click()")
    page.evaluate("document.querySelector('.profile__actions .btn--primary').click()")
    page.wait_for("document.querySelector('.profile__save-status').classList.contains('is-ok')")
    config, _ = load_search_config(ws.config)
    assert config.eligibility.candidate_country == country
    assert country in config.eligibility.eligible_countries
    text = (ws.config / "search.local.yaml").read_text(encoding="utf-8").lower()
    assert not any(word in text for word in ("hubspot", "salesforce", "revops", "thais"))

    open_evidence(page, ws.base)
    cv = (
        f"Alex Example {code}\n{title}\n\nExperience\n"
        f"{title}, Invented Company {code}, Jan 2022 to Dec 2024\n{evidence}\n"
        "Maintained accurate records and communicated follow-up actions to colleagues.\n"
        "Prepared clear written updates and coordinated daily tasks with the team.\n"
        f"\nSkills\n{tool}, written communication, documentation\n"
        "\nEducation\nDiploma in Business Administration, Invented College, 2021\n"
    )
    page.evaluate(
        "(() => { const d = new DataTransfer();"
        f"d.items.add(new File([{json.dumps(cv)}], 'synthetic-cv.txt', {{type:'text/plain'}}));"
        "const i=document.querySelector('#ev-file'); i.files=d.files;"
        "i.dispatchEvent(new Event('change',{bubbles:true})); })()"
    )
    # The review opens on its summary: the job this CV describes, read as a
    # company, a role and dates, and nothing confirmed.
    page.wait_for(
        "document.querySelector('#page-manage .cvr__summary') !== null"
        " || document.querySelector('.ev__flash--bad')"
    )
    assert not page.evaluate("Boolean(document.querySelector('.ev__flash--bad'))"), page.evaluate(
        "document.querySelector('.ev__flash--bad')?.textContent"
    )
    row = str(page.evaluate("document.querySelector('#page-manage .cvr__row').textContent"))
    assert f"Invented Company {code}" in row and title in row, row
    open_next(page)
    answer(page, 0, "Yes, that is true")
    page.wait_for("document.querySelector('#page-manage .ev__decision')")
    with connect(ws.db) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM verified_claim WHERE verified=1").fetchone()[0] == 1
        )

    # Invented inventory through the real importer. No live source success is claimed.
    country_name = "Brazil" if country == "BR" else "United States"
    with connect(ws.db) as conn:
        job = import_posting(
            conn,
            config,
            title=title,
            company=f"Invented Company {code}",
            description=f"We hire candidates in {country_name} only. "
            f"This is a {model.lower()} role. "
            f"You will perform {phrase}. Experience with {tool} is required. "
            "We require written communication and accurate documentation.",
            url=f"https://example.invalid/{code}",
            location=country_name,
            config_id=config.config_id,
            config_version=config.config_version,
            now=now_utc(),
        )
    open_list(page, ws.base)
    page.wait_for("document.querySelector('#hiddennotice .hidden__show')")
    # Manual imports have no provider-declared scope. Explicitly inspect uncertainty,
    # never silently reclassify it as employer permission to make the journey pass.
    page.evaluate("document.querySelector('#hiddennotice .hidden__show').click()")
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')")
    assert title in str(page.evaluate("document.querySelector('#list').innerText"))
    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.startsWith('Show filters'))?.click()"
    )
    page.wait_for("document.querySelector('#f-search')")
    input_value(page, "#f-search", "no-such-invented-vacancy")
    page.wait_for("!document.querySelector('.card:not(.card--skeleton)')")
    input_value(page, "#f-search", "")
    page.wait_for("document.querySelector('.card:not(.card--skeleton)')")
    page.evaluate("document.querySelector('.card:not(.card--skeleton)').click()")
    page.wait_for("document.querySelector('#drawer-tab-why')")
    page.evaluate("document.querySelector('#drawer-tab-why').click()")
    page.wait_for("document.querySelector('#drawer-panel-why').innerText.length > 30")
    page.evaluate("document.querySelector('#drawer-tab-prepare').click()")
    page.wait_for("document.querySelector('#drawer-panel-prepare').innerText.length > 50")
    assert page.evaluate("document.querySelectorAll('#drawer-panel-prepare .prep__row').length") > 0
    page.screenshot(Path(f"out/rc-persona-{code}-prepare.png"))
    page.press("Escape")
    page.evaluate("document.querySelector('.card__save').click()")
    page.wait_for("document.querySelector('.card__save.is-saved')")
    page.evaluate(
        "document.querySelector('.card .select--status').value='SHORTLISTED';"
        "document.querySelector('.card .select--status')"
        ".dispatchEvent(new Event('change',{bubbles:true}))"
    )
    # The page's own signal that the save reached the server -- not the
    # control's value, which this test set itself and is true at once.
    page.wait_for("!document.documentElement.hasAttribute('data-saving')")
    page.wait_for("document.querySelector('.card .select--status').value === 'SHORTLISTED'")
    page.evaluate("document.querySelector('.topnav__link[data-page=\"applications\"]').click()")
    page.wait_for("document.querySelector('.kcard .select--status')")
    set_value(page, "document.querySelector('.kcard .select--status')", "APPLIED", "change")
    page.wait_for("!document.documentElement.hasAttribute('data-saving')")
    page.wait_for("document.querySelector('.kcard .select--status').value === 'APPLIED'")
    choose_theme(page, "dark")
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.set_viewport(390, 844)
    page.wait_for("document.querySelector('.sidenav').getBoundingClientRect().right <= 1")
    page.wait_for("document.querySelector('.appmain').getBoundingClientRect().left >= 0")
    page.screenshot(Path(f"out/rc-persona-{code}-mobile.png"))
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ws.stop()
    ws.start()
    open_home_past_setup(page, ws.base)
    with connect(ws.db) as conn:
        assert (
            conn.execute("SELECT status FROM job_application WHERE job_id=?", (job,)).fetchone()[0]
            == "APPLIED"
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM verified_claim WHERE verified=1").fetchone()[0] == 1
        )
    assert page.evaluate("document.documentElement.dataset.theme") == "dark"
    assert page.evaluate("localStorage.getItem('careerAgent.locale.v1')") == "pt-BR"
    page.evaluate("document.querySelector('.topnav__link[data-page=\"settings\"]').click()")
    page.wait_for("document.querySelector('.maintenance')")
    assert "30" in str(page.evaluate("document.querySelector('.maintenance').innerText"))

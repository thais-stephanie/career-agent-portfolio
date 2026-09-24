"""Candidate work at experience scale, through the real browser and scratch database."""

import json
import shutil
import threading
import time

import pytest
from tests.browser.conftest import _free_port
from tests.browser.test_evidence_workspace import open_evidence

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.storage.career_repo import CareerRepo
from career_agent.storage.db import connect, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server


def click(page, text, within=".career"):
    page.evaluate(
        f"[...document.querySelectorAll({json.dumps(within + ' button')})]"
        f".find(b => b.textContent === {json.dumps(text)}).click()"
    )


def seed(path, count=320):
    conn = connect(path)
    with transaction(conn):
        candidate = ensure_candidate(conn)
        claims = ClaimRepo(conn)
        for n in range(count):
            claims.add(
                candidate,
                VerifiedClaim(
                    claim_key=f"persona-{n}",
                    claim_type=ClaimType.EMPLOYMENT,
                    text=f"Organized handover {n} and trained colleagues.",
                    employer=f"Company {n % 5}",
                    period_start="2019-01",
                    period_end="2023-12",
                    source=ClaimSource.RESUME,
                    verified=False,
                    evidence_ref=f"CV source {n}",
                ),
            )
        repo = CareerRepo(conn, candidate)
        for n in range(10):
            command = {
                "action": "create",
                "keys": [f"persona-{i}" for i in range(n, count, 10)],
                "metadata": {
                    "company": f"Company {n % 5}",
                    "title": f"Role {n}",
                    "period_start": "2019-01",
                    "period_end": "2023-12",
                },
            }
            repo.apply(command, repo.preview(command)["preview_hash"])
    conn.close()


def test_320_evidence_bulk_work_and_lazy_render(page, pristine_server, tmp_path):
    seed(tmp_path / "pristine.db")
    start = time.perf_counter()
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelectorAll('.career__cards article').length === 10")
    initial_ms = round((time.perf_counter() - start) * 1000)
    from pathlib import Path

    page.screenshot(Path("out/career-v2-experiences-overview.png"))
    assert page.evaluate("document.querySelectorAll('.career__evidence').length") == 0
    start = time.perf_counter()
    click(page, "Explore evidence")
    page.wait_for("document.querySelectorAll('.career__evidence').length === 32")
    expansion_ms = round((time.perf_counter() - start) * 1000)
    # CAREER EVIDENCE V2: there is no bulk confirmation. Selecting a whole
    # experience offers organising and retiring; confirming is one statement,
    # from its own row, after reading it.
    click(page, "Select all in this experience")
    assert not page.evaluate(
        "[...document.querySelectorAll('.career button')]"
        ".some(b => /confirm/i.test(b.textContent) && /select/i.test(b.textContent))"
    )
    page.evaluate(
        "document.querySelector('.career__evidence button[aria-label^=\"Confirm\"]').click()"
    )
    page.wait_for("document.querySelector('.career__review input[type=checkbox]') !== null")
    assert page.evaluate(
        "[...document.querySelectorAll('.career__review button')]"
        ".find(b => b.textContent === 'Apply reviewed changes').disabled"
    )
    page.evaluate("document.querySelector('.career__review input[type=checkbox]').click()")
    click(page, "Apply reviewed changes", ".career__review")
    page.wait_for("document.querySelector('.career__cards').textContent.includes('1 confirmed')")
    conn = connect(tmp_path / "pristine.db")
    candidate = ensure_candidate(conn)
    assert sum(c.verified for c in ClaimRepo(conn).current(candidate)) == 1
    conn.close()
    (tmp_path / "performance.json").write_text(
        json.dumps(
            {
                "initial_ms": initial_ms,
                "expansion_ms": expansion_ms,
                "claims": 320,
                "experience_cards": 10,
                "initial_full_editors": 0,
                "expanded_rows": 32,
            }
        ),
        encoding="utf-8",
    )
    print(f"CAREER_PERFORMANCE initial={initial_ms}ms expansion={expansion_ms}ms claims=320")
    from pathlib import Path

    page.screenshot(Path("out/career-v2-experienced.png"))
    assert initial_ms < 15000 and expansion_ms < 5000


@pytest.mark.parametrize(
    ("title", "kind"),
    [
        ("Community mentoring", "VOLUNTEER"),
        ("Final-year research project", "ACADEMIC"),
        ("Independent client work", "FREELANCE"),
    ],
)
def test_nonemployment_experience_can_be_created_without_invented_company(
    page,
    pristine_server,
    title,
    kind,
):
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelector('.career__actions button') !== null")
    click(page, "Add experience")
    page.evaluate(
        "(() => { const inputs = document.querySelectorAll('.career__metadata input');"
        f"inputs[1].value = {json.dumps(title)};"
        f"document.querySelector('.career__metadata select').value = {json.dumps(kind)}; }})()"
    )
    click(page, "Review changes", ".career__metadata")
    page.wait_for("document.querySelector('.career__review dl') !== null")
    click(page, "Apply reviewed changes", ".career__review")
    page.wait_for(
        f"document.querySelector('.career__cards').textContent.includes({json.dumps(title)})"
    )
    assert page.evaluate(
        "document.querySelector('.career__cards').textContent.includes('Independent experience')"
    )


def test_settings_phrase_inputs_persist_and_model_is_separate(page, pristine_server):
    page.navigate(pristine_server + "/#settings")
    page.wait_for("document.querySelector('#settings-prefer_keyword') !== null")
    assert not page.evaluate("document.querySelector('#settings-model').open")
    for key, phrase in [
        ("prefer_keyword", "Mentoring"),
        ("avoid_keyword", "Night shift"),
        ("exclude_keyword", "Commission only"),
    ]:
        page.evaluate(
            f"(() => {{ const input = document.getElementById('settings-{key}');"
            f"input.value = {json.dumps(phrase)};"
            "input.dispatchEvent(new KeyboardEvent('keydown', {key:'Enter', bubbles:true})); })()"
        )
    page.navigate(pristine_server + "/#settings")
    page.wait_for("document.querySelector('#settings-prefer_keyword') !== null")
    for key, phrase in [
        ("prefer_keyword", "mentoring"),
        ("avoid_keyword", "night shift"),
        ("exclude_keyword", "commission only"),
    ]:
        assert page.evaluate(
            f"document.querySelector('[data-preference={key}]').textContent.includes({json.dumps(phrase)})"
        )


@pytest.fixture
def neutral_server(tmp_path, committed_config, pristine_server):
    config = tmp_path / "neutral-config"
    shutil.copytree(committed_config, config)
    (config / "search.local.yaml").unlink()
    port = _free_port()
    app = JobsApi(
        ServerConfig(db_path=tmp_path / "pristine.db", config_dir=config, port=port), quiet=True
    )
    server = build_server(app)
    server.handle_error = lambda request, client_address: None
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def test_new_candidate_has_no_occupational_defaults_and_market_pause(page, neutral_server):
    page.navigate(neutral_server + "/#settings")
    page.wait_for("document.querySelector('[data-source=gupy]') !== null")
    assert page.evaluate(
        "document.querySelector('[data-source=gupy]').textContent.includes('Paused')"
    )
    text = page.evaluate("document.getElementById('search-settings-host').textContent").lower()
    assert not any(
        word in text for word in ("hubspot", "crm architecture", "webhooks", "lead routing")
    )
    page.evaluate("document.getElementById('settings-model').open = true")
    page.wait_for("document.querySelector('.search-review') !== null")
    assert page.evaluate("document.querySelectorAll('.prefs__signal').length") == 0
    report = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    assert not any(r["path"].startswith("lexicon.") for r in report["rows"])
    from pathlib import Path

    page.screenshot(Path("out/career-v2-neutral-settings.png"))


def test_duplicate_import_is_a_question_and_can_stay_separate(page, pristine_server, tmp_path):
    from tests.integration.test_career_import_review import package

    from career_agent.intake.store import import_package

    conn = connect(tmp_path / "pristine.db")
    import_package(conn, package())
    conn.close()
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelector('.career__proposals') !== null")
    page.evaluate("document.querySelector('.career__proposals').open = true")
    assert page.evaluate(
        "document.querySelector('.career__proposals').textContent.includes('Teem / Teem LLC')"
    )
    assert page.evaluate("document.querySelectorAll('.career__cards article').length") == 0
    click(page, "Keep separate")
    page.wait_for("document.querySelector('.career__review h3') !== null")
    click(page, "Apply reviewed changes", ".career__review")
    page.wait_for(
        "document.querySelector('.career__proposals').textContent"
        ".includes('keep these companies separate')"
    )
    conn = connect(tmp_path / "pristine.db")
    assert conn.execute("SELECT count(*) FROM verified_claim").fetchone()[0] == 0
    conn.close()


def test_unknown_company_and_dates_remain_in_inbox(page, pristine_server, tmp_path):
    conn = connect(tmp_path / "pristine.db")
    with transaction(conn):
        ClaimRepo(conn).add(
            ensure_candidate(conn),
            VerifiedClaim(
                claim_key="unknown-role",
                text="Helped colleagues document the handover process.",
                claim_type=ClaimType.PROJECT,
                source=ClaimSource.RESUME,
                verified=False,
            ),
        )
    conn.close()
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelector('.career__actions') !== null")
    click(page, "Needs organizing (1)")
    page.wait_for("document.querySelector('.career__evidence') !== null")
    assert page.evaluate("document.querySelectorAll('.career__cards article').length") == 0
    assert page.evaluate(
        "document.querySelector('.career__work').textContent.includes('Missing companies')"
    )


def test_graduate_organizes_education_projects_and_certification_together(
    page, pristine_server, tmp_path
):
    conn = connect(tmp_path / "pristine.db")
    with transaction(conn):
        candidate = ensure_candidate(conn)
        for category, text in [
            (ClaimType.EDUCATION, "Completed the environmental studies program."),
            (ClaimType.PROJECT, "Built a shared fieldwork checklist for the class."),
            (ClaimType.CERTIFICATION, "Completed a first aid certification."),
        ]:
            ClaimRepo(conn).add(
                candidate,
                VerifiedClaim(
                    claim_key=category.value.lower(),
                    text=text,
                    claim_type=category,
                    source=ClaimSource.RESUME,
                    verified=False,
                ),
            )
    conn.close()
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelector('.career__actions') !== null")
    click(page, "Needs organizing (3)")
    page.wait_for("document.querySelectorAll('.career__evidence').length === 3")
    click(page, "Select all 3 filtered items")
    click(page, "Add experience")
    page.evaluate(
        "document.querySelectorAll('.career__metadata input')[1].value = 'Studies and fieldwork'"
    )
    page.evaluate("document.querySelector('.career__metadata select').value = 'ACADEMIC'")
    click(page, "Review changes", ".career__metadata")
    page.wait_for("document.querySelector('.career__review dl') !== null")
    click(page, "Apply reviewed changes", ".career__review")
    page.wait_for(
        "document.querySelector('.career__cards').textContent.includes('3 evidence items')"
    )
    click(page, "Explore evidence")
    page.wait_for("document.querySelectorAll('.career__category').length === 3")
    conn = connect(tmp_path / "pristine.db")
    assert (
        conn.execute(
            "SELECT count(*) FROM verified_claim WHERE claim_type = 'EMPLOYMENT'"
        ).fetchone()[0]
        == 0
    )
    conn.close()


def test_career_changer_keeps_old_work_and_transferable_project_distinct(
    page, pristine_server, tmp_path
):
    conn = connect(tmp_path / "pristine.db")
    with transaction(conn):
        candidate = ensure_candidate(conn)
        repo = CareerRepo(conn, candidate)
        for key, company, title, text in [
            (
                "care",
                "Community clinic",
                "Care coordinator",
                "Organized patient handovers between shifts.",
            ),
            (
                "project",
                None,
                "Service request project",
                "Built an internal app to organize service requests.",
            ),
        ]:
            ClaimRepo(conn).add(
                candidate,
                VerifiedClaim(
                    claim_key=key,
                    text=text,
                    claim_type=ClaimType.PROJECT if key == "project" else ClaimType.EMPLOYMENT,
                    source=ClaimSource.SELF_ATTESTED,
                    verified=True,
                ),
            )
            command = {
                "action": "create",
                "keys": [key],
                "metadata": {
                    "company": company,
                    "title": title,
                    "kind": "PERSONAL" if key == "project" else "EMPLOYMENT",
                },
            }
            repo.apply(command, repo.preview(command)["preview_hash"])
    conn.close()
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelectorAll('.career__cards article').length === 2")
    text = page.evaluate("document.querySelector('.career__cards').textContent")
    assert "Care coordinator" in text and "Service request project" in text
    click(page, "Find evidence across experiences")
    page.wait_for("document.querySelectorAll('.career__evidence').length === 2")


@pytest.mark.parametrize("failed", [False, True])
def test_source_refresh_state_does_not_prevent_discover(page, pristine_server, tmp_path, failed):
    from pathlib import Path

    from career_agent.domain.enums import PipelineRunStatus
    from career_agent.storage.repositories import PipelineRunRepo

    conn = connect(tmp_path / "pristine.db")
    with transaction(conn):
        runs = PipelineRunRepo(conn)
        run_id = runs.start("collect-gupy")
        if failed:
            runs.finish(run_id, PipelineRunStatus.FAILED, error="Synthetic unavailable source")
        else:
            runs.progress(run_id, {"postings_seen": 200, "claimed_total": 500})
    conn.close()
    page.navigate(pristine_server + "/#settings")
    page.wait_for("document.querySelector('[data-source=gupy]') !== null")
    text = page.evaluate("document.querySelector('[data-source=gupy]').textContent")
    assert ("Last refresh failed" if failed else "Updating") in text
    page.evaluate("document.querySelector('[data-source=gupy]').scrollIntoView({block:'center'})")
    page.screenshot(Path(f"out/career-v2-source-{'failed' if failed else 'updating'}.png"))
    page.evaluate("document.querySelector('.topnav__link[data-page=jobs]').click()")
    page.wait_for("document.querySelectorAll('.card').length > 0")


def test_experience_workspace_fits_mobile_and_keeps_editors_lazy(page, pristine_server, tmp_path):
    from pathlib import Path

    seed(tmp_path / "pristine.db")
    page.set_viewport(390, 844, mobile=True)
    open_evidence(page, pristine_server)
    page.wait_for("document.querySelectorAll('.career__cards article').length === 10")
    assert page.evaluate("document.querySelectorAll('.career__evidence').length") == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    click(page, "Explore evidence")
    page.wait_for("document.querySelectorAll('.career__evidence').length === 32")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.wait_for(
        "document.querySelector('.career__work').getBoundingClientRect().top < 150",
        message="the selected experience to scroll into view",
    )
    page.screenshot(Path("out/career-v2-mobile.png"))

"""The eligibility form in a browser, on invented profiles (2026-10-09, ADR-0034).

Four short questions in front; what only some jobs ask is folded; the retired
question is shown back only when it was answered, and only she removes it;
each country list carries its question once; and a job that needs an answer
opens the profile at that question.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_profile_editing import open_profile
from tests.browser.test_release_personas import Workspace

from career_agent.clock import now_utc
from career_agent.config.candidate_writer import current_candidate_fields, set_candidate_fields
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.manual_import import import_posting
from career_agent.storage.db import connect


@pytest.fixture
def workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    try:
        yield ws
    finally:
        ws.stop()


def _restart_with(ws: Workspace, changes: dict) -> None:
    """Answers written as an earlier version would have left them."""
    ws.stop()
    set_candidate_fields(ws.config, changes)
    ws.start()


def _questions(page: Chrome) -> list[str]:
    return list(
        page.evaluate(
            "[...document.querySelectorAll('.choicegroup .choice__q')].map((q) => q.textContent)"
        )
    )


def test_four_questions_in_front_and_the_rest_folded(page: Chrome, workspace: Workspace) -> None:
    open_home_past_setup(page, workspace.base)
    open_profile(page, workspace.base)
    where = page.evaluate(
        "[...document.querySelectorAll('.choicegroup')][0].querySelectorAll('.choice__q').length"
    )
    assert where == 4
    assert _questions(page)[:4] == [
        "Where do you live?",
        "What kind of work are you looking for?",
        "Which markets do you want to prioritize?",
        "In which countries do you know you can be employed?",
    ]
    more = "document.querySelector('.choicegroup--more')"
    assert page.evaluate(f"{more}.tagName") == "DETAILS"
    assert not page.evaluate(f"{more}.open"), "the optional section must start folded"
    assert "Additional information for some jobs" in page.evaluate(f"{more}.innerText")
    # Never answered: the retired question is not drawn at all.
    assert not page.evaluate("Boolean(document.querySelector('.choice--legacy'))")
    body = str(page.evaluate("document.querySelector('#page-profile').innerText"))
    assert "These are stored as they are" not in body
    assert "Which countries can employ you directly" not in body


def test_each_country_list_says_its_question_once(page: Chrome, workspace: Workspace) -> None:
    open_home_past_setup(page, workspace.base)
    open_profile(page, workspace.base)
    page.evaluate("document.querySelector('.choicegroup--more').open = true")
    visible = page.evaluate(
        "[...document.querySelectorAll('.choicegroup .tags label.field__label')]"
        ".filter((l) => getComputedStyle(l).position !== 'absolute').length"
    )
    assert visible == 0, "a country list drew its question twice"
    # Still named for a screen reader, by the label it carries.
    unnamed = page.evaluate(
        "[...document.querySelectorAll('.choicegroup .tags input')]"
        ".filter((n) => !(n.labels && n.labels.length)).map((n) => n.id)"
    )
    assert unnamed == []


def test_the_retired_answer_is_shown_back_and_only_she_removes_it(
    page: Chrome, workspace: Workspace
) -> None:
    _restart_with(workspace, {"candidate_country": "BR", "eligible_countries": ["BR", "PT"]})
    open_home_past_setup(page, workspace.base)
    open_profile(page, workspace.base)
    page.evaluate("document.querySelector('.choicegroup--more').open = true")
    legacy = str(page.evaluate("document.querySelector('.choice--legacy').innerText"))
    assert "Which countries can employ you directly" in legacy
    assert "Brazil" in legacy and "Portugal" in legacy
    # Opening the page wrote nothing.
    assert current_candidate_fields(workspace.config)["eligible_countries"] == ["BR", "PT"]
    page.evaluate("document.querySelector('#profile-legacy-eligible_countries-remove').click()")
    page.evaluate("document.querySelector('.profile__actions .btn--primary').click()")
    page.wait_for("document.querySelector('.profile__save-status').classList.contains('is-ok')")
    fields = current_candidate_fields(workspace.config)
    assert fields["eligible_countries"] == []
    assert not fields["authorized_countries"], "the old answer was copied into the new question"


def test_portuguese_names_markets_and_countries_in_portuguese(
    page: Chrome, workspace: Workspace
) -> None:
    _restart_with(workspace, {"candidate_country": "BR", "authorized_countries": ["BR"]})
    open_home_past_setup(page, workspace.base)
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.navigate(workspace.base)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"profile\"]').click()")
    page.wait_for("document.querySelectorAll('.profiletab').length > 0")
    page.evaluate("[...document.querySelectorAll('.profiletab')].at(-1).click()")
    page.wait_for("document.querySelectorAll('.choicegroup').length > 0")
    text = str(page.evaluate("[...document.querySelectorAll('.choicegroup')][0].innerText"))
    assert "Onde você mora?" in text
    assert "América do Norte (Estados Unidos e Canadá)" in text
    assert "North America" not in text
    assert "Brasil" in text
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'en')")


def test_a_job_that_needs_an_answer_opens_the_profile_at_that_question(
    page: Chrome, workspace: Workspace
) -> None:
    _restart_with(workspace, {"candidate_country": "BR"})
    config, _ = load_search_config(workspace.config)
    with connect(workspace.db) as conn:
        job = import_posting(
            conn,
            config,
            title="Invented Analyst",
            company="Invented Company Z",
            description=(
                "You will reconcile invented records for an invented team. "
                "You must be authorized to work in the United States."
            ),
            url="https://example.invalid/z",
            location="Remote",
            config_id=config.config_id,
            config_version=config.config_version,
            now=now_utc(),
        )
    open_home_past_setup(page, workspace.base)
    page.navigate(f"{workspace.base}/?job={job}")
    page.wait_for(
        "document.querySelector('[data-answer-field]') !== null", message="the answer link"
    )
    assert page.evaluate("document.querySelector('[data-answer-field]').dataset.answerField") == (
        "authorized_countries"
    )
    page.evaluate("document.querySelector('[data-answer-field]').click()")
    page.wait_for(
        "document.activeElement && document.activeElement.closest('.choice')"
        " && document.activeElement.closest('.choice').innerText"
        ".includes('In which countries do you know you can be employed?')",
        message="the profile, at the question",
    )

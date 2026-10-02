"""Search Fit feedback in the drawer: recorded, kept across a reload, and inert.

`pristine_server` because answering writes a row.
"""

from __future__ import annotations

import json

from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import A_REAL_CARD, LOCALE_KEY, click, open_list

FEEDBACK = "document.querySelector('#drawer-panel-why .d-sec--fit-feedback')"
BUTTONS = f"{FEEDBACK}.querySelectorAll('.segmented__btn')"
PRESSED = f"Array.from({BUTTONS}).findIndex((b) => b.getAttribute('aria-pressed') === 'true')"


def open_first_why(page: Chrome, server: str) -> tuple[str, str]:
    """The first card's drawer, on its reasoning tab. Returns (job id, score text)."""
    open_list(page, server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    job = str(page.evaluate(f"{A_REAL_CARD}.closest('[data-job-id]').dataset.jobId"))
    click(page, f"document.querySelector('[data-job-id={json.dumps(job)}]')")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.drawer__tab').length === 4",
        message="the drawer and its tabs",
    )
    click(page, "document.getElementById('drawer-tab-why')")
    page.wait_for(f"Boolean({FEEDBACK})", message="the Search Fit question")
    return job, str(page.evaluate("document.querySelector('.drawer .why__head').textContent"))


def test_an_answer_is_saved_survives_a_reload_and_moves_no_number(
    page: Chrome, pristine_server: str
) -> None:
    job, score = open_first_why(page, pristine_server)
    heading = page.evaluate(f"{FEEDBACK}.querySelector('.d-sec__head').textContent")
    assert heading == "Does this % look right?"
    assert page.evaluate(PRESSED) == -1, "nothing is answered until the person answers"

    click(page, f"{BUTTONS}[1]")  # Too high
    page.wait_for(
        f"{PRESSED} === 1 && Boolean({FEEDBACK}.querySelector('select'))",
        message="the answer and its optional reason",
    )
    page.evaluate(
        f"(() => {{ const s = {FEEDBACK}.querySelector('select'); s.value = 'TOOLS_NOT_WORK';"
        " s.dispatchEvent(new Event('change', { bubbles: true })); return true; })()"
    )
    page.wait_for(
        f"{FEEDBACK}.querySelector('select').value === 'TOOLS_NOT_WORK'"
        f" && {FEEDBACK}.querySelector('[role=status]').textContent.includes('Saved')",
        message="the reason to be saved",
    )

    again, score_after = open_first_why(page, pristine_server)
    assert again == job
    assert page.evaluate(PRESSED) == 1, "the answer did not survive a reload"
    assert page.evaluate(f"{FEEDBACK}.querySelector('select').value") == "TOOLS_NOT_WORK"
    assert score_after == score, "answering moved a number on the card"


def test_the_question_is_asked_in_portuguese(page: Chrome, pristine_server: str) -> None:
    page.navigate(pristine_server)
    page.evaluate(f"localStorage.setItem({json.dumps(LOCALE_KEY)}, 'pt-BR')")
    try:
        open_first_why(page, pristine_server)
        labels = page.evaluate(f"Array.from({BUTTONS}).map((b) => b.textContent)")
        heading = page.evaluate(f"{FEEDBACK}.querySelector('.d-sec__head').textContent")
    finally:
        page.evaluate(f"localStorage.removeItem({json.dumps(LOCALE_KEY)})")
    assert heading == "Esta aderência parece certa?"
    assert labels == ["Sim", "Alta demais", "Baixa demais", "A vaga não dá informação suficiente"]

"""Career Evidence V2 in the browser: a CV reviewed as a career, not a wall.

Driven through the real page and the real file input, over a fresh database
per test (`pristine_server`). Every CV is invented (`tests/fixtures/cv`).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_evidence_workspace import open_evidence
from tests.support_cv import load_cv, long_cv

LOCALE_KEY = "careerAgent.locale.v1"
OUT = Path("out")


def choose_file(page: Chrome, text: str, name: str) -> None:
    page.evaluate(
        "(() => { const box = new DataTransfer();"
        f" box.items.add(new File([{json.dumps(text)}], {json.dumps(name)},"
        " { type: 'text/plain' }));"
        " const input = document.getElementById('ev-file'); input.files = box.files;"
        " input.dispatchEvent(new Event('change', { bubbles: true })); })()"
    )
    page.wait_for(
        "document.querySelector('#page-evidence .cvr__summary [data-summary]') !== null",
        message="the review summary",
    )


def summary(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('#page-evidence [data-summary]').textContent"))


def rows(page: Chrome) -> list[str]:
    return list(
        page.evaluate(
            "[...document.querySelectorAll('#page-evidence .cvr__rows[aria-label] .cvr__row')]"
            ".map((row) => row.textContent)"
        )
    )


def press_button(page: Chrome, text: str) -> None:
    page.evaluate(
        "[...document.querySelectorAll('#page-evidence button')]"
        f".find((b) => b.textContent.trim() === {json.dumps(text)}).click()"
    )


def api(page: Chrome, path: str) -> dict:
    return dict(page.evaluate(f"fetch({json.dumps(path)}).then((r) => r.json())"))


def waiting(page: Chrome) -> int:
    steps = api(page, "/api/firstrun")["steps"]
    return int(next(step for step in steps if step["key"] == "evidence")["waiting"])


# =========================================================================


def test_a_markdown_cv_opens_on_its_experiences(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("markdown_complex.md"), "riley.md")
    assert summary(page).startswith("5 experiences found / 30 suggestions /")
    assert page.evaluate("document.querySelectorAll('#page-evidence .ev__card').length") == 0
    found = rows(page)
    # Newest first: the role still held, then Teem, which the CV lists last.
    assert found[0].startswith("Fabrikam CloudSenior Solutions Consultant")
    assert "Teem" in found[1] and "Business Operations / RevOps" in found[1]
    text = str(page.evaluate("document.querySelector('#page-evidence .cvr').textContent"))
    for token in ("**", "##", "](", "`"):
        assert token not in text, token
    OUT.mkdir(exist_ok=True)
    page.screenshot(OUT / "career-evidence-v2-summary.png")
    assert page.console_errors() == []


def test_a_long_cv_is_fast_and_grouped(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    started = time.perf_counter()
    choose_file(page, long_cv(20, 14, markdown=True), "long.md")
    to_summary_ms = round((time.perf_counter() - started) * 1000)
    assert summary(page).startswith("20 experiences found / 285 suggestions /")
    assert len(rows(page)) == 20
    assert page.evaluate("document.querySelectorAll('#page-evidence .ev__card').length") == 0
    started = time.perf_counter()
    page.evaluate("document.querySelector('#page-evidence .cvr__row button').click()")
    page.wait_for("document.querySelectorAll('#page-evidence .ev__card').length === 14")
    open_ms = round((time.perf_counter() - started) * 1000)
    print(f"CV_REVIEW_PERFORMANCE summary={to_summary_ms}ms open={open_ms}ms suggestions=285")
    OUT.mkdir(exist_ok=True)
    page.screenshot(OUT / "career-evidence-v2-experience.png")
    assert to_summary_ms < 8000 and open_ms < 3000


def test_next_item_walks_the_read_and_nothing_is_confirmed_in_bulk(
    page: Chrome, pristine_server: str
) -> None:
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("plain_promotions.txt"), "jordan.txt")
    page.evaluate("document.querySelector('#page-evidence [data-action=\"next\"]').click()")
    page.wait_for("document.querySelectorAll('#page-evidence .ev__card').length > 0")
    heading = str(
        page.evaluate("document.querySelector('#page-evidence .cvr__group h3').textContent")
    )
    assert heading == "Globex Logistics / Operations Director"
    # Focus is on the first card waiting, not somewhere at the top of a list.
    assert page.evaluate("document.activeElement.dataset.decision") == "PENDING"
    buttons = page.evaluate(
        "[...document.querySelectorAll('#page-evidence button')].map(b => b.textContent)"
    )
    assert not any("all" in str(b).lower() and "confirm" in str(b).lower() for b in buttons)
    page.evaluate("document.activeElement.querySelector('[data-answer=\"ACCEPTED\"]').click()")
    page.wait_for("document.querySelectorAll('#page-evidence .ev__decision').length === 1")
    page.wait_for("document.activeElement.dataset.decision === 'PENDING'")
    page.evaluate("document.activeElement.querySelector('[data-answer=\"ACCEPTED\"]').click()")
    # The second answer finished this experience; Next took her to the one after.
    page.wait_for(
        "document.querySelector('#page-evidence .cvr__group h3').textContent"
        " === 'Globex Logistics / Operations Manager'"
    )
    assert api(page, "/api/evidence")["confirmed"] == 2


def test_archive_and_restore_empty_and_refill_every_counter(
    page: Chrome, pristine_server: str
) -> None:
    open_evidence(page, pristine_server)
    choose_file(page, long_cv(11, 13), "wrong.txt")
    assert waiting(page) == 148
    page.evaluate("document.querySelector('#page-evidence [data-action=\"archive\"]').click()")
    page.wait_for("document.querySelector('#page-evidence .cvr__archived') !== null")
    assert waiting(page) == 0
    assert api(page, "/api/career")["unassigned"] == 0
    # HOME AGREES. The reproduced bug was here: archived, and still
    # "148 statements waiting" in the setup list on Home.
    open_home_past_setup(page, pristine_server)
    home = str(page.evaluate("document.querySelector('#page-home, main').textContent"))
    assert "148" not in home and "statements waiting" not in home
    open_evidence(page, pristine_server)
    page.evaluate(
        "[...document.querySelectorAll('#page-evidence button')]"
        ".find((b) => b.textContent.trim() === 'Look inside').click()"
    )
    page.wait_for("document.querySelector('#page-evidence .cvr__archived') !== null")
    assert not page.evaluate(
        "Boolean(document.querySelector('#page-evidence [data-action=\"next\"]'))"
    )
    press_button(page, "Restore")
    page.wait_for("document.querySelector('#page-evidence .cvr__archived') === null")
    assert waiting(page) == 148


def test_the_wrong_cv_is_deleted_after_saying_exactly_what_goes(
    page: Chrome, pristine_server: str
) -> None:
    """ "I uploaded the wrong CV. Remove this import and all 142 unconfirmed
    suggestions." """
    open_evidence(page, pristine_server)
    choose_file(page, long_cv(11, 13), "wrong.txt")
    page.evaluate("document.querySelector('#page-evidence .cvr [data-action=\"delete\"]').click()")
    page.wait_for("document.querySelector('#page-evidence .cvr__confirm') !== null")
    said = str(page.evaluate("document.querySelector('#page-evidence .cvr__confirm').textContent"))
    assert "Delete wrong.txt permanently?" in said
    assert "all 148 suggestions in it (148 unanswered, 0 rejected)" in said
    assert "Nothing you confirmed came from it." in said
    assert "cannot be undone" in said and "archive it instead" in said
    assert api(page, "/api/cv/imports")["imports"], "showing the plan deleted something"
    page.evaluate(
        "document.querySelector('#page-evidence [data-action=\"delete-confirm\"]').click()"
    )
    page.wait_for("document.querySelector('#page-evidence .ev__privacy') !== null")
    assert api(page, "/api/cv/imports")["imports"] == []
    assert waiting(page) == 0


def test_deleting_after_confirming_says_what_is_kept(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("plain_promotions.txt"), "jordan.txt")
    page.evaluate("document.querySelector('#page-evidence [data-action=\"next\"]').click()")
    page.wait_for("document.activeElement.dataset.decision === 'PENDING'")
    page.evaluate("document.activeElement.querySelector('[data-answer=\"ACCEPTED\"]').click()")
    page.wait_for("document.querySelectorAll('#page-evidence .ev__decision').length === 1")
    press_button(page, "Back to all experiences")
    page.evaluate("document.querySelector('#page-evidence .cvr [data-action=\"delete\"]').click()")
    page.wait_for("document.querySelector('#page-evidence .cvr__confirm') !== null")
    said = str(page.evaluate("document.querySelector('#page-evidence .cvr__confirm').textContent"))
    assert "9 unconfirmed suggestions are removed" in said
    assert "The 1 you confirmed stay in your evidence" in said
    page.evaluate(
        "document.querySelector('#page-evidence [data-action=\"delete-confirm\"]').click()"
    )
    page.wait_for("document.querySelector('#page-evidence .ev__privacy') !== null")
    assert api(page, "/api/evidence")["confirmed"] == 1


def test_an_unresolved_experience_is_corrected_not_guessed(
    page: Chrome, pristine_server: str
) -> None:
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("overlap_missing.md"), "casey.md")
    umbrella = [r for r in rows(page) if "Umbrella Corp" in r][0]
    assert "Role not stated" in umbrella and "Dates not stated" in umbrella
    page.evaluate(
        "[...document.querySelectorAll('#page-evidence .cvr__row')]"
        ".find((r) => r.textContent.includes('Umbrella')).querySelector('button').click()"
    )
    page.wait_for("document.querySelector('#page-evidence .cvr__group .cvr__attention') !== null")
    press_button(page, "Correct company, role or dates")
    page.evaluate(
        "(() => { const inputs = document.querySelectorAll('#page-evidence .cvr__form input');"
        " inputs[1].value = 'Organiser'; inputs[2].value = '2018-01'; inputs[3].value = '2018-12';"
        " })()"
    )
    press_button(page, "Save details")
    page.wait_for(
        "document.querySelector('#page-evidence .cvr__group h3').textContent"
        " === 'Umbrella Corp / Organiser'"
    )
    assert not page.evaluate(
        "[...document.querySelectorAll('#page-evidence .cvr__group > .cvr__attention')].length"
    )
    source = str(page.evaluate("document.querySelector('#page-evidence .cvr__source').textContent"))
    assert "### Umbrella Corp" in source, "the document's own line was rewritten"


def test_the_review_reads_in_portuguese(page: Chrome, pristine_server: str) -> None:
    page.navigate(pristine_server)
    page.evaluate(f"window.localStorage.setItem('{LOCALE_KEY}', 'pt-BR')")
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("portuguese.txt"), "maria.txt")
    assert summary(page).startswith("3 experiências encontradas / 9 sugestões /")
    assert "Empresa Fictícia S.A." in rows(page)[0]
    text = str(page.evaluate("document.querySelector('#page-evidence .cvr').textContent"))
    for english in ("experiences found", "Review", "Next item", "Archive"):
        assert english not in text, english
    page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")


def test_the_review_fits_a_phone_and_every_control_is_named(
    page: Chrome, pristine_server: str
) -> None:
    page.set_viewport(390, 844, mobile=True)
    open_evidence(page, pristine_server)
    choose_file(page, load_cv("markdown_complex.md"), "riley.md")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    page.evaluate("document.querySelector('#page-evidence [data-action=\"next\"]').click()")
    page.wait_for("document.querySelectorAll('#page-evidence .ev__card').length > 0")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    unnamed = page.evaluate(
        "[...document.querySelectorAll('#page-evidence .cvr button, #page-evidence .cvr select,"
        " #page-evidence .cvr input')].filter((n) => !(n.getAttribute('aria-label')"
        ' || n.textContent.trim() || (n.id && document.querySelector(`label[for="${n.id}"]`))'
        " || n.closest('label'))).length"
    )
    assert unnamed == 0
    OUT.mkdir(exist_ok=True)
    page.screenshot(OUT / "career-evidence-v2-mobile.png")
    assert page.console_errors() == []

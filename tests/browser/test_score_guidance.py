"""What the % measures, on the list and in the drawer (2026-10-10).

Synthetic postings, a fake provider that is never asked anything. The list
names its evaluation mode quietly and explains it on request; the drawer says
what limits THIS score and whose gap each limit is; no screen calls the % a
chance of being hired.
"""

from __future__ import annotations

import os
from pathlib import Path

from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_deep_reading import deep_server  # noqa: F401 - fixture

SHOTS = os.environ.get("DEEP_SHOTS")


def _jobs(page: Chrome, url: str) -> None:
    open_home_past_setup(page, url)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.querySelector('[data-job-id]')", message="the list")


def test_the_list_names_its_mode_and_explains_it_on_request(
    page: Chrome,
    deep_server: dict,  # noqa: F811
) -> None:
    _jobs(page, deep_server["url"])
    summary = str(page.evaluate("document.getElementById('eval-mode-summary').textContent"))
    assert summary == "How this score works"
    assert not page.evaluate("document.getElementById('eval-mode').open"), "closed by default"
    page.evaluate("document.getElementById('eval-mode-summary').click()")
    body = str(page.evaluate("document.getElementById('eval-mode-body').innerText"))
    assert "not a chance of being hired" in body
    assert "does not change this score" in body
    assert "completes that part" in body and "adds nothing" in body
    # The first choice in a part brings a new question into the % (owner, 2026-10-13).
    assert "Your first choice in a part:" in body and "never lowers a score" in body
    # Three different answers, kept apart; and when a score is out of date.
    assert "never inside it" in body and "marked as earlier" in body
    # It promises no improvement it cannot deliver.
    assert "raise" not in body and "improve" not in body.lower()
    # The panel takes the list's width (2026-10-13): at a fixed 640px every
    # explanation broke early beside empty space on a wide window. One
    # paragraph per explanation, its label in bold on the same line.
    width = page.evaluate("document.getElementById('eval-mode-body').getBoundingClientRect().width")
    column = page.evaluate("document.getElementById('eval-mode').getBoundingClientRect().width")
    assert width >= column - 1, (width, column)
    assert page.evaluate(
        "[...document.querySelectorAll('#eval-mode-body .evalmode__row')].every((row) =>"
        " row.tagName === 'P'"
        " && getComputedStyle(row.querySelector('strong')).display === 'inline')"
    )
    if SHOTS:
        page.screenshot(Path(SHOTS) / "guidance-list.png")
    assert deep_server["fake"].messages == [], "the guidance sent something"


def test_the_drawer_says_what_limits_this_score_and_whose_gap_it_is(
    page: Chrome,
    deep_server: dict,  # noqa: F811
) -> None:
    _jobs(page, deep_server["url"])
    # A posting where none of the work listed reads as a duty of the role:
    # the one the limits section is about. With one, it rightly says nothing.
    focused = page.evaluate(
        "(async () => { for (const n of document.querySelectorAll('[data-job-id]')) {"
        " const j = await (await fetch('/api/jobs/' + n.dataset.jobId)).json();"
        " const w = (j.components || []).find((c) => c.component_id === 'responsibilities');"
        " const central = w ? w.contributions"
        ".filter((c) => c.prominence === 'PRIMARY' && c.points > 0).length : 1;"
        " if (central === 0) return n.dataset.jobId; } return null; })()"
    )
    assert focused, "the demo list holds a posting with no central kind of work"
    page.evaluate(f"document.querySelector('[data-job-id=\"{focused}\"]').click()")
    page.wait_for(
        "Array.from(document.querySelectorAll('.d-sec__head'))"
        ".some((n) => n.textContent === 'What limits this score')",
        message="the limits section",
    )
    section = (
        "Array.from(document.querySelectorAll('.d-sec__head'))"
        ".find((n) => n.textContent === 'What limits this score').closest('.d-sec')"
    )
    kinds = page.evaluate(
        f"Array.from({section}.querySelectorAll('[data-limit]')).map((n) => n.dataset.limit)"
    )
    assert "reader" in kinds, kinds
    text = str(page.evaluate(f"{section}.innerText"))
    assert "reads as a duty of this role" in text and "not of your profile" in text
    assert "Your history and evidence are not part of this %" in text
    if SHOTS:
        page.screenshot(Path(SHOTS) / "guidance-drawer.png")
    page.evaluate(
        f"Array.from({section}.querySelectorAll('button'))"
        ".find((b) => /in depth/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-panel .dr-card')", message="the deep reading panel opened"
    )
    assert deep_server["fake"].messages == [], "opening the panel sent something"


def test_the_guidance_reads_in_portuguese(
    page: Chrome,
    deep_server: dict,  # noqa: F811
) -> None:
    open_home_past_setup(page, deep_server["url"])
    page.evaluate("window.localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    _jobs(page, deep_server["url"])
    assert (
        str(page.evaluate("document.getElementById('eval-mode-summary').textContent"))
        == "Como esta nota funciona"
    )
    page.evaluate("document.getElementById('eval-mode-summary').click()")
    body = str(page.evaluate("document.getElementById('eval-mode-body').innerText"))
    assert "não é uma chance de ser contratada" in body and "evalMode." not in body

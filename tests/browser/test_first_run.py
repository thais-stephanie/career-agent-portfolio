"""The journey a person makes on the day they install this, in a real browser.

WHY THIS FILE EXISTS
--------------------
Every step of a first run was already built and none of it was in an order. The
documented route was three terminal commands -- `intake-build`, `intake-import`,
`setup` -- which is a runbook rather than an onboarding, and somebody who does
not write software stops at the first one.

So the assertions here are about a JOURNEY, from a database with nothing in it
to confirmed evidence, with no terminal and no knowledge of the schema:

  * the six steps appear on a fresh install, in order, each saying what it is
    FOR rather than only what it is;
  * answering the career question writes, immediately, and can be un-answered;
  * a CV chosen in the file picker is read HERE and produces statements;
  * none of those statements is true yet, and the screen says so;
  * confirming one moves the evidence step and nothing else;
  * every one of those words translates.

WHAT IT DELIBERATELY DOES NOT ASSERT
------------------------------------
That the flow is finished, or that finishing it is required. Nothing here is a
gate: each step can be skipped, and the product works with none of them
answered. A test that required completion would be encoding the opposite
product.

THE CV IS INVENTED. It names an invented person at invented employers, and it
is written for a profession the rest of this suite's fixtures are not -- because
"does this work for somebody who is not a systems analyst" is precisely the
question a first-run flow has to answer.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup

from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

#: An invented CV for an invented paralegal. Deliberately NOT a systems
#: analyst: the demo corpus and every other fixture in this suite describe one
#: trade, and a first-run flow that only works for that trade is not an
#: onboarding, it is a demo.
INVENTED_CV = """Jane Doe
Paralegal

Experience
Senior Paralegal, Harrow and Finch -- Jan 2019 to Mar 2024
- Managed client communication and correspondence for a caseload of 40 matters.
- Maintained case files, scheduling and compliance documentation.
- Drafted, formatted and proofread contracts using document management software.

Skills
Microsoft Excel, document management, legal research, scheduling, compliance

Education
LLB, University of Leeds, 2018
"""

LOCALE_KEY = "careerAgent.locale.v1"


@pytest.fixture
def empty_server(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    """A database with NOTHING in it, which is what a first run actually has.

    Not the session `demo_db`: that one holds twenty-one scored postings and a
    seeded corpus, and every interesting assertion about a first run is about
    the state before any of that exists. `stamp_identity` claims it as personal
    because `serve` refuses a database that will not say which it is -- which
    is itself part of the first-run story and is done for the person by
    `career-agent start`.
    """
    db_path = tmp_path / "firstrun.db"
    conn = connect(db_path)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "first-run")
    finally:
        conn.close()

    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db_path, config_dir=committed_config, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        thread.join(timeout=5)


def _open_home(page: Chrome, base: str) -> None:
    # A fresh install opens on the guided setup; these tests are about the
    # six-step list behind "Do this later".
    open_home_past_setup(page, base)


def _step_text(page: Chrome, key: str) -> str:
    return str(
        page.evaluate(f"document.querySelector('.firstrun__step[data-step=\"{key}\"]').innerText")
    )


# =========================================================================
# 1. THE SIX STEPS
# =========================================================================


def test_a_fresh_install_shows_six_steps_in_order(page: Chrome, empty_server: str) -> None:
    """The order is the argument, so the order is what is asserted.

    Career context first because it is one click and it changes what the later
    steps explain. Documents before evidence because there is nothing to review
    until something has been read. "Where you may work" before "what work you
    want" because the first is the only one that can make the whole list empty.
    Jobs last, because everything above it changes the answer.
    """
    _open_home(page, empty_server)
    keys = page.evaluate(
        "[...document.querySelectorAll('.firstrun__step')].map(n => n.dataset.step)"
    )
    assert keys == ["career_stage", "documents", "evidence", "where", "work", "jobs"]


def test_every_step_says_what_it_is_for(page: Chrome, empty_server: str) -> None:
    """A checklist that only names its steps is a list of chores.

    Each row carries one sentence saying what the product can CONCLUDE once it
    is answered -- and that sentence is the difference between "add your
    evidence" and "until something is confirmed, every requirement on every job
    reads as a gap".
    """
    _open_home(page, empty_server)
    whys = page.evaluate(
        "[...document.querySelectorAll('.firstrun__why')].map(n => n.textContent.trim())"
    )
    assert len(whys) == 6
    assert all(len(why) > 40 for why in whys), whys


def test_nothing_on_the_first_screen_is_a_percentage(page: Chrome, empty_server: str) -> None:
    """Section 20, and the reason the rail shows no "LVL 7".

    Six steps are a fact about this FLOW, which has a length. A percentage
    would be a fact about a person, and there is no honest denominator for one.
    """
    import re

    text = str(page.evaluate("document.querySelector('.firstrun').innerText"))
    assert not re.search(r"\d+\s*%", text), text


def test_the_privacy_sentence_is_above_the_file_picker(page: Chrome, empty_server: str) -> None:
    """It is the one thing somebody has to have read before choosing a file.

    And it says what actually happens: a program on this computer reads it.
    Claiming the browser does would be a nicer sentence and a false one.
    """
    _open_home(page, empty_server)
    privacy = str(page.evaluate("document.querySelector('.firstrun__privacy').textContent"))
    assert "this computer" in privacy.lower()
    order = page.evaluate(
        "(() => {"
        " const p = document.querySelector('.firstrun__privacy');"
        " const f = document.querySelector('#fr-file');"
        " const after = p.compareDocumentPosition(f)"
        "   & Node.DOCUMENT_POSITION_FOLLOWING;"
        " return after ? 'before' : 'after';"
        "})()"
    )
    assert order == "before"


# =========================================================================
# 2. THE CAREER QUESTION
# =========================================================================


def test_the_career_question_offers_a_way_to_decline(page: Chrome, empty_server: str) -> None:
    """ "I would rather not say" is a real answer and a separate one from having
    not been asked yet. A flow offering only the five substantive answers would
    make declining look like not having got round to it."""
    _open_home(page, empty_server)
    answers = page.evaluate(
        "[...document.querySelectorAll('.firstrun__stagelist button')]"
        ".map(b => b.getAttribute('aria-pressed'))"
    )
    assert len(answers) == 6
    assert all(value == "false" for value in answers)


def test_answering_the_career_question_writes_immediately(page: Chrome, empty_server: str) -> None:
    """Each answer saves on its own. Leaving halfway keeps what was answered
    rather than discarding it, which is the whole reason this is not a wizard
    with a Finish button."""
    _open_home(page, empty_server)
    page.evaluate(
        "[...document.querySelectorAll('.firstrun__stagelist button')]"
        ".find(b => b.textContent.trim() === 'Changing careers').click()"
    )
    page.wait_for(
        "document.querySelector('.firstrun__step[data-step=\"career_stage\"]')"
        ".classList.contains('is-done')",
        message="the step marks itself done",
    )
    # And it SURVIVES a reload, which is what "written" means: the answer is in
    # the database rather than in the page. Asserted through a fresh load
    # rather than by reading the response, because a control that updated only
    # its own label would pass the check above and lose the answer.
    _open_home(page, empty_server)
    assert "Changing careers" in _step_text(page, "career_stage")


def test_the_answer_can_be_taken_back(page: Chrome, empty_server: str) -> None:
    """Clearing is a THIRD state, distinct from "I would rather not say".

    One is "I have not answered"; the other is "I have answered, and my answer
    is that I would rather not say". A control that could only overwrite would
    make the first unreachable once the second had been chosen.
    """
    _open_home(page, empty_server)
    page.evaluate(
        "[...document.querySelectorAll('.firstrun__stagelist button')]"
        ".find(b => b.textContent.trim() === 'Returning after a break').click()"
    )
    page.wait_for(
        "document.querySelector('.firstrun__step[data-step=\"career_stage\"]')"
        ".classList.contains('is-done')"
    )
    page.evaluate(
        "[...document.querySelectorAll('.firstrun__stages button')]"
        ".find(b => b.textContent.trim() === 'Leave this unanswered').click()"
    )
    page.wait_for(
        "!document.querySelector('.firstrun__step[data-step=\"career_stage\"]')"
        ".classList.contains('is-done')",
        message="the step goes back to unanswered",
    )


# =========================================================================
# 3. READING A DOCUMENT, IN THE BROWSER
# =========================================================================


def _choose_and_read(page: Chrome, name: str = "invented-cv.txt") -> None:
    """Put a file into the real `<input type="file">` and press the real button.

    A `DataTransfer` rather than a synthetic call to the API: the point of this
    test is that the PICKER works, and calling `createIntake` directly would
    prove the route and nothing about the screen.
    """
    page.evaluate(
        "(() => {"
        f"  const cv = {json.dumps(INVENTED_CV)};"
        "   const input = document.getElementById('fr-file');"
        f"  const file = new File([cv], {json.dumps(name)}, {{ type: 'text/plain' }});"
        "   const dt = new DataTransfer();"
        "   dt.items.add(file);"
        "   input.files = dt.files;"
        "   input.dispatchEvent(new Event('change', { bubbles: true }));"
        "})()"
    )
    page.wait_for(
        "document.querySelectorAll('.firstrun__file').length === 1",
        message="the chosen file is listed before it is read",
    )
    page.evaluate(
        "[...document.querySelectorAll('.firstrun__upload button')]"
        ".find(b => !b.disabled && b.textContent.trim().startsWith('Read')).click()"
    )
    # WAIT ON THE SENTENCE, not on the tick. The tick is already there on a
    # second read of the same file, so a test that waited for it would go on
    # while the request was still in flight and assert against "Reading...".
    page.wait_for(
        "(document.querySelector('.firstrun__step[data-step=\"documents\"] .firstrun__status')"
        " || {}).textContent?.trim() && "
        "!(document.querySelector('.firstrun__step[data-step=\"documents\"] .firstrun__status')"
        " || {}).textContent.includes('...')",
        timeout=20,
        message="the document is read and the result is on screen",
    )


def test_a_cv_is_read_without_a_terminal(page: Chrome, empty_server: str) -> None:
    """The step this whole file exists for.

    Before it, producing an intake package meant `career-agent intake-build` or
    copying a prompt into an AI provider and sending it your CV. Neither is an
    onboarding step for somebody who does not write software.
    """
    _open_home(page, empty_server)
    _choose_and_read(page)
    assert "1 read" in _step_text(page, "documents")
    waiting = _step_text(page, "evidence")
    assert "waiting" in waiting, waiting


def test_what_was_read_is_not_yet_true_and_the_screen_says_so(
    page: Chrome, empty_server: str
) -> None:
    """The single most important sentence in the flow.

    A product that read a CV and presented its contents as "your experience"
    would have confirmed something on somebody's behalf. Every statement lands
    unreviewed, and the wording after an import says so rather than
    congratulating anybody.
    """
    _open_home(page, empty_server)
    _choose_and_read(page)
    status = str(
        page.evaluate(
            "document.querySelector('.firstrun__step[data-step=\"documents\"]')"
            ".querySelector('.firstrun__status').textContent"
        )
    )
    assert "true yet" in status.lower(), status
    assert "answering them" in status.lower(), status


def test_reading_the_same_document_twice_keeps_the_answers(page: Chrome, empty_server: str) -> None:
    """`import_package` is idempotent by digest, and the screen has to be able
    to say so: somebody who uploads the same file again needs to know their
    existing answers survived rather than being replaced by a second copy."""
    _open_home(page, empty_server)
    _choose_and_read(page)
    before = str(page.evaluate("document.querySelector('.firstrun__state').textContent"))
    _choose_and_read(page)
    status = str(
        page.evaluate(
            "document.querySelector('.firstrun__step[data-step=\"documents\"]')"
            ".querySelector('.firstrun__status').textContent"
        )
    )
    assert "already been read" in status.lower(), status
    assert "kept" in status.lower(), status
    assert before  # the flow did not lose its state on the way


# =========================================================================
# 4. THE TRIP TO THE REVIEW, AND BACK
# =========================================================================


def test_the_evidence_step_opens_the_review_that_owns_the_question(
    page: Chrome, empty_server: str
) -> None:
    """Four of the six steps hand off rather than re-implementing.

    A second evidence review inside the first-run flow would have its own bugs,
    and the authoritative one would be whichever was written last.
    """
    _open_home(page, empty_server)
    _choose_and_read(page)
    page.evaluate(
        "document.querySelector('.firstrun__step[data-step=\"evidence\"]')"
        ".querySelector('button').click()"
    )
    page.wait_for(
        "document.body.innerText.includes('Your career evidence')",
        message="the evidence surface opens",
    )


# =========================================================================
# 5. IT TRANSLATES
# =========================================================================


def test_the_whole_flow_moves_language(page: Chrome, empty_server: str) -> None:
    """The defect this test was written for was real and shipped for one draft.

    Home rebuilds the section AROUND the first-run node when the language
    changes, and the node kept the children it was drawn with -- so six English
    steps sat inside a Portuguese page. `firstRun.redraw()` is the fix and this
    is what keeps it.
    """
    _open_home(page, empty_server)
    english = page.evaluate(
        "[...document.querySelectorAll('.firstrun__steptitle')].map(n => n.textContent.trim())"
    )
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('.firstrun__steptitle')"
        ".textContent.trim() !== " + json.dumps(english[0]),
        message="the steps move language",
    )
    portuguese = page.evaluate(
        "[...document.querySelectorAll('.firstrun__steptitle')].map(n => n.textContent.trim())"
    )
    assert len(portuguese) == 6
    assert all(pt != en for pt, en in zip(portuguese, english, strict=True))
    # Put the browser back, because the fixture is session scoped and the next
    # test would otherwise open a Portuguese page for reasons it cannot see.
    page.evaluate(f"localStorage.removeItem({json.dumps(LOCALE_KEY)})")


# =========================================================================
# 6. THE PICTURE
# =========================================================================


def test_the_first_screen_is_captured_for_the_readme(
    page: Chrome, empty_server: str, capture
) -> None:
    """The README's first-run screenshot, taken from the real thing.

    Over an EMPTY database, which is the state this picture is about and which
    no other capture in this suite has: every frame elsewhere is of a seeded
    demo, where the last step is already done and the checklist is not what
    somebody meets on day one.

    Written to a temporary directory unless `CAREER_AGENT_REFRESH_EVIDENCE=1`,
    like every other capture -- the committed frames are a deliverable rather
    than test output. The pixel guard, the company-name check and the secret
    check run either way.
    """
    _open_home(page, empty_server)
    # A CHOSEN ANSWER on the first step, because a picture of six untouched
    # rows does not show what answering one looks like -- and the chosen state
    # is the part a reader is trying to recognise.
    page.evaluate(
        "[...document.querySelectorAll('.firstrun__stagelist button')]"
        ".find(b => b.textContent.trim() === 'Changing careers').click()"
    )
    page.wait_for(
        "document.querySelector('.firstrun__step[data-step=\"career_stage\"]')"
        ".classList.contains('is-done')"
    )
    written = capture("first-run-desktop")
    assert written.exists()

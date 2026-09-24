"""Reviewing a Candidate Intake Package in a browser.

WHY THIS FILE EXISTS SEPARATELY FROM `test_evidence_workspace.py`
----------------------------------------------------------------
That file covers reading a CV: forty proposals, one document, one list. This
one covers the thing that broke at real scale. The owner's package holds 309
proposed claims, and the failure mode is not a bug -- it is a wall. A list of
309 sentences about your own career is unreviewable, so every claim in it stays
unreviewed forever and the product silently has no evidence to prepare with.

So the assertions here are about SHAPE as much as behaviour:

  * the overview returns headings, never three hundred sentences;
  * a heading is a job she recognises, with the dates her document wrote;
  * a disagreement between two documents is asked ONCE, not once per sentence;
  * nothing on the screen can confirm a claim in bulk;
  * a claim whose dates are disputed cannot be confirmed at all until she has
    settled them, because confirming it would write a date her documents
    disagree about onto a fact she stands behind.

EVERY PACKAGE HERE IS SYNTHETIC. The employers, dates and sentences are
invented for this file. The owner's real documents are never read by a test,
never copied into one, and nothing about them is asserted.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome

from career_agent.config.search_config import load_search_config
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

LOCALE_KEY = "careerAgent.locale.v1"
DEMO_FILE = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"

#: Two invented employers, two invented documents, and one real disagreement:
#: the CV ends the Contoso role in February and the profile ends it in March.
#: Everything else about the role is stated identically by both, which is what
#: makes the disagreement worth asking about rather than a formatting artifact.
CONTOSO = "Contoso Health"
NORTHWIND = "Northwind Retail"


def _employment(ref: str, text: str, employer: str, end: str, end_read: str) -> dict:
    return {
        "type": "EMPLOYMENT",
        "text": text,
        "source_ref": ref,
        "employer": employer,
        "period": {
            "start": {"original": "Dec 2018", "normalized": "2018-12"},
            "end": {"original": end, "normalized": end_read},
        },
        "evidence": {"quote": f"{text} ({employer}, Dec 2018 to {end})"},
        "tools": ["Workato"],
    }


def synthetic_package(scale: int = 4) -> dict:
    """One package, at a size the caller chooses.

    `scale` multiplies the bullets per employer, so the same fixture can be a
    handful of claims for a behavioural test and three hundred for the one
    that measures whether the screen still arrives quickly.
    """
    claims: list[dict] = [
        _employment("cv", f"Integration Consultant at {CONTOSO}", CONTOSO, "Feb 2021", "2021-02"),
        _employment("li", f"Integration Consultant at {CONTOSO}", CONTOSO, "Mar 2021", "2021-03"),
    ]
    for n in range(scale):
        claims.append(
            _employment(
                "cv" if n % 2 == 0 else "li",
                f"Ran the {['billing', 'onboarding', 'reporting', 'partner'][n % 4]} "
                f"workstream number {n}",
                CONTOSO,
                "Feb 2021",
                "2021-02",
            )
        )
        claims.append(
            {
                "type": "ACHIEVEMENT",
                "text": f"Shipped the self-serve portal, release {n}",
                "source_ref": "cv",
                "employer": NORTHWIND,
                "period": {
                    "start": {"original": "Apr 2017", "normalized": "2017-04"},
                    "end": {"original": "Nov 2018", "normalized": "2018-11"},
                },
                "evidence": {"locator": f"Experience, {NORTHWIND}"},
                "metrics": [{"original": f"cut handover time by {10 + n}%"}],
            }
        )
    claims.extend(
        {
            "type": "SKILL",
            "text": skill,
            "source_ref": "cv",
            "evidence": {"locator": "Skills"},
        }
        for skill in ("Solution design", "Discovery workshops", "API integration")
    )
    return {
        "schema_version": "1.0",
        "generator": {"kind": "EXTERNAL_AI", "name": "an assistant"},
        "sources": [
            {"ref": "cv", "kind": "RESUME", "title": "invented-cv.docx"},
            {"ref": "li", "kind": "LINKEDIN", "title": "invented-profile.pdf"},
        ],
        "claims": claims,
    }


def _serve(db_path: Path, config_dir: Path) -> Iterator[str]:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db_path, config_dir=config_dir, port=port), quiet=True)
    httpd: ThreadingHTTPServer = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


def _stage(db_path: Path, config_dir: Path, package: dict) -> None:
    from career_agent.intake import parse_package
    from career_agent.intake.store import import_package

    conn = connect(db_path)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        load_search_config(config_dir)
        import_package(conn, parse_package(package), filename="invented-package.json")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def package_server(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    """A server over a database holding ONE staged synthetic package.

    Function scoped, because these tests CONFIRM CLAIMS. A confirmed claim in
    a shared database answers requirements in every later test, which is how a
    suite starts agreeing with itself.
    """
    db_path = tmp_path / "package.db"
    _stage(db_path, committed_config, synthetic_package())
    yield from _serve(db_path, committed_config)


@pytest.fixture
def big_package_server(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    """The same thing at the owner's real scale: 300-odd staged claims."""
    db_path = tmp_path / "big.db"
    _stage(db_path, committed_config, synthetic_package(scale=150))
    yield from _serve(db_path, committed_config)


@pytest.fixture
def job_and_package_server(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    """A corpus AND a staged package, for the loop that joins them.

    The contextual trip -- a requirement on a job, then what is waiting to be
    asked about it -- needs both halves in one database, which no other
    fixture in this file provides.
    """
    from career_agent.pipeline.demo_seed import seed_demo

    db_path = tmp_path / "both.db"
    _stage(db_path, committed_config, synthetic_package())
    conn = connect(db_path)
    try:
        config, _ = load_search_config(committed_config)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    yield from _serve(db_path, committed_config)


# =========================================================================
# getting there
# =========================================================================


def open_evidence(page: Chrome, server: str) -> None:
    page.navigate(server)
    page.wait_for(
        "document.querySelector('.topnav__link[data-page=\"evidence\"]') !== null",
        message="the global navigation",
    )
    page.evaluate("document.querySelector('.topnav__link[data-page=\"evidence\"]').click()")
    # The statement manager moved behind Evidence: "Manage all statements".
    page.wait_for("document.querySelector('#page-evidence .evp-manage .cw-link') !== null")
    page.evaluate("document.querySelector('#page-evidence .evp-manage .cw-link').click()")
    page.wait_for(
        "document.querySelector('#page-manage .ev__privacy') !== null",
        message="the statement manager",
    )


def click_text(page: Chrome, needle: str, scope: str = "#page-manage") -> None:
    """Click the button a person would click: by the words on it."""
    page.evaluate(
        "(() => {"
        f" const wanted = {json.dumps(needle)};"
        f" const found = [...document.querySelectorAll('{scope} button')]"
        "    .find((b) => b.textContent.includes(wanted));"
        "  if (!found) throw new Error('no button says ' + wanted);"
        "  found.click();"
        "})()"
    )


def texts(page: Chrome, selector: str) -> list[str]:
    return list(
        page.evaluate(
            f"[...document.querySelectorAll('#page-manage {selector}')].map(n => n.textContent)"
        )
    )


def open_package(page: Chrome, server: str, start: str = "Start review") -> None:
    """`start` is the words on the button, so the Portuguese test can open the
    same package by reading the same screen a Portuguese reader reads."""
    open_evidence(page, server)
    page.wait_for(
        "document.querySelectorAll('#page-manage .ev__import').length > 0",
        message="the staged import",
    )
    # THE IMPORTS MOVED TO THE BOTTOM AND BEHIND A DISCLOSURE. They are the
    # answer to "where did this come from", which is the third question this
    # page answers rather than the first. Opening the fold is what a reader
    # does; the button inside it is the same button.
    page.evaluate(
        "(() => { const f = document.querySelector('#page-manage .ev__sources');"
        " if (f) f.open = true; })()"
    )
    click_text(page, start)
    # The OVERVIEW's own heading, not `.ev__meter`: the package row on the
    # home view draws a meter too, so waiting for one is waiting for something
    # that was already on the screen -- which is how this test first "passed"
    # against a page it had never left.
    page.wait_for(
        "document.querySelector('#page-manage .ev__reviewhead') !== null",
        message="the package overview",
    )


def open_group(page: Chrome, employer: str) -> None:
    page.evaluate(
        "(() => {"
        f" const wanted = {json.dumps(employer)};"
        "  const card = [...document.querySelectorAll('#page-manage .ev__card--group')]"
        "    .find((c) => c.querySelector('.ev__proposal')"
        "      && c.querySelector('.ev__proposal').textContent === wanted);"
        "  if (!card) throw new Error('no group for ' + wanted);"
        "  card.querySelector('.ev__actions button').click();"
        "})()"
    )
    # The GROUP's own heading. `.ev__reviewhead` is on the overview too, so
    # waiting for one is waiting for something already on the screen -- and a
    # predicate that is true before the click is not a wait at all.
    page.wait_for(
        "(document.querySelector('#page-manage .d-sec__head') || {}).textContent === "
        + json.dumps(employer),
        message=f"the {employer} group",
    )


# =========================================================================
# 1. HEADINGS, NEVER A WALL
# =========================================================================


def test_the_overview_shows_headings_and_not_the_claims(page: Chrome, package_server: str) -> None:
    """The property the whole design exists for.

    The overview may name jobs, counts and documents. It may not put a single
    proposed sentence on the screen, because three hundred of those is the
    wall this replaced.
    """
    open_package(page, package_server)

    body = str(page.evaluate("document.querySelector('#page-manage').innerText"))
    assert CONTOSO in body, "the overview did not name the job"
    assert "Ran the billing workstream number 0" not in body, (
        "a proposed sentence reached the overview; that is the wall coming back"
    )


def test_a_heading_carries_the_dates_the_document_wrote(page: Chrome, package_server: str) -> None:
    """`Dec 2018`, not `2018-12`. The wording is the evidence and the reading
    is somebody's interpretation of it; a heading showing only the reading
    hides the half she can actually check."""
    open_package(page, package_server)

    headings = " ".join(texts(page, ".ev__meta"))
    assert "Dec 2018" in headings
    assert "2018-12" not in headings


def test_the_documents_behind_the_package_are_named(page: Chrome, package_server: str) -> None:
    open_package(page, package_server)
    names = texts(page, ".ev__docname")
    assert "invented-cv.docx" in names
    assert "invented-profile.pdf" in names


def test_progress_is_announced_to_a_screen_reader(page: Chrome, package_server: str) -> None:
    """The bar is a picture. Without a label it is a decorative rectangle, and
    the one fact it carries is invisible to anybody not looking at it."""
    open_package(page, package_server)
    label = page.evaluate(
        "document.querySelector('#page-manage .ev__meter').getAttribute('aria-label')"
    )
    assert label and "of" in str(label)


# =========================================================================
# 2. ONE GROUP AT A TIME
# =========================================================================


def test_a_group_holds_everything_about_one_job(page: Chrome, package_server: str) -> None:
    """Bullets, achievements and projects from one job are one memory. Asking
    about them on three separate screens is asking somebody to remember the
    same year three times."""
    open_package(page, package_server)
    open_group(page, CONTOSO)

    proposals = texts(page, ".ev__proposal")
    assert any("Integration Consultant" in text for text in proposals)
    assert any("workstream" in text for text in proposals)
    # And nothing from the other job.
    assert not any("self-serve portal" in text for text in proposals)


def test_every_claim_shows_where_it_came_from(page: Chrome, package_server: str) -> None:
    """Provenance on EVERY card. A citation that appears only sometimes teaches
    people to stop looking for it, and looking for it is the entire mechanism
    by which a package written by somebody else's model can be trusted."""
    open_package(page, package_server)
    open_group(page, NORTHWIND)

    cards = page.evaluate(
        "[...document.querySelectorAll('#page-manage .ev__card')].map((card) => ({"
        "  labels: [...card.querySelectorAll('.ev__fromlabel')].map(n => n.textContent),"
        "  cited: card.querySelector('.quote') !== null"
        "      || [...card.querySelectorAll('.ev__meta')].some(n =>"
        "           n.textContent.includes('Found in')),"
        "}))"
    )
    assert cards, "no cards rendered"
    for card in cards:
        assert any("invented-" in label for label in card["labels"]), card
        assert card["cited"], card


def test_both_halves_of_every_date_are_on_the_card(page: Chrome, package_server: str) -> None:
    open_package(page, package_server)
    open_group(page, NORTHWIND)

    meta = " ".join(texts(page, ".ev__meta"))
    assert "Apr 2017" in meta, "the document's own wording is missing"
    assert "2017-04" in meta, "the reading of it is missing"


def test_a_figure_stays_inside_its_sentence(page: Chrome, package_server: str) -> None:
    open_package(page, package_server)
    open_group(page, NORTHWIND)

    figures = texts(page, ".ev__figure")
    assert figures, "the figure was not flagged"
    assert any("cut handover time by 10%" in text for text in figures), (
        "the number was lifted out of the sentence it was stated in"
    )


# =========================================================================
# 3. THE DISAGREEMENT, ASKED ONCE
# =========================================================================


def test_the_disagreement_is_one_question_with_both_sides(
    page: Chrome, package_server: str
) -> None:
    open_package(page, package_server)

    sides = texts(page, ".ev__sidedates")
    assert len(sides) == 2, sides
    joined = " ".join(sides)
    assert "Feb 2021" in joined and "Mar 2021" in joined
    # Which document said which, in her own filenames.
    meta = " ".join(texts(page, ".ev__sidemeta"))
    assert "invented-cv.docx" in meta and "invented-profile.pdf" in meta


def test_a_disputed_claim_cannot_be_confirmed_until_the_dates_are_settled(
    page: Chrome, package_server: str
) -> None:
    """The release-blocking half of this screen.

    Every claim about a role whose dates are disputed CARRIES those dates, so
    confirming one would write a date two documents disagree about onto a fact
    she stands behind. There is no confirm button on such a card at all.
    """
    open_package(page, package_server)
    open_group(page, CONTOSO)

    buttons = texts(page, ".ev__card .ev__actions button")
    assert buttons, "no actions rendered"
    assert not any("Yes, that is true" in text for text in buttons), (
        "a claim with disputed dates offered a confirm button"
    )
    assert any("Settle the dates first" in text for text in buttons)


def test_settling_it_once_releases_every_claim_it_held(page: Chrome, package_server: str) -> None:
    open_package(page, package_server)
    click_text(page, "These dates are right")
    page.wait_for(
        "document.querySelector('#page-manage .ev__sidechosen') !== null",
        message="the resolution",
    )

    open_group(page, CONTOSO)
    buttons = texts(page, ".ev__card .ev__actions button")
    assert any("Yes, that is true" in text for text in buttons), (
        "settling the dates did not release the claims"
    )


def test_settling_it_confirms_nothing(page: Chrome, package_server: str) -> None:
    """Agreeing about when a role ran is not standing behind a sentence about
    it. Every released claim still has to be answered on its own."""
    open_package(page, package_server)
    click_text(page, "These dates are right")
    page.wait_for(
        "document.querySelector('#page-manage .ev__sidechosen') !== null",
        message="the resolution",
    )
    click_text(page, "Back to your evidence")
    page.wait_for(
        "document.querySelector('#page-manage .ev__privacy') !== null",
        message="the evidence page",
    )

    claims = page.evaluate("document.querySelectorAll('#page-manage .evrow').length")
    assert claims == 0, "resolving a disagreement created a verified claim"


# =========================================================================
# 4. NOTHING CONFIRMS IN BULK
# =========================================================================


def test_there_is_no_confirm_everything(page: Chrome, package_server: str) -> None:
    """Anything confirmed here can end up on a real application. One click
    cannot honestly mean somebody read three hundred sentences."""
    open_package(page, package_server)
    open_group(page, NORTHWIND)

    buttons = texts(page, "button")
    for text in buttons:
        lowered = text.lower()
        assert not (("all" in lowered or "every" in lowered) and "confirm" in lowered), text
    # The one batch action offered is the one that creates nothing.
    assert any("Set the remaining" in text for text in buttons)


def test_confirming_one_claim_creates_exactly_one(page: Chrome, package_server: str) -> None:
    open_package(page, package_server)
    open_group(page, NORTHWIND)
    first = texts(page, ".ev__proposal")[0]

    click_text(page, "Yes, that is true")
    page.wait_for(
        "[...document.querySelectorAll('#page-manage .ev__tag')]"
        ".some(n => n.textContent.includes('Confirmed'))",
        message="the confirmation",
    )
    click_text(page, "Back to the overview")
    page.wait_for(
        "document.querySelector('#page-manage .ev__sides') !== null",
        message="the overview",
    )
    click_text(page, "Back to your evidence")
    page.wait_for(
        "document.querySelector('#page-manage .evgroup') !== null",
        message="the ledger",
    )
    # The categories are compact rows until they are opened, and a work group
    # builds its claims on first open. Nothing is missing before that.
    page.evaluate(
        "(() => {"
        "  for (const d of document.querySelectorAll('#page-manage .evgroup')) d.open = true;"
        "  for (const d of document.querySelectorAll('#page-manage .ev__employer')) {"
        "    d.open = true;"
        "  }"
        "})()"
    )

    ledger = texts(page, ".evrow__text")
    assert ledger == [first]


def test_an_answer_survives_a_reload(page: Chrome, package_server: str) -> None:
    """A review of three hundred things is not one sitting. Every answer is
    committed on its own, which is what makes closing the tab safe."""
    open_package(page, package_server)
    open_group(page, NORTHWIND)
    click_text(page, "Not sure yet")
    page.wait_for(
        "[...document.querySelectorAll('#page-manage .ev__tag')]"
        ".some(n => n.textContent.includes('Not sure yet'))",
        message="the answer",
    )

    open_package(page, package_server)
    open_group(page, NORTHWIND)
    assert any("Not sure yet" in text for text in texts(page, ".ev__tag"))


# =========================================================================
# 5. IN PORTUGUESE, AND AT SCALE
# =========================================================================


def test_the_review_speaks_portuguese(page: Chrome, package_server: str) -> None:
    page.navigate(package_server)
    page.evaluate(f"window.localStorage.setItem('{LOCALE_KEY}', 'pt-BR')")
    try:
        open_package(page, package_server, start="Começar revisão")
        # Element by element, and case folded. `innerText` is truncated by the
        # DevTools protocol on a long page -- so a whole-page read silently
        # loses its middle -- and these headings are uppercased by CSS, which
        # `innerText` faithfully reports as uppercase.
        heads = [head.casefold() for head in texts(page, ".ev__grouphead")]
        assert any("por onde começar" in head for head in heads), heads
        assert any("onde seus documentos discordam" in head for head in heads), heads
        assert not any("where to start" in head for head in heads), heads
        # And the employer's own name is untouched, in either language.
        assert any(CONTOSO in text for text in texts(page, ".ev__proposal"))
    finally:
        page.evaluate(f"window.localStorage.removeItem('{LOCALE_KEY}')")


def test_a_full_size_package_still_arrives_as_a_screen(
    page: Chrome, big_package_server: str
) -> None:
    """Three hundred claims, and the reader still meets a page rather than a
    scroll bar. The number asserted is the number of HEADINGS, and the budget
    is generous on purpose: this guards against a redesign that renders every
    claim, not against a slow machine."""
    started = time.perf_counter()
    open_package(page, big_package_server)
    elapsed = time.perf_counter() - started

    counts = page.evaluate(
        "({"
        "  cards: document.querySelectorAll('#page-manage .ev__card').length,"
        "  quotes: document.querySelectorAll('#page-manage .quote').length,"
        "})"
    )
    assert counts["cards"] <= 12, counts
    assert counts["quotes"] == 0, "the overview rendered evidence quotes"
    assert elapsed < 10, f"the overview took {elapsed:.1f}s to arrive"


# =========================================================================
# 6. THE LOOP BACK FROM A JOB
# =========================================================================


def test_a_requirement_leads_to_what_is_waiting_about_it(
    page: Chrome, job_and_package_server: str
) -> None:
    """The trip this screen exists to complete.

    Somebody reading a posting says "I have done this, it is not in my
    profile". That answer already named the requirement; the interface used to
    throw it away and open Career Evidence at the top of a long page, so the
    trip ended with nothing saying what it had been for.
    """
    server = job_and_package_server
    page.navigate(server)
    page.wait_for("document.querySelectorAll('[data-job-id]').length > 0", message="the cards")
    page.evaluate("document.querySelectorAll('[data-job-id]')[0].click()")
    page.wait_for(
        "document.querySelectorAll('#drawer-host [role=\"tab\"]').length === 3",
        message="the drawer tabs",
    )
    page.evaluate("document.querySelectorAll('#drawer-host [role=\"tab\"]')[2].click()")
    page.wait_for(
        "document.querySelector('#drawer-panel-prepare .prep__row') !== null",
        message="the prepare panel",
    )
    requirement = str(
        page.evaluate(
            "document.querySelector('#drawer-panel-prepare .prep__row .prep__label').textContent"
        )
    )
    page.evaluate(
        "(() => {"
        "  const row = document.querySelector('#drawer-panel-prepare .prep__row');"
        "  row.querySelector('details').open = true;"
        "  [...row.querySelectorAll('.prep__verdicts button')]"
        "    .find((b) => b.textContent.includes('it is not in my profile')).click();"
        "})()"
    )
    # The row's own prompt, which appears only once the verdict is recorded.
    # `.prep__prompt` alone is satisfied immediately by the panel-level "you
    # have no evidence yet" line, so waiting for it waits for nothing.
    page.wait_for(
        "document.querySelector('#drawer-panel-prepare .prep__row .prep__prompt') !== null",
        message="the route to the evidence",
    )
    # The prompt INSIDE THE ROW. There is a second button with the same words
    # at the top of the panel -- "you have no evidence yet" -- and that one
    # passes no requirement, which is correct for what it is and is not this
    # trip. Scoped to the row, so the test cannot pass by clicking the other.
    page.evaluate(
        "[...document.querySelectorAll("
        "  '#drawer-panel-prepare .prep__row .prep__prompt button')]"
        ".find((b) => b.textContent.includes('Your career evidence')).click()"
    )

    page.wait_for(
        "document.querySelector('#page-manage .ev__focus') !== null",
        message="the focused review",
    )
    focus = " ".join(texts(page, ".ev__focus .ev__note"))
    assert requirement in focus, focus
    # And it is a NARROWING she can see and drop, never a screen that quietly
    # shows a subset of what is waiting.
    assert any("Show everything waiting" in text for text in texts(page, ".ev__focus button"))


# =========================================================================
# WHERE TO START
#
# The grouped overview stops the SCREEN being a wall of three hundred
# sentences. These cover the question underneath it: of the three hundred,
# which ones matter before the product is usable at all.
# =========================================================================


def test_the_overview_opens_with_where_to_start(page: Chrome, package_server: str) -> None:
    """A reader who arrives at "309 waiting" needs a first thing to do, and
    the first thing on the screen should be it."""
    open_package(page, package_server)
    page.wait_for(
        "document.querySelector('#page-manage .ev__group--start') !== null",
        message="the queue",
    )
    # CASE-FOLDED, because the heading is uppercased by the stylesheet and
    # `innerText` returns what is rendered. Asserting the authored casing here
    # would be asserting a CSS rule from a test about content.
    body = str(page.evaluate("document.querySelector('.ev__group--start').innerText"))
    assert "where to start" in body.lower()
    # Every step, including the empty ones: a step that vanished when it
    # emptied would make "answered" and "you have none of these" identical.
    cards = page.evaluate("document.querySelectorAll('#page-manage .ev__card--step').length")
    assert int(cards) == 8, f"expected every step to be listed, saw {cards}"


def test_the_queue_says_it_is_an_order_and_not_a_ranking(page: Chrome, package_server: str) -> None:
    """Priority is navigation, never truth, and a reader must not have to
    infer that from an ordered list."""
    open_package(page, package_server)
    body = str(page.evaluate("document.querySelector('.ev__group--start').innerText"))
    assert "not a ranking" in body
    assert "Nothing is hidden from any step" in body


def test_the_queue_invents_no_score_level_or_percentage(page: Chrome, package_server: str) -> None:
    """Any progress number must have a denominator somebody can point at.

    Read off the RENDERED text rather than the payload, because a fabricated
    completeness figure would be composed in the interface, which is exactly
    where the payload test cannot see it.
    """
    open_package(page, package_server)
    body = str(page.evaluate("document.querySelector('.ev__group--start').innerText"))
    lowered = body.lower()
    for invented in ("lvl", "level ", "badge", "streak", "xp", "%"):
        assert invented not in lowered, f"the queue put {invented!r} on the screen"
    # ...and what it does say names its own denominator.
    assert "of" in body and "answered in this step" in body


def test_the_blocking_step_says_why_rather_than_that_it_matters(
    page: Chrome, package_server: str
) -> None:
    open_package(page, package_server)
    body = str(page.evaluate("document.querySelector('.ev__group--start').innerText"))
    assert "nothing else in the package can be confirmed" in body


def test_opening_a_step_shows_that_step_and_the_four_answers(
    page: Chrome, package_server: str
) -> None:
    """A step is a filter over the same claims, answered the same four ways."""
    open_package(page, package_server)
    # A step that is NOT the blocking one: the disagreement step deliberately
    # offers no confirm button until the dates are settled, so opening it
    # would test the opposite of what this test is about.
    page.evaluate(
        "(() => {"
        "  const cards = [...document.querySelectorAll('#page-manage .ev__card--step')];"
        "  const card = cards.find((c) => c.querySelector('.ev__actions button')"
        "    && !c.querySelector('.ev__figure'));"
        "  if (!card) throw new Error('no ordinary step is openable');"
        "  card.querySelector('.ev__actions button').click();"
        "})()"
    )
    page.wait_for(
        "[...document.querySelectorAll('#page-manage button')]"
        ".some((b) => b.textContent.includes('Yes, that is true'))",
        message="the claims in the step",
    )
    buttons = texts(page, "button")
    joined = " | ".join(buttons)
    for answer in ("Yes, that is true", "Not sure yet", "No"):
        assert answer in joined, f"a step offered no way to answer {answer!r}: {joined[:400]}"
    # ...and the step is still not a way to confirm everything at once.
    for text in buttons:
        lowered = text.lower()
        assert not (("all" in lowered or "every" in lowered) and "confirm" in lowered), text


def test_the_way_back_to_the_groups_and_the_packages_is_on_the_screen(
    page: Chrome, package_server: str
) -> None:
    """A guided path that cannot be left is a cage."""
    open_package(page, package_server)
    body = str(page.evaluate("document.querySelector('#page-manage').innerText"))
    assert "where to start" in body.lower(), "the queue is not on the overview"
    # The GROUPS are still there, under it. A guided path that replaced the
    # rest of the review would be a cage rather than a path.
    assert CONTOSO in body, "the employer groups left the overview"
    assert "Back to your evidence" in " | ".join(texts(page, "button"))

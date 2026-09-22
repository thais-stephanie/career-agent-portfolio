"""Reading a CV, answering it, and preparing an application -- in a browser.

Driven through the real page against the real server. The assertions are about
what a person experiences, and specifically about the four things this surface
could get wrong in ways that would not look like bugs:

  * a document is read and something is quietly confirmed;
  * an edit rewrites the line it was read from, so the citation no longer
    cites anything;
  * a decision is lost when the tab closes;
  * a requirement with no evidence is dropped from the page.

Every test uses `pristine_server`. These tests CONFIRM CLAIMS, and a claim
written into the session database would answer requirements in every later
test, which is how a suite starts agreeing with itself.
"""

from __future__ import annotations

import json

from tests.browser.chrome import Chrome

#: A CV nobody has. Written for this file, with headings the reader knows, one
#: line carrying a figure, and one list line that becomes several proposals.
SYNTHETIC_CV = "\n".join(
    [
        "Jordan Vale",
        "Business Systems Analyst",
        "",
        "Experience",
        "Built automated deal-to-ticket workflows in HubSpot for the billing team",
        "Owned the reporting stack and reduced manual reconciliation by 40%",
        "Ran discovery with sales and support to map the order-to-cash process",
        "",
        "Skills",
        "Process mapping, requirements gathering, stakeholder interviews",
        "",
        "Tools",
        "HubSpot, Salesforce, SQL, Looker, n8n",
        "",
    ]
)


def open_evidence(page: Chrome, server: str) -> None:
    """Career Evidence is a page now, reached from the global navigation.

    It used to be a rail button that opened an overlay. The overlay still
    exists and is still what the Prepare tab opens -- somebody comparing a
    requirement against her evidence should not lose the posting to do it --
    but reviewing forty proposals is not a thing to do inside a dialog on top
    of a job.
    """
    page.navigate(server)
    page.wait_for(
        "document.querySelector('.topnav__link[data-page=\"evidence\"]') !== null",
        message="the global navigation",
    )
    page.evaluate("document.querySelector('.topnav__link[data-page=\"evidence\"]').click()")
    page.wait_for(
        "document.querySelector('#page-evidence .ev__privacy') !== null",
        message="the evidence page",
    )


def import_cv(page: Chrome) -> None:
    """Choose a file the way a person does: through the file input itself.

    A `DataTransfer` rather than a call into the module, so what is exercised
    is the change handler, the byte reading and the base64 encoding -- the
    three steps between a person picking a file and the server seeing it.
    """
    page.evaluate(
        "(() => {"
        f"  const cv = {json.dumps(SYNTHETIC_CV)};"
        "   const input = document.getElementById('ev-file');"
        "   const box = new DataTransfer();"
        "   box.items.add(new File([cv], 'jordan-vale-cv.txt', { type: 'text/plain' }));"
        "   input.files = box.files;"
        "   input.dispatchEvent(new Event('change', { bubbles: true }));"
        "})()"
    )
    page.wait_for(
        "document.querySelectorAll('#page-evidence .ev__card').length > 0",
        message="the review cards",
    )


def cards(page: Chrome) -> list:
    return list(
        page.evaluate(
            "[...document.querySelectorAll('#page-evidence .ev__card')].map((card) => ({"
            "  proposal: card.querySelector('.ev__proposal').textContent,"
            "  source: card.querySelector('.ev__from .quote').textContent,"
            "  decision: card.querySelector('.ev__decision')"
            "    ? card.querySelector('.ev__decision').textContent : null,"
            "  figure: card.querySelector('.ev__figure') !== null,"
            "}))"
        )
    )


def answer(page: Chrome, index: int, label: str) -> None:
    """Click one of the three answers on one card, by its visible words."""
    page.evaluate(
        "(() => {"
        f" const card = [...document.querySelectorAll('#page-evidence .ev__card')][{index}];"
        f" const wanted = {json.dumps(label)};"
        "  const found = [...card.querySelectorAll('.ev__actions button')]"
        "    .find((b) => b.textContent.includes(wanted));"
        "  found.click();"
        "})()"
    )


def open_every_category(page: Chrome) -> None:
    """Open every category and every employer fold in the evidence browser.

    THE ROWS ARE NOT THERE UNTIL SOMETHING IS OPENED, and that is the point of
    the redesign rather than an obstacle to it: a category is a compact row
    until it is asked for, and a work group builds its claims on first open.
    A test that queried `.evrow` on arrival would be asserting about a screen
    the product deliberately does not draw.
    """
    page.evaluate(
        "(() => {"
        "  for (const d of document.querySelectorAll('#page-evidence .evgroup')) d.open = true;"
        "  for (const d of document.querySelectorAll('#page-evidence .ev__employer')) {"
        "    d.open = true;"
        "  }"
        "})()"
    )


def ledger(page: Chrome) -> list:
    open_every_category(page)
    return list(
        page.evaluate(
            "[...document.querySelectorAll('#page-evidence .evrow')].map((row) => ({"
            "  text: row.querySelector('.evrow__text').textContent,"
            "  origin: row.querySelector('.quote--origin')"
            "    ? row.querySelector('.quote--origin').textContent : null,"
            "  retired: row.classList.contains('evrow--aside'),"
            "}))"
        )
    )


def back_to_ledger(page: Chrome) -> None:
    page.evaluate(
        "[...document.querySelectorAll('#page-evidence button')]"
        ".find((b) => b.textContent.includes('Back to your evidence')).click()"
    )
    # Waits on the INTAKE section, which is drawn whenever the review is not.
    # `.ev__ledger` would have been the obvious thing to wait for and is wrong:
    # a ledger with nothing in it is prose rather than an empty list, so a test
    # that rejected its only proposal would wait for a list that correctly
    # never appears.
    page.wait_for(
        "document.querySelector('#page-evidence .ev__privacy') !== null",
        message="the ledger view",
    )


# =========================================================================
# 1. READING CONFIRMS NOTHING
# =========================================================================


def test_reading_a_cv_confirms_nothing(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    import_cv(page)
    assert all(card["decision"] is None for card in cards(page))
    confirmed = page.evaluate("document.querySelector('#page-evidence .ev__reviewhead') !== null")
    assert confirmed, "the review did not open"


def test_every_card_shows_the_line_it_was_read_from(page: Chrome, pristine_server: str) -> None:
    """Drawn always, even when it equals the proposal. A citation that appears
    only sometimes teaches people to stop looking for it, and looking for it is
    the whole mechanism."""
    open_evidence(page, pristine_server)
    import_cv(page)
    for card in cards(page):
        assert card["source"].strip(), card["proposal"]
        assert card["source"] in SYNTHETIC_CV


def test_a_figure_is_flagged_on_the_card_that_carries_it(
    page: Chrome, pristine_server: str
) -> None:
    open_evidence(page, pristine_server)
    import_cv(page)
    flagged = [card for card in cards(page) if card["figure"]]
    assert len(flagged) == 1
    assert "40%" in flagged[0]["proposal"], "the figure was lifted out of its sentence"


# =========================================================================
# 2. THREE ANSWERS, AND EDIT IS THE ONE THAT MATTERS
# =========================================================================


def test_accepting_puts_the_line_in_the_ledger(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    import_cv(page)
    first = cards(page)[0]["proposal"]
    answer(page, 0, "Yes, that is true")
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision') !== null",
        message="the decision mark",
    )
    back_to_ledger(page)
    assert [row["text"] for row in ledger(page)] == [first]


def test_an_edit_keeps_the_document_line_beside_the_correction(
    page: Chrome, pristine_server: str
) -> None:
    """Section 10 of the V1.3 brief, asserted on screen. The claim may be her
    wording; the line her CV carried is never rewritten to agree with it."""
    open_evidence(page, pristine_server)
    import_cv(page)
    original = cards(page)[1]["proposal"]
    corrected = "Owned the reporting stack and cut manual reconciliation"

    answer(page, 1, "Not quite")
    page.evaluate(
        "(() => {"
        "  const card = [...document.querySelectorAll('#page-evidence .ev__card')][1];"
        "  const box = card.querySelector('textarea');"
        f" box.value = {json.dumps(corrected)};"
        "  box.dispatchEvent(new Event('input', { bubbles: true }));"
        "  [...card.querySelectorAll('button')]"
        "    .find((b) => b.textContent.includes('Confirm my wording')).click();"
        "})()"
    )
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision--edited') !== null",
        message="the edited mark",
    )
    back_to_ledger(page)

    rows = ledger(page)
    assert len(rows) == 1
    assert rows[0]["text"] == corrected
    assert rows[0]["origin"] == original
    assert "40%" in str(rows[0]["origin"]), "the source line lost the figure it carried"


def test_rejecting_confirms_nothing(page: Chrome, pristine_server: str) -> None:
    open_evidence(page, pristine_server)
    import_cv(page)
    answer(page, 0, "No, drop it")
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision--rejected') !== null",
        message="the dropped mark",
    )
    back_to_ledger(page)
    assert ledger(page) == []


# =========================================================================
# 3. A REVIEW SURVIVES THE TAB CLOSING
# =========================================================================


def test_answers_survive_a_reload(page: Chrome, pristine_server: str) -> None:
    """Somebody answers two proposals and closes the browser. Both are still
    answered, and the rest are still reviewable."""
    open_evidence(page, pristine_server)
    import_cv(page)
    total = len(cards(page))
    answer(page, 0, "Yes, that is true")
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision') !== null",
        message="the first decision",
    )
    answer(page, 1, "No, drop it")
    page.wait_for(
        "document.querySelectorAll('#page-evidence .ev__decision').length === 2",
        message="the second decision",
    )

    open_evidence(page, pristine_server)
    pending = page.evaluate("document.querySelector('#page-evidence .ev__importstate').textContent")
    assert str(pending) == f"{total - 2} still to answer"

    page.evaluate(
        "[...document.querySelectorAll('#page-evidence button')]"
        ".find((b) => b.textContent.includes('Continue reviewing')).click()"
    )
    page.wait_for(
        "document.querySelectorAll('#page-evidence .ev__card').length > 0",
        message="the reopened review",
    )
    decided = [card["decision"] for card in cards(page) if card["decision"]]
    assert len(decided) == 2


# =========================================================================
# 4. THE LEDGER
# =========================================================================


def test_a_fact_written_by_hand_says_it_was_written_by_hand(
    page: Chrome, pristine_server: str
) -> None:
    open_evidence(page, pristine_server)
    page.evaluate(
        # WHICHEVER BOX THE CATEGORY IS SHOWING. Adding something picks a
        # category first now and the field follows it: a skill or a tool gets a
        # single line, everything else keeps the paragraph. Typing into
        # `#ev-new-text` unconditionally wrote into the hidden one.
        "(() => {"
        "  document.querySelector('.ev__add').open = true;"
        "  const box = [...document.querySelectorAll("
        "    '#page-evidence .ev__addbox input, #page-evidence .ev__addbox textarea')]"
        "    .find((n) => !n.hidden);"
        "  box.value = 'Ran the order-to-cash redesign at Acme';"
        "  [...document.querySelectorAll('#page-evidence .ev__add button')]"
        "    .find((b) => b.textContent.includes('Confirm this about me')).click();"
        "})()"
    )
    page.wait_for(
        "document.querySelector('#page-evidence .evgroup') !== null",
        message="the new claim",
    )
    open_every_category(page)
    sources = page.evaluate(
        "[...document.querySelectorAll('#page-evidence .evrow__src')].map(n => n.textContent)"
    )
    assert list(sources) == ["Added by you"]
    assert ledger(page)[0]["origin"] is None, "a hand-written claim cited something"


def test_retiring_keeps_the_claim_and_marks_it(page: Chrome, pristine_server: str) -> None:
    """Not a delete. The claim may already have prepared an application
    somebody sent, and the record of what she believed then is the record."""
    open_evidence(page, pristine_server)
    import_cv(page)
    answer(page, 0, "Yes, that is true")
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision') !== null",
        message="the decision",
    )
    back_to_ledger(page)

    page.evaluate("window.confirm = () => true")
    open_every_category(page)
    # THROUGH THE DISCLOSURE, the way a person reaches it. The row shows the
    # sentence and one control; everything that acts on the claim lives
    # behind `Details`, which is what took this screen from three items per
    # viewport to fourteen.
    page.evaluate("document.querySelector('#page-evidence .evrow .evrow__more').click()")
    page.evaluate(
        "[...document.querySelectorAll('#page-evidence .evrow button')]"
        ".find((b) => b.textContent.includes('Remove from profile')).click()"
    )
    page.wait_for(
        "document.querySelector('#page-evidence .evrow--aside') !== null",
        message="the claim that was removed from the profile",
    )
    rows = ledger(page)
    assert len(rows) == 1 and rows[0]["retired"] is True


# =========================================================================
# 5. PREPARING, WITH THE GAPS IN IT
# =========================================================================


def open_prepare(page: Chrome, server: str) -> None:
    page.navigate(server)
    page.wait_for("document.querySelectorAll('[data-job-id]').length > 0", message="the cards")
    page.evaluate("document.querySelectorAll('[data-job-id]')[0].click()")
    page.wait_for(
        "document.querySelectorAll('#drawer-host [role=\"tab\"]').length === 3",
        message="the three drawer tabs",
    )
    page.evaluate("document.querySelectorAll('#drawer-host [role=\"tab\"]')[2].click()")
    page.wait_for(
        "document.querySelector('#drawer-panel-prepare .prep__tally') !== null"
        " || document.querySelector('#drawer-panel-prepare .prep__empty') !== null",
        message="the prepare panel",
    )


def test_with_nothing_confirmed_every_requirement_is_a_gap(
    page: Chrome, pristine_server: str
) -> None:
    open_prepare(page, pristine_server)
    counts = page.evaluate(
        "[...document.querySelectorAll('#drawer-panel-prepare .prep__tallyitem')]"
        ".map((n) => n.textContent)"
    )
    joined = " ".join(str(item) for item in counts)
    assert "0Related confirmed evidence" in joined.replace("✓", "").replace("≈", "")
    gaps = page.evaluate(
        "document.querySelectorAll('#drawer-panel-prepare .prep__group--gap .prep__row').length"
    )
    assert int(gaps) > 0, "a posting with no evidence behind it showed no gaps"


def test_a_gap_is_drawn_with_the_employer_quote_and_no_evidence(
    page: Chrome, pristine_server: str
) -> None:
    """A gap is not hidden and not softened. It shows what they asked for and
    shows nothing on the candidate's side, because there is nothing."""
    open_prepare(page, pristine_server)
    row = page.evaluate(
        "(() => {"
        "  const first = document.querySelector("
        "    '#drawer-panel-prepare .prep__group--gap .prep__row');"
        "  return {"
        "    theySay: first.querySelector('.prep__said .quote')"
        "      ? first.querySelector('.prep__said .quote').textContent : null,"
        "    mine: first.querySelector('.quote--mine') !== null,"
        "  };"
        "})()"
    )
    assert row["mine"] is False, "a gap showed candidate evidence"


def test_a_verdict_is_recorded_and_changes_no_readiness(page: Chrome, pristine_server: str) -> None:
    open_prepare(page, pristine_server)
    before = page.evaluate(
        "document.querySelector('#drawer-panel-prepare .prep__row .prep__state').textContent"
    )
    page.evaluate(
        "(() => {"
        "  const row = document.querySelector('#drawer-panel-prepare .prep__row');"
        "  row.querySelector('details').open = true;"
        "  [...row.querySelectorAll('.prep__verdicts button')]"
        "    .find((b) => b.textContent.includes('does not support')).click();"
        "})()"
    )
    page.wait_for(
        "document.querySelector('#drawer-panel-prepare .btn--verdict.is-chosen') !== null",
        message="the recorded verdict",
    )
    after = page.evaluate(
        "document.querySelector('#drawer-panel-prepare .prep__row .prep__state').textContent"
    )
    assert after == before, "a verdict rewrote the readiness it disagreed with"


def test_confirmed_evidence_answers_a_requirement_and_shows_itself(
    page: Chrome, pristine_server: str
) -> None:
    """The end-to-end claim of this whole surface: a line she confirmed off her
    CV appears, as her own words, beside the employer's sentence."""
    open_evidence(page, pristine_server)
    import_cv(page)
    answer(page, 0, "Yes, that is true")  # the HubSpot workflow line
    page.wait_for(
        "document.querySelector('#page-evidence .ev__decision') !== null",
        message="the decision",
    )
    open_prepare(page, pristine_server)
    matched = page.evaluate(
        "[...document.querySelectorAll('#drawer-panel-prepare .prep__group--matched"
        " .prep__row, #drawer-panel-prepare .prep__group--partial .prep__row')]"
        ".map((row) => ({"
        "  mine: row.querySelector('.quote--mine')"
        "    ? row.querySelector('.quote--mine').textContent : null,"
        "  matchedOn: row.querySelector('.prep__matched')"
        "    ? row.querySelector('.prep__matched').textContent : null,"
        "}))"
    )
    rows = list(matched)
    assert rows, "a confirmed CV line answered nothing in the whole corpus"
    for row in rows:
        assert row["mine"], "a match was asserted with no evidence shown"
        assert row["matchedOn"], "a match was asserted with no phrase to check it against"


# =========================================================================
# 6. TODAY
# =========================================================================


def _committed_config():
    """The same configuration the server under test is running."""
    from tests.support import committed_config_dir

    from career_agent.config.search_config import load_search_config

    loaded, _ = load_search_config(committed_config_dir())
    return loaded


def open_daily(page: Chrome, server: str) -> None:
    page.navigate(server)
    page.wait_for("document.getElementById('daily-open') !== null", message="the Today button")
    page.evaluate("document.getElementById('daily-open').click()")
    page.wait_for(
        "document.querySelector('#daily-host .daily__sec') !== null",
        message="the digest",
    )


def test_the_digest_opens_with_every_section_even_the_empty_ones(
    page: Chrome, pristine_server: str
) -> None:
    """An empty section keeps its heading. One that vanished would be
    indistinguishable from one that failed to look."""
    open_daily(page, pristine_server)
    sections = page.evaluate(
        "[...document.querySelectorAll('#daily-host .daily__sec')].map((s) => ({"
        "  head: s.querySelector('.d-sec__head').textContent,"
        "  lede: s.querySelector('.d-sec__lede').textContent,"
        "  rows: s.querySelectorAll('.daily__row').length,"
        "  empty: s.querySelector('.daily__empty') !== null,"
        "}))"
    )
    rows = list(sections)
    # Five since migration 0020 added the employment-context section. Asserted
    # against the shared definition rather than a literal, so adding a section
    # is one edit rather than two.
    from career_agent.digest import sections as defined

    assert len(rows) == len(defined(_committed_config()))
    for section in rows:
        assert str(section["head"]).strip()
        assert str(section["lede"]).strip()
        assert bool(section["rows"]) != bool(section["empty"])


def test_a_digest_row_says_which_kind_of_date_it_carries(
    page: Chrome, pristine_server: str
) -> None:
    """`posted` and `first seen` are different facts. Neither is ever rendered
    as the other, and a row with no date says that rather than guessing."""
    open_daily(page, pristine_server)
    whens = page.evaluate(
        "[...document.querySelectorAll('#daily-host .daily__when')].map(n => n.textContent)"
    )
    values = [str(item) for item in whens]
    assert values, "the digest drew no rows at all"
    for when in values:
        assert when.startswith(("posted ", "first seen ")) or when == "no date", when


def test_opening_a_row_opens_that_job(page: Chrome, pristine_server: str) -> None:
    open_daily(page, pristine_server)
    title = str(page.evaluate("document.querySelector('#daily-host .daily__title').textContent"))
    page.evaluate("document.querySelector('#daily-host .daily__open').click()")
    # Waits for the drawer to have PAINTED, not merely to be visible. It shows
    # "Loading" for one request while the job is fetched, and asserting on the
    # title in that window compares against a placeholder.
    page.wait_for(
        "document.querySelector('#drawer-host .drawer') !== null"
        " && !document.querySelector('#drawer-host .drawer').hidden"
        " && document.querySelectorAll('#drawer-panel-details .d-sec').length > 0",
        message="the drawer for the chosen job",
    )
    opened = str(page.evaluate("document.getElementById('drawer-title').textContent"))
    assert opened == title


def test_reading_the_digest_does_not_mark_it_read(page: Chrome, pristine_server: str) -> None:
    open_daily(page, pristine_server)
    before = str(page.evaluate("document.querySelector('#daily-host .ev__note').textContent"))
    assert "never marked this read" in before

    page.evaluate(
        "[...document.querySelectorAll('#daily-host button')]"
        ".find((b) => b.textContent.includes('I have read this')).click()"
    )
    page.wait_for(
        "document.querySelector('#daily-host .ev__note').textContent"
        ".includes('You last marked this read')",
        message="the recorded checkpoint",
    )
    heads = page.evaluate(
        "[...document.querySelectorAll('#daily-host .d-sec__head')].map(n => n.textContent)"
    )
    assert any("Since you last looked" in str(head) for head in heads)

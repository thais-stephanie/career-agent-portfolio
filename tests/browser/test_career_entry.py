"""Three candidates the rest of this product was not written for.

WHY THIS FILE EXISTS
--------------------
Every fixture in this suite, and every posting in the demo corpus until
migration 0027, described somebody continuing in the work they already do. For
three other people the product was quietly useless:

  A. A CAREER CHANGER. Their search describes where they have BEEN, so
     `screening.required_any_groups` sets aside every posting in the field they
     are moving TO. Nothing is broken; the answer is just always no.

  B. A NEW GRADUATE, with an education and no work history. Every requirement
     on every posting reads as a gap, including on the postings that said they
     would train somebody.

  C. SOMEBODY SEARCHING SPECIFICALLY for work that needs no experience. There
     was no way to ask.

None of that is fixed by hiding jobs or by changing what is collected. It is
fixed by READING what the employer said about experience, and by giving a
person the controls to ask about it.

WHAT IS ASSERTED, AND WHAT IS REFUSED
--------------------------------------
That the controls exist, narrow, and say true things. And -- the assertion that
matters most -- that a posting which never mentioned experience is NOT offered
as one that is open to a beginner. Silence is not an invitation, and a filter
built on the opposite reading would put thousands of senior roles in front of
somebody looking for their first job.
"""

from __future__ import annotations

import json

from tests.browser.chrome import Chrome

#: The three widenings, so these tests ask about the CORPUS rather than about
#: what the default view shows. Two of the postings this file is about are set
#: aside by default -- one by the search, one by nothing at all -- and a test
#: that forgot this would be measuring the narrowings instead.
WIDEN = "include_ineligible=1&include_off_target=1&include_unresolved=1"


def _open_jobs(page: Chrome, server: str, query: str = "", *, widen: bool = True) -> None:
    """Open the job list at `query`.

    `widen=False` is for the one test that is ABOUT a narrowing. Passing
    `include_off_target=0` while `WIDEN` already said `=1` does not override it
    -- a second value for the same key is simply a second value -- so a test
    that needed the default narrowing in force had to be able to drop the
    prefix rather than argue with it.
    """
    parts = [p for p in ((WIDEN if widen else ""), query) if p]
    page.navigate(f"{server}/?{'&'.join(parts)}")
    page.evaluate(
        "[...document.querySelectorAll('.topnav__link')]"
        ".find(n => n.dataset.page === 'jobs').click()"
    )
    # WAIT FOR THE HEADER TO STOP SAYING IT IS LOADING, not just for a card to
    # exist. The previous response is still on screen while the next one is
    # being fetched, so a list read at that moment is the answer to the query
    # BEFORE the one under test -- the same trap `test_keyword_filters` fell
    # into, and it only became visible when the machine was busy.
    page.wait_for(
        "!document.getElementById('resultcount').textContent.includes('Loading')"
        " && (document.querySelectorAll('#list .card').length > 0"
        " || Boolean(document.querySelector('.state__msg')))",
        message="the job list finishes loading",
    )


def _titles(page: Chrome) -> list[str]:
    """`textContent`, never `innerText`.

    `innerText` is layout-dependent and returns an empty string for a node the
    browser has not laid out yet, which is how this helper reported six cards
    with no titles on them while the machine was under load.
    """
    return list(
        page.evaluate(
            "[...document.querySelectorAll('#list .card .card__title')]"
            ".map(n => n.textContent.trim())"
        )
        or []
    )


# =========================================================================
# 1. THE CONTROLS EXIST, AND THEY ARE FINDABLE
# =========================================================================


def test_the_getting_in_section_is_its_own_section(page: Chrome, server: str) -> None:
    """Not a row inside "Role and level".

    The three questions here belong to people the rest of the panel was not
    written for, and burying "no previous experience required" under Seniority
    makes it findable only by somebody who already knows it is there.
    """
    _open_jobs(page, server)
    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim().startsWith('Show filters'))?.click()"
    )
    page.wait_for("document.querySelector('.filters details')", message="the rail opens")
    headings = page.evaluate(
        "[...document.querySelectorAll('.filters > details > summary')]"
        ".map(s => s.textContent.replace(/\\d+$/, '').trim())"
    )
    assert "Getting in" in headings, headings


def test_the_experience_control_says_what_it_excludes(page: Chrome, server: str) -> None:
    """The sentence that keeps the control honest.

    Without it "Up to 1 year" reads as "jobs a beginner can do". It is not: it
    is jobs that SAID they want at most a year, which is a much smaller and
    much more useful list than every posting that never raised the subject.
    """
    _open_jobs(page, server)
    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim().startsWith('Show filters'))?.click()"
    )
    page.wait_for("document.querySelector('#f-experience-years')")
    help_text = str(
        page.evaluate(
            "document.querySelector('#f-experience-years')"
            ".closest('.filters__block').querySelector('.toggle__help').textContent"
        )
    )
    assert "SAID" in help_text, help_text
    assert "never mention" in help_text.lower(), help_text


# =========================================================================
# 2. C -- SOMEBODY ASKING FOR WORK THAT NEEDS NO EXPERIENCE
# =========================================================================


def test_asking_for_no_experience_returns_only_what_said_so(page: Chrome, server: str) -> None:
    """The corpus holds one posting that says none is needed, and it is the one
    that comes back. Every other posting either states a minimum or never
    raised the subject, and neither of those is an invitation."""
    _open_jobs(page, server, "experience_max_years=0")
    titles = _titles(page)
    assert titles == ["Junior Business Systems Analyst"], titles


def test_silence_is_not_offered_as_openness(page: Chrome, server: str) -> None:
    """The assertion this whole file is really about.

    Fourteen of the twenty-one demo postings never mention experience. If the
    filter counted those as open, this list would be fifteen long and would be
    telling somebody looking for their first job that fourteen senior roles had
    invited them.
    """
    _open_jobs(page, server, "experience_max_years=2")
    assert len(_titles(page)) == 1


# =========================================================================
# 3. B -- A NEW GRADUATE
# =========================================================================


def test_the_entry_signals_are_askable_one_at_a_time(page: Chrome, server: str) -> None:
    """ "Recent graduates welcome" and "training provided" are two different
    promises, and a person weighs them differently. They are separate values,
    separately askable, and the reading never merges them."""
    for signal in ("RECENT_GRADUATE", "TRAINING_PROVIDED", "ENTRY_LEVEL"):
        _open_jobs(page, server, f"entry_signal={signal}")
        assert _titles(page) == ["Junior Business Systems Analyst"], signal


# =========================================================================
# 4. A -- A CAREER CHANGER
# =========================================================================


def test_the_transition_control_is_off_by_default_and_says_why(page: Chrome, server: str) -> None:
    """It reads what the person has CONFIRMED, so it is opt-in, and its help
    text says plainly that it does nothing until something is confirmed.

    An always-on version would make the recommended list depend on the evidence
    review, which is the one thing the evidence screen promises it does not do.
    """
    _open_jobs(page, server)
    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim().startsWith('Show filters'))?.click()"
    )
    page.wait_for("document.querySelector('#f-include_transferable')")
    assert page.evaluate("document.querySelector('#f-include_transferable').checked") is False
    help_text = str(
        page.evaluate(
            "document.querySelector('#f-include_transferable')"
            ".closest('.toggles').querySelectorAll('.toggle__help')[0].textContent"
        )
    )
    assert "confirmed" in help_text.lower(), help_text


# =========================================================================
# 4b. A CAREER CHANGER, END TO END
# =========================================================================
#
# The scenario the transition control exists for, walked rather than asserted
# in pieces: somebody whose search describes systems work confirms one thing
# they have actually done, turns the control on, and an ADMINISTRATIVE posting
# comes back that their own search had set aside.
#
# It uses `writable_server`, which serves a COPY of the configuration, because
# confirming a claim writes to the database this suite shares and the reveal
# has to be measured against a list nobody else has moved.


def _confirm(page: Chrome, server: str, text: str) -> None:
    """Write one confirmed claim, the way the evidence screen does.

    Through the API rather than by driving the ledger form: what is under test
    is what a CONFIRMED FACT does to the job list, and re-driving the evidence
    editor here would be testing that screen a third time.
    """
    result = page.evaluate(
        "fetch('/api/evidence', {"
        "  method: 'POST',"
        "  headers: { 'Content-Type': 'application/json' },"
        f"  body: JSON.stringify({{ claim_type: 'SKILL', text: {json.dumps(text)} }}),"
        "}).then((r) => r.status)"
    )
    assert int(result) in (200, 201), result


def test_a_career_changer_gets_back_the_work_their_search_set_aside(
    page: Chrome, writable_server: str
) -> None:
    """The whole feature, in one journey.

    demo-021 is an Administrative Assistant role, open worldwide. Nothing an
    employer wrote rules this candidate out -- the geography gate PASSES. What
    sets it aside is a systems search whose required signals do not fire on
    administration, which is exactly the position somebody changing profession
    is in: their search describes where they have BEEN.

    One confirmed line about documentation is enough to bring it back, because
    `documentation_practice` is a signal that DID fire on that posting. The
    control reads what has been confirmed, and confirms nothing itself.
    """

    # `widen=False`, because `include_off_target` is the narrowing under test
    # and the shared prefix turns it off. ONE narrowing left in force -- the one
    # that sets this posting aside -- and the other two widened, so nothing else
    # moves between the four readings below.
    narrow = "include_ineligible=1&include_unresolved=1&group_duplicates=0"

    def open_with(query: str) -> None:
        _open_jobs(page, writable_server, query, widen=False)

    # 1. IT IS NOT THERE. The search sets it aside, correctly.
    open_with(narrow)
    before = _titles(page)
    assert "Administrative Assistant" not in before, before

    # 2. AND THE CONTROL ALONE DOES NOTHING, because nothing is confirmed yet.
    #    This is the assertion that stops it from being a second "show
    #    everything" switch wearing an honest label.
    open_with(f"{narrow}&include_transferable=1")
    assert "Administrative Assistant" not in _titles(page)

    # 3. ONE CONFIRMED FACT.
    _confirm(page, writable_server, "Wrote the documentation for our case files")

    # 4. NOW IT COMES BACK, and only with the control on.
    open_with(f"{narrow}&include_transferable=1")
    assert "Administrative Assistant" in _titles(page), _titles(page)

    # 5. AND THE DEFAULT LIST HAS NOT MOVED. The promise the evidence screen
    #    makes -- confirming a fact moves no recommendation -- is kept because
    #    the default never carries this control.
    open_with(narrow)
    assert _titles(page) == before, "confirming a fact changed the default list"


# =========================================================================
# 5. THE EXPLANATION
# =========================================================================


def _open_why(page: Chrome) -> str:
    """Open the first card and switch to the reasoning tab.

    The tab is found by its ID rather than by its words: `drawer-tab-why` is
    stable and the label is not, and a test that hunted for the English text
    would go blind the moment somebody read the product in Portuguese -- the
    one language it must never be tested in less carefully.

    And the wait is on the PANEL BEING PAINTED rather than on the tab being
    pressed. `test_browser_acceptance` records why: `.drawer__tabpanel
    { display: flex }` is an author rule that beats `[hidden] { display: none }`,
    so both panels were once painted at once and pressing the tab moved an
    underline and nothing else, for a week.
    """
    page.evaluate("document.querySelector('#list .card').click()")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.drawer__tab').length === 3",
        message="the drawer opens with its three tabs",
    )
    page.evaluate("document.getElementById('drawer-tab-why').click()")
    page.wait_for(
        "getComputedStyle(document.getElementById('drawer-panel-why')).display !== 'none'",
        message="the reasoning tab is painted",
    )
    return str(page.evaluate("document.getElementById('drawer-panel-why').innerText"))


def test_a_beginner_friendly_posting_says_so_in_a_sentence(page: Chrome, server: str) -> None:
    """ "This role does not require previous professional experience" is the
    sentence somebody needs, and it is followed by the employer's own line.

    A chip reading `NONE_REQUIRED` would be this product's vocabulary on
    screen, which `test_plain_language.py` exists to fail.
    """
    _open_jobs(page, server, "experience_max_years=0")
    text = _open_why(page)
    assert "does not require previous professional experience" in text
    assert "no previous experience is required" in text.lower()


def test_a_posting_that_asks_for_years_says_how_many(page: Chrome, server: str) -> None:
    """And it does NOT soften it. Showing only the transferable half would
    flatter somebody on their way into an interview."""
    _open_jobs(page, server, "keyword=Administrative")
    text = _open_why(page)
    assert "at least 3 years of experience" in text
    assert "A minimum of 3 years of experience" in text


def test_a_posting_that_never_mentioned_it_is_not_reported_as_open(
    page: Chrome, server: str
) -> None:
    """The drawer's version of the filter's rule.

    A posting that said nothing gets a sentence saying that is not the same as
    saying none is needed -- or gets no section at all. What it must never get
    is the sentence a beginner-friendly posting gets.
    """
    _open_jobs(page, server, "experience_requirement=NOT_STATED")
    text = _open_why(page)
    assert "does not require previous professional experience" not in text


# =========================================================================
# 6. IT TRANSLATES
# =========================================================================


def test_the_getting_in_controls_move_language(page: Chrome, server: str) -> None:
    """Every sentence in this section is product-authored copy, and the one
    failure mode the localisation gate cannot see is a control drawn once
    before the locale was read."""
    _open_jobs(page, server)
    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim().startsWith('Show filters'))?.click()"
    )
    page.wait_for("document.querySelector('#f-experience-years')")
    english = str(
        page.evaluate("document.querySelector('label[for=\"f-experience-years\"]').textContent")
    )
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('label[for=\"f-experience-years\"]').textContent !== "
        + json.dumps(english),
        message="the control's label moves language",
    )
    section = str(
        page.evaluate(
            "[...document.querySelectorAll('.filters > details')]"
            ".find(d => d.querySelector('#f-experience-years')).innerText"
        )
    )
    assert "Experience the posting asks for" not in section, section
    page.evaluate("localStorage.removeItem('careerAgent.locale.v1')")

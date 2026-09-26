"""What a person used to check by hand, checked by a browser instead.

Every workflow below was verified once, manually, on the day the interface was
built. That is worth exactly as much as the memory of the person who did it.
These tests drive a real headless Chrome against a real server over a real
socket, so the answer survives the next change to `state.js`.

Three things they are careful about:

  * They wait on CONDITIONS, never on the clock. A sleep long enough on this
    machine is a flake on the next one.
  * They assert on the DOM the reader sees, not on the JSON the server sent.
    `tests/integration/test_web_api.py` already owns the payload; the question
    here is whether the page rendered it.
  * They never trigger enrichment. `POST /api/jobs/*/enrich` runs a real local
    model for minutes; the local-model assertions read `/api/health` and the
    header wording instead, which is exactly the state a page load produces.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import (
    DEMO_GROUPED_COUNT,
    DEMO_HIDDEN_COUNT,
    DEMO_POSTING_COUNT,
    DEMO_UNRESOLVED_COUNT,
    DEMO_VISIBLE_COUNT,
    DEMO_VISIBLE_GROUPED_COUNT,
    DESKTOP,
    LAPTOP,
    MOBILE,
)

# -- JS expressions the tests share ---------------------------------------

#: Every rendered posting, in document order. `[data-job-id]` is on the card in
#: Cards and on the row in Table, which is what makes the two views comparable.
RENDERED_IDS = "Array.from(document.querySelectorAll('[data-job-id]')).map((n) => n.dataset.jobId)"
RENDERED_COUNT = "document.querySelectorAll('[data-job-id]').length"

#: A DRAWN card, not a placeholder. `cardsSkeleton` renders `.card
#: .card--skeleton`, so a bare `.card` wait is satisfied by the loading state
#: and the click that follows it finds nothing. That was a latent race the whole
#: time; it became a real one when switching to Cards started issuing a request
#: (Cards groups duplicates, Table does not) instead of repainting from cache.
A_REAL_CARD = "document.querySelector('.card:not(.card--skeleton)')"

#: Roles at or above a match of 55, as a person sees them. Numbers rather than
#: proportions: if the corpus or the matcher moves, this must fail and be
#: looked at rather than quietly re-derived.
#:
#: It moved, and this is the second time. It was nine, then eight when the
#: eligibility default began hiding a posting that rules this candidate out.
#: It is FIVE now because the title stopped buying compatibility points: seven
#: postings clear 55, and three of those seven are one role.
FILTERED_COUNT = 5
#: The same population in Table, which does not group.
FILTERED_UNGROUPED_COUNT = 7

#: The role the corpus publishes three times, and the badge Cards draws for it.
GROUPED_TITLE = "Business Systems Engineer"
GROUPED_COMPANY = "Northwind Systems"
GROUPED_POSTINGS = 3

#: A phrase that appears in three descriptions and in no title.
DESCRIPTION_ONLY_TERM = "lead routing"

BLOCKED_TITLE = "Senior Revenue Operations Systems Analyst"
EXPLAINED_TITLE = "Business Systems Analyst"
STATUS_TITLE = "Integration Specialist"
APPLIED_TITLE = "HubSpot Consultant"

#: Two more postings that no other test touches. The server fixture is session
#: scoped and these tests MUTATE application status, so a title shared between
#: two of them makes their order matter -- which is how a suite starts passing
#: only when it is run whole.
# A posting the DEFAULT view shows. It was `Salesforce Administrator`, which
# the off-target narrowing now hides: the lifecycle tests below drive the
# status control on a row, and a row that is not rendered has no control.
SYNC_TITLE = "Marketing Technology Specialist"
CLEARED_TITLE = "Marketing Technology Specialist"

#: `Log.entryAdded` and `Runtime.*` sources that mean OUR code threw or shouted.
PAGE_SCRIPT_SOURCES = frozenset({"console", "exception", "javascript"})


#: Where a card or row carries its title. A card's title is a HEADING now,
#: not a button: the whole card is the control, so a nested button would be a
#: second tab stop for one action. The table still uses a button per row.
TITLE_SELECTOR = ".card__title, .cell__title-btn"


def choose_theme(page: Chrome, choice: str) -> None:
    """Press a button in the theme control and wait for the attribute to land.

    Through the CONTROL rather than by emulating `prefers-color-scheme`, which
    is the difference V3 introduced: the media query proves the stylesheet has
    a dark palette, and only the control proves a person can choose it. The
    resolved value is written to `data-theme` on the root element, so that is
    what this waits on.
    """
    # WAIT FOR THE CONTROL, THEN PRESS IT. Two separate things, and the order
    # is the point -- the same order `test_the_theme_choice_is_remembered`
    # already documents. `data-theme` is written SYNCHRONOUSLY in the head,
    # before the body exists; the buttons are built on DOMContentLoaded, much
    # later. After a reload the attribute is therefore already correct while
    # the control is still missing, and clicking it threw "Cannot read
    # properties of null".
    page.wait_for(
        f"Boolean(document.querySelector('.themeswitch__btn[data-theme=\"{choice}\"]'))",
        message=f"the {choice} theme button to be built",
    )
    click(page, f"document.querySelector('.themeswitch__btn[data-theme=\"{choice}\"]')")
    page.wait_for(
        f"document.documentElement.getAttribute('data-theme') === '{choice}'",
        message=f"the {choice} theme to be applied",
    )


def reset_theme(page: Chrome) -> None:
    """Put the control back to NOBODY HAS CHOSEN, and mean it.

    This used to be a press on a third segment, because a third segment
    existed whose whole content was "I am not deciding". The segment is gone --
    the design defines light and dark only, and in Portuguese its label did not
    fit the 220px rail -- but the STATE it named is still there, and it is
    still what every test has to be left in.

    It has to be left in it because Chrome is session scoped and the `page`
    fixture resets the viewport, the media emulation and the console but NOT
    `localStorage`. A test that photographs the dark theme and stops there
    hands the next test a dark page and a mysterious failure. That is the leak
    these calls have always been closing.

    The resolve rule is repeated here rather than reached for in `theme.js`,
    which is a duplication and is deliberate: the alternative is a hook that
    exists in shipped code for no reason but this, and a test may know a
    contract -- the storage key and `data-theme` are already all over this
    file -- without the product growing an API to be tested through.
    """
    page.wait_for(
        "document.querySelectorAll('.themeswitch__btn').length > 0",
        message="the theme control to be built before it is reset",
    )
    # WRAPPED IN A FUNCTION, and it has to be. `Runtime.evaluate` runs in the
    # page's global scope, so a top-level `const root` survives the call and
    # the SECOND reset in a session died with "Identifier 'root' has already
    # been declared".
    page.evaluate(
        "(() => {"
        " window.localStorage.removeItem('careerAgent.theme.v1');"
        " const root = document.documentElement;"
        " root.removeAttribute('data-theme-choice');"
        " const dark = window.matchMedia('(prefers-color-scheme: dark)').matches;"
        " root.setAttribute('data-theme', dark ? 'dark' : 'light');"
        " for (const b of document.querySelectorAll('.themeswitch__btn')) {"
        "   b.setAttribute('aria-pressed',"
        "     String(b.dataset.theme === root.getAttribute('data-theme')));"
        " }"
        " return true; })()"
    )


def open_settings(page: Chrome) -> None:
    """Go to Settings and Sources, the way a person does.

    The three panels behind it -- preferences, the source matrix and the
    retrieval funnel -- were disclosures inside the Jobs filter rail until the
    Workspace V2 shell. Where the postings come from is not a filter, and
    reaching it meant opening a `<details>` inside a panel that only exists on
    one page. They load on ARRIVAL now, for the same reason they loaded on
    open: a screen of jobs should not wait on a catalogue read nobody asked
    for.
    """
    page.evaluate(
        "(() => { const link ="
        " document.querySelector('.topnav__link[data-page=\"settings\"]');"
        " if (!link) throw new Error('there is no Settings destination');"
        " link.click(); return true; })()"
    )
    page.wait_for(
        "document.getElementById('page-settings')"
        " && !document.getElementById('page-settings').hidden",
        message="the Settings page",
    )


def node_for(title: str) -> str:
    """A JS expression for the card or row whose title reads `title`."""
    return (
        "Array.from(document.querySelectorAll('[data-job-id]')).find((n) => {"
        f" const b = n.querySelector('{TITLE_SELECTOR}');"
        f" return b && b.textContent.trim() === {json.dumps(title)};}})"
    )


def click(page: Chrome, expression: str) -> None:
    page.evaluate(f"(() => {{ {expression}.click(); return true; }})()")


def set_value(page: Chrome, expression: str, value: str, *events: str) -> None:
    """Set a form control's value and fire the events a person would."""
    fired = "".join(
        f" node.dispatchEvent(new Event({json.dumps(name)}, {{ bubbles: true }}));"
        for name in events
    )
    page.evaluate(
        f"(() => {{ const node = {expression}; node.value = {json.dumps(value)};{fired}"
        " return true; })()"
    )


def wait_for_count(page: Chrome, expected: int, what: str) -> None:
    page.wait_for(
        f"{RENDERED_COUNT} === {expected}",
        message=f"{expected} rendered postings {what}",
    )


#: Deliberately NOT pinned. English is the default for a reader who has never
#: chosen, and nothing is inferred from the machine -- so every English
#: assertion below is deterministic by construction rather than by a fixture
#: that sets a key. There was such a fixture here for one afternoon, while the
#: interface still read `navigator.language`; removing the inference removed
#: the need for it, and keeping it would have hidden the next regression.
LOCALE_KEY = "careerAgent.locale.v1"


#: Every default narrowing, undone. Named once because there are THREE of them
#: now and a test that lists two is a test quietly measuring a third of the
#: corpus less than it thinks.
SHOW_EVERYTHING = "?include_ineligible=1&include_off_target=1&include_unresolved=1"


def open_list(page: Chrome, server: str, query: str = "") -> None:
    """Open the product ON THE JOB LIST.

    The landing page is HOME now, and the job list is one of five
    destinations. Asking for it explicitly is what makes every assertion below
    about a VISIBLE list: a hidden page still renders its cards, so the old
    form kept passing its DOM assertions while every measurement of width,
    overflow or position read zero.
    """
    page.navigate(f"{server}/{query}")
    page.wait_for(
        f"{RENDERED_COUNT} > 0 || document.querySelector('.state__head')",
        message="the first paint of the list",
    )
    # Pressed rather than asked for in the URL. `#jobs` names the page and a
    # bookmark uses it, but a hash-only difference from the previous URL is a
    # SAME-DOCUMENT navigation: the page would not reload, and a locale stored
    # a moment earlier would never be read.
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for(
        "document.getElementById('page-jobs').hidden === false",
        message="the job list to be the page on screen",
    )


# =========================================================================
# 0. the harness itself
# =========================================================================


def test_the_fixtures_are_wired(server: str) -> None:
    """The server fixture serves the seeded demo corpus.

    Deliberately free of the Chrome fixture. Without it, a machine with no
    browser would collect thirteen skips and no evidence that any of the
    scaffolding around them works -- a suite that is vacuous rather than
    skipped.
    """
    with urllib.request.urlopen(f"{server}/api/health", timeout=10) as response:
        payload = json.loads(response.read().decode("utf-8"))
    assert payload["ok"] is True
    assert payload["job_count"] == DEMO_POSTING_COUNT
    assert payload["scored_count"] == DEMO_POSTING_COUNT


# =========================================================================
# 1-13. the workflows
# =========================================================================


def test_the_job_list_loads_and_renders_the_whole_corpus(page: Chrome, server: str) -> None:
    """1. The page boots, asks the API once, and draws the whole corpus.

    "The whole corpus" is seventeen CARDS over nineteen postings, because
    Cards is the reading view and collapses the role the corpus publishes
    three times. Nothing is dropped -- the next test opens Table and finds all
    nineteen -- so the number here is a statement about the view, not a loss.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "on first load")
    titles = page.evaluate(
        "Array.from(document.querySelectorAll('.card__title')).map((n) => n.textContent.trim())"
    )
    assert len(titles) == DEMO_VISIBLE_GROUPED_COUNT
    assert all(title for title in titles), "a card rendered without a title"


def test_switching_views_renders_the_same_jobs_and_keeps_the_filter(
    page: Chrome, server: str
) -> None:
    """2. Cards -> Table -> Cards is identity, under a filter.

    This is the whole argument of `state.js` made falsifiable: `view` is not a
    key `apiQuery` can see, so the two views cannot ask different questions.
    Set equality would pass on a view that dropped and re-added rows in a
    different order, so the assertion is on the ORDERED list.

    The one thing view DOES write into the store is `group_duplicates` --
    Cards is the reading view and groups, Table is the "show me every row"
    view and does not. That is a visible store write, not a hidden derivation:
    it lands in the URL and in a pressed button. So the two views legitimately
    render different ROW COUNTS over the duplicate trio, and the honest
    statement of the relationship is containment plus an exact difference:
    Table shows every posting Cards shows, plus the siblings Cards collapsed.
    Identity is asserted where it is actually claimed -- Cards before and
    after the round trip.
    """
    open_list(page, server, "?min_score=55")
    wait_for_count(page, FILTERED_COUNT, "under ?min_score=55")
    as_cards = page.evaluate(RENDERED_IDS)

    click(page, "document.getElementById('view-table')")
    page.wait_for(
        f"document.querySelector('table.jobs') && {RENDERED_COUNT} === {FILTERED_UNGROUPED_COUNT}",
        message="the table to render the filtered rows, ungrouped",
    )
    as_table = page.evaluate(RENDERED_IDS)

    click(page, "document.getElementById('view-cards')")
    page.wait_for(
        f"{A_REAL_CARD} && {RENDERED_COUNT} === {FILTERED_COUNT}",
        message="the cards to come back",
    )
    as_cards_again = page.evaluate(RENDERED_IDS)

    assert as_cards == as_cards_again, "the round trip changed which cards are shown"
    assert set(as_cards) <= set(as_table), "Cards showed a posting the Table does not have"
    assert len(as_table) - len(as_cards) == GROUPED_POSTINGS - 1, (
        "the only difference between the views must be the collapsed siblings"
    )
    assert len(set(as_cards)) == FILTERED_COUNT, "a job id was rendered twice"
    assert len(set(as_table)) == FILTERED_UNGROUPED_COUNT, "a job id was rendered twice"

    # The filter survived both switches, in the URL, in the control and in the chip.
    assert "min_score=55" in str(page.evaluate("location.search"))
    assert page.evaluate("document.getElementById('f-min-score').value") == "55"
    assert "55" in str(page.evaluate("document.getElementById('chipbar').textContent"))

    # And grouping came back on with Cards, showing on the button rather than
    # only in the request. A default nobody can see is a behaviour, not a
    # default.
    assert (
        page.evaluate("document.getElementById('group-duplicates').getAttribute('aria-pressed')")
        == "true"
    )


def test_the_announced_result_count_matches_what_was_drawn(page: Chrome, server: str) -> None:
    """3. The live region tells a screen reader the same number the eye sees."""
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before reading the live region")

    assert page.evaluate("document.getElementById('resultcount').getAttribute('role')") == "status"
    assert (
        page.evaluate("document.getElementById('resultcount').getAttribute('aria-live')")
        == "polite"
    )

    for query, expected in (("", DEMO_VISIBLE_GROUPED_COUNT), ("?min_score=55", FILTERED_COUNT)):
        open_list(page, server, query)
        wait_for_count(page, expected, f"under {query or 'no filter'}")
        announced = str(page.evaluate("document.getElementById('resultcount').textContent"))
        assert announced.split()[0] == str(expected), announced


def test_a_filter_narrows_the_list_and_its_chip_undoes_it(page: Chrome, server: str) -> None:
    """4. A filter is reversible from the chip that reports it."""
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before filtering")

    set_value(page, "document.getElementById('f-min-score')", "55", "input", "change")
    wait_for_count(page, FILTERED_COUNT, "after raising the minimum fit to 55")

    assert page.evaluate("document.getElementById('chipbar').hidden") is False
    chip = (
        "Array.from(document.querySelectorAll('#chipbar .chip--active'))"
        ".find((n) => n.textContent.includes('55'))"
    )
    assert page.evaluate(f"Boolean({chip})"), "no chip reported the fit filter"
    assert page.evaluate(f"Boolean({chip}.querySelector('.chip__x'))"), "the chip cannot be removed"

    click(page, f"{chip}.querySelector('.chip__x')")
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "after removing the chip")
    assert page.evaluate("document.getElementById('chipbar').hidden") is True


def test_a_term_only_in_a_description_finds_jobs_whose_titles_do_not_carry_it(
    page: Chrome, server: str
) -> None:
    """5. Search reads the posting, not the headline.

    "Search for the work, not the title" is a product invariant, and this is
    the shape it takes in the interface: a phrase buried in the body of three
    postings must surface all three, none of which is named after it.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before searching")

    set_value(page, "document.getElementById('f-search')", DESCRIPTION_ONLY_TERM, "input")
    page.wait_for(
        f"{RENDERED_COUNT} > 0 && {RENDERED_COUNT} < {DEMO_VISIBLE_GROUPED_COUNT}",
        message=f"the debounced search for {DESCRIPTION_ONLY_TERM!r} to narrow the list",
    )

    titles = [
        str(title)
        for title in page.evaluate(
            "Array.from(document.querySelectorAll('.card__title')).map((n) => n.textContent.trim())"
        )
    ]
    assert titles, "the search returned nothing"
    without = [t for t in titles if DESCRIPTION_ONLY_TERM not in t.lower()]
    assert without, f"every hit named the term in its title: {titles}"


def test_the_drawer_explains_the_number_with_quotes_gates_and_gaps(
    page: Chrome, server: str
) -> None:
    """6. Opening a posting shows why: a quote, a gate, and a missing item.

    The third one is the promise that is easy to drop. "We could not read
    this" is information, and a breakdown that only lists what was awarded is
    a breakdown that flatters itself.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before opening the drawer")

    click(page, f"{node_for(EXPLAINED_TITLE)}")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.drawer__tab').length === 3",
        message=f"the drawer for {EXPLAINED_TITLE!r} to render its three tabs",
    )

    # The reasoning now lives in its own tab, and the drawer opens on the job.
    # That ordering is the V3 change this assertion follows: the arithmetic was
    # the first thing a person met, and it is the worst possible first screen.
    # PAINTED, not merely `hidden === false`.
    #
    # The first version of this asserted the PROPERTY, and it passed for a week
    # while the feature was broken: `.drawer__tabpanel { display: flex }` is an
    # author rule and beats the browser's own `[hidden] { display: none }`, so
    # both panels were painted at once, stacked, and pressing this tab moved an
    # underline and nothing else. An independent UX review found it by looking
    # at the screenshots and noticing the two frames were identical.
    click(page, "document.getElementById('drawer-tab-why')")
    page.wait_for(
        "getComputedStyle(document.getElementById('drawer-panel-why')).display !== 'none'"
        " && getComputedStyle(document.getElementById('drawer-panel-details')).display === 'none'"
        " && document.querySelectorAll('.component').length > 0",
        message="the reasoning tab to be the one on screen",
    )

    # The plain-language answer comes FIRST, before any number is broken down.
    summary = str(page.evaluate("document.querySelector('.why__summary').textContent"))
    assert "out of 100" in summary and "Search Fit" in summary, summary
    assert "Evidence / Readiness" in summary, summary
    assert int(page.evaluate("document.querySelectorAll('.reason .quote').length")) >= 1, (
        "no reason carried a quote from the posting"
    )

    quoted = page.evaluate("document.querySelectorAll('.contrib blockquote.quote').length")
    assert int(quoted) >= 1, "no scoring contribution carried a quote"
    assert int(page.evaluate("document.querySelectorAll('.gates .gate').length")) >= 1
    # Inside `Advanced scoring details`, which is where the item-by-item
    # numbers belong. Still in the DOM, still assertable: folded is not deleted.
    assert int(page.evaluate("document.querySelectorAll('.conf__item--missing').length")) >= 1

    click(page, "document.querySelector('.drawer .btn--close')")
    page.wait_for(
        "document.querySelector('.drawer').hidden === true",
        message="the drawer to close",
    )


def test_a_blocked_posting_is_marked_blocked_despite_a_high_score(
    page: Chrome, server: str
) -> None:
    """7. A failed gate is not softened by a good fit.

    The score and the eligibility answer are separate questions (ADR-0004), and
    the failure mode this guards against is a blocked posting that only LOOKS
    blocked because it also scored badly. So the assertion is deliberately
    two-handed: the marker is present AND the number beside it is high.
    """
    # ASKED FOR, because this posting is one the eligibility default hides:
    # its geography gate failed, which is the whole subject of this test. The
    # default is asserted separately, in
    # `test_a_posting_that_rules_you_out_is_hidden_until_you_ask_for_it`.
    open_list(page, server, SHOW_EVERYTHING)
    wait_for_count(page, DEMO_GROUPED_COUNT, "before inspecting the blocked posting")

    card = node_for(BLOCKED_TITLE)
    assert page.evaluate(f"{card}.classList.contains('card--blocked')")
    assert page.evaluate(f"Boolean({card}.querySelector('.card__blocked'))")
    marker = str(page.evaluate(f"{card}.querySelector('.card__blocked').textContent"))
    # It names the POSTING as the source of the requirement, not the person as
    # the thing that fell short. "Blocked" was the old word and it read as a
    # verdict on somebody rather than as a line in a job advert.
    #
    # It says WHY now, in the employer's own words where the gate quoted any:
    # `Not eligible: Remote (United States)`. It used to say only "this posting
    # states a requirement you do not meet" and leave the reason in the drawer,
    # which was fine while these were hidden by default and is not fine now
    # that revealing them is a deliberate act.
    assert "not eligible" in marker.lower(), marker
    where = str(
        page.evaluate(
            f"{card}.querySelector('.card__meta, .card__location') ? "
            f"{card}.querySelector('.card__meta, .card__location').textContent : ''"
        )
    )
    del where  # the location is rendered elsewhere on the card; the reason is here
    assert "united states" in marker.lower(), (
        f"the marker no longer says WHAT rules her out: {marker}"
    )
    assert "✕" in marker, "the marker relies on colour alone"

    raw = str(page.evaluate(f"{card}.querySelector('.badge--match .badge__value').textContent"))
    score = int(raw.replace("%", "").strip())
    assert score > 40, f"{BLOCKED_TITLE} scored {score}; this test no longer proves its point"

    eligibility = str(page.evaluate(f"{card}.querySelector('.badge--eligibility').textContent"))
    # "Rules you out" is the plain-language rendering of VERIFIED_NOT_ELIGIBLE.
    # The badge used to read "Not eligible", which is closer to the enum than
    # to a sentence a person would say.
    assert "rules you out" in eligibility.lower()


def test_the_grouping_toggle_is_visible_survives_a_reload_and_follows_the_view(
    page: Chrome, server: str
) -> None:
    """Grouping is a control, not a behaviour.

    Three things have to hold for that to be true: the toggle shows its own
    state, a deliberate choice survives a reload, and switching view restores
    that view's default in the open rather than leaving the previous one
    silently in force.

    And a fourth, which the corpus only recently made assertable: pressing it
    has to actually change what is drawn. Until the demo corpus published a
    role twice, this test could not tell a working toggle from an inert one --
    every count was the same on both sides of the click.
    """
    pressed = "document.getElementById('group-duplicates').getAttribute('aria-pressed')"
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before touching the grouping toggle")
    assert page.evaluate(pressed) == "true", "Cards is the reading view and groups by default"

    click(page, "document.getElementById('group-duplicates')")
    page.wait_for(f"{pressed} === 'false'", message="the toggle to turn grouping off")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "with grouping switched off")
    # A choice that lives only in memory is a choice the URL cannot carry and a
    # reload silently reverses.
    assert "group_duplicates=0" in str(page.evaluate("location.search"))
    page.reload()
    wait_for_count(page, DEMO_VISIBLE_COUNT, "after reloading with grouping off")
    assert page.evaluate(pressed) == "false"

    click(page, "document.getElementById('view-table')")
    page.wait_for(
        f"document.querySelector('table.jobs') && {pressed} === 'false'",
        message="the table, which is the ungrouped view",
    )
    click(page, "document.getElementById('view-cards')")
    page.wait_for(
        f"{A_REAL_CARD} && {pressed} === 'true'"
        f" && {RENDERED_COUNT} === {DEMO_VISIBLE_GROUPED_COUNT}",
        message="Cards to restore its own default, and re-collapse the trio",
    )


def test_the_grouped_card_says_what_it_stands_for_and_the_table_does_not_hide_it(
    page: Chrome, server: str
) -> None:
    """The duplicate group, on screen, in both views.

    `tests/integration/test_web_api.py` proves the payload carries
    `duplicate_count` and `sibling_locations`. That is a different question
    from whether anybody can SEE them, and until the demo corpus published a
    role three times nothing could ask the second question at all: seventeen
    distinct postings meant every badge branch returned null.

    Cards is the reading view: one card, a "3 locations" badge, and the three
    places named. Table is the "show me every row" view: three rows, no
    marker, nothing collapsed. Both are true, of different questions, and the
    reader is told which one they are looking at.

    THE CARD NAMES TWO PLACES AND COUNTS THE REST. It used to name all of
    them, which was right while a card was 334px wide and wrong once five fit
    across a desktop: three location strings filled three lines above the
    salary. What must stay true is not the naming of every place -- the drawer
    does that -- but that the count is honest and the remainder is STATED. A
    representative card that quietly stood for seven others is the defect
    grouping is not allowed to have; a card that says "+1 more" is not.

    The server's own truncation, at `MAX_SIBLING_LOCATIONS` (12), is a
    different cap and is asserted in `tests/unit/test_mvp_repo.py`, where it
    can be built exactly and cheaply.
    """
    card = node_for(GROUPED_TITLE)
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before looking for the grouped card")

    assert page.evaluate(f"Boolean({card})"), f"no card for {GROUPED_TITLE!r}"
    assert page.evaluate(f"{card}.querySelector('.card__company').textContent.trim()") == (
        GROUPED_COMPANY
    )
    assert page.evaluate(f"Boolean({card}.querySelector('.card__group'))"), (
        "the representative card drew no badge, so it swallowed two postings silently"
    )
    count = str(page.evaluate(f"{card}.querySelector('.card__group-count').textContent"))
    assert count == f"{GROUPED_POSTINGS} locations", count

    places = str(page.evaluate(f"{card}.querySelector('.card__group-places').textContent"))
    #: `MAX_PLACES` in `cards.js`. Two named, and the remainder counted.
    named = 2
    assert places.count("·") == named, f"expected {named} places named plus a count, got {places!r}"
    assert f"+{GROUPED_POSTINGS - named} more" in places, (
        f"{GROUPED_POSTINGS - named} places were capped away and the badge did not say so:"
        f" {places!r}"
    )
    assert "(Americas)" in places, places

    # And no OTHER card claims to stand for more than itself: a badge on a
    # singleton would be worse than no badge at all.
    badges = page.evaluate("document.querySelectorAll('.card .card__group').length")
    assert int(badges) == 1, f"{badges} cards drew a group badge; exactly one role repeats"

    # The Table asks the other question and gets the other answer.
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "in the ungrouped table")
    rows = page.evaluate(
        "Array.from(document.querySelectorAll('.cell__title-btn'))"
        f".filter((n) => n.textContent.trim() === {json.dumps(GROUPED_TITLE)}).length"
    )
    assert int(rows) == GROUPED_POSTINGS, f"the table showed {rows} of the three postings"
    assert int(page.evaluate("document.querySelectorAll('.row__group').length")) == 0, (
        "an ungrouped table must not mark a row as standing for others"
    )

    # Asked to group, it marks the row rather than pretending nothing happened.
    open_list(page, server, "?view=table&group_duplicates=1")
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "in the grouped table")
    marker = str(page.evaluate("document.querySelector('.row__group').textContent"))
    assert marker == f"×{GROUPED_POSTINGS}", marker


def test_the_drawer_names_the_other_postings_of_the_same_role(page: Chrome, server: str) -> None:
    """ "Published more than once", with the places listed rather than counted.

    A card that says "3 locations" provokes exactly one question, and the
    drawer is where it is answered. Opened from the grouped card, so this is
    the path a reader actually takes.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before opening the grouped card")

    click(page, f"{node_for(GROUPED_TITLE)}")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.d-sec').length > 0",
        message=f"the drawer for {GROUPED_TITLE!r}",
    )

    assert int(page.evaluate("document.querySelectorAll('.d-places__item').length")) == (
        GROUPED_POSTINGS
    ), "the drawer must name every location the badge counted"
    assert int(page.evaluate("document.querySelectorAll('.d-places__item--more').length")) == 0
    listed = str(page.evaluate("document.querySelector('.d-places').textContent"))
    assert "(Americas)" in listed and "(EMEA)" in listed, listed

    click(page, "document.querySelector('.drawer .btn--close')")
    page.wait_for(
        "document.querySelector('.drawer').hidden === true",
        message="the drawer to close",
    )


def test_a_status_set_in_the_table_survives_a_reload_and_shows_in_cards(
    page: Chrome, pristine_server: str
) -> None:
    """8. A status change is written to the server, not to a rendering.

    The probe attribute is how the wait distinguishes "the select I just typed
    into" from "the select the repaint built out of the server's answer". Both
    read INTERVIEW; only the second one proves the PATCH round-tripped.

    **`pristine_server`, because this test TRACKS a posting.** Its subject is
    one the V1.5 unresolved narrowing sets aside, and a tracked posting is
    exempt from every narrowing by design -- so on the shared database this
    test would have added one row to the default view of every test that ran
    after it, and eight of them would fail for a reason none of them is about.
    """
    # EVERY narrowing undone, because this test is about a status round trip
    # and the posting it uses is one the V1.5 unresolved narrowing sets aside
    # by default. Asking for the whole corpus is what keeps the subject of the
    # test on screen without making the test about eligibility.
    open_list(page, pristine_server, f"{SHOW_EVERYTHING}&view=table")
    wait_for_count(page, DEMO_POSTING_COUNT, "in the table before changing a status")

    row = node_for(STATUS_TITLE)
    page.evaluate(
        f"(() => {{ {row}.querySelector('select.select--status').dataset.probe = '1';"
        " return true; })()"
    )
    set_value(page, f"{row}.querySelector('select.select--status')", "INTERVIEW", "change")
    page.wait_for(
        f"(() => {{ const s = {row}.querySelector('select.select--status');"
        " return s && !s.dataset.probe && s.value === 'INTERVIEW'; })()",
        message="the table to repaint from the server's answer",
    )

    page.reload()
    wait_for_count(page, DEMO_POSTING_COUNT, "in the table after location.reload()")
    assert page.evaluate(f"{row}.querySelector('select.select--status').value") == "INTERVIEW"

    click(page, "document.getElementById('view-cards')")
    page.wait_for(
        f"{A_REAL_CARD} && {RENDERED_COUNT} === {DEMO_GROUPED_COUNT}",
        message="the cards view after the reload",
    )
    assert page.evaluate(f"{row}.querySelector('select.select--status').value") == "INTERVIEW"


def test_applying_records_a_date_that_stepping_back_does_not_erase(
    page: Chrome, pristine_server: str
) -> None:
    """9. The date records an event; the status records a stage. ADR-0012.

    THIS TEST REPLACES ONE THAT ASSERTED THE OPPOSITE. It read: "moving back to
    SHORTLISTED must clear it, or the pipeline grows a second, stale source of
    truth for did-I-apply". The premise is wrong in one word -- the date is not
    stale. An application was sent on that day, and reconsidering the stage
    afterwards does not un-send it.

    What the old rule cost: one drag on the board, or one dropdown here, and
    the day you applied was gone with no warning and no undo.
    """
    # The whole corpus, for the reason given in the status test above.
    open_list(page, pristine_server, f"{SHOW_EVERYTHING}&view=table")
    wait_for_count(page, DEMO_POSTING_COUNT, "in the table before applying")

    row = node_for(APPLIED_TITLE)
    date_input = f"{row}.querySelector('input.input--date')"
    assert page.evaluate(f"{date_input}.value") == ""

    set_value(page, f"{row}.querySelector('select.select--status')", "APPLIED", "change")
    page.wait_for(
        f"{date_input}.value.length === 10",
        message=f"an applied date to appear for {APPLIED_TITLE!r}",
    )
    stamped = str(page.evaluate(f"{date_input}.value"))

    set_value(page, f"{row}.querySelector('select.select--status')", "SHORTLISTED", "change")
    page.wait_for(
        f"{row}.querySelector('select.select--status').value === 'SHORTLISTED'",
        message="the status to step back",
    )
    assert page.evaluate(f"{date_input}.value") == stamped, (
        "stepping back erased the date the person applied"
    )

    # And it survives a reload, so this is the STORED value rather than a
    # screen that merely has not caught up yet.
    #
    # WHERE it survives is the 2026-09-07 correction, asserted here in the
    # browser because this is the test that used to assert the opposite. This
    # posting is one the unresolved narrowing sets aside: nothing in it says
    # whether she could take it. Shortlisting it makes it a job she DECIDED
    # about, and a decision is kept in the views that are about her decisions.
    # It is not a recommendation, so the default list stays exactly as long as
    # it was -- `DEMO_VISIBLE_COUNT`, not one more.
    open_list(page, pristine_server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "after the reload")
    assert page.evaluate(f"{node_for(APPLIED_TITLE)} === undefined"), (
        "a posting whose eligibility nothing answered came back into the "
        "recommendations because she shortlisted it"
    )

    # ...and it is right there on the board, carrying the date.
    open_list(page, pristine_server, "?view=table&status=SHORTLISTED")
    page.wait_for(
        f"{node_for(APPLIED_TITLE)} !== undefined",
        message="the shortlisted posting in the view that holds her decisions",
    )
    reloaded = f"{node_for(APPLIED_TITLE)}.querySelector('input.input--date').value"
    assert page.evaluate(reloaded) == stamped


def test_the_date_survives_a_backward_move_on_every_surface(page: Chrome, server: str) -> None:
    """One store, four surfaces, and the date is the same on all of them.

    The specification names Cards, Table, Kanban and the detail view, and the
    old behaviour was reachable from every one of them because they all call
    the same handler. So this walks all four rather than trusting that.
    """
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before applying")
    row = node_for(SYNC_TITLE)
    job_id = str(page.evaluate(f"{row}.dataset.jobId"))
    set_value(page, f"{row}.querySelector('select.select--status')", "APPLIED", "change")
    page.wait_for(
        f"{row}.querySelector('input.input--date').value.length === 10",
        message="the date to be recorded",
    )
    stamped = str(page.evaluate(f"{row}.querySelector('input.input--date').value"))

    # -- the board, where the backward gesture is one drag ------------------
    page.navigate(f"{server}/?view=kanban")
    page.wait_for(
        "document.querySelector('.kanban')"
        " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
        " && Array.from(document.querySelectorAll('.kcard')).some((n) =>"
        f" n.dataset.jobId === {json.dumps(job_id)})",
        message="the card on the board",
    )
    node = (
        "Array.from(document.querySelectorAll('.kcard'))"
        f".find((n) => n.dataset.jobId === {json.dumps(job_id)})"
    )
    set_value(page, f"{node}.querySelector('.select--status')", "SHORTLISTED", "change")
    page.wait_for(
        "Array.from(document.querySelectorAll('[data-column=\"SHORTLISTED\"] .kcard'))"
        f".some((n) => n.dataset.jobId === {json.dumps(job_id)})",
        message="the card to move back to Interested",
    )
    on_board = str(page.evaluate(f"{node}.querySelector('.kcard__applied').textContent"))
    assert on_board.strip(), "the board dropped the applied date on a backward move"

    # -- cards ---------------------------------------------------------------
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the cards")
    card = (
        "Array.from(document.querySelectorAll('[data-job-id]'))"
        f".find((n) => n.dataset.jobId === {json.dumps(job_id)})"
    )
    assert page.evaluate(f"{card}.querySelector('select.select--status').value") == "SHORTLISTED"

    # -- the drawer, which is the only place the date can be removed ---------
    open_list(page, server, f"?job={job_id}")
    page.wait_for("document.querySelector('.d-applied')", message="the drawer applied line")
    assert "Applied on" in str(page.evaluate("document.querySelector('.d-applied').textContent"))

    # -- and the table it started in -----------------------------------------
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "back in the table")
    again = (
        "Array.from(document.querySelectorAll('[data-job-id]'))"
        f".find((n) => n.dataset.jobId === {json.dumps(job_id)})"
        ".querySelector('input.input--date').value"
    )
    assert page.evaluate(again) == stamped


def test_clearing_the_date_needs_a_confirmation_and_honours_the_answer(
    page: Chrome, server: str
) -> None:
    """The confirmation IS the control, not decoration around it.

    Both answers are asserted, because a dialog that is shown and then ignored
    is worse than no dialog: it teaches the person that the guard is real.
    `window.confirm` is stubbed rather than driven through CDP so the test can
    say which answer it gave; that the stub is consulted at all is what proves
    the code path asks before destroying anything.
    """
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before applying")
    row = node_for(CLEARED_TITLE)
    job_id = str(page.evaluate(f"{row}.dataset.jobId"))
    set_value(page, f"{row}.querySelector('select.select--status')", "APPLIED", "change")
    page.wait_for(
        f"{row}.querySelector('input.input--date').value.length === 10",
        message="the date to be recorded",
    )

    # A post-application status offers no clear at all: the server would put
    # the date straight back, and a control that always fails is not a control.
    open_list(page, server, f"?job={job_id}")
    page.wait_for("document.querySelector('.d-applied')", message="the applied line")
    assert page.evaluate("document.querySelector('.d-applied .btn--danger') === null")
    assert "Kept while the status is" in str(
        page.evaluate("document.querySelector('.d-applied').textContent")
    )

    # Step back. The date stays, and now there is a way to remove it.
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before stepping back")
    back = f"{node_for(CLEARED_TITLE)}.querySelector('select.select--status')"
    set_value(page, back, "SHORTLISTED", "change")
    page.wait_for(f"{back}.value === 'SHORTLISTED'", message="the step back")

    open_list(page, server, f"?job={job_id}")
    page.wait_for(
        "document.querySelector('.d-applied .btn--danger')",
        message="the clear control on a pre-application status",
    )

    # Answering NO must change nothing.
    page.evaluate(
        "(() => { window.__asked = 0;"
        " window.confirm = () => { window.__asked += 1; return false; };"
        " return true; })()"
    )
    click(page, "document.querySelector('.d-applied .btn--danger')")
    page.wait_for("window.__asked === 1", message="the confirmation to be asked for")
    assert page.evaluate("document.querySelector('.d-applied') !== null"), (
        "declining the confirmation cleared the date anyway"
    )

    # Answering YES must clear it, and the line goes with it.
    page.evaluate("(() => { window.confirm = () => true; return true; })()")
    click(page, "document.querySelector('.d-applied .btn--danger')")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelector('.d-applied') === null",
        message="the applied line to disappear",
    )

    # And it is gone from the STORE, not only from the drawer.
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "after clearing")
    field = f"{node_for(CLEARED_TITLE)}.querySelector('input.input--date').value"
    assert page.evaluate(field) == ""


def test_the_empty_state_offers_a_way_out_that_works(page: Chrome, server: str) -> None:
    """10. Nothing matched is a state with an exit, not a blank page."""
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before searching for nothing")

    set_value(page, "document.getElementById('f-search')", "zzzzznothingmatches", "input")
    page.wait_for(
        "Boolean(document.querySelector('#list .state__head'))",
        message="the empty state to replace the list",
    )
    head = str(page.evaluate("document.querySelector('.state__head').textContent"))
    assert "Nothing matches" in head, head

    clear_button = (
        "Array.from(document.querySelectorAll('#list button'))"
        ".find((n) => n.textContent.toLowerCase().includes('clear'))"
    )
    assert page.evaluate(f"Boolean({clear_button})"), "the empty state offered no way back"
    click(page, clear_button)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "after clearing from the empty state")


def test_the_local_model_is_reported_as_never_contacted(page: Chrome, server: str) -> None:
    """11. A page load opens no socket to Ollama, and says so in those words.

    `reachable` is three-valued and the middle value is the whole point: `null`
    is "we have not asked", and printing it as "not running" would be a claim
    nobody checked. This test never posts to `/enrich`; doing so would run a
    real local model for minutes against a bounded allowance.
    """
    with urllib.request.urlopen(f"{server}/api/health", timeout=10) as response:
        health = json.loads(response.read().decode("utf-8"))
    assert health["ollama"]["reachable"] is None
    assert health["ollama"]["checked_at"] is None

    open_list(page, server)
    page.wait_for(
        "Boolean(document.querySelector('.health__ollama'))",
        message="the header health readout",
    )
    readout = str(page.evaluate("document.querySelector('.health__ollama').textContent"))
    assert readout == "local model not contacted yet", readout
    assert page.evaluate(
        "document.querySelector('.health__ollama').classList.contains('is-unknown')"
    )


def test_a_full_pass_raises_nothing_from_page_script(page: Chrome, server: str) -> None:
    """12. Load, switch, filter, open, close -- and the console stays quiet.

    A browser-emitted resource error is a different animal from a module that
    threw, so the two are separated rather than lumped together: the first is
    reported with its source and its URL, the second is a defect.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "at the start of the full pass")

    click(page, "document.getElementById('view-table')")
    page.wait_for("Boolean(document.querySelector('table.jobs'))", message="the table")
    click(page, "document.getElementById('view-cards')")
    page.wait_for(f"Boolean({A_REAL_CARD})", message="the cards")

    set_value(page, "document.getElementById('f-min-score')", "55", "input", "change")
    wait_for_count(page, FILTERED_COUNT, "during the full pass")

    # EXPLAINED_TITLE, not BLOCKED_TITLE: the ruled-out posting is hidden by
    # default now, and this only needs a drawer. The blocked card has its own
    # test, which asks for it explicitly.
    click(page, f"{node_for(EXPLAINED_TITLE)}")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.d-sec').length > 0",
        message="the drawer during the full pass",
    )
    click(page, "document.querySelector('.drawer .btn--close')")
    page.wait_for(
        "document.querySelector('.drawer').hidden === true",
        message="the drawer to close",
    )

    errors = page.console_errors()
    from_script = [error for error in errors if error["source"] in PAGE_SCRIPT_SOURCES]
    assert from_script == [], f"page script raised: {from_script}"
    for error in errors:
        assert "/js/" not in error["url"], f"a module failed to load: {error}"
        assert "failed to load resource" in error["text"].lower(), (
            f"an error that is not a resource load reached the console: {error}"
        )


def test_the_mobile_layout_scrolls_the_table_and_not_the_page(page: Chrome, server: str) -> None:
    """13. At 390x844 the page fits; only the table moves sideways.

    A horizontally scrolling BODY is the failure this guards: it drags the
    header off screen and makes every tap a guess. The table is allowed to
    overflow, inside its own labelled scroll region.
    """
    page.set_viewport(*MOBILE, mobile=True)
    try:
        open_list(page, server)
        wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "on a 390px viewport")
        assert page.evaluate("document.body.scrollWidth <= document.documentElement.clientWidth"), (
            f"the page scrolls sideways: body {page.evaluate('document.body.scrollWidth')} vs "
            f"viewport {page.evaluate('document.documentElement.clientWidth')}"
        )

        click(page, "document.getElementById('view-table')")
        page.wait_for(
            "Boolean(document.querySelector('.tablescroll table.jobs'))",
            message="the table on mobile",
        )
        assert page.evaluate("document.body.scrollWidth <= document.documentElement.clientWidth"), (
            "the table pushed the page wider than the viewport"
        )

        scroller = "document.querySelector('.tablescroll')"
        assert page.evaluate(f"{scroller}.scrollWidth > {scroller}.clientWidth"), (
            "the table did not overflow, so this assertion proves nothing"
        )
        overflow = str(page.evaluate(f"getComputedStyle({scroller}).overflowX"))
        assert overflow in {"auto", "scroll"}, overflow
    finally:
        page.set_viewport(1440, 960)


# =========================================================================
# 14. the evidence
# =========================================================================


def test_the_published_screenshots_show_only_invented_data(
    page: Chrome, pristine_server: str, capture: Callable[[str], Path]
) -> None:
    """The fifteen committed PNGs, each checked before it is written.

    Over `pristine_server` -- a database nothing has touched -- because the
    shared one is MUTATED by the rest of the suite, and every status a test
    moves shows up in a frame. Running the suite whole and running this test
    alone used to produce six different screenshots, which makes a `git diff`
    on the evidence mean nothing.

    `capture` refuses to save a frame containing a real employer name, a path
    under somebody's account, or any value out of `.env`. The check runs on the
    rendered text of the page being photographed, so it fails on the frame that
    would have leaked rather than on a rule about frames in general.
    """
    server = pristine_server
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before the cards screenshot")
    written = [capture("cards-desktop")]

    # BOTH themes, chosen through the control rather than emulated.
    #
    # The frame above is whatever this machine prefers. These two are what a
    # person gets when they press Light or Dark, which is a different claim and
    # the one V3 has to support: the palette existing is not the same as the
    # palette being reachable. Every status colour in the interface appears in
    # one of these two frames, which is how "test every important status colour
    # in both modes" is actually checked rather than asserted.
    choose_theme(page, "light")
    written.append(capture("cards-desktop-light"))
    choose_theme(page, "dark")
    written.append(capture("cards-desktop-dark"))

    # And the disclosure the eligibility default puts above the results, which
    # only exists in the default view and is the newest thing on this screen.
    choose_theme(page, "light")
    page.wait_for(
        "document.getElementById('hiddennotice').hidden === false",
        message="the hidden-postings notice",
    )
    written.append(capture("eligibility-hidden-desktop"))

    reset_theme(page)
    page.set_color_scheme(None)

    click(page, "document.getElementById('view-table')")
    # For ROWS, not for the table element: `table.jobs` is on screen while the
    # skeleton is, so waiting on it photographs placeholder rows under the word
    # "Loading...". The table is ungrouped, so it shows all nineteen postings
    # where Cards shows seventeen roles.
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before the table screenshot")
    written.append(capture("table-desktop"))

    # BOTH themes for the table too. The frames used to cover both only in
    # Cards, so the evidence claimed "both themes" on the strength of one
    # view. The table is the densest surface in the product and the one where
    # a colour mistake hides best.
    choose_theme(page, "light")
    written.append(capture("table-desktop-light"))
    choose_theme(page, "dark")
    written.append(capture("table-desktop-dark"))
    reset_theme(page)

    click(page, "document.getElementById('view-cards')")
    page.wait_for(f"Boolean({A_REAL_CARD})", message="the cards to come back")
    # EXPLAINED_TITLE, not BLOCKED_TITLE: the ruled-out posting is hidden by
    # default now, and this only needs a drawer. The blocked card has its own
    # test, which asks for it explicitly.
    click(page, f"{node_for(EXPLAINED_TITLE)}")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.d-sec').length > 0",
        message="the drawer to photograph",
    )
    # LIGHT explicitly, so this frame and the dark one below are actually a
    # pair. Headless Chrome prefers dark, so a "system" frame here would have
    # been byte-identical to the dark one and the evidence would have shown one
    # theme twice while claiming to show two.
    choose_theme(page, "light")
    written.append(capture("detail-desktop"))

    # The drawer in the dark, because it carries the most colour in the product:
    # two tabs, the match and detail badges, the eligibility badge, quotes and
    # the folded advanced section. If a status colour is going to be unreadable
    # in one theme, it is unreadable here.
    choose_theme(page, "dark")
    written.append(capture("detail-desktop-dark"))

    # And its second tab, which is where the reasoning lives.
    click(page, "document.getElementById('drawer-tab-why')")
    page.wait_for(
        "document.getElementById('drawer-panel-why').hidden === false",
        message="the reasoning tab to open for its photograph",
    )
    written.append(capture("detail-why-desktop-dark"))
    reset_theme(page)
    click(page, "document.querySelector('.drawer .btn--close')")
    page.wait_for(
        "document.querySelector('.drawer').hidden === true",
        message="the drawer to close",
    )

    # The grouped role gets its own frame. `cards-desktop` shows the badge and
    # `table-desktop` shows the three rows it stands for; this is the third
    # surface, and the only one that answers "three locations, but WHICH?".
    click(page, f"{node_for(GROUPED_TITLE)}")
    page.wait_for(
        "Boolean(document.querySelector('.d-places__item'))",
        message="the drawer's list of the other postings of this role",
    )
    # "Published more than once" sits below the score breakdown, so a frame of
    # the drawer as it opens is a frame of something else. The screenshot is
    # the evidence; scroll to what it is evidence OF.
    page.evaluate(
        "(() => { document.querySelector('.d-places')"
        ".scrollIntoView({ block: 'center' }); return true; })()"
    )
    page.wait_for(
        "(() => { const r = document.querySelector('.d-places').getBoundingClientRect();"
        " return r.top > 0 && r.bottom < window.innerHeight; })()",
        message="the duplicates section to be inside the viewport being photographed",
    )
    written.append(capture("detail-grouped-desktop"))
    click(page, "document.querySelector('.drawer .btn--close')")
    page.wait_for(
        "document.querySelector('.drawer').hidden === true",
        message="the drawer to close",
    )

    # The board is the third view and had no frame at all. It is also the only
    # surface that shows what the status palette is FOR.
    click(page, "document.getElementById('view-kanban')")
    page.wait_for(
        "document.querySelector('.kanban')"
        " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
        " && document.querySelectorAll('.kcol').length === 6",
        message="the tracker board to photograph",
    )
    written.append(capture("kanban-desktop"))
    choose_theme(page, "light")
    written.append(capture("kanban-desktop-light"))
    reset_theme(page)

    # The table scrolled to its right-hand end. `table-desktop` proves the
    # first columns render; this proves the rest are REACHABLE, which is the
    # thing that was actually broken -- eighteen columns behind a scrollbar
    # that sat below the fold.
    # Reached by URL rather than by clicking the view switch, because the board
    # narrows to its nine tracked statuses and clicking back would carry that
    # narrowing into the frame -- so the "scrolled table" would photograph a
    # different population than `table-desktop` and the pair would not compare.
    open_list(page, server, "?view=table")
    # For ROWS, not for the table element. `table.jobs` exists while the
    # skeleton is on screen, so this wait passed during loading and the frame
    # came out as eight grey placeholder rows under the word "Loading...".
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before the scrolled table shot")
    # Scroll as far as it goes and photograph that. Whether it reaches the
    # exact end is asserted by `test_the_table_scrolls_sideways_...`; asking
    # for it twice makes the evidence depend on a pixel measurement, and a
    # screenshot test that fails on arithmetic teaches nothing.
    page.evaluate(
        "(() => { const s = document.querySelector('.tablescroll');"
        " s.scrollLeft = s.scrollWidth; return s.scrollLeft; })()"
    )
    written.append(capture("table-scrolled-desktop"))

    # The filter panel, OPENED. It is a horizontal panel above the results
    # now rather than a rail beside them, and it is closed by default, so the
    # frame has to open it -- which is itself the thing worth photographing.
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before the filter panel shot")
    _panel_open(page)
    page.evaluate(
        "(() => { const g = Array.from(document.querySelectorAll('.facet__summary'))"
        "   .find((n) => n.textContent.includes('Country on the posting'));"
        " if (g) g.parentElement.open = true; return true; })()"
    )
    written.append(capture("filters-desktop"))

    # The source matrix, which is where "why is there nothing from Gupy" is
    # answered. Opened rather than fetched on load, so the frame has to open it.
    open_settings(page)
    page.wait_for(
        "document.querySelectorAll('#sources-host .src__row').length > 0",
        message="the source rows to photograph",
    )
    written.append(capture("sources-desktop"))

    # A laptop, which is what most people actually have. 1440 is generous and
    # 390 is a phone; the width where three columns become two is the one
    # neither of them tests.
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before the laptop shot")
    # The preceding filter photograph deliberately opened this persistent panel.
    # Close it through its control so laptop/mobile frames actually show jobs.
    page.evaluate(
        "(() => { if (!document.getElementById('filterpanel').hidden)"
        " document.getElementById('rail-toggle').click(); return true; })()"
    )
    page.wait_for("document.getElementById('filterpanel').hidden === true")
    page.set_viewport(*LAPTOP)
    click(page, "document.getElementById('view-cards')")
    page.wait_for(f"Boolean({A_REAL_CARD})", message="the cards at laptop width")
    written.append(capture("cards-laptop"))
    page.set_viewport(*DESKTOP)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="the cards back at desktop width")

    set_value(page, "document.getElementById('f-search')", "zzzzznothingmatches", "input")
    page.wait_for(
        "Boolean(document.querySelector('#list .state__head'))",
        message="the empty state to photograph",
    )
    written.append(capture("empty-state"))

    # Mobile gets all three views, not just Cards. A re-review pointed out
    # that one narrow screenshot is not visual QA of a responsive interface:
    # the Table is the view most likely to break at 390px, and the drawer is
    # the one whose focus trap has the least room to work in.
    page.set_viewport(*MOBILE, mobile=True)
    try:
        open_list(page, server)
        wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before the mobile screenshot")
        written.append(capture("cards-mobile"))

        # Both themes on a narrow screen too. The rail collapses to a
        # disclosure here and the toolbar wraps, so this is where a theme is
        # most likely to break something the desktop frames would not show.
        choose_theme(page, "light")
        written.append(capture("cards-mobile-light"))
        choose_theme(page, "dark")
        written.append(capture("cards-mobile-dark"))
        reset_theme(page)

        click(page, "document.getElementById('view-table')")
        page.wait_for(
            "Boolean(document.querySelector('table.jobs'))",
            message="the table to photograph on a narrow screen",
        )
        written.append(capture("table-mobile"))

        click(page, "document.getElementById('view-cards')")
        page.wait_for(
            f"Boolean({A_REAL_CARD})",
            message="the cards to come back on a narrow screen",
        )
        # EXPLAINED_TITLE, not BLOCKED_TITLE: the ruled-out posting is hidden
        # by default now, and this only needs a drawer.
        click(page, f"{node_for(EXPLAINED_TITLE)}")
        page.wait_for(
            "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
            " && document.querySelectorAll('.d-sec').length > 0",
            message="the drawer to photograph on a narrow screen",
        )
        written.append(capture("detail-mobile"))
        click(page, "document.querySelector('.drawer .btn--close')")

        # Application tracking on a phone, which is where a person actually
        # updates a status -- on the train after an interview, not at a desk.
        click(page, "document.getElementById('view-kanban')")
        page.wait_for(
            "document.querySelector('.kanban')"
            " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
            " && document.querySelectorAll('.kcol').length === 6",
            message="the board on a narrow screen",
        )
        written.append(capture("kanban-mobile"))
    finally:
        page.set_viewport(1440, 960)

    assert [path.name for path in written] == [
        "cards-desktop.png",
        "cards-desktop-light.png",
        "cards-desktop-dark.png",
        "eligibility-hidden-desktop.png",
        "table-desktop.png",
        "table-desktop-light.png",
        "table-desktop-dark.png",
        "detail-desktop.png",
        "detail-desktop-dark.png",
        "detail-why-desktop-dark.png",
        "detail-grouped-desktop.png",
        "kanban-desktop.png",
        "kanban-desktop-light.png",
        "table-scrolled-desktop.png",
        "filters-desktop.png",
        "sources-desktop.png",
        "cards-laptop.png",
        "empty-state.png",
        "cards-mobile.png",
        "cards-mobile-light.png",
        "cards-mobile-dark.png",
        "table-mobile.png",
        "detail-mobile.png",
        "kanban-mobile.png",
    ]
    for path in written:
        assert path.stat().st_size > 5_000, f"{path} is too small to be a rendered page"


# =========================================================================
# The tracker board
# =========================================================================


def test_the_board_shows_tracked_jobs_and_says_so(page: Chrome, server: str) -> None:
    """A board of everything is the list again with worse ergonomics.

    Kanban narrows to the nine tracked statuses, and the narrowing is VISIBLE:
    it lands in the URL and shows as a chip. The first version of this failed
    the honesty test -- opening `?view=kanban` directly skipped the narrowing
    that the button applied, so the header counted nineteen postings while the
    columns rendered five and silently dropped the rest. A count and a list
    disagreeing is the defect this codebase keeps closing.
    """
    page.navigate(f"{server}/?view=kanban")
    # The SKELETON also draws six columns, so waiting on the column count
    # alone races the real render. `renderKanban` clears `aria-busy`, which is
    # the only signal that says "this is the data, not the placeholder".
    page.wait_for(
        "document.querySelector('.kanban')"
        " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
        " && document.querySelectorAll('.kcol').length === 6",
        message="the six tracker columns, painted with real data",
    )

    # Every card the board draws is in a column, and the header agrees.
    rendered = int(page.evaluate("document.querySelectorAll('.kcard').length"))
    counted = int(
        page.evaluate(
            "Array.from(document.querySelectorAll('.kcol__count'))"
            ".reduce((sum, n) => sum + Number(n.textContent), 0)"
        )
    )
    assert rendered == counted, "a column count disagreed with the cards in it"

    chips = str(
        page.evaluate(
            "Array.from(document.querySelectorAll('.chip--active .chip__text'))"
            ".map((n) => n.textContent).join('|')"
        )
    )
    assert "Only ones I am tracking" in chips, "the board's narrowing must be visible and removable"


def track_one_then_open_the_board(page: Chrome, server: str) -> None:
    """Put a posting on the board, then go and look at it.

    THE BOARD SHOWS WHAT YOU ARE TRACKING, so on a database where nothing has
    been picked up it is correctly empty -- seven columns and no cards. The two
    tests below used to open it and expect a card, which only worked because
    an EARLIER test in the file had left one there. Run either on its own and
    it timed out on a board that was behaving perfectly.

    An order dependency is a test that passes for a reason it does not state.
    This makes the setup explicit: track one posting through the status
    control, which is the accessible path and the one the tests are about
    anyway, and only then open the board.
    """
    open_list(page, server, f"{SHOW_EVERYTHING}&view=table")
    # For the whole table, not for one row. A row that is merely PRESENT can
    # still be a skeleton, and `find` on a half-painted table returns
    # undefined a moment later -- which is how this waited successfully and
    # then read `querySelector` off nothing.
    wait_for_count(page, DEMO_POSTING_COUNT, "in the table before tracking one")
    row = node_for(STATUS_TITLE)
    page.wait_for(
        f"Boolean({row}) && Boolean({row}.querySelector('select.select--status'))",
        message="the posting to put on the board",
    )
    set_value(page, f"{row}.querySelector('select.select--status')", "SHORTLISTED", "change")
    page.wait_for(
        f"{row}.querySelector('select.select--status').value === 'SHORTLISTED'",
        message="the status to be recorded",
    )
    page.navigate(f"{server}/?view=kanban")
    page.wait_for(
        "document.querySelector('.kanban')"
        " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
        " && document.querySelectorAll('.kcard').length > 0",
        message="the posting to appear on the board",
    )


def test_moving_a_card_on_the_board_persists_and_reaches_the_other_views(
    page: Chrome, server: str
) -> None:
    """A drop is a write, not a render.

    Asserted through the status control rather than by simulating a drag,
    because that control is the accessible path and the one that must work:
    dragging is an accelerant on top of it, not the only way in.
    """
    track_one_then_open_the_board(page, server)

    card = "document.querySelector('.kcard')"
    job_id = str(page.evaluate(f"{card}.dataset.jobId"))
    set_value(page, "document.querySelector('.kcard .select--status')", "INTERVIEW", "change")
    page.wait_for(
        "Array.from(document.querySelectorAll('[data-column=\"INTERVIEW\"] .kcard'))"
        f".some((n) => n.dataset.jobId === {json.dumps(job_id)})",
        message="the card to arrive in Interview",
    )

    # The other views must show the same thing, because they read one store.
    #
    # EVERY NARROWING UNDONE, for the reason the status round-trip test above
    # already gives: the posting this uses is one the default view sets aside,
    # and tracking it does not bring it back -- `exempt_tracked` is False by
    # default, so shortlisting something an employer has ruled you out of does
    # not put it back in the recommendations. Asking for the whole corpus is
    # what keeps the subject of the test on screen without making the test
    # about eligibility.
    page.navigate(f"{server}/{SHOW_EVERYTHING}&view=table")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the table")
    row = (
        "Array.from(document.querySelectorAll('[data-job-id]'))"
        f".find((n) => n.dataset.jobId === {json.dumps(job_id)})"
    )
    assert page.evaluate(f"{row}.querySelector('select.select--status').value") == "INTERVIEW"


def test_applying_records_a_date_that_later_stages_do_not_erase(page: Chrome, server: str) -> None:
    """Being moved to Interview must not lose the day you applied.

    This repository erased that date once already, when an omitted field was
    read as "clear it" rather than "leave it". The board moves jobs between
    stages constantly, so it is the surface most likely to do it again.
    """
    track_one_then_open_the_board(page, server)

    card = "document.querySelector('.kcard')"
    job_id = str(page.evaluate(f"{card}.dataset.jobId"))
    node = (
        "Array.from(document.querySelectorAll('.kcard'))"
        f".find((n) => n.dataset.jobId === {json.dumps(job_id)})"
    )

    set_value(page, "document.querySelector('.kcard .select--status')", "APPLIED", "change")
    page.wait_for(
        f"{node} && {node}.querySelector('.kcard__applied')",
        message="the applied date to appear",
    )
    applied = str(page.evaluate(f"{node}.querySelector('.kcard__applied').textContent"))

    set_value(page, f"{node}.querySelector('.select--status')", "OFFER", "change")
    page.wait_for(
        "Array.from(document.querySelectorAll('[data-column=\"OFFER\"] .kcard'))"
        f".some((n) => n.dataset.jobId === {json.dumps(job_id)})",
        message="the card to reach Offer",
    )
    assert str(page.evaluate(f"{node}.querySelector('.kcard__applied').textContent")) == applied


def test_a_board_card_opens_the_drawer_from_its_body_but_not_from_its_control(
    page: Chrome, server: str
) -> None:
    """Same rule as the cards grid: the surface opens, the controls do not."""
    track_one_then_open_the_board(page, server)

    # The status select must not open the drawer behind itself.
    click(page, "document.querySelector('.kcard .select--status')")
    assert page.evaluate(
        "!document.querySelector('.drawer') || document.querySelector('.drawer').hidden === true"
    ), "the status control opened the drawer behind itself"

    click(page, "document.querySelector('.kcard__title')")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden",
        message="the drawer opened from the card body",
    )


# =========================================================================
# Preference editing, retrieval, and accessibility
# =========================================================================


def test_a_keyword_preference_can_be_edited_without_opening_the_yaml(
    page: Chrome, writable_server: str
) -> None:
    """The whole point of the editor: 54 phrase groups, none of them in a file
    the person has to find.

    Asserted through the panel rather than through the API because the API half
    already has ten unit tests, and what was never checked is that the panel
    reaches it -- the same gap that left `/api/sources` with no caller.

    Uses `writable_server`, which is bound to a COPY of `config/`. A save here
    writes `search.local.yaml` and bumps `config_version`, and against the real
    directory that would edit the developer's own search configuration and
    detach the corpus from its scores. See the fixture for the full reason.
    """
    server = writable_server
    # The scoring vocabulary is a developer view, reached with `?debug=1`.
    open_list(page, server, "?debug=1")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list")
    open_settings(page)
    page.evaluate("document.getElementById('settings-model').open = true")
    page.wait_for(
        "document.querySelectorAll('#prefs-host .prefs__signal').length > 0",
        message="the preference groups",
    )

    signal = "document.querySelector('#prefs-host .prefs__signal')"
    phrase = "browser acceptance phrase"

    # A phrase group is a set of CHIPS now, not a textarea of one per line.
    # Typed into the control's own box and committed with Enter, which is what
    # a person does; `set_value` on a textarea is what this used to be.
    page.evaluate(
        f"(() => {{ const box = {signal}.querySelector('.tags__input');"
        f" box.value = '{phrase}';"
        f" box.dispatchEvent(new KeyboardEvent('keydown',"
        f" {{key: 'Enter', bubbles: true}})); }})()"
    )
    page.wait_for(
        f"[...{signal}.querySelectorAll('.chip__text')].some(n => n.textContent === '{phrase}')",
        message="the phrase to become a chip",
    )

    # **SAVING A PHRASE GROUP TAKES TWO PRESSES**, and the first one is not a
    # save. It shows what changed and what changing it costs, because editing a
    # phrase re-scores the whole corpus and the reader deserves to know that
    # before it starts rather than after.
    click(page, f"{signal}.querySelector('.prefs__save')")
    page.wait_for(
        f"{signal}.querySelector('.prefs__diff') && !{signal}.querySelector('.prefs__diff').hidden",
        message="what the edit changes, before it is applied",
    )
    proposed = str(page.evaluate(f"{signal}.querySelector('.prefs__diff').textContent"))
    assert phrase in proposed, proposed

    click(page, f"{signal}.querySelector('.prefs__save')")
    # **WAIT FOR THE SUCCESS CLASS, NOT FOR TEXT.** Any status at all used to
    # satisfy this, which is how a single press -- landing on "press Save again
    # to confirm" -- read as a completed save for a whole session. A status
    # that merely exists says nothing about which of the three states it is in.
    page.wait_for(
        f"{signal}.querySelector('.prefs__status--ok') !== null",
        message="the save to succeed",
    )
    said = str(page.evaluate(f"{signal}.querySelector('.prefs__status').textContent"))
    assert "error" not in said.lower(), said

    # **THE LIST STAYS USABLE.** This used to assert an EMPTY list and a good
    # explanation of it, which was the best answer available at the time:
    # saving bumped `config_version`, every stored score belonged to the
    # previous one, and at least the screen said so rather than claiming the
    # database was empty.
    #
    # ADR-0017 replaced that with a better one. A configuration is a QUESTION
    # and a scored population is an ANSWER; between them there is a third
    # option that is neither "show the new answer" (it does not exist yet) nor
    # "show nothing": serve the previous answer and say which question it
    # answers. The owner met the old behaviour on her own corpus on
    # 2026-09-08 and read the empty list as the product having broken.
    page.navigate(f"{server}/?debug=1")
    page.wait_for(
        f"{RENDERED_COUNT} > 0 || Boolean(document.querySelector('.state__head'))",
        message="the list after a preference edit",
    )
    assert int(page.evaluate(RENDERED_COUNT)) > 0, (
        "the list went empty the moment a preference was saved"
    )

    # And it says WHICH preferences these answer. Serving the previous answer
    # silently would be worse than the empty list, not better: a list that
    # quietly answers a question she stopped asking is one she cannot trust.
    notice = "document.getElementById('revnotice')"
    page.wait_for(f"Boolean({notice}) && !{notice}.hidden", message="the revision notice")
    explained = str(page.evaluate(f"{notice}.innerText"))
    assert "revision" in explained.lower(), explained
    assert "lost" in explained.lower(), explained

    # Still without sending anyone to a terminal. The screen a person reaches
    # by EDITING A PREFERENCE IN THE INTERFACE used to end at
    # `career-agent rescore`, which is a command, a program name and a shell
    # for somebody who has only ever clicked. An independent UX review found
    # this and called it the point at which the product stops.
    assert "career-agent" not in explained, explained
    assert "config" not in explained.lower(), explained
    assert "collection" not in explained.lower(), explained

    # And there is a way FORWARD, not just a description. The sentence above
    # promises a recalculation the page has to be able to start.
    page.wait_for(
        f"Array.from({notice}.querySelectorAll('button'))"
        ".some((n) => /recalculate|recalcular/i.test(n.textContent))",
        message="the recalculate button",
    )
    click(
        page,
        f"Array.from({notice}.querySelectorAll('button'))"
        ".find((n) => /recalculate|recalcular/i.test(n.textContent))",
    )
    # It finishes, the new population takes over, and the notice goes away
    # because there is nothing left to explain. No terminal, no command, no
    # version number anywhere on screen.
    page.wait_for(
        f"Boolean({notice}) && {notice}.hidden",
        timeout=120,
        message="the notice to clear once the new scores are current",
    )
    assert int(page.evaluate(RENDERED_COUNT)) > 0, "the list emptied after a recalculation"

    open_settings(page)
    page.evaluate("document.getElementById('settings-model').open = true")
    page.wait_for(
        "document.querySelectorAll('#prefs-host .prefs__signal .tags').length > 0",
        message="the preferences again",
    )
    assert phrase in str(
        page.evaluate(
            "Array.from(document.querySelectorAll('#prefs-host .chip__text'))"
            ".map((n) => n.textContent).join(String.fromCharCode(10))"
        )
    ), "the edited phrase did not survive a reload"


def test_every_control_has_a_name_a_screen_reader_can_read(page: Chrome, server: str) -> None:
    """An icon button with no accessible name is a button nobody can use.

    Computed the way a screen reader does -- visible text, then `aria-label`,
    then `title`, then the `<label>` bound by `for` -- rather than by looking
    for one attribute, because each of those is a legitimate way to name a
    control and requiring a particular one would fail correct markup.
    """
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "the table")

    unnamed = page.evaluate("""(() => {
      const named = (n) => {
        const own = (n.textContent || '').trim();
        if (own) return true;
        if ((n.getAttribute('aria-label') || '').trim()) return true;
        if ((n.getAttribute('title') || '').trim()) return true;
        const id = n.getAttribute('id');
        if (id && document.querySelector(`label[for="${id}"]`)) return true;
        return Boolean(n.closest('label'));
      };
      return Array.from(document.querySelectorAll(
        'button, a[href], input:not([type=hidden]), select, textarea'
      )).filter((n) => n.offsetParent !== null && !named(n))
        .map((n) => `${n.tagName}.${n.className}`);
    })()""")
    assert unnamed == [], unnamed


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_colour_is_never_the_only_signal_and_the_ink_clears_4_5_to_1(
    page: Chrome, server: str, theme: str
) -> None:
    """Section 27 of the corrective report claims both. This checks both.

    Every status tag carries its WORD as well as its colour, so someone who
    cannot distinguish the hues still reads the status, and the ink measured
    against the background it actually sits on clears WCAG AA for normal text.

    The ratio is computed from `getComputedStyle` rather than from the palette
    constants, so it tests the pixels a person sees rather than the intent
    recorded in a CSS comment.

    IN BOTH THEMES, which V3 made a real distinction. The status hues used to
    keep their LIGHT washes in the dark: a sage tag was near-black text on a
    pale green card floating in a charcoal page, legible and obviously wrong.
    Those dark variants are new, and a measurement that only ever ran in one
    theme would not have caught the old behaviour either.

    `.badge__value` is included alongside the tags because the two numbers a
    person reads first, match and detail, are drawn as badges rather than tags
    and were never measured.
    """
    open_list(page, server, "?view=table")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "the table")
    choose_theme(page, theme)

    failures = page.evaluate(r"""(() => {
      const lum = (c) => {
        const [r, g, b] = c.map((v) => {
          const s = v / 255;
          return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
        });
        return 0.2126 * r + 0.7152 * g + 0.0722 * b;
      };
      const parse = (s) => (s.match(/\d+(\.\d+)?/g) || []).slice(0, 3).map(Number);
      // The nearest ancestor that actually paints, which is what the ink sits
      // on. Walking up is the whole point: a transparent tag over a tinted row
      // over the page has three candidate backgrounds and only one is real.
      const backdrop = (n) => {
        for (let e = n; e; e = e.parentElement) {
          const bg = getComputedStyle(e).backgroundColor;
          const rgba = (bg.match(/[\d.]+/g) || []).map(Number);
          if (rgba.length < 4 || rgba[3] > 0.99) {
            if (bg && bg !== 'transparent' && rgba.length >= 3) return rgba.slice(0, 3);
          }
        }
        return [255, 255, 255];
      };
      const out = [];
      const selector = '.tag, .pill, .chip--active, .badge__value, .badge__label,'
        + ' .fsec__count, .hidden__text';
      for (const tag of document.querySelectorAll(selector)) {
        if (tag.offsetParent === null) continue;
        if (!(tag.textContent || '').trim()) {
          out.push(`${tag.className}: colour with no word`);
          continue;
        }
        const ink = parse(getComputedStyle(tag).color);
        const paper = backdrop(tag);
        const a = lum(ink), b = lum(paper);
        const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
        if (ratio < 4.5) {
          out.push(`${tag.className} "${tag.textContent.trim()}": ${ratio.toFixed(2)}:1`);
        }
      }
      return out;
    })()""")
    assert failures == [], f"in the {theme} theme: {failures}"


def test_nothing_marked_hidden_is_painted(page: Chrome, server: str) -> None:
    """`hidden` has to mean hidden, everywhere, at once.

    The browser's `[hidden] { display: none }` is a USER AGENT rule, so any
    author rule with a `display` beats it. This file had several, and two of
    them were live defects: both drawer tabs painted at once, and an empty
    yellow alert strip on the zero-results screen.

    `app.css` now carries `[hidden] { display: none !important }`, which is the
    only `!important` in the file. This is the test that keeps it honest, and
    it sweeps the whole document rather than the two elements that happened to
    be wrong, because the next one will be a different element.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before sweeping for painted hidden nodes")
    click(page, f"{node_for(EXPLAINED_TITLE)}")
    page.wait_for(
        "document.querySelectorAll('.drawer__tab').length === 3",
        message="the drawer, so its hidden panels are in the sweep too",
    )

    painted = page.evaluate(
        "Array.from(document.querySelectorAll('[hidden]'))"
        ".filter((n) => getComputedStyle(n).display !== 'none')"
        ".map((n) => n.tagName.toLowerCase() + '.' + (n.className || '(no class)'))"
    )
    assert painted == [], f"marked hidden and still painted: {painted}"


def test_the_theme_control_actually_changes_the_page(page: Chrome, server: str) -> None:
    """A control that is pressed and changes nothing is worse than no control.

    Measured on the painted background rather than on the attribute, because
    the attribute landing is what `choose_theme` already waits for and it would
    prove only that JavaScript ran.
    """
    open_list(page, server)

    def paper() -> str:
        return str(page.evaluate("getComputedStyle(document.body).backgroundColor"))

    choose_theme(page, "light")
    light = paper()
    choose_theme(page, "dark")
    dark = paper()
    assert light != dark, f"both themes painted {light}"

    # Warm dark neutrals, never pure black: the two themes have to read as one
    # product under different light.
    channels = [float(v) for v in (dark.replace("rgb(", "").replace(")", "").split(","))[:3]]
    assert any(value > 8 for value in channels), f"the dark theme is pure black: {dark}"
    assert max(channels) - min(channels) >= 1, f"the dark theme has no warmth: {dark}"

    reset_theme(page)


def test_the_theme_choice_is_remembered_and_announced(page: Chrome, server: str) -> None:
    """Persisted locally, and named rather than drawn as a bare icon."""
    open_list(page, server)
    choose_theme(page, "dark")
    assert page.evaluate("window.localStorage.getItem('careerAgent.theme.v1') === 'dark'")

    page.evaluate("window.location.reload()")
    # Two separate things to wait for, and the order is the point. `data-theme`
    # is written SYNCHRONOUSLY in the head, before the body exists, which is
    # what prevents the flash. The control itself is built on DOMContentLoaded,
    # much later. Waiting only on the attribute finds no buttons.
    page.wait_for(
        "document.documentElement.getAttribute('data-theme') === 'dark'",
        message="the remembered theme to be applied before the first paint",
    )
    page.wait_for(
        "document.querySelectorAll('.themeswitch__btn').length === 2",
        message="the theme control to be built",
    )

    # Every button says a word and carries an accessible name with a sentence
    # in it. "Do not rely on an unlabeled icon" is the requirement, and a moon
    # glyph can mean "currently dark" or "switch to dark".
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('.themeswitch__btn'))"
        ".map((n) => [n.textContent.trim(), n.getAttribute('aria-label') || '',"
        " n.getAttribute('title') || ''])"
    )
    assert len(labels) == 2, labels
    for text, aria, title in labels:
        assert text, "a theme button renders no word at all"
        assert len(str(aria).split()) > 3, f"{text}: aria-label is not a sentence: {aria!r}"
        assert title, f"{text}: no tooltip"

    # Nothing chosen is a state, and it is the one a first visit is in. The
    # control does not draw it as a segment -- there is no button meaning "I am
    # not deciding" -- so what proves it is that the key is GONE and the page
    # still resolves to a theme rather than to nothing.
    reset_theme(page)
    assert page.evaluate("window.localStorage.getItem('careerAgent.theme.v1') === null")
    assert page.evaluate(
        "['light', 'dark'].includes(document.documentElement.getAttribute('data-theme'))"
    )
    assert page.evaluate("document.documentElement.hasAttribute('data-theme-choice') === false")


def test_a_first_visit_follows_the_machine_and_a_choice_overrules_it(
    page: Chrome, server: str
) -> None:
    """No Auto segment, and the behaviour it named is still there.

    The owner asked for the third button to go and the design defines light and
    dark only. What must NOT go with it is the product's answer on a first
    visit, which is to follow `prefers-color-scheme` rather than to pick a side
    for somebody. This asserts the two halves of that: absence follows the
    machine, and a press stops it following.
    """
    open_list(page, server)
    reset_theme(page)

    page.set_color_scheme("dark")
    page.evaluate("window.location.reload()")
    page.wait_for(
        "document.documentElement.getAttribute('data-theme') === 'dark'",
        message="a first visit on a dark machine to come up dark",
    )

    page.set_color_scheme("light")
    page.evaluate("window.location.reload()")
    page.wait_for(
        "document.documentElement.getAttribute('data-theme') === 'light'",
        message="a first visit on a light machine to come up light",
    )

    # Now a choice, which outranks the machine from here on.
    choose_theme(page, "dark")
    page.set_color_scheme("light")
    page.evaluate("window.location.reload()")
    page.wait_for(
        "document.documentElement.getAttribute('data-theme') === 'dark'",
        message="the chosen theme to survive a light machine",
    )

    reset_theme(page)
    page.set_color_scheme(None)


# =========================================================================
# Source coverage, reported in the product
# =========================================================================


def test_the_source_panel_reports_coverage_and_never_on_page_load(
    page: Chrome, server: str, capture
) -> None:
    """A capability matrix nobody can open is a claim, not a feature.

    `/api/sources` existed and no module called it, so the classification was a
    document that happened to have an endpoint -- while the question it answers,
    "why is there nothing here from Gupy", is asked of the interface.

    Two properties: it is NOT fetched on load (a list of jobs must not wait on
    a catalogue read), and opening it reports the five-value classification.
    """
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list")
    assert page.evaluate("document.getElementById('sources-host').children.length === 0"), (
        "the catalogue was read on page load"
    )

    open_settings(page)
    page.wait_for(
        "document.querySelectorAll('#sources-host .src__row').length > 0",
        message="the source rows",
    )

    # Case-folded: the coverage labels are rendered through `text-transform:
    # uppercase`, and `innerText` returns what is on screen rather than what
    # the DOM holds. Asserting the mixed case would be asserting the CSS.
    assert page.evaluate("document.querySelector('#sources-host details.src').open") is False
    page.evaluate("document.querySelector('#sources-host details.src').open = true")
    text = str(page.evaluate("document.getElementById('sources-host').innerText")).lower()

    # What is ON SCREEN is the plain wording. "Partially operational" and
    # "Blocked by terms or robots" are the exact classification and they are a
    # status vocabulary; these say what it means for the person reading it.
    assert "jobs are coming in" in text, text[:300]
    assert "not allowed to fetch" in text, text[:300]
    assert "a board is one company, not a source." in text
    assert "connector famil" in text

    # And the exact classification is still attached, as the tag's tooltip, so
    # the plain words and the vocabulary in `docs/product/` stay visibly one
    # thing rather than forking into two claims about the same source.
    titles = str(
        page.evaluate(
            "Array.from(document.querySelectorAll('#sources-host .src__head .tag'))"
            ".map((n) => n.getAttribute('title') || '').join('|')"
        )
    ).lower()
    assert "operational" in titles, titles
    assert "blocked by terms" in titles, titles

    for name, expected in (
        ("Jooble", "off, quota exhausted"),
        ("Elite Software Automation", "access blocked or unavailable"),
        ("Lemon.io", "no job board published"),
    ):
        row_text = str(
            page.evaluate(
                "Array.from(document.querySelectorAll('#sources-host .src__row'))"
                f".find((n) => n.querySelector('th').textContent === {name!r}).innerText"
            )
        ).lower()
        assert expected in row_text, row_text
    assert "last successful run" in text
    capture("sources-desktop")


def test_a_blocked_source_shows_the_reason_and_gupy_does_not_claim_to_work(
    page: Chrome, server: str
) -> None:
    """ "We cannot fetch this" is only useful with "because".

    Gupy is named because it is the row most likely to drift, and the reason it
    drifts changed on 2026-09-09. It used to be that the documentation read
    encouragingly and reading documentation is not retrieval. Now the connector
    is real and 7,090 postings are in the owner's corpus -- so the drift to
    guard against is the opposite one: a row that claims to be working over a
    database holding none of its postings.

    This runs against the DEMO corpus, which holds no Gupy posting, so the
    panel must say the source has not run here. That is `resolve` refusing a
    declaration the corpus cannot support, seen from the screen.
    """
    open_list(page, server)
    open_settings(page)
    page.wait_for(
        "document.querySelectorAll('#sources-host .src__why').length > 0",
        message="a recorded reason",
    )
    # Unreachable leads may honestly have UNDECLARED permission. That is not
    # the same as permission silence on an accessible public endpoint, and
    # the catalogue now records the specific access blocker separately.
    # Test the visible reason and tone, not an empty historical category.

    # EVERY COVERAGE VALUE THE PANEL CAN SHOW MUST HAVE A TONE, which is what
    # the removed half really guarded: UNDOCUMENTED was added in V3 to a panel
    # whose ORDER and TONE lists predated it, so the rows it was invented for
    # sorted last with no tone at all. Asserted now of every tag on the screen
    # rather than of one value, which is the version that cannot expire.
    page.evaluate("document.querySelector('#sources-host details.src').open = true")
    untoned = page.evaluate(
        "Array.from(document.querySelectorAll('#sources-host .src__head .tag'))"
        ".filter((n) => !/tag--/.test(n.className)).map((n) => n.textContent)"
    )
    assert not untoned, f"coverage values with no tone: {untoned}"

    # Scoped to Gupy's own row rather than the whole panel, because the panel
    # legitimately contains operational sources and a substring search over all
    # of it would pass for the wrong reason.
    gupy_row = str(
        page.evaluate(
            "(() => { const row = Array.from("
            "document.querySelectorAll('#sources-host .src__row'))"
            ".find((n) => n.innerText.includes('Gupy'));"
            " return row ? row.innerText : ''; })()"
        )
    )
    assert gupy_row, "the sources panel no longer lists Gupy at all"

    # What Gupy's row says WITHOUT being opened: a sentence a job seeker can
    # act on. Not `robots.txt`, not a 404, not "first-party statement".
    #
    # It asked for a sentence about UNDECLARED PERMISSION until 2026-09-09,
    # which was right while Gupy was `UNDOCUMENTED` and uncollected. ADR-0018
    # ended that and the connector shipped, so the guard moved to the claim
    # that now matters more: over a corpus with none of its postings, the row
    # must NOT read as working. The catalogue declares `PARTIAL`; `resolve`
    # downgrades it here because the demo database proves nothing.
    lowered = gupy_row.lower()
    assert "not run yet" in lowered or "nothing from it yet" in lowered, (
        "Gupy claims to be working over a corpus that holds none of its postings: " + gupy_row
    )
    for working in ("collecting", "operational", "in your list"):
        assert working not in lowered, f"Gupy's row reads as working: {gupy_row}"

    # And the dated evidence, one click down. Opened rather than read out of
    # `textContent`, because "the bytes are in the DOM" is what the drawer-tab
    # defect looked like from a test: both panels were in the DOM and both
    # were painted. If a person cannot reach it, it is not published.
    page.evaluate(
        "(() => { for (const d of document.querySelectorAll('#sources-host .src__tech'))"
        " d.open = true; return true; })()"
    )
    opened = str(
        page.evaluate(
            "(() => { const row = Array.from("
            "document.querySelectorAll('#sources-host .src__row'))"
            ".find((n) => n.innerText.includes('Gupy'));"
            " return row ? row.innerText : ''; })()"
        )
    )
    assert "measured 2026-09-05" in opened.lower(), opened
    assert "10,000" in opened or "82,958" in opened, (
        "the row no longer says how much of the feed is actually reachable"
    )

    whole = str(page.evaluate("document.getElementById('sources-host').innerText"))
    assert "automacao" in whole or "automação" in whole or "Disallow" in whole, (
        "no blocked source quotes the first-party statement that blocks it"
    )


# =========================================================================
# The twelve filters, in the browser
# =========================================================================

#: Store key -> the facet group heading the panel draws for it. Every one of
#: these must be CLICKABLE: the previous round shipped a "Technology / signal"
#: group whose counts nobody produced, so it rendered only once a value was
#: already chosen, which is a control that cannot be used to choose.
FACET_CONTROLS = {
    "country": "Country the posting names",
    "region": "Part of the world the posting names",
    "worksite": "Office, hybrid or remote",
    "seniority": "Seniority",
    "employment_type": "Employment type",
    "salary_currency": "Salary currency",
    "salary_period": "Salary period",
    "technology": "Technology / software",
}


def _panel_open(page: Chrome) -> None:
    """The filter panel, opened.

    It is CLOSED by default now: the filters left the 288px rail they used to
    live in and became a horizontal panel above the results, and the design
    gives the width to the list. So this presses the toggle rather than
    setting a disclosure's `open`, and it is idempotent -- pressing a control
    that is already open would close it.
    """
    page.evaluate(
        "(() => { const p = document.getElementById('filterpanel');"
        " if (p.hidden) document.getElementById('rail-toggle').click();"
        " return true; })()"
    )
    page.wait_for(
        "document.getElementById('filterpanel').hidden === false",
        message="the filter panel to open",
    )


def test_every_new_filter_has_a_control_a_person_can_click(page: Chrome, server: str) -> None:
    """A filter with no reachable control is not implemented, whatever the
    API accepts. Asserted per group, by finding a checkbox and reading the
    count printed beside it."""
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list")
    _panel_open(page)
    page.wait_for(
        "document.querySelectorAll('input[data-facet]').length > 0",
        message="the facet groups to render",
    )

    for key, label in FACET_CONTROLS.items():
        boxes = int(
            page.evaluate(
                f"document.querySelectorAll('input[data-facet={json.dumps(key)}]').length"
            )
        )
        assert boxes > 0, f"{label!r} rendered no checkbox, so nothing can be chosen"

    # The two shorthands and the paired salary control are not facets.
    for control in (
        "f-latam_only",
        "f-worldwide_only",
        "f-min-salary",
        "f-salary-currency",
        "f-keyword",
        "f-exclude",
    ):
        assert page.evaluate(f"Boolean(document.getElementById({json.dumps(control)}))"), control


def test_a_filter_value_is_spelled_the_way_it_is_written(page: Chrome, server: str) -> None:
    """A chip that misspells the value the person clicked is worse than a raw code.

    Found by clicking `BR` in a real browser, not by a test: the chip read
    "Country (as posted): Br". `humanLabel` sentence-cases a SCREAMING_SNAKE
    vocabulary, which is right for `NOT_STATED` and wrong for every code --
    `US` became "Us" and `USD` became "Usd". `vocabLabel` already existed for
    exactly this and the panel was not using it.

    Asserted on the rendered text of both the facet row and the chip, because
    they are two code paths and only one of them was wrong.
    """
    open_list(page, server, "?country=BR&salary_period=MONTH")
    page.wait_for("document.querySelector('.chipbar .chip__text')", message="the chips")

    chips = page.evaluate(
        "Array.from(document.querySelectorAll('.chipbar .chip__text')).map((n) => n.textContent)"
    )
    joined = " · ".join(str(chip) for chip in chips)
    assert "BR" in joined, joined
    assert "Br" not in joined.replace("BR", ""), f"a country code was sentence-cased: {joined}"
    # A period IS a word and must not be shouted back.
    assert "Month" in joined, joined


def test_a_signal_facet_uses_the_name_the_cards_use(page: Chrome, server: str) -> None:
    """One vocabulary, not two.

    The technology facet counts lexicon IDs, and the configuration already
    knows `hubspot_platform` is called "HubSpot platform ownership" -- which is
    exactly what the cards print. The panel was sentence-casing the id, so it
    offered "Hubspot platform" and "Ipaas" beside cards saying something else.
    """
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list")
    page.evaluate(
        "(() => { const g = Array.from(document.querySelectorAll('.facet__summary'))"
        "  .find((n) => n.textContent.includes('Tools named in the posting'));"
        " if (g) g.parentElement.open = true; return true; })()"
    )
    page.wait_for(
        "document.querySelectorAll('input[data-facet=\"technology\"]').length > 0",
        message="the technology group",
    )
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('input[data-facet=\"technology\"]'))"
        ".map((n) => n.closest('.facet__row').querySelector('.facet__name').textContent)"
    )
    assert labels, "the technology facet is empty"
    # The ids would render as "Ipaas" / "Api integration"; the labels do not.
    for label in labels:
        assert "_" not in str(label), f"a raw signal id reached the panel: {label}"
    on_cards = str(page.evaluate("document.querySelector('.card').innerText"))
    shared = [label for label in labels if str(label) in on_cards]
    assert shared, f"no facet label matches any card tag: {labels}"


def test_a_facet_chip_narrows_the_list_to_the_count_it_printed(page: Chrome, server: str) -> None:
    """The number beside a checkbox is a promise about what clicking it does.

    Checked against the rendered rows rather than against the header, because
    the header and the list disagreeing is the older defect and this is the
    newer one: a facet counted over a different population than the filter
    selects.
    """
    open_list(page, server, "?group_duplicates=0")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the list")
    _panel_open(page)
    page.wait_for(
        "document.querySelectorAll('input[data-facet=\"worksite\"]').length > 0",
        message="the workplace group",
    )
    first = "document.querySelector('input[data-facet=\"worksite\"]')"
    value = str(page.evaluate(f"{first}.dataset.value"))
    promised = int(
        page.evaluate(
            f"Number({first}.closest('.facet__row').querySelector('.facet__n').textContent)"
        )
    )
    click(page, first)
    wait_for_count(page, promised, f"after choosing workplace {value}")


def test_the_new_filters_survive_the_url_and_the_back_button(page: Chrome, server: str) -> None:
    """They are ordinary store keys, so this is really a test that nobody gave
    them a second mechanism. A filter held outside the store would not appear
    in the URL and would not come back."""
    open_list(page, server, "?region=LATAM&worksite=REMOTE&min_salary=1000&salary_currency=BRL")
    page.wait_for(f"{RENDERED_COUNT} >= 0", message="the filtered list")
    _panel_open(page)
    assert page.evaluate("document.getElementById('f-min-salary').value") == "1000"
    assert page.evaluate("document.getElementById('f-salary-currency').value") == "BRL"
    checked = page.evaluate(
        "Array.from(document.querySelectorAll('input[data-facet]:checked'))"
        ".map((n) => `${n.dataset.facet}=${n.dataset.value}`).sort()"
    )
    assert "region=LATAM" in checked
    assert "worksite=REMOTE" in checked


def test_a_keyword_becomes_a_removable_tag_and_filters(page: Chrome, server: str) -> None:
    """Enter is the confirmation: a phrase you can see in the list is applied."""
    open_list(page, server, "?group_duplicates=0")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "before the keyword")
    _panel_open(page)

    set_value(page, "document.getElementById('f-keyword')", DESCRIPTION_ONLY_TERM, "input")
    page.evaluate(
        "(() => { const i = document.getElementById('f-keyword');"
        " i.dispatchEvent(new KeyboardEvent('keydown', {key: 'Enter', bubbles: true}));"
        " return true; })()"
    )
    page.wait_for(
        f"{RENDERED_COUNT} > 0 && {RENDERED_COUNT} < {DEMO_VISIBLE_COUNT}",
        message="the keyword to narrow the list",
    )
    narrowed = int(page.evaluate(RENDERED_COUNT))

    tag = "document.querySelector('.phrases .chip--tag')"
    assert DESCRIPTION_ONLY_TERM in str(page.evaluate(f"{tag}.textContent"))

    click(page, f"{tag}.querySelector('.chip__x')")
    wait_for_count(page, DEMO_VISIBLE_COUNT, "after removing the keyword tag")
    assert narrowed < DEMO_VISIBLE_COUNT


def test_clear_all_removes_the_new_filters_too(page: Chrome, server: str) -> None:
    """`clearedFilters` rebuilds from DEFAULTS, so this is really a test that
    every new key was added there rather than only to the panel."""
    open_list(
        page,
        server,
        "?region=LATAM&worksite=REMOTE&seniority=LEAD&latam_only=1&keyword=integration",
    )
    page.wait_for(f"{RENDERED_COUNT} >= 0", message="the filtered list")
    click(page, "document.querySelector('.chipbar .chip--clear')")
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "after clearing every filter")
    assert page.evaluate("window.location.search") in ("", "?")


def test_a_minimum_salary_without_a_currency_says_so_instead_of_lying(
    page: Chrome, server: str
) -> None:
    """The server refuses it, and the interface must show that refusal rather
    than an empty list. Nothing here converts between currencies, so a bare
    figure would compare amounts that are not comparable."""
    open_list(page, server, "?min_salary=100000")
    page.wait_for(
        "document.querySelector('.state__msg') || document.querySelector('.flash')",
        message="the refusal to be shown",
    )
    shown = str(page.evaluate("document.body.innerText")).lower()
    assert "currency" in shown, shown[:400]


# =========================================================================
# The two-column layout
# =========================================================================


def test_the_first_posting_is_above_the_fold_in_every_view(page: Chrome, server: str) -> None:
    """A list whose first row is a thousand pixels down is an empty pane.

    `.layout` is a two-column grid, and the three disclosures in the rail used
    to be its direct children. Grid auto-placement fills row by row, so adding
    the retrieval and preference panels put Filters at (1,1), Retrieve at
    (1,2), Preferences at (2,1) -- and pushed `.results` into row 2, which
    starts below the sticky rail's full 100vh. At 1440x960 the first card sat
    at **y = 1084**: the header said seventeen roles and the reader saw
    nothing, in all three views at once.

    Nothing failed. `wait_for_count` passed, the DOM held every row, the
    console was clean, and thirteen screenshots were written and committed
    showing an empty results pane -- which is how this was found at all. So the
    assertion is deliberately about GEOMETRY rather than about the DOM: a row
    existing and a row being on screen are different claims, and only the
    second one is what a list view promises.

    Asserted for Cards and Table because each renders its own first row and the
    defect was in the container they share. Kanban is left out for a reason
    that is about the fixture rather than the layout: on the demo corpus no
    posting has been tracked yet, so the board correctly shows its empty state
    and has no first row to measure.
    """
    for view, query in (("cards", ""), ("table", "?view=table")):
        open_list(page, server, query)
        page.wait_for(f"{RENDERED_COUNT} > 0", message=f"the first row of {view}")
        top = page.evaluate(
            "(() => { const n = document.querySelector('[data-job-id]');"
            " return n.getBoundingClientRect().top; })()"
        )
        assert top < page.evaluate("window.innerHeight"), (
            f"in {view} the first posting starts at y={top}, below a "
            f"{page.evaluate('window.innerHeight')}px viewport -- the list "
            "opens on an empty pane"
        )


def test_the_rail_holds_every_sidebar_panel_so_a_new_one_cannot_move_the_results(
    page: Chrome, server: str
) -> None:
    """The structural half of the fix, stated so it survives the next panel.

    Placing the results by hand -- `grid-row: 1 / -1` -- is only half of it.
    The other half is that column 1 contains ONE element however many panels go
    inside it, so a fourth disclosure is added to the rail rather than to the
    grid, and cannot repeat the placement accident that caused this.
    """
    open_list(page, server)
    stray = page.evaluate(
        "(() => Array.from(document.querySelector('.layout').children)"
        " .filter((n) => !n.classList.contains('rail') && n.tagName !== 'MAIN')"
        " .map((n) => `${n.tagName}.${n.className}`))()"
    )
    assert stray == [], f"a panel was added to the grid instead of the rail: {stray}"
    # ONE panel, and that is the Workspace V2 correction rather than a loss.
    # The rail held four: filters, retrieval, preferences and the source
    # matrix. Only the first narrows the list beside it; the other three are
    # about the product and moved to the Settings destination, where they are
    # reachable from every page instead of from inside one.
    #
    # The STRUCTURAL claim above is what this test is really for and it is
    # unchanged: column 1 holds one element however many panels go into it, so
    # a new panel is added to the rail rather than to the grid and cannot
    # repeat the placement accident that caused this.
    # The filters live INSIDE the results column now, above the list, and the
    # rail they used to occupy is gone. The structural claim survives the
    # move and is what this still asserts: the filters are one element in one
    # place, so a panel added to them cannot displace the results.
    panels = page.evaluate(
        "document.querySelectorAll('#results > #filterpanel #filters-host').length"
    )
    assert panels == 1, f"the filter panel is not above the results: {panels}"
    moved = page.evaluate(
        "['prefs-host', 'sources-host', 'retr-host']"
        ".every((id) => document.getElementById(id)"
        " && document.getElementById(id).closest('#page-settings') !== null)"
    )
    assert moved, "a panel that left the rail did not land in Settings"


# =========================================================================
# The dense table
# =========================================================================


def test_the_table_scrolls_sideways_in_a_viewport_you_can_see_the_bar_in(
    page: Chrome, server: str
) -> None:
    """A scrollbar below the fold is the same as no scrollbar.

    The table is wider than any sensible window -- eighteen columns -- so the
    columns past Technologies are reachable ONLY by horizontal scrolling. That
    made the height cap load-bearing: without it the table is as tall as the
    page, its horizontal bar sits under the fold, and half the columns are
    effectively invisible.
    """
    open_list(page, server, "?view=table")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the table body")

    box = page.evaluate(
        "(() => { const s = document.querySelector('.tablescroll');"
        " return { scrollWidth: s.scrollWidth, clientWidth: s.clientWidth,"
        " clientHeight: s.clientHeight, viewport: window.innerHeight }; })()"
    )
    assert box["scrollWidth"] > box["clientWidth"], "nothing to scroll to"
    assert box["clientHeight"] < box["viewport"], (
        "the scroll viewport is taller than the window, so its horizontal bar is below the fold"
    )

    # And the LAST COLUMN actually becomes visible, which is the claim.
    #
    # This compared `scrollLeft + clientWidth` against `scrollWidth` once, with
    # a two-pixel tolerance, and it broke the day the row count changed:
    # `scrollWidth` includes the container's right padding and the maximum
    # `scrollLeft` does not, so the two differ by the padding and the gap moves
    # whenever column widths do. It was measuring the box rather than the
    # columns. Asking whether the last header is on screen is the thing the
    # test is named for and does not care about padding.
    reached = page.evaluate(
        "(() => { const s = document.querySelector('.tablescroll');"
        " s.scrollLeft = s.scrollWidth;"
        # `thead th`, not every `th`: the body uses row headers too, so a bare
        # `th` selector picks a cell in the first column and measures the
        # opposite edge from the one this test is about.
        " const headers = s.querySelectorAll('thead th');"
        " if (!headers.length) return false;"
        " const last = headers[headers.length - 1].getBoundingClientRect();"
        " const box = s.getBoundingClientRect();"
        " return last.right <= box.right + 1 && last.left >= box.left; })()"
    )
    assert reached, "the rightmost column is still off screen after scrolling to the end"


def test_clicking_a_table_row_opens_the_job_but_a_control_does_not(
    page: Chrome, server: str
) -> None:
    """Same rule as Cards and Kanban: the surface opens, the controls do not.

    Hitting a 24-pixel title link in a row you have already read is not a
    reasonable ask, and the row is the object the eye is on.
    """
    open_list(page, server, "?view=table")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the table body")

    # A control first: the status select must not open the drawer behind it.
    click(page, "document.querySelector('.jobs tbody tr select.select--status')")
    assert page.evaluate(
        "!document.querySelector('.drawer') || document.querySelector('.drawer').hidden === true"
    ), "a row control opened the drawer behind itself"

    # Then a plain cell.
    click(page, "document.querySelector('.jobs tbody tr td.col--num')")
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden",
        message="the drawer opened from the row body",
    )


def test_the_table_shows_numbers_not_bars(page: Chrome, server: str) -> None:
    """§11 applies to every view, not only the cards.

    The table carried the same two-track readout at a twentieth of the size --
    a 40-pixel bar under each number, in two adjacent columns, inviting exactly
    the length comparison match and confidence do not support.
    """
    open_list(page, server, "?view=table")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the table body")

    assert page.evaluate("document.querySelectorAll('.mini__track').length") == 0, (
        "a progress track survives in the table"
    )
    headers = str(
        page.evaluate(
            "Array.from(document.querySelectorAll('.jobs thead th'))"
            ".map((n) => n.textContent.trim()).join('|')"
        )
    )
    # "Posting detail", not "Confidence" and not a bare "Detail". The table
    # alone used to say Confidence, which was a third vocabulary for two
    # numbers; and "Detail 77%" beside "Match 80%" reads as a second grade for
    # the job when it is not a grade at all. It is how much the POSTING said.
    assert "Search Fit" in headers and "Posting completeness" in headers, headers
    assert "Confidence" not in headers, "the table still calls the second number Confidence"
    assert "Fit" not in headers.split("|") and "Read" not in headers.split("|"), (
        "the table still uses the old Fit/Read vocabulary"
    )


def test_a_posting_the_search_screened_out_is_not_called_a_rejection(
    page: Chrome, server: str
) -> None:
    """The CRITICAL an independent UX review found, and the test that missed it.

    Two different facts shared one badge:

      `blockers`         the EMPLOYER states a requirement this person does
                         not meet, with a quote behind it
      `screening_state`  the SEARCH decided this is not the kind of work that
                         was asked for

    Measured on the demo corpus: three postings carry `screening_state:
    BLOCKED` with ZERO blockers and `eligibility_status: UNRESOLVED`. All
    three showed a red strip reading "Blocked: the posting rules you out" on
    the default screen. Nobody had ruled anybody out; one of them is a
    quota-carrying sales role, which is simply not the work.

    Meanwhile the three postings that genuinely do rule this person out -- a
    US residence requirement with no sponsorship, a security clearance, fifty
    percent travel -- are hidden by the eligibility default and said nothing.

    The badge was on exactly the wrong three cards, and it read as an employer
    rejecting somebody when their own search had set the posting aside. That
    is invariant 6 broken in the one direction that costs a person something.
    """
    open_list(page, server, f"{SHOW_EVERYTHING}&min_score=0")
    wait_for_count(page, DEMO_GROUPED_COUNT, "before inspecting the screened-out postings")

    found = page.evaluate(
        """(() => {
          const out = { gated: 0, offTarget: 0, both: 0, wrong: [] };
          for (const card of document.querySelectorAll('.card')) {
            const gate = card.querySelector('.card__blocked');
            const off = card.querySelector('.card__offtarget');
            if (gate) out.gated += 1;
            if (off) out.offTarget += 1;
            if (gate && off) out.both += 1;
            // A card marked off-target must never also claim the posting
            // rules this person out, which is the defect itself.
            const claimsAGate = /rules you out|requirement you do not meet|not eligible/i;
            if (off && claimsAGate.test(off.textContent)) {
              out.wrong.push(card.querySelector('.card__title').textContent);
            }
          }
          return JSON.stringify(out);
        })()"""
    )
    counts = json.loads(str(found))

    assert counts["offTarget"] > 0, (
        "the demo corpus no longer contains a screened-out posting, so this proves nothing"
    )
    assert counts["gated"] > 0, "no posting shows a failed gate, so this proves nothing"
    assert counts["both"] == 0, "a card claims both at once"
    assert counts["wrong"] == [], counts["wrong"]

    # And the off-target card says WHY, because unlike a failed gate the
    # reason is short and is the whole of the news.
    said = str(page.evaluate("document.querySelector('.card__offtarget').textContent")).lower()
    assert "not the work you asked for" in said, said
    assert "_" not in said, f"a raw identifier reached the card: {said}"


def test_the_header_says_which_population_is_on_screen(page: Chrome, server: str) -> None:
    """The mode work reached no pixel, and the header regressed to blank.

    The server started sending `runtime` and `db_name`; the header still read
    the removed `db_path`, so it rendered an empty span and no banner existed
    anywhere in the interface. A guarantee the interface never states is a
    guarantee the person cannot rely on -- and two commit messages claimed
    this banner was already the evidence that personal and demo stay apart.
    """
    open_list(page, server)
    page.wait_for(
        "Boolean(document.querySelector('.health__mode'))",
        message="the runtime banner",
    )
    # A short tag on the status card, with the full sentence as its title.
    banner = str(page.evaluate("document.querySelector('.health__mode').textContent"))
    assert banner.strip() == "Demo", banner
    title = str(page.evaluate("document.querySelector('.health__mode').title"))
    assert title.startswith("Demo data"), title

    # And the database is named, never shown as a path.
    named = str(page.evaluate("document.querySelector('.health__db').textContent"))
    assert named.strip(), "the header names no database"
    assert "/" not in named and "\\" not in named, f"the header is showing a path: {named!r}"


def test_a_sort_the_interface_offers_does_not_break_the_list(page: Chrome, server: str) -> None:
    """Picking "Posted date" used to 400 and empty the list."""
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before sorting")

    set_value(page, "document.getElementById('sort')", "posted", "change")
    page.wait_for(
        f"{RENDERED_COUNT} > 0",
        message="the list to survive sorting by posted date",
    )


def test_the_retrieval_panel_shows_the_funnel_and_refuses_a_demo_run(
    page: Chrome, server: str
) -> None:
    """§6's interface half, tested where starting a run is safe.

    The browser fixture is a DEMO database, and retrieval against one is
    refused by design -- the demo corpus is invented, and putting real
    postings in it is the mixing the runtime modes exist to prevent. So this
    asserts the two things that do not need a network: the funnel renders from
    real counts, and pressing the button surfaces the refusal rather than
    failing silently.
    """
    open_list(page, server)
    open_settings(page)
    page.wait_for(
        "document.querySelectorAll('.retr__stage').length > 0",
        message="the funnel to render",
    )

    stages = page.evaluate(
        "Array.from(document.querySelectorAll('.retr__stage-label'))"
        ".map((n) => n.textContent.trim())"
    )
    assert "Fetched" in stages and "Recommended" in stages, stages

    # The funnel must narrow: fetched is the widest, recommended the narrowest.
    counts = page.evaluate(
        "Array.from(document.querySelectorAll('.retr__stage-count'))"
        ".map((n) => Number(n.textContent))"
    )
    assert counts[0] >= counts[-1], f"the funnel does not narrow: {counts}"

    click(page, "document.querySelector('.retr__start')")
    page.wait_for(
        "Boolean(document.querySelector('.retr__error'))",
        message="the demo-mode refusal to be shown to the person",
    )
    message = str(page.evaluate("document.querySelector('.retr__error').textContent"))
    assert "personal" in message.lower(), message

    # And the refused run left the corpus alone. Asserted after the message is
    # read, because reloading rebuilds the panel and takes the refusal with it:
    # a refusal that had already written something would be worse than none.
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "after the refused retrieval")


# =========================================================================
# the eligibility default: what a person is shown, and what they can ask for
# =========================================================================


def forget_eligibility_choice(page: Chrome, server: str) -> None:
    """Start from the DEFAULT, whatever an earlier test left behind.

    TWO kinds of leftovers, and both had to be dealt with.

    `localStorage` survives between tests, because the `page` fixture is
    session-scoped and the eligibility choice is deliberately remembered there.
    That is what this clears.

    The other kind cannot be cleared from the browser at all: the session
    DATABASE is mutated by the suite, and a posting an earlier test moved to
    INTERVIEW is a TRACKED posting, which the eligibility default exempts by
    design. A test about the default would then be reading a state another test
    created. That is why every test below takes `pristine_server` instead,
    which is seeded fresh, and which exists for exactly this reason.
    """
    page.navigate(server)
    page.evaluate("window.localStorage.removeItem('careerAgent.includeIneligible.v1')")


def test_a_posting_that_rules_you_out_is_hidden_until_you_ask_for_it(
    page: Chrome, pristine_server: str
) -> None:
    """The default, the disclosure and the way back, in one pass.

    Three of the nineteen demo postings state a requirement this candidate does
    not meet. They are hidden, the list says how many and why in words, and one
    button brings them back.
    """
    forget_eligibility_choice(page, pristine_server)
    open_list(page, pristine_server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before checking the hidden notice")

    # The ruled-out posting is not on screen.
    #
    # `Boolean(...)`, not `=== null`: `Array.prototype.find` returns
    # `undefined` when nothing matches, so `=== null` is false whether the
    # posting is there or not, and the assertion would have been decorative.
    assert not page.evaluate(f"Boolean({node_for(BLOCKED_TITLE)})"), (
        "a posting whose gate failed is on screen by default"
    )

    notice = str(page.evaluate("document.getElementById('hiddennotice').innerText"))
    assert str(DEMO_HIDDEN_COUNT) in notice, f"the notice does not say how many: {notice!r}"
    # And every OTHER narrowing gets its own sentence in the same place. A
    # sentence each rather than one number, because "an employer states a
    # requirement you do not meet", "nobody could tell" and "your search set
    # this work aside" are different facts, and merging them is a defect this
    # product already shipped once.
    assert str(DEMO_UNRESOLVED_COUNT) in notice
    assert "never said where the employer hires" in notice.lower(), notice
    assert "hidden" in notice.lower()
    # **The off-target sentence is PRESENT SINCE demo-021, and its absence used
    # to be the reporting gap this suite recorded rather than papered over.**
    #
    # Each number promises "how many MORE you would see if you ticked THIS
    # box". Every off-target posting in this corpus used to be unresolved or
    # ineligible as well, so its own box revealed nothing and its sentence
    # correctly said nothing -- a true number with nothing to demonstrate.
    #
    # demo-021 is the case that changed it: an Administrative Assistant role
    # open worldwide, so the geography gate PASSES and nothing an employer
    # wrote rules this candidate out. The only thing setting it aside is a
    # systems search whose required signals do not fire on administration,
    # which is exactly the career changer's situation.
    #
    # THE REPORTING GAP ITSELF IS UNCHANGED and is still not papered over: a
    # posting held by TWO narrowings is mentioned by neither, and the honest
    # fix is a fourth sentence rather than widening what any of these numbers
    # claims to mean.
    assert "different kind of work" in notice.lower(), (
        "the off-target sentence vanished; demo-021 is the posting it is about"
    )
    assert "nothing is wrong with it" in notice.lower(), notice
    # It has to say WHY, in words a person can act on, and not in an enum.
    # "Rules you out" was the first attempt and reads as a judgement about the
    # person rather than a line in a posting; naming the KIND of requirement
    # is what a review asked for, and what a person can check themselves.
    assert "requirement you do not meet" in notice.lower(), notice
    assert "work permit" in notice.lower(), notice
    # And the button does not scold. "Show them anyway" is what you say to
    # somebody being unreasonable, and wanting to see a job that asks for a
    # permit you are in the middle of getting is not unreasonable.
    assert "anyway" not in notice.lower(), notice
    assert "VERIFIED_NOT_ELIGIBLE" not in notice

    # The FIRST button is the eligibility one, and clicking it must reveal only
    # the three it named -- not the postings the other two notices are about.
    click(page, "document.querySelectorAll('.hidden__show')[0]")
    wait_for_count(
        page,
        DEMO_VISIBLE_GROUPED_COUNT + DEMO_HIDDEN_COUNT,
        "after asking to see the ones that rule you out",
    )
    assert page.evaluate(f"Boolean({node_for(BLOCKED_TITLE)})")

    # The notice does not retire. It CHANGES, and it carries the way back.
    #
    # It used to disappear, and the way back out lived on a chip in the filter
    # row. That was wrong twice over: the chip row is for things that NARROW
    # the list, and "Clear all filters" deliberately never touches this key,
    # so pressing Clear left the chip behind and the counter announced "1
    # filter active" about a button that had just been pressed.
    shown = str(page.evaluate("document.getElementById('hiddennotice').innerText"))
    assert page.evaluate("document.getElementById('hiddennotice').hidden === false")
    assert "showing jobs that state a requirement" in shown.lower(), shown
    assert "hide them again" in shown.lower(), shown

    # And the row of filter chips does not claim this as one of them.
    chips = str(page.evaluate("document.getElementById('chipbar').innerText")).lower()
    assert "eligibility conflict" not in chips, chips

    # Pressing it puts things back.
    click(page, "document.querySelector('.hidden__show')")
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "after hiding them again")
    assert not page.evaluate(f"Boolean({node_for(BLOCKED_TITLE)})")


def test_silence_about_eligibility_is_a_narrowing_and_never_a_verdict(
    page: Chrome, pristine_server: str
) -> None:
    """**This test used to assert the opposite, and the reversal was
    deliberate.**

    It said silence is never hidden, because hiding a missing sentence turns
    it into a rejection. The V1.5 eligibility correction hid it anyway, and
    the reason is a measurement: on the owner's corpus 12,634 postings never
    say where the employer hires and 354 are known to be open to her, so a
    list that shows silence first is a list nobody can use.

    What may never happen is silence being called a REFUSAL. Three things
    still hold, and they are what this test asserts now:

      * it is set aside by a narrowing that NAMES ITSELF and offers a control,
        in words that say nothing rules her out and nothing confirms her;
      * asking for those postings by their own filter beats the default;
      * once shown, each reads "Did not say" and never "rules you out".
    """
    forget_eligibility_choice(page, pristine_server)
    open_list(page, pristine_server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before reading the notice")

    notice = str(page.evaluate("document.getElementById('hiddennotice').innerText")).lower()
    assert "never said where the employer hires" in notice, notice
    assert "nothing rules you out" in notice, notice

    # And they are still THERE, one click away, saying what they actually say.
    open_list(page, pristine_server, "?include_unresolved=1")
    labels = page.evaluate(
        "Array.from(document.querySelectorAll('[data-job-id] .badge--eligibility'))"
        ".map((n) => n.innerText.trim())"
    )
    assert labels, "no eligibility badge was rendered at all"
    assert any("did not say" in str(text).lower() for text in labels), labels
    assert not any("rules you out" in str(text).lower() for text in labels), (
        "a posting that never said anything was reported as ruling her out"
    )


def test_the_choice_to_see_them_survives_a_reload(page: Chrome, pristine_server: str) -> None:
    """It is a preference, so it is remembered rather than re-asked."""
    forget_eligibility_choice(page, pristine_server)
    open_list(page, pristine_server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "before turning the default off")
    click(page, "document.querySelectorAll('.hidden__show')[0]")
    revealed = DEMO_VISIBLE_GROUPED_COUNT + DEMO_HIDDEN_COUNT
    wait_for_count(page, revealed, "after asking to see them")

    page.evaluate("window.location.href = window.location.pathname")
    wait_for_count(page, revealed, "after reloading with no query string")
    assert page.evaluate(
        "window.localStorage.getItem('careerAgent.includeIneligible.v1') === '1'"
    ), "the choice was not remembered"

    # Put it back, so the session-scoped browser fixture hands the next test the
    # default rather than this test's leftovers.
    page.evaluate("window.localStorage.removeItem('careerAgent.includeIneligible.v1')")


def test_saving_a_ruled_out_job_moves_it_to_saved_and_not_to_the_list(
    page: Chrome, pristine_server: str
) -> None:
    """You already decided about it. The interface keeps it -- somewhere true.

    THIS TEST REPLACES ONE THAT ASSERTED THE OPPOSITE, and the reason is the
    same shape as the applied-date reversal above: the old rule was right about
    the promise and wrong about where it is kept.

    "A job she decided about must never vanish under her" was implemented as
    "it stays in the default list", and the default list is the one place that
    promise cannot be kept honestly. That list says: these are jobs you could
    take. A posting a gate has verified as ruling her out is not one, and
    saving it does not change what the employer wrote. The V1.5 acceptance
    audit found exactly that on the real corpus.

    So both halves are asserted here, in the browser, on the same posting:
    GONE from the recommendations, PRESENT in Saved, carrying its restriction.

    Driven through the API rather than the interface, because the posting is
    not on screen to click: that is the state this test exists to describe.
    """
    open_list(page, pristine_server, SHOW_EVERYTHING)
    wait_for_count(page, DEMO_GROUPED_COUNT, "with the ruled-out postings shown")
    job_id = str(page.evaluate(f"{node_for(BLOCKED_TITLE)}.dataset.jobId"))

    page.evaluate(
        f"""fetch('/api/jobs/{job_id}/saved', {{
            method: 'PATCH',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{saved: true}}),
        }}).then(() => 'done')"""
    )
    page.wait_for(
        f"fetch('/api/jobs/{job_id}').then(r => r.json()).then(j => j.saved) || true",
        message="the save to land",
    )

    forget_eligibility_choice(page, pristine_server)

    # GONE from the recommendations. The count is the unchanged default, which
    # is the assertion that would fail if the posting had merely moved rather
    # than left.
    open_list(page, pristine_server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the default recommendations")
    assert page.evaluate(f"{node_for(BLOCKED_TITLE)} === undefined"), (
        "saving a posting an employer ruled her out of put it back on the "
        "list of jobs this product is recommending"
    )

    # PRESENT in Saved, which is the view that holds her decisions.
    open_list(page, pristine_server, "?saved_only=1&group_duplicates=0")
    page.wait_for(
        f"Boolean({node_for(BLOCKED_TITLE)})",
        message="the saved posting in Saved",
    )

    # ...and still carrying what rules her out, ON THE ROW. A restriction she
    # has to open something to see is a restriction she can miss.
    badge = page.evaluate(
        f"{node_for(BLOCKED_TITLE)}.querySelector('.badge--eligibility')"
        f" ? {node_for(BLOCKED_TITLE)}.querySelector('.badge--eligibility').textContent.trim()"
        " : ''"
    )
    assert str(badge).strip(), "the saved posting carries no eligibility badge at all"

    # Put it back for the next test.
    page.evaluate(
        f"""fetch('/api/jobs/{job_id}/saved', {{
            method: 'PATCH',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify({{saved: false}}),
        }})"""
    )


# =========================================================================
# Accessibility, measured in the browser
# =========================================================================


def test_the_heading_outline_never_skips_a_level(page: Chrome, server: str) -> None:
    """One h1, and no jump from h2 to h4.

    Somebody using a screen reader navigates a page by its headings, the way
    a sighted reader navigates by scanning. A skipped level tells them a
    section is nested inside something that is not there.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards")

    levels = page.evaluate(
        "Array.from(document.querySelectorAll('h1,h2,h3,h4,h5,h6'))"
        ".filter((n) => n.offsetParent !== null)"
        ".map((n) => [Number(n.tagName[1]), n.textContent.trim().slice(0, 40)])"
    )
    assert levels, "the page renders no headings at all"

    first = [text for level, text in levels if level == 1]
    assert len(first) == 1, f"expected exactly one h1, got {first}"
    assert levels[0][0] == 1, f"the first heading is an h{levels[0][0]}: {levels[0][1]!r}"

    previous = 1
    for level, text in levels:
        assert level <= previous + 1, f"the outline jumps from h{previous} to h{level} at {text!r}"
        previous = level


def test_tabbing_through_the_page_always_shows_where_you_are(page: Chrome, server: str) -> None:
    """A keyboard user has to be able to see where they are.

    Focus styling used to be declared per control, so a control added later
    got whatever the browser felt like, and on a coloured background the
    default ring is often invisible. There is one rule now, on
    `:where(a, button, input, select, textarea, summary, [tabindex])`.

    Driven with REAL Tab presses, and that matters. `:focus-visible` is a
    heuristic about how focus ARRIVED: a programmatic `element.focus()` does
    not satisfy it on a button, so the first version of this test focused
    forty controls in a loop and reported every one of them as ringless. The
    stylesheet was fine; the test was measuring something else.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards")
    page.evaluate("(() => { document.body.focus(); return true; })()")

    seen: list[str] = []
    ringless: list[str] = []
    for _ in range(45):
        page.press("Tab")
        described = page.evaluate(
            """(() => {
              const node = document.activeElement;
              if (!node || node === document.body) return null;
              const style = getComputedStyle(node);
              return JSON.stringify({
                name: `${node.tagName}.${node.className || '(none)'}`,
                width: parseFloat(style.outlineWidth) || 0,
                style: style.outlineStyle,
                visible: node.matches(':focus-visible'),
              });
            })()"""
        )
        if not described:
            continue
        import json as _json

        found = _json.loads(str(described))
        seen.append(found["name"])
        if found["visible"] and not (found["width"] >= 1 and found["style"] != "none"):
            ringless.append(f"{found['name']} outline {found['width']}px {found['style']}")

    assert len(seen) >= 15, f"Tab reached only {len(seen)} controls: {seen}"
    assert ringless == [], f"focused by keyboard with no visible ring: {ringless}"


def test_no_interactive_target_is_smaller_than_a_fingertip(
    page: Chrome, pristine_server: str
) -> None:
    """WCAG 2.2 asks for 24 by 24 CSS pixels on a pointer target.

    Checked at phone width, which is where it matters and where the
    interface has the least room to give.
    """
    page.set_viewport(*MOBILE, mobile=True)
    try:
        open_list(page, pristine_server)
        wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards on a phone")
        small = page.evaluate(
            """(() => {
                  const out = [];
                  for (const node of document.querySelectorAll('button, a[href], summary')) {
                    if (node.offsetParent === null) continue;
                    const box = node.getBoundingClientRect();
                    if (box.width < 24 || box.height < 24) {
                      out.push(`${node.className || node.tagName}` +
                        ` ${Math.round(box.width)}x${Math.round(box.height)}` +
                        ` "${node.textContent.trim().slice(0, 20)}"`);
                    }
                  }
                  return out;
            })()"""
        )
        assert small == [], f"targets under 24px at 390px wide: {small}"
    finally:
        page.set_viewport(*DESKTOP)


def test_nothing_scrolls_sideways_on_a_phone(page: Chrome, pristine_server: str) -> None:
    """A horizontal scrollbar on a phone means something did not give way.

    The table is exempt: it scrolls inside its own wrapper by design, and
    that wrapper draws an edge shadow so the affordance is visible. What
    must never scroll is the PAGE.
    """
    page.set_viewport(*MOBILE, mobile=True)
    try:
        open_list(page, pristine_server)
        wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards on a phone")
        overflow = page.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert int(overflow) <= 0, f"the page scrolls {overflow}px sideways at 390px"
    finally:
        page.set_viewport(*DESKTOP)


def test_the_star_is_still_for_somebody_who_asked_for_less_motion(
    page: Chrome, server: str
) -> None:
    """`prefers-reduced-motion` is answered by asking for a different FILE.

    Not by pausing an animation, and the distinction is the point: a file
    that decides for itself has already been downloaded and parsed before it
    can decide. So this sets the preference and checks which file the page
    actually asks for.

    It is also the only assertion that the sprite exists at all. It is
    generated by `scripts/make_star.py` from sixteen rows of characters, and
    a generator whose output nobody loads is a generator that can quietly
    stop producing anything.
    """
    page.set_reduced_motion(True)
    try:
        open_list(page, server, "?search=zzzznothingmatches")
        # Wait for the FILE, not for the element. The element is in the DOM the
        # moment the empty state renders and carries its `src` attribute
        # immediately, but `complete` is about a fetch that has not happened
        # yet -- so reading it here asked a question about an event nobody had
        # waited for, and the answer depended on how busy the machine was.
        # This is the same assertion, awaited: a sprite that never loads still
        # fails, loudly, on the timeout.
        page.wait_for(
            """(() => {
              const img = document.querySelector('.state__star');
              return !!img && img.complete && img.naturalWidth > 0;
            })()""",
            message="the still star to load",
        )
        still = page.evaluate(
            """(() => {
              const img = document.querySelector('.state__star');
              return JSON.stringify({
                src: img.getAttribute('src'),
                loaded: img.complete && img.naturalWidth > 0,
                hidden: img.getAttribute('aria-hidden'),
                alt: img.getAttribute('alt'),
              });
            })()"""
        )
        found = json.loads(str(still))
        assert found["src"] == "./star.svg", found
        assert found["loaded"] is True, "the still star did not load"
        # ...which the wait above has already established. Kept because it is
        # what the next reader checks for, and it costs nothing.
        # Decoration. A screen reader reading out "star" to somebody waiting
        # to hear why their list is empty is reading them a drawing.
        assert found["hidden"] == "true"
        assert found["alt"] == ""
    finally:
        page.set_reduced_motion(False)

    # And without the preference, the twinkling one.
    open_list(page, server, "?search=zzzznothingmatches")
    page.wait_for(
        """(() => {
          const img = document.querySelector('.state__star');
          return !!img && img.complete && img.naturalWidth > 0;
        })()""",
        message="the twinkling star to load",
    )
    moving = str(page.evaluate("document.querySelector('.state__star').getAttribute('src')"))
    assert moving == "./star-twinkle.svg", moving

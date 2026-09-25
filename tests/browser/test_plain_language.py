"""Nothing on screen may be a name this system chose for itself.

A person looking for a job should never read `VERIFIED_NOT_ELIGIBLE`,
`title_class`, `config_version` or `data_confidence`. Those are all correct
names for what they are, they are all in the code, and none of them is English.

This is asserted against the RENDERED TEXT of the whole interface rather than
against the source, because the source is full of those words legitimately:
they are column names, enum members and store keys. What matters is which of
them reach a pixel.

Every view, every panel and both themes, on one page load each.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import DEMO_VISIBLE_GROUPED_COUNT
from tests.browser.test_browser_acceptance import (
    RENDERED_COUNT,
    click,
    node_for,
    open_list,
    wait_for_count,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: A word in SCREAMING_SNAKE is always ours: enum members, coverage values,
#: eligibility statuses, gate names. There is no English word shaped like that.
SHOUTED = re.compile(r"\b[A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+\b")

#: `snake_case` with a lowercase start is a field name, a store key or a
#: column. Two or more segments, because a hyphenated English compound is not
#: this shape and a single lowercase word obviously is not either.
SNAKE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")

#: Implementation vocabulary that is ordinary English and still wrong here.
#: Each one names a thing inside the program rather than a thing in the world.
#:
#: Matched on WORD BOUNDARIES, which the first version of this list did not do:
#: `repo` reported a hit on every "Reporting and operational analytics" on the
#: page, which is a configured label and an ordinary English word.
INTERNAL = (
    "enum",
    "payload",
    "deserialise",
    "serialise",
    "regex",
    "traceback",
    "sqlite",
    "adapter",
    "fingerprint",
    "membership",
    "corpus",
    "repo",
    "repository",
    "config",
    "boolean",
    "null",
    "undefined",
)

#: Where a shouted or snaked word is DATA rather than vocabulary.
#:
#: A currency is ISO 4217 and `BRL` is its name. A country is ISO 3166 and the
#: filter rail now prints "Brazil", but the drawer still quotes what a board
#: printed, which is the board's words and not ours to rewrite.
ALLOWED_SHOUTED = frozenset(
    {
        "BRL",
        "USD",
        "EUR",
        "GBP",
        "CLT",
        "PJ",
        "LATAM",
        "EMEA",
        "APAC",
        "REST",
        "API",
        "APIs",
        "SQL",
        "JSON",
        "CTRL",
        "GTM",
        "HTTP",
        "URL",
        "AI",
        "WCAG",
    }
)


def visible_text(page: Chrome) -> str:
    """Everything a sighted reader can actually read, plus every label a
    screen reader would announce. Both, because a raw enum hidden in an
    `aria-label` is still a raw enum being read to somebody."""
    return str(
        page.evaluate(
            """(() => {
              const parts = [document.body.innerText];
              for (const node of document.querySelectorAll(
                '[aria-label], [title], [placeholder]'
              )) {
                parts.push(node.getAttribute('aria-label') || '');
                parts.push(node.getAttribute('title') || '');
                parts.push(node.getAttribute('placeholder') || '');
              }
              return parts.join('\\n');
            })()"""
        )
    )


def offenders(text: str) -> list[str]:
    found = []
    for word in SHOUTED.findall(text):
        if word not in ALLOWED_SHOUTED:
            found.append(word)
    for word in SNAKE.findall(text):
        # A quoted line from a posting is somebody else's text. `.quote` and
        # the description are excluded by the caller, not here.
        found.append(word)
    lowered = text.lower()
    for word in INTERNAL:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", lowered):
            found.append(word)
    return sorted(set(found))


def configured_labels() -> list[str]:
    """The names the CONFIGURATION gives its own signals.

    These are the person's words, not the system's. "Custom objects / schema
    extension" and "Data synchronisation and migration" are how somebody
    described the work they want, and `schema` and `migration` are ordinary
    vocabulary in that trade. Read out of the shipped example rather than
    listed here, so a lexicon edit cannot make this test wrong.
    """
    import yaml

    example = REPO_ROOT / "config" / "search.worked-example.yaml"
    parsed = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    return [
        str(entry.get("label", ""))
        for entry in (parsed.get("lexicon") or {}).values()
        if isinstance(entry, dict) and entry.get("label")
    ]


def ours(text: str) -> str:
    """`text` with every configured label removed.

    The boundary this whole file is about: a word the SYSTEM chose is a defect
    on screen, and a word the CONFIGURATION chose is the product working.
    """
    for label in sorted(configured_labels(), key=len, reverse=True):
        text = text.replace(label, " ")
    return text


def strip_third_party(page: Chrome) -> None:
    """Remove everything an employer wrote before reading the page.

    A posting can contain anything, including `snake_case` and SHOUTING, and
    none of it is this project's language to fix. ADR-0002 makes those quotes
    verbatim on purpose. What is left after this is OURS.
    """
    page.evaluate(
        """(() => {
          const written = '.quote, .d-desc, .card__title, .cell__title, .card__company,'
            + ' .d-company, .cell__company, .card__group-places, .d-loc, .chips--tech,'
            + ' .facet__name, .src__techbody, .kcard__title, .kcard__company';
          for (const node of document.querySelectorAll(written)) node.remove();
          return true;
        })()"""
    )


@pytest.mark.parametrize("view", ["cards", "table", "kanban"])
def test_no_view_prints_a_name_this_system_chose(page: Chrome, server: str, view: str) -> None:
    """The three views, each read after the employer's own words are removed."""
    if view == "kanban":
        # The board paints into `.kanban`, not into the list, so `open_list`
        # would wait for a first paint that never comes.
        page.navigate(f"{server}/?view=kanban")
        page.wait_for(
            "document.querySelector('.kanban')"
            " && !document.querySelector('.kanban').hasAttribute('aria-busy')"
            " && document.querySelectorAll('.kcol').length === 7",
            message="the board",
        )
    else:
        open_list(page, server, f"?view={view}")
        page.wait_for(f"{RENDERED_COUNT} > 0", message=f"the {view} view")

    strip_third_party(page)
    found = offenders(ours(visible_text(page)))
    assert found == [], f"{view} prints: {found}"


def test_the_filter_rail_is_written_in_plain_language(page: Chrome, server: str) -> None:
    """Every section open, every facet group expanded, then read.

    The rail is the surface most likely to leak: its controls are named after
    store keys, its facet buckets are enum members, and both are one careless
    fallback away from the screen.
    """
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards")
    page.evaluate(
        """(() => {
          for (const node of document.querySelectorAll('#filters-host details')) {
            node.open = true;
          }
          if (document.getElementById('filterpanel').hidden) {
            document.getElementById('rail-toggle').click();
          }
          return true;
        })()"""
    )
    page.wait_for(
        "document.querySelectorAll('#filters-host .facet__row').length > 0",
        message="the facet rows",
    )

    strip_third_party(page)
    rail = str(page.evaluate("document.getElementById('filters-host').innerText"))
    labels = str(
        page.evaluate(
            "Array.from(document.querySelectorAll('#filters-host [aria-label], "
            "#filters-host [title]'))"
            ".map((n) => `${n.getAttribute('aria-label') || ''} ${n.getAttribute('title') || ''}`)"
            ".join('\\n')"
        )
    )
    found = offenders(f"{rail}\n{labels}")
    assert found == [], f"the filter rail prints: {found}"


def test_the_drawer_explains_itself_without_internal_words(page: Chrome, server: str) -> None:
    """Both tabs, including the advanced disclosure, which is where the raw
    scoring vocabulary is ALLOWED to live and still may not be jargon."""
    open_list(page, server)
    wait_for_count(page, DEMO_VISIBLE_GROUPED_COUNT, "the cards")
    click(page, node_for("Business Systems Analyst"))
    page.wait_for(
        "document.querySelector('.drawer') && !document.querySelector('.drawer').hidden"
        " && document.querySelectorAll('.d-sec').length > 0",
        message="the drawer",
    )

    tabs = str(
        page.evaluate(
            "Array.from(document.querySelectorAll('.drawer__tab'))"
            ".map((n) => n.textContent.trim()).join('|')"
        )
    )
    assert tabs == "Job details|Why this fits your search|Prepare to apply", tabs

    click(page, "document.getElementById('drawer-tab-why')")
    page.wait_for(
        "document.getElementById('drawer-panel-why').hidden === false",
        message="the reasoning tab",
    )
    page.evaluate(
        "(() => { for (const d of document.querySelectorAll('.drawer details')) d.open = true;"
        " return true; })()"
    )

    strip_third_party(page)
    found = offenders(ours(str(page.evaluate("document.querySelector('.drawer').innerText"))))
    assert found == [], f"the drawer prints: {found}"


def test_the_sources_panel_says_why_without_saying_robots_txt(page: Chrome, server: str) -> None:
    """The panel leads in plain words. The exact wording is one click down,
    and this reads only what is on the surface."""
    open_list(page, server)
    page.evaluate(
        "(() => { const link ="
        " document.querySelector('.topnav__link[data-page=\"settings\"]');"
        " link.click(); return true; })()"
    )
    page.wait_for(
        "document.querySelectorAll('#sources-host .src__row').length > 0",
        message="the source rows",
    )

    surface = str(page.evaluate("document.getElementById('sources-host').innerText"))
    for banned in ("robots.txt", "endpoint", "404", "403", "pagination", "first-party"):
        assert banned not in surface.lower(), f"the sources panel surfaces {banned!r}"

    found = offenders(ours(surface))
    assert found == [], f"the sources panel prints: {found}"


@pytest.mark.parametrize("view", ["cards", "table", "kanban"])
def test_every_view_renders_without_falling_into_its_error_state(
    page: Chrome, server: str, view: str
) -> None:
    """A smoke test, and it exists because one was missing.

    Renaming a variable in `table.js` left `blocked` referenced in `cell()`
    and undefined. The whole table view died with "The list of jobs could not
    be loaded. blocked is not defined", and the frontend gate passed: it runs
    `node --check`, which is syntax only, and finding a free variable inside a
    function needs a scope analyser this project has no dependency for.

    A browser test did catch it, but by accident, because it happened to open
    the table on its way to checking something else. This asks the question
    directly, of every view, so the next one cannot depend on luck.
    """
    if view == "kanban":
        page.navigate(f"{server}/?view=kanban")
        page.wait_for(
            "document.querySelector('.kanban')"
            " && !document.querySelector('.kanban').hasAttribute('aria-busy')",
            message="the board",
        )
    else:
        open_list(page, server, f"?view={view}")
        page.wait_for(f"{RENDERED_COUNT} > 0", message=f"the {view} view")

    broken = page.evaluate(
        r"""(() => {
          const state = document.querySelector('.state--error');
          return state ? state.innerText.replace(/\s+/g, ' ').trim() : '';
        })()"""
    )
    assert broken == "", f"{view} fell into its error state: {broken}"

    errors = [
        entry
        for entry in page.console_errors()
        # A favicon the demo does not ship is not a defect in the view.
        if "favicon" not in str(entry.get("text", "")).lower()
    ]
    assert errors == [], f"{view} logged: {errors}"


def test_eligibility_reads_the_same_in_the_card_and_in_the_filter(
    page: Chrome, server: str
) -> None:
    """`badges.js` says it above `eligibilityWords`: one vocabulary, both views.

    The rail was a THIRD view and never got the function. A card said "Did not
    say" and the filter beside it offered "Unresolved 6"; a card said "Rules
    you out" and the filter said "Verified not eligible". Nobody can connect
    those, and the whole point of the eligibility work is that a person reads
    one answer rather than three spellings of it.
    """
    open_list(page, server, "?include_ineligible=1")
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the cards")
    page.evaluate(
        """(() => {
          if (document.getElementById('filterpanel').hidden) {
            document.getElementById('rail-toggle').click();
          }
          for (const node of document.querySelectorAll('#filters-host details')) node.open = true;
          return true;
        })()"""
    )
    page.wait_for(
        "document.querySelectorAll(\"[data-facet='eligibility']\").length > 0",
        message="the eligibility facet",
    )

    rail = page.evaluate(
        """Array.from(document.querySelectorAll('[data-facet="eligibility"]'))
             .map((n) => n.closest('.facet__row'))
             .map((row) => row.querySelector('.facet__name').textContent.trim())"""
    )
    cards = page.evaluate(
        """Array.from(document.querySelectorAll('.badge--eligibility'))
             .map((n) => n.textContent.replace(/^Can you take it/i, '').trim())"""
    )

    assert rail, "the eligibility facet rendered no values"
    assert cards, "no card carries an eligibility badge"

    # Every word the rail offers has to be a word a card can show.
    normalise = lambda text: str(text).lower().strip()  # noqa: E731
    card_words = {normalise(c) for c in cards}
    for value in rail:
        assert any(normalise(value) in word or word in normalise(value) for word in card_words), (
            f"the rail offers {value!r}, and no card says anything like it: {sorted(card_words)}"
        )

    # And specifically: the enum spellings are gone from the rail.
    joined = " ".join(str(v).lower() for v in rail)
    for enum_word in ("verified", "unresolved", "eligible"):
        assert enum_word not in joined, f"the rail still spells {enum_word!r}: {rail}"


def test_which_population_is_on_screen_needs_no_click(page: Chrome, server: str) -> None:
    """The one status that may never be behind a disclosure.

    The whole point of the runtime-mode work is that somebody can never
    mistake invented postings for their own. Moving the status line behind a
    summary briefly took the "Demo data" banner with it, which is the single
    thing on that line that has to be visible without being asked for.
    """
    open_list(page, server)
    page.wait_for("document.querySelector('.health__mode')", message="the mode banner")

    visible = page.evaluate(
        """(() => {
          const node = document.querySelector('.health__mode');
          const inDisclosure = Boolean(node.closest('details'));
          const style = getComputedStyle(node);
          return JSON.stringify({
            text: node.textContent.trim(),
            inDisclosure,
            painted: style.display !== 'none' && node.offsetParent !== null,
          });
        })()"""
    )
    found = json.loads(str(visible))
    assert found["painted"] is True, found
    assert found["inDisclosure"] is False, "the mode banner is hidden behind a disclosure"
    assert found["text"], "the mode banner is empty"


def test_the_status_line_says_how_much_is_worth_opening_it_for(page: Chrome, server: str) -> None:
    """A summary that always reads the same is a summary nobody opens.

    The header used to print a database hash, "search is running slowly" on a
    database seeded a minute earlier, and a sentence about a local model, all
    before the first job. It is behind a disclosure now, and the disclosure
    says how many of those are something to act on.
    """
    open_list(page, server)
    page.wait_for(
        "document.getElementById('health-summary').textContent.trim().length > 0",
        message="the status summary",
    )
    summary = str(page.evaluate("document.getElementById('health-summary').textContent")).strip()
    assert summary, "the summary is empty"
    assert "#" not in summary, f"a database reference reached the summary: {summary}"

    # And the detail is still there, in full: the status line's tooltip.
    detail = str(page.evaluate("document.getElementById('health').textContent"))
    assert "jobs" in detail.lower(), detail
    title = str(page.evaluate("document.getElementById('health-summary').title"))
    degraded = page.evaluate(
        "document.getElementById('health-summary').classList.contains('is-attention')"
    )
    assert not degraded or "jobs" in title.lower(), title


def test_the_board_and_the_dropdown_use_the_same_words(page: Chrome, server: str) -> None:
    """Somebody who marks a job Shortlisted must find a column with that name.

    The board called it "Interested" and the dropdown called it "Shortlisted",
    so a person set a status and then went looking for a column that was not
    there. The comment above `STATUS_WORDS` even noted that "the board already
    made this correction once"; what it had not done was make it in one place.

    Only the statuses that have a column of their own are checked. `CLOSED`
    carries three -- rejected, withdrawn and archived -- deliberately, because
    a board with a column for each of those is a board about endings.
    """
    # The dropdown is read from CARDS, where every job has one. The board
    # narrows to what is being tracked, and on a pristine demo that is
    # nothing, so a board card is not guaranteed to exist.
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the cards")
    options = [
        str(name).strip().lower()
        for name in page.evaluate(
            "Array.from(document.querySelectorAll('.card select.status-tag option'))"
            ".map((n) => n.textContent)"
        )
    ]

    page.navigate(f"{server}/?view=kanban")
    page.wait_for("document.querySelectorAll('.kcol').length === 7", message="the board")
    columns = [
        str(name).strip().lower()
        for name in page.evaluate(
            "Array.from(document.querySelectorAll('.kcol__head'))"
            ".map((n) => n.textContent.replace(/[0-9]+$/, '').trim())"
        )
    ]
    assert columns, "the board rendered no column headings"
    assert options, "no card on the board offers a status dropdown"

    # Every column except the one that collapses three endings has to be a
    # word the dropdown also offers.
    unmatched = [
        name
        for name in columns
        if name != "closed" and not any(name in option for option in options)
    ]
    assert unmatched == [], (
        f"the board names {unmatched} and the dropdown offers {sorted(set(options))}"
    )

    assert "shortlisted" not in " ".join(options), (
        "the dropdown still says Shortlisted while the board says Interested"
    )

"""The Discover cleanup: toolbar search, card source line, table, notices.

Four claims, each small and each easy to regress without anybody noticing:

* the list can be searched from the toolbar, locally, together with every
  other filter, and the search can be cleared;
* a card names its source and prints "Posted" only beside a date the
  employer published, never beside the date this app collected it;
* the table has no bulk "move the ticked jobs" control and no row checkboxes
  that only fed it;
* a dismissed hidden-jobs notice stays dismissed by its KIND, whatever its
  count becomes, and speaks only on Discover.

Synthetic rows and a pristine database throughout; nothing here reads local
configuration or a personal database.
"""

from __future__ import annotations

import json
import time

from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import (
    A_REAL_CARD,
    RENDERED_COUNT,
    RENDERED_IDS,
    SHOW_EVERYTHING,
    open_list,
    set_value,
)
from tests.browser.test_card_density import _row, inject

SEARCH = "document.getElementById('f-search')"
NOTICE = "document.getElementById('hiddennotice')"
BY_YOU = f"{NOTICE}.querySelector('[data-notice=\"include_user_hidden\"]')"

# -- A: the toolbar search ----------------------------------------------------


def test_the_toolbar_search_is_labelled_local_combined_and_clearable(
    page: Chrome, server: str
) -> None:
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")

    # In the Discover toolbar, with a real label rather than a placeholder.
    assert page.evaluate(f"Boolean({SEARCH}.closest('.topbar__controls'))")
    label = page.evaluate("document.querySelector('label[for=\"f-search\"]').textContent")
    assert str(label).strip(), "the search box has no accessible label"
    assert page.evaluate(f"{SEARCH}.closest('[role=search]') !== null")

    before = int(page.evaluate(RENDERED_COUNT))
    # Every request the page makes from here on, recorded as it is made:
    # resource timing lands only when a response finishes, which can be after
    # the skeleton has already emptied the list.
    page.evaluate(
        "(() => { window.__asked = []; const real = window.fetch;"
        " window.fetch = (url, opts) => { window.__asked.push(String(new URL(url, location.href)));"
        " return real(url, opts); }; return true; })()"
    )
    set_value(page, SEARCH, "zzzzznothingmatches", "input")
    page.wait_for(
        "window.__asked.some((u) => u.includes('search=zzzzznothingmatches'))"
        f" && {RENDERED_COUNT} === 0 && Boolean(document.querySelector('.state__head'))",
        message="the search to empty the list",
    )

    # Answered by the local server, and COMBINED with the filters already in
    # force rather than replacing them.
    urls = [str(url) for url in page.evaluate("window.__asked")]
    origin = str(page.evaluate("location.origin"))
    assert all(url.startswith(origin) for url in urls), urls
    searched = [url for url in urls if "/api/jobs?" in url and "search=zzzzznothingmatches" in url]
    assert searched, urls
    assert all("include_ineligible=" in url for url in searched), searched

    # Cleared by its own button, and the list comes back.
    page.wait_for("!document.getElementById('f-search-clear').hidden", message="the clear button")
    page.evaluate("document.getElementById('f-search-clear').click()")
    page.wait_for(f"{RENDERED_COUNT} === {before}", message="the list after clearing")
    assert page.evaluate(f"{SEARCH}.value") == ""
    assert "search=" not in str(page.evaluate("location.search"))
    assert page.console_errors() == []


# -- B: the source line on a card ---------------------------------------------


def test_a_card_says_posted_only_beside_a_published_date(
    page: Chrome, server: str, demo_company_names: frozenset[str]
) -> None:
    companies = sorted(demo_company_names)
    open_list(page, server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    inject(
        page,
        [
            _row("syn-dated", companies[0], access_method="aggregator_api"),
            _row(
                "syn-undated",
                companies[1],
                access_method="ats_structured",
                posted_at=None,
                freshness="UNKNOWN",
                first_seen_at="2026-09-01T00:00:00+00:00",
            ),
        ],
    )
    footer = '(id) => document.querySelector(`[data-job-id="${id}"] .card__age`).textContent'
    dated = str(page.evaluate(f"({footer})('syn-dated')"))
    undated = str(page.evaluate(f"({footer})('syn-undated')"))

    assert "Posted: 10 Sep 2026" in dated, dated
    assert "Posted" not in undated, undated
    assert "Sep 2026" not in undated, "a collection date was printed as the posting date"
    for text in (dated, undated):
        for plumbing in ("ATS structured", "Aggregator API", "How we read it"):
            assert plumbing not in text, text


# -- D: the table -------------------------------------------------------------


def test_the_table_has_no_bulk_move_and_no_dead_checkboxes(page: Chrome, server: str) -> None:
    open_list(page, server, SHOW_EVERYTHING)
    page.evaluate("document.getElementById('view-table').click()")
    page.wait_for(
        "document.querySelectorAll('table.jobs tbody tr[data-job-id]').length > 0",
        message="the table",
    )
    assert page.evaluate("document.querySelectorAll('.tablebar select').length") == 0
    toolbar = str(page.evaluate("document.querySelector('.tablebar').innerText"))
    assert "Move the ticked" not in toolbar, toolbar
    assert page.evaluate("document.querySelectorAll('table.jobs thead input').length") == 0
    # The one checkbox left on a row is "Applied?", which writes the status.
    labels = page.evaluate(
        "Array.from(document.querySelector('table.jobs tbody tr')"
        ".querySelectorAll('input[type=checkbox]')).map((n) => n.getAttribute('aria-label'))"
    )
    assert all(not str(label).startswith("Select ") for label in labels), labels
    assert len(labels) <= 1, labels
    assert page.console_errors() == []


# -- F: the notices -----------------------------------------------------------


def _hide_first_card(page: Chrome) -> str:
    first = str(page.evaluate(f"{RENDERED_IDS}[0]"))
    page.evaluate("document.querySelector('.cards .card .card__hide').click()")
    page.wait_for(
        f"{RENDERED_COUNT} > 0 && !{RENDERED_IDS}.includes({json.dumps(first)})",
        message="the hidden card to leave the list",
    )
    return first


def test_a_dismissed_notice_stays_dismissed_by_kind_and_only_speaks_on_discover(
    page: Chrome, pristine_server: str
) -> None:
    open_list(page, pristine_server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")

    _hide_first_card(page)
    page.wait_for(f"Boolean({BY_YOU})", message="the notice about what she hid")
    page.evaluate(f"{BY_YOU}.querySelector('.hidden__dismiss').click()")
    page.wait_for(f"!{BY_YOU}", message="the notice to go quiet")

    # A NEW COUNT IS THE SAME NOTICE. Hiding a second posting changes "1" to
    # "2"; the dismissed category must not come back with the new number.
    _hide_first_card(page)
    time.sleep(0.5)
    assert not page.evaluate(f"Boolean({BY_YOU})"), "a changed count resurrected the notice"
    stored = page.evaluate("localStorage.getItem('careerAgent.notices.v1')")
    assert "include_user_hidden" in json.loads(str(stored)), stored

    # And after a reload, which is what "persist" means.
    page.reload()
    page.wait_for(f"Boolean({A_REAL_CARD})", message="the list after a reload")
    time.sleep(0.5)
    assert not page.evaluate(f"Boolean({BY_YOU})"), "the dismissal did not survive a reload"

    # Any other kind still offers its way in, and "Show them too" still works.
    other = '.hidden__row:not([data-notice="include_user_hidden"]) .hidden__show'
    reveal = f"{NOTICE}.querySelector({json.dumps(other)})"
    if page.evaluate(f"Boolean({reveal})"):
        kind = str(page.evaluate(f"{reveal}.closest('.hidden__row').dataset.notice"))
        page.evaluate(f"{reveal}.click()")
        page.wait_for(
            f"location.search.includes({json.dumps(kind + '=')})",
            message="the reveal to widen the list",
        )

    # Applications shares the container; the notices do not follow it there.
    page.evaluate("document.querySelector('.topnav__link[data-page=\"applications\"]').click()")
    page.wait_for("document.body.dataset.page === 'applications'", message="Applications")
    page.wait_for(
        f"{NOTICE}.hidden || getComputedStyle({NOTICE}).display === 'none'",
        message="the Discover notices to stay on Discover",
    )
    assert page.console_errors() == []

"""The collapsed card is short, and stays short on the corpus's worst rows.

THE REGRESSION THIS GUARDS
--------------------------
Measured 2026-09-11 on Discover Jobs at 1440x900 with sixty cards of real
data: every card was 462px tall, identical, and one row was on screen. Two
causes, and neither was a single rule:

* `.cards` carried `grid-auto-rows: 1fr`. On IMPLICIT rows `1fr` means every
  row shares the tallest card's height -- the tallest on the PAGE, not in its
  row -- and `margin-top: auto` on the footer turned the difference into a
  blank band above every footer (57px on the card measured).
* The content itself: badges wrapping to two lines, a three-row fact table
  with a label column, two rows of tool chips and a three-row footer. The
  real corpus adds a failed-gate line, a group line and six-country lists
  that wrapped to three lines.

The demo corpus never showed it. Its titles are short, its locations are
one place, and nothing in it is a Workable multi-country fold or a
metadata-only lead. So half of this file renders SYNTHETIC rows through the
real renderer: the same `renderCards`, the same stylesheet, fed through a
patched `fetch` so the page cannot tell the difference. The demo database is
not extended for it -- twenty-one postings is a number a dozen other tests
count, and a corpus row must be reconstructible from a provider payload,
which a layout fixture has no business being.

WHAT IS NOT ASSERTED
--------------------
Nothing about matching, eligibility or scores: the synthetic rows carry
whatever the layout needs and mean nothing. And no pixel-exact height: the
ceiling is generous, because the point is "several cards on a screen", not
"this many pixels".
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import (
    A_REAL_CARD,
    RENDERED_COUNT,
    SHOW_EVERYTHING,
    open_list,
)

#: The tallest a collapsed card may be, at a 300-370px column. The measured
#: regression was 462; the compact card measures 268-337 on real data with a
#: two-line title, a failed-gate line and a group line all present.
CARD_HEIGHT_CEILING_PX = 350

#: At 1440x900 the toolbar and the notices leave roughly 430px for cards, so
#: two rows must at least START on the screen and three cards fit whole.
DESKTOP = (1440, 900)
MOBILE = (390, 844)

#: Every card's own sections, top to bottom, so a test can say WHICH one grew.
SECTIONS = (
    "Array.from(card.children)"
    ".map((n) => [n.className.split(' ')[0], n.getBoundingClientRect().height])"
)


def heights(page: Chrome) -> list[int]:
    raw = page.evaluate(
        "Array.from(document.querySelectorAll('.card:not(.card--skeleton)'))"
        ".map((n) => Math.round(n.getBoundingClientRect().height))"
    )
    return [int(h) for h in raw]


def sections_of(page: Chrome, index: int) -> list[tuple[str, float]]:
    raw = page.evaluate(
        "(() => { const card = document.querySelectorAll('.card:not(.card--skeleton)')"
        f"[{index}]; return {SECTIONS}; }})()"
    )
    return [(str(name), float(h)) for name, h in raw]


def fully_visible(page: Chrome) -> int:
    return int(
        page.evaluate(
            "Array.from(document.querySelectorAll('.card:not(.card--skeleton)'))"
            ".filter((n) => { const r = n.getBoundingClientRect();"
            " return r.top >= 0 && r.bottom <= window.innerHeight; }).length"
        )
    )


# -- synthetic rows ----------------------------------------------------------


def _tool(label: str, prominence: str, signal_id: str) -> dict[str, str]:
    return {"label": label, "prominence": prominence, "signal_id": signal_id}


def _row(job_id: str, company: str, **overrides: object) -> dict[str, object]:
    """One list item, shaped like `/api/jobs` shapes them, meaning nothing."""
    base: dict[str, object] = {
        "job_id": job_id,
        "title": "Business Systems Analyst",
        "company_name": company,
        "company_slug": company.lower().replace(" ", "-"),
        "url": "https://example.invalid/apply",
        "provider": "greenhouse",
        "access_method": "ats_structured",
        "location_raw": "Remote, Americas",
        "work_model": "REMOTE",
        "seniority": "SENIOR",
        "seniority_stated": True,
        "employment_type": "FULL_TIME",
        "salary": None,
        "match_score": 61,
        "fit_band": "GOOD",
        "data_confidence": 80,
        "confidence_band": "HIGH",
        "eligibility_status": "VERIFIED_ELIGIBLE",
        "blockers": [],
        "screening_state": "NOT_BLOCKED",
        "content_completeness": "FULL_CONTENT",
        "domestic_context": "UNRESOLVED",
        "duplicate_count": 1,
        "sibling_locations": [],
        "technologies": [
            _tool("REST APIs, webhooks, integration engineering", "PRIMARY", "api"),
            _tool("iPaaS / orchestration tooling", "PRIMARY", "ipaas"),
            _tool("Scripting and data transformation", "SECONDARY", "scripting"),
        ],
        "freshness": "FRESH",
        "posted_at": "2026-09-10T00:00:00+00:00",
        "application_status": "DISCOVERED",
        "saved": False,
        "hidden": False,
        "scored": True,
    }
    base.update(overrides)
    return base


SIX_COUNTRIES = "AR / BR / CO / MX / CL / PA"
TWO_LONG_CITIES = (
    "Shenzhen, Guangdong Province, CN / Kuala Lumpur, Federal Territory of Kuala Lumpur, MY"
)
FIVE_US_OFFICES = "Austin, TX, US / US / Las Vegas, NV, US / Atlanta, GA, US / Denver, CO, US"
LONG_TITLE = (
    "Senior Revenue Operations and Marketing Technology Systems Analyst, "
    "Enterprise Integrations and Automation Platform (Remote, Americas, EMEA and APAC)"
)


def synthetic_rows(companies: list[str]) -> list[dict[str, object]]:
    """The eight shapes the brief names, one card each.

    Company names come from the demo corpus so the screenshot guard, which
    refuses any employer it does not know to be invented, accepts the frame.
    """
    c = companies
    return [
        _row("syn-short", c[0], title="GTM Engineer", location_raw="Berlin"),
        _row("syn-long-title", c[1], title=LONG_TITLE),
        _row(
            "syn-workable-multi",
            c[2],
            provider="workable",
            access_method="aggregator_api",
            location_raw=SIX_COUNTRIES,
            duplicate_count=6,
            sibling_locations=[SIX_COUNTRIES, "BR", "MX", "CO", "CL", "PA"],
        ),
        _row(
            "syn-jobgether-lead",
            c[3],
            provider="jobgether",
            access_method="aggregator_api",
            content_completeness="METADATA_ONLY",
            location_raw="Anywhere",
            technologies=[],
            data_confidence=30,
            confidence_band="LOW",
        ),
        _row(
            "syn-dynamitejobs",
            c[4],
            provider="dynamitejobs",
            access_method="sitemap_jsonld",
            location_raw="Remote (Latin America)",
        ),
        _row(
            "syn-long-salary",
            c[5],
            salary={"currency": "USD", "min": 1000000, "max": 1500000, "period": "YEAR"},
        ),
        _row(
            "syn-unresolved",
            c[6],
            eligibility_status="UNRESOLVED",
            domestic_context="LIKELY_US_DOMESTIC",
            location_raw=TWO_LONG_CITIES,
        ),
        _row(
            "syn-blocked",
            c[7],
            eligibility_status="VERIFIED_NOT_ELIGIBLE",
            location_raw=FIVE_US_OFFICES,
            blockers=[{"gate": "geography", "reason": "stated", "quote": None}],
        ),
    ]


def inject(page: Chrome, rows: list[dict[str, object]]) -> None:
    """Make the next list fetch answer with `rows`, then cause one.

    The store re-fetches only when the QUERY changes, so after patching
    `fetch` the sort direction is flipped: a real control, a real request,
    the real renderer, and only the bytes on the wire are invented.
    """
    payload = json.dumps(
        {"total": len(rows), "offset": 0, "limit": 60, "items": rows, "facets": {}}
    )
    page.evaluate(
        "(() => { const payload = " + payload + ";"
        " const real = window.fetch;"
        " window.fetch = (url, opts) => String(url).includes('/api/jobs?')"
        "   ? Promise.resolve(new Response(JSON.stringify(payload),"
        "       {status: 200, headers: {'Content-Type': 'application/json'}}))"
        "   : real(url, opts);"
        " document.getElementById('direction').click();"
        " return true; })()"
    )
    page.wait_for(
        f"{RENDERED_COUNT} === {len(rows)}",
        message=f"{len(rows)} synthetic cards through the real renderer",
    )


@pytest.fixture
def demo_companies(demo_company_names: frozenset[str]) -> list[str]:
    names = sorted(demo_company_names)
    assert len(names) >= 8, "the synthetic rows borrow eight invented employers"
    return names


# -- the demo corpus, as it is ------------------------------------------------


def test_no_card_is_stretched_to_match_another(page: Chrome, server: str) -> None:
    """Content-based height: the grid does not equalise rows and the footer
    is not pushed down to fill a band the content never asked for."""
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")

    grid = page.evaluate("getComputedStyle(document.querySelector('.cards')).gridAutoRows")
    assert str(grid) == "auto", f"implicit rows are still equalised: {grid}"
    align = page.evaluate("getComputedStyle(document.querySelector('.cards')).alignItems")
    assert str(align) == "start", align
    footer_gap = page.evaluate(
        f"getComputedStyle({A_REAL_CARD}.querySelector('.card__footer')).marginTop"
    )
    assert str(footer_gap) == "0px", f"the footer is still pushed down: {footer_gap}"


def test_several_demo_cards_fit_on_a_desktop_screen(page: Chrome, server: str) -> None:
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")

    tall = [h for h in heights(page) if h > CARD_HEIGHT_CEILING_PX]
    assert not tall, f"cards over {CARD_HEIGHT_CEILING_PX}px: {tall}"
    assert fully_visible(page) >= 3, (
        f"only {fully_visible(page)} whole cards on a 1440x900 screen; heights {heights(page)}"
    )


def test_the_badges_are_one_row_and_the_facts_are_three_lines(page: Chrome, server: str) -> None:
    """The two sections that had doubled. Each one line per fact, the
    labels present for a screen reader and off the screen."""
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")

    badge_tops = page.evaluate(
        f"Array.from({A_REAL_CARD}.querySelectorAll('.badges .badge'))"
        ".map((n) => Math.round(n.getBoundingClientRect().top))"
    )
    # Centred on one row: three badges of three sizes sit within a pixel or
    # two of each other, and a second row would be a full line away.
    assert max(badge_tops) - min(badge_tops) < 8, f"the badges wrapped: tops {badge_tops}"

    labels = page.evaluate(
        f"Array.from({A_REAL_CARD}.querySelectorAll('.card__facts dt'))"
        ".map((n) => [n.textContent, n.getBoundingClientRect().width])"
    )
    assert [label for label, _ in labels] == ["Where", "Contract", "Salary"], labels
    assert all(float(w) <= 1 for _, w in labels), f"a fact label is painted: {labels}"
    facts = page.evaluate(f"getComputedStyle({A_REAL_CARD}.querySelector('.card__facts')).height")
    assert float(str(facts).replace("px", "")) < 80, f"the fact block is {facts} tall"


# -- the corpus's worst rows, invented ----------------------------------------


def test_the_hard_shapes_stay_under_the_ceiling(
    page: Chrome, server: str, demo_companies: list[str]
) -> None:
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    inject(page, synthetic_rows(demo_companies))

    measured = heights(page)
    over = [
        (i, h, sections_of(page, i)) for i, h in enumerate(measured) if h > CARD_HEIGHT_CEILING_PX
    ]
    assert not over, f"synthetic cards over the ceiling: {over}"


def test_a_six_country_list_is_counted_not_hidden(
    page: Chrome, server: str, demo_companies: list[str]
) -> None:
    """`AR / BR / CO / MX +2 more`, the whole list in `title`, and the
    eligibility answer still on the card: nothing about Brazil is hidden."""
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    inject(page, synthetic_rows(demo_companies))

    card = "document.querySelector('[data-job-id=\"syn-workable-multi\"]')"
    shown = str(page.evaluate(f"{card}.querySelector('.fact--place').textContent"))
    full = str(page.evaluate(f"{card}.querySelector('.fact--place').title"))
    assert "+2 more" in shown, shown
    assert "AR / BR / CO / MX" in shown, shown
    assert SIX_COUNTRIES in full, full
    eligibility = str(page.evaluate(f"{card}.querySelector('.badge--eligibility').textContent"))
    assert eligibility.strip(), "the eligibility answer left the card"
    group = str(page.evaluate(f"{card}.querySelector('.card__group').textContent"))
    assert "6 locations" in group, group


def test_a_long_title_is_clamped_and_kept_whole_in_the_tooltip(
    page: Chrome, server: str, demo_companies: list[str]
) -> None:
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    inject(page, synthetic_rows(demo_companies))

    card = "document.querySelector('[data-job-id=\"syn-long-title\"]')"
    title = page.evaluate(
        f"(() => {{ const n = {card}.querySelector('.card__title');"
        " const s = getComputedStyle(n);"
        " return [n.getBoundingClientRect().height, parseFloat(s.lineHeight),"
        "  n.title, n.textContent]; })()"
    )
    height, line, tooltip, text = title
    assert float(height) <= float(line) * 2 + 1, f"the title runs to {height}px at {line}px lines"
    assert tooltip == LONG_TITLE, "the whole title must survive in `title`"
    assert text == LONG_TITLE, "the clamp is visual; the text node keeps every word"


def test_the_notes_and_chips_each_take_one_line(
    page: Chrome, server: str, demo_companies: list[str]
) -> None:
    """A metadata-only lead, a US-domestic hint and a failed gate each add
    ONE line, and the tool chips never start a second row."""
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    inject(page, synthetic_rows(demo_companies))

    for job_id, note in (
        ("syn-jobgether-lead", ".card__partial"),
        ("syn-unresolved", ".card__context"),
        ("syn-blocked", ".card__blocked"),
    ):
        card = f"document.querySelector('[data-job-id=\"{job_id}\"]')"
        assert page.evaluate(f"Boolean({card}.querySelector('{note}'))"), f"{job_id} lost {note}"
        h = page.evaluate(f"{card}.querySelector('{note}').getBoundingClientRect().height")
        # One line of 12px type plus the band's padding is under 34px; a
        # second line is a full 16px away from that.
        assert float(h) < 40, f"{note} on {job_id} is {h}px: it wrapped"
        tooltip = str(page.evaluate(f"{card}.querySelector('{note}').title || ''"))
        text = str(page.evaluate(f"{card}.querySelector('{note}').textContent"))
        if note == ".card__blocked":
            assert "Austin, TX, US" in tooltip, "the clipped reason must survive in `title`"
            assert "Austin, TX, US" in text

    chip_tops = page.evaluate(
        "Array.from(document.querySelectorAll("
        "'[data-job-id=\"syn-long-salary\"] .chips--tech .chip'))"
        ".map((n) => Math.round(n.getBoundingClientRect().top))"
    )
    assert max(chip_tops) - min(chip_tops) < 8, f"the chips wrapped: {chip_tops}"
    more = str(
        page.evaluate(
            "document.querySelector('[data-job-id=\"syn-long-salary\"] .chip--more').textContent"
        )
    )
    assert more == "+1", more


def test_the_card_holds_on_a_phone(page: Chrome, server: str, demo_companies: list[str]) -> None:
    """One column, no horizontal scroll, and the footer's two rows hold."""
    page.set_viewport(*MOBILE, mobile=True)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a card at 390px")
    inject(page, synthetic_rows(demo_companies))

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (
        "the page scrolls sideways on a phone"
    )
    tall = [h for h in heights(page) if h > CARD_HEIGHT_CEILING_PX + 40]
    assert not tall, f"phone cards over the ceiling: {tall}"
    columns = page.evaluate(
        "getComputedStyle(document.querySelector('.cards')).gridTemplateColumns"
    )
    assert len(str(columns).split()) == 1, f"more than one column at 390px: {columns}"
    page.set_viewport(*DESKTOP)


# -- evidence -------------------------------------------------------------------


def test_capture_the_compact_cards(
    page: Chrome,
    server: str,
    demo_companies: list[str],
    capture: Callable[[str], Path],
) -> None:
    """Two frames: the demo corpus and the eight hard shapes, both desktop."""
    page.set_viewport(*DESKTOP)
    open_list(page, server, SHOW_EVERYTHING)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    capture("cards-density-desktop")
    inject(page, synthetic_rows(demo_companies))
    capture("cards-density-hard-shapes")
    page.set_viewport(*MOBILE, mobile=True)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="cards at 390px")
    capture("cards-density-mobile")
    page.set_viewport(*DESKTOP)

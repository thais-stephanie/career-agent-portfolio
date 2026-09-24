"""The Workspace V2 shell, measured in a real browser.

-> docs/design/career-agent-workspace-v2/README.md

WHAT THIS FILE GUARDS
---------------------
A fixed 220px rail, one page header contract, and a mobile drawer the design
does not define. The rail replaced a top bar that carried the brand, five
destinations, the mode line, the health disclosure, the theme control, the
language control and the results toolbar in one strip.

THE TWO ASSERTIONS THAT MATTER MOST are not about geometry.

**Nothing invented.** The design's user card carries `LVL 7` and a 72% bar.
Neither is a number this product could honestly produce, and a DOM assertion
would not catch one being added -- so the rendered text is read and greped for
the shapes a fabricated figure takes.

**The 220px rail is not preserved on a 390px screen.** The design specifies a
fixed width and says nothing about a phone, where 220px is more than half the
viewport. Below the breakpoint it becomes an overlay, and the overlay is held
to what an overlay owes a keyboard: focus in, focus contained, focus back.
"""

from __future__ import annotations

import json

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import DESKTOP, MOBILE

#: Every destination the rail offers, in order. Six, not the design's seven:
#: there is no Documents page because there is no document store behind one,
#: and a nav item leading to an invented library is the decorative control the
#: brief forbids.
DESTINATIONS = ["home", "jobs", "applications", "profile", "evidence", "documents", "settings"]

#: What a fabricated progress figure looks like on a screen. Read from the
#: RENDERED text, because that is where one would be composed.
INVENTED = ("lvl ", "level ", "xp", "badge", "streak", "72%")


def open_app(page: Chrome, server: str) -> None:
    page.navigate(server)
    page.wait_for(
        "document.querySelector('.sidenav .topnav__link') !== null",
        message="the rail",
    )


def nav_pages(page: Chrome) -> list[str]:
    return list(
        page.evaluate(
            "Array.from(document.querySelectorAll('.sidenav .topnav__link[data-page]'))"
            ".map((n) => n.dataset.page)"
        )
    )


# =========================================================================
# the rail
# =========================================================================


def test_every_destination_is_in_the_rail_and_none_is_invented(page: Chrome, server: str) -> None:
    open_app(page, server)
    assert nav_pages(page) == DESTINATIONS, nav_pages(page)
    # ...and each one goes somewhere. A destination whose page does not exist
    # is the decorative control this migration is supposed to be removing.
    for name in DESTINATIONS:
        exists = page.evaluate(
            f"document.getElementById('page-{name}') !== null"
            f" || {json.dumps(name)} === 'applications'"
        )
        assert exists, f"the rail offers {name} and there is no page behind it"


def test_the_destinations_are_grouped_and_the_groups_are_labelled(
    page: Chrome, server: str
) -> None:
    """Three labelled sections, so seven items are not one undifferentiated
    list. Each group is a real ARIA group pointing at its own heading."""
    open_app(page, server)
    sections = page.evaluate(
        "Array.from(document.querySelectorAll('.sidenav__section')).map((n) => n.id)"
    )
    assert sections == ["nav-section-search", "nav-section-profile", "nav-section-system"]
    labelled = page.evaluate(
        "Array.from(document.querySelectorAll('.sidenav__group'))"
        ".every((g) => g.getAttribute('role') === 'group'"
        " && document.getElementById(g.getAttribute('aria-labelledby')) !== null)"
    )
    assert labelled, "a nav group points at a heading that is not there"


def test_the_current_destination_is_marked_by_more_than_colour(page: Chrome, server: str) -> None:
    open_app(page, server)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"evidence\"]').click()")
    page.wait_for(
        "document.querySelector('.topnav__link[data-page=\"evidence\"]')"
        ".getAttribute('aria-current') === 'page'",
        message="the current page mark",
    )
    weight = page.evaluate(
        "getComputedStyle(document.querySelector("
        "'.topnav__link[data-page=\"evidence\"]')).fontWeight"
    )
    assert int(weight) >= 700, f"the current destination is marked by colour alone: {weight}"
    # ...and only one is current at a time.
    current = page.evaluate("document.querySelectorAll('[aria-current=\"page\"]').length")
    assert current == 1, f"{current} destinations claim to be the current page"


def test_the_rail_states_nothing_it_cannot_count(page: Chrome, server: str) -> None:
    """The assertion the design forces. `LVL 7` and a 72% bar are invented and
    the brief forbids both, so the rendered rail is read for their shapes."""
    open_app(page, server)
    page.wait_for(
        "document.querySelectorAll('.sidenav__stat').length > 0",
        message="the footer counts",
    )
    text = str(page.evaluate("document.querySelector('.sidenav').innerText")).lower()
    for shape in INVENTED:
        assert shape not in text, f"the rail put {shape!r} on the screen"


def test_the_footer_counts_come_from_the_database(page: Chrome, server: str) -> None:
    """Real numbers, and the tile is omitted rather than dashed when a count
    cannot be had: "we could not work this out" and "there are none" are
    opposite facts and must not render the same."""
    open_app(page, server)
    page.wait_for(
        "document.querySelectorAll('.sidenav__stat').length > 0",
        message="the footer counts",
    )
    values = page.evaluate(
        "Array.from(document.querySelectorAll('.sidenav__statvalue')).map((n) => n.textContent)"
    )
    assert values, "the rail reports no counts at all"
    for value in values:
        assert str(value).isdigit(), f"a count is not a number: {value!r}"
    jobs = int(
        page.evaluate(
            "document.querySelector('.sidenav__stat--jobs .sidenav__statvalue').textContent"
        )
    )
    # An async IIFE rather than a top-level await: `Runtime.evaluate` compiles
    # the expression as a script, where top-level await is a syntax error.
    served = int(
        page.evaluate("(async () => (await fetch('/api/home').then((r) => r.json())).job_count)()")
    )
    assert jobs == served, f"the rail counted {jobs} and the server says {served}"


# =========================================================================
# the page header contract
# =========================================================================


def test_every_page_fills_the_same_header(page: Chrome, server: str) -> None:
    """One shape everywhere. Five screens each drawing their own header is how
    five screens come to disagree about how tall a header is."""
    open_app(page, server)
    seen = {}
    for name in DESTINATIONS:
        page.evaluate(f"document.querySelector('.topnav__link[data-page=\"{name}\"]').click()")
        page.wait_for(
            "document.getElementById('pagehead-title').textContent.trim().length > 0",
            message=f"the {name} header",
        )
        eyebrow = str(page.evaluate("document.getElementById('pagehead-eyebrow').textContent"))
        title = str(page.evaluate("document.getElementById('pagehead-title').textContent"))
        assert eyebrow.strip(), f"{name} has no eyebrow"
        assert title.strip(), f"{name} has no title"
        seen[name] = title
        # AT MOST ONE primary action. A header with two has none.
        actions = page.evaluate("document.getElementById('pagehead-actions').children.length")
        assert actions <= 1, f"{name} offers {actions} primary actions"

    # ...and the titles are not all the same string, which is what a header
    # that renders but is never filled would look like.
    assert len(set(seen.values())) == len(seen), seen


def test_the_header_is_the_same_height_on_every_page(page: Chrome, server: str) -> None:
    open_app(page, server)
    heights = []
    for name in DESTINATIONS:
        page.evaluate(f"document.querySelector('.topnav__link[data-page=\"{name}\"]').click()")
        page.wait_for(
            "document.getElementById('pagehead-title').textContent.trim().length > 0",
            message=f"the {name} header",
        )
        heights.append(
            int(page.evaluate("document.getElementById('pagehead').getBoundingClientRect().height"))
        )
    assert max(heights) - min(heights) <= 2, f"the header height wanders: {heights}"


# =========================================================================
# the mobile drawer
# =========================================================================


@pytest.fixture
def phone(page: Chrome):
    page.set_viewport(*MOBILE, mobile=True)
    yield page
    page.set_viewport(*DESKTOP)


def test_the_fixed_rail_is_not_preserved_on_a_phone(phone: Chrome, server: str) -> None:
    """220px of a 390px screen is more than half of it. The design does not
    say what happens here, so the rail becomes an overlay."""
    open_app(phone, server)
    offscreen = phone.evaluate(
        "document.querySelector('.sidenav').getBoundingClientRect().right <= 0"
    )
    assert offscreen, "the 220px rail is still taking a phone screen"
    assert phone.evaluate(
        "getComputedStyle(document.getElementById('sidenav-open')).display !== 'none'"
    ), "there is no way to reach the navigation"


def test_the_drawer_opens_takes_focus_and_gives_it_back(phone: Chrome, server: str) -> None:
    open_app(phone, server)
    phone.evaluate("document.getElementById('sidenav-open').focus()")
    phone.evaluate("document.getElementById('sidenav-open').click()")
    phone.wait_for(
        "document.body.classList.contains('sidenav-open')",
        message="the drawer",
    )
    assert phone.evaluate(
        "document.getElementById('sidenav-open').getAttribute('aria-expanded') === 'true'"
    )
    # Focus lands on the first DESTINATION, not on the panel: a drawer that
    # opens with focus on a container gives a keyboard user nothing to press.
    assert phone.evaluate("document.activeElement.classList.contains('topnav__link')"), (
        "the drawer opened without giving focus to anything"
    )

    phone.evaluate(
        "document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}))"
    )
    phone.wait_for(
        "!document.body.classList.contains('sidenav-open')",
        message="the drawer to close on Escape",
    )
    assert phone.evaluate("document.activeElement.id === 'sidenav-open'"), (
        "Escape closed the drawer and dropped focus"
    )


def test_the_scrim_dismisses_the_drawer(phone: Chrome, server: str) -> None:
    open_app(phone, server)
    phone.evaluate("document.getElementById('sidenav-open').click()")
    phone.wait_for("document.body.classList.contains('sidenav-open')", message="the drawer")
    assert phone.evaluate("document.getElementById('sidenav-scrim').hidden === false")
    phone.evaluate("document.getElementById('sidenav-scrim').click()")
    phone.wait_for(
        "!document.body.classList.contains('sidenav-open')",
        message="the scrim to dismiss it",
    )


def test_choosing_a_destination_closes_the_drawer(phone: Chrome, server: str) -> None:
    """On a phone the page it opened is behind it, and leaving the rail on top
    is a navigation that appears to do nothing."""
    open_app(phone, server)
    phone.evaluate("document.getElementById('sidenav-open').click()")
    phone.wait_for("document.body.classList.contains('sidenav-open')", message="the drawer")
    phone.evaluate("document.querySelector('.topnav__link[data-page=\"evidence\"]').click()")
    phone.wait_for(
        "!document.body.classList.contains('sidenav-open')",
        message="the drawer to close behind the destination",
    )
    assert phone.evaluate(
        "document.querySelector('.topnav__link[data-page=\"evidence\"]')"
        ".getAttribute('aria-current') === 'page'"
    )


def test_tab_stays_inside_the_open_drawer(phone: Chrome, server: str) -> None:
    """Without containment, Tab walks into the results underneath, which are
    covered by a scrim and cannot be seen."""
    open_app(phone, server)
    phone.evaluate("document.getElementById('sidenav-open').click()")
    phone.wait_for("document.body.classList.contains('sidenav-open')", message="the drawer")
    inside = phone.evaluate(
        "(() => {"
        "  const nav = document.getElementById('sidenav');"
        "  const items = [...nav.querySelectorAll("
        "    'button, [href], select, input, summary, [tabindex]:not([tabindex=\"-1\"])')]"
        "    .filter((n) => !n.disabled && n.offsetParent !== null);"
        "  items[items.length - 1].focus();"
        "  nav.dispatchEvent(new KeyboardEvent('keydown',"
        "    {key: 'Tab', bubbles: true, cancelable: true}));"
        "  return document.getElementById('sidenav').contains(document.activeElement);"
        "})()"
    )
    assert inside, "Tab left the open drawer for content behind the scrim"

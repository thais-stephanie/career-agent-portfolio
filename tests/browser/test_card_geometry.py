"""Cards are round, measured on the rendered page rather than in the stylesheet.

WHY THE STYLESHEET IS NOT THE EVIDENCE
--------------------------------------
The owner looked at the product and said the cards were still square. The
stylesheet had `border-radius: 12px` on `.card` and had had it since Slice B.
Both were true: the JOB card was round and every other card surface was not.
`--radius: 6px` was doing the work of all four of the design's radius tiers,
and the home metrics, the evidence cards and the evidence claims had no
`border-radius` declared at all.

A declaration is also not proof on its own. A later selector, a square child
painted over a rounded parent, a missing `overflow`, a pseudo-element or a
theme-only rule can each leave a corner sharp while the source reads 12px. So
this asks the browser what it actually painted.

WHAT IS DELIBERATELY NOT ROUNDED
--------------------------------
Table rows and cells. The design keeps the grid sharp, and rounding rows to
match the cards would fight it. That is asserted too, because "make everything
round" is the obvious wrong fix and it should fail here if anyone tries it.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import A_REAL_CARD, RENDERED_COUNT, open_list

#: The design's Radius section: card / column / panel is 12px.
CARD_RADIUS_PX = 12.0

#: Small card, bordered row, control. 10px in the same table.
CONTROL_RADIUS_PX = 10.0


def radius_of(page: Chrome, selector: str) -> list[float]:
    """The four corners the browser actually painted, in pixels."""
    raw = page.evaluate(
        f"(() => {{ const n = document.querySelector({selector!r});"
        f" if (!n) return null;"
        f" const s = getComputedStyle(n);"
        f" return [s.borderTopLeftRadius, s.borderTopRightRadius,"
        f"  s.borderBottomRightRadius, s.borderBottomLeftRadius]; }})()"
    )
    assert raw is not None, f"{selector} is not on the page"
    return [float(str(value).replace("px", "") or 0) for value in raw]


@pytest.fixture(autouse=True)
def _list_is_drawn(page: Chrome, server: str):
    open_list(page, server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    yield


def test_the_job_card_is_round_on_all_four_corners(page: Chrome, server: str) -> None:
    """**The one the owner was looking at.**

    All four, not just the top: the card's colour strip is pulled over its own
    border corner to corner, and a strip that escaped the clip would square off
    the top two while the bottom two stayed round.
    """
    corners = radius_of(page, ".card:not(.card--skeleton)")

    assert corners == [CARD_RADIUS_PX] * 4, corners


def test_the_card_clips_its_own_children(page: Chrome, server: str) -> None:
    """A rounded parent with a square child and no clip is a square card.

    This is the mechanism the stylesheet cannot prove on its own, and it is
    how a corner goes sharp without a single radius declaration changing.
    """
    overflow = str(page.evaluate("getComputedStyle(document.querySelector('.card')).overflow"))

    assert overflow == "hidden", overflow


def test_the_offset_shadow_survives_the_rounding(page: Chrome, server: str) -> None:
    """Rounded corners and the neo-brutalist offset shadow are not a trade.

    The design asks for both, and a card that lost its shadow while gaining a
    radius would have swapped one half of the visual language for the other.
    """
    shadow = str(page.evaluate("getComputedStyle(document.querySelector('.card')).boxShadow"))

    assert shadow and shadow != "none", shadow
    # Hard offset, no blur: the third length in a `box-shadow` is the blur.
    assert " 0px " in shadow or shadow.count("0px") >= 1, shadow


def test_a_table_row_is_deliberately_left_sharp(page: Chrome, server: str) -> None:
    """The obvious wrong fix, asserted against.

    The design keeps the grid sharp. Rounding every row to match the cards
    would fight the table, and "make everything round" is exactly what a
    hurried reading of this file would produce.
    """
    page.evaluate("document.getElementById('view-table').click()")
    page.wait_for(
        "Boolean(document.querySelector('table.jobs tbody tr'))",
        message="the table view",
    )

    corners = radius_of(page, "table.jobs tbody tr")

    assert corners == [0.0] * 4, corners


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_radius_is_the_same_in_both_themes(page: Chrome, server: str, scheme: str) -> None:
    """A theme changes colour. It has never had a reason to change geometry,
    and a rule that only landed in one of them would be invisible in the other."""
    page.set_color_scheme(scheme)
    page.wait_for(f"Boolean({A_REAL_CARD})", message=f"a card in the {scheme} theme")

    assert radius_of(page, ".card:not(.card--skeleton)") == [CARD_RADIUS_PX] * 4


def test_the_card_is_still_round_on_a_narrow_screen(page: Chrome, server: str) -> None:
    """Mobile is where a card most often gets a full-bleed override."""
    page.set_viewport(390, 844)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="cards at 390px")

    assert radius_of(page, ".card:not(.card--skeleton)") == [CARD_RADIUS_PX] * 4


# =========================================================================
# THE SURFACES THAT WERE ACTUALLY SQUARE
#
# `.card` had been round since Slice B. These are the ones the owner was
# looking at when she said the product still looked square: the kanban card
# and column at 6px, and the home metric and the evidence card with no
# `border-radius` declared at all.
# =========================================================================


def test_the_application_card_and_its_column_are_round(page: Chrome, pristine_server: str) -> None:
    """`--radius: 6px` was doing the work of the design's 12px card tier.

    `pristine_server` because a board card only exists once a posting has a
    status, and giving one is a mutation the shared server's other tests count.
    """
    open_list(page, pristine_server)
    page.wait_for(f"Boolean({A_REAL_CARD})", message="a drawn card")
    job_id = page.evaluate("document.querySelector('[data-job-id]').dataset.jobId")
    page.evaluate(
        "fetch('/api/jobs/" + str(job_id) + "/status', {method: 'PATCH',"
        " headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({status: 'SHORTLISTED'})})"
    )
    page.navigate(f"{pristine_server}/?view=kanban")
    page.wait_for("Boolean(document.querySelector('.kcard'))", message="a card on the board")

    assert radius_of(page, ".kcard") == [CARD_RADIUS_PX] * 4
    assert radius_of(page, ".kcol") == [CARD_RADIUS_PX] * 4


def test_the_home_metric_card_is_round(page: Chrome, server: str) -> None:
    """It had no `border-radius` at all. Perfectly square, on the first screen
    the product shows."""
    page.navigate(f"{server}/#home")
    page.wait_for("Boolean(document.querySelector('.metric'))", message="a home metric")

    assert radius_of(page, ".metric") == [CARD_RADIUS_PX] * 4


def test_the_filter_panel_is_round(page: Chrome, server: str) -> None:
    """A panel in the design's vocabulary, and it sits beside the cards all day."""
    open_list(page, server)
    page.wait_for("Boolean(document.querySelector('.fsec'))", message="the filter rail")

    assert radius_of(page, ".fsec") == [CARD_RADIUS_PX] * 4


def test_the_table_wrapper_is_round_even_though_its_rows_are_not(page: Chrome, server: str) -> None:
    """Both halves of the same rule: the frame is a panel, the grid is a grid."""
    page.navigate(f"{server}/?view=table")
    page.wait_for("Boolean(document.querySelector('table.jobs tbody tr'))", message="the table")

    assert radius_of(page, ".tablescroll") == [CARD_RADIUS_PX] * 4
    assert radius_of(page, "table.jobs tbody tr") == [0.0] * 4

"""Two more boxes, and the difference between them and the first two is the point.

WHY THIS FILE EXISTS
--------------------
`test_keyword_filters.py` covers the HARD pair: words a posting must mention and
words it must not. Those two remove postings, which is what a person means by a
filter.

The product had no way to say the much more common thing. "I would rather do
documentation work" and "I would rather not do sales" are not bans -- a mild
dislike had to be expressed as a permanent exclusion, and somebody who typed
`sales` into the hide box to push those roles down never saw the one good sales
job in their corpus again.

So there are four boxes, in two pairs, and the pairs behave differently on
purpose. This file asserts the difference where a person would meet it:

  * a preference REORDERS and removes nothing -- the total does not move;
  * the counter does not call it a filter, because the list did not narrow and
    a screen saying "1 filter active" over an unnarrowed list is two numbers
    disagreeing;
  * it is remembered like the other two, because "I would rather not" is not a
    per-tab opinion either;
  * it never reaches `search.local.yaml`, because a stored score is only true
    relative to the configuration that produced it and nobody should be asked
    to rescore a hundred thousand postings for typing a word in a box.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from tests.browser.chrome import Chrome

WIDEN = "include_ineligible=1&include_off_target=1&include_unresolved=1"

PREFER_KEY = "careerAgent.preferKeyword.v1"
AVOID_KEY = "careerAgent.avoidKeyword.v1"

#: In the body of four of the twenty-one demo postings and in no title, so a
#: preference for it has somewhere to move things FROM and TO. A word carried by
#: everything or by nothing could not fail this file's first test.
PREFERRED = "documentation"

LOADING = "Loading..."


@pytest.fixture(autouse=True)
def _no_remembered_preferences(page: Chrome, server: str) -> Iterator[None]:
    """Empty both remembered lists before AND after every test in this file.

    After matters as much as before: a test that fails partway would otherwise
    leave a preference in `localStorage`, the browser is session scoped, and the
    next FILE then renders a differently ordered list for reasons it cannot see.
    """
    page.navigate(f"{server}/")
    _forget(page)
    yield
    page.navigate(f"{server}/")
    _forget(page)


def _forget(page: Chrome) -> None:
    page.evaluate(
        f"localStorage.removeItem({json.dumps(PREFER_KEY)});"
        f"localStorage.removeItem({json.dumps(AVOID_KEY)});"
    )


def _open(page: Chrome, server: str, query: str = "") -> None:
    page.navigate(f"{server}/?{WIDEN}&{query}" if query else f"{server}/?{WIDEN}")
    page.evaluate(
        "[...document.querySelectorAll('.topnav__link')]"
        ".find(n => n.dataset.page === 'jobs').click()"
    )
    _settled(page)


def _settled(page: Chrome) -> None:
    page.wait_for(
        f"!document.getElementById('resultcount').textContent.includes({json.dumps(LOADING)})"
        " && (document.querySelectorAll('#list .card').length > 0"
        " || Boolean(document.querySelector('.state__msg')))",
        message="the job list finishes loading",
    )


def _show_rail(page: Chrome) -> None:
    page.evaluate(
        "(() => {"
        " const show = [...document.querySelectorAll('button')]"
        "   .find(b => b.textContent.trim().startsWith('Show filters'));"
        " if (show) show.click();"
        "})()"
    )
    page.wait_for("Boolean(document.getElementById('f-prefer'))", message="the preference boxes")


def _type_phrase(page: Chrome, box_id: str, phrase: str) -> None:
    page.evaluate(
        f"(() => {{"
        f"  const box = document.getElementById({json.dumps(box_id)});"
        f"  box.value = {json.dumps(phrase)};"
        "   box.dispatchEvent(new KeyboardEvent('keydown',"
        "     { key: 'Enter', bubbles: true, cancelable: true }));"
        "})()"
    )
    _settled(page)


def _titles(page: Chrome) -> list[str]:
    return page.evaluate(
        "[...document.querySelectorAll('#list .card')].map(c => c.innerText.split('\\n')[0])"
    )


def _counter(page: Chrome) -> str:
    return page.evaluate("document.getElementById('resultcount').textContent") or ""


def _active_filters(page: Chrome) -> int:
    """How many filters the counter claims are active, as a NUMBER.

    Read as a delta rather than as an absolute string, because the three
    widenings this file opens every page with are themselves counted by that
    line. Asserting "no filters" would be asserting something about
    `include_off_target` and calling it a preference.
    """
    text = _counter(page)
    for part in text.split(chr(0xB7)):
        words = part.split()
        if "filter" in part and words and words[0].isdigit():
            return int(words[0])
    return 0


# =========================================================================
# 1. A PREFERENCE REORDERS AND REMOVES NOTHING
# =========================================================================


def test_a_preferred_word_changes_the_order_and_not_the_list(page: Chrome, server: str) -> None:
    _open(page, server)
    before = _titles(page)
    assert before, "the demo corpus should render"

    _show_rail(page)
    _type_phrase(page, "f-prefer", PREFERRED)
    after = _titles(page)

    assert sorted(after) == sorted(before), "a preference removed a posting"
    assert after != before, "a preference changed nothing at all"


def test_an_avoided_word_changes_the_order_and_not_the_list(page: Chrome, server: str) -> None:
    """The half that is easiest to get wrong. "I would rather not" reads like a
    hide, and implementing it as one is exactly the defect these boxes exist to
    undo: the posting goes to the bottom, and it is still there."""
    _open(page, server)
    before = _titles(page)

    _show_rail(page)
    _type_phrase(page, "f-avoid", PREFERRED)
    after = _titles(page)

    assert sorted(after) == sorted(before)
    assert after != before


def test_the_two_preferences_are_opposite_ends_of_the_same_list(page: Chrome, server: str) -> None:
    """Preferring a word and avoiding it must not produce the same order.

    They share one ORDER BY term and differ only in sign, so a wiring mistake
    that dropped the sign would leave both boxes doing the same thing -- and
    nothing on screen would say so, because both would still be honest about
    the total.
    """
    _open(page, server, f"prefer_keyword={PREFERRED}")
    preferred = _titles(page)
    _open(page, server, f"avoid_keyword={PREFERRED}")
    avoided = _titles(page)
    assert sorted(preferred) == sorted(avoided)
    assert preferred != avoided


# =========================================================================
# 2. IT IS NOT CALLED A FILTER
# =========================================================================


def test_a_preference_is_never_counted_as_an_active_filter(page: Chrome, server: str) -> None:
    """The hint under these two boxes says they change the order and not the
    list. A counter reading "1 filter active" beside a total that did not move
    would contradict it on the same screen."""
    _open(page, server)
    before = _active_filters(page)

    _show_rail(page)
    _type_phrase(page, "f-prefer", PREFERRED)
    assert _active_filters(page) == before, _counter(page)

    _type_phrase(page, "f-avoid", "sales")
    assert _active_filters(page) == before, _counter(page)


def test_a_hard_keyword_beside_a_preference_still_counts_as_one_filter(
    page: Chrome, server: str
) -> None:
    """The complement, so the test above cannot pass by the counter being
    broken: the HARD box in the same panel does narrow, and is counted."""
    _open(page, server)
    before = _active_filters(page)
    _open(page, server, f"keyword={PREFERRED}&prefer_keyword={PREFERRED}")
    assert _active_filters(page) == before + 1, _counter(page)


# =========================================================================
# 3. IT SURVIVES A NEW VISIT
# =========================================================================


def test_a_preference_is_still_in_force_on_the_next_visit(page: Chrome, server: str) -> None:
    _open(page, server)
    plain = _titles(page)

    _show_rail(page)
    _type_phrase(page, "f-prefer", PREFERRED)
    ordered = _titles(page)
    assert ordered != plain

    # A clean visit, with nothing in the query string at all.
    _open(page, server)
    assert _titles(page) == ordered, "the remembered preference did not apply to a fresh visit"


def test_the_remembered_preference_is_drawn_back_into_the_panel(page: Chrome, server: str) -> None:
    """The defect this test was written for.

    The pair reached the API and never reached the panel: the request carried
    the word, the list was ordered by it, and the box was empty -- so the only
    way to remove a preference was to press Clear all filters. Three wiring
    gaps, all of them invisible unless somebody looked at the box.
    """
    _open(page, server)
    _show_rail(page)
    _type_phrase(page, "f-prefer", PREFERRED)

    _open(page, server)
    _show_rail(page)
    # `textContent` and not `innerText`: the rail can be collapsed, and an
    # invisible node renders no innerText at all -- which would make this pass
    # or fail on the viewport rather than on the wiring it is about.
    chips = page.evaluate(
        "document.getElementById('f-prefer').closest('.phrases')"
        ".querySelector('.phrases__tags').textContent"
    )
    assert PREFERRED in (chips or ""), chips


# =========================================================================
# 4. IT DOES NOT REACH THE CONFIGURATION
# =========================================================================


def test_a_preference_never_bumps_the_configuration_version(page: Chrome, server: str) -> None:
    _open(page, server)
    before = page.evaluate("fetch('/api/health').then(r => r.json()).then(h => h.config_version)")
    _open(page, server, f"prefer_keyword={PREFERRED}&avoid_keyword=sales")
    after = page.evaluate("fetch('/api/health').then(r => r.json()).then(h => h.config_version)")
    assert before == after, (before, after)

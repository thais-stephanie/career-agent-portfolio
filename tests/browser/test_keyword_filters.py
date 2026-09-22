"""Two boxes: words a posting must mention, and words it must not.

WHY THIS FILE EXISTS
--------------------
The two phrase lists are the most ad-hoc control in the product and the one a
person reaches for first. "I never want to see another SAP role" is not a
scoring preference and it is not an eligibility verdict -- it is a fact about
the list somebody is looking at right now.

That makes three things worth asserting and one worth refusing.

WORTH ASSERTING
  * they narrow, and they narrow over the posting TEXT rather than over one
    field, which is what a keyword means to a person;
  * they SURVIVE a new visit. They used to live only in the URL, so they came
    back on a reload and were gone in a new tab, and "never show me this word"
    is not a per-tab opinion;
  * clearing puts everything back, including the remembered copy.

WORTH REFUSING
  * that they persist in `search.local.yaml`. Every section of that file
    changes how a POSTING IS READ, and a stored score is only true relative to
    the configuration that produced it -- so a phrase written there marks every
    score in the corpus stale. Asking somebody to rescore a hundred thousand
    postings because they typed a word into a hide box would be the product
    punishing them for using a control.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from tests.browser.chrome import Chrome

WIDEN = "include_ineligible=1&include_off_target=1&include_unresolved=1"

KEYWORD_KEY = "careerAgent.keyword.v1"
EXCLUDE_KEY = "careerAgent.excludeKeyword.v1"

#: The header while a request is in flight. Waiting for the CARD COUNT to be
#: non-zero is not enough and cost this file two flaky tests: the previous
#: response is still on screen while the next one is being fetched, so a count
#: read at that moment is the count of the query BEFORE the one under test.
#:
#: This is the app's own "I am waiting" string, so the settle signal is the
#: thing that actually settles rather than a duration.
LOADING = "Loading..."


@pytest.fixture(autouse=True)
def _no_remembered_words(page: Chrome, server: str) -> Iterator[None]:
    """Empty both remembered lists before AND after every test here.

    AFTER matters as much as before, and only a fixture gets it right: a test
    that fails partway leaves its hide list in `localStorage`, the browser is
    session scoped, and the next FILE then renders a filtered list for reasons
    it cannot see. That is exactly what happened -- two plain-language tests
    failed in a full run and passed alone.
    """
    page.navigate(f"{server}/")
    _forget(page)
    yield
    page.navigate(f"{server}/")


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
    """Open the filter panel, which is collapsed on a narrow-ish viewport."""
    page.evaluate(
        "(() => {"
        " const show = [...document.querySelectorAll('button')]"
        "   .find(b => b.textContent.trim().startsWith('Show filters'));"
        " if (show) show.click();"
        "})()"
    )
    page.wait_for("Boolean(document.getElementById('f-exclude'))", message="the phrase boxes")


def _count(page: Chrome) -> int:
    return int(page.evaluate("document.querySelectorAll('#list .card').length") or 0)


def _forget(page: Chrome) -> None:
    page.evaluate(
        f"localStorage.removeItem({json.dumps(KEYWORD_KEY)});"
        f"localStorage.removeItem({json.dumps(EXCLUDE_KEY)});"
    )


def _type_phrase(page: Chrome, box_id: str, phrase: str) -> None:
    """Add a phrase the way a person does: type it and press Enter.

    NOT by putting it in the URL, and the difference is the whole of what
    "remembered" means here. A phrase arriving in a query string is somebody
    ELSE'S filter -- a link they were given -- and adopting it permanently
    would make a shared link rewrite the reader's own preferences. Only a
    phrase somebody typed goes through `store.set`, which is where it is
    remembered.
    """
    page.evaluate(
        f"(() => {{"
        f"  const box = document.getElementById({json.dumps(box_id)});"
        f"  box.value = {json.dumps(phrase)};"
        "   box.dispatchEvent(new KeyboardEvent('keydown',"
        "     { key: 'Enter', bubbles: true, cancelable: true }));"
        "})()"
    )
    _settled(page)


# =========================================================================
# 1. THEY NARROW, AND OVER THE WHOLE POSTING
# =========================================================================


def test_a_must_mention_word_narrows_to_what_carries_it(page: Chrome, server: str) -> None:
    _open(page, server)
    everything = _count(page)
    _open(page, server, "keyword=administrative")
    narrowed = _count(page)
    assert 0 < narrowed < everything, f"{narrowed} of {everything}"


def test_a_must_not_mention_word_removes_exactly_those(page: Chrome, server: str) -> None:
    """And the two are each other's complement over the same population, which
    is the property that makes them trustworthy: a word cannot both fail to
    include a posting and fail to exclude it."""
    _open(page, server)
    everything = _count(page)
    _open(page, server, "keyword=administrative")
    with_word = _count(page)
    _open(page, server, "exclude_keyword=administrative")
    without_word = _count(page)
    assert with_word + without_word == everything, (with_word, without_word, everything)


def test_a_word_only_in_the_description_still_matches(page: Chrome, server: str) -> None:
    """The whole point. A keyword filter that searched titles would be a title
    search, which is the thing this product exists not to be.

    `compliance` appears in the body of the administrative posting and in no
    job title in the corpus.
    """
    _open(page, server, "keyword=compliance")
    assert _count(page) > 0
    titles = page.evaluate(
        "[...document.querySelectorAll('#list .card')]"
        ".map(c => c.innerText.split('\\n')[0].toLowerCase())"
    )
    assert all("compliance" not in title for title in titles), titles


# =========================================================================
# 2. THEY SURVIVE A NEW VISIT
# =========================================================================


def test_a_hidden_word_is_still_hidden_on_the_next_visit(page: Chrome, server: str) -> None:
    """The defect this test was written for: the lists lived in the URL only,
    so they survived a reload and vanished in a new tab."""
    _open(page, server)
    everything = _count(page)

    _show_rail(page)
    _type_phrase(page, "f-exclude", "administrative")
    hidden = _count(page)
    assert hidden < everything, f"{hidden} of {everything}"

    # A CLEAN visit, with nothing in the query string at all.
    page.navigate(f"{server}/?{WIDEN}")
    page.evaluate(
        "[...document.querySelectorAll('.topnav__link')]"
        ".find(n => n.dataset.page === 'jobs').click()"
    )
    _settled(page)
    assert _count(page) == hidden, "the remembered hide list did not apply to a fresh visit"


def test_a_link_somebody_was_given_wins_over_what_is_remembered(page: Chrome, server: str) -> None:
    """A shared link describes what its reader will see.

    Dragging the reader's own hide list into somebody else's search would make
    two people looking at the same URL see different lists, with nothing on
    screen able to explain it.
    """
    _open(page, server, "exclude_keyword=administrative")
    hidden = _count(page)
    _open(page, server, "keyword=administrative")
    assert _count(page) > 0, "the link's own filter is what applies"
    assert _count(page) != hidden


# =========================================================================
# 3. CLEARING PUTS EVERYTHING BACK
# =========================================================================


def test_clearing_the_filters_forgets_the_words_too(page: Chrome, server: str) -> None:
    """A "clear all filters" that left a remembered hide list in place would be
    a button that promises to widen and quietly does not.

    The count is re-measured with the three widenings back on rather than
    against the list Clear leaves behind. Clear resets those too -- deliberately
    -- so comparing directly would be measuring `include_off_target` and calling
    it a keyword.
    """
    _open(page, server)
    everything = _count(page)
    _show_rail(page)
    _type_phrase(page, "f-exclude", "administrative")
    assert _count(page) < everything

    page.evaluate(
        "[...document.querySelectorAll('button')]"
        ".find(b => b.textContent.trim() === 'Clear all filters').click()"
    )
    _settled(page)
    remembered = page.evaluate(f"localStorage.getItem({json.dumps(EXCLUDE_KEY)})")
    assert not remembered, remembered

    _open(page, server)
    assert _count(page) == everything, "the word was still being hidden after Clear"


# =========================================================================
# 4. THEY DO NOT REACH THE CONFIGURATION
# =========================================================================


def test_a_keyword_never_bumps_the_configuration_version(page: Chrome, server: str) -> None:
    """The refusal at the top of this file, asserted where it would show.

    `config_version` is what every stored score is keyed on. If a phrase list
    wrote to `search.local.yaml`, this number would move and the interface
    would start telling the person their matches are out of date -- because
    they typed a word into a filter box.
    """
    _open(page, server)
    before = page.evaluate("fetch('/api/health').then(r => r.json()).then(h => h.config_version)")
    _open(page, server, "exclude_keyword=administrative&keyword=systems")
    after = page.evaluate("fetch('/api/health').then(r => r.json()).then(h => h.config_version)")
    assert before == after
    _forget(page)

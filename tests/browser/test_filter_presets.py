"""Every preset in the filter rail is a request the server will answer.

WHY THIS FILE EXISTS
--------------------
"Nothing standing in the way" asked `/api/jobs` for `LIKELY_ELIGIBLE`, which
that route has never accepted. Clicking it returned HTTP 400 and the Jobs view
said "The list of jobs could not be loaded." It was found by the owner in her
own Morning Acceptance run, on the real corpus, after the suite was green.

The browser suite had every ingredient and never combined them: it drives the
list, it drives the filter rail, and it had never CLICKED A PRESET. So the one
control whose entire job is to write a filter value into a request was the one
control no test ever pressed.

The unit-level guard is `tests/unit/test_filter_vocabulary_contract.py`, which
reads the values straight out of `filters.js` and is the faster of the two.
This one is the honest end of it: a real Chrome, a real click, a real request,
and an assertion that the list is still a list afterwards.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import RENDERED_COUNT, open_list

#: The preset ids `filters.js` renders, read off the rendered page rather than
#: written down here -- a preset added tomorrow must be covered by this file
#: without anybody remembering to add it.
PRESET_IDS = (
    "Array.from(document.querySelectorAll('.presets button'))"
    ".map((b) => b.dataset.preset || b.textContent.trim())"
)

#: Whether the list is showing a failure. `state__head` is also the empty
#: state, so the error text is what separates "no results" from "no answer".
FAILURE_SHOWN = (
    "Boolean(document.querySelector('.state--error'))"
    " || /could not be loaded|não foi possível carregar/i"
    ".test(document.body.textContent || '')"
)


@pytest.fixture(autouse=True)
def _list_is_drawn(page: Chrome, server: str):
    open_list(page, server)
    page.wait_for(
        f"{RENDERED_COUNT} > 0 || document.querySelector('.state__head')",
        message="the first paint of the list",
    )
    yield


def preset_ids(page: Chrome) -> list[str]:
    return [str(value) for value in page.evaluate(PRESET_IDS)]


def test_the_presets_are_actually_on_the_page(page: Chrome, server: str) -> None:
    """Otherwise the parametrised assertions below would pass over nothing."""
    found = preset_ids(page)

    assert len(found) >= 4, f"only found {found}"


def test_every_preset_returns_a_list_rather_than_an_error(page: Chrome, server: str) -> None:
    """Click each one in turn and confirm the view is still a view.

    Not "returns results". A preset may legitimately match nothing, and the
    empty state is a correct answer. What must never happen is the request
    being refused, which is what a stale filter value produces.
    """
    failures: list[str] = []

    for index, name in enumerate(preset_ids(page)):
        page.evaluate(f"document.querySelectorAll('.presets button')[{index}].click()")
        page.wait_for(
            f"{RENDERED_COUNT} > 0"
            " || Boolean(document.querySelector('.state__head'))"
            f" || {FAILURE_SHOWN}",
            message=f"the list settling after the {name!r} preset",
        )
        if page.evaluate(FAILURE_SHOWN):
            failures.append(name)

    assert not failures, f"these presets made the list fail to load: {failures}"


def test_the_eligibility_preset_asks_only_for_values_the_route_accepts(
    page: Chrome, server: str
) -> None:
    """The specific defect, watched at the wire rather than at the screen.

    A preset could in principle render a list while still having sent a bad
    request and recovered; this reads the URL the page actually built.
    """
    page.evaluate(
        "window.__requested = [];"
        "if (!window.__fetchPatched) {"
        "  window.__fetchPatched = true;"
        "  const original = window.fetch;"
        "  window.fetch = function (input, init) {"
        "    try { window.__requested.push(String(input)); } catch (e) {}"
        "    return original.apply(this, arguments);"
        "  };"
        "}"
    )
    page.evaluate("document.querySelector('.presets button[data-preset=\"eligible\"]').click()")
    page.wait_for(
        "window.__requested.some((u) => u.includes('/api/jobs'))",
        message="the request the preset issued",
    )
    page.wait_for(
        f"{RENDERED_COUNT} > 0 || Boolean(document.querySelector('.state__head'))",
        message="the list settling",
    )

    urls = [str(u) for u in page.evaluate("window.__requested") if "/api/jobs" in str(u)]
    assert urls, "the preset issued no jobs request"
    assert not any("LIKELY_ELIGIBLE" in url for url in urls), (
        f"the preset still asks for a value /api/jobs refuses: {urls[-1]}"
    )
    assert not page.evaluate(FAILURE_SHOWN)

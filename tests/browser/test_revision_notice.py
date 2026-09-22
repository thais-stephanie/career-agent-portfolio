"""Editing a preference must not empty the list, and must say what happened.

THE MORNING THIS REPRODUCES
---------------------------
2026-09-08, 13:16. The owner opened Settings and set her seniority preferences
to Mid-level and Senior, excluding Intern, Staff, Principal and Lead. Correct,
and entirely hers.

Her configuration went from revision 4 to revision 6. All 19,469 stored scores
answer revision 4. The product asked for revision 6, found nothing, and showed
an empty Jobs list -- with a seven-minute terminal command as the remedy.

This drives that journey in a real browser against a disposable corpus: change
a preference through the product's own API, then look at the list.

WHY THE SENTENCE MATTERS AS MUCH AS THE ROWS
--------------------------------------------
Serving the previous answer without saying so would be worse than the empty
list, not better: a list that silently answers a question she stopped asking
is a list she cannot trust. The rows and the sentence are one change.
"""

from __future__ import annotations

import json

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import RENDERED_COUNT, open_list

#: The notice element, and the class its text carries.
NOTICE = "document.getElementById('revnotice')"
NOTICE_SHOWN = f"Boolean({NOTICE}) && !{NOTICE}.hidden"


def bump_preferences(page: Chrome, server: str) -> None:
    """Change a candidate preference the way the interface does.

    Through `PATCH /api/profile`, which is the route the settings panel uses,
    so this exercises the real path rather than editing a file behind the
    product's back.
    """
    page.evaluate(
        "window.__patched = null;"
        "fetch('/api/profile', {method: 'PATCH',"
        " headers: {'Content-Type': 'application/json'},"
        # `{changes: {...}}`, which is the shape the route requires. Sending
        # the fields bare returns a 400 whose body is still JSON, so a test
        # that only waited for a response would sail past a save that never
        # happened -- and then assert about a revision that never moved.
        " body: JSON.stringify({changes: {seniority_excluded: ['LEAD']}})})"
        " .then((r) => r.json()).then((d) => { window.__patched = d; })"
    )
    page.wait_for("window.__patched !== null", message="the preference to save")
    saved = page.evaluate("JSON.stringify(window.__patched)")
    assert "error" not in str(saved), f"the preference did not save: {saved}"


@pytest.fixture
def edited(page: Chrome, writable_server: str):
    """A corpus scored under the previous revision, and a newer preference.

    `writable_server` because this WRITES `search.local.yaml`, which bumps
    `config_version` and would detach the shared server's corpus from its
    scores for every other test in the session.
    """
    open_list(page, writable_server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="a scored list before the edit")
    before = int(page.evaluate(RENDERED_COUNT))
    bump_preferences(page, writable_server)
    open_list(page, writable_server)
    page.wait_for(
        f"{RENDERED_COUNT} > 0 || Boolean(document.querySelector('.state__head'))",
        message="the list after the edit",
    )
    yield before


def test_the_list_is_not_empty_after_a_preference_edit(page: Chrome, edited) -> None:
    """**The defect, as one assertion.** It used to render nothing at all."""
    rendered = int(page.evaluate(RENDERED_COUNT))

    assert rendered > 0, "the list went empty the moment a preference changed"


def test_the_reader_is_told_which_preferences_these_answer(page: Chrome, edited) -> None:
    """Serving the old answer silently would be worse than serving nothing."""
    assert page.evaluate(NOTICE_SHOWN), "no notice explained the older population"

    text = str(page.evaluate(f"{NOTICE}.textContent"))
    assert "revision" in text.lower() or "revis" in text.lower(), text
    # And it says nothing was lost, because "0 jobs" is what she read last time.
    assert "lost" in text.lower() or "perdid" in text.lower(), text


def test_the_notice_offers_the_way_forward_without_a_terminal(page: Chrome, edited) -> None:
    """The remedy used to be a seven-minute command she had to know about."""
    buttons = page.evaluate(
        f"Array.from({NOTICE}.querySelectorAll('button')).map((b) => b.textContent.trim())"
    )

    assert buttons, "the notice states a problem and offers no action"


def test_the_payload_names_one_revision_and_never_a_blend(page: Chrome, edited) -> None:
    """The rows come from exactly one population, and it says which.

    Blending would produce a list where some rows answer the new preferences
    and some the old, with nothing distinguishing them.
    """
    page.evaluate(
        "window.__jobs = null;"
        "fetch('/api/jobs?limit=5').then((r) => r.json()).then((d) => { window.__jobs = d; })"
    )
    page.wait_for("window.__jobs !== null", message="a jobs response")
    payload = json.loads(str(page.evaluate("JSON.stringify(window.__jobs.revision)")))

    assert payload["serving"] is not None
    assert payload["is_stale"] is True
    assert payload["is_current"] is False
    assert payload["serving"]["config_version"] < payload["current"]["config_version"]
    assert payload["serving"]["complete"] is True, "a half-built population was served"


def test_the_notice_is_absent_when_the_answer_is_current(page: Chrome, server: str) -> None:
    """Silence is the ordinary case and must look like nothing at all.

    The shared `server` fixture has never had its preferences edited, so its
    scores answer the configuration in force.
    """
    open_list(page, server)
    page.wait_for(f"{RENDERED_COUNT} > 0", message="the ordinary list")

    assert not page.evaluate(NOTICE_SHOWN), "a notice appeared with nothing to report"

"""A phrase group decides what counts as her work. Editing one must not be blind.

WHAT WAS MISSING
----------------
`/api/preferences` let the owner edit the phrase groups that decide what counts
as the work she wants, and showed her the phrases and nothing else. Every edit
was a guess: add a phrase, remove a phrase, and find out what happened by
rescoring nineteen thousand postings and reading the list.

Measured on her corpus 2026-09-08, the blindness was hiding real facts --
`documentation_practice` matches 6,506 postings and `selling_hubspot` matches
none at all. Neither is a verdict. "No posting says this" and "this work does
not exist" are different statements, and only she can tell them apart.

AND WHAT AN EDIT COSTS
----------------------
Saving one of these changes how every posting is read, so it bumps the
configuration version and asks for a recalculation of the whole corpus. A
control that expensive says so before it fires, and asks twice.
"""

from __future__ import annotations

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_browser_acceptance import open_list, open_settings

SIGNAL = "document.querySelector('#prefs-host .prefs__signal')"
#: A phrase group is a set of chips now rather than a textarea of one phrase
#: per line. `.tags` is the control that replaced `.prefs__area`; the group's
#: identity, its reach line and its two-press save are unchanged.
AREAS = "document.querySelectorAll('#prefs-host .prefs__signal .tags')"


def open_panel(page: Chrome, server: str) -> None:
    """Land on the product first: `open_settings` presses a nav destination,
    and there is no nav until a page has loaded."""
    # The scoring vocabulary is a developer view, reached with `?debug=1`.
    open_list(page, server, "?debug=1")
    open_settings(page)
    page.evaluate("document.getElementById('settings-model').open = true")
    page.wait_for(f"{AREAS}.length > 0", message="the preference editor")


@pytest.fixture
def reading(page: Chrome, server: str):
    """The shared server, for the tests that only look."""
    open_panel(page, server)
    yield


@pytest.fixture
def writing(page: Chrome, writable_server: str):
    """A disposable one, for the tests that press Save.

    Saving a phrase group writes `search.local.yaml` and bumps
    `config_version`, which would detach the shared corpus from its scores for
    every other test in the session.
    """
    open_panel(page, writable_server)
    yield


def test_every_phrase_group_says_what_it_reaches(page: Chrome, reading) -> None:
    """The number, on every group, or an honest statement of its absence."""
    lines = page.evaluate(
        "Array.from(document.querySelectorAll('#prefs-host .prefs__signal'))"
        ".map((n) => { const r = n.querySelector('.prefs__reach');"
        "  return r && !r.hidden ? r.textContent.trim() : null; })"
    )

    said = [line for line in (lines or []) if line]
    assert said, "no phrase group reported its reach"
    assert len(said) == len(lines), "some groups reported nothing at all"


def test_portuguese_phrase_copy_does_not_deny_scoring(page: Chrome, reading, server) -> None:
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    open_panel(page, server)
    text = page.evaluate(f"{SIGNAL}.textContent")
    assert "Expressões salvas atualmente" in text
    assert "Alcance lexical" in text
    assert "Alcance positivo na descrição" in text
    assert "Nada digitado aqui" not in text
    assert "recalcule" in text.lower()


def test_a_group_that_matches_nothing_is_not_reported_as_a_plain_number(
    page: Chrome, reading
) -> None:
    """Zero is the finding, and it reads differently from 6,506.

    The demo corpus is nineteen postings, so several groups genuinely match
    none of it -- which is exactly the case this has to render well.
    """
    none = page.evaluate(
        "Array.from(document.querySelectorAll('#prefs-host .prefs__reach--none'))"
        ".map((n) => n.textContent.trim())"
    )

    assert none, "no group matched nothing, so this asserts over an empty set"
    for text in none:
        assert "lexical" in text.lower(), text
        assert "different wording" in text.lower() or "outras palavras" in text.lower(), text


def test_a_hard_exclusion_says_it_is_not_counted_rather_than_zero(page: Chrome, reading) -> None:
    """It is a rule the eligibility gate applies, not a phrase the scorer
    records, so a zero would claim something this count never looked at."""
    unmeasured = page.evaluate(
        "Array.from(document.querySelectorAll('#prefs-host .prefs__reach--unmeasured'))"
        ".map((n) => n.textContent.trim())"
    )

    assert unmeasured, "the hard exclusions reported a number they cannot have"
    for text in unmeasured:
        assert "not counted" in text.lower() or "não contado" in text.lower(), text


def test_saving_shows_what_changes_before_it_changes_it(page: Chrome, writing) -> None:
    """One press describes the edit; a second makes it.

    A phrase group decides what counts as her work across every posting in the
    corpus. A control that expensive says so before it fires.
    """
    # Typing into the chip control's own box and committing it the way a
    # person does, with Enter. It was a line appended to a textarea.
    page.evaluate(
        f"(() => {{ const box = {SIGNAL}.querySelector('.tags__input');"
        f" box.value = 'a brand new phrase';"
        f" box.dispatchEvent(new KeyboardEvent('keydown',"
        f" {{key: 'Enter', bubbles: true}})); }})()"
    )
    page.wait_for(
        f"[...{SIGNAL}.querySelectorAll('.chip__text')]"
        f".some(n => n.textContent === 'a brand new phrase')",
        message="the phrase to become a chip",
    )
    page.evaluate(f"{SIGNAL}.querySelector('.prefs__save').click()")
    page.wait_for(
        f"Boolean({SIGNAL}.querySelector('.prefs__diff'))"
        f" && !{SIGNAL}.querySelector('.prefs__diff').hidden",
        message="the change to be described",
    )

    diff = str(page.evaluate(f"{SIGNAL}.querySelector('.prefs__diff').textContent"))
    assert "a brand new phrase" in diff, diff
    # And it says what the save costs, which is a recalculation of everything.
    assert "recalculat" in diff.lower() or "recalcul" in diff.lower(), diff
    # ...and that the results do not vanish while that runs, which is the
    # promise ADR-0017 made and this screen has to keep.
    assert "remain available" in diff.lower() or "continuam disponíveis" in diff.lower(), diff


def test_saved_draft_reach_and_rescore_are_distinct(page: Chrome, writing, writable_server) -> None:
    before = page.evaluate("fetch('/api/preferences').then(r => r.json())")
    assert "Current saved phrases" in page.evaluate(f"{SIGNAL}.textContent")
    assert "Nothing typed here" not in page.evaluate(f"{SIGNAL}.textContent")
    assert "Lexical reach" in page.evaluate(f"{SIGNAL}.textContent")
    assert "Positive body scoring reach" in page.evaluate(f"{SIGNAL}.textContent")
    page.evaluate(
        f"(() => {{const box={SIGNAL}.querySelector('.tags__input');"
        "box.value='a frozen browser review phrase';"
        "box.dispatchEvent(new KeyboardEvent('keydown',{key:'Enter',bubbles:true}));})()"
    )
    assert "Unsaved edits" in page.evaluate(f"{SIGNAL}.textContent")
    draft = page.evaluate("fetch('/api/preferences').then(r => r.json())")
    assert draft["config_version"] == before["config_version"]
    assert draft["reach"] == before["reach"]
    page.evaluate(f"{SIGNAL}.querySelector('.prefs__save').click()")
    assert (
        page.evaluate("fetch('/api/preferences').then(r=>r.json())")["config_version"]
        == before["config_version"]
    )
    page.evaluate(f"{SIGNAL}.querySelector('.prefs__save').click()")
    page.wait_for(f"Boolean({SIGNAL}.querySelector('.prefs__status--ok'))", message="saved phrases")
    saved = page.evaluate("fetch('/api/preferences').then(r=>r.json())")
    assert saved["config_version"] == before["config_version"] + 1
    assert saved["reach_config_version"] == before["reach_config_version"]
    assert saved["reach"] == before["reach"]
    assert (
        "recalculat"
        in page.evaluate(f"{SIGNAL}.querySelector('.prefs__status').textContent").lower()
    )
    open_list(page, writable_server)
    page.wait_for(
        "document.querySelectorAll('#list .card').length > 0", message="old scores remain usable"
    )
    assert not page.evaluate("document.getElementById('revnotice').hidden")


def test_a_save_that_changes_nothing_is_refused_before_it_costs_anything(
    page: Chrome, writing
) -> None:
    """Pressing Save on a group you only came to read must not bump the
    configuration version and recalculate nineteen thousand postings.

    `set_candidate_fields` refuses a no-op one layer down; this refuses it
    before the request, so the person is told rather than left wondering why
    nothing happened.
    """
    page.evaluate(f"{SIGNAL}.querySelector('.prefs__save').click()")
    page.wait_for(
        f"{SIGNAL}.querySelector('.prefs__status').textContent.trim().length > 0",
        message="the refusal",
    )

    said = str(page.evaluate(f"{SIGNAL}.querySelector('.prefs__status').textContent"))
    assert "nothing" in said.lower() or "nada" in said.lower(), said
    assert page.evaluate(f"{SIGNAL}.querySelector('.prefs__diff').hidden"), (
        "a no-op offered a confirmation for a change nobody made"
    )


def test_search_review_requires_preview_and_keep_does_not_rescore(page: Chrome, writing) -> None:
    page.evaluate("document.querySelector('.search-review').open = true")
    selector = "document.querySelector('[data-review-path=\"lexicon.crm_architecture.patterns\"]')"
    page.wait_for(f"Boolean({selector})", message="current search provenance")
    before = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    page.evaluate(f"{selector}.open = true")
    page.evaluate(f"{selector}.querySelector('.review-keep').click()")
    page.wait_for(
        f"{selector}.querySelector('.review-status').textContent.length > 0",
        message="review preview",
    )
    unchanged = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    assert unchanged == before
    page.evaluate(
        f"[...{selector}.querySelectorAll('button')]"
        ".find(b=>b.textContent==='Confirm review').click()"
    )
    page.wait_for("Boolean(document.querySelector('.review-saved'))", message="review saved")
    after = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    assert after["config_version"] == before["config_version"]
    row = next(row for row in after["rows"] if row["path"] == "lexicon.crm_architecture.patterns")
    assert row["origin"] == "reviewed_by_user"
    assert after["document_hash"] == before["document_hash"]


def test_legacy_concept_removal_is_explicit_and_persists(page: Chrome, writing, writable_server):
    page.evaluate("document.querySelector('.search-review').open = true")
    selector = "document.querySelector('[data-review-path=\"lexicon.crm_architecture.patterns\"]')"
    page.wait_for(f"Boolean({selector})", message="legacy concept")
    before = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    page.evaluate(f"{selector}.open = true")
    page.evaluate(f"{selector}.querySelector('.review-remove').click()")
    page.wait_for(
        f"{selector}.querySelector('.review-status').textContent.length > 0",
        message="removal preview",
    )
    assert page.evaluate("fetch('/api/search-review').then(r=>r.json())") == before
    page.evaluate(
        f"[...{selector}.querySelectorAll('button')]"
        ".find(b=>b.textContent==='Confirm review').click()"
    )
    page.wait_for("Boolean(document.querySelector('.review-saved'))", message="review saved")
    open_panel(page, writable_server)
    after = page.evaluate("fetch('/api/search-review').then(r=>r.json())")
    row = next(row for row in after["rows"] if row["path"] == "lexicon.crm_architecture.patterns")
    assert row["origin"] == "reviewed_by_user"
    assert after["config_version"] == before["config_version"] + 1
    assert row["value"] == []
    assert row["review"]["action"] == "remove"

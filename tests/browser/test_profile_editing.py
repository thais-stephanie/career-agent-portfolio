"""Changing your own settings from the screen, and seeing that it worked.

Driven through the real page against the real writer. The assertions are about
what a person experiences: the button says how much is pending, a bad value
names the box rather than saying "invalid", and a saved change is still there
after a reload.

Every test here runs against `writable_config`, never `config/`. The writer
replaces `search.local.yaml` and bumps `config_version`; a browser test doing
that to the repository would replace the owner's real search and detach her
corpus from its scores.
"""

from __future__ import annotations

from tests.browser.chrome import Chrome


def open_profile(page: Chrome, server: str) -> None:
    page.navigate(server)
    # The Career Profile is a page reached from the global navigation. It was
    # a rail disclosure, which made it read as settings for a job list.
    page.evaluate("document.querySelector('.topnav__link[data-page=\"profile\"]').click()")
    # AND THE FORM IS NOT THE FIRST THING ON IT ANY MORE. The page opens on
    # who she is -- what she has confirmed, her work, her skills -- and the
    # settings are the fourth tab. Waiting for the tab row first and then
    # pressing Preferences is the path a person takes; waiting only for the
    # form would time out on a page that is working.
    page.wait_for(
        "document.querySelectorAll('.profiletab').length > 0"
        " || document.querySelectorAll('.profile__form input').length > 0",
        message="the profile to paint",
    )
    page.evaluate(
        "(() => { const tab = [...document.querySelectorAll('.profiletab')]"
        ".find((b) => b.textContent.trim() === 'Preferences'); if (tab) tab.click(); })()"
    )
    page.wait_for(
        "document.querySelectorAll('.profile__form input').length > 0",
        message="the editable profile form",
    )


def status(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.profile__save-status').textContent"))


def save_button(page: Chrome) -> str:
    return str(
        page.evaluate("document.querySelector('.profile__actions .btn--primary').textContent")
    )


def test_the_form_draws_itself_from_what_the_server_offered(
    page: Chrome, writable_server: str
) -> None:
    """Nine fields, each with a label. A control the backend would refuse must
    not be able to appear, which is why the descriptor and the whitelist are
    the same table."""
    open_profile(page, writable_server)
    labels = page.evaluate(
        "[...document.querySelectorAll('.profile__form .field__label')].map(n => n.textContent)"
    )
    assert len(list(labels)) >= 9
    assert all(str(label).strip() for label in labels), "a control was drawn with no label"


def test_nothing_is_saveable_until_something_changes(page: Chrome, writable_server: str) -> None:
    """A Save that does nothing is a Save that makes a person doubt the last
    one worked."""
    open_profile(page, writable_server)
    assert page.evaluate("document.querySelector('.profile__actions .btn--primary').disabled")
    assert save_button(page) == "Save"


def test_the_button_says_how_much_is_pending(page: Chrome, writable_server: str) -> None:
    open_profile(page, writable_server)
    page.evaluate("document.querySelector('#profile-field-work_models-ONSITE-prefer').click()")
    page.wait_for(
        "document.querySelector('.profile__actions .btn--primary').textContent === 'Save 1 change'",
        message="the button to count the change",
    )


def test_choosing_a_value_back_to_its_original_is_not_a_change(
    page: Chrome, writable_server: str
) -> None:
    """Without this, a person who ticks a box and unticks it has a pending edit
    that would bump the configuration version for nothing."""
    open_profile(page, writable_server)
    page.evaluate("document.querySelector('#profile-field-work_models-ONSITE-prefer').click()")
    page.wait_for(
        "document.querySelector('.profile__actions .btn--primary').textContent !== 'Save'",
        message="the change to register",
    )
    # One answer per way of working now: going back is choosing the original.
    page.evaluate("document.querySelector('#profile-field-work_models-ONSITE-fine').click()")
    page.wait_for(
        "document.querySelector('.profile__actions .btn--primary').disabled === true",
        message="the change to cancel itself out",
    )


def test_a_saved_change_survives_a_reload(page: Chrome, writable_server: str) -> None:
    open_profile(page, writable_server)
    page.evaluate("document.querySelector('#profile-field-work_models-ONSITE-prefer').click()")
    page.evaluate("document.querySelector('.profile__actions .btn--primary').click()")
    page.wait_for(
        "document.querySelector('.profile__save-status').classList.contains('is-ok')",
        message="the save to be confirmed",
    )
    assert "recalculat" in status(page), (
        "the reader was not told her existing matches are now stale"
    )

    open_profile(page, writable_server)
    # One radio group per way of working, the same control the setup draws.
    assert page.evaluate(
        "document.querySelector('#profile-field-work_models-ONSITE-prefer').checked"
    ), "the saved choice did not survive the reload"


def test_a_country_nobody_has_heard_of_never_reaches_the_save(
    page: Chrome, writable_server: str
) -> None:
    """The refusal moved from the server to the control, and that is better.

    This used to type "Brazil" into a two-character box and assert that the
    server came back with a sentence naming the field. Both halves changed:
    "Brazil" is now a VALID answer -- the field resolves a name to its code --
    and a word the gazetteer does not know never becomes a pending change at
    all, so there is nothing to refuse later.

    The server's own refusal is unchanged and still covered, by
    `tests/integration/test_career_profile.py` and
    `tests/unit/test_candidate_writer.py`. What this asserts is the thing a
    person now meets: an unrecognised country is not silently kept, the box
    goes back to the answer that was stored, and Save has nothing to do.
    """
    open_profile(page, writable_server)
    page.evaluate(
        "(() => { const c = document.querySelector('#profile-field-candidate_country');"
        " c.value = 'Atlantis'; c.dispatchEvent(new Event('change')); })()"
    )
    restored = page.evaluate("document.querySelector('#profile-field-candidate_country').value")
    assert restored != "Atlantis", "an unknown country was left in the box"
    assert restored, "the stored answer was lost along with the bad one"
    assert page.evaluate("document.querySelector('.profile__actions .btn--primary').disabled"), (
        "a value the control refused still counted as a pending change"
    )


def test_the_save_result_is_announced(page: Chrome, writable_server: str) -> None:
    """A screen reader has to learn that a save happened. `role=status` is how,
    and it is the reason the outcome is text rather than a colour."""
    open_profile(page, writable_server)
    assert (
        page.evaluate("document.querySelector('.profile__save-status').getAttribute('role')")
        == "status"
    )


def test_every_control_can_be_reached_by_keyboard(page: Chrome, writable_server: str) -> None:
    """A settings form nobody can tab through is a settings form for some
    people only."""
    open_profile(page, writable_server)
    unreachable = page.evaluate(
        "[...document.querySelectorAll('.profile__form input')]"
        ".filter(n => n.tabIndex < 0 || n.disabled).length"
    )
    assert int(unreachable) == 0


def test_a_group_of_choices_announces_its_question_first(
    page: Chrome, writable_server: str
) -> None:
    """Which is what makes a screen reader say "Which levels make sense for you
    right now" before reading out seven unlabelled chips.

    It was a `<fieldset>` with a `<legend>`. The choices are chips now -- a
    button with `aria-pressed`, which is what a chip IS -- and a fieldset
    cannot group buttons, so the grouping is `role="group"` with
    `aria-labelledby` pointing at the question. The requirement is unchanged
    and this asserts it in the form it now takes: every group is named, and
    the name is not empty.
    """
    open_profile(page, writable_server)
    named = page.evaluate(
        "[...document.querySelectorAll('.choicechips[role=group]')].map(g => {"
        " const id = g.getAttribute('aria-labelledby');"
        " const label = id && document.getElementById(id);"
        " return label ? label.textContent.trim() : null; })"
    )
    assert list(named), "no grouped control was drawn"
    assert all(named), "a group of choices was drawn with nothing naming it"

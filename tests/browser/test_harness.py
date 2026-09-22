"""Tests of the test harness itself.

A defect here does not fail: it makes other tests assert against a page that
is not the page they think they are driving. That is worse than a failure,
because a green suite is the evidence everything else rests on.
"""

from __future__ import annotations

from tests.browser.chrome import Chrome


def test_debug_port_waits_for_chrome_to_release_its_marker(tmp_path):
    from pathlib import Path
    from unittest.mock import Mock, patch

    from tests.browser.chrome import _await_debug_port

    (tmp_path / "DevToolsActivePort").write_text("12345\n", encoding="utf-8")
    process = Mock()
    process.poll.return_value = None
    with patch.object(Path, "read_text", side_effect=[PermissionError(), "12345\n"]) as read:
        assert _await_debug_port(tmp_path, process) == 12345
    assert read.call_count == 2


def matches(page: Chrome, query: str) -> bool:
    return bool(page.evaluate(f"matchMedia('{query}').matches"))


def test_two_emulated_preferences_do_not_erase_each_other(page: Chrome, server: str) -> None:
    """`Emulation.setEmulatedMedia` REPLACES the feature list, never merges.

    So two helpers that each sent only their own feature silently undid each
    other, and the direction that mattered was quiet: setting reduced motion
    cleared the emulated colour scheme, leaving headless Chrome to answer
    `dark` by default. A test photographing the light palette got the dark one
    with nothing failing to say so.
    """
    page.navigate(server)
    try:
        page.set_color_scheme("light")
        assert matches(page, "(prefers-color-scheme: light)")

        page.set_reduced_motion(True)
        assert matches(page, "(prefers-reduced-motion: reduce)")
        assert matches(page, "(prefers-color-scheme: light)"), (
            "setting reduced motion cleared the colour scheme"
        )

        page.set_color_scheme("dark")
        assert matches(page, "(prefers-reduced-motion: reduce)"), (
            "setting the colour scheme cleared reduced motion"
        )
    finally:
        page.set_reduced_motion(False)
        page.set_color_scheme(None)


def test_clearing_the_colour_scheme_is_not_the_same_as_choosing_one(
    page: Chrome, server: str
) -> None:
    """`None` removes the override; it does not emulate a neutral value.

    There is no neutral value to emulate. Every browser answers
    `prefers-color-scheme` with something, so the only way to ask what THIS
    browser would say on its own is to stop overriding it.
    """
    page.navigate(server)
    page.set_color_scheme("light")
    assert matches(page, "(prefers-color-scheme: light)")

    page.set_color_scheme(None)
    # Headless Chrome answers dark on its own, which is the whole reason
    # `set_color_scheme` exists -- the committed evidence was entirely dark
    # before it did. That is an assumption the harness already rests on, so
    # asserting it here is the honest form: if a Chrome release ever changes
    # the default, this says so instead of the screenshots quietly changing.
    assert matches(page, "(prefers-color-scheme: dark)"), (
        "either the light override survived being cleared, or headless Chrome "
        "no longer answers dark by default"
    )

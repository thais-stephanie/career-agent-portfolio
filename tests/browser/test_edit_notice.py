"""One short notice after a save, with the one action that fits.

The page is given the `effects` a save returns (the server side is covered in
tests/integration/test_edit_effects.py); this checks the words, the single
action, that a second notice replaces the first, and that nothing starts until
the person presses the button.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_local_profiles import live  # noqa: F401  (the fixture)

READY = "document.querySelector('.home')?.dataset.view"


def _fire(page: Chrome, effects: str) -> str:
    page.evaluate(f"window.dispatchEvent(new CustomEvent('ca:effects', {{ detail: {effects} }}))")
    page.wait_for("!document.querySelector('.toast')?.hidden", message="the notice")
    return str(page.evaluate("document.querySelector('.toast').innerText")).replace("\n", " | ")


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_each_kind_of_edit_gets_its_own_sentence_and_action(
    page: Chrome,
    live: tuple[str, Path],
    locale: str,  # noqa: F811
) -> None:
    base, _ = live
    page.navigate(base)
    page.evaluate(f"localStorage.setItem('careerAgent.locale.v1', {locale!r})")
    page.navigate(base)
    page.wait_for(READY, message="Home")
    page.evaluate(
        "window.__writes = []; const real = window.fetch; window.fetch = (u, o) => {"
        " const m = ((o && o.method) || 'GET').toUpperCase();"
        " if (m !== 'GET') window.__writes.push(m + ' ' + u); return real(u, o); }"
    )
    deep = _fire(page, "{ needs_rescore: false, offer_discovery: false, deep_stale: 3 }")
    assert ("3 deep readings are out of date" in deep) or ("3 leituras aprofundadas" in deep)
    assert deep.count("|") == 1, "one sentence and one action"
    assert "Read again" in deep or "Ler de novo" in deep
    search = _fire(page, "{ needs_rescore: false, offer_discovery: true, deep_stale: 0 }")
    assert "Look for jobs" in search or "Buscar vagas" in search
    assert "deep readings" not in search and "leituras" not in search, "the notice was replaced"
    score = _fire(page, "{ needs_rescore: true, offer_discovery: false, deep_stale: 0 }")
    assert "Recalculate" in score or "Recalcular" in score
    quiet = page.evaluate(
        "window.dispatchEvent(new CustomEvent('ca:effects', { detail:"
        " { needs_rescore: false, offer_discovery: false, deep_stale: 0 } })); true"
    )
    assert quiet is True
    # Nothing ran by itself: no collection, recalculation or model call was asked for.
    assert page.evaluate("window.__writes") == []

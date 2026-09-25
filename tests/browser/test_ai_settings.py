"""Settings & Sources -> AI & Semantic Matching, in a real browser.

Every provider's availability is replaced, so nothing leaves the machine and
no CLI starts. What is held: the default view is short and truthful, every
provider shows a state in the reader's language, the DeepSeek key goes in and
never comes back out, and Search Fit readiness is said in words.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_guided_setup import Fresh, _click, _open, fresh  # noqa: F401

from career_agent.semantic import routing
from career_agent.semantic.providers import Availability, ProviderStatus

KEY = "sk-" + "b7c6d5e4" * 4

STATES = {
    "deepseek": ProviderStatus(Availability.KEY_MISSING, "missing"),
    "codex": ProviderStatus(Availability.SIGN_IN_REQUIRED, "sign in", {"auth": "api_key"}),
    "claude_code": ProviderStatus(Availability.AVAILABLE, "ready", {"auth": "subscription"}),
    "laya": ProviderStatus(Availability.NOT_INSTALLED, "absent"),
}


@pytest.fixture
def offline_providers(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    def status(provider_id: str, **_: object) -> ProviderStatus:
        if provider_id == "deepseek" and os.environ.get("DEEPSEEK_API_KEY"):
            return ProviderStatus(Availability.AVAILABLE, "key")
        return STATES[provider_id]

    monkeypatch.setattr(routing, "provider_status", status)
    routing.forget_status()


def _settings(page: Chrome, fresh: Fresh) -> None:  # noqa: F811
    _open(page, fresh.base)
    _click(page, "#setup-later")
    page.wait_for("document.querySelectorAll('.firstrun__step').length === 5")
    _click(page, '.topnav__link[data-page="settings"]')
    page.wait_for("document.querySelector('#ai-settings-host .ai__summary')", message="AI panel")


def test_the_default_view_says_what_ai_is_used_and_nothing_more(
    page: Chrome,
    fresh: Fresh,  # noqa: F811
    offline_providers,
    capture: Callable[[str], Path],
) -> None:
    _settings(page, fresh)
    summary = str(
        page.evaluate("document.querySelector('#ai-settings-host .ai__summary').textContent")
    )
    assert "ready" in summary.lower() and "Claude Code" in summary
    # DeepSeek was preferred and is not available: said, in words.
    fallback = str(page.evaluate("document.querySelector('.ai__fallback').textContent"))
    assert "DeepSeek Flash" in fallback and "Claude Code" in fallback
    # The provider list waits behind a disclosure.
    assert page.evaluate("!document.querySelector('.ai__details').open")
    page.evaluate("document.querySelector('.ai__details').open = true")
    states = page.evaluate(
        "Object.fromEntries(Array.from(document.querySelectorAll('.ai__provider'))"
        ".map(n => [n.dataset.provider, n.querySelector('.ai__state').textContent]))"
    )
    assert states["deepseek"] == "API key missing"
    assert states["codex"] == "Sign-in required"
    codex = str(page.evaluate("document.querySelector('[data-provider=codex]').innerText"))
    assert "would bill the API" in codex, "an API-key login is explained, never used"
    assert states["claude_code"] == "Available"
    assert states["laya"] == "Not installed"
    text = str(page.evaluate("document.querySelector('#ai-settings-host').innerText"))
    assert "Never sent" in text and "Career Profile" in text
    page.evaluate("window.scrollTo(0, document.querySelector('#ai-settings-host').offsetTop - 80)")
    capture("ai-semantic-matching-settings")


def test_the_key_goes_in_and_never_comes_back_out(
    page: Chrome,
    fresh: Fresh,  # noqa: F811
    offline_providers,
) -> None:
    _settings(page, fresh)
    page.evaluate("document.querySelector('.ai__details').open = true")
    page.evaluate(
        "(() => { const n = document.querySelector('#ai-deepseek-key');"
        f" n.value = {json.dumps(KEY)};"
        " n.dispatchEvent(new Event('input', {bubbles: true})); })()"
    )
    page.evaluate(
        "Array.from(document.querySelectorAll('.ai__key button'))"
        ".find(b => b.textContent === 'Save key').click()"
    )
    page.wait_for(
        "document.querySelector('.ai__summary')?.textContent.includes('DeepSeek')",
        message="DeepSeek to become the active provider",
    )
    html = str(page.evaluate("document.documentElement.outerHTML"))
    assert KEY not in html and KEY[-8:] not in html
    assert page.evaluate("document.querySelector('#ai-deepseek-key').value") == ""
    env = fresh.config_dir.parent / ".env"
    assert KEY in env.read_text(encoding="utf-8")
    page.evaluate("document.querySelector('.ai__details').open = true")
    page.evaluate(
        "Array.from(document.querySelectorAll('.ai__key button'))"
        ".find(b => b.textContent === 'Remove key').click()"
    )
    page.wait_for(
        "!document.querySelector('.ai__summary')?.textContent.includes('DeepSeek')",
        message="the key to be removed",
    )
    assert KEY not in env.read_text(encoding="utf-8")


def test_deterministic_only_is_one_choice_away(
    page: Chrome,
    fresh: Fresh,  # noqa: F811
    offline_providers,
) -> None:
    _settings(page, fresh)
    page.evaluate(
        "(() => { const s = document.querySelector('#ai-mode'); s.value = 'deterministic';"
        " s.dispatchEvent(new Event('change', {bubbles: true})); })()"
    )
    page.wait_for(
        "document.querySelector('.ai__summary')?.textContent.startsWith('Deterministic only')",
        message="deterministic only",
    )
    saved = (fresh.config_dir / "semantic.local.yaml").read_text(encoding="utf-8")
    assert "mode: deterministic" in saved


def test_readiness_is_said_in_words_before_any_work_is_described(
    page: Chrome,
    fresh: Fresh,  # noqa: F811
    offline_providers,
) -> None:
    _settings(page, fresh)
    page.wait_for(
        "document.querySelector('.settings__readiness')?.dataset.state",
        message="Search Fit readiness",
    )
    state = page.evaluate("document.querySelector('.settings__readiness').dataset.state")
    text = str(page.evaluate("document.querySelector('.settings__readiness').textContent"))
    assert state == "NOT_READY"
    assert "what work you want next" in text

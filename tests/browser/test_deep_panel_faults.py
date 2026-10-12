"""The Deep reading panel tells its states apart (2026-10-12).

A load that failed, a choice that did not save, nothing to read, scores from
earlier preferences, a coverage count that failed, a provider that is not ready,
and a start that may or may not have reached the provider are SIX different
sentences. The panel is drawn by the real code over a patched `fetch`; nothing is
sent, and "nothing was sent" appears only where it is certain.
"""

# ruff: noqa: E501, F811 - JavaScript literals read whole; fixtures are imported by name

from __future__ import annotations

import json
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_deep_panel import DEEPSEEK, PREVIEW, STATUS, _providers
from tests.browser.test_deep_reading import deep_server  # noqa: F401 - fixture

PATCH = """
(() => {
  const real = window.fetch.bind(window);
  window.__deepAsked = [];
  window.fetch = (input, init = {}) => {
    const url = new URL(String(input), location.href);
    if (!url.pathname.startsWith('/api/deep')) return real(input, init);
    const d = window.__deep;
    const method = (init.method || 'GET').toUpperCase();
    window.__deepAsked.push(`${method} ${url.pathname}`);
    const json = (body, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' } }));
    const fail = (spec) => (spec.network
      ? Promise.reject(new TypeError('Failed to fetch')) : json(spec.body, spec.status));
    if (url.pathname === '/api/deep') return d.statusError ? fail(d.statusError) : json(d.status);
    if (url.pathname === '/api/deep/reviews') return json({ reviews: [] });
    if (url.pathname === '/api/deep/preview') return d.previewError ? fail(d.previewError) : json(d.preview);
    if (url.pathname === '/api/deep/run' && method === 'POST') {
      return d.runError ? fail(d.runError) : json({ run: { status: 'running' }, live: {} });
    }
    if (url.pathname === '/api/deep/run') return json({ run: { status: 'idle' }, live: {} });
    if (url.pathname === '/api/deep/provider') {
      return d.providerError ? fail(d.providerError) : json({ provider: JSON.parse(init.body).provider });
    }
    return json({ error: 'not in this test' }, 404);
  };
})();
"""

BASE = {
    **STATUS,
    "profile": "profile-a",
    "coverage_ok": True,
    "scores": {"state": "current", "serving_version": 9, "current_version": 9, "building": False},
    "age_limit": {"days": 120, "include_undated": False},
    "excluded_by_age": 0,
    "available_unresolved": 90,
    "unavailable": None,
}


def _open(page: Chrome, url: str, **fields: Any) -> None:
    open_home_past_setup(page, url)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.getElementById('deep-open')", message="the Deep reading button")
    data = {
        "status": {**BASE, **fields.pop("status", {})},
        "preview": fields.pop("preview", PREVIEW),
        **fields,
    }
    page.evaluate(PATCH + f"window.__deep = {json.dumps(data)};")
    page.evaluate("document.getElementById('deep-open').click()")
    page.wait_for(
        "document.querySelector('.dr-panel .dr-body') && !document.querySelector('.dr-panel .sk')",
        message="the panel drawn",
    )


def _body(page: Chrome) -> str:
    return str(page.evaluate("document.querySelector('.dr-panel').innerText"))


def _start_disabled(page: Chrome) -> bool:
    return bool(page.evaluate("document.querySelector('.dr-foot .dr-btn').disabled"))


def test_a_coverage_count_that_failed_is_not_shown_as_zero(page: Chrome, deep_server: dict) -> None:
    _open(
        page,
        deep_server["url"],
        status={
            "coverage_ok": False, "main_list_roles": None, "readable": None, "too_short": None,
            "available": None, "available_unresolved": None,
        },
    )  # fmt: skip
    text = _body(page)
    assert "We could not check which jobs can be read" in text
    assert "That is not the same as having none" in text and "Nothing was sent" in text
    assert "not yet read by this provider" not in text, (
        "no strip of zeros stands in for the failure"
    )
    assert _start_disabled(page)
    assert "Still needed: a successful check of your jobs." in text
    assert deep_server["fake"].messages == []


def test_nothing_to_read_says_why_offers_the_action_and_cannot_start_empty(
    page: Chrome, deep_server: dict
) -> None:
    _open(
        page,
        deep_server["url"],
        status={
            "available": 0, "available_unresolved": 0, "readable": 0, "main_list_roles": 0,
            "scores": {"state": "none", "serving_version": None, "current_version": 9, "building": False},
            "unavailable": "NO_SCORES",
        },
    )  # fmt: skip
    text = _body(page)
    assert "No job has a match score yet" in text and "Recalculate in Find jobs first." in text
    assert _start_disabled(page) and "Still needed: at least one posting to read." in text
    page.evaluate(
        "[...document.querySelectorAll('.dr-banner button')]"
        ".find((b) => /Open Find jobs/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-panel') === null || document.querySelector('.drawer--deep').hidden",
        message="closed",
    )
    assert (
        page.evaluate("document.querySelector('.topnav__link[aria-current=page]')?.dataset.page")
        == "jobs"
    )


def test_every_job_already_read_points_to_the_results(page: Chrome, deep_server: dict) -> None:
    _open(
        page,
        deep_server["url"],
        status={
            "available": 0, "available_unresolved": 0, "reviewed": 84, "unavailable": "ALL_READ",
        },
    )  # fmt: skip
    assert "already has a current reading from this provider" in _body(page)
    assert _start_disabled(page)
    page.evaluate(
        "[...document.querySelectorAll('.dr-banner button')]"
        ".find((b) => /See the results/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-foot')?.innerText.includes('Read again')", message="results"
    )


def test_jobs_that_do_not_say_where_they_hire_can_be_included_and_then_it_can_start(
    page: Chrome, deep_server: dict
) -> None:
    _open(
        page,
        deep_server["url"],
        status={"available": 0, "available_unresolved": 14, "unavailable": "UNRESOLVED_ONLY"},
    )
    assert _start_disabled(page)
    assert "14 more do not say where they hire" in _body(page)
    page.evaluate(
        "[...document.querySelectorAll('.dr-banner button')]"
        ".find((b) => /Include them/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-foot .dr-btn').disabled === false", message="enabled"
    )
    assert page.evaluate("document.getElementById('deep-count').max") == "14"


def test_scores_from_earlier_preferences_are_said_and_the_panel_still_works(
    page: Chrome, deep_server: dict
) -> None:
    _open(
        page,
        deep_server["url"],
        status={
            "scores": {
                "state": "older",
                "serving_version": 7,
                "current_version": 9,
                "building": False,
            }
        },
    )
    text = _body(page)
    assert "earlier preferences (revision 7)" in text and "you are now on revision 9" in text
    assert not _start_disabled(page)
    assert "published in the last 120 days" in text, "the age limit is said, as in Find jobs"


def test_a_number_the_person_chose_is_kept_and_a_number_above_what_exists_is_lowered(
    page: Chrome, deep_server: dict
) -> None:
    _open(page, deep_server["url"])
    set_count = (
        "(() => { const n = document.getElementById('deep-count'); n.value = %d;"
        " n.dispatchEvent(new Event('input', {bubbles: true})); })()"
    )
    page.evaluate(set_count % 20)
    # Another choice redraws the panel: a valid number survives it.
    page.evaluate("document.querySelectorAll('.dr-choice')[1].click()")
    page.wait_for(
        "document.querySelector('.dr-panel .dr-body') && !document.querySelector('.dr-panel .sk')",
        message="redrawn",
    )
    assert page.evaluate("document.getElementById('deep-count').value") == "20"
    page.evaluate(set_count % 80)
    page.evaluate("window.__deep.status = {...window.__deep.status, available: 10}")
    page.evaluate("document.querySelectorAll('.dr-choice')[1].click()")
    page.wait_for("document.getElementById('deep-count').value === '10'", message="lowered")
    assert "Changed to 10: that is how many are available now." in _body(page)


def test_a_preview_refused_because_the_numbers_moved_says_so_and_shows_the_true_ones(
    page: Chrome, deep_server: dict
) -> None:
    _open(
        page,
        deep_server["url"],
        previewError={
            "status": 400,
            "body": {"error": "x", "code": "LIMIT_OUT_OF_RANGE", "available": 7},
        },
    )
    page.evaluate("document.querySelector('.dr-foot .dr-btn').click()")
    page.wait_for("document.querySelector('.dr-banner[role=status]')", message="the message")
    assert "The number of postings available changed (now" in _body(page)
    assert "Nothing was sent" in _body(page)


@pytest.mark.parametrize(
    ("kind", "spec", "phrase", "certain"),
    [
        ("load", {"statusError": {"status": 500, "body": {"error": "boom"}}}, "could not be loaded", True),
        ("load_offline", {"statusError": {"network": True}}, "did not answer", True),
        ("save", {"providerError": {"status": 500, "body": {"error": "boom"}}}, "could not be saved", True),
        ("preview", {"previewError": {"status": 500, "body": {"error": "boom"}}}, "preview could not be prepared", True),
    ],
)  # fmt: skip
def test_a_load_a_save_and_a_preview_that_failed_say_nothing_was_sent(
    page: Chrome, deep_server: dict, kind: str, spec: dict, phrase: str, certain: bool
) -> None:
    if kind.startswith("load"):
        open_home_past_setup(page, deep_server["url"])
        page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
        page.wait_for("document.getElementById('deep-open')", message="the button")
        page.evaluate(
            PATCH + f"window.__deep = {json.dumps({'status': BASE, 'preview': PREVIEW, **spec})};"
        )
        page.evaluate("document.getElementById('deep-open').click()")
        page.wait_for("document.querySelector('.dr-banner')", message="the failure")
    else:
        _open(page, deep_server["url"], **spec)
        if kind == "save":
            page.evaluate("document.querySelectorAll('.dr-choice')[1].click()")
        else:
            page.evaluate("document.querySelector('.dr-foot .dr-btn').click()")
        page.wait_for(
            f"document.querySelector('.dr-panel').innerText.includes({phrase!r})",
            message="the message",
        )
    text = _body(page)
    assert phrase in text and "Nothing was sent" in text
    assert deep_server["fake"].messages == []


def test_a_start_that_may_have_reached_the_provider_does_not_say_nothing_was_sent(
    page: Chrome, deep_server: dict
) -> None:
    _open(page, deep_server["url"], runError={"network": True})
    page.evaluate("document.querySelector('.dr-foot .dr-btn').click()")
    page.wait_for("document.querySelector('.dr-rows')", message="the check step")
    page.evaluate("document.querySelector('.dr-check input').click()")
    page.evaluate(
        "[...document.querySelectorAll('.dr-foot button')].find((b) => /Start reading/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-panel').innerText.includes('could not confirm whether the reading started')",
        message="uncertain",
    )
    text = _body(page)
    assert "a second attempt can be charged" in text
    assert "Nothing was sent" not in text, "no certainty where there is none"


def test_a_start_refused_before_anything_ran_says_so(page: Chrome, deep_server: dict) -> None:
    _open(
        page,
        deep_server["url"],
        status=DEEPSEEK | {"coverage_ok": True, "available": 84, "age_limit": BASE["age_limit"]},
        runError={
            "status": 409,
            "body": {
                "error": "PROVIDER_NOT_READY",
                "code": "PROVIDER_NOT_READY",
                "state": "KEY_MISSING",
            },
        },
        preview={
            **PREVIEW,
            "provider": "deepseek",
            "billing": "METERED_API",
            "budget_usd": 1.0,
            "estimate": {},
            "price": None,
        },
    )
    page.evaluate("document.querySelector('.dr-foot .dr-btn').click()")
    page.wait_for("document.querySelector('.dr-rows')", message="the check step")
    page.evaluate("document.querySelector('.dr-check input').click()")
    page.evaluate(
        "[...document.querySelectorAll('.dr-foot button')].find((b) => /Start reading/.test(b.textContent)).click()"
    )
    page.wait_for(
        "document.querySelector('.dr-panel').innerText.includes('is not ready')", message="refused"
    )
    assert "Nothing was sent" in _body(page)


def test_a_provider_that_is_not_ready_cannot_start_and_says_what_is_missing(
    page: Chrome, deep_server: dict
) -> None:
    status = {**DEEPSEEK, "providers": _providers("deepseek", deepseek_state="KEY_MISSING")}
    _open(page, deep_server["url"], status=status)
    text = _body(page)
    assert "DeepSeek is not ready" in text or "is not ready" in text
    assert "Still needed: a provider that is ready." in text
    assert _start_disabled(page)
    assert "sk-" not in text, "no credential is ever shown"

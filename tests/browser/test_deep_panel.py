"""The Deep reading panel's presentation, state by state (2026-10-10).

The panel is drawn by the real code over a PATCHED `fetch` that answers the
deep routes with invented payloads, so every state can be reached without a
run: choose, check, reading, results, a load error, a paused run and an
out-of-date reading. Nothing is sent: the patch answers before any request
leaves the page, and the fake provider is asserted untouched.

What must hold in each state, theme and width (1440, 390, 360): no
horizontal overflow, the footer inside the window with 44px actions, no list
clipped by its column, no catalogue key or empty value on screen, focus on
the title when the panel opens and back on the button when it closes.
"""

# ruff: noqa: E501, F811 - JavaScript literals read whole; the fixture is imported by name

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_deep_reading import deep_server  # noqa: F401 - fixture

SHOTS = os.environ.get("DEEP_SHOTS")

STATUS = {
    "demo": False,
    "provider": "claude_code",
    "provider_reason": None,
    "provider_state": "available",
    "auth": "subscription",
    "share_evidence": False,
    "cap": 25,
    "main_list_roles": 120,
    "readable": 84,
    "too_short": 36,
    "reviewed": 0,
    "stale": 0,
    "not_reviewed": 84,
    "last_run": None,
    "run": {"status": "idle"},
    "available": 84,
    "budget_default": 1.0,
    "job": None,
    "live": {},
}
PRICE = {
    "input_per_mtok": 0.3,
    "cached_input_per_mtok": 0.006,
    "output_per_mtok": 1.2,
    "observed_at": "2026-10-10",
}


def _providers(chosen: str, deepseek_state: str = "AVAILABLE") -> list[dict]:
    rows = [
        ("claude_code", "", "subscription", "SUBSCRIPTION", "AVAILABLE", "subscription", None),
        ("codex", "", "subscription", "SUBSCRIPTION", "NOT_INSTALLED", None, None),
        ("deepseek", "deepseek-flash", "api", "METERED_API", deepseek_state, None, PRICE),
    ]
    return [
        {
            "id": pid,
            "model": model,
            "connection": connection,
            "billing": billing,
            "state": state,
            "usable": state == "AVAILABLE",
            "auth": auth,
            "price": price,
            "consent": False,
            "chosen": pid == chosen,
        }
        for pid, model, connection, billing, state, auth, price in rows
    ]


STATUS["providers"] = _providers("claude_code")
DEEPSEEK = {**STATUS, "provider": "deepseek", "auth": None, "providers": _providers("deepseek")}
POSTINGS = [
    {"title": t, "company": c, "stratum": s, "local_score": n}
    for t, c, s, n in [
        ("Business Systems Engineer", "Fictional Northwind", "top_local", 89),
        (
            "Business Systems Analyst with a deliberately long title that has to wrap",
            "Fictional Quill",
            "top_local",
            70,
        ),
        ("Forward Deployed AI Engineer", "Fictional Cobalt", "lexical", 69),
        ("GTM Engineer", "Fictional Lumen", "lexical", 67),
        ("Integrations Developer", "Fictional Ridge", "control", 41),
        ("Revenue Operations Analyst", "Fictional Vane", "control", 38),
    ]
]
PREVIEW = {
    "provider": "claude_code",
    "reason": None,
    "postings": POSTINGS,
    "population": 84,
    "already_reviewed": 0,
    "too_short": 36,
    "intent": {"work": ["business systems", "integrations"], "tools": ["Fictional CRM"]},
    "levels": ["SENIOR"],
    "share_evidence": False,
    "evidence_per_posting": [0],
    "confirmed_evidence": 0,
    "evidence_example": [],
    "estimated_input_tokens": 18800,
    "model": "",
    "billing": "SUBSCRIPTION",
    "available": 84,
    "requested": 6,
    "batch_size": 25,
    "budget_usd": None,
    "estimate": {},
    "price": None,
    "packet": {"postings_cut": 0, "skills_left_out": 0, "schema_in_prompt": False},
}
PREVIEW_DEEPSEEK = {
    **PREVIEW,
    "provider": "deepseek",
    "model": "deepseek-flash",
    "billing": "METERED_API",
    "requested": 6,
    "budget_usd": 1.0,
    "estimate": {
        "input_tokens": 36_000,
        "input_tokens_ceiling": 120_000,
        "output_tokens_typical": 19_800,
        "output_tokens_ceiling": 48_000,
        "typical_usd": 0.0346,
        "off_peak_usd": 0.0173,
        "worst_usd": 0.0936,
        "covers_typical": 173,
        "covers_worst": 64,
    },
    "price": PRICE,
    "packet": {
        "postings_cut": 1,
        "posting_chars_cut": 530,
        "max_posting_chars": 12000,
        "skills_left_out": 0,
        "experience_left_out_max": 12,
        "experience_shortened_max": 10,
        "schema_in_prompt": True,
    },
}


def _assessment(rec: str, alignment: str, history: str, gaps: int = 0) -> dict:
    requirements = [
        {
            "quote": "Fictional Tool Certification is required",
            "kind": "mandatory",
            "category": "credential",
            "status": "not_shown",
            "reading_said": "not_shown",
            "notes": [],
            "parts": [],
            "evidence_ids": [],
            "central": True,
        }
    ]
    return {
        "recommendation": rec,
        "alignment": alignment,
        "history": history,
        "counts": {
            "mandatory": 4,
            "shown": 1,
            "partial": 1,
            "declared": 1,
            "review": 0,
            "not_shown": 1,
            "unknown": 0,
        },
        "central_gaps": [0] if gaps else [],
        "to_review": 0,
        "conditions": [{"quote": "Remote from a fictional region", "topic": "place"}],
        "languages": [],
        "questions": [],
        "requirements": requirements,
    }


REVIEWS = [
    {
        "job_id": "J1",
        "title": "Business Systems Engineer",
        "company": "Fictional Northwind",
        "level": "PREPARE",
        "level_then": "PREPARE",
        "stale": False,
        "stale_reasons": [],
        "comparison": "agrees",
        "local_score": 89,
        "assessment": _assessment("PREPARE", "CENTRAL", "PARTLY"),
    },
    {
        "job_id": "J2",
        "title": "GTM Engineer",
        "company": "Fictional Lumen",
        "level": "CHECK_FIRST",
        "level_then": "CHECK_FIRST",
        "stale": False,
        "stale_reasons": [],
        "comparison": "agrees",
        "local_score": 67,
        "assessment": _assessment("CHECK_FIRST", "CENTRAL", "PARTLY", gaps=1),
    },
    {
        "job_id": "J3",
        "title": "Revenue Operations Analyst",
        "company": "Fictional Vane",
        "level": "STALE",
        "level_then": "LOW",
        "stale": True,
        "stale_reasons": ["profile"],
        "comparison": "stale",
        "local_score": 38,
        "assessment": _assessment("LOW", "NEIGHBOUR", "LITTLE"),
    },
]
RUNNING = {"status": "running", "boards_done": 4, "boards_total": 9}
PAUSED = {
    "published": 8,
    "candidates": 9,
    "failed": 1,
    "rejected": 0,
    "stop_reason": "LIMIT_REACHED",
}

STATES = {
    "choose": {"status": STATUS, "reviews": []},
    "check": {"status": STATUS, "reviews": [], "click": True},
    "reading": {"status": {**STATUS, "run": RUNNING}, "reviews": []},
    "results": {
        "status": {**STATUS, "reviewed": 2, "stale": 1, "last_run": PAUSED},
        "reviews": REVIEWS,
    },
    "error": {"status": None, "reviews": []},
    "no_provider": {
        "status": {
            **STATUS,
            "provider": None,
            "provider_reason": "CHOOSE_PROVIDER",
            "providers": _providers(""),
        },
        "reviews": [],
    },
    "deepseek_choose": {"status": DEEPSEEK, "reviews": []},
    "deepseek_check": {"status": DEEPSEEK, "reviews": [], "click": True},
    "deepseek_reading": {
        "status": {
            **DEEPSEEK,
            "run": {"status": "running", "boards_done": 30, "boards_total": 60},
            "live": {"spent_usd": 0.2104, "budget_usd": 1.0, "batch": 2, "batches": 3},
        },
        "reviews": [],
    },
    "deepseek_budget": {
        "status": {
            **DEEPSEEK,
            "reviewed": 2,
            "last_run": {**PAUSED, "stop_reason": "BUDGET_REACHED", "failed": 0},
            "job": {
                "id": "djob_x",
                "provider": "deepseek",
                "model": "deepseek-flash",
                "billing": "METERED_API",
                "status": "PAUSED",
                "stop_reason": "BUDGET_REACHED",
                "requested": 60,
                "done": 41,
                "spent_usd": 0.9871,
                "budget_usd": 1.0,
                "attempts": {"PUBLISHED": 41},
                "resumable": True,
                "created_at": "2026-10-11T00:00:00Z",
            },
        },
        "reviews": REVIEWS,
    },
}

PATCH = """
(() => {
  const real = window.fetch.bind(window);
  window.__deepAsked = [];
  window.fetch = (input, init = {}) => {
    const url = new URL(String(input), location.href);
    if (!url.pathname.startsWith('/api/deep')) return real(input, init);
    const d = window.__deep;
    window.__deepAsked.push(`${(init.method || 'GET')} ${url.pathname}`);
    const json = (body, status = 200) => Promise.resolve(new Response(JSON.stringify(body), {
      status, headers: { 'Content-Type': 'application/json' } }));
    if (url.pathname === '/api/deep') return d.status ? json(d.status) : json({ error: 'x' }, 500);
    if (url.pathname === '/api/deep/reviews') return json({ reviews: d.reviews });
    if (url.pathname === '/api/deep/preview') return json(d.preview);
    if (url.pathname === '/api/deep/run') {
      return json({ run: d.status ? d.status.run : null, live: d.status ? d.status.live : {} });
    }
    if (url.pathname === '/api/deep/provider') return json({ provider: JSON.parse(init.body).provider });
    return json({ error: 'not in this test' }, 404);
  };
})();
"""

LAYOUT = """
(() => {
  const panel = document.querySelector('.dr-panel');
  const body = panel.querySelector('.dr-body');
  const foot = panel.querySelector('.dr-foot');
  const out = [];
  if (document.documentElement.scrollWidth > window.innerWidth) out.push('page scrolls sideways');
  if (panel.scrollWidth > panel.clientWidth + 1) out.push('panel overflows sideways');
  if (body.scrollWidth > body.clientWidth + 1) out.push('body overflows sideways');
  for (const node of body.querySelectorAll('.dr-rows, .dr-card, .dr-job, .dr-banner')) {
    if (node.scrollHeight > node.clientHeight + 2) out.push(`clipped: ${node.className}`);
    if (node.getBoundingClientRect().right > body.getBoundingClientRect().right + 1) {
      out.push(`wider than the body: ${node.className}`);
    }
  }
  if (foot.children.length) {
    const r = foot.getBoundingClientRect();
    if (r.bottom > window.innerHeight + 1 || r.top < 0) out.push('footer outside the window');
    for (const b of foot.querySelectorAll('button')) {
      if (b.getBoundingClientRect().height < 44) out.push(`small footer action: ${b.textContent}`);
    }
  }
  // Every toned ground, and the step numbers: ink against its own ground.
  const rgba = (v) => { const n = (v.match(/[\\d.]+/g) || []).map(Number); return [n[0], n[1], n[2], n.length > 3 ? n[3] : 1]; };
  // A translucent ground is what it looks like over the nearest opaque one.
  const ground = (node) => {
    const layers = [];
    for (let el = node; el; el = el.parentElement) {
      const c = rgba(getComputedStyle(el).backgroundColor);
      if (c[3] > 0) layers.push(c);
      if (c[3] >= 1) break;
    }
    let [r, g, b] = layers.length && layers[layers.length - 1][3] >= 1 ? layers.pop() : [255, 255, 255];
    for (const [lr, lg, lb, la] of layers.reverse()) { r = lr * la + r * (1 - la); g = lg * la + g * (1 - la); b = lb * la + b * (1 - la); }
    return [r, g, b];
  };
  const lum = (c) => { const [r, g, b] = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  for (const n of panel.querySelectorAll('[data-tone], .dr-step__n, .dr-btn, .dr-btn2')) {
    if (!n.textContent.trim() || !n.getClientRects().length) continue;
    const [a, b] = [lum(rgba(getComputedStyle(n).color)), lum(ground(n))].sort((x, y) => y - x);
    const ratio = (a + 0.05) / (b + 0.05);
    if (ratio < 4.5) out.push(`contrast ${ratio.toFixed(2)}: ${n.className} ${n.textContent.trim().slice(0, 30)}`);
  }
  const text = panel.innerText;
  if (/deep\\.|undefined|NaN|\\{[a-z]+\\}/.test(text)) out.push('a key or an empty value on screen');
  return out;
})()
"""


def _open(page: Chrome, url: str, state: str, locale: str) -> None:
    open_home_past_setup(page, url)
    if locale != "en":
        page.evaluate(f"window.localStorage.setItem('careerAgent.locale.v1', {locale!r})")
        page.reload()
        open_home_past_setup(page, url)
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.getElementById('deep-open')", message="the Deep reading button")
    spec = STATES[state]
    preview = PREVIEW_DEEPSEEK if state.startswith("deepseek") else PREVIEW
    data = {"status": spec["status"], "reviews": spec["reviews"], "preview": preview}
    page.evaluate(PATCH + f"window.__deep = {json.dumps(data)};")
    page.evaluate("document.getElementById('deep-open').click()")
    page.wait_for(
        "document.querySelector('.dr-panel .dr-body') && !document.querySelector('.dr-panel .sk')",
        message="the panel drawn",
    )
    if spec.get("click"):
        # The primary action of the first step, in any language.
        page.evaluate("document.querySelector('.dr-foot .dr-btn').click()")
        page.wait_for("document.querySelector('.dr-rows')", message="the check step")


@pytest.mark.parametrize("state", list(STATES))
@pytest.mark.parametrize("width", [1440, 390, 360])
def test_each_state_fits_the_window(
    page: Chrome, deep_server: dict, state: str, width: int
) -> None:  # noqa: F811
    page.set_viewport(width, 900 if width > 600 else 780, mobile=width < 600)
    page.set_reduced_motion(True)  # a frame, never the slide-in halfway
    for scheme in ("light", "dark"):
        page.set_color_scheme(scheme)
        _open(page, deep_server["url"], state, "en")
        problems = page.evaluate(LAYOUT)
        opaque = page.evaluate(
            "getComputedStyle(document.querySelector('.dr-panel')).backgroundColor"
        )
        assert opaque.startswith("rgb("), opaque
        assert problems == [], (state, width, scheme, problems)
        if SHOTS:
            page.screenshot(Path(SHOTS) / f"panel-{state}-{width}-{scheme}.png")
        page.press("Escape")
    page.set_color_scheme(None)
    assert deep_server["fake"].messages == [], "the panel sent something"


@pytest.mark.parametrize("state", ["choose", "check", "reading", "results", "error"])
def test_each_state_reads_in_portuguese(page: Chrome, deep_server: dict, state: str) -> None:  # noqa: F811
    _open(page, deep_server["url"], state, "pt-BR")
    assert page.evaluate(LAYOUT) == []
    text = str(page.evaluate("document.querySelector('.dr-panel').innerText"))
    assert "Leitura aprofundada" in text.title() or "LEITURA APROFUNDADA" in text, text[:200]
    if SHOTS:
        page.screenshot(Path(SHOTS) / f"panel-{state}-pt.png")


def test_the_states_say_what_they_are(page: Chrome, deep_server: dict) -> None:  # noqa: F811
    _open(page, deep_server["url"], "results", "en")
    text = str(page.evaluate("document.querySelector('.dr-panel').innerText"))
    for fragment in (
        "Paused: your subscription limit was reached",  # the pause
        "Out of date",  # the stale reading keeps its group
        "Level when read: Low priority (not current)",
        "Central requirement not in what was sent",
        "Your history: partly shown",
        "Read again",
        "Back to my jobs",
    ):
        assert fragment in text, fragment
    current = page.evaluate("document.querySelector('.dr-step[aria-current=\"step\"]')")
    assert current is None, "the results step is done, none is current"
    page.press("Escape")
    _open(page, deep_server["url"], "reading", "en")
    bar = page.evaluate(
        "(() => { const b = document.querySelector('.dr-progress');"
        " return [b.getAttribute('role'), b.getAttribute('aria-valuenow'), b.getAttribute('aria-valuemax'),"
        " b.firstChild.style.width]; })()"
    )
    assert bar == ["progressbar", "4", "9", "44%"]
    assert "Stop reading" in str(page.evaluate("document.querySelector('.dr-foot').innerText"))
    page.press("Escape")
    _open(page, deep_server["url"], "error", "en")
    assert "could not be loaded" in str(
        page.evaluate("document.querySelector('.dr-body').innerText")
    )


def test_the_check_step_keeps_the_confirmation_and_sends_nothing(
    page: Chrome, deep_server: dict
) -> None:  # noqa: F811
    _open(page, deep_server["url"], "check", "en")
    start = "Array.from(document.querySelectorAll('.dr-foot button')).find((b) => /Start reading/.test(b.textContent))"
    assert page.evaluate(f"{start}.disabled"), "Start before the confirmation"
    text = str(page.evaluate("document.querySelector('.dr-body').innerText"))
    assert "Show all 6 postings" in text and "Not shared" in text
    assert (
        "no employer or date field" not in text
        and "employer and date fields are never sent" in text
    )
    page.evaluate(
        "Array.from(document.querySelectorAll('.dr-rows__foot button')).find((b) => /Show all/.test(b.textContent))"
        ".click()"
    )
    assert (
        page.evaluate("document.querySelectorAll('.dr-rows')[1].querySelectorAll('.dr-row').length")
        == 6
    )
    page.evaluate("document.querySelector('.dr-check input').click()")
    assert not page.evaluate(f"{start}.disabled")
    page.evaluate(
        "Array.from(document.querySelectorAll('.dr-foot button')).find((b) => /Back/.test(b.textContent)).click()"
    )
    page.wait_for("document.querySelector('.dr-switch')", message="back on the first step")
    asked = page.evaluate("window.__deepAsked")
    assert "POST /api/deep/run" not in asked, asked
    assert deep_server["fake"].messages == []


def test_focus_moves_in_and_back_and_the_list_behind_keeps_its_look(
    page: Chrome, deep_server: dict
) -> None:  # noqa: F811
    open_home_past_setup(page, deep_server["url"])
    page.evaluate("document.querySelector('.topnav__link[data-page=\"jobs\"]').click()")
    page.wait_for("document.querySelector('[data-job-id]')", message="the list")
    behind = (
        "(() => { const c = document.querySelector('[data-job-id]'); const s = getComputedStyle(c);"
        " return [s.backgroundColor, s.borderTopColor, s.fontFamily, s.fontSize, s.borderRadius]; })()"
    )
    before = page.evaluate(behind)
    _open(page, deep_server["url"], "choose", "en")
    assert page.evaluate("document.activeElement.id") == "deep-title"
    assert page.evaluate(behind) == before
    page.press("Tab")
    assert page.evaluate("!!document.activeElement.closest('.dr-panel')")
    page.press("Escape")
    assert page.evaluate("document.querySelector('.drawer--deep').hidden")
    assert page.evaluate("document.activeElement.id") == "deep-open"
    # The job drawer, another overlay, keeps its own dimmed backdrop.
    page.evaluate("document.querySelector('[data-job-id]').click()")
    page.wait_for(
        "document.querySelector('.drawer:not([hidden]):not(.drawer--deep) .drawer__scrim')",
        message="job",
    )
    scrim = page.evaluate(
        "getComputedStyle(document.querySelector('.drawer:not([hidden]):not(.drawer--deep) .drawer__scrim'))"
        ".backgroundColor"
    )
    assert scrim not in ("transparent", "rgba(0, 0, 0, 0)")


def test_deepseek_is_chosen_by_name_with_its_cost_and_its_budget(
    page: Chrome, deep_server: dict
) -> None:  # noqa: F811
    _open(page, deep_server["url"], "deepseek_choose", "en")
    radios = page.evaluate(
        "Array.from(document.querySelectorAll('.dr-choice')).map((n) =>"
        " [n.dataset.provider, n.getAttribute('role'), n.getAttribute('aria-checked')])"
    )
    assert radios == [
        ["claude_code", "radio", "false"],
        ["codex", "radio", "false"],
        ["deepseek", "radio", "true"],
    ]
    text = str(page.evaluate("document.querySelector('.dr-body').innerText"))
    for fragment in (
        "Model deepseek-flash",
        "API key",
        "Pay per use: US$ 0.3 per million input tokens",
        "read 2026-10-10",
        "Not installed on this computer",
        "Most to spend (US$)",
        "How many postings to read",
        "84 readable postings",
        "consent is for DeepSeek only",
    ):
        assert fragment in text, fragment
    assert page.evaluate("document.getElementById('deep-budget').value") == "1"
    assert page.evaluate("document.getElementById('deep-count').value") == "25"
    # Choosing another provider is a request of its own, and sends nothing else.
    page.evaluate("document.querySelector('.dr-choice[data-provider=claude_code]').click()")
    page.wait_for(
        "window.__deepAsked.includes('POST /api/deep/provider')", message="the choice was saved"
    )
    assert "POST /api/deep/run" not in page.evaluate("window.__deepAsked")
    assert deep_server["fake"].messages == []


def test_the_check_step_shows_the_estimate_and_its_assumptions(
    page: Chrome, deep_server: dict
) -> None:  # noqa: F811
    _open(page, deep_server["url"], "deepseek_check", "en")
    page.evaluate("document.querySelector('.dr-cost details').open = true")
    text = str(page.evaluate("document.querySelector('.dr-body').innerText"))
    for fragment in (
        "About US$ 0.0346 for 6 postings, never more than your budget of US$ 1.00",
        "about 36000 in and about 19800 out (at most 48000 out)",
        "off-peak: US$ 0.0173",
        "Worst case reserved",
        "covers about 173 postings at the estimate and at least 64",
        "batches of 25",
        "3,300 tokens per posting",
        "This is an estimate, not a quote",
        "1 postings are longer than 12000 characters",
        "JSON Schema of the answer is added",
    ):
        assert fragment in text, fragment
    start = "document.querySelector('.dr-foot .dr-btn')"
    assert page.evaluate(f"{start}.disabled"), "Start before the confirmation"


@pytest.mark.parametrize("locale", ["en", "pt-BR"])
def test_a_reading_paused_by_the_budget_says_so_and_offers_to_resume(
    page: Chrome, deep_server: dict, locale: str
) -> None:  # noqa: F811
    _open(page, deep_server["url"], "deepseek_budget", locale)
    text = str(page.evaluate("document.querySelector('.dr-body').innerText"))
    if locale == "en":
        assert "next posting\u2019s worst case" in text or "next posting's worst case" in text
        assert "41 of 60 postings done" in text and "Spent US$ 0.99 of US$ 1.00" in text
    else:
        assert "41 de 60 vagas" in text and "Gasto US$ 0.99 de US$ 1.00" in text
    assert page.evaluate("!!document.getElementById('deep-resume-budget')")
    assert page.evaluate(LAYOUT) == []
    if SHOTS:
        page.screenshot(Path(SHOTS) / f"panel-deepseek-budget-{locale}.png")

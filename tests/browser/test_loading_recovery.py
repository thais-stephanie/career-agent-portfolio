# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""A slow, failing or never-answering read holds only its own part of a screen.

The job lists behind Resume Tailor's Start and Make were the slowest reads in
the app (minutes on a large corpus) and every part of both tabs waited for
them. Here they are held, delayed or failed IN THE BROWSER, with a synthetic
profile and the invented demo jobs, and what must hold is what the person can
do meanwhile: open Make, paste an ad, choose a base, read every other part --
and never read a failure as "you have no jobs" or "nothing is confirmed".

Also: Home asks its two reads together; the resumes library says where a CV
added to the profile went; the one-line refresh bar says a site is being read.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from tests.browser.chrome import Chrome
from tests.browser.test_resume_tailor import _serve, tailor_server  # noqa: F401

#: Installed before any page script: records every request, and applies the
#: rules in `window.__rules` -- hold (until released or aborted), delay, a
#: status, or a JSON answer -- to the first rule whose `match` is in the URL.
INTERCEPT = """(() => {
  const real = window.fetch.bind(window);
  window.__rules = %s;
  window.__calls = [];
  window.__aborted = [];
  window.__held = [];
  window.fetch = (input, options = {}) => {
    const url = String(input);
    const method = ((options && options.method) || 'GET').toUpperCase();
    const entry = { url, method, at: performance.now(), end: null };
    window.__calls.push(entry);
    const signal = options && options.signal;
    if (signal) signal.addEventListener('abort', () => window.__aborted.push(url));
    const settle = (promise) => promise.finally(() => { entry.end = performance.now(); });
    const rule = window.__rules.find(
      (r) => url.includes(r.match) && !(r.not && url.includes(r.not)));
    if (!rule) return settle(real(input, options));
    if (rule.mode === 'hold') {
      return settle(new Promise((resolve, reject) => {
        const go = () => real(input, { ...options, signal: undefined }).then(resolve, reject);
        window.__held.push(go);
        const stop = () => reject(new DOMException('Aborted', 'AbortError'));
        if (signal) signal.addEventListener('abort', stop);
      }));
    }
    if (rule.mode === 'delay') {
      return settle(new Promise((r) => setTimeout(r, rule.ms)).then(() => real(input, options)));
    }
    const body = rule.mode === 'json' ? rule.body : { error: 'synthetic failure' };
    return settle(Promise.resolve(new Response(JSON.stringify(body), {
      status: rule.mode === 'json' ? 200 : rule.status,
      headers: { 'Content-Type': 'application/json' },
    })));
  };
})()"""

LISTS = "/api/jobs?"
#: Resume Tailor's two lists, told apart from the Jobs page's own list.
TAILOR_LISTS = (
    "saved_only=true&group_duplicates=1&limit=24",
    "/api/jobs?group_duplicates=1&limit=8",
)
PART = "document.querySelector('#page-tailor [data-part={part}]')"


_INSTALLED: list[str] = []


@pytest.fixture(autouse=True)
def _uninstall(page: Chrome) -> Iterator[None]:
    """The session browser keeps a script added for new documents: remove it,
    or every later test would run under this one's rules."""
    yield
    while _INSTALLED:
        page._cdp.call("Page.removeScriptToEvaluateOnNewDocument", {"identifier": _INSTALLED.pop()})


def intercept(page: Chrome, rules: list[dict[str, Any]]) -> None:
    added = page._cdp.call(
        "Page.addScriptToEvaluateOnNewDocument", {"source": INTERCEPT % json.dumps(rules)}
    )
    _INSTALLED.append(added["identifier"])


def tailor_lists(page: Chrome) -> list[str]:
    return [u for u in calls(page, LISTS) if any(k in u for k in TAILOR_LISTS)]


def rules(page: Chrome, now: list[dict[str, Any]]) -> None:
    page.evaluate(f"window.__rules = {json.dumps(now)}")


def calls(page: Chrome, needle: str) -> list[str]:
    return list(
        page.evaluate(f"window.__calls.filter((c) => c.url.includes({needle!r})).map((c) => c.url)")
    )


def part(page: Chrome, name: str, what: str = "dataset.load") -> Any:
    return page.evaluate(f"{PART.format(part=name)}?.{what}")


def wait_part(page: Chrome, name: str, load: str, timeout: float = 15) -> None:
    page.wait_for(
        f"{PART.format(part=name)}?.dataset.load === {load!r}",
        timeout=timeout,
        message=f"the {name} part {load}",
    )


def tailor(page: Chrome, url: str) -> float:
    """Open Resume Tailor; the seconds until Start could be used."""
    page.set_viewport(1440, 960)
    page.navigate(url + "/#tailor")
    started = time.monotonic()
    page.wait_for(
        "document.getElementById('page-tailor').dataset.shell === 'start'",
        timeout=15,
        message="Start usable",
    )
    return time.monotonic() - started


def tab(page: Chrome, name: str) -> None:
    page.evaluate(f"document.getElementById('tp-tab-{name}').click()")
    page.wait_for(
        f"document.getElementById('page-tailor').dataset.shell === {name!r}",
        timeout=15,
        message=f"{name} usable",
    )


def text(page: Chrome) -> str:
    return str(page.evaluate("document.getElementById('page-tailor').innerText"))


def paste_an_ad(page: Chrome) -> None:
    page.evaluate(
        """(() => {
      const put = (id, value) => {
        const node = document.getElementById(id);
        node.value = value;
        node.dispatchEvent(new Event('input', { bubbles: true }));
      };
      put('tp-ad-title', 'Revenue Operations Lead (fictional)');
      put('tp-ad-text', 'Own HubSpot workflow automation and pipeline reporting for sales.');
    })()"""
    )


# -- Resume Tailor ------------------------------------------------------------


def test_start_and_make_are_usable_while_the_job_lists_never_answer(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": LISTS, "mode": "hold"}])
    seconds = tailor(page, tailor_server["url"])
    assert seconds < 5, f"Start took {seconds:.1f}s to be usable with the job lists held"
    wait_part(page, "made", "ready")
    wait_part(page, "evidence", "ready")
    assert part(page, "waiting") == "loading"
    assert page.evaluate("document.getElementById('page-tailor').dataset.state") == "loading"
    page.wait_for(
        f"{PART.format(part='waiting')}.querySelector('.tp-part__slow')",
        timeout=15,
        message="the slow note",
    )
    assert "taking longer than usual" in text(page)
    # The hero's own button needs no job list.
    page.evaluate("document.getElementById('tp-start-make').click()")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.shell === 'make'", message="Make usable"
    )
    assert part(page, "jobs") == "loading"
    assert page.evaluate("document.getElementById('tp-check').disabled") is True
    paste_an_ad(page)
    page.wait_for("!document.getElementById('tp-check').disabled", message="Check enabled")
    assert (
        page.evaluate("document.querySelectorAll('[aria-labelledby=tp-make-2] .tp-radio').length")
        >= 1
    )
    lists = tailor_lists(page)
    assert len(lists) == 2, f"Start and Make share the two reads in flight: {lists}"
    assert all("narrowings=later" in u for u in lists), lists
    assert any(
        "saved_only=true" in u and "group_duplicates=1" in u and "limit=24" in u for u in lists
    )
    assert any(
        "saved_only" not in u and "group_duplicates=1" in u and "limit=8" in u for u in lists
    )


def test_a_job_list_that_never_answers_is_cancelled_and_said_never_shown_as_empty(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": LISTS, "mode": "hold"}])
    tailor(page, tailor_server["url"])
    wait_part(page, "waiting", "error", timeout=70)
    said = str(part(page, "waiting", "innerText"))
    assert "did not answer in time" in said
    assert "You have not saved a job yet" not in said
    aborted = page.evaluate("window.__aborted")
    assert sorted(k for k in TAILOR_LISTS for u in aborted if k in u) == sorted(TAILOR_LISTS)
    assert page.evaluate("document.getElementById('page-tailor').dataset.state") == "start"
    rules(page, [])
    page.evaluate(f"{PART.format(part='waiting')}.querySelector('button').click()")
    wait_part(page, "waiting", "ready")


def test_failures_are_said_in_their_part_never_as_empty_lists(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(
        page,
        [
            {"match": LISTS, "mode": "status", "status": 500},
            {"match": "/api/career", "mode": "status", "status": 503},
        ],
    )
    tailor(page, tailor_server["url"])
    wait_part(page, "waiting", "error")
    wait_part(page, "evidence", "error")
    wait_part(page, "made", "ready")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="settled"
    )
    shown = text(page)
    assert "The job list could not be read just now" in shown
    assert "This part could not be read just now" in shown
    assert "You have not saved a job yet" not in shown
    assert "Nothing is confirmed yet" not in shown
    assert "Every saved job already has a resume" not in shown
    for name in ("waiting", "evidence"):
        assert page.evaluate(f"{PART.format(part=name)}.querySelector('[role=alert]') !== null")
    tab(page, "make")
    wait_part(page, "jobs", "error")
    assert "There are no jobs to choose from yet" not in text(page)
    paste_an_ad(page)
    page.wait_for("!document.getElementById('tp-check').disabled", message="Check enabled")
    # Back on Start, the reads answer again and the lists are drawn.
    rules(page, [])
    tab(page, "start")
    wait_part(page, "waiting", "ready")
    wait_part(page, "evidence", "ready")
    assert "confirmed" in str(part(page, "evidence", "innerText"))
    assert page.console_errors() == []


def test_coming_back_reads_the_lists_again_only_after_something_changed(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [])
    tailor(page, tailor_server["url"])
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'start'", message="start"
    )
    assert len(tailor_lists(page)) == 2
    for _ in range(2):
        tab(page, "make")
        wait_part(page, "jobs", "ready")
        tab(page, "start")
        page.wait_for(
            "document.getElementById('page-tailor').dataset.state === 'start'", message="again"
        )
    assert len(tailor_lists(page)) == 2, "coming back to Start or Make read the lists again"
    assert len(calls(page, "/api/jobs/freshness")) >= 1, "reuse is checked, not assumed"
    # A real change: a job is saved. The next visit reads the lists again and shows it.
    tab(page, "make")
    wait_part(page, "jobs", "ready")
    job = page.evaluate(
        "document.querySelector('#page-tailor [data-part=jobs] .tp-radio[data-job]').dataset.job"
    )
    status = page.evaluate(
        f"fetch('/api/jobs/{job}/saved', {{method: 'PATCH', headers: {{'Content-Type':"
        " 'application/json'}, body: JSON.stringify({saved: true})}).then((r) => r.status)"
    )
    assert status == 200
    tab(page, "start")
    wait_part(page, "waiting", "ready")
    assert len(tailor_lists(page)) == 4
    assert page.evaluate(
        f"{PART.format(part='waiting')}.querySelector('[data-job={json.dumps(job)}]') !== null"
    ), "the job saved a moment ago is listed as waiting for a resume"


def test_fast_switches_draw_only_the_last_tab_and_share_the_reads(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": LISTS, "mode": "delay", "ms": 1500}])
    tailor(page, tailor_server["url"])
    for name in ("make", "resumes", "start", "experience", "make"):
        page.evaluate(f"document.getElementById('tp-tab-{name}').click()")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'make'",
        timeout=15,
        message="make",
    )
    wait_part(page, "jobs", "ready")
    assert page.evaluate("document.getElementById('page-tailor').dataset.tab") == "make"
    assert page.evaluate("document.querySelectorAll('#page-tailor .tp-make').length") == 1
    assert page.evaluate("document.querySelector('#page-tailor [data-part=waiting]')") is None
    assert len(tailor_lists(page)) == 2


def _profiles(active: str) -> dict[str, Any]:
    """Local profiles switched on, `active` open: two synthetic profiles."""
    rows = [
        {"id": "prof-a-synthetic", "label": "Example A", "color": "teal"},
        {"id": "prof-b-synthetic", "label": "Example B", "color": "plum"},
    ]
    return {
        "enabled": True,
        "active": next(r for r in rows if r["id"] == active),
        "profiles": [{**r, "active": r["id"] == active, "original": False} for r in rows],
        "note": "Synthetic profiles.",
    }


@pytest.mark.parametrize("switched", [True, False])
def test_an_answer_read_for_another_profile_is_never_drawn(
    page: Chrome, tailor_server: dict[str, Any], switched: bool
) -> None:
    intercept(
        page,
        [
            {"match": "/api/profiles", "mode": "json", "body": _profiles("prof-a-synthetic")},
            {"match": LISTS, "mode": "hold"},
        ],
    )
    tailor(page, tailor_server["url"])
    page.wait_for(
        "window.__calls.filter((c) => c.url.includes('limit=24') || c.url.includes('limit=8&'))"
        ".length === 2",
        message="both lists asked",
    )
    who = "import('/js/api.js').then((m) => m.getLocalProfile())"
    assert page.evaluate(who) == "prof-a-synthetic", "the lists were asked for profile A"
    if switched:
        # Another profile is open before they answer (a real switch also
        # reloads the page; this is the answer that arrives first).
        page.evaluate("import('/js/api.js').then((m) => m.setLocalProfile('prof-b-synthetic'))")
    page.evaluate("window.__held.splice(0).forEach((go) => go())")
    if switched:
        wait_part(page, "waiting", "error")
        assert page.evaluate(f"{PART.format(part='waiting')}.querySelector('[data-job]')") is None
    else:
        wait_part(page, "waiting", "ready")


def test_tips_are_drawn_while_the_saved_jobs_never_answer(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": LISTS, "mode": "hold"}])
    tailor(page, tailor_server["url"])
    page.evaluate("document.getElementById('tp-tab-tips').click()")
    page.wait_for(
        "document.getElementById('page-tailor').dataset.state === 'tips'",
        timeout=25,
        message="tips drawn",
    )
    assert "Aim for 2 pages" in text(page)
    assert calls(page, "saved_only=true&limit=50&narrowings=later")


def test_the_new_words_are_in_portuguese_too(page: Chrome, tailor_server: dict[str, Any]) -> None:
    intercept(page, [{"match": LISTS, "mode": "status", "status": 500}])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#tailor")
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    page.reload()
    page.wait_for_app()
    wait_part(page, "waiting", "error")
    assert "Não foi possível ler a lista de vagas agora" in str(part(page, "waiting", "innerText"))
    page.evaluate("localStorage.removeItem('careerAgent.locale.v1')")


# -- Home ---------------------------------------------------------------------


def test_home_asks_first_run_beside_home_not_after_it(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": "/api/home", "not": "/api/home/", "mode": "delay", "ms": 1500}])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#home")
    page.wait_for(
        "['dashboard', 'setup'].includes(document.querySelector('#page-home .home')?.dataset.view)",
        timeout=20,
        message="Home drawn",
    )
    home = page.evaluate(
        "window.__calls.find((c) => c.url.endsWith('/api/home') && c.end !== null)"
    )
    first = page.evaluate("window.__calls.find((c) => c.url.includes('/api/firstrun'))")
    assert home and first
    assert first["at"] < home["end"], "first-run was asked only after Home answered"


def test_home_says_a_failed_read_and_offers_to_try_again(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": "/api/home", "not": "/api/home/", "mode": "status", "status": 500}])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#home")
    page.wait_for(
        "document.querySelector('.state__msg') !== null", timeout=20, message="the failure"
    )
    assert page.evaluate(
        "[...document.querySelectorAll('#page-home button')]"
        ".some((b) => b.innerText.trim() === 'Retry')"
    )


# -- the resumes library, empty ----------------------------------------------


@pytest.fixture
def empty_server(tmp_path: Path, committed_config: Path) -> Iterator[dict[str, Any]]:
    yield from _serve(tmp_path, committed_config, lambda conn: None)


def library(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/#resume")
    page.wait_for("document.querySelector('.rvw__tab')", timeout=20, message="Resumes")
    page.evaluate(
        "[...document.querySelectorAll('.rvw__tab')]"
        ".find((b) => b.innerText === 'My resumes').click()"
    )
    page.wait_for(
        "document.querySelector('.rvw__empty[data-empty=library]')", timeout=15, message="empty"
    )


SOURCES = "document.querySelector('.rvw__empty[data-empty=library] [data-sources]')"


def test_an_empty_library_says_where_a_cv_added_to_the_profile_went(
    page: Chrome, empty_server: dict[str, Any]
) -> None:
    documents = {
        "documents": [
            {"kind": "cv", "id": "cv-synthetic", "name": "Example CV (fictional).pdf",
             "status": "active", "confirmed": 12, "waiting": 0, "total": 12},
            {"kind": "cv", "id": "cv-old", "name": "Old CV (fictional).pdf",
             "status": "archived", "confirmed": 3, "waiting": 0, "total": 3},
        ],
        "counts": {},
    }  # fmt: skip
    intercept(page, [{"match": "/api/documents", "mode": "json", "body": documents}])
    library(page, empty_server["url"])
    page.wait_for(f"{SOURCES}.dataset.sources === '1'", message="the documents note")
    box = str(page.evaluate("document.querySelector('.rvw__empty[data-empty=library]').innerText"))
    assert "No resumes here yet." in box
    assert "This list holds the resumes you create or import to edit here." in box
    assert "Your profile has 1 document in Documents." in box
    assert "You confirmed 12 statements from them." in box
    assert "build your Master" in box
    for label in ("Go to Resumes home", "Import PDF or DOCX", "Open Documents"):
        assert label in box, label
    # Opening the page created nothing: no Master, no import, no write.
    assert page.evaluate("window.__calls.filter((c) => c.method !== 'GET').length") == 0
    page.evaluate(
        "[...document.querySelectorAll('.rvw__empty button')]"
        ".find((b) => b.innerText === 'Open Documents').click()"
    )
    page.wait_for("!document.getElementById('page-documents').hidden", message="Documents")


def test_an_empty_library_claims_nothing_it_could_not_read(
    page: Chrome, empty_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": "/api/documents", "mode": "status", "status": 500}])
    library(page, empty_server["url"])
    time.sleep(1.5)
    assert page.evaluate(f"{SOURCES}.dataset.sources") == "unknown"
    assert page.evaluate(f"{SOURCES}.textContent") == ""
    assert "This list holds the resumes" in str(
        page.evaluate("document.querySelector('.rvw__empty').innerText")
    )


def test_an_empty_library_with_no_documents_says_no_count(
    page: Chrome, empty_server: dict[str, Any]
) -> None:
    intercept(page, [])
    library(page, empty_server["url"])
    page.wait_for(f"{SOURCES}.dataset.sources === 'none'", message="answered: no documents")
    assert page.evaluate(f"{SOURCES}.textContent") == ""


def test_a_library_with_a_master_is_not_empty(page: Chrome, tailor_server: dict[str, Any]) -> None:
    intercept(page, [])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#resume")
    page.wait_for("document.querySelector('.rvw__tab')", timeout=20, message="Resumes")
    page.evaluate(
        "[...document.querySelectorAll('.rvw__tab')]"
        ".find((b) => b.innerText === 'My resumes').click()"
    )
    page.wait_for("document.querySelector('.rvl__row')", timeout=15, message="a resume row")
    assert page.evaluate("document.querySelector('.rvw__empty[data-empty=library]')") is None


# -- the one-line refresh bar -------------------------------------------------


def running(kind: str, current: str) -> dict[str, Any]:
    started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 400))
    return {
        "running": True,
        "now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scoring": {"running": False, "done": 0, "total": 0},
        "run": {
            "run_id": "run-synthetic", "status": "running", "started_at": started,
            "finished_at": None, "boards_total": 29, "boards_done": 0, "kind": kind,
            "current": current, "current_started_at": started, "plan": [], "not_refreshed": [],
            "current_detail": None, "current_boards": None,
        },
    }  # fmt: skip


@pytest.mark.parametrize(
    ("kind", "current", "said", "never"),
    [
        ("auto", "rippling:example-board", "Reading a site now (6 min", "rippling:example-board"),
        ("find", "Example Jobs Board", "Now reading Example Jobs Board (6 min", None),
    ],
)
def test_the_one_line_bar_says_a_long_first_site_is_being_read(
    page: Chrome,
    tailor_server: dict[str, Any],
    kind: str,
    current: str,
    said: str,
    never: str | None,
) -> None:
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running(kind, current)}])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#tailor")
    page.wait_for(
        "!document.getElementById('collectbar').hidden", timeout=20, message="the refresh bar"
    )
    page.wait_for(
        f"document.getElementById('collectbar').innerText.includes({said!r})",
        timeout=10,
        message="the site being read",
    )
    bar = str(page.evaluate("document.getElementById('collectbar').innerText"))
    assert "0 of 29" in bar
    if never:
        assert never not in bar

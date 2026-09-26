"""The post-merge continuity pass, held in a real browser.

Each test here reproduced a defect before the fix it guards:

* finding jobs lived in whichever screen started it. Leaving Home and coming
  back rebuilt Home without the progress, so a run that was still going looked
  stopped, and every redraw of the setup's last card orphaned a poll that kept
  asking `/api/retrieval` for the rest of the run;
* Discover's toolbar -- the filters, the sort, the views -- stayed hidden
  after the first collection filled an empty database, until a reload;
* the browser tab wore a generic icon instead of the Career Agent star;
* Home listed setup steps that had been finished long ago as open tasks;
* a job offered "Open Resume Tailor Beta" to somebody who had given Career
  Agent nothing about their career;
* switching to Portuguese while on Career Evidence left the page in English,
  and every date printed an English month;
* the setup's phrase bounds surfaced as a server refusal worded like a
  database constraint.

Nothing leaves the machine: every source's work is replaced by an offline
stand-in, and every database is synthetic and temporary.
"""

from __future__ import annotations

import json
import re
import shutil
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from tests.browser.chrome import Chrome
from tests.browser.conftest import DEMO_FILE

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig, build_server

REPO = Path(__file__).resolve().parents[2]
LOCALE_KEY = "careerAgent.locale.v1"
#: Stop exists from the first frame and is hidden until the server has a run.
VISIBLE_STOP = (
    "document.querySelector('#setup-stop') && !document.querySelector('#setup-stop').hidden"
)
ENGLISH_MONTH = re.compile(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b")


@dataclass
class Live:
    base: str
    app: JobsApi
    config_dir: Path
    db: Path
    #: `(monotonic seconds, method, path)` for every API request served.
    log: list[tuple[float, str, str]] = field(default_factory=list)
    #: Released to let the stand-in sources finish.
    gate: threading.Event = field(default_factory=threading.Event)

    def count(self, path: str, since: float, until: float | None = None) -> int:
        return sum(
            1
            for at, method, seen in list(self.log)
            if seen == path and method == "GET" and at >= since and (until is None or at < until)
        )


def _serve(tmp_path: Path, *, described: bool = False) -> Iterator[Live]:
    config_dir = tmp_path / "config"
    shutil.copytree(REPO / "config", config_dir, ignore=shutil.ignore_patterns("*.local.*"))
    if described:
        # A search already described, as it is by the time somebody finds jobs:
        # the same worked example the demo corpus is scored under.
        shutil.copyfile(config_dir / "search.worked-example.yaml", config_dir / "search.local.yaml")
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "continuity")
    finally:
        conn.close()
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = int(probe.getsockname()[1])
    probe.close()
    app = JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=port), quiet=True)
    live = Live(f"http://127.0.0.1:{port}", app, config_dir, db)
    original = app.handle_api

    def logged(method, path, query, body):
        live.log.append((time.monotonic(), method, path))
        return original(method, path, query, body)

    app.handle_api = logged  # type: ignore[method-assign]
    httpd = build_server(app)
    httpd.handle_error = lambda request, client_address: None  # type: ignore[method-assign]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield live
    finally:
        live.gate.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=10)


@pytest.fixture
def fresh(tmp_path: Path) -> Iterator[Live]:
    yield from _serve(tmp_path)


@pytest.fixture
def described(tmp_path: Path) -> Iterator[Live]:
    yield from _serve(tmp_path, described=True)


def _offline_sources(
    monkeypatch: pytest.MonkeyPatch, live: Live, *, fill: bool = False, hold: bool = True
) -> None:
    """Every source becomes an offline stand-in.

    `hold` keeps the FIRST source reading until `live.gate` is released, so a
    test can walk around the app while a run is really going. `fill` makes the
    first source store the invented demo corpus, as a first collection would.
    """
    first = threading.Event()

    def work_for(key: str):
        def work(state, cancel) -> None:
            if first.is_set():
                return
            first.set()
            if fill:
                conn = connect(live.db)
                try:
                    config, _ = load_search_config(live.config_dir)
                    seed_demo(conn, config, source=DEMO_FILE)
                finally:
                    conn.close()
            if hold:
                while not live.gate.is_set() and not cancel.is_set():
                    live.gate.wait(0.1)

        return work

    monkeypatch.setattr(
        live.app, "_collect_work", lambda limit, provider=None: work_for(f"collect:{provider}")
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.feed_work", lambda db, stage, **_: work_for(stage)
    )
    monkeypatch.setattr(
        "career_agent.web.source_refresh.employer_board_work",
        lambda app, families: work_for("employer-boards"),
    )
    monkeypatch.setattr(live.app.rescore, "start", lambda work, run_id: None)


def _click(page: Chrome, selector: str) -> None:
    page.evaluate(f"document.querySelector({json.dumps(selector)}).click()")


def _text(page: Chrome, selector: str) -> str:
    return str(page.evaluate(f"document.querySelector({json.dumps(selector)})?.innerText || ''"))


def _go(page: Chrome, name: str) -> None:
    _click(page, f'.topnav__link[data-page="{name}"]')


def _start_from_the_last_card(page: Chrome, live: Live) -> None:
    page.set_viewport(1280, 900)
    page.navigate(live.base)
    page.wait_for("document.querySelector('#setup-later')", message="the guided setup")
    _click(page, "#setup-later")
    page.wait_for("document.querySelector('#firstrun-find-jobs')")
    _click(page, "#firstrun-find-jobs")
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'ready'")
    _click(page, "#setup-find")
    page.wait_for(VISIBLE_STOP, message="the run's progress")


# =========================================================================
# 1 + 2: one run, every page, bounded polling
# =========================================================================


def test_a_run_survives_navigation_and_every_page_shows_it(
    page: Chrome, fresh: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_sources(monkeypatch, fresh)
    _start_from_the_last_card(page, fresh)

    for name in ("jobs", "evidence", "settings", "profile"):
        _go(page, name)
        page.wait_for(
            "!document.querySelector('#collectbar').hidden"
            " && document.querySelector('#collectbar').innerText.includes('of')",
            message=f"the run on {name}",
        )
        assert fresh.app.retrieval.running, f"leaving Home for {name} stopped the run"

    _go(page, "home")
    # The same card, still drawing the same run -- not the start of Home, and
    # not the six steps with the progress gone.
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'ready'")
    page.wait_for(VISIBLE_STOP)
    assert page.evaluate("document.querySelectorAll('.firstrun__step').length") == 0
    assert page.evaluate("document.querySelector('#collectbar').hidden"), (
        "Home shows the run itself; the page-wide line would say it twice"
    )
    assert fresh.app.retrieval.running

    fresh.gate.set()
    page.wait_for("document.querySelector('#setup-see')", timeout=30)
    assert "Done" in _text(page, ".setup__find")

    # Finished stays finished after navigation: no restart, no welcome card.
    _go(page, "jobs")
    page.wait_for("document.querySelector('#collectbar-dismiss')")
    _go(page, "home")
    page.wait_for(
        "document.querySelector('.home__sec--collect') || document.querySelector('#setup-see')"
    )
    assert page.evaluate("document.querySelector('.setup__card')?.dataset.step") != "welcome"
    assert page.console_errors() == []


def test_polling_is_one_loop_whatever_the_screens_do(
    page: Chrome, fresh: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Measured before the fix: 2 requests per 4 s on the last card, 8 after
    eight language switches, and 5 on Discover from loops nothing could stop."""
    _offline_sources(monkeypatch, fresh)
    _start_from_the_last_card(page, fresh)

    for _ in range(4):
        for locale in ("pt-BR", "en"):
            page.evaluate(f"document.querySelector('[data-locale=\"{locale}\"]').click()")
            time.sleep(0.05)
    for name in ("jobs", "home", "settings", "home", "evidence", "home"):
        _go(page, name)
        time.sleep(0.1)

    start = time.monotonic()
    time.sleep(8)
    asked = fresh.count("/api/retrieval", start)
    # One loop at one request every two seconds is four; one more for a poll
    # already in flight at either edge of the window.
    assert asked <= 5, f"{asked} progress requests in 8 s: more than one loop is polling"
    assert fresh.app.retrieval.running

    fresh.gate.set()
    page.wait_for("document.querySelector('#setup-see')", timeout=30)
    time.sleep(0.5)
    idle = time.monotonic()
    time.sleep(5)
    assert fresh.count("/api/retrieval", idle) == 0, "something still polls with nothing running"


def test_the_progress_says_what_it_is_doing_and_invents_no_estimate(
    page: Chrome, fresh: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    _offline_sources(monkeypatch, fresh)
    _start_from_the_last_card(page, fresh)
    page.wait_for(
        "document.querySelector('.setup__find').innerText.includes('Now reading')",
        message="the source being read, by name",
    )
    first = _text(page, ".setup__find")
    assert re.search(r"Checked \d+ of \d+ job sources", first), first
    assert "so far" in first
    assert "No time left is shown" in first
    assert not re.search(r"(remaining|left)\b.*\d", first.split("No time left")[0]), first
    bar = page.evaluate(
        "(() => { const b = document.querySelector('.setup__find [role=progressbar]');"
        " return b && [b.getAttribute('aria-valuenow'), b.getAttribute('aria-valuemax')]; })()"
    )
    assert bar and int(bar[1]) > 0, bar
    # The seconds move without a request per second.
    before = fresh.count("/api/retrieval", 0)
    time.sleep(2.2)
    later = _text(page, ".setup__find")
    assert later != first, "elapsed time did not move"
    assert fresh.count("/api/retrieval", 0) - before <= 2

    focused = page.evaluate(
        "(() => { const s = document.querySelector('#setup-stop'); s.focus();"
        " return document.activeElement === s; })()"
    )
    assert focused
    time.sleep(1.5)
    assert page.evaluate("document.activeElement?.id") == "setup-stop", (
        "the ticking progress took keyboard focus off Stop"
    )
    _click(page, "#setup-stop")
    page.wait_for(
        "document.querySelector('.setup__find').innerText.includes('Stopped')", timeout=20
    )


# =========================================================================
# 3: Discover's filters after the first collection
# =========================================================================


def test_filters_come_back_when_the_first_collection_lands(
    page: Chrome, described: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    fresh = described
    _offline_sources(monkeypatch, fresh, fill=True, hold=False)
    page.set_viewport(1280, 900)
    page.navigate(fresh.base + "/#jobs")
    page.wait_for("document.querySelector('#empty-find-jobs')", message="the empty Discover")
    assert (
        page.evaluate("getComputedStyle(document.querySelector('.topbar__controls')).display")
        == "none"
    )

    _click(page, "#empty-find-jobs")
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'ready'")
    _click(page, "#setup-find")
    page.wait_for("document.querySelector('#setup-see')", timeout=30)
    _click(page, "#setup-see")

    # No reload: the list and its toolbar are there because the page asked again.
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.wait_for(
        "getComputedStyle(document.querySelector('.topbar__controls')).display !== 'none'",
        message="the Discover toolbar",
    )
    assert page.evaluate("document.querySelector('#rail-toggle').getBoundingClientRect().width") > 0
    _click(page, "#rail-toggle")
    page.wait_for("!document.querySelector('#filterpanel').hidden")
    assert page.evaluate("document.querySelectorAll('#filters-host details').length") > 0
    assert page.console_errors() == []


def test_filters_are_on_discover_at_every_width(page: Chrome, pristine_server: str) -> None:
    for width, height in ((1440, 960), (1280, 720), (390, 844)):
        page.set_viewport(width, height, mobile=width < 500)
        page.navigate(pristine_server + "/#jobs")
        page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
        page.wait_for(
            "document.querySelector('#rail-toggle').getBoundingClientRect().width > 0",
            message=f"the filters toggle at {width}px",
        )
        if page.evaluate("document.querySelector('#filterpanel').hidden"):
            _click(page, "#rail-toggle")
        page.wait_for(
            "document.querySelector('#filterpanel').getBoundingClientRect().height > 0",
            message=f"the filters at {width}px",
        )


# =========================================================================
# 4: the tab wears the star
# =========================================================================


def test_the_tab_icon_is_the_career_agent_star(page: Chrome, pristine_server: str) -> None:
    page.navigate(pristine_server)
    icons = page.evaluate(
        "[...document.querySelectorAll('link[rel~=icon]')].map(l => l.getAttribute('href'))"
    )
    assert icons and icons[0] == "star.svg", icons
    served = page.evaluate(
        "Promise.all([...document.querySelectorAll('link[rel~=icon]')].map(l =>"
        " fetch(l.href).then(r => [r.status, r.headers.get('content-type')])))"
    )
    assert all(status == 200 for status, _ in served), served
    assert "svg" in served[0][1]
    assert not (REPO / "src/career_agent/web/static/favicon.svg").exists()


# =========================================================================
# 5: Home after setup
# =========================================================================


def test_home_lists_only_the_setup_that_is_left(page: Chrome, pristine_server: str) -> None:
    page.navigate(pristine_server)
    page.wait_for(
        "document.querySelector('.home__sec--start') || document.querySelector('.home__metrics')"
    )
    state = page.evaluate("fetch('/api/firstrun').then(r => r.json())")
    left = [step["key"] for step in state["steps"] if not step["done"]]
    done = [step["key"] for step in state["steps"] if step["done"]]
    assert left and done, "the demo corpus no longer has both kinds of step; this test is blind"
    page.wait_for("document.querySelectorAll('.firstrun__step').length > 0")
    shown = page.evaluate(
        "[...document.querySelectorAll('.firstrun__step')].map(n => n.dataset.step)"
    )
    assert shown == left, shown
    summary = _text(page, ".home__sec--start summary")
    assert summary == f"Finish setup · {len(left)} left", summary
    assert page.evaluate("document.querySelectorAll('.firstrun__step.is-done').length") == 0
    # "Change my answers" is where the finished ones are changed.
    _go(page, "settings")
    page.wait_for("document.querySelector('#settings-open-setup')")


def test_a_finished_setup_is_not_on_home_at_all(
    page: Chrome, pristine_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    page.navigate(pristine_server)
    page.wait_for("document.querySelector('.home__metrics')")
    # Every step answered, as the server would report it.
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) => {"
        "  if (String(url).endsWith('/api/firstrun')) {"
        "    return real(url, opts).then(r => r.json()).then(j => {"
        "      j.steps = j.steps.map(s => ({ ...s, done: true })); j.fresh = false;"
        "      return new Response(JSON.stringify(j), {status: 200,"
        "        headers: {'Content-Type': 'application/json'}}); });"
        "  } return real(url, opts); }; })()"
    )
    _go(page, "jobs")
    _go(page, "home")
    page.wait_for("document.querySelector('.home__metrics')")
    time.sleep(0.3)
    assert not page.evaluate("Boolean(document.querySelector('.home__sec--start'))")
    assert page.evaluate("document.querySelectorAll('.firstrun__step').length") == 0


# =========================================================================
# 6: Resume Tailor is offered as far as it can be used
# =========================================================================


def _open_first_job(page: Chrome, base: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(base + "/#jobs")
    # `[data-job-id]`, not `.card`: a loading skeleton is a `.card` too.
    page.wait_for("document.querySelector('#list [data-job-id]')", timeout=20)
    page.evaluate("document.querySelector('#list [data-job-id]').click()")
    page.wait_for(
        "document.querySelector('.d-tailor')?.dataset.tailor", message="the Tailor section"
    )


def test_tailor_waits_for_career_context_and_says_what_to_add(
    page: Chrome, pristine_server: str
) -> None:
    _open_first_job(page, pristine_server)
    assert page.evaluate("document.querySelector('.d-tailor').dataset.tailor") == "needs-career"
    text = _text(page, ".d-tailor")
    assert "works from your CV" in text, text
    assert "Open Resume Tailor Beta" not in text.split("?")[0], (
        "Tailor still reads as the next step"
    )
    # Access is kept for a CV already given to Tailor itself.
    assert page.evaluate(
        "document.querySelector('#drawer-open-tailor').getAttribute('href')"
    ).startswith("/resume-tailor?job=")
    _click(page, "#drawer-add-career")
    page.wait_for("!document.querySelector('#page-documents').hidden", message="Documents")
    assert page.console_errors() == []


def test_tailor_is_the_next_step_once_career_context_exists(
    page: Chrome, pristine_server: str
) -> None:
    page.navigate(pristine_server)
    page.evaluate(
        "fetch('/api/evidence', {method: 'POST', headers: {'Content-Type': 'application/json'},"
        " body: JSON.stringify({claim_type: 'SKILL', text: 'Invented spreadsheet modelling'})})"
        ".then(r => r.status)"
    )
    _open_first_job(page, pristine_server)
    page.wait_for("document.querySelector('.d-tailor').dataset.tailor === 'ready'")
    assert "Open Resume Tailor Beta" in _text(page, ".d-tailor")


# =========================================================================
# 7: Career Evidence in Portuguese
# =========================================================================


@pytest.fixture
def package_live(tmp_path: Path, committed_config: Path) -> Iterator[str]:
    from tests.browser.test_career_evidence import _serve as serve_evidence
    from tests.browser.test_career_evidence import _stage, synthetic_package

    db = tmp_path / "package.db"
    _stage(db, committed_config, synthetic_package())
    yield from serve_evidence(db, committed_config)


def test_switching_to_portuguese_on_career_evidence_translates_the_page(
    page: Chrome, package_live: str
) -> None:
    from tests.browser.test_career_evidence import open_evidence

    open_evidence(page, package_live)
    page.wait_for("document.querySelector('#page-manage').innerText.includes('Your experiences')")
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('#page-manage').innerText.includes('Suas experiências')",
        message="the evidence page in Portuguese",
    )
    text = _text(page, "#page-manage")
    for english in (
        "Your experiences",
        "Sources and imports",
        "Add experience",
        "Needs organizing",
    ):
        assert english not in text, f"{english!r} stayed English after the switch"

    # The date an import was read carries the reader's month.
    page.evaluate("document.querySelectorAll('#page-manage details').forEach(d => d.open = true)")
    read_line = str(
        page.evaluate(
            "[...document.querySelectorAll('#page-manage *')].map(n => n.textContent)"
            ".find(t => t.includes('lidos em') && t.length < 200) || ''"
        )
    )
    assert read_line, "the import's read date is not on the page"
    assert not ENGLISH_MONTH.search(read_line), read_line
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")


def test_a_review_open_in_portuguese_stays_open_when_the_language_changes(
    page: Chrome, package_live: str
) -> None:
    from tests.browser.test_career_evidence import open_package

    page.navigate(package_live)
    page.evaluate(f"localStorage.setItem({json.dumps(LOCALE_KEY)}, 'en')")
    open_package(page, package_live)
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    page.wait_for(
        "document.querySelector('#page-manage').innerText.includes('Voltar às suas evidências')",
        message="the open review, translated in place",
    )
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")


# =========================================================================
# 8: the phrase bounds, explained where the typing happens
# =========================================================================


def _to_work_card(page: Chrome, live: Live) -> None:
    page.set_viewport(1280, 900)
    page.navigate(live.base)
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'welcome'")
    _click(page, "#setup-next")
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'work'")


def _type(page: Chrome, selector: str, value: str) -> None:
    page.evaluate(
        f"(() => {{ const n = document.querySelector({json.dumps(selector)});"
        f" n.value = {json.dumps(value)};"
        " n.dispatchEvent(new Event('input', {bubbles: true})); })()"
    )


def test_too_many_or_too_long_phrases_are_named_before_anything_is_sent(
    page: Chrome, fresh: Live
) -> None:
    _to_work_card(page, fresh)
    _type(page, "#setup-work", "\n".join(f"kind of work {n}" for n in range(21)))
    _click(page, "#setup-next")
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    message = _text(page, "#setup-error")
    assert "21 lines" in message and "20" in message, message
    assert "characters" not in message

    _type(page, "#setup-work", "customer onboarding\n" + "a very long sentence " * 8)
    _click(page, "#setup-next")
    page.wait_for("document.querySelector('#setup-error').textContent.includes('Line 2')")
    assert (
        page.evaluate("document.querySelector('#setup-work').getAttribute('aria-invalid')")
        == "true"
    )
    assert not any(path == "/api/first-search" for _, _, path in fresh.log), (
        "a refused answer was still sent"
    )
    assert (
        _text(page, ".setup__card")
        and page.evaluate("document.querySelector('.setup__card').dataset.step") == "work"
    )

    _type(page, "#setup-work", "customer onboarding")
    _click(page, "#setup-next")
    # The optional "roles in mind" card comes next.
    page.wait_for("document.querySelector('.setup__card')?.dataset.step === 'roles'")
    config, _ = load_search_config(fresh.config_dir)
    assert config.lexicon


def test_the_portuguese_message_is_portuguese(page: Chrome, fresh: Live) -> None:
    _to_work_card(page, fresh)
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    _type(page, "#setup-work", "\n".join(f"trabalho {n}" for n in range(25)))
    _click(page, "#setup-next")
    page.wait_for("document.querySelector('#setup-error').textContent.length > 0")
    assert "São 25 linhas" in _text(page, "#setup-error")
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")


def test_pressing_find_while_a_status_request_is_in_flight_still_shows_the_run(
    page: Chrome, fresh: Live, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The last card asks once on arrival. A Find pressed before that answer
    came back used to share it -- an answer from BEFORE the run existed -- and
    the watcher concluded nothing was running and never asked again."""
    _offline_sources(monkeypatch, fresh)
    page.set_viewport(1280, 900)
    page.navigate(fresh.base)
    page.wait_for("document.querySelector('#setup-later')", message="the guided setup")
    _click(page, "#setup-later")
    page.wait_for("document.querySelector('#firstrun-find-jobs')")
    # Every progress request now takes a second and a half to answer.
    page.evaluate(
        "(() => { const real = window.fetch; window.fetch = (url, opts) => {"
        "  const answer = real(url, opts);"
        "  return String(url).includes('/api/retrieval?funnel=false')"
        "    ? answer.then((r) => new Promise((ok) => setTimeout(() => ok(r), 1500)))"
        "    : answer; }; })()"
    )
    _click(page, "#firstrun-find-jobs")
    page.wait_for("document.querySelector('#setup-find')")
    _click(page, "#setup-find")
    page.wait_for(VISIBLE_STOP, timeout=20, message="the run's progress")
    assert fresh.app.retrieval.running
    fresh.gate.set()
    page.wait_for("document.querySelector('#setup-see')", timeout=30)


def test_switching_language_on_evidence_never_discards_what_is_being_typed(
    page: Chrome, package_live: str
) -> None:
    """The redraw that translates the page must not cost an unsaved sentence.

    Switching language redraws Career Evidence so it stops being English. A
    sentence half-written into one of its boxes is the person's work, and a
    redraw would throw it away: while anything typed there is unsaved, the
    page keeps its nodes and translates on its next arrival instead.
    """
    from tests.browser.test_career_evidence import open_evidence

    open_evidence(page, package_live)
    page.wait_for("document.querySelector('#page-manage textarea')", message="a text box")
    page.evaluate(
        "(() => { const box = document.querySelector('#page-manage textarea');"
        " box.value = 'Ran the weekly supplier review';"
        " box.dispatchEvent(new Event('input', {bubbles: true})); })()"
    )
    page.evaluate("document.querySelector('[data-locale=\"pt-BR\"]').click()")
    time.sleep(1.5)
    kept = page.evaluate(
        "[...document.querySelectorAll('#page-manage textarea')]"
        ".some((box) => box.value === 'Ran the weekly supplier review')"
    )
    assert kept, "switching language threw away an unsaved sentence"

    # Nothing typed: the next arrival translates the page as before.
    _go(page, "home")
    page.navigate(f"{package_live}/?debug=statements")
    page.wait_for(
        "document.querySelector('#page-manage').innerText.includes('Suas experiências')",
        message="the evidence page in Portuguese after the next arrival",
    )
    page.evaluate("document.querySelector('[data-locale=\"en\"]').click()")

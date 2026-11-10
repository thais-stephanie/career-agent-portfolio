"""Home in the browser while jobs are being collected.

* Opened with no answer yet, Home draws its page at once and says its lists
  and counts are being counted; Home is asked once.
* During a collection it says when its lists were counted and that new jobs
  are counted when the update ends; the progress is drawn beside it.
* When an automatic update ends, Home counts once WITHOUT clearing the page,
  and the job list is not asked again (the person updates it when they want).
* A failed recount keeps the last lists and says from when.
* An answer for a profile that is no longer open is never drawn.

The collection is a synthetic `/api/retrieval` answer; nothing is collected.
"""

# ruff: noqa: F811 -- `tailor_server` is a fixture imported by name

from __future__ import annotations

import time
from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.test_loading_recovery import (  # noqa: F401 - fixtures
    _profiles,
    _uninstall,
    calls,
    intercept,
    rules,
    running,
    tailor_server,
)

HOME = {"match": "/api/home", "not": "/api/home/"}
VIEW = "document.querySelector('#page-home .home')?.dataset.view"
NOTE = "document.querySelector('#page-home [data-home-part=lists-at]')"


def ended(status: str = "done") -> dict[str, Any]:
    """The synthetic run, over: three sources, two of them read."""
    answer = running("auto", "")
    run = answer["run"]
    answer["running"] = False
    run.update(
        status=status,
        finished_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        boards_total=3,
        boards_done=3,
        current=None,
        current_started_at=None,
        jobs_new=4,
        sources=[
            {"source_id": "a", "status": "ok"},
            {"source_id": "b", "status": "ok"},
            {"source_id": "c", "status": "failed"},
        ],
    )
    return answer


def open_home(page: Chrome, url: str) -> None:
    page.set_viewport(1440, 960)
    page.navigate(url + "/#home")


def home_asked(page: Chrome) -> int:
    return int(page.evaluate("window.__lrCalls.filter((c) => /\\/api\\/home$/.test(c.url)).length"))


def release(page: Chrome) -> None:
    page.evaluate("window.__lrHeld.splice(0).forEach((go) => go())")


def test_home_draws_its_page_before_its_numbers(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{**HOME, "mode": "hold"}])
    open_home(page, tailor_server["url"])
    waiting = "document.querySelector('#page-home [data-home-part=lists]')"
    page.wait_for(f"{waiting} !== null", timeout=20, message="the page before its numbers")
    assert page.evaluate(VIEW) == "loading"
    assert "Counting your jobs" in str(page.evaluate(f"{waiting}.innerText"))
    assert page.evaluate(f"{waiting}.getAttribute('aria-busy')") == "true"
    assert home_asked(page) == 1
    release(page)
    page.wait_for(f"{VIEW} === 'dashboard'", timeout=20, message="the dashboard")
    assert page.evaluate(waiting) is None
    assert home_asked(page) == 1


def test_during_a_run_home_says_when_its_lists_were_counted(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running("auto", "x:y")}])
    open_home(page, tailor_server["url"])
    page.wait_for(f"{VIEW} === 'dashboard'", timeout=20, message="the dashboard")
    page.wait_for(f"{NOTE}?.dataset.lists === 'updating'", timeout=10, message="the note")
    note = str(page.evaluate(f"{NOTE}.innerText"))
    assert note.startswith("Lists and counts as of ") and "being updated" in note
    assert "{time}" not in note and "home.lists" not in note
    # The progress is its own section, live beside the kept lists.
    assert page.evaluate("!document.querySelector('#page-home .home__sec--collect').hidden")


def _run_then_end(page: Chrome, url: str, home_after: dict[str, Any]) -> int:
    """Home open during a run; the run ends; Home's recount held. Returns the
    job-list requests asked before the end."""
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running("auto", "x:y")}])
    open_home(page, url)
    page.wait_for(f"{VIEW} === 'dashboard'", timeout=20, message="the dashboard")
    page.wait_for(f"{NOTE}?.dataset.lists === 'updating'", timeout=10, message="updating")
    lists_before = len(calls(page, "/api/jobs?"))
    asked = home_asked(page)
    rules(page, [{"match": "/api/retrieval", "mode": "json", "body": ended()}, home_after])
    page.wait_for(
        f"window.__lrCalls.filter((c) => /\\/api\\/home$/.test(c.url)).length > {asked}",
        timeout=15,
        message="Home asked again at the end of the run",
    )
    return lists_before


def test_the_end_of_an_update_counts_home_once_and_keeps_the_page(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    lists_before = _run_then_end(page, tailor_server["url"], {**HOME, "mode": "hold"})
    page.wait_for(f"{NOTE}?.dataset.lists === 'counting'", timeout=10, message="counting")
    assert page.evaluate(VIEW) == "dashboard", "the page was cleared while it was counted"
    assert "Counting the jobs that arrived" in str(page.evaluate(f"{NOTE}.innerText"))
    asked = home_asked(page)
    release(page)
    page.wait_for(f"{NOTE}?.dataset.lists === 'current'", timeout=15, message="counted")
    assert page.evaluate(f"{NOTE}.hidden") is True
    time.sleep(2.5)  # past the next poll: nothing asks Home again
    assert home_asked(page) == asked
    # The job list is left as it was: the person updates it when they want.
    assert len(calls(page, "/api/jobs?")) == lists_before
    # What the run did is said, partial included.
    said = str(page.evaluate("document.querySelector('#page-home .home__sec--collect').innerText"))
    assert "2 of 3 sources" in said and "4 new jobs" in said


def test_a_failed_recount_keeps_the_last_lists_and_says_from_when(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    _run_then_end(page, tailor_server["url"], {**HOME, "mode": "status", "status": 503})
    page.wait_for(f"{NOTE}?.dataset.lists === 'failed'", timeout=15, message="the failure")
    assert page.evaluate(VIEW) == "dashboard"
    note = str(page.evaluate(f"{NOTE}.innerText"))
    assert note.startswith("The jobs could not be counted again. Showing the counts from ")
    assert page.evaluate("document.querySelector('#page-home .home__main').children.length") > 1


def test_an_answer_for_a_profile_no_longer_open_is_never_drawn(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(
        page, [{"match": "/api/profiles", "mode": "json", "body": _profiles("prof-a-synthetic")}]
    )
    open_home(page, tailor_server["url"])
    page.wait_for(f"{VIEW} === 'dashboard'", timeout=20, message="the dashboard")
    real = page.evaluate("fetch('/api/home').then((r) => r.json())")
    rules(
        page,
        [
            {"match": "/api/profiles", "mode": "json", "body": _profiles("prof-a-synthetic")},
            {**HOME, "mode": "hold", "body": {**real, "profile": "prof-a-synthetic", "total": 987}},
        ],
    )
    asked = home_asked(page)
    page.evaluate("document.querySelector('.topnav__link[data-page=jobs]').click()")
    page.evaluate("document.querySelector('.topnav__link[data-page=home]').click()")
    page.wait_for(
        f"window.__lrCalls.filter((c) => /\\/api\\/home$/.test(c.url)).length > {asked}",
        timeout=15,
        message="Home asked again",
    )
    page.evaluate("import('/js/api.js').then((m) => m.setLocalProfile('prof-b-synthetic'))")
    release(page)
    time.sleep(1.0)
    assert page.evaluate(VIEW) != "dashboard", "profile A's answer was drawn for profile B"
    assert "987" not in str(page.evaluate("document.getElementById('page-home').innerText"))


def test_the_lists_note_is_in_portuguese_too(page: Chrome, tailor_server: dict[str, Any]) -> None:
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running("auto", "x:y")}])
    open_home(page, tailor_server["url"])
    page.evaluate("localStorage.setItem('careerAgent.locale.v1', 'pt-BR')")
    try:
        page.reload()
        page.wait_for_app()
        page.wait_for(f"{NOTE}?.dataset.lists === 'updating'", timeout=20, message="the note")
        note = str(page.evaluate(f"{NOTE}.innerText"))
        assert note.startswith("Listas e contagens de ")
        assert "as novas entram na contagem quando a atualização terminar" in note
    finally:
        page.evaluate("localStorage.removeItem('careerAgent.locale.v1')")

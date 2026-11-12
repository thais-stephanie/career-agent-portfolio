# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""The Find jobs list is read where it is shown, in a real browser.

Opening the app on Home used to read the whole Find jobs list and then its
narrowings, the heaviest requests of the opening, for a page nobody was
looking at; a collection or a recalculation that ended while Home showed read
it again. The list is now read when Find jobs or Applications opens, and a
list already held is shown and checked with the one small freshness question.
Synthetic profile, invented demo jobs; nothing is collected.
"""

from __future__ import annotations

import time
from typing import Any

from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_home_during_refresh import ended
from tests.browser.test_loading_recovery import (  # noqa: F401 - fixtures
    TAILOR_LISTS,
    _uninstall,
    calls,
    intercept,
    rules,
    running,
)
from tests.browser.test_resume_tailor import tailor_server  # noqa: F401

CARDS = "document.querySelector('#page-jobs .cards .card[data-job-id]')"


def find_lists(page: Chrome) -> list[str]:
    """Find jobs' own list reads (Resume Tailor's two carry their own keys)."""
    return [u for u in calls(page, "/api/jobs?") if not any(k in u for k in TAILOR_LISTS)]


def narrowings(page: Chrome) -> list[str]:
    return calls(page, "/api/jobs/narrowings")


def home_asked(page: Chrome) -> int:
    return int(page.evaluate(r"window.__lrCalls.filter((c) => /\/api\/home$/.test(c.url)).length"))


def go(page: Chrome, name: str) -> None:
    page.evaluate(f"document.querySelector('.topnav__link[data-page={name}]').click()")


def settle() -> None:
    """Long enough for any read the page would ask on its own to be asked."""
    time.sleep(1.5)


def test_home_opens_without_reading_the_find_jobs_list(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [])
    open_home_past_setup(page, tailor_server["url"])
    settle()
    assert find_lists(page) == [], "Home read the Find jobs list"
    assert narrowings(page) == [], "Home read the Find jobs narrowings"
    # Opening Find jobs reads it, once, and paints it.
    go(page, "jobs")
    page.wait_for(CARDS, timeout=20, message="the Find jobs list")
    settle()
    assert len(find_lists(page)) == 1, find_lists(page)
    assert all("narrowings=later" in u for u in find_lists(page))
    # Back to Home: nothing of the list is asked again.
    go(page, "home")
    page.wait_for("document.querySelector('.home[data-view=dashboard]')", message="Home")
    settle()
    assert len(find_lists(page)) == 1
    # Find jobs again: the list it holds, not read again.
    go(page, "jobs")
    page.wait_for(CARDS, timeout=20, message="the list, again")
    settle()
    assert len(find_lists(page)) == 1, "the held, unchanged list was read again"


def test_opening_on_find_jobs_reads_the_list_at_once(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    intercept(page, [])
    page.set_viewport(1440, 960)
    page.navigate(tailor_server["url"] + "/#jobs")
    page.wait_for(CARDS, timeout=20, message="the Find jobs list")
    settle()
    assert len(find_lists(page)) == 1, find_lists(page)


def test_a_collection_ending_on_home_leaves_the_list_for_find_jobs(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    """A collection the person started ends while Home shows: Home counts
    again; the list waits for Find jobs, which then reads the new one."""
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running("find", "x:y")}])
    open_home_past_setup(page, tailor_server["url"])
    go(page, "jobs")
    page.wait_for(CARDS, timeout=20, message="the Find jobs list")
    go(page, "home")
    page.wait_for("document.querySelector('.home[data-view=dashboard]')", message="Home")
    settle()
    lists = len(find_lists(page))
    over = ended()
    over["run"]["kind"] = "find"  # one she started: its end reads the lists again
    rules(page, [{"match": "/api/retrieval", "mode": "json", "body": over}])
    homes = home_asked(page)
    page.wait_for(
        f"window.__lrCalls.filter((c) => /\\/api\\/home$/.test(c.url)).length > {homes}",
        timeout=20,
        message="Home counting what the run brought",
    )
    settle()
    assert len(find_lists(page)) == lists, "the end of a run read the list while Home showed"
    # Find jobs then reads the list, or (nothing really arrived here: the run
    # is synthetic) asks whether the one it holds is still current.
    asked = len(calls(page, "/api/jobs/freshness"))
    go(page, "jobs")
    page.wait_for(
        f"window.__lrCalls.filter((c) => c.url.includes('/api/jobs/freshness')).length > {asked}"
        f" || window.__lrCalls.filter((c) => c.url.includes('/api/jobs?')"
        f" && !c.url.includes('limit=24') && !c.url.includes('limit=8')).length > {lists}",
        timeout=20,
        message="the list checked or read after the run",
    )
    page.wait_for(CARDS, timeout=20, message="the list after the run")


# "Update the list", offered when a run the app started by itself ends. On
# Home it used to only mark the list as due: nothing visible happened.
FILTER = "include_ineligible=1"
UPDATE = (
    "Array.from(document.querySelectorAll('.toast button'))"
    ".find((b) => /Update the list/.test(b.innerText))"
)
JOBS_SHOWN = "!document.getElementById('page-jobs').hidden && document.body.dataset.page === 'jobs'"


def auto_run_ends_on_home(page: Chrome, url: str, *, list_first: bool) -> int:
    """Home showing, the app's own run ends with new jobs; returns the list
    reads made until its notice is up."""
    intercept(page, [{"match": "/api/retrieval", "mode": "json", "body": running("auto", "x:y")}])
    page.set_viewport(1440, 960)
    page.navigate(f"{url}/?{FILTER}")
    open_home_past_setup(page, f"{url}/?{FILTER}")
    if list_first:
        go(page, "jobs")
        page.wait_for(CARDS, timeout=20, message="the Find jobs list")
        go(page, "home")
        page.wait_for("document.querySelector('.home[data-view=dashboard]')", message="Home")
    settle()
    rules(page, [{"match": "/api/retrieval", "mode": "json", "body": ended()}])
    page.wait_for(f"Boolean({UPDATE})", timeout=30, message="the new-jobs notice")
    settle()
    return len(find_lists(page))


def test_update_the_list_on_home_opens_find_jobs_with_the_list_read_once(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    before = auto_run_ends_on_home(page, tailor_server["url"], list_first=True)
    assert before == 1, "only the visit to Find jobs read the list"
    assert page.evaluate("document.body.dataset.page") == "home", "the notice moved the page"
    page.evaluate(f"{UPDATE}.click()")
    page.wait_for(JOBS_SHOWN, timeout=10, message="Find jobs")
    page.wait_for(CARDS, timeout=20, message="the list after the update")
    settle()
    after = find_lists(page)
    assert len(after) == before + 1, f"one fresh read, not a held list nor two: {after}"
    assert FILTER in after[-1], "the filters in force were dropped"
    assert FILTER in str(page.evaluate("location.search")), "the address lost the filters"


def test_update_the_list_reads_it_when_none_was_read_yet(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    before = auto_run_ends_on_home(page, tailor_server["url"], list_first=False)
    assert before == 0, "Home read the list, or the notice read it without a press"
    page.evaluate(f"{UPDATE}.click()")
    page.wait_for(JOBS_SHOWN, timeout=10, message="Find jobs")
    page.wait_for(CARDS, timeout=20, message="the list after the update")
    settle()
    assert len(find_lists(page)) == 1, find_lists(page)


def test_update_the_list_says_so_when_the_read_fails(
    page: Chrome, tailor_server: dict[str, Any]
) -> None:
    auto_run_ends_on_home(page, tailor_server["url"], list_first=False)
    rules(
        page,
        [
            {"match": "/api/retrieval", "mode": "json", "body": ended()},
            {"match": "/api/jobs?", "mode": "status", "status": 500},
        ],
    )
    page.evaluate(f"{UPDATE}.click()")
    page.wait_for(JOBS_SHOWN, timeout=10, message="Find jobs")
    page.wait_for(
        "document.querySelector('#page-jobs .state--error button')",
        timeout=20,
        message="the failure, with a retry",
    )
    assert "load" in str(page.evaluate("document.querySelector('.state--error').innerText")).lower()

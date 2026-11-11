# ruff: noqa: F811  (fixtures are imported, then used as arguments)
"""Home when a recalculation it followed ends, in a real browser.

The end of a recalculation is what Home counts moving without a write from
this page. Home reads again then; a Home read asked while the recalculation
went (a visit, say) is never taken as that read, and if it answers late it is
not drawn over the newer one. A real recalculation runner holds synthetic
work; the corpus is synthetic; nothing is scored.
"""

from __future__ import annotations

import threading
import time

from tests.browser.chrome import Chrome
from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_home_during_refresh import HOME, HOME_TEXT, RAIL_JOBS, _marked, ended
from tests.browser.test_home_tasks import RECALC, RECALC_HELP, _hold, pending  # noqa: F401
from tests.browser.test_loading_recovery import (  # noqa: F401 - fixtures
    _uninstall,
    intercept,
    rules,
    running,
)

from career_agent.web.api import JobsApi

MARK = "Answer asked during the recalculation"


def home_asked(page: Chrome) -> int:
    return int(page.evaluate(r"window.__lrCalls.filter((c) => /\/api\/home$/.test(c.url)).length"))


def asked_after(page: Chrome, count: int, seconds: float = 15.0) -> int:
    """Home reads once `count` is passed, or `count` if none came in time."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and home_asked(page) <= count:
        time.sleep(0.1)
    return home_asked(page)


def drawn_after_the_end(page: Chrome) -> None:
    page.wait_for("document.querySelector('.home[data-view=dashboard]')", message="Home")
    page.wait_for(f"{RECALC}?.getAttribute('aria-busy') === 'false'", message="the run over")
    assert "Updating 3 of 20" not in str(page.evaluate(RECALC_HELP))


def test_the_end_of_a_recalculation_never_reuses_a_read_asked_before_it(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    """A collection ends while the recalculation goes: Home counts again
    quietly (the dashboard and its recalculation row stay) and the rail reads
    too; that read is slow. The recalculation ends while it is in flight."""
    base, app = pending
    release = threading.Event()
    _hold(app, release, done=3, total=20)
    collecting = {"match": "/api/retrieval", "mode": "json", "body": running("auto", "x:y")}
    over = {"match": "/api/retrieval", "mode": "json", "body": ended()}
    try:
        intercept(page, [collecting])
        open_home_past_setup(page, base)
        page.wait_for(f"{RECALC_HELP}.includes('Updating 3 of 20')", message="the run, followed")
        during = page.evaluate("fetch('/api/home').then((r) => r.json())")
        assert during["scores"]["running"] is True
        count = f"{during['job_count']:,}"
        rules(
            page,
            [over, {**HOME, "mode": "hold", "body": _marked(during, MARK, during["job_count"])}],
        )
        page.wait_for(
            "window.__lrHeld.length === 1", message="the read asked at the collection's end"
        )
        asked = home_asked(page)
        # What is asked from now on is answered by the server, when released.
        rules(page, [over, {**HOME, "mode": "hold"}])
    finally:
        release.set()
        app.rescore.join(10)

    after = asked_after(page, asked)
    if after > asked:
        # Asked again now, by anything: shared with the read the end asked.
        page.evaluate("import('/js/api.js').then((m) => { m.getHome().catch(() => {}); })")
        time.sleep(0.5)
        assert home_asked(page) == after, "two reads after the end were not shared"
        # The newer answer first, then the read asked during the run, late.
        page.evaluate("window.__lrHeld[1]()")
        drawn_after_the_end(page)
        assert MARK not in str(page.evaluate(HOME_TEXT))
    page.evaluate("window.__lrHeld[0]()")
    time.sleep(1.5)
    assert MARK not in str(page.evaluate(HOME_TEXT)), (
        "Home drew a read asked while the recalculation was going, after it ended"
    )
    drawn_after_the_end(page)
    assert after == asked + 1, "the end of the recalculation did not read Home again"
    # The rail draws its own latest read (asked at the collection's end); a
    # recalculation does not move the counts it shows.
    assert page.evaluate(RAIL_JOBS) == count


def test_the_end_of_a_recalculation_reads_home_once_when_nothing_is_in_flight(
    page: Chrome, pending: tuple[str, JobsApi]
) -> None:
    base, app = pending
    release = threading.Event()
    _hold(app, release, done=3, total=20)
    try:
        intercept(page, [])
        open_home_past_setup(page, base)
        page.wait_for(f"{RECALC_HELP}.includes('Updating 3 of 20')", message="the run, followed")
        asked = home_asked(page)
    finally:
        release.set()
        app.rescore.join(10)
    assert asked_after(page, asked) == asked + 1
    drawn_after_the_end(page)
    time.sleep(1.0)
    assert home_asked(page) == asked + 1, "the end read Home more than once"

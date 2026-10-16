"""Source states say what happened, and "not run yet" only of a built connector.

Before 2026-10-16 one chip, "Not run yet", was shown both for a connector that
had never been asked and for seven sources with no connector at all, and a
successful refresh could not say whether it brought new jobs, only jobs
already held, or ran into a failing board. These pin the vocabulary.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from career_agent.sources.catalogue import Coverage, Source, SourceStatus
from career_agent.sources.health import LastRefresh, SourceHealth, _last_refresh

NOW = "2026-10-16T20:00:00Z"


def _source(**over) -> Source:
    base = Source(
        id="example",
        name="Example (fictional)",
        region="global",
        status=SourceStatus.LIVE_INTEGRATION,
        authorization="fictional",
        provider="greenhouse",
        coverage=Coverage.OPERATIONAL,
    )
    return replace(base, **over)


def _run(stage, started, *, status="OK", finished=True, **stats):
    return {
        "stage": stage,
        "started_at": started,
        "finished_at": started if finished else None,
        "status": status,
        "stats_json": json.dumps(stats),
        "error": None,
    }


@pytest.mark.parametrize(
    ("over", "state"),
    [
        ({"provider": None, "coverage": Coverage.PLANNED}, "PLANNED"),
        ({"provider": None, "coverage": Coverage.UNSUPPORTED}, "DISABLED"),
        ({"provider": None, "coverage": Coverage.PARTIAL}, "NOT_IMPLEMENTED"),
        ({"id": "remotesource", "provider": None}, "DISCOVERY_ONLY"),
        ({"coverage": Coverage.BLOCKED, "experimental_provider": "linkedin"}, "EXPERIMENTAL"),
        ({"collection_blocker": "The feed stopped serving."}, "BLOCKED_PROVIDER"),
        ({"id": "jooble", "provider": "jooble"}, "QUOTA_EXHAUSTED"),
    ],
)
def test_what_a_source_is_comes_before_what_it_did(over, state) -> None:
    assert SourceHealth(source=_source(**over)).state == state


def test_not_run_yet_only_for_a_built_connector() -> None:
    assert SourceHealth(source=_source()).state == "NOT_RUN"
    assert SourceHealth(source=_source(provider=None)).state != "NOT_RUN"


@pytest.mark.parametrize(
    ("refresh", "state"),
    [
        (LastRefresh(runs=1, new=3, seen_again=10), "SUCCESS_NEW"),
        (LastRefresh(runs=1, new=0, seen_again=10), "ALL_DUPLICATES"),
        (LastRefresh(runs=1), "SUCCESS_ZERO_NEW"),
        (LastRefresh(runs=2, failed_runs=1, new=5), "PARTIAL_FAILURE"),
        (LastRefresh(runs=3, failures=1, new=5), "PARTIAL_FAILURE"),
        (LastRefresh(runs=2, failed_runs=2), "FAILED"),
        (LastRefresh(runs=1, running=True), "RUNNING"),
        (LastRefresh(runs=4, new=5, gone=2), "SUCCESS_NEW"),
    ],
)
def test_the_last_refresh_decides_how_a_run_source_reads(refresh, state) -> None:
    health = SourceHealth(source=_source(), postings=10, last_refresh=refresh)
    assert health.state == state


def test_a_board_family_is_judged_by_its_whole_refresh_and_a_gone_board_is_no_failure() -> None:
    runs = [
        _run("collect", "2026-10-16T15:00:00Z",
             by_board=[{"provider": "greenhouse", "jobs_new": 4, "jobs_seen_again": 9}]),
        _run("collect", "2026-10-16T15:01:00Z", status="FAILED",
             by_board=[{"provider": "greenhouse", "jobs_new": 0, "jobs_seen_again": 0}],
             failures=[{"provider": "greenhouse", "category": "NOT_FOUND"}]),
        _run("collect", "2026-10-15T08:00:00Z", status="FAILED",
             by_board=[{"provider": "greenhouse"}],
             failures=[{"provider": "greenhouse", "category": "HTTP_ERROR"}]),
    ]  # fmt: skip
    refresh = _last_refresh(runs, now=NOW)["greenhouse"]
    assert (refresh.runs, refresh.new, refresh.failed_runs, refresh.gone) == (2, 4, 0, 1)
    assert SourceHealth(source=_source(), postings=13, last_refresh=refresh).state == "SUCCESS_NEW"


def test_a_feed_is_judged_by_its_latest_run() -> None:
    runs = [
        _run("collect-workable", "2026-10-16T14:00:00Z", status="FAILED", failures=[{"x": 1}]),
        _run("collect-workable", "2026-10-16T16:00:00Z", jobs_new=0, jobs_seen_again=7),
    ]
    refresh = _last_refresh(runs, now=NOW)["workable"]
    assert (refresh.runs, refresh.failed_runs, refresh.seen_again) == (1, 0, 7)


def test_an_open_run_is_running_and_an_abandoned_one_failed() -> None:
    live = _last_refresh([_run("collect-gupy", "2026-10-16T19:30:00Z", finished=False)], now=NOW)
    assert live["gupy"].running
    dead = _last_refresh([_run("collect-gupy", "2026-10-15T09:00:00Z", finished=False)], now=NOW)
    assert not dead["gupy"].running and dead["gupy"].failed_runs == 1


def test_a_count_of_failures_instead_of_a_list_is_tolerated() -> None:
    refresh = _last_refresh([_run("collect-wwr", "2026-10-16T19:00:00Z", failures=3)], now=NOW)
    assert refresh["wwr"].runs == 1

"""The corpus stays usable while the market data is being refreshed.

WHY THIS FILE EXISTS
--------------------
`tests/unit/test_source_progress.py` proves that `read_progress` reads the run
ledger honestly, and `tests/unit/test_source_scheduling.py` proves that a plan
decides WHEN a board is refreshed without ever deciding what a posting means.
Neither of them can fail for the reason a person would actually notice.

The failure a person notices is this one: a source is half-collected, or paused,
or broken, and the product that has 21 perfectly good postings in its database
shows them nothing. That is the shape the V1.9 work exists to forbid, and it
spans three layers -- the ledger, the scheduler and the jobs query -- so it
cannot be asserted in any of them alone.

So this file drives `JobsApi` over ONE scratch database holding one of each
situation at the same time:

  a source RUNNING with a provider total      (a percentage is honest)
  a source RUNNING with no provider total     (a percentage would be invented)
  a source that FAILED                        (and must not close anything)
  a source PAUSED for this candidate's markets
  every other source NOT_STARTED

and then asks the question that matters: is Discover Jobs still a product.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: Every posting in the demo corpus, with all three narrowings widened, so the
#: number this file compares against is "the whole database" rather than "the
#: whole database minus whatever the gates concluded today".
WIDEN = {
    "include_ineligible": ["1"],
    "include_off_target": ["1"],
    "include_unresolved": ["1"],
    "group_duplicates": ["0"],
}

NOW = datetime.now(UTC)

#: A run in flight that the PROVIDER gave a denominator for, and one it did not.
#:
#: `claimed_total` is the only reason a percentage may appear. The pair is here
#: rather than in two fixtures because the interesting assertion is that the two
#: rows render differently in the SAME payload: an interface that learned to
#: divide would quietly give the second one a bar too.
MEASURED = {"postings_seen": 500, "pages_read": 5, "claimed_total": 2000}
UNMEASURED = {"postings_seen": 120, "pages_read": 3}


def _insert_run(
    conn: sqlite3.Connection,
    stage: str,
    *,
    run_id: str,
    status: str,
    stats: dict,
    started: datetime,
    finished: datetime | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            run_id,
            stage,
            started.isoformat(),
            finished.isoformat() if finished else None,
            status,
            json.dumps(stats),
            error,
        ),
    )


@pytest.fixture(scope="module")
def api(tmp_path_factory: pytest.TempPathFactory) -> JobsApi:
    """A scratch demo database with a deliberately messy run ledger.

    A SCRATCH DATABASE AND NOT THE CORPUS, on purpose. The real corpus is the
    owner's and is measured in gigabytes; a test that opened it read-write once
    checkpointed a 21 MB write-ahead log and changed every byte of it. Nothing
    here may touch `data/`.
    """
    tmp = tmp_path_factory.mktemp("progressive")
    db_path = tmp / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    _insert_run(
        conn,
        "collect-workable",
        run_id="running-measured",
        status="RUNNING",
        stats=MEASURED,
        started=NOW - timedelta(minutes=10),
    )
    _insert_run(
        conn,
        "collect-himalayas",
        run_id="running-unmeasured",
        status="RUNNING",
        stats=UNMEASURED,
        started=NOW - timedelta(minutes=4),
    )
    _insert_run(
        conn,
        "collect-gupy",
        run_id="broken",
        status="FAILED",
        stats={"failures": 3},
        started=NOW - timedelta(hours=2),
        finished=NOW - timedelta(hours=1, minutes=55),
        error="the feed answered 500 on page 2",
    )
    conn.commit()
    conn.close()
    return JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)


@pytest.fixture(scope="module")
def sources(api: JobsApi) -> dict:
    return api.handle_api("GET", "/api/sources", {}, {})


@pytest.fixture(scope="module")
def refresh(sources: dict) -> dict[str, dict]:
    return {row["source_id"]: row for row in sources["refresh"]}


def _jobs(api: JobsApi, **extra) -> dict:
    query = dict(WIDEN)
    query.update({key: [str(value)] for key, value in extra.items()})
    return api.handle_api("GET", "/api/jobs", query, {})


# =========================================================================
# 1. THE CORPUS IS STILL A PRODUCT
# =========================================================================


def test_the_whole_corpus_is_returned_while_two_sources_are_running(api: JobsApi) -> None:
    """The assertion this file exists for.

    Two collections in flight, one source broken, one paused for this
    candidate's geography -- and the 21 postings that were scored before any of
    that are all still there. A retrieval in progress is a fact about
    FRESHNESS, never a reason to withhold what is already known.
    """
    payload = _jobs(api)
    assert payload["total"] == 21, payload["total"]
    assert len(payload["items"]) > 0


def test_a_failed_source_closes_nothing(api: JobsApi, refresh: dict[str, dict]) -> None:
    """A discovery failure is the collector's problem and not the posting's.

    Gupy failed two hours ago in this ledger. If a failure were allowed to
    decide that a posting is gone, the worst day a source ever has would empty
    the candidate's list -- and it would look exactly like the market drying up.
    """
    assert refresh["gupy"]["state"] == "FAILED"
    open_postings = _jobs(api)["total"]
    assert open_postings == 21, open_postings


# =========================================================================
# 2. NO INVENTED PERCENTAGES
# =========================================================================


def test_a_running_source_reports_how_far_it_has_got(refresh: dict[str, dict]) -> None:
    row = refresh["workable"]
    assert row["state"] == "RUNNING"
    assert row["retrieved"] == MEASURED["postings_seen"]
    assert row["expected_total"] == MEASURED["claimed_total"]
    assert row["measurable"] is True
    assert row["percent"] == 25


def test_a_running_source_with_no_provider_total_offers_no_percentage(
    refresh: dict[str, dict],
) -> None:
    """The whole danger of a progress display, asserted beside its opposite.

    This row has counters. It would be one division away from a bar, and the
    denominator would be a guess -- Himalayas publishes no total. `measurable`
    is False and `percent` is null, and the interface renders the count with
    "total not published" instead.
    """
    row = refresh["himalayas"]
    assert row["state"] == "RUNNING"
    assert row["retrieved"] == UNMEASURED["postings_seen"]
    assert row["measurable"] is False
    assert row["percent"] is None
    assert row["expected_total"] is None


def test_no_row_anywhere_reports_a_percentage_without_a_total(refresh: dict[str, dict]) -> None:
    for source_id, row in refresh.items():
        if row["percent"] is not None:
            assert row["measurable"] is True, source_id
            assert row["expected_total"], source_id
            assert 0 <= row["percent"] <= 100, (source_id, row["percent"])


def test_a_source_nothing_has_run_for_says_so_rather_than_zero(refresh: dict[str, dict]) -> None:
    """`0` and "no measurement" are different statements and one of them is a
    lie. A source nothing has ever run for reports null counters, which the
    panel prints as "not measured" -- never as a bar at the left-hand end."""
    untouched = [row for row in refresh.values() if row["state"] == "NOT_STARTED"]
    assert untouched, "the demo ledger should leave most sources untouched"
    for row in untouched:
        assert row["retrieved"] is None, row["source_id"]
        assert row["percent"] is None, row["source_id"]


# =========================================================================
# 3. SCHEDULING DECIDES WHEN, AND SAYS WHY
# =========================================================================


def test_a_paused_source_always_says_why_it_is_paused(refresh: dict[str, dict]) -> None:
    """A board held back with no reason on screen is indistinguishable from a
    broken one, and the person cannot tell which -- so they cannot act."""
    paused = [row for row in refresh.values() if row["state"] == "PAUSED"]
    for row in paused:
        assert row["blocker"], row["source_id"]


def test_the_market_the_candidate_lives_in_is_never_paused(
    sources: dict, refresh: dict[str, dict]
) -> None:
    """The defect that shipped inside the first version of the scheduler.

    It read `preferences.eligible_countries`, a field that does not exist -- the
    list lives under `eligibility` -- so every candidate resolved to an empty
    set of markets, and the board holding 77% of a Brazilian candidate's own
    corpus was paused as "a market you have not asked for".
    """
    from career_agent.sources.scheduling import regions_for

    config, _ = load_search_config(CONFIG_DIR)
    countries = {config.eligibility.candidate_country, *config.eligibility.eligible_countries}
    mine = set(regions_for({code for code in countries if code}))
    assert mine, "the committed configuration should name at least one market"

    # The regions come off the SOURCES payload rather than out of the catalogue
    # file, so this reads the same answer the screen does.
    for source in sources["sources"]:
        row = refresh.get(source["id"])
        if row is None or not source.get("region"):
            continue
        if str(source["region"]).strip().lower() in mine:
            assert row["state"] != "PAUSED", source["id"]

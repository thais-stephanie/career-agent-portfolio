"""`/api/sources` reads the provider facts of `job` once (Phase 4, Step 2).

`health` used to run two aggregates over `job`. `_posting_facts` counted every
posting, open or closed, and took the newest `first_seen_at`. The catalogue's
`_counts_by_provider` then counted the open ones again. The first walked the
whole table in the order of its unique index, with a row lookup for each
posting. These tests pin what the two answered, so one pass can replace them
without the answer moving:

- pinned values for each shape a corpus can take (part 1);
- the whole `/api/sources` answer against the two-query path, on a migrated
  database, after the catalogue changes and for two profiles sharing one
  catalogue (part 2);
- how many statements read `job`, and the plan of the one that remains
  (part 3).

Synthetic data only.
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_shared_catalogue import two_profiles  # noqa: F401 -- fixture
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.sources import catalogue
from career_agent.sources import health as health_mod
from career_agent.sources.health import SourceHealth, health
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import CandidateStateRepo, ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"

#: The same loose schema as `tests/unit/test_source_health.py`, where a
#: provider or a first sighting may be NULL. The real schema forbids both; the
#: answer for them is pinned anyway, because the code never relied on it.
SCHEMA = """
CREATE TABLE job (id TEXT PRIMARY KEY, provider TEXT, first_seen_at TEXT,
                  company_id TEXT, content_hash TEXT, closed_at TEXT,
                  collection_status TEXT, location_raw TEXT, source_board_id TEXT);
CREATE TABLE source_board (id TEXT PRIMARY KEY, provider TEXT, company_id TEXT,
                           last_collected_at TEXT, last_error TEXT);
CREATE TABLE pipeline_run (id TEXT PRIMARY KEY, stage TEXT, started_at TEXT,
                           finished_at TEXT, status TEXT, stats_json TEXT, error TEXT);
CREATE TABLE job_match (id TEXT PRIMARY KEY, job_id TEXT, config_id TEXT,
                        config_version INTEGER, schema_version INTEGER);
"""

#: Live integrations with a registered adapter, so the corpus decides their
#: status: none open is CONFIGURED, one open is LIVE_INTEGRATION.
LIVE = ("greenhouse", "lever", "ashby", "workable")
CLOSED = "2026-11-01T00:00:00+00:00"


def _loose(jobs=(), boards=()) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO source_board (id, provider) VALUES (?, ?)", list(boards))
    conn.executemany(
        "INSERT INTO job (id, provider, first_seen_at, closed_at, source_board_id)"
        " VALUES (?, ?, ?, ?, ?)",
        [(f"j{n}", *row) for n, row in enumerate(jobs)],
    )
    return conn


def _seen(entries: list[SourceHealth]) -> dict[str, tuple]:
    """What the corpus decides for each live source."""
    return {
        e.source.id: (
            e.postings,
            e.last_new_posting,
            e.source.collected,
            e.source.status.value,
            e.source.coverage.value,
            e.source.downgraded_because,
            e.source.boards,
            e.source.boards_with_postings,
        )
        for e in entries
        if e.source.id in LIVE
    }


NEVER = "the adapter exists but has never persisted a posting"
NOT_IN = "no posting from this source is in the corpus"


def _absent(source_id: str) -> tuple:
    return (0, None, 0, "CONFIGURED", "PLANNED", NEVER, None, None)


# ============================================================ 1. pinned shapes


def test_an_empty_corpus_holds_nothing_from_any_source() -> None:
    seen = _seen(health(_loose()))
    assert seen == {source: _absent(source) for source in LIVE}


def test_one_open_posting_makes_its_source_live() -> None:
    seen = _seen(health(_loose([("greenhouse", "2026-10-01T00:00:00Z", None, "b1")])))
    assert seen["greenhouse"][:4] == (1, "2026-10-01T00:00:00Z", 1, "LIVE_INTEGRATION")
    assert seen["lever"] == _absent("lever")


def test_open_and_closed_count_apart() -> None:
    jobs = [("greenhouse", f"2026-10-0{d}T00:00:00Z", None, "b1") for d in (1, 2)]
    jobs += [("greenhouse", f"2026-10-0{d}T00:00:00Z", CLOSED, "b1") for d in (3, 4, 5)]
    seen = _seen(health(_loose(jobs)))
    # Every posting counts in `postings`; only the open ones in `collected`.
    # The newest sighting is a closed posting's, and it still counts.
    assert seen["greenhouse"][:4] == (5, "2026-10-05T00:00:00Z", 2, "LIVE_INTEGRATION")


def test_a_source_whose_postings_all_closed_has_none_collected() -> None:
    jobs = [
        ("lever", "2026-10-07T00:00:00Z", CLOSED, "b1"),
        ("lever", "2026-10-02T00:00:00Z", CLOSED, "b1"),
    ]
    seen = _seen(health(_loose(jobs)))
    assert seen["lever"][:6] == (2, "2026-10-07T00:00:00Z", 0, "CONFIGURED", "PLANNED", NEVER)


def test_only_open_postings() -> None:
    jobs = [
        ("ashby", "2026-09-01T00:00:00Z", None, "b1"),
        ("ashby", "2026-09-03T00:00:00Z", None, "b1"),
    ]
    assert _seen(health(_loose(jobs)))["ashby"][:4] == (
        2,
        "2026-09-03T00:00:00Z",
        2,
        "LIVE_INTEGRATION",
    )


def test_a_missing_first_sighting_is_skipped_and_none_alone_is_no_date() -> None:
    jobs = [("greenhouse", None, None, "b1"), ("greenhouse", "2026-10-09T00:00:00Z", None, "b1")]
    jobs += [("lever", None, None, "b1")]
    seen = _seen(health(_loose(jobs)))
    assert seen["greenhouse"][:3] == (2, "2026-10-09T00:00:00Z", 2)
    assert seen["lever"][:3] == (1, None, 1)


def test_providers_no_source_declares_change_nothing() -> None:
    jobs = [
        ("nobody-declares-this", "2026-10-01T00:00:00Z", None, "b1"),
        (None, "2026-10-02T00:00:00Z", None, "b1"),
    ]
    jobs += [("workable", "2026-10-03T00:00:00Z", None, "b1")]
    seen = _seen(health(_loose(jobs)))
    assert seen["workable"][:3] == (1, "2026-10-03T00:00:00Z", 1)
    assert {s: v for s, v in seen.items() if s != "workable"} == {
        s: _absent(s) for s in LIVE if s != "workable"
    }


def test_boards_configured_and_producing_are_counted_by_board() -> None:
    boards = [("b1", "greenhouse"), ("b2", "greenhouse"), ("b3", "greenhouse"), ("b4", "lever")]
    jobs = [("greenhouse", "2026-10-01T00:00:00Z", None, "b1")] * 3
    jobs += [("greenhouse", "2026-10-01T00:00:00Z", CLOSED, "b2")]
    jobs += [("lever", "2026-10-01T00:00:00Z", CLOSED, "nowhere")]
    seen = _seen(health(_loose(jobs, boards)))
    # A closed posting still makes its board a producing one; a posting on a
    # board that is not configured makes none. A provider with no producing
    # board has no entry at all, so it reads None rather than 0.
    assert seen["greenhouse"][6:] == (3, 2)
    assert seen["lever"][6:] == (1, None)


def test_an_identifier_is_matched_exactly() -> None:
    jobs = [
        ("Greenhouse", "2026-10-01T00:00:00Z", None, "b1"),
        ("greenhouse ", "2026-10-01T00:00:00Z", None, "b1"),
    ]
    assert _seen(health(_loose(jobs)))["greenhouse"] == _absent("greenhouse")


# ================================================= 2. the whole answer, A = B


def _legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The path as it was: `_posting_facts`, and the catalogue counting the open
    postings itself."""
    monkeypatch.setattr(
        health_mod, "_provider_facts", lambda conn: (health_mod._posting_facts(conn), None)
    )


def _answer(api: JobsApi) -> dict:
    return json.loads(json.dumps(api.handle_api("GET", "/api/sources", {}, {}), default=str))


def _both(api: JobsApi, monkeypatch: pytest.MonkeyPatch) -> tuple[dict, dict]:
    new = _answer(api)
    with monkeypatch.context() as m:
        _legacy(m)
        old = _answer(api)
    return new, old


def _run(conn, stage, run_id, status, started, finished=None, error=None, stats=None) -> None:
    conn.execute(
        "INSERT INTO pipeline_run (id, stage, started_at, finished_at, status, stats_json, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, stage, started, finished, status, json.dumps(stats or {}), error),
    )


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    """The demo corpus plus a messy run ledger, a closed-only provider, a
    paused source and a board that produced nothing."""
    db_path = tmp_path / "sources.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    now = datetime.now(UTC).replace(microsecond=0)
    with transaction(conn):
        # Every ashby posting closed: postings counted, none collected.
        conn.execute("UPDATE job SET closed_at = ? WHERE provider = 'ashby'", (CLOSED,))
        conn.execute(
            "INSERT INTO source_board (id, company_id, provider, board_identifier)"
            " SELECT 'idle-board', company_id, 'lever', 'idle' FROM source_board LIMIT 1"
        )
        _run(
            conn,
            "collect-workable",
            "r-running",
            "RUNNING",
            (now - timedelta(minutes=5)).isoformat(),
        )
        _run(
            conn,
            "collect-lever",
            "r-ok",
            "OK",
            (now - timedelta(hours=3)).isoformat(),
            (now - timedelta(hours=2)).isoformat(),
            stats={"jobs_new": 4},
        )
        _run(
            conn,
            "collect-greenhouse",
            "r-failed",
            "FAILED",
            (now - timedelta(hours=1)).isoformat(),
            (now - timedelta(minutes=50)).isoformat(),
            error="the feed answered 500",
        )
        for n in range(6):
            _run(
                conn,
                "collect",
                f"r-hist-{n}",
                "OK",
                (now - timedelta(days=n + 1)).isoformat(),
                (now - timedelta(days=n + 1) + timedelta(minutes=9)).isoformat(),
                stats={"by_board": {"idle-board": {"new": n}}},
            )
        CandidateStateRepo(conn).set(ensure_candidate(conn), "source_refresh.remoteok", "PAUSED")
    conn.close()
    return JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)


def test_the_whole_answer_is_the_one_the_two_queries_gave(api, monkeypatch) -> None:
    new, old = _both(api, monkeypatch)
    assert new == old
    rows = {r["id"]: r for r in new["sources"]}
    # The fixture's shapes are really in the answer, so the equality means something.
    assert rows["ashby"]["postings"] > 0 and rows["ashby"]["collected"] == 0
    assert rows["lever"]["collected"] > 0
    assert rows["remoteok"]["refresh_mode"] == "PAUSED"


def test_the_answer_follows_the_catalogue_between_requests(api, monkeypatch) -> None:
    before, _ = _both(api, monkeypatch)
    conn = connect(api.config.db_path)
    with transaction(conn):
        conn.execute("UPDATE job SET closed_at = NULL WHERE provider = 'ashby'")
        conn.execute("UPDATE job SET closed_at = ? WHERE provider = 'lever'", (CLOSED,))
        conn.execute(
            "UPDATE job SET first_seen_at = '2026-11-15T00:00:00+00:00'"
            " WHERE id = (SELECT MIN(id) FROM job)"
        )
    conn.close()
    after, legacy = _both(api, monkeypatch)
    assert after == legacy
    rows = {r["id"]: r for r in after["sources"]}
    assert rows["ashby"]["collected"] > 0 and rows["lever"]["collected"] == 0
    assert after != before


def test_two_profiles_sharing_one_catalogue(two_profiles, monkeypatch) -> None:  # noqa: F811
    answers = []
    for db, config in (("a_db", "a_config"), ("b_db", "b_config")):
        api = JobsApi(
            ServerConfig(db_path=two_profiles[db], config_dir=two_profiles[config]), quiet=True
        )
        new, old = _both(api, monkeypatch)
        assert new == old
        answers.append(
            {
                r["id"]: (r["postings"], r["collected"], r["last_new_posting"])
                for r in new["sources"]
            }
        )
    assert answers[0] == answers[1], "the catalogue's facts do not depend on the profile"
    assert any(p for p, _, _ in answers[0].values())


def test_a_job_table_without_closed_at_is_answered_as_before(monkeypatch) -> None:
    """The catalogue's open count never raised; it answered nothing. One pass
    that needs `closed_at` must not turn that into an error."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA.replace(" closed_at TEXT,", ""))
    conn.execute(
        "INSERT INTO job (id, provider, first_seen_at) VALUES ('j', 'greenhouse', '2026-10-01')"
    )
    new = [e.as_dict() for e in health(conn)]
    with monkeypatch.context() as m:
        _legacy(m)
        old = [e.as_dict() for e in health(conn)]
    assert new == old
    assert next(e for e in new if e["id"] == "greenhouse")["postings"] == 1


# ===================================================== 3. one pass, its plan

READS_JOB = re.compile(r"\b(FROM|JOIN)\s+job\b(?!_)", re.IGNORECASE)


def _statements(api: JobsApi) -> list[str]:
    seen: list[str] = []
    real = api.connect

    def connect_traced(*a, **k):
        conn = real(*a, **k)
        conn.set_trace_callback(seen.append)
        return conn

    api.connect = connect_traced  # type: ignore[method-assign]
    try:
        api.handle_api("GET", "/api/sources", {}, {})
    finally:
        api.connect = real  # type: ignore[method-assign]
    return [s for s in seen if READS_JOB.search(s)]


def test_sources_reads_the_provider_facts_of_job_once(api, monkeypatch) -> None:
    new = _statements(api)
    with monkeypatch.context() as m:
        _legacy(m)
        old = _statements(api)
    grouped = [s for s in new if "GROUP BY provider" in s and "source_board" not in s]
    assert len(grouped) == 1, new
    assert "closed_at IS NULL" in grouped[0] and "first_seen_at" in grouped[0]
    assert len(new) == len(old) - 1, (new, old)


def test_the_provider_facts_scan_the_table_rather_than_walk_its_index(api) -> None:
    conn = connect(api.config.db_path)
    try:
        plan = " | ".join(
            r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + health_mod.PROVIDER_FACTS)
        )
    finally:
        conn.close()
    assert "SCAN job" in plan and "INDEX" not in plan.replace("TEMP B-TREE", ""), plan


def test_the_catalogue_still_counts_by_itself_when_asked_alone(api) -> None:
    """`resolve` has other callers (the matrix) that pass no counts."""
    conn = connect(api.config.db_path)
    try:
        alone = {
            s.id: s.collected for s in catalogue.resolve(conn, CONFIG_DIR / "source_catalogue.yaml")
        }
        via_health = {
            e.source.id: e.source.collected
            for e in health(conn, catalogue_path=CONFIG_DIR / "source_catalogue.yaml")
        }
    finally:
        conn.close()
    assert alone == via_health

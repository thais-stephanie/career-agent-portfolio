"""Incremental scoring gives the SAME answer as a clean full recomputation.

The property this milestone rests on. `rescore` no longer walks the corpus;
it scores the postings the dirty ledger and the anti-join name. If either
misses a way a score can change, the incremental answer silently diverges
from the full one -- and nobody would know, because both are "complete".

So every case here is a DIFFERENTIAL: make one kind of change, run the
ordinary targeted rescore on the database, then take a copy of that database,
recompute EVERYTHING on the copy with `force`, and require the two `job_match`
populations to be byte-identical (ids and timestamps aside). One case per way
a score's inputs can move: text, geography, a hiring-scope sighting, a new
posting, a closed-and-reopened posting, a newer payload, a configuration
version, the matcher's result schema, an employer rename.

The other half is cost: a run with nothing to do must read no description,
and a run with one dirty posting must read one. That is asserted with a
counting connection, not a stopwatch.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.domain.enums import CollectionStatus
from career_agent.pipeline.rescore import RescoreMode, plan, rescore
from career_agent.storage import search_index
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    DiscoverySourceRepo,
    JobRawRepo,
    JobRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

CONFIG_DIR = committed_config_dir()

TEXTS = {
    "seed-0": (
        "Business Systems Engineer",
        "You will own our HubSpot CRM architecture, build workflow automation across "
        "our business systems, and maintain REST API integrations and webhooks between "
        "them. We use n8n for orchestration. We hire globally and work from anywhere.",
    ),
    "seed-1": (
        "Account Executive",
        "Carry a quota, close deals, and own outbound prospecting including cold calling "
        "for your territory. Build your own book of business. Based in Denver, Colorado.",
    ),
    "seed-2": (
        "Integration Specialist",
        "Build and maintain REST API integrations, webhooks and the data synchronization "
        "between our internal systems. Comfortable with JSON and SQL. Remote in Brazil.",
    ),
    "seed-3": (
        "Salesforce Administrator",
        "Administer Salesforce, build flows and reports, and support the revenue team. "
        "Five years of experience required. This role is open to candidates in the United "
        "States only.",
    ),
}

#: Columns that identify a score rather than describe it.
_IGNORED = {"id", "computed_at"}


# -- fixtures -----------------------------------------------------------------


@pytest.fixture
def corpus(tmp_path: Path) -> tuple[sqlite3.Connection, Any, dict[str, str], str]:
    """Four scored postings on a Greenhouse board, and the ledger empty."""
    conn = connect(tmp_path / "incremental.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    ids: dict[str, str] = {}
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="greenhouse",
                board_identifier="acme",
                board_url="https://boards.greenhouse.io/acme",
            )
        )
        for external, (title, text) in TEXTS.items():
            ids[external] = _put_job(conn, company_id, board_id, external, title, text)
    stats = rescore(conn, config)
    assert stats.jobs_scored == len(TEXTS)
    assert _ledger(conn) == {}
    yield conn, config, ids, board_id
    conn.close()


def _put_job(
    conn: sqlite3.Connection,
    company_id: str,
    board_id: str,
    external: str,
    title: str,
    text: str,
    *,
    location: str | None = "Remote",
) -> str:
    digest = JobRawRepo(conn).put(text)
    return JobRepo(conn).upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id=external,
            url=f"https://boards.greenhouse.io/acme/jobs/{external}",
            title=title,
            location_raw=location,
            content_hash=digest,
        ),
        status=CollectionStatus.NORMALISED,
    )


def _ledger(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        str(r["job_id"]): str(r["reason"])
        for r in conn.execute("SELECT job_id, reason FROM job_dirty")
    }


def _snapshot(conn: sqlite3.Connection, config: Any) -> dict[str, dict[str, Any]]:
    """Every stored score for this configuration version, keyed by posting."""
    rows = conn.execute(
        "SELECT * FROM job_match WHERE config_id = ? AND config_version = ?",
        (str(config.config_id), int(config.config_version)),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = {k: row[k] for k in row.keys() if k not in _IGNORED}  # noqa: SIM118
        # The serialised result carries its own timestamp; two passes a
        # second apart are the same arithmetic.
        result = json.loads(str(record["result_json"]))
        result.pop("computed_at", None)
        record["result_json"] = result
        out[str(row["job_id"])] = record
    return out


def _full_recompute_on_a_copy(
    conn: sqlite3.Connection, config: Any, tmp_path: Path
) -> dict[str, dict[str, Any]]:
    """Copy the database, recompute every open posting on the copy, snapshot."""
    copy_path = tmp_path / "full-copy.db"
    if copy_path.exists():
        copy_path.unlink()
    copy = connect(copy_path)
    try:
        conn.backup(copy)
        stats = rescore(copy, config, force=True)
        assert stats.mode == RescoreMode.ALL.value
        return _snapshot(copy, config)
    finally:
        copy.close()


def _assert_incremental_equals_full(
    conn: sqlite3.Connection, config: Any, tmp_path: Path, *, expect_scored: int | None = None
) -> None:
    stats = rescore(conn, config)
    assert stats.mode == RescoreMode.TARGETED.value
    if expect_scored is not None:
        assert stats.jobs_scored == expect_scored, stats
    incremental = _snapshot(conn, config)
    full = _full_recompute_on_a_copy(conn, config, tmp_path)
    assert incremental.keys() == full.keys()
    for job_id, record in full.items():
        assert incremental[job_id] == record, f"score of {job_id} diverges from a full recompute"
    assert _ledger(conn) == {}, "a completed pass leaves the ledger clear"


# -- one case per way a score can change ----------------------------------------


def test_a_no_op_pass_targets_nothing_and_reads_no_description(corpus, tmp_path) -> None:
    conn, config, _, _ = corpus
    reads = _CountingReads(conn)
    stats = rescore(conn, config)
    assert stats.jobs_targeted == 0
    assert stats.jobs_scored == 0
    assert stats.jobs_skipped_already_scored == len(TEXTS)
    assert stats.search_refresh == "INCREMENTAL"
    assert stats.search_rows_indexed == 0
    assert reads.description_statements == 0, "a pass with nothing to do read a description"
    assert reads.payload_statements == 0
    conn.set_trace_callback(None)
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=0)


def test_changed_text_is_recomputed_and_matches_full(corpus, tmp_path) -> None:
    conn, config, ids, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(
            conn,
            company_id,
            board_id,
            "seed-1",
            TEXTS["seed-1"][0],
            "Rewritten: build REST API integrations and webhooks for the revenue systems. "
            "Fully remote, hiring in Brazil and Portugal.",
        )
    assert _ledger(conn) == {ids["seed-1"]: "CONTENT"}
    before = _snapshot(conn, config)[ids["seed-1"]]
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)
    after = _snapshot(conn, config)[ids["seed-1"]]
    assert after["content_hash"] != before["content_hash"]


def test_changed_geography_is_recomputed_and_matches_full(corpus, tmp_path) -> None:
    conn, config, ids, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(
            conn, company_id, board_id, "seed-2", *TEXTS["seed-2"], location="Sao Paulo, Brazil"
        )
    assert _ledger(conn) == {ids["seed-2"]: "FACTS"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)


def test_a_hiring_scope_sighting_is_recomputed_and_matches_full(corpus, tmp_path) -> None:
    """A WWR sighting states `Anywhere in the World`; the canonical Greenhouse
    board states nothing. The gate reads the sighting (ADR-0013 addendum), so
    the score must move -- and must move the same way under both paths."""
    conn, config, ids, _ = corpus
    before = _snapshot(conn, config)[ids["seed-1"]]
    with transaction(conn):
        DiscoverySourceRepo(conn).record(
            ids["seed-1"],
            "wwr",
            "wwr-77",
            "ORIGIN_URL",
            payload={"region": "Anywhere in the World"},
        )
    assert _ledger(conn) == {ids["seed-1"]: "SIGHTING"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)
    after = _snapshot(conn, config)[ids["seed-1"]]
    assert after["eligibility_status"] != before["eligibility_status"]


def test_a_refreshed_sighting_is_recomputed(corpus, tmp_path) -> None:
    conn, config, ids, _ = corpus
    with transaction(conn):
        DiscoverySourceRepo(conn).record(
            ids["seed-0"], "wwr", "wwr-1", "ORIGIN_URL", payload={"region": "USA Only"}
        )
    rescore(conn, config)
    with transaction(conn):
        DiscoverySourceRepo(conn).record(
            ids["seed-0"], "wwr", "wwr-1", "ORIGIN_URL", payload={"region": "Anywhere in the World"}
        )
    assert _ledger(conn) == {ids["seed-0"]: "SIGHTING"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)


def test_a_newly_collected_posting_is_scored_and_matches_full(corpus, tmp_path) -> None:
    conn, config, _, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        new_id = _put_job(
            conn,
            company_id,
            board_id,
            "seed-9",
            "Revenue Operations Analyst",
            "Own the CRM data model, automate lead routing, and integrate marketing tools "
            "over REST APIs. Remote, Latin America.",
        )
    assert _ledger(conn) == {new_id: "NEW"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)
    assert new_id in _snapshot(conn, config)


def test_a_closed_then_reopened_posting_matches_full(corpus, tmp_path) -> None:
    conn, config, ids, board_id = corpus
    jobs = JobRepo(conn)
    with transaction(conn):
        closed = jobs.close_absent(board_id, {"seed-0", "seed-1", "seed-2"})
    assert closed == 1
    assert len(_ledger(conn)) == 1, "closing invalidates a worker's earlier input snapshot"
    stats = rescore(conn, config)
    assert stats.jobs_targeted == 0
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(conn, company_id, board_id, "seed-3", *TEXTS["seed-3"])
    assert _ledger(conn) == {ids["seed-3"]: "REOPENED"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)


def test_a_newer_payload_is_recomputed_and_matches_full(corpus, tmp_path) -> None:
    conn, config, ids, _ = corpus
    with transaction(conn):
        ProviderPayloadRepo(conn).put(
            ProviderPayloadRecord(
                job_id=ids["seed-3"],
                provider="greenhouse",
                payload={
                    "id": 3,
                    "title": TEXTS["seed-3"][0],
                    "location": {"name": "Remote - United States"},
                    "metadata": [],
                },
            )
        )
    assert _ledger(conn) == {ids["seed-3"]: "PAYLOAD"}
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=1)


def test_an_unchanged_payload_marks_nothing(corpus) -> None:
    conn, _, ids, _ = corpus
    record = ProviderPayloadRecord(job_id=ids["seed-0"], provider="greenhouse", payload={"id": 1})
    with transaction(conn):
        ProviderPayloadRepo(conn).put(record)
    conn.execute("DELETE FROM job_dirty")
    with transaction(conn):
        ProviderPayloadRepo(conn).put(record)
    assert _ledger(conn) == {}


def test_a_configuration_version_bump_recomputes_everything(corpus, tmp_path) -> None:
    conn, config, _, _ = corpus
    bumped = config.model_copy(update={"config_version": int(config.config_version) + 1})
    chosen = plan(conn, bumped)
    assert len(chosen.targets) == len(TEXTS)
    assert chosen.breakdown == {"MISSING_OR_STALE": len(TEXTS)}
    _assert_incremental_equals_full(conn, bumped, tmp_path, expect_scored=len(TEXTS))
    # And the previous version is untouched: revisions are immutable.
    assert len(_snapshot(conn, config)) == len(TEXTS)


def test_a_matcher_schema_bump_recomputes_everything(corpus, tmp_path) -> None:
    """Rows written under an older result schema carry empty facet columns
    and are stale whatever their content hash says. Simulated the way
    `test_schema_version_upgrade` does: age the stored rows by one."""
    conn, config, _, _ = corpus
    with transaction(conn):
        conn.execute("UPDATE job_match SET schema_version = schema_version - 1")
    chosen = plan(conn, config)
    assert len(chosen.targets) == len(TEXTS), "rows under an older result schema are stale"
    _assert_incremental_equals_full(conn, config, tmp_path, expect_scored=len(TEXTS))
    assert plan(conn, config).targets == []


def test_an_employer_rename_refreshes_the_search_index(corpus) -> None:
    conn, config, ids, _ = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        conn.execute("UPDATE company SET name = 'Acme Robotics' WHERE id = ?", (company_id,))
    assert set(_ledger(conn).values()) == {"COMPANY"}
    stats = rescore(conn, config)
    assert stats.search_refresh == "INCREMENTAL"
    assert stats.search_rows_indexed == len(TEXTS)
    hits = conn.execute(
        "SELECT job_id FROM job_search WHERE job_search MATCH ?",
        (search_index.to_match_query("Robotics"),),
    ).fetchall()
    assert {str(r["job_id"]) for r in hits} == set(ids.values())
    assert search_index.is_current(conn)


def test_explicit_targets_recompute_regardless_of_currency(corpus, tmp_path) -> None:
    conn, config, ids, _ = corpus
    stats = rescore(conn, config, job_ids=[ids["seed-0"], ids["seed-2"]])
    assert stats.mode == RescoreMode.EXPLICIT.value
    assert stats.jobs_scored == 2
    stats = rescore(conn, config, provider="greenhouse")
    assert stats.jobs_scored == len(TEXTS)
    stats = rescore(conn, config, board="greenhouse:acme")
    assert stats.jobs_scored == len(TEXTS)
    stats = rescore(conn, config, board="greenhouse:nobody")
    assert stats.jobs_scored == 0
    assert _snapshot(conn, config) == _full_recompute_on_a_copy(conn, config, tmp_path)


def test_dirty_mode_reads_only_the_ledger(corpus) -> None:
    conn, config, ids, _ = corpus
    with transaction(conn):
        conn.execute("DELETE FROM job_match WHERE job_id = ?", (ids["seed-0"],))
        DiscoverySourceRepo(conn).record(
            ids["seed-1"], "wwr", "wwr-2", "ORIGIN_URL", payload={"region": "Anywhere in the World"}
        )
    stats = rescore(conn, config, mode=RescoreMode.DIRTY)
    assert stats.jobs_targeted == 1, "the deleted score is not in the ledger and DIRTY ignores it"
    assert stats.jobs_scored == 1
    stats = rescore(conn, config)
    assert stats.plan_breakdown == {"MISSING_OR_STALE": 1}
    assert stats.jobs_scored == 1


def test_a_mark_written_during_the_pass_survives_it(corpus) -> None:
    """The generation guard. A collector re-marks a posting while the pass is
    scoring it; the pass must not clear that newer mark."""
    conn, config, ids, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(conn, company_id, board_id, "seed-0", TEXTS["seed-0"][0], "First rewrite.")
    calls = {"n": 0}

    def during(stats) -> None:
        # The first call is the plan; the ledger has been read. Re-mark now.
        calls["n"] += 1
        if calls["n"] == 1:
            with transaction(conn):
                _put_job(
                    conn, company_id, board_id, "seed-0", TEXTS["seed-0"][0], "Second rewrite."
                )

    stats = rescore(conn, config, progress=during)
    assert stats.dirty_marked == 1
    assert stats.dirty_cleared == 0, "the newer mark must survive"
    assert _ledger(conn) == {ids["seed-0"]: "CONTENT"}
    stats = rescore(conn, config)
    assert stats.jobs_scored == 1
    assert _ledger(conn) == {}


def test_the_seeded_new_mark_with_a_current_score_is_settled_without_work(corpus) -> None:
    """The demo seeder inserts a posting and scores it in the same process;
    its `NEW` mark names a posting whose score is already current."""
    conn, config, _, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        new_id = _put_job(conn, company_id, board_id, "seed-8", "Ops Analyst", "Own the CRM.")
    rescore(conn, config, job_ids=[new_id])
    assert _ledger(conn) == {}, "an explicit pass that scored the posting settles its mark"


def test_the_full_text_index_follows_a_text_change(corpus) -> None:
    conn, config, ids, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(
            conn, company_id, board_id, "seed-2", TEXTS["seed-2"][0], "Kubernetes platform work."
        )
    stats = rescore(conn, config)
    assert stats.search_refresh == "INCREMENTAL"
    assert stats.search_rows_indexed == 1
    q = search_index.to_match_query
    assert [
        str(r[0])
        for r in conn.execute(
            "SELECT job_id FROM job_search WHERE job_search MATCH ?", (q("Kubernetes"),)
        )
    ] == [ids["seed-2"]]
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM job_search WHERE job_search MATCH ?", (q("synchronization"),)
        ).fetchone()[0]
        == 0
    ), "the old text must be gone from the index"
    assert conn.execute("SELECT COUNT(*) FROM job_search").fetchone()[0] == len(TEXTS)
    assert search_index.is_current(conn)


def test_a_force_pass_rebuilds_the_index_and_still_agrees(corpus, tmp_path) -> None:
    conn, config, _, _ = corpus
    before = _snapshot(conn, config)
    stats = rescore(conn, config, force=True)
    assert stats.search_refresh == "FULL"
    assert _snapshot(conn, config) == before, "a forced recompute reproduces the same arithmetic"


# -- cost, asserted by counting reads rather than timing ----------------------


class _CountingReads:
    """Counts the statements that read description text or archived payloads,
    through SQLite's own trace hook. A stopwatch would make this test flaky;
    a statement count is the thing the design actually promises. Each
    statement reads one page of at most 500 postings, so a count of zero is
    zero postings and a count of one is at most one page."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.description_statements = 0
        self.payload_statements = 0

        def trace(sql: str) -> None:
            if "description_text" in sql:
                self.description_statements += 1
            if "FROM job_provider_payload" in sql:
                self.payload_statements += 1

        conn.set_trace_callback(trace)


def test_one_dirty_posting_reads_one_description(corpus) -> None:
    conn, config, ids, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(conn, company_id, board_id, "seed-0", TEXTS["seed-0"][0], "One rewrite.")
    reads = _CountingReads(conn)
    stats = rescore(conn, config)
    assert stats.jobs_scored == 1
    # One page for the score, one for the search-index row of the same posting.
    assert reads.description_statements == 2
    assert reads.payload_statements == 1
    conn.set_trace_callback(None)


def test_a_posting_not_yet_in_the_map_does_not_force_a_rebuild(corpus) -> None:
    """Two collectors at once: one pass records the corpus count while the
    other is still inserting. The map is short by those postings, their
    marks are in the ledger, and the next pass must refresh -- not rebuild
    1.5 GB under one lock, which is what took three collectors down on
    2026-09-12."""
    conn, config, _, board_id = corpus
    company_id = conn.execute("SELECT company_id FROM job LIMIT 1").fetchone()[0]
    with transaction(conn):
        _put_job(conn, company_id, board_id, "seed-7", "Data Analyst", "SQL and dashboards.")
    mapped = conn.execute("SELECT COUNT(*) FROM search_index_map").fetchone()[0]
    jobs = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    assert mapped == jobs - 1
    assert search_index.can_refresh(conn)
    stats = rescore(conn, config)
    assert stats.search_refresh == "INCREMENTAL"
    assert stats.search_rows_indexed == 1
    assert conn.execute("SELECT COUNT(*) FROM search_index_map").fetchone()[0] == jobs

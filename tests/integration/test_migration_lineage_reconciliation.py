"""The production upgrade path, end to end, on a database shaped like hers.

Production ran migrations 1..35 from `main`, then 36 and 37 from the Search
Fit lane (`searchfit_v4_beta`, `searchfit_reader_lifecycle`), which has not
merged. `main` then merged score replay as its own 0036 and the runner, which
identified a migration by number alone, reported it as applied on production:
`input_digest` was never created and the next rescore would have failed on
its first write (ADR-0028, `docs/checkpoints/production-migration-reconciliation.md`).

This file walks the whole path the fix promises, with the ledger produced BY
THE RUNNER rather than hand-inserted, and with real postings scored through
the real pipeline:

  1. main 1..35, then a foreign lineage's 36 and 37 under production's names;
  2. this build's `migrate` applies 0038 and nothing else, and leaves the two
     foreign rows byte for byte;
  3. a second `migrate` is a no-op;
  4. the first pass writes `input_digest` (the write that would have failed);
  5. a preference edit then REPLAYS every posting, equal to a forced pass.

The foreign lineage is simulated with a probe table, deliberately: the other
lane's schema is not `main`'s to carry, and the runner's behaviour depends on
the ledger names alone. The same walk against the lane's real files, and
against production's schema compared object for object, is recorded in the
checkpoint.
"""

from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

import pytest
from tests.integration.test_score_replay import (
    CONFIG_DIR,
    POSTINGS,
    _forced_on_a_copy,
    _halve_the_weights,
    _put,
    _snapshot,
)

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import (
    MIGRATIONS_DIR,
    applied_ledger,
    connect,
    discover_migrations,
    migrate,
    transaction,
)
from career_agent.storage.records import (
    CompanyRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

#: What production's ledger says at 36 and 37, by name.
FOREIGN_ROWS = {
    36: "searchfit_v4_beta",
    37: "searchfit_reader_lifecycle",
}


def _main_up_to(tmp_path: Path, last: int) -> Path:
    directory = tmp_path / f"main_to_{last}"
    directory.mkdir()
    for migration in discover_migrations(MIGRATIONS_DIR):
        if migration.version <= last:
            shutil.copy(migration.path, directory / migration.path.name)
    return directory


def _foreign_lane(tmp_path: Path) -> Path:
    """main 1..35 plus a lane that spent 36 and 37 under production's names."""
    directory = _main_up_to(tmp_path, 35)
    for version, name in FOREIGN_ROWS.items():
        (directory / f"{version:04d}_{name}.sql").write_text(
            f"CREATE TABLE lane_probe_{version} (id TEXT PRIMARY KEY);", encoding="utf-8"
        )
    return directory


def _ledger_rows(conn: sqlite3.Connection) -> list[tuple[int, str, str]]:
    return [
        (int(r["version"]), str(r["name"]), str(r["applied_at"]))
        for r in conn.execute(
            "SELECT version, name, applied_at FROM schema_migration ORDER BY version"
        )
    ]


@pytest.fixture
def production_shaped(tmp_path: Path):
    conn = connect(tmp_path / "production_shaped.db")
    applied = migrate(conn, _foreign_lane(tmp_path))
    assert [m.version for m in applied] == list(range(1, 38))
    assert applied_ledger(conn)[36] == "searchfit_v4_beta"
    assert applied_ledger(conn)[37] == "searchfit_reader_lifecycle"
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_match)")}
    assert "input_digest" not in columns, "the fixture must start where production is"
    yield conn, tmp_path
    conn.close()


def test_the_production_path(production_shaped) -> None:
    conn, tmp_path = production_shaped
    before = _ledger_rows(conn)

    # 2. Only what main added since runs: 0038, 0039 (Career Evidence V2),
    # 0040 (an experience description), 0041 (semantic evaluations) and the
    # later ones through 0044 (To apply merged into Interested).
    # The foreign rows stand, applied_at included.
    applied = migrate(conn)
    assert [(m.version, m.name) for m in applied] == [
        (38, "score_replay_input_digest"),
        (39, "career_evidence_structure"),
        (40, "career_experience_description"),
        (41, "semantic_evaluation"),
        (42, "job_retrieval_lane"),
        (43, "profile_identity"),
        (44, "merge_to_apply_into_interested"),
    ]
    after = _ledger_rows(conn)
    assert after[:37] == before
    assert after[37][:2] == (38, "score_replay_input_digest")
    assert after[38][:2] == (39, "career_evidence_structure")
    assert after[39][:2] == (40, "career_experience_description")
    assert after[40][:2] == (41, "semantic_evaluation")
    assert after[41][:2] == (42, "job_retrieval_lane")
    assert after[42][:2] == (43, "profile_identity")
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(job_match)")}
    assert "input_digest" in columns
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'lane_probe_36'").fetchone()

    # 3. Again: nothing.
    assert migrate(conn) == []
    assert _ledger_rows(conn) == after

    # 4. The first pass writes the digest. This is the write that failed.
    config, _ = load_search_config(CONFIG_DIR)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider="greenhouse", board_identifier="acme")
        )
        for external, (title, text, location) in POSTINGS.items():
            job_id = _put(conn, company_id, board_id, external, title, text, location)
            ProviderPayloadRepo(conn).put(
                ProviderPayloadRecord(
                    job_id=job_id,
                    provider="greenhouse",
                    payload={"id": external, "location": {"name": location or ""}},
                )
            )
    stats = rescore(conn, config)
    assert stats.jobs_scored == len(POSTINGS)
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == len(POSTINGS)
    digests = conn.execute(
        "SELECT COUNT(*) FROM job_match WHERE input_digest IS NOT NULL AND config_version = ?",
        (int(config.config_version),),
    ).fetchone()[0]
    assert digests == len(POSTINGS)

    # 5. A weight edit replays every posting and equals a forced recomputation.
    edited = _halve_the_weights(config)
    stats = rescore(conn, edited)
    assert stats.jobs_scored == len(POSTINGS)
    assert stats.jobs_replayed == len(POSTINGS), stats.replay_refused
    assert stats.jobs_read_in_full == 0
    replayed = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    assert replayed == full

    # And the ledger has not moved through any of it.
    assert _ledger_rows(conn) == after

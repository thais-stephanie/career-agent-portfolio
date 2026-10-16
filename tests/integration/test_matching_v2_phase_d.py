"""Phase D: work matching from stored semantic facts, switchable both ways.

The switch is `CAREER_AGENT_WORK_MATCH_SOURCE` (`legacy` by default,
`semantic_v2`). What is proven here, on a real database and the real
`rescore`:

* the default is legacy, a value that is neither is refused by name, and the
  legacy reader identity is `READER_IDENTITY` exactly as before;
* semantic_v2 has its own identity, so switching makes every stored score a
  reading the other reader no longer makes (the targeted plan picks them all
  up), and switching back does the same in reverse;
* a semantic pass answers the same from stored facts as from facts computed
  in memory, and counts which it used;
* the gates answer the same under both readers (work matching moves, no
  eligibility does), and the semantic reader really reads (an About-us
  sentence stops counting as work);
* ROLLBACK is the variable back to legacy and one targeted pass: the legacy
  rows come back identical, `job_features` stays where it is.
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
from career_agent.match import identity
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import phase_c, phase_d
from career_agent.pipeline.rescore import plan, rescore
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

CONFIG_DIR = committed_config_dir()

#: Invented postings (fictional employer).
POSTINGS = {
    "p-duty": ("Automation Specialist", "Responsibilities\nBuild workflow automation with "
               "Zapier and design REST API integrations for our clients.\nRequirements\n"
               "Experience with HubSpot.", "Remote (LATAM)"),
    "p-about": ("Account Manager", "About us\nAcme is a workflow automation and REST API "
                "integration company.\nResponsibilities\nManage client renewals.",
                "Remote - Brazil"),
    "p-passive": ("Coordinator", "Our HubSpot workflow automation is designed to help "
                  "teams.\nResponsibilities\nCoordinate the team.", None),
    "p-scope": ("Support Specialist", "Remote. Open to candidates based in Brazil and "
                "Argentina.\nResponsibilities\nAnswer customer tickets in Zendesk.",
                "Remote (LATAM)"),
    "p-us": ("Analyst", "You must be authorized to work in the United States.\n"
             "Responsibilities\nMaintain the CRM data model and data migration.",
             "Remote - United States"),
}  # fmt: skip

_IGNORED = frozenset({"id", "computed_at", "input_digest"})


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(phase_d.ENV, raising=False)
    monkeypatch.delenv(phase_c.ENV, raising=False)
    conn = connect(tmp_path / "phase_d.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme (fictional)"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider="greenhouse", board_identifier="acme")
        )
        for external, (title, text, location) in POSTINGS.items():
            JobRepo(conn).upsert_seen(
                JobRecord(
                    company_id=company_id,
                    source_board_id=board_id,
                    provider="greenhouse",
                    external_id=external,
                    url=f"https://boards.greenhouse.io/acme/jobs/{external}",
                    title=title,
                    location_raw=location,
                    content_hash=JobRawRepo(conn).put(text),
                ),
                status=CollectionStatus.NORMALISED,
            )
    yield conn, config
    conn.close()


def _snapshot(conn: sqlite3.Connection, config: Any) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM job_match WHERE config_id = ? AND config_version = ?",
        (str(config.config_id), int(config.config_version)),
    ).fetchall()
    out = {}
    for row in rows:
        record = {k: row[k] for k in row.keys() if k not in _IGNORED}  # noqa: SIM118
        result = json.loads(str(record["result_json"]))
        result.pop("computed_at", None)
        record["result_json"] = result
        out[str(row["job_id"])] = record
    return out


def _digests(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT DISTINCT input_digest FROM job_match")}


def _jobs(conn: sqlite3.Connection) -> list[tuple[JobFacts, str]]:
    rows = conn.execute(
        "SELECT j.id, j.title, j.location_raw, j.content_hash, r.description_text FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash ORDER BY j.id"
    ).fetchall()
    return [
        (
            JobFacts(
                title=r["title"],
                description=r["description_text"],
                location_raw=r["location_raw"],
                job_id=r["id"],
            ),
            r["content_hash"],
        )
        for r in rows
    ]


def _set(monkeypatch, source: str | None) -> None:
    if source is None:
        monkeypatch.delenv(phase_d.ENV, raising=False)
    else:
        monkeypatch.setenv(phase_d.ENV, source)


def _pass(conn, config, monkeypatch, source: str | None, *, force: bool = True):
    _set(monkeypatch, source)
    stats = rescore(conn, config, force=force)
    assert stats.errors == 0, stats.error_samples
    return _snapshot(conn, config), stats


def test_default_is_legacy_a_typo_is_refused_and_legacy_identity_is_unchanged() -> None:
    assert identity.work_match_source({}) == phase_d.LEGACY
    assert identity.work_match_source({phase_d.ENV: " Semantic_V2 "}) == phase_d.SEMANTIC_V2
    with pytest.raises(ValueError, match="expected one of legacy, semantic_v2"):
        identity.work_match_source({phase_d.ENV: "v2"})
    assert identity.reader_identity({}) == identity.READER_IDENTITY == "readers-14"
    semantic = identity.reader_identity({phase_d.ENV: "semantic_v2"})
    prefix = "readers-14+semantic/"
    assert semantic.startswith(prefix) and len(semantic) == len(prefix) + 8


SEMANTIC = {phase_d.ENV: "semantic_v2"}


def _bumped(name: str, extra=None) -> dict:
    from dataclasses import replace

    from career_agent.matching_v2 import components as comp

    graph = dict(comp.REGISTRY)
    if name in graph:
        graph[name] = replace(graph[name], version=graph[name].version + "-next")
    if extra is not None:
        graph[extra.name] = extra
    return graph


@pytest.mark.parametrize(
    "name",
    ["pay_facts", "work_match", "tools_match", "eligibility_facts", "seniority_facts",
     "evidence_policy", "work_facts", "score"],
)  # fmt: skip
def test_every_component_a_score_reads_moves_the_semantic_identity(name) -> None:
    """The semantic identity once covered work and tools only (2026-10-16):
    a pay change moved nothing and every stored score stayed stale."""
    before = identity.reader_identity(SEMANTIC)
    assert identity.reader_identity(SEMANTIC, _bumped(name)) != before


def test_a_component_no_score_reads_does_not_move_it() -> None:
    from career_agent.matching_v2.components import Component

    unused = Component("unused_facts", "1", "job", ("text_parser",))
    before = identity.reader_identity(SEMANTIC)
    assert identity.reader_identity(SEMANTIC, _bumped("unused_facts", unused)) == before
    unused_next = Component("unused_facts", "2", "job", ("text_parser",))
    assert identity.reader_identity(SEMANTIC, _bumped("x", unused_next)) == before


def test_the_legacy_readers_are_part_of_the_semantic_identity(monkeypatch) -> None:
    before = identity.reader_identity(SEMANTIC)
    monkeypatch.setattr(identity, "READER_IDENTITY", "readers-99")
    after = identity.reader_identity(SEMANTIC)
    assert after != before and after.startswith("readers-99+semantic/")
    assert identity.reader_identity({}) == "readers-99"


def test_a_pay_bump_restales_only_pay_and_what_reads_it() -> None:
    """Component-aware invalidation below the identity: a stored posting
    row is stale for `pay_facts` and its dependents, nothing else."""
    from career_agent.matching_v2 import components as comp

    graph = _bumped("pay_facts")
    moved = comp.affected({"pay_facts"}, graph)
    assert moved == {"pay_facts", "compensation_match", "score"}


def test_switch_rescore_and_roll_back(corpus, monkeypatch) -> None:
    conn, config = corpus
    legacy, stats = _pass(conn, config, monkeypatch, None)
    assert stats.work_match_source == "legacy" and stats.reader_identity == "readers-14"
    assert stats.work_match_reads == {}
    legacy_digest = _digests(conn)

    # Switching makes every score a reading this process no longer makes.
    _set(monkeypatch, "semantic_v2")
    planned = plan(conn, config)
    assert set(planned.targets) == set(legacy) and set(planned.breakdown) == {"READER_MOVED"}

    # Nothing materialised: facts computed in memory, counted, never written.
    memory, stats = _pass(conn, config, monkeypatch, "semantic_v2")
    assert stats.work_match_source == "semantic_v2"
    assert stats.reader_identity.startswith("readers-14+semantic/")
    assert stats.work_match_reads == {"read_not_materialised": len(POSTINGS)}
    assert conn.execute("SELECT count(*) FROM job_features").fetchone()[0] == 0
    semantic_digest = _digests(conn)
    assert semantic_digest.isdisjoint(legacy_digest)

    # Materialised: the same answer, from the store.
    done = phase_c.materialize(conn, config, _jobs(conn))
    assert done["written"] == len(POSTINGS)
    stored, stats = _pass(conn, config, monkeypatch, "semantic_v2")
    assert stored == memory
    assert stats.work_match_reads == {"from_store": len(POSTINGS)}
    facts = json.loads(conn.execute("SELECT facts_json FROM job_features LIMIT 1").fetchone()[0])
    assert {"text_structure", "semantic_evidence_facts"} <= set(facts)

    # The gates never move; the work reading does, where it should.
    for job_id, row in legacy.items():
        assert stored[job_id]["eligibility_status"] == row["eligibility_status"]
        assert stored[job_id]["result_json"]["gates"] == row["result_json"]["gates"]
    about = conn.execute("SELECT id FROM job WHERE external_id = 'p-about'").fetchone()[0]
    assert stored[about]["match_score"] < legacy[about]["match_score"], (
        "a company's description of itself is not the role's work"
    )

    # Rollback: legacy again, and one targeted pass restores every legacy row.
    _set(monkeypatch, None)
    planned = plan(conn, config)
    assert set(planned.targets) == set(legacy) and set(planned.breakdown) == {"READER_MOVED"}
    back, stats = _pass(conn, config, monkeypatch, None, force=False)
    assert stats.jobs_scored == len(POSTINGS) and stats.work_match_source == "legacy"
    assert back == legacy and _digests(conn) == legacy_digest
    assert conn.execute("SELECT count(*) FROM job_features").fetchone()[0] == len(POSTINGS)
    assert plan(conn, config).targets == []

    # And forward again, now that everything is current: one targeted pass.
    again, _ = _pass(conn, config, monkeypatch, "semantic_v2", force=False)
    assert again == stored


def test_eligibility_switch_is_independent(corpus, monkeypatch) -> None:
    """semantic_v2 with eligibility on legacy drops the stored eligibility
    facts (the gates read the advert); with v2 it uses them. Same answer."""
    conn, config = corpus
    phase_c.materialize(conn, config, _jobs(conn))
    monkeypatch.setenv(phase_c.ENV, "legacy")
    with_legacy, _ = _pass(conn, config, monkeypatch, "semantic_v2")
    monkeypatch.setenv(phase_c.ENV, "v2")
    with_v2, _ = _pass(conn, config, monkeypatch, "semantic_v2")
    assert with_legacy == with_v2

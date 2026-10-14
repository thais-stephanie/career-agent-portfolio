"""Phase C: eligibility read from stored shared facts, switchable both ways.

The switch is `CAREER_AGENT_ELIGIBILITY_SOURCE` (`legacy` by default, `v2`).
What is proven here, on a real database and the real `rescore`:

* the default is legacy, and a value that is neither is refused by name;
* a forced pass under v2 writes exactly the rows a legacy pass writes, with
  no facts stored (every posting read, and counted), with every fact stored
  (none read), and with some facts stale (those read);
* v2 really reads the store: a fact deliberately falsified in `job_features`
  moves that posting's answer under v2 and under nothing else;
* ROLLBACK is the variable back to legacy: the next pass writes the legacy
  answer again, with `job_features` left in place and nothing rebuilt.
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
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import phase_c
from career_agent.pipeline.rescore import rescore
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

CONFIG_DIR = committed_config_dir()

#: Invented postings (fictional employer), one per eligibility situation.
POSTINGS = {
    "p-scope": ("Support Specialist", "Remote. Open to candidates based in Brazil and "
                "Argentina.\nResponsibilities\nAnswer customer tickets.", "Remote (LATAM)"),
    "p-us-auth": ("Analyst", "You must be authorized to work in the United States. We are "
                  "unable to sponsor visas.", "Remote - United States"),
    "p-world": ("Support Specialist", "We hire globally. This role is fully remote "
                "worldwide.", "Remote"),
    "p-mm": ("Senior Engineer", "We work remotely across the US, Europe, LatAm and beyond.\n"
             "Responsibilities\nBuild features.", "Remote - United States"),
    "p-el": ("Solutions Engineer", "We hire globally and work remotely from anywhere in the "
             "world.", "Colombia"),
    "p-office": ("Coordinator", "This role requires three days a week in our Lisbon office.",
                 "Lisbon, Portugal"),
    "p-silence": ("Coordinator", "Responsibilities\nCoordinate the team.", None),
    "p-except": ("Support Specialist", "Remote across Latin America, except Brazil.",
                 "Remote"),
}  # fmt: skip

_IGNORED = frozenset({"id", "computed_at"})


@pytest.fixture
def corpus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(phase_c.ENV, raising=False)
    conn = connect(tmp_path / "phase_c.db")
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


def _pass(conn, config, monkeypatch, source: str | None):
    if source is None:
        monkeypatch.delenv(phase_c.ENV, raising=False)
    else:
        monkeypatch.setenv(phase_c.ENV, source)
    stats = rescore(conn, config, force=True)
    assert stats.errors == 0, stats.error_samples
    return _snapshot(conn, config), stats


def test_default_is_legacy_and_a_typo_is_refused() -> None:
    assert phase_c.eligibility_source({}) == phase_c.LEGACY
    assert phase_c.eligibility_source({phase_c.ENV: " V2 "}) == phase_c.V2
    with pytest.raises(ValueError, match="expected one of legacy, v2"):
        phase_c.eligibility_source({phase_c.ENV: "v3"})


def test_v2_writes_exactly_the_legacy_rows_and_rolls_back(corpus, monkeypatch) -> None:
    conn, config = corpus
    legacy, stats = _pass(conn, config, monkeypatch, None)
    assert stats.eligibility_source == "legacy" and stats.eligibility_reads == {}
    assert len(legacy) == len(POSTINGS)
    statuses = {r["eligibility_status"] for r in legacy.values()}
    assert len(statuses) >= 3, "the corpus must exercise several eligibility answers"

    # v2 with nothing materialised: every posting read from its advert.
    nothing, stats = _pass(conn, config, monkeypatch, "v2")
    assert nothing == legacy
    assert stats.eligibility_reads == {"read_not_materialised": len(POSTINGS)}

    # Materialised (the background pass, eligible first in production).
    done = phase_c.materialize(conn, config, _jobs(conn))
    assert done["written"] == len(POSTINGS)
    again = phase_c.materialize(conn, config, _jobs(conn))
    assert again == {"fresh": len(POSTINGS)}, "a second run must resume, not redo"
    stored, stats = _pass(conn, config, monkeypatch, "v2")
    assert stored == legacy
    assert stats.eligibility_reads == {"from_store": len(POSTINGS)}

    # One row stale (written under another negation key): read, not trusted.
    row = conn.execute("SELECT inputs FROM job_features ORDER BY job_id LIMIT 1").fetchone()
    inputs = json.loads(row[0])
    inputs["machinery.negation"] = "another"
    with transaction(conn):
        conn.execute(
            "UPDATE job_features SET inputs = ? WHERE job_id ="
            " (SELECT job_id FROM job_features ORDER BY job_id LIMIT 1)",
            (json.dumps(inputs),),
        )
    stale, stats = _pass(conn, config, monkeypatch, "v2")
    assert stale == legacy
    assert stats.eligibility_reads == {"from_store": len(POSTINGS) - 1, "read_stale": 1}

    # Rollback: the variable back to legacy. Nothing rebuilt, nothing dropped.
    back, stats = _pass(conn, config, monkeypatch, "legacy")
    assert back == legacy and stats.eligibility_source == "legacy"
    assert conn.execute("SELECT count(*) FROM job_features").fetchone()[0] == len(POSTINGS)


def test_v2_really_reads_the_store_and_rollback_undoes_it(corpus, monkeypatch) -> None:
    """Falsify one stored fact (the scope clause of `p-scope` removed): v2
    answers from it, legacy does not, and switching back restores the legacy
    answer without touching the falsified row."""
    conn, config = corpus
    legacy, _ = _pass(conn, config, monkeypatch, None)
    phase_c.materialize(conn, config, _jobs(conn))
    job_id = conn.execute("SELECT id FROM job WHERE external_id = 'p-scope'").fetchone()[0]
    facts = json.loads(
        conn.execute("SELECT facts_json FROM job_features WHERE job_id = ?", (job_id,)).fetchone()[
            0
        ]
    )
    assert facts["eligibility_facts"]["clauses"], "the posting states a scope clause"
    facts["eligibility_facts"]["clauses"] = []
    with transaction(conn):
        conn.execute(
            "UPDATE job_features SET facts_json = ? WHERE job_id = ?", (json.dumps(facts), job_id)
        )
    falsified, _ = _pass(conn, config, monkeypatch, "v2")
    assert falsified[job_id]["result_json"]["gates"] != legacy[job_id]["result_json"]["gates"]
    others = {k: v for k, v in falsified.items() if k != job_id}
    assert others == {k: v for k, v in legacy.items() if k != job_id}
    back, _ = _pass(conn, config, monkeypatch, None)
    assert back == legacy

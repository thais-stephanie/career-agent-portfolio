"""Score replay: the same answer, without reading the advert again.

`tests/integration/test_incremental_rescore.py` already holds the general
differential -- a targeted pass must equal a forced recomputation, for every
way a score's inputs can move -- and with replay in the targeted path, every
one of those cases now exercises it. This file is the replay CONTRACT: which
edits may reuse a stored reading, which must refuse, and what a refusal costs.

The oracle throughout is a forced pass on a copy. `--force` never replays (see
`rescore._replay_allowed`), so it is an independent recomputation and not the
thing under test comparing itself with itself.

Nothing here needs the owner's configuration or her corpus.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import SearchConfig, load_search_config
from career_agent.domain.enums import CollectionStatus
from career_agent.match import identity
from career_agent.pipeline.rescore import RescoreMode, rescore
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

CONFIG_DIR = committed_config_dir()

#: Deliberately varied: one that fires several signals, one that fires none,
#: one with a stated hiring scope, one with a salary in the payload, one whose
#: body is short. A replay that only works on rich postings is not a replay.
POSTINGS = {
    "p-crm": (
        "Business Systems Engineer",
        "You will own our HubSpot CRM architecture, build workflow automation across our "
        "business systems, and maintain REST API integrations and webhooks between them. "
        "We use n8n for orchestration. We hire globally and work from anywhere.",
        "Remote",
    ),
    "p-sales": (
        "Account Executive",
        "Carry a quota, close deals, and own outbound prospecting including cold calling "
        "for your territory. Build your own book of business. Based in Denver, Colorado.",
        "Denver, Colorado",
    ),
    "p-integration": (
        "Integration Specialist",
        "Build and maintain REST API integrations, webhooks and the data synchronization "
        "between our internal systems. Comfortable with JSON and SQL. Remote in Brazil.",
        "Remote (Brazil)",
    ),
    "p-thin": (
        "Operations Coordinator",
        "Coordinate the team. Send the reports.",
        None,
    ),
    "p-senior": (
        "Senior Revenue Operations Manager",
        "Lead the revenue operations function. Own the GTM systems roadmap, the CRM data "
        "model and the reporting stack. Minimum 8 years of experience required. "
        "This role is open to candidates based in Brazil and Argentina.",
        "Remote (LATAM)",
    ),
}


@pytest.fixture
def corpus(tmp_path: Path):
    """Five scored postings, the ledger clear, every row carrying a digest."""
    conn = connect(tmp_path / "replay.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="greenhouse",
                board_identifier="acme",
            )
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
    # The first pass on a fresh database has nothing to replay from.
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == len(POSTINGS)
    yield conn, config, tmp_path
    conn.close()


def _put(conn, company_id, board_id, external, title, text, location) -> str:
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


_IGNORED = frozenset({"id", "computed_at"})


def _snapshot(conn: sqlite3.Connection, config: Any) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM job_match WHERE config_id = ? AND config_version = ?",
        (str(config.config_id), int(config.config_version)),
    ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        record = {k: row[k] for k in row.keys() if k not in _IGNORED}  # noqa: SIM118
        result = json.loads(str(record["result_json"]))
        result.pop("computed_at", None)
        record["result_json"] = result
        out[str(row["job_id"])] = record
    return out


def _forced_on_a_copy(conn, config, tmp_path: Path, name: str = "oracle.db"):
    """An independent full recomputation. `--force` never replays."""
    path = tmp_path / name
    if path.exists():
        path.unlink()
    copy = connect(path)
    try:
        conn.backup(copy)
        stats = rescore(copy, config, force=True)
        assert stats.mode == RescoreMode.ALL.value
        assert stats.jobs_replayed == 0, "a forced pass replayed; the oracle is not independent"
        return _snapshot(copy, config), stats
    finally:
        copy.close()


def _bump(config: SearchConfig, **sections: Any) -> SearchConfig:
    """A new configuration version with the named sections replaced."""
    data = config.model_dump()
    data["config_version"] = config.config_version + 1
    for name, value in sections.items():
        data[name] = value
    return SearchConfig.model_validate(data)


def _halve_the_weights(config: SearchConfig) -> SearchConfig:
    data = config.model_dump()
    component = data["scoring"]["components"]["responsibilities"]
    component["max"] = component["max"] / 2
    component["weights"] = {k: v / 2 for k, v in component["weights"].items()}
    return _bump(config, scoring=data["scoring"])


# =====================================================================
# 1 and 2. A scoring-only edit replays, and agrees with a full pass
# =====================================================================


def test_a_weight_change_replays_every_posting_and_equals_a_full_pass(corpus):
    conn, config, tmp_path = corpus
    edited = _halve_the_weights(config)

    stats = rescore(conn, edited)
    assert stats.jobs_scored == len(POSTINGS)
    assert stats.jobs_replayed == len(POSTINGS), stats.replay_refused
    assert stats.jobs_read_in_full == 0
    assert stats.replay_source_version == config.config_version

    replayed = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    assert replayed.keys() == full.keys()
    for job_id, record in full.items():
        assert replayed[job_id] == record, f"replayed score of {job_id} diverges from a full pass"


def test_the_weight_change_actually_moved_something(corpus):
    """A guard on the test above: equality proves nothing if nothing moved."""
    conn, config, _ = corpus
    before = {j: r["match_score"] for j, r in _snapshot(conn, config).items()}
    edited = _halve_the_weights(config)
    rescore(conn, edited)
    after = {j: r["match_score"] for j, r in _snapshot(conn, edited).items()}
    assert any(before[j] != after[j] for j in before), (
        "halving the responsibilities weights changed no score; this corpus"
        " cannot detect a replay that silently returns the old answer"
    )


def test_the_previous_version_is_still_readable_after_a_replay(corpus):
    """Historical rows survive, and answer the question they were asked."""
    conn, config, _ = corpus
    before = _snapshot(conn, config)
    rescore(conn, _halve_the_weights(config))
    after = _snapshot(conn, config)
    assert after == before, "replaying into a new version rewrote the old one"


# =====================================================================
# 3, 4 and 5. What must refuse
# =====================================================================


def test_changed_text_refuses_the_replay_and_reads_the_advert(corpus):
    conn, config, tmp_path = corpus
    with transaction(conn):
        digest = JobRawRepo(conn).put("A completely rewritten advert about warehouse logistics.")
        conn.execute("UPDATE job SET content_hash = ? WHERE external_id = ?", (digest, "p-crm"))
    edited = _halve_the_weights(config)
    stats = rescore(conn, edited)

    assert stats.jobs_read_in_full >= 1
    assert (
        stats.replay_refused.get("CONTENT_MOVED", 0) + stats.replay_refused.get("INPUTS_MOVED", 0)
        >= 1
    ), stats.replay_refused

    replayed = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    for job_id, record in full.items():
        assert replayed[job_id] == record


def test_a_lexicon_change_refuses_every_replay(corpus):
    """The reading configuration moved, so no stored reading is an answer."""
    conn, config, tmp_path = corpus
    data = config.model_dump()
    signal = next(iter(data["lexicon"]))
    data["lexicon"][signal]["patterns"] = [*data["lexicon"][signal]["patterns"], "quantum ledger"]
    edited = _bump(config, lexicon=data["lexicon"])

    assert identity.input_digest(edited) != identity.input_digest(config)
    stats = rescore(conn, edited)
    assert stats.jobs_replayed == 0, "a lexicon change replayed a stale reading"
    assert stats.jobs_read_in_full == len(POSTINGS)
    assert stats.replay_source_version is None

    read = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


def test_an_eligibility_change_refuses_every_replay(corpus):
    """Gate outcomes are carried, so they may never be carried across a change."""
    conn, config, tmp_path = corpus
    data = config.model_dump()
    data["eligibility"]["eligible_countries"] = ["PT"]
    edited = _bump(config, eligibility=data["eligibility"])

    stats = rescore(conn, edited)
    assert stats.jobs_replayed == 0, "an eligibility change replayed a stale gate outcome"
    assert stats.jobs_read_in_full == len(POSTINGS)

    read = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


def test_a_screening_change_refuses_every_replay(corpus):
    conn, config, _ = corpus
    data = config.model_dump()
    data["screening"]["required_any_groups"] = []
    edited = _bump(config, screening=data["screening"])
    stats = rescore(conn, edited)
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == len(POSTINGS)


def test_a_reader_identity_bump_refuses_every_replay(corpus, monkeypatch):
    """The configuration did not move; the CODE did. This is what replaces --force."""
    conn, config, tmp_path = corpus
    edited = _halve_the_weights(config)
    monkeypatch.setattr(identity, "READER_IDENTITY", "readers-under-test")

    stats = rescore(conn, edited)
    assert stats.jobs_replayed == 0, "a reader identity bump reused readings from the old reader"
    assert stats.jobs_read_in_full == len(POSTINGS)

    read = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


def test_the_pass_after_an_identity_bump_replays_from_the_readings_it_wrote(corpus, monkeypatch):
    """The other half of the bump, and the half that makes it affordable.

    Refusing every stored reading is only correct if the refusal is ONCE. The
    pass that reads the adverts again must write its readings under the NEW
    identity, so the next scoring-only edit replays from them. Without this the
    bump would be indistinguishable from a permanent `--no-replay`.

    This is the shape the release merge takes: `ceec020` moved what
    `match/gates.py` produces, `READER_IDENTITY` moved with it, the first pass
    on an existing corpus reads everything, and the second is fast again.
    """
    conn, config, tmp_path = corpus
    monkeypatch.setattr(identity, "READER_IDENTITY", "readers-after-the-gates-fix")

    first = rescore(conn, _halve_the_weights(config))
    assert first.jobs_replayed == 0, "a reading written by the old reader was reused"
    assert first.jobs_read_in_full == len(POSTINGS)

    moved = _halve_the_weights(config).model_copy(update={"config_version": 99})
    second = rescore(conn, moved)
    assert second.jobs_replayed == len(POSTINGS), second.replay_refused
    assert second.jobs_read_in_full == 0

    replayed = _snapshot(conn, moved)
    full, _ = _forced_on_a_copy(conn, moved, tmp_path)
    for job_id, record in full.items():
        assert replayed[job_id] == record


def test_a_newer_payload_refuses_the_replay_for_that_posting(corpus):
    """A payload can carry a declared hiring scope, so its change is an input change."""
    conn, config, _ = corpus
    target = str(conn.execute("SELECT id FROM job WHERE external_id = 'p-crm'").fetchone()["id"])
    with transaction(conn):
        ProviderPayloadRepo(conn).put(
            ProviderPayloadRecord(
                job_id=target,
                provider="greenhouse",
                payload={"id": "p-crm", "location": {"name": "Remote"}, "extra": "moved"},
            )
        )
    stats = rescore(conn, _halve_the_weights(config))
    assert stats.replay_refused.get("INPUTS_MOVED", 0) >= 1, stats.replay_refused
    assert stats.jobs_read_in_full >= 1


# =====================================================================
# 6. Unchanged configuration does no work
# =====================================================================


def test_an_unchanged_configuration_scores_nothing(corpus):
    conn, config, _ = corpus
    stats = rescore(conn, config)
    assert stats.jobs_scored == 0
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == 0


# =====================================================================
# The escape hatches stay escape hatches
# =====================================================================


def test_force_never_replays(corpus):
    conn, config, _ = corpus
    edited = _halve_the_weights(config)
    stats = rescore(conn, edited, force=True)
    assert stats.mode == RescoreMode.ALL.value
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == len(POSTINGS)


def test_an_explicit_provider_pass_never_replays(corpus):
    conn, config, _ = corpus
    edited = _halve_the_weights(config)
    stats = rescore(conn, edited, provider="greenhouse")
    assert stats.mode == RescoreMode.EXPLICIT.value
    assert stats.jobs_replayed == 0


def test_no_replay_switch_forces_the_full_path(corpus):
    conn, config, tmp_path = corpus
    edited = _halve_the_weights(config)
    stats = rescore(conn, edited, no_replay=True)
    assert stats.jobs_replayed == 0
    assert stats.jobs_read_in_full == len(POSTINGS)
    read = _snapshot(conn, edited)
    full, _ = _forced_on_a_copy(conn, edited, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


# =====================================================================
# The upgrade path
# =====================================================================


def test_rows_without_a_digest_are_never_replayed_from(corpus):
    """Every row written before migration 0038 has a NULL digest.

    Simulated by clearing the column, which is exactly the state an upgraded
    database is in. The pass must fall back to reading, and must fill the
    column in as it goes, so the NEXT preference edit can replay.
    """
    conn, config, _ = corpus
    with transaction(conn):
        conn.execute("UPDATE job_match SET input_digest = NULL")

    first = rescore(conn, _halve_the_weights(config))
    assert first.jobs_replayed == 0
    assert first.jobs_read_in_full == len(POSTINGS)
    assert first.replay_source_version is None

    filled = conn.execute(
        "SELECT COUNT(*) FROM job_match WHERE input_digest IS NOT NULL"
    ).fetchone()[0]
    assert filled == len(POSTINGS)

    second_config = _halve_the_weights(_halve_the_weights(config))
    second = rescore(conn, second_config)
    assert second.jobs_replayed == len(POSTINGS), second.replay_refused


# =====================================================================
# HCE01 (ADR-0029). A row written under the previous result schema is
# never a replay source, and the population never empties while the pass
# that rewrites it is under way.
# =====================================================================


def _age_every_row_to_the_previous_schema(
    conn: sqlite3.Connection, config: SearchConfig, schema: int | None = None
) -> int:
    """Turn the corpus into what production is the moment the build moves
    from MATCH_SCHEMA_VERSION 8 to 9: rows whose digest and content are
    current and whose result shape is the old one (five gate outcomes).

    Aged BELOW the replay floor by default. Schema 10 changed only arithmetic
    and provenance, so a schema 9 row is a legitimate replay source; the
    refusal this helper exists to exercise is the one for a READING shape
    that moved, which is anything under `REPLAY_MIN_SCHEMA`."""
    from career_agent.domain.matching import REPLAY_MIN_SCHEMA

    aged = REPLAY_MIN_SCHEMA - 1 if schema is None else schema
    with transaction(conn):
        cursor = conn.execute(
            "UPDATE job_match SET schema_version = ? WHERE config_id = ? AND config_version = ?",
            (aged, str(config.config_id), int(config.config_version)),
        )
    return cursor.rowcount


def test_a_row_under_the_previous_result_schema_is_never_a_replay_source(corpus):
    """Matrix 16. The schema floor alone refuses the replay (the identity
    is unchanged here on purpose: the belt without the braces), every
    posting is a target again, every row is rewritten under the current
    schema, and the rewritten rows equal an independent full pass."""
    from career_agent.domain.matching import MATCH_SCHEMA_VERSION
    from career_agent.storage.mvp_repo import MatchRepo

    conn, config, tmp_path = corpus
    aged = _age_every_row_to_the_previous_schema(conn, config)
    assert aged == len(POSTINGS)
    repo = MatchRepo(conn)
    config_id, version = str(config.config_id), int(config.config_version)
    assert repo.stale_schema_count(config_id, version) == len(POSTINGS)
    from career_agent.domain.matching import REPLAY_MIN_SCHEMA

    assert (
        repo.replay_source_version(
            config_id,
            version,
            identity.input_digest(config),
            REPLAY_MIN_SCHEMA,
            MATCH_SCHEMA_VERSION,
        )
        is None
    )

    stats = rescore(conn, config)
    assert stats.jobs_scored == len(POSTINGS), "an aged row was treated as already scored"
    assert stats.jobs_replayed == 0, "a five-gate reading was replayed into a seven-gate result"
    assert stats.jobs_read_in_full == len(POSTINGS)
    # No version qualifies as a source, so replay is off for the whole pass
    # (the stronger refusal: nothing is even consulted per row).
    assert stats.replay_source_version is None
    assert sum(stats.replay_refused.values()) == 0
    assert repo.stale_schema_count(config_id, version) == 0
    for row in conn.execute("SELECT schema_version FROM job_match").fetchall():
        assert int(row["schema_version"]) == MATCH_SCHEMA_VERSION

    read = _snapshot(conn, config)
    full, _ = _forced_on_a_copy(conn, config, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


def test_a_scoring_only_schema_move_replays_instead_of_reading_again(corpus):
    """Schema 10 changed Search Fit's arithmetic and provenance, never a
    reading. A row written by schema 9 at the CURRENT config version is an
    older build's answer, not this pass's own output, so it replays: no
    posting is read again, every row is rewritten under schema 10, and the
    result equals an independent full pass."""
    from career_agent.domain.matching import MATCH_SCHEMA_VERSION, REPLAY_MIN_SCHEMA

    conn, config, tmp_path = corpus
    assert _age_every_row_to_the_previous_schema(conn, config, REPLAY_MIN_SCHEMA) == len(POSTINGS)
    # Aging by UPDATE fires the population trigger, which drops the score
    # receipts. A row the previous build WROTE keeps its receipt, so put them
    # back: that is the production state this test describes.
    from career_agent.storage import invalidation

    ids = [str(r[0]) for r in conn.execute("SELECT id FROM job").fetchall()]
    with transaction(conn):
        for job_id, revision in invalidation.input_revisions(conn, ids).items():
            invalidation.receipt(conn, job_id, config, revision)
    stats = rescore(conn, config)
    assert stats.jobs_scored == len(POSTINGS)
    assert stats.replay_source_version == int(config.config_version)
    assert stats.jobs_replayed == len(POSTINGS), stats.replay_refused
    assert stats.jobs_read_in_full == 0
    for row in conn.execute("SELECT schema_version FROM job_match").fetchall():
        assert int(row["schema_version"]) == MATCH_SCHEMA_VERSION
    read = _snapshot(conn, config)
    full, _ = _forced_on_a_copy(conn, config, tmp_path)
    for job_id, record in full.items():
        assert read[job_id] == record


def test_the_population_stays_served_while_the_schema_pass_is_under_way(corpus):
    """Matrix 17. A schema-only move keeps `config_version`, so the rows
    are rewritten in place: after a partial pass the served population is
    still every posting (old rows beside new), `/api/health`'s stale count
    says how many are still the old shape, and the revision resolver keeps
    the same revision current. Nothing goes empty at any moment."""
    from career_agent.storage.mvp_repo import MatchRepo
    from career_agent.storage.revisions import resolve

    conn, config, _ = corpus
    _age_every_row_to_the_previous_schema(conn, config)
    repo = MatchRepo(conn)
    config_id, version = str(config.config_id), int(config.config_version)

    partial = rescore(conn, config, limit=2)
    assert partial.jobs_scored == 2
    assert repo.count_for(config_id, version) == len(POSTINGS)
    assert repo.stale_schema_count(config_id, version) == len(POSTINGS) - 2
    decision = resolve(conn, config_id, version)
    assert decision.serving is not None and decision.serving.scored == len(POSTINGS)
    assert decision.is_current and not decision.has_nothing

    rest = rescore(conn, config)
    assert rest.jobs_scored == len(POSTINGS) - 2, "the two rewritten rows were rewritten again"
    assert repo.stale_schema_count(config_id, version) == 0
    assert repo.count_for(config_id, version) == len(POSTINGS)


# =====================================================================
# Schema 11: an unstated level is scored as MID, and stored rows replay
# =====================================================================


def test_a_schema_10_reading_replays_into_the_mid_fallback(corpus):
    """Rows written under schema 10 scored an unstated level as unevidenced.
    The bump is arithmetic only: every row replays, nothing is read again, and
    the result equals a full pass, including the seniority label."""
    conn, config, tmp_path = corpus
    # Editing a row fires the trigger that drops its score receipt; a real
    # schema-10 row keeps it, so the receipts are put back after the edit.
    receipts = conn.execute("SELECT * FROM job_score_revision").fetchall()
    with transaction(conn):
        for row in conn.execute("SELECT job_id, result_json FROM job_match").fetchall():
            result = json.loads(str(row["result_json"]))
            for component in result.get("components", []):
                if component.get("key") == "seniority":
                    component["points"] = 0
                    component["label"] = "stale schema 10 label"
            conn.execute(
                "UPDATE job_match SET schema_version = 10, result_json = ? WHERE job_id = ?",
                (json.dumps(result), row["job_id"]),
            )
        conn.executemany(
            "INSERT OR REPLACE INTO job_score_revision VALUES (?, ?, ?, ?)",
            [tuple(r) for r in receipts],
        )

    stats = rescore(conn, config)
    assert stats.jobs_replayed == len(POSTINGS), stats.replay_refused
    assert stats.jobs_read_in_full == 0

    replayed = _snapshot(conn, config)
    full, _ = _forced_on_a_copy(conn, config, tmp_path)
    for job_id, record in full.items():
        assert replayed[job_id] == record, f"{job_id} did not replay into schema 11"
    assert "stale schema 10 label" not in json.dumps(replayed)

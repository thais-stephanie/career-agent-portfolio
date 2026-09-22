"""The development harness, end to end, on a real database and no network.

What these tests actually assert is that the harness is *not a second
extraction path*. The requests it exports are the production requests, the
answers it imports go through the production validation, verification and
assembly, and the only thing that distinguishes the run afterwards is the two
labels on the stored rows.

The failure cases matter as much as the success one. A harness that quietly
repaired malformed output would report a health the production path does not
have, and the entire point of running this before spending money is to find out
what the real prompt and the real schema produce.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.llm.transport import ALL_DIMENSIONS
from career_agent.pipeline.cowork import (
    COWORK_MODEL_IDENTIFIER,
    EXPORTED_KEYS,
    CoworkBatchError,
    export_batch,
    load_batch,
    run_batch,
)
from career_agent.pipeline.extract import load_source, store_outcome
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

DESCRIPTION = (
    "Business Technology Analyst\n\n"
    "You will own our internal business systems end to end.\n"
    "We hire anywhere in the world, except Cuba and Iran.\n"
    "Experience with HubSpot is required.\n"
)

PAYLOAD = {
    "id": 4242,
    "title": "Business Technology Analyst",
    "location": {"name": "Remote - United States"},
    "content": "<p>ignored here</p>",
}


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def job_id(conn: sqlite3.Connection) -> str:
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="exampleco", name="Example Co"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company, provider="greenhouse", board_identifier="exampleco"
            )
        )
        content_hash = JobRawRepo(conn).put(DESCRIPTION)
        identifier = JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company,
                source_board_id=board,
                provider="greenhouse",
                external_id="4242",
                url="https://boards.greenhouse.io/exampleco/jobs/4242",
                title="Business Technology Analyst",
                content_hash=content_hash,
            )
        )
        ProviderPayloadRepo(conn).put(
            ProviderPayloadRecord(job_id=identifier, provider="greenhouse", payload=PAYLOAD)
        )
    return identifier


# --- the answers an assisting agent would write -----------------------------


def description_answer() -> str:
    """A complete, well-formed description-family answer.

    Every dimension appears, because a forgotten dimension and a dimension the
    posting was silent about are different facts and the assembler refuses to
    treat them as one.
    """
    observations = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.0}
        for name in ALL_DIMENSIONS
        if name != "hiring_scope"
    ]
    observations.append(
        {
            "dimension": "hiring_scope",
            "status": "EXPLICIT",
            "value": "WORLDWIDE:!CU,IR",
            "confidence": 0.95,
            "evidence_id": "ev_01",
        }
    )
    return json.dumps(
        {
            "observed_title": "Business Technology Analyst",
            "function_signals": ["business_systems"],
            "observations": observations,
            "responsibilities": [
                {
                    "category": "business_systems_administration",
                    "prominence": "PRIMARY",
                    "confidence": 0.9,
                    "evidence_id": "ev_02",
                }
            ],
            "software": [
                {
                    "raw_mention": "HubSpot",
                    "canonical_suggestion": "hubspot",
                    "centrality": "REQUIRED",
                    "confidence": 0.9,
                    "evidence_id": "ev_03",
                }
            ],
            "languages": [],
            "evidence": [
                {
                    "id": "ev_01",
                    "source_kind": "JOB_DESCRIPTION",
                    "quote": "We hire anywhere in the world, except Cuba and Iran.",
                },
                {
                    "id": "ev_02",
                    "source_kind": "JOB_DESCRIPTION",
                    "quote": "You will own our internal business systems end to end.",
                },
                {
                    "id": "ev_03",
                    "source_kind": "JOB_DESCRIPTION",
                    "quote": "Experience with HubSpot is required.",
                },
            ],
        }
    )


def provider_answer() -> str:
    """The ATS record read on its own, disagreeing with the posting on purpose.

    The posting says worldwide; the ATS location field names the United States.
    Both are true statements about different artefacts, and the design requires
    them to survive side by side rather than be reconciled here.
    """
    return json.dumps(
        {
            "observations": [
                {
                    "dimension": "hiring_location_hint",
                    "status": "EXPLICIT",
                    "value": "COUNTRY_LIST:US",
                    "confidence": 0.9,
                    "evidence_id": "ev_01",
                }
            ],
            "evidence": [
                {
                    "id": "ev_01",
                    "source_kind": "PROVIDER_FIELD",
                    "provider": "greenhouse",
                    "source_field": "location.name",
                    "source_value": "Remote - United States",
                }
            ],
        }
    )


def answer(batch_dir: Path, family: str, raw: str) -> None:
    """Write one result file the way the README asks for it."""
    request = next((batch_dir / "requests").glob(f"*-{family}-*.json"))
    payload = json.loads(request.read_text(encoding="utf-8"))
    (batch_dir / "results" / request.name).write_text(
        json.dumps({"cache_key": payload["cache_key"], "raw_output": raw}), encoding="utf-8"
    )


@pytest.fixture
def batch(conn: sqlite3.Connection, job_id: str, tmp_path: Path) -> Path:
    source = load_source(conn, job_id)
    summary = export_batch([source], tmp_path / "m2-cowork", "batch-test")
    return summary.directory


# --- export -----------------------------------------------------------------


def test_export_writes_one_file_per_call_the_pipeline_would_make(
    conn: sqlite3.Connection, job_id: str, tmp_path: Path
) -> None:
    """Two families, two files -- and never a file for a call we would not make."""
    summary = export_batch([load_source(conn, job_id)], tmp_path / "m2-cowork", "batch-shape")

    assert summary.jobs == 1
    assert summary.description_requests == 1
    assert summary.provider_requests == 1
    assert len(list((summary.directory / "requests").glob("*.json"))) == 2


def test_the_exported_prompt_is_the_production_prompt(batch: Path) -> None:
    """Verbatim, not a paraphrase.

    The whole value of the exercise is that what the assisting agent answers is
    what a paid model would have been asked. A prompt rewritten to read more
    naturally here would make every result evidence about a prompt we do not
    ship.
    """
    from career_agent.llm.prompts import DESCRIPTION_PROMPT_VERSION, load_prompt

    exported = json.loads(
        next((batch / "requests").glob("*-description-*.json")).read_text(encoding="utf-8")
    )

    assert exported["system"] == load_prompt(DESCRIPTION_PROMPT_VERSION)
    assert exported["user"] == DESCRIPTION
    assert exported["prompt_version"] == DESCRIPTION_PROMPT_VERSION


def test_an_exported_request_carries_nothing_but_the_declared_keys(batch: Path) -> None:
    """The privacy boundary, asserted rather than trusted.

    A batch contains a job posting, an ATS record and our own prompts. No CV,
    no career intent, no residence, no compensation requirement, no candidate
    identity -- and this test is what stops a convenient extra field from
    arriving later without anyone noticing what it carries.
    """
    for path in (batch / "requests").glob("*.json"):
        assert set(json.loads(path.read_text(encoding="utf-8"))) == EXPORTED_KEYS


def test_the_provider_request_never_carries_the_posting_text(batch: Path) -> None:
    exported = json.loads(
        next((batch / "requests").glob("*-provider-*.json")).read_text(encoding="utf-8")
    )

    assert "business systems end to end" not in json.dumps(exported)
    assert exported["user"].endswith("hiring_location_hint\tlocation.name\tRemote - United States")


# --- the round trip ---------------------------------------------------------


def test_a_full_round_trip_produces_a_stored_fingerprint(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    answer(batch, "description", description_answer())
    answer(batch, "provider", provider_answer())

    imported = load_batch(batch)
    assert imported.unanswered == ()
    assert imported.answered_jobs() == [job_id]

    outcomes = run_batch(imported, [load_source(conn, job_id)])
    assert len(outcomes) == 1
    outcome = outcomes[0]

    assert outcome.ok, outcome.failure
    assert outcome.paid_calls == 0
    assert outcome.unverified_evidence == ()
    assert outcome.verification.rate == 1.0

    with transaction(conn):
        fingerprint_id = store_outcome(conn, outcome)

    stored = conn.execute(
        "SELECT model, prompt_version FROM fingerprint WHERE id = ?", (fingerprint_id,)
    ).fetchone()
    assert stored["model"] == COWORK_MODEL_IDENTIFIER


def test_both_provenance_channels_survive_side_by_side(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """The posting says worldwide; the ATS record names the United States.

    Neither wins here and neither is softened. M3 weighs them with both sets of
    evidence in hand, and it can only do that if M2 kept both.
    """
    answer(batch, "description", description_answer())
    answer(batch, "provider", provider_answer())

    outcome = run_batch(load_batch(batch), [load_source(conn, job_id)])[0]
    assert outcome.fingerprint is not None

    scope = outcome.fingerprint.eligibility.hiring_scope
    assert scope.value is not None
    assert scope.value.kind.value == "WORLDWIDE"
    assert set(scope.value.exclusions) == {"CU", "IR"}

    provider_rows = outcome.fingerprint.provider_observations
    assert [row.dimension.value for row in provider_rows] == ["hiring_location_hint"]
    assert provider_rows[0].value == "COUNTRY_LIST:US"


def test_worldwide_with_exclusions_is_never_downgraded(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """The rule that runs backwards from intuition.

    "Open worldwide, except where we cannot legally employ" is the usual shape
    of a genuinely global posting -- exactly the jobs this product exists to
    find. Treating it as suspicious would degrade the best results in the
    corpus.
    """
    answer(batch, "description", description_answer())
    answer(batch, "provider", provider_answer())

    outcome = run_batch(load_batch(batch), [load_source(conn, job_id)])[0]

    assert outcome.validation is not None
    assert outcome.validation.corrections == []


def test_the_run_is_recorded_as_development_and_as_free(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """Two labels, and they are the only thing that marks this run apart.

    A development fingerprint that could be mistaken for a benchmark result
    would let an accuracy claim be made about a model that never ran.
    """
    answer(batch, "description", description_answer())
    answer(batch, "provider", provider_answer())
    outcome = run_batch(load_batch(batch), [load_source(conn, job_id)])[0]

    with transaction(conn):
        store_outcome(conn, outcome)

    rows = conn.execute("SELECT runner, model, provider, cost_usd FROM llm_call").fetchall()
    assert len(rows) == 2
    assert {row["runner"] for row in rows} == {"COWORK_ASSISTED"}
    assert {row["model"] for row in rows} == {COWORK_MODEL_IDENTIFIER}
    assert {row["cost_usd"] for row in rows} == {0.0}


def test_importing_the_same_batch_twice_stores_one_fingerprint(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """Re-running is what happens while a prompt is being worked on."""
    answer(batch, "description", description_answer())
    answer(batch, "provider", provider_answer())
    source = load_source(conn, job_id)

    first = run_batch(load_batch(batch), [source])[0]
    second = run_batch(load_batch(batch), [source])[0]
    with transaction(conn):
        first_id = store_outcome(conn, first)
        second_id = store_outcome(conn, second)

    assert first_id == second_id
    assert conn.execute("SELECT COUNT(*) c FROM fingerprint").fetchone()["c"] == 1


# --- what the harness refuses to do -----------------------------------------


def test_unparseable_output_fails_and_is_stored_exactly_as_written(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """No repair. The raw text survives and the attempt is marked failed.

    This is the measurement the exercise exists for: an importer that tidied
    its input would report a health the production path does not have.
    """
    prose = "Sure! Here is the extraction you asked for: {not really json}"
    answer(batch, "description", prose)
    answer(batch, "provider", provider_answer())

    outcome = run_batch(load_batch(batch), [load_source(conn, job_id)])[0]

    assert not outcome.ok
    assert outcome.fingerprint is None
    assert [a.parsed_ok for a in outcome.attempts] == [False, False]

    with transaction(conn):
        store_outcome(conn, outcome)

    rows = conn.execute("SELECT raw_output, parsed_ok FROM llm_call").fetchall()
    assert {row["raw_output"] for row in rows} == {prose}
    assert conn.execute("SELECT COUNT(*) c FROM fingerprint").fetchone()["c"] == 0


def test_schema_shaped_but_wrong_output_is_a_validation_failure(
    conn: sqlite3.Connection, job_id: str, batch: Path
) -> None:
    """Parsed cleanly, and still not an extraction.

    Kept distinct from unparseable output because the two want different fixes:
    a wrapper problem versus a prompt problem, and one boolean would hide which
    is happening.
    """
    answer(batch, "description", json.dumps({"observed_title": "missing everything else"}))
    answer(batch, "provider", provider_answer())

    outcome = run_batch(load_batch(batch), [load_source(conn, job_id)])[0]

    assert not outcome.ok
    assert all(a.parsed_ok for a in outcome.attempts)
    assert not any(a.validated_ok for a in outcome.attempts)


def test_an_answer_to_a_request_this_batch_never_sent_is_refused(batch: Path) -> None:
    """A stray result is not a harmless extra file.

    It means the answer was produced against a request this batch did not
    export, and running it would attribute an extraction to a prompt that never
    asked for it.
    """
    (batch / "results" / "stray.json").write_text(
        json.dumps({"cache_key": "sha256:invented", "raw_output": "{}"}), encoding="utf-8"
    )

    with pytest.raises(CoworkBatchError, match="never exported"):
        load_batch(batch)


def test_an_answer_supplied_as_an_object_is_refused(batch: Path) -> None:
    """`raw_output` must be a string, so the real parser sees the real bytes.

    Re-serialising an object here would hide trailing prose, truncation and
    not-quite-schema-shaped answers -- the failures worth finding before any
    money is spent.
    """
    request = next((batch / "requests").glob("*-description-*.json"))
    key = json.loads(request.read_text(encoding="utf-8"))["cache_key"]
    (batch / "results" / request.name).write_text(
        json.dumps({"cache_key": key, "raw_output": {"observed_title": "an object"}}),
        encoding="utf-8",
    )

    with pytest.raises(CoworkBatchError, match="not a string"):
        load_batch(batch)


def test_a_job_answered_on_only_one_family_is_not_run(batch: Path) -> None:
    """Half an extraction is not a smaller extraction.

    Assembling from one family and an empty stand-in for the other would
    produce a fingerprint whose provider channel says nothing -- which is a
    claim, not a gap.
    """
    answer(batch, "description", description_answer())

    imported = load_batch(batch)

    assert len(imported.unanswered) == 1
    assert imported.answered_jobs() == []

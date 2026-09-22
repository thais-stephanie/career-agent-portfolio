"""Acceptance criterion 18: a round trip through the repository layer for
every M0 table, plus the behaviours that are easy to get quietly wrong.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from career_agent.clock import is_valid_id
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import (
    ClaimSource,
    ClaimType,
    CollectionStatus,
    PipelineRunStatus,
    ResponsibilityCategory,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CandidateRepo,
    ClaimRepo,
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    PipelineRunRepo,
    ProviderPayloadRepo,
    SearchProfileVersionRepo,
    SourceBoardRepo,
)


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(tmp_path / "career.db")
    migrate(connection)
    with transaction(connection):
        yield connection
    connection.close()


@pytest.fixture
def candidate_id(conn: sqlite3.Connection) -> str:
    return CandidateRepo(conn).upsert("demo_candidate", "Alex")


def _claim(**overrides: object) -> VerifiedClaim:
    defaults: dict[str, object] = {
        "claim_key": "claim_001",
        "claim_type": ClaimType.EMPLOYMENT,
        "text": "Built integrations between HubSpot, Stripe and n8n.",
        "employer": "Example Co",
        "period_start": "2022-01",
        "period_end": "2024-06",
        "source": ClaimSource.RESUME,
        "verified": True,
        "tools": ["hubspot", "stripe", "n8n"],
        "tags": [ResponsibilityCategory.SYSTEM_INTEGRATION],
    }
    defaults.update(overrides)
    return VerifiedClaim(**defaults)  # type: ignore[arg-type]


# --- candidate -------------------------------------------------------------


def test_candidate_round_trip(conn: sqlite3.Connection) -> None:
    repo = CandidateRepo(conn)
    candidate_id = repo.upsert("demo_candidate", "Alex")
    assert is_valid_id(candidate_id)

    row = repo.get("demo_candidate")
    assert row is not None
    assert row["display_name"] == "Alex"
    assert row["created_at"].endswith("Z")


def test_candidate_upsert_is_stable(conn: sqlite3.Connection) -> None:
    repo = CandidateRepo(conn)
    first = repo.upsert("demo_candidate", "Alex")
    second = repo.upsert("demo_candidate", "Alex M.")
    assert first == second
    assert repo.get("demo_candidate")["display_name"] == "Alex M."


# --- search profile versions ----------------------------------------------


def test_profile_version_round_trip(conn: sqlite3.Connection, candidate_id: str) -> None:
    repo = SearchProfileVersionRepo(conn)
    version_id = repo.record(candidate_id, "candidate_key: demo_candidate\n")
    row = repo.get(version_id)
    assert row is not None
    assert row["yaml_snapshot"] == "candidate_key: demo_candidate\n"
    assert len(row["content_hash"]) == 64


def test_identical_yaml_does_not_create_a_second_version(
    conn: sqlite3.Connection, candidate_id: str
) -> None:
    """Running the pipeline repeatedly must not litter the table."""
    repo = SearchProfileVersionRepo(conn)
    first = repo.record(candidate_id, "a: 1\n")
    second = repo.record(candidate_id, "a: 1\n")
    assert first == second


def test_edited_yaml_creates_a_new_version(conn: sqlite3.Connection, candidate_id: str) -> None:
    repo = SearchProfileVersionRepo(conn)
    first = repo.record(candidate_id, "annual_minimum: 0\n")
    second = repo.record(candidate_id, "annual_minimum: 70000\n")
    assert first != second
    assert repo.latest(candidate_id)["id"] == second


# --- verified claims -------------------------------------------------------


def test_claim_round_trip_preserves_every_field(
    conn: sqlite3.Connection, candidate_id: str
) -> None:
    repo = ClaimRepo(conn)
    original = _claim()
    repo.add(candidate_id, original)

    loaded = repo.current(candidate_id)
    assert len(loaded) == 1
    assert loaded[0] == original


def test_claim_revision_supersedes_without_deleting(
    conn: sqlite3.Connection, candidate_id: str
) -> None:
    repo = ClaimRepo(conn)
    first = _claim(verified=False)
    repo.add(candidate_id, first)

    corrected = first.next_revision(
        text="Built HubSpot, Stripe and n8n integrations.", verified=True
    )
    repo.supersede(candidate_id, corrected)

    current = repo.current(candidate_id)
    assert len(current) == 1
    assert current[0].revision == 2
    assert current[0].verified is True

    history = repo.history(candidate_id, "claim_001")
    assert [c.revision for c in history] == [1, 2]
    assert history[0].text == first.text, "the superseded revision must survive as history"


def test_claims_survive_a_profile_version_change(
    conn: sqlite3.Connection, candidate_id: str
) -> None:
    """The whole point of detaching claims from preference versions: changing a
    salary floor must not touch career facts."""
    claims = ClaimRepo(conn)
    versions = SearchProfileVersionRepo(conn)

    claims.add(candidate_id, _claim())
    versions.record(candidate_id, "annual_minimum: 0\n")
    versions.record(candidate_id, "annual_minimum: 70000\n")

    assert len(claims.current(candidate_id)) == 1
    stored = conn.execute("SELECT COUNT(*) AS n FROM verified_claim").fetchone()["n"]
    assert stored == 1, "a preference change must not duplicate claims"


# --- company and boards ----------------------------------------------------


@pytest.fixture
def company_id(conn: sqlite3.Connection) -> str:
    return CompanyRepo(conn).upsert(
        CompanyRecord(slug="example-co", name="Example Co", hq_country="CH", stage="series_b")
    )


@pytest.fixture
def board_id(conn: sqlite3.Connection, company_id: str) -> str:
    return SourceBoardRepo(conn).upsert(
        SourceBoardRecord(
            company_id=company_id, provider="greenhouse", board_identifier="exampleco"
        )
    )


def test_company_round_trip(conn: sqlite3.Connection, company_id: str) -> None:
    row = CompanyRepo(conn).get_by_slug("example-co")
    assert row is not None
    assert row["name"] == "Example Co"
    assert row["hq_country"] == "CH"
    assert is_valid_id(str(row["id"]))


def test_company_upsert_updates_in_place(conn: sqlite3.Connection, company_id: str) -> None:
    repo = CompanyRepo(conn)
    again = repo.upsert(CompanyRecord(slug="example-co", name="Example Company", hq_country="CH"))
    assert again == company_id
    assert repo.get_by_slug("example-co")["name"] == "Example Company"
    assert len(repo.all()) == 1


def test_board_round_trip_and_upsert(
    conn: sqlite3.Connection, company_id: str, board_id: str
) -> None:
    repo = SourceBoardRepo(conn)
    again = repo.upsert(
        SourceBoardRecord(
            company_id=company_id,
            provider="greenhouse",
            board_identifier="exampleco",
            board_url="https://boards.greenhouse.io/exampleco",
        )
    )
    assert again == board_id
    boards = repo.for_company(company_id)
    assert len(boards) == 1
    assert boards[0]["board_url"] == "https://boards.greenhouse.io/exampleco"


def test_board_failure_does_not_advance_last_collected_at(
    conn: sqlite3.Connection, board_id: str
) -> None:
    """The bug this guards against would mass-close a company's jobs after a
    single network blip."""
    repo = SourceBoardRepo(conn)
    repo.mark_error(board_id, "connection timed out")

    row = repo.get(board_id)
    assert row["last_error"] == "connection timed out"
    assert row["last_collected_at"] is None

    repo.mark_collected(board_id)
    row = repo.get(board_id)
    assert row["last_collected_at"].endswith("Z")
    assert row["last_error"] is None


# --- job text, jobs, payloads ----------------------------------------------


def test_job_raw_is_content_addressed(conn: sqlite3.Connection) -> None:
    repo = JobRawRepo(conn)
    text = "You will own our HubSpot instance end to end."
    first = repo.put(text)
    second = repo.put(text)
    assert first == second, "identical text must not be stored twice"

    row = repo.get(first)
    assert row["description_text"] == text
    assert row["byte_length"] == len(text.encode("utf-8"))

    stored = conn.execute("SELECT COUNT(*) AS n FROM job_raw").fetchone()["n"]
    assert stored == 1


def test_job_round_trip(conn: sqlite3.Connection, company_id: str, board_id: str) -> None:
    content_hash = JobRawRepo(conn).put("Full description text.")
    repo = JobRepo(conn)
    job_id = repo.upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id="4001",
            url="https://boards.greenhouse.io/exampleco/jobs/4001",
            title="Business Systems Analyst",
            department="Revenue Operations",
            location_raw="Remote - United States",
            content_hash=content_hash,
        )
    )
    row = repo.get(job_id)
    assert row["title"] == "Business Systems Analyst"
    assert row["location_raw"] == "Remote - United States"
    assert row["collection_status"] == CollectionStatus.DISCOVERED.value
    assert row["closed_at"] is None


def test_seeing_a_job_again_refreshes_last_seen_but_not_first_seen(
    conn: sqlite3.Connection, company_id: str, board_id: str
) -> None:
    repo = JobRepo(conn)
    record = JobRecord(
        company_id=company_id,
        source_board_id=board_id,
        provider="greenhouse",
        external_id="4001",
        url="https://example.com/1",
        title="Business Systems Analyst",
    )
    job_id = repo.upsert_seen(record)
    first_seen = repo.get(job_id)["first_seen_at"]

    again = repo.upsert_seen(record)
    assert again == job_id, "the same posting must not be duplicated"
    assert repo.get(job_id)["first_seen_at"] == first_seen

    count = conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"]
    assert count == 1


def test_status_transitions_round_trip(
    conn: sqlite3.Connection, company_id: str, board_id: str
) -> None:
    repo = JobRepo(conn)
    job_id = repo.upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id="4002",
            url="https://example.com/2",
            title="Automation Specialist",
        )
    )
    repo.set_status(job_id, CollectionStatus.PREFILTERED_OUT, prefilter_reason="excluded company")
    row = repo.get(job_id)
    assert row["collection_status"] == CollectionStatus.PREFILTERED_OUT.value
    assert row["prefilter_reason"] == "excluded company"
    assert repo.by_status(CollectionStatus.PREFILTERED_OUT)[0]["id"] == job_id


def test_provider_payload_round_trip(
    conn: sqlite3.Connection, company_id: str, board_id: str
) -> None:
    """The payload must come back byte-faithful: at M2 it is what PROVIDER_FIELD
    evidence is verified against."""
    job_id = JobRepo(conn).upsert_seen(
        JobRecord(
            company_id=company_id,
            source_board_id=board_id,
            provider="greenhouse",
            external_id="4003",
            url="https://example.com/3",
            title="GTM Systems Analyst",
        )
    )
    repo = ProviderPayloadRepo(conn)
    payload = {"location": {"name": "Remote - United States"}, "metadata": {"remote": True}}
    first = repo.put(ProviderPayloadRecord(job_id=job_id, provider="greenhouse", payload=payload))
    second = repo.put(ProviderPayloadRecord(job_id=job_id, provider="greenhouse", payload=payload))
    assert first == second, "an unchanged payload must not be archived twice"

    assert repo.latest_for_job(job_id) == payload


# --- pipeline runs ---------------------------------------------------------


def test_pipeline_run_round_trip(conn: sqlite3.Connection) -> None:
    repo = PipelineRunRepo(conn)
    run_id = repo.start("collect")
    assert repo.get(run_id)["status"] == PipelineRunStatus.RUNNING.value

    repo.finish(run_id, PipelineRunStatus.OK, stats={"jobs": 214})
    row = repo.get(run_id)
    assert row["status"] == PipelineRunStatus.OK.value
    assert row["finished_at"].endswith("Z")
    assert '"jobs": 214' in row["stats_json"]
    assert repo.recent()[0]["id"] == run_id


def test_pipeline_failure_records_the_error(conn: sqlite3.Connection) -> None:
    repo = PipelineRunRepo(conn)
    run_id = repo.start("fingerprint")
    repo.finish(run_id, PipelineRunStatus.FAILED, error="provider unreachable")
    row = repo.get(run_id)
    assert row["status"] == PipelineRunStatus.FAILED.value
    assert row["error"] == "provider unreachable"
    assert row["stats_json"] == "{}"

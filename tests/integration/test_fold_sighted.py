"""An aggregator's own row folds once its vacancy is a sighting on the
employer's posting (2026-10-16: 125 Speedrun rows duplicated open Ashby or
Greenhouse postings). Fictional companies and postings."""

from __future__ import annotations

from career_agent.domain.enums import CollectionStatus
from career_agent.pipeline.fold_sighted import fold_sighted_duplicates
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    DiscoverySourceRepo,
    JobRawRepo,
    JobRepo,
    SourceBoardRepo,
)


def _job(conn, provider: str, external: str, url: str) -> str:
    company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme (fictional)"))
    board = SourceBoardRepo(conn).upsert(
        SourceBoardRecord(company_id=company, provider=provider, board_identifier="acme")
    )
    JobRepo(conn).upsert_seen(
        JobRecord(
            company_id=company,
            source_board_id=board,
            provider=provider,
            external_id=external,
            url=url,
            title="Strategic Account Executive (fictional)",
            location_raw="Remote",
            content_hash=JobRawRepo(conn).put(f"An advert for {external}."),
        ),
        status=CollectionStatus.NORMALISED,
    )
    return str(
        conn.execute(
            "SELECT id FROM job WHERE provider = ? AND external_id = ?", (provider, external)
        ).fetchone()[0]
    )


def _open(conn) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT id FROM job WHERE closed_at IS NULL")}


def test_the_aggregators_own_row_folds_into_the_sighted_canonical_posting(tmp_path) -> None:
    conn = connect(tmp_path / "fold.db")
    migrate(conn)
    with transaction(conn):
        early = _job(conn, "speedrun", "89e1", "https://aggregator.example/jobs/89e1")
        canonical = _job(conn, "ashby", "89e1", "https://jobs.ashbyhq.com/acme/89e1")
        unrelated = _job(conn, "speedrun", "77aa", "https://aggregator.example/jobs/77aa")
        DiscoverySourceRepo(conn).record(
            job_id=canonical,
            source="speedrun",
            external_id="89e1",
            matched_by="EXTERNAL_ID",
            url="https://aggregator.example/jobs/89e1",
        )
    assert fold_sighted_duplicates(conn) == [early]
    assert _open(conn) == {canonical, unrelated}
    sighting = conn.execute(
        "SELECT job_id FROM job_discovery_source WHERE source = 'speedrun'"
    ).fetchone()[0]
    assert sighting == canonical, "the aggregator's provenance stays on the canonical posting"
    assert conn.execute("SELECT count(*) FROM job").fetchone()[0] == 3, "nothing deleted"
    assert fold_sighted_duplicates(conn) == [], "idempotent"
    conn.close()


def test_nothing_folds_into_a_closed_canonical_posting(tmp_path) -> None:
    conn = connect(tmp_path / "fold.db")
    migrate(conn)
    with transaction(conn):
        early = _job(conn, "speedrun", "89e1", "https://aggregator.example/jobs/89e1")
        canonical = _job(conn, "ashby", "89e1", "https://jobs.ashbyhq.com/acme/89e1")
        DiscoverySourceRepo(conn).record(
            job_id=canonical, source="speedrun", external_id="89e1", matched_by="EXTERNAL_ID"
        )
        conn.execute("UPDATE job SET closed_at = '2026-10-16T00:00:00Z' WHERE id = ?", (canonical,))
    assert fold_sighted_duplicates(conn) == []
    assert early in _open(conn), "the only open copy of the vacancy stays open"
    conn.close()


def test_an_origin_pointer_records_the_missing_sighting_and_folds(tmp_path) -> None:
    """A collector that already holds its own row never asks again whether the
    employer's posting arrived; the stored origin pointer does."""
    from career_agent.storage.records import ProviderPayloadRecord
    from career_agent.storage.repositories import ProviderPayloadRepo

    uuid = "11111111-2222-3333-4444-555555555555"
    conn = connect(tmp_path / "fold.db")
    migrate(conn)
    with transaction(conn):
        early = _job(conn, "speedrun", "sr-1", "https://aggregator.example/jobs/sr-1")
        canonical = _job(conn, "ashby", uuid, f"https://jobs.ashbyhq.com/acme/{uuid}")
        ProviderPayloadRepo(conn).put(
            ProviderPayloadRecord(
                job_id=early,
                provider="speedrun",
                payload={
                    "apply": {"kind": "external", "url": f"https://jobs.ashbyhq.com/acme/{uuid}"}
                },
            )
        )
    assert fold_sighted_duplicates(conn) == [early]
    row = conn.execute(
        "SELECT job_id, matched_by FROM job_discovery_source WHERE source = 'speedrun'"
    ).fetchone()
    assert (row[0], row[1]) == (canonical, "ORIGIN_URL")
    assert _open(conn) == {canonical}
    conn.close()

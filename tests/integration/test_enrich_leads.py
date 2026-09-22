"""A metadata-only lead folds into its employer's own posting, and its scope
travels with the sighting. Mock transport, temporary database."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.enrich_leads import (
    AMBIGUOUS,
    NO_CANONICAL_ORIGIN,
    RESOLVED_EXISTING_JOB,
    RESOLVED_NEW_ATS_JOB,
    LeadEnricher,
    normalise_title,
)
from career_agent.pipeline.jobgether_collect import JobgetherCollector
from career_agent.pipeline.rescore import rescore
from career_agent.providers.jobgether import Slice
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobgether"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))
LEAD = dict(PAGE["jobs"][0])  # location: Anywhere


@pytest.fixture
def conn(tmp_path: Path):
    connection = connect(tmp_path / "enrich.db")
    migrate(connection)
    yield connection
    connection.close()


def _lead_into(conn: Any, company: str, title: str) -> None:
    row = dict(LEAD, company=company, title=title)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"jobs": [row], "pagination": {"hasMore": False}})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    JobgetherCollector(conn, fetcher).collect([Slice("anywhere")])


def _canonical(conn: Any, slug: str, name: str, title: str, provider: str = "greenhouse") -> str:
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug=slug, name=name))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider=provider, board_identifier=slug)
        )
        text_hash = JobRawRepo(conn).put("We build things. Apply today.", "<p>We build things.</p>")
        return JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=provider,
                external_id=f"{provider}-{slug}-{normalise_title(title)}",
                url=f"https://boards.greenhouse.io/{slug}/jobs/1",
                title=title,
                department=None,
                location_raw="San Francisco, CA",
                posted_at=None,
                content_hash=text_hash,
            )
        )


def _offline(conn: Any) -> LeadEnricher:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    return LeadEnricher(conn, fetcher)


def test_a_lead_folds_into_its_employers_own_posting_and_keeps_its_scope(conn) -> None:
    canonical = _canonical(conn, "acme", "Acme", "Senior Backend Engineer")
    _lead_into(conn, "Acme", "senior backend engineer")
    stats = _offline(conn).enrich(probe=False)
    assert stats.outcomes[RESOLVED_EXISTING_JOB] == 1
    assert stats.affected_canonical_ids == {canonical}
    lead = conn.execute(
        "SELECT closed_at, collection_status FROM job WHERE provider='jobgether'"
    ).fetchone()
    assert lead["closed_at"] is not None and lead["collection_status"] == "CLOSED"
    sighting = conn.execute(
        "SELECT job_id, matched_by, source FROM job_discovery_source WHERE source='jobgether'"
    ).fetchone()
    assert sighting["job_id"] == canonical and sighting["matched_by"] == "EMPLOYER_TITLE"
    # The scope travels: the Greenhouse posting says nothing about hiring and
    # the sighting says Anywhere.
    config, _ = load_search_config(committed_config_dir())
    rescore(conn, config)
    status = conn.execute(
        "SELECT eligibility_status, content_completeness FROM job_match WHERE job_id = ?",
        (canonical,),
    ).fetchone()
    assert status["eligibility_status"] == "VERIFIED_ELIGIBLE"
    assert status["content_completeness"] == "FULL_CONTENT"


def test_two_twins_are_ambiguous_and_nothing_moves(conn) -> None:
    _canonical(conn, "acme", "Acme", "Account Executive")
    with transaction(conn):
        company_id = CompanyRepo(conn).get_by_slug("acme")["id"]
        board_id = SourceBoardRepo(conn).get_by_identifier("greenhouse", "acme")["id"]
        text_hash = JobRawRepo(conn).put("Sell things in Berlin.", None)
        JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="greenhouse",
                external_id="greenhouse-acme-ae-2",
                url="https://boards.greenhouse.io/acme/jobs/2",
                title="Account Executive",
                department=None,
                location_raw="Berlin",
                posted_at=None,
                content_hash=text_hash,
            )
        )
    _lead_into(conn, "Acme", "Account Executive")
    stats = _offline(conn).enrich(probe=False)
    assert stats.outcomes[AMBIGUOUS] == 1
    assert (
        conn.execute("SELECT closed_at FROM job WHERE provider='jobgether'").fetchone()[0] is None
    )


def test_a_probed_board_is_accepted_only_when_it_carries_the_title(conn) -> None:
    _lead_into(conn, "Northwind", "Data Engineer")
    board = {
        "jobs": [
            {
                "id": 1,
                "title": "Data Engineer",
                "absolute_url": "https://boards.greenhouse.io/northwind/jobs/1",
                "location": {"name": "Remote"},
                "updated_at": "2026-09-01T00:00:00Z",
                "content": "<p>Pipelines, warehouses, and the people who need them.</p>",
            }
        ]
    }
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(str(request.url))
        if "greenhouse" in request.url.host and "northwind" in request.url.path:
            return httpx.Response(200, json=board)
        return httpx.Response(404, json={"error": "not found"})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = LeadEnricher(conn, fetcher).enrich(max_companies=5)
    assert stats.boards_registered == 1
    assert stats.outcomes[RESOLVED_NEW_ATS_JOB] == 1
    row = conn.execute(
        "SELECT provider, board_identifier, discovery_method FROM source_board"
        " WHERE provider = 'greenhouse'"
    ).fetchone()
    assert row["board_identifier"] == "northwind"
    assert row["discovery_method"] == "lead_enrichment_title_match"
    canonical = conn.execute(
        "SELECT id, content_hash FROM job WHERE provider = 'greenhouse'"
    ).fetchone()
    assert canonical["content_hash"] is not None, "collected through the normal runner"


def test_a_board_that_answers_without_the_title_is_not_this_employer(conn) -> None:
    _lead_into(conn, "Northwind", "Data Engineer")
    board = {
        "jobs": [
            {
                "id": 9,
                "title": "Barista",
                "absolute_url": "https://boards.greenhouse.io/northwind/jobs/9",
                "location": {"name": "Seattle"},
                "updated_at": "2026-09-01T00:00:00Z",
                "content": "<p>Coffee.</p>",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if "greenhouse" in request.url.host and "northwind" in request.url.path:
            return httpx.Response(200, json=board)
        return httpx.Response(404, json={"error": "not found"})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    stats = LeadEnricher(conn, fetcher).enrich(max_companies=5)
    assert stats.boards_registered == 0
    assert stats.outcomes[NO_CANONICAL_ORIGIN] == 1
    assert (
        conn.execute("SELECT COUNT(*) FROM source_board WHERE provider='greenhouse'").fetchone()[0]
        == 0
    )


def test_title_normalisation_keeps_seniority() -> None:
    assert normalise_title("Senior Backend Engineer (Remote)") == "senior backend engineer remote"
    assert normalise_title("Sénior Backend") == "senior backend"
    assert normalise_title("Backend Engineer") != normalise_title("Senior Backend Engineer")

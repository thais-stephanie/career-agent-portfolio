"""The normal board runner must reach a verified Recruitee registry entry."""

import httpx

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


def test_registered_recruitee_board_is_collected_by_the_normal_runner(tmp_path):
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="example", name="Example"))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company,
                provider="recruitee",
                board_identifier="example",
                board_url="https://careers.example.org",
            )
        )
    requests = []

    def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "offers": [
                    {
                        "id": 7,
                        "title": "Office coordinator",
                        "careers_url": "https://careers.example.org/o/7",
                        "careers_apply_url": "https://careers.example.org/o/7/apply",
                        "description": "<p>Support office operations in Paris.</p>",
                    }
                ]
            },
        )

    with HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay_seconds=0
    ) as fetcher:
        stats = Collector(conn, fetcher).collect_all()
    assert requests == ["https://careers.example.org/api/offers/"]
    assert stats.boards_attempted == stats.boards_succeeded == stats.jobs_new == 1
    assert stats.descriptions_non_empty == 1
    assert conn.execute("SELECT COUNT(*) FROM job_provider_payload").fetchone()[0] == 1
    assert stats.by_board[0]["board_identifier"] == "example"
    conn.close()

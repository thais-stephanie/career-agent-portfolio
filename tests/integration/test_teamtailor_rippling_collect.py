"""The normal board runner reaches a verified Teamtailor tenant and a verified
Rippling board, and stores what the detail request adds."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
TT_FEED = (FIXTURES / "teamtailor" / "feed.xml").read_text(encoding="utf-8")
RIP_BOARD = json.loads((FIXTURES / "rippling" / "board.json").read_text(encoding="utf-8"))
RIP_DETAIL = json.loads((FIXTURES / "rippling" / "detail.json").read_text(encoding="utf-8"))


def _registry(conn, provider: str, identifier: str, url: str | None) -> None:
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug=identifier, name=identifier))
        SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company,
                provider=provider,
                board_identifier=identifier,
                board_url=url,
            )
        )


def test_a_registered_teamtailor_tenant_is_collected_by_the_normal_runner(tmp_path) -> None:
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    _registry(conn, "teamtailor", "teamtailor", "https://career.teamtailor.com")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        return httpx.Response(200, text=TT_FEED)

    with HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay_seconds=0
    ) as fetcher:
        stats = Collector(conn, fetcher).collect_all()
    assert requests == ["https://career.teamtailor.com/jobs.rss"]
    assert stats.boards_attempted == stats.boards_succeeded == 1
    assert stats.jobs_new == 3 and stats.descriptions_non_empty == 3
    row = conn.execute(
        "SELECT location_raw, posted_at, url FROM job WHERE title = 'Group Financial Controller'"
    ).fetchone()
    assert row["location_raw"] == "Stockholm, Sweden"
    assert row["posted_at"] is not None
    assert row["url"].startswith("https://career.teamtailor.com/jobs/")
    conn.close()


def test_a_registered_rippling_board_is_collected_with_its_detail(tmp_path) -> None:
    conn = connect(tmp_path / "scratch.db")
    migrate(conn)
    _registry(conn, "rippling", "rippling", "https://ats.rippling.com/rippling/jobs")
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path.endswith("/board/rippling/jobs"):
            return httpx.Response(200, json=RIP_BOARD)
        uuid = request.url.path.rsplit("/", 1)[-1]
        detail = dict(RIP_DETAIL, uuid=uuid, url=f"https://ats.rippling.com/rippling/jobs/{uuid}")
        return httpx.Response(200, json=detail)

    with HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(handler)), request_delay_seconds=0
    ) as fetcher:
        stats = Collector(conn, fetcher).collect_all()
    assert len(requests) == 1 + len(RIP_BOARD), "one list, then one detail per posting"
    assert stats.jobs_new == len(RIP_BOARD)
    assert stats.descriptions_non_empty == len(RIP_BOARD)
    rows = conn.execute(
        "SELECT location_raw, posted_at FROM job WHERE provider = 'rippling'"
    ).fetchall()
    assert all(r["posted_at"] is not None for r in rows), "the detail's createdOn reached the row"
    conn.close()

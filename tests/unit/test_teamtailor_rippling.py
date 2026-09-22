"""Two ATS families read from employer-published identities. Fixtures were
captured on 2026-09-11 from real tenants and trimmed; no socket opens here."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.facts import employment_type_from, workplace_type_from
from career_agent.providers.base import BoardRef
from career_agent.providers.registry import get_provider
from career_agent.providers.rippling import RipplingProvider, workplace_of
from career_agent.providers.rippling import read_compensation as rippling_pay
from career_agent.providers.teamtailor import TeamtailorProvider, location_of, parse_feed

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers"
TT_FEED = (FIXTURES / "teamtailor" / "feed.xml").read_text(encoding="utf-8")
RIP_BOARD = json.loads((FIXTURES / "rippling" / "board.json").read_text(encoding="utf-8"))
RIP_DETAIL = json.loads((FIXTURES / "rippling" / "detail.json").read_text(encoding="utf-8"))


def _fetcher(handler) -> HttpFetcher:
    return HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))


# -- Teamtailor -------------------------------------------------------------------


def test_teamtailor_reads_the_tenant_feed_and_only_the_tenant_feed() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text=TT_FEED, headers={"content-type": "application/rss+xml"})

    with _fetcher(handler) as fetcher:
        provider = TeamtailorProvider(fetcher)
        board = BoardRef("teamtailor", "teamtailor", "teamtailor", "https://career.teamtailor.com")
        stubs = list(provider.list_postings(board))
        posting = provider.fetch_posting(board, stubs[0])
    assert seen == ["https://career.teamtailor.com/jobs.rss"]
    assert len(stubs) == 3
    assert stubs[0].url.startswith("https://career.teamtailor.com/jobs/")
    assert stubs[0].location_raw == "Stockholm, Sweden"
    assert stubs[0].department == "Finance"
    assert stubs[0].posted_at == "2026-07-24T11:57:16+00:00"
    assert posting.description_text and "Teamtailor" in posting.description_text
    assert provider.identifier_is_guessable is True


def test_teamtailor_remote_status_is_the_work_model_and_none_is_nothing() -> None:
    rows = parse_feed(TT_FEED, "career.teamtailor.com")
    statuses = {row["title"]: row["remoteStatus"] for row in rows}
    assert statuses["Mid-Market Account Executive (Outbound) - Madrid"] == "fully"
    with _fetcher(lambda r: httpx.Response(200, text=TT_FEED)) as fetcher:
        board = BoardRef("teamtailor", "teamtailor", "teamtailor", "https://career.teamtailor.com")
        stubs = {s.title: s for s in TeamtailorProvider(fetcher).list_postings(board)}
    assert (
        workplace_type_from("teamtailor", stubs["Group Financial Controller"].payload) == "HYBRID"
    )
    remote = stubs["Mid-Market Account Executive (Outbound) - Madrid"]
    assert workplace_type_from("teamtailor", remote.payload) == "REMOTE"
    assert TeamtailorProvider.capabilities.publishes_hiring_scope is False


def test_teamtailor_default_origin_is_the_vendor_subdomain_and_a_custom_origin_is_explicit() -> (
    None
):
    with _fetcher(lambda r: httpx.Response(200, text=TT_FEED)) as fetcher:
        provider = TeamtailorProvider(fetcher)
        assert provider.feed_url(BoardRef("payfit", "teamtailor", "payfit")) == (
            "https://payfit.teamtailor.com/jobs.rss"
        )
        assert provider.validate_board(
            BoardRef("x", "teamtailor", "x", "https://jobs.example.com/careers")
        )
        assert provider.validate_board(BoardRef("x", "teamtailor", "Not A Slug"))


def test_teamtailor_a_non_rss_document_is_a_failure_not_an_empty_board() -> None:
    with (
        _fetcher(lambda r: httpx.Response(200, text="<html>nope</html>")) as fetcher,
        pytest.raises(FetchError),
    ):
        list(TeamtailorProvider(fetcher).list_postings(BoardRef("x", "teamtailor", "x")))


def test_teamtailor_location_is_an_office_list() -> None:
    row = {
        "locations": [
            {"city": "Stockholm", "country": "Sweden"},
            {"city": None, "country": "Spain"},
        ]
    }
    assert location_of(row) == "Stockholm, Sweden | Spain"
    assert location_of({"locations": []}) is None


# -- Rippling ---------------------------------------------------------------------


def _rippling_handler(seen: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path.endswith("/jobs"):
            return httpx.Response(200, json=RIP_BOARD)
        if request.url.path.endswith(RIP_DETAIL["uuid"]):
            return httpx.Response(200, json=RIP_DETAIL)
        return httpx.Response(404, json={"error_code": "RESOURCE_NOT_FOUND"})

    return handler


def test_rippling_lists_from_the_board_api_and_fetches_one_detail_per_posting() -> None:
    seen: list[str] = []
    with _fetcher(_rippling_handler(seen)) as fetcher:
        provider = RipplingProvider(fetcher)
        board = BoardRef("rippling", "rippling", "rippling")
        stubs = list(provider.list_postings(board))
        target = next(s for s in stubs if s.external_id.endswith(RIP_DETAIL["uuid"]))
        posting = provider.fetch_posting(board, target)
    assert seen[0] == "https://api.rippling.com/platform/api/ats/v1/board/rippling/jobs"
    assert seen[1].endswith("/board/rippling/jobs/" + RIP_DETAIL["uuid"])
    assert len(stubs) == 3
    assert all(s.url.startswith("https://ats.rippling.com/rippling/jobs/") for s in stubs)
    assert posting.description_text and "[fixture trimmed" in posting.description_text
    assert posting.stub.posted_at == "2026-08-13T15:54:48+00:00"
    assert posting.stub.location_raw == "Remote (United States)"
    assert employment_type_from("rippling", posting.payload) == "FULL_TIME"
    pay = rippling_pay(posting.payload)
    assert pay is not None and pay.currency == "USD" and pay.period == "yearly"
    assert provider.board_url(board) == "https://ats.rippling.com/rippling/jobs"


def test_rippling_reads_the_work_model_from_the_label_prefix() -> None:
    assert workplace_of("Remote (United States)") == "remote"
    assert workplace_of("Hybrid (San Francisco, California, US)") == "hybrid"
    assert workplace_of("Bangalore, India") == "onsite"
    with _fetcher(_rippling_handler([])) as fetcher:
        stubs = {
            s.location_raw: s
            for s in RipplingProvider(fetcher).list_postings(
                BoardRef("rippling", "rippling", "rippling")
            )
        }
    assert workplace_type_from("rippling", stubs["Remote (United States)"].payload) == "REMOTE"
    assert workplace_type_from("rippling", stubs["Bangalore, India"].payload) == "ONSITE"
    assert RipplingProvider.capabilities.publishes_hiring_scope is False


def test_rippling_a_missing_board_is_a_failure_not_an_empty_board() -> None:
    with (
        _fetcher(lambda r: httpx.Response(404, json={"error_code": "RESOURCE_NOT_FOUND"})) as f,
        pytest.raises(FetchError),
    ):
        list(RipplingProvider(f).list_postings(BoardRef("nope", "rippling", "nope")))


def test_rippling_refuses_a_row_whose_url_is_not_the_boards() -> None:
    rows = [dict(RIP_BOARD[0], url="https://example.com/jobs/x")]
    with _fetcher(lambda r: httpx.Response(200, json=rows)) as fetcher:
        provider = RipplingProvider(fetcher)
        assert list(provider.list_postings(BoardRef("rippling", "rippling", "rippling"))) == []
        assert provider.postings_skipped == 1


def test_both_families_are_registered_as_ats() -> None:
    with _fetcher(lambda r: httpx.Response(200)) as fetcher:
        for name, cls in (("teamtailor", TeamtailorProvider), ("rippling", RipplingProvider)):
            adapter = get_provider(name, fetcher)
            assert isinstance(adapter, cls)
            assert adapter.addresses_boards_by_company is True

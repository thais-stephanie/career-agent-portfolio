"""Invented tenant fixtures; no inference from a vendor wildcard host."""

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.facts import employment_type_from, workplace_type_from
from career_agent.providers.base import BoardRef
from career_agent.providers.recruitee import RecruiteeProvider, read_compensation


def test_offers_keep_requirements_custom_destination_and_salary():
    body = {
        "offers": [
            {
                "id": 7,
                "title": "Office coordinator",
                "careers_url": "https://careers.example.org/o/7",
                "description": "<p>Support the office.</p>",
                "requirements": "<p>French required.</p>",
                "city": "Paris",
                "country": "France",
                "hybrid": True,
                "employment_type_code": "fulltime_permanent",
                "salary": {"min": "42000", "max": None, "currency": "EUR", "period": "year"},
            }
        ]
    }
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json=body)

    with HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler))) as fetcher:
        provider = RecruiteeProvider(fetcher)
        board = BoardRef("example", "recruitee", "example", "https://careers.example.org")
        stub = list(provider.list_postings(board))[0]
        posting = provider.fetch_posting(board, stub)
    assert seen == ["https://careers.example.org/api/offers/"]
    assert "French required." in posting.description_text
    assert stub.url == "https://careers.example.org/o/7"
    assert workplace_type_from("recruitee", stub.payload) == "HYBRID"
    assert employment_type_from("recruitee", stub.payload) == "FULL_TIME"
    pay = read_compensation(stub.payload)
    assert pay.min_value == 42000 and pay.max_value is None and pay.period == "yearly"
    assert provider.identifier_is_guessable is False


@pytest.mark.parametrize("payload", [{}, {"offers": None}, {"offers": "bad"}])
def test_malformed_offers_are_failure_not_empty(payload):
    with (
        HttpFetcher(
            client=httpx.Client(
                transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
            )
        ) as f,
        pytest.raises(FetchError),
    ):
        list(RecruiteeProvider(f).list_postings(BoardRef("example", "recruitee", "example")))


def test_legitimate_empty_and_invalid_identifier():
    with HttpFetcher(
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"offers": []}))
        )
    ) as f:
        provider = RecruiteeProvider(f)
        assert list(provider.list_postings(BoardRef("example", "recruitee", "example"))) == []
        assert provider.validate_board(BoardRef("example", "recruitee", "../admin"))

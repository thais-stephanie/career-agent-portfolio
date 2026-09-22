"""Remotive: a documented hiring-scope field, a grant with a cadence, one host.

The fixture is CAPTURED from the live response of 2026-09-11 with bodies
trimmed, which the first-party grant permits. Nothing here opens a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.registry import get_provider
from career_agent.providers.remotive import (
    ATTRIBUTION,
    MIN_SECONDS_BETWEEN_POLLS,
    RemotiveError,
    RemotiveProvider,
    canonical_url,
    company_of,
    external_id,
    hiring_scope,
    next_allowed_at,
    published_at,
    read_compensation,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "remotive"
WINDOW = json.loads((FIXTURES / "window.json").read_text(encoding="utf-8"))


def _row(index: int = 0) -> dict[str, Any]:
    return dict(WINDOW["jobs"][index])


def provider(body: Any = None, seen: list[httpx.Request] | None = None) -> RemotiveProvider:
    payload = WINDOW if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return RemotiveProvider(fetcher)


# -- the hiring scope ---------------------------------------------------------


def test_it_publishes_a_hiring_scope_and_says_so() -> None:
    assert RemotiveProvider.capabilities.publishes_hiring_scope is True


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, "Worldwide"), (1, "LATAM, Europe, USA, Canada, APAC"), (2, "Europe"), (3, "USA")],
)
def test_the_restriction_reaches_the_stub_as_the_location(index: int, expected: str) -> None:
    """Including `Worldwide`: here it is the documented restriction "if any",
    stated beside `Europe` and `USA` in the same window, not a vendor default."""
    stub = to_stub(_row(index))
    assert stub is not None
    assert stub.location_raw == expected


def test_an_empty_restriction_is_nothing_stated() -> None:
    row = _row(0)
    row["candidate_required_location"] = "  "
    assert hiring_scope(row) is None


# -- the licence, in code ----------------------------------------------------


def test_the_apply_target_is_always_the_feeds_own_url() -> None:
    stub = to_stub(_row(0))
    assert stub is not None
    assert stub.url.startswith("https://remotive.com/remote-jobs/")


def test_the_attribution_the_interface_owes_is_a_constant() -> None:
    assert ATTRIBUTION == "Remotive"


def test_four_a_day_is_arithmetic_rather_than_a_comment() -> None:
    assert MIN_SECONDS_BETWEEN_POLLS == 6 * 3600
    assert next_allowed_at(None) == 0.0
    assert next_allowed_at(1_000.0) == 1_000.0 + MIN_SECONDS_BETWEEN_POLLS


def test_it_makes_exactly_one_request_with_no_filter_and_no_limit() -> None:
    """`category`, `search` and `company_name` are questions somebody chose,
    and ADR-0019 keeps a candidate's vocabulary out of ingestion. `limit` was
    measured ignored. So: the bare path, once."""
    seen: list[httpx.Request] = []
    read = provider(seen=seen).read_window()
    assert len(seen) == 1
    assert seen[0].url.path == "/api/remote-jobs"
    assert seen[0].url.query == b""
    assert len(read.jobs) == 4
    assert read.claimed_count == 4 and read.claimed_total == 4


@pytest.mark.parametrize(
    "url",
    [
        "http://remotive.com/remote-jobs/x-1",
        "https://remotive.com.evil.example/remote-jobs/x-1",
        "javascript:alert(1)",
        "https://jobicy.com/jobs/1",
    ],
)
def test_a_hostile_url_is_refused_before_it_is_rendered(url: str) -> None:
    row = _row(0)
    row["url"] = url
    assert canonical_url(row) is None
    assert to_stub(row) is None


def test_the_adapter_talks_to_one_host_only() -> None:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    with pytest.raises(RemotiveError):
        RemotiveProvider(fetcher, api_host="https://example.com").feed_url()


def test_it_cannot_be_asked_about_one_employer() -> None:
    from career_agent.providers.base import BoardRef

    assert RemotiveProvider.addresses_boards_by_company is False
    refusal = provider().validate_board(
        BoardRef(company_slug="acme", provider="remotive", board_identifier="acme")
    )
    assert refusal is not None and "collect-remotive" in refusal


# -- the row --------------------------------------------------------------------


def test_the_id_is_the_vendors_and_is_a_string() -> None:
    assert external_id(_row(0)) == "2086540"
    assert external_id({"id": True}) is None
    assert external_id({"id": "abc"}) is None


def test_the_publication_date_is_read_as_utc_and_said_so() -> None:
    assert published_at(_row(0)) == "2026-09-08T21:47:54Z"
    assert published_at({"publication_date": "2026-01-01T10:00:00+02:00"}) == "2026-01-01T08:00:00Z"
    assert published_at({"publication_date": "yesterday"}) is None


def test_the_employer_is_read_rather_than_guessed() -> None:
    assert company_of(_row(0)) == "Credit Wellness, LLC"


def test_the_whole_advert_arrives_in_the_list_response() -> None:
    assert RemotiveProvider.capabilities.full_description_in_list is True
    stub = to_stub(_row(0))
    assert stub is not None and stub.description_html and "<p" in stub.description_html


def test_a_row_with_no_body_is_counted_rather_than_invented() -> None:
    row = _row(0)
    row["description"] = ""
    assert to_stub(row) is None
    body = dict(WINDOW)
    body["jobs"] = [row]
    assert provider(body=body).read_window().unaddressable == 1


def test_a_contract_error_is_not_an_empty_window() -> None:
    with pytest.raises(RemotiveError):
        provider(body={"jobs": "nope"}).read_window()
    with pytest.raises(RemotiveError):
        provider(body=[]).read_window()


# -- pay, read from a string the vendor composed ------------------------------------


def test_a_dollar_band_is_read_as_a_band() -> None:
    hint = read_compensation({"salary": "$60,000 - $80,000"})
    assert hint is not None
    assert hint.currency == "USD"
    assert hint.min_value == 60000 and hint.max_value == 80000


def test_an_hourly_rate_keeps_its_period() -> None:
    hint = read_compensation(_row(3))
    assert hint is not None
    assert hint.currency == "USD" and hint.period == "HOUR"


def test_a_figure_with_no_currency_yields_nothing() -> None:
    assert read_compensation({"salary": "60k - 80k"}) is None
    assert read_compensation({"salary": "Competitive"}) is None
    assert read_compensation({"salary": ""}) is None


# -- registered ----------------------------------------------------------------


def test_it_is_registered() -> None:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    assert isinstance(get_provider("remotive", fetcher), RemotiveProvider)

"""Jobgether: a specific grant, geographic questions only, metadata only.

The fixture rows are CAPTURED from the API the vendor publishes for agents on
2026-09-11; they carry no advert because the API carries none. Nothing here
opens a socket.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.jobgether import (
    CONTRACT_TYPES,
    EXPERIENCE_BANDS,
    LOCATION_SLUGS,
    MAX_LIMIT,
    MAX_PAGE,
    JobgetherError,
    JobgetherProvider,
    Slice,
    default_slices,
    external_id,
    hiring_scope,
    read_compensation,
    to_stub,
)
from career_agent.providers.registry import get_provider, publishes_hiring_scope
from career_agent.sources.scheduling import slice_order

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobgether"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))


def _row(index: int = 0) -> dict[str, Any]:
    return dict(PAGE["jobs"][index])


def provider(body: Any = None, seen: list[httpx.Request] | None = None) -> JobgetherProvider:
    payload = PAGE if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return JobgetherProvider(fetcher)


# -- the grant, in code --------------------------------------------------------


def test_it_asks_the_explicitly_allowed_alias_with_a_query_string() -> None:
    url = provider().page_url(Slice("brazil"), 1)
    assert url.startswith("https://jobgether.com/astroapi/ai/jobs.json?")
    assert "/api/v1/jobs" not in url


def test_no_request_ever_carries_a_keyword_or_an_occupational_taxonomy() -> None:
    """ADR-0019: the candidate's vocabulary never becomes a collection query."""
    seen: list[httpx.Request] = []
    provider(seen=seen).read_page(Slice("anywhere", "full-time", "senior-5-10-years"), 2)
    query = seen[0].url.params
    assert set(query.keys()) == {"locations", "contractType", "experience", "sort", "limit", "page"}
    assert "keyword" not in query and "jobReferences" not in query
    assert query["sort"] == "date" and query["limit"] == str(MAX_LIMIT)


def test_fetch_posting_never_opens_the_offer_page() -> None:
    """The page carries the advert and is closed by the terms."""
    seen: list[httpx.Request] = []
    stub = to_stub(_row(0))
    assert stub is not None
    posting = provider(seen=seen).fetch_posting(None, stub)  # type: ignore[arg-type]
    assert seen == []
    assert posting.description_text == "" and posting.description_html == ""


def test_the_caps_are_the_documented_ones_and_are_not_probed() -> None:
    assert MAX_PAGE == 10 and MAX_LIMIT == 25
    with pytest.raises(JobgetherError):
        provider().page_url(Slice("anywhere"), MAX_PAGE + 1)


# -- slices are geography and the vendor's closed lists --------------------------


def test_a_slice_refuses_anything_that_is_not_a_documented_value() -> None:
    with pytest.raises(JobgetherError):
        Slice("anywhere", contract_type="senior")
    with pytest.raises(JobgetherError):
        Slice("anywhere", experience="frontend")
    with pytest.raises(JobgetherError):
        Slice("hubspot developer")


def test_the_default_plan_is_every_location_cut_by_the_vendors_lists() -> None:
    plan = default_slices(("anywhere", "brazil"))
    assert len(plan) == 2 * len(CONTRACT_TYPES) * len(EXPERIENCE_BANDS)
    assert plan[0].location == "anywhere"
    assert {s.location for s in plan} == {"anywhere", "brazil"}


def test_scheduling_reorders_and_never_changes_the_set() -> None:
    ordered = slice_order(LOCATION_SLUGS, countries=("BR",))
    assert set(ordered) == set(LOCATION_SLUGS) and len(ordered) == len(LOCATION_SLUGS)
    assert ordered[:4] == ("anywhere", "south-america", "latam", "brazil")
    us = slice_order(LOCATION_SLUGS, countries=("US",))
    assert us[:3] == ("anywhere", "north-america", "united-states")
    assert set(us) == set(LOCATION_SLUGS)
    unknown = slice_order(LOCATION_SLUGS, countries=("XX",))
    assert unknown == tuple(LOCATION_SLUGS)


# -- the row ---------------------------------------------------------------------


def test_the_hiring_scope_is_the_location_field_and_anywhere_is_a_scope_here() -> None:
    assert publishes_hiring_scope("jobgether") is True
    assert hiring_scope(_row(0)) == "Anywhere"
    stub = to_stub(_row(0))
    assert stub is not None and stub.location_raw == "Anywhere"


@pytest.mark.parametrize("index", range(len(PAGE["jobs"])))
def test_the_id_is_the_one_in_the_url_not_the_list_rows_id(index: int) -> None:
    row = _row(index)
    identity = external_id(row)
    assert identity is not None and len(identity) == 24
    assert identity in row["url"]


def test_a_row_whose_url_is_not_jobgethers_is_not_addressed() -> None:
    row = _row(0)
    row["url"] = "https://example.com/offer/6aa3137cb563ea142e935877-x"
    assert to_stub(row) is None


def test_a_page_without_jobs_is_a_refusal_with_its_code() -> None:
    problem = {"code": "invalid_parameter", "detail": "Invalid value for 'locations'"}
    with pytest.raises(JobgetherError, match="invalid_parameter"):
        provider(body=problem).read_page(Slice("anywhere"), 1)


def test_a_salary_range_needs_a_currency_and_keeps_an_unknown_period() -> None:
    hint = read_compensation({"salaryRange": "60000-80000 EUR"})
    assert hint is not None
    assert (hint.min_value, hint.max_value, hint.currency, hint.period) == (
        60000,
        80000,
        "EUR",
        None,
    )
    assert read_compensation({"salaryRange": "60000-80000"}) is None
    assert read_compensation({"salaryRange": "Competitive"}) is None


def test_it_is_registered_as_metadata_only() -> None:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    adapter = get_provider("jobgether", fetcher)
    assert isinstance(adapter, JobgetherProvider)
    assert adapter.capabilities.obtains_full_description is False

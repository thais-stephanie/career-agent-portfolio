"""4 Day Week: a documented API with one condition, and a remote-allowed list
that is a hiring scope. Fixture captured 2026-09-11 and trimmed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.pipeline.facts import employment_type_from, workplace_type_from
from career_agent.providers.fourdayweek import (
    ATTRIBUTION,
    PER_PAGE,
    FourDayWeekError,
    FourDayWeekProvider,
    hiring_scope,
    offices,
    read_compensation,
    to_stub,
)
from career_agent.providers.registry import get_provider, publishes_hiring_scope

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "fourdayweek"
PAGE = json.loads((FIXTURES / "page.json").read_text(encoding="utf-8"))


def _row(index: int = 0) -> dict[str, Any]:
    return dict(PAGE["data"][index])


def provider(
    pages: list[dict[str, Any]] | None = None, seen: list[httpx.Request] | None = None, **kw
):
    served = pages or [PAGE]

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        page = int(request.url.params.get("page", "1"))
        return httpx.Response(200, json=served[min(page, len(served)) - 1])

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return FourDayWeekProvider(fetcher, **kw)


def test_the_remote_allowed_list_is_the_scope_and_offices_are_not() -> None:
    assert publishes_hiring_scope("fourdayweek") is True
    onsite, remote = _row(0), _row(1)
    assert hiring_scope(onsite) is None
    assert offices(onsite).startswith("London, United Kingdom")
    assert hiring_scope(remote) == "Luxembourg"
    remote_stub = to_stub(remote)
    assert remote_stub is not None and remote_stub.location_raw == "Luxembourg"
    onsite_stub = to_stub(onsite)
    assert onsite_stub is not None and onsite_stub.location_raw == offices(onsite)


def test_a_remote_role_that_names_no_place_states_nothing() -> None:
    row = _row(1)
    row["work_arrangement"] = "remote"
    row["locations"] = [{"city": "Paris", "country": "France", "work_arrangement": "onsite"}]
    stub = to_stub(row)
    assert stub is not None
    assert stub.location_raw is None, "an office is not the scope of a remote role"
    assert stub.payload["locations"], "and the office is kept in the payload"


def test_the_split_shape_the_openapi_documents_is_read_too() -> None:
    row = _row(1)
    row.pop("locations", None)
    row["office_locations"] = [{"city": "Berlin", "country": "Germany"}]
    row["remote_allowed"] = [{"country": "Brazil"}, {"continent": "Europe"}]
    assert hiring_scope(row) == "Brazil, Europe"
    assert offices(row) == "Berlin, Germany"


def test_work_model_and_contract_type_resolve() -> None:
    assert workplace_type_from("fourdayweek", to_stub(_row(0)).payload) == "ONSITE"  # type: ignore[union-attr]
    assert workplace_type_from("fourdayweek", to_stub(_row(1)).payload) == "REMOTE"  # type: ignore[union-attr]
    assert employment_type_from("fourdayweek", to_stub(_row(0)).payload) == "FULL_TIME"  # type: ignore[union-attr]


def test_salary_is_in_the_smallest_unit_and_needs_a_currency() -> None:
    hint = read_compensation(_row(2))
    assert hint is not None
    assert hint.min_value == 123036.0 and hint.currency == "USD" and hint.period == "YEAR"
    assert read_compensation({"salary_min": 100, "salary_max": 200}) is None
    assert read_compensation({"salary_min": 0, "salary_currency": "USD"}) is None


def test_the_walk_is_bounded_and_says_so() -> None:
    seen: list[httpx.Request] = []
    read = provider(seen=seen, max_pages=2).read_feed()
    assert len(seen) == 2
    assert read.pages == 2 and read.stopped_early is True
    assert read.claimed_total == 23690
    assert seen[0].url.params["limit"] == str(PER_PAGE)
    assert "q" not in seen[0].url.params and "category" not in seen[0].url.params


def test_the_end_of_the_feed_is_not_the_budget() -> None:
    last = dict(PAGE, has_more=False)
    read = provider(pages=[last], max_pages=5).read_feed()
    assert read.pages == 1 and read.stopped_early is False


def test_a_contract_error_is_not_an_empty_feed() -> None:
    with pytest.raises(FourDayWeekError):
        provider(pages=[{"jobs": []}]).read_feed()


def test_the_apply_target_is_the_vendors_page_and_the_credit_is_a_constant() -> None:
    stub = to_stub(_row(0))
    assert stub is not None and stub.url.startswith("https://4dayweek.io/job/")
    assert ATTRIBUTION == "4 Day Week"
    row = _row(0)
    row["url"] = "https://example.com/job/x"
    assert to_stub(row) is None


def test_it_is_registered_with_full_bodies() -> None:
    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    )
    adapter = get_provider("fourdayweek", fetcher)
    assert isinstance(adapter, FourDayWeekProvider)
    assert adapter.capabilities.obtains_full_description is True
    posting = adapter.fetch_posting(None, to_stub(_row(0)))  # type: ignore[arg-type]
    assert "[fixture trimmed" in posting.description_text

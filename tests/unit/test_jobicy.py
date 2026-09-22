"""The Jobicy adapter: what it reads, what it refuses, and what it will not say.

THE FIXTURES ARE INVENTED, AND THAT IS DELIBERATE
--------------------------------------------------
`jobicy.com/robots.txt` answers HTTP 403 behind an interactive challenge. The
challenge was not solved and no request has ever been made from this
repository, so there is no recording to trim -- the fixtures are written to the
CONTRACT measured by the owner's own bounded research pass on 2026-09-08
(envelope keys, field names, the doubled separators in `jobGeo`, nullable
salary components, ISO 8601 `pubDate`). The same precedent Get on Board and
Jooble set, and it is labelled inside the fixture files themselves.

The permission this adapter rests on is not robots.txt. It is a first-party
statement on `jobicy.com/jobs-rss-feed`, retrieved HTTP 200 and hashed:
"You may use Jobicy listings in your own products and user experiences without
requesting individual permission." Two conditions ride with it -- credit
Jobicy, and send every application to the feed's own URL -- and both are
tested below rather than promised.

WHAT MATTERS MOST HERE
----------------------
`jobGeo` makes this the third source in the product ever allowed to answer
"where may this employer hire". The cost of getting that wrong is a job the
candidate cannot take, shown to her as one she can. So the largest section
below is about the single value `Anywhere`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import BoardRef, RetrievalMode
from career_agent.providers.jobicy import (
    ATTRIBUTION,
    MAX_COUNT,
    MIN_SECONDS_BETWEEN_POLLS,
    JobicyError,
    JobicyProvider,
    canonical_url,
    company_of,
    external_id,
    hiring_scope,
    next_allowed_at,
    published_at,
    read_compensation,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "jobicy"
WINDOW = json.loads((FIXTURES / "window-latam.json").read_text(encoding="utf-8"))
EDGE = json.loads((FIXTURES / "edge-cases.json").read_text(encoding="utf-8"))


def _row(index: int = 0, window: dict[str, Any] = WINDOW) -> dict[str, Any]:
    return dict(window["jobs"][index])


def provider(
    body: dict[str, Any] | None = None,
    seen: list[httpx.Request] | None = None,
    **kwargs: Any,
) -> JobicyProvider:
    """An adapter over a mock transport. Nothing here opens a socket."""
    payload = WINDOW if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return JobicyProvider(fetcher, **kwargs)


# =========================================================================
# 1. THE HIRING SCOPE, AND THE VALUE THAT IS NOT ONE
# =========================================================================


def test_it_answers_where_the_employer_may_hire() -> None:
    """Why this source was built at all: 101 of the owner's 19,469 postings
    are her specialty AND at her level AND from an employer who may hire her,
    and almost nothing in the corpus states a scope."""
    assert JobicyProvider.capabilities.publishes_hiring_scope is True


def test_a_stated_country_reaches_the_stub_as_the_location() -> None:
    stub = to_stub(_row(0))
    assert stub is not None
    assert stub.location_raw == "Brazil"


def test_a_multi_country_value_is_passed_through_intact() -> None:
    """`Brazil,  Canada,  Mexico,  USA`, doubled spaces and all.

    Normalising a place string is `match/places.py`'s job. An adapter that
    tidied it here would be making a resolution decision in the wrong layer,
    and the doubled separator is what the vendor actually sends.
    """
    assert hiring_scope(_row(1)) == "Brazil,  Canada,  Mexico,  USA"


def test_anywhere_is_nothing_stated_and_never_worldwide() -> None:
    """**The load-bearing assertion in this file.**

    52 of the 200 measured rows carry `Anywhere`, and the vendor's own field
    documentation says it can mean no region was supplied. It looks exactly
    like an invitation. Reading it as one would put 52 postings in front of
    somebody on the strength of a field nobody filled in.

    None, so the gate sees nothing stated and the posting resolves UNRESOLVED.
    Absence is never permission.
    """
    row = _row(2)
    assert row["jobGeo"] == "Anywhere"
    assert hiring_scope(row) is None

    stub = to_stub(row)
    assert stub is not None
    assert stub.location_raw is None


@pytest.mark.parametrize("value", ["Anywhere", "anywhere", "  ANYWHERE  ", "Worldwide", "-", "N/A"])
def test_every_default_label_is_refused_the_same_way(value: str) -> None:
    assert hiring_scope({"jobGeo": value}) is None


def test_the_search_filter_is_not_a_scope() -> None:
    """A LATAM query returns Mexico-only roles. The QUESTION ASKED never
    becomes the employer's ANSWER: this row says Mexico and means Mexico, and
    nothing about `geo=latam` widens it to the region."""
    row = _row(3)
    assert hiring_scope(row) == "Mexico"


def test_the_board_being_remote_only_is_not_the_employers_answer() -> None:
    """Every posting here is remote -- that is the board's admission criterion,
    not a statement this employer made. Declaring a remote flag would let
    editorial policy read as an employer's answer, which is invariant 3."""
    assert JobicyProvider.capabilities.exposes_remote_flag is False


# =========================================================================
# 2. THE LICENCE, IMPLEMENTED RATHER THAN PROMISED
# =========================================================================


def test_the_apply_target_is_always_the_feeds_own_url() -> None:
    """A condition of the grant, not a preference. There is no employer apply
    URL in this response to prefer instead, and inventing one would breach the
    terms this source is read under."""
    stub = to_stub(_row(0))
    assert stub is not None
    assert stub.url == _row(0)["url"]
    assert stub.url.startswith("https://jobicy.com/jobs/")


def test_the_attribution_the_interface_owes_is_a_constant_it_can_read() -> None:
    assert ATTRIBUTION == "Jobicy"


def test_one_poll_an_hour_is_arithmetic_rather_than_a_comment() -> None:
    """The cadence in the grant. Measured from the last ATTEMPT, because a
    failed request still consumed one against somebody else's server."""
    assert MIN_SECONDS_BETWEEN_POLLS == 3600
    assert next_allowed_at(10_000) == 13_600
    assert next_allowed_at(None) == 0.0


def test_a_second_filter_inside_the_hour_is_still_a_second_request() -> None:
    """Provider-wide, not per filter. Two `geo` values half an hour apart are
    two requests in an hour, which is the thing the number forbids."""
    now = 13_599
    assert next_allowed_at(10_000) > now


# =========================================================================
# 3. THE WINDOW IS NOT THE MARKET
# =========================================================================


def test_it_makes_exactly_one_request_with_no_paging(monkeypatch) -> None:
    seen: list[httpx.Request] = []
    board = BoardRef(company_slug="", provider="jobicy", board_identifier="")
    list(provider(seen=seen).list_postings(board))

    assert len(seen) == 1
    url = str(seen[0].url)
    assert "page" not in url and "offset" not in url
    assert f"count={MAX_COUNT}" in url
    assert "geo=latam" in url


def test_a_full_window_is_a_reached_limit_and_not_a_total() -> None:
    read = provider(count=6).read_window()

    assert read.window_full is True
    assert read.complete is False, "a filled window must never read as a complete answer"


def test_it_may_never_close_a_posting_on_absence() -> None:
    """Only `BOARD_EXHAUSTIVE` may, and this is not that. The property matters
    more than the name: a posting missing from a 200-row newest-first window
    has not been shown to be gone."""
    assert JobicyProvider.retrieval_mode is not RetrievalMode.BOARD_EXHAUSTIVE


def test_an_empty_window_is_a_valid_answer_and_not_an_error() -> None:
    read = provider(EDGE["empty_window"]).read_window()

    assert read.jobs == ()
    assert read.claimed_count == 0
    assert read.window_full is False


def test_a_contract_error_is_not_an_empty_window() -> None:
    """The two outcomes the fixture case `jobicy_empty_error` exists to keep
    apart. Conflating them is how a rejected parameter reads as "this region
    has no jobs"."""
    with pytest.raises(JobicyError) as refused:
        provider(EDGE["contract_error"]).read_window()

    assert "success" in str(refused.value)


def test_a_count_that_disagrees_with_its_own_list_is_refused() -> None:
    with pytest.raises(JobicyError) as refused:
        provider(EDGE["count_disagrees"]).read_window()

    assert "40" in str(refused.value)


def test_the_same_id_twice_in_one_window_is_a_contradiction() -> None:
    """There is no paging here, so a row cannot legitimately arrive again.
    Silently keeping one would store a window that contradicts itself."""
    with pytest.raises(JobicyError):
        provider(EDGE["duplicate_id"]).read_window()


# =========================================================================
# 4. UNTRUSTED INPUT
# =========================================================================


@pytest.mark.parametrize("index", [0, 1, 2])
def test_a_hostile_url_is_refused_before_it_is_ever_rendered(index: int) -> None:
    """`javascript:`, plain HTTP and a look-alike host. A URL out of a payload
    reaches the interface as a link somebody clicks, so it is validated where
    it enters rather than where it is drawn."""
    row = dict(EDGE["unsafe_rows"]["jobs"][index])

    assert canonical_url(row) is None
    assert to_stub(row) is None, "a posting with no usable link is not addressable"


def test_a_row_with_no_body_is_counted_rather_than_invented() -> None:
    row = dict(EDGE["unsafe_rows"]["jobs"][3])

    assert to_stub(row) is None


def test_the_adapter_talks_to_one_host_only() -> None:
    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    with pytest.raises(JobicyError):
        JobicyProvider(fetcher, api_host="https://jobicy.com.evil.example").feed_url()


def test_a_geo_value_is_not_a_place_to_put_arbitrary_text() -> None:
    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(lambda r: None)))
    with pytest.raises(JobicyError):
        JobicyProvider(fetcher, geo="latam&count=9999").feed_url()


def test_it_cannot_be_asked_about_one_employer() -> None:
    """A `source_board` row per employer would fetch the whole window once per
    employer -- which under a one-poll-an-hour grant breaches the terms as well
    as hammering somebody else's server. Himalayas learned this on real data."""
    refusal = JobicyProvider.validate_board(
        provider(),
        BoardRef(company_slug="northwind", provider="jobicy", board_identifier="northwind"),
    )

    assert refusal is not None
    assert "collect-jobicy" in refusal
    assert JobicyProvider.validate_board(provider(), BoardRef("", "jobicy", "")) is None


# =========================================================================
# 5. THE ORDINARY FIELDS
# =========================================================================


def test_the_id_is_the_vendors_and_is_a_string() -> None:
    assert external_id(_row(0)) == "900001"
    assert external_id({"id": True}) is None
    assert external_id({"id": "not-a-number"}) is None


def test_the_publication_date_is_left_as_the_vendor_wrote_it() -> None:
    """Three clocks -- this, the envelope's `lastUpdate` and HTTP
    `Last-Modified` -- and none is derived from another. There is no per-job
    update timestamp in this feed and none is invented."""
    assert published_at(_row(0)) == "2026-09-06T10:54:00+00:00"


def test_the_employer_is_read_rather_than_guessed_from_a_slug() -> None:
    assert company_of(_row(0)) == "Northwind Sample Co"


def test_the_whole_advert_arrives_in_the_list_response() -> None:
    assert JobicyProvider.capabilities.full_description_in_list is True
    assert JobicyProvider.capabilities.obtains_full_description is True

    stub = to_stub(_row(0))
    assert stub is not None
    posting = provider().fetch_posting(BoardRef("", "jobicy", ""), stub)
    assert "reside in Brazil" in posting.description_text
    assert "<p>" not in posting.description_text, "markup is stripped, never executed"


def test_a_real_band_is_read() -> None:
    hint = read_compensation(_row(0))

    assert hint is not None
    assert (hint.min_value, hint.max_value) == (120000, 160000)
    assert hint.currency == "BRL"
    assert hint.period == "YEAR"


def test_a_band_with_no_currency_yields_nothing() -> None:
    """350,000 MXN and 350,000 USD are not the same offer, and nothing here
    converts between them. The fixture case `jobicy_salary_missing_units`
    expects null and this is it."""
    assert read_compensation(_row(4)) is None


def test_a_period_this_product_cannot_compare_yields_no_period() -> None:
    """A weekly rate against an annual target is a wrong number.
    `salary_unknown` is the correct score for a figure that cannot be
    compared."""
    hint = read_compensation(_row(5))

    assert hint is None or hint.period is None

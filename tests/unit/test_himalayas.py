"""The Himalayas adapter: what it reads, and every refusal.

The fixtures are CAPTURED from a real response on 2026-09-07 and trimmed.
Nothing on `himalayas.app` restricts this reader -- its robots.txt is one
`User-agent: *` block with `Allow: /`, it names no AI crawler and it does not
mention the feed path -- so a recording was available where Get on Board's and
Jooble's were not, and it is labelled as one.

The tests that matter are the ones about the hiring scope. This is only the
second adapter in the product allowed to answer "where may this employer hire",
and the cost of that being wrong is a job the candidate cannot take shown as
one she can.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import BoardRef
from career_agent.providers.himalayas import (
    PER_PAGE,
    HimalayasError,
    HimalayasProvider,
    application_link,
    canonical_url,
    company_of,
    hiring_scope,
    read_compensation,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "himalayas"
PAGE1 = json.loads((FIXTURES / "feed-page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIXTURES / "feed-page2.json").read_text(encoding="utf-8"))
MALFORMED = json.loads((FIXTURES / "malformed-rows.json").read_text(encoding="utf-8"))


def _row(index: int = 0, page: dict[str, Any] = PAGE1) -> dict[str, Any]:
    return dict(page["jobs"][index])


def provider(
    pages: list[dict[str, Any]] | None = None,
    seen: list[httpx.Request] | None = None,
    **kwargs: Any,
) -> HimalayasProvider:
    """An adapter over a mock transport. Nothing here opens a socket."""
    bodies = pages if pages is not None else [PAGE1, PAGE2]
    order = iter(bodies)

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        try:
            return httpx.Response(200, json=next(order))
        except StopIteration:  # pragma: no cover - a walk asked for too much
            return httpx.Response(200, json={"jobs": [], "nextCursor": None})

    fetcher = HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    return HimalayasProvider(fetcher, **kwargs)


# =========================================================================
# the capability that matters most
# =========================================================================


def test_it_answers_where_the_employer_may_hire() -> None:
    """The whole reason this source was chosen over the alternatives."""
    assert HimalayasProvider.capabilities.publishes_hiring_scope is True


def test_the_restriction_list_reaches_the_stub_as_the_location() -> None:
    stub = to_stub(_row())
    assert stub is not None
    assert stub.location_raw
    scope = hiring_scope(_row())
    assert scope == stub.location_raw


def test_an_empty_restriction_list_is_nothing_stated_and_never_worldwide() -> None:
    """The most tempting field in this feed to over-read.

    An empty list looks exactly like "anywhere", and a source that returned it
    that way would make somebody eligible for a job they cannot take. Absence
    is never permission.
    """
    assert hiring_scope({"locationRestrictions": []}) is None
    assert hiring_scope({}) is None
    assert hiring_scope({"locationRestrictions": ["", "  "]}) is None


def test_the_board_does_not_claim_a_remote_flag() -> None:
    """Every posting here is remote because that is the board's admission
    criterion, not because the employer said so about this role."""
    assert HimalayasProvider.capabilities.exposes_remote_flag is False


# =========================================================================
# the body
# =========================================================================


def test_the_feed_carries_the_whole_advert() -> None:
    assert HimalayasProvider.capabilities.full_description_in_list is True
    assert HimalayasProvider.capabilities.obtains_full_description is True


def test_fetch_posting_makes_no_second_request_and_keeps_the_body() -> None:
    seen: list[httpx.Request] = []
    prov = provider(seen=seen)
    board = BoardRef(company_slug="", provider="himalayas", board_identifier="")
    stub = next(iter(prov.list_postings(board)))
    before = len(seen)

    raw = prov.fetch_posting(board, stub)

    assert len(seen) == before
    assert raw.has_description is True
    assert len(raw.description_text) > 200
    assert raw.payload == stub.payload


# =========================================================================
# compensation
# =========================================================================


def test_a_band_with_a_currency_and_a_period_is_read() -> None:
    hint = read_compensation(_row())
    assert hint is not None
    assert hint.currency == "USD"
    assert hint.period == "YEAR"
    assert hint.min_value and hint.max_value


def test_a_band_with_no_currency_is_not_a_band() -> None:
    """Nothing in this product converts between currencies, so an amount with
    no unit cannot be compared against a target."""
    assert read_compensation({"minSalary": 100000, "maxSalary": 150000}) is None


def test_a_period_outside_the_vocabulary_resolves_to_none() -> None:
    """A weekly rate compared against an annual target is a wrong number, and
    `salary_unknown` is the correct score for a figure that cannot be
    compared."""
    hint = read_compensation(
        {"minSalary": 100, "maxSalary": 200, "currency": "EUR", "salaryPeriod": "weekly"}
    )
    assert hint is not None
    assert hint.period is None


def test_a_posting_that_states_no_pay_reads_as_none() -> None:
    assert read_compensation({"currency": "USD"}) is None
    assert read_compensation(None) is None


# =========================================================================
# addressing, and the refusals
# =========================================================================


def test_a_row_with_no_guid_cannot_be_addressed() -> None:
    assert to_stub(MALFORMED["jobs"][0]) is None


def test_a_row_with_no_title_cannot_be_addressed() -> None:
    assert to_stub(MALFORMED["jobs"][1]) is None


def test_a_guid_pointing_off_host_is_refused() -> None:
    """A URL from a payload is untrusted input: it reaches the interface as a
    link somebody clicks."""
    assert canonical_url(MALFORMED["jobs"][2]) is None
    assert to_stub(MALFORMED["jobs"][2]) is None


def test_an_impossible_publication_date_is_absent_rather_than_wrong() -> None:
    """A date this adapter cannot read is an absent date, never today's."""
    stub = to_stub(MALFORMED["jobs"][3])
    assert stub is not None
    assert stub.posted_at is None


def test_the_application_link_is_not_offered_as_an_origin() -> None:
    """Measured 2026-09-07: on all 20 rows of the first page this equalled
    `guid`, which is this board's own posting page. Naming it for what it is
    stops a later reader trusting it for cross-source identity."""
    row = _row()
    assert application_link(row) == canonical_url(row)


def test_the_company_is_read_and_never_invented() -> None:
    assert company_of(_row())
    assert company_of({"companyName": "  "}) is None
    assert company_of({}) is None


# =========================================================================
# the walk
# =========================================================================


def test_the_walk_follows_the_cursor_and_stops_at_the_end() -> None:
    seen: list[httpx.Request] = []
    read = provider(seen=seen, max_pages=5).read_feed()

    assert read.pages == 2
    assert read.stopped_early is False
    assert len(read.jobs) == len(PAGE1["jobs"]) + len(PAGE2["jobs"])
    assert "cursor=cursor-page-2" in str(seen[1].url)


def test_the_page_budget_is_reported_apart_from_the_end_of_the_feed() -> None:
    """ "There is no more" and "we chose to stop" are different facts, and a
    caller that cannot tell reports a bounded read as a complete one."""
    read = provider(max_pages=1).read_feed()

    assert read.pages == 1
    assert read.stopped_early is True


def test_the_default_budget_is_bounded() -> None:
    """A walk that can be asked for everything is a walk somebody will ask for
    everything."""
    prov = provider()
    assert prov._max_pages == 2  # noqa: SLF001


def test_one_row_arriving_twice_becomes_one_job() -> None:
    """The cursor is documented never to return the same job twice. This is
    not trust in that: a feed being written to while it is read is exactly
    where a duplicate appears."""
    repeated = {**PAGE1, "nextCursor": None}
    read = provider(pages=[PAGE1, repeated], max_pages=3).read_feed()

    ids = [j.get("guid") for j in read.jobs]
    assert len(ids) == len(set(ids))


def test_an_unexpected_shape_raises_rather_than_reading_as_empty() -> None:
    """An empty feed and a broken response are different outcomes."""
    prov = provider(pages=[{"results": []}])
    with pytest.raises(HimalayasError):
        prov.read_feed()


def test_the_offset_parameter_is_never_built() -> None:
    """The vendor documents it as deprecated and as able to return the same job
    twice. A walk that re-served and skipped rows would produce a corpus wrong
    in a way no counter here could detect."""
    url = provider().feed_url()
    assert "offset" not in url
    assert f"limit={PER_PAGE}" in url


def test_a_cursor_that_is_not_a_token_is_refused() -> None:
    """A token comes back from a response, and a response is untrusted input."""
    with pytest.raises(HimalayasError):
        provider().feed_url("../../admin?x=1")


def test_every_url_it_builds_is_https_on_the_one_host() -> None:
    prov = provider()
    for url in (prov.feed_url(), prov.feed_url("abc123"), prov.board_url(BoardRef("", "", ""))):
        assert url.startswith("https://himalayas.app/")


# =========================================================================
# rescore reconstruction
# =========================================================================


def test_normalisation_is_deterministic_over_an_archived_payload() -> None:
    """The same payload must yield the same stub, every time, with no clock.

    `pipeline/facts.py` rebuilds a posting's facts from its archived payload,
    so a rescore has to reach the same answer collection did.
    """
    first = to_stub(_row())
    second = to_stub(json.loads(json.dumps(_row())))
    assert first == second

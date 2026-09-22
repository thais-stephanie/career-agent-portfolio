"""The Workday adapter, and the three traps that make it not a loop.

THE FIXTURES ARE INVENTED, and that is the point of this paragraph.

Nothing here was captured. The shape is built to the contract measured on
2026-09-05 against two real tenants and written up in
`docs/product/source-evidence-2026-09-05.md` -- the same standard the Get on
Board and Remote OK fixtures were built to, and labelled the same way so nobody
reads an invented body as a recording. No request in this file leaves the
process: every adapter is constructed over `httpx.MockTransport`.

WHAT IS WORTH ASSERTING, AND WHY EACH ONE IS HERE
-------------------------------------------------
Two of these tests exist because the alternative is an INFINITE COLLECTION.

`offset >= total` serves page zero again rather than an empty page, so a walk
that stops on an empty page never stops, and re-ingests the first twenty
postings for as long as the process lives. Trap one makes that easy to reach by
accident: `total` is zero on every page after the first, so a bound recomputed
per page would be zero, and the walk would then ask for offset 20 forever.

The third trap is cheaper and still worth a test: page size 20 is a refusal
rather than a clamp, so a larger page is a 400 and no postings at all.

And one refusal is asserted before any of that: a board identifier that does not
name a tenant, a data centre AND a site is rejected with no request made. That
is the whole reason this adapter exists rather than having existed a year ago --
a Workday identity cannot be guessed, so an adapter that accepted two thirds of
one would be inventing the rest.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import BoardRef
from career_agent.providers.workday import (
    PAGE_SIZE,
    WorkdayProvider,
    recognise_posting_url,
)

BOARD = BoardRef(
    company_slug="purple",
    provider="workday",
    #: The identity `scan-career-sites` actually resolved for this employer on
    #: 2026-09-10, from its own careers page. The company is real; every posting
    #: below is invented.
    board_identifier="purple.wd1/purplecareers",
)


def _listing(count: int, total: int, *, first: int = 0) -> dict[str, Any]:
    """One listing page, shaped like the measured response.

    `total` is a parameter rather than derived, because the ability to report a
    total that contradicts the page is the first trap and has to be expressible.
    """
    return {
        "total": total,
        "jobPostings": [
            {
                "title": f"Invented Role {first + index}",
                "externalPath": f"/job/Remote/Invented-Role-{first + index}_JR{first + index}",
                "locationsText": "Remote, United Kingdom and 2 more",
                "postedOn": "Posted Today",
                "bulletFields": [f"JR{first + index}"],
            }
            for index in range(count)
        ],
        "facets": [],
        "userAuthenticated": False,
    }


def _detail(description: str = "<p>An invented advert.</p>") -> dict[str, Any]:
    return {
        "jobPostingInfo": {
            "jobDescription": description,
            "startDate": "2026-09-01",
            "country": {"descriptor": "United Kingdom"},
            "timeType": "Full time",
            "jobRequisitionId": "JR0",
            "externalUrl": "https://purple.wd1.myworkdayjobs.com/purplecareers/job/x_JR0",
        },
        "hiringOrganization": {"name": "Purple"},
    }


def _provider(
    handler, seen: list[httpx.Request] | None = None
) -> tuple[WorkdayProvider, list[httpx.Request]]:
    recorded: list[httpx.Request] = seen if seen is not None else []

    def wrapped(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return handler(request)

    fetcher = HttpFetcher(
        client=httpx.Client(transport=httpx.MockTransport(wrapped)),
        request_delay_seconds=0,
    )
    return WorkdayProvider(fetcher), recorded


class _Sequence:
    """Serves listing pages in request order, repeating page one at the end."""

    def __init__(self, *bodies: dict[str, Any]) -> None:
        self._bodies = list(bodies)
        self.calls = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=_detail())
        body = self._bodies[self.calls] if self.calls < len(self._bodies) else self._bodies[0]
        self.calls += 1
        return httpx.Response(200, json=body)


# =========================================================================
# 1. THE IDENTITY, AND THE REFUSAL
# =========================================================================


def test_a_board_identity_needs_all_three_parts() -> None:
    """A tenant alone, or a tenant and a data centre, is not a board.

    Answered with NO REQUEST, so `collect` rejects the plan entry before a
    socket opens and carries on with every other board.
    """
    provider, seen = _provider(lambda request: httpx.Response(200, json=_listing(0, 0)))
    for identifier in ("purple", "purple.wd1", "purple/purplecareers", "purple.wd1/"):
        board = BoardRef(company_slug="purple", provider="workday", board_identifier=identifier)
        assert provider.validate_board(board) is not None, identifier
    assert seen == [], "validating an identity must not call the vendor"


def test_a_resolved_identity_is_accepted_and_builds_the_vendor_urls() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json=_listing(0, 0)))
    assert provider.validate_board(BOARD) is None
    assert provider.jobs_url(BOARD) == (
        "https://purple.wd1.myworkdayjobs.com/wday/cxs/purple/purplecareers/jobs"
    )
    assert provider.board_url(BOARD) == "https://purple.wd1.myworkdayjobs.com/purplecareers"


def test_the_detail_url_does_not_repeat_the_job_segment() -> None:
    """The measurement recorded `<base>/job<externalPath>`, and the vendor's own
    `externalPath` already begins `/job/`. Concatenating both would ask for
    `/job/job/...`; inserting the segment only when it is absent keeps both
    observed shapes reachable without guessing at a value we were handed."""
    provider, _ = _provider(lambda request: httpx.Response(200, json=_detail()))
    base = "https://purple.wd1.myworkdayjobs.com/wday/cxs/purple/purplecareers"
    assert provider.detail_url(BOARD, "/job/Remote/Role_JR1") == f"{base}/job/Remote/Role_JR1"
    assert provider.detail_url(BOARD, "/Remote/Role_JR1") == f"{base}/job/Remote/Role_JR1"


def test_an_unresolvable_identity_raises_rather_than_returning_nothing() -> None:
    """An empty iterator would read as an empty board, and an empty board closes
    postings. A board this adapter cannot address must fail loudly."""
    provider, _ = _provider(lambda request: httpx.Response(200, json=_listing(0, 0)))
    board = BoardRef(company_slug="purple", provider="workday", board_identifier="purple")
    with pytest.raises(FetchError):
        list(provider.list_postings(board))


# =========================================================================
# 2. THE WALK, AND THE TRAPS
# =========================================================================


def test_the_bound_comes_from_the_first_page_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """TRAP ONE. `total` is 45 on page one and zero on every page after it.

    A bound recomputed per page would be zero, and the walk would end holding
    twenty of forty-five postings while the vendor had answered 200 three times.
    """
    sequence = _Sequence(
        _listing(PAGE_SIZE, 45, first=0),
        _listing(PAGE_SIZE, 0, first=PAGE_SIZE),
        _listing(5, 0, first=PAGE_SIZE * 2),
    )
    provider, _ = _provider(sequence)
    stubs = list(provider.list_postings(BOARD))
    assert len(stubs) == 45
    assert len({stub.external_id for stub in stubs}) == 45


def test_the_walk_stops_rather_than_re_reading_page_zero_forever() -> None:
    """TRAP TWO, and the reason this file exists.

    `_Sequence` serves page one again once it runs out of pages, which is what
    the vendor does for `offset >= total`. A walk that stopped on an EMPTY page
    would never see one.
    """
    sequence = _Sequence(_listing(PAGE_SIZE, PAGE_SIZE, first=0))
    provider, _ = _provider(sequence)
    stubs = list(provider.list_postings(BOARD))
    assert len(stubs) == PAGE_SIZE
    assert sequence.calls == 1, "the bound was reached and no second page was asked for"


def test_a_total_that_lies_high_still_terminates() -> None:
    """The defensive half of trap two.

    If `total` claims more than the board serves, the bound alone would keep
    asking -- and every extra request comes back as page zero. The walk compares
    a later page against the first page's ids and stops, so a wrong total costs
    one extra request rather than an unbounded collection.
    """
    sequence = _Sequence(_listing(PAGE_SIZE, 10_000, first=0))
    provider, seen = _provider(sequence)
    stubs = list(provider.list_postings(BOARD))
    assert len(stubs) == PAGE_SIZE
    assert sequence.calls == 2, "one page, one repeat, then stop"
    assert len(seen) == 2


def test_the_page_size_is_twenty_and_is_sent_on_every_request() -> None:
    """TRAP THREE. 21 is a 400 rather than a clamp, so this is not a knob."""
    sequence = _Sequence(_listing(PAGE_SIZE, 40, first=0), _listing(PAGE_SIZE, 0, first=PAGE_SIZE))
    provider, seen = _provider(sequence)
    list(provider.list_postings(BOARD))
    assert PAGE_SIZE == 20
    posts = [request for request in seen if request.method == "POST"]
    assert posts, "the listing is a POST"
    for request in posts:
        assert b'"limit": 20' in request.content or b'"limit":20' in request.content


def test_an_empty_board_yields_nothing_and_does_not_raise() -> None:
    """An empty board and a failed request are different outcomes, and
    conflating them would let a network blip close a company's whole history."""
    provider, _ = _provider(lambda request: httpx.Response(200, json=_listing(0, 0)))
    assert list(provider.list_postings(BOARD)) == []


def test_a_body_that_is_not_the_expected_envelope_is_a_failure() -> None:
    provider, _ = _provider(lambda request: httpx.Response(200, json={"errorCode": "HTTP_400"}))
    with pytest.raises(FetchError):
        list(provider.list_postings(BOARD))


def test_a_malformed_entry_is_skipped_and_counted() -> None:
    """Losing one posting is better than losing a company, and a skip that
    nobody counts is a silent loss. `postings_skipped` is what
    `career-agent ingestion-report` reads."""
    page = _listing(2, 2)
    page["jobPostings"].append({"title": "No path here"})
    page["jobPostings"].append({"externalPath": "/job/Remote/No-title_JR9"})
    page["total"] = 4
    sequence = _Sequence(page)
    provider, _ = _provider(sequence)
    stubs = list(provider.list_postings(BOARD))
    assert len(stubs) == 2
    assert provider.postings_skipped == 2


# =========================================================================
# 3. WHAT ONE POSTING BECOMES
# =========================================================================


def test_a_listing_stub_carries_no_date_because_the_listing_has_none() -> None:
    """`postedOn` is the words "Posted Today". Turning that into a timestamp
    would be this program inventing a date and attributing it to an employer."""
    sequence = _Sequence(_listing(1, 1))
    provider, _ = _provider(sequence)
    stub = next(iter(provider.list_postings(BOARD)))
    assert stub.posted_at is None
    assert stub.description_html is None
    assert stub.location_raw == "Remote, United Kingdom and 2 more"
    assert stub.external_id.startswith("/job/")
    assert stub.url == (
        "https://purple.wd1.myworkdayjobs.com/purplecareers/job/Remote/Invented-Role-0_JR0"
    )


def test_the_description_and_the_real_date_arrive_with_the_detail() -> None:
    sequence = _Sequence(_listing(1, 1))
    provider, seen = _provider(sequence)
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)

    assert posting.has_description
    assert "invented advert" in posting.description_text.lower()
    assert posting.stub.posted_at == "2026-09-01T00:00:00+00:00"
    assert [request.method for request in seen] == ["POST", "GET"]


def test_the_stored_payload_holds_both_halves() -> None:
    """The field map reads `locationsText` from the listing and
    `jobPostingInfo.timeType` from the detail, so one stored payload has to
    carry both or half the map resolves to nothing."""
    sequence = _Sequence(_listing(1, 1))
    provider, _ = _provider(sequence)
    stub = next(iter(provider.list_postings(BOARD)))
    posting = provider.fetch_posting(BOARD, stub)
    assert posting.payload["locationsText"]
    assert posting.payload["jobPostingInfo"]["timeType"] == "Full time"


def test_this_adapter_claims_no_hiring_scope() -> None:
    """Invariant 3. `jobPostingInfo.country` is where the requisition is, and no
    CxS field says where the employer may hire. An adapter that claimed
    otherwise would make a posting look open to somebody it is closed to."""
    from career_agent.providers.registry import publishes_hiring_scope

    assert publishes_hiring_scope("workday") is False


# =========================================================================
# 4. IDENTITY FROM A URL
# =========================================================================


@pytest.mark.parametrize(
    "url",
    [
        "https://purple.wd1.myworkdayjobs.com/purplecareers/job/Remote/Role_JR1",
        "https://purple.wd1.myworkdayjobs.com/en-US/purplecareers/job/Remote/Role_JR1",
        "https://purple.wd1.myworkdaysite.com/en-GB/purplecareers/job/Remote/Role_JR1",
    ],
)
def test_a_public_posting_url_resolves_to_the_stored_id(url: str) -> None:
    """ADR-0013: one authoritative origin per posting, recovered from the
    pointer the provider published. The id returned is `externalPath`, which is
    what the stub stores, so a pasted URL finds the row already here."""
    assert recognise_posting_url(url) == "/job/Remote/Role_JR1"


@pytest.mark.parametrize(
    "url",
    [
        "https://purple.wd1.myworkdayjobs.com/purplecareers",
        "https://boards.greenhouse.io/purple/jobs/123",
        "https://purple.com/careers",
        "",
    ],
)
def test_a_url_that_is_not_one_of_ours_is_not_claimed(url: str) -> None:
    assert recognise_posting_url(url) is None


def test_this_adapter_is_never_probed_with_a_guessed_identifier() -> None:
    """The structural half of "never invent a tenant".

    `collect` walks every family with a board per employer, which includes this
    one. `discover` probes only the families whose identifier can be DERIVED
    from a company name, which must never include this one.
    """
    from career_agent.providers.registry import board_providers, guessable_providers

    assert "workday" in board_providers()
    assert "workday" not in guessable_providers()

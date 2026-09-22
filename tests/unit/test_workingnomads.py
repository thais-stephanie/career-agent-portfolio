"""The Working Nomads adapter: what it reads, and the field it refuses to read.

The fixtures are CAPTURED from a real response on 2026-09-07 and trimmed.
`www.workingnomads.com/robots.txt` is one `User-agent: *` block with an empty
`Disallow:`, so a recording was available.

The test that matters most is the one asserting this adapter does NOT publish a
hiring scope. Its `location` field carries `Global` and `Europe, LATAM, APAC`,
which look exactly like an answer to "where may this employer hire" -- and it
also carries bare country names that could as easily be where the company is.
Invariant 3 exists because that conflation is the most expensive mistake this
system makes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import BoardRef
from career_agent.providers.workingnomads import (
    WorkingNomadsError,
    WorkingNomadsProvider,
    canonical_url,
    company_of,
    external_id,
    to_stub,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "workingnomads"
FEED = json.loads((FIXTURES / "feed.json").read_text(encoding="utf-8"))
MALFORMED = json.loads((FIXTURES / "malformed-rows.json").read_text(encoding="utf-8"))


def _row(index: int = 0) -> dict[str, Any]:
    return dict(FEED[index])


def provider(body: Any = None, seen: list[httpx.Request] | None = None) -> WorkingNomadsProvider:
    payload = FEED if body is None else body

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return WorkingNomadsProvider(
        HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(handler)))
    )


# =========================================================================
# the refusal that matters
# =========================================================================


def test_it_does_not_claim_to_publish_a_hiring_scope() -> None:
    """The hard call in this adapter, asserted so a later edit is deliberate.

    `Global` and `Europe, LATAM, APAC` are tempting. The field is named
    `location`, nothing first-party says what it means, and the same field
    carries `Australia` and `Germany`. A false PASS shows a job she cannot
    take; a false FAIL hides one she can.
    """
    assert WorkingNomadsProvider.capabilities.publishes_hiring_scope is False


def test_the_location_still_reaches_the_stub_to_be_shown() -> None:
    """Refusing to SCORE it is not refusing to show it. A reader deciding
    whether to open a link wants to see what the board printed."""
    stub = to_stub(_row())
    assert stub is not None
    assert stub.location_raw == _row()["location"]


def test_the_board_does_not_claim_a_remote_flag() -> None:
    """Every posting here is remote because that is the board's admission
    criterion, not because the employer said so about this role."""
    assert WorkingNomadsProvider.capabilities.exposes_remote_flag is False


def test_the_board_category_is_not_a_department() -> None:
    """`category_name` is the board's taxonomy. Reading it as a department
    would put the board's vocabulary where the employer's belongs."""
    assert WorkingNomadsProvider.capabilities.exposes_department is False
    stub = to_stub(_row())
    assert stub is not None
    assert stub.department is None


# =========================================================================
# the body
# =========================================================================


def test_the_feed_carries_the_whole_advert() -> None:
    assert WorkingNomadsProvider.capabilities.full_description_in_list is True
    assert WorkingNomadsProvider.capabilities.obtains_full_description is True


def test_fetch_posting_makes_no_second_request_and_keeps_the_body() -> None:
    seen: list[httpx.Request] = []
    prov = provider(seen=seen)
    board = BoardRef(company_slug="", provider="workingnomads", board_identifier="")
    stub = next(iter(prov.list_postings(board)))
    before = len(seen)

    raw = prov.fetch_posting(board, stub)

    assert len(seen) == before
    assert raw.has_description is True
    assert len(raw.description_text) > 200
    assert raw.payload == stub.payload


# =========================================================================
# addressing, and the refusals
# =========================================================================


def test_the_vendor_id_comes_from_its_own_canonical_url() -> None:
    row = _row()
    assert external_id(row) == row["url"].rstrip("/").rsplit("/", 1)[-1]


def test_a_row_with_no_url_cannot_be_addressed() -> None:
    assert to_stub(MALFORMED[0]) is None


def test_a_row_with_no_title_cannot_be_addressed() -> None:
    assert to_stub(MALFORMED[1]) is None


def test_a_url_pointing_off_host_is_refused() -> None:
    assert canonical_url(MALFORMED[2]) is None
    assert to_stub(MALFORMED[2]) is None


def test_a_url_carrying_no_numeric_id_is_refused() -> None:
    """A posting this system cannot address consistently across collections
    would be re-created as a new row every time."""
    assert external_id(MALFORMED[3]) is None
    assert to_stub(MALFORMED[3]) is None


def test_an_unreadable_date_is_absent_rather_than_today() -> None:
    stub = to_stub(MALFORMED[4])
    assert stub is not None
    assert stub.posted_at is None


def test_a_real_date_is_normalised_to_utc() -> None:
    stub = to_stub(_row())
    assert stub is not None
    assert stub.posted_at is not None
    assert stub.posted_at.endswith("Z")


def test_the_company_is_read_and_never_invented() -> None:
    assert company_of(_row())
    assert company_of({"company_name": "  "}) is None
    assert company_of({}) is None


# =========================================================================
# the window
# =========================================================================


def test_one_request_reads_the_whole_window() -> None:
    seen: list[httpx.Request] = []
    read = provider(seen=seen).read_feed()

    assert len(seen) == 1
    assert len(read.jobs) == len(FEED)


def test_no_page_parameter_is_ever_built() -> None:
    """Measured 2026-09-07: the bare path and `?limit=200` return the same 32
    postings. A page parameter the vendor ignores would make a walk look
    bounded when it is simply short."""
    url = provider().feed_url()
    assert "page" not in url
    assert "limit" not in url
    assert url.startswith("https://www.workingnomads.com/")


def test_an_unexpected_shape_raises_rather_than_reading_as_empty() -> None:
    """An empty window and a broken request are different outcomes."""
    with pytest.raises(WorkingNomadsError):
        provider(body={"data": []}).read_feed()


def test_unaddressable_rows_are_counted_rather_than_dropped_silently() -> None:
    read = provider(body=MALFORMED).read_feed()

    assert read.unaddressable == 4
    assert len(read.jobs) == len(MALFORMED)


def test_normalisation_is_deterministic_over_an_archived_payload() -> None:
    """A rescore rebuilds facts from the archived payload and must reach the
    same answer collection did."""
    assert to_stub(_row()) == to_stub(json.loads(json.dumps(_row())))

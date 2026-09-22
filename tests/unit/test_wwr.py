"""The We Work Remotely adapter, against a recorded feed.

`tests/fixtures/providers/wwr/feed-programming.xml` is a real response, trimmed
to three items. A recorded fixture tests the adapter against the feed; a
hand-written one would test it against the author's belief about the feed.

Nothing here opens a socket.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import ProviderKind, RetrievalMode
from career_agent.providers.wwr import (
    FEEDS,
    WwrProvider,
    company_of,
    external_id_from,
    split_title,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "providers" / "wwr"
FEED = (FIXTURES / "feed-programming.xml").read_text(encoding="utf-8")


def provider(body: str = FEED, *, status: int = 200, seen: list | None = None) -> WwrProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, text=body, headers={"content-type": "application/rss+xml"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    return WwrProvider(HttpFetcher(client=client, request_delay_seconds=0, sleep=lambda _s: None))


# =========================================================================
# 1. THE FEED
# =========================================================================


def test_the_recorded_feed_parses_into_addressable_rows() -> None:
    read = provider().read_feed("programming")
    assert len(read.items) == 3
    assert read.unaddressable == 0
    assert read.empty is False


def test_every_known_feed_has_a_url_and_an_unknown_one_is_refused() -> None:
    subject = provider()
    for name in FEEDS:
        assert subject.feed_url(name).startswith("https://weworkremotely.com/")
    with pytest.raises(ValueError, match="unknown WWR feed"):
        subject.feed_url("not-a-feed")


def test_reading_a_feed_costs_exactly_one_request() -> None:
    """There is no pagination to walk. A loop here would be inventing an
    interface the source does not have."""
    seen: list[httpx.Request] = []
    provider(seen=seen).read_feed("programming")
    assert len(seen) == 1


def test_an_empty_feed_is_a_success_and_not_a_failure() -> None:
    """The distinction the whole lifecycle rests on: an empty read must never
    be a reason to close jobs."""
    empty = (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0">'
        "<channel><title>WWR</title></channel></rss>"
    )
    read = provider(empty).read_feed("all")
    assert read.items == ()
    assert read.empty is True


def test_a_broken_document_raises_rather_than_reading_as_empty() -> None:
    with pytest.raises(Exception):  # noqa: B017 - ElementTree's own ParseError
        provider("this is not xml at all").read_feed("all")


def test_a_transport_failure_raises_rather_than_reading_as_empty() -> None:
    with pytest.raises(FetchError):
        provider("nope", status=500).read_feed("all")


# =========================================================================
# 2. ONE POSTING
# =========================================================================


def test_a_row_becomes_a_stub_with_the_whole_posting() -> None:
    """The reason this source was built. `description` is the full posting,
    which is what a matcher that reads bodies actually needs."""
    subject = provider()
    read = subject.read_feed("programming")
    stub = subject.to_stub(read.items[0])

    assert stub is not None
    assert stub.external_id and "/" not in stub.external_id
    assert stub.url.startswith("https://weworkremotely.com/remote-jobs/")
    assert stub.posted_at is not None and "+00:00" in stub.posted_at
    assert stub.location_raw, "region should carry the hiring scope"
    assert stub.description_html and len(stub.description_html) > 500


def test_the_description_becomes_readable_text_with_the_html_kept() -> None:
    """ADR-0002 verifies a quote against the archived text. The HTML is archived
    beside the text so there are exact bytes to verify against."""
    subject = provider()
    read = subject.read_feed("programming")
    stub = subject.to_stub(read.items[0])
    assert stub is not None

    raw = subject.fetch_posting(None, stub)  # type: ignore[arg-type]

    assert raw.has_description
    assert "<" not in raw.description_text, "tags survived into the readable text"
    assert "&amp;" not in raw.description_text, "entities were not unescaped"
    assert raw.description_html == stub.description_html
    assert raw.payload == stub.payload


def test_fetching_a_posting_makes_no_second_request() -> None:
    seen: list[httpx.Request] = []
    subject = provider(seen=seen)
    read = subject.read_feed("programming")
    stub = subject.to_stub(read.items[0])
    assert stub is not None

    before = len(seen)
    subject.fetch_posting(None, stub)  # type: ignore[arg-type]
    assert len(seen) == before


def test_a_row_without_a_guid_is_counted_rather_than_dropped_silently() -> None:
    """A feed that stops carrying `guid` is a broken contract, not a quiet day."""
    broken = FEED.replace("<guid>", "<notguid>").replace("</guid>", "</notguid>")
    read = provider(broken).read_feed("programming")
    assert read.items == ()
    assert read.unaddressable == 3


# =========================================================================
# 3. THE TITLE CARRIES THE COMPANY, AND ONLY WHEN IT DOES
# =========================================================================


@pytest.mark.parametrize(
    ("raw", "company", "role"),
    [
        ("Edfinity: Senior Software Engineer, remote", "Edfinity", "Senior Software Engineer"),
        ("Acme Corp: Staff Engineer", "Acme Corp", "Staff Engineer"),
        ("Company: Role - Remote", "Company", "Role"),
    ],
)
def test_a_colon_title_splits_into_company_and_role(raw: str, company: str, role: str) -> None:
    assert split_title(raw) == (company, role)


def test_a_title_with_no_colon_resolves_no_company() -> None:
    """Guessing an employer from the first two words is the fuzzy identity
    ADR-0008 closes by forbidding. No colon, no company."""
    company, role = split_title("Senior Software Engineer, remote")
    assert company is None
    assert role == "Senior Software Engineer"


def test_every_recorded_item_resolves_a_company() -> None:
    read = provider().read_feed("programming")
    assert all(company_of(row) for row in read.items)


def test_the_external_id_is_the_slug_and_not_the_whole_url() -> None:
    """The URL would work and would break the day the site changes scheme or
    host. The slug is the stable half."""
    assert (
        external_id_from("https://weworkremotely.com/remote-jobs/edfinity-senior-engineer")
        == "edfinity-senior-engineer"
    )
    assert external_id_from("https://weworkremotely.com/remote-jobs/x/") == "x"


# =========================================================================
# 4. WHAT IT CLAIMS ABOUT ITSELF
# =========================================================================


def test_it_declares_a_feed_that_is_a_window_rather_than_a_board() -> None:
    assert WwrProvider.kind is ProviderKind.AGGREGATOR
    assert WwrProvider.retrieval_mode is RetrievalMode.AGGREGATOR_FEED


def test_it_does_not_claim_a_remote_flag_it_does_not_have() -> None:
    """Every posting on this board is remote, which sounds like a remote flag
    and is not one. That is the board's admission criterion; `region` is the
    employer's hiring scope, and conflating them is invariant 3."""
    assert WwrProvider.capabilities.exposes_remote_flag is False
    assert WwrProvider.capabilities.full_description_in_list is True
    mapped = set(WwrProvider.field_map.paths())
    assert mapped == {"region"}


def test_reading_the_same_feed_twice_produces_the_same_stubs() -> None:
    """Idempotence at the adapter, which is what the collector's idempotence
    is built on."""
    subject = provider()
    first = [subject.to_stub(row) for row in subject.read_feed("programming").items]
    second = [subject.to_stub(row) for row in subject.read_feed("programming").items]
    assert first == second

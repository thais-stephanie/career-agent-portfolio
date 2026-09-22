"""Working Nomads, read from the public job feed it publishes.

WHY THIS ONE, AND WHY NOT THE OBVIOUS ALTERNATIVE
--------------------------------------------------
Arbeitnow was the other candidate and was measured first: a documented public
API, 250 rows a page, real pagination, full descriptions, and a permissive
robots.txt. It is a perfectly good source and it is not built here, because of
what the measurement said about the postings rather than about the interface.

Of 250 rows read live on 2026-09-07, **11 were remote** and the locations were
London, Munich, Paris and Berlin -- European offices. For a candidate in Brazil
that is volume she cannot take. `docs/product/source-capability-matrix.md`
records it honestly rather than counting it.

This board is remote-only by admission policy, and every posting on it is
therefore a job somebody outside the company's city could plausibly do.

WHAT IT SUPPLIES
----------------
The whole advert -- 1,281 characters at the shortest and 3,524 at the median,
measured over one window -- plus a company, a category, a publication date, a
tag list and a location string.

WHAT IT DOES NOT SUPPLY: COMPLETENESS
--------------------------------------
The feed is a WINDOW, not a paginated archive. One request returns 32 postings
covering roughly four weeks, and `?limit=200` returns the same 32 -- measured,
not assumed. Collecting repeatedly accumulates over time and never yields a
whole board, exactly as for We Work Remotely, and nothing built on this may
present its counts as coverage of remote hiring.

WHY `publishes_hiring_scope` IS FALSE, WHICH IS THE HARD CALL HERE
-------------------------------------------------------------------
The `location` field carries values like `Europe, North America, Latin America,
APAC`, `Global` and `Europe, LATAM, APAC, the U.S., Canada`. Those are plainly
not places a company has desks, and on a remote-only board no location can be
an office somebody must attend. The temptation to read the field as a hiring
scope is real, and it is refused.

Two reasons, and the second decides it:

1. The field is named `location`, not `locationRestrictions`, and no
   first-party page has been read that says what it means. Himalayas earns True
   because its field is named for the restriction and documented as one.
2. The same field also carries `Germany`, `Australia`, `Canada` and
   `Japan - Remote`, which could as easily be where the company is as where it
   may hire -- and invariant 3 exists because that conflation is the most
   expensive mistake this system makes. A false PASS shows a job she cannot
   take.

So the string is stored and shown, the eligibility gate does not read it, and
geography is answered from the body like every ATS posting in the corpus. **If
a first-party page is ever read that documents the field, this is a one-line
change** -- the same note Get on Board carried about its description, which
turned out to be right.

AUTHORISATION, READ FIRST-PARTY 2026-09-07
-------------------------------------------
`www.workingnomads.com/robots.txt` is one `User-agent: *` block with an empty
`Disallow:`. Nothing is restricted, no AI crawler is named, and the API path is
not mentioned. Read-only, one request per collection, under this product's own
user agent.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)

API_HOST = "https://www.workingnomads.com"

#: The one host this adapter will talk to.
TRUSTED_HOST = "www.workingnomads.com"

FEED_PATH = "/api/exposed_jobs/"

#: `https://www.workingnomads.com/job/go/1837431/` -- the numeric id is the
#: vendor's own and is what makes a posting addressable across collections.
JOB_ID = re.compile(r"/job/go/(?P<id>\d+)/?$")


WORKINGNOMADS_CAPABILITIES = ProviderCapabilities(
    # The whole advert, in the feed, in one request.
    full_description_in_list=True,
    obtains_full_description=True,
    # `pub_date`, ISO 8601 with an offset. A real publication date.
    exposes_posted_date=True,
    # `category_name` is a board taxonomy, not the employer's team. Reading it
    # as a department would put the board's vocabulary where the employer's
    # belongs.
    exposes_department=False,
    # Nothing structured. Pay appears inside the description prose on some
    # postings, which the matcher reads as text like any other sentence.
    exposes_compensation=False,
    # One free-text string, no country codes.
    exposes_location_structured=False,
    # Every posting here is remote, which is the board's admission criterion
    # rather than a statement the employer made about this role. Same False as
    # We Work Remotely and Himalayas, for the same reason.
    exposes_remote_flag=False,
    # No employment-type element.
    exposes_employment_type=False,
    # **False, and it is the hard call in this file.** See the module
    # docstring: the field is named `location`, nothing first-party says what
    # it means, and it carries bare country names that could be either. The
    # asymmetry decides it.
    publishes_hiring_scope=False,
)


WORKINGNOMADS_FIELD_MAP = ProviderFieldMap(
    mappings=(FieldMapping(path="location", dimension=MetadataDimension.HIRING_LOCATION_HINT),)
)


class WorkingNomadsError(ValueError):
    """A response this adapter will not use."""


def _text(value: Any) -> str | None:
    return value.strip() or None if isinstance(value, str) else None


def canonical_url(job: Any) -> str | None:
    """`url`, accepted only as HTTPS on the trusted host.

    A URL from a payload is untrusted input: it reaches the interface as a link
    somebody clicks, so a `javascript:` scheme or an off-host redirect target
    is an attack surface rather than a data-quality issue.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("url"))
    if raw is None:
        return None

    from urllib.parse import urlsplit

    parts = urlsplit(raw)
    if parts.scheme != "https" or parts.hostname != TRUSTED_HOST:
        return None
    return raw


def external_id(job: Any) -> str | None:
    """The vendor's numeric id, from its own canonical URL.

    Derived from the URL rather than invented, and None when the URL does not
    carry one -- a posting this system cannot address consistently across
    collections would be re-created as a new row every time.
    """
    url = canonical_url(job)
    if url is None:
        return None
    match = JOB_ID.search(url)
    return match.group("id") if match else None


def company_of(job: Any) -> str | None:
    """The employer this posting names, for the collector to resolve."""
    if not isinstance(job, dict):
        return None
    return _text(job.get("company_name"))


def _posted_at(job: Any) -> str | None:
    """`pub_date`, ISO 8601 with an offset, as RFC 3339 UTC.

    A date this adapter cannot read is an absent date, never today's.
    """
    if not isinstance(job, dict):
        return None
    raw = _text(job.get("pub_date"))
    if raw is None:
        return None
    from datetime import UTC, datetime

    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def to_stub(job: Any) -> PostingStub | None:
    """One feed row into a stub, or None when it cannot be addressed."""
    if not isinstance(job, dict):
        return None

    title = _text(job.get("title"))
    url = canonical_url(job)
    identity = external_id(job)
    if title is None or url is None or identity is None:
        return None

    return PostingStub(
        external_id=identity,
        title=title,
        url=url,
        # Stored and SHOWN, never read as a hiring scope. See the module
        # docstring and `publishes_hiring_scope`.
        location_raw=_text(job.get("location")),
        department=None,
        posted_at=_posted_at(job),
        description_html=_text(job.get("description")),
        payload=dict(job),
    )


@dataclass(frozen=True)
class FeedRead:
    """One read of the window, and what it could not address."""

    jobs: tuple[Any, ...]
    unaddressable: int = 0

    @property
    def empty(self) -> bool:
        return not self.jobs


class WorkingNomadsProvider(JobProvider):
    """The Working Nomads public job feed."""

    name = "workingnomads"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    #: The feed is one stream. Asking this adapter about one employer is a
    #: question in the wrong vocabulary, as for WWR and Himalayas.
    addresses_boards_by_company = False
    capabilities = WORKINGNOMADS_CAPABILITIES
    field_map = WORKINGNOMADS_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, api_host: str = API_HOST) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def feed_url(self) -> str:
        """The window. No page parameter, because there is no pagination.

        Measured 2026-09-07: the bare path and `?limit=200` return the same 32
        postings. Building a page parameter that the vendor ignores would make
        a walk look bounded when it is simply short.
        """
        url = f"{self._api_host}{FEED_PATH}"

        from urllib.parse import urlsplit

        parts = urlsplit(url)
        if parts.scheme != "https" or parts.hostname != TRUSTED_HOST:
            raise WorkingNomadsError(f"untrusted Working Nomads host in {url}")
        return url

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this up by hand."""
        del board
        return f"{self._api_host}/jobs"

    # -- reading -----------------------------------------------------------

    def read_feed(self, *, use_cache: bool = True) -> FeedRead:
        """The window, once.

        A failed request raises. It is never converted into an empty feed: an
        empty window and a broken request are different outcomes, and
        conflating them lets a network blip close a company's whole posting
        history.
        """
        body = self._fetcher.get_json(self.feed_url(), use_cache=use_cache)
        if not isinstance(body, list):
            shape = type(body).__name__
            raise WorkingNomadsError(
                f"Working Nomads returned an unexpected shape: expected a list, got {shape}"
            )
        jobs = tuple(body)
        return FeedRead(
            jobs=jobs,
            unaddressable=sum(1 for job in jobs if to_stub(job) is None),
        )

    # -- the protocol ------------------------------------------------------

    def validate_board(self, board: BoardRef) -> str | None:
        """This source is ONE STREAM. It cannot be asked about an employer.

        `list_postings` here does `del board` and reads the whole feed, so a
        `source_board` row per employer would fetch the entire feed once per
        employer. Measured on the real corpus 2026-09-08: 11 rows carry
        this provider with a company slug in `board_identifier`, recorded as
        PROVENANCE by the dedicated collector and read as a FETCH TARGET by the
        generic one. That is 11 identical full-feed reads against somebody
        else's server for one run.

        `addresses_boards_by_company` has said False since this adapter was
        written. This is that declaration made answerable before the run.
        """
        identifier = (board.board_identifier or "").strip()
        if identifier in ("", "all"):
            return None
        return (
            f"{identifier!r} is not something Working Nomads can be asked for: this source is one "
            f"feed, not a board per employer. Collect it with `career-agent collect-workingnomads`."
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        del board
        for job in self.read_feed().jobs:
            stub = to_stub(job)
            if stub is not None:
                yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the feed gave it, body included. No second request."""
        del board
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html) if html else "",
            payload=dict(stub.payload),
        )

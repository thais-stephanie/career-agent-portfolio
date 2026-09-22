"""Legacy Workable API protocol and shared record helpers.

Production uses WorkableXmlProvider from workable_xml.py. This API adapter is
retained for offline regression tests of identity, sections and page-token traps;
no production command constructs it. Workable Support supplied the replacement
public XML endpoint on 2026-09-10. Do not retry the old rate-limited API.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.timestamps import to_rfc3339_utc
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

PROVIDER = "workable"

HOST = "jobs.workable.com"
API_URL = f"https://{HOST}/api/v1/jobs"

#: The vendor's cap, measured: `limit=100` answers
#: `400 {"limit":"Must be less than or equal to 20"}`.
PER_PAGE = 20

#: The REQUEST parameter. The response calls the same value `nextPageToken`,
#: and sending it under that name returns page one with HTTP 200 forever.
PAGE_TOKEN_PARAM = "pageToken"

#: A page token is a value out of a response and therefore untrusted input.
#: It is base64url in every sample; anything else is refused rather than
#: pasted into a URL.
_TOKEN = re.compile(r"^[A-Za-z0-9_=+/-]{1,4096}$")


class WorkableError(ValueError):
    """This adapter refused to do something, and says which."""


WORKABLE_FIELD_MAP = ProviderFieldMap(
    mappings=(
        #: `on_site` folds to `onsite` through `normalise_work_model`'s key
        #: function, which strips non-alphanumerics. Measured across 500 rows:
        #: on_site 302, remote 119, hybrid 79, and no fourth value.
        FieldMapping(path="workplace", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="employmentType", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
        #: `location` is DELIBERATELY UNMAPPED. It is where the work is, which
        #: is not where the employer may hire, and reading the first as the
        #: second is the conflation invariant 3 forbids.
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise WorkableError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def company_of(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    return _text(record.get("company"), "title")


def company_website(record: Any) -> str | None:
    """The employer's own site.

    Carried because it is the closest thing to an origin pointer this feed
    publishes, and NOT used as an apply target: it is a marketing homepage, and
    sending somebody to a homepage instead of to the advert they were reading
    is worse than useless.
    """
    if not isinstance(record, dict):
        return None
    return _text(record.get("company"), "website")


def public_url(record: Any) -> str | None:
    """The vendor's own posting page, which is the only Apply target here."""
    url = _text(record, "url")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in {
        HOST,
        "apply.workable.com",
    }:
        return None
    return url


def location_of(record: Any) -> str | None:
    """`city, subregion, countryName`, composed, or None.

    The three parts are joined rather than one of them chosen because
    `match/places.py` resolves a composed string better than a bare city: `CA`
    is California AND Canada, and `San Jose` is in four countries. Duplicates
    are dropped -- a city-state writes the same word twice -- so
    `Singapore, Singapore` does not reach the resolver as a pair.
    """
    if not isinstance(record, dict):
        return None
    locations = record.get("xml_locations")
    if isinstance(locations, list) and locations:
        rendered = [location_of({"location": place}) for place in locations]
        return " / ".join(dict.fromkeys(value for value in rendered if value)) or None
    place = record.get("location")
    if not isinstance(place, dict):
        return None
    parts: list[str] = []
    for key in ("city", "subregion", "countryName"):
        value = place.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    return ", ".join(parts) or None


def posted_at(record: Any) -> str | None:
    """`created`, never `updated`.

    "Since you last looked" asks when a posting FIRST appeared, and an employer
    correcting a typo does not make a job new again.
    """
    if not isinstance(record, dict):
        return None
    return to_rfc3339_utc(record.get("created"))


#: The parts of the advert, in the order a reader meets them.
#:
#: `company.description` is NOT among them, and that is the Get on Board
#: correction applied in advance: it is the employer's marketing about itself,
#: it is identical on every posting that employer publishes, and folding it in
#: makes every advert longer, more similar and no more informative. It stays in
#: the archived payload.
_SECTIONS = ("description", "requirementsSection", "benefitsSection")


def compose_description(record: Any) -> str:
    """The whole advert, reassembled from the fields it arrives split across.

    Reading only the key literally named `description` returns roughly the
    first half of the posting and looks exactly like a complete one. Get on
    Board shipped that way for a whole milestone and 409 postings were stored
    as METADATA_ONLY because of it.
    """
    if not isinstance(record, dict):
        return ""
    parts = [value for key in _SECTIONS if (value := _text(record, key))]
    return "\n\n".join(parts)


def to_stub(record: Any) -> PostingStub | None:
    """One search result as a stub, or None when it cannot be addressed."""
    if not isinstance(record, dict):
        return None
    external = _text(record, "id")
    title = _text(record, "title")
    url = public_url(record)
    if not external or not title or not url:
        return None
    return PostingStub(
        external_id=f"workable-{external}",
        title=title,
        url=url,
        location_raw=location_of(record),
        department=_text(record, "department"),
        posted_at=posted_at(record),
        description_html=compose_description(record) or None,
        payload=dict(record),
    )


@dataclass(frozen=True)
class FeedPage:
    """One page of the index, handed over before the next one is asked for.

    THE UNIT OF WORK, and the reason it exists. A walk that returns everything
    at the end is a walk that returns NOTHING when its last request fails: the
    first production run of this source read for 404 seconds, met an HTTP 429
    on a later page, and stored zero postings. Gupy learned the same lesson at
    a larger scale earlier the same day.
    """

    records: tuple[dict[str, Any], ...]
    page: int
    claimed_total: int | None


@dataclass(frozen=True)
class FeedRead:
    """One bounded walk, and every bound it ran under."""

    records: tuple[dict[str, Any], ...]
    pages: int
    stopped_early: bool
    #: The walk stopped because a page added no posting it had not already
    #: seen. Kept apart from the page budget because they mean opposite things:
    #: one is this product deciding to stop, the other is the feed repeating
    #: itself, which is what a mis-sent page token looks like from here.
    repeated_itself: bool
    claimed_total: int | None


class WorkableProvider(JobProvider):
    """One search index over every public Workable board."""

    name = PROVIDER
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    #: An aggregator has no board per employer. See `providers/speedrun.py`,
    #: where inheriting the default True made discovery report two fictional
    #: boards as supported.
    addresses_boards_by_company = False
    field_map = WORKABLE_FIELD_MAP
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        #: No salary field anywhere in the record.
        exposes_compensation=False,
        exposes_location_structured=True,
        exposes_remote_flag=True,
        exposes_employment_type=True,
        #: FALSE. `location` is where the work is. See the module docstring.
        publishes_hiring_scope=False,
    )

    def __init__(
        self,
        fetcher: HttpFetcher,
        api_url: str = API_URL,
        max_pages: int | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._api_url = assert_trusted(api_url)
        #: A bound with no way to say "unlimited". 169,980 postings at twenty a
        #: page is 8,499 requests, and a walk that can be asked for everything
        #: is a walk somebody will ask for everything.
        #:
        #: The vendor has an opinion too: 500 pages in one run answered **HTTP
        #: 429** on 2026-09-09. That is a rate limit, and this project does not
        #: work around one -- it walks less at a time and comes back.
        self._max_pages = 25 if max_pages is None else max(1, int(max_pages))
        #: Left behind by `walk`, which is a generator and cannot return them.
        self.pages_read = 0
        self.stopped_early = False
        self.repeated_itself = False

    def page_url(
        self,
        *,
        token: str | None = None,
        query: str | None = None,
        day_range: int | None = None,
    ) -> str:
        """One page of the index.

        No query by default, and the absence is the design. A search API is the
        easiest place in this system to encode one person's preferences without
        noticing, and a collector built around one job title would decide at the
        ingestion layer what work exists.
        """
        url = f"{self._api_url}?limit={PER_PAGE}"
        if query:
            url = f"{url}&query={quote(query, safe='')}"
        if day_range is not None:
            if day_range < 1:
                raise WorkableError(f"day_range must be at least 1, got {day_range}")
            url = f"{url}&day_range={int(day_range)}"
        if token is not None:
            if not _TOKEN.match(token):
                raise WorkableError(
                    "the feed returned a page token this adapter will not put in a URL. "
                    "A token from a response is untrusted input."
                )
            url = f"{url}&{PAGE_TOKEN_PARAM}={quote(token, safe='')}"
        return assert_trusted(url)

    def walk(
        self,
        *,
        query: str | None = None,
        day_range: int | None = None,
        use_cache: bool = True,
    ) -> Iterator[FeedPage]:
        """The index, one page at a time, to the page budget or to the end.

        A GENERATOR, so the caller can persist a page before the next request
        is made. That is not a style preference: the first production run of
        this source read for 404 seconds, met an HTTP 429 on a later page, and
        stored ZERO postings, because everything was held until the end.

        A failed request still RAISES, and it now raises AFTER the caller has
        kept what came before it. An empty feed and a broken request remain
        different outcomes -- conflating them is how a network blip closes a
        company's whole posting history -- and a broken request on page four no
        longer throws away pages one to three.

        `pages_read`, `stopped_early` and `repeated_itself` are left on the
        instance, because a generator cannot return them and the caller needs
        them whether the walk finished or raised.
        """
        seen: set[str] = set()
        token: str | None = None
        claimed: int | None = None
        self.pages_read = 0
        self.stopped_early = False
        self.repeated_itself = False

        while self.pages_read < self._max_pages:
            url = self.page_url(token=token, query=query, day_range=day_range)
            body = self._fetcher.get_json(url, use_cache=use_cache)
            if not isinstance(body, dict):
                raise WorkableError(f"{url} returned {type(body).__name__}, not an object")
            if claimed is None:
                total = body.get("totalSize")
                if isinstance(total, int) and not isinstance(total, bool):
                    claimed = total

            rows = body.get("jobs")
            entries = (
                [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
            )
            self.pages_read += 1
            if not entries:
                return

            fresh: list[dict[str, Any]] = []
            for entry in entries:
                external = _text(entry, "id")
                if not external or external in seen:
                    continue
                seen.add(external)
                fresh.append(dict(entry))

            if not fresh:
                # THE MIS-SENT TOKEN, CAUGHT. A page of twenty perfectly good
                # postings this walk already holds is what page one looks like
                # when the token did not take, and the vendor answers 200 the
                # whole way. Walking on would burn the budget re-reading it.
                self.repeated_itself = True
                return

            yield FeedPage(records=tuple(fresh), page=self.pages_read, claimed_total=claimed)

            next_token = body.get("nextPageToken")
            if not isinstance(next_token, str) or not next_token:
                return
            token = next_token

        self.stopped_early = token is not None

    def read_feed(
        self,
        *,
        query: str | None = None,
        day_range: int | None = None,
        use_cache: bool = True,
    ) -> FeedRead:
        """Every page at once, for a caller with no reason to stream.

        Kept because a caller wanting the whole window in memory is a
        legitimate thing to be. The COLLECTOR does not use it; see `walk`.
        """
        collected: list[dict[str, Any]] = []
        claimed: int | None = None
        for page in self.walk(query=query, day_range=day_range, use_cache=use_cache):
            collected.extend(page.records)
            if claimed is None:
                claimed = page.claimed_total
        return FeedRead(
            records=tuple(collected),
            pages=self.pages_read,
            stopped_early=self.stopped_early,
            repeated_itself=self.repeated_itself,
            claimed_total=claimed,
        )

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        del board
        return f"https://{HOST}/"

    def validate_board(self, board: BoardRef) -> str | None:
        """One index, no per-employer endpoint. Nothing to check."""
        del board
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise WorkableError(
            "Workable's job board is one index, not a board per employer. "
            "Use read_feed(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. The whole advert was already in the search result."""
        del board
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=strip_html(html),
            payload=dict(stub.payload),
        )


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    """The advert as text, with paragraph breaks kept.

    The same reduction every sibling adapter performs. A requirements list
    collapsed onto one line makes the employer's sentences harder to read than
    the employer wrote them, and ADR-0002 verifies every evidence quote against
    exactly this text.
    """
    if not html:
        return ""
    text = re.sub(r"(?i)<br\s*/?>", "\n", html)
    text = re.sub(r"(?i)</(p|div|li|ul|ol|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "\n", text)
    text = _TAG.sub("", text)
    for entity, char in (
        ("&nbsp;", " "),
        ("&amp;", "&"),
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
    ):
        text = text.replace(entity, char)
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _BLANKS.sub("\n\n", text).strip()


#: The two public posting shapes: the short form the XML feed publishes
#: (`apply.workable.com/j/<CODE>`) and the account form the site links
#: (`apply.workable.com/<account>/j/<CODE>/`, with or without `/apply/`).
#: `jobs.workable.com/view/...` carries a different id and is NOT claimed.
_POSTING_URL = re.compile(
    r"^https?://apply\.workable\.com/(?:(?P<account>[A-Za-z0-9_-]+)/)?j/"
    r"(?P<code>[A-Z0-9]{6,})/?(?:apply/?)?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The stored `external_id` for a public Workable posting URL, or None.

    ADR-0013 identity for the largest provider in the corpus, which had none:
    a WWR row and its Workable original sat as two jobs because nothing
    recognised `apply.workable.com/<account>/j/<CODE>/`. The shortcode is the
    id the feed's `referencenumber` carries and `to_stub` stores as
    `workable-<CODE>`, so the URL resolves to the row already here.
    """
    match = _POSTING_URL.match(url.strip())
    return f"workable-{match.group('code').upper()}" if match else None


def recognise_board_url(url: str) -> tuple[str, str | None] | None:
    """`(account, external_id)` for the account form of a posting URL.

    The account names the employer's Workable presence, but Workable is not
    collected board by board -- the whole feed is (ADR-0021) -- so a caller
    discovering boards reads this as "already covered by the feed" rather
    than as a board to register. The short form names no account and returns
    None.
    """
    match = _POSTING_URL.match(url.strip())
    if match is None or not match.group("account"):
        return None
    return match.group("account").lower(), f"workable-{match.group('code').upper()}"

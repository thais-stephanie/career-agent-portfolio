"""Avlis Talent, a Brazilian agency placing engineers at United States startups.

WHY IT IS BUILT, HAVING BEEN MEASURED AND DECLINED HOURS EARLIER
-----------------------------------------------------------------
Its catalogue row said, on the same day: permitted, trivially readable, and NOT
BUILT ON VOLUME -- six postings against a corpus of a hundred thousand.

That refusal was the same mistake this whole session exists to correct, in a
smaller costume. "Six is not worth an adapter" is a judgement about whether the
work is worth SHOWING, and ADR-0019 says that judgement belongs to the filter
layer rather than to a collector. Six postings at United States startups, open
to Brazilians, is precisely the market one of the three people who will use
this product is looking at -- and a corpus that leaves them out because they
are few is a corpus deciding for her.

AUTHORISATION, READ FIRST-PARTY 2026-09-09
--------------------------------------------
`avlistalent.com/robots.txt` is `User-agent: *` with `Allow: /` and then names
the jobs path itself:

    Allow: /
    Allow: /hire
    Allow: /contact
    Allow: /recruiting
    Allow: /jobs

    Disallow: /internal
    Disallow: /admin/
    Disallow: /mkt
    Disallow: /scorecard

No AI crawler is named. `/api/jobs` is not among the exclusions and the four
that are named are an admin area, an internal area, a marketing area and a
scorecard -- none of which this adapter would ever build a URL for.

WHAT ONE RECORD CARRIES, AND THE THREE THINGS IT DOES NOT
-----------------------------------------------------------
`id`, `title`, `department`, `location`, `type`, `description` in full,
`salary`, `skills`, `createdAt`, `active`.

**There is no per-posting URL, and that is measured rather than assumed.**
`/jobs/26`, `/job/26`, `/jobs?id=26` and `/jobs#26` all return the same 5,932
byte application shell. The board has ONE page, so `job.url` is that page for
every posting here. The external id stays unique per posting, so identity is
intact; what is lost is the ability to link somebody straight to the advert,
and pretending otherwise by inventing a fragment would send them to a page that
does not read it.

**`location` is `Remote (US Timezone)` on every row**, which is a work model
and a TIMEZONE. Invariant 3 keeps those apart from geography, so it maps to no
dimension at all: `publishes_hiring_scope` is False and `exposes_remote_flag`
is False. The word `Remote` still reaches the matcher through `location_raw`,
the way it does for any board that writes it in free text.

**`salary` is free text** -- `$75-$100k` on one row and `US$75k-US$90k` on
another. There is no currency field and no period field, so
`exposes_compensation` is False and the string stays in the archived payload
where a later decision can reach it. Reading a number whose unit was inferred
is worse than reading no number, which is the rule Remote OK's row already
records.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

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

PROVIDER = "avlis"

HOST = "avlistalent.com"
API_URL = f"https://{HOST}/api/jobs"

#: The one page every posting here links to. There is no per-posting URL; see
#: the module docstring, where the four paths that were tried are named.
BOARD_URL = f"https://{HOST}/jobs"

#: The employer of record for every posting on this board. It is an agency
#: placing people at its clients, and the clients are not named in the feed --
#: so the company stored is the agency, which is who a person applies to.
AGENCY = "Avlis Talent"


class AvlisError(ValueError):
    """This adapter refused to do something, and says which."""


AVLIS_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
        #: `location` is DELIBERATELY UNMAPPED, and it is the one field somebody
        #: would map by reflex. `Remote (US Timezone)` is a work model and a
        #: TIMEZONE; invariant 3 keeps a timezone away from geography, and the
        #: string does not normalise to a work model either.
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise AvlisError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    return value.strip() or None


def is_open(record: Any) -> bool:
    """Whether the agency still lists this posting.

    `active` is a real field and every row carried True when this was measured.
    A row that says False is not collected: an agency saying a role is closed is
    the employer speaking, which is different from a posting simply vanishing
    from a feed.
    """
    if not isinstance(record, dict):
        return False
    return record.get("active") is not False


def posted_at(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    return to_rfc3339_utc(record.get("createdAt"))


def to_stub(record: Any) -> PostingStub | None:
    """One row of the feed as a stub, or None when it cannot be addressed."""
    if not isinstance(record, dict):
        return None
    identifier = record.get("id")
    title = _text(record, "title")
    if isinstance(identifier, bool) or not isinstance(identifier, int) or not title:
        return None
    return PostingStub(
        external_id=f"{PROVIDER}-{identifier}",
        title=title,
        #: THE BOARD PAGE, for every posting. There is no per-posting URL.
        url=BOARD_URL,
        #: `Remote (US Timezone)`. A work model and a timezone, never a place,
        #: and the matcher reads the word the way it reads any free-text one.
        location_raw=_text(record, "location"),
        department=_text(record, "department"),
        posted_at=posted_at(record),
        description_html=_text(record, "description"),
        payload=dict(record),
    )


@dataclass(frozen=True)
class FeedRead:
    """One read of the feed, and what it held."""

    records: tuple[dict[str, Any], ...]
    #: Rows the agency marks as no longer open. Counted rather than silently
    #: filtered, because "the feed shrank" and "the agency closed a role" are
    #: different facts and only one of them is the agency speaking.
    closed: int


class AvlisProvider(JobProvider):
    """One agency, one feed, no board parameter."""

    name = PROVIDER
    #: An ATS rather than an aggregator: the agency wrote these postings in its
    #: own system and this feed is that system's own record, so
    #: `access_method` reporting `ats_structured` is accurate.
    kind = ProviderKind.ATS
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    #: FALSE. One agency with one feed. Asking this adapter about an employer
    #: is a question in the wrong vocabulary, and `pipeline.collect` reads this
    #: to keep the row out of its plan.
    addresses_boards_by_company = False
    field_map = AVLIS_FIELD_MAP
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=True,
        #: FALSE. `salary` is free text with no currency and no period.
        exposes_compensation=False,
        exposes_location_structured=False,
        #: FALSE. There is no work-model field; `location` is prose.
        exposes_remote_flag=False,
        exposes_employment_type=True,
        #: FALSE. `Remote (US Timezone)` is where the work happens and when,
        #: not where this agency may hire.
        publishes_hiring_scope=False,
    )

    def __init__(self, fetcher: HttpFetcher, api_url: str = API_URL) -> None:
        self._fetcher = fetcher
        self._api_url = assert_trusted(api_url)

    def read_feed(self, *, use_cache: bool = True) -> FeedRead:
        """The whole board, in one request. There is no pagination to bound."""
        body = self._fetcher.get_json(self._api_url, use_cache=use_cache)
        if not isinstance(body, list):
            raise AvlisError(f"{self._api_url} returned {type(body).__name__}, not a list")
        rows = [row for row in body if isinstance(row, dict)]
        return FeedRead(
            records=tuple(dict(row) for row in rows if is_open(row)),
            closed=sum(1 for row in rows if not is_open(row)),
        )

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        del board
        return BOARD_URL

    def validate_board(self, board: BoardRef) -> str | None:
        del board
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise AvlisError(
            "Avlis is one agency with one feed, not a board per employer. "
            "Use read_feed(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. The whole advert was already in the feed."""
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

    This feed writes MARKDOWN rather than HTML -- `* bullet` lines and real
    newlines -- so the tag stripping below finds almost nothing to do and the
    whitespace handling is what matters. Kept identical to its siblings anyway:
    a source that starts sending HTML tomorrow should not need this rewritten,
    and ADR-0002 verifies every evidence quote against exactly this text.
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

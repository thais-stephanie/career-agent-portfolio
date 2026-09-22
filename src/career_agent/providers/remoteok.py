"""Remote OK, read from the JSON feed it publishes with its terms attached.

WHY IT IS WORTH BUILDING, AND WHY IT WAS NOT BUILT BEFORE
----------------------------------------------------------
It was refused in V3 for one reason, recorded in
`docs/product/source-expansion-v4.md`: it publishes **no origin pointer**. All
100 `apply_url` values in the sampled feed point at `remoteok.com`, and
`original` is a boolean rather than a link, so the deterministic identity in
ADR-0013 has nothing to resolve against and a posting also carried by an
employer's own Greenhouse board becomes two rows.

That reasoning was right about the mechanism and wrong about the conclusion,
and Himalayas is why. It shipped on 2026-09-07 with **exactly the same gap** --
`applicationLink` byte-identical to `guid` on all 20 sampled rows -- under a
trade written into its collector:

    Two visible rows for one job is a smaller harm than one invisible job.

That is the trade ADR-0008 already made when it forbade fuzzy matching, and it
applies here unchanged. Rules 1, 3 and 4 remain: an id another provider already
holds, the same canonical URL, and a byte-identical description from the same
employer for the same role. Rule 4 works precisely BECAUSE this feed carries
full bodies.

WHAT IT ADDS THAT THIS CORPUS LACKS
-------------------------------------
Not more engineering roles. The captured feed carries
`Roupeiro Muro Alto PE` at a resort in Ipojuca, Pernambuco, tagged
`non tech` -- a Brazilian hospitality job -- beside
`Customer Support & Success Specialist`. A corpus meant to serve a lawyer
moving into an executive assistant role, somebody starting in sales, and a
customer success manager in Utah needs exactly this and has almost none of it.

AUTHORISATION, AND A DIRECTIVE THAT NAMES THE AGENT WRITING THIS
-----------------------------------------------------------------
`remoteok.com/robots.txt`, read first-party 2026-09-09:

* the generic group is `User-agent: *` with `Allow: /`, `Crawl-delay: 1` and
  `Content-Signal: search=yes,ai-train=no,use=reference`. This product builds
  no search index for third parties, trains nothing, and uses postings by
  reference. That is a permitted reader.
* **`User-agent: ClaudeBot` carries `Disallow: /`.**

The agent that wrote this file IS ClaudeBot, and the collector is not. That is
the Get on Board situation exactly, and it is handled the same way: **zero live
requests were made from this repository.** The fixture this adapter is built
and tested against was captured on 2026-09-05, before this work, and the first
live call belongs to the owner running `collect-remoteok`.

The `robots.txt` above was fetched to discover the directive and nothing else
was requested afterwards. Reading the file that states a policy is how the
policy is discovered; acting past it is what the directive forbids.

THE API'S OWN TERMS, WHICH ARE A GRANT WITH THREE CONDITIONS
--------------------------------------------------------------
The feed's first element is not a posting. It is a licence, and the vendor
repeats it in every response:

    "Please link back (with follow, and without nofollow!) to the URL on
     Remote OK and mention Remote OK as a source, so we get traffic back from
     your site. If you do not we'll have to suspend API access."

Implemented rather than promised, the same way Jobicy's three conditions are:

1. **Apply goes to the Remote OK URL.** `job.url` is the feed's own `url` and
   this adapter produces no other target, so the link back is structural
   rather than a habit somebody could change.
2. **Remote OK is credited** on the posting, through the source it is stored
   under and the catalogue row that names it.
3. **The logo is not used.** `company_logo` and `logo` are archived in the
   payload and read by nothing.

WHAT THIS ADAPTER REFUSES TO READ
-----------------------------------
`salary_min` and `salary_max` arrive as bare integers with **no currency
field anywhere in the record**. They are almost certainly US dollars and this
adapter does not say so: `min_salary` filtering compares within one currency
and nothing here converts between them, so a number with an invented unit is
worse than no number. The values stay in the archived payload, where a later
decision can reach them, and `exposes_compensation` is False.

`location` is where the work is, when it says anything at all -- `Ipojuca, `
on one row and empty on two others. It is NOT a hiring scope and
`publishes_hiring_scope` is False.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)

PROVIDER = "remoteok"

HOST = "remoteok.com"
API_URL = f"https://{HOST}/api"


class RemoteOkError(ValueError):
    """This adapter refused to do something, and says which."""


#: Deliberately empty, and that is a statement rather than an oversight.
#:
#: `location` is free text about where the work is, `tags` are the board's own
#: categories, and `original` says whether the posting started here. None of
#: them is a work model, an employment type or a hiring scope, and declaring
#: one as such would be inventing a dimension the vendor does not answer.
REMOTEOK_FIELD_MAP = ProviderFieldMap(mappings=())


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise RemoteOkError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip().strip(",").strip()
    return stripped or None


def is_posting(record: Any) -> bool:
    """Whether this element is a job rather than the licence.

    **The first element of every response is the terms of service**, carrying
    `legal` and `last_updated` and no `id`. A reader that took element zero as
    a posting would file the licence text as a job advert, which is both wrong
    and a good way to never notice the terms exist.
    """
    return isinstance(record, dict) and "legal" not in record and record.get("id") is not None


def licence_text(body: Any) -> str | None:
    """The vendor's terms, as the vendor stated them in this response.

    Returned so a caller can record what it agreed to on the day it collected,
    rather than trusting a sentence copied into a docstring months earlier.
    """
    if isinstance(body, list) and body and isinstance(body[0], dict):
        legal = body[0].get("legal")
        if isinstance(legal, str) and legal.strip():
            return legal.strip()
    return None


def company_of(record: Any) -> str | None:
    return _text(record, "company")


def public_url(record: Any) -> str | None:
    """The Remote OK posting page, which the licence requires as the link back.

    The ONLY Apply target this adapter produces. Anything off the vendor's host
    is refused rather than followed: a feed that started pointing elsewhere
    would take the link back with it, and the link back is half the licence.
    """
    url = _text(record, "url")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() != HOST:
        return None
    return url


def _external_id(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get("id")
    if isinstance(value, int):
        return f"remoteok-{value}"
    if isinstance(value, str) and value.strip():
        return f"remoteok-{value.strip()}"
    return None


def to_stub(record: Any) -> PostingStub | None:
    """One feed record as a stub, or None when it cannot be addressed."""
    if not is_posting(record):
        return None
    external_id = _external_id(record)
    title = _text(record, "position")
    url = public_url(record)
    if not external_id or not title or not url:
        return None
    return PostingStub(
        external_id=external_id,
        title=title,
        url=url,
        #: Empty on most rows, and empty is stored as nothing rather than as
        #: `Remote`. A remote posting with no stated place resolves to no
        #: country, which is the honest answer and the one invariant 3 wants.
        location_raw=_text(record, "location"),
        department=None,
        posted_at=to_rfc3339_utc(_text(record, "date")),
        description_html=_text(record, "description"),
        payload=dict(record) if isinstance(record, dict) else {},
    )


@dataclass(frozen=True)
class FeedRead:
    """One window of the feed, and the licence it was served under."""

    records: tuple[dict[str, Any], ...]
    licence: str | None
    #: What the response carried before postings were separated from the
    #: licence row. Recorded so "the feed shrank" and "we filtered more" stay
    #: answerable apart.
    elements: int


class RemoteOkProvider(JobProvider):
    """One request, one window. There is no paging and none is invented."""

    name = PROVIDER
    #: It republishes work advertised elsewhere and composes its own posting
    #: page. `original` is True on a minority of rows and is archived rather
    #: than read: a boolean is not a provenance chain.
    kind = ProviderKind.AGGREGATOR
    #: FALSE. An AGGREGATOR republishes postings that originate elsewhere and
    #: has no board per employer by construction. Left inherited, the default
    #: True makes `board_providers()` offer this feed to `discover`, which then
    #: asks it whether a named company has a board here -- and a feed filtered
    #: by an identifier nothing matches answers with an empty list rather than
    #: an error, which discovery reads as a real board with no openings today.
    addresses_boards_by_company = False
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    field_map = REMOTEOK_FIELD_MAP
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=False,
        #: FALSE. `salary_min`/`salary_max` are bare integers with no currency
        #: field anywhere in the record. See the module docstring: a number
        #: with an invented unit is worse than no number.
        exposes_compensation=False,
        exposes_location_structured=False,
        exposes_remote_flag=False,
        exposes_employment_type=False,
        #: FALSE. `location` is where the work is, when it says anything.
        publishes_hiring_scope=False,
    )

    def __init__(self, fetcher: HttpFetcher, api_url: str = API_URL) -> None:
        self._fetcher = fetcher
        self._api_url = assert_trusted(api_url)

    def feed_url(self) -> str:
        return self._api_url

    def read_feed(self, *, use_cache: bool = True) -> FeedRead:
        body = self._fetcher.get_json(self.feed_url(), use_cache=use_cache)
        if not isinstance(body, list):
            raise RemoteOkError(
                f"{self.feed_url()} returned {type(body).__name__}, not a list of postings"
            )
        return FeedRead(
            records=tuple(record for record in body if is_posting(record)),
            licence=licence_text(body),
            elements=len(body),
        )

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return f"https://{HOST}/"

    def validate_board(self, board: BoardRef) -> str | None:
        """One stream, no per-employer endpoint. Nothing to check."""
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise RemoteOkError(
            "Remote OK is one feed, not a board per employer. Use read_feed(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. The advert was already in the feed."""
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

    The same reduction the Gupy and Programathor adapters perform, and for the
    same reason: a requirements list collapsed onto one line makes the
    employer's own sentences harder to read than the employer wrote them, and
    ADR-0002 verifies evidence quotes against exactly this text.
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

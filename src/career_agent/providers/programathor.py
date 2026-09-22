"""Programathor, read from the structured data it publishes for machines.

WHY IT IS WORTH THE REQUESTS
-----------------------------
It is a Brazilian developer board, and it publishes **schema.org `JobPosting`**
on every posting page. That is not scraped HTML: it is a contract a site fills
in deliberately so that search engines can read it, and it carries fields this
corpus is starved of.

Measured 2026-09-09 on live postings:

* `baseSalary` -- `{"currency": "BRL", "value": 18000, "unitText": "MONTH"}`.
  A real band with a CURRENCY and a PERIOD, which `min_salary` filtering has
  always required and which almost nothing here states.
* `employmentType` -- `CONTRACTOR` on a Brazilian board is **PJ**, and
  `FULL_TIME` is the CLT-shaped answer. This corpus has never held a
  structured Brazilian contract signal; `contract_regime` is NULL on every
  scored row and a 21,252-description census found the Portuguese and English
  prose forms zero times.
* `jobLocation.address.addressCountry` -- a bare `BR`. An ISO code, not a
  string to be parsed.
* `validThrough` -- a real expiry date, so a closed posting says so.
* `hiringOrganization.sameAs` -- the employer's own domain.

AUTHORISATION, READ FIRST-PARTY 2026-09-09
-------------------------------------------
`programathor.com.br/robots.txt` answers 200 with one `User-agent: *` group
disallowing exactly four paths: `/admin/`, `/user/`, `/users/`, `/company/`.
Neither `/jobs` nor a posting page is among them. The same file **declares a
sitemap**, which is an affirmative aid to a crawler rather than the mere
absence of a ban.

Nothing was bypassed and no access control was met. This sits comfortably
inside the collection policy adopted 2026-09-09 (ADR-0018), and would have
sat inside a stricter one.

THE COST, MEASURED RATHER THAN HOPED
-------------------------------------
This is the expensive source in the corpus and the numbers should be in front
of whoever raises its page budget.

* **One request per posting.** The listing carries no description, so
  `full_description_in_list` is False and a detail fetch is required. The
  listing itself yields 15 postings per request.
* **A material fraction of the posting pages answer HTTP 500**, and it is
  deterministic per URL: four consecutive requests for one URL returned 500
  four times, so it is neither a rate limit nor transient. The RATE was
  measured three times and moved each time -- 3 of 6, then 12 of 15, then
  **106 of 112** over eight listing pages. The largest sample is the one to
  believe and the first was a hand-rolled probe rather than this adapter, so
  the working figure is that about one posting in twenty cannot be opened.
  It is reported per run rather than assumed, because a rate that has moved
  this much is a rate that will move again.

**AND A REFUSED POSTING IS RETRIED**, which is the part that is easy to miss.
`HttpFetcher` retries a 5xx, correctly for a transient failure and exactly
wrong for a deterministic one, so a posting this vendor will never render costs
THREE requests rather than one. Measured against the captured listing: fifteen
postings, five servable, and the walk makes 36 requests rather than the 16 a
reading of the paragraph above would predict.

So a listing page costs about 17 requests and yields about 13 usable
postings. Gupy yields 100 complete postings per request. That is still two
orders of magnitude, and it is why the default budget here is two pages. What
buys it back is the salary band and the contract signal, which Gupy does not
carry at all: over eight pages, 28 of 106 postings stated pay with both a
currency and a period.

The failures are COUNTED rather than swallowed. A source whose error rate
climbed to 100% would otherwise look exactly like a source with nothing new.

WHAT THIS ADAPTER DOES NOT CLAIM
---------------------------------
`publishes_hiring_scope` is False. `addressCountry: BR` is where the work is,
the same reading `providers/gupy.py` argues at length. There is no
`jobLocationType` in the measured records, so a posting's remoteness is not
asserted from a field that is not there.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import (
    BoardRef,
    CompensationHint,
    FieldMapping,
    JobProvider,
    PostingStub,
    ProviderCapabilities,
    ProviderFieldMap,
    ProviderKind,
    RawPosting,
    RetrievalMode,
)
from career_agent.providers.feeds import FeedPage, PageBudget, walk_feed

PROVIDER = "programathor"

HOST = "programathor.com.br"
SITE = f"https://{HOST}"
LISTING = f"{SITE}/jobs"

#: What one listing page yields. Not configurable: it is the vendor's layout.
PER_PAGE = 15

#: `/jobs/33685-desenvolvedor-back-end-senior-ai-engineer`. The numeric prefix
#: is the identifier; the slug is decoration and changes when a title is
#: edited, so it is never part of the id.
JOB_PATH = re.compile(r"^/jobs/(\d+)(?:-[^/?#]*)?$")

_LD_JSON = re.compile(
    r"<script[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.IGNORECASE | re.DOTALL,
)


class ProgramathorError(ValueError):
    """This adapter refused to do something, and says which."""


#: Exactly one mapping, and the ABSENCE of a second one is the decision.
#:
#: `jobLocation.address.addressCountry` is a clean ISO code and there is no
#: dimension it may travel in. The only location dimension this product has is
#: `HIRING_LOCATION_HINT`, which means "the employer's answer to where it may
#: hire", and `BR` here means "the work is in Brazil". Declaring it would make
#: the eligibility gate read a place of work as a hiring scope for every
#: Brazilian posting on this board, which is invariant 3 broken at the widest
#: possible blast radius. It travels as `job.location_raw` instead, exactly as
#: it does for Gupy, where `gates.structured_geography` can weigh it against
#: what the posting actually says about working arrangements.
PROGRAMATHOR_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="employmentType", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception.

    A listing page is third-party HTML and the links in it are third-party
    input. An adapter that followed one off the host has handed the vendor the
    ability to point this program anywhere.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise ProgramathorError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def job_paths(html: str) -> tuple[str, ...]:
    """Every posting path on a listing page, in order, without duplicates.

    Matched against `JOB_PATH` rather than against "contains /jobs/", because a
    listing page also links to category pages such as `/jobs-front-end` and to
    `/jobs-front-end/remoto`, and neither is a posting.
    """
    seen: dict[str, None] = {}
    for href in re.findall(r'href="(/jobs/[^"?#]+)"', html):
        if JOB_PATH.match(href):
            seen.setdefault(href, None)
    return tuple(seen)


def posting_id(url_or_path: str) -> str | None:
    match = JOB_PATH.match(urlsplit(url_or_path).path)
    return match.group(1) if match else None


def job_posting(html: str) -> dict[str, Any] | None:
    """The `JobPosting` block, or None when the page carries none.

    `strict=False` is REQUIRED and is not laxity: the vendor emits raw carriage
    returns and newlines inside the `description` string, which a strict JSON
    parser rejects with "Invalid control character". The first read of this
    source lost every posting to exactly that, and the block was there the
    whole time.

    A page carries more than one block -- a `BreadcrumbList` sits beside the
    posting -- so the type is checked rather than the position assumed.
    """
    for block in _LD_JSON.findall(html):
        try:
            parsed = json.loads(block.strip(), strict=False)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(parsed, dict) and parsed.get("@type") == "JobPosting":
            return parsed
    return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def company_of(posting: Any) -> str | None:
    if not isinstance(posting, dict):
        return None
    org = posting.get("hiringOrganization")
    return _text(org.get("name")) if isinstance(org, dict) else None


def company_domain(posting: Any) -> str | None:
    """The employer's own domain, from `sameAs`.

    Recorded because it is the strongest identity signal this board carries and
    `company.canonical_domain` is a column that exists. It arrives bare
    (`convolut.ai`) on some rows and as a URL on others, so both are reduced to
    a hostname and anything else is dropped rather than guessed at.
    """
    if not isinstance(posting, dict):
        return None
    org = posting.get("hiringOrganization")
    if not isinstance(org, dict):
        return None
    raw = _text(org.get("sameAs"))
    if not raw:
        return None
    host = urlsplit(raw if "//" in raw else f"//{raw}").hostname
    if not host or "." not in host:
        return None
    return host.lower().removeprefix("www.")


def compose_location(posting: Any) -> str | None:
    """`locality, region, country`, from the PostalAddress, skipping filler.

    `addressRegion` arrives as a literal `-` on some rows, which is the
    vendor's way of writing "not applicable" and would otherwise reach the
    gazetteer as a token. `addressCountry` is a bare ISO code, so it is
    expanded through nothing: `resolve_place` already reads `BR`.
    """
    if not isinstance(posting, dict):
        return None
    place = posting.get("jobLocation")
    address = place.get("address") if isinstance(place, dict) else None
    if not isinstance(address, dict):
        return None
    parts = [
        _text(address.get("addressLocality")),
        _text(address.get("addressRegion")),
        _text(address.get("addressCountry")),
    ]
    kept = [part for part in parts if part and part not in {"-", "--", "N/A"}]
    return ", ".join(kept) or None


def read_compensation(payload: Any) -> CompensationHint | None:
    """`baseSalary` as a band, when it states an amount with a currency.

    Registered in the provider registry the same way Himalayas' reader is. A
    band with no currency is not returned at all: nothing in this product
    converts between currencies, and a bare number would be compared against a
    target in another one.
    """
    if not isinstance(payload, dict):
        return None
    salary = payload.get("baseSalary")
    if not isinstance(salary, dict):
        return None
    currency = _text(salary.get("currency"))
    value = salary.get("value")
    if not currency or not isinstance(value, dict):
        return None
    amount = value.get("value")
    minimum = value.get("minValue")
    maximum = value.get("maxValue")
    period = _text(value.get("unitText"))
    low = _number(minimum if minimum is not None else amount)
    high = _number(maximum if maximum is not None else amount)
    if low is None and high is None:
        return None
    return CompensationHint(
        source_field="baseSalary",
        min_value=low,
        max_value=high,
        currency=currency.upper(),
        period=period.upper() if period else None,
    )


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", "."))
        except ValueError:
            return None
    return None


def to_stub(url: str, posting: Any) -> PostingStub | None:
    """One posting page as a stub, or None when it cannot be addressed."""
    external_id = posting_id(url)
    title = _text(posting.get("title")) if isinstance(posting, dict) else None
    if not external_id or not title:
        return None
    return PostingStub(
        external_id=f"programathor-{external_id}",
        title=title,
        url=url,
        location_raw=compose_location(posting),
        department=None,
        posted_at=to_rfc3339_utc(_text(posting.get("datePosted"))),
        description_html=_text(posting.get("description")),
        payload=dict(posting) if isinstance(posting, dict) else {},
    )


@dataclass(frozen=True)
class ListingRead:
    """Every posting path one bounded walk of the listing found."""

    paths: tuple[str, ...]
    pages: int
    stopped_early: bool


class ProgramathorProvider(JobProvider):
    """A listing walk, then one request per posting."""

    name = PROVIDER
    #: The employer's own advert, republished on a board that composes its own
    #: page around it. The text is the employer's; the page is not.
    kind = ProviderKind.AGGREGATOR
    #: FALSE. An AGGREGATOR republishes postings that originate elsewhere and
    #: has no board per employer by construction. Left inherited, the default
    #: True makes `board_providers()` offer this feed to `discover`, which then
    #: asks it whether a named company has a board here -- and a feed filtered
    #: by an identifier nothing matches answers with an empty list rather than
    #: an error, which discovery reads as a real board with no openings today.
    addresses_boards_by_company = False
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    field_map = PROGRAMATHOR_FIELD_MAP
    capabilities = ProviderCapabilities(
        #: The listing carries titles and links, never a description.
        full_description_in_list=False,
        #: But a detail request gets the whole advert, so a stored posting is
        #: complete. This is the pair Jooble cannot satisfy.
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=False,
        exposes_compensation=True,
        exposes_location_structured=True,
        #: No `jobLocationType` in the measured records, so remoteness is not
        #: asserted from a field that is not there.
        exposes_remote_flag=False,
        exposes_employment_type=True,
        #: `addressCountry: BR` is where the work is. See `providers/gupy.py`.
        publishes_hiring_scope=False,
    )

    def __init__(
        self,
        fetcher: HttpFetcher,
        site: str = SITE,
        max_pages: int | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._site = site.rstrip("/")
        #: Small by default, and the module docstring says why: sixteen
        #: requests buy about eight postings here.
        self._budget = PageBudget.resolve(PER_PAGE, 2 if max_pages is None else max_pages)

    # -- URLs --------------------------------------------------------------

    def listing_url(self, page: int) -> str:
        if page < 1:
            raise ProgramathorError(f"page must be at least 1, got {page}")
        suffix = "" if page == 1 else f"?page={page}"
        return assert_trusted(f"{self._site}/jobs{suffix}")

    def posting_url(self, path: str) -> str:
        if not JOB_PATH.match(path):
            raise ProgramathorError(f"not a posting path: {path!r}")
        return assert_trusted(f"{self._site}{path}")

    # -- reading -----------------------------------------------------------

    def read_listing(self, *, use_cache: bool = True) -> ListingRead:
        """Walk the listing to its end or to the page budget."""

        def read_page(page: int) -> FeedPage:
            url = self.listing_url(page)
            html = self._fetcher.get_text(url, use_cache=use_cache)
            return FeedPage(url=url, entries=job_paths(html), page=page)

        walk = walk_feed(scope="jobs", read_page=read_page, budget=self._budget, first_page=1)
        # First sighting wins. The listing repeats a promoted posting across
        # pages, and a walk that kept both would fetch the same advert twice.
        seen: dict[str, None] = {}
        for path in walk.entries:
            seen.setdefault(str(path), None)
        return ListingRead(
            paths=tuple(seen), pages=len(walk.pages), stopped_early=walk.hit_page_limit
        )

    def read_posting(self, path: str, *, use_cache: bool = True) -> PostingStub | None:
        """One posting, or None when the vendor will not serve it.

        A 500 here is ORDINARY and roughly half of them are: it is measured,
        deterministic per URL, and not a rate limit. Raising would abort a walk
        over a record the vendor cannot render, so it is returned as an absence
        and counted by the caller.
        """
        url = self.posting_url(path)
        try:
            html = self._fetcher.get_text(url, use_cache=use_cache)
        except FetchError:
            return None
        posting = job_posting(html)
        if posting is None:
            return None
        return to_stub(url, posting)

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return f"{self._site}/jobs"

    def validate_board(self, board: BoardRef) -> str | None:
        """There is no per-employer board here. The listing is one stream."""
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise ProgramathorError(
            "Programathor is read as one listing, not per board. "
            "Use read_listing() and read_posting(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. `read_posting` already carried the advert."""
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

    The same reduction `providers/gupy.py` performs and for the same reason:
    a requirements list collapsed onto one line makes the employer's own
    sentences harder to read than the employer wrote them, and ADR-0002
    verifies evidence quotes against exactly this text.
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

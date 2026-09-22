"""Arbeitnow, read from the free public API it documents.

WHY IT IS BUILT NOW, HAVING BEEN EVALUATED AND REFUSED
--------------------------------------------------------
Its catalogue row has said since 2026-09-07 that it was measured, permitted and
deliberately NOT built, and it stated the exact condition that would change
that:

    Unblocked by: a candidate whose search includes European on-site work.

The refusal was never about permission. It was that of 250 rows read live, 11
were remote and the rest were offices in London, Munich, Paris and Berlin --
"volume she cannot take", where "she" was the one person using the product.

That reasoning belongs to a FILTER, not to a collector. The corpus describes
work and a later layer decides whether it is wanted; the same posting has to
produce the same fingerprint for every candidate, which is invariant 4. A
product that will be opened by somebody looking for European on-site work
cannot have had that work excluded at collection time by somebody else's
preferences.

Measured again 2026-09-09: London 33, Paris 22, Berlin 21, Cardiff 11,
Munich 8, and 15 of 250 remote. Full descriptions at 5,000 characters.

AUTHORISATION, READ FIRST-PARTY 2026-09-09
-------------------------------------------
`www.arbeitnow.com/robots.txt` is `User-agent: *` with an EMPTY `Disallow:`
and one query-parameter exclusion (`/*?__hstc`, an analytics parameter). No AI
crawler is named and **`ClaudeBot` does not appear**, which is what separates
this source from Remote OK and Get on Board: the reader here is permitted and
the fixtures below were captured live.

The API states its own terms in `meta.terms` of every response:

    "This is a free public API for jobs, please do not abuse. I would
     appreciate linking back to the site. By using the API, you agree to the
     terms of service present on Arbeitnow.com"

Honoured structurally rather than promised, the same way Jobicy's and Remote
OK's are: `job.url` is the feed's own posting URL and this adapter produces no
other Apply target, so the link back cannot be dropped by a later edit. "Do
not abuse" is answered by the shared page budget, which has no way to express
"unlimited".

WHAT IT SUPPLIES
------------------
* `description` -- the whole advert, in the listing. No detail request.
  In TWO shapes, one of them a mix: raw html, or the employer's html
  entity-escaped, and in both cases followed by the vendor's own raw
  link-back footer. `advert_html` is the reader for that; see it.
* `remote` -- a real BOOLEAN, which is the employer's own structured answer
  and the field `gates.structured_geography` needs to decide whether a
  location is a place of work or a hiring region.
* `location` -- a city, and an OFFICE. Not a hiring scope: a company with a
  desk in Berlin is not a company that will employ you there, which is
  invariant 3 and why `publishes_hiring_scope` is False.
* `slug`, `company_name`, `title`, `created_at` (epoch SECONDS, not
  milliseconds), `job_types`, `tags`.

`job_types` arrives with inconsistent casing and in two languages -- `Full
Time`, `Full time`, `berufserfahren` -- which is exactly what
`normalise_employment_type` is for: a spelling nobody catalogued resolves
UNMAPPED and yields nothing rather than a value this system invented.

There is no salary field anywhere in the record, so `exposes_compensation` is
False and nothing is inferred from the body.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from html import unescape
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
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
from career_agent.providers.feeds import FeedPage, PageBudget, walk_feed

PROVIDER = "arbeitnow"

HOST = "www.arbeitnow.com"
API_URL = f"https://{HOST}/api/job-board-api"

#: The vendor's own country sites, and the reason this is a SET rather than one
#: host.
#:
#: Measured 2026-09-09 on one page of 250: `www.arbeitnow.com` 100,
#: `www.arbeitnow.co.uk` 75, `www.arbeitnow.fr` 75. One feed, one company,
#: three domains -- and an adapter that accepted only the first refused
#: **150 of 250 rows as unaddressable**, which is 60% of the source thrown
#: away for a reason that was never true of it. The first production run
#: reported exactly that: 600 postings seen, 300 stored, 300 not addressable.
#:
#: CLOSED AND NAMED, never "any host that looks related". A feed response is
#: third-party input, and the whole point of the guard below is that a vendor
#: cannot point this program somewhere else by editing a field. Adding a
#: country here is a reviewable commit, which is what it should cost.
#:
#: It is deliberately NOT the same set as `assert_trusted` uses. Where this
#: adapter FETCHES from and what it LINKS TO are different questions with
#: different risks, and the first stays a single host.
POSTING_HOSTS = frozenset(
    {
        "www.arbeitnow.com",
        "www.arbeitnow.co.uk",
        "www.arbeitnow.fr",
    }
)

#: The vendor's page size, stated in `meta.per_page` and not a preference.
PER_PAGE = 250


class ArbeitnowError(ValueError):
    """This adapter refused to do something, and says which."""


ARBEITNOW_FIELD_MAP = ProviderFieldMap(
    mappings=(
        #: A boolean on a field named for remoteness. `resolve_deterministically`
        #: reads `true` as REMOTE and `false` as UNCLEAR rather than ONSITE,
        #: which is the right asymmetry: a vendor saying a role is not remote
        #: has not said where its desk is.
        FieldMapping(path="remote", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="job_types", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception.

    `links.next` comes out of the response and is therefore untrusted input.
    An adapter that followed it anywhere would have handed the vendor the
    ability to point this program at any host it liked.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != HOST:
        raise ArbeitnowError(f"refusing to fetch a URL outside {HOST}: {url!r}")
    return url


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def company_of(record: Any) -> str | None:
    return _text(record, "company_name")


def public_url(record: Any) -> str | None:
    """The Arbeitnow posting page, which its terms ask to be linked back to.

    Accepts any of the vendor's OWN country domains, which is where 60% of its
    postings live. See `POSTING_HOSTS`: the set is closed and named, and it is
    not the set this adapter is willing to FETCH from.
    """
    url = _text(record, "url")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "").lower() not in POSTING_HOSTS:
        return None
    return url


def posted_at(record: Any) -> str | None:
    """`created_at` is an epoch in SECONDS, and that matters.

    `to_rfc3339_utc` reads a bare integer as MILLISECONDS, which is the right
    default for the vendors that came before this one. Passing 1,788,967,859
    through unchanged dates every posting to 1970, and a board publishing no
    usable date is permanently invisible to "since you last looked".
    """
    if not isinstance(record, dict):
        return None
    value = record.get("created_at")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return to_rfc3339_utc(value * 1000)


def to_stub(record: Any) -> PostingStub | None:
    """One feed record as a stub, or None when it cannot be addressed."""
    if not isinstance(record, dict):
        return None
    slug = _text(record, "slug")
    title = _text(record, "title")
    url = public_url(record)
    if not slug or not title or not url:
        return None
    return PostingStub(
        external_id=f"arbeitnow-{slug}",
        title=title,
        url=url,
        #: A CITY, and an office. Never a hiring scope. What decides whether it
        #: is read as a place of work or as a region is `remote`, through
        #: `gates.structured_geography`.
        location_raw=_text(record, "location"),
        department=None,
        posted_at=posted_at(record),
        description_html=_text(record, "description"),
        payload=dict(record),
    )


@dataclass(frozen=True)
class FeedRead:
    """Everything one bounded walk read, and the terms it read it under."""

    records: tuple[dict[str, Any], ...]
    pages: int
    stopped_early: bool
    terms: str | None


class ArbeitnowProvider(JobProvider):
    """A paginated feed of full adverts, walked to a stated page budget."""

    name = PROVIDER
    #: It republishes postings that originate on employers' own systems and
    #: composes its own page around them.
    kind = ProviderKind.AGGREGATOR
    #: FALSE. An AGGREGATOR republishes postings that originate elsewhere and
    #: has no board per employer by construction. Left inherited, the default
    #: True makes `board_providers()` offer this feed to `discover`, which then
    #: asks it whether a named company has a board here -- and a feed filtered
    #: by an identifier nothing matches answers with an empty list rather than
    #: an error, which discovery reads as a real board with no openings today.
    addresses_boards_by_company = False
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    field_map = ARBEITNOW_FIELD_MAP
    capabilities = ProviderCapabilities(
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=False,
        #: No salary field anywhere in the record.
        exposes_compensation=False,
        #: `location` is one free-text city, not a structured place.
        exposes_location_structured=False,
        exposes_remote_flag=True,
        exposes_employment_type=True,
        #: FALSE. `location` is an office. See the module docstring.
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
        self._budget = PageBudget.resolve(PER_PAGE, max_pages)

    def feed_url(self, page: int) -> str:
        if page < 1:
            raise ArbeitnowError(f"page must be at least 1, got {page}")
        return assert_trusted(f"{self._api_url}?page={page}")

    def read_feed(self, *, use_cache: bool = True) -> FeedRead:
        terms: str | None = None

        def read_page(page: int) -> FeedPage:
            nonlocal terms
            url = self.feed_url(page)
            body = self._fetcher.get_json(url, use_cache=use_cache)
            if not isinstance(body, dict):
                raise ArbeitnowError(f"{url} returned {type(body).__name__}, not an object")
            meta = body.get("meta")
            if terms is None and isinstance(meta, dict):
                stated = meta.get("terms")
                if isinstance(stated, str) and stated.strip():
                    terms = " ".join(stated.split())
            data = body.get("data")
            entries = (
                tuple(item for item in data if isinstance(item, dict))
                if isinstance(data, list)
                else ()
            )
            return FeedPage(url=url, entries=entries, page=page)

        walk = walk_feed(scope="jobs", read_page=read_page, budget=self._budget, first_page=1)
        return FeedRead(
            records=tuple(dict(entry) for entry in walk.entries),
            pages=len(walk.pages),
            stopped_early=walk.hit_page_limit,
            terms=terms,
        )

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return f"https://{HOST}/"

    def validate_board(self, board: BoardRef) -> str | None:
        """One stream, no per-employer endpoint. Nothing to check."""
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise ArbeitnowError(
            "Arbeitnow is one feed, not a board per employer. Use read_feed(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. The advert was already in the listing.

        The payload's `description` is read FIRST, and the stub's
        `description_html` only when the payload has none. `to_stub` fills both
        from the same record, so at collection time this is one string read
        twice; at REBUILD time (`pipeline.rebuild_bodies`) the stub carries no
        html and the archived payload is the only input there is. The first
        version read the stub alone, which made a rebuild of this provider
        compose every row to nothing -- counted, left alone, and never fixed.
        """
        field = _text(stub.payload, "description") or stub.description_html or ""
        html = advert_html(field)
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=html_to_text(html),
            payload=dict(stub.payload),
        )


def advert_html(description: str) -> str:
    """The vendor's `description` field as ONE HTML document.

    THE FIELD ARRIVES IN TWO SHAPES, AND ONE OF THEM IS A MIX. Measured on the
    corpus 2026-09-19, 600 open postings, every one of them ending in the
    vendor's own raw-HTML link-back footer (`<p>Find more <a href=...>...</a>
    on Arbeitnow</a>`, with a stray `</a>` and no `</p>`, as published):

    * 506 carry the employer's advert as RAW html before that footer;
    * 94 carry it ENTITY-ESCAPED -- `&lt;p&gt;`, `&quot;`, and the employer's
      own `&nbsp;` as `&amp;nbsp;` -- with the same raw footer appended after
      it. These are the postings the vendor republishes from boards that serve
      html escaped inside JSON (the `content-intro` wrappers are Greenhouse's),
      and it appends its footer without decoding what it wraps.

    So the field is neither html nor escaped html but an escaped advert FOLLOWED
    BY raw markup, and both readers this repository had got it wrong the same
    way: `domain.normalize.html_to_text` unescapes only a string with no `<` in
    it at all, and the adapter's old stripper removed the raw tags first and
    decoded `&lt;p&gt;` into a literal `<p>` afterwards. 94 of 600 stored
    bodies carried the employer's markup as TEXT, and three postings in a
    consumed evaluation holdout read as having no duties because of it.

    The rule: everything before the FIRST raw `<` is the employer's part.
    When that part BEGINS with an escaped tag it is an escaped document, it is
    unescaped ONCE, and the rest of the field is appended untouched. A field
    with no raw `<` is one part. A raw advert has no escaped tag before its
    first real one and passes through unchanged.

    "Begins with" rather than "contains" is the safe side of an edge nobody
    has measured: all 94 escaped heads open with `&lt;` and no raw head holds
    one. A raw advert whose leading prose mentioned `&lt;script&gt;` would,
    under the looser test, be decoded into a tag the parser then SKIPS, which
    is silent loss of an employer's words; an escaped advert that opened with
    bare prose would, under this one, keep its residue, which the residue
    measurement sees. Between a silent failure and a visible one, the visible
    one.

    Nothing is dropped: the footer is the vendor's text, but it is text the
    vendor published in the field, and what to do about it is a different
    decision from how to decode the field.
    """
    if not description:
        return ""
    cut = description.find("<")
    head = description if cut < 0 else description[:cut]
    if not head.lstrip().startswith("&lt;"):
        return description
    return unescape(head) + ("" if cut < 0 else description[cut:])


def advert_text(description: str) -> str:
    """The advert as text: the vendor's field decoded, then the repository's
    one HTML-to-text contract, so a quote verifies against the same text every
    sibling adapter would have stored (ADR-0002)."""
    return html_to_text(advert_html(description))

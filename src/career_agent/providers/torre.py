"""Torre, implemented in full and refused before a request is sent.

Torre is a pan-LATAM talent marketplace, Colombia-born, whose board carries
remote roles that never reach Greenhouse, Lever or Ashby. It is exactly the
kind of source this product needs, and it is the one source here that is built
and switched off.

**Why, in one line, read first-party on 2026-09-07:**

    torre.ai/robots.txt, User-agent: *  ...  Disallow: /api/

**The host question, because it is the interesting part.** Career-Ops reaches
Torre through `https://search.torre.co/opportunities/_search`, and that host
serves no `robots.txt` at all -- a 404, confirmed the same day. So there are
two readings available:

* a sibling host that omits a policy file is a separate origin, therefore open;
* or Torre has said what it thinks about automated API access, once and
  plainly, and reaching the same product's API through the host that happens
  not to repeat the sentence is routing around it.

**This module takes the second reading**, because invariant 2 does not have a
special case for convenience: absence is never permission, and a 404
`robots.txt` is the absence of a statement. The only statement Torre has
actually made about its API is `Disallow`.

**So why does this file exist at all?**

Because "we did not build it" and "we built it and will not run it" are
different facts, and only the second one can be reviewed. Everything here that
does not touch the network is complete and tested: the query builder with its
validated experience enum, the normaliser, the identity rule, the fixtures, the
deduplication and the provider-health reporting. If the access question is ever
answered -- Torre replies to an email, or publishes terms, or the `robots.txt`
changes -- what remains is to change one recorded fact, not to write a
connector under time pressure.

This is the shape Jooble already established here: wired, and switched off
before a request leaves the machine. Jooble is switched off to protect a
lifetime quota. Torre is switched off because a first-party page says not to,
which is a stronger reason and gets a stronger gate.

**The gate is a FACT, not a preference**, and that is deliberate. There is no
environment variable and no configuration key, because either would let this be
flipped by a settings edit nobody reviews. `ROBOTS_DISALLOWS_API` records what
was read, on what date, from what host. Changing it means changing a recorded
observation in a commit, which is the level of friction an ethical boundary
should have.

-- the API contract, so that it is not lost --

The endpoint, its three quirks and the experience enum below were learned from
Career-Ops's MIT-licensed `providers/torre.mjs`, read 2026-09-07. No code was
copied. `THIRD_PARTY_NOTICES.md` carries the notice. Their measurements, which
this module encodes rather than rediscovers:

1. **Unknown filter keys are silently ignored.** Posting an unrecognised filter
   returns the FULL unfiltered catalogue with a 200 and no error, and the same
   `total` as an empty body. A connector that trusted an unverified filter
   would quietly walk the entire board while believing it had searched.
2. **The result set is capped at 20 and cannot be paged.** `size` above 20
   returns an EMPTY array rather than clamping, so a naive increase reads as
   "the board is empty" rather than as an error. Every pagination form --
   `?offset=`, `?page=`, `?from=`, and a body `offset` -- returns the
   byte-identical first 20 rows. **One request per search, and no walk.**
3. **`skill/role` requires a companion `experience`**, or the API answers 500.
   Every accepted value returns the identical `total`, so it is required and
   inert: a schema obligation rather than a filter. It is a validated enum
   because an unrecognised value is refused server-side.

Quirk 2 is why `RetrievalMode.QUERY_DRIVEN` is right and why breadth here comes
from configuring several searches rather than from paging. **Twenty results per
search is a ceiling, and Torre coverage may never be presented as exhaustive.**
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

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
from career_agent.providers.feeds import deduplicate_by

# =========================================================================
# the recorded observation that gates this adapter
# =========================================================================

#: What `torre.ai/robots.txt` said, and when it was read.
#:
#: A dated observation, in the shape `llm/quotas.py` already uses for rate
#: limits: a fact somebody measured, not a policy this file invents. It is the
#: ONLY thing standing between this adapter and a live request.
ROBOTS_READ_ON = "2026-09-07"
ROBOTS_HOST = "torre.ai"
ROBOTS_RULE = "User-agent: *  ...  Disallow: /api/"

#: Whether the vendor's own page currently forbids the access this adapter
#: would need.
#:
#: **Setting this False is an owner decision and a reviewable commit.** It
#: asserts that the observation above no longer holds -- that Torre's policy
#: changed, or that permission was obtained directly. It is not a feature flag
#: and it must never be flipped to make a test pass or a count larger.
ROBOTS_DISALLOWS_API = True

SEARCH_ENDPOINT = "https://search.torre.co/opportunities/_search"
TRUSTED_API_HOST = "search.torre.co"

#: Postings are displayed on torre.ai; `/post/{id}` is the public permalink.
POSTING_BASE = "https://torre.ai/post/"

#: The hard ceiling. Above 20 the API returns an empty array rather than
#: clamping, and no pagination form advances. See quirk 2.
PAGE_SIZE = 20

#: Torre ids are short URL-safe tokens. Anchored so an id from an untrusted
#: payload can never inject a path segment or a query into the permalink this
#: product renders as a link somebody clicks.
TORRE_ID = re.compile(r"^[A-Za-z0-9_-]{4,64}$")

#: The values the API accepts for the required `skill/role.experience`
#: companion. Anything else is refused server-side, so this is a validated
#: vocabulary rather than free text.
EXPERIENCE_LEVELS: frozenset[str] = frozenset(
    {
        "potential-to-develop",
        "1-plus-year",
        "2-plus-years",
        "3-plus-years",
        "5-plus-years",
    }
)

#: Arbitrary among the accepted values, and that is not sloppiness: every one
#: returns the identical result set, because the field is required and inert.
DEFAULT_EXPERIENCE = "1-plus-year"


class TorreCollectionRefused(RuntimeError):
    """A live Torre request was attempted while its vendor's page forbids it.

    Raised BEFORE any URL is built or any client is constructed. A caller
    seeing this has not sent a request and has not been rate limited: it is a
    refusal by this product, and the message names the line it is honouring.
    """


def refuse_if_disallowed() -> None:
    """The gate. Called before anything that could become a request.

    Deliberately not a return value anybody could ignore. Every network path in
    this module calls it first, and `tests/unit/test_torre.py` asserts that a
    collection attempt raises rather than fetching.
    """
    if ROBOTS_DISALLOWS_API:
        raise TorreCollectionRefused(
            f"Torre collection is refused. {ROBOTS_HOST}/robots.txt, read "
            f"{ROBOTS_READ_ON}, says: {ROBOTS_RULE}. The API this adapter would "
            f"call is reached through {TRUSTED_API_HOST}, which serves no "
            "robots.txt at all, and absence is never permission. Everything in "
            "this adapter that does not touch the network is built and tested; "
            "unblocking it means changing the recorded observation in "
            "providers/torre.py, which is an owner decision."
        )


# =========================================================================
# capabilities
# =========================================================================

TORRE_CAPABILITIES = ProviderCapabilities(
    # The search response carries an `objective` -- a one-line role statement --
    # and no advert body.
    full_description_in_list=False,
    # And there is no detail endpoint in the contract above. As Jooble: the
    # shortness is permanent and is a fact about the SOURCE.
    obtains_full_description=False,
    # `created`, ISO 8601.
    exposes_posted_date=True,
    exposes_department=False,
    # Torre renders compensation on some postings; the search response shape
    # was not read for it from a first-party page.
    exposes_compensation=False,
    # `locations` is an array of strings, which is a list rather than prose.
    exposes_location_structured=True,
    # `remote`, a boolean.
    exposes_remote_flag=True,
    exposes_employment_type=False,
    # False, for the reason Get on Board's is False: `locations` on a LATAM
    # board is where a posting says it is, not the employer's answer to where
    # it may hire. UNRESOLVED is the correct outcome, not a guess in either
    # direction.
    publishes_hiring_scope=False,
)


TORRE_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="remote", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="locations", dimension=MetadataDimension.HIRING_LOCATION_HINT),
    )
)


# =========================================================================
# one search
# =========================================================================


class TorreQueryError(ValueError):
    """A configured search that cannot be sent as written."""


@dataclass(frozen=True)
class TorreSearch:
    """One deliberate question, which is the whole unit of coverage here.

    **Derived from skills, routines and tools rather than from titles.** That
    is invariant 7 applied to retrieval: a search for `Operations Manager`
    finds postings that chose that phrase, and a search for `workflow
    automation` finds the work. The second is what this product is for.

    `remote_only` expresses only the positive case. `{"remote": {"term":
    false}}` is not a verified filter and quirk 1 says an unverified filter is
    silently ignored, so a falsy value sends no key at all rather than a filter
    that looks effective and is not.
    """

    text: str
    experience: str = DEFAULT_EXPERIENCE
    remote_only: bool = False

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise TorreQueryError(
                "a Torre search needs text. An empty search returns the entire "
                "unfiltered catalogue with a 200, which reads as a successful "
                "broad result rather than as a missing filter."
            )
        if self.experience not in EXPERIENCE_LEVELS:
            accepted = ", ".join(sorted(EXPERIENCE_LEVELS))
            raise TorreQueryError(
                f"invalid Torre experience {self.experience!r}. Accepted: {accepted}"
            )

    def body(self) -> dict[str, Any]:
        """The request body, with the required companion always present.

        `experience` is emitted unconditionally beside `text` rather than when
        configured, because omitting it is a hard 500 rather than a broader
        search. That is invisible to a mocked test and shows up only against
        the live API, which is why it is encoded here.
        """
        payload: dict[str, Any] = {
            "skill/role": {"text": self.text.strip(), "experience": self.experience}
        }
        if self.remote_only:
            payload["remote"] = {"term": True}
        return payload


def looks_unfiltered(claimed_total: int | None, threshold: int = 250_000) -> bool:
    """Whether a response's own total suggests the filter was ignored.

    Quirk 1 cannot be detected from the twenty rows that come back, because
    twenty rows of the whole catalogue look exactly like twenty rows of a
    search. The `total` is the only tell: Career-Ops measured the unfiltered
    catalogue at roughly 304,000 and a real search at roughly 39,000.

    This is a WARNING and never a silent correction. A run that trips it must
    say so, because the alternative is presenting the front of the entire board
    as the answer to a question about workflow automation.
    """
    return claimed_total is not None and claimed_total >= threshold


# =========================================================================
# one opportunity
# =========================================================================


def to_stub(opportunity: Any) -> PostingStub | None:
    """One Torre opportunity into a stub, or None when it cannot be addressed.

    Closed postings are dropped: the search endpoint returns them, and a closed
    role is not an opening. An ABSENT status is treated as open, because the
    field is present on every observed row and a missing one must not silently
    empty the feed if Torre stops sending it.

    The URL is BUILT from the id rather than taken from the payload. A payload
    URL is attacker-controlled input that this product would render as a link;
    building it from an id validated against `TORRE_ID` means there is no such
    surface.
    """
    if not isinstance(opportunity, dict):
        return None

    title = opportunity.get("objective")
    if not isinstance(title, str) or not title.strip():
        return None

    status = opportunity.get("status")
    if isinstance(status, str) and status.strip() and status.strip() != "open":
        return None

    external_id = opportunity.get("id")
    if not isinstance(external_id, str) or not TORRE_ID.match(external_id.strip()):
        return None
    external_id = external_id.strip()

    return PostingStub(
        external_id=external_id,
        title=title.strip(),
        url=f"{POSTING_BASE}{external_id}",
        location_raw=_location(opportunity),
        department=None,
        posted_at=_created_at(opportunity),
        description_html=None,
        payload=dict(opportunity),
    )


def company_of(opportunity: Any) -> str | None:
    """The first named organisation, or None.

    None rather than a placeholder. Torre lists solo and anonymous posters with
    no organisation at all, and naming that company "Torre" would assert an
    employer this adapter invented.
    """
    if not isinstance(opportunity, dict):
        return None
    organisations = opportunity.get("organizations")
    if not isinstance(organisations, list):
        return None
    for org in organisations:
        if isinstance(org, dict):
            name = org.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _location(opportunity: dict[str, Any]) -> str | None:
    """The stated locations, joined. `remote` is a separate dimension.

    Unlike Career-Ops's normaliser this does NOT fold "Remote" into the
    location string. Invariant 3 keeps work model and geography apart, and
    `remote` already has its own field mapping -- writing it into the location
    too would let one assertion be counted twice by two different readers.
    """
    locations = opportunity.get("locations")
    if not isinstance(locations, list):
        return None
    named = [loc.strip() for loc in locations if isinstance(loc, str) and loc.strip()]
    return ", ".join(named) or None


def _created_at(opportunity: dict[str, Any]) -> str | None:
    """`created`, ISO 8601, normalised to RFC 3339 UTC. None when unreadable."""
    created = opportunity.get("created")
    if not isinstance(created, str) or not created.strip():
        return None
    # Handed to the shared normaliser as a STRING: it already accepts RFC 3339
    # with `Z` or a numeric offset, and a second date parser inside a provider
    # is how two encodings of the same column start disagreeing.
    return to_rfc3339_utc(created.strip())


def recognise_posting_url(url: str) -> str | None:
    """Whether this URL is a Torre posting, and its native id.

    Returns a value byte-identical to the `external_id` `to_stub` stores, which
    is the contract `_URL_RECOGNISERS` depends on: an aggregator publishing a
    Torre permalink resolves to an exact `(provider, external_id)` key with no
    fuzzy matching anywhere near it.
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url.strip())
    if parts.hostname not in {"torre.ai", "www.torre.ai"}:
        return None
    segments = [s for s in parts.path.split("/") if s]
    if len(segments) != 2 or segments[0] != "post":
        return None
    return segments[1] if TORRE_ID.match(segments[1]) else None


# =========================================================================
# the provider
# =========================================================================


@dataclass(frozen=True)
class SearchRead:
    """One search's answer, with the two things that make it honest.

    `ceiling_reached` says twenty rows came back, which is the cap rather than
    the answer. `filter_suspected_ignored` says the response's own total looks
    like the whole catalogue. Both are fields rather than log lines, for the
    reason `feeds.BoundedWalk` states: a caller that cannot tell a complete
    answer from a capped one will present the capped one as coverage.
    """

    search: TorreSearch
    opportunities: tuple[Any, ...]
    claimed_total: int | None = None
    unaddressable: int = 0

    @property
    def ceiling_reached(self) -> bool:
        return len(self.opportunities) >= PAGE_SIZE

    @property
    def filter_suspected_ignored(self) -> bool:
        return looks_unfiltered(self.claimed_total)


class TorreProvider(JobProvider):
    """The Torre public opportunity search, built and refused.

    Every method that could reach the network calls `refuse_if_disallowed`
    first. Everything else -- query building, normalisation, identity,
    deduplication -- works offline and is tested offline, which is what makes
    this a finished adapter rather than an intention.
    """

    name = "torre"
    kind = ProviderKind.AGGREGATOR
    #: Results are the answer to a question. Coverage is a property of the
    #: SEARCHES CONFIGURED and may never be presented as the LATAM market.
    retrieval_mode = RetrievalMode.QUERY_DRIVEN
    addresses_boards_by_company = False
    capabilities = TORRE_CAPABILITIES
    field_map = TORRE_FIELD_MAP

    def __init__(self, fetcher: HttpFetcher, endpoint: str = SEARCH_ENDPOINT) -> None:
        self._fetcher = fetcher
        self._endpoint = endpoint

    def board_url(self, board: BoardRef) -> str:
        """Where a person would run this search by hand.

        A search link is always available to a person, and the `robots.txt`
        line this adapter honours is about automated API access rather than
        about somebody opening a page. `MANUAL_ONLY` in the source matrix means
        exactly this: the human route stays open.
        """
        del board
        return "https://torre.ai/search/jobs"

    def search(self, search: TorreSearch, *, use_cache: bool = True) -> SearchRead:
        """One bounded request per search. Refused before it is built.

        There is no page loop and there must not be one: quirk 2 says every
        pagination form returns the identical first twenty rows, so a loop
        could only refetch and its results would be discarded as duplicates.
        """
        refuse_if_disallowed()

        body = search.body()
        url = f"{self._endpoint}?offset=0&size={PAGE_SIZE}"
        payload = self._fetcher.post_json(
            url,
            body,
            # No credential is in this URL, so the safe form IS the real one.
            # It is still passed explicitly rather than defaulted, because
            # `post_json` requires a caller to have thought about it once.
            safe_url=url,
            # The endpoint AND the body. Two searches differing only in their
            # text must not collide in the cache, and the endpoint alone is
            # identical for every search this adapter makes.
            cache_key=f"torre:{search.text}:{search.experience}:{search.remote_only}",
            use_cache=use_cache,
        )

        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            keys = ", ".join(sorted(payload)) if isinstance(payload, dict) else "not an object"
            raise ValueError(
                f"Torre returned an unexpected shape for {search.text!r}: "
                f"expected {{'results': [...]}}, got [{keys}]"
            )

        opportunities = tuple(payload["results"])
        addressable = tuple(o for o in opportunities if to_stub(o) is not None)
        return SearchRead(
            search=search,
            opportunities=opportunities,
            claimed_total=_total(payload),
            unaddressable=len(opportunities) - len(addressable),
        )

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings for one configured search, named by `board_identifier`.

        `board_identifier` is the SEARCH TEXT. Torre has no boards -- results
        are the answer to a question -- so this is the same accommodation WWR
        makes for feed names, and `retrieval_mode` is what says the shape out
        loud.
        """
        read = self.search(TorreSearch(text=board.board_identifier))
        yield from self.normalise(read.opportunities)

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as the search gave it. No detail endpoint exists.

        This one does NOT refuse: it makes no request, and it is the path a
        rescore takes over an already-archived payload. Refusing here would
        make stored Torre rows unreadable, which is a different thing from not
        collecting new ones.
        """
        del board
        return RawPosting(
            stub=stub,
            description_html="",
            description_text="",
            payload=dict(stub.payload),
        )

    # -- offline, and tested offline ---------------------------------------

    @staticmethod
    def normalise(opportunities: tuple[Any, ...]) -> tuple[PostingStub, ...]:
        """Payload rows into stubs, deduplicated by exact vendor id.

        Reachable without a fetcher and without authorisation, because it is
        what a rescore runs over archived payloads. It is also what makes this
        adapter reviewable while its collection path is closed.
        """
        unique = deduplicate_by(opportunities, _identity)
        stubs = (to_stub(o) for o in unique)
        return tuple(stub for stub in stubs if stub is not None)


def _total(payload: dict[str, Any]) -> int | None:
    """`total`, when the response states one.

    Recorded for one purpose only: `looks_unfiltered` reads it, and it is the
    ONLY way quirk 1 can be detected. Twenty rows of the whole catalogue look
    exactly like twenty rows of a search.
    """
    total = payload.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        return None
    return total if total >= 0 else None


def _identity(opportunity: Any) -> str | None:
    if not isinstance(opportunity, dict):
        return None
    value = opportunity.get("id")
    return value.strip() or None if isinstance(value, str) else None

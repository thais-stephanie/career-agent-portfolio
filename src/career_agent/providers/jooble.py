"""Jooble, an aggregator reached by asking it questions.

Every provider before this one enumerates: give Greenhouse a board and it hands
back that employer's postings, all of them, and running it twice returns the
same set. Jooble does not work that way. Its REST contract is

    POST https://{domain}/api/{key}
    {"keywords": "...", "location": "...", "page": 1}

with `keywords` and `location` both REQUIRED. There is no "everything" query.
What comes back is the answer to the question asked, which means coverage is a
property of the questions rather than of the source, and that distinction has
to survive all the way to the interface. Twenty queries against Jooble are
twenty answers, never "the Brazilian job market". `RetrievalMode.QUERY_DRIVEN`
is how that is carried, and `sources/catalogue.py` explains why it exists.

**The vocabulary used to FIND a job may never earn it points.** A query is a
question about where to look; the lexicon is a statement about what the person
wants. They are different files, different code paths and different lifecycles,
and `tests/unit/test_retrieval_vocabulary.py` asserts it, because the founding
complaint of this product is a search that rewards a job for matching the words
somebody typed.

**Three facts shape the rest of this module.**

*The key is in the URL.* `POST /api/{key}` means the request URL is a
credential, and a URL is what `FetchError` records, what a caller prints from
one, and what names a cache file. So nothing here hands the real URL to
anything that keeps it: `HttpFetcher.post_json` takes a redacted `safe_url` and
an explicit `cache_key`, and this module is the reason it does.

*The quota is a lifetime.* Five hundred requests per key, ever. Development is
therefore fixture-driven, every request passes a ledger that refuses BEFORE it
is sent, and the cache is on by default so that asking the same question twice
costs one answer. See `jooble_quota.py`.

*The key is bound to one country.* Jooble's own documentation: "Each Jooble
domain (country) requires its own unique REST API key", and a key issued on
`jooble.org` reaches US postings only. Which market a given key belongs to
cannot be read from the key, and discovering it by trying is exactly the kind of
curiosity a lifetime budget cannot afford -- so the domain is REQUIRED
configuration, with no default. Guessing `jooble.org` would have quietly made
this a US-only connector for an owner in Brazil.
"""

from __future__ import annotations

import hashlib
import json
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
from career_agent.providers.jooble_quota import QuotaLedger

#: The domain is configuration and has no default on purpose. See the module
#: docstring: a key is country-bound and the wrong domain answers 403 at best
#: and, at worst, answers a different country's questions perfectly well.
DOMAIN_ENV = "JOOBLE_DOMAIN"
KEY_ENV = "JOOBLE_API_KEY"

#: What Jooble returns per page when asked. The documented parameter is
#: `ResultOnPage`; 20 is the value this adapter asks for, chosen because a
#: lifetime quota makes a larger page strictly better and 20 is the largest the
#: contract has been observed to honour. Measured, not assumed -- see the
#: session evidence file.
RESULTS_PER_PAGE = 20


class JoobleConfigurationError(RuntimeError):
    """The connector cannot run because something local is missing.

    Its message names the ENVIRONMENT VARIABLE and never its value, which is
    the whole distinction this class exists to keep: `doctor` may say
    `JOOBLE_API_KEY: MISSING`, and may never say what a present one contains.
    """


JOOBLE_CAPABILITIES = ProviderCapabilities(
    # `snippet`, and the name is the warning. Jooble's list response carries a
    # short excerpt rather than the employer's description, and there is no
    # detail endpoint in the documented contract to fetch the rest from. This
    # is the single most important fact about this source for THIS product,
    # whose matcher reads bodies: a snippet is not a description, and a
    # connector that presented one as a description would make every posting
    # from here look thin on evidence rather than genuinely thin.
    full_description_in_list=False,
    # AND IT CANNOT BE OBTAINED AT ALL. The documented contract is one
    # `/api/{key}` search call returning `snippet`, and there is no detail
    # endpoint anywhere in it to fetch the remainder from. So the
    # shortness of a Jooble body is permanent, and it is a fact about
    # THIS SOURCE rather than about the employer -- which is exactly what
    # `ContentCompleteness.PARTIAL_CONTENT` exists to record. Without it a
    # 400-character excerpt and a genuinely brief posting are one number
    # on a card.
    obtains_full_description=False,
    # `updated`, and it is an UPDATE timestamp rather than a publication one.
    # Carried, because it bounds the age from above and that is worth having;
    # never relabelled as a posted date, because ADR-0012's discipline about
    # dates meaning what they say applies to sources as much as to statuses.
    exposes_posted_date=True,
    # No department, function or team field in the documented response.
    exposes_department=False,
    # `salary`, as free text the aggregator composed. It travels as a hint and
    # is never parsed into numbers here.
    exposes_compensation=True,
    # `location` is one free-text string, so the SHAPE is unstructured. Saying
    # otherwise would make a missing country read as "the employer did not say".
    exposes_location_structured=False,
    # Nothing in the documented contract distinguishes remote from onsite. The
    # word may appear inside `location` or `snippet`, which is text, not a flag.
    exposes_remote_flag=False,
    # `type` exists and carries values like "Full-time" in the samples the
    # documentation shows. Declared true and verified by fixture.
    exposes_employment_type=True,
)


JOOBLE_FIELD_MAP = ProviderFieldMap(
    (
        FieldMapping("location", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping("type", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        # The aggregator's rendered pay string. It travels and is never parsed,
        # exactly as `CompensationHint.raw_text` requires: a display string is a
        # rounding of a measurement rather than the measurement.
        FieldMapping("salary", MetadataDimension.COMPENSATION_HINT),
    )
)

# Archived but unmapped, each for a stated reason:
#   source   - the name of the board Jooble found this on, and the ORIGIN
#              pointer this source offers. It is not a metadata hint about the
#              work; it is provenance, read by the collector rather than by the
#              field map. ADR-0013.
#   link     - where to apply, and the second half of identity
#   company  - identity, resolved by the collector
#   snippet  - the excerpt. It becomes the description text, with its shortness
#              declared by `full_description_in_list=False` rather than hidden
#   id       - the stable upstream identifier


@dataclass(frozen=True, slots=True)
class Query:
    """One question, which is the unit of retrieval for this source.

    Both fields are required by the vendor's contract, and modelling them as a
    pair rather than as loose arguments is what lets a collection record WHICH
    questions it asked. A coverage claim from a query-driven source is
    meaningless without them.
    """

    keywords: str
    location: str

    def as_dict(self) -> dict[str, Any]:
        return {"keywords": self.keywords, "location": self.location}

    @property
    def label(self) -> str:
        return f"{self.keywords!r} in {self.location!r}"


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One answered page, and what it says about the rest.

    `total_count` is the aggregator's own claim about how many postings match.
    It is recorded rather than trusted: a page that returns fewer rows than it
    promised is the silent-truncation failure every paginator in this project
    is written to notice.
    """

    query: Query
    page: int
    jobs: tuple[dict[str, Any], ...]
    total_count: int | None
    payload: dict[str, Any]

    @property
    def has_more(self) -> bool:
        """Whether asking for the next page could return anything.

        A SHORT page ends the walk whatever `totalCount` claims, and that order
        of tests is the whole point. The obvious version -- keep going while
        `page * size < totalCount` -- reads the aggregator's own arithmetic as
        authoritative and would spend request after request on a source that
        promised 137 postings and served three. Against a lifetime quota that
        is not a slow paginator, it is the allowance.
        """
        if len(self.jobs) < RESULTS_PER_PAGE:
            return False
        if self.total_count is None:
            return True
        return self.page * RESULTS_PER_PAGE < self.total_count

    @property
    def truncated(self) -> bool:
        """The source claimed more than it served on a page it filled short.

        Not an error and not a reason to retry: aggregators overstate totals
        routinely, and the honest response is to record that coverage of this
        query is smaller than the number the source printed. It is the same
        discipline `FeedWalk.truncated` applies to Speedrun.
        """
        if self.total_count is None:
            return False
        served = (self.page - 1) * RESULTS_PER_PAGE + len(self.jobs)
        return len(self.jobs) < RESULTS_PER_PAGE and served < self.total_count


class JoobleProvider(JobProvider):
    """The Jooble REST API, asked bounded questions under a lifetime budget."""

    name = "jooble"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.QUERY_DRIVEN
    # It CAN be asked about a company, and it must not be probed with one:
    # `discover` would spend a permanently limited request to learn whether an
    # employer has a board on a source that has no boards.
    addresses_boards_by_company = False
    capabilities = JOOBLE_CAPABILITIES
    field_map = JOOBLE_FIELD_MAP

    def __init__(
        self,
        fetcher: HttpFetcher,
        *,
        domain: str,
        api_key: str,
        ledger: QuotaLedger | None = None,
    ) -> None:
        if not domain:
            raise JoobleConfigurationError(
                f"{DOMAIN_ENV} is not set. A Jooble API key is bound to ONE country: a key "
                f"issued on jooble.org reaches United States postings and nothing else. "
                f"Set {DOMAIN_ENV} to the domain your key was issued on -- for example "
                f"`jooble.org`, `uk.jooble.org` or `br.jooble.org`. There is deliberately no "
                f"default: guessing would spend a request from a 500-request lifetime quota "
                f"to discover a fact you already know."
            )
        if not api_key:
            raise JoobleConfigurationError(
                f"{KEY_ENV} is not set. Put it in `.env`, which is gitignored."
            )
        self._fetcher = fetcher
        self._domain = domain.strip().rstrip("/").removeprefix("https://").removeprefix("http://")
        self._key = api_key
        self._ledger = ledger

    # -- URLs, and the one that may be recorded ----------------------------

    @property
    def safe_url(self) -> str:
        """The endpoint with the key replaced by a placeholder.

        The ONLY URL this module lets out of its hands. Everything that keeps a
        URL -- errors, statistics, logs, the cache filename -- gets this one.
        """
        return f"https://{self._domain}/api/{{key}}"

    def _endpoint(self) -> str:
        return f"https://{self._domain}/api/{self._key}"

    def board_url(self, board: BoardRef) -> str:
        """Where a person would look this company up by hand."""
        return board.board_url or f"https://{self._domain}/SearchResult?ukw={board.company_slug}"

    # -- asking ------------------------------------------------------------

    def search(self, query: Query, page: int = 1, *, budget: int | None = None) -> SearchPage:
        """One page of one question. Costs at most one request, and may cost none.

        The cache is consulted first and a hit never reaches the ledger,
        because the vendor never saw it. The ledger is consulted second and
        refuses BEFORE the request rather than after, because a quota failure
        discovered from a 403 has already cost the thing the check protects.
        """
        body: dict[str, Any] = {
            **query.as_dict(),
            "page": page,
            "ResultOnPage": RESULTS_PER_PAGE,
        }
        cache_key = self._cache_key(body)

        cached = self._cached(cache_key)
        if cached is not None:
            return self._to_page(query, page, cached)

        if self._ledger is not None:
            self._ledger.reserve(budget=budget)

        payload = self._fetcher.post_json(
            self._endpoint(),
            body,
            safe_url=self.safe_url,
            cache_key=cache_key,
        )
        return self._to_page(query, page, payload)

    def _cache_key(self, body: dict[str, Any]) -> str:
        """Identity of a POST: the endpoint AND the body, with no key in it.

        The domain is included because two countries answer the same question
        differently. The API key is not, because a cache entry is not a place
        to put a credential even in a hash.
        """
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(f"{self._domain}|{canonical}".encode()).hexdigest()
        return f"jooble:{digest}"

    def _cached(self, cache_key: str) -> Any | None:
        cache = getattr(self._fetcher, "cache", None)
        return None if cache is None else cache.get(cache_key)

    def _to_page(self, query: Query, page: int, payload: Any) -> SearchPage:
        if not isinstance(payload, dict):
            return SearchPage(query, page, (), None, {})
        jobs = payload.get("jobs")
        rows = tuple(row for row in jobs if isinstance(row, dict)) if isinstance(jobs, list) else ()
        total = payload.get("totalCount")
        return SearchPage(
            query=query,
            page=page,
            jobs=rows,
            total_count=int(total) if isinstance(total, int | float) else None,
            payload=payload,
        )

    def walk(
        self, query: Query, *, max_pages: int = 1, budget: int | None = None
    ) -> Iterator[SearchPage]:
        """Pages of one question, bounded by an EXPLICIT page count.

        `max_pages` defaults to one. Every other paginator in this project
        defaults to reading until the source says stop; this one cannot, because
        "until it stops" against a lifetime quota is a way to spend all of it on
        a single question.
        """
        for spent, page in enumerate(range(1, max_pages + 1)):
            remaining = None if budget is None else budget - spent
            answered = self.search(query, page, budget=remaining)
            yield answered
            if not answered.has_more:
                return

    # -- the protocol ------------------------------------------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Postings this aggregator holds for one company.

        Implemented because the protocol means a board, and ADR-0008 says a
        board belongs to one company -- registering the whole aggregator as one
        board carrying hundreds of employers is the attribution failure that
        ADR-0008 exists to prevent.

        It is NOT how this source is collected in bulk. One request per company
        against a 500-request lifetime is arithmetic nobody should run, so the
        collector uses `walk` and groups the answers by company, exactly as the
        Speedrun adapter does for its own reason.
        """
        query = Query(keywords=board.board_identifier, location=board.company_slug or "")
        for answered in self.walk(query, max_pages=1):
            for row in answered.jobs:
                stub = self.to_stub(row)
                if stub is not None:
                    yield stub

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """The posting as this source gave it, which is all there is.

        The documented contract has no per-posting endpoint: the search response
        is the whole record. So this makes no request, spends no quota, and
        returns what the listing already carried. `full_description_in_list` is
        False and the text is a `snippet`, and both of those stay true here
        rather than being smoothed over by a fetch that cannot happen.
        """
        del board
        text = str(stub.payload.get("snippet") or "").strip()
        return RawPosting(
            stub=stub,
            description_html=text,
            description_text=text,
            payload=dict(stub.payload),
        )

    # -- one row -----------------------------------------------------------

    def to_stub(self, row: dict[str, Any]) -> PostingStub | None:
        """One search result, or None when it is not addressable.

        A row without an id or a link cannot be identified across two runs, and
        an unidentifiable posting is a duplicate waiting to happen. Dropping it
        is the only safe answer, and it is counted rather than silent.
        """
        external_id = str(row.get("id") or "").strip()
        link = str(row.get("link") or "").strip()
        title = str(row.get("title") or "").strip()
        if not external_id or not link or not title:
            return None

        return PostingStub(
            external_id=external_id,
            title=title,
            url=link,
            location_raw=(str(row.get("location") or "").strip() or None),
            department=None,
            posted_at=_updated_at(row.get("updated")),
            description_html=(str(row.get("snippet") or "").strip() or None),
            payload=dict(row),
        )


def _updated_at(value: Any) -> str | None:
    """`updated`, normalised, and never renamed to a publication date.

    Jooble calls this field `updated`, and this project has an invariant about
    dates meaning what they say. It bounds the posting's age from above, which
    is worth carrying; it is not the day the employer published, which nothing
    in the documented contract supplies.
    """
    if not value:
        return None
    try:
        return to_rfc3339_utc(str(value))
    except (ValueError, TypeError):
        return None


def origin_hint(row: dict[str, Any]) -> str | None:
    """Which board Jooble found this on, when it says.

    The `source` field, and the closest thing this aggregator offers to an
    origin pointer. It is a NAME rather than a URL -- "Greenhouse", "LinkedIn"
    -- so it cannot resolve a posting to its ATS record the way Speedrun's
    `apply` URL can. Recorded as a sighting under ADR-0013, never used to claim
    two postings are one.
    """
    name = str(row.get("source") or "").strip()
    return name or None

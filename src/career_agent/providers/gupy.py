"""Gupy, read from the candidate-facing portal feed.

WHY THIS SOURCE, AHEAD OF EVERY OTHER BRAZILIAN CANDIDATE
----------------------------------------------------------
Every measurement this product has taken of its own corpus says the same
thing: it is a United States job search with a Brazilian owner. On 2026-09-09,
of 19,485 open postings, **211 were located in Brazil and 697 anywhere in
Latin America**. Get on Board, the only LATAM board here, held 409 of them.

Gupy is the applicant tracking system most large Brazilian employers use, and
its candidate portal answers with **81,310 postings**, unauthenticated, with
the whole advert in the listing response.

WHAT THE FEED SUPPLIES, MEASURED 2026-09-09
--------------------------------------------
`GET https://employability-portal.gupy.io/api/v1/jobs` returns
`{"data": [...], "pagination": {...}}`.

* `description` -- the whole advert, in the LISTING. No detail request at all,
  which is the same economics Get on Board has and Workday does not.
* `city`, `state`, `country` -- three separate fields, and `country` is
  `Brasil` in Portuguese. The gazetteer already resolves it.
* `workplaceType` -- `remote` / `hybrid` / `on-site`, the employer's own
  structured answer, which is what `gates.structured_geography` needs in order
  to read a location as a place-of-work rather than as a hiring scope. Every
  one of those three spellings already normalises; no alias was added.
* `isRemoteWork` -- a boolean saying the same thing a second way. Stored, and
  deliberately NOT read as geography.
* `publishedDate`, `type`, `skills`, `disabilities`, `careerPageName`,
  `careerPageUrl`, `jobUrl`.

`disabilities` is a Brazilian accessibility field with no analogue anywhere
else in this corpus. It is archived and read by nothing here: what it means
for a search is a product question nobody has answered.

IDENTITY, AND WHY THIS SOURCE HAS ONE
--------------------------------------
`jobUrl` is `https://<employer>.gupy.io/job/<token>`, on the EMPLOYER'S own
subdomain. That is an origin pointer in the sense ADR-0013 requires: it names
one employer, it is stable, and it is not this portal's own page. It is the
field whose absence keeps Remote OK unbuilt, and it is why this source could
be built at all.

AUTHORISATION, READ FIRST-PARTY 2026-09-05 AND AGAIN 2026-09-09
----------------------------------------------------------------
* `GET https://employability-portal.gupy.io/robots.txt` returns **404**, body
  `{"message":"Cannot GET /robots.txt",...}`. This host publishes no robots
  file at all.
* `portal.gupy.io/robots.txt` returns 200 with `User-agent: *` and an empty
  `Disallow:`. That is the FRONTEND host and a robots file does not govern a
  different host, so it is recorded and not relied on.
* Gupy's published developer documentation describes a different, employer-
  facing API on `api.gupy.io` requiring a Bearer token. It does not mention
  this host and states no terms for it.
* No authentication, no CAPTCHA, no rate-limit header, no access control of
  any kind was encountered or circumvented.

So: **public in fact, undeclared in policy.** Under the collection policy this
product adopted on 2026-09-09 (ADR-0018) that is a source we may read, bounded
and politely, which is a reversal of the earlier rule and is recorded as one.

THREE MEASURED TRAPS, EACH OF WHICH BREAKS A NAIVE WALK
--------------------------------------------------------
1. **`offset` is capped at 10,000, hard.** `offset=9990` answers 200;
   `offset=10000` answers `400 Bad Request`. So no single query can reach more
   than 10,000 of the 81,310, and the walk must partition.
2. **`total` changes meaning with `limit`.** At `limit=10` it is the real
   total; at `limit=100` it comes back as `100`. A walk that stopped on
   `total` would end on its first page. `walk_feed` already refuses to use it
   as a stop condition and `providers/feeds.FeedPage` cites this exact vendor.
3. **`limit=1000` answers 400**; 100 works. The maximum is somewhere in
   (100, 1000] and was not narrowed further, because finding it costs requests
   and buys nothing.

The partition is `workplaceType`, measured on 2026-09-09 at remote 2,115,
hybrid 5,970 and on-site 72,682. The first two fit under the ceiling whole and
are what a remote-first candidate can actually take from anywhere in the
country, so they are the default. `on-site` does NOT fit and is reachable only
a slice at a time; it is offered, never defaulted, and the stats say when a
partition hit the ceiling rather than its end.

WHAT THIS ADAPTER DOES NOT CLAIM
---------------------------------
`publishes_hiring_scope` is **False**, and on a Brazilian board read by a
Brazilian candidate that is the tempting one to get wrong. `country: Brasil`
on an on-site posting in Cachoeira do Sul says where the WORK IS, not that the
employer will hire from anywhere in Brazil. Invariant 3 is the same invariant
whichever country it is applied to. What makes these postings reachable is
`structured_geography`, which reads the workplace type and the place together
and needs no scope claim from anybody.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
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
from career_agent.providers.feeds import BoundedWalk, FeedPage, PageBudget, walk_feed

PROVIDER = "gupy"

#: The candidate portal. NOT `api.gupy.io`, which is the employer-facing API
#: this product has no credential for and does not want one.
API_HOST = "https://employability-portal.gupy.io/api/v1"

#: The only host this adapter will fetch from. A URL that arrives in a payload
#: is untrusted input; nothing built here is allowed to point elsewhere.
API_HOSTNAME = "employability-portal.gupy.io"

#: 100 works; 1000 answers 400. Measured, not chosen.
PER_PAGE = 100

#: `offset=10000` answers 400 Bad Request. A walk that ignores this reports a
#: vendor error as a failed collection.
MAX_OFFSET = 10_000

#: The partition dimension, and its three measured values.
WORKPLACE_TYPES = ("remote", "hybrid", "on-site")

#: What a run reads when nobody says otherwise. `on-site` is excluded because
#: it holds 72,682 postings against a 10,000 ceiling, so a default that
#: included it would silently read 14% of one partition and call it Gupy.
DEFAULT_WORKPLACE_TYPES = ("remote", "hybrid")

#: A partition name goes into a URL, so it is checked against the measured set
#: rather than escaped and hoped for.
_WORKPLACE = re.compile(r"^[a-z][a-z-]{1,15}$")


#: Every value the vendor's `type` field takes, read off 7,090 collected
#: payloads on 2026-09-09 rather than guessed.
#:
#: **This is the Brazilian contract regime, stated structurally**, which is the
#: thing `contract_regime` has been NULL for since the column existed:
#: `vacancy_type_effective` is CLT and `vacancy_legal_entity` is PJ.
#:
#: It is also an EXHAUSTIVE partition, verified by measurement: over the
#: on-site slice the fourteen values sum to 73,299 against a declared 72,682,
#: so every posting carries one. (The excess is the feed moving between
#: requests, not double counting.) That is what makes it the first dimension
#: to split on: a split that loses rows is not a partition.
#:
#: `vacancy_type_talent_pool` is 9,343 of the on-site slice and is a TALENT
#: POOL rather than an opening. It is collected like the rest -- deciding what
#: a person wants to see is the filter layer's job, not the collector's -- and
#: it is named here so nobody reads that count as vacancies.
CONTRACT_TYPES = (
    "vacancy_type_effective",
    "vacancy_legal_entity",
    "vacancy_type_temporary",
    "vacancy_type_internship",
    "vacancy_type_associate",
    "vacancy_type_talent_pool",
    "vacancy_type_outsource",
    "vacancy_type_autonomous",
    "vacancy_type_parter",
    "vacancy_type_lecturer",
    "vacancy_type_freelancer",
    "vacancy_type_intermittent",
    "vacancy_type_apprentice",
    "vacancy_type_trainee",
)

#: Brazil's twenty-seven federative units, in the vendor's own spelling.
#:
#: Hardcoded, and that is defensible where a city list would not be: the set is
#: constitutional and has not changed since 1988. Measured coverage over the
#: largest cell (on-site, CLT): the twenty-seven sum to 56,374 of 56,920, so
#: **1% of postings carry no state at all**. A split on this dimension is
#: therefore NOT exhaustive, which is why `plan_slices` walks the parent as
#: well as the children rather than only the children.
BRAZILIAN_STATES = (
    "São Paulo",
    "Minas Gerais",
    "Rio de Janeiro",
    "Bahia",
    "Paraná",
    "Rio Grande do Sul",
    "Pernambuco",
    "Ceará",
    "Pará",
    "Santa Catarina",
    "Goiás",
    "Maranhão",
    "Paraíba",
    "Espírito Santo",
    "Amazonas",
    "Rio Grande do Norte",
    "Alagoas",
    "Mato Grosso",
    "Piauí",
    "Distrito Federal",
    "Mato Grosso do Sul",
    "Sergipe",
    "Rondônia",
    "Tocantins",
    "Acre",
    "Amapá",
    "Roraima",
)

#: The order slices are cut in, and the order is the whole design.
#:
#: `workplaceType` first because it is three values and every posting has one.
#: `type` second because it is exhaustive and cuts the largest cell by 4x.
#: `state` third because it is 99% exhaustive. `city` last, and its values are
#: LEARNED from what has already been collected rather than hardcoded, because
#: a city list is unbounded and a stale one silently loses a city.
PARTITION_ORDER = ("workplaceType", "type", "state", "city")

#: The most queries one plan may contain, whatever the feed answers.
#:
#: The same kind of guard as `feeds.MAX_PAGES_CAP` and for the same reason: a
#: plan is a cost that lands on somebody else's server, and every slice costs a
#: probe plus a walk. Against the real feed on 2026-09-09 the plan came to 45
#: slices, so this is roughly a four-times headroom rather than a limit anybody
#: is expected to meet.
#:
#: It is not hypothetical. A vendor that answered the same total for every
#: query -- which a stub does, and which a broken endpoint might -- would make
#: every cell look too big and drive the planner to the bottom of every
#: dimension: three workplace types times fourteen contract types times
#: twenty-seven states is 1,134 queries for a feed nobody could partition. The
#: plan stops at this number and the caller reports that it did.
MAX_SLICES = 200


class GupyError(ValueError):
    """This adapter refused to do something, and says which."""


GUPY_FIELD_MAP = ProviderFieldMap(
    mappings=(
        FieldMapping(path="workplaceType", dimension=MetadataDimension.WORK_MODEL_HINT),
        FieldMapping(path="type", dimension=MetadataDimension.EMPLOYMENT_TYPE_HINT),
    ),
)


def assert_trusted(url: str) -> str:
    """A URL this adapter is willing to fetch, or an exception.

    Get on Board's adapter carries the same guard for the same reason: a feed
    response is third-party input, and an adapter that follows a link out of
    one has handed the vendor the ability to point this program anywhere.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != API_HOSTNAME:
        raise GupyError(f"refusing to fetch a URL outside {API_HOSTNAME}: {url!r}")
    return url


def _text(record: Any, key: str) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get(key)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def compose_location(record: Any) -> str | None:
    """`city, state, country`, skipping whatever the record left empty.

    Composed rather than stored as three columns because `job.location_raw` is
    one column and the gazetteer reads a string. Measured: this resolves --
    `Cachoeira do Sul, Rio Grande do Sul, Brasil` gives `BR` and `LATAM`, and
    so does `Bogota, Colombia` from the same feed.

    The country is LAST, which is the Brazilian convention and also the one
    `resolve_place` is least likely to mis-token: a bare `Rio Grande do Sul`
    resolves to nothing, correctly, and the country carries the answer.
    """
    parts = [_text(record, "city"), _text(record, "state"), _text(record, "country")]
    kept = [part for part in parts if part]
    return ", ".join(kept) or None


def company_of(record: Any) -> str | None:
    """The employer, as the employer's own career page names itself."""
    return _text(record, "careerPageName")


def public_url(record: Any) -> str | None:
    """The employer's own posting page, on the employer's own subdomain.

    `jobUrl` and never a URL this adapter composes. A portal link would make
    every one of 81,310 postings look like it came from one place, which is
    the attribution failure ADR-0008 exists to prevent.
    """
    url = _text(record, "jobUrl")
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".gupy.io"):
        return None
    return url


def _external_id(record: Any) -> str | None:
    if not isinstance(record, dict):
        return None
    value = record.get("id")
    if isinstance(value, int):
        return f"gupy-{value}"
    if isinstance(value, str) and value.strip():
        return f"gupy-{value.strip()}"
    return None


def to_stub(record: Any) -> PostingStub | None:
    """One feed record as a stub, or None when it cannot be addressed.

    Three fields are required and the rest are optional: an id, a title and an
    employer-owned URL. A record missing any of them is not a posting this
    product can file, and inventing a substitute for one is how a corpus grows
    rows nobody can open.
    """
    external_id = _external_id(record)
    title = _text(record, "name")
    url = public_url(record)
    if not external_id or not title or not url:
        return None

    return PostingStub(
        external_id=external_id,
        title=title,
        url=url,
        location_raw=compose_location(record),
        department=None,
        posted_at=to_rfc3339_utc(_text(record, "publishedDate")),
        description_html=_text(record, "description"),
        payload=dict(record) if isinstance(record, dict) else {},
    )


@dataclass(frozen=True)
class Slice:
    """One query this collector will walk, and what the vendor says it holds.

    A slice is a SET OF FILTERS, not a subset of the feed. Two slices may
    overlap and that costs requests rather than correctness: the collector
    deduplicates by `external_id` before anything is written. What must never
    happen is a GAP, and `over_ceiling` is how one is reported when the
    dimensions run out before the cell fits.
    """

    filters: tuple[tuple[str, str], ...]
    declared_total: int | None
    #: True when this cell still exceeds what a walk can reach and there is no
    #: dimension left to cut it by. The postings past the ceiling are not
    #: collected and nothing here pretends otherwise.
    over_ceiling: bool = False

    @property
    def as_params(self) -> dict[str, str]:
        return dict(self.filters)

    @property
    def label(self) -> str:
        return " / ".join(value for _, value in self.filters) or "everything"


def plan_slices(
    total_for: Callable[[dict[str, str]], int | None],
    values_for: Callable[[str, dict[str, str]], Sequence[str]],
    *,
    ceiling: int,
    seed: dict[str, str] | None = None,
    dimensions: Sequence[str] = PARTITION_ORDER,
    budget: int = MAX_SLICES,
) -> list[Slice]:
    """Cut the feed into queries small enough that a walk can reach the end.

    Pure over two callables, so the whole plan is testable without a socket.

    The rule is simple and the exception is the interesting part:

    * a cell the vendor says fits under `ceiling` becomes one slice;
    * a cell that does not is cut by the next dimension, **and the parent is
      kept as well**. Keeping the parent is not redundancy. `state` is only
      99% exhaustive -- 546 of 56,920 postings in the largest cell carry no
      state -- so the children alone would silently drop them, and walking the
      parent to its own ceiling recovers what the children cannot name;
    * a cell with no dimensions left is returned with `over_ceiling` set, and
      the caller reports it rather than rounding it away.

    A dimension whose vocabulary is empty is skipped rather than treated as a
    failed cut: `city` is learned from the corpus, and on a first run there is
    nothing to learn from.

    `budget` stops a plan from growing without bound. It is not a nicety: a
    feed answering the same total for every query drives the recursion to the
    bottom of every dimension, and the caller needs the plan it can afford
    rather than the one the arithmetic implies.
    """
    seed = dict(seed or {})
    total = total_for(seed)

    if total is not None and total <= ceiling:
        return [Slice(filters=tuple(sorted(seed.items())), declared_total=total)]

    for index, dimension in enumerate(dimensions):
        if dimension in seed:
            continue
        values = values_for(dimension, seed)
        if not values:
            continue
        rest = dimensions[index + 1 :]
        # The parent, walked to its own ceiling. See the docstring: this is
        # what keeps a non-exhaustive cut from losing rows.
        plan: list[Slice] = [
            Slice(filters=tuple(sorted(seed.items())), declared_total=total, over_ceiling=True)
        ]
        for value in values:
            if len(plan) >= budget:
                # Out of budget with children left to cut. The parent above is
                # already in the plan and already flagged `over_ceiling`, so
                # what this loses is depth rather than a whole branch.
                break
            child = {**seed, dimension: value}
            plan.extend(
                plan_slices(
                    total_for,
                    values_for,
                    ceiling=ceiling,
                    seed=child,
                    dimensions=(dimension, *rest),
                    budget=max(1, budget - len(plan)),
                )
            )
        return plan

    return [Slice(filters=tuple(sorted(seed.items())), declared_total=total, over_ceiling=True)]


@dataclass(frozen=True)
class PartitionRead:
    """One `workplaceType` slice, and whether the ceiling cut it short.

    `hit_ceiling` is kept apart from the budget's own `stopped_early` because
    they are different facts: one is this product choosing to stop, the other
    is the vendor refusing to serve past offset 10,000. A caller that cannot
    tell them apart reports a wall as a decision.
    """

    workplace_type: str
    walk: BoundedWalk
    claimed_total: int | None
    hit_ceiling: bool

    @property
    def records(self) -> tuple[Any, ...]:
        return self.walk.entries


class GupyProvider(JobProvider):
    """The candidate portal, one bounded walk per workplace type."""

    name = PROVIDER
    kind = ProviderKind.ATS
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    field_map = GUPY_FIELD_MAP
    capabilities = ProviderCapabilities(
        #: The whole advert arrives in the listing. No detail request exists in
        #: this adapter at all.
        full_description_in_list=True,
        obtains_full_description=True,
        exposes_posted_date=True,
        exposes_department=False,
        #: No salary field of any kind in the measured record shape. Brazilian
        #: postings very often state `A combinar`, and a field that is not
        #: there cannot be read optimistically.
        exposes_compensation=False,
        exposes_location_structured=True,
        exposes_remote_flag=True,
        exposes_employment_type=True,
        #: FALSE, and the module docstring says why at length. `country` is
        #: where the work is, never where the employer may hire.
        publishes_hiring_scope=False,
    )

    def __init__(
        self,
        fetcher: HttpFetcher,
        api_host: str = API_HOST,
        max_pages: int | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._api_host = api_host.rstrip("/")
        #: Pages of 100, per partition. `PageBudget` has no way to say
        #: unlimited and that is deliberate for every feed here.
        self._budget = PageBudget.resolve(PER_PAGE, max_pages)

    # -- URLs --------------------------------------------------------------

    def slice_url(self, filters: dict[str, str], offset: int) -> str:
        """One page of any slice the planner produced.

        Every filter VALUE is quoted and every filter NAME is checked against
        the measured set, because these become a URL and a feed response is
        untrusted input. A name nobody measured is refused rather than passed
        through: an unknown parameter this vendor answers 400 for would read as
        a broken source, and one it silently ignores would read as a partition
        that is not one.
        """
        unknown = sorted(set(filters) - set(PARTITION_ORDER))
        if unknown:
            raise GupyError(f"unmeasured filter(s) {unknown}; known are {list(PARTITION_ORDER)}")
        if offset < 0 or offset >= MAX_OFFSET:
            raise GupyError(
                f"offset {offset} is outside what this feed serves: it answers 400 at "
                f"{MAX_OFFSET} and above, measured 2026-09-05"
            )
        query = f"limit={PER_PAGE}&offset={offset}"
        for name in PARTITION_ORDER:
            if name in filters:
                query += f"&{name}={quote(str(filters[name]))}"
        return assert_trusted(f"{self._api_host}/jobs?{query}")

    def slice_count_url(self, filters: dict[str, str]) -> str:
        """The `limit=10` probe for a slice, which is the only honest total."""
        unknown = sorted(set(filters) - set(PARTITION_ORDER))
        if unknown:
            raise GupyError(f"unmeasured filter(s) {unknown}")
        query = "limit=10&offset=0"
        for name in PARTITION_ORDER:
            if name in filters:
                query += f"&{name}={quote(str(filters[name]))}"
        return assert_trusted(f"{self._api_host}/jobs?{query}")

    def slice_total(self, filters: dict[str, str], *, use_cache: bool = True) -> int | None:
        body = self._body(self.slice_count_url(filters), use_cache=use_cache)
        pagination = body.get("pagination")
        if isinstance(pagination, dict):
            total = pagination.get("total")
            if isinstance(total, int):
                return total
        return None

    def read_slice(self, slice_: Slice, *, use_cache: bool = True) -> PartitionRead:
        """Walk one planned slice to its end, its budget, or the vendor's wall."""
        ceiling_hit = False
        filters = slice_.as_params

        def read_page(page: int) -> FeedPage:
            nonlocal ceiling_hit
            offset = (page - 1) * PER_PAGE
            if offset >= MAX_OFFSET:
                ceiling_hit = True
                return FeedPage(url="", entries=(), page=page)
            url = self.slice_url(filters, offset)
            body = self._body(url, use_cache=use_cache)
            data = body.get("data")
            entries = (
                tuple(item for item in data if isinstance(item, dict))
                if isinstance(data, list)
                else ()
            )
            return FeedPage(url=url, entries=entries, page=page)

        walk = walk_feed(scope=slice_.label, read_page=read_page, budget=self._budget, first_page=1)
        return PartitionRead(
            workplace_type=slice_.label,
            walk=walk,
            claimed_total=slice_.declared_total,
            hit_ceiling=ceiling_hit,
        )

    def plan(
        self,
        *,
        workplace_types: Sequence[str] | None = None,
        cities_for: Callable[[str], Sequence[str]] | None = None,
        use_cache: bool = True,
    ) -> list[Slice]:
        """The slices this run will walk, measured against the live feed.

        `cities_for` supplies the last dimension and is LEARNED rather than
        listed: a caller hands in the cities already seen for a state, so the
        plan sharpens as the corpus grows and a first run simply stops one
        level higher. Passing nothing is a valid first run.
        """
        chosen = tuple(workplace_types or WORKPLACE_TYPES)

        def total_for(filters: dict[str, str]) -> int | None:
            return self.slice_total(filters, use_cache=use_cache)

        def values_for(dimension: str, seed: dict[str, str]) -> Sequence[str]:
            if dimension == "type":
                return CONTRACT_TYPES
            if dimension == "state":
                return BRAZILIAN_STATES
            if dimension == "city" and cities_for is not None:
                return cities_for(seed.get("state", ""))
            return ()

        # The reachable ceiling is the SMALLER of what the vendor serves and
        # what this product's own page budget allows. Both are real walls and
        # the plan has to respect whichever binds first.
        ceiling = min(MAX_OFFSET, self._budget.page_size * self._budget.max_pages)

        # ONE PLAN PER REQUESTED WORKPLACE TYPE, rather than one plan that may
        # cut by workplace type if the arithmetic happens to need it.
        #
        # `workplace_types` is a caller's SELECTION and not an optimisation to
        # discard. Seeding the recursion from an empty filter set let a small
        # feed collapse to a single unfiltered query -- correct arithmetic, and
        # it quietly ignored what the caller asked for. A selection has to be a
        # floor the planner cuts BELOW, never a dimension it may skip.
        plan: list[Slice] = []
        budget = MAX_SLICES
        for workplace_type in chosen:
            if budget <= 0:
                break
            cut = plan_slices(
                total_for,
                values_for,
                ceiling=ceiling,
                seed={"workplaceType": workplace_type},
                dimensions=PARTITION_ORDER[1:],
                budget=budget,
            )
            plan.extend(cut)
            budget -= len(cut)
        return plan

    def feed_url(self, workplace_type: str, offset: int) -> str:
        if not _WORKPLACE.match(workplace_type) or workplace_type not in WORKPLACE_TYPES:
            raise GupyError(
                f"unknown workplace type {workplace_type!r}; measured values are "
                f"{', '.join(WORKPLACE_TYPES)}"
            )
        if offset < 0 or offset >= MAX_OFFSET:
            raise GupyError(
                f"offset {offset} is outside what this feed serves: it answers 400 at "
                f"{MAX_OFFSET} and above, measured 2026-09-05"
            )
        return assert_trusted(
            f"{self._api_host}/jobs"
            f"?limit={PER_PAGE}&offset={offset}&workplaceType={quote(workplace_type)}"
        )

    def count_url(self, workplace_type: str) -> str:
        """A `limit=10` probe, which is the only shape whose `total` is real."""
        if workplace_type not in WORKPLACE_TYPES:
            raise GupyError(f"unknown workplace type {workplace_type!r}")
        return assert_trusted(
            f"{self._api_host}/jobs?limit=10&offset=0&workplaceType={quote(workplace_type)}"
        )

    # -- reading -----------------------------------------------------------

    def _body(self, url: str, *, use_cache: bool = True) -> dict[str, Any]:
        body = self._fetcher.get_json(url, use_cache=use_cache)
        if not isinstance(body, dict):
            raise GupyError(f"{url} returned {type(body).__name__}, not an object")
        return body

    def claimed_total(self, workplace_type: str, *, use_cache: bool = True) -> int | None:
        """How many postings this partition says it holds.

        A SEPARATE request at `limit=10`, because the paginated response lies:
        at `limit=100` this vendor reports `total: 100`. Recorded for the run
        stats and never used to end a walk.
        """
        body = self._body(self.count_url(workplace_type), use_cache=use_cache)
        pagination = body.get("pagination")
        if isinstance(pagination, dict):
            total = pagination.get("total")
            if isinstance(total, int):
                return total
        return None

    def read_partition(self, workplace_type: str, *, use_cache: bool = True) -> PartitionRead:
        """Walk one `workplaceType` to its end, its budget, or the ceiling."""
        ceiling_hit = False

        def read_page(page: int) -> FeedPage:
            nonlocal ceiling_hit
            offset = (page - 1) * PER_PAGE
            if offset >= MAX_OFFSET:
                # Not an error: the vendor serves no further. Reported as a
                # ceiling so the difference from "we chose to stop" survives.
                ceiling_hit = True
                return FeedPage(url="", entries=(), page=page)
            url = self.feed_url(workplace_type, offset)
            body = self._body(url, use_cache=use_cache)
            data = body.get("data")
            entries = (
                tuple(item for item in data if isinstance(item, dict))
                if isinstance(data, list)
                else ()
            )
            return FeedPage(url=url, entries=entries, page=page)

        walk = walk_feed(
            scope=workplace_type, read_page=read_page, budget=self._budget, first_page=1
        )
        return PartitionRead(
            workplace_type=workplace_type,
            walk=walk,
            claimed_total=self.claimed_total(workplace_type, use_cache=use_cache),
            hit_ceiling=ceiling_hit,
        )

    def read_partitions(
        self, workplace_types: tuple[str, ...] | None = None, *, use_cache: bool = True
    ) -> tuple[PartitionRead, ...]:
        chosen = workplace_types or DEFAULT_WORKPLACE_TYPES
        return tuple(self.read_partition(w, use_cache=use_cache) for w in chosen)

    # -- the JobProvider protocol -----------------------------------------

    def board_url(self, board: BoardRef) -> str:
        """The employer's own career page, which is what a board means here."""
        return f"https://{board.board_identifier}.gupy.io"

    def validate_board(self, board: BoardRef) -> str | None:
        """There is no per-employer endpoint on this feed, so nothing to check.

        The portal is queried by workplace type and returns every employer at
        once; a board here is a bookkeeping unit created from what arrived, not
        a place this adapter can visit. Saying so is more useful than a check
        that always passes.
        """
        return None

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        raise GupyError(
            "Gupy is read by workplace-type partition, not per board. "
            "Use read_partitions(); the collector does."
        )

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """No request. The advert was already in the listing.

        The signature keeps the protocol's shape so the collector reads like
        its siblings, and the absence of a fetch is the economic point of this
        source rather than an omission.
        """
        html = stub.description_html or ""
        return RawPosting(
            stub=stub,
            description_html=html,
            description_text=_strip_html(html),
            payload=dict(stub.payload),
        )


_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n{3,}")


def _strip_html(html: str) -> str:
    """The advert as text, with paragraph breaks kept.

    Deliberately not a parser dependency: this feed's HTML is the small subset
    a rich-text editor emits, and the product already has no runtime
    dependencies in this layer. Block tags become newlines so that a list of
    requirements does not collapse into one line, which would make the evidence
    quotes ADR-0002 verifies harder to read than the employer wrote them.
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

"""a16z Speedrun Talent Network: a read-only jobs API, and an aggregator.

    GET https://speedrun-talent-network.com/api/v1/jobs?scope=...&page=N
    GET https://speedrun-talent-network.com/api/v1/jobs/{id}

WHAT IT IS, AND WHAT THE SOURCE MATRIX USED TO SAY IT WAS
---------------------------------------------------------
`config/source_catalogue.yaml` described this source as "a curated matching
programme with an application form, not a feed", coverage UNSUPPORTED, with the
reason "there is no feed to read". That was wrong. The first-party developer
page at `https://speedrun-talent-network.com/developers` documents a public,
read-only, unauthenticated REST API with an OpenAPI 3.1 specification, and the
`/jobs` endpoint returns 48,129 open roles at its widest scope.

THIS ADAPTER IS AN AGGREGATOR, NOT A FOURTH ATS
-----------------------------------------------
`kind = ProviderKind.AGGREGATOR`, and everything follows from that.

The postings here originate in other systems. `apply.url` on the detail
endpoint is the employer's real application link, and in the sample taken while
this was written every one of them pointed at an ATS this project already
collects from directly. So the same posting can arrive twice: once from the
employer's own board, once republished here.

The rule is the one `docs/research/source-expansion-speedrun.md` section 4
recorded before any of this was built: identity resolution happens BEFORE
insertion, and richer ATS data is never replaced by a poorer duplicate. What
makes that safe is that the identity is deterministic rather than fuzzy, and
the reason it can be is in `providers/base.py::PostingUrlRecogniser`: an ATS
adapter recognises its own apply URL and returns its own `external_id`. Against
the archived corpus that recognises 21,136 of 21,136 job URLs with a
byte-identical id, and no adapter claims another's.

THE TEXT HERE IS THE AGGREGATOR'S COPY
---------------------------------------
`description_text` is capped at 15,000 characters by the published schema and
is null on closed roles. It is republished text, and it must never be labelled
as the employer's original. That is what `ProviderKind.AGGREGATOR` carries
through `access_method_for`, and it is why a posting we can reach at its ATS is
kept at its ATS.

TWO MEASURED FACTS ABOUT THE LIVE API THAT SHAPE THIS CODE
-----------------------------------------------------------
Both were measured on 2026-09-05, and both are the kind of thing a paginator
gets wrong silently.

1. **Intermittent HTTP 500.** Twelve identical requests returned eight 500s and
   four 200s. The error body is `{"error":{"code":"internal","message":"query
   failed"}}` with `Cache-Control: no-store`; a success carries
   `s-maxage=60`, so once a page lands it stays cached for a minute. This is
   why `SPEEDRUN_MAX_ATTEMPTS` is well above the shared default. It is NOT a
   reason to treat a failure as an empty page: `HttpFetcher` raises on an
   exhausted retry and this module never converts that into "no jobs", which
   is the same discipline `pipeline/collect.py` documents for boards.

2. **`page` above 200 is silently clamped.** The spec declares
   `page: maximum 200`, but a request for page 300, 500, 962 or 1200 returns
   HTTP 200 carrying page 200's contents, with `page: 200` echoed in the body.
   It does not error. So a paginator that trusts `total_pages` (963 at the
   widest scope) would re-read one page forever and believe it had everything.

   `walk_feed` therefore stops when the echoed page stops matching the
   requested one, and reports the gap between what the feed says it holds and
   what the API will actually serve. That number is the difference between a
   bounded retrieval and a silent truncation, so it is returned rather than
   logged.

WHAT IS NEVER CALLED
--------------------
The OpenAPI specification contains eight paths and every one of them is a GET.
`join_network` and `express_interest` are documented on the developer page as
consent-required actions and appear nowhere in the v1 REST surface. This module
constructs no request to them, sends no candidate data, and has no code path
that could. `tests/unit/test_speedrun.py` asserts both halves, in
`test_no_write_action_is_named_anywhere_in_the_adapter` (the strings) and
`test_every_request_this_adapter_makes_is_a_get_to_a_read_path` (the behaviour).

ATTRIBUTION
-----------
The developer page asks callers to self-identify with `?source=` and to link to
the canonical `url` rather than re-hosting. Both are honoured:
`SOURCE_TAG` is sent on every request, and the canonical URL is what is stored
and what the interface links to.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import MetadataDimension
from career_agent.domain.normalize import html_to_text
from career_agent.domain.timestamps import to_rfc3339_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
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
    parse_amount,
    stated_amounts,
)

API_BASE = "https://speedrun-talent-network.com/api/v1"
PUBLIC_BASE = "https://speedrun-talent-network.com"

#: The attribution tag the developer page asks agents to send. Its documented
#: pattern is `^[a-zA-Z0-9._:-]+$`, max 64 characters.
#:
#: It is echoed back in the response body and, notably, appended to every
#: canonical job URL as `utm_source`. That is why `canonical_url` drops the
#: query string before comparing two URLs: the tracking parameters we asked for
#: would otherwise make our own postings look different from anybody else's.
SOURCE_TAG = "career-agent"

#: A page holds 50, declared `const` in the response schema and confirmed live.
DECLARED_PAGE_SIZE = 50

#: The last page the API will actually serve. Above this it clamps silently.
MAX_PAGE = 200

#: Eight times, not three. See the intermittent-500 note in the module
#: docstring: at the observed failure rate the shared default of 3 would lose
#: roughly a third of all pages, and a lost page in a feed walk is a hole in
#: the middle of the corpus rather than a visible error.
SPEEDRUN_MAX_ATTEMPTS = 8

#: The scopes the API offers, widest last. `portfolio` is the documented
#: default. They are a property of the source, so they live here rather than in
#: preferences: which slice of a feed to read is a collection decision.
SCOPES = ("speedrun", "portfolio", "everywhere")


SPEEDRUN_CAPABILITIES = ProviderCapabilities(
    # The list response carries no description at all. Every posting needs a
    # second request to `/jobs/{id}`, which is the single largest cost of this
    # source and the reason retrieval is bounded by an explicit page limit.
    full_description_in_list=False,
    # AND YET THE STORED TEXT IS COMPLETE. This is the pair of flags that
    # made the second one necessary: the list response carries nothing,
    # and the adapter then requests `/jobs/{id}` for every posting and
    # stores what comes back. A second request is a cost, not a limit.
    obtains_full_description=True,
    # `published_at`, RFC 3339 with an offset, on every sampled posting.
    exposes_posted_date=True,
    # `function` is a coarse org unit (engineering, research, product, design,
    # sales, marketing, operations, other) and is carried as `department`. It
    # is one level rather than the two Ashby and Lever give, which is a fact
    # about this source, not a defect.
    exposes_department=True,
    # `comp_min` / `comp_max` / `comp_currency` / `comp_period` as numbers, plus
    # `comp_summary` as display text on the detail endpoint.
    exposes_compensation=True,
    # `location` is one free-text string. There is no locality, region or
    # country field, so the SHAPE is not structured, and saying otherwise would
    # make a missing country read as "the employer did not say" rather than as
    # "this source has no such field".
    exposes_location_structured=False,
    # `workplace_type` is Onsite | Hybrid | Remote.
    exposes_remote_flag=True,
    # `employment_type` is FullTime | PartTime | Intern | Contract | Temporary.
    exposes_employment_type=True,
)


SPEEDRUN_FIELD_MAP = ProviderFieldMap(
    (
        # `workplace_type`, deliberately NOT `remote`.
        #
        # This source supplies both, and they are not equivalent: `remote` is a
        # boolean and `workplace_type` is the three-way answer. Ashby carries
        # exactly the same pair and this project made the same choice there for
        # the same reason, written out in `providers/ashby.py`: taking a vendor's
        # pre-collapsed boolean imports someone else's judgement as though it
        # were data, and "remote does not necessarily mean remote for me" is the
        # founding complaint of this product. `remote` stays archived, unmapped.
        FieldMapping("workplace_type", MetadataDimension.WORK_MODEL_HINT),
        # One free-text location. One assertion, so one path, unlike Ashby's
        # three. Absence of a country field is why `exposes_location_structured`
        # is False above.
        FieldMapping("location", MetadataDimension.HIRING_LOCATION_HINT),
        FieldMapping("employment_type", MetadataDimension.EMPLOYMENT_TYPE_HINT),
        # The display string the source composed for a human, on the detail
        # endpoint only. It travels and is never parsed into numbers, exactly as
        # `CompensationHint.raw_text` documents: a rendered string is a rounding
        # of a measurement, not the measurement.
        FieldMapping("comp_summary", MetadataDimension.COMPENSATION_HINT),
    )
)

# Archived but unmapped, each for a stated reason:
#   remote           - a lossy derivative of `workplace_type`, mapped above
#   seniority        - founding | exec | staff | senior | junior | intern. There
#                      is no seniority MetadataDimension, and inventing one for
#                      a single source would put a dimension in the contract
#                      that three of four providers cannot speak to
#   function         - already carried as PostingStub.department
#   tier, cohort     - speedrun | a16z | market | universe, and SR007-style tags.
#                      These describe the INVESTOR's relationship to the company,
#                      not the job. Real provenance, no canonical dimension
#   stealth          - whether the company name is masked. A property of the
#                      listing, recorded in the payload, never a claim about work
#   company_slug,    - identity, resolved by the collector rather than by the
#   company_url        field map
#   apply            - the ORIGIN pointer, and the most load-bearing field here.
#                      It is not a metadata hint about the work; it is how a
#                      posting is matched to its ATS record. Read by
#                      `origin_url`, not by the field map


class SpeedrunProvider(JobProvider):
    """The Speedrun feed, read two ways.

    `list_postings(board)` reads ONE company, because that is what the
    `JobProvider` contract means by a board and because ADR-0008 says a board
    belongs to one company. Registering this feed as a single board carrying
    hundreds of employers' postings is precisely the attribution failure that
    ADR-0008 exists to prevent, so it is not done.

    `walk_feed()` is the bulk path and is not part of the protocol. It reads the
    paginated feed 50 postings at a time and lets the collector group by
    company, which costs one request per 50 postings instead of one per company.
    Both paths produce the same stubs.
    """

    name = "speedrun"
    kind = ProviderKind.AGGREGATOR
    retrieval_mode = RetrievalMode.AGGREGATOR_FEED
    capabilities = SPEEDRUN_CAPABILITIES
    field_map = SPEEDRUN_FIELD_MAP
    #: FALSE, and it inherited True until 2026-09-09, which produced a wrong
    #: answer rather than a slow one.
    #:
    #: `board_providers()` reads this to decide what `discover` may probe, so
    #: this adapter was asked "does Reformation have a board here?" -- a
    #: question about a per-employer board on a feed that has none. It does not
    #: raise: `list_postings` filters one talent-network feed by an identifier
    #: nothing matches and returns an EMPTY LIST, which discovery reads as
    #: `VALID_EMPTY`, which means A REAL BOARD WITH NO OPENINGS TODAY. Two
    #: fictional boards were reported as supported in one run before this was
    #: noticed, and promoting one would have added a permanently empty board to
    #: every collection pass thereafter.
    #:
    #: An AGGREGATOR republishes other people's postings and has no board per
    #: employer by construction. `tests/unit/test_retrieval_vocabulary.py`
    #: asserts that for every adapter now, so the next aggregator cannot
    #: inherit its way back into this.
    addresses_boards_by_company = False

    def __init__(self, fetcher: HttpFetcher, api_base: str = API_BASE) -> None:
        self._fetcher = fetcher
        self._api_base = api_base.rstrip("/")

    # -- URLs --------------------------------------------------------------

    def board_url(self, board: BoardRef) -> str:
        return board.board_url or f"{PUBLIC_BASE}/companies/{board.company_slug}"

    def jobs_url(self, page: int = 0, **filters: str) -> str:
        """One `/jobs` query, with the attribution tag always attached."""
        from urllib.parse import urlencode

        params: dict[str, str] = {"page": str(page), "source": SOURCE_TAG}
        params.update({key: value for key, value in filters.items() if value})
        return f"{self._api_base}/jobs?{urlencode(sorted(params.items()))}"

    def detail_url(self, external_id: str) -> str:
        from urllib.parse import urlencode

        return f"{self._api_base}/jobs/{external_id}?{urlencode({'source': SOURCE_TAG})}"

    # -- listing one company, which is what a board means -------------------

    def list_postings(self, board: BoardRef) -> Iterator[PostingStub]:
        """Every open posting this feed holds for one company.

        `board_identifier` is the company DISPLAY key, not the slug, because
        that is what the `company` filter accepts. Measured: `company=Abridge`
        returns 40 postings and `company=abridge` returns 0. The slug is kept on
        the company record, where it identifies the company; the display key is
        kept on the board, where it addresses the query.
        """
        for page in range(MAX_PAGE + 1):
            envelope = self._read_page(self.jobs_url(page=page, company=board.board_identifier))
            for entry in envelope.jobs:
                stub = self._to_stub(entry)
                if stub is not None:
                    yield stub
            if not envelope.has_more(page):
                return

    # -- listing the whole feed, which is not a board -----------------------

    def walk_feed(
        self,
        scope: str = "portfolio",
        max_pages: int | None = None,
        on_page: Any = None,
        should_stop: Any = None,
    ) -> FeedWalk:
        """Read the paginated feed and return what was read, and what was not.

        `max_pages` bounds the walk. `None` means "as far as the API will
        serve", which is 201 pages, NOT `total_pages`.

        The returned object always says how many postings the feed claims to
        hold and how many were actually reachable. A caller that stops early,
        and a caller that hit the API's own ceiling, are told which happened.
        """
        pages: list[FeedPage] = []
        stopped_early = False
        for page in range(MAX_PAGE + 1):
            if max_pages is not None and page >= max_pages:
                stopped_early = True
                break
            if should_stop is not None and should_stop():
                stopped_early = True
                break
            envelope = self._read_page(self.jobs_url(page=page, scope=scope))
            pages.append(envelope)
            if on_page is not None:
                on_page(envelope)
            if not envelope.has_more(page):
                break
        return FeedWalk(scope=scope, pages=tuple(pages), stopped_early=stopped_early)

    # -- reading one page --------------------------------------------------

    def _read_page(self, url: str) -> FeedPage:
        payload = self._fetcher.get_json(url, use_cache=False)
        if not isinstance(payload, dict) or "jobs" not in payload:
            raise FetchError(
                FetchErrorCategory.MALFORMED, url, "response did not contain a 'jobs' key"
            )
        jobs = payload["jobs"]
        if not isinstance(jobs, list):
            raise FetchError(FetchErrorCategory.MALFORMED, url, "'jobs' was not a list")
        return FeedPage(
            url=url,
            jobs=tuple(entry for entry in jobs if isinstance(entry, dict)),
            page=_as_int(payload.get("page")),
            page_size=_as_int(payload.get("page_size")) or DECLARED_PAGE_SIZE,
            total=_as_int(payload.get("total")),
            total_pages=_as_int(payload.get("total_pages")),
            echoed_source=(str(payload["source"]) if payload.get("source") else None),
        )

    def _to_stub(self, entry: dict[str, Any]) -> PostingStub | None:
        """Translate one listing entry.

        A single unusable entry is skipped rather than failing the page: losing
        one malformed posting is better than losing fifty.
        """
        external_id = entry.get("id")
        title = entry.get("title")
        url = entry.get("url")
        if not external_id or not title or not url:
            return None
        location = entry.get("location")
        function = entry.get("function")
        return PostingStub(
            external_id=str(external_id),
            title=str(title),
            url=str(url),
            location_raw=(str(location).strip() or None) if location else None,
            department=(str(function).strip() or None) if function else None,
            posted_at=to_rfc3339_utc(entry.get("published_at")),
            # Deliberately empty. The list response carries no description, and
            # an empty string here is what makes `fetch_posting` do its second
            # request rather than silently storing a posting with no text.
            description_html=None,
            payload=entry,
        )

    # -- completing a posting ----------------------------------------------

    def fetch_posting(self, board: BoardRef, stub: PostingStub) -> RawPosting:
        """Fetch the full record. One request per posting; there is no batch.

        The detail payload REPLACES the listing payload rather than merging into
        it. They describe the same posting and the detail response is a strict
        superset, so merging would only create a chance for two fields to
        disagree about which one was archived.

        `description_text` is plain text already; the schema says so and the
        sampled values confirm it. It is still passed through `html_to_text`
        when it looks like markup, because a source that starts returning HTML
        would otherwise put raw tags into the corpus and into every quote taken
        from it.
        """
        payload = self._fetcher.get_json(self.detail_url(stub.external_id), use_cache=False)
        if not isinstance(payload, dict) or "job" not in payload:
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                self.detail_url(stub.external_id),
                "response did not contain a 'job' key",
            )
        job = payload["job"]
        if not isinstance(job, dict):
            raise FetchError(
                FetchErrorCategory.MALFORMED,
                self.detail_url(stub.external_id),
                "'job' was not an object",
            )

        raw_text = str(job.get("description_text") or "")
        looks_like_markup = "<" in raw_text and ">" in raw_text
        text = html_to_text(raw_text) if looks_like_markup else raw_text
        return RawPosting(
            stub=stub,
            description_html=raw_text if looks_like_markup else "",
            description_text=text,
            payload=job,
        )


# =========================================================================
# what a page and a walk are
# =========================================================================


@dataclass(frozen=True)
class FeedPage:
    """One `/jobs` response, with the numbers it reported about itself."""

    url: str
    jobs: tuple[dict[str, Any], ...]
    page: int | None
    page_size: int
    total: int | None
    total_pages: int | None
    echoed_source: str | None

    def has_more(self, requested_page: int) -> bool:
        """Whether it is worth asking for the next page.

        Three separate reasons to stop, and the first one is the important one.

        **The echoed page is the clamp detector.** Above page 200 this API
        returns HTTP 200 carrying page 200 again, with `page: 200` in the body.
        A walk that only checked `total_pages` would loop until it ran out of
        patience, storing the same fifty postings over and over. So a response
        whose `page` is not the page that was asked for is the end of what this
        API will serve, whatever it claims to hold.
        """
        if self.page is not None and self.page != requested_page:
            return False
        if not self.jobs:
            return False
        if self.total_pages is not None and requested_page + 1 >= self.total_pages:
            return False
        return requested_page < MAX_PAGE


@dataclass(frozen=True)
class FeedWalk:
    """Everything one walk read, and everything it could not reach.

    The unreachable count is a field rather than a log line on purpose. "We
    retrieved 500 of 48,129" and "we retrieved everything" are different
    outcomes, and a connector that cannot tell a person which one happened is
    the silent-truncation failure this whole class exists to prevent.
    """

    scope: str
    pages: tuple[FeedPage, ...]
    stopped_early: bool

    @property
    def truncated(self) -> bool:
        """The walk ended on an empty page the feed had not finished serving.

        An empty page is a legitimate end of a walk: the feed ran out. It is
        NOT a legitimate end when the feed's own `total_pages` says there are
        more, and this API is documented above as returning intermittent 500s.
        A 200 carrying `jobs: []` on page 3 of 40 used to end the walk and be
        reported as a complete pass over 150 postings.

        False when the walk was stopped by a page limit or by the caller: that
        is a deliberate bound, not a truncation, and `stopped_early` is the
        field that says so.
        """
        if self.stopped_early or not self.pages:
            return False
        last = self.pages[-1]
        if last.jobs:
            return False
        if last.page is not None and last.page != len(self.pages) - 1:
            # The silent clamp, which `has_more` detects separately. Not this.
            return False
        return last.total_pages is not None and len(self.pages) < last.total_pages

    @property
    def entries(self) -> tuple[dict[str, Any], ...]:
        return tuple(entry for page in self.pages for entry in page.jobs)

    @property
    def claimed_total(self) -> int | None:
        """What the feed says it holds at this scope, as of the first page."""
        return self.pages[0].total if self.pages else None

    @property
    def servable_total(self) -> int | None:
        """The most this API will hand over at this scope, ceiling included."""
        total = self.claimed_total
        if total is None:
            return None
        return min(total, (MAX_PAGE + 1) * DECLARED_PAGE_SIZE)

    @property
    def beyond_reach(self) -> int:
        """Postings the feed claims to hold that the API will not serve.

        Zero for every scope small enough to fit inside 201 pages. At the widest
        scope it was 38,079 of 48,129 when this was written, which is a property
        of the source and not a failure of this connector.
        """
        total = self.claimed_total
        servable = self.servable_total
        if total is None or servable is None:
            return 0
        return max(0, total - servable)

    @property
    def retrieved(self) -> int:
        return len(self.entries)

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "pages_read": len(self.pages),
            "retrieved": self.retrieved,
            "claimed_total": self.claimed_total,
            "servable_total": self.servable_total,
            "beyond_reach": self.beyond_reach,
            "stopped_early": self.stopped_early,
            "truncated": self.truncated,
        }


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# =========================================================================
# the contract check
# =========================================================================

#: What this adapter reads. Checked against the live specification before a
#: walk, so a contract change is a reported refusal rather than a wrong number.
REQUIRED_PATHS: tuple[str, ...] = ("/api/v1/jobs", "/api/v1/jobs/{id}")
REQUIRED_JOB_PARAMETERS: tuple[str, ...] = ("page", "source", "company", "scope")
REQUIRED_JOB_FIELDS: tuple[str, ...] = (
    "id",
    "title",
    "company",
    "company_slug",
    "url",
    "location",
    "workplace_type",
    "employment_type",
    "function",
    "seniority",
    "published_at",
    "comp_min",
    "comp_max",
    "comp_currency",
    "comp_period",
)
REQUIRED_DETAIL_FIELDS: tuple[str, ...] = ("status", "description_text", "apply", "comp_summary")


@dataclass(frozen=True)
class ContractCheck:
    """What the live OpenAPI specification says, against what we depend on."""

    version: str | None
    missing_paths: tuple[str, ...]
    missing_parameters: tuple[str, ...]
    missing_job_fields: tuple[str, ...]
    missing_detail_fields: tuple[str, ...]
    #: Every non-GET operation in the specification. Expected to be empty.
    #: Recorded rather than assumed, because "this API is read-only" is a claim
    #: about someone else's service that can stop being true without notice.
    write_operations: tuple[str, ...]
    declared_max_page: int | None
    declared_page_size: int | None

    @property
    def ok(self) -> bool:
        return not (
            self.missing_paths
            or self.missing_parameters
            or self.missing_job_fields
            or self.missing_detail_fields
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "version": self.version,
            "missing_paths": list(self.missing_paths),
            "missing_parameters": list(self.missing_parameters),
            "missing_job_fields": list(self.missing_job_fields),
            "missing_detail_fields": list(self.missing_detail_fields),
            "write_operations": list(self.write_operations),
            "declared_max_page": self.declared_max_page,
            "declared_page_size": self.declared_page_size,
        }

    def describe(self) -> str:
        if self.ok:
            return (
                f"contract ok (OpenAPI {self.version}), "
                f"{len(self.write_operations)} write operations in the specification"
            )
        problems = []
        for label, values in (
            ("paths", self.missing_paths),
            ("parameters", self.missing_parameters),
            ("job fields", self.missing_job_fields),
            ("detail fields", self.missing_detail_fields),
        ):
            if values:
                problems.append(f"{label}: {', '.join(values)}")
        return "the published contract no longer matches what this adapter reads. " + "; ".join(
            problems
        )


def validate_contract(fetcher: HttpFetcher, api_base: str = API_BASE) -> ContractCheck:
    """Read the published specification and check it against what we depend on.

    One request, and it is the first thing a retrieval does. The alternative is
    discovering a renamed field as a column of nulls weeks later, which is the
    silent rot `pipeline/collect.py::_metadata_summary` already guards against
    for field maps.

    It also counts write operations. There are none today, and there is no code
    here that could call one; counting them turns "this API is read-only" from
    an assumption into an observation with a date on it.
    """
    url = f"{api_base.rstrip('/')}/openapi.json"
    spec = fetcher.get_json(url, use_cache=False)
    if not isinstance(spec, dict):
        raise FetchError(FetchErrorCategory.MALFORMED, url, "the specification was not an object")

    raw_paths = spec.get("paths")
    paths: dict[str, Any] = raw_paths if isinstance(raw_paths, dict) else {}
    schemas: dict[str, Any] = {}
    components = spec.get("components")
    if isinstance(components, dict) and isinstance(components.get("schemas"), dict):
        schemas = components["schemas"]

    missing_paths = tuple(name for name in REQUIRED_PATHS if name not in paths)

    jobs_operation = paths.get("/api/v1/jobs", {}).get("get", {}) if paths else {}
    declared = {
        str(parameter.get("name"))
        for parameter in jobs_operation.get("parameters", [])
        if isinstance(parameter, dict)
    }
    missing_parameters = tuple(
        name for name in REQUIRED_JOB_PARAMETERS if name not in declared and not missing_paths
    )

    write_operations = tuple(
        sorted(
            f"{method.upper()} {path}"
            for path, operations in paths.items()
            if isinstance(operations, dict)
            for method in operations
            if method.lower() not in ("get", "head", "options", "parameters")
        )
    )

    return ContractCheck(
        version=(str(spec.get("openapi")) if spec.get("openapi") else None),
        missing_paths=missing_paths,
        missing_parameters=missing_parameters,
        missing_job_fields=_missing_properties(schemas.get("Job"), REQUIRED_JOB_FIELDS),
        missing_detail_fields=_missing_properties(schemas.get("JobDetail"), REQUIRED_DETAIL_FIELDS),
        write_operations=write_operations,
        declared_max_page=_parameter_bound(jobs_operation, "page", "maximum"),
        declared_page_size=_const_page_size(jobs_operation),
    )


def _missing_properties(schema: Any, required: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        # No schema at all is every field missing. Reporting "nothing missing"
        # because there was nothing to compare against is how a contract check
        # passes on an empty document.
        return required
    properties = schema["properties"]
    return tuple(name for name in required if name not in properties)


def _parameter_bound(operation: Any, parameter: str, bound: str) -> int | None:
    if not isinstance(operation, dict):
        return None
    for entry in operation.get("parameters", []):
        if isinstance(entry, dict) and entry.get("name") == parameter:
            schema = entry.get("schema")
            if isinstance(schema, dict):
                return _as_int(schema.get(bound))
    return None


def _const_page_size(operation: Any) -> int | None:
    """`page_size` is declared `const` on the response, not on a parameter."""
    if not isinstance(operation, dict):
        return None
    try:
        schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
        return _as_int(schema["properties"]["page_size"].get("const"))
    except (KeyError, TypeError, AttributeError):
        return None


# =========================================================================
# the origin pointer, which is what makes deduplication deterministic
# =========================================================================


def origin_url(payload: Any) -> str | None:
    """The employer's own application URL, from a stored detail payload.

    `apply` is `{"kind": "onsite" | "external", "url": ...}` and is null on
    closed roles. `external` means the application happens at the employer's
    own system, which is the case that lets a posting be matched to its ATS
    record; `onsite` means the application happens on the aggregator, and there
    is no ATS record to match.

    Both are returned. Deciding which kinds are worth resolving is the
    collector's business, and an adapter that filtered here would be making a
    collection decision inside a translation layer.
    """
    if not isinstance(payload, dict):
        return None
    apply_block = payload.get("apply")
    if not isinstance(apply_block, dict):
        return None
    url = apply_block.get("url")
    return str(url).strip() or None if url else None


def origin_kind(payload: Any) -> str | None:
    """`external` or `onsite`, as the payload stated it."""
    if not isinstance(payload, dict):
        return None
    apply_block = payload.get("apply")
    if not isinstance(apply_block, dict):
        return None
    kind = apply_block.get("kind")
    return str(kind).strip().lower() or None if kind else None


#: `https://speedrun-talent-network.com/jobs/<slug>-<8 hex>`, any query string.
#:
#: The trailing eight hex characters are the first eight of the job UUID, which
#: the published schema states works as a lookup key. That is a PREFIX of the
#: id rather than the id, and claiming it is the full id would be inventing 28
#: characters, so it is returned as it was found.
#:
#: WHAT THAT MEANS FOR RULE 2, stated plainly because the comment here used to
#: say "the caller matches on a prefix" and no caller does. `get_by_external`
#: is an exact match, the stored `external_id` is the full 36-character UUID,
#: and an independent functional review confirmed against the corpus that the
#: 82 Speedrun rows return `('speedrun', '228d2bfe')` against a 36-character
#: id and therefore never match. So rule 2 cannot resolve a link that points
#: BACK at this aggregator, and rule 3 -- the canonical URL, compared as a
#: string -- is what does. There is a test for exactly that.
#:
#: The recogniser still earns its place: it is what stops another adapter
#: claiming one of these URLs unopposed, which is a real defect this codebase
#: has already had once.
_POSTING_URL = re.compile(
    r"^https?://speedrun-talent-network\.com/jobs/[^/?#]*?(?P<id>[0-9a-f]{8})/?(?:[?#].*)?$",
    re.IGNORECASE,
)


def recognise_posting_url(url: str) -> str | None:
    """The Speedrun job id prefix in this URL, or None when it is not ours."""
    match = _POSTING_URL.match(url.strip())
    return match.group("id").lower() if match else None


# =========================================================================
# compensation
# =========================================================================

COMPENSATION_PATHS: tuple[str, ...] = (
    "comp_min",
    "comp_max",
    "comp_currency",
    "comp_period",
    "comp_summary",
)

#: This source's `comp_period` strings, mapped to the shared vocabulary.
#:
#: `week` and `day` resolve to None deliberately. `COMPENSATION_PERIODS` holds
#: YEAR, MONTH and HOUR, and a weekly rate compared against a monthly target is
#: a wrong number; `salary_unknown` is the right answer for a figure that cannot
#: be compared, which is exactly the rule `providers/base.py` already states.
COMPENSATION_PERIODS: dict[str, str] = {
    "year": "YEAR",
    "month": "MONTH",
    "hour": "HOUR",
    "week": "",
    "day": "",
}


def read_compensation(payload: Any) -> CompensationHint | None:
    """What a stored Speedrun payload says about pay. No network, no guessing.

    **A null `comp_period` means annual, and that is read from the contract
    rather than assumed.** The published schema says so in as many words: "null
    = annual by convention". This is not the absence-is-never-permission case;
    absence here is a documented encoding, and treating a documented encoding as
    unknown would discard a number the source actually stated. A period outside
    the vocabulary is a different matter and resolves to None.

    Only ever one band. Unlike Ashby, this source states a single currency per
    posting, so `alternate_bands` is always empty and the multi-currency
    grouping that adapter needs has nothing to do here.
    """
    if not isinstance(payload, dict):
        return None

    minimum, maximum = stated_amounts(
        parse_amount(payload.get("comp_min")), parse_amount(payload.get("comp_max"))
    )
    summary = payload.get("comp_summary")
    raw_text = str(summary).strip() or None if summary else None
    if minimum is None and maximum is None and raw_text is None:
        return None

    raw_period = payload.get("comp_period")
    if raw_period is None:
        period: str | None = "YEAR"
    else:
        period = COMPENSATION_PERIODS.get(str(raw_period).strip().lower()) or None

    currency = payload.get("comp_currency")
    return CompensationHint(
        source_field="comp_min/comp_max",
        min_value=minimum,
        max_value=maximum,
        currency=(str(currency).strip().upper() or None) if currency else None,
        period=period,
        raw_text=raw_text,
    )

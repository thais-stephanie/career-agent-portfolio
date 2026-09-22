"""Collecting Jobgether: bounded questions, walked in an order somebody chose.

WHAT A RUN IS
-------------
A list of SLICES -- a geography, cut by the vendor's own contract-type and
experience vocabularies -- each walked newest-first through at most ten pages
of twenty-five. Every slice is capped at 250 rows by the vendor and page 11
repeats page 10 (measured 2026-09-11), so a slice ends on one of three facts,
each recorded by name: the vendor said there was no more (`exhausted`), the
page cap was reached with more behind it (`capped`), or a whole page was
already held from an earlier run (`caught_up`), which on a date-sorted feed
means everything older is held too.

`caught_up` is a heuristic about ONE feed and is written down as such: a
posting that returns after being edited keeps its id and its date, so it is
still found; a posting inserted with an old date behind ones already held is
not. On a source whose whole point is recency, that is the right trade, and it
is what lets a refresh cost tens of requests instead of thousands.

THE ORDER IS THE CALLER'S; THE SET IS NOT; AND THE ORDER DOES NOT REPEAT
--------------------------------------------------------------------------
`slices` is the whole neutral universe. `priority` is a candidate's preferred
order over it and decides only which slice becomes fresh first: the run is
planned by `storage/slice_state.plan_order`, fewest-walks-first, so a bounded
run that stopped at slice 90 continues at slice 91 next time whatever the
candidate prefers, and nothing is walked twice while something waits for its
first walk. The first run of 2026-09-11 walked the owner's 90 slices and would
have walked the same 90 forever; that is the breach this closes. Nothing here
derives a slice from a search configuration, a title, a skill or a phrase
group, and `tests/unit/test_source_scheduling.py` reads this module's syntax
tree for those names. The vendor's `keyword` and `jobReferences` parameters do
not appear in this file or in the adapter.

A REQUEST BUDGET, NOT A PAGE BUDGET
-----------------------------------
`max_requests` bounds the whole run rather than each slice, because the
robots `Crawl-delay: 2` makes requests the scarce thing: 2,500 requests is an
hour and twenty minutes against somebody else's server. Progress is written to
`pipeline_run.stats_json` after every page (`PipelineRunRepo.progress`), so a
killed run keeps what it stored and the source panel can say how far it got.

METADATA ONLY, AND STORED AS SUCH
---------------------------------
No row carries a description. Each is stored against the EMPTY body -- one
`job_raw` row whose text is the empty string -- rather than against no body
at all, because `job_match.content_hash` is NOT NULL and a lead that states a
hiring scope must be scoreable: `rescore` scores a textless row when its
board declared where it hires, and the geography gate answers from the field
alone. The status is FETCHED, never NORMALISED, and `content_completeness`
reads METADATA_ONLY from the adapter's own declaration. Rule 4
(byte-identical body) therefore never folds a lead into anything, rule 2
(origin URL) is unreachable, and duplicates against the rest of the corpus
are expected rather than failures: the lead carries the one fact the ATS row
usually lacks, a hiring scope.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import resolve_metadata
from career_agent.providers.jobgether import (
    MAX_PAGE,
    JobgetherError,
    JobgetherProvider,
    Slice,
    company_of,
    default_slices,
    to_stub,
)
from career_agent.providers.registry import get_provider
from career_agent.storage.db import transaction
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    PipelineRunRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)
from career_agent.storage.slice_state import SliceStateRepo, plan_order

PROVIDER = "jobgether"
RUN_NAME = "collect-jobgether"

#: Requests per run unless told otherwise: about fifteen minutes at the
#: crawl delay, which is a refresh rather than a census.
DEFAULT_MAX_REQUESTS = 400

SLICE_EXHAUSTED = "exhausted"
SLICE_CAPPED = "capped"
SLICE_CAUGHT_UP = "caught_up"
SLICE_BUDGET = "budget"
SLICE_STOPPED = "stopped"
SLICE_FAILED = "failed"


@dataclass
class SliceStats:
    key: str
    pages: int = 0
    rows: int = 0
    new: int = 0
    seen_again: int = 0
    ended: str | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "pages": self.pages,
            "rows": self.rows,
            "new": self.new,
            "seen_again": self.seen_again,
            "ended": self.ended,
            "error": self.error,
        }


@dataclass
class JobgetherStats:
    """What one run did, slice by slice, with the bound it ran under."""

    max_requests: int = DEFAULT_MAX_REQUESTS
    requests: int = 0
    slices_planned: int = 0
    slices_walked: int = 0
    postings_seen: int = 0
    postings_unaddressable: int = 0
    postings_without_company: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    with_hiring_scope: int = 0
    without_hiring_scope: int = 0
    companies_new: int = 0
    boards_new: int = 0
    slices: list[SliceStats] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)
    budget_exhausted: bool = False
    elapsed_ms: int = 0
    http: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        capped = sum(1 for s in self.slices if s.ended == SLICE_CAPPED)
        return {
            "max_requests": self.max_requests,
            "requests": self.requests,
            "slices_planned": self.slices_planned,
            "slices_walked": self.slices_walked,
            "slices_capped": capped,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "with_hiring_scope": self.with_hiring_scope,
            "without_hiring_scope": self.without_hiring_scope,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "slices": [s.as_dict() for s in self.slices],
            "failures": list(self.failures),
            "budget_exhausted": self.budget_exhausted,
            # The vocabulary the source matrix reads for "did this run reach
            # the end": a run that ran out of budget, or left a slice capped
            # at the vendor's 250, did not, and PARTIAL is the honest state.
            "stopped_early": self.budget_exhausted or capped > 0,
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            "coverage_note": (
                "bounded questions, 250 rows each, newest first; never the vendor's inventory. "
                "A capped slice has more behind it that this API cannot reach."
            ),
            "content_note": "metadata only: the API carries no description and the page is closed",
            "attribution": "Jobgether, credited with a direct link to every listing",
        }


class JobgetherCollector:
    """A list of slices, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider: JobgetherProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.runs = PipelineRunRepo(conn)
        self.slice_state = SliceStateRepo(conn)
        self._held_before: frozenset[str] = frozenset()

    def collect(
        self,
        slices: Sequence[Slice] | None = None,
        *,
        priority: Sequence[str] = (),
        max_requests: int = DEFAULT_MAX_REQUESTS,
        on_progress: Any = None,
        should_stop: Any = None,
    ) -> JobgetherStats:
        started = time.monotonic()
        universe = tuple(slices) if slices is not None else default_slices()
        by_key = {slice_.key: slice_ for slice_ in universe}
        with transaction(self.conn):
            run_id = self.runs.start(RUN_NAME)
            self.slice_state.ensure(PROVIDER, by_key)
        walked = self.slice_state.rows_for(PROVIDER)
        planned = tuple(by_key[key] for key in plan_order(list(by_key), walked, priority=priority))
        stats = JobgetherStats(max_requests=max_requests, slices_planned=len(planned))

        held = self._held_own_ids()
        # What was held BEFORE this run, frozen. `caught_up` asks this and
        # not `held`: the canary of 2026-09-11 saw 75 rows carry 36 distinct
        # offers, because the vendor lists one row per location variant of an
        # offer, so a page can be entirely "already seen" from the page before
        # it without a single row being from an earlier run.
        self._held_before = frozenset(held)
        status = PipelineRunStatus.OK
        try:
            for slice_ in planned:
                if should_stop is not None and should_stop():
                    break
                if stats.requests >= max_requests:
                    stats.budget_exhausted = True
                    break
                self._walk(slice_, held, stats, should_stop)
                stats.slices_walked += 1
                with transaction(self.conn):
                    self.runs.progress(run_id, stats.as_dict())
                if on_progress is not None:
                    on_progress(stats.slices_walked, len(planned))
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            stats.failures.append(f"{type(exc).__name__}: {exc}")
            status = PipelineRunStatus.FAILED
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                status,
                stats=stats.as_dict(),
                error=stats.failures[0] if stats.failures else None,
            )
        return stats

    # -- one slice ---------------------------------------------------------

    def _walk(
        self, slice_: Slice, held: dict[str, str], stats: JobgetherStats, should_stop: Any
    ) -> None:
        record = SliceStats(key=slice_.key)
        stats.slices.append(record)
        with transaction(self.conn):
            self.slice_state.start(PROVIDER, slice_.key)
        try:
            self._walk_pages(slice_, held, stats, should_stop, record)
        finally:
            with transaction(self.conn):
                self.slice_state.finish(
                    PROVIDER,
                    slice_.key,
                    ended=record.ended or SLICE_FAILED,
                    pages=record.pages,
                    rows=record.rows,
                    new=record.new,
                    error=record.error,
                )

    def _walk_pages(
        self,
        slice_: Slice,
        held: dict[str, str],
        stats: JobgetherStats,
        should_stop: Any,
        record: SliceStats,
    ) -> None:
        for page in range(1, MAX_PAGE + 1):
            if should_stop is not None and should_stop():
                record.ended = SLICE_STOPPED
                return
            if stats.requests >= stats.max_requests:
                record.ended = SLICE_BUDGET
                stats.budget_exhausted = True
                return
            stats.requests += 1
            try:
                read = self.provider.read_page(slice_, page)
            except (FetchError, JobgetherError) as exc:
                record.ended = SLICE_FAILED
                record.error = str(exc)
                stats.failures.append(f"{slice_.key} page {page}: {exc}")
                return
            record.pages += 1
            record.rows += len(read.jobs)
            stats.postings_unaddressable += read.unaddressable
            unseen_before = 0
            for job in read.jobs:
                stats.postings_seen += 1
                stub = to_stub(job)
                if stub is not None and stub.external_id not in self._held_before:
                    unseen_before += 1
                if self._handle(job, held, stats):
                    record.new += 1
                else:
                    record.seen_again += 1
            if not read.has_more:
                record.ended = SLICE_EXHAUSTED
                return
            if read.jobs and unseen_before == 0 and page > 1:
                # A whole page already held, on a date-sorted feed: the rest
                # is older still. Page 1 is exempt so a refresh always looks
                # at least one page deep before concluding it is current.
                record.ended = SLICE_CAUGHT_UP
                return
        record.ended = SLICE_CAPPED

    # -- one row -----------------------------------------------------------

    def _handle(self, job: dict[str, Any], held: dict[str, str], stats: JobgetherStats) -> bool:
        """Persist one row. Returns True when it was new to this provider."""
        stub = to_stub(job)
        if stub is None:
            return False
        if stub.location_raw:
            stats.with_hiring_scope += 1
        else:
            stats.without_hiring_scope += 1

        company = company_of(job)
        if not company:
            stats.postings_without_company += 1
            return False
        slug = _slugify(company)

        with transaction(self.conn):
            # The empty body, once. See the module docstring.
            empty_hash = self.raw.put("", "")
            existed = self.companies.get_by_slug(slug)
            company_id = self.companies.upsert(
                CompanyRecord(slug=slug, name=company, discovery_source=PROVIDER)
            )
            if existed is None:
                stats.companies_new += 1
            board_existed = self.boards.get_by_identifier(PROVIDER, slug)
            board_id = self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=PROVIDER,
                    board_identifier=slug,
                    board_url=None,
                    discovery_method="jobgether_api",
                )
            )
            if board_existed is None:
                stats.boards_new += 1

            is_new = stub.external_id not in held
            if is_new:
                stats.jobs_new += 1
            else:
                stats.jobs_seen_again += 1
            job_id = self.jobs.upsert_seen(
                JobRecord(
                    company_id=company_id,
                    source_board_id=board_id,
                    provider=PROVIDER,
                    external_id=stub.external_id,
                    url=stub.url,
                    title=stub.title,
                    department=None,
                    # The employer's hiring scope. `publishes_hiring_scope`
                    # is True, so the pipeline reads it as a declared scope.
                    location_raw=stub.location_raw,
                    posted_at=stub.posted_at,
                    content_hash=empty_hash,
                ),
                status=CollectionStatus.FETCHED,
            )
            held[stub.external_id] = job_id
            self.payloads.put(
                ProviderPayloadRecord(job_id=job_id, provider=PROVIDER, payload=dict(job))
            )
            resolve_metadata(PROVIDER, self.provider.field_map, job)
        return is_new

    def _held_own_ids(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT external_id, id FROM job WHERE provider = ? AND external_id IS NOT NULL",
            (PROVIDER,),
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}


def _slugify(name: str) -> str:
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

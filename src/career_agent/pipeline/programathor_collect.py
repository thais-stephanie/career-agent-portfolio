"""Collecting Programathor: a listing walk, then one request per posting.

The same identity rules as every other feed collector here. Three things are
different and each changes what a number means.

**A VENDOR ERROR IS ORDINARY, AND IT IS COUNTED.** A material fraction of this
board's posting pages answer HTTP 500, deterministically -- four consecutive
requests for one URL returned 500 four times, so it is neither a rate limit
nor transient. The first corpus run served 106 of 112.
`postings_unavailable` is its own counter, apart from
`postings_unaddressable`, because "the vendor would not serve it" and "we
could not read what it served" are different problems with different fixes. A
source whose error rate reached 100% would otherwise look exactly like a
source with nothing new.

**THE DESCRIPTION COSTS A REQUEST.** The listing carries none, so this is the
one source here where the page budget understates the request count by a
factor of fifteen. A budget of two pages is 2 listing requests plus up to 30
posting requests. That is why the default is two.

**THE SALARY IS WORTH THE TRIP.** `baseSalary` states an amount, a currency
and a period, which is the combination `min_salary` filtering has always
needed and which almost nothing in this corpus supplies. `employmentType`
carries `CONTRACTOR` -- PJ on a Brazilian board -- on four of the five
postings captured. `contract_regime` has been NULL on every scored row in this
corpus since the column existed, not because the reader was too narrow but
because no source stated it. One now does.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.base import resolve_metadata
from career_agent.providers.programathor import (
    ProgramathorError,
    ProgramathorProvider,
    company_domain,
    company_of,
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
    DiscoverySourceRepo,
    JobRawRepo,
    JobRepo,
    PipelineRunRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

PROVIDER = "programathor"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class ProgramathorStats:
    """What one pass did, with the vendor's failures kept visible."""

    pages_read: int = 0
    stopped_early: bool = False
    postings_listed: int = 0
    #: The vendor answered 500, or served a page with no `JobPosting` block.
    #: Its own counter: this is the source's defining cost and burying it in a
    #: general failure count would hide the one number that decides whether the
    #: page budget is worth raising.
    postings_unavailable: int = 0
    #: Served, parsed, and still missing something required to file it.
    postings_unaddressable: int = 0
    postings_without_company: int = 0
    postings_seen: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    with_location: int = 0
    without_location: int = 0
    #: Postings stating a salary with a currency AND a period. Counted because
    #: it is the reason this source is worth its request cost.
    with_salary: int = 0
    companies_new: int = 0
    boards_new: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    http: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "pages_read": self.pages_read,
            "stopped_early": self.stopped_early,
            "postings_listed": self.postings_listed,
            "postings_unavailable": self.postings_unavailable,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "postings_seen": self.postings_seen,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "with_location": self.with_location,
            "without_location": self.without_location,
            "with_salary": self.with_salary,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            "coverage_note": (
                "a bounded walk of the listing, one request per posting, and "
                "roughly half of this vendor's posting pages answer 500"
            ),
        }


class ProgramathorCollector:
    """One pass over the listing, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher, max_pages: int | None = None) -> None:
        self.conn = conn
        self.fetcher = fetcher
        get_provider(PROVIDER, fetcher)
        self.provider = ProgramathorProvider(fetcher, max_pages=max_pages)
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> ProgramathorStats:
        started = time.monotonic()
        stats = ProgramathorStats()

        with transaction(self.conn):
            run_id = self.runs.start("collect-programathor")

        held_external_ids = self._held_external_ids()

        try:
            listing = self.provider.read_listing()
        except (FetchError, ProgramathorError, ValueError) as exc:
            # A failed LISTING walk is a failure. A failed POSTING is not: see
            # the module docstring, where half of them are the ordinary case.
            stats.failures.append(str(exc))
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
                )
            return stats

        stats.pages_read = listing.pages
        stats.stopped_early = listing.stopped_early
        stats.postings_listed = len(listing.paths)

        for index, path in enumerate(listing.paths, start=1):
            if should_stop is not None and should_stop():
                break
            stub = self.provider.read_posting(path)
            if stub is None:
                stats.postings_unavailable += 1
            else:
                stats.postings_seen += 1
                self._handle(stub, held_external_ids, stats)
            if on_progress is not None:
                on_progress(index, len(listing.paths))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, stub: Any, held_external_ids: dict[str, str], stats: ProgramathorStats
    ) -> None:
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, MATCH_EXTERNAL_ID, stats)
            return

        match = self._job_by_canonical_url(stub.url)
        if match is not None:
            self._record_sighting(match, stub, MATCH_CANONICAL_URL, stats)
            return

        posting = self.provider.fetch_posting(None, stub)  # type: ignore[arg-type]
        text_hash = (
            self.raw.put(posting.description_text, posting.description_html)
            if posting.description_text
            else None
        )
        if posting.has_description:
            stats.descriptions_non_empty += 1
        else:
            stats.descriptions_empty += 1

        if stub.location_raw:
            stats.with_location += 1
        else:
            stats.without_location += 1

        from career_agent.providers.programathor import read_compensation

        band = read_compensation(stub.payload)
        if band is not None and band.currency and band.period:
            stats.with_salary += 1

        company = company_of(stub.payload)
        if not company:
            stats.postings_without_company += 1
            return

        slug = _slugify(company)

        if text_hash is not None:
            duplicate = self._job_by_content_hash(text_hash, slug=slug, title=stub.title)
            if duplicate is not None:
                self._record_sighting(duplicate, stub, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(stub, posting, text_hash, company, slug, stats)

    def _record_sighting(self, job_id: str, stub: Any, rule: str, stats: ProgramathorStats) -> None:
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                payload=dict(stub.payload),
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        stub: Any,
        posting: Any,
        text_hash: str | None,
        company: str,
        slug: str,
        stats: ProgramathorStats,
    ) -> None:
        with transaction(self.conn):
            existed = self.companies.get_by_slug(slug)
            company_id = self.companies.upsert(
                CompanyRecord(
                    slug=slug,
                    name=company,
                    discovery_source=PROVIDER,
                    # `hiringOrganization.sameAs`. The strongest identity signal
                    # this board carries, and a column that already exists.
                    canonical_domain=company_domain(stub.payload),
                )
            )
            if existed is None:
                stats.companies_new += 1

            board_existed = self.boards.get_by_identifier(PROVIDER, slug)
            board_id = self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=PROVIDER,
                    board_identifier=slug,
                    # No per-employer page on this board that this adapter has
                    # verified. A search link stored as a board URL would make
                    # a query look like a place.
                    board_url=None,
                    discovery_method="programathor_listing",
                )
            )
            if board_existed is None:
                stats.boards_new += 1

            existing = self.jobs.get_by_external(PROVIDER, stub.external_id)
            if existing is None:
                stats.jobs_new += 1
            else:
                stats.jobs_seen_again += 1
                if text_hash is not None and existing["content_hash"] != text_hash:
                    stats.jobs_changed += 1

            job_id = self.jobs.upsert_seen(
                JobRecord(
                    company_id=company_id,
                    source_board_id=board_id,
                    provider=PROVIDER,
                    external_id=stub.external_id,
                    url=stub.url,
                    title=stub.title,
                    department=stub.department,
                    location_raw=stub.location_raw,
                    posted_at=stub.posted_at,
                    content_hash=text_hash,
                ),
                status=CollectionStatus.NORMALISED if text_hash else CollectionStatus.FETCHED,
            )
            self.payloads.put(
                ProviderPayloadRecord(
                    job_id=job_id, provider=PROVIDER, payload=dict(posting.payload)
                )
            )
            resolve_metadata(PROVIDER, self.provider.field_map, stub.payload)

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        rows = self.conn.execute(
            "SELECT external_id, id FROM job WHERE provider != ? AND external_id IS NOT NULL",
            (PROVIDER,),
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}

    def _job_by_canonical_url(self, url: str) -> str | None:
        target = canonical_posting_url(url)
        if not target:
            return None
        host = urlsplit(url).hostname or ""
        if not host:
            return None
        rows = self.conn.execute(
            "SELECT id, url FROM job WHERE provider != ? AND url LIKE ?",
            (PROVIDER, f"%{host}%"),
        ).fetchall()
        for row in rows:
            if canonical_posting_url(str(row["url"])) == target:
                return str(row["id"])
        return None

    def _job_by_content_hash(self, text_hash: str, *, slug: str, title: str) -> str | None:
        if not slug or not title:
            return None
        row = self.conn.execute(
            "SELECT j.id AS id FROM job j"
            " JOIN company c ON c.id = j.company_id"
            " WHERE j.content_hash = ? AND j.provider != ? AND c.slug = ? AND j.title = ?"
            " LIMIT 1",
            (text_hash, PROVIDER, slug, title),
        ).fetchone()
        return str(row["id"]) if row else None


def _slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

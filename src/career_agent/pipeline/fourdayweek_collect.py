"""Collecting 4 Day Week, one bounded walk of the paginated feed.

The same shape as the 4 Day Week collector: a page budget with no "unlimited"
value, `stopped_early` kept apart from the end of the feed, and full bodies in
the list response so rule 4 works. `total` is what the vendor reports and is
recorded as a claim, never as coverage: a 50-page default reads 5,000 of the
23,690 the feed reported on 2026-09-11.

**THE ATTRIBUTION IS THE LICENCE.** "All we ask is that you link back to
https://4dayweek.io": Apply opens the 4dayweek.io posting page, which is also
the only URL the v2 response carries, and the interface credits the source.

**THREE RULES, NOT FOUR.** `ORIGIN_URL` is unreachable: `apply_url` was
removed from the API. Rules 1, 3 and 4 remain.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.base import resolve_metadata
from career_agent.providers.fourdayweek import (
    FourDayWeekProvider,
    company_of,
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
    DiscoverySourceRepo,
    JobRawRepo,
    JobRepo,
    PipelineRunRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

PROVIDER = "fourdayweek"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class FourDayWeekStats:
    """What one pass did, with the bound it ran under kept visible."""

    pages_read: int = 0
    #: True when the walk stopped at the page budget rather than at the end of
    #: the feed. Counted apart from a failure because nothing broke: there is
    #: simply more, and a caller that cannot tell reports a bounded read as a
    #: complete one.
    stopped_early: bool = False
    #: What the feed says it holds in total. Recorded and never used as a stop
    #: condition, and never presented as what was collected.
    claimed_total: int | None = None
    postings_seen: int = 0
    postings_unaddressable: int = 0
    postings_without_company: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    #: Postings that state where the employer may hire, and those that state
    #: nothing. Kept apart because an empty restriction list is the most
    #: tempting field in this feed to over-read: it looks exactly like
    #: "anywhere", and absence is never permission.
    with_hiring_scope: int = 0
    without_hiring_scope: int = 0
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
            "claimed_total": self.claimed_total,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "with_hiring_scope": self.with_hiring_scope,
            "without_hiring_scope": self.without_hiring_scope,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            # Said in the stats rather than only in a docstring, because this
            # is what a source panel reads.
            "coverage_note": ("a bounded walk of a feed, never a census of remote hiring"),
        }


class FourDayWeekCollector:
    """One pass over the feed, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher, max_pages: int | None = None) -> None:
        self.conn = conn
        self.fetcher = fetcher
        provider = get_provider(PROVIDER, fetcher)
        # The registry builds it from a fetcher alone and cannot carry a page
        # budget, so a caller that asked for one gets its own instance. The
        # registry call above still happens, because it is what proves this
        # provider is registered rather than merely importable.
        self.provider: FourDayWeekProvider = (
            FourDayWeekProvider(fetcher, max_pages=max_pages) if max_pages is not None else provider  # type: ignore[assignment]
        )
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> FourDayWeekStats:
        """Read the feed once, to the page budget, and persist what is new."""
        started = time.monotonic()
        stats = FourDayWeekStats()

        with transaction(self.conn):
            run_id = self.runs.start("collect-fourdayweek")

        held_external_ids = self._held_external_ids()

        # PAGE BY PAGE, PERSISTED AS IT GOES. The first production run held
        # the whole 23,690-row walk in memory and then persisted it; stopped
        # by the agent after 548 rows. Each page is stored before the next is
        # asked for, and progress is written to the ledger after every page,
        # so a killed run keeps what it read and the source panel can say how
        # far it got.
        status = PipelineRunStatus.OK
        try:
            for page in self.provider.iter_pages():
                if should_stop is not None and should_stop():
                    stats.stopped_early = True
                    break
                stats.pages_read += 1
                if stats.claimed_total is None:
                    stats.claimed_total = page.claimed_total
                stats.postings_unaddressable += page.unaddressable
                for job in page.jobs:
                    stats.postings_seen += 1
                    self._handle(job, held_external_ids, stats)
                stats.stopped_early = page.stopped_early
                with transaction(self.conn):
                    self.runs.progress(run_id, stats.as_dict())
                if on_progress is not None:
                    on_progress(stats.pages_read, self.provider.max_pages)
        except (FetchError, ValueError) as exc:
            # A failed page is a failure, never an empty feed; the pages
            # before it are kept.
            stats.failures.append(str(exc))
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

    # -- one posting -------------------------------------------------------

    def _handle(
        self, job: dict[str, Any], held_external_ids: dict[str, str], stats: FourDayWeekStats
    ) -> None:
        stub = to_stub(job)
        if stub is None:
            return

        # RULE 1, checked first because it is free.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, job, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 2 (ORIGIN_URL) is unreachable. See the module docstring: this
        # feed publishes no employer apply link, measured rather than assumed.

        # RULE 3 (CANONICAL_URL) is deliberately NOT asked. Every URL this
        # feed publishes is its own posting page, which no other provider
        # stores, so the lookup could never fire -- and on the production
        # corpus it cost a full scan of 210,000 rows per posting.

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

        # `location_raw` may be the offices; the SCOPE is the remote-allowed
        # list, and only that counts here.
        from career_agent.providers.fourdayweek import hiring_scope

        if hiring_scope(job):
            stats.with_hiring_scope += 1
        else:
            stats.without_hiring_scope += 1

        company = company_of(job)
        if not company:
            # The feed carried no employer. Inventing one from the title is the
            # fuzzy identity ADR-0008 forbids.
            stats.postings_without_company += 1
            return

        slug = _slugify(company)

        # RULE 4: byte-identical description, FROM THE SAME EMPLOYER, FOR THE
        # SAME ROLE. Three fields matching is equality, not resemblance.
        if text_hash is not None:
            duplicate = self._job_by_content_hash(text_hash, slug=slug, title=stub.title)
            if duplicate is not None:
                self._record_sighting(duplicate, stub, job, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(job, stub, posting, text_hash, company, slug, stats)

    def _record_sighting(
        self, job_id: str, stub: Any, job: dict[str, Any], rule: str, stats: FourDayWeekStats
    ) -> None:
        """Record that this source also holds it. Deliberately the ONLY effect.

        No job field is touched. Whoever holds the original holds a better
        record than an aggregator's copy of it, and the way to guarantee that
        survives is to have no code path that writes over it.
        """
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                payload=job,
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        job: dict[str, Any],
        stub: Any,
        posting: Any,
        text_hash: str | None,
        company: str,
        slug: str,
        stats: FourDayWeekStats,
    ) -> None:
        """A posting nothing else in the corpus holds.

        One board per company, created on demand, which is what keeps ADR-0008
        true through a source whose real shape is a single stream. Registering
        the whole feed as one board carrying thousands of employers is exactly
        the attribution failure that document exists to prevent.
        """
        with transaction(self.conn):
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
                    # No URL: there is no per-employer board page this adapter
                    # has verified. Inventing a search link and storing it as a
                    # board URL would make a query look like a place.
                    board_url=None,
                    discovery_method="fourdayweek_feed",
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
                    # The employer's HIRING RESTRICTION, not an office. See the
                    # module docstring.
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
            resolve_metadata(PROVIDER, self.provider.field_map, job)

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        """Every `external_id` held by ANOTHER provider, mapped to its job id.

        `provider != ?`, and the exclusion is the correction every sibling
        collector already carries: applied to this source's OWN rows the rule
        would file a posting collected last week as a sighting of itself, which
        freezes `last_seen_at` and writes a discovery row migration 0018
        forbids.
        """
        rows = self.conn.execute(
            "SELECT external_id, id FROM job WHERE provider != ? AND external_id IS NOT NULL",
            (PROVIDER,),
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}

    def _job_by_canonical_url(self, url: str) -> str | None:
        target = canonical_posting_url(url)
        if not target:
            return None
        host = _host(url)
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


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def _slugify(name: str) -> str:
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

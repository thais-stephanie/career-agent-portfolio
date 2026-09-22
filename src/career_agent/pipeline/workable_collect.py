"""Persist Jobs by Workable XML through the ordinary aggregator identity rules.

The adapter downloads broadly to disk, emits complete batches and reports any
truncated transfer after retaining its complete records. No absence closes a
posting. Apply URLs are stored unchanged; canonicalization is comparison-only.
Source XML fields and the application destination remain in the archived payload.
The legacy search API is retained only as an offline protocol regression fixture.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.base import resolve_metadata
from career_agent.providers.registry import get_provider
from career_agent.providers.workable import (
    company_of,
    company_website,
    to_stub,
)
from career_agent.providers.workable_xml import WorkableXmlProvider as WorkableProvider
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

PROVIDER = "workable"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class WorkableStats:
    """What one pass did, with the bound it ran under kept visible."""

    xml_jobs: int = 0
    xml_bytes: int = 0
    filtered_out: int = 0
    feed_duplicates: int = 0
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
    employer_identities_corrected: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    #: Postings whose composed `city, subregion, countryName` is non-empty,
    #: and those with nothing to resolve. A PLACE OF WORK either way, never a
    #: hiring scope: the opposite of Himalayas, in the same column.
    with_location: int = 0
    without_location: int = 0
    #: The walk stopped because a page added no posting it did not already
    #: hold. Kept apart from `stopped_early` because they mean opposite things,
    #: and because this one is what a mis-sent page token looks like from here:
    #: HTTP 200, twenty perfectly good postings, and no progress at all.
    repeated_itself: bool = False
    #: The query and time window this walk ran under, recorded so a count can
    #: never be read as the market. Empty is the default and the point.
    query: str | None = None
    day_range: int | None = None
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
            "xml_jobs": self.xml_jobs,
            "xml_bytes": self.xml_bytes,
            "filtered_out": self.filtered_out,
            "feed_duplicates": self.feed_duplicates,
            "pages_read": self.pages_read,
            "stopped_early": self.stopped_early,
            "claimed_total": self.claimed_total,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "employer_identities_corrected": self.employer_identities_corrected,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "with_location": self.with_location,
            "without_location": self.without_location,
            "repeated_itself": self.repeated_itself,
            "query": self.query,
            "day_range": self.day_range,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            # Said in the stats rather than only in a docstring, because this
            # is what a source panel reads.
            "coverage_note": (
                "Jobs by Workable public hourly XML feed; not every Workable customer board"
            ),
        }


class WorkableCollector:
    """One pass over the feed, against one database connection."""

    def __init__(
        self,
        conn: Any,
        fetcher: HttpFetcher,
        max_pages: int | None = None,
        query: str | None = None,
        day_range: int | None = None,
        xml_path: Path | None = None,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        #: Both default to None, and both are recorded in the stats when they
        #: are not. A narrowing nobody can see afterwards is a narrowing that
        #: turns into an unexplained gap in the corpus.
        self.query = query
        self.day_range = day_range
        provider = get_provider(PROVIDER, fetcher)
        # The registry builds it from a fetcher alone and cannot carry a page
        # budget, so a caller that asked for one gets its own instance. The
        # registry call above still happens, because it is what proves this
        # provider is registered rather than merely importable.
        self.provider: WorkableProvider = (
            WorkableProvider(fetcher, max_pages=max_pages) if max_pages is not None else provider  # type: ignore[assignment]
        )
        if xml_path is not None:
            self.provider = WorkableProvider(fetcher, xml_path=xml_path)
        self._canonical_jobs: dict[str, str] = {}
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)
        self._employer_ids: dict[tuple[str, str], tuple[str, str | None]] = {}

    # -- the pass ----------------------------------------------------------

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> WorkableStats:
        """Read the feed once, to the page budget, and persist what is new."""
        started = time.monotonic()
        stats = WorkableStats()
        self._employer_ids.clear()

        with transaction(self.conn):
            run_id = self.runs.start("collect-workable")

        held_external_ids = self._held_external_ids()
        self._canonical_jobs = {
            canonical_posting_url(str(row["url"])): str(row["id"])
            for row in self.conn.execute("SELECT id, url FROM job WHERE provider != ?", (PROVIDER,))
            if row["url"]
        }

        stats.query = self.query
        stats.day_range = self.day_range

        # **PERSISTED PAGE BY PAGE**, and the reason is a measured loss rather
        # than a preference. The first production run of this source read for
        # 404 seconds, met an HTTP 429 on a later page, and stored ZERO
        # postings, because the whole walk was held in memory until the end.
        # Gupy learned the same lesson at a larger scale earlier the same day.
        #
        # A failure now ends the WALK and keeps everything already written.
        stopped = False
        try:
            for page in self.provider.walk(query=self.query, day_range=self.day_range):
                stats.claimed_total = stats.claimed_total or page.claimed_total
                for job in page.records:
                    if should_stop is not None and should_stop():
                        stopped = True
                        break
                    stats.postings_seen += 1
                    self._handle(job, held_external_ids, stats)
                for key in ("xml_jobs", "xml_bytes", "filtered_out", "feed_duplicates"):
                    setattr(stats, key, getattr(self.provider, key, 0))
                stats.pages_read = self.provider.pages_read
                with transaction(self.conn):
                    self.runs.progress(run_id, stats=stats.as_dict())
                if on_progress is not None:
                    on_progress(stats.postings_seen, stats.claimed_total)
                if stopped:
                    break
        except (FetchError, ValueError) as exc:
            # STILL A FAILURE, and still not an empty feed -- conflating those
            # is how a network blip closes a company's whole posting history.
            # What has changed is that the pages before it are already in the
            # corpus, so the run is recorded as partial rather than as nothing.
            stats.failures.append(str(exc))
            stats.pages_read = self.provider.pages_read
            stats.stopped_early = self.provider.stopped_early
            stats.repeated_itself = self.provider.repeated_itself
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
                )
            return stats

        stats.pages_read = self.provider.pages_read
        stats.stopped_early = self.provider.stopped_early
        stats.repeated_itself = self.provider.repeated_itself

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, job: dict[str, Any], held_external_ids: dict[str, str], stats: WorkableStats
    ) -> None:
        stub = to_stub(job)
        if stub is None:
            # No id, no title or no URL on the vendor's own host. Counted here
            # rather than by the provider because the provider hands back what
            # it read and this is where a record is turned down.
            stats.postings_unaddressable += 1
            return

        # RULE 1, checked first because it is free.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, job, MATCH_EXTERNAL_ID, stats)
            return

        # No separate external ATS origin is published. The feed's Workable
        # apply URL remains unchanged; canonical comparison below is lookup-only.

        # RULE 3: the same URL as one another provider already stored.
        match = self._job_by_canonical_url(stub.url)
        if match is not None:
            self._record_sighting(match, stub, job, MATCH_CANONICAL_URL, stats)
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

        company = company_of(job)
        if not company:
            # The feed carried no employer. Inventing one from the title is the
            # fuzzy identity ADR-0008 forbids.
            stats.postings_without_company += 1
            return

        slug, repair_from = self._employer_identity(job, company)

        # RULE 4: byte-identical description, FROM THE SAME EMPLOYER, FOR THE
        # SAME ROLE. Three fields matching is equality, not resemblance.
        if text_hash is not None:
            duplicate = self._job_by_content_hash(text_hash, slug=slug, title=stub.title)
            if duplicate is not None:
                self._record_sighting(duplicate, stub, job, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(job, stub, posting, text_hash, company, slug, stats, repair_from)

    def _employer_identity(self, job: dict[str, Any], name: str) -> tuple[str, str | None]:
        """Keep Unicode names and conflicting published employer domains distinct."""
        import hashlib

        origin = _host(company_website(job) or "").removeprefix("www.")
        key = (name, origin)
        if key not in self._employer_ids:
            slug = _slugify(name)
            repair_from = "unknown-company" if slug.startswith("workable-unicode-") else None
            existing = self.companies.get_by_slug(slug)
            old_origin = _host(existing["website"] or "").removeprefix("www.") if existing else ""
            if existing and origin and old_origin and origin != old_origin:
                # Measured REACH/reach.paris and Reach/withreach.com are not
                # identity evidence for each other merely because case folds.
                repair_from = slug
                slug += "-" + hashlib.sha256(origin.encode("utf-8")).hexdigest()[:12]
            self._employer_ids[key] = (slug, repair_from)
        return self._employer_ids[key]

    def _record_sighting(
        self, job_id: str, stub: Any, job: dict[str, Any], rule: str, stats: WorkableStats
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
        stats: WorkableStats,
        repair_from: str | None = None,
    ) -> None:
        """A posting nothing else in the corpus holds.

        One board per company, created on demand, which is what keeps ADR-0008
        true through a source whose real shape is a single stream. Registering
        the whole feed as one board carrying thousands of employers is exactly
        the attribution failure that document exists to prevent.
        """
        with transaction(self.conn):
            existed = self.companies.get_by_slug(slug)
            if existed is None:
                company_id = self.companies.upsert(
                    CompanyRecord(
                        slug=slug,
                        name=company,
                        # A company homepage is never an application target.
                        website=company_website(job),
                        discovery_source=PROVIDER,
                    )
                )
                stats.companies_new += 1
            else:
                # The feed cannot erase curated HQ, domain, notes or other
                # registry fields simply because it does not publish them.
                company_id = str(existed["id"])

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
                    discovery_method="workable_index",
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
                    # WHERE THE WORK IS, and not where the employer may hire.
                    # `workplace` is what makes it readable, through
                    # `gates.structured_geography`.
                    location_raw=stub.location_raw,
                    posted_at=stub.posted_at,
                    content_hash=text_hash,
                ),
                status=CollectionStatus.NORMALISED if text_hash else CollectionStatus.FETCHED,
            )
            if existing is not None and repair_from:
                # Repair only this collector's measured legacy identity.
                # Keep posting ids, tracking, timestamps and all old payloads.
                corrected = self.conn.execute(
                    "UPDATE job SET company_id=?, source_board_id=?"
                    " WHERE id=? AND provider=? AND company_id IN"
                    " (SELECT id FROM company WHERE slug=?)",
                    (company_id, board_id, job_id, PROVIDER, repair_from),
                ).rowcount
                stats.employer_identities_corrected += corrected
                if corrected:
                    self.conn.execute(
                        "UPDATE source_board SET active=0 WHERE id=? AND provider=?"
                        " AND board_identifier=?"
                        " AND NOT EXISTS (SELECT 1 FROM job WHERE source_board_id=source_board.id)",
                        (existing["source_board_id"], PROVIDER, repair_from),
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
        return self._canonical_jobs.get(canonical_posting_url(url))

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
    import hashlib
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-")
    if slug:
        return slug
    # Six real Greek employers otherwise collapsed into one placeholder.
    # Normalize Unicode, then hash exact identity; never invent a translation.
    identity = unicodedata.normalize("NFKC", name).casefold().strip()
    return "workable-unicode-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]

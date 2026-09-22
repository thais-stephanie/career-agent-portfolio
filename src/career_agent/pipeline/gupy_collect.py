"""Collecting Gupy, one bounded walk per workplace-type partition.

The same shape as the Himalayas and Working Nomads collectors, with four
differences that change what the numbers mean.

**THE WALK IS PARTITIONED, AND THE PARTITION IS THE COVERAGE.** `offset` is
capped at 10,000 by the vendor while the feed holds 81,310, so there is no
query that reads this source whole. It is read one `workplaceType` at a time.
`remote` and `hybrid` fit under the ceiling entirely; `on-site` holds 72,682
and cannot, so a run that includes it reads a slice and the stats say so.
`GupyStats.ceiling_hit` is kept apart from `stopped_early` because they are
different facts: one is this product choosing to stop, the other is the vendor
refusing to serve further.

**THERE IS NO DETAIL REQUEST.** The advert arrives in the listing, so one page
of 100 costs one request and yields 100 complete postings. That is the same
economics Get on Board has, and it is why a bounded run here produces more
usable inventory per request than anything else in this corpus.

**A BOARD IS `careerPageId`, NOT A SLUG.** Every other feed collector here
slugifies an employer name because it has nothing better, which merges
`Grupo SysMap` and `Grupo Sysmap` and separates `Teem` from `Teem LLC`. This
feed carries the vendor's own stable numeric employer id AND that employer's
own career-page URL, so ADR-0008's "a board belongs to one company" is
satisfied by an identifier rather than by a normalisation that hopes.

**THE LOCATION IS A PLACE OF WORK, NOT A HIRING SCOPE.** `publishes_hiring_
scope` is False for this adapter and `job.location_raw` holds
`city, state, country`. What makes these postings resolve for a candidate in
Brazil is `gates.structured_geography`, which reads that string together with
the employer's own `workplaceType`: REMOTE plus a place is a scope, ONSITE or
HYBRID plus a place is somewhere she would have to be, and both are answerable.
Reading `country: Brasil` as "hires anywhere in Brazil" would be invariant 3
broken in Portuguese, and this collector does not do it.
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
from career_agent.providers.gupy import (
    WORKPLACE_TYPES,
    GupyError,
    GupyProvider,
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

PROVIDER = "gupy"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class GupyStats:
    """What one pass did, with every bound it ran under kept visible."""

    #: Which `workplaceType` slices were asked for, in order.
    partitions: list[str] = field(default_factory=list)
    #: What each partition SAYS it holds, from a separate `limit=10` probe.
    #: The paginated response cannot be asked: this vendor reports `total: 100`
    #: when `limit=100`.
    partition_totals: dict[str, int] = field(default_factory=dict)
    #: How many postings each partition actually yielded on this run.
    partition_read: dict[str, int] = field(default_factory=dict)
    pages_read: int = 0
    #: The walk stopped at the page budget. This product's decision.
    stopped_early: bool = False
    #: The walk stopped at offset 10,000. The vendor's refusal. A different
    #: fact, and conflating the two would report a wall as a choice.
    ceiling_hit: list[str] = field(default_factory=list)
    #: How many queries the planner cut the feed into, measured against the
    #: live feed rather than configured.
    slices_planned: int = 0
    #: Slices the planner could not cut small enough. Whatever they hold past
    #: the ceiling is NOT collected, and naming them is the only honest way to
    #: report a walk that did not finish.
    slices_over_ceiling: list[str] = field(default_factory=list)
    #: Postings that arrived in more than one slice. A cost in requests, never
    #: a duplicate row: overlap is how a non-exhaustive cut avoids a gap.
    slice_overlap: int = 0
    postings_seen: int = 0
    postings_unaddressable: int = 0
    postings_without_company: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    #: Postings whose composed `city, state, country` is non-empty, and those
    #: with nothing to resolve. Counted because a feed that stopped filling
    #: those fields would keep working and quietly stop being useful.
    with_location: int = 0
    without_location: int = 0
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
            "partitions": list(self.partitions),
            "partition_totals": dict(self.partition_totals),
            "partition_read": dict(self.partition_read),
            "pages_read": self.pages_read,
            "stopped_early": self.stopped_early,
            "ceiling_hit": list(self.ceiling_hit),
            "slices_planned": self.slices_planned,
            "slices_over_ceiling": list(self.slices_over_ceiling),
            "slice_overlap": self.slice_overlap,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "with_location": self.with_location,
            "without_location": self.without_location,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            "coverage_note": (
                "one bounded walk per workplace-type partition, never a census "
                "of Brazilian hiring: the vendor caps offset at 10,000 and the "
                "on-site partition alone holds more than seven times that"
            ),
        }


class GupyCollector:
    """One pass over the chosen partitions, against one database connection."""

    def __init__(
        self,
        conn: Any,
        fetcher: HttpFetcher,
        max_pages: int | None = None,
        workplace_types: tuple[str, ...] | None = None,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        # The registry call is what proves this provider is registered rather
        # than merely importable; the instance below is what carries the page
        # budget, which the registry's factory signature cannot.
        get_provider(PROVIDER, fetcher)
        self.provider = GupyProvider(fetcher, max_pages=max_pages)
        #: ALL THREE by default, and that is a correction rather than a tuning.
        #:
        #: This defaulted to remote and hybrid because the owner works remotely,
        #: which mistook a fact about ONE candidate for a fact about the corpus.
        #: Invariant 4 says the opposite: the same posting must produce the same
        #: fingerprint for every candidate, because the corpus describes work
        #: and a later layer decides whether it is wanted. A lawyer moving into
        #: an office job and a candidate in Utah are served by the same database
        #: and neither is served by one filtered to somebody else's taste. The
        #: FILTERS decide what is shown; the collector decides nothing.
        self.workplace_types = workplace_types or WORKPLACE_TYPES
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> GupyStats:
        started = time.monotonic()
        stats = GupyStats(partitions=list(self.workplace_types))

        with transaction(self.conn):
            run_id = self.runs.start("collect-gupy")

        held_external_ids = self._held_external_ids()

        try:
            plan = self.provider.plan(
                workplace_types=self.workplace_types, cities_for=self._cities_for
            )
        except (FetchError, GupyError, ValueError) as exc:
            # A failed PLAN is a failure: without it there is nothing to walk.
            stats.failures.append(str(exc))
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
                )
            return stats

        stats.slices_planned = len(plan)
        stats.slices_over_ceiling = [one.label for one in plan if one.over_ceiling]

        # ONE SLICE AT A TIME, WALKED AND THEN PERSISTED.
        #
        # This read every slice into memory and persisted at the end, which was
        # fine for the three-hundred-posting feeds it was copied from and is
        # not fine here. A full plan reaches tens of thousands of postings, each
        # carrying its whole advert: holding them costs hundreds of megabytes,
        # and an interrupted run loses everything it had already fetched. This
        # session interrupted two collections, so that is a measured risk rather
        # than a hypothetical one.
        #
        # Persisting per slice bounds the memory to one slice and makes an
        # interruption keep what it collected. `seen` still spans the whole run,
        # because slices deliberately OVERLAP -- a parent is walked beside its
        # children so rows carrying no value for the cut dimension are not lost,
        # and 546 of 56,920 postings in the largest cell carry no state -- but
        # it holds only ids, not records.
        seen: set[str] = set()
        for slice_ in plan:
            if should_stop is not None and should_stop():
                break
            try:
                read = self.provider.read_slice(slice_)
            except (FetchError, GupyError, ValueError) as exc:
                # ONE slice failing is not the run failing. A walk that gave up
                # here would throw away every slice already persisted, which is
                # the failure mode this loop exists to prevent.
                stats.failures.append(f"{slice_.label}: {exc}")
                continue

            stats.pages_read += len(read.walk.pages)
            stats.stopped_early = stats.stopped_early or read.walk.hit_page_limit
            if read.hit_ceiling:
                stats.ceiling_hit.append(read.workplace_type)
            if read.claimed_total is not None:
                stats.partition_totals[read.workplace_type] = read.claimed_total
            stats.partition_read[read.workplace_type] = len(read.records)

            for record in read.records:
                if should_stop is not None and should_stop():
                    break
                key = str(record.get("id", ""))
                if not key:
                    continue
                if key in seen:
                    stats.slice_overlap += 1
                    continue
                seen.add(key)
                stats.postings_seen += 1
                self._handle(record, held_external_ids, stats)
                if on_progress is not None:
                    on_progress(stats.postings_seen, None)

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, record: dict[str, Any], held_external_ids: dict[str, str], stats: GupyStats
    ) -> None:
        stub = to_stub(record)
        if stub is None:
            stats.postings_unaddressable += 1
            return

        # RULE 1, checked first because it is free.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, record, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 3: the same URL another provider already stored. Rule 2 is not
        # separate here and its absence is not a gap: `jobUrl` IS the origin
        # pointer, on the employer's own subdomain, so the canonical-URL rule
        # already compares origins rather than an aggregator's own page.
        match = self._job_by_canonical_url(stub.url)
        if match is not None:
            self._record_sighting(match, stub, record, MATCH_CANONICAL_URL, stats)
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

        company = company_of(record)
        if not company:
            # Inventing an employer from the title is the fuzzy identity
            # ADR-0008 forbids.
            stats.postings_without_company += 1
            return

        identifier = board_identifier(record) or _slugify(company)

        # RULE 4: byte-identical description, FROM THE SAME EMPLOYER, FOR THE
        # SAME ROLE. Three fields matching is equality, not resemblance.
        if text_hash is not None:
            duplicate = self._job_by_content_hash(text_hash, slug=identifier, title=stub.title)
            if duplicate is not None:
                self._record_sighting(duplicate, stub, record, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(record, stub, posting, text_hash, company, identifier, stats)

    def _record_sighting(
        self, job_id: str, stub: Any, record: dict[str, Any], rule: str, stats: GupyStats
    ) -> None:
        """Record that this source also holds it. Deliberately the ONLY effect.

        No job field is touched. Whoever holds the original holds a better
        record than a second sighting of it, and the way to guarantee that
        survives is to have no code path that writes over it.
        """
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                payload=record,
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        record: dict[str, Any],
        stub: Any,
        posting: Any,
        text_hash: str | None,
        company: str,
        identifier: str,
        stats: GupyStats,
    ) -> None:
        with transaction(self.conn):
            slug = _slugify(company)
            existed = self.companies.get_by_slug(slug)
            company_id = self.companies.upsert(
                CompanyRecord(slug=slug, name=company, discovery_source=PROVIDER)
            )
            if existed is None:
                stats.companies_new += 1

            board_existed = self.boards.get_by_identifier(PROVIDER, identifier)
            board_id = self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=PROVIDER,
                    board_identifier=identifier,
                    # A REAL board URL, unlike every other feed collector here.
                    # The employer's own career page, with the portal's
                    # tracking token stripped: a board is a place, and a place
                    # with a campaign parameter on it is a link, not a place.
                    board_url=board_url(record),
                    discovery_method="gupy_portal",
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
                    # `city, state, country`. A PLACE OF WORK. See the module
                    # docstring: this is not a hiring scope and nothing here
                    # may read it as one.
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
            resolve_metadata(PROVIDER, self.provider.field_map, record)

    def _cities_for(self, state: str) -> tuple[str, ...]:
        """Cities already seen in this state, as the planner's last cut.

        LEARNED rather than listed. A city list is unbounded and a hardcoded
        one silently loses whichever city the vendor added last; this asks the
        corpus what it already holds for the state, so the plan sharpens every
        run and a first run simply stops one level higher.

        Read out of `job.location_raw`, which this collector composed as
        `city, state, country`, so the city is the first field and it is the
        vendor's own spelling rather than a normalisation of one.
        """
        if not state:
            return ()
        rows = self.conn.execute(
            "SELECT DISTINCT location_raw FROM job WHERE provider = ? AND location_raw LIKE ?",
            (PROVIDER, f"%, {state}, %"),
        ).fetchall()
        found = {
            str(row["location_raw"]).split(",")[0].strip()
            for row in rows
            if str(row["location_raw"]).count(",") >= 2
        }
        return tuple(sorted(city for city in found if city))

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        """Every `external_id` held by ANOTHER provider, mapped to its job id.

        `provider != ?`, and the exclusion is the correction every sibling
        collector carries: applied to this source's own rows the rule would
        file a posting collected last week as a sighting of itself.
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
            " JOIN source_board b ON b.id = j.source_board_id"
            " WHERE j.content_hash = ? AND j.provider != ? AND b.board_identifier = ?"
            " AND j.title = ? LIMIT 1",
            (text_hash, PROVIDER, slug, title),
        ).fetchone()
        return str(row["id"]) if row else None


def board_identifier(record: Any) -> str | None:
    """The vendor's own stable employer id, as a string.

    Preferred over a slugified name because a slug is a normalisation that
    hopes: it merges two spellings that are two companies and separates two
    spellings that are one. `careerPageId` is an identifier.
    """
    if not isinstance(record, dict):
        return None
    value = record.get("careerPageId")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip().isdigit():
        return value.strip()
    return None


def board_url(record: Any) -> str | None:
    """The employer's career page, without the portal's tracking token.

    `careerPageUrl` arrives as `https://grupoboticario.gupy.io/<base64>`, where
    the path is a campaign marker rather than a location. The origin alone is
    the board. Anything not on `*.gupy.io` is refused rather than stored: a
    field that suddenly pointed elsewhere would put an unverified URL in front
    of somebody as this employer's own page.
    """
    if not isinstance(record, dict):
        return None
    raw = record.get("careerPageUrl")
    if not isinstance(raw, str) or not raw.strip():
        return None
    parts = urlsplit(raw.strip())
    if parts.scheme != "https" or not (parts.hostname or "").endswith(".gupy.io"):
        return None
    return f"https://{parts.hostname}"


def _host(url: str) -> str:
    return urlsplit(url).hostname or ""


def _slugify(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

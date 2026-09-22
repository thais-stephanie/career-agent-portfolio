"""Collecting We Work Remotely, one feed at a time.

Shorter than the Speedrun collector for two reasons that are worth stating
rather than inferring from the line count.

**There is no detail request.** The feed carries the whole posting, so a pass
costs exactly one request per feed and `descriptions_empty` counts a genuinely
empty description rather than a fetch that failed. That is the single biggest
difference between this source and every aggregator that returns an excerpt.

**There is no origin rule.** Speedrun publishes the employer's own apply URL,
so `identify_posting_url` can turn a republished posting into an exact
`(provider, external_id)` key and collapse it onto the ATS record. WWR links to
WWR. Its `guid` and its `link` are both a page on their own site, so there is
nothing to resolve against and the ORIGIN_URL rule is absent -- not skipped,
not weakened, absent, because the field it reads does not exist here.

The consequence is honest and bounded: a job posted both on WWR and on an
employer's Greenhouse board will be TWO rows unless their description text is
byte-identical. It usually is not, because WWR renders its own HTML. That is a
duplicate this source cannot avoid without fuzzy matching, and fuzzy matching
is the thing ADR-0008 closes by forbidding. Two visible rows for one job is a
smaller harm than one invisible job, which is the trade that document already
made.

**A FEED IS A WINDOW.** Twenty-five recent postings, no pagination, no archive.
Collecting repeatedly accumulates over time and never completes, so nothing
built on these numbers may present them as coverage of remote hiring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import resolve_metadata
from career_agent.providers.registry import canonical_posting_url, get_provider
from career_agent.providers.wwr import FEEDS, WwrProvider, company_of
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

PROVIDER = "wwr"

#: Match rules, strongest first, and one fewer than the aggregator next door.
#: `ORIGIN_URL` is absent because this source publishes no origin pointer; see
#: the module docstring.
MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class WwrStats:
    """What one pass did. Every number counted, none estimated."""

    feeds: list[str] = field(default_factory=list)
    feeds_read: int = 0
    feeds_failed: int = 0
    postings_seen: int = 0
    #: Items the feed carried that carried no `guid` or no title. A posting we
    #: cannot address twice is a duplicate waiting to be created.
    postings_unaddressable: int = 0
    #: Items dropped because WWR's `Company: Role` title had no colon, so no
    #: employer could be resolved without guessing one.
    postings_without_company: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    companies_new: int = 0
    boards_new: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    #: A feed that answered and held nothing. NOT a failure, and the
    #: distinction is why it has its own counter: an empty read must never be a
    #: reason to close a job.
    feeds_empty: int = 0
    elapsed_ms: int = 0
    failures: list[str] = field(default_factory=list)
    http: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "feeds": list(self.feeds),
            "feeds_read": self.feeds_read,
            "feeds_failed": self.feeds_failed,
            "feeds_empty": self.feeds_empty,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_without_company": self.postings_without_company,
            "duplicates": dict(sorted(self.duplicates.items())),
            "duplicates_total": self.duplicates_total,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "elapsed_ms": self.elapsed_ms,
            "failures": list(self.failures),
            "http": dict(self.http),
            # Said in the stats, not only in a docstring, so a report built
            # from this dictionary cannot accidentally claim completeness.
            "coverage_note": (
                "A feed window of the most recent postings, with no pagination "
                "and no archive. These counts are what the feeds held when "
                "asked; they are not coverage of remote hiring."
            ),
        }


class WwrCollector:
    """One pass over one or more feeds, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider: WwrProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(
        self,
        feeds: tuple[str, ...] = ("all",),
        on_progress: Any = None,
        should_stop: Any = None,
    ) -> WwrStats:
        """Read each named feed once and persist what is genuinely new.

        Defaults to the single global feed rather than to all nine. The
        category feeds OVERLAP with it heavily, so asking for everything costs
        nine requests to see mostly the same postings, and a default that does
        that to somebody else's server is not this project's collection
        posture.

        ONE FEED FAILING DOES NOT END THE PASS, and does not close anything.
        It is counted in `feeds_failed` and named in `failures`, because a run
        that quietly read six of nine feeds looks identical to a run that found
        fewer postings.
        """
        started = time.monotonic()
        stats = WwrStats(feeds=list(feeds))

        with transaction(self.conn):
            run_id = self.runs.start("collect-wwr")

        held_external_ids = self._held_external_ids()

        for name in feeds:
            if should_stop is not None and should_stop():
                break
            try:
                read = self.provider.read_feed(name)
            except (FetchError, ValueError) as exc:
                stats.feeds_failed += 1
                stats.failures.append(f"{name}: {exc}")
                continue

            stats.feeds_read += 1
            stats.postings_unaddressable += read.unaddressable
            if read.empty:
                stats.feeds_empty += 1
                continue

            for index, row in enumerate(read.items, start=1):
                if should_stop is not None and should_stop():
                    break
                stats.postings_seen += 1
                self._handle(row, held_external_ids, stats)
                if on_progress is not None:
                    on_progress(index, len(read.items))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                PipelineRunStatus.OK if not stats.failures else PipelineRunStatus.FAILED,
                stats=stats.as_dict(),
                error=None if not stats.failures else f"{len(stats.failures)} feed(s) failed",
            )
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(self, row: dict[str, Any], held_external_ids: dict[str, str], stats: WwrStats):
        stub = self.provider.to_stub(row)
        if stub is None:
            stats.postings_unaddressable += 1
            return

        # RULE 1, checked first because it is free: a posting another provider
        # already holds under this id is the same posting.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, row, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 2 in the other collector reads the employer's apply link. There
        # is none here. See the module docstring.

        # RULE 3: the same URL as one we already stored, compared as a string.
        match = self._job_by_canonical_url(stub.url)
        if match is not None:
            self._record_sighting(match, stub, row, MATCH_CANONICAL_URL, stats)
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

        company = company_of(row)
        if not company:
            # WWR writes `Company: Role`. Without the colon there is no
            # employer, and inventing one from the first two words is the fuzzy
            # identity ADR-0008 closes by forbidding.
            stats.postings_without_company += 1
            return

        slug = _slugify(company)

        # RULE 4: byte-identical description, FROM THE SAME EMPLOYER, FOR THE
        # SAME ROLE. Three fields matching is equality, not resemblance -- the
        # hash alone once filed 53 postings from 53 distinct shops as one job.
        if text_hash is not None:
            match = self._job_by_content_hash(text_hash, slug=slug, title=stub.title)
            if match is not None:
                self._record_sighting(match, stub, row, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(row, stub, posting, text_hash, company, slug, stats)

    def _record_sighting(
        self, job_id: str, stub: Any, row: dict[str, Any], rule: str, stats: WwrStats
    ) -> None:
        """Record that this source also holds it. Deliberately the ONLY effect.

        No job field is touched. Whoever holds the original holds a better
        record than this source's copy of it, and the way to guarantee that
        survives is to have no code path that writes over it.
        """
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                payload=row,
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        row: dict[str, Any],
        stub: Any,
        posting: Any,
        text_hash: str | None,
        company: str,
        slug: str,
        stats: WwrStats,
    ) -> None:
        """A posting nothing else in the corpus holds.

        One board per company, created on demand, which is what keeps ADR-0008
        true through a source whose real shape is a category feed: registering
        the whole site as one board carrying hundreds of employers is exactly
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
                    # The company slug, because a WWR "board" is a fiction this
                    # collector maintains to keep one employer per board. The
                    # FEED name would be the other option and would put many
                    # employers on one board.
                    board_identifier=slug,
                    # No URL, because there is no such page. WWR has no
                    # per-employer board, and inventing a search link and
                    # storing it as a board URL would make a query look like a
                    # place. `Source.search_url` is where a link somebody can
                    # open by hand belongs.
                    board_url=None,
                    discovery_method="wwr_feed",
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
            # The same neutral mechanism the board collector uses: ask the
            # adapter which paths carry meaning, resolve them without naming one.
            resolve_metadata(PROVIDER, self.provider.field_map, posting.payload)

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        """Every `external_id` held by ANOTHER provider, mapped to its job id.

        `provider != ?` and the exclusion is the correction Speedrun's version
        already carries: applied to this source's OWN rows the rule would say a
        posting collected last week needs no further attention, which freezes
        its `last_seen_at`, keeps a rewritten description out of the corpus,
        and writes the one discovery row migration 0018 forbids.
        """
        rows = self.conn.execute(
            "SELECT id, external_id FROM job WHERE provider != ?", (PROVIDER,)
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}

    def _job_by_canonical_url(self, url: str) -> str | None:
        """A job ANOTHER provider stored at this same URL.

        `provider != ?`, and the exclusion is not cosmetic. The URL compared
        here is this source's own posting page, so without it every posting
        matches the row this collector wrote last time and a second pass files
        all of them as sightings of themselves -- which freezes `last_seen_at`,
        keeps a rewritten description out of the corpus, and writes the exact
        row migration 0018 forbids.

        The tests caught it on the first run: a second pass reported
        `{CANONICAL_URL: 3}` and `jobs_seen_again: 0`. It is the same defect
        `_held_external_ids` excludes for rule 1, arriving through rule 3
        because this source's `link` is its own page rather than an employer's.
        """
        target = canonical_posting_url(url)
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


def known_feeds() -> tuple[str, ...]:
    """Every feed name this collector will accept. Named, never discovered."""
    return tuple(sorted(FEEDS))


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _slugify(name: str) -> str:
    """A company slug from a display name.

    The same shape the other collectors use. It is identity for a company
    record and nothing else -- no posting is ever matched to another by it.
    """
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", folded.lower())).strip("-")

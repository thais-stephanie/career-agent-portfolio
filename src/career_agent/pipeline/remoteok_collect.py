"""Collecting Remote OK, one window of a feed that carries its own licence.

Three rules rather than four, and the absence is the reason this source was
refused once. `apply_url` is byte-identical to `url` on every row, so there is
no ORIGIN pointer to resolve against and rule 2 is unreachable. Rules 1, 3 and
4 remain, and rule 4 -- byte-identical description from the same employer for
the same role -- works here precisely BECAUSE the feed carries full bodies.

The consequence is stated rather than discovered later: a job listed here and
on an employer's own Greenhouse board is two rows unless the external id, the
canonical URL or the body already matched. **Two visible rows for one job is a
smaller harm than one invisible job**, which is the trade ADR-0008 made when it
forbade fuzzy matching and the trade Himalayas already shipped under.

**THE FIRST ELEMENT OF THE RESPONSE IS THE LICENCE, NOT A JOB.** It carries
`legal` and no `id`. This collector stores the text it was served under on the
run rather than trusting a sentence in a docstring, because the terms are a
grant with conditions and the conditions are what make collecting it lawful:
link back to the Remote OK URL, credit Remote OK, do not use the logo.

`job.url` is the feed's own URL and this collector produces no other Apply
target, so the link back is structural rather than a habit.

**A WINDOW, NOT A MARKET.** One request returns what the board is showing now.
There is no paging parameter, so repeated collection accumulates over time and
never yields a complete board. Nothing built on these counts may present them
as coverage of remote hiring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.base import resolve_metadata
from career_agent.providers.registry import get_provider
from career_agent.providers.remoteok import (
    RemoteOkProvider,
    company_of,
    to_stub,
)
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

PROVIDER = "remoteok"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class RemoteOkStats:
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
    #: Postings that say where the work is, and those that say nothing.
    #: `location` is empty on most rows and empty is stored as nothing rather
    #: than as `Remote`: a remote posting with no stated place resolves to no
    #: country, which is the honest answer.
    with_location: int = 0
    without_location: int = 0
    #: The licence text this run was served, recorded verbatim.
    #:
    #: Not decoration. The grant is conditional and the vendor restates it in
    #: every response, so the run that collected under it should carry what it
    #: agreed to rather than what a docstring remembers.
    licence: str | None = None
    #: Elements the response carried before the licence row was separated out.
    #: Kept so "the feed shrank" and "we filtered more" stay answerable apart.
    elements_served: int = 0
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
            "with_location": self.with_location,
            "without_location": self.without_location,
            "licence": self.licence,
            "elements_served": self.elements_served,
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


class RemoteOkCollector:
    """One pass over the feed, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher, max_pages: int | None = None) -> None:
        self.conn = conn
        self.fetcher = fetcher
        # There is no page budget here because there is no paging: one
        # request returns the window. `max_pages` is accepted and ignored so
        # the collector signature matches its siblings, and saying so is
        # better than a parameter that silently does nothing.
        del max_pages
        self.provider: RemoteOkProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> RemoteOkStats:
        """Read the feed once, to the page budget, and persist what is new."""
        started = time.monotonic()
        stats = RemoteOkStats()

        with transaction(self.conn):
            run_id = self.runs.start("collect-remoteok")

        held_external_ids = self._held_external_ids()

        try:
            read = self.provider.read_feed()
        except (FetchError, ValueError) as exc:
            # A failed walk is a failure, never an empty feed. Conflating them
            # is how a network blip closes a company's whole posting history.
            stats.failures.append(str(exc))
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
                )
            return stats

        stats.pages_read = 1
        stats.licence = read.licence
        stats.elements_served = read.elements
        stats.claimed_total = len(read.records)

        for index, job in enumerate(read.records, start=1):
            if should_stop is not None and should_stop():
                break
            stats.postings_seen += 1
            self._handle(job, held_external_ids, stats)
            if on_progress is not None:
                on_progress(index, len(read.records))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, job: dict[str, Any], held_external_ids: dict[str, str], stats: RemoteOkStats
    ) -> None:
        stub = to_stub(job)
        if stub is None:
            stats.postings_unaddressable += 1
            return

        # RULE 1, checked first because it is free.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, job, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 2 (ORIGIN_URL) is unreachable. See the module docstring: this
        # feed publishes no employer apply link, measured rather than assumed.

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
        self, job_id: str, stub: Any, job: dict[str, Any], rule: str, stats: RemoteOkStats
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
        stats: RemoteOkStats,
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
                    discovery_method="remoteok_feed",
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
                    # Where the work is, when the board says anything at all.
                    # NOT a hiring scope: `publishes_hiring_scope` is False for
                    # this adapter and nothing may read this column as one.
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

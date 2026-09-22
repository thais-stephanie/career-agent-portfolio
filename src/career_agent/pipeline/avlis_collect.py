"""Collecting Avlis Talent, one request against a six-posting board.

The smallest collector here, and it exists because "too small to bother" is a
judgement about whether work is worth SHOWING, which ADR-0019 puts in the
filter layer rather than in a collector. Six postings at United States startups
open to Brazilians is exactly the market one of this product's first three
users is looking at.

Three differences from its siblings, and each one is a thing this source cannot
do rather than a thing it does badly.

**EVERY POSTING SHARES ONE URL.** There is no per-posting page: `/jobs/26`,
`/job/26`, `/jobs?id=26` and `/jobs#26` all return the same application shell,
measured 2026-09-09. The external id stays unique so identity is intact, and
the ADR-0013 CANONICAL_URL rule is therefore useless here -- six postings
sharing a URL cannot be told apart by it. Rules 1 and 4 do the work.

**THE EMPLOYER IS THE AGENCY.** Avlis places people at client startups and the
clients are not named in the feed. The company stored is Avlis Talent, which is
who a person applies to, and inventing a client name from the advert would be
the fuzzy identity ADR-0008 forbids.

**`active: false` IS THE AGENCY SPEAKING.** A row marked closed is not
collected, and that is different from a posting simply vanishing from a feed.
It is counted so the two never read alike.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.avlis import (
    AGENCY,
    AvlisProvider,
    to_stub,
)
from career_agent.providers.base import resolve_metadata
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

PROVIDER = "avlis"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class AvlisStats:
    """What one pass did, with the bound it ran under kept visible."""

    #: There is no page count and no page budget, because there is no
    #: pagination: one request returns the window. Fields for a bound that
    #: does not exist would describe this source as something it is not.
    postings_seen: int = 0
    postings_unaddressable: int = 0
    #: Rows the agency marks `active: false`. NOT a failure and not a
    #: disappearance: an agency saying a role is closed is the employer
    #: speaking, and a posting vanishing from a feed is not.
    postings_closed_by_source: int = 0
    postings_without_company: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    companies_new: int = 0
    boards_new: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    deferred_reason: str | None = None
    elapsed_ms: int = 0
    http: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "deferred_reason": self.deferred_reason,
            "stopped_early": self.deferred_reason is not None,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
            "postings_closed_by_source": self.postings_closed_by_source,
            "postings_without_company": self.postings_without_company,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            # Said in the stats rather than only in a docstring, because this
            # is what a source panel reads.
            "coverage_note": ("one read of a window of recent postings, never a whole board"),
        }


class AvlisCollector:
    """One pass over the feed, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider: AvlisProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(self, on_progress: Any = None, should_stop: Any = None) -> AvlisStats:
        """Read the window once and persist what is genuinely new."""
        started = time.monotonic()
        stats = AvlisStats()

        with transaction(self.conn):
            run_id = self.runs.start("collect-avlis")

        held_external_ids = self._held_external_ids()

        try:
            read = self.provider.read_feed()
        except BudgetExpired:
            stats.deferred_reason = "TIME_BUDGET"
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict())
            return stats
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

        stats.postings_closed_by_source = read.closed

        for index, job in enumerate(read.records, start=1):
            if should_stop is not None and should_stop():
                break
            try:
                self.fetcher.check_budget()
            except BudgetExpired:
                stats.deferred_reason = "TIME_BUDGET"
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
        self, job: dict[str, Any], held_external_ids: dict[str, str], stats: AvlisStats
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

        # RULE 2 (ORIGIN_URL) is unreachable. This feed publishes no employer
        # apply link: the clients are not named at all.

        # RULE 3 (CANONICAL_URL) IS DELIBERATELY NOT USED HERE, and this is the
        # one place in this repository where that rule is switched off.
        #
        # Every posting on this board shares ONE url, because the board has one
        # page -- `/jobs/26`, `/job/26`, `/jobs?id=26` and `/jobs#26` all
        # return the same application shell, measured 2026-09-09. A rule that
        # says "the same URL means the same posting" is therefore false here
        # for six rows out of six, and applying it would file five of them as
        # sightings of the first.
        #
        # Rules 1 and 4 still run: an external id another provider holds, and a
        # byte-identical description from the same employer for the same role.

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

        # THE AGENCY, always. Avlis places people at client startups and the
        # feed does not name the clients, so the employer stored is who a
        # person actually applies to. Reading a client name out of the advert
        # would be the fuzzy identity ADR-0008 forbids.
        company = AGENCY
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
        self, job_id: str, stub: Any, job: dict[str, Any], rule: str, stats: AvlisStats
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
        stats: AvlisStats,
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
                    discovery_method="avlis_feed",
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
                    # Stored and shown; NEVER read as a hiring scope.
                    # `publishes_hiring_scope` is False for this adapter.
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
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

"""Collecting Get on Board, one category at a time.

The same shape as the We Work Remotely collector next door, with two
differences that change what the numbers mean.

**Rule 2 cannot fire, and rule 4 now can.** `ORIGIN_URL` is unreachable
because this source publishes no employer apply link -- `links.public_url` is
a page on getonbrd.com -- so a job listed both here and on an employer's
Greenhouse board is TWO rows unless the external id, the canonical URL or the
body already matched. **Two visible rows for one job is a smaller harm than
one invisible job**, which is the trade ADR-0008 already made when it forbade
fuzzy matching.

Rule 4 was unreachable too until 2026-09-07, when the owner's first live
retrieval showed the feed carries the employer's posting after all, split
across the five fields Get on Board's posting form asks for separately. The
adapter composes them; this collector stores the result and hashes it, and the
content-hash rule fires under the sibling collectors' three-field form.

**There IS pagination, and the walk carries its own bound.** WWR's feed is a
window with no pagination to get wrong. This one is a paginated JSON:API, so
`providers/feeds.py` does the walking and `CategoryRead.walk` says whether the
walk finished, hit the page budget, or was truncated by a feed that stopped
serving what it claimed to hold. Those are three different outcomes and the
stats keep them apart: a run that read three pages of forty must never report
the same way as one that read the whole category.

**ZERO REQUESTS HAVE BEEN MADE FROM THIS MACHINE.** `www.getonbrd.com`
allows a generic reader and names `ClaudeBot` with `Disallow: /`; this
collector is not ClaudeBot, and the agent that wrote it is. Everything here is
tested against fixtures built from the vendor's contract, and the first live
run belongs to the owner. `sources.resolve` refuses to call the source
operational until the corpus proves it.

**A CATEGORY IS NOT A MARKET.** Five categories read to three pages each is an
answer about five slices of one LATAM board, and nothing built on these numbers
may present them as coverage of Latin American hiring.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import resolve_metadata
from career_agent.providers.getonbrd import (
    DEFAULT_CATEGORIES,
    CategoryError,
    GetonbrdProvider,
    company_of,
    to_stub,
)
from career_agent.providers.registry import canonical_posting_url, get_provider
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

PROVIDER = "getonbrd"

#: Three rules, not four, and the one absence has a reason.
#:
#: `ORIGIN_URL` is absent because this source publishes no employer apply link:
#: `links.public_url` is a page on getonbrd.com. It is not skipped or weakened.
#:
#: `CONTENT_HASH` used to be absent too, for a reason that stopped being true
#: on 2026-09-07: there was said to be no description to hash. There is, and
#: the rule is the sibling collectors' -- byte-identical text FROM THE SAME
#: EMPLOYER FOR THE SAME ROLE, because the hash alone once filed 53 postings
#: from 53 distinct shops as one job.
MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class GetonbrdStats:
    """What one pass did, with the bound it ran under kept visible."""

    categories: list[str] = field(default_factory=list)
    categories_read: int = 0
    categories_empty: int = 0
    categories_failed: int = 0
    #: Categories whose walk stopped at the page budget rather than at the end
    #: of the feed. Counted separately from a failure because nothing broke:
    #: there is simply more, and a caller that cannot tell will report a bounded
    #: read as a complete one.
    categories_page_limited: int = 0
    #: Categories where the feed stopped serving before its own stated total.
    #: This one IS a problem and must not be filed beside the line above.
    categories_truncated: int = 0
    postings_seen: int = 0
    postings_unaddressable: int = 0
    postings_without_company: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    #: A posting whose stored body changed since the last pass. Only ever set
    #: when there is a body to compare, which is why it sat at zero until the
    #: description was mapped.
    jobs_changed: int = 0
    #: How many postings arrived with a composed body and how many did not.
    #: Kept apart so "this source stopped publishing descriptions" cannot hide
    #: inside "this source returned fewer postings".
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    companies_new: int = 0
    boards_new: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    http: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    @property
    def complete(self) -> bool:
        """Whether every category was read to the end of its feed.

        False when anything was page-limited, truncated or failed. A single
        property, because "did we get all of it" is the question a person
        actually asks and three fields is how it gets answered wrongly.
        """
        return not (
            self.categories_page_limited or self.categories_truncated or self.categories_failed
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "categories": list(self.categories),
            "categories_read": self.categories_read,
            "categories_empty": self.categories_empty,
            "categories_failed": self.categories_failed,
            "categories_page_limited": self.categories_page_limited,
            "categories_truncated": self.categories_truncated,
            "postings_seen": self.postings_seen,
            "postings_unaddressable": self.postings_unaddressable,
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
            "complete": self.complete,
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
        }


class GetonbrdCollector:
    """One pass over one or more categories, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher, max_pages: int | None = None) -> None:
        self.conn = conn
        self.fetcher = fetcher
        provider = get_provider(PROVIDER, fetcher)
        # The registry builds it from a fetcher alone and cannot carry a page
        # budget, so a caller that asked for one gets its own instance. The
        # registry call above still happens, because it is what proves this
        # provider is registered rather than merely importable.
        self.provider: GetonbrdProvider = (
            GetonbrdProvider(fetcher, max_pages=max_pages) if max_pages is not None else provider  # type: ignore[assignment]
        )
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.jobs = JobRepo(conn)
        self.raw = JobRawRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def collect(
        self,
        categories: tuple[str, ...] | None = None,
        on_progress: Any = None,
        should_stop: Any = None,
    ) -> GetonbrdStats:
        """Read each named category once and persist what is genuinely new.

        ONE CATEGORY FAILING DOES NOT END THE PASS and closes nothing. It is
        counted in `categories_failed` and named in `failures`, because a run
        that quietly read three of five categories looks identical to a run
        that found fewer postings -- and the second is a fact about the market
        while the first is a fact about us.
        """
        started = time.monotonic()
        chosen = categories or DEFAULT_CATEGORIES
        stats = GetonbrdStats(categories=list(chosen))

        with transaction(self.conn):
            run_id = self.runs.start("collect-getonbrd")

        held_external_ids = self._held_external_ids()
        #: Postings seen earlier in THIS pass. A posting is listed under every
        #: category it belongs to, so without this the second category files
        #: the first one's rows as sightings of themselves.
        seen_this_pass: set[str] = set()

        for name in chosen:
            if should_stop is not None and should_stop():
                break
            try:
                read = self.provider.read_category(name)
            except (FetchError, ValueError, CategoryError) as exc:
                stats.categories_failed += 1
                stats.failures.append(f"{name}: {exc}")
                continue

            stats.categories_read += 1
            stats.postings_unaddressable += read.unaddressable
            if read.walk.hit_page_limit:
                stats.categories_page_limited += 1
            if read.walk.truncated:
                stats.categories_truncated += 1
                stats.failures.append(
                    f"{name}: the feed stopped serving before its own stated total of "
                    f"{read.walk.claimed_total}. Read {len(read.resources)}."
                )
            if not read.resources:
                stats.categories_empty += 1
                continue

            for index, resource in enumerate(read.resources, start=1):
                if should_stop is not None and should_stop():
                    break
                stats.postings_seen += 1
                self._handle(resource, held_external_ids, seen_this_pass, stats)
                if on_progress is not None:
                    on_progress(index, len(read.resources))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                PipelineRunStatus.OK if not stats.failures else PipelineRunStatus.FAILED,
                stats=stats.as_dict(),
                error=None if not stats.failures else f"{len(stats.failures)} category(ies) failed",
            )
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self,
        resource: dict[str, Any],
        held_external_ids: dict[str, str],
        seen_this_pass: set[str],
        stats: GetonbrdStats,
    ) -> None:
        stub = to_stub(resource)
        if stub is None:
            stats.postings_unaddressable += 1
            return

        # A posting listed under a second category, inside one pass. Not a
        # duplicate in the ADR-0013 sense and deliberately not recorded as one:
        # it is the same feed handing back the same row, and filing a discovery
        # sighting for it would be this source citing itself.
        if stub.external_id in seen_this_pass:
            return
        seen_this_pass.add(stub.external_id)

        # RULE 1, checked first because it is free: a posting another provider
        # already holds under this id is the same posting.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, resource, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 2 (ORIGIN_URL) is unreachable here. See the module docstring:
        # this source publishes no employer apply link.

        # RULE 3: the same URL as one another provider already stored.
        match = self._job_by_canonical_url(stub.url)
        if match is not None:
            self._record_sighting(match, stub, resource, MATCH_CANONICAL_URL, stats)
            return

        # The body, composed from the sections the feed splits it into. No
        # second request: `fetch_posting` reads the resource it was handed.
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

        company = company_of(resource)
        if not company:
            # `expand[]=company` was asked for and this row carried none. The
            # employer is genuinely unknown, and inventing one is the fuzzy
            # identity ADR-0008 forbids.
            stats.postings_without_company += 1
            return

        slug = _slugify(company)

        # RULE 4: byte-identical description, FROM THE SAME EMPLOYER, FOR THE
        # SAME ROLE. Three fields matching is equality, not resemblance.
        if text_hash is not None:
            match = self._job_by_content_hash(text_hash, slug=slug, title=stub.title)
            if match is not None:
                self._record_sighting(match, stub, resource, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(resource, stub, posting, text_hash, company, slug, stats)

    def _record_sighting(
        self, job_id: str, stub: Any, resource: dict[str, Any], rule: str, stats: GetonbrdStats
    ) -> None:
        """Record that this source also holds it. Deliberately the ONLY effect.

        No job field is touched. Whoever holds the original holds a better
        record than this source's metadata-only copy -- there is not even a
        description here to prefer -- and the way to guarantee that survives is
        to have no code path that writes over it.
        """
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                payload=resource,
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        resource: dict[str, Any],
        stub: Any,
        posting: Any,
        text_hash: str | None,
        company: str,
        slug: str,
        stats: GetonbrdStats,
    ) -> None:
        """A posting nothing else in the corpus holds.

        One board per company, created on demand, which is what keeps ADR-0008
        true through a source whose real shape is a category feed. Registering
        the whole board as one carrying hundreds of employers is exactly the
        attribution failure that document exists to prevent.

        **NORMALISED when there is a body, FETCHED when there is not.** The
        distinction is the one this collector shipped with and it has simply
        changed sides for most rows: FETCHED says the text stage never had
        anything to run on, and a posting whose sections were all empty still
        earns it.
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
                    # The company slug, because a Get on Board "board" is a
                    # fiction this collector maintains to keep one employer per
                    # board. The CATEGORY name would be the other option and
                    # would put many employers on one board.
                    board_identifier=slug,
                    # No URL: there is no per-employer board page this adapter
                    # has verified. Inventing a search link and storing it as a
                    # board URL would make a query look like a place.
                    board_url=None,
                    discovery_method="getonbrd_category",
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
                ProviderPayloadRecord(job_id=job_id, provider=PROVIDER, payload=dict(resource))
            )
            # The same neutral mechanism the board collector uses: ask the
            # adapter which paths carry meaning, resolve them without naming one.
            resolve_metadata(PROVIDER, self.provider.field_map, resource)

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        """Every `external_id` held by ANOTHER provider, mapped to its job id.

        `provider != ?`, and the exclusion is the correction both sibling
        collectors already carry: applied to this source's OWN rows the rule
        would file a posting collected last week as a sighting of itself, which
        freezes `last_seen_at` and writes the one discovery row migration 0018
        forbids.
        """
        rows = self.conn.execute(
            "SELECT id, external_id FROM job WHERE provider != ?", (PROVIDER,)
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}

    def _job_by_content_hash(self, text_hash: str, *, slug: str, title: str) -> str | None:
        """One job, matched on all three of hash, employer and title.

        `provider != ?` for the same reason every sibling carries it: without
        it this source files last week's own row as a sighting of itself.
        """
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

    def _job_by_canonical_url(self, url: str) -> str | None:
        """A job ANOTHER provider stored at this same URL.

        `provider != ?` for the same reason as above: the URL compared here is
        this source's own posting page, so without the exclusion every posting
        matches the row this collector wrote last time.
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


def known_categories() -> tuple[str, ...]:
    """The categories this collector reads by default. Named, never discovered."""
    return DEFAULT_CATEGORIES


def _host(url: str) -> str:
    from urllib.parse import urlparse

    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def _slugify(name: str) -> str:
    """A company slug from a display name.

    The same shape the other collectors use. It is identity for a COMPANY
    record and nothing else -- no posting is ever matched to another by it.
    """
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", folded.lower())).strip("-")

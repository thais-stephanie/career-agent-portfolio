"""Collecting from an aggregator, without letting it double-count the corpus.

`pipeline/collect.py` walks boards. This walks a feed, and the difference is
not just the request shape.

WHY THIS IS NOT `Collector` WITH A FOURTH PROVIDER
---------------------------------------------------
`Collector` asks each `source_board` for its postings. That works because a
board belongs to one company (ADR-0008) and every posting on it belongs to that
company. An aggregator's feed carries hundreds of employers in one response, so
there is no board to ask.

The obvious shortcut is to register the feed as a single board. It is also the
one thing ADR-0008 exists to prevent: hundreds of companies' postings attached
to one "board" makes every per-company and per-source number meaningless.

So the feed is read page by page for efficiency (50 postings per request rather
than one request per company) and then GROUPED BY COMPANY for storage, with one
`source_board` per company. Efficient retrieval, honest attribution, and no
change to what a board means.

THE DEDUPLICATION, AND WHY IT HAS NO SIMILARITY SCORE IN IT
------------------------------------------------------------
Four rules, tried in this order, each of them exact:

  1. EXTERNAL_ID    the aggregator's own job id is byte-identical to an id we
                    already hold. Free: it needs no extra request, so a posting
                    we already have never costs a detail fetch.
  2. ORIGIN_URL     the aggregator publishes the employer's apply link, and a
                    provider adapter recognises it as its own posting. This is
                    the authoritative one, because it yields an exact
                    `(provider, external_id)` from the data itself rather than
                    from a coincidence of id minting.
  3. CANONICAL_URL  the apply link equals a stored job URL once scheme and host
                    are lowercased and query and fragment are dropped.
  4. CONTENT_HASH   the description text is byte-identical after normalisation.

Not one of them is a similarity measure. ADR-0008 closes by warning that no
fuzzy job merging exists in this system and that introducing one is a decision
with its own ADR; this introduces none. A posting that matches nothing is a new
posting, which is the safe direction to be wrong in: a false duplicate hides a
job the person should have seen, and a false new row is visible and countable.

Rule 1 is an optimisation that changes the request count, so it is worth being
explicit about what it assumes. This aggregator appears to mint its job id from
the source posting's id: every Ashby-origin posting sampled carried an id
byte-identical to the Ashby posting UUID. A collision would require two systems
to mint the same UUID independently. The rule is still verified rather than
trusted, because rule 2 is checked for every posting whose detail we do fetch,
and `matched_by` records which rule actually fired.

NEVER REPLACE RICHER DATA WITH POORER
--------------------------------------
When any rule matches, this module writes a `job_discovery_source` row and
NOTHING else. It does not update the job's description, its URL, its title, its
company or its payload. `DiscoverySourceRepo` has no method that could. The
employer's own record stays the employer's own record, and the aggregator's copy
is recorded as a sighting.

WHAT IT COSTS
-------------
Two requests per NEW posting: one page read amortised over 50, plus one detail
fetch. Zero extra requests for a posting rule 1 already matched. That asymmetry
is why a second run over the same feed is far cheaper than the first, and why
`max_pages` bounds the walk rather than a posting count.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import BoardRef, resolve_metadata
from career_agent.providers.registry import (
    canonical_posting_url,
    get_provider,
    identify_posting_url,
)
from career_agent.providers.speedrun import (
    SpeedrunProvider,
    origin_kind,
    origin_url,
    validate_contract,
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

PROVIDER = "speedrun"

#: Match rules, strongest first. The order here IS the order they are tried.
MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_ORIGIN_URL = "ORIGIN_URL"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


class ContractRefused(RuntimeError):
    """The published API contract no longer matches what the adapter reads.

    Raised BEFORE any feed request. A connector that keeps walking after its
    contract check failed is a connector that writes wrong data confidently,
    and the whole point of checking first is to make that impossible.
    """


@dataclass
class SpeedrunStats:
    """What one feed pass did. Every number is counted, none is estimated."""

    scope: str = ""
    contract: dict[str, Any] = field(default_factory=dict)
    pages_read: int = 0
    postings_seen: int = 0
    #: Postings that resolved to something already in the corpus, by rule.
    duplicates: dict[str, int] = field(default_factory=dict)
    #: Postings this source is the only holder of.
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    companies_new: int = 0
    boards_new: int = 0
    details_fetched: int = 0
    details_failed: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    postings_skipped_malformed: int = 0
    #: Where the origin links pointed. This is the measurement M1D section 14
    #: could not make: which systems hold the postings our registry misses.
    origin_hosts: dict[str, int] = field(default_factory=dict)
    origin_kinds: dict[str, int] = field(default_factory=dict)
    #: What the feed claims to hold, what the API will serve, and the gap.
    claimed_total: int | None = None
    servable_total: int | None = None
    beyond_reach: int = 0
    stopped_early: bool = False
    #: The walk ended on an empty page the feed had not finished serving. A
    #: separate field from `stopped_early`, because a bound we chose and a
    #: truncation we did not are different outcomes.
    truncated: bool = False
    elapsed_ms: int = 0
    failures: list[str] = field(default_factory=list)
    http: dict[str, Any] = field(default_factory=dict)

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope,
            "contract": self.contract,
            "pages_read": self.pages_read,
            "postings_seen": self.postings_seen,
            "duplicates": dict(sorted(self.duplicates.items())),
            "duplicates_total": self.duplicates_total,
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "companies_new": self.companies_new,
            "boards_new": self.boards_new,
            "details_fetched": self.details_fetched,
            "details_failed": self.details_failed,
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "postings_skipped_malformed": self.postings_skipped_malformed,
            "origin_hosts": dict(
                sorted(self.origin_hosts.items(), key=lambda item: (-item[1], item[0]))
            ),
            "origin_kinds": dict(sorted(self.origin_kinds.items())),
            "claimed_total": self.claimed_total,
            "servable_total": self.servable_total,
            "beyond_reach": self.beyond_reach,
            "stopped_early": self.stopped_early,
            "truncated": self.truncated,
            "elapsed_ms": self.elapsed_ms,
            "failures": self.failures[:10],
            "failures_total": len(self.failures),
            "http": self.http,
        }


class SpeedrunCollector:
    """One feed pass against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider: SpeedrunProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
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
        scope: str = "portfolio",
        max_pages: int | None = 2,
        on_progress: Any = None,
        should_stop: Any = None,
        check_contract: bool = True,
    ) -> SpeedrunStats:
        """Walk the feed, resolve identity, persist what is genuinely new.

        `max_pages` defaults to 2 (100 postings) rather than to None. A default
        that reads the whole feed would make an exploratory run cost thousands
        of requests against someone else's service, and this project's collection
        posture is deliberately polite. Reading more is a thing a person asks
        for.

        `check_contract` exists for tests, which drive this against a fake
        transport that has no specification to read. It is never False in
        production, and `ContractRefused` is raised before any feed request when
        the check fails.
        """
        started = time.monotonic()
        stats = SpeedrunStats(scope=scope)

        if check_contract:
            check = validate_contract(self.fetcher)
            stats.contract = check.as_dict()
            if not check.ok:
                raise ContractRefused(check.describe())

        with transaction(self.conn):
            run_id = self.runs.start("collect-speedrun")

        try:
            walk = self.provider.walk_feed(
                scope=scope, max_pages=max_pages, should_stop=should_stop
            )
        except FetchError as exc:
            stats.failures.append(f"{exc.category.value}: {exc.message}")
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=exc.message[:300]
                )
            raise

        stats.pages_read = len(walk.pages)
        stats.claimed_total = walk.claimed_total
        stats.servable_total = walk.servable_total
        stats.beyond_reach = walk.beyond_reach
        stats.stopped_early = walk.stopped_early

        # A walk that ended on an empty page the feed had not finished serving
        # is a truncated pass, not a complete one. Recorded as a failure so the
        # run is FAILED rather than OK: "we read 150 of 4,000 and stopped for a
        # reason nobody can see" must never be reported as a clean collection.
        # This API returns intermittent 500s, which is exactly how a mid-walk
        # empty page arrives.
        stats.truncated = walk.truncated
        if walk.truncated:
            stats.failures.append(
                f"the feed stopped serving at page {len(walk.pages) - 1} of "
                f"{walk.pages[-1].total_pages}, so this pass is incomplete"
            )

        entries = walk.entries
        stats.postings_seen = len(entries)

        # One query, not one per posting. A feed page of 50 against a corpus of
        # 18,550 would otherwise be 50 round trips before any work happened.
        held_external_ids = self._held_external_ids()

        for index, entry in enumerate(entries, start=1):
            if should_stop is not None and should_stop():
                stats.stopped_early = True
                break
            try:
                self._handle(entry, held_external_ids, stats)
            except FetchError as exc:
                # One posting's detail request failing must not end the pass.
                # It is counted, not swallowed: a run that quietly dropped
                # postings would look identical to a run that found fewer.
                stats.details_failed += 1
                stats.failures.append(f"{exc.category.value}: {exc.message[:160]}")
            if on_progress is not None:
                on_progress(index, len(entries))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                PipelineRunStatus.OK if not stats.failures else PipelineRunStatus.FAILED,
                stats=stats.as_dict(),
                error=None if not stats.failures else f"{len(stats.failures)} posting(s) failed",
            )
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, entry: dict[str, Any], held_external_ids: dict[str, str], stats: SpeedrunStats
    ) -> None:
        stub = self.provider._to_stub(entry)
        if stub is None:
            stats.postings_skipped_malformed += 1
            return

        # RULE 1, and it is checked before anything is fetched. A posting we
        # already hold under this id costs zero requests.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            with transaction(self.conn):
                self.discovery.record(
                    job_id=held,
                    source=PROVIDER,
                    external_id=stub.external_id,
                    matched_by=MATCH_EXTERNAL_ID,
                    url=stub.url,
                    payload=entry,
                )
            stats.duplicates[MATCH_EXTERNAL_ID] = stats.duplicates.get(MATCH_EXTERNAL_ID, 0) + 1
            return

        posting = self.provider.fetch_posting(_feed_board(entry), stub)
        stats.details_fetched += 1
        payload = posting.payload

        link = origin_url(payload)
        kind = origin_kind(payload) or "unstated"
        stats.origin_kinds[kind] = stats.origin_kinds.get(kind, 0) + 1
        if link:
            host = _host(link)
            stats.origin_hosts[host] = stats.origin_hosts.get(host, 0) + 1

        # RULE 2: the employer's apply link, recognised by the adapter that
        # built that shape. Exact `(provider, external_id)`, no guessing.
        if link:
            identified = identify_posting_url(link)
            if identified is not None:
                existing = self.jobs.get_by_external(*identified)
                if existing is not None:
                    self._record_duplicate(
                        existing["id"], stub, entry, link, MATCH_ORIGIN_URL, stats
                    )
                    return

            # RULE 3: the same URL as one we stored, compared as a string.
            match = self._job_by_canonical_url(link)
            if match is not None:
                self._record_duplicate(match, stub, entry, link, MATCH_CANONICAL_URL, stats)
                return

        text_hash = (
            self.raw.put(posting.description_text, posting.description_html)
            if posting.description_text
            else None
        )
        if posting.has_description:
            stats.descriptions_non_empty += 1
        else:
            stats.descriptions_empty += 1

        # RULE 4: byte-identical description text, FROM THE SAME EMPLOYER, FOR
        # THE SAME ROLE.
        #
        # It used to be the hash alone, and an independent functional review
        # measured what that does to this corpus:
        #
        #     content hashes shared by more than one job:  1,136
        #     ...of those, spanning more than one title:     386
        #     jobs sharing a hash:      3,084 of 21,218  (14.5%)
        #     worst group: 53 jobs, 53 DISTINCT titles, one description --
        #       "Bilingual Sales Representative (Spanish)" / Toronto
        #       "Sales Representative - Tecumseh Mall"     / Windsor
        #       "Sales Representative - Masonville Place"  / London
        #
        # A retailer writes one description and pastes it into fifty-three
        # postings for fifty-three shops. They are fifty-three jobs. The rule
        # would have filed an incoming posting as a sighting of whichever of
        # them SQLite happened to return first -- no ORDER BY, so not even
        # deterministically the same one twice -- and the job would vanish
        # from the list. That is precisely what this module's own docstring
        # calls the expensive failure: "a false duplicate hides a job the
        # person should have seen".
        #
        # Company and title are now part of the comparison, so the rule
        # catches what it was written for -- one employer reposting one role
        # -- and nothing else. Three fields matching is still equality; it is
        # not resemblance, and it is not a similarity score.
        if text_hash is not None:
            match = self._job_by_content_hash(text_hash, slug=_entry_slug(entry), title=stub.title)
            if match is not None:
                self._record_duplicate(match, stub, entry, link, MATCH_CONTENT_HASH, stats)
                return

        self._persist_new(entry, stub, posting, text_hash, stats)

    def _record_duplicate(
        self,
        job_id: str,
        stub: Any,
        entry: dict[str, Any],
        link: str | None,
        rule: str,
        stats: SpeedrunStats,
    ) -> None:
        """Record the sighting. Deliberately the ONLY thing that happens here.

        No job field is touched. The employer's own record is better than this
        source's copy of it, and the way to guarantee it survives is to have no
        code path that writes over it.
        """
        with transaction(self.conn):
            self.discovery.record(
                job_id=job_id,
                source=PROVIDER,
                external_id=stub.external_id,
                matched_by=rule,
                url=stub.url,
                origin_url=link,
                payload=entry,
            )
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _persist_new(
        self,
        entry: dict[str, Any],
        stub: Any,
        posting: Any,
        text_hash: str | None,
        stats: SpeedrunStats,
    ) -> None:
        """A posting this source is the only holder of.

        The company and the board are created on demand, one board per company,
        which is what keeps ADR-0008 true through an aggregator.
        """
        display = str(entry.get("company") or "").strip()
        slug = _entry_slug(entry)
        if not display or not slug:
            stats.postings_skipped_malformed += 1
            return

        with transaction(self.conn):
            existed = self.companies.get_by_slug(slug)
            company_id = self.companies.upsert(
                CompanyRecord(
                    slug=slug,
                    name=display,
                    website=_entry_url(entry),
                    discovery_source=PROVIDER,
                )
            )
            if existed is None:
                stats.companies_new += 1

            board_identifier = display
            board_existed = self.boards.get_by_identifier(PROVIDER, board_identifier)
            board_id = self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=PROVIDER,
                    # The DISPLAY key, not the slug: that is what the `company`
                    # filter accepts. Measured, `company=Abridge` returns 40 and
                    # `company=abridge` returns 0.
                    board_identifier=board_identifier,
                    board_url=_entry_url(entry),
                    discovery_method="speedrun_feed",
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
            # Same neutral mechanism the board collector uses: ask the adapter
            # which paths carry meaning, resolve them without naming one.
            resolve_metadata(PROVIDER, self.provider.field_map, posting.payload)

    # -- corpus lookups ----------------------------------------------------

    def _held_external_ids(self) -> dict[str, str]:
        """Every `external_id` held by ANOTHER provider, mapped to its job id.

        Across all providers except this one, and the exception is the whole
        correction. The point of rule 1 is that a Speedrun id equal to an Ashby
        id means the two are the same posting -- a statement about OTHER
        sources. Applied to Speedrun's own rows it says something different and
        false: that a posting we collected last week needs no further attention.

        Measured by an independent functional review on a two-pass run:

            pass 1  jobs_new 1, content_hash b28366ff..., last_seen_at 05:59:56Z
            (the employer rewrites the description; the feed serves the new text)
            pass 2  jobs_new 0, jobs_seen_again 0, duplicates {EXTERNAL_ID: 1}
                    content_hash b28366ff...  UNCHANGED
                    last_seen_at              UNCHANGED
                    job_discovery_source.source == job.provider

        Three consequences in one line of SQL. The posting's `last_seen_at`
        froze, so it never aged and could never be marked gone. The rewritten
        description never entered the corpus. And it wrote the one row
        migration 0018 explicitly forbids -- "Never the job's own provider: a
        row saying 'we found it where we found it' carries nothing".

        As a side effect `jobs_seen_again` and `jobs_changed` were unreachable
        on any second pass, which is why no counter looked wrong.

        One dictionary rather than one query per posting. At 18,550 rows this is
        a few megabytes and one scan; the alternative is 50 round trips per page.
        """
        rows = self.conn.execute(
            "SELECT id, external_id FROM job WHERE provider != ?", (PROVIDER,)
        ).fetchall()
        return {str(row["external_id"]): str(row["id"]) for row in rows}

    def _job_by_canonical_url(self, url: str) -> str | None:
        """A stored job whose URL is the same string as this one.

        Compared in Python rather than in SQL because `canonical_url` lowercases
        the host and drops the query, and expressing that in portable SQL would
        mean either a stored normalised column or three vendor-specific string
        functions. The candidate set is narrowed by a LIKE on the host first, so
        this is not a scan of the corpus per posting.
        """
        target = canonical_posting_url(url)
        host = _host(url)
        if not host:
            return None
        rows = self.conn.execute(
            "SELECT id, url FROM job WHERE url LIKE ?", (f"%{host}%",)
        ).fetchall()
        for row in rows:
            if canonical_posting_url(str(row["url"])) == target:
                return str(row["id"])
        return None

    def _job_by_content_hash(self, text_hash: str, *, slug: str, title: str) -> str | None:
        """A job with this description, from this company, for this role.

        The hash alone is not an identity in this corpus and the numbers are
        in the caller. Company is compared on the SLUG, which is how the rest
        of this module identifies a company; title on the exact string, since
        an employer reposting a role reposts its name too.

        Still `LIMIT 1`, and now that is defensible: the rows it can return
        agree on all three fields, so which one comes back does not change
        what the sighting means.
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


def _entry_url(entry: dict[str, Any]) -> str | None:
    """The company URL, or None. Never the four characters `None`.

    `str(entry.get("company_url")) or None` reads as a null-safe idiom and is
    not one: `str(None)` is the string `"None"`, which is truthy, so `or None`
    never fires. An independent functional review found the string in the
    database, in `company.website` and `source_board.board_url`, for every
    feed entry that omitted the field -- and `company.website` is rendered.

    `speedrun._to_stub` already had this right. The correct shape is to test
    the value BEFORE converting it, which is what this does.
    """
    value = entry.get("company_url")
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _entry_slug(entry: dict[str, Any]) -> str:
    """The company slug for a feed entry, by the same rule `_persist_new` uses.

    Extracted because rule 4 now needs it BEFORE the persist path is reached,
    and two spellings of "which company is this" would be one spelling too
    many for a rule whose whole job is deciding whether two rows are the same.
    """
    display = str(entry.get("company") or "").strip()
    return str(entry.get("company_slug") or "").strip() or _slugify(display)


def _feed_board(entry: dict[str, Any]) -> BoardRef:
    """A `BoardRef` for one feed entry, used only to satisfy `fetch_posting`.

    The adapter's detail request is addressed by posting id and does not read
    the board, so this carries identity for readability rather than for routing.
    """
    display = str(entry.get("company") or "").strip()
    slug = _entry_slug(entry)
    return BoardRef(
        company_slug=slug,
        provider=PROVIDER,
        board_identifier=display,
        board_url=_entry_url(entry),
    )


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return urlsplit(url).netloc.lower()
    except ValueError:
        return ""


def _slugify(name: str) -> str:
    """A fallback slug for a company the feed named but did not slug.

    Stealth listings arrive as the literal company "Stealth" with a null slug.
    They still describe real work, so they are collected rather than dropped,
    under a slug derived from the name they gave.
    """
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unnamed"

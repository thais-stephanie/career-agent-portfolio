"""Collecting LinkedIn through python-jobspy: bounded, paced, honest about refusals.

EXPERIMENTAL AND OPT-IN. `web/source_refresh.py` runs this only for a profile
whose owner switched the experimental override on, and `collect-linkedin`
refuses otherwise (switching it on at a terminal is
`career-agent experimental-source linkedin_br --on --i-understand`). The choice
is checked again before every search and page read, so switching it off stops
a run in progress. See `providers/linkedin_jobspy.py` for what LinkedIn says
and why this is not an ordinary source.

THE SHAPE OF A RUN
------------------
1. SEARCH. The targeted plan (role anchors, their aliases, work phrases) times
   the market scopes, capped. One small query each, never one giant one;
   cards only, no posting pages. Paced; a refusal (429, 999, 403) is retried
   once after a backoff and then ends the searching; three failed searches in
   a row end it too.
2. DEDUPLICATE. Across queries by LinkedIn id, then against the corpus:
   * an id already held is seen again (and its query recorded);
   * an id already recorded as a SIGHTING of another source's job stays one,
     and its page is never read again;
   * an employer apply link that a board adapter already holds by canonical
     URL is a SIGHTING of that job, never a second row.
3. ENRICH. Only postings the corpus does not hold yet, most-queries-first,
   one page at a time, slowly, capped; postings held from an earlier run
   without their text come after the new ones. Three unreadable pages in a
   row, or one refusal, end the enrichment: a posting without a description
   is still stored (as FETCHED, never scored as if it had text) and its page
   is read on a later run.

Every query that returned a posting is recorded in `job_retrieval_lane`
(targeted). Nothing here reads or changes eligibility or Search Fit.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from career_agent.clock import now_utc
from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.linkedin_jobspy import (
    EMPTY,
    OK,
    PROVIDER,
    RATE_LIMITED,
    LinkedInJobSpyProvider,
    company_of,
    direct_url,
    external_id,
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
    sha256_text,
)

STAGE = "collect-linkedin"
MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_ORIGIN_URL = "ORIGIN_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"

#: Defaults, all bounded, all overridable from the command line.
DEFAULT_MAX_QUERIES = 24
DEFAULT_RESULTS_PER_QUERY = 25
DEFAULT_HOURS_OLD = 24 * 14
DEFAULT_MAX_ENRICH = 60
#: Seconds between searches and between posting pages, randomised in range.
SEARCH_PAUSE = (3.0, 6.0)
ENRICH_PAUSE = (2.0, 4.0)
#: One retry of a refused search, after this long. Never more.
BACKOFF_SECONDS = 30.0
MAX_CONSECUTIVE_ENRICH_FAILURES = 3
MAX_CONSECUTIVE_SEARCH_FAILURES = 3


@dataclass
class LinkedInStats:
    queries_planned: int = 0
    #: Planned searches asked (a retry is not a second attempt).
    queries_attempted: int = 0
    queries_succeeded: int = 0
    queries_empty: int = 0
    queries_failed: int = 0
    #: Searches whose final answer was a refusal.
    queries_rate_limited: int = 0
    #: Searches refused once and answered on the single retry.
    refusals_recovered: int = 0
    retries: int = 0
    #: HTTP requests made to LinkedIn, searches and pages together, as counted
    #: by the adapter's session (0 from a test fake).
    requests: int = 0
    raw_results: int = 0
    unique_results: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    #: Held earlier without a description, and read this time.
    jobs_described_later: int = 0
    #: Already recorded as a sighting of another source's job.
    known_sightings: int = 0
    #: Cards without a title or an employer: nothing to store.
    dropped_incomplete: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    enrich_attempted: int = 0
    enrich_succeeded: int = 0
    enrich_skipped_budget: int = 0
    without_description: int = 0
    companies_new: int = 0
    unique_by_scope: dict[str, int] = field(default_factory=dict)
    unique_by_origin: dict[str, int] = field(default_factory=dict)
    #: Why the run left part of its own plan undone, if it did.
    stopped_reason: str | None = None
    failures: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    @property
    def duplicates_total(self) -> int:
        return sum(self.duplicates.values())

    def as_dict(self) -> dict[str, Any]:
        return {
            **{
                k: v
                for k, v in self.__dict__.items()
                if k not in {"duplicates", "failures", "unique_by_scope", "unique_by_origin"}
            },
            "duplicates": dict(self.duplicates),
            "duplicates_total": self.duplicates_total,
            "unique_by_scope": dict(self.unique_by_scope),
            "unique_by_origin": dict(self.unique_by_origin),
            "failures": list(self.failures),
            # Read by `sources/progress.partial_reason`: a query-driven source
            # never covers a market, and a stopped plan is a partial run.
            "stopped_early": self.stopped_reason is not None,
            "postings_seen": self.raw_results,
            "coverage_note": "answers to the questions asked, never LinkedIn's market",
        }


@dataclass
class _Found:
    record: dict[str, Any]
    queries: list[Any] = field(default_factory=list)
    #: Set when the corpus already holds this posting WITHOUT its text: a
    #: page read later completes the row rather than adding one.
    held_id: str | None = None


def expressible(scope: Any) -> bool:
    """Whether LinkedIn's guest search can ask this scope at all.

    MEASURED 2026-09-25: the guest search ignores the remote-work filter.
    "Brazil" with and without it returned the same 25 of 25 postings, and in
    a 24-search run the country and "Remote" (home market) scopes found the
    same 96. So the home-market "Remote" scope is a duplicate query here.
    "Remote Worldwide" is asked as the place "Worldwide", which IS a different
    question (72 of its 75 postings came from no other scope).
    """
    return not (getattr(scope, "key", "") == "remote" and getattr(scope, "home", None))


class LinkedInCollector:
    def __init__(
        self,
        conn: Any,
        provider: LinkedInJobSpyProvider | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
        pause: Callable[[tuple[float, float]], float] | None = None,
    ) -> None:
        self.conn = conn
        self.provider = provider or LinkedInJobSpyProvider()
        self._sleep = sleep
        self._pause = pause or (lambda span: random.uniform(*span))
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    def collect(
        self,
        queries: tuple[Any, ...],
        *,
        results_per_query: int = DEFAULT_RESULTS_PER_QUERY,
        hours_old: int = DEFAULT_HOURS_OLD,
        max_enrich: int = DEFAULT_MAX_ENRICH,
        should_stop: Callable[[], bool] | None = None,
    ) -> LinkedInStats:
        started = time.monotonic()
        stats = LinkedInStats(queries_planned=len(queries))
        with transaction(self.conn):
            run_id = self.runs.start(STAGE)
        try:
            found = self._search(queries, results_per_query, hours_old, stats, should_stop)
            self._store(found, max_enrich, stats, should_stop)
        except Exception as exc:
            stats.failures.append(type(exc).__name__)
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            with transaction(self.conn):
                self.runs.finish(
                    run_id,
                    PipelineRunStatus.FAILED,
                    stats=stats.as_dict(),
                    error=type(exc).__name__,
                )
            raise
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        # Every query refused is a failed run, not an empty market.
        status = (
            PipelineRunStatus.FAILED
            if stats.queries_attempted and not (stats.queries_succeeded + stats.queries_empty)
            else PipelineRunStatus.OK
        )
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                status,
                stats=stats.as_dict(),
                error=stats.stopped_reason if status is PipelineRunStatus.FAILED else None,
            )
        return stats

    # -- 1. search ---------------------------------------------------------

    def _search(self, queries, results, hours_old, stats, should_stop) -> dict[str, _Found]:
        found: dict[str, _Found] = {}
        failures_in_row = 0
        for index, query in enumerate(queries):
            if should_stop is not None and should_stop():
                stats.stopped_reason = "cancelled"
                break
            if index:
                self._sleep(self._pause(SEARCH_PAUSE))
            stats.queries_attempted += 1
            outcome = self._ask(query, results, hours_old, stats)
            if outcome.status == RATE_LIMITED:
                # One retry after a backoff, then stop asking: LinkedIn said no.
                self._sleep(BACKOFF_SECONDS)
                if should_stop is not None and should_stop():
                    stats.stopped_reason = "cancelled"
                    break
                stats.retries += 1
                outcome = self._ask(query, results, hours_old, stats)
                if outcome.status == RATE_LIMITED:
                    stats.queries_rate_limited += 1
                    stats.stopped_reason = "rate_limited"
                    stats.failures.append(f"rate limited: {query.key}")
                    break
                stats.refusals_recovered += 1
            if outcome.status == OK:
                stats.queries_succeeded += 1
                failures_in_row = 0
            elif outcome.status == EMPTY:
                stats.queries_empty += 1
                failures_in_row = 0
            else:
                stats.queries_failed += 1
                stats.failures.append(f"{outcome.status.lower()}: {query.key}")
                failures_in_row += 1
                if failures_in_row >= MAX_CONSECUTIVE_SEARCH_FAILURES:
                    stats.stopped_reason = "repeated_failures"
                    break
                continue
            stats.raw_results += len(outcome.records)
            for record in outcome.records:
                ident = external_id(record)
                if not ident:
                    continue
                if ident not in found:
                    found[ident] = _Found(record)
                    stats.unique_by_scope[query.scope.key] = (
                        stats.unique_by_scope.get(query.scope.key, 0) + 1
                    )
                    stats.unique_by_origin[query.term.origin] = (
                        stats.unique_by_origin.get(query.term.origin, 0) + 1
                    )
                found[ident].queries.append(query)
        stats.unique_results = len(found)
        return found

    def _ask(self, query, results, hours_old, stats):
        outcome = self.provider.search(
            query.term.text,
            location=query.scope.location_text,
            remote=bool(getattr(query.scope, "remote", False)),
            results_wanted=results,
            hours_old=hours_old,
        )
        stats.requests += outcome.requests
        return outcome

    # -- 2 and 3. deduplicate, enrich, store ----------------------------------

    def _store(self, found: dict[str, _Found], max_enrich: int, stats, should_stop) -> None:
        pending: list[_Found] = []
        for ident, item in found.items():
            held = self.jobs.get_by_external(PROVIDER, ident)
            if held is not None:
                self._seen_again(held, item, stats)
                if not held["content_hash"]:
                    item.held_id = str(held["id"])
                    pending.append(item)
                continue
            sighted = self._sighted(ident)
            if sighted is not None:
                # Resolved on an earlier run: never read its page again.
                with transaction(self.conn):
                    self._lanes(sighted, item)
                stats.known_sightings += 1
                continue
            origin = direct_url(item.record)
            match = self._job_by_canonical_url(origin) if origin else None
            if match is not None:
                self._sighting(match, item, MATCH_ORIGIN_URL, stats)
                continue
            pending.append(item)
        # New postings before completing held ones, and most-asked first: a
        # posting several questions returned is the one most worth a page read.
        pending.sort(key=lambda i: (i.held_id is not None, -len(i.queries)))
        failures_in_row = 0
        enriching = True
        for item in pending:
            if enriching and should_stop is not None and should_stop():
                stats.stopped_reason = stats.stopped_reason or "cancelled"
                enriching = False
            record = dict(item.record)
            if enriching and stats.enrich_attempted < max_enrich:
                if stats.enrich_attempted:
                    self._sleep(self._pause(ENRICH_PAUSE))
                stats.enrich_attempted += 1
                stats.requests += 1
                status, details = self.provider.enrich(record)
                if status == OK:
                    stats.enrich_succeeded += 1
                    failures_in_row = 0
                    record["description"] = details.get("description")
                    if details.get("job_url_direct"):
                        record["job_url_direct"] = details["job_url_direct"]
                elif status == RATE_LIMITED:
                    enriching = False
                    stats.stopped_reason = stats.stopped_reason or "rate_limited"
                    stats.failures.append("posting pages rate limited")
                else:
                    failures_in_row += 1
                    if failures_in_row >= MAX_CONSECUTIVE_ENRICH_FAILURES:
                        enriching = False
                        stats.stopped_reason = stats.stopped_reason or "pages_unreadable"
                        stats.failures.append("posting pages unreadable")
            elif enriching:
                stats.enrich_skipped_budget += 1
            if item.held_id is not None:
                # Already stored and already counted as seen again: only a
                # description read this time changes anything.
                if record.get("description"):
                    self._persist(record, item, stats)
                continue
            self._persist(record, item, stats)

    def _persist(self, record: dict[str, Any], item: _Found, stats: LinkedInStats) -> None:
        stub = to_stub(record)
        company = company_of(record)
        if stub is None or not company:
            stats.dropped_incomplete += 1
            return
        posting = self.provider.fetch_posting(None, stub)  # type: ignore[arg-type]
        text = posting.description_text or ""
        slug = _slugify(company)
        if item.held_id is None:
            # A NEW posting may be one a board adapter already holds: by the
            # employer's own link, or by identical text from the same
            # employer for the same title. A held row is completed as it is:
            # the corpus already chose to keep it.
            origin = direct_url(record)
            match = self._job_by_canonical_url(origin) if origin else None
            if match is None and text:
                match = self._job_by_content_hash(sha256_text(text), slug=slug, title=stub.title)
                rule = MATCH_CONTENT_HASH
            else:
                rule = MATCH_ORIGIN_URL
            if match is not None:
                self._sighting(match, item, rule, stats)
                return
        text_hash = self.raw.put(text, posting.description_html) if text else None
        if text_hash is None:
            stats.without_description += 1
        with transaction(self.conn):
            if self.companies.get_by_slug(slug) is None:
                stats.companies_new += 1
            company_id = self.companies.upsert(
                CompanyRecord(slug=slug, name=company, discovery_source=PROVIDER)
            )
            board_id = self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=PROVIDER,
                    board_identifier=slug,
                    board_url=None,
                    discovery_method="linkedin_guest_search",
                )
            )
            job_id = self.jobs.upsert_seen(
                JobRecord(
                    company_id=company_id,
                    source_board_id=board_id,
                    provider=PROVIDER,
                    external_id=stub.external_id,
                    url=stub.url,
                    title=stub.title,
                    department=None,
                    location_raw=stub.location_raw,
                    posted_at=stub.posted_at,
                    content_hash=text_hash,
                ),
                status=CollectionStatus.NORMALISED if text_hash else CollectionStatus.FETCHED,
            )
            self.payloads.put(
                ProviderPayloadRecord(job_id=job_id, provider=PROVIDER, payload=dict(stub.payload))
            )
            if item.held_id is not None and text_hash:
                # `upsert_seen` keeps an open row's status on purpose; a row
                # that only now has its text moves on explicitly.
                self.conn.execute(
                    "UPDATE job SET collection_status = ? WHERE id = ? AND collection_status = ?",
                    (
                        CollectionStatus.NORMALISED.value,
                        str(job_id),
                        CollectionStatus.FETCHED.value,
                    ),
                )
            self._lanes(str(job_id), item)
        if item.held_id is not None:
            stats.jobs_described_later += 1
        else:
            stats.jobs_new += 1

    def _seen_again(self, held: Any, item: _Found, stats: LinkedInStats) -> None:
        """Through `upsert_seen`, like every collector: a closed row seen again
        reopens, and the card's title and place are kept current."""
        stub = to_stub(item.record)
        with transaction(self.conn):
            if stub is not None:
                self.jobs.upsert_seen(
                    JobRecord(
                        company_id=str(held["company_id"]),
                        source_board_id=held["source_board_id"],
                        provider=PROVIDER,
                        external_id=stub.external_id,
                        url=stub.url,
                        title=stub.title,
                        department=None,
                        location_raw=stub.location_raw,
                        posted_at=stub.posted_at or held["posted_at"],
                        content_hash=held["content_hash"],
                    ),
                    status=CollectionStatus(str(held["collection_status"]))
                    if held["collection_status"] != "CLOSED"
                    else (
                        CollectionStatus.NORMALISED
                        if held["content_hash"]
                        else CollectionStatus.FETCHED
                    ),
                )
            self._lanes(str(held["id"]), item)
        stats.jobs_seen_again += 1

    def _sighted(self, ident: str) -> str | None:
        row = self.conn.execute(
            "SELECT job_id FROM job_discovery_source WHERE source = ? AND external_id = ? LIMIT 1",
            (PROVIDER, ident),
        ).fetchone()
        return str(row[0]) if row else None

    def _sighting(self, job_id: str, item: _Found, rule: str, stats: LinkedInStats) -> None:
        stub = to_stub(item.record)
        with transaction(self.conn):
            if stub is not None:
                self.discovery.record(
                    job_id=job_id,
                    source=PROVIDER,
                    external_id=stub.external_id,
                    matched_by=rule,
                    url=stub.url,
                    payload=dict(stub.payload),
                )
            self._lanes(job_id, item)
        stats.duplicates[rule] = stats.duplicates.get(rule, 0) + 1

    def _lanes(self, job_id: str, item: _Found) -> None:
        now = now_utc()
        for query in item.queries:
            self.conn.execute(
                "INSERT INTO job_retrieval_lane (job_id, lane, source, query_key, term_origin,"
                " first_seen_at, last_seen_at) VALUES (?, 'targeted', ?, ?, ?, ?, ?)"
                " ON CONFLICT (job_id, lane, source, query_key)"
                " DO UPDATE SET last_seen_at = excluded.last_seen_at",
                (job_id, PROVIDER, query.key, query.term.origin, now, now),
            )

    # -- corpus lookups ----------------------------------------------------

    def _job_by_canonical_url(self, url: str) -> str | None:
        target = canonical_posting_url(url)
        if not target:
            return None
        from urllib.parse import urlsplit

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
        row = self.conn.execute(
            "SELECT j.id AS id FROM job j JOIN company c ON c.id = j.company_id"
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

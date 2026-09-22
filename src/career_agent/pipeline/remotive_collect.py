"""Collecting Remotive, one read of the public window, at most four times a day.

The same shape as the Remotive collector, because the grant has the same shape:
an affirmative first-party statement with conditions, one of which is a
cadence. "You only need to GET Remotive job data ... a couple of times a day
(we advise max. 4 times a day)", so `MIN_SECONDS_BETWEEN_POLLS` is six hours,
provider-wide, measured from the last ATTEMPT out of `pipeline_run`. A refusal
is not a failure and writes no run row: recording a refusal as an attempt would
push the next permitted poll further out every time somebody typed the command.

**THE PUBLIC WINDOW IS THE PUBLIC INVENTORY, AND IT IS SMALL.** Measured
2026-09-11: `job-count` 18 and `total-job-count` 18 in the same envelope. The
vendor's fuller inventory is a paid product this collector does not reach for.
Eighteen rows with a documented hiring-scope field and whole adverts are worth
one request four times a day; they are never presented as a census.

**THREE RULES, NOT FOUR.** `ORIGIN_URL` is unreachable: the feed publishes no
employer apply link, and the licence requires the link to be Remotive's own
URL anyway. Rules 1, 3 and 4 remain, and rule 4 -- byte-identical body, same
employer, same title -- works because the window carries full adverts.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.providers.base import canonical_url as canonical_posting_url
from career_agent.providers.base import resolve_metadata
from career_agent.providers.registry import get_provider
from career_agent.providers.remotive import (
    MIN_SECONDS_BETWEEN_POLLS,
    RemotiveCoolingDown,
    RemotiveProvider,
    company_of,
    next_allowed_at,
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

PROVIDER = "remotive"

#: The run name, and the key the cadence gate looks itself up by.
RUN_NAME = "collect-remotive"

MATCH_EXTERNAL_ID = "EXTERNAL_ID"
MATCH_CANONICAL_URL = "CANONICAL_URL"
MATCH_CONTENT_HASH = "CONTENT_HASH"


@dataclass
class RemotiveStats:
    """What one pass did, with the bound it ran under kept visible."""

    #: What the envelope said it was returning. Checked against the rows
    #: actually present rather than trusted, and never presented as coverage.
    claimed_count: int | None = None
    #: `total-job-count`. On 2026-09-11 it equalled `job-count`: the public
    #: window is the whole public inventory, and that is the honest bound.
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
    #: nothing. Every measured row stated one; the second bucket exists so an
    #: empty field is counted rather than assumed away.
    with_hiring_scope: int = 0
    without_hiring_scope: int = 0
    companies_new: int = 0
    boards_new: int = 0
    duplicates: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)
    #: Set when the run was refused by the cadence gate. Not a failure.
    cooling_down_until: float | None = None
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
            "claimed_count": self.claimed_count,
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
            "cooling_down_until": self.cooling_down_until,
            "elapsed_ms": self.elapsed_ms,
            "http": dict(self.http),
            "coverage_note": (
                "the public window, which the vendor reports as its whole public inventory; "
                "never a census of remote hiring"
            ),
            "attribution": "Remotive, credited with a direct link to every source posting",
        }


class RemotiveCollector:
    """One bounded window, against one database connection."""

    def __init__(self, conn: Any, fetcher: HttpFetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider: RemotiveProvider = get_provider(PROVIDER, fetcher)  # type: ignore[assignment]
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the cadence gate --------------------------------------------------

    def last_attempt_at(self) -> float | None:
        """When this collector last opened a request, from `pipeline_run`.

        Every run is started BEFORE the request and finished after it, whatever
        the outcome, so a crashed run still leaves the attempt recorded. That
        is the property this gate needs: a process that died mid-request must
        not hand the next one a free poll.
        """
        row = self.conn.execute(
            "SELECT started_at FROM pipeline_run WHERE stage = ? ORDER BY started_at DESC LIMIT 1",
            (RUN_NAME,),
        ).fetchone()
        if row is None or row["started_at"] is None:
            return None
        return _epoch_seconds(str(row["started_at"]))

    def cooling_down(self, now: float | None = None) -> float | None:
        """The epoch second another poll becomes permitted, or None if it is.

        Returns the DEADLINE rather than a bare boolean so the caller can say
        how long is left. "Not yet" with no number is the kind of refusal
        people work around.
        """
        moment = time.time() if now is None else now
        allowed = next_allowed_at(self.last_attempt_at())
        return allowed if moment < allowed else None

    # -- the pass ----------------------------------------------------------

    def collect(
        self,
        on_progress: Any = None,
        should_stop: Any = None,
        now: float | None = None,
        force: bool = False,
    ) -> RemotiveStats:
        """Read the window once, if the hour allows, and persist what is new.

        `force` exists for tests and for an owner who has read the licence and
        has a reason. It is deliberately a parameter rather than a config key
        or an environment variable: an ethical boundary should cost a
        reviewable argument at the call site, the same reasoning that keeps
        Torre's gate out of the configuration.
        """
        started = time.monotonic()
        stats = RemotiveStats()

        if not force:
            deadline = self.cooling_down(now)
            if deadline is not None:
                # **NOT A FAILURE, AND NOT A RUN.** No `pipeline_run` row is
                # written: recording a refusal as an attempt would push the
                # next permitted poll an hour further out every time somebody
                # typed the command, which is a gate that tightens itself.
                stats.cooling_down_until = deadline
                stats.elapsed_ms = int((time.monotonic() - started) * 1000)
                return stats

        with transaction(self.conn):
            run_id = self.runs.start(RUN_NAME)

        held_external_ids = self._held_external_ids()

        try:
            read = self.provider.read_window()
        except BudgetExpired:
            stats.deferred_reason = "TIME_BUDGET"
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict())
            return stats
        except (FetchError, ValueError) as exc:
            # A failed request is a failure, never an empty window. Conflating
            # them is how a network blip reads as "this region has no jobs".
            stats.failures.append(str(exc))
            stats.elapsed_ms = int((time.monotonic() - started) * 1000)
            stats.http = self.fetcher.stats.as_dict()
            with transaction(self.conn):
                self.runs.finish(
                    run_id, PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
                )
            return stats

        stats.claimed_count = read.claimed_count
        stats.claimed_total = read.claimed_total
        stats.postings_unaddressable = read.unaddressable

        for index, job in enumerate(read.jobs, start=1):
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
                on_progress(index, len(read.jobs))

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        with transaction(self.conn):
            self.runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats

    # -- one posting -------------------------------------------------------

    def _handle(
        self, job: dict[str, Any], held_external_ids: dict[str, str], stats: RemotiveStats
    ) -> None:
        stub = to_stub(job)
        if stub is None:
            return

        # RULE 1, checked first because it is free.
        held = held_external_ids.get(stub.external_id)
        if held is not None:
            self._record_sighting(held, stub, job, MATCH_EXTERNAL_ID, stats)
            return

        # RULE 2 (ORIGIN_URL) is unreachable: this feed publishes no employer
        # apply link, and the licence requires the application to go to
        # Remotive's own URL in any case.

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
            stats.with_hiring_scope += 1
        else:
            stats.without_hiring_scope += 1

        company = company_of(job)
        if not company:
            # The window carried no employer. Inventing one from the title or
            # the slug is the fuzzy identity ADR-0008 forbids.
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
        self, job_id: str, stub: Any, job: dict[str, Any], rule: str, stats: RemotiveStats
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
        stats: RemotiveStats,
    ) -> None:
        """A posting nothing else in the corpus holds.

        One board per company, created on demand, which is what keeps ADR-0008
        true through a source whose real shape is a single window. Registering
        the whole window as one board carrying seventy-five employers is the
        attribution failure that document exists to prevent.
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
                    discovery_method="remotive_window",
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
                    # The employer's HIRING SCOPE, not an office -- and None
                    # where the vendor said `Anywhere`, which is its default
                    # rather than an invitation.
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
        would file a posting collected last week as a sighting of itself.

        A caveat this source makes sharper than the others: a Remotive id is a
        bare integer, and another provider's bare integer is not the same
        identity. Rule 1 matching across providers on a naked number is a real
        collision risk, and it is bounded here only because the corpus's other
        numeric-id providers use different ranges. `job.provider` is the other
        half of every identity and ADR-0013 is where the rule lives.
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


def describe_cooldown(deadline: float, now: float | None = None) -> str:
    """ "Not yet, and here is how long" -- because a refusal with no number is
    the kind people work around."""
    moment = time.time() if now is None else now
    minutes = max(0, int((deadline - moment) // 60))
    return (
        f"Remotive advises at most four requests a day and the last one was under six hours ago. "
        f"About {minutes} minute(s) left. Nothing was fetched and nothing failed."
    )


__all__ = [
    "MIN_SECONDS_BETWEEN_POLLS",
    "PROVIDER",
    "RUN_NAME",
    "RemotiveCollector",
    "RemotiveCoolingDown",
    "RemotiveStats",
    "describe_cooldown",
]


def _epoch_seconds(stamp: str) -> float | None:
    """A stored timestamp as epoch seconds, or None when it cannot be read.

    **None means "no usable record of an attempt", and the caller treats that
    as permission to poll.** That is the right way round only because the
    alternative -- refusing forever on an unparseable timestamp -- turns one
    bad row into a permanently disabled source with no way to see why. A
    malformed `started_at` is a storage bug, and this gate is not the place to
    discover it.
    """
    from datetime import UTC, datetime

    text = stamp.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.timestamp()


def _host(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def _slugify(name: str) -> str:
    import re
    import unicodedata

    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-") or "unknown-company"

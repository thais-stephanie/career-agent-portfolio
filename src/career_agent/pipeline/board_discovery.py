"""Finding ATS boards through an aggregator that publishes the employer's own
apply link, and registering only the ones the employer's ATS then answers for.

WHY AN AGGREGATOR CAN DO WHAT 280 CAREERS PAGES COULD NOT
---------------------------------------------------------
Greenhouse is at 100% of what it offers and the limit is the 122 companies in
the registry (`docs/product/greenhouse-completeness-2026-09-10.md`).
`scan-career-sites` read 280 employers' own websites and resolved two Workday
identities. RemoteSource links the original ATS on every page sampled -- 150
of 150, with 41 distinct Workday identities among them -- so one page per
employer names the board directly, all three parts of a Workday identity
included. The index is not a source of postings here (`providers/
remotesource.py` says why); it is a list of boards somebody published.

THE PATH, AND WHERE A GUESS WOULD HAVE BEEN
--------------------------------------------
    index page  ->  employer's apply URL  ->  family recognises its own board
                ->  the family's ATS answers for that board  ->  registered

* The family names the board. `registry.identify_board_url` asks every
  family "is this a posting on one of your hosted boards?", and a family
  answers only for the URL shapes it builds itself. A `gh_jid` on an
  employer's own domain proves Greenhouse and NOT the board, so it is
  UNSUPPORTED here rather than completed by a guess. A Workday URL without
  a site names two thirds of an identity and is refused the same way.
* The ATS validates it. A board is registered only after the family's own
  adapter listed it: `NOT_FOUND` and `MALFORMED` fail, a timeout defers.
  For a family whose host answers for slugs nobody registered (Recruitee),
  answering is not enough -- the listing must carry the very posting the
  aggregator linked, or the board is refused.
* Nothing about the candidate enters. The walk is ordered by the index's own
  employer keys and skips the ones already answered. Which boards exist is a
  fact about the market (ADR-0019); what gets refreshed first is a scheduling
  question that lives elsewhere.

WHAT IS WRITTEN
---------------
One `board_discovery_lead` row per employer, whatever the outcome, so a
bounded run resumes where the last one stopped. A `company` row when the
employer is new (the index's own name and website; a slug that already exists
is reused and never rewritten), and a `source_board` row with
`discovery_method = aggregator_origin_pointer`. No posting: the registered
board is collected by the ordinary `collect` under the family's own adapter
and permission, so every posting that arrives keeps the employer's own words
and an origin URL on the employer's own board (ADR-0013).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from itertools import islice
from typing import Any
from urllib.parse import urlsplit

from career_agent.clock import new_id, now_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.net.robots import RobotsDecision, RobotsPolicy
from career_agent.pipeline.discover import BoardOutcome, candidate_identifiers, normalise_domain
from career_agent.providers import remotesource
from career_agent.providers.base import BoardRef, canonical_url
from career_agent.providers.registry import (
    answering_for_unknown_providers,
    board_providers,
    board_recogniser_families,
    get_provider,
    guessable_providers,
    identify_board_url,
    identify_posting_url,
)
from career_agent.storage.db import transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    PipelineRunRepo,
    PipelineRunStatus,
    SourceBoardRepo,
)

INDEX_SOURCE = "remotesource"
DISCOVERY_METHOD = "aggregator_origin_pointer"
#: A board whose identifier was DERIVED from the employer's domain and name and
#: then CONFIRMED by its listing carrying the exact posting the aggregator
#: linked. Weaker evidence than a published identity, and recorded as such.
DISCOVERY_METHOD_CONFIRMED_PROBE = "aggregator_origin_confirmed_probe"
RUN_NAME = "discover-remotesource"


class LeadOutcome(StrEnum):
    """What one employer's page led to. Each is a different fact."""

    #: The family's ATS answered for the board and it is in `source_board` now.
    REGISTERED = "REGISTERED"
    #: The board was already registered, by this walk or by anybody.
    ALREADY_REGISTERED = "ALREADY_REGISTERED"
    #: The family named a board and its ATS said there is no such board, or
    #: answered for it without listing the posting where that is required.
    VALIDATION_FAILED = "VALIDATION_FAILED"
    #: The ATS could not be reached; nothing is known and the lead is retried.
    VALIDATION_DEFERRED = "VALIDATION_DEFERRED"
    #: The origin is on a provider this product collects as a whole feed, not
    #: board by board. Nothing to register; the feed already carries it.
    FEED_COVERED = "FEED_COVERED"
    #: The origin names no family that recognises its own boards, or names a
    #: family without naming the board (an embedded Greenhouse link).
    UNSUPPORTED_FAMILY = "UNSUPPORTED_FAMILY"
    #: The page was read and published no ATS apply link.
    NO_ORIGIN = "NO_ORIGIN"
    #: The page could not be read.
    UNREADABLE = "UNREADABLE"
    #: The index's own robots.txt refuses this product the page.
    REFUSED_BY_HOST = "REFUSED_BY_HOST"


#: Outcomes a later walk may try again, with the next page of the same
#: employer. The others are answers.
RETRIABLE = frozenset(
    {
        LeadOutcome.VALIDATION_DEFERRED,
        LeadOutcome.NO_ORIGIN,
        LeadOutcome.UNREADABLE,
        # A family nobody recognised today may be recognised after the next
        # adapter lands, and an embedded board's next page may name it.
        LeadOutcome.UNSUPPORTED_FAMILY,
    }
)

#: How many listed postings a validation reads before it stops. A Workday
#: board can hold thousands, twenty a page, and validation is a question
#: about the BOARD, not a collection: two hundred is ten pages.
VALIDATION_LIST_CAP = 200


@dataclass(frozen=True, slots=True)
class Lead:
    employer_key: str
    sample_url: str
    origin_url: str | None = None
    provider: str | None = None
    board_identifier: str | None = None
    external_id: str | None = None
    outcome: LeadOutcome = LeadOutcome.NO_ORIGIN
    detail: str | None = None
    employer_name: str | None = None
    employer_website: str | None = None
    source_board_id: str | None = None


@dataclass
class DiscoveryStats:
    """Measured by family, which is the question the walk exists to answer."""

    employers_in_index: int = 0
    employers_already_walked: int = 0
    employers_walked: int = 0
    requests_made: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)
    by_family: dict[str, dict[str, int]] = field(default_factory=dict)
    boards_registered: list[str] = field(default_factory=list)

    def record(self, lead: Lead) -> None:
        self.outcomes[lead.outcome.value] = self.outcomes.get(lead.outcome.value, 0) + 1
        family = lead.provider or ("none" if lead.origin_url is None else "unrecognised")
        bucket = self.by_family.setdefault(family, {})
        bucket[lead.outcome.value] = bucket.get(lead.outcome.value, 0) + 1
        if lead.outcome is LeadOutcome.REGISTERED and lead.provider and lead.board_identifier:
            self.boards_registered.append(f"{lead.provider}:{lead.board_identifier}")

    def as_dict(self) -> dict[str, Any]:
        return {
            "employers_in_index": self.employers_in_index,
            "employers_already_walked": self.employers_already_walked,
            "employers_walked": self.employers_walked,
            "requests_made": self.requests_made,
            "outcomes": dict(sorted(self.outcomes.items())),
            "by_family": {k: dict(sorted(v.items())) for k, v in sorted(self.by_family.items())},
            "boards_registered": list(self.boards_registered),
        }


# -- classifying one origin URL ------------------------------------------------


def classify_origin(origin_url: str | None, source_type: str | None) -> Lead:
    """What an apply URL says, before any ATS is asked. Pure."""
    if not origin_url:
        return Lead("", "", outcome=LeadOutcome.NO_ORIGIN, detail="no applicationUrl on the page")
    if (source_type or "").lower() not in ("ats", ""):
        return Lead(
            "",
            "",
            origin_url=origin_url,
            outcome=LeadOutcome.NO_ORIGIN,
            detail=f"sourceType is {source_type!r}, not an ATS pointer",
        )
    found = identify_board_url(origin_url)
    if found is None:
        # An EMBEDDED board: the URL proves the family and the posting
        # (`?gh_jid=` on the employer's own domain) and names no board. The
        # posting id is kept so a bounded probe can look for the board and
        # be held to it; the family is recorded, the identifier is not.
        posting = identify_posting_url(origin_url)
        if posting is not None and posting[0] in guessable_providers():
            return Lead(
                "",
                "",
                origin_url=origin_url,
                provider=posting[0],
                external_id=posting[1],
                outcome=LeadOutcome.VALIDATION_DEFERRED,
                detail="embedded board: the family and the posting are named, the board is not",
            )
        return Lead(
            "",
            "",
            origin_url=origin_url,
            outcome=LeadOutcome.UNSUPPORTED_FAMILY,
            detail=f"no board family recognises {urlsplit(origin_url).netloc.lower()}",
        )
    provider, identifier, external_id = found
    if provider not in board_providers():
        return Lead(
            "",
            "",
            origin_url=origin_url,
            provider=provider,
            board_identifier=identifier,
            external_id=external_id,
            outcome=LeadOutcome.FEED_COVERED,
            detail="collected as a whole feed, never board by board",
        )
    return Lead(
        "",
        "",
        origin_url=origin_url,
        provider=provider,
        board_identifier=identifier,
        external_id=external_id,
        outcome=LeadOutcome.VALIDATION_DEFERRED,
        detail="named a board; the family's ATS has not been asked yet",
    )


# -- the walk --------------------------------------------------------------------


class BoardDiscovery:
    """One bounded, resumable walk over one aggregator index."""

    def __init__(
        self,
        conn: Any,
        fetcher: HttpFetcher,
        *,
        robots: RobotsPolicy | None = None,
        index_source: str = INDEX_SOURCE,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.robots = robots
        self.index_source = index_source
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- planning ----------------------------------------------------------

    def plan(
        self, employers: dict[str, list[str]], *, retry: bool = False
    ) -> list[tuple[str, str]]:
        """`(employer_key, sample_url)` for every employer still to walk.

        Employers with an answer are skipped; with `retry`, employers whose
        last outcome is retriable are walked again on the NEXT of their
        pages, so a page that published no origin is not asked twice.
        """
        rows = self.conn.execute(
            "SELECT employer_key, sample_url, outcome FROM board_discovery_lead"
            " WHERE index_source = ?",
            (self.index_source,),
        ).fetchall()
        answered = {r["employer_key"]: (r["sample_url"], r["outcome"]) for r in rows}
        planned: list[tuple[str, str]] = []
        for key, urls in employers.items():
            if not urls:
                continue
            previous = answered.get(key)
            if previous is None:
                planned.append((key, urls[0]))
                continue
            sample, outcome = previous
            if not retry or outcome not in RETRIABLE:
                continue
            fresh = [u for u in urls if u != sample]
            if fresh:
                planned.append((key, fresh[0]))
        return planned

    # -- one employer --------------------------------------------------------

    def walk_one(self, employer_key: str, sample_url: str, stats: DiscoveryStats) -> Lead:
        if self.robots is not None and self.robots.decide(sample_url) is not RobotsDecision.ALLOWED:
            lead = Lead(employer_key, sample_url, outcome=LeadOutcome.REFUSED_BY_HOST)
            return self._record(lead, stats)

        stats.requests_made += 1
        page = remotesource.fetch_lead(self.fetcher, sample_url)
        if page is None:
            lead = Lead(employer_key, sample_url, outcome=LeadOutcome.UNREADABLE)
            return self._record(lead, stats)

        classified = classify_origin(page.origin_url, page.source_type)
        lead = Lead(
            employer_key,
            sample_url,
            origin_url=classified.origin_url,
            provider=classified.provider,
            board_identifier=classified.board_identifier,
            external_id=classified.external_id,
            outcome=classified.outcome,
            detail=classified.detail,
            employer_name=page.employer_name,
            employer_website=page.employer_website,
        )
        if lead.outcome is LeadOutcome.VALIDATION_DEFERRED:
            if lead.board_identifier:
                lead = self._validate_and_register(lead, stats)
            else:
                lead = self._probe_embedded(lead, stats)
        return self._record(lead, stats)

    def _probe_embedded(self, lead: Lead, stats: DiscoveryStats) -> Lead:
        """An embedded board, found by a bounded probe and held to the posting.

        26 of the first 27 unsupported origins in production were
        `?gh_jid=` links on employers' own sites: the family and the exact
        posting are named, the board token is not. `candidate_identifiers`
        derives a handful of plausible identifiers from the employer's domain
        and name -- discovery, not a namespace walk -- and a board that
        answers is accepted ONLY when its listing carries that very posting.
        `acme` on the family may be a different Acme; carrying posting
        4125779003 is what makes it this one. Recorded under its own
        `discovery_method`, because a derived-and-confirmed identity is a
        different kind of evidence from a published one.
        """
        assert lead.provider and lead.external_id  # noqa: S101 -- classified above
        domain = normalise_domain(lead.employer_website) or normalise_domain(
            urlsplit(lead.origin_url or "").netloc
        )
        identifiers = candidate_identifiers(domain, lead.employer_name)
        if not identifiers:
            return _with(
                lead,
                outcome=LeadOutcome.UNSUPPORTED_FAMILY,
                detail="embedded board and no domain or name to derive an identifier from",
            )
        provider = get_provider(lead.provider, self.fetcher)
        deferred: str | None = None
        for identifier in identifiers:
            existing = self.boards.get_by_identifier(lead.provider, identifier)
            board = BoardRef(
                company_slug=lead.employer_key, provider=lead.provider, board_identifier=identifier
            )
            stats.requests_made += 1
            try:
                stubs = list(provider.list_postings(board))
            except FetchError as exc:
                if exc.category not in (
                    FetchErrorCategory.NOT_FOUND,
                    FetchErrorCategory.MALFORMED,
                ):
                    deferred = f"ATS unreachable for {identifier!r}: {exc.message[:100]}"
                continue
            if not any(stub.external_id == lead.external_id for stub in stubs):
                continue
            if existing is not None:
                return _with(
                    lead,
                    board_identifier=identifier,
                    outcome=LeadOutcome.ALREADY_REGISTERED,
                    detail="embedded board already in source_board; the posting is listed there",
                    source_board_id=str(existing["id"]),
                )
            confirmed = _with(lead, board_identifier=identifier)
            board_id = self._register(
                confirmed, provider.board_url(board), method=DISCOVERY_METHOD_CONFIRMED_PROBE
            )
            return _with(
                confirmed,
                outcome=LeadOutcome.REGISTERED,
                detail=f"embedded board; identifier derived from the employer and confirmed"
                f" by the linked posting among {len(stubs)} listed",
                source_board_id=board_id,
            )
        if deferred is not None:
            return _with(lead, outcome=LeadOutcome.VALIDATION_DEFERRED, detail=deferred)
        return _with(
            lead,
            outcome=LeadOutcome.UNSUPPORTED_FAMILY,
            detail=f"embedded board; none of {len(identifiers)} derived identifiers"
            " lists the linked posting",
        )

    def _validate_and_register(self, lead: Lead, stats: DiscoveryStats) -> Lead:
        assert lead.provider and lead.board_identifier  # noqa: S101 -- classified above
        existing = self.boards.get_by_identifier(lead.provider, lead.board_identifier)
        if existing is not None:
            return _with(
                lead,
                outcome=LeadOutcome.ALREADY_REGISTERED,
                detail="board already in source_board",
                source_board_id=str(existing["id"]),
            )

        provider = get_provider(lead.provider, self.fetcher)
        board = BoardRef(
            company_slug=lead.employer_key,
            provider=lead.provider,
            board_identifier=lead.board_identifier,
        )
        stats.requests_made += 1
        try:
            stubs = list(islice(provider.list_postings(board), VALIDATION_LIST_CAP))
        except FetchError as exc:
            outcome = {
                FetchErrorCategory.NOT_FOUND: BoardOutcome.NOT_FOUND,
                FetchErrorCategory.MALFORMED: BoardOutcome.MALFORMED,
            }.get(exc.category, BoardOutcome.TEMPORARY_FAILURE)
            if outcome is BoardOutcome.TEMPORARY_FAILURE:
                return _with(
                    lead,
                    outcome=LeadOutcome.VALIDATION_DEFERRED,
                    detail=f"ATS unreachable: {exc.message[:120]}",
                )
            return _with(
                lead,
                outcome=LeadOutcome.VALIDATION_FAILED,
                detail=f"ATS answered {outcome.value}: {exc.message[:120]}",
            )

        listed = _lists_the_posting(stubs, lead)
        # A host that answers for identifiers nobody registered proves
        # nothing by answering. For such a family the listing must carry the
        # very posting the aggregator linked. Not carrying it is not a
        # refusal -- the posting may have closed between the index and the
        # probe -- so the lead stays open for a retry on the employer's next
        # page rather than being recorded as an answer. Every other family
        # answers a wrong identity with an error, so a board that answered
        # for a PUBLISHED identity is a board, listed posting or not.
        if lead.provider in answering_for_unknown_providers() and listed is not True:
            return _with(
                lead,
                outcome=LeadOutcome.VALIDATION_DEFERRED,
                detail="the board answered and did not list the linked posting;"
                " this family is registered only on a listed posting",
            )

        board_id = self._register(lead, provider.board_url(board))
        detail = (
            f"{len(stubs)}{'+' if len(stubs) >= VALIDATION_LIST_CAP else ''} postings listed"
        ) + ("; linked posting among them" if listed else "; linked posting not listed")
        return _with(lead, outcome=LeadOutcome.REGISTERED, detail=detail, source_board_id=board_id)

    def _register(self, lead: Lead, board_url: str, method: str = DISCOVERY_METHOD) -> str:
        assert lead.provider and lead.board_identifier  # noqa: S101
        with transaction(self.conn):
            company_id = self._company_for(lead)
            return self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=lead.provider,
                    board_identifier=lead.board_identifier,
                    board_url=board_url,
                    discovery_method=method,
                    verified_at=now_utc(),
                )
            )

    def _company_for(self, lead: Lead) -> str:
        """An existing company row when one is already here, else a new one.

        Existing rows are never rewritten: `CompanyRepo.upsert` refreshes every
        mutable column, and an index's spelling must not overwrite a name and
        a website the registry or another source already established.
        """
        domain = normalise_domain(lead.employer_website)
        if domain is not None:
            row = self.companies.get_by_domain(domain)
            if row is not None:
                return str(row["id"])
        row = self.companies.get_by_slug(lead.employer_key)
        if row is not None:
            return str(row["id"])
        return self.companies.upsert(
            CompanyRecord(
                slug=lead.employer_key,
                name=lead.employer_name or lead.employer_key,
                website=lead.employer_website,
                canonical_domain=domain,
                discovery_source=self.index_source,
            )
        )

    def _record(self, lead: Lead, stats: DiscoveryStats) -> Lead:
        now = now_utc()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO board_discovery_lead"
                " (id, index_source, employer_key, employer_name, employer_website,"
                "  sample_url, origin_url, provider, board_identifier, external_id,"
                "  outcome, detail, source_board_id, first_seen_at, last_walked_at,"
                "  times_walked)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)"
                " ON CONFLICT (index_source, employer_key) DO UPDATE SET"
                "   employer_name = COALESCE(excluded.employer_name, employer_name),"
                "   employer_website = COALESCE(excluded.employer_website, employer_website),"
                "   sample_url = excluded.sample_url,"
                "   origin_url = excluded.origin_url,"
                "   provider = excluded.provider,"
                "   board_identifier = excluded.board_identifier,"
                "   external_id = excluded.external_id,"
                "   outcome = excluded.outcome,"
                "   detail = excluded.detail,"
                "   source_board_id = COALESCE(excluded.source_board_id, source_board_id),"
                "   last_walked_at = excluded.last_walked_at,"
                "   times_walked = times_walked + 1",
                (
                    new_id(),
                    self.index_source,
                    lead.employer_key,
                    lead.employer_name,
                    lead.employer_website,
                    lead.sample_url,
                    lead.origin_url,
                    lead.provider,
                    lead.board_identifier,
                    lead.external_id,
                    lead.outcome.value,
                    lead.detail,
                    lead.source_board_id,
                    now,
                    now,
                ),
            )
        stats.employers_walked += 1
        stats.record(lead)
        return lead

    # -- the whole run -------------------------------------------------------

    def run(
        self,
        employers: dict[str, list[str]],
        *,
        max_employers: int,
        retry: bool = False,
        progress: Any = None,
    ) -> DiscoveryStats:
        """Walk up to `max_employers` employers not yet answered, recording each.

        The run ledger sees the counters after every employer, so a walk that
        is killed leaves an honest row and the employers it answered stay
        answered: the next run starts after them.
        """
        stats = DiscoveryStats(employers_in_index=len(employers))
        planned = self.plan(employers, retry=retry)
        stats.employers_already_walked = len(employers) - len(planned)
        with transaction(self.conn):
            run_id = self.runs.start(RUN_NAME)
        status = PipelineRunStatus.OK
        error: str | None = None
        try:
            for key, url in planned[: max(0, max_employers)]:
                lead = self.walk_one(key, url, stats)
                with transaction(self.conn):
                    self.runs.progress(run_id, stats.as_dict())
                if progress is not None:
                    progress(lead, stats)
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            status = PipelineRunStatus.FAILED
            error = f"{type(exc).__name__}: {exc}"
        with transaction(self.conn):
            self.runs.finish(run_id, status, stats=stats.as_dict(), error=error)
        if error is not None:
            raise RuntimeError(error)
        return stats


# -- helpers ---------------------------------------------------------------------


def _with(lead: Lead, **changes: Any) -> Lead:
    values = {name: getattr(lead, name) for name in Lead.__dataclass_fields__}
    values.update(changes)
    return Lead(**values)


def _lists_the_posting(stubs: Iterable[Any], lead: Lead) -> bool | None:
    """True/False when the linked posting can be looked for; None when the
    family's URL named no posting id and the listing links cannot say."""
    stubs = list(stubs)
    if lead.external_id:
        return any(stub.external_id == lead.external_id for stub in stubs)
    if lead.origin_url:
        wanted = canonical_url(lead.origin_url)
        hits = [stub for stub in stubs if canonical_url(stub.url) == wanted]
        return bool(hits) if stubs else None
    return None


def supported_families() -> tuple[str, ...]:
    return board_recogniser_families()

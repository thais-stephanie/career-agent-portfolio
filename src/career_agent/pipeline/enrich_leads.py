"""Turning a metadata-only lead into a full posting, when its employer's own
board can be found without guessing.

WHAT A LEAD IS, AND WHAT IT LACKS
---------------------------------
A Jobgether row names an employer, a title, a hiring scope and a date, and
carries no advert: the page that holds one is closed by the vendor's terms
(`providers/jobgether.py`). The API publishes no origin pointer either. So the
only honest way to a full posting is the employer's OWN board, read through a
family this product already collects under its own permission.

TWO PATHS, BOTH EVIDENCE-BACKED
-------------------------------
1. RESOLVED_EXISTING_JOB. The corpus already holds an open, full-content
   posting from a canonical provider at the SAME employer (same company row --
   Jobgether's company slug and the registry's are the same string for the
   same name) with the SAME title, normalised. One match is the posting; the
   lead becomes a sighting on it (`job_discovery_source`, `matched_by =
   EMPLOYER_TITLE`) and is closed as folded. Two or more matches are
   AMBIGUOUS and nothing moves: a title an employer posts in three cities is
   three postings, and picking one would be a guess.

2. RESOLVED_NEW_ATS_JOB. The employer has no board in the registry. A BOUNDED
   probe through the guessable families (`pipeline/discover`), with the
   identifiers `candidate_identifiers` derives from the employer's name, may
   find a live board. A board that merely answers is NOT accepted: `acme` on
   Greenhouse may be a different Acme. It is accepted only when its listing
   carries the lead's exact title, which is the same evidence path 1 needs
   and is what makes a name-derived identifier stop being a guess. The board
   is then registered with `discovery_method = lead_enrichment_title_match`,
   collected through the normal runner, and the lead folds as in path 1.

Everything else is NO_CANONICAL_ORIGIN, or BLOCKED_ORIGIN when the family's
probe was refused by the host. `ORIGIN_FOUND_UNSUPPORTED` cannot occur for
this vendor, which publishes no origin; the classification exists for the
report so the absence is stated rather than implied.

THE LEAD'S SCOPE TRAVELS WITH THE SIGHTING
------------------------------------------
Folding a lead that said `Anywhere` into an ATS posting that says nothing
about hiring would lose the one fact the lead was worth. `pipeline/facts.py`
reads a sighting's hiring scope when the canonical provider publishes none,
and `rescore` is asked to recompute only the postings this pass touched.

Nothing here reads a search configuration, a title list or a keyword. The
title compared is the lead's own, against the employer's own board.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from career_agent.clock import now_utc
from career_agent.domain.enums import CollectionStatus, ContentCompleteness, PipelineRunStatus
from career_agent.net.fetcher import FetchError, HttpFetcher
from career_agent.pipeline.collect import Collector
from career_agent.pipeline.discover import candidate_identifiers, probe_board
from career_agent.providers.base import BoardRef
from career_agent.providers.registry import (
    content_completeness_for,
    get_provider,
    guessable_providers,
)
from career_agent.storage.db import transaction
from career_agent.storage.records import SourceBoardRecord
from career_agent.storage.repositories import (
    DiscoverySourceRepo,
    PipelineRunRepo,
    SourceBoardRepo,
)

RUN_NAME = "enrich-leads"
MATCH_EMPLOYER_TITLE = "EMPLOYER_TITLE"

RESOLVED_EXISTING_JOB = "RESOLVED_EXISTING_JOB"
RESOLVED_NEW_ATS_JOB = "RESOLVED_NEW_ATS_JOB"
ORIGIN_FOUND_UNSUPPORTED = "ORIGIN_FOUND_UNSUPPORTED"
NO_CANONICAL_ORIGIN = "NO_CANONICAL_ORIGIN"
BLOCKED_ORIGIN = "BLOCKED_ORIGIN"
AMBIGUOUS = "AMBIGUOUS"
NOT_EXAMINED = "NOT_EXAMINED"

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalise_title(title: str | None) -> str:
    """Case, accents, punctuation and whitespace folded; nothing else.

    `Senior Backend Engineer (Remote)` and `senior backend engineer - remote`
    are one title here; `Senior Backend Engineer` and `Backend Engineer` are
    two, because the seniority is part of what the employer posted.
    """
    if not title:
        return ""
    folded = unicodedata.normalize("NFKD", title)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return _PUNCT.sub(" ", ascii_only.casefold()).strip()


@dataclass
class EnrichmentStats:
    provider: str
    leads_examined: int = 0
    origin_urls_available: int = 0
    companies_examined: int = 0
    companies_probed: int = 0
    probes_made: int = 0
    boards_registered: int = 0
    ats_families_detected: Counter = field(default_factory=Counter)
    outcomes: Counter = field(default_factory=Counter)
    folded_job_ids: list[str] = field(default_factory=list)
    affected_canonical_ids: set[str] = field(default_factory=set)
    failures: list[str] = field(default_factory=list)
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "leads_examined": self.leads_examined,
            "origin_urls_available": self.origin_urls_available,
            "companies_examined": self.companies_examined,
            "companies_probed": self.companies_probed,
            "probes_made": self.probes_made,
            "boards_registered": self.boards_registered,
            "ats_families_detected": dict(self.ats_families_detected),
            "outcomes": dict(self.outcomes),
            "leads_folded": len(self.folded_job_ids),
            "canonical_jobs_affected": len(self.affected_canonical_ids),
            "failures": list(self.failures),
            "elapsed_ms": self.elapsed_ms,
            "note": (
                "a lead folds only into a posting at the same employer with the same title; "
                "a probed board is accepted only when its listing carries that title"
            ),
        }


class LeadEnricher:
    def __init__(self, conn: Any, fetcher: HttpFetcher, provider: str = "jobgether") -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.provider = provider
        self.boards = SourceBoardRepo(conn)
        self.discovery = DiscoverySourceRepo(conn)
        self.runs = PipelineRunRepo(conn)

    # -- the pass ----------------------------------------------------------

    def enrich(
        self,
        *,
        max_companies: int = 100,
        max_probes: int = 600,
        probe: bool = True,
        on_progress: Any = None,
        should_stop: Any = None,
    ) -> EnrichmentStats:
        started = time.monotonic()
        stats = EnrichmentStats(provider=self.provider)
        with transaction(self.conn):
            run_id = self.runs.start(RUN_NAME)
        status = PipelineRunStatus.OK
        try:
            leads = self._open_leads()
            stats.leads_examined = len(leads)
            stats.origin_urls_available = sum(1 for lead in leads if lead.get("origin_url"))
            by_company: dict[str, list[dict[str, Any]]] = {}
            for lead in leads:
                by_company.setdefault(lead["company_id"], []).append(lead)
            stats.companies_examined = len(by_company)

            # Path 1, offline, for every company.
            unresolved_companies: list[str] = []
            for company_id, rows in by_company.items():
                remaining = self._fold_against_corpus(rows, stats)
                if remaining:
                    unresolved_companies.append(company_id)
                by_company[company_id] = remaining

            # Path 2, bounded, for companies with no board of their own.
            if probe:
                for company_id in unresolved_companies:
                    if should_stop is not None and should_stop():
                        break
                    if stats.companies_probed >= max_companies or stats.probes_made >= max_probes:
                        break
                    rows = by_company[company_id]
                    if not rows or self._has_board(company_id):
                        # A registered board with no matching title: the
                        # employer's own board does not carry this posting.
                        for lead in rows:
                            lead["_outcome"] = NO_CANONICAL_ORIGIN
                            stats.outcomes[NO_CANONICAL_ORIGIN] += 1
                        continue
                    stats.companies_probed += 1
                    self._probe_and_fold(company_id, rows, stats, max_probes)
                    with transaction(self.conn):
                        self.runs.progress(run_id, stats.as_dict())
                    if on_progress is not None:
                        on_progress(
                            stats.companies_probed, min(max_companies, len(unresolved_companies))
                        )
            for rows in by_company.values():
                # Whatever is still open and unclassified was examined and
                # found nothing; say so rather than leaving a gap.
                for lead in rows:
                    if lead.get("_outcome") is None:
                        lead["_outcome"] = NO_CANONICAL_ORIGIN
                        stats.outcomes[NO_CANONICAL_ORIGIN] += 1
        except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
            stats.failures.append(f"{type(exc).__name__}: {exc}")
            status = PipelineRunStatus.FAILED
        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                status,
                stats=stats.as_dict(),
                error=stats.failures[0] if stats.failures else None,
            )
        return stats

    # -- path 1 --------------------------------------------------------------

    def _fold_against_corpus(
        self, rows: list[dict[str, Any]], stats: EnrichmentStats
    ) -> list[dict[str, Any]]:
        """Fold every lead that has exactly one full-content twin. Returns the
        leads that still need an origin."""
        remaining: list[dict[str, Any]] = []
        for lead in rows:
            twins = self._twins(lead)
            if len(twins) == 1:
                self._fold(lead, twins[0], stats, RESOLVED_EXISTING_JOB)
            elif len(twins) > 1:
                lead["_outcome"] = AMBIGUOUS
                stats.outcomes[AMBIGUOUS] += 1
            else:
                remaining.append(lead)
        return remaining

    def _twins(self, lead: dict[str, Any]) -> list[dict[str, Any]]:
        wanted = normalise_title(lead["title"])
        if not wanted:
            return []
        rows = self.conn.execute(
            "SELECT j.id, j.title, j.provider FROM job j"
            " WHERE j.company_id = ? AND j.provider != ? AND j.closed_at IS NULL"
            " AND j.content_hash IS NOT NULL",
            (lead["company_id"], self.provider),
        ).fetchall()
        found = [
            dict(row)
            for row in rows
            if content_completeness_for(row["provider"], has_text=True)
            is ContentCompleteness.FULL_CONTENT
            and normalise_title(row["title"]) == wanted
        ]
        # Two rows of the same canonical posting from two providers are one
        # twin; two postings from one provider are two.
        return found

    def _fold(
        self, lead: dict[str, Any], canonical: dict[str, Any], stats: EnrichmentStats, outcome: str
    ) -> None:
        with transaction(self.conn):
            self.discovery.record(
                job_id=canonical["id"],
                source=self.provider,
                external_id=lead["external_id"],
                matched_by=MATCH_EMPLOYER_TITLE,
                url=lead["url"],
                origin_url=None,
                payload=self._payload_of(lead["id"]),
            )
            # Closed as FOLDED: the posting lives on under its employer's own
            # row, this row stays as provenance, and the open count no longer
            # carries the same job twice.
            self.conn.execute(
                "UPDATE job SET closed_at = ?, collection_status = ?, updated_at = ? WHERE id = ?",
                (now_utc(), CollectionStatus.CLOSED.value, now_utc(), lead["id"]),
            )
        lead["_outcome"] = outcome
        stats.outcomes[outcome] += 1
        stats.folded_job_ids.append(lead["id"])
        stats.affected_canonical_ids.add(canonical["id"])
        stats.ats_families_detected[canonical["provider"]] += 1

    # -- path 2 --------------------------------------------------------------

    def _probe_and_fold(
        self,
        company_id: str,
        rows: list[dict[str, Any]],
        stats: EnrichmentStats,
        max_probes: int,
    ) -> None:
        company = self.conn.execute(
            "SELECT slug, name, website FROM company WHERE id = ?", (company_id,)
        ).fetchone()
        if company is None:
            return
        wanted = {normalise_title(lead["title"]) for lead in rows}
        identifiers = candidate_identifiers(domain=company["website"], name=company["name"])
        for provider_name in guessable_providers():
            for identifier in identifiers:
                if stats.probes_made >= max_probes:
                    return
                stats.probes_made += 1
                try:
                    result = probe_board(self.fetcher, provider_name, identifier)
                except FetchError as exc:
                    stats.failures.append(f"{provider_name}:{identifier}: {exc}")
                    continue
                if result.outcome.name == "TEMPORARY_FAILURE":
                    # A host that would not answer today. Not a refusal of the
                    # employer and not a board: the leads stay open.
                    stats.outcomes[BLOCKED_ORIGIN] += 1
                    continue
                if not result.outcome.is_board or result.posting_count == 0:
                    continue
                titles = self._board_titles(provider_name, identifier)
                if not titles & wanted:
                    # A board that answers is not this employer's board until
                    # it carries this employer's posting.
                    continue
                board_id = self._register(company_id, provider_name, identifier)
                stats.boards_registered += 1
                stats.ats_families_detected[provider_name] += 1
                self._collect_board(board_id)
                for lead in list(rows):
                    twins = self._twins(lead)
                    if len(twins) == 1:
                        self._fold(lead, twins[0], stats, RESOLVED_NEW_ATS_JOB)
                        rows.remove(lead)
                    elif len(twins) > 1:
                        lead["_outcome"] = AMBIGUOUS
                        stats.outcomes[AMBIGUOUS] += 1
                        rows.remove(lead)
                return

    def _board_titles(self, provider_name: str, identifier: str) -> set[str]:
        provider = get_provider(provider_name, self.fetcher)
        board = BoardRef(
            company_slug=identifier, provider=provider_name, board_identifier=identifier
        )
        try:
            return {normalise_title(stub.title) for stub in provider.list_postings(board)}
        except (FetchError, ValueError):
            return set()

    def _register(self, company_id: str, provider_name: str, identifier: str) -> str:
        provider = get_provider(provider_name, self.fetcher)
        board = BoardRef(
            company_slug=identifier, provider=provider_name, board_identifier=identifier
        )
        with transaction(self.conn):
            return self.boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=provider_name,
                    board_identifier=identifier,
                    board_url=provider.board_url(board),
                    discovery_method="lead_enrichment_title_match",
                    verified_at=now_utc(),
                )
            )

    def _collect_board(self, board_id: str) -> None:
        Collector(self.conn, self.fetcher).collect_all(board_ids={board_id})

    # -- lookups -------------------------------------------------------------

    def _open_leads(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT j.id, j.company_id, j.external_id, j.url, j.title FROM job j"
            " WHERE j.provider = ? AND j.closed_at IS NULL ORDER BY j.company_id, j.id",
            (self.provider,),
        ).fetchall()
        return [dict(row) for row in rows]

    def _has_board(self, company_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM source_board WHERE company_id = ? AND provider != ? LIMIT 1",
            (company_id, self.provider),
        ).fetchone()
        return row is not None

    def _payload_of(self, job_id: str) -> dict[str, Any]:
        import json

        row = self.conn.execute(
            "SELECT payload_json FROM job_provider_payload WHERE job_id = ?"
            " ORDER BY captured_at DESC LIMIT 1",
            (job_id,),
        ).fetchone()
        return json.loads(row[0]) if row else {}

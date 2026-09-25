"""Employer board discovery: from employers the corpus has seen to their ATS.

An aggregator shows one posting from an employer; that employer's own board on
Ashby, Greenhouse or Lever usually holds many more, including roles no
aggregator republished. Measured on 2026-09-25: Himalayas carried ONE posting
from an employer whose Ashby board held 83, several of them squarely what the
person searched for.

THE METHOD
----------
For each employer seen on an aggregator and holding no board yet (most
relevant first, by the best Search Fit any of its postings earned):

1. a SMALL set of plausible identifiers: the aggregator's own company slug
   first (the strongest evidence there is), then `pipeline.discover`'s bounded
   name-derived candidates. Never a namespace walk;
2. each identifier on each guessable family, through that family's own
   adapter -- the same request a collection would make;
3. a board is REGISTERED only when it lists a posting whose title matches one
   the aggregator showed for this employer. A host answering for an identifier
   is not evidence of the right board: a board called "the" may well exist.

Every employer's outcome is recorded in `board_discovery_lead`, so the next
run never probes it again unless the answer was a temporary failure.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.net.fetcher import FetchError, FetchErrorCategory, HttpFetcher
from career_agent.pipeline.discover import candidate_identifiers
from career_agent.providers.base import BoardRef
from career_agent.providers.registry import get_provider
from career_agent.storage.db import transaction
from career_agent.storage.records import SourceBoardRecord
from career_agent.storage.repositories import SourceBoardRepo

INDEX_SOURCE = "employer_name_probe"
DISCOVERY_METHOD = "employer_name_probe"
#: Families whose board identity can be derived from a company name and whose
#: public board is one JSON request. Others need a tenant or site id nobody
#: can guess, and probing them would be inventing identities.
FAMILIES: tuple[str, ...] = ("ashby", "greenhouse", "lever")
MAX_IDENTIFIERS = 3
LIST_CAP = 400
#: Providers that aggregate other employers' postings. Their employers are the
#: leads. A board family's own employers already have a board.
AGGREGATORS: tuple[str, ...] = (
    "himalayas",
    "jobgether",
    "remoteok",
    "wwr",
    "workingnomads",
    "jobicy",
    "dynamitejobs",
    "fourdayweek",
    "getonbrd",
    "remotive",
    "speedrun",
    "linkedin",
)

REGISTERED = "REGISTERED"
#: The existing lead vocabulary's word for "every candidate board answered
#: no": not found, or found without this employer's postings.
NO_BOARD = "VALIDATION_FAILED"
DEFERRED = "VALIDATION_DEFERRED"

_TITLE = re.compile(r"[^\w]+")


def title_key(title: str | None) -> str:
    return _TITLE.sub(" ", str(title or "").casefold()).strip()


@dataclass
class Employer:
    company_id: str
    key: str
    name: str
    titles: set[str] = field(default_factory=set)
    best_fit: int = 0
    #: A posting of theirs came back from one of the person's targeted searches.
    targeted: bool = False


@dataclass
class EmployerBoardStats:
    employers_considered: int = 0
    employers_probed: int = 0
    requests: int = 0
    registered: int = 0
    no_board: int = 0
    deferred: int = 0
    boards: list[str] = field(default_factory=list)
    stopped_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "employers_considered": self.employers_considered,
            "employers_probed": self.employers_probed,
            "requests": self.requests,
            "registered": self.registered,
            "no_board": self.no_board,
            "deferred": self.deferred,
            "boards": list(self.boards),
            "stopped_reason": self.stopped_reason,
        }


def employers_to_probe(conn: sqlite3.Connection, *, limit: int) -> list[Employer]:
    """Aggregator employers with no board of a guessable family and no final
    answer yet: those the targeted searches surfaced first, then by the best
    Search Fit any of their postings earned."""
    marks = ",".join("?" for _ in AGGREGATORS)
    families = ",".join("?" for _ in FAMILIES)
    rows = conn.execute(
        f"""
        SELECT c.id, c.slug, c.name, j.title, COALESCE(MAX(jm.match_score), 0) AS fit,
               EXISTS (
                 SELECT 1 FROM job_retrieval_lane l
                 WHERE l.job_id = j.id AND l.lane = 'targeted'
               ) AS targeted
        FROM job j
        JOIN company c ON c.id = j.company_id
        LEFT JOIN job_match jm ON jm.job_id = j.id
        WHERE j.provider IN ({marks}) AND j.closed_at IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM source_board sb
            WHERE sb.company_id = c.id AND sb.provider IN ({families})
          )
          AND NOT EXISTS (
            SELECT 1 FROM board_discovery_lead l
            WHERE l.index_source = ? AND l.employer_key = c.slug AND l.outcome <> ?
          )
        GROUP BY c.id, j.id
        """,
        (*AGGREGATORS, *FAMILIES, INDEX_SOURCE, DEFERRED),
    ).fetchall()
    employers: dict[str, Employer] = {}
    for company_id, slug, name, title, fit, targeted in rows:
        employer = employers.setdefault(
            str(company_id), Employer(str(company_id), str(slug), str(name or slug))
        )
        employer.titles.add(title_key(title))
        employer.best_fit = max(employer.best_fit, int(fit or 0))
        employer.targeted = employer.targeted or bool(targeted)
    # The person's own searches surfaced these employers: the strongest
    # relevance signal there is, and one that does not depend on literal
    # phrase matches the way Search Fit alone would.
    ranked = sorted(employers.values(), key=lambda e: (not e.targeted, -e.best_fit, e.key))
    return ranked[:limit]


def _identifiers(employer: Employer) -> list[str]:
    extra = (employer.key,) if employer.key else ()
    return list(candidate_identifiers(name=employer.name, extra=extra))[:MAX_IDENTIFIERS]


def probe_employer(
    fetcher: HttpFetcher, employer: Employer, stats: EmployerBoardStats
) -> tuple[str, str | None, str | None, str]:
    """(outcome, family, identifier, detail) for one employer."""
    for identifier in _identifiers(employer):
        for family in FAMILIES:
            provider = get_provider(family, fetcher)
            board = BoardRef(
                company_slug=employer.key, provider=family, board_identifier=identifier
            )
            stats.requests += 1
            try:
                stubs = list(islice(provider.list_postings(board), LIST_CAP))
            except FetchError as exc:
                if exc.category in (FetchErrorCategory.NOT_FOUND, FetchErrorCategory.MALFORMED):
                    continue
                return DEFERRED, family, identifier, f"{family} could not be reached"
            except Exception:  # noqa: BLE001 - an adapter's refusal is "not this board"
                continue
            listed = {title_key(stub.title) for stub in stubs}
            if listed & employer.titles:
                return REGISTERED, family, identifier, f"{len(stubs)} postings listed"
    return NO_BOARD, None, None, "no board listed this employer's postings"


def discover_employer_boards(
    conn: sqlite3.Connection,
    fetcher: HttpFetcher,
    *,
    limit: int = 40,
    should_stop: Callable[[], bool] | None = None,
) -> EmployerBoardStats:
    stats = EmployerBoardStats()
    employers = employers_to_probe(conn, limit=limit)
    stats.employers_considered = len(employers)
    boards = SourceBoardRepo(conn)
    for employer in employers:
        if should_stop is not None and should_stop():
            stats.stopped_reason = "cancelled"
            break
        stats.employers_probed += 1
        outcome, family, identifier, detail = probe_employer(fetcher, employer, stats)
        board_id: str | None = None
        with transaction(conn):
            if outcome == REGISTERED and family and identifier:
                provider = get_provider(family, fetcher)
                board_id = boards.upsert(
                    SourceBoardRecord(
                        company_id=employer.company_id,
                        provider=family,
                        board_identifier=identifier,
                        board_url=provider.board_url(
                            BoardRef(
                                company_slug=employer.key,
                                provider=family,
                                board_identifier=identifier,
                            )
                        ),
                        discovery_method=DISCOVERY_METHOD,
                        verified_at=now_utc(),
                    )
                )
                stats.registered += 1
                stats.boards.append(f"{family}:{identifier}")
            elif outcome == NO_BOARD:
                stats.no_board += 1
            else:
                stats.deferred += 1
            _record(conn, employer, outcome, family, identifier, detail, board_id)
    return stats


def _record(
    conn: sqlite3.Connection,
    employer: Employer,
    outcome: str,
    family: str | None,
    identifier: str | None,
    detail: str,
    board_id: str | None,
) -> None:
    now = now_utc()
    conn.execute(
        "INSERT INTO board_discovery_lead"
        " (id, index_source, employer_key, employer_name, sample_url, provider,"
        "  board_identifier, outcome, detail, source_board_id, first_seen_at,"
        "  last_walked_at, times_walked)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)"
        " ON CONFLICT (index_source, employer_key) DO UPDATE SET"
        "   provider = excluded.provider, board_identifier = excluded.board_identifier,"
        "   outcome = excluded.outcome, detail = excluded.detail,"
        "   source_board_id = COALESCE(excluded.source_board_id, source_board_id),"
        "   last_walked_at = excluded.last_walked_at, times_walked = times_walked + 1",
        (
            new_id(),
            INDEX_SOURCE,
            employer.key,
            employer.name,
            f"employer:{employer.key}",
            family,
            identifier,
            outcome,
            detail,
            board_id,
            now,
            now,
        ),
    )

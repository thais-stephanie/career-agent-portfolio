"""Manual import: the universal path for every source we may not fetch.

Eleven of the eighteen sources in `docs/product/source-capability-matrix.md`
are manual-import-only: Vagas, Indeed, LinkedIn, Wellfound, Trampos, SINE and
the rest, because their terms or their robots.txt say so, or because we could
not read their terms at all. The honest response is not a cleverer scraper. It
is this: the person is allowed to read those pages, and the agent is allowed to
help with what they bring back.

A pasted posting becomes a first-class row. It is normalised, content-hashed,
deduplicated, scored and tracked exactly like a collected one, and it keeps its
provenance: `provider = "manual_import"` and `access_method = "manual_import"`
travel with it everywhere, so the interface can always say where a job came
from and nothing pretends a paste was an API.

Reuse, not reimplementation: `JobRawRepo` content-addresses the text,
`JobRepo.upsert_seen` handles the duplicate case, and `content_hash` means
pasting the same posting twice produces one row. The only thing invented here
is the synthetic company and board that satisfy `job`'s NOT NULL foreign keys.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any

from career_agent.domain.enums import CollectionStatus
from career_agent.domain.normalize import collapse_whitespace, content_hash
from career_agent.match.engine import JobFacts, match_job
from career_agent.match.identity import input_digest as reading_identity
from career_agent.storage.db import transaction
from career_agent.storage.mvp_repo import ACCESS_METHOD_MANUAL, MatchRepo
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

#: Our own provider name, not a vendor's. A pasted posting is still a posting.
MANUAL_PROVIDER = ACCESS_METHOD_MANUAL

#: `job.url` is NOT NULL and a pasted posting may have no URL. This scheme is
#: deliberately not http(s) so that the interface's own URL check renders it as
#: text rather than as a broken link.
_PLACEHOLDER_SCHEME = "manual:"


class ImportError_(ValueError):
    """A paste that cannot become a posting."""


def slugify(name: str) -> str:
    """A stable company key from a typed company name.

    Accent-folded so "Ativa Sistemas" and "Ativa Sistemas" reached by different
    keyboards land on the same company rather than creating two.
    """
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only.casefold()).strip("-")
    return slug or "unknown-company"


def import_posting(
    conn: sqlite3.Connection,
    config: Any,
    *,
    title: str,
    company: str,
    description: str,
    url: str | None = None,
    location: str | None = None,
    department: str | None = None,
    posted_at: str | None = None,
    config_id: str,
    config_version: int,
    now: str,
) -> str:
    """Persist one pasted posting and score it. Returns the job id.

    Idempotent on the description text: pasting the same posting for the same
    company twice updates the existing row instead of creating a second.
    """
    title = collapse_whitespace(title or "")
    company = collapse_whitespace(company or "")
    description = (description or "").strip()
    if not title:
        raise ImportError_("a title is required")
    if not company:
        raise ImportError_("a company name is required")
    if len(description) < 40:
        raise ImportError_("the description is too short to score meaningfully")

    slug = slugify(company)
    digest = content_hash(description)
    # The external id is derived from the text, so re-pasting is an update.
    # UNIQUE(provider, external_id) then does the deduplication for us.
    external_id = f"{slug}:{digest[:16]}"

    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(
            CompanyRecord(
                slug=slug,
                name=company,
                discovery_source="manual_import",
                notes="Created by manual import. Not a collected board.",
            )
        )
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider=MANUAL_PROVIDER,
                board_identifier=slug,
                board_url=f"{_PLACEHOLDER_SCHEME}//{slug}",
                active=False,  # nothing ever collects from it
                discovery_method="manual_import",
            )
        )
        JobRawRepo(conn).put(description)
        job_id = JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=MANUAL_PROVIDER,
                external_id=external_id,
                url=url or f"{_PLACEHOLDER_SCHEME}//{external_id}",
                title=title,
                department=department,
                location_raw=location,
                posted_at=posted_at,
                content_hash=digest,
            ),
            status=CollectionStatus.NORMALISED,
        )

    result = match_job(
        config,
        JobFacts(
            job_id=job_id,
            title=title,
            description=description,
            location_raw=location,
            posted_at=posted_at,
            provider=MANUAL_PROVIDER,
            access_method=MANUAL_PROVIDER,
        ),
        computed_at=now,
    )
    with transaction(conn):
        matches = MatchRepo(conn)
        matches.store(
            job_id,
            digest,
            result,
            config_digest=str(getattr(config, "digest", "UNRECORDED")),
            # Same reason as the demo seeder: a path that writes a score must
            # write the identity it was computed under, or its rows are
            # permanently unreplayable and differ in a column from a rescored
            # row of the same posting.
            input_digest=reading_identity(config),
        )
        # Scored in the same process from the rows written above; the ledger
        # marks those writes left are settled. See `MatchRepo.settle_dirty`.
        matches.settle_dirty([job_id])
    return job_id

"""Building the demo database from the sanitised corpus.

`evaluation/demo/demo_postings.yaml` holds nineteen invented postings at
invented companies, every URL on the reserved `.invalid` TLD. Nineteen
postings, seventeen roles: one of them is published once per regional board,
so the corpus can demonstrate duplicate grouping rather than assert it.

This turns them into a real database: real companies, real boards, real
`job_raw` rows, content-addressed and deduplicated exactly like a collected
posting, then scored by the same matcher.

That last part is what makes the demo trustworthy rather than a mock-up. There
is no separate demo code path -- the screenshots show the product running the
production matcher over the production schema, and only the *text* is invented.

**A DEMO POSTING MAY ONLY ASSERT WHAT ITS PROVIDER CAN PUBLISH.** The corpus
demonstrates what Career Agent can know from a source, not what a fixture
author wishes it knew. This used to be false in a way nothing announced: the
seeder built `JobFacts` from the corpus file, handing the matcher an
employment type and a pay period as keyword arguments, while `rescore` -- the
only path the real corpus ever takes -- can read those only out of an archived
provider payload through that provider's own reader. Pressing Recalculate on
the demo therefore moved 18 of its 19 scores.

So each posting now names the provider it imitates and carries a payload
shaped like that provider's real response, the payload is archived exactly as
a collection would archive it, and the facts are derived by
`pipeline/facts.py` -- the same function `rescore` calls. A Greenhouse posting
has no employment type and no pay period, because Greenhouse publishes
neither, and a card that said otherwise would be this fixture lying about what
the product can see.

`data/demo.db` is gitignored like everything else under `data/`, so the demo is
rebuilt from the committed YAML rather than shipped as a binary.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from career_agent.domain.enums import CollectionStatus
from career_agent.domain.normalize import content_hash
from career_agent.match.engine import match_job
from career_agent.match.identity import input_digest as reading_identity
from career_agent.pipeline.facts import job_facts
from career_agent.storage.db import transaction
from career_agent.storage.mvp_repo import MatchRepo
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)
from career_agent.yaml_io import safe_load

#: Which provider the demo corpus imitates is read from the corpus FILE, not
#: written here. A vendor name in generic code is what
#: `tests/unit/test_provider_neutrality.py` forbids, and it is right to: the
#: demo wants "a structured ATS", not any particular one. The fallback is the
#: first name the registry offers, so the demo follows the adapters rather
#: than pinning one.
DEMO_PROVIDER_KEY = "imitates_provider"

#: The same key, on a POSTING, overrides the file-level one.
#:
#: The corpus imitates several providers on purpose. A posting that has to
#: demonstrate a salary with a period needs an adapter that can read one, and
#: only some can: Ashby's `compensation.compensationTiers` carries an
#: `interval`, Greenhouse's `currency_range` carries no interval at all. So
#: the posting names the source whose capabilities its facts require, and the
#: alternative -- one provider for the whole corpus -- would force every rich
#: posting to be a fiction about what that provider exposes.


def load_demo_postings(source: Path) -> tuple[list[dict[str, Any]], str]:
    """The postings, and the provider name the corpus says it imitates."""
    parsed = safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict) or "postings" not in parsed:
        raise ValueError(f"{source} does not look like a demo corpus")
    provider = parsed.get(DEMO_PROVIDER_KEY) or _first_registered_provider()
    return list(parsed["postings"]), str(provider)


def _first_registered_provider() -> str:
    from career_agent.providers.registry import available_providers

    names = available_providers()
    if not names:
        raise ValueError("no providers are registered; the demo cannot name a source")
    return sorted(names)[0]


def seed_demo(
    conn: sqlite3.Connection,
    config: Any,
    *,
    source: Path,
    now: str = "2026-09-04T00:00:00Z",
) -> dict[str, int]:
    """Populate and score. Idempotent: running twice changes nothing."""
    postings, demo_provider = load_demo_postings(source)

    companies = CompanyRepo(conn)
    boards = SourceBoardRepo(conn)
    raws = JobRawRepo(conn)
    jobs = JobRepo(conn)
    payloads = ProviderPayloadRepo(conn)
    matches = MatchRepo(conn)
    config_digest = str(getattr(config, "digest", "UNRECORDED"))

    seen_companies: dict[str, str] = {}
    scored = 0

    for posting in postings:
        company = posting["company"]
        slug = company["slug"]
        description = posting["description"]
        digest = content_hash(description)
        provider = str(posting.get(DEMO_PROVIDER_KEY) or demo_provider)
        payload = posting.get("payload")

        with transaction(conn):
            if slug not in seen_companies:
                seen_companies[slug] = companies.upsert(
                    CompanyRecord(
                        slug=slug,
                        name=company["name"],
                        hq_country=company.get("hq_country"),
                        discovery_source="demo_corpus",
                        notes="Invented company from the sanitised demo corpus.",
                    )
                )
            company_id = seen_companies[slug]
            board_id = boards.upsert(
                SourceBoardRecord(
                    company_id=company_id,
                    provider=provider,
                    board_identifier=f"demo-{slug}",
                    board_url=posting["url"],
                    active=False,  # never collected from; the corpus is the file
                    discovery_method="demo_corpus",
                )
            )
            raws.put(description)
            job_id = jobs.upsert_seen(
                JobRecord(
                    company_id=company_id,
                    source_board_id=board_id,
                    provider=provider,
                    external_id=posting["external_id"],
                    url=posting["url"],
                    title=posting["title"],
                    department=posting.get("department"),
                    location_raw=posting.get("location_raw"),
                    posted_at=posting.get("posted_at"),
                    content_hash=digest,
                ),
                status=CollectionStatus.NORMALISED,
            )
            # ARCHIVED BEFORE IT IS READ, exactly as a collection archives it.
            # The facts below are then derived from this row rather than from
            # the corpus file, so seeding and rescoring cannot disagree: they
            # are reading the same bytes through the same reader.
            if payload is not None:
                payloads.put(
                    ProviderPayloadRecord(job_id=job_id, provider=provider, payload=payload)
                )

        result = match_job(
            config,
            # NO KEYWORD ARGUMENTS FOR FACTS. `job_facts` takes the job row and
            # the archived payload and nothing else, so this seeder cannot hand
            # the matcher something a rescore could not reconstruct. That used
            # to be possible and was the whole defect.
            job_facts(
                job_id=job_id,
                title=posting["title"],
                description=description,
                location_raw=posting.get("location_raw"),
                posted_at=posting.get("posted_at"),
                provider=provider,
                payload=payload,
            ),
            computed_at=now,
        )
        with transaction(conn):
            matches.store(
                job_id,
                digest,
                result,
                config_digest=config_digest,
                # THE SEEDER WRITES THE SAME IDENTITY A RESCORE WOULD.
                # It scores with today's readers under today's configuration,
                # so its rows are as replayable as any other -- and leaving
                # the column NULL made a seeded row differ from a rescored
                # one in a column, which is exactly what
                # `test_demo_is_reconstructible` exists to catch.
                input_digest=reading_identity(config),
            )
            # Scored from the rows written above, in this process: the marks
            # those writes left are settled. See `MatchRepo.settle_dirty`.
            matches.settle_dirty([job_id])
        scored += 1

    return {
        "companies": len(seen_companies),
        "postings": len(postings),
        "scored": scored,
    }

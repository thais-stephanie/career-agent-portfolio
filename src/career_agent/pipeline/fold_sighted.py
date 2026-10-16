"""One vacancy, one open posting: an aggregator's own row folds into its sighting.

ADR-0013: an aggregator's posting whose origin is an employer's ATS posting is
recorded as a SIGHTING on that canonical posting, never as a second job. That
holds when the canonical posting is already held. When the aggregator saw the
vacancy FIRST, it became a job of its own; when the employer's board was
collected later and the aggregator's next pass recognised the canonical
posting, the sighting was recorded and the aggregator's own row stayed open
beside it. Measured 2026-10-16: 125 open Speedrun postings duplicated an open
Ashby or Greenhouse posting, one of them three times in a GOOD/STRONG export.

This closes such a row as FOLDED, the way `enrich_leads` closes a lead: the
row stays as provenance, the sighting keeps the aggregator's link and payload,
and the open count no longer carries the same job twice. Deterministic: the
aggregator's row and its sighting share the aggregator's own `external_id`
or URL. Nothing is deleted.
"""

from __future__ import annotations

import sqlite3

from career_agent.clock import now_utc
from career_agent.domain.enums import CollectionStatus
from career_agent.storage.db import transaction

_TWINS = """
    SELECT DISTINCT own.id
    FROM job_discovery_source AS sighting
    JOIN job AS own
      ON own.provider = sighting.source
     AND (own.external_id = sighting.external_id
          OR (own.url IS NOT NULL AND own.url = sighting.url))
    JOIN job AS canonical ON canonical.id = sighting.job_id
    WHERE own.id != sighting.job_id
      AND own.closed_at IS NULL
      AND canonical.closed_at IS NULL
      AND canonical.provider != sighting.source
"""


def _sight_by_origin(conn: sqlite3.Connection) -> None:
    """Record the sighting an aggregator's later pass never recorded.

    A collector that already holds its own row for a posting stops at its
    first identity rule (its own id) and never asks again whether the
    employer's posting has since been collected. The stored payload still
    names that posting, so the sighting is recorded here, from the same
    deterministic pointer the collector would have used."""
    from career_agent.providers.registry import (
        identify_posting_url,
        origin_reader_providers,
        origin_url_of,
    )
    from career_agent.storage.repositories import DiscoverySourceRepo, ProviderPayloadRepo

    providers = origin_reader_providers()
    if not providers:
        return
    marks = ",".join("?" * len(providers))
    rows = conn.execute(
        f"SELECT id, provider, external_id, url FROM job WHERE closed_at IS NULL"
        f" AND provider IN ({marks})",
        providers,
    ).fetchall()
    if not rows:
        return
    payloads = ProviderPayloadRepo(conn).latest_for_jobs([str(r[0]) for r in rows])
    found = []
    for job_id, provider, external_id, url in rows:
        link = origin_url_of(str(provider), payloads.get(str(job_id)))
        identified = identify_posting_url(link) if link else None
        if identified is None or identified[0] == provider:
            continue
        canonical = conn.execute(
            "SELECT id FROM job WHERE provider = ? AND external_id = ? AND closed_at IS NULL",
            identified,
        ).fetchone()
        if canonical is not None:
            found.append((str(canonical[0]), str(provider), str(external_id), url, link))
    if not found:
        return
    sightings = DiscoverySourceRepo(conn)
    with transaction(conn):
        for canonical_id, provider, external_id, url, link in found:
            held = conn.execute(
                "SELECT job_id FROM job_discovery_source WHERE source = ? AND external_id = ?",
                (provider, external_id),
            ).fetchone()
            if held is not None and str(held[0]) == canonical_id:
                continue
            sightings.record(
                job_id=canonical_id,
                source=provider,
                external_id=external_id,
                matched_by="ORIGIN_URL",
                url=url,
                origin_url=link,
            )


def fold_sighted_duplicates(conn: sqlite3.Connection) -> list[str]:
    """Close every open aggregator row whose vacancy is an open canonical
    posting's sighting, after recording the sightings an aggregator's origin
    pointer proves. Returns the ids closed."""
    _sight_by_origin(conn)
    ids = [str(row[0]) for row in conn.execute(_TWINS)]
    if not ids:
        return []
    now = now_utc()
    with transaction(conn):
        conn.executemany(
            "UPDATE job SET closed_at = ?, collection_status = ?, updated_at = ?"
            " WHERE id = ? AND closed_at IS NULL",
            [(now, CollectionStatus.CLOSED.value, now, job_id) for job_id in ids],
        )
    return ids

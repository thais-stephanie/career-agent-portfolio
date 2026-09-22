"""Re-derive stored description text after a normalisation change.

Normalisation is deterministic, so `job_raw.description_text` is a *derived*
view of `job_raw.description_html`. When the rendering rules improve, the derived
view goes stale -- and because `job` points at raw rows by content hash, stale
text is what M2 would pay to extract from.

This module rebuilds that view offline. No network, no provider adapter, no
board: the html we already archived is the input, and `html_to_text` is the only
logic. That is the whole reason `job_raw.description_html` and
`job_provider_payload` are kept.

**This is a NORMALISATION CHANGE, not a SOURCE CHANGE.** The employer did not
touch the posting; we improved our rendering of it. So the backfill must not
look like an observation from the board: it never closes or reopens a job, never
advances `last_seen_at` or `first_seen_at`, never touches a board's success or
failure state, and never invents a provider payload. It moves jobs from one
immutable raw row to another and leaves the old row where it is.

Safety note, proven on the corpus rather than assumed: `job_raw` is
content-addressed on the TEXT, so several postings can share one row while their
original html differed. Re-normalising the row and moving all of its jobs
together is correct only if those postings would still render identically. At
the time of the M1C.1 migration that held -- 127 shared groups covering 315
jobs, zero divergent, audited against each posting's own archived payload. Use
`shared_rows(conn)` to list the groups carrying that risk before trusting a
future normalisation change; re-deriving each member's own html to compare needs
the provider adapters, which is why that audit lives outside this
provider-neutral module.
"""

import time
from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import PipelineRunStatus
from career_agent.domain.normalize import content_hash, html_to_text
from career_agent.storage.db import transaction
from career_agent.storage.repositories import JobRawRepo, JobRepo, PipelineRunRepo


@dataclass
class RenormalizeStats:
    """What the backfill did. Reported, and stored on `pipeline_run`."""

    raw_rows_inspected: int = 0
    raw_rows_unchanged: int = 0
    raw_rows_rewritten: int = 0
    #: New raw rows written because the corrected text was not already stored.
    raw_rows_created: int = 0
    #: Corrected texts that turned out to equal an existing row -- two postings
    #: converging once the rendering improved. Deduplication, not loss.
    raw_rows_converged: int = 0
    #: Raw rows with no archived html, which cannot be re-derived and are left
    #: exactly as they are rather than guessed at.
    raw_rows_without_html: int = 0
    jobs_repointed: int = 0
    jobs_by_provider: dict[str, int] = field(default_factory=dict)
    elapsed_ms: int = 0

    @property
    def is_noop(self) -> bool:
        return self.raw_rows_rewritten == 0 and self.jobs_repointed == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": "NORMALIZATION_CHANGE",
            "raw_rows_inspected": self.raw_rows_inspected,
            "raw_rows_unchanged": self.raw_rows_unchanged,
            "raw_rows_rewritten": self.raw_rows_rewritten,
            "raw_rows_created": self.raw_rows_created,
            "raw_rows_converged": self.raw_rows_converged,
            "raw_rows_without_html": self.raw_rows_without_html,
            "jobs_repointed": self.jobs_repointed,
            "jobs_by_provider": self.jobs_by_provider,
            "elapsed_ms": self.elapsed_ms,
        }


def _referenced_rows(conn: Any) -> list[Any]:
    """Raw rows that a job currently points at, with who points at them.

    Only reachable rows are rebuilt. Superseded ones are history: their text was
    correct for the posting as it read at the time and for the rules in force,
    and rewriting history to today's renderer would destroy the very provenance
    this table exists to keep.
    """
    return conn.execute(
        "SELECT r.content_hash, r.description_html,"
        "       COUNT(j.id) AS job_count,"
        "       GROUP_CONCAT(DISTINCT j.provider) AS providers"
        "  FROM job_raw r"
        "  JOIN job j ON j.content_hash = r.content_hash"
        " GROUP BY r.content_hash, r.description_html"
        " ORDER BY r.content_hash"
    ).fetchall()


def renormalize(conn: Any) -> RenormalizeStats:
    """Rebuild every reachable description_text under the current rules.

    Idempotent by construction: the second run recomputes the same text, finds
    the hash already correct, and changes nothing.
    """
    started = time.monotonic()
    stats = RenormalizeStats()
    raw = JobRawRepo(conn)
    jobs = JobRepo(conn)
    runs = PipelineRunRepo(conn)

    with transaction(conn):
        run_id = runs.start("renormalize")

    rows = _referenced_rows(conn)
    with transaction(conn):
        for row in rows:
            stats.raw_rows_inspected += 1
            html = row["description_html"]
            if not html:
                stats.raw_rows_without_html += 1
                continue

            corrected = html_to_text(html)
            new_hash = content_hash(corrected)
            if new_hash == row["content_hash"]:
                stats.raw_rows_unchanged += 1
                continue

            if raw.get(new_hash) is None:
                stats.raw_rows_created += 1
            else:
                stats.raw_rows_converged += 1
            raw.put(corrected, html)

            moved = jobs.repoint_content_hash(row["content_hash"], new_hash)
            stats.raw_rows_rewritten += 1
            stats.jobs_repointed += moved

            # A raw row shared across providers would make per-provider
            # attribution a guess, so it is reported as MIXED rather than split
            # arbitrarily. None exist in the current corpus; the branch is here
            # so that if one ever appears it shows up instead of being averaged
            # away.
            providers = [p for p in str(row["providers"] or "").split(",") if p]
            key = providers[0] if len(providers) == 1 else "MIXED"
            stats.jobs_by_provider[key] = stats.jobs_by_provider.get(key, 0) + moved

    stats.elapsed_ms = int((time.monotonic() - started) * 1000)
    with transaction(conn):
        runs.finish(run_id, PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
    return stats


def shared_rows(conn: Any) -> list[Any]:
    """Raw rows that more than one current job points at.

    These are the only rows where re-normalisation could, in principle, give one
    posting another's text: the row stores whichever posting's html arrived
    first. Listing them is the cheap, provider-neutral half of that check -- the
    expensive half, re-deriving each member's own html from its archived
    payload, needs the adapters and belongs to a milestone audit rather than
    here.
    """
    return conn.execute(
        "SELECT r.content_hash, COUNT(j.id) AS job_count,"
        "       GROUP_CONCAT(DISTINCT j.provider) AS providers"
        "  FROM job_raw r"
        "  JOIN job j ON j.content_hash = r.content_hash"
        " GROUP BY r.content_hash"
        " HAVING COUNT(j.id) > 1"
        " ORDER BY job_count DESC, r.content_hash"
    ).fetchall()

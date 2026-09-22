"""Re-derive stored posting bodies after a SOURCE-CAPABILITY change.

WHY THIS FILE EXISTS
--------------------
On 2026-09-07 the owner made Get on Board's first live retrieval. 409 postings
were persisted, and every one of them was stored with no body -- not because
the feed sent none, but because the adapter had declared that it sent none.

That declaration was honest when it was written and it was wrong. Get on Board
does not hold a posting in one element; it holds `functions`, `description`,
`desirable`, `benefits` and `perks`, the fields its posting form asks an
employer to fill in separately. An adapter reading the key literally named
`description` got a requirements list on its own, which reads exactly like an
excerpt. `getonbrd.compose_description` puts them back together.

So 409 rows in the corpus were metadata-only records of postings whose full
text this machine already held, archived verbatim in `job_provider_payload`.
Re-collecting to fix that would have been 409 requests to somebody else's
server for bytes already on disk.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
`renormalize.py` handles a NORMALISATION change: our rendering of archived html
improved. This handles a CAPABILITY change: our reading of an archived PAYLOAD
improved. Same discipline, one layer further out.

**It is not an observation from the board.** The employer did not touch the
posting and this source was not asked anything. So, exactly as in
`renormalize.py`: no job is closed or reopened, `first_seen_at` and
`last_seen_at` do not move, no board's success or failure state is touched, and
no payload is invented. The only writes are the body, its hash, and the
collection status that follows from having one.

**It opens no socket.** The input is `job_provider_payload`, which is why that
table is kept. `tests/integration/test_rebuild_bodies_is_offline.py` asserts it
by making every transport raise.

WHY IT IS PROVIDER-NEUTRAL
--------------------------
No vendor is named here and no payload path is spelled. The adapter answers
what its own archived resource means, through the same `fetch_posting` the
collector calls, so the body this rebuilds is byte-identical to the body a
fresh collection would store. That is the property that matters: a rebuilt row
and a re-collected row must not be distinguishable, or `rescore` reconstructs
one thing and the corpus holds another.

It is written generically because this will happen again. A source whose
capabilities are read conservatively -- which is the direction this product
always takes -- is a source that will one day be found to publish more than it
was credited with, and the fix should not be a new script each time.

WHAT IT REFUSES TO DO
---------------------
**It never replaces a body with a shorter one, or with nothing.** A rebuild
that emptied a description would be this program deleting an employer's words
because an adapter regressed. Rows whose rebuilt body is empty are counted and
left exactly as they are.

**It only ever reads a payload this provider itself archived.** The join is
`p.provider = j.provider`, so a discovery sighting filed by a second source
cannot supply the body of a job the first source owns.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.providers.base import PostingStub
from career_agent.providers.registry import available_providers
from career_agent.storage.db import transaction
from career_agent.storage.repositories import JobRawRepo, PipelineRunRepo

#: Read in pages. The payloads are large -- Get on Board embeds the whole
#: company record in every one -- and holding 409 of them is fine while
#: holding twenty thousand is not.
_PAGE = 200


@dataclass
class RebuildStats:
    """What the rebuild did. Reported, and stored on `pipeline_run`."""

    provider: str = ""
    jobs_inspected: int = 0
    #: Rows that gained a body they did not have.
    jobs_given_a_body: int = 0
    #: Rows whose rebuilt body differs from the one already stored. Separated
    #: from the line above because gaining a description and having one
    #: corrected are different events and a single counter hides which happened.
    jobs_body_changed: int = 0
    jobs_unchanged: int = 0
    #: Payloads that compose to nothing. Left alone, never emptied.
    jobs_payload_yields_nothing: int = 0
    #: Jobs with no archived payload at all. Nothing to re-derive from.
    jobs_without_payload: int = 0
    errors: int = 0
    elapsed_ms: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "jobs_inspected": self.jobs_inspected,
            "jobs_given_a_body": self.jobs_given_a_body,
            "jobs_body_changed": self.jobs_body_changed,
            "jobs_unchanged": self.jobs_unchanged,
            "jobs_payload_yields_nothing": self.jobs_payload_yields_nothing,
            "jobs_without_payload": self.jobs_without_payload,
            "errors": self.errors,
            "elapsed_ms": self.elapsed_ms,
        }


class UnsupportedProvider(ValueError):
    """A provider whose bodies cannot be rebuilt from an archived payload."""


class RebuildAttemptedARequest(RuntimeError):
    """An adapter tried to fetch during a rebuild. It may not."""


def _no_transport() -> Any:
    """An `HttpFetcher` whose transport refuses every request.

    Structural, not aspirational. The registry hands each adapter a fetcher,
    and an adapter that starts making a detail request per row would turn a
    free backfill into one request per posting -- 409 of them, for bytes
    already on this disk -- while every other test in the suite kept passing.

    A refusing transport makes that impossible rather than discouraged, and it
    costs nothing: no connection is opened to build one.
    """
    import httpx

    from career_agent.net.fetcher import HttpFetcher

    def refuse(request: httpx.Request) -> httpx.Response:
        raise RebuildAttemptedARequest(
            f"a rebuild tried to request {request.url}. The input is the archived "
            "payload; a rebuild that fetches is a collection wearing its name."
        )

    return HttpFetcher(client=httpx.Client(transport=httpx.MockTransport(refuse)))


def _stub_from_row(row: sqlite3.Row, payload: Any) -> PostingStub:
    """The stub `fetch_posting` needs, rebuilt from the row that was stored.

    Only `payload` carries meaning for a rebuild -- an adapter composing a body
    reads the resource, not the title. The rest is filled from the job row so
    that the stub is truthful rather than blank, and so an adapter that does
    consult a stub field gets what collection gave it.
    """
    return PostingStub(
        external_id=str(row["external_id"] or ""),
        title=str(row["title"] or ""),
        url=str(row["url"] or ""),
        location_raw=row["location_raw"],
        department=row["department"],
        posted_at=row["posted_at"],
        description_html=None,
        payload=payload if isinstance(payload, dict) else {},
    )


def rebuild_bodies(
    conn: sqlite3.Connection,
    *,
    provider: str,
    dry_run: bool = False,
) -> RebuildStats:
    """Re-derive every body this provider's archived payloads can produce.

    Idempotent by construction: the second run finds each rebuilt body already
    equal to what the adapter produces and counts it `jobs_unchanged`. That is
    asserted rather than asserted-to-be-true --
    `tests/integration/test_rebuild_bodies.py` runs it twice.
    """
    from career_agent.providers.registry import get_provider

    if provider not in available_providers():
        raise UnsupportedProvider(
            f"no adapter for provider {provider!r}; available: {', '.join(available_providers())}"
        )

    # A fetcher is constructed because the registry's signature asks for one,
    # and it is one that CANNOT FETCH. `fetch_posting` for a list-complete
    # source reads the resource it was handed, so a rebuild needs no transport
    # -- and the way to guarantee that stays true through a future edit is to
    # hand it a transport that raises rather than to promise not to call one.
    adapter = get_provider(provider, _no_transport())
    if not hasattr(adapter, "fetch_posting"):  # pragma: no cover - protocol guarantee
        raise UnsupportedProvider(f"{provider!r} cannot produce a posting body")

    stats = RebuildStats(provider=provider)
    started = time.monotonic()
    raws = JobRawRepo(conn)
    runs = PipelineRunRepo(conn)
    run_id = None if dry_run else runs.start("rebuild-bodies")

    try:
        offset = 0
        while True:
            rows = conn.execute(
                "SELECT j.id AS id, j.external_id AS external_id, j.title AS title,"
                "       j.url AS url, j.location_raw AS location_raw, j.department AS department,"
                "       j.posted_at AS posted_at, j.content_hash AS content_hash,"
                "       p.payload_json AS payload_json,"
                "       r.description_text AS stored_text"
                " FROM job j"
                " LEFT JOIN job_provider_payload p"
                "        ON p.job_id = j.id AND p.provider = j.provider"
                " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
                " WHERE j.provider = ?"
                " ORDER BY j.id LIMIT ? OFFSET ?",
                (provider, _PAGE, offset),
            ).fetchall()
            if not rows:
                break
            offset += len(rows)

            for row in rows:
                stats.jobs_inspected += 1
                try:
                    _rebuild_one(conn, adapter, raws, row, stats, dry_run=dry_run)
                except Exception:  # pragma: no cover - defensive
                    stats.errors += 1

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        if run_id is not None:
            runs.finish(run_id, status=PipelineRunStatus.OK, stats=stats.as_dict(), error=None)
        return stats
    except Exception as exc:  # pragma: no cover - defensive
        if run_id is not None:
            runs.finish(
                run_id, status=PipelineRunStatus.FAILED, stats=stats.as_dict(), error=str(exc)
            )
        raise


def _rebuild_one(
    conn: sqlite3.Connection,
    adapter: Any,
    raws: JobRawRepo,
    row: sqlite3.Row,
    stats: RebuildStats,
    *,
    dry_run: bool,
) -> None:
    payload_json = row["payload_json"]
    if not payload_json:
        stats.jobs_without_payload += 1
        return

    try:
        payload = json.loads(str(payload_json))
    except (TypeError, ValueError):
        stats.errors += 1
        return

    stub = _stub_from_row(row, payload)
    posting = adapter.fetch_posting(None, stub)
    text = posting.description_text or ""
    if not text.strip():
        # The payload composes to nothing. Never write that over a body: an
        # adapter regression must not delete an employer's words.
        stats.jobs_payload_yields_nothing += 1
        return

    stored = row["stored_text"] or ""
    if stored == text:
        stats.jobs_unchanged += 1
        return

    if dry_run:
        if stored:
            stats.jobs_body_changed += 1
        else:
            stats.jobs_given_a_body += 1
        return

    with transaction(conn):
        text_hash = raws.put(text, posting.description_html or "")
        conn.execute(
            "UPDATE job SET content_hash = ?, collection_status = ?, updated_at = ? WHERE id = ?",
            (text_hash, CollectionStatus.NORMALISED.value, _stamp(), row["id"]),
        )
    if stored:
        stats.jobs_body_changed += 1
    else:
        stats.jobs_given_a_body += 1


def _stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

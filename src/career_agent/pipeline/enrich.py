"""Local enrichment for one posting, on demand.

Three gates stand between a job and the local model, and all three are here
rather than in the adapter, because they are product decisions rather than
transport ones.

**Deterministic triage first.** Only a job at or above
`thresholds.local_ai_min_score` may be enriched. The model never sees the
corpus; it sees the handful of postings that already survived scoring. That is
what "use local AI only for jobs that survive deterministic triage" means
operationally.

**Explicit action only.** Nothing calls this on a page load, on a rescore, or
on a schedule. It is reachable from one button and one CLI command.

**An unacceptable answer is not an answer.** Verification happens before
anything is written: quotes that are not in the posting are dropped, and if
what survives is not acceptable the run is retried exactly once and then
recorded as a failure. `LocalEnrichmentCache.put` refuses an unacceptable
enrichment outright, so an invalid answer can never become a cache hit.

The enrichment is stored beside the score and never inside it. No field it
produces reaches `match_score`, `data_confidence` or any gate. The interface
labels it "Local model observation, not part of the score", and that label is
true because there is no code path that would make it false.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from career_agent.local_ai.cache import (
    LocalEnrichmentCache,
    local_cache_key,
    settings_digest,
    text_digest,
)
from career_agent.local_ai.contract import VerifiedEnrichment, json_schema, verify
from career_agent.local_ai.ollama import (
    OllamaCancelled,
    OllamaClient,
    OllamaFailed,
    OllamaInvalidOutput,
    OllamaRefused,
    OllamaSettings,
    OllamaTimedOut,
    OllamaUnavailable,
)
from career_agent.local_ai.prompt import LOCAL_PROMPT_VERSION, PROMPT_DIGEST, build_messages
from career_agent.storage.db import transaction

#: Where accepted local answers are archived. `out/` is already gitignored.
CACHE_ROOT = Path("out") / "local_ai"

#: The longest posting text sent to the model. On a laptop CPU the model reads
#: roughly 25 tokens a second, so the prompt, not the answer, is what grows a
#: reading past its deadline. A quote cut from the first part of a posting is
#: still verified against that part, so nothing unverifiable can pass.
MAX_DESCRIPTION_CHARS = 6000

#: The capability sentence the model is given about the candidate.
#:
#: Deliberately generic and deliberately short. It is not a resume, carries no
#: employer, no contact detail and no compensation figure, and it is safe to
#: appear in a cache file, a log line or a screenshot. A local model is still a
#: model: what goes into a prompt goes into all three.
DEFAULT_PROFILE_SUMMARY = (
    "A business systems and automation professional with several years of experience in "
    "CRM architecture, workflow automation, system integration through REST APIs and "
    "webhooks, iPaaS tooling, operational data modelling and requirements work."
)


class EnrichmentUnavailable(RuntimeError):
    """The local model could not be reached, or refused to run.

    Not a failure of the product. The interface shows the message and every
    other feature keeps working.
    """


class EnrichmentRejected(RuntimeError):
    """The reading was refused or could not be verified. `code` says which:
    `no_such_job`, `below_threshold` or `unverifiable`."""

    def __init__(self, message: str, code: str = "unverifiable") -> None:
        super().__init__(message)
        self.code = code


class EnrichmentFailed(RuntimeError):
    """Ollama answered and could not finish; the message is Ollama's words."""


class EnrichmentModelMissing(EnrichmentUnavailable):
    """Ollama is running, and the configured model is not installed."""


class EnrichmentTimedOut(RuntimeError):
    """The reading did not finish before its total deadline."""


class EnrichmentCancelled(RuntimeError):
    """The person cancelled the reading."""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def enrich_one(
    conn: sqlite3.Connection,
    job_id: str,
    *,
    config_id: str,
    config_version: int,
    state: dict[str, Any] | None = None,
    profile_summary: str = DEFAULT_PROFILE_SUMMARY,
    settings: OllamaSettings | None = None,
    client: OllamaClient | None = None,
    cache: LocalEnrichmentCache | None = None,
    env: dict[str, str] | None = None,
    min_score: int | None = None,
    max_attempts: int = 2,
    should_stop: Any = None,
    deadline: float | None = None,
    on_progress: Any = None,
) -> dict[str, Any]:
    """Enrich one job. Returns the updated Ollama state for the health payload.

    `max_attempts` is 2: one call, and one retry if schema validation or
    verification fails. Never more. A local model that cannot produce a
    verifiable answer in two tries is telling us something, and hiding it
    behind a retry loop would waste the candidate's laptop and the finding.
    """
    import os
    import time

    stop = should_stop or (lambda: False)

    def progress(phase: str, tokens: int = 0) -> None:
        if on_progress is not None:
            on_progress(phase, tokens)

    row = _load_job(conn, job_id)
    if row is None:
        raise EnrichmentRejected(f"no such job: {job_id}", code="no_such_job")

    score = _stored_score(conn, job_id, config_id, config_version)
    if min_score is not None and (score is None or score < min_score):
        raise EnrichmentRejected(
            f"job scores {score if score is not None else 'nothing'} and the local model "
            f"threshold is {min_score}. Deterministic triage runs first.",
            code="below_threshold",
        )

    settings = settings or OllamaSettings.from_env(env if env is not None else dict(os.environ))
    cache = cache or LocalEnrichmentCache(CACHE_ROOT)
    description = (row["description_text"] or "")[:MAX_DESCRIPTION_CHARS]
    if deadline is None:
        deadline = time.monotonic() + settings.timeout_seconds

    key = local_cache_key(
        description_digest=text_digest(description),
        profile_digest=text_digest(profile_summary),
        model=settings.model,
        prompt_version=LOCAL_PROMPT_VERSION,
        prompt_digest=PROMPT_DIGEST,
        settings_digest=settings_digest(settings.digest_material()),
    )

    cached = cache.get(key)
    if cached is not None:
        _persist(conn, row, key, settings, cached, {"cache": "HIT"})
        return _state(state, settings, reachable=True, note="served from local cache")

    owns_client = client is None
    try:
        client = client or OllamaClient(settings)
    except OllamaRefused as exc:
        # A non-loopback URL. This is a refusal, not an outage, and the message
        # must say so -- silently falling back to a remote endpoint is the
        # exact failure this whole package exists to make impossible.
        raise EnrichmentUnavailable(f"refused to call a non-local endpoint: {exc}") from exc

    progress("checking")
    try:
        available = client.health()
    except OllamaUnavailable as exc:
        raise EnrichmentUnavailable(
            "Ollama is not running. Start it with `ollama serve` and try again; "
            "everything else in this application works without it."
        ) from exc

    if stop():
        raise EnrichmentCancelled("the reading was cancelled")
    if not client.is_model_available():
        raise EnrichmentModelMissing(
            f"Ollama is running but the model {settings.model!r} is not installed. "
            f"Install it with `ollama pull {settings.model}`. Installed: {', '.join(available)}"
        )

    messages = build_messages(
        job_title=row["title"] or "",
        company=row["company_name"] or "",
        description=description,
        profile_summary=profile_summary,
    )
    schema = json_schema()

    if stop():
        raise EnrichmentCancelled("the reading was cancelled")
    wanted = settings.model.removesuffix(":latest")
    loaded = any(name.removesuffix(":latest") == wanted for name in client.loaded_models())
    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        if stop():
            raise EnrichmentCancelled("the reading was cancelled")
        if time.monotonic() > deadline:
            raise EnrichmentTimedOut("the local model did not finish in time")
        progress("reading" if loaded else "loading")
        tokens = 0

        def chunk(part: dict[str, Any]) -> None:
            nonlocal tokens
            if (part.get("message") or {}).get("content"):
                tokens += 1
                progress("writing", tokens)

        try:
            enrichment, stats = client.enrich(
                messages, schema=schema, deadline=deadline, should_stop=stop, on_chunk=chunk
            )
        except OllamaInvalidOutput as exc:
            last_error = f"attempt {attempt}: {exc}"
            loaded = True
            continue
        except OllamaCancelled as exc:
            raise EnrichmentCancelled(str(exc)) from exc
        except OllamaTimedOut as exc:
            raise EnrichmentTimedOut(str(exc)) from exc
        except OllamaFailed as exc:
            raise EnrichmentFailed(str(exc)) from exc
        except OllamaUnavailable as exc:
            raise EnrichmentUnavailable(f"Ollama became unreachable mid-request: {exc}") from exc
        loaded = True
        progress("verifying", tokens)

        verified = verify(enrichment, description)
        stats = dict(stats)
        stats.update(
            attempt=attempt,
            cache="MISS",
            verified=verified.verified_count,
            rejected=verified.rejected_count,
        )

        if not verified.is_acceptable:
            last_error = (
                f"attempt {attempt}: {verified.rejected_count} of "
                f"{verified.rejected_count + verified.verified_count} observations cited text "
                f"that is not in the posting"
            )
            continue

        cache.put(key, verified, stats)
        _persist(conn, row, key, settings, verified, stats)
        if owns_client:
            pass  # the client holds no long-lived resource; nothing to close
        return _state(state, settings, reachable=True, note=f"enriched in {attempt} attempt(s)")

    raise EnrichmentRejected(
        f"the local model did not produce a verifiable answer in {max_attempts} attempts. "
        f"Last: {last_error}"
    )


def probe(
    *,
    settings: OllamaSettings | None = None,
    client: OllamaClient | None = None,
    env: dict[str, str] | None = None,
    state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One health check. The only call this module makes that is not enrichment."""
    import os

    settings = settings or OllamaSettings.from_env(env if env is not None else dict(os.environ))
    try:
        client = client or OllamaClient(settings)
        models = client.health()
    except (OllamaUnavailable, OllamaRefused) as exc:
        return _state(state, settings, reachable=False, note=str(exc))
    return _state(
        state, settings, reachable=True, note=f"{len(models)} model(s) installed", models=models
    )


def _state(
    state: dict[str, Any] | None,
    settings: OllamaSettings,
    *,
    reachable: bool,
    note: str,
    models: list[str] | None = None,
) -> dict[str, Any]:
    payload = dict(state or {})
    payload.update(
        configured=True,
        reachable=reachable,
        model=settings.model,
        endpoint=settings.base_url,
        note=note,
        checked_at=_now(),
    )
    if models is not None:
        payload["models"] = models
    return payload


def _load_job(conn: sqlite3.Connection, job_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT j.id, j.title, j.content_hash, c.name AS company_name, r.description_text
        FROM job j
        JOIN company c  ON c.id = j.company_id
        JOIN job_raw r  ON r.content_hash = j.content_hash
        WHERE j.id = ?
        """,
        (job_id,),
    ).fetchone()


def _stored_score(
    conn: sqlite3.Connection, job_id: str, config_id: str, config_version: int
) -> int | None:
    row = conn.execute(
        "SELECT match_score FROM job_match WHERE job_id = ? AND config_id = ? "
        "AND config_version = ?",
        (job_id, config_id, config_version),
    ).fetchone()
    return int(row["match_score"]) if row else None


def _persist(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    key: str,
    settings: OllamaSettings,
    verified: VerifiedEnrichment,
    stats: dict[str, Any],
) -> None:
    from ulid import ULID

    payload = verified.model_dump_json()
    with transaction(conn):
        conn.execute(
            """
            INSERT INTO job_enrichment (
                id, job_id, content_hash, cache_key, model, prompt_version, prompt_digest,
                schema_version, endpoint, verified_count, rejected_count,
                payload_json, stats_json, created_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT (job_id) DO UPDATE SET
                content_hash   = excluded.content_hash,
                cache_key      = excluded.cache_key,
                model          = excluded.model,
                prompt_version = excluded.prompt_version,
                prompt_digest  = excluded.prompt_digest,
                schema_version = excluded.schema_version,
                endpoint       = excluded.endpoint,
                verified_count = excluded.verified_count,
                rejected_count = excluded.rejected_count,
                payload_json   = excluded.payload_json,
                stats_json     = excluded.stats_json,
                created_at     = excluded.created_at
            """,
            (
                str(ULID()),
                row["id"],
                row["content_hash"],
                key,
                settings.model,
                LOCAL_PROMPT_VERSION,
                PROMPT_DIGEST,
                verified.schema_version,
                settings.base_url,
                verified.verified_count,
                verified.rejected_count,
                payload,
                json.dumps(stats, sort_keys=True, ensure_ascii=False, default=str),
                _now(),
            ),
        )

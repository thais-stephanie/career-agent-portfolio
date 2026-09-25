"""One bounded semantic run: choose candidates, ask, gate, store.

NEVER THE WHOLE CORPUS
----------------------
Candidates come from a deterministic, recall-first prefilter:

* the posting is open, has text, is not verified ineligible, is not blocked by
  screening, and is not at a seniority level the person hid;
* it has not already been evaluated for this Search Intent and contract;
* its title or body matches at least one intent item by PROXIMITY (two of
  one item's words within a few words of each other: "revenue ...
  operations"), through the local full-text index. Deliberately wider than
  the scorer's exact phrases, which is the coverage failure semantic matching
  exists to fix, and far narrower than "any intent word appears" (73% of the
  corpus). Measured on the owner's corpus, 2026-09-25: 8,236 of 60,438 open,
  eligible postings (13.6%), keeping every posting a person labelled a fit.

Candidates are ranked by full-text relevance to the intent, then by recency,
and a run takes at most the configured number.

SPENDING
--------
A metered provider has a hard budget per run. Each call in flight reserves
its WORST case (the whole prompt plus the full output ceiling) before it is
sent; the reservation is replaced by the real cost when it returns. So a run
can stop under its budget but never over it. Subscription providers have no
price Career Agent can know; they get a smaller posting cap instead.

FAILURE
-------
A provider that fails produces no evaluation, and no evaluation is exactly "no
semantic evidence": the posting keeps its deterministic score. Three failures
in a row stop the run with the provider's state as the reason.
"""

from __future__ import annotations

import concurrent.futures as cf
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass

from career_agent.clock import new_id
from career_agent.config.search_config import SearchConfig
from career_agent.semantic.contract import (
    SYSTEM_PROMPT,
    AnswerRejected,
    contract_identity,
    parse_answer,
    posting_text,
    user_message,
)
from career_agent.semantic.evidence import to_evidence
from career_agent.semantic.gate import publish
from career_agent.semantic.intent import SearchIntent, search_intent
from career_agent.semantic.providers import Billing, ProviderAnswer, ProviderFailed
from career_agent.semantic.routing import Route
from career_agent.semantic.settings import SemanticSettings
from career_agent.semantic.store import RunStats, SemanticRepo, StoredEvaluation
from career_agent.storage import invalidation
from career_agent.storage.db import transaction

#: Words that carry no meaning on their own inside an intent item.
_STOP = frozenset(
    [
        "and",
        "with",
        "the",
        "for",
        "from",
        "into",
        "of",
        "to",
        "a",
        "an",
        "or",
        "in",
        "on",
        "own",
        "other",
        "our",
        "your",
        "via",
        "per",
    ]
)
#: How close an item's words must sit to count as that item, in tokens.
NEAR_DISTANCE = 6
#: Characters per token, pessimistically, for estimates before a call.
CHARS_PER_TOKEN = 3.0
#: Output tokens a typical answer uses, for the EXPECTED estimate only.
TYPICAL_OUTPUT_TOKENS = 350
CONSECUTIVE_FAILURES_STOP = 3
MIN_DESCRIPTION_CHARS = 200


@dataclass(frozen=True)
class Candidate:
    job_id: str
    content_hash: str
    title: str
    description: str


def _terms(text: str) -> list[str]:
    head = text.split("(")[0]
    return [w for w in re.findall(r"[^\W_]+", head.lower()) if w not in _STOP and len(w) >= 2]


def intent_match_query(intent: SearchIntent) -> str | None:
    """An FTS5 expression: any intent item, any two of its words near each other.

    Adjacent PAIRS rather than every word together: "CRM architecture and data
    model" is found in "HubSpot CRM architecture" and in "own the data model",
    and a four-word proximity would find neither. A one-word item (a tool) is
    that word.
    """
    parts: list[str] = []
    for item in intent.items:
        words = list(dict.fromkeys(_terms(item.text)))[:6]
        if len(words) == 1:
            parts.append(f'"{words[0]}"')
        for left, right in zip(words, words[1:], strict=False):
            parts.append(f'NEAR("{left}" "{right}", {NEAR_DISTANCE})')
    return " OR ".join(dict.fromkeys(parts)) or None


@dataclass
class Selection:
    candidates: list[Candidate]
    #: Every posting that passed the prefilter, before the per-run cap.
    eligible: int
    #: Why nothing could be selected, when that is the case.
    reason: str = ""


def select_candidates(
    conn: sqlite3.Connection,
    config: SearchConfig,
    intent: SearchIntent,
    *,
    limit: int,
) -> Selection:
    if not intent.items:
        return Selection([], 0, "No search intent is configured yet.")
    query = intent_match_query(intent)
    if query is None:
        return Selection([], 0, "No search intent is configured yet.")
    # A stale index still answers for every posting it holds; it only misses
    # the newest, which the next run picks up. A missing one answers nothing.
    if not _index_has_rows(conn):
        return Selection([], 0, "The search index is not built yet. Recalculate Search Fit first.")
    contract = contract_identity()
    done = SemanticRepo(conn).evaluated_hashes(intent.digest, contract)
    hidden = [str(level.value) for level in config.preferences.seniority.excluded]
    hidden_clause = (
        f" AND coalesce(jm.seniority, '') NOT IN ({','.join('?' for _ in hidden)})"
        if hidden
        else ""
    )
    rows = conn.execute(
        "SELECT j.id, j.content_hash, j.title, r.description_text"
        " FROM job_search s"
        " JOIN job j ON j.id = s.job_id"
        " JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ? AND jm.config_version = ?"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE job_search MATCH ? AND j.closed_at IS NULL"
        "   AND jm.eligibility_status <> 'VERIFIED_NOT_ELIGIBLE'"
        "   AND jm.screening_state = 'NOT_BLOCKED'"
        f"  AND length(r.description_text) >= ?{hidden_clause}"
        " ORDER BY bm25(job_search), coalesce(j.posted_at, j.first_seen_at) DESC, j.id",
        (
            str(config.config_id),
            int(config.config_version),
            query,
            MIN_DESCRIPTION_CHARS,
            *hidden,
        ),
    ).fetchall()
    fresh = [r for r in rows if str(r[1]) not in done]
    chosen = [
        Candidate(job_id=str(r[0]), content_hash=str(r[1]), title=str(r[2] or ""), description=r[3])
        for r in fresh[:limit]
    ]
    return Selection(chosen, len(fresh))


def _index_has_rows(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM job_search LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


@dataclass(frozen=True)
class Estimate:
    candidates: int
    eligible: int
    input_tokens: int
    expected_usd: float | None
    worst_usd: float | None
    billing: str


def estimate(route: Route, selection: Selection, intent: SearchIntent) -> Estimate:
    provider = route.provider
    inputs = [
        estimate_tokens(SYSTEM_PROMPT)
        + estimate_tokens(user_message(intent, c.title, posting_text(c.description)))
        for c in selection.candidates
    ]
    if provider is None:
        return Estimate(len(inputs), selection.eligible, sum(inputs), None, None, "NONE")
    ceiling = int(getattr(provider, "max_output_tokens", 2_500))
    expected = [provider.estimate_cost(i, TYPICAL_OUTPUT_TOKENS) for i in inputs]
    worst = [provider.estimate_cost(i, ceiling) for i in inputs]
    known = all(x is not None for x in expected)
    return Estimate(
        candidates=len(inputs),
        eligible=selection.eligible,
        input_tokens=sum(inputs),
        expected_usd=round(sum(x or 0.0 for x in expected), 4) if known else None,
        worst_usd=round(sum(x or 0.0 for x in worst), 4) if known else None,
        billing=provider.capabilities().billing.value,
    )


def run_cap(settings: SemanticSettings, route: Route) -> int:
    if route.provider is None:
        return 0
    if route.provider.capabilities().billing is Billing.SUBSCRIPTION:
        return min(settings.max_jobs_per_run, settings.max_jobs_per_subscription_run)
    return settings.max_jobs_per_run


def run_semantic(
    conn: sqlite3.Connection,
    config: SearchConfig,
    settings: SemanticSettings,
    route: Route,
    *,
    requested: str,
    progress: Callable[[RunStats], None] | None = None,
    cancel: threading.Event | None = None,
) -> RunStats:
    """Evaluate a bounded batch. Returns the run's stats; never raises for a
    provider failure. Marks every evaluated posting for rescoring."""
    intent = search_intent(config)
    stats = RunStats(id=new_id(), mode=settings.mode.value, fallbacks=list(route.fallbacks))
    repo = SemanticRepo(conn)
    provider = route.provider
    if provider is None:
        stats.status = "SKIPPED"
        stats.stop_reason = route.reason or "No semantic provider is available."
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats
    stats.provider = provider.id
    selection = select_candidates(conn, config, intent, limit=run_cap(settings, route))
    stats.candidates = len(selection.candidates)
    plan = estimate(route, selection, intent)
    metered = provider.capabilities().billing is Billing.METERED_API
    stats.budget_usd = settings.budget_per_run_usd if metered else None
    stats.estimated_usd = plan.expected_usd
    with transaction(conn):
        repo.start_run(stats)
    if not selection.candidates:
        stats.status = "DONE"
        stats.stop_reason = selection.reason or "Nothing new to evaluate."
        with transaction(conn):
            repo.finish_run(stats)
        return stats

    contract = contract_identity()
    lock = threading.Lock()
    reserved = [0.0]
    stop = threading.Event()
    failures = [0]
    evaluated: list[str] = []
    ceiling = int(getattr(provider, "max_output_tokens", 2_500))
    workers = max(1, min(provider.capabilities().max_concurrency, 4))
    slots = threading.Semaphore(workers)

    def worst_case(candidate: Candidate) -> float:
        text = SYSTEM_PROMPT + user_message(
            intent, candidate.title, posting_text(candidate.description)
        )
        return provider.estimate_cost(estimate_tokens(text), ceiling) or 0.0

    def ask(candidate: Candidate) -> tuple[Candidate, ProviderAnswer | None, str | None]:
        try:
            answer = provider.evaluate(intent, candidate.title, posting_text(candidate.description))
            return candidate, answer, None
        except ProviderFailed as exc:
            return candidate, None, f"{exc.state.value}: {exc}"
        except Exception as exc:  # noqa: BLE001 - one posting never aborts a run
            return candidate, None, f"ERROR: {type(exc).__name__}"

    def record(candidate: Candidate, answer: ProviderAnswer | None, error: str | None) -> None:
        if answer is None:
            stats.failed += 1
            failures[0] += 1
            if failures[0] >= CONSECUTIVE_FAILURES_STOP:
                stats.stop_reason = f"The provider stopped answering ({error})."
                stop.set()
            return
        failures[0] = 0
        stats.calls += 1
        stats.input_tokens += int(answer.input_tokens or 0)
        stats.output_tokens += int(answer.output_tokens or 0)
        stats.spent_usd += float(answer.cost_usd or 0.0)
        post = posting_text(candidate.description)
        try:
            parsed = parse_answer(answer.raw_text)
        except AnswerRejected:
            # Malformed: no finding, never fit. Not stored, so a later run may
            # ask again; counted so the run says it happened.
            stats.rejected += 1
            return
        published = publish(parsed, intent, post)
        evidence = to_evidence(
            published,
            intent,
            evaluation_id="",
            provider=provider.id,
            model=answer.model,
            contract=contract,
            requested_provider=requested if requested != provider.id else None,
            fallback_reason=(
                "; ".join(f"{f['preferred']}: {f['reason']}" for f in route.fallbacks) or None
            ),
        )
        with transaction(conn):
            repo.save(
                StoredEvaluation(
                    job_id=candidate.job_id,
                    content_hash=candidate.content_hash,
                    evidence=evidence,
                    gate_rejected=dict(published.report.rejected),
                    role_core=parsed.role_core[:300] or None,
                    provider_confidence=parsed.provider_confidence,
                    raw_response=answer.raw_text,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                    cost_usd=answer.cost_usd,
                    latency_ms=answer.latency_ms,
                    run_id=stats.id,
                )
            )
        stats.published += 1
        evaluated.append(candidate.job_id)

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict[cf.Future, float] = {}
        for candidate in selection.candidates:
            if stop.is_set() or (cancel is not None and cancel.is_set()):
                break
            slots.acquire()
            reserve = worst_case(candidate) if metered else 0.0
            with lock:
                if metered and stats.spent_usd + reserved[0] + reserve > (stats.budget_usd or 0.0):
                    stats.stop_reason = "The run's budget was reached."
                    slots.release()
                    stop.set()
                    break
                reserved[0] += reserve
            future = pool.submit(ask, candidate)
            pending[future] = reserve
            future.add_done_callback(lambda _f: slots.release())
            # Results are recorded on THIS thread: the connection is not shared.
            for done in [f for f in pending if f.done()]:
                with lock:
                    reserved[0] -= pending.pop(done)
                record(*done.result())
            if progress is not None:
                progress(stats)
        for done in cf.as_completed(list(pending)):
            with lock:
                reserved[0] -= pending.pop(done)
            record(*done.result())
            if progress is not None:
                progress(stats)

    if cancel is not None and cancel.is_set() and not stats.stop_reason:
        stats.stop_reason = "Cancelled."
    stats.status = "DONE"
    with transaction(conn):
        if evaluated:
            invalidation.request(conn, evaluated)
        repo.finish_run(stats)
    if progress is not None:
        progress(stats)
    return stats

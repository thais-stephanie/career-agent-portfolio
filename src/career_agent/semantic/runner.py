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
from career_agent.semantic.providers import (
    Billing,
    ProviderAnswer,
    ProviderFailed,
    SemanticProvider,
)
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


#: Why a run stopped or selected nothing, as CODES the interface translates.
STOP_NO_PROVIDER = "NO_PROVIDER"
STOP_NO_INTENT = "NO_INTENT"
STOP_NO_INDEX = "NO_INDEX"
STOP_NOTHING_NEW = "NOTHING_NEW"
STOP_BUDGET = "BUDGET"
STOP_PROVIDER = "PROVIDER_STOPPED"
STOP_CANCELLED = "CANCELLED"
STOP_PRICE_UNKNOWN = "PRICE_UNKNOWN"
STOP_ERROR = "ERROR"


@dataclass
class Selection:
    candidates: list[Candidate]
    #: Every posting text that passed the prefilter, before the per-run cap.
    eligible: int
    #: Why nothing could be selected, as a code, when that is the case.
    reason: str = ""


#: How a batch is chosen from the postings the intent's words reach.
#:
#: `relevance` (the default everywhere): full-text relevance to the intent.
#: `priority`: postings a targeted search returned first, then the
#: deterministic Search Fit already computed, then relevance. For a
#: revalidation after the corpus grew, where the question is "which of the new
#: postings deserve a reading first". Neither order changes what is admitted.
ORDERS = ("relevance", "priority")


def select_candidates(
    conn: sqlite3.Connection,
    config: SearchConfig,
    intent: SearchIntent,
    *,
    limit: int,
    order: str = "relevance",
    since: str | None = None,
) -> Selection:
    if not intent.items:
        return Selection([], 0, STOP_NO_INTENT)
    query = intent_match_query(intent)
    if query is None:
        return Selection([], 0, STOP_NO_INTENT)
    # A stale index still answers for every posting it holds; it only misses
    # the newest, which the next run picks up. A missing one answers nothing.
    if not _index_has_rows(conn):
        return Selection([], 0, STOP_NO_INDEX)
    contract = contract_identity()
    done = SemanticRepo(conn).evaluated_hashes(intent.digest, contract)
    hidden = [str(level.value) for level in config.preferences.seniority.excluded]
    hidden_clause = (
        f" AND coalesce(jm.seniority, '') NOT IN ({','.join('?' for _ in hidden)})"
        if hidden
        else ""
    )
    if order not in ORDERS:
        raise ValueError(f"order must be one of {ORDERS}")
    targeted = (
        "EXISTS (SELECT 1 FROM job_retrieval_lane l WHERE l.job_id = j.id AND l.lane = 'targeted')"
    )
    ranking = (
        f"{targeted} DESC, jm.match_score DESC, bm25(job_search)"
        if order == "priority"
        else "bm25(job_search)"
    )
    # Collected after `since`, or returned by a targeted search at any time.
    since_clause = f" AND (j.first_seen_at >= ? OR {targeted})" if since else ""
    # Identities first; the text is read only for the postings chosen.
    rows = conn.execute(
        "SELECT j.id, j.content_hash, j.title"
        " FROM job_search s"
        " JOIN job j ON j.id = s.job_id"
        " JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ? AND jm.config_version = ?"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE job_search MATCH ? AND j.closed_at IS NULL"
        "   AND jm.eligibility_status <> 'VERIFIED_NOT_ELIGIBLE'"
        "   AND jm.screening_state = 'NOT_BLOCKED'"
        f"  AND length(r.description_text) >= ?{hidden_clause}{since_clause}"
        f" ORDER BY {ranking}, coalesce(j.posted_at, j.first_seen_at) DESC, j.id",
        (
            str(config.config_id),
            int(config.config_version),
            query,
            MIN_DESCRIPTION_CHARS,
            *hidden,
            *((since,) if since else ()),
        ),
    ).fetchall()
    # One question per posting TEXT: two jobs sharing a content hash are asked
    # about once, and the answer reaches both.
    fresh: dict[str, tuple[str, str]] = {}
    for job_id, content_hash, title in rows:
        key = str(content_hash)
        if key not in done and key not in fresh:
            fresh[key] = (str(job_id), str(title or ""))
    picked = list(fresh.items())[:limit]
    texts = _descriptions(conn, [content_hash for content_hash, _ in picked])
    chosen = [
        Candidate(job_id=job_id, content_hash=content_hash, title=title, description=text)
        for content_hash, (job_id, title) in picked
        if (text := texts.get(content_hash))
    ]
    return Selection(chosen, len(fresh))


def _descriptions(conn: sqlite3.Connection, hashes: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for start in range(0, len(hashes), 500):
        chunk = hashes[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        for content_hash, text in conn.execute(
            f"SELECT content_hash, description_text FROM job_raw WHERE content_hash IN ({marks})",
            chunk,
        ):
            found[str(content_hash)] = str(text or "")
    return found


def jobs_sharing(conn: sqlite3.Connection, content_hash: str) -> list[str]:
    """Every job whose text this is: one evaluation serves all of them."""
    rows = conn.execute("SELECT id FROM job WHERE content_hash = ?", (content_hash,)).fetchall()
    return [str(r[0]) for r in rows]


def _index_has_rows(conn: sqlite3.Connection) -> bool:
    try:
        return conn.execute("SELECT 1 FROM job_search LIMIT 1").fetchone() is not None
    except sqlite3.Error:
        return False


def estimate_tokens(text: str) -> int:
    """A typical count, for the EXPECTED estimate shown before a run."""
    return int(len(text) / CHARS_PER_TOKEN) + 1


#: Tokens a chat template adds around the two messages, generously.
TEMPLATE_OVERHEAD_TOKENS = 64


def ceiling_tokens(text: str) -> int:
    """An UPPER bound, for the reservation a budget relies on.

    Byte-level tokenizers never emit more tokens than the text has UTF-8
    bytes. Characters per token is only an average: for Chinese or Greek a
    posting holds two or three times the tokens the average predicts, and a
    reservation made from it could be exceeded by the real bill.
    """
    return len(text.encode("utf-8")) + TEMPLATE_OVERHEAD_TOKENS


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
    order: str = "relevance",
    since: str | None = None,
) -> RunStats:
    """Evaluate a bounded batch. Returns the run's stats; never raises for a
    provider failure. Marks every evaluated posting for rescoring."""
    intent = search_intent(config)
    stats = RunStats(id=new_id(), mode=settings.mode.value, fallbacks=list(route.fallbacks))
    repo = SemanticRepo(conn)
    provider = route.provider
    if provider is None:
        stats.status = "SKIPPED"
        stats.stop_reason = STOP_NO_PROVIDER
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats
    stats.provider = provider.id
    metered = provider.capabilities().billing is Billing.METERED_API
    if metered and provider.estimate_cost(1, 1) is None:
        # A metered provider with no recorded price cannot be held to a
        # budget, so it is not run at all.
        stats.status = "SKIPPED"
        stats.stop_reason = STOP_PRICE_UNKNOWN
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats
    selection = select_candidates(
        conn, config, intent, limit=run_cap(settings, route), order=order, since=since
    )
    stats.candidates = len(selection.candidates)
    plan = estimate(route, selection, intent)
    stats.budget_usd = settings.budget_per_run_usd if metered else None
    stats.estimated_usd = plan.expected_usd
    with transaction(conn):
        repo.start_run(stats)
    if not selection.candidates:
        stats.status = "DONE"
        stats.stop_reason = selection.reason or STOP_NOTHING_NEW
        with transaction(conn):
            repo.finish_run(stats)
        return stats
    try:
        _evaluate(
            conn,
            config,
            intent,
            route,
            provider,
            selection,
            stats,
            requested,
            metered,
            progress,
            cancel,
        )
    except BaseException:
        # Interrupted or crashed, it is recorded as what it was: never DONE.
        stats.status = "FAILED"
        stats.stop_reason = stats.stop_reason or STOP_ERROR
        raise
    finally:
        # Whatever happened, the run row is closed: a run that died must not
        # read as running for ever.
        if stats.status == "RUNNING":
            stats.status = "DONE"
        with transaction(conn):
            repo.finish_run(stats)
        if progress is not None:
            progress(stats)
    return stats


def _evaluate(
    conn: sqlite3.Connection,
    config: SearchConfig,
    intent: SearchIntent,
    route: Route,
    provider: SemanticProvider,
    selection: Selection,
    stats: RunStats,
    requested: str,
    metered: bool,
    progress: Callable[[RunStats], None] | None,
    cancel: threading.Event | None,
) -> None:
    repo = SemanticRepo(conn)

    contract = contract_identity()
    lock = threading.Lock()
    reserved = [0.0]
    stop = threading.Event()
    failures = [0]
    ceiling = int(getattr(provider, "max_output_tokens", 2_500))
    workers = max(1, min(provider.capabilities().max_concurrency, 4))
    slots = threading.Semaphore(workers)

    def worst_case(candidate: Candidate) -> float:
        text = SYSTEM_PROMPT + user_message(
            intent, candidate.title, posting_text(candidate.description)
        )
        return provider.estimate_cost(ceiling_tokens(text), ceiling) or 0.0

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
                stats.stop_reason = STOP_PROVIDER
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
        # The evaluation and the marks for every job with this text commit
        # together: an evaluation stored without its rescore request would be
        # skipped by every later run and never reach a score.
        with transaction(conn):
            invalidation.request(conn, jobs_sharing(conn, candidate.content_hash))
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

    with cf.ThreadPoolExecutor(max_workers=workers) as pool:
        pending: dict[cf.Future, float] = {}
        for candidate in selection.candidates:
            if stop.is_set() or (cancel is not None and cancel.is_set()):
                break
            slots.acquire()
            reserve = worst_case(candidate) if metered else 0.0
            with lock:
                if metered and stats.spent_usd + reserved[0] + reserve > (stats.budget_usd or 0.0):
                    stats.stop_reason = STOP_BUDGET
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
        stats.stop_reason = STOP_CANCELLED

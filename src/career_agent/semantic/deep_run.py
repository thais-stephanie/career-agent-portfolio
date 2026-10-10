"""One bounded deep-reading run: choose, show what leaves, ask, verify, store.

WHICH POSTINGS
--------------
Only the main list: open, eligible where the person lives (and to-confirm
locations only when they ask for them), not blocked, not at a hidden level,
not hidden by hand, with a readable advert, and ONE copy per reposted role,
elected the way the list elects it. A run is split in three strata so the
first stage cannot decide alone what the model ever sees:

* `top_local`: the highest local scores;
* `lexical`: the best full-text matches for the person's words;
* `control`: a fixed sample of the REST, ordered by a hash of the job id.
  What the reading raises there is what the local ranking was missing.

Nothing is read twice for the same provider and contract; a review written
for an earlier profile is shown as such and read again only on request.

WHAT LEAVES THE COMPUTER
------------------------
The person's work, tool and other phrases, their preferred levels, the title
and the advert. Their confirmed evidence ONLY when this profile named this
provider in `share_evidence_with`, and then only what is relevant to the
posting: skills and tools, and the experience lines whose words the posting
shares, each under a local id (E1, E2, ...) with contact details, links and
the person's name removed. Never an employer field, never dates (a duration
in years instead), never a CV, never another profile.

STOPPING
--------
A cancel ends the call in flight. A plan limit pauses the run at once. Three
failures in a row stop it. Nothing ever moves to another provider.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.config.search_config import SearchConfig
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimType
from career_agent.match.phrase_forms import STOPWORDS, root
from career_agent.semantic import deep
from career_agent.semantic.intent import Aspect, SearchIntent, search_intent
from career_agent.semantic.providers import (
    Availability,
    Billing,
    ProviderFailed,
    SemanticProvider,
)
from career_agent.semantic.runner import intent_match_query
from career_agent.semantic.store import RunStats, SemanticRepo
from career_agent.storage.db import transaction

MODE = "deep"
MIN_DESCRIPTION_CHARS = 200
CONSECUTIVE_FAILURES_STOP = 3
#: Share of a batch per stratum; `control` takes what is left.
SHARES = {"top_local": 0.4, "lexical": 0.25}
#: Explicit ceilings on what one posting's packet may carry. Skills and tools
#: are a few words each, so all of a typical profile fits; experience is
#: chosen in two parts (`evidence_packet`).
MAX_SKILLS = 80
MAX_MATCHED_EXPERIENCE = 10
MAX_SUMMARY_EXPERIENCE = 10
SUMMARY_CHARS = 120
#: Content words an experience line must share with the posting to count as
#: matched. It only ORDERS the packet: lines that share none still reach it
#: through the summary, because the same work is often described in other words.
MIN_SHARED_ROOTS = 2
CHARS_PER_TOKEN = 3.0

STOP_NOT_SUBSCRIPTION = "DEEP_NEEDS_SUBSCRIPTION"
STOP_LIMIT = "LIMIT_REACHED"
#: The command line could not use the sign-in (logged out, or its token being
#: refreshed by another Claude Code process). Pause: retrying spends nothing
#: useful, and nothing may move to another way of paying.
STOP_SIGN_IN = "SIGN_IN"
STOP_CANCELLED = "CANCELLED"
STOP_PROVIDER = "PROVIDER_STOPPED"
STOP_NOTHING_NEW = "NOTHING_NEW"
STOP_NO_INTENT = "NO_INTENT"

_EXPERIENCE = (
    ClaimType.EMPLOYMENT,
    ClaimType.PROJECT,
    ClaimType.ACHIEVEMENT,
    ClaimType.METRIC,
    ClaimType.EDUCATION,
    ClaimType.CERTIFICATION,
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
#: A year inside free text ("2018 - 2023", "April 2020"). Dates never leave;
#: a duration in years is sent beside the line instead.
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_WORD = re.compile(r"[^\W\d_]+")


@dataclass(frozen=True)
class DeepCandidate:
    job_id: str
    content_hash: str
    title: str
    description: str
    stratum: str
    local_score: int
    band: str


# ---------------------------------------------------------------------------
# the population and the strata
# ---------------------------------------------------------------------------


def _population(
    conn: sqlite3.Connection,
    config: SearchConfig,
    include_unresolved: bool,
    min_chars: int = MIN_DESCRIPTION_CHARS,
) -> list[sqlite3.Row]:
    """The main list's representatives, the way the list elects them."""
    statuses = ["VERIFIED_ELIGIBLE", "LIKELY_ELIGIBLE"] + (
        ["UNRESOLVED"] if include_unresolved else []
    )
    hidden = [str(level.value) for level in config.preferences.seniority.excluded]
    hidden_clause = (
        f" AND coalesce(jm.seniority, '') NOT IN ({','.join('?' for _ in hidden)})"
        if hidden
        else ""
    )
    rows = conn.execute(
        "SELECT j.id, j.content_hash, j.title, j.company_id, jm.match_score, jm.fit_band,"
        "       jm.eligibility_status"
        "  FROM job j"
        "  JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ? AND jm.config_version = ?"
        "  JOIN job_raw r ON r.content_hash = j.content_hash"
        "  LEFT JOIN job_application ja ON ja.job_id = j.id"
        " WHERE j.closed_at IS NULL AND ja.hidden_at IS NULL"
        "   AND jm.screening_state = 'NOT_BLOCKED'"
        f"  AND jm.eligibility_status IN ({','.join('?' for _ in statuses)})"
        f"{hidden_clause}"
        "   AND length(r.description_text) >= ?",
        (
            str(config.config_id),
            int(config.config_version),
            *statuses,
            *hidden,
            min_chars,
        ),
    ).fetchall()
    rank = {"VERIFIED_ELIGIBLE": 0, "LIKELY_ELIGIBLE": 1, "UNRESOLVED": 2}
    elected: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (str(row["company_id"]), str(row["title"]))
        best = elected.get(key)
        mine = (rank[row["eligibility_status"]], -int(row["match_score"]), str(row["id"]))
        if best is None or mine < (
            rank[best["eligibility_status"]],
            -int(best["match_score"]),
            str(best["id"]),
        ):
            elected[key] = row
    return list(elected.values())


@dataclass(frozen=True)
class Coverage:
    """How much of the MAIN LIST a deep reading can reach. Never the catalogue."""

    roles: int
    readable: int

    @property
    def too_short(self) -> int:
        """Roles with no copy whose advert has enough text to read."""
        return self.roles - self.readable


def coverage(
    conn: sqlite3.Connection, config: SearchConfig, include_unresolved: bool = False
) -> tuple[Coverage, list[sqlite3.Row]]:
    readable = _population(conn, config, include_unresolved)
    everything = _population(conn, config, include_unresolved, min_chars=0)
    return Coverage(len(everything), len(readable)), readable


def _lexical_order(conn: sqlite3.Connection, intent: SearchIntent, ids: set[str]) -> list[str]:
    query = intent_match_query(intent)
    if query is None:
        return []
    try:
        rows = conn.execute(
            "SELECT s.job_id FROM job_search s WHERE job_search MATCH ? ORDER BY bm25(job_search)",
            (query,),
        ).fetchall()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows if str(r[0]) in ids]


def _control_key(job_id: str) -> str:
    return hashlib.sha1(job_id.encode("utf-8")).hexdigest()  # noqa: S324 - an order, not security


@dataclass(frozen=True)
class Selection:
    candidates: list[DeepCandidate]
    population: int
    reviewed: int


def select(
    conn: sqlite3.Connection,
    config: SearchConfig,
    *,
    provider_id: str,
    limit: int,
    include_unresolved: bool = False,
    reread_stale: bool = False,
    current_digest: str = "",
    only: set[str] | None = None,
) -> Selection:
    """`only` narrows the main list to named postings (a labelled pilot set);
    it never widens it past the main list."""
    intent = search_intent(config)
    population = _population(conn, config, include_unresolved)
    if only is not None:
        population = [row for row in population if str(row["id"]) in only]
    done = DeepRepo(conn).reviewed(provider_id, deep.contract_identity())
    fresh = [
        row
        for row in population
        if str(row["content_hash"]) not in done
        or (reread_stale and current_digest not in done[str(row["content_hash"])])
    ]
    by_id = {str(row["id"]): row for row in fresh}
    seen_hashes: set[str] = set()
    picked: list[tuple[sqlite3.Row, str]] = []

    def take(rows: list[sqlite3.Row], stratum: str, count: int) -> None:
        for row in rows:
            if count <= 0 or len(picked) >= limit:
                return
            if str(row["content_hash"]) in seen_hashes:
                continue
            seen_hashes.add(str(row["content_hash"]))
            picked.append((row, stratum))
            count -= 1

    top = sorted(fresh, key=lambda r: (-int(r["match_score"]), str(r["id"])))
    take(top, "top_local", round(limit * SHARES["top_local"]))
    lexical = [by_id[i] for i in _lexical_order(conn, intent, set(by_id))]
    take(lexical, "lexical", round(limit * SHARES["lexical"]))
    rest = sorted(fresh, key=lambda r: _control_key(str(r["id"])))
    take(rest, "control", limit - len(picked))
    texts = _descriptions(conn, [str(row["content_hash"]) for row, _ in picked])
    chosen = [
        DeepCandidate(
            job_id=str(row["id"]),
            content_hash=str(row["content_hash"]),
            title=str(row["title"] or ""),
            description=texts.get(str(row["content_hash"]), ""),
            stratum=stratum,
            local_score=int(row["match_score"]),
            band=str(row["fit_band"]),
        )
        for row, stratum in picked
    ]
    return Selection(
        [c for c in chosen if c.description], len(population), len(population) - len(fresh)
    )


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


# ---------------------------------------------------------------------------
# the evidence packet
# ---------------------------------------------------------------------------


def confirmed_claims(conn: sqlite3.Connection) -> list[VerifiedClaim]:
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import candidate_id_of

    candidate = candidate_id_of(conn)
    if not candidate:
        return []
    return [c for c in ClaimRepo(conn).current(candidate) if c.verified]


def person_names(conn: sqlite3.Connection) -> list[str]:
    """Names that must never leave: the candidate's display name, when real."""
    row = conn.execute("SELECT display_name FROM candidate LIMIT 1").fetchone()
    name = str(row[0]).strip() if row and row[0] else ""
    return [part for part in name.split() if len(part) >= 3]


def redact(text: str, names: list[str]) -> str:
    out = _URL.sub("[link removed]", text)
    out = _EMAIL.sub("[contact removed]", out)
    out = _YEAR.sub("[year]", out)
    out = _PHONE.sub("[contact removed]", out)
    for name in names:
        out = re.sub(rf"\b{re.escape(name)}\b", "[name removed]", out, flags=re.IGNORECASE)
    return out[: deep.MAX_TEXT_CHARS]


def _roots(text: str) -> set[str]:
    return {root(w) for w in _WORD.findall(text.casefold()) if w not in STOPWORDS and len(w) > 2}


def _years(claim: VerifiedClaim) -> float | None:
    if not claim.period_start:
        return None
    end = claim.period_end or now_utc()[:7]
    (y1, m1), (y2, m2) = (map(int, claim.period_start.split("-")), map(int, end[:7].split("-")))
    return round(max(0, (y2 - y1) * 12 + (m2 - m1)) / 12, 1)


def evidence_packet(
    claims: list[VerifiedClaim], posting: str, names: list[str]
) -> tuple[list[dict[str, Any]], dict[str, list[Any]]]:
    """What is sent for ONE posting, and the alias map kept at home.

    * every confirmed skill and tool, up to MAX_SKILLS (they are short and
      they are what requirements name);
    * up to MAX_MATCHED_EXPERIENCE experience lines sharing words with the
      posting, most shared first, in full;
    * a SUMMARY of up to MAX_SUMMARY_EXPERIENCE other experience lines, most
      recent first, each cut to SUMMARY_CHARS: a line that describes the same
      work in other words still reaches the reader.

    What is not in the packet was not sent; it is never read as "not true".
    """
    posting_roots = _roots(posting)
    skills = [c for c in claims if c.claim_type in (ClaimType.SKILL, ClaimType.TOOL)][:MAX_SKILLS]
    experience = [c for c in claims if c.claim_type in _EXPERIENCE]
    shared = {
        c.claim_key: len(_roots(c.text + " " + " ".join(c.tools)) & posting_roots)
        for c in experience
    }
    matched = sorted(
        (c for c in experience if shared[c.claim_key] >= MIN_SHARED_ROOTS),
        key=lambda c: (-shared[c.claim_key], c.claim_key),
    )[:MAX_MATCHED_EXPERIENCE]
    chosen = {c.claim_key for c in matched}
    summary = sorted(
        (c for c in experience if c.claim_key not in chosen),
        key=lambda c: (c.period_end or "9999", c.period_start or "", c.claim_key),
        reverse=True,
    )[:MAX_SUMMARY_EXPERIENCE]
    items: list[dict[str, Any]] = []
    sent: dict[str, list[Any]] = {}
    rows = [(c, False) for c in skills + matched] + [(c, True) for c in summary]
    for index, (claim, short) in enumerate(rows, start=1):
        alias = f"E{index}"
        text = redact(claim.text, names)
        item: dict[str, Any] = {
            "id": alias,
            "type": claim.claim_type.value,
            "text": text[:SUMMARY_CHARS] if short else text,
        }
        if short:
            item["summary"] = True
        if claim.tools:
            item["tools"] = [redact(t, names) for t in claim.tools[:8]]
        years = _years(claim)
        if years is not None:
            item["years"] = years
        items.append(item)
        sent[alias] = [claim.claim_key, claim.revision]
    return items, sent


def profile_digest(
    intent: SearchIntent, levels: list[str], claims: list[VerifiedClaim], shared: bool
) -> str:
    canonical = json.dumps(
        [
            intent.digest,
            sorted(levels),
            shared,
            sorted([c.claim_key, c.revision] for c in claims) if shared else [],
        ],
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


def preferred_levels(config: SearchConfig) -> list[str]:
    return [str(level.value) for level in config.preferences.seniority.preferred]


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------


class DeepRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def reviewed(self, provider: str, contract: str) -> dict[str, set[str]]:
        """Content hash -> the profile digests it was reviewed under."""
        found: dict[str, set[str]] = {}
        for content_hash, digest in self.conn.execute(
            "SELECT content_hash, profile_digest FROM deep_review"
            " WHERE provider = ? AND contract = ?",
            (provider, contract),
        ):
            found.setdefault(str(content_hash), set()).add(str(digest))
        return found

    def save(self, row: dict[str, Any]) -> None:
        keys = list(row)
        self.conn.execute(
            f"INSERT INTO deep_review ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
            " ON CONFLICT (content_hash, profile_digest, contract, provider, model) DO UPDATE SET "
            + ", ".join(f"{k} = excluded.{k}" for k in keys if k != "id"),
            [row[k] for k in keys],
        )

    def newest_per_job(self, job_ids: list[str] | None = None) -> list[sqlite3.Row]:
        """The newest review of each posting, whatever text it was made for:
        a review of an EARLIER text is returned too, so it can be shown as
        out of date instead of disappearing or passing as current."""
        if job_ids is None:
            rows = self.conn.execute(
                "SELECT * FROM deep_review ORDER BY created_at ASC, id ASC"
            ).fetchall()
        else:
            rows = []
            for start in range(0, len(job_ids), 500):
                chunk = job_ids[start : start + 500]
                marks = ",".join("?" for _ in chunk)
                rows += self.conn.execute(
                    f"SELECT * FROM deep_review WHERE job_id IN ({marks})"
                    " ORDER BY created_at ASC, id ASC",
                    chunk,
                ).fetchall()
            rows.sort(key=lambda r: (str(r["created_at"]), str(r["id"])))
        newest: dict[str, sqlite3.Row] = {}
        for row in rows:
            newest[str(row["job_id"])] = row
        return list(newest.values())


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Context:
    """Everything one run sends besides the postings, computed once."""

    intent: SearchIntent
    intent_payload: dict[str, Any]
    levels: list[str]
    claims: list[VerifiedClaim]
    names: list[str]
    shared: bool
    digest: str


def context(conn: sqlite3.Connection, config: SearchConfig, share: bool) -> Context:
    intent = search_intent(config)
    claims = confirmed_claims(conn) if share else []
    levels = preferred_levels(config)
    return Context(
        intent=intent,
        intent_payload=intent.for_provider(),
        levels=levels,
        claims=claims,
        names=person_names(conn) if share else [],
        shared=share,
        digest=profile_digest(intent, levels, claims, share),
    )


def request_for(ctx: Context, candidate: DeepCandidate) -> tuple[str, list[dict[str, Any]], dict]:
    posting = deep.posting_text(candidate.description)
    items, sent = evidence_packet(ctx.claims, posting, ctx.names) if ctx.shared else ([], {})
    message = deep.user_message(ctx.intent_payload, ctx.levels, items, candidate.title, posting)
    return message, items, sent


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def run_deep(
    conn: sqlite3.Connection,
    config: SearchConfig,
    provider: SemanticProvider,
    *,
    share: bool,
    limit: int,
    include_unresolved: bool = False,
    reread_stale: bool = False,
    only: set[str] | None = None,
    progress: Callable[[RunStats], None] | None = None,
    cancel: threading.Event | None = None,
) -> RunStats:
    """Read a bounded batch, one posting at a time. Never raises for a provider
    failure; never touches `job_match`, evidence or resumes."""
    stats = RunStats(id=new_id(), mode=MODE, provider=provider.id)
    repo = SemanticRepo(conn)
    if provider.capabilities().billing is not Billing.SUBSCRIPTION:
        stats.status, stats.stop_reason = "SKIPPED", STOP_NOT_SUBSCRIPTION
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats
    ctx = context(conn, config, share)
    if not ctx.intent.of(Aspect.WORK):
        stats.status, stats.stop_reason = "SKIPPED", STOP_NO_INTENT
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats
    selection = select(
        conn,
        config,
        provider_id=provider.id,
        limit=limit,
        include_unresolved=include_unresolved,
        reread_stale=reread_stale,
        current_digest=ctx.digest,
        only=only,
    )
    stats.candidates = len(selection.candidates)
    with transaction(conn):
        repo.start_run(stats)
    contract = deep.contract_identity()
    work_ids = {item.intent_id for item in ctx.intent.of(Aspect.WORK)}
    failures = 0
    try:
        for candidate in selection.candidates:
            if cancel is not None and cancel.is_set():
                stats.stop_reason = STOP_CANCELLED
                break
            message, items, sent = request_for(ctx, candidate)
            try:
                answer = provider.complete(deep.SYSTEM_PROMPT, message, deep.answer_schema())
            except ProviderFailed as exc:
                if cancel is not None and cancel.is_set():
                    stats.stop_reason = STOP_CANCELLED
                    break
                if exc.limit:
                    stats.stop_reason = STOP_LIMIT
                    break
                if exc.state is Availability.SIGN_IN_REQUIRED:
                    stats.stop_reason = STOP_SIGN_IN
                    break
                stats.failed += 1
                failures += 1
                if failures >= CONSECUTIVE_FAILURES_STOP:
                    stats.stop_reason = STOP_PROVIDER
                    break
                continue
            failures = 0
            stats.calls += 1
            stats.input_tokens += int(answer.input_tokens or 0)
            stats.output_tokens += int(answer.output_tokens or 0)
            posting = deep.posting_text(candidate.description)
            try:
                verified = deep.verify(answer.raw_text, posting, work_ids, {i["id"] for i in items})
            except deep.ReviewRejected:
                stats.rejected += 1
                continue
            with transaction(conn):
                DeepRepo(conn).save(
                    {
                        "id": "deep_"
                        + hashlib.sha256(
                            "\x1f".join(
                                (
                                    candidate.content_hash,
                                    ctx.digest,
                                    contract,
                                    provider.id,
                                    answer.model,
                                )
                            ).encode()
                        ).hexdigest()[:24],
                        "job_id": candidate.job_id,
                        "content_hash": candidate.content_hash,
                        "profile_digest": ctx.digest,
                        "contract": contract,
                        "provider": provider.id,
                        "model": answer.model,
                        "stratum": candidate.stratum,
                        "review_json": json.dumps(
                            verified.review, ensure_ascii=False, sort_keys=True
                        ),
                        "gate_json": json.dumps(verified.dropped, sort_keys=True),
                        "sent_json": json.dumps(
                            {"shared": ctx.shared, "evidence": sent}, sort_keys=True
                        ),
                        "raw_response": answer.raw_text[:20_000],
                        "input_tokens": answer.input_tokens,
                        "output_tokens": answer.output_tokens,
                        "latency_ms": answer.latency_ms,
                        "run_id": stats.id,
                        "created_at": now_utc(),
                    }
                )
            stats.published += 1
            if progress is not None:
                progress(stats)
    except BaseException:
        stats.status, stats.stop_reason = "FAILED", stats.stop_reason or "ERROR"
        raise
    finally:
        if stats.status == "RUNNING":
            paused = stats.stop_reason in (STOP_LIMIT, STOP_SIGN_IN)
            stats.status = "PAUSED" if paused else "DONE"
        if not stats.candidates and not stats.stop_reason:
            stats.stop_reason = STOP_NOTHING_NEW
        with transaction(conn):
            repo.finish_run(stats)
        if progress is not None:
            progress(stats)
    return stats

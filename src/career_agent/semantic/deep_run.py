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

A posting is not read again while its reading is CURRENT: the same advert
(content hash) under the same title (the reader is shown both, never the
employer, so a repost shares a reading and another title does not; migration
0058), the same profile digest, the same instructions (contract), the
same provider and, when one was named, the same model. A reading for an
earlier profile or another model is shown as such and the posting can be read
again. An answer that was paid for and rejected is not asked again unless the
person asks for exactly that (`retry_rejected`), because a retry is paid.

WHAT LEAVES THE COMPUTER
------------------------
The person's work, tool and other phrases, their preferred levels, the title
and the advert. Their confirmed evidence ONLY when this profile named this
provider in `share_evidence_with`, and then only what is relevant to the
posting: skills and tools, and the experience lines whose words the posting
shares, each under a local id (E1, E2, ...) with contact details, links and
the person's name removed. Never an employer field, never dates (a duration
in years instead), never a CV, never another profile.

A JOB, IN BATCHES
-----------------
The preview is approved as a JOB (migration 0052): the provider and its exact
model, how many postings, the plan in strata taken in turn, and for a
metered API the most it may spend. It is read in batches of BATCH_SIZE
without asking again; a resume continues the same plan and never asks again
for a posting this job already answered.

SPENDING
--------
A subscription spends plan limits Career Agent cannot price; a plan limit
pauses the job. A metered API runs only at a recorded price for its exact
model and inside the approved budget: before each call the worst case is
reserved against what is left (every byte a token, no cache, the whole
output ceiling, peak rate) and replaced by the cost accounted from the usage
the provider reports. When the next worst case does not fit, or a call's
usage cannot be accounted for, the job pauses.

STOPPING
--------
A cancel ends the call in flight. A pause stops before the next posting.
Three failures in a row pause the job. Nothing ever moves to another
provider or another model.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.config.search_config import SearchConfig
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimType
from career_agent.llm.pricing import ModelPrice, observed_price
from career_agent.match.phrase_forms import STOPWORDS, root
from career_agent.semantic import deep
from career_agent.semantic.intent import Aspect, SearchIntent, search_intent
from career_agent.semantic.providers import (
    Availability,
    Billing,
    ProviderAnswer,
    ProviderFailed,
    SemanticProvider,
)
from career_agent.semantic.runner import ceiling_tokens, intent_match_query
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

#: The postings one batch holds. A job reads batch after batch without asking
#: again; the count the person approved is the only limit on how many.
BATCH_SIZE = 25
#: The answer ceiling for a metered provider. The pilot's answers averaged
#: about 3,300 output tokens; a truncated answer is paid for and rejected, so
#: the ceiling is generous and the budget reserves all of it per call.
DEEP_MAX_OUTPUT_TOKENS = 8_000
#: The output an ESTIMATE assumes per posting: the pilot's mean (Claude Code,
#: 33,026 tokens for 10 postings). Another model's tokenizer and verbosity
#: differ; the measured usage of each call replaces it.
TYPICAL_OUTPUT_TOKENS = 3_300
UNIT_SEPARATOR = chr(31)

#: Neither a subscription nor a metered API (a local model, or nothing).
STOP_UNSUPPORTED = "DEEP_UNSUPPORTED"
#: A metered provider asked to run without a budget the person approved.
STOP_NO_BUDGET = "NO_BUDGET"
#: A metered provider whose exact model has no recorded price.
STOP_NO_PRICE = "NO_PRICE"
#: The next call's worst case does not fit in what is left of the budget.
STOP_BUDGET = "BUDGET_REACHED"
#: A call came back (or may have been billed) without usage that can be
#: accounted for: its whole reservation is counted and the job pauses.
STOP_UNACCOUNTED = "UNACCOUNTED"
#: The provider answered with another model than the one approved.
STOP_MODEL = "MODEL_CHANGED"
#: The profile (search, levels, evidence or consent) changed since approval.
STOP_PROFILE_CHANGED = "PROFILE_CHANGED"
#: The person paused the job; it resumes where it stopped.
STOP_PAUSED = "PAUSED"
STOP_LIMIT = "LIMIT_REACHED"
#: The command line could not use the sign-in (logged out, or its token being
#: refreshed by another Claude Code process). Pause: retrying spends nothing
#: useful, and nothing may move to another way of paying.
STOP_SIGN_IN = "SIGN_IN"
#: The provider refused the key (HTTP 401 or 403) or none is set: the request
#: was rejected before it was processed, so it cost nothing.
STOP_AUTH = "AUTH_FAILED"
STOP_CANCELLED = "CANCELLED"
STOP_PROVIDER = "PROVIDER_STOPPED"
STOP_NOTHING_NEW = "NOTHING_NEW"
STOP_NO_INTENT = "NO_INTENT"
#: Stops a job can be resumed from.
PAUSING = frozenset(
    {
        "LIMIT_REACHED",
        "SIGN_IN",
        "BUDGET_REACHED",
        "UNACCOUNTED",
        "MODEL_CHANGED",
        "PAUSED",
        "PROVIDER_STOPPED",
    }
)

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


def population_rows(
    conn: sqlite3.Connection,
    config: SearchConfig,
    scores: tuple[str, int] | None = None,
) -> list[sqlite3.Row]:
    """Every open, unblocked, unhidden posting the main list could show, once.

    The one read behind every deep-reading count. Each count used to run its
    own copy of this join (up to six per page open, with the advert's length
    read every time): the coverage with and without the age limit, the
    readable and the whole list, and a selection with and without unresolved
    postings. They all differ only by filters cheap in Python, so the rows are
    read once, with the length of the advert and the publication date, and
    `_elect` derives each population from them. Hidden levels are a
    configuration fact and stay in SQL.
    """
    config_id, config_version = scores or (str(config.config_id), int(config.config_version))
    hidden = [str(level.value) for level in config.preferences.seniority.excluded]
    hidden_clause = (
        f" AND coalesce(jm.seniority, '') NOT IN ({','.join('?' for _ in hidden)})"
        if hidden
        else ""
    )
    return conn.execute(
        "SELECT j.id, j.content_hash, j.title, j.company_id, jm.match_score, jm.fit_band,"
        "       jm.eligibility_status, j.posted_at,"
        # The advert's length WITHOUT reading the advert: from the covering
        # index of migration 0054, named so a missing index is an error
        # rather than a 30-second read of every advert's record.
        "       length(r.description_text) AS chars"
        "  FROM job j"
        "  JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ? AND jm.config_version = ?"
        "  JOIN job_raw r INDEXED BY idx_job_raw_chars ON r.content_hash = j.content_hash"
        "  LEFT JOIN job_application ja ON ja.job_id = j.id"
        " WHERE j.closed_at IS NULL AND ja.hidden_at IS NULL"
        # An advert whose own page said it is gone is never read again.
        "   AND NOT EXISTS (SELECT 1 FROM job_availability av"
        "                   WHERE av.job_id = j.id AND av.state = 'REMOVED')"
        "   AND jm.screening_state = 'NOT_BLOCKED'"
        "   AND jm.eligibility_status IN ('VERIFIED_ELIGIBLE', 'LIKELY_ELIGIBLE', 'UNRESOLVED')"
        f"{hidden_clause}",
        (str(config_id), int(config_version), *hidden),
    ).fetchall()


def _elect(
    rows: list[sqlite3.Row],
    include_unresolved: bool,
    min_chars: int,
    age: tuple[int | None, bool] | None,
) -> list[sqlite3.Row]:
    """One population from `population_rows`: the filters, then the election."""
    max_days, undated = age if age is not None else (None, False)
    cutoff = (
        (datetime.now(UTC) - timedelta(days=max_days)).date().isoformat()
        if max_days is not None
        else None
    )
    statuses = {"VERIFIED_ELIGIBLE", "LIKELY_ELIGIBLE"} | (
        {"UNRESOLVED"} if include_unresolved else set()
    )
    rank = {"VERIFIED_ELIGIBLE": 0, "LIKELY_ELIGIBLE": 1, "UNRESOLVED": 2}
    elected: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        # A missing advert is never readable, nor counted (SQL's NULL >= 0).
        if row["eligibility_status"] not in statuses or row["chars"] is None:
            continue
        if int(row["chars"]) < min_chars:
            continue
        if cutoff is not None:
            posted = row["posted_at"]
            # The same comparison SQL made: the date string against the cutoff.
            if posted is None:
                if not undated:
                    continue
            elif str(posted) < cutoff:
                continue
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


def _population(
    conn: sqlite3.Connection,
    config: SearchConfig,
    include_unresolved: bool,
    min_chars: int = MIN_DESCRIPTION_CHARS,
    scores: tuple[str, int] | None = None,
    age: tuple[int | None, bool] | None = None,
    rows: list[sqlite3.Row] | None = None,
) -> list[sqlite3.Row]:
    """The main list's representatives, the way the list elects them.

    `scores` is the (config_id, config_version) of the revision the list is
    SERVING (storage.revisions). Until a recalculation finishes that is the
    previous revision, and the list on screen is built from it; reading only
    the current revision made this panel see zero roles under a list of
    thousands. None keeps the configuration in force (the command line).
    """
    if rows is None:
        rows = population_rows(conn, config, scores)
    return _elect(rows, include_unresolved, min_chars, age)


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
    conn: sqlite3.Connection,
    config: SearchConfig,
    include_unresolved: bool = False,
    scores: tuple[str, int] | None = None,
    age: tuple[int | None, bool] | None = None,
    rows: list[sqlite3.Row] | None = None,
) -> tuple[Coverage, list[sqlite3.Row]]:
    if rows is None:
        rows = population_rows(conn, config, scores)
    readable = _population(conn, config, include_unresolved, scores=scores, age=age, rows=rows)
    everything = _population(
        conn, config, include_unresolved, min_chars=0, scores=scores, age=age, rows=rows
    )
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
    #: Readable postings of the main list without a CURRENT reading by this
    #: provider (nor a rejected answer, unless retrying): how many a job
    #: could ask for.
    available: int = 0
    #: Postings whose current answer was paid for and rejected.
    rejected: int = 0


def read_key(row: sqlite3.Row) -> tuple[str, str]:
    """What a reading is OF: the advert and the title the reader was shown.

    The employer is never sent, so two postings with the same advert and the
    same title get the same question and share one reading (a repost does);
    the same advert under another title is another question (migration 0058).
    """
    return str(row["content_hash"]), str(row["title"] or "")


def interleave(picked: list[tuple[sqlite3.Row, str]]) -> list[tuple[sqlite3.Row, str]]:
    """The plan's order: one of each stratum in turn, so a job paused halfway
    has read a little of every stratum, never only the top local scores."""
    by_stratum: dict[str, list[tuple[sqlite3.Row, str]]] = {}
    for item in picked:
        by_stratum.setdefault(item[1], []).append(item)
    queues = [by_stratum.get(s, []) for s in ("top_local", "lexical", "control")]
    out: list[tuple[sqlite3.Row, str]] = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


def select(
    conn: sqlite3.Connection,
    config: SearchConfig,
    *,
    provider_id: str,
    limit: int,
    include_unresolved: bool = False,
    current_digest: str = "",
    model: str = "",
    retry_rejected: bool = False,
    only: set[str] | None = None,
    scores: tuple[str, int] | None = None,
    age: tuple[int | None, bool] | None = None,
    rows: list[sqlite3.Row] | None = None,
) -> Selection:
    """`only` narrows the main list to named postings (a labelled pilot set);
    it never widens it past the main list. `limit=0` counts and picks nothing,
    so it reads no full-text index and no advert."""
    intent = search_intent(config)
    population = _population(conn, config, include_unresolved, scores=scores, age=age, rows=rows)
    if only is not None:
        population = [row for row in population if str(row["id"]) in only]
    repo = DeepRepo(conn)
    key = (provider_id, deep.contract_identity(), current_digest, model)
    done = repo.reviewed(*key)
    # An answer that was paid for and rejected is asked again only on request.
    refused = repo.rejected(*key)
    pairs = {read_key(row) for row in population}
    skip = done | (set() if retry_rejected else refused)
    fresh = [row for row in population if read_key(row) not in skip]
    by_id = {str(row["id"]): row for row in fresh}
    seen: set[tuple[str, str]] = set()
    picked: list[tuple[sqlite3.Row, str]] = []

    def take(rows: list[sqlite3.Row], stratum: str, count: int) -> None:
        for row in rows:
            if count <= 0 or len(picked) >= limit:
                return
            if read_key(row) in seen:
                continue
            seen.add(read_key(row))
            picked.append((row, stratum))
            count -= 1

    if limit > 0:
        top = sorted(fresh, key=lambda r: (-int(r["match_score"]), str(r["id"])))
        take(top, "top_local", round(limit * SHARES["top_local"]))
        lexical = [by_id[i] for i in _lexical_order(conn, intent, set(by_id))]
        take(lexical, "lexical", round(limit * SHARES["lexical"]))
        rest = sorted(fresh, key=lambda r: _control_key(str(r["id"])))
        take(rest, "control", limit - len(picked))
    picked = interleave(picked)
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
        [c for c in chosen if c.description],
        len(population),
        len(population) - len(fresh),
        len(fresh),
        len(refused & pairs),
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

    @staticmethod
    def _current(model: str) -> str:
        # A provider without a named model (a subscription CLI) matches any
        # model it answered with; a named model matches only itself.
        return " AND model = ?" if model else ""

    def resolve_model(self, provider: str, model: str) -> str:
        """The stored model name a configured `model` stands for.

        Claude Code is configured with an ALIAS (`sonnet`) and every answer is
        stored under the full name that answered (`claude-sonnet-5-5`), so the
        alias matched nothing and a new job planned the owner's 706 readings
        again (2026-10-19). An alias stands for the NEWEST full name of its own
        family this provider has answered with: what an older version of the
        family read is a reading by a different model and stays one. A full
        name, an unknown word, another provider's model and a family nobody has
        answered with yet are returned unchanged and match only themselves.
        """
        from career_agent.semantic.providers.local_cli import (
            CLAUDE_MODEL_ALIASES,
            ClaudeCodeProvider,
        )

        prefix = CLAUDE_MODEL_ALIASES.get(model) if provider == ClaudeCodeProvider.id else None
        if prefix is None:
            return model
        row = self.conn.execute(
            "SELECT model FROM deep_review WHERE provider = ? AND substr(model, 1, ?) = ?"
            " ORDER BY created_at DESC, id DESC LIMIT 1",
            (provider, len(prefix), prefix),
        ).fetchone()
        return str(row[0]) if row is not None else model

    def reviewed(
        self, provider: str, contract: str, digest: str, model: str = ""
    ) -> set[tuple[str, str]]:
        """(advert, title) pairs with a CURRENT reading: this provider, contract,
        profile digest and (when named) model, an alias read as the model it
        answers with (`resolve_model`)."""
        model = self.resolve_model(provider, model)
        return {
            (str(r[0]), str(r[1]))
            for r in self.conn.execute(
                "SELECT content_hash, title FROM deep_review WHERE provider = ? AND contract = ?"
                " AND profile_digest = ?" + self._current(model),
                (provider, contract, digest, *([model] if model else [])),
            )
        }

    def rejected(
        self, provider: str, contract: str, digest: str, model: str = ""
    ) -> set[tuple[str, str]]:
        """(advert, title) pairs whose current answer was paid for and rejected."""
        model = self.resolve_model(provider, model)
        return {
            (str(r[0]), str(r[1]))
            for r in self.conn.execute(
                "SELECT content_hash, title FROM deep_attempt WHERE provider = ? AND contract = ?"
                " AND profile_digest = ? AND outcome = 'REJECTED'" + self._current(model),
                (provider, contract, digest, *([model] if model else [])),
            )
        }

    def answered_in(self, deep_job_id: str) -> set[tuple[str, str]]:
        """(advert, title) pairs this job already has an answer for, kept or not."""
        return {
            (str(r[0]), str(r[1]))
            for r in self.conn.execute(
                "SELECT content_hash, title FROM deep_attempt WHERE deep_job_id = ?"
                " AND outcome IN ('PUBLISHED', 'REJECTED')",
                (deep_job_id,),
            )
        }

    def job(self, deep_job_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM deep_job WHERE id = ?", (deep_job_id,)
        ).fetchone()
        return row

    def last_job(self) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM deep_job ORDER BY created_at DESC, id DESC LIMIT 1"
        ).fetchone()
        return row

    def create_job(self, row: dict[str, Any]) -> None:
        keys = list(row)
        self.conn.execute(
            f"INSERT INTO deep_job ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )

    def update_job(self, deep_job_id: str, status: str, stop_reason: str | None) -> None:
        self.conn.execute(
            "UPDATE deep_job SET status = ?, stop_reason = ?, updated_at = ? WHERE id = ?",
            (status, stop_reason, now_utc(), deep_job_id),
        )

    def set_budget(self, deep_job_id: str, budget_usd: float) -> None:
        with transaction(self.conn):
            self.conn.execute(
                "UPDATE deep_job SET budget_usd = ?, updated_at = ? WHERE id = ?",
                (budget_usd, now_utc(), deep_job_id),
            )

    def spent(self, deep_job_id: str) -> float:
        row = self.conn.execute(
            "SELECT coalesce(sum(cost_usd), 0) FROM deep_attempt WHERE deep_job_id = ?",
            (deep_job_id,),
        ).fetchone()
        return float(row[0])

    def attempts(self, deep_job_id: str) -> dict[str, int]:
        return {
            str(outcome): int(count)
            for outcome, count in self.conn.execute(
                "SELECT outcome, count(*) FROM deep_attempt WHERE deep_job_id = ? GROUP BY outcome",
                (deep_job_id,),
            )
        }

    def save_attempt(self, row: dict[str, Any]) -> None:
        keys = list(row)
        self.conn.execute(
            f"INSERT INTO deep_attempt ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})",
            [row[k] for k in keys],
        )

    def save(self, row: dict[str, Any]) -> None:
        keys = list(row)
        self.conn.execute(
            f"INSERT INTO deep_review ({', '.join(keys)}) VALUES ({', '.join('?' for _ in keys)})"
            " ON CONFLICT (content_hash, title, profile_digest, contract, provider, model)"
            " DO UPDATE SET " + ", ".join(f"{k} = excluded.{k}" for k in keys if k != "id"),
            [row[k] for k in keys],
        )

    def newest_per_job(self, job_ids: list[str] | None = None) -> list[tuple[str, sqlite3.Row]]:
        """Each posting's reading, as `(job_id, review)`.

        The newest review of the posting's CURRENT advert under its CURRENT
        title, whichever posting it was asked for: a repost of the same
        vacancy shares it, exactly as the planner skips it (`read_key`). Else
        the posting's own newest review of an earlier text or title, so it is
        shown as out of date instead of disappearing or passing as current. The
        same advert under another title shares nothing: the reader was shown
        that other title. `None` means every posting that has one.
        """
        if job_ids is None:
            reviews = self.conn.execute("SELECT * FROM deep_review").fetchall()
            hashes = sorted({str(r["content_hash"]) for r in reviews})
            own = sorted({str(r["job_id"]) for r in reviews})
            jobs = {
                str(r[0]): (str(r[1]), str(r[2] or ""))
                for column, values in (("id", own), ("content_hash", hashes))
                for r in self._chunked(
                    f"SELECT id, content_hash, title FROM job WHERE {column} IN", values
                )
            }
        else:
            jobs = {
                str(r[0]): (str(r[1]), str(r[2] or ""))
                for r in self._chunked(
                    "SELECT id, content_hash, title FROM job WHERE id IN", job_ids
                )
            }
            hashes = sorted({h for h, _ in jobs.values()})
            reviews = list(self._chunked("SELECT * FROM deep_review WHERE job_id IN", job_ids))
            seen = {str(r["id"]) for r in reviews}
            reviews += [
                r
                for r in self._chunked("SELECT * FROM deep_review WHERE content_hash IN", hashes)
                if str(r["id"]) not in seen
            ]
        reviews.sort(key=lambda r: (str(r["created_at"]), str(r["id"])))
        by_read: dict[tuple[str, str], sqlite3.Row] = {}
        by_job: dict[str, sqlite3.Row] = {}
        for row in reviews:
            by_read[(str(row["content_hash"]), str(row["title"]))] = row
            by_job[str(row["job_id"])] = row
        out = []
        for job_id in job_ids if job_ids is not None else sorted(jobs):
            found = by_read.get(jobs[job_id]) if job_id in jobs else None
            found = found or by_job.get(job_id)
            if found is not None:
                out.append((job_id, found))
        return out

    def _chunked(self, select: str, values: list[str]) -> list[sqlite3.Row]:
        rows: list[sqlite3.Row] = []
        for start in range(0, len(values), 500):
            chunk = values[start : start + 500]
            rows += self.conn.execute(
                f"{select} ({','.join('?' for _ in chunk)})", chunk
            ).fetchall()
        return rows


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


@dataclass(frozen=True)
class PacketNotes:
    """What did NOT reach the reader for one posting, so it is said, never
    silent: characters of the advert past MAX_POSTING_CHARS, confirmed skills
    past MAX_SKILLS, experience lines in neither the matched nor the summary
    part, and summary lines cut to SUMMARY_CHARS."""

    posting_chars_cut: int = 0
    skills_left_out: int = 0
    experience_left_out: int = 0
    experience_shortened: int = 0


def request_for(
    ctx: Context, candidate: DeepCandidate
) -> tuple[str, list[dict[str, Any]], dict, PacketNotes]:
    posting = deep.posting_text(candidate.description)
    items, sent = evidence_packet(ctx.claims, posting, ctx.names) if ctx.shared else ([], {})
    skill_types = {ClaimType.SKILL.value, ClaimType.TOOL.value}
    skills_sent = sum(1 for i in items if i["type"] in skill_types)
    skills_all = sum(1 for c in ctx.claims if c.claim_type in (ClaimType.SKILL, ClaimType.TOOL))
    experience_all = sum(1 for c in ctx.claims if c.claim_type in _EXPERIENCE)
    notes = PacketNotes(
        posting_chars_cut=max(0, len(candidate.description) - len(posting)),
        skills_left_out=max(0, skills_all - skills_sent) if ctx.shared else 0,
        experience_left_out=max(0, experience_all - (len(items) - skills_sent))
        if ctx.shared
        else 0,
        experience_shortened=sum(1 for i in items if i.get("summary")),
    )
    message = deep.user_message(ctx.intent_payload, ctx.levels, items, candidate.title, posting)
    return message, items, sent, notes


def estimate_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


def system_for(provider: SemanticProvider) -> str:
    """The instructions this provider is sent: with the schema written in when
    it does not hold its answer to one."""
    return deep.system_prompt(with_schema=not getattr(provider, "enforces_schema", True))


# ---------------------------------------------------------------------------
# spending: a metered provider is held to a budget the person approved
# ---------------------------------------------------------------------------


def price_for(provider: SemanticProvider) -> ModelPrice | None:
    """The recorded price of this provider's EXACT model, or nothing. Never a
    neighbour's price and never a guess."""
    model = getattr(provider, "model", None)
    return observed_price(provider.id, model) if isinstance(model, str) else None


def max_output_for(provider: SemanticProvider) -> int:
    return int(getattr(provider, "max_output_tokens", DEEP_MAX_OUTPUT_TOKENS))


def reservation_usd(price: ModelPrice, system: str, message: str, max_output: int) -> float:
    """The WORST case of one call, held before it is sent: every UTF-8 byte of
    the prompt as a token (no tokenizer emits more), none of it from cache,
    and the whole output ceiling, all at the peak rate."""
    tokens_in = ceiling_tokens(system + message)
    return (tokens_in * price.input_per_mtok + max_output * price.output_per_mtok) / 1_000_000


def accounted_usd(price: ModelPrice, answer: ProviderAnswer) -> float | None:
    """What one answered call cost, from the usage the provider REPORTED, at
    the peak rate (off-peak is half, so this can only over-count). None when
    the usage was not reported: that call cannot be accounted for."""
    if answer.input_tokens is None or answer.output_tokens is None:
        return None
    hit = min(max(int(answer.cached_input_tokens or 0), 0), int(answer.input_tokens))
    cached = price.cached_input_per_mtok
    cached = price.input_per_mtok if cached is None else cached
    return (
        hit * cached
        + (int(answer.input_tokens) - hit) * price.input_per_mtok
        + int(answer.output_tokens) * price.output_per_mtok
    ) / 1_000_000


@dataclass(frozen=True)
class Estimate:
    """What a plan would cost, before anything is sent. An ESTIMATE: the
    measured cost of each call is what the provider later reports."""

    postings: int
    input_tokens: int
    input_tokens_ceiling: int
    output_tokens_typical: int
    output_tokens_ceiling: int
    #: USD at the peak rate with no cache, at the typical output length.
    typical_usd: float | None
    #: Half of it: every call off-peak (the cache would lower both).
    off_peak_usd: float | None
    #: Every byte a token and every answer at its ceiling: what the budget
    #: reserves call by call.
    worst_usd: float | None


def estimate(
    provider: SemanticProvider, system: str, messages: list[str], max_output: int
) -> Estimate:
    price = price_for(provider)
    inputs = [estimate_tokens(system + m) for m in messages]
    ceilings = [ceiling_tokens(system + m) for m in messages]
    typical = TYPICAL_OUTPUT_TOKENS * len(messages)
    ceiling_out = max_output * len(messages)
    if price is None:
        return Estimate(
            len(messages), sum(inputs), sum(ceilings), typical, ceiling_out, None, None, None
        )
    typical_usd = (sum(inputs) * price.input_per_mtok + typical * price.output_per_mtok) / 1_000_000
    worst = (sum(ceilings) * price.input_per_mtok + ceiling_out * price.output_per_mtok) / 1_000_000
    return Estimate(
        postings=len(messages),
        input_tokens=sum(inputs),
        input_tokens_ceiling=sum(ceilings),
        output_tokens_typical=typical,
        output_tokens_ceiling=ceiling_out,
        typical_usd=round(typical_usd, 4),
        off_peak_usd=round(typical_usd / 2, 4),
        worst_usd=round(worst, 4),
    )


# ---------------------------------------------------------------------------
# the job: an approved plan, read in batches, paused and resumed
# ---------------------------------------------------------------------------


@dataclass
class JobProgress:
    deep_job_id: str
    requested: int
    done: int
    spent_usd: float
    budget_usd: float | None
    batch: int
    batches: int


def _plan(candidates: list[DeepCandidate]) -> str:
    return json.dumps(
        [[c.job_id, c.content_hash, c.stratum] for c in candidates], separators=(",", ":")
    )


def _candidates_from_plan(conn: sqlite3.Connection, plan: list[list[str]]) -> list[DeepCandidate]:
    texts = _descriptions(conn, [h for _job, h, _s in plan])
    rows = (
        {
            str(r["id"]): r
            for r in conn.execute(
                f"SELECT j.id, j.title FROM job j WHERE j.id IN ({','.join('?' for _ in plan)})",
                [job for job, _h, _s in plan],
            )
        }
        if plan
        else {}
    )
    out = []
    for job, content_hash, stratum in plan:
        row = rows.get(job)
        if row is None or not texts.get(content_hash):
            continue
        out.append(
            DeepCandidate(
                job_id=job,
                content_hash=content_hash,
                title=str(row["title"] or ""),
                description=texts[content_hash],
                stratum=stratum,
                local_score=0,
                band="",
            )
        )
    return out


def run_deep(
    conn: sqlite3.Connection,
    config: SearchConfig,
    provider: SemanticProvider,
    *,
    share: bool,
    limit: int,
    include_unresolved: bool = False,
    retry_rejected: bool = False,
    only: set[str] | None = None,
    budget_usd: float | None = None,
    resume_job: str | None = None,
    progress: Callable[[RunStats], None] | None = None,
    job_progress: Callable[[JobProgress], None] | None = None,
    cancel: threading.Event | None = None,
    pause: threading.Event | None = None,
    scores: tuple[str, int] | None = None,
    age: tuple[int | None, bool] | None = None,
) -> RunStats:
    """Read an approved plan in batches, one posting at a time, until it is
    done, paused or cancelled. Never raises for a provider failure; never
    touches `job_match`, evidence or resumes.

    A metered provider runs only with a budget, at a recorded price for its
    exact model; before each call the worst case of that call is reserved
    against what is left, and the run PAUSES rather than overspend, or when
    a call's usage cannot be accounted for. `resume_job` continues a paused
    job with the same provider and model, from where it stopped."""
    stats = RunStats(id=new_id(), mode=MODE, provider=provider.id)
    repo = SemanticRepo(conn)
    deep_repo = DeepRepo(conn)
    billing = provider.capabilities().billing
    metered = billing is Billing.METERED_API
    price = price_for(provider) if metered else None

    def skipped(reason: str) -> RunStats:
        stats.status, stats.stop_reason = "SKIPPED", reason
        with transaction(conn):
            repo.start_run(stats)
            repo.finish_run(stats)
        return stats

    if billing not in (Billing.SUBSCRIPTION, Billing.METERED_API):
        return skipped(STOP_UNSUPPORTED)
    ctx = context(conn, config, share)
    if not ctx.intent.of(Aspect.WORK):
        return skipped(STOP_NO_INTENT)
    contract = deep.contract_identity()
    model = str(getattr(provider, "model", "") or "")
    if resume_job is not None:
        job = deep_repo.job(resume_job)
        if job is None:
            return skipped(STOP_NOTHING_NEW)
        if (str(job["provider"]), str(job["model"])) != (provider.id, model):
            # Never another provider and never another model of the same one.
            return skipped(STOP_MODEL)
        if str(job["profile_digest"]) != ctx.digest or bool(job["share_evidence"]) != share:
            return skipped(STOP_PROFILE_CHANGED)
        deep_job_id = str(job["id"])
        if budget_usd is None:
            budget_usd = job["budget_usd"]
        elif metered:
            deep_repo.set_budget(deep_job_id, budget_usd)
        plan = json.loads(str(job["plan_json"]))
        requested = int(job["requested"])
        if metered and price is None:
            return skipped(STOP_NO_PRICE)
    else:
        if metered and price is None:
            return skipped(STOP_NO_PRICE)
        if metered and (budget_usd is None or budget_usd <= 0):
            return skipped(STOP_NO_BUDGET)
        selection = select(
            conn,
            config,
            provider_id=provider.id,
            limit=limit,
            include_unresolved=include_unresolved,
            current_digest=ctx.digest,
            model=model,
            retry_rejected=retry_rejected,
            only=only,
            scores=scores,
            age=age,
        )
        candidates = selection.candidates
        deep_job_id = "djob_" + new_id()
        requested = len(candidates)
        plan = json.loads(_plan(candidates))
        with transaction(conn):
            deep_repo.create_job(
                {
                    "id": deep_job_id,
                    "provider": provider.id,
                    "model": model,
                    "billing": billing.value,
                    "contract": contract,
                    "profile_digest": ctx.digest,
                    "share_evidence": int(share),
                    "include_unresolved": int(include_unresolved),
                    "requested": requested,
                    "budget_usd": budget_usd if metered else None,
                    "plan_json": _plan(candidates),
                    "status": "RUNNING",
                    "stop_reason": None,
                    "created_at": now_utc(),
                    "updated_at": now_utc(),
                }
            )
    # The plan already left out what the person did not ask to retry; a
    # resume skips what is current now and what this job already answered.
    finished = deep_repo.reviewed(provider.id, contract, ctx.digest, model) | (
        deep_repo.answered_in(deep_job_id)
    )
    pending = [
        c for c in _candidates_from_plan(conn, plan) if (c.content_hash, c.title) not in finished
    ]
    stats.candidates = len(pending)
    stats.budget_usd = budget_usd if metered else None
    spent = deep_repo.spent(deep_job_id)
    stats.spent_usd = spent
    done = requested - len(pending)
    batches = max(1, -(-requested // BATCH_SIZE))
    with transaction(conn):
        repo.start_run(stats)
        deep_repo.update_job(deep_job_id, "RUNNING", None)
    work_ids = {item.intent_id for item in ctx.intent.of(Aspect.WORK)}
    system = system_for(provider)
    max_output = max_output_for(provider)

    def report() -> None:
        if job_progress is not None:
            job_progress(
                JobProgress(
                    deep_job_id,
                    requested,
                    done,
                    round(spent, 6),
                    budget_usd if metered else None,
                    min(batches, done // BATCH_SIZE + 1),
                    batches,
                )
            )

    def attempt(
        candidate: DeepCandidate, outcome: str, answer: ProviderAnswer | None, cost: float
    ) -> None:
        deep_repo.save_attempt(
            {
                "id": "datt_" + new_id(),
                "deep_job_id": deep_job_id,
                "job_id": candidate.job_id,
                "content_hash": candidate.content_hash,
                "title": candidate.title,
                "provider": provider.id,
                "model": answer.model if answer is not None else model,
                "contract": contract,
                "profile_digest": ctx.digest,
                "outcome": outcome,
                "input_tokens": answer.input_tokens if answer is not None else None,
                "cached_input_tokens": answer.cached_input_tokens if answer is not None else None,
                "output_tokens": answer.output_tokens if answer is not None else None,
                "cost_usd": cost,
                "run_id": stats.id,
                "created_at": now_utc(),
            }
        )

    failures = 0
    report()
    try:
        for candidate in pending:
            if cancel is not None and cancel.is_set():
                stats.stop_reason = STOP_CANCELLED
                break
            if pause is not None and pause.is_set():
                stats.stop_reason = STOP_PAUSED
                break
            message, items, sent, _notes = request_for(ctx, candidate)
            reserve = 0.0
            if metered and price is not None:
                reserve = reservation_usd(price, system, message, max_output)
                if spent + reserve > float(budget_usd or 0.0):
                    stats.stop_reason = STOP_BUDGET
                    break
            try:
                answer = provider.complete(system, message, deep.answer_schema())
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
                if exc.state is Availability.KEY_MISSING and not exc.billed:
                    stats.stop_reason = STOP_AUTH
                    break
                if metered and (exc.state is Availability.CONNECTION_FAILED or exc.billed):
                    # The request may have reached the provider and been
                    # billed: count the whole reservation and stop.
                    spent += reserve
                    with transaction(conn):
                        attempt(candidate, "UNKNOWN", None, reserve)
                    stats.spent_usd = spent
                    stats.stop_reason = STOP_UNACCOUNTED
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
            cost = 0.0
            unaccounted = False
            if metered and price is not None:
                measured = accounted_usd(price, answer)
                unaccounted = measured is None
                cost = reserve if measured is None else measured
                spent += cost
                stats.spent_usd = spent
            if metered and model and answer.model != model:
                with transaction(conn):
                    attempt(candidate, "MODEL_MISMATCH", answer, cost)
                stats.stop_reason = STOP_MODEL
                break
            posting = deep.posting_text(candidate.description)
            try:
                verified = deep.verify(answer.raw_text, posting, work_ids, {i["id"] for i in items})
            except deep.ReviewRejected:
                stats.rejected += 1
                with transaction(conn):
                    attempt(candidate, "REJECTED", answer, cost)
                done += 1
                report()
                if unaccounted:
                    stats.stop_reason = STOP_UNACCOUNTED
                    break
                continue
            with transaction(conn):
                deep_repo.save(
                    {
                        "id": "deep_"
                        + hashlib.sha256(
                            UNIT_SEPARATOR.join(
                                (
                                    candidate.content_hash,
                                    candidate.title,
                                    ctx.digest,
                                    contract,
                                    provider.id,
                                    answer.model,
                                )
                            ).encode()
                        ).hexdigest()[:24],
                        "job_id": candidate.job_id,
                        "content_hash": candidate.content_hash,
                        "title": candidate.title,
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
                attempt(candidate, "PUBLISHED", answer, cost)
            stats.published += 1
            done += 1
            if done % BATCH_SIZE == 0:
                # A batch ends: the job is checkpointed and the next batch
                # starts at once, within the approved count and budget.
                with transaction(conn):
                    deep_repo.update_job(deep_job_id, "RUNNING", None)
            report()
            if progress is not None:
                progress(stats)
            if unaccounted:
                stats.stop_reason = STOP_UNACCOUNTED
                break
    except BaseException:
        stats.status, stats.stop_reason = "FAILED", stats.stop_reason or "ERROR"
        with transaction(conn):
            deep_repo.update_job(deep_job_id, "PAUSED", stats.stop_reason)
        raise
    finally:
        if stats.status == "RUNNING":
            stats.status = "PAUSED" if stats.stop_reason in PAUSING else "DONE"
        if not stats.candidates and not stats.stop_reason:
            stats.stop_reason = STOP_NOTHING_NEW
        if stats.status != "FAILED":
            job_status = (
                "CANCELLED"
                if stats.stop_reason == STOP_CANCELLED
                else "PAUSED"
                if stats.status == "PAUSED"
                else "DONE"
            )
            with transaction(conn):
                deep_repo.update_job(deep_job_id, job_status, stats.stop_reason)
        with transaction(conn):
            repo.finish_run(stats)
        report()
        if progress is not None:
            progress(stats)
    return stats

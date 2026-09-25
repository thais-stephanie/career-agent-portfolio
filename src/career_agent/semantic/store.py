"""Where published semantic evaluations and runs are kept."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field

from career_agent.clock import now_utc
from career_agent.domain.matching import SemanticEvidence
from career_agent.storage.mvp_repo import semantic_from_dict, semantic_to_dict

#: The provider's own words are kept for audit, never as evidence. Capped so a
#: runaway answer cannot bloat the database.
RAW_RESPONSE_CAP = 20_000


def evaluation_id(
    content_hash: str, intent_digest: str, contract: str, provider: str, model: str
) -> str:
    canonical = "\x1f".join((content_hash, intent_digest, contract, provider, model))
    return "sem_" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


@dataclass
class StoredEvaluation:
    job_id: str
    content_hash: str
    evidence: SemanticEvidence
    gate_rejected: dict[str, int]
    role_core: str | None
    provider_confidence: float | None
    raw_response: str | None
    input_tokens: int | None
    output_tokens: int | None
    cost_usd: float | None
    latency_ms: int | None
    run_id: str | None


@dataclass
class RunStats:
    id: str
    mode: str
    provider: str | None = None
    status: str = "RUNNING"
    stop_reason: str | None = None
    candidates: int = 0
    budget_usd: float | None = None
    estimated_usd: float | None = None
    spent_usd: float = 0.0
    calls: int = 0
    cached: int = 0
    failed: int = 0
    rejected: int = 0
    published: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    started_at: str = ""
    finished_at: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "mode": self.mode,
            "provider": self.provider,
            "status": self.status,
            "stop_reason": self.stop_reason,
            "candidates": self.candidates,
            "budget_usd": self.budget_usd,
            "estimated_usd": self.estimated_usd,
            "spent_usd": round(self.spent_usd, 6),
            "calls": self.calls,
            "cached": self.cached,
            "failed": self.failed,
            "rejected": self.rejected,
            "published": self.published,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "fallbacks": list(self.fallbacks),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


class SemanticRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def has(self, content_hash: str, intent_digest: str, contract: str, provider: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM semantic_evaluation WHERE content_hash = ? AND intent_digest = ?"
            " AND contract = ? AND provider = ? LIMIT 1",
            (content_hash, intent_digest, contract, provider),
        ).fetchone()
        return row is not None

    def evaluated_hashes(self, intent_digest: str, contract: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT content_hash FROM semantic_evaluation"
            " WHERE intent_digest = ? AND contract = ?",
            (intent_digest, contract),
        ).fetchall()
        return {str(r[0]) for r in rows}

    def save(self, stored: StoredEvaluation) -> str:
        e = stored.evidence
        row_id = evaluation_id(
            stored.content_hash, e.intent_digest, e.contract, e.provider, e.model
        )
        raw = stored.raw_response[:RAW_RESPONSE_CAP] if stored.raw_response else None
        self.conn.execute(
            "INSERT INTO semantic_evaluation (id, job_id, content_hash, intent_digest, contract,"
            " provider, model, requested_provider, fallback_reason, evidence_json, gate_json,"
            " role_core, provider_confidence, raw_response, input_tokens, output_tokens,"
            " cost_usd, latency_ms, run_id, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT (content_hash, intent_digest, contract, provider, model) DO UPDATE SET"
            " job_id = excluded.job_id, evidence_json = excluded.evidence_json,"
            " gate_json = excluded.gate_json, role_core = excluded.role_core,"
            " provider_confidence = excluded.provider_confidence,"
            " raw_response = excluded.raw_response, input_tokens = excluded.input_tokens,"
            " output_tokens = excluded.output_tokens, cost_usd = excluded.cost_usd,"
            " latency_ms = excluded.latency_ms, run_id = excluded.run_id,"
            " requested_provider = excluded.requested_provider,"
            " fallback_reason = excluded.fallback_reason, created_at = excluded.created_at",
            (
                row_id,
                stored.job_id,
                stored.content_hash,
                e.intent_digest,
                e.contract,
                e.provider,
                e.model,
                e.requested_provider,
                e.fallback_reason,
                json.dumps(semantic_to_dict(_with_id(e, row_id)), sort_keys=True),
                json.dumps(stored.gate_rejected, sort_keys=True),
                stored.role_core,
                stored.provider_confidence,
                raw,
                stored.input_tokens,
                stored.output_tokens,
                stored.cost_usd,
                stored.latency_ms,
                stored.run_id,
                now_utc(),
            ),
        )
        return row_id

    def evidence_for(
        self, content_hashes: Sequence[str], intent_digest: str, contract: str
    ) -> dict[str, SemanticEvidence]:
        """The newest published evaluation per posting text, for this intent."""
        found: dict[str, SemanticEvidence] = {}
        hashes = [h for h in dict.fromkeys(content_hashes) if h]
        for start in range(0, len(hashes), 500):
            chunk = hashes[start : start + 500]
            marks = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                "SELECT content_hash, evidence_json FROM semantic_evaluation"
                f" WHERE intent_digest = ? AND contract = ? AND content_hash IN ({marks})"
                " ORDER BY created_at ASC, id ASC",
                (intent_digest, contract, *chunk),
            ).fetchall()
            for content_hash, evidence_json in rows:
                evidence = semantic_from_dict(json.loads(evidence_json))
                if evidence is not None:
                    found[str(content_hash)] = evidence
        return found

    def jobs_with_evaluations(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT job_id FROM semantic_evaluation").fetchall()
        return [str(r[0]) for r in rows]

    # -- runs -------------------------------------------------------------
    def start_run(self, stats: RunStats) -> None:
        stats.started_at = now_utc()
        self.conn.execute(
            "INSERT INTO semantic_run (id, started_at, mode, provider, status, candidates,"
            " budget_usd, estimated_usd) VALUES (?,?,?,?,?,?,?,?)",
            (
                stats.id,
                stats.started_at,
                stats.mode,
                stats.provider,
                stats.status,
                stats.candidates,
                stats.budget_usd,
                stats.estimated_usd,
            ),
        )

    def finish_run(self, stats: RunStats) -> None:
        stats.finished_at = now_utc()
        self.conn.execute(
            "UPDATE semantic_run SET finished_at = ?, provider = ?, status = ?, stop_reason = ?,"
            " spent_usd = ?, calls = ?, cached = ?, failed = ?, rejected = ?, published = ?,"
            " input_tokens = ?, output_tokens = ?, fallback_json = ? WHERE id = ?",
            (
                stats.finished_at,
                stats.provider,
                stats.status,
                stats.stop_reason,
                stats.spent_usd,
                stats.calls,
                stats.cached,
                stats.failed,
                stats.rejected,
                stats.published,
                stats.input_tokens,
                stats.output_tokens,
                json.dumps(stats.fallbacks),
                stats.id,
            ),
        )

    def last_run(self) -> dict[str, object] | None:
        row = self.conn.execute(
            "SELECT id, started_at, finished_at, mode, provider, status, stop_reason, candidates,"
            " budget_usd, estimated_usd, spent_usd, calls, cached, failed, rejected, published,"
            " input_tokens, output_tokens, fallback_json FROM semantic_run"
            " ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        keys = [
            "id",
            "started_at",
            "finished_at",
            "mode",
            "provider",
            "status",
            "stop_reason",
            "candidates",
            "budget_usd",
            "estimated_usd",
            "spent_usd",
            "calls",
            "cached",
            "failed",
            "rejected",
            "published",
            "input_tokens",
            "output_tokens",
            "fallbacks",
        ]
        data = dict(zip(keys, tuple(row), strict=True))
        data["fallbacks"] = json.loads(str(data["fallbacks"] or "[]"))
        return data


def _with_id(evidence: SemanticEvidence, row_id: str) -> SemanticEvidence:
    from dataclasses import replace

    return replace(evidence, evaluation_id=row_id)

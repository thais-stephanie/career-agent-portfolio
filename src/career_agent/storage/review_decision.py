"""What the person decided about a posting after reading it (migration 0060).

Measurement only: nothing in scoring, ranking, filtering, reading, gating or
retrieval reads this table (a test asserts so). It is the ground truth
`evaluation/ranking.py` measures a ranking against.

KEEP means "worth keeping for consideration or application". It is not
"strong fit", "likely to be hired", "no gaps" or "ideal job": a stretch the
person keeps is a KEEP. MAYBE is its own answer and is never folded into
REJECT.

A row keeps the EXPOSURE beside the decision: what the system showed when the
person decided (score, band, eligibility, deep level and state, versions,
the list's sort and rank). A later rescore never rewrites it. Every exposure
field is optional; what was not known is stored as NULL, never guessed.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from career_agent.clock import now_utc

DECISIONS = ("KEEP", "MAYBE", "REJECT")
#: Why a posting is not a clear KEEP. One primary reason (MAYBE or REJECT);
#: any decision may also carry secondary reasons from the same list.
REASONS = (
    "WRONG_PRIMARY_WORK",
    "MANDATORY_EXPERIENCE_GAP",
    "MANDATORY_TOOL_PLATFORM_GAP",
    "MANDATORY_CREDENTIAL_GAP",
    "LOCATION_OR_WORK_AUTH",
    "COMPENSATION",
    "SENIORITY_OR_SCOPE",
    "WORK_MODEL_OR_SCHEDULE",
    "COMPANY_OR_OPPORTUNITY_QUALITY",
    "NOT_INTERESTED_DESPITE_FIT",
    "DUPLICATE",
    "STALE_OR_CLOSED",
    "INSUFFICIENT_INFORMATION",
)
ORIGINS = ("APP", "IMPORT", "PANEL")
DEEP_STATES = ("current", "evidence", "stale", "unread")
NOTE_MAX = 2_000
#: How the CSV export words them (English; the screen has both languages),
#: and what an import accepts besides the codes.
DECISION_WORDS = {"KEEP": "Keep", "MAYBE": "Maybe", "REJECT": "Reject"}
REASON_WORDS = {
    "WRONG_PRIMARY_WORK": "The main work is not what I want",
    "MANDATORY_EXPERIENCE_GAP": "Missing required experience",
    "MANDATORY_TOOL_PLATFORM_GAP": "Missing a required tool or platform",
    "MANDATORY_CREDENTIAL_GAP": "Missing a required credential",
    "LOCATION_OR_WORK_AUTH": "Location or work authorization",
    "COMPENSATION": "Pay",
    "SENIORITY_OR_SCOPE": "Seniority or scope",
    "WORK_MODEL_OR_SCHEDULE": "Work model or schedule",
    "COMPANY_OR_OPPORTUNITY_QUALITY": "The company or the opportunity",
    "NOT_INTERESTED_DESPITE_FIT": "Not interested, even though it fits",
    "DUPLICATE": "Duplicate of another posting",
    "STALE_OR_CLOSED": "Old or closed",
    "INSUFFICIENT_INFORMATION": "Not enough information to decide",
}
#: The exposure columns, in table order. All optional.
EXPOSURE = (
    "content_hash",
    "provider",
    "config_id",
    "config_version",
    "match_schema",
    "match_score",
    "fit_band",
    "eligibility_status",
    "deep_level",
    "deep_state",
    "deep_contract",
    "deep_assessment",
    "freshness",
    "served_sort",
    "served_rank",
    "review_lane",
    "served_query",
)
_COLUMNS = (
    "job_id, decision, primary_reason, secondary_reasons_json, note, origin, "
    + ", ".join(EXPOSURE)
    + ", created_at, updated_at"
)


class DecisionRefused(ValueError):
    """The decision is not one this table accepts."""


def _reasons(decision: str, primary: str | None, secondary: Any) -> list[str]:
    if primary is not None and primary not in REASONS:
        raise DecisionRefused(f"unknown reason: {primary!r}")
    if primary is not None and decision == "KEEP":
        raise DecisionRefused("a KEEP takes no primary reason (secondary reasons are allowed)")
    if secondary is None:
        return []
    if not isinstance(secondary, list | tuple) or not all(isinstance(r, str) for r in secondary):
        raise DecisionRefused("secondary reasons must be a list of reason codes")
    unknown = [r for r in secondary if r not in REASONS]
    if unknown:
        raise DecisionRefused(f"unknown reason: {unknown[0]!r}")
    # The primary reason is not repeated, and each reason counts once.
    return [r for i, r in enumerate(secondary) if r != primary and r not in secondary[:i]]


class ReviewDecisionRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(
        self,
        job_id: str,
        *,
        decision: str,
        primary_reason: str | None = None,
        secondary_reasons: list[str] | None = None,
        note: str | None = None,
        origin: str = "APP",
        exposure: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> None:
        if decision not in DECISIONS:
            raise DecisionRefused(f"unknown decision: {decision!r}")
        secondary = _reasons(decision, primary_reason, secondary_reasons)
        if origin not in ORIGINS:
            raise DecisionRefused(f"unknown origin: {origin!r}")
        note = (note or "").strip() or None
        if note is not None and len(note) > NOTE_MAX:
            raise DecisionRefused(f"the note is limited to {NOTE_MAX} characters")
        if self.conn.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is None:
            raise DecisionRefused(f"no such posting: {job_id}")
        seen = {key: (exposure or {}).get(key) for key in EXPOSURE}
        if seen["deep_state"] is not None and seen["deep_state"] not in DEEP_STATES:
            raise DecisionRefused(f"unknown deep state: {seen['deep_state']!r}")
        rank = seen["served_rank"]
        if rank is not None and (isinstance(rank, bool) or not isinstance(rank, int) or rank < 1):
            raise DecisionRefused("served_rank must be a positive whole number")
        stamp = now or now_utc()
        updates = ", ".join(
            f"{c} = excluded.{c}"
            for c in (
                "decision",
                "primary_reason",
                "secondary_reasons_json",
                "note",
                "origin",
                *EXPOSURE,
                "updated_at",
            )
        )
        marks = ",".join("?" for _ in range(8 + len(EXPOSURE)))
        self.conn.execute(
            f"INSERT INTO review_decision ({_COLUMNS}) VALUES ({marks})"
            # A new decision replaces the old; `created_at` stays the first.
            f" ON CONFLICT (job_id) DO UPDATE SET {updates}",
            (
                job_id,
                decision,
                primary_reason,
                json.dumps(secondary),
                note,
                origin,
                *seen.values(),
                stamp,
                stamp,
            ),
        )

    def clear(self, job_id: str) -> None:
        self.conn.execute("DELETE FROM review_decision WHERE job_id = ?", (job_id,))

    def current(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT decision, primary_reason, secondary_reasons_json, note, origin, updated_at"
            " FROM review_decision WHERE job_id = ?",
            (job_id,),
        ).fetchone()
        if row is None:
            return None
        out = dict(row)
        out["secondary_reasons"] = json.loads(out.pop("secondary_reasons_json") or "[]")
        return out

    def all(self) -> list[dict[str, Any]]:
        """Every decision with its exposure, in the order first decided."""
        rows = self.conn.execute(
            f"SELECT {_COLUMNS} FROM review_decision ORDER BY created_at, job_id"
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["secondary_reasons"] = json.loads(item.pop("secondary_reasons_json") or "[]")
            out.append(item)
        return out

    def export_rows(self) -> list[dict[str, Any]]:
        """Every decision, with the public posting facts a reader needs, newest first."""
        rows = self.conn.execute(
            "SELECT d.job_id, j.title, c.name AS company, j.provider, j.url, d.decision,"
            " d.primary_reason, d.secondary_reasons_json, d.note, d.match_score, d.fit_band,"
            " d.eligibility_status, d.deep_level, d.deep_state, d.served_sort, d.served_rank,"
            " d.config_version, d.created_at, d.updated_at FROM review_decision d"
            " LEFT JOIN job j ON j.id = d.job_id LEFT JOIN company c ON c.id = j.company_id"
            " ORDER BY d.updated_at DESC, d.job_id"
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["secondary_reasons"] = json.loads(item.pop("secondary_reasons_json") or "[]")
            out.append(item)
        return out

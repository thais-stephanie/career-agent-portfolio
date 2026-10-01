"""What the person thought of a Search Fit score (migration 0046).

Observation only: nothing in scoring, ranking, filtering or retrieval reads
this table. A row records the score, band and schema version on screen when
the person answered, so a later rescore never rewrites what they judged.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from career_agent.clock import now_utc

VERDICTS = ("ACCURATE", "TOO_HIGH", "TOO_LOW", "NOT_ENOUGH_INFORMATION")
REASONS: dict[str, tuple[str, ...]] = {
    "TOO_HIGH": (
        "WORK_NOT_WANTED",
        "TOOLS_NOT_WORK",
        "SECONDARY_DUTY",
        "ONLY_ASKS_EXPERIENCE",
        "SENIORITY",
        "CONDITIONS",
        "OTHER",
    ),
    "TOO_LOW": (
        "WORK_MATCHES_MORE",
        "WORDING_MISSED",
        "CENTRAL_AS_SECONDARY",
        "TOOLS_MISSED",
        "SENIORITY_FITS",
        "OTHER",
    ),
}
NOTE_MAX = 2_000


class FeedbackRefused(ValueError):
    """The answer is not one this table accepts."""


class ScoreChanged(FeedbackRefused):
    """The score was recalculated after the person saw it."""


class FitFeedbackRepo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def save(
        self,
        job_id: str,
        config_id: str,
        config_version: int,
        *,
        verdict: str,
        reason: str | None = None,
        note: str | None = None,
        seen_score: float | None = None,
        now: str | None = None,
    ) -> None:
        if verdict not in VERDICTS:
            raise FeedbackRefused(f"unknown verdict: {verdict!r}")
        if reason is not None and reason not in REASONS.get(verdict, ()):
            raise FeedbackRefused(f"reason {reason!r} does not go with {verdict}")
        note = (note or "").strip() or None
        if note is not None and len(note) > NOTE_MAX:
            raise FeedbackRefused(f"the note is limited to {NOTE_MAX} characters")
        # The score on screen: the posting's row under this configuration.
        judged = self.conn.execute(
            "SELECT schema_version, match_score, fit_band FROM job_match"
            " WHERE job_id = ? AND config_id = ? AND config_version = ?",
            (job_id, config_id, config_version),
        ).fetchone()
        if judged is None or judged["match_score"] is None:
            raise FeedbackRefused("this posting has no Search Fit score to judge")
        if seen_score is not None and seen_score != judged["match_score"]:
            # Recalculated while the drawer was open: the answer would be about
            # a score the person never saw.
            raise ScoreChanged(
                "Search Fit was recalculated since it was shown. Reopen the posting."
            )
        stamp = now or now_utc()
        self.conn.execute(
            "INSERT INTO search_fit_feedback (job_id, config_id, config_version, schema_version,"
            " match_score, fit_band, verdict, reason, note, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)"
            # The judged score is part of the key: a rescore that moves the
            # number gets a new row and never rewrites what was judged.
            " ON CONFLICT (job_id, config_id, config_version, schema_version, match_score)"
            " DO UPDATE SET fit_band = excluded.fit_band, verdict = excluded.verdict,"
            " reason = excluded.reason, note = excluded.note,"
            " updated_at = excluded.updated_at",
            (
                job_id,
                config_id,
                config_version,
                judged["schema_version"],
                judged["match_score"],
                judged["fit_band"],
                verdict,
                reason,
                note,
                stamp,
                stamp,
            ),
        )

    def current(self, job_id: str, config_id: str, config_version: int) -> dict[str, Any] | None:
        """The answer about the score on screen now, or None."""
        row = self.conn.execute(
            "SELECT f.verdict, f.reason, f.note, f.updated_at FROM search_fit_feedback f"
            " JOIN job_match m ON m.job_id = f.job_id AND m.config_id = f.config_id"
            "  AND m.config_version = f.config_version AND m.schema_version = f.schema_version"
            "  AND m.match_score = f.match_score"
            " WHERE f.job_id = ? AND f.config_id = ? AND f.config_version = ?",
            (job_id, config_id, config_version),
        ).fetchone()
        return dict(row) if row is not None else None

    def export_rows(self) -> list[dict[str, Any]]:
        """Every answer, with the public posting facts a reader needs, newest first."""
        rows = self.conn.execute(
            "SELECT f.job_id, j.title, c.name AS company, j.provider, j.url, f.match_score,"
            " f.fit_band, f.schema_version, f.config_version, f.verdict, f.reason, f.note,"
            " f.created_at, f.updated_at FROM search_fit_feedback f"
            " LEFT JOIN job j ON j.id = f.job_id LEFT JOIN company c ON c.id = j.company_id"
            " ORDER BY f.updated_at DESC, f.job_id"
        ).fetchall()
        return [dict(r) for r in rows]

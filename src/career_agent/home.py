"""Where the job search stands today. Counted, never estimated.

The landing page needs a handful of numbers, and a dashboard's whole risk is
that a number is easier to render than to define. Every figure here has one
definition, written beside it, and answers exactly one question -- because the
failure mode is not a wrong count, it is a plausible one nobody can trace.

TWO KINDS OF NUMBER, AND THEY ARE NEVER MIXED
----------------------------------------------
A STOCK is how many things are in a state right now: four applications are at
interview. An EVENT COUNT is how many times something happened in a window:
two applications moved forward since she last looked. Putting both under one
heading is how "5 interviews" comes to mean either five conversations or five
moves, depending on who wrote the query. Each figure below declares which it
is, and the interface labels them differently.

WHAT "PROGRESSED" MEANS, AND WHY IT IS NOT A SORT ORDER
--------------------------------------------------------
`STATUS_ORDER` lists every status including the terminal ones, so REJECTED
sorts after APPLIED. Counting "later in that order" as progress would report a
week of rejections as a week of progress. `ADVANCEMENT_LADDER` holds the rungs
and nothing else, and `moved_forward` is false for any move touching a status
that is not on it.

NOTHING HERE RANKS ANYTHING
----------------------------
The lists this module returns are filters over the same deterministic score
every other surface uses, assembled from `career_agent.digest`. A dashboard
with its own ordering would be a second opinion nobody could trace, which is
what ADR-0004 refuses.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from career_agent.domain.application import ApplicationStatus, moved_forward


@dataclass(frozen=True, slots=True)
class Metric:
    """One number, and what it is a number OF."""

    key: str
    value: int
    #: `stock` = how many are in this state now. `event` = how many times this
    #: happened since the checkpoint. Never both.
    kind: str
    #: The status filter that reproduces this number in the job list, when one
    #: does. `None` for figures that are not a list of jobs.
    statuses: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "kind": self.kind,
            "statuses": list(self.statuses),
        }


def _count(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0]) if row is not None else 0


def metrics(
    conn: sqlite3.Connection,
    *,
    last_reviewed_at: str | None,
    config_id: str,
    config_version: int,
) -> list[Metric]:
    """The summary cards, each with one definition.

    `last_reviewed_at` is the reader's own checkpoint. When she has never set
    one, the two "since you last looked" figures are reported as zero rather
    than as a guess over some default window -- a number derived from a date
    nobody chose is worse than no number.
    """
    tracked = tuple(
        status.value
        for status in ApplicationStatus
        if status not in {ApplicationStatus.DISCOVERED, ApplicationStatus.ARCHIVED}
    )

    # NEW: postings this machine first held after her checkpoint. `first_seen_at`
    # and never `posted_at` -- the first is when WE noticed, which is the only
    # thing a board publishing no dates lets anybody know.
    new_since = 0
    if last_reviewed_at:
        new_since = _count(
            conn,
            "SELECT COUNT(*) FROM job j"
            " JOIN job_match m ON m.job_id = j.id"
            "  AND m.config_id = ? AND m.config_version = ?"
            " WHERE j.closed_at IS NULL AND j.first_seen_at > ?",
            (config_id, config_version, last_reviewed_at),
        )

    saved = _count(conn, "SELECT COUNT(*) FROM job_application WHERE saved = 1")

    # APPLIED: an application was actually SENT. Either the status is one that
    # cannot be true otherwise, or a date records it -- the same derivation
    # `has_applied` makes, because a second definition would eventually
    # disagree with the first.
    applied = _count(
        conn,
        "SELECT COUNT(*) FROM job_application"
        " WHERE status IN ('APPLIED','INTERVIEW','OFFER','HIRED','WITHDRAWN')"
        "    OR applied_at IS NOT NULL",
    )

    interviews = _count(conn, "SELECT COUNT(*) FROM job_application WHERE status = 'INTERVIEW'")
    offers = _count(conn, "SELECT COUNT(*) FROM job_application WHERE status = 'OFFER'")

    return [
        Metric("new", new_since, "event"),
        Metric("saved", saved, "stock"),
        Metric("applied", applied, "stock", ("APPLIED", "INTERVIEW", "OFFER", "HIRED")),
        Metric("interviews", interviews, "stock", ("INTERVIEW",)),
        Metric("offers", offers, "stock", ("OFFER",)),
        Metric("progressed", progressed(conn, since=last_reviewed_at), "event"),
        Metric(
            "tracking",
            _count(
                conn,
                f"SELECT COUNT(*) FROM job_application WHERE status IN "
                f"({', '.join('?' for _ in tracked)})",
                tracked,
            ),
            "stock",
            tracked,
        ),
    ]


def progressed(conn: sqlite3.Connection, *, since: str | None) -> int:
    """UNIQUE applications that moved UP the ladder since the checkpoint.

    Unique, because an application that went SHORTLISTED -> APPLIED -> INTERVIEW
    in one afternoon progressed once as far as a person is concerned; counting
    two would make a busy afternoon look like two opportunities.

    Zero when there is no checkpoint. A count over an invented window is a
    number nobody asked for, and the card says so instead.
    """
    if not since:
        return 0
    rows = conn.execute(
        "SELECT job_id, from_status, to_status FROM job_application_event WHERE occurred_at > ?",
        (since,),
    ).fetchall()
    return len({str(row[0]) for row in rows if moved_forward(row[1], row[2])})


def profile_gaps(conn: sqlite3.Connection, config: Any) -> list[str]:
    """What the Career Profile is missing, as facts rather than as a score.

    Section 20: no "72% complete". A percentage over a profile invents a
    denominator -- how many facts IS a complete person -- and then reports
    somebody's career as a progress bar. These are the specific things that,
    if absent, make a specific part of the product answer nothing.
    """
    missing: list[str] = []

    confirmed = _count(
        conn,
        "SELECT COUNT(*) FROM verified_claim WHERE superseded_by_id IS NULL AND verified = 1",
    )
    if not confirmed:
        # Without this, every requirement on every posting reads as a gap.
        missing.append("evidence")

    eligibility = getattr(config, "eligibility", None)
    if eligibility is not None:
        if not getattr(eligibility, "candidate_country", ""):
            missing.append("residence")
        if not getattr(eligibility, "eligible_scopes", ()) and not getattr(
            eligibility, "eligible_countries", ()
        ):
            # Without this the geography gate can never PASS: nothing is an
            # acceptable hiring scope, so every posting stays unresolved.
            missing.append("hiring_scopes")

    preferences = getattr(config, "preferences", None)
    if preferences is not None:
        compensation = getattr(preferences, "compensation", None)
        if compensation is not None and not getattr(compensation, "target_monthly_amount", 0):
            missing.append("compensation")

    lexicon = getattr(config, "lexicon", {}) or {}
    if not lexicon:
        missing.append("work")

    return missing

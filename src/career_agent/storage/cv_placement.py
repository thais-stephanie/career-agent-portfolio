"""Which career experience a CV entry's statements belong to, and a repair.

THE DEFECT (2026-09-25, the owner's own workspace)
--------------------------------------------------
The guided review showed each job in a CV as "Already in your profile" when it
matched an experience, but it never RECORDED that match. Confirming a line
created a claim under the proposal's key and linked it nowhere, so 56 confirmed
details sat outside the experiences they were reviewed under, and "Keep them
together" was no longer offered once nothing was waiting.

ONE RULE, used by the review as each answer is given and by the repair for
what was already confirmed:

1. **Entry identity.** Statements from the same `cv_entry` (same read, same
   entry key) that are already linked, all to ONE active experience: that one.
2. **The job itself.** Otherwise, exactly one active experience with the same
   employer AND the same role title, both stated: that one.
3. **Anything else is ambiguous** -- two experiences, no role, siblings split
   between experiences -- and nothing is moved. It is reported instead.

A statement's own words never decide where it goes.

THREE STATES, told apart by the `career_evidence_link` row itself:

    NEVER PLACED          no row for the claim key
    EXPLICITLY UNLINKED   a row whose experience_id is NULL -- the person took
                          it out of an experience (the Experience editor,
                          Remove experience)
    LINKED                a row with an experience

Automatic placement and the repair act on NEVER PLACED statements only. The
other two are the person's decisions and are never changed here.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any

#: The basis a placement was decided on, as the dry-run report says it.
BY_ENTRY = "other statements from this CV entry are already there"
BY_JOB = "same employer and same role title"


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def assign(
    company: str | None,
    role: str | None,
    sibling_targets: set[str],
    experiences: list[dict],
) -> tuple[str | None, str | None, str | None]:
    """(experience id, basis, why not) for one entry. See the module rule."""
    from career_agent.intake.conflicts import normalise_employer

    active = {e["id"] for e in experiences}
    siblings = sibling_targets & active
    if len(siblings) == 1:
        return next(iter(siblings)), BY_ENTRY, None
    if len(siblings) > 1:
        return None, None, "its statements are already split between experiences"
    employer = normalise_employer(company or "")
    title = _norm(role)
    if not employer:
        return None, None, "the CV entry names no employer"
    if not title:
        return None, None, "the CV entry names no role, so the employer alone cannot decide"
    same = [
        e
        for e in experiences
        if normalise_employer(e.get("company") or "") == employer and _norm(e.get("title")) == title
    ]
    if len(same) == 1:
        return same[0]["id"], BY_JOB, None
    if not same:
        return None, None, "no experience in the profile has this employer and role"
    return None, None, "more than one experience has this employer and role"


@dataclass
class EntryRepair:
    """One CV entry: what is confirmed, what is linked, what would be linked."""

    import_id: str
    import_name: str
    entry_key: str
    company: str | None
    role: str | None
    imported: int
    confirmed: int
    linked_here: int = 0
    linked_elsewhere: int = 0
    #: Taken out of an experience by the person: reported, never proposed.
    unlinked_by_person: int = 0
    missing: list[str] = field(default_factory=list)
    experience_id: str | None = None
    experience_label: str | None = None
    basis: str | None = None
    ambiguous: str | None = None

    @property
    def proposed(self) -> list[str]:
        return self.missing if self.experience_id else []


def placement_decided(conn: sqlite3.Connection, candidate_id: str) -> set[str]:
    """Claim keys the person has placed or deliberately unplaced: any link row."""
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT claim_key FROM career_evidence_link WHERE candidate_id = ?", (candidate_id,)
        )
    }


def _experiences(conn: sqlite3.Connection, candidate_id: str) -> list[dict]:
    return [
        dict(row)
        for row in conn.execute(
            "SELECT e.id, e.title, c.label AS company FROM career_experience e"
            " JOIN career_company c ON c.id = e.company_id"
            " WHERE e.candidate_id = ? AND e.archived = 0 ORDER BY e.id",
            (candidate_id,),
        )
    ]


def plan_repair(conn: sqlite3.Connection, candidate_id: str) -> list[EntryRepair]:
    """Read-only: every structured CV entry with confirmed statements."""
    from career_agent.storage.repositories import ClaimRepo

    experiences = _experiences(conn, candidate_id)
    active = {e["id"]: e for e in experiences}
    links = {
        str(r["claim_key"]): r["experience_id"]
        for r in conn.execute(
            "SELECT claim_key, experience_id FROM career_evidence_link WHERE candidate_id = ?",
            (candidate_id,),
        )
    }
    states = ClaimRepo(conn).states(candidate_id)
    reads = conn.execute(
        "SELECT id, source_name FROM cv_import WHERE candidate_id = ?"
        " AND deleted_at IS NULL ORDER BY created_at",
        (candidate_id,),
    ).fetchall()
    plans: list[EntryRepair] = []
    for read in reads:
        entries = conn.execute(
            "SELECT entry_key, company, role_title FROM cv_entry"
            " WHERE import_id = ? ORDER BY rowid",
            (read["id"],),
        ).fetchall()
        for entry in entries:
            rows = conn.execute(
                "SELECT claim_key, decision FROM cv_proposal WHERE import_id = ? AND entry_key = ?",
                (read["id"], entry["entry_key"]),
            ).fetchall()
            confirmed = [
                str(r["claim_key"])
                for r in rows
                if r["decision"] in {"ACCEPTED", "EDITED"}
                and states.get(str(r["claim_key"])) == "CONFIRMED"
            ]
            if not confirmed:
                continue
            report = EntryRepair(
                import_id=str(read["id"]),
                import_name=str(read["source_name"]),
                entry_key=str(entry["entry_key"]),
                company=entry["company"],
                role=entry["role_title"],
                imported=len(rows),
                confirmed=len(confirmed),
            )
            siblings = {
                str(links[str(r["claim_key"])])
                for r in rows
                if links.get(str(r["claim_key"])) in active
            }
            target, basis, why = assign(
                entry["company"], entry["role_title"], siblings, experiences
            )
            report.experience_id, report.basis, report.ambiguous = target, basis, why
            if target:
                chosen = active[target]
                report.experience_label = f"{chosen['company']} / {chosen['title'] or ''}".strip(
                    " /"
                )
            for key in confirmed:
                if key not in links:
                    report.missing.append(key)  # never placed
                elif links[key] is None:
                    report.unlinked_by_person += 1
                elif links[key] == target:
                    report.linked_here += 1
                else:
                    report.linked_elsewhere += 1
            plans.append(report)
    return plans


def apply_repair(conn: sqlite3.Connection, candidate_id: str, plans: list[EntryRepair]) -> int:
    """Link each unambiguous missing statement. Idempotent, recorded, undoable.

    One `move` per experience through `CareerRepo`, so every link is a history
    event that Organization history can undo. Re-running finds nothing to do.
    """
    from career_agent.storage.career_repo import CareerRepo
    from career_agent.storage.db import transaction

    by_target: dict[str, list[str]] = {}
    for plan in plans:
        for key in plan.proposed:
            by_target.setdefault(str(plan.experience_id), []).append(key)
    linked = 0
    for target, keys in by_target.items():
        with transaction(conn):
            repo = CareerRepo(conn, candidate_id)
            command = {"action": "move", "keys": sorted(set(keys)), "experience_id": target}
            repo.apply(command, repo.preview(command)["preview_hash"])
        linked += len(set(keys))
    return linked

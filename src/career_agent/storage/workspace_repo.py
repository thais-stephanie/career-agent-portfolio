"""Storage for the candidate's side of the workspace.

Four aggregates, added in migration 0019, and one thing they share: every row
here is text the CANDIDATE produced or a decision she made. Nothing an employer
published reaches these tables, and nothing in them is ever shown as though an
employer had said it.

The rules that shaped the SQL are written where the SQL is, in
`migrations/0019_application_workspace.sql`. What is worth repeating up here is
the one this module could break on its own:

**A decision never becomes a fact by itself.** `CvReviewRepo.decide` records an
answer; only `accept` -- which is the caller passing the result of
`cv.propose.to_claim` -- creates a claim. `RequirementReviewRepo` creates
nothing at all. If a future edit makes either of these write a
`verified_claim`, the truthfulness guarantee is gone and the tests in
`tests/unit/test_workspace_repo.py` are what should stop it.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from career_agent.clock import new_id, now_utc
from career_agent.storage.repositories import _Repo, sha256_text

#: The four answers a proposal can carry. `PENDING` is the state a proposal is
#: born in, and it is a real state rather than the absence of one: a review
#: that can be resumed has to be able to say how much of it is left.
DECISIONS = frozenset({"PENDING", "ACCEPTED", "EDITED", "REJECTED"})

#: What the candidate can say about a requirement/evidence pair.
VERDICTS = frozenset({"SUPPORTS", "PARTIALLY_SUPPORTS", "DOES_NOT_SUPPORT", "EVIDENCE_MISSING"})

#: Local reading state. Two keys, and the table existed for exactly this: the
#: second one cost no migration.
LAST_REVIEWED_AT = "last_reviewed_at"

#: WHERE THE PERSON SAYS THEY ARE IN THEIR CAREER, from
#: `domain/enums.py::CareerStage`.
#:
#: HERE RATHER THAN IN `search.local.yaml`, and that placement is the decision.
#: Every section of the search configuration changes how a POSTING IS READ, and
#: a stored score is only true relative to the configuration that produced it:
#: adding a key there bumps `config_version` and marks a hundred thousand rows
#: stale. This answer changes no reading of any posting. It changes what the
#: product EXPLAINS and which filters it offers to put in front of somebody,
#: which is presentation.
#:
#: It is CONTEXT, never identity. It is not an ingestion constraint -- nothing
#: in collection, fingerprinting or any gate reads it -- and
#: `PREFER_NOT_TO_SAY` is a real value rather than the absence of one, because
#: "I would rather not answer" and "nobody has asked me yet" are different
#: facts and a first run has to tell them apart.
CAREER_STAGE = "career_stage"


@dataclass(frozen=True, slots=True)
class StagedImport:
    """One CV read, and how far through reviewing it somebody is."""

    import_id: str
    source_name: str
    kind: str
    pages: int | None
    characters: int
    text_sha256: str
    status: str
    created_at: str
    pending: int = 0
    accepted: int = 0
    edited: int = 0
    rejected: int = 0
    #: Put away, reversibly. An archived read is kept whole and counts nowhere.
    archived_at: str | None = None
    #: Jobs the reader found in it, and how many of those it could not fully
    #: read (a company, role or dates it had to leave for the person).
    entries: int = 0

    @property
    def archived(self) -> bool:
        return self.archived_at is not None

    @property
    def total(self) -> int:
        return self.pending + self.accepted + self.edited + self.rejected

    @property
    def confirmed(self) -> int:
        """Proposals that became claims. EDITED is one of them."""
        return self.accepted + self.edited


def candidate_id_of(conn: sqlite3.Connection) -> str | None:
    """The sole candidate, or None if this database has never had one.

    Read-only on purpose. A GET that silently created a candidate row would
    make "nothing has been confirmed about you" indistinguishable from "this
    database has no candidate", and the interface needs to tell those apart.
    """
    row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
    return str(row["id"]) if row is not None else None


def ensure_candidate(conn: sqlite3.Connection) -> str:
    """The sole candidate, created if absent. Personal Alpha is N=1 by design."""
    existing = candidate_id_of(conn)
    if existing is not None:
        return existing
    candidate_id = new_id()
    conn.execute(
        "INSERT INTO candidate (id, candidate_key, display_name, created_at)"
        " VALUES (?, 'owner', 'You', ?)",
        (candidate_id, now_utc()),
    )
    return candidate_id


class CvReviewRepo(_Repo):
    """A staged CV read, its proposals, and the answers given to them."""

    # -- staging -------------------------------------------------------
    def stage(
        self,
        candidate_id: str,
        *,
        source_name: str,
        kind: str,
        pages: int | None,
        characters: int,
        text: str,
        proposals: list,
        entries: list | None = None,
    ) -> str:
        """Persist one read, the jobs it found, and everything it proposed.

        `text` is hashed and discarded. The document is not stored: what a
        review needs is the proposals and the lines they came from, and both
        of those are on the proposal rows. The jobs (`cv/structure.Entry`)
        keep the heading and date lines that stated them, verbatim.
        """
        import_id = new_id()
        stamp = now_utc()
        self.conn.execute(
            "INSERT INTO cv_import"
            " (id, candidate_id, source_name, kind, pages, characters,"
            "  text_sha256, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, ?)",
            (
                import_id,
                candidate_id,
                source_name,
                kind,
                pages,
                characters,
                sha256_text(text),
                stamp,
                stamp,
            ),
        )
        for ordinal, entry in enumerate(entries or [], start=1):
            span = entry.span
            self.conn.execute(
                "INSERT INTO cv_entry"
                " (id, import_id, entry_key, section, company, role_title, period_text,"
                "  period_start, period_end, current_role, start_year, end_year, location,"
                "  label, source_json, unresolved_json, edited, ordinal, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    new_id(),
                    import_id,
                    entry.key,
                    entry.section,
                    entry.company,
                    entry.role,
                    span.text if span else None,
                    span.start if span else None,
                    span.end if span else None,
                    int(bool(span and span.current)),
                    span.start_year if span else None,
                    span.end_year if span else None,
                    entry.location,
                    entry.label,
                    json.dumps(
                        [{"line": line.number, "text": line.raw} for line in entry.lines],
                        ensure_ascii=False,
                    ),
                    json.dumps(entry.unresolved),
                    ordinal,
                    stamp,
                    stamp,
                ),
            )
        for ordinal, proposal in enumerate(proposals, start=1):
            self.conn.execute(
                "INSERT INTO cv_proposal"
                " (id, import_id, claim_key, claim_type, section, text, evidence,"
                "  has_measurement, decision, decided_text, decided_at, claim_id,"
                "  ordinal, created_at, entry_key, source_line, source_text)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, NULL, NULL, ?, ?, ?, ?, ?)"
                " ON CONFLICT (import_id, claim_key) DO NOTHING",
                (
                    new_id(),
                    import_id,
                    proposal.claim_key,
                    proposal.claim_type.value,
                    proposal.section,
                    proposal.text,
                    proposal.evidence,
                    int(proposal.has_measurement),
                    ordinal,
                    stamp,
                    getattr(proposal, "entry_key", None),
                    getattr(proposal, "source_line", None),
                    getattr(proposal, "source_text", None),
                ),
            )
        return import_id

    def seen_before(self, candidate_id: str, text: str) -> list[sqlite3.Row]:
        """Earlier imports of the same extracted text, newest first.

        Identity is the TEXT, not the file. The same CV exported twice has
        different bytes and the same words, and it is the words a review is
        about.
        """
        return self.conn.execute(
            "SELECT * FROM cv_import WHERE candidate_id = ? AND text_sha256 = ?"
            " AND deleted_at IS NULL ORDER BY created_at DESC, id DESC",
            (candidate_id, sha256_text(text)),
        ).fetchall()

    # -- reading -------------------------------------------------------
    def imports(self, candidate_id: str) -> list[StagedImport]:
        """Every staged read, newest first, with its review counted.

        One query. A per-import count would be a query per row, which is the
        N+1 this project has already paid for once in `facets()`.
        """
        rows = self.conn.execute(
            "SELECT i.id, i.source_name, i.kind, i.pages, i.characters,"
            "       i.text_sha256, i.status, i.created_at, i.archived_at,"
            "       (SELECT COUNT(*) FROM cv_entry e WHERE e.import_id = i.id) AS entries,"
            "       SUM(CASE WHEN p.decision = 'PENDING'  THEN 1 ELSE 0 END) AS pending,"
            "       SUM(CASE WHEN p.decision = 'ACCEPTED' THEN 1 ELSE 0 END) AS accepted,"
            "       SUM(CASE WHEN p.decision = 'EDITED'   THEN 1 ELSE 0 END) AS edited,"
            "       SUM(CASE WHEN p.decision = 'REJECTED' THEN 1 ELSE 0 END) AS rejected"
            "  FROM cv_import i"
            "  LEFT JOIN cv_proposal p ON p.import_id = i.id"
            " WHERE i.candidate_id = ? AND i.deleted_at IS NULL"
            " GROUP BY i.id"
            " ORDER BY i.created_at DESC, i.id DESC",
            (candidate_id,),
        ).fetchall()
        return [
            StagedImport(
                import_id=str(row["id"]),
                source_name=str(row["source_name"]),
                kind=str(row["kind"]),
                pages=row["pages"],
                characters=int(row["characters"]),
                text_sha256=str(row["text_sha256"]),
                status=str(row["status"]),
                created_at=str(row["created_at"]),
                pending=int(row["pending"] or 0),
                accepted=int(row["accepted"] or 0),
                edited=int(row["edited"] or 0),
                rejected=int(row["rejected"] or 0),
                archived_at=row["archived_at"],
                entries=int(row["entries"] or 0),
            )
            for row in rows
        ]

    def get_import(self, candidate_id: str, import_id: str) -> sqlite3.Row | None:
        """One read she can still see. A deleted read is gone from here."""
        return self.conn.execute(
            "SELECT * FROM cv_import WHERE id = ? AND candidate_id = ? AND deleted_at IS NULL",
            (import_id, candidate_id),
        ).fetchone()

    def entries(self, import_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cv_entry WHERE import_id = ? ORDER BY ordinal", (import_id,)
        ).fetchall()

    def entry(self, import_id: str, entry_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cv_entry WHERE import_id = ? AND entry_key = ?",
            (import_id, entry_key),
        ).fetchone()

    def proposals(self, import_id: str) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM cv_proposal WHERE import_id = ? ORDER BY ordinal",
            (import_id,),
        ).fetchall()

    def proposal(self, import_id: str, claim_key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cv_proposal WHERE import_id = ? AND claim_key = ?",
            (import_id, claim_key),
        ).fetchone()

    # -- deciding ------------------------------------------------------
    def record_decision(
        self,
        import_id: str,
        claim_key: str,
        *,
        decision: str,
        decided_text: str | None = None,
        claim_id: str | None = None,
    ) -> None:
        """Write one answer. Creates no claim; the caller does that.

        Deliberately idempotent in shape rather than in effect: answering a
        proposal twice overwrites the answer, which is what a person changing
        their mind inside an open review expects.
        """
        if decision not in DECISIONS:
            raise ValueError(f"unknown decision {decision!r}")
        self.conn.execute(
            "UPDATE cv_proposal"
            "   SET decision = ?, decided_text = ?, decided_at = ?, claim_id = ?"
            " WHERE import_id = ? AND claim_key = ?",
            (
                decision,
                decided_text,
                now_utc() if decision != "PENDING" else None,
                claim_id,
                import_id,
                claim_key,
            ),
        )
        self.conn.execute(
            "UPDATE cv_import SET updated_at = ? WHERE id = ?", (now_utc(), import_id)
        )

    def close_if_finished(self, import_id: str) -> bool:
        """Mark the review closed once nothing is pending. Returns whether it is."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS pending FROM cv_proposal"
            " WHERE import_id = ? AND decision = 'PENDING'",
            (import_id,),
        ).fetchone()
        finished = int(row["pending"] or 0) == 0
        self.conn.execute(
            "UPDATE cv_import SET status = ?, updated_at = ? WHERE id = ?",
            ("CLOSED" if finished else "OPEN", now_utc(), import_id),
        )
        return finished

    # -- lifecycle -----------------------------------------------------
    #
    # ARCHIVE and DELETE are different acts and the interface names them
    # differently. See migration 0039 and docs/CAREER_EVIDENCE.md.
    #
    #   archive   reversible; every row stays; counts nowhere until restored
    #   delete    permanent; every UNCONFIRMED suggestion goes; a confirmed one
    #             keeps its row, because its claim cites it as provenance
    #
    # Neither touches `verified_claim`. A confirmed claim stopped belonging to
    # its import the moment she confirmed it; retiring it is its own act, in
    # Career Evidence.

    def archive(self, candidate_id: str, import_id: str) -> bool:
        if self.get_import(candidate_id, import_id) is None:
            return False
        self.conn.execute(
            "UPDATE cv_import SET archived_at = COALESCE(archived_at, ?), updated_at = ?"
            " WHERE id = ?",
            (now_utc(), now_utc(), import_id),
        )
        return True

    def restore(self, candidate_id: str, import_id: str) -> bool:
        if self.get_import(candidate_id, import_id) is None:
            return False
        self.conn.execute(
            "UPDATE cv_import SET archived_at = NULL, updated_at = ? WHERE id = ?",
            (now_utc(), import_id),
        )
        return True

    def delete_plan(self, candidate_id: str, import_id: str) -> dict[str, int] | None:
        """Exactly what deleting this read would remove and keep. Changes nothing."""
        if self.get_import(candidate_id, import_id) is None:
            return None
        row = self.conn.execute(
            "SELECT SUM(CASE WHEN decision = 'PENDING' THEN 1 ELSE 0 END),"
            "       SUM(CASE WHEN decision = 'REJECTED' THEN 1 ELSE 0 END),"
            "       SUM(CASE WHEN decision IN ('ACCEPTED', 'EDITED') THEN 1 ELSE 0 END)"
            "  FROM cv_proposal WHERE import_id = ?",
            (import_id,),
        ).fetchone()
        pending, rejected, confirmed = (int(value or 0) for value in row)
        entries = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM cv_entry WHERE import_id = ?", (import_id,)
            ).fetchone()[0]
        )
        return {
            "pending": pending,
            "rejected": rejected,
            "removed": pending + rejected,
            "confirmed_kept": confirmed,
            "entries": entries,
        }

    def delete(self, candidate_id: str, import_id: str) -> dict[str, int] | None:
        """Remove a read for good. Returns what was removed and what was kept.

        With nothing confirmed from it, every row goes: the read, its jobs and
        its suggestions. With some confirmed, the unconfirmed suggestions go,
        the confirmed suggestion rows and the jobs they sit under stay as the
        provenance their claims cite, and the read is marked deleted so it
        appears nowhere else.
        """
        plan = self.delete_plan(candidate_id, import_id)
        if plan is None:
            return None
        keys = [
            str(row["claim_key"])
            for row in self.conn.execute(
                "SELECT claim_key FROM cv_proposal WHERE import_id = ?"
                " AND decision IN ('PENDING', 'REJECTED')",
                (import_id,),
            )
        ]
        self.conn.execute(
            "DELETE FROM cv_proposal WHERE import_id = ? AND decision IN ('PENDING', 'REJECTED')",
            (import_id,),
        )
        if plan["confirmed_kept"]:
            self.conn.execute(
                "DELETE FROM cv_entry WHERE import_id = ? AND entry_key NOT IN"
                " (SELECT entry_key FROM cv_proposal WHERE import_id = ?"
                "   AND entry_key IS NOT NULL)",
                (import_id, import_id),
            )
            self.conn.execute(
                "UPDATE cv_import SET deleted_at = ?, archived_at = NULL, status = 'CLOSED',"
                " updated_at = ? WHERE id = ?",
                (now_utc(), now_utc(), import_id),
            )
        else:
            self.conn.execute("DELETE FROM cv_entry WHERE import_id = ?", (import_id,))
            self.conn.execute("DELETE FROM cv_proposal WHERE import_id = ?", (import_id,))
            self.conn.execute("DELETE FROM cv_import WHERE id = ?", (import_id,))
        drop_orphan_links(self.conn, candidate_id, keys)
        return plan

    # -- organising one read -------------------------------------------
    #
    # A suggestion's job can be corrected before anything is confirmed: moved
    # to another job, split into a new one, two jobs merged, a missing job
    # created. Each changes `entry_key` or an entry's fields and NOTHING ELSE:
    # the suggestion's text, its source line and its raw source text are never
    # rewritten, so provenance survives every one of them.

    def update_entry(self, import_id: str, entry_key: str, fields: dict) -> bool:
        row = self.entry(import_id, entry_key)
        if row is None:
            return False
        merged = {key: row[key] for key in ENTRY_FIELDS}
        merged.update({key: value for key, value in fields.items() if key in ENTRY_FIELDS})
        if merged["current_role"]:
            merged["period_end"] = None
        self.conn.execute(
            "UPDATE cv_entry SET company = ?, role_title = ?, period_start = ?, period_end = ?,"
            " current_role = ?, start_year = ?, end_year = ?, unresolved_json = ?, edited = 1,"
            " updated_at = ? WHERE import_id = ? AND entry_key = ?",
            (
                merged["company"],
                merged["role_title"],
                merged["period_start"],
                merged["period_end"],
                int(bool(merged["current_role"])),
                _year(merged["period_start"]) or row["start_year"],
                _year(merged["period_end"])
                or (None if merged["current_role"] else row["end_year"]),
                json.dumps(_unresolved(merged, row)),
                now_utc(),
                import_id,
                entry_key,
            ),
        )
        return True

    def create_entry(self, import_id: str, fields: dict, *, section: str = "experience") -> str:
        count = int(
            self.conn.execute(
                "SELECT COUNT(*) FROM cv_entry WHERE import_id = ?", (import_id,)
            ).fetchone()[0]
        )
        existing = {str(r["entry_key"]) for r in self.entries(import_id)}
        number = count + 1
        while f"added-e{number:02d}" in existing:
            number += 1
        key = f"added-e{number:02d}"
        stamp = now_utc()
        self.conn.execute(
            "INSERT INTO cv_entry (id, import_id, entry_key, section, source_json,"
            " unresolved_json, edited, ordinal, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, '[]', '[]', 1, ?, ?, ?)",
            (new_id(), import_id, key, section, count + 1, stamp, stamp),
        )
        self.update_entry(import_id, key, fields)
        return key

    def move(self, import_id: str, claim_keys: list[str], entry_key: str | None) -> int:
        if entry_key is not None and self.entry(import_id, entry_key) is None:
            raise ValueError("no such experience in this read")
        moved = 0
        for key in claim_keys:
            moved += self.conn.execute(
                "UPDATE cv_proposal SET entry_key = ? WHERE import_id = ? AND claim_key = ?",
                (entry_key, import_id, key),
            ).rowcount
        self.conn.execute(
            "UPDATE cv_import SET updated_at = ? WHERE id = ?", (now_utc(), import_id)
        )
        return moved

    def merge_entries(self, import_id: str, source_key: str, target_key: str) -> int:
        """Fold one job into another. The source's heading lines move with it."""
        source = self.entry(import_id, source_key)
        target = self.entry(import_id, target_key)
        if source is None or target is None or source_key == target_key:
            raise ValueError("choose two different experiences from this read")
        moved = self.conn.execute(
            "UPDATE cv_proposal SET entry_key = ? WHERE import_id = ? AND entry_key = ?",
            (target_key, import_id, source_key),
        ).rowcount
        lines = json.loads(target["source_json"] or "[]") + json.loads(
            source["source_json"] or "[]"
        )
        self.conn.execute(
            "UPDATE cv_entry SET source_json = ?, edited = 1, updated_at = ?"
            " WHERE import_id = ? AND entry_key = ?",
            (json.dumps(lines, ensure_ascii=False), now_utc(), import_id, target_key),
        )
        self.conn.execute(
            "DELETE FROM cv_entry WHERE import_id = ? AND entry_key = ?", (import_id, source_key)
        )
        return moved

    def split(self, import_id: str, claim_keys: list[str], fields: dict) -> str:
        """A new job holding the chosen suggestions."""
        first = self.conn.execute(
            "SELECT e.section, e.company FROM cv_proposal p"
            " JOIN cv_entry e ON e.import_id = p.import_id AND e.entry_key = p.entry_key"
            " WHERE p.import_id = ? AND p.claim_key = ?",
            (import_id, claim_keys[0] if claim_keys else ""),
        ).fetchone()
        seeded = {"company": first["company"]} if first is not None else {}
        seeded.update(fields)
        key = self.create_entry(
            import_id, seeded, section=str(first["section"]) if first else "experience"
        )
        self.move(import_id, claim_keys, key)
        return key

    def delete_entry(self, import_id: str, entry_key: str) -> bool:
        """Remove a job with nothing left in it. A job with suggestions is merged
        or has them moved first, so nothing is lost by removing a heading."""
        held = self.conn.execute(
            "SELECT COUNT(*) FROM cv_proposal WHERE import_id = ? AND entry_key = ?",
            (import_id, entry_key),
        ).fetchone()[0]
        if held:
            raise ValueError("move or merge this experience's suggestions first")
        return bool(
            self.conn.execute(
                "DELETE FROM cv_entry WHERE import_id = ? AND entry_key = ?",
                (import_id, entry_key),
            ).rowcount
        )

    def delete_proposals(self, candidate_id: str, import_id: str, claim_keys: list[str]) -> int:
        """Remove unconfirmed suggestions for good. A confirmed one is refused:
        it is evidence now, and withdrawing evidence is retiring it."""
        removed = 0
        for key in claim_keys:
            removed += self.conn.execute(
                "DELETE FROM cv_proposal WHERE import_id = ? AND claim_key = ?"
                " AND decision IN ('PENDING', 'REJECTED')",
                (import_id, key),
            ).rowcount
        drop_orphan_links(self.conn, candidate_id, claim_keys)
        self.close_if_finished(import_id)
        return removed


#: What a person may correct about a job.
ENTRY_FIELDS = ("company", "role_title", "period_start", "period_end", "current_role")


def _year(month: str | None) -> int | None:
    return int(month[:4]) if month and len(month) >= 4 and month[:4].isdigit() else None


def _unresolved(fields: dict, row: sqlite3.Row) -> list[str]:
    dated = bool(fields["period_start"] or row["start_year"] or row["period_text"])
    return [
        reason
        for reason, missing in (
            ("company", not fields["company"]),
            ("role", not fields["role_title"]),
            ("dates", not dated),
        )
        if missing
    ]


def drop_orphan_links(conn: sqlite3.Connection, candidate_id: str, keys: list[str]) -> None:
    """Forget where a removed suggestion was organised, if nothing else has it.

    `career_evidence_link` is keyed by claim key, and the same key can arrive
    from two reads of one CV. A link is removed only when no confirmed claim,
    no remaining CV suggestion and no intake claim still carries the key.
    """
    has_links = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name = 'career_evidence_link'"
    ).fetchone()
    if not has_links:
        return
    for key in set(keys):
        alive = conn.execute(
            "SELECT 1 FROM verified_claim WHERE claim_key = ? AND candidate_id = ?"
            " UNION ALL SELECT 1 FROM cv_proposal WHERE claim_key = ?"
            " UNION ALL SELECT 1 FROM intake_claim"
            "  WHERE COALESCE(resolved_claim_key, claim_key) = ?"
            " LIMIT 1",
            (key, candidate_id, key, key),
        ).fetchone()
        if not alive:
            conn.execute(
                "DELETE FROM career_evidence_link WHERE candidate_id = ? AND claim_key = ?",
                (candidate_id, key),
            )


class RequirementReviewRepo(_Repo):
    """What the candidate thinks of one requirement/evidence pair.

    Writes nothing anywhere else. A verdict is an opinion about a mapping this
    system produced; it is not evidence, it is not a claim, and no code path
    may promote it into one.
    """

    def set(
        self,
        candidate_id: str,
        job_id: str,
        signal_id: str,
        *,
        verdict: str,
        note: str | None = None,
    ) -> None:
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        stamp = now_utc()
        self.conn.execute(
            "INSERT INTO requirement_review"
            " (id, candidate_id, job_id, signal_id, verdict, note, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (candidate_id, job_id, signal_id) DO UPDATE SET"
            "   verdict = excluded.verdict,"
            "   note = excluded.note,"
            "   updated_at = excluded.updated_at",
            (new_id(), candidate_id, job_id, signal_id, verdict, note, stamp, stamp),
        )

    def clear(self, candidate_id: str, job_id: str, signal_id: str) -> None:
        """Remove one verdict. The only destructive operation here, and it is
        the person withdrawing her own opinion about her own evidence."""
        self.conn.execute(
            "DELETE FROM requirement_review"
            " WHERE candidate_id = ? AND job_id = ? AND signal_id = ?",
            (candidate_id, job_id, signal_id),
        )

    def for_job(self, candidate_id: str, job_id: str) -> dict[str, sqlite3.Row]:
        rows = self.conn.execute(
            "SELECT * FROM requirement_review WHERE candidate_id = ? AND job_id = ?",
            (candidate_id, job_id),
        ).fetchall()
        return {str(row["signal_id"]): row for row in rows}

    def count(self, candidate_id: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM requirement_review WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        return int(row["n"] or 0)


class CandidateStateRepo(_Repo):
    """Where the person got to. Local reading state, nothing more."""

    def get(self, candidate_id: str, key: str) -> str | None:
        row = self.conn.execute(
            "SELECT value FROM candidate_state WHERE candidate_id = ? AND key = ?",
            (candidate_id, key),
        ).fetchone()
        return str(row["value"]) if row is not None else None

    def set(self, candidate_id: str, key: str, value: str) -> None:
        stamp = now_utc()
        self.conn.execute(
            "INSERT INTO candidate_state (candidate_id, key, value, updated_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (candidate_id, key) DO UPDATE SET"
            "   value = excluded.value, updated_at = excluded.updated_at",
            (candidate_id, key, value, stamp),
        )

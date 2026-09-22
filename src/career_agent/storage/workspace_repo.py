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
    ) -> str:
        """Persist one read and everything it proposed.

        `text` is hashed and discarded. The document is not stored: what a
        review needs is the proposals and the lines they came from, and both
        of those are on the proposal rows.
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
        for ordinal, proposal in enumerate(proposals, start=1):
            self.conn.execute(
                "INSERT INTO cv_proposal"
                " (id, import_id, claim_key, claim_type, section, text, evidence,"
                "  has_measurement, decision, decided_text, decided_at, claim_id,"
                "  ordinal, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', NULL, NULL, NULL, ?, ?)"
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
            " ORDER BY created_at DESC, id DESC",
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
            "       i.text_sha256, i.status, i.created_at,"
            "       SUM(CASE WHEN p.decision = 'PENDING'  THEN 1 ELSE 0 END) AS pending,"
            "       SUM(CASE WHEN p.decision = 'ACCEPTED' THEN 1 ELSE 0 END) AS accepted,"
            "       SUM(CASE WHEN p.decision = 'EDITED'   THEN 1 ELSE 0 END) AS edited,"
            "       SUM(CASE WHEN p.decision = 'REJECTED' THEN 1 ELSE 0 END) AS rejected"
            "  FROM cv_import i"
            "  LEFT JOIN cv_proposal p ON p.import_id = i.id"
            " WHERE i.candidate_id = ?"
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
            )
            for row in rows
        ]

    def get_import(self, candidate_id: str, import_id: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT * FROM cv_import WHERE id = ? AND candidate_id = ?",
            (import_id, candidate_id),
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

    def discard(self, candidate_id: str, import_id: str) -> int:
        """Throw away a staged read and everything still undecided in it.

        Accepted proposals have already produced claims and those claims are
        NOT touched: they are facts the person confirmed, and they stopped
        belonging to this import at the moment she confirmed them.
        """
        owned = self.get_import(candidate_id, import_id)
        if owned is None:
            return 0
        self.conn.execute("DELETE FROM cv_proposal WHERE import_id = ?", (import_id,))
        self.conn.execute("DELETE FROM cv_import WHERE id = ?", (import_id,))
        return 1


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

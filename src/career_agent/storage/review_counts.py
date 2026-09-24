"""What is waiting for the person to review. ONE definition, read everywhere.

Before this module the question had four answers. The first screen counted
every unanswered intake claim in every package, including the ones in a
package she had archived; the terminal counted only the package in force; the
Career workspace counted everything not yet placed in an experience, including
suggestions she had rejected. So archiving 142 unanswered suggestions left
"142 statements waiting" on Home, and the setup still said she had not
finished.

The definition, in words:

    A statement NEEDS REVIEW when nobody has answered it yet AND it is live:
    a suggestion from a CV read that is neither archived nor deleted, a
    suggestion from the intake package in force, or a DRAFT claim -- one
    recorded but never confirmed in any revision (`ClaimRepo.states`).

That is all. An archived import is work she put down, not work waiting. A
rejected suggestion was answered. A confirmed one is evidence.

Every surface that shows a count reads it from here: `/api/firstrun`, the
Home setup, the Career Evidence summary, `/api/cv/imports`, the Career
workspace and `career-agent evidence`. `tests/integration/test_review_counts.py`
checks that they agree.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from career_agent.intake.models import ReviewState

#: The SQL predicate for a CV read she is working on. Written once.
ACTIVE_CV_IMPORT = "i.archived_at IS NULL AND i.deleted_at IS NULL"
#: The SQL predicate for the intake package in force.
ACTIVE_PACKAGE = "p.status = 'ACTIVE' AND p.deleted_at IS NULL"
#: Intake states that still want an answer.
ANSWERABLE = tuple(sorted(ReviewState.ANSWERABLE))


@dataclass(frozen=True, slots=True)
class ReviewCounts:
    """How much is waiting, and in how many documents she is working on."""

    cv_waiting: int = 0
    package_waiting: int = 0
    #: Claims recorded but never confirmed (a `verified: false` career fact).
    drafts: int = 0
    #: CV reads she is working on, and archived ones kept for later.
    cv_imports: int = 0
    cv_archived: int = 0
    #: Intake packages not archived or deleted (in force or superseded).
    packages: int = 0
    packages_archived: int = 0

    @property
    def waiting(self) -> int:
        return self.cv_waiting + self.package_waiting + self.drafts

    @property
    def documents(self) -> int:
        """Documents she has imported and not put away or removed."""
        return self.cv_imports + self.packages

    def as_dict(self) -> dict[str, int]:
        return {
            "waiting": self.waiting,
            "cv_waiting": self.cv_waiting,
            "package_waiting": self.package_waiting,
            "drafts": self.drafts,
            "documents": self.documents,
            "cv_imports": self.cv_imports,
            "cv_archived": self.cv_archived,
            "packages": self.packages,
            "packages_archived": self.packages_archived,
        }


def _has(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(row[1] == column for row in conn.execute(f"PRAGMA table_info({table})"))


def review_counts(conn: sqlite3.Connection, candidate_id: str | None = None) -> ReviewCounts:
    """The counts, from the database as it stands.

    A database predating the CV or intake tables answers zero for what it
    does not have, rather than raising on the first screen somebody sees.
    """
    cv_waiting = cv_imports = cv_archived = 0
    if _has(conn, "cv_import", "archived_at"):
        scope = " AND i.candidate_id = ?" if candidate_id else ""
        args: tuple = (candidate_id,) if candidate_id else ()
        cv_waiting = int(
            conn.execute(
                "SELECT COUNT(*) FROM cv_proposal c JOIN cv_import i ON i.id = c.import_id"
                f" WHERE c.decision = 'PENDING' AND {ACTIVE_CV_IMPORT}{scope}",
                args,
            ).fetchone()[0]
        )
        row = conn.execute(
            "SELECT SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END),"
            "       SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END)"
            f"  FROM cv_import i WHERE deleted_at IS NULL{scope}",
            args,
        ).fetchone()
        cv_imports, cv_archived = int(row[0] or 0), int(row[1] or 0)

    package_waiting = packages = packages_archived = 0
    if _has(conn, "intake_package", "deleted_at"):
        package_waiting = int(
            conn.execute(
                "SELECT COUNT(*) FROM intake_claim c JOIN intake_package p ON p.id = c.package_id"
                f" WHERE {ACTIVE_PACKAGE} AND c.review_state IN (?, ?, ?)",
                ANSWERABLE,
            ).fetchone()[0]
        )
        row = conn.execute(
            "SELECT SUM(CASE WHEN status != 'DISCARDED' THEN 1 ELSE 0 END),"
            "       SUM(CASE WHEN status = 'DISCARDED' THEN 1 ELSE 0 END)"
            "  FROM intake_package WHERE deleted_at IS NULL"
        ).fetchone()
        packages, packages_archived = int(row[0] or 0), int(row[1] or 0)

    drafts = 0
    if candidate_id or _has(conn, "verified_claim", "verified"):
        scope = " AND c.candidate_id = ?" if candidate_id else ""
        drafts = int(
            conn.execute(
                "SELECT COUNT(*) FROM verified_claim c"
                " WHERE c.superseded_by_id IS NULL AND c.verified = 0"
                "   AND NOT EXISTS (SELECT 1 FROM verified_claim h"
                "                    WHERE h.candidate_id = c.candidate_id"
                f"                      AND h.claim_key = c.claim_key AND h.verified = 1){scope}",
                (candidate_id,) if candidate_id else (),
            ).fetchone()[0]
        )

    return ReviewCounts(
        drafts=drafts,
        cv_waiting=cv_waiting,
        package_waiting=package_waiting,
        cv_imports=cv_imports,
        cv_archived=cv_archived,
        packages=packages,
        packages_archived=packages_archived,
    )

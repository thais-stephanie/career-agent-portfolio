"""Staging a package, reviewing it, and the one bridge to a verified claim.

WHAT THIS OWNS
--------------
`intake_package` and `intake_claim`, migration 0023. A package that has been
imported is a REVIEW IN PROGRESS: a list of proposals, each with a state, that
survives closing the tab and re-opening it tomorrow.

THE ONE BRIDGE
--------------
`confirm` is the only function here that can produce a `VerifiedClaim`, and it
does not construct one. It builds a `cv.propose.Proposal` and hands it to
`cv.propose.to_claim`, which is one of the three places in this product allowed
to set `verified=True`.

That indirection looks like ceremony and is not. `tests/unit/test_cv_intake.py`
walks the syntax tree of the `cv` package to assert those three sites; if this
module constructed its own `VerifiedClaim(verified=True)` there would be a
fourth, in a package whose whole input is a file somebody else's model wrote.
`tests/unit/test_intake_never_verifies.py` walks this package for the same
reason.

IDEMPOTENCE
-----------
Importing the same package twice does not produce two reviews. The second
import is recognised by `package_sha256` and RETURNS the existing one with its
answers intact -- which is the property that makes it safe to re-run an import
after fixing an unrelated field, and the property that stops a re-import
burying forty already-answered proposals under forty identical new ones.

WHAT A DISCARD DOES NOT DO
--------------------------
It does not DELETE. A discarded package's REJECTED rows are the only thing
that stops those lines being proposed again by the next import of the same
documents, exactly as migration 0019 reasoned for `cv_proposal`. A rejection is
a fact about one read of one package; it never becomes a claim, and it is never
consulted by the matcher or by preparation.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any

from career_agent.intake.conflicts import Reconciliation, reconcile
from career_agent.intake.models import (
    IntakePackage,
    PackageStatus,
    ProposedClaim,
    ReviewState,
    SourceKind,
)
from career_agent.intake.parse import package_digest
from career_agent.storage.db import transaction
from career_agent.storage.repositories import ClaimRepo, new_id, now_utc
from career_agent.storage.workspace_repo import drop_orphan_links, ensure_candidate


class IntakeReviewError(ValueError):
    """A review action that cannot be taken on this row."""


@dataclass
class ImportPreview:
    """What importing this package would do. Nothing is written for one."""

    schema_version: str
    generator: str
    sources: list[str] = field(default_factory=list)
    claims_total: int = 0
    claims_new: int = 0
    #: Claims whose key this candidate has already CONFIRMED as a claim. They
    #: are still staged -- a re-import must not silently skip a row -- but they
    #: are counted apart so a preview can say "thirty of these you already
    #: stand behind".
    claims_already_confirmed: int = 0
    duplicates_collapsed: int = 0
    conflict_groups: int = 0
    conflicted_claims: int = 0
    missing_kinds: list[str] = field(default_factory=list)
    #: Set when this exact package has been imported before.
    existing_package_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generator": self.generator,
            "sources": list(self.sources),
            "claims_total": self.claims_total,
            "claims_new": self.claims_new,
            "claims_already_confirmed": self.claims_already_confirmed,
            "duplicates_collapsed": self.duplicates_collapsed,
            "conflict_groups": self.conflict_groups,
            "conflicted_claims": self.conflicted_claims,
            "missing_kinds": list(self.missing_kinds),
            "already_imported": self.existing_package_id is not None,
        }


def preview(
    conn: sqlite3.Connection, package: IntakePackage, *, filename: str | None = None
) -> ImportPreview:
    """The dry run. Reads the corpus, writes nothing at all.

    Separate from `import_package` rather than a flag on it, because a dry run
    that shares a code path with a write is a dry run that one refactor away
    starts writing. The two agree because both go through `reconcile`.
    """
    del filename
    reconciliation = reconcile(package)
    digest = package_digest(package)
    existing = conn.execute(
        "SELECT id FROM intake_package WHERE package_sha256 = ?" + _live(conn),
        (digest,),
    ).fetchone()

    confirmed_keys = _confirmed_claim_keys(conn)
    already = sum(1 for g in reconciliation.groups if g.claim_key in confirmed_keys)

    return ImportPreview(
        schema_version=package.schema_version,
        generator=f"{package.generator.kind}:{package.generator.name}".rstrip(":"),
        sources=[f"{s.ref} ({s.kind})" for s in package.sources],
        claims_total=len(package.claims),
        claims_new=len(reconciliation.groups) - already,
        claims_already_confirmed=already,
        duplicates_collapsed=reconciliation.duplicates_collapsed,
        conflict_groups=len(reconciliation.conflicts),
        conflicted_claims=len(reconciliation.conflicted_keys),
        missing_kinds=list(reconciliation.missing_kinds),
        existing_package_id=str(existing["id"]) if existing else None,
    )


def import_package(
    conn: sqlite3.Connection, package: IntakePackage, *, filename: str | None = None
) -> str:
    """Stage the package for review. Atomic, and idempotent by digest.

    Returns the package id. Every claim lands UNREVIEWED, or CONFLICT when the
    reconciliation found it disagrees with another -- and CONFLICT is still an
    unanswered state, not a verdict.
    """
    digest = package_digest(package)
    existing = conn.execute(
        "SELECT id FROM intake_package WHERE package_sha256 = ?" + _live(conn),
        (digest,),
    ).fetchone()
    if existing is not None:
        # The same bytes. Returning the review already in progress is the whole
        # point: its answers are the candidate's and a second copy would
        # discard them.
        return str(existing["id"])

    reconciliation = reconcile(package)
    package_id = new_id()
    stamp = now_utc()

    # A package with nothing in it is not a reading of anything, and it must
    # never retire one. Landing it INCOMPLETE is what makes "a failed newer
    # import cannot supersede a review she has half finished" a property of
    # the schema rather than a promise in a docstring.
    complete = bool(reconciliation.groups)
    status = PackageStatus.ACTIVE if complete else PackageStatus.INCOMPLETE

    with transaction(conn):
        ensure_candidate(conn)
        if complete:
            # The successor takes over and the predecessor is SUPERSEDED, not
            # deleted and not DISCARDED: the machine retired it, she did not,
            # and `select` puts it straight back. Nothing it holds moves.
            _supersede_active(conn, by=package_id, stamp=stamp)
        conn.execute(
            "INSERT INTO intake_package (id, schema_version, package_sha256, generator,"
            " declared_sources, claim_count, status, filename, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                package_id,
                package.schema_version,
                digest,
                f"{package.generator.kind}:{package.generator.name}".rstrip(":"),
                json.dumps(
                    [s.model_dump(mode="json") for s in package.sources],
                    sort_keys=True,
                    ensure_ascii=False,
                ),
                len(reconciliation.groups),
                status,
                filename,
                stamp,
                stamp,
            ),
        )
        conflicted = reconciliation.conflicted_keys
        for group in reconciliation.groups:
            conn.execute(
                "INSERT INTO intake_claim (id, package_id, claim_key, claim_type,"
                " payload_json, source_ref, review_state, conflict_group,"
                " created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    new_id(),
                    package_id,
                    group.claim_key,
                    group.claim.type.value,
                    json.dumps(
                        group.claim.model_dump(mode="json", exclude_none=True),
                        sort_keys=True,
                        ensure_ascii=False,
                    ),
                    ",".join(group.sources),
                    (
                        ReviewState.CONFLICT
                        if group.claim_key in conflicted
                        else ReviewState.UNREVIEWED
                    ),
                    group.conflict_group,
                    stamp,
                    stamp,
                ),
            )
    return package_id


# =========================================================================
# review
# =========================================================================


def rows(
    conn: sqlite3.Connection, package_id: str, *, state: str | None = None
) -> list[sqlite3.Row]:
    """Every staged claim, in a stable order.

    Conflicts first, because they are the rows that need a person most, then
    by kind and key so the order does not move between page loads.
    """
    sql = (
        "SELECT * FROM intake_claim WHERE package_id = ?"
        + (" AND review_state = ?" if state else "")
        + " ORDER BY (conflict_group IS NULL), conflict_group, claim_type, claim_key"
    )
    args: tuple[Any, ...] = (package_id, state) if state else (package_id,)
    return list(conn.execute(sql, args).fetchall())


def summary(conn: sqlite3.Connection, package_id: str) -> dict[str, int]:
    """How far the review has got, by state. Every state, including the zeros.

    Zeros included on purpose: a summary that omits `REJECTED` when nothing has
    been rejected makes the absence of the state look like the absence of the
    feature.
    """
    counts = {state: 0 for state in sorted(ReviewState.ALL)}
    for row in conn.execute(
        "SELECT review_state, COUNT(*) AS n FROM intake_claim WHERE package_id = ?"
        " GROUP BY review_state",
        (package_id,),
    ):
        counts[str(row["review_state"])] = int(row["n"])
    return counts


def _live(conn: sqlite3.Connection) -> str:
    """` AND deleted_at IS NULL`, on a schema that has the column (0039+).

    The store runs against a database before it is migrated in exactly one
    place, the upgrade tests, and there no package can have been deleted.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(intake_package)")}
    return " AND deleted_at IS NULL" if "deleted_at" in columns else ""


def _row(conn: sqlite3.Connection, package_id: str, claim_key: str) -> sqlite3.Row:
    """The claim an ANSWER is about. Every answer comes through here.

    An archived or deleted package cannot be answered. It is out of every
    active queue and counter, and a confirmation given inside it would create
    evidence from a reading she said she was not working from. Restoring it
    first is one click and says what she means.
    """
    package = conn.execute(
        "SELECT status FROM intake_package WHERE id = ?" + _live(conn), (package_id,)
    ).fetchone()
    if (
        package is None
        and conn.execute("SELECT 1 FROM intake_package WHERE id = ?", (package_id,)).fetchone()
    ):
        raise IntakeReviewError("this import was deleted.")
    if package is not None and str(package["status"]) == PackageStatus.DISCARDED:
        raise IntakeReviewError("this import is archived. Restore it before answering.")
    row = conn.execute(
        "SELECT * FROM intake_claim WHERE package_id = ? AND claim_key = ?",
        (package_id, claim_key),
    ).fetchone()
    if row is None:
        raise IntakeReviewError(f"no staged claim {claim_key!r} in this package")
    return row


def _set_state(
    conn: sqlite3.Connection,
    package_id: str,
    claim_key: str,
    state: str,
    *,
    corrected_text: str | None = None,
    resolved_claim_key: str | None = None,
) -> None:
    conn.execute(
        "UPDATE intake_claim SET review_state = ?, corrected_text = COALESCE(?, corrected_text),"
        " resolved_claim_key = COALESCE(?, resolved_claim_key), reviewed_at = ?, updated_at = ?"
        " WHERE package_id = ? AND claim_key = ?",
        (state, corrected_text, resolved_claim_key, now_utc(), now_utc(), package_id, claim_key),
    )


def confirm(
    conn: sqlite3.Connection,
    package_id: str,
    claim_key: str,
    *,
    text: str | None = None,
    atomic: bool = True,
) -> str:
    """The candidate stands behind this claim. The one bridge to `verified_claim`.

    `text` is the third answer and the one that matters: a proposed line is
    often nearly right, and accept-or-reject alone pushes somebody into keeping
    a sentence they would have corrected. When it is given the state is
    CORRECTED_BY_USER, the candidate's wording becomes the claim, and the
    package's original wording stays in `payload_json` -- so the review can
    always show what arrived beside what she made of it.

    Returns the `verified_claim.claim_key`.
    """
    from career_agent.cv.propose import Proposal, to_claim

    if not atomic and not conn.in_transaction:
        raise IntakeReviewError("A bulk review must own a transaction.")
    row = _row(conn, package_id, claim_key)
    if row["review_state"] == ReviewState.REJECTED:
        raise IntakeReviewError(
            f"{claim_key!r} was rejected. Reopen it before confirming, so that saying no "
            "and changing your mind are two visible acts rather than one silent one."
        )
    # **A disputed period may not be confirmed, whatever state the row is in.**
    #
    # Every claim about a role whose dates two documents disagree about CARRIES
    # those dates, so confirming one writes a date nobody has settled onto a
    # fact she stands behind -- and that fact then goes on an application.
    #
    # Asked of the RESOLUTION rather than of `review_state`, because the state
    # can leave CONFLICT without the disagreement being answered: "not sure
    # yet" is a legitimate reply to one of these and clears the marker. The
    # screen refuses this too, and this is the floor under the screen.
    if row["conflict_group"] and _resolution(conn, package_id, str(row["conflict_group"])) is None:
        raise IntakeReviewError(
            f"{claim_key!r} carries dates your documents disagree about. Settle the "
            "disagreement first, once, and every statement it holds comes back."
        )

    claim = ProposedClaim.model_validate(json.loads(str(row["payload_json"])))
    source_kind = _kind_of(conn, package_id, str(row["source_ref"]))
    corrected = (text or "").strip() or None

    # **The period comes from the resolved claim, not from this one.**
    #
    # When two documents disagreed about when a role ran, she answered that
    # once (`resolve_conflict`) and every claim about that role now carries
    # the dates from the version she accepted. Reading this claim's own period
    # instead would write the losing document's dates onto a confirmed fact
    # and make the resolution mean nothing.
    #
    # It is still a document's dates, never a third statement typed here:
    # `chosen_claim_key` names a claim whose payload holds the original
    # wording, which is what keeps every confirmed fact traceable.
    period_source = _period_source(conn, package_id, row) or claim

    # A `cv.propose.Proposal`, so that the ONE function allowed to set
    # `verified=True` is the one that does it. See the module docstring.
    proposal = Proposal(
        claim_key=claim_key,
        claim_type=claim.type,
        text=claim.text,
        section="intake",
        evidence=(claim.evidence.quote or claim.evidence.locator or "")[:2000],
        has_measurement=bool(claim.metrics),
    )
    verified = to_claim(proposal, text=corrected)
    # Provenance from the DECLARED source, and the period as the package read
    # it. `to_claim` does not carry these because a CV line has no structure;
    # a package does, and discarding it would make a confirmed claim poorer
    # than the proposal it came from.
    verified = verified.model_copy(
        update={
            "source": SourceKind.to_claim_source(source_kind),
            "employer": claim.employer,
            "period_start": (
                period_source.period.start.normalized
                if period_source.period and period_source.period.start
                else None
            ),
            "period_end": (
                period_source.period.end.normalized
                if period_source.period and period_source.period.end
                else None
            ),
            "tools": list(claim.tools),
        }
    )

    candidate_id = ensure_candidate(conn)
    repo = ClaimRepo(conn)
    with transaction(conn) if atomic else nullcontext():
        current = repo.current_row(candidate_id, claim_key)
        if current is None:
            repo.add(candidate_id, verified)
        else:
            # A revision, never an overwrite. The earlier claim may already
            # have prepared an application somebody sent.
            repo.supersede(
                candidate_id, verified.model_copy(update={"revision": int(current["revision"]) + 1})
            )
        _set_state(
            conn,
            package_id,
            claim_key,
            ReviewState.CORRECTED_BY_USER if corrected else ReviewState.CONFIRMED,
            corrected_text=corrected,
            resolved_claim_key=claim_key,
        )
    return claim_key


def conflict_groups(conn: sqlite3.Connection, package_id: str) -> list[dict[str, Any]]:
    """Every disagreement in this package, with both sides and its answer.

    One entry per group, carrying the claims whose periods differ so a reader
    can compare them side by side. `resolved_claim_key` is None until she has
    answered, and `member_count` is how many claims the answer releases --
    which on the owner's real documents is 32 for a single disputed date.
    """
    rows = conn.execute(
        "SELECT * FROM intake_claim WHERE package_id = ? AND conflict_group IS NOT NULL"
        " ORDER BY conflict_group, claim_key",
        (package_id,),
    ).fetchall()
    resolutions = {
        str(r["conflict_group"]): str(r["chosen_claim_key"])
        for r in conn.execute(
            "SELECT conflict_group, chosen_claim_key FROM intake_conflict_resolution"
            " WHERE package_id = ?",
            (package_id,),
        )
    }

    grouped: dict[str, list[sqlite3.Row]] = {}
    for row in rows:
        grouped.setdefault(str(row["conflict_group"]), []).append(row)

    # The employer as a DOCUMENT spells it. The group key is a normalised,
    # case-folded handle -- `EMPLOYMENT:contoso health` -- and putting that on
    # a screen shows somebody a lower-cased version of a company they worked
    # for, which reads as a bug about their own history.
    def employer_of(members: list[sqlite3.Row]) -> str:
        for member in members:
            payload = json.loads(str(member["payload_json"]))
            name = (payload.get("employer") or "").strip()
            if name:
                return name
        return ""

    out: list[dict[str, Any]] = []
    for group, members in grouped.items():
        # The DISTINCT versions of the dates, one per shape, each with the
        # document that stated it. Thirty-two claims about one role carry two
        # periods between them, and two is what a person should be shown.
        sides: dict[tuple[str, str, bool], dict[str, Any]] = {}
        for row in members:
            claim = ProposedClaim.model_validate(json.loads(str(row["payload_json"])))
            period = claim.period
            signature = (
                (period.start.normalized or "") if period and period.start else "",
                (period.end.normalized or "") if period and period.end else "",
                bool(period.current) if period else False,
            )
            side = sides.setdefault(
                signature,
                {
                    "claim_key": str(row["claim_key"]),
                    "sources": [],
                    "start_original": (period.start.original if period and period.start else None),
                    "end_original": period.end.original if period and period.end else None,
                    "start": signature[0] or None,
                    "end": signature[1] or None,
                    "current": signature[2],
                    "claim_count": 0,
                },
            )
            side["claim_count"] += 1
            for ref in str(row["source_ref"]).split(","):
                if ref and ref not in side["sources"]:
                    side["sources"].append(ref)

        out.append(
            {
                "conflict_group": group,
                "employer": employer_of(members) or group.split(":", 1)[-1],
                "member_count": len(members),
                "resolved_claim_key": resolutions.get(group),
                "sides": list(sides.values()),
            }
        )
    return out


def resolve_conflict(
    conn: sqlite3.Connection, package_id: str, group: str, chosen_claim_key: str
) -> int:
    """Answer one disagreement, and release the claims it was holding.

    Returns how many claims returned to the queue.

    **It confirms nothing.** Agreeing about when a role ran is not the same as
    standing behind a sentence about it, so every released claim is UNREVIEWED
    and still has to be answered on its own.

    A claim already CONFIRMED or REJECTED keeps its answer: she decided that
    one deliberately, and a group-level action must not quietly undo it.
    """
    chosen = _row(conn, package_id, chosen_claim_key)
    if str(chosen["conflict_group"] or "") != group:
        raise IntakeReviewError(
            f"{chosen_claim_key!r} is not part of the disagreement {group!r}. "
            "Choose one of the versions this group actually contains."
        )

    with transaction(conn):
        conn.execute(
            "INSERT INTO intake_conflict_resolution"
            " (package_id, conflict_group, chosen_claim_key, resolved_at)"
            " VALUES (?, ?, ?, ?)"
            " ON CONFLICT (package_id, conflict_group) DO UPDATE SET"
            "   chosen_claim_key = excluded.chosen_claim_key,"
            "   resolved_at      = excluded.resolved_at",
            (package_id, group, chosen_claim_key, now_utc()),
        )
        cursor = conn.execute(
            "UPDATE intake_claim SET review_state = ?, updated_at = ?"
            " WHERE package_id = ? AND conflict_group = ? AND review_state = ?",
            (ReviewState.UNREVIEWED, now_utc(), package_id, group, ReviewState.CONFLICT),
        )
        released = int(cursor.rowcount or 0)
    return released


def reopen_conflict(conn: sqlite3.Connection, package_id: str, group: str) -> None:
    """Unmake a resolution. The one delete this schema allows.

    A resolution is a statement about how to READ two documents rather than a
    claim about the candidate, so unmaking it leaves nothing unsaid. Claims she
    has since answered keep their answers; only the still-unanswered ones go
    back to CONFLICT.
    """
    with transaction(conn):
        conn.execute(
            "DELETE FROM intake_conflict_resolution WHERE package_id = ? AND conflict_group = ?",
            (package_id, group),
        )
        conn.execute(
            "UPDATE intake_claim SET review_state = ?, updated_at = ?"
            " WHERE package_id = ? AND conflict_group = ? AND review_state = ?",
            (ReviewState.CONFLICT, now_utc(), package_id, group, ReviewState.UNREVIEWED),
        )


def _resolution(conn: sqlite3.Connection, package_id: str, group: str) -> str | None:
    """Which version of a disputed period the candidate accepted, if any."""
    row = conn.execute(
        "SELECT chosen_claim_key FROM intake_conflict_resolution"
        " WHERE package_id = ? AND conflict_group = ?",
        (package_id, group),
    ).fetchone()
    return str(row["chosen_claim_key"]) if row is not None else None


def _period_source(
    conn: sqlite3.Connection, package_id: str, row: sqlite3.Row
) -> ProposedClaim | None:
    """The claim whose dates a confirmation should use, when one was chosen."""
    group = row["conflict_group"]
    if not group:
        return None
    chosen = conn.execute(
        "SELECT chosen_claim_key FROM intake_conflict_resolution"
        " WHERE package_id = ? AND conflict_group = ?",
        (package_id, str(group)),
    ).fetchone()
    if chosen is None:
        return None
    key = str(chosen["chosen_claim_key"])
    if key == str(row["claim_key"]):
        return None
    payload = conn.execute(
        "SELECT payload_json FROM intake_claim WHERE package_id = ? AND claim_key = ?",
        (package_id, key),
    ).fetchone()
    if payload is None:
        return None
    return ProposedClaim.model_validate(json.loads(str(payload["payload_json"])))


def reject(
    conn: sqlite3.Connection, package_id: str, claim_key: str, *, atomic: bool = True
) -> None:
    """The candidate says no. Recorded, and it creates nothing.

    The row stays for migration 0019's reason: "answered no" is the only thing
    that stops this line reappearing at the top of the list tomorrow. It never
    becomes a claim and dies with the package.

    **It refuses a row that is already a confirmed claim**, and the refusal is
    the interesting part. Marking such a row REJECTED would leave the review
    saying the candidate declined a fact while `verified_claim` still held it,
    verified, available to prepare an application. Withdrawing a claim is its
    own act, in Career Evidence, where it is a revision with `verified=False`
    and never a DELETE -- because the claim may already have prepared an
    application somebody sent.
    """
    if not atomic and not conn.in_transaction:
        raise IntakeReviewError("A bulk review must own a transaction.")
    row = _row(conn, package_id, claim_key)
    if row["review_state"] in (ReviewState.CONFIRMED, ReviewState.CORRECTED_BY_USER):
        raise IntakeReviewError(
            f"{claim_key!r} is already a claim you stand behind. Rejecting it here would "
            "leave this review disagreeing with your Career Evidence. Retire the claim "
            "there instead, which keeps its history."
        )
    with transaction(conn) if atomic else nullcontext():
        _set_state(conn, package_id, claim_key, ReviewState.REJECTED)


def mark_unresolved(conn: sqlite3.Connection, package_id: str, claim_key: str) -> None:
    """The candidate looked and cannot answer yet.

    Distinct from UNREVIEWED on purpose. "I do not know whether that date is
    right" is an answer, and a review that files it as untouched will present
    it as new work every time.
    """
    _row(conn, package_id, claim_key)
    with transaction(conn):
        _set_state(conn, package_id, claim_key, ReviewState.UNRESOLVED)


def reopen(conn: sqlite3.Connection, package_id: str, claim_key: str) -> None:
    """Undo an answer, returning the row to the queue.

    It does NOT retire a claim that a confirmation already created. Those are
    two separate acts -- `verified_claim` has its own retirement, which is a
    revision with `verified=False` and never a DELETE -- and collapsing them
    would let an undo in a review quietly withdraw a fact an application
    already rested on.
    """
    row = _row(conn, package_id, claim_key)
    if row["review_state"] in (ReviewState.CONFIRMED, ReviewState.CORRECTED_BY_USER):
        raise IntakeReviewError(
            f"{claim_key!r} is already a claim you stand behind. Reopening it here would "
            "put the review and your Career Evidence out of step. Retire the claim there "
            "instead."
        )
    with transaction(conn):
        _set_state(
            conn,
            package_id,
            claim_key,
            ReviewState.CONFLICT if row["conflict_group"] else ReviewState.UNREVIEWED,
        )


# =========================================================================
# The package lifecycle
#
# Which reading of her documents is in force. Four states, described in
# `PackageStatus`, and one rule that holds across all of them: NOTHING HERE
# TOUCHES A CLAIM. Superseding confirms nothing, discarding confirms nothing,
# selecting confirms nothing, and every answer she has given stays attached to
# the package she gave it in.
#
# Every transition is reversible. That is the point: on 2026-09-07 the owner's
# database held two packages built from the same two documents by two versions
# of the same parser, and choosing between them was a judgement -- one she
# made by hand, with nothing recording that she had.
# =========================================================================


def _supersede_active(conn: sqlite3.Connection, *, by: str, stamp: str) -> None:
    """Retire whatever is in force, in favour of `by`. Caller holds the lock.

    Not a public function and not a transaction of its own: "the new one is in
    force" and "the old one is not" have to be one write, or a crash between
    them leaves a database with two active readings or none.
    """
    conn.execute(
        "UPDATE intake_package SET status = ?, superseded_by = ?, updated_at = ?"
        " WHERE status = ? AND id <> ?",
        (PackageStatus.SUPERSEDED, by, stamp, PackageStatus.ACTIVE, by),
    )


def active_package(conn: sqlite3.Connection) -> str | None:
    """The reading in force, or None when nothing is.

    None is a real answer and not an error. It happens after she discards the
    only package she has, and the interface's job then is to offer the list
    rather than to pick one for her.
    """
    row = conn.execute(
        "SELECT id FROM intake_package WHERE status = ?"
        + _live(conn)
        + " ORDER BY created_at DESC LIMIT 1",
        (PackageStatus.ACTIVE,),
    ).fetchone()
    return str(row["id"]) if row is not None else None


def select(conn: sqlite3.Connection, package_id: str) -> None:
    """Put this package in force, and retire whatever was.

    ONE transaction, and the reason is the invariant: at most one ACTIVE. Two
    selections racing through two transactions could both read "nothing is
    active" and both write ACTIVE, and a review split across two readings of
    one career cannot be finished.

    Refuses an INCOMPLETE package, which holds nothing to review, and a
    DISCARDED one, which is restored first -- putting something back and
    choosing it are two acts and they get two buttons.
    """
    row = conn.execute("SELECT status FROM intake_package WHERE id = ?", (package_id,)).fetchone()
    if row is None:
        raise IntakeReviewError(f"no such package: {package_id!r}")
    status = str(row["status"])
    if status not in PackageStatus.SELECTABLE:
        raise IntakeReviewError(
            f"a {status} package cannot be put in force. "
            + (
                "Restore it first."
                if status == PackageStatus.DISCARDED
                else "It holds no claims to review."
            )
        )
    stamp = now_utc()
    with transaction(conn):
        _supersede_active(conn, by=package_id, stamp=stamp)
        conn.execute(
            "UPDATE intake_package SET status = ?, superseded_by = NULL, updated_at = ?"
            " WHERE id = ?",
            (PackageStatus.ACTIVE, stamp, package_id),
        )


def discard(conn: sqlite3.Connection, package_id: str) -> None:
    """Put the package away. HER act, and the rows stay.

    See the module docstring: a discarded package's REJECTED rows are the only
    thing that stops those lines being proposed again by the next import.

    Discarding the ACTIVE package leaves NOTHING in force, deliberately. The
    alternative is promoting the next one along, which is the product choosing
    which reading of her career to use because she declined one -- and she is
    one click from choosing, with the list in front of her.
    """
    with transaction(conn):
        conn.execute(
            "UPDATE intake_package SET status = ?, superseded_by = NULL, updated_at = ?"
            " WHERE id = ?",
            (PackageStatus.DISCARDED, now_utc(), package_id),
        )


def restore(conn: sqlite3.Connection, package_id: str) -> str:
    """Take a package back out of the drawer. Returns the status it landed in.

    With NOTHING in force it becomes ACTIVE: there is no other reading for it
    to conflict with, and making her click twice to express one intention is
    the interface being pedantic rather than careful.

    With something already in force it becomes SUPERSEDED, and she selects it
    if she wants it. Silently replacing the reading she is working in would be
    a helpful default overriding a stated answer, which is the mistake this
    session's own `career-agent start` made once.
    """
    row = conn.execute(
        "SELECT status, claim_count FROM intake_package WHERE id = ?", (package_id,)
    ).fetchone()
    if row is None:
        raise IntakeReviewError(f"no such package: {package_id!r}")
    if str(row["status"]) != PackageStatus.DISCARDED:
        raise IntakeReviewError("only a package you put away can be restored")

    stamp = now_utc()
    if not int(row["claim_count"]):
        landed = PackageStatus.INCOMPLETE
    elif active_package(conn) is None:
        landed = PackageStatus.ACTIVE
    else:
        landed = PackageStatus.SUPERSEDED
    with transaction(conn):
        conn.execute(
            "UPDATE intake_package SET status = ?, updated_at = ? WHERE id = ?",
            (landed, stamp, package_id),
        )
    return landed


#: Answers that made a claim. Their rows are the provenance that claim cites.
_CONFIRMED_STATES = (ReviewState.CONFIRMED, ReviewState.CORRECTED_BY_USER)


def delete_plan(conn: sqlite3.Connection, package_id: str) -> dict[str, int]:
    """Exactly what deleting this package would remove and keep. Writes nothing."""
    row = conn.execute(
        "SELECT 1 FROM intake_package WHERE id = ?" + _live(conn), (package_id,)
    ).fetchone()
    if row is None:
        raise IntakeReviewError(f"no such package: {package_id!r}")
    counts = summary(conn, package_id)
    confirmed = sum(counts.get(state, 0) for state in _CONFIRMED_STATES)
    total = sum(counts.values())
    return {
        "waiting": sum(counts.get(state, 0) for state in ReviewState.ANSWERABLE),
        "rejected": counts.get(ReviewState.REJECTED, 0),
        "removed": total - confirmed,
        "confirmed_kept": confirmed,
    }


def delete(conn: sqlite3.Connection, package_id: str) -> dict[str, int]:
    """Remove a package for good: HER act, permanent, and it asked first.

    Different from `discard`, which puts a package away with every row kept.
    Every claim she did not confirm goes -- unanswered, unsure, rejected and
    conflicted alike, with the conflict answers that referred to them. A claim
    she DID confirm keeps its row, because the verified claim it produced
    cites it as where it came from; the package row stays with it, marked
    deleted, so it appears nowhere else. With nothing confirmed, nothing stays.

    `verified_claim` is not touched. Withdrawing evidence is retiring it, in
    Career Evidence, and it is a revision rather than a delete.
    """
    plan = delete_plan(conn, package_id)
    keys = [
        str(row[0])
        for row in conn.execute(
            "SELECT COALESCE(resolved_claim_key, claim_key) FROM intake_claim"
            " WHERE package_id = ? AND review_state NOT IN (?, ?)",
            (package_id, *_CONFIRMED_STATES),
        )
    ]
    stamp = now_utc()
    with transaction(conn):
        conn.execute(
            "DELETE FROM intake_claim WHERE package_id = ? AND review_state NOT IN (?, ?)",
            (package_id, *_CONFIRMED_STATES),
        )
        conn.execute("DELETE FROM intake_conflict_resolution WHERE package_id = ?", (package_id,))
        if plan["confirmed_kept"]:
            conn.execute(
                "UPDATE intake_package SET status = ?, superseded_by = NULL, deleted_at = ?,"
                " updated_at = ? WHERE id = ?",
                (PackageStatus.DISCARDED, stamp, stamp, package_id),
            )
        else:
            conn.execute("DELETE FROM intake_package WHERE id = ?", (package_id,))
        # A package that named this one as its successor now points at nothing.
        conn.execute(
            "UPDATE intake_package SET superseded_by = NULL WHERE superseded_by = ?",
            (package_id,),
        )
        candidate = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
        if candidate is not None:
            drop_orphan_links(conn, str(candidate["id"]), keys)
    return plan


# =========================================================================
# helpers
# =========================================================================


def _kind_of(conn: sqlite3.Connection, package_id: str, source_ref: str) -> str:
    """The declared kind of the source a claim cites.

    A claim collapsed from two documents carries both refs. The FIRST is used,
    because the claim keyed identically from both is the same sentence and the
    kind only decides which `ClaimSource` label it wears. Where the two
    documents genuinely disagree they produce different keys and never reach
    this line.
    """
    row = conn.execute(
        "SELECT declared_sources FROM intake_package WHERE id = ?", (package_id,)
    ).fetchone()
    declared = {s["ref"]: s["kind"] for s in json.loads(str(row["declared_sources"]))}
    first = source_ref.split(",")[0]
    return declared.get(first, SourceKind.DOCUMENT)


def _confirmed_claim_keys(conn: sqlite3.Connection) -> frozenset[str]:
    rows = conn.execute(
        "SELECT claim_key FROM verified_claim WHERE superseded_by_id IS NULL AND verified = 1"
    ).fetchall()
    return frozenset(str(r["claim_key"]) for r in rows)


def reconciliation_of(package: IntakePackage) -> Reconciliation:
    """Exposed so a caller can report without importing `conflicts` directly."""
    return reconcile(package)

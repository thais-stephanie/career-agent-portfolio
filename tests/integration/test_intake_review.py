"""Staging a package, answering it, and what survives a restart.

Every package here is SYNTHETIC. The contract was designed against the owner's
real CV and LinkedIn export, and neither appears in a tracked test.

The tests that matter are the ones about state: a review that forgets what was
answered is a review that re-asks, and a review that re-asks is one people stop
reading -- which is how an unread proposal becomes an accepted claim.
"""

from __future__ import annotations

import pathlib
import sqlite3
import tempfile
from collections.abc import Iterator
from typing import Any

import pytest

from career_agent.intake import parse_package
from career_agent.intake.models import ReviewState
from career_agent.intake.store import (
    IntakeReviewError,
    confirm,
    discard,
    import_package,
    mark_unresolved,
    preview,
    reject,
    reopen,
    rows,
    summary,
)
from career_agent.storage.db import connect, migrate
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="intake-review")) / "corpus.db"
    connection = connect(db_path)
    migrate(connection)
    yield connection
    connection.close()


def _role(ref: str, end: str, *, text: str = "Integration Engineer at Acme") -> dict[str, Any]:
    return {
        "type": "EMPLOYMENT",
        "text": text,
        "source_ref": ref,
        "employer": "Acme",
        "period": {
            "start": {"original": "Jan 2020", "normalized": "2020-01"},
            "end": {"original": end, "normalized": end},
        },
        "evidence": {"quote": f"Integration Engineer, Acme, Jan 2020 to {end}"},
        "tools": ["Workato"],
    }


def _package(claims: list[dict[str, Any]] | None = None) -> Any:
    return parse_package(
        {
            "schema_version": "1.0",
            "generator": {"kind": "EXTERNAL_AI", "name": "an assistant"},
            "sources": [
                {"ref": "cv", "kind": "RESUME", "title": "cv.docx"},
                {"ref": "li", "kind": "LINKEDIN", "title": "profile.pdf"},
            ],
            "claims": claims
            if claims is not None
            else [
                _role("cv", "2021-03"),
                _role("li", "2021-04"),
                {
                    "type": "SKILL",
                    "text": "Workato",
                    "source_ref": "cv",
                    "evidence": {"locator": "Skills"},
                },
                {
                    "type": "SKILL",
                    "text": "Workato",
                    "source_ref": "li",
                    "evidence": {"locator": "Top Skills"},
                },
            ],
        }
    )


def _keys(conn: sqlite3.Connection, package_id: str) -> dict[str, str]:
    return {str(r["claim_key"]): str(r["review_state"]) for r in rows(conn, package_id)}


# =========================================================================
# import
# =========================================================================


def test_every_imported_claim_starts_unanswered(conn) -> None:
    """The central promise. Nothing arrives confirmed."""
    package_id = import_package(conn, _package())

    states = set(_keys(conn, package_id).values())
    assert states <= {ReviewState.UNREVIEWED, ReviewState.CONFLICT}
    assert ReviewState.CONFIRMED not in states


def test_importing_creates_no_verified_claim(conn) -> None:
    import_package(conn, _package())

    candidate_id = ensure_candidate(conn)
    assert ClaimRepo(conn).current(candidate_id) == []


def test_a_disagreement_between_documents_arrives_as_conflict(conn) -> None:
    package_id = import_package(conn, _package())

    counts = summary(conn, package_id)
    assert counts[ReviewState.CONFLICT] == 2
    assert counts[ReviewState.UNREVIEWED] == 1  # the collapsed skill


def test_a_dry_run_writes_nothing(conn) -> None:
    package = _package()

    result = preview(conn, package)

    assert result.conflict_groups == 1
    assert result.duplicates_collapsed == 1
    assert conn.execute("SELECT COUNT(*) c FROM intake_package").fetchone()["c"] == 0


def test_the_dry_run_and_the_import_agree(conn) -> None:
    package = _package()
    result = preview(conn, package)

    package_id = import_package(conn, package)

    assert len(rows(conn, package_id)) == result.claims_total - result.duplicates_collapsed


def test_importing_the_same_package_twice_returns_one_review(conn) -> None:
    """Idempotence, and the thing it protects: the answers already given."""
    package = _package()
    first = import_package(conn, package)
    key = next(k for k, state in _keys(conn, first).items() if state == ReviewState.UNREVIEWED)
    confirm(conn, first, key)

    second = import_package(conn, package)

    assert second == first
    assert _keys(conn, second)[key] == ReviewState.CONFIRMED
    assert conn.execute("SELECT COUNT(*) c FROM intake_package").fetchone()["c"] == 1


# =========================================================================
# answering
# =========================================================================


def test_confirming_creates_exactly_one_verified_claim(conn) -> None:
    package_id = import_package(conn, _package())
    key = next(k for k, state in _keys(conn, package_id).items() if state == ReviewState.UNREVIEWED)

    confirm(conn, package_id, key)

    claims = ClaimRepo(conn).current(ensure_candidate(conn))
    assert len(claims) == 1
    assert claims[0].verified is True
    assert claims[0].claim_key == key


def test_a_confirmed_claim_keeps_the_provenance_the_package_declared(conn) -> None:
    """A claim from a LinkedIn export must not read as one from a CV."""
    from career_agent.domain.enums import ClaimSource

    package = _package(
        claims=[
            {
                "type": "SKILL",
                "text": "Workato",
                "source_ref": "li",
                "evidence": {"locator": "Top Skills"},
            }
        ]
    )
    package_id = import_package(conn, package)
    key = next(iter(_keys(conn, package_id)))

    confirm(conn, package_id, key)

    claim = ClaimRepo(conn).current(ensure_candidate(conn))[0]
    assert claim.source is ClaimSource.LINKEDIN


def test_a_confirmed_claim_keeps_the_period_and_tools_the_package_carried(conn) -> None:
    """A structured package must not produce a poorer claim than the proposal
    it came from."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))

    confirm(conn, package_id, key)

    claim = ClaimRepo(conn).current(ensure_candidate(conn))[0]
    assert claim.employer == "Acme"
    assert claim.period_start == "2020-01"
    assert claim.period_end == "2021-03"
    assert claim.tools == ["Workato"]


def test_correcting_stores_her_words_and_keeps_the_originals(conn) -> None:
    """The third answer, and the one that matters most.

    Accept-or-reject alone pushes somebody into keeping a sentence they would
    have corrected.
    """
    import json

    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))

    confirm(conn, package_id, key, text="Integration engineer, billing systems, at Acme")

    claim = ClaimRepo(conn).current(ensure_candidate(conn))[0]
    assert claim.text == "Integration engineer, billing systems, at Acme"
    assert _keys(conn, package_id)[key] == ReviewState.CORRECTED_BY_USER

    staged = rows(conn, package_id)[0]
    original = json.loads(str(staged["payload_json"]))
    assert original["text"] == "Integration Engineer at Acme"


def test_rejecting_creates_nothing_and_keeps_the_row(conn) -> None:
    """The row stays because "answered no" is the only thing that stops the
    line reappearing tomorrow."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))

    reject(conn, package_id, key)

    assert ClaimRepo(conn).current(ensure_candidate(conn)) == []
    assert _keys(conn, package_id)[key] == ReviewState.REJECTED


def test_unresolved_is_an_answer_and_not_the_absence_of_one(conn) -> None:
    """ "I do not know whether that date is right" must stop the row being
    presented as untouched work every time."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))

    mark_unresolved(conn, package_id, key)

    assert _keys(conn, package_id)[key] == ReviewState.UNRESOLVED
    assert summary(conn, package_id)[ReviewState.UNREVIEWED] == 0


# =========================================================================
# the boundaries between a review and Career Evidence
# =========================================================================


def test_a_confirmed_row_cannot_be_rejected_from_the_review(conn) -> None:
    """Otherwise the review says she declined a fact her Career Evidence still
    holds, verified and available to prepare an application."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))
    confirm(conn, package_id, key)

    with pytest.raises(IntakeReviewError) as exc:
        reject(conn, package_id, key)
    assert "Career Evidence" in str(exc.value)
    assert len(ClaimRepo(conn).current(ensure_candidate(conn))) == 1


def test_a_confirmed_row_cannot_be_reopened_either(conn) -> None:
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))
    confirm(conn, package_id, key)

    with pytest.raises(IntakeReviewError):
        reopen(conn, package_id, key)


def test_a_rejected_row_must_be_reopened_before_it_can_be_confirmed(conn) -> None:
    """Saying no and changing your mind are two visible acts, not one silent
    one."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))
    reject(conn, package_id, key)

    with pytest.raises(IntakeReviewError):
        confirm(conn, package_id, key)

    reopen(conn, package_id, key)
    confirm(conn, package_id, key)
    assert len(ClaimRepo(conn).current(ensure_candidate(conn))) == 1


def test_reopening_a_conflicted_row_returns_it_to_conflict(conn) -> None:
    """Not to UNREVIEWED. The disagreement did not go away because she
    changed her mind about one side of it."""
    package_id = import_package(conn, _package())
    key = next(k for k, state in _keys(conn, package_id).items() if state == ReviewState.CONFLICT)
    reject(conn, package_id, key)

    reopen(conn, package_id, key)

    assert _keys(conn, package_id)[key] == ReviewState.CONFLICT


def test_a_disputed_period_cannot_be_confirmed_at_all(conn) -> None:
    """The rule that used to live only on the screen.

    Every claim about a role whose dates two documents disagree about CARRIES
    those dates, so confirming one writes a date nobody has settled onto a
    fact she stands behind -- and that fact then goes on an application. This
    used to be allowed, and the first version of this test asserted it.

    Asked of the RESOLUTION rather than of `review_state`, because the state
    can leave CONFLICT without the disagreement being answered: "not sure yet"
    is a legitimate reply to one of these and clears the marker.
    """
    package_id = import_package(conn, _package())
    conflicted = [k for k, s in _keys(conn, package_id).items() if s == ReviewState.CONFLICT]

    with pytest.raises(IntakeReviewError) as refused:
        confirm(conn, package_id, conflicted[0])

    assert "disagree" in str(refused.value)
    assert _keys(conn, package_id)[conflicted[0]] == ReviewState.CONFLICT
    assert ClaimRepo(conn).current(ensure_candidate(conn)) == []


def test_saying_not_sure_does_not_open_a_disputed_period_for_confirmation(conn) -> None:
    """The way round the rule, closed. UNRESOLVED clears the CONFLICT marker,
    and a guard reading `review_state` would have let the next click through."""
    package_id = import_package(conn, _package())
    conflicted = [k for k, s in _keys(conn, package_id).items() if s == ReviewState.CONFLICT]
    mark_unresolved(conn, package_id, conflicted[0])

    with pytest.raises(IntakeReviewError):
        confirm(conn, package_id, conflicted[0])


# =========================================================================
# persistence
# =========================================================================


def test_the_review_survives_a_restart(conn) -> None:
    """The whole reason migration 0023 exists. A browser is not one
    continuous act."""
    package_id = import_package(conn, _package())
    answered = {}
    for key, state in _keys(conn, package_id).items():
        if state == ReviewState.UNREVIEWED:
            confirm(conn, package_id, key)
            answered[key] = ReviewState.CONFIRMED

    path = pathlib.Path(str(conn.execute("PRAGMA database_list").fetchone()["file"]))
    conn.commit()
    conn.close()

    reopened = connect(path)
    try:
        for key, state in answered.items():
            assert _keys(reopened, package_id)[key] == state
        assert len(ClaimRepo(reopened).current(ensure_candidate(reopened))) == len(answered)
    finally:
        reopened.close()


def test_discarding_a_package_deletes_nothing(conn) -> None:
    """Its rejections are the only thing that stops those lines being
    proposed again by the next import of the same documents."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))
    reject(conn, package_id, key)

    discard(conn, package_id)

    assert conn.execute("SELECT COUNT(*) c FROM intake_claim").fetchone()["c"] == 1
    status = conn.execute(
        "SELECT status FROM intake_package WHERE id = ?", (package_id,)
    ).fetchone()
    assert status["status"] == "DISCARDED"


def test_confirming_the_same_claim_twice_revises_rather_than_duplicates(conn) -> None:
    """A claim may already have prepared an application somebody sent, so the
    earlier revision is retired rather than overwritten."""
    package_id = import_package(conn, _package(claims=[_role("cv", "2021-03")]))
    key = next(iter(_keys(conn, package_id)))
    confirm(conn, package_id, key)

    confirm(conn, package_id, key, text="Integration engineer at Acme, billing")

    repo = ClaimRepo(conn)
    candidate_id = ensure_candidate(conn)
    current = repo.current(candidate_id)
    assert len(current) == 1
    assert current[0].text == "Integration engineer at Acme, billing"
    assert len(repo.history(candidate_id, key)) == 2


# =========================================================================
# one answer to a disagreement, rather than one answer per sentence about it
# =========================================================================


def test_a_disagreement_is_shown_as_two_sides_rather_than_many_rows(conn) -> None:
    """The wall this exists to prevent.

    Measured on the owner's real documents: 32 of 309 claims in one group. The
    disagreement is a single fact -- did that role end, or is it ongoing --
    and asking somebody to answer it thirty-two times is not a review.
    """
    from career_agent.intake.store import conflict_groups

    package_id = import_package(
        conn,
        _package(
            claims=[
                _role("cv", "2021-03"),
                _role("cv", "2021-03", text="Owned the billing pipeline"),
                _role("li", "2021-04"),
            ]
        ),
    )

    [group] = conflict_groups(conn, package_id)

    # The employer as her DOCUMENT spells it. The group key is a normalised
    # handle (`employment:acme`), and putting that on a screen shows somebody
    # a lower-cased version of a company they worked for.
    assert group["employer"] == "Acme"
    assert group["member_count"] == 3
    assert group["resolved_claim_key"] is None
    # THREE claims, TWO sides. Two is what a person should be shown.
    assert len(group["sides"]) == 2
    ends = sorted(side["end"] for side in group["sides"])
    assert ends == ["2021-03", "2021-04"]
    # And each side says which document stated it, so she is choosing between
    # her own sources rather than between two anonymous dates.
    assert {side["sources"][0] for side in group["sides"]} == {"cv", "li"}


def test_resolving_releases_the_group_and_confirms_nothing(conn) -> None:
    """Agreeing about when a role ran is not the same as standing behind a
    sentence about it."""
    from career_agent.intake.store import conflict_groups, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    chosen = group["sides"][0]["claim_key"]

    released = resolve_conflict(conn, package_id, group["conflict_group"], chosen)

    assert released == 2
    states = _keys(conn, package_id)
    assert ReviewState.CONFLICT not in states.values()
    assert states[chosen] == ReviewState.UNREVIEWED
    # Nothing became a fact about her.
    assert ClaimRepo(conn).current(ensure_candidate(conn)) == []


def test_a_confirmed_claim_carries_the_dates_she_chose(conn) -> None:
    """The whole point of resolving.

    Reading the claim's OWN period after a resolution would write the losing
    document's dates onto a confirmed fact and make the answer mean nothing.
    """
    from career_agent.intake.store import conflict_groups, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    keeps_march = next(s["claim_key"] for s in group["sides"] if s["end"] == "2021-03")
    says_april = next(s["claim_key"] for s in group["sides"] if s["end"] == "2021-04")

    resolve_conflict(conn, package_id, group["conflict_group"], keeps_march)
    # She then confirms the OTHER claim -- the sentence from the other document
    # -- and it must still carry the dates she settled on.
    confirm(conn, package_id, says_april)

    claim = ClaimRepo(conn).current(ensure_candidate(conn))[0]
    assert claim.period_end == "2021-03"


def test_a_resolution_can_be_unmade(conn) -> None:
    """The one delete this schema allows, and why it is safe: a resolution is a
    statement about how to READ two documents, not a claim about her."""
    from career_agent.intake.store import conflict_groups, reopen_conflict, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    chosen = group["sides"][0]["claim_key"]
    resolve_conflict(conn, package_id, group["conflict_group"], chosen)

    reopen_conflict(conn, package_id, group["conflict_group"])

    assert _keys(conn, package_id)[chosen] == ReviewState.CONFLICT
    assert conflict_groups(conn, package_id)[0]["resolved_claim_key"] is None


def test_reopening_a_resolution_keeps_answers_she_already_gave(conn) -> None:
    """A group-level action must not quietly undo an individual decision."""
    from career_agent.intake.store import conflict_groups, reopen_conflict, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    chosen = group["sides"][0]["claim_key"]
    other = group["sides"][1]["claim_key"]
    resolve_conflict(conn, package_id, group["conflict_group"], chosen)
    confirm(conn, package_id, chosen)

    reopen_conflict(conn, package_id, group["conflict_group"])

    states = _keys(conn, package_id)
    assert states[chosen] == ReviewState.CONFIRMED
    assert states[other] == ReviewState.CONFLICT


def test_a_claim_from_another_group_cannot_be_chosen(conn) -> None:
    """Otherwise a resolution could point at dates from an unrelated role."""
    from career_agent.intake.store import conflict_groups, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    outsider = next(k for k, s in _keys(conn, package_id).items() if s == ReviewState.UNREVIEWED)

    with pytest.raises(IntakeReviewError):
        resolve_conflict(conn, package_id, group["conflict_group"], outsider)


def test_a_resolution_survives_a_restart(conn) -> None:
    import pathlib as _pathlib

    from career_agent.intake.store import conflict_groups, resolve_conflict

    package_id = import_package(conn, _package())
    group = conflict_groups(conn, package_id)[0]
    chosen = group["sides"][0]["claim_key"]
    resolve_conflict(conn, package_id, group["conflict_group"], chosen)

    path = _pathlib.Path(str(conn.execute("PRAGMA database_list").fetchone()["file"]))
    conn.commit()
    conn.close()

    reopened = connect(path)
    try:
        assert conflict_groups(reopened, package_id)[0]["resolved_claim_key"] == chosen
    finally:
        reopened.close()

"""Which reading of her documents is in force, and every way that can go wrong.

WHY THIS FILE EXISTS
--------------------
On 2026-09-07 the owner's database held two intake packages, sixty-nine
seconds apart, built from the same CV and the same LinkedIn export by two
versions of the same parser:

    07:23:13  334 claims
    07:24:22  309 claims
    07:24:30  the first was put away, by hand

It came out right because a person noticed. Nothing in the schema said which
was in force, nothing stopped both staying open, and a review spread across
two readings of one career cannot be finished: answering a claim in one leaves
its twin in the other unanswered forever.

Migration 0025 gives that four states and one invariant -- AT MOST ONE ACTIVE
-- and this file is the guard on both. Every package here is SYNTHETIC.

THE INVARIANT THAT IS EASIEST TO BREAK
---------------------------------------
Not "at most one active". That one is loud. The dangerous one is that NONE of
these transitions may touch a claim: superseding, discarding, selecting and
restoring are decisions about which reading is on screen, never about whether
a sentence is true. A lifecycle that quietly confirmed, cleared or dropped an
answer would be this product deciding something about somebody's career, and
it would look exactly like tidying up.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from career_agent.intake import parse_package
from career_agent.intake.models import CURRENT_SCHEMA_VERSION, PackageStatus, ReviewState
from career_agent.intake.store import (
    IntakeReviewError,
    active_package,
    discard,
    import_package,
    restore,
    select,
    summary,
)
from career_agent.storage.db import connect, migrate


@pytest.fixture
def conn(tmp_path: Path):
    """A disposable database. Nothing here ever touches the owner's."""
    connection = connect(tmp_path / "lifecycle.db")
    migrate(connection)
    yield connection
    connection.close()


def _package(*claims: str, source: str = "cv") -> Any:
    """A package holding one EMPLOYMENT claim per name given. Invented."""
    return parse_package(
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "generator": {"kind": "EXTERNAL_AI", "name": "an assistant"},
            "sources": [{"ref": source, "kind": "RESUME", "title": "cv.docx"}],
            "claims": [
                {
                    "type": "EMPLOYMENT",
                    "text": f"Integration Engineer at {name}",
                    "source_ref": source,
                    "employer": name,
                    "period": {"start": {"original": "Jan 2020", "normalized": "2020-01"}},
                    "evidence": {"quote": f"Integration Engineer, {name}, Jan 2020"},
                }
                for name in claims
            ],
        }
    )


def _empty_package() -> Any:
    """A package that declares documents and yields no claims at all."""
    return parse_package(
        {
            "schema_version": CURRENT_SCHEMA_VERSION,
            "generator": {"kind": "EXTERNAL_AI", "name": "an assistant"},
            "sources": [{"ref": "cv", "kind": "RESUME", "title": "cv.docx"}],
            "claims": [],
        }
    )


def _status(conn: Any, package_id: str) -> str:
    row = conn.execute("SELECT status FROM intake_package WHERE id = ?", (package_id,)).fetchone()
    return str(row["status"])


def _superseded_by(conn: Any, package_id: str) -> str | None:
    row = conn.execute(
        "SELECT superseded_by FROM intake_package WHERE id = ?", (package_id,)
    ).fetchone()
    return None if row["superseded_by"] is None else str(row["superseded_by"])


# =========================================================================
# the invariant
# =========================================================================


def test_the_first_package_imported_is_the_one_in_force(conn: Any) -> None:
    first = import_package(conn, _package("Acme"))
    assert _status(conn, first) == PackageStatus.ACTIVE
    assert active_package(conn) == first


def test_a_successor_takes_over_and_the_predecessor_is_kept(conn: Any) -> None:
    """The 2026-09-07 situation, and what the product does with it now.

    SUPERSEDED, not DISCARDED and never deleted. The product retired it; she
    did not, and a screen that told her otherwise would be lying about who
    decided. `superseded_by` names the successor, because "a newer one exists"
    is not an answer to "why is this one not in force".
    """
    first = import_package(conn, _package("Acme", "Beta"))
    second = import_package(conn, _package("Acme"))

    assert active_package(conn) == second
    assert _status(conn, first) == PackageStatus.SUPERSEDED
    assert _superseded_by(conn, first) == second
    assert _superseded_by(conn, second) is None

    # And the predecessor still holds everything it held.
    kept = conn.execute(
        "SELECT COUNT(*) AS n FROM intake_claim WHERE package_id = ?", (first,)
    ).fetchone()
    assert int(kept["n"]) == 2, "superseding a package cost it a claim"


def test_never_two_active_however_many_are_imported(conn: Any) -> None:
    ids = [import_package(conn, _package(f"Employer{n}")) for n in range(5)]
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM intake_package WHERE status = ?", (PackageStatus.ACTIVE,)
    ).fetchone()
    assert int(active["n"]) == 1
    assert active_package(conn) == ids[-1]


def test_selecting_an_older_package_is_reversible_both_ways(conn: Any) -> None:
    """Choosing is not a one-way door. She may go back, and back again."""
    first = import_package(conn, _package("Acme"))
    second = import_package(conn, _package("Beta"))

    select(conn, first)
    assert active_package(conn) == first
    assert _status(conn, second) == PackageStatus.SUPERSEDED
    assert _superseded_by(conn, second) == first
    assert _superseded_by(conn, first) is None, "the package in force is superseded by nothing"

    select(conn, second)
    assert active_package(conn) == second
    assert _status(conn, first) == PackageStatus.SUPERSEDED


# =========================================================================
# INCOMPLETE, and what it protects
# =========================================================================


def test_an_empty_import_lands_incomplete_and_retires_nothing(conn: Any) -> None:
    """The invariant that costs the most if it is missing.

    A re-import that produced nothing -- a truncated file, a parser that read
    no claims -- must not take over from a review she has half finished. It
    cannot: an empty package is INCOMPLETE, and INCOMPLETE never supersedes.
    """
    good = import_package(conn, _package("Acme"))
    broken = import_package(conn, _empty_package())

    assert _status(conn, broken) == PackageStatus.INCOMPLETE
    assert _status(conn, good) == PackageStatus.ACTIVE, "a broken import retired a good review"
    assert active_package(conn) == good


def test_an_incomplete_package_cannot_be_put_in_force(conn: Any) -> None:
    import_package(conn, _package("Acme"))
    broken = import_package(conn, _empty_package())
    with pytest.raises(IntakeReviewError, match="no claims"):
        select(conn, broken)


# =========================================================================
# discard and restore
# =========================================================================


def test_discarding_the_active_package_leaves_nothing_in_force(conn: Any) -> None:
    """Deliberately. Promoting the next one along would be the product choosing
    which reading of her career to use because she declined one."""
    first = import_package(conn, _package("Acme"))
    second = import_package(conn, _package("Beta"))
    assert active_package(conn) == second

    discard(conn, second)
    assert active_package(conn) is None
    assert _status(conn, first) == PackageStatus.SUPERSEDED, "the older one was promoted for her"


def test_a_discarded_package_cannot_be_selected_until_it_is_restored(conn: Any) -> None:
    first = import_package(conn, _package("Acme"))
    import_package(conn, _package("Beta"))
    discard(conn, first)
    with pytest.raises(IntakeReviewError, match="[Rr]estore"):
        select(conn, first)


def test_restoring_with_nothing_in_force_puts_it_in_force(conn: Any) -> None:
    only = import_package(conn, _package("Acme"))
    discard(conn, only)
    assert active_package(conn) is None

    assert restore(conn, only) == PackageStatus.ACTIVE
    assert active_package(conn) == only


def test_restoring_beside_a_live_review_does_not_replace_it(conn: Any) -> None:
    """A helpful default must never override a stated answer.

    She is working in one reading; taking another out of the drawer is not
    saying she wants to switch to it. It lands SUPERSEDED and there is a
    button.
    """
    first = import_package(conn, _package("Acme"))
    second = import_package(conn, _package("Beta"))
    discard(conn, first)

    assert restore(conn, first) == PackageStatus.SUPERSEDED
    assert active_package(conn) == second


def test_restore_refuses_a_package_that_was_never_put_away(conn: Any) -> None:
    only = import_package(conn, _package("Acme"))
    with pytest.raises(IntakeReviewError, match="put away"):
        restore(conn, only)


# =========================================================================
# what none of it touches
# =========================================================================


def test_no_transition_confirms_corrects_or_drops_a_single_claim(conn: Any) -> None:
    """The dangerous invariant, walked end to end.

    Every state a package can reach, in one sequence, with the claim census
    read before and after. A lifecycle that tidied an answer away would look
    exactly like housekeeping and would be this product deciding something
    about her career.
    """
    first = import_package(conn, _package("Acme", "Beta", "Gamma"))
    before = summary(conn, first)
    rows_before = conn.execute(
        "SELECT claim_key, review_state, payload_json FROM intake_claim"
        " WHERE package_id = ? ORDER BY claim_key",
        (first,),
    ).fetchall()

    second = import_package(conn, _package("Delta"))  # first -> SUPERSEDED
    select(conn, first)  # first -> ACTIVE
    select(conn, second)  # first -> SUPERSEDED
    discard(conn, first)  # first -> DISCARDED
    restore(conn, first)  # first -> SUPERSEDED

    assert summary(conn, first) == before
    rows_after = conn.execute(
        "SELECT claim_key, review_state, payload_json FROM intake_claim"
        " WHERE package_id = ? ORDER BY claim_key",
        (first,),
    ).fetchall()
    assert [tuple(r) for r in rows_after] == [tuple(r) for r in rows_before]
    assert all(str(r["review_state"]) == ReviewState.UNREVIEWED for r in rows_after)


def test_nothing_in_the_lifecycle_produces_a_verified_claim(conn: Any) -> None:
    """`verified_claim` is empty before and after. Confirmation is an act."""
    first = import_package(conn, _package("Acme"))
    second = import_package(conn, _package("Beta"))
    select(conn, first)
    discard(conn, second)
    restore(conn, second)

    rows = conn.execute("SELECT COUNT(*) AS n FROM verified_claim").fetchone()
    assert int(rows["n"]) == 0, "a package moved between states and a claim became verified"


def test_a_historical_package_stays_inspectable(conn: Any) -> None:
    """Discarded is not gone. Its rows are what stop those lines being
    proposed again by the next import, and its review is still readable."""
    first = import_package(conn, _package("Acme", "Beta"))
    import_package(conn, _package("Gamma"))
    discard(conn, first)

    row = conn.execute("SELECT * FROM intake_package WHERE id = ?", (first,)).fetchone()
    assert row is not None
    assert int(row["claim_count"]) == 2
    assert sum(summary(conn, first).values()) == 2


def test_selecting_a_package_that_does_not_exist_says_so(conn: Any) -> None:
    with pytest.raises(IntakeReviewError, match="no such package"):
        select(conn, "01ZZZZZZZZZZZZZZZZZZZZZZZZ")

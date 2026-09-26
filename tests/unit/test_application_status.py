"""`has_applied` is derived, and derivation is what makes it incapable of lying.

`domain/application.py` deliberately stores no `applied` boolean: the answer is
computed every time from (status, applied_at). These tests are the proof that
the two stored facts and the derived one cannot contradict each other, for
every status and both date states -- which is the whole argument for not having
the column.
"""

import itertools

import pytest

from career_agent.domain.application import (
    POST_APPLICATION_STATUSES,
    STATUS_ORDER,
    ApplicationStatus,
    has_applied,
    normalise_application_state,
)

#: Every status, listed once. `STATUS_ORDER` is the canonical ten.
ALL_STATUSES = STATUS_ORDER
A_DATE = "2026-08-14"
TODAY = "2026-09-04"


def test_status_order_covers_the_whole_vocabulary() -> None:
    """A guard on the guard: if a status is added and not ordered, the
    parametrised tests below would silently stop covering it."""
    assert set(ALL_STATUSES) == set(ApplicationStatus)
    assert len(ALL_STATUSES) == 9, "TO_APPLY was merged into SHORTLISTED (migration 0044)"


# --- has_applied ------------------------------------------------------------


@pytest.mark.parametrize("status", sorted(POST_APPLICATION_STATUSES))
def test_post_application_statuses_imply_an_application_whatever_the_date(
    status: ApplicationStatus,
) -> None:
    """You cannot be at INTERVIEW without having applied, and a missing date is
    a bookkeeping gap rather than evidence that you did not."""
    assert has_applied(status, None) is True
    assert has_applied(status, A_DATE) is True


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_any_status_with_a_date_counts_as_applied(status: ApplicationStatus) -> None:
    """The date is the other half of the derivation. REJECTED is the status
    this exists for: it means both 'they turned me down' and 'I passed', and
    only the date separates them."""
    assert has_applied(status, A_DATE) is True


def test_pre_application_statuses_without_a_date_are_not_applied() -> None:
    for status in (
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.SHORTLISTED,
    ):
        assert has_applied(status, None) is False


# --- normalise_application_state -------------------------------------------


def test_moving_to_applied_defaults_the_date() -> None:
    status, applied_at = normalise_application_state(
        ApplicationStatus.APPLIED, None, default_applied_at=TODAY
    )
    assert status is ApplicationStatus.APPLIED
    assert applied_at == TODAY
    assert has_applied(status, applied_at) is True


def test_an_existing_date_is_never_overwritten_by_the_default() -> None:
    _, applied_at = normalise_application_state(
        ApplicationStatus.INTERVIEW, A_DATE, default_applied_at=TODAY
    )
    assert applied_at == A_DATE


@pytest.mark.parametrize(
    "status",
    [
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.SHORTLISTED,
    ],
)
def test_stepping_back_from_applied_keeps_the_date(status: ApplicationStatus) -> None:
    """The user moved APPLIED -> SHORTLISTED. They did not un-apply.

    THIS TEST REPLACES ONE THAT ASSERTED THE OPPOSITE, and the reversal is the
    point rather than an accommodation. The old rule read: keeping the date
    would leave `has_applied` saying yes about a job the person stepped back
    from. That is true, and it is the correct answer -- they DID apply. The
    date records an event, not a stage, and no later move can make the event
    not have happened.

    What the old rule actually cost: on the board, moving a card from Applied
    to Interested is one drag, and it silently deleted the day you applied.
    That is the single most expensive fact this product stores, destroyed by
    the gesture the board most invites. ADR-0012.

    Parameterised over every pre-application status because the old
    behaviour was a property of the whole branch, not of SHORTLISTED.
    """
    resolved, applied_at = normalise_application_state(status, A_DATE, default_applied_at=TODAY)
    assert resolved is status
    assert applied_at == A_DATE, "a status move erased the date the person applied"
    assert has_applied(resolved, applied_at) is True


def test_a_status_that_never_had_a_date_still_has_none() -> None:
    """Keeping a date must not become inventing one. The pre-application
    statuses gain nothing; only the post-application ones default."""
    for status in (
        ApplicationStatus.DISCOVERED,
        ApplicationStatus.SHORTLISTED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.ARCHIVED,
    ):
        assert normalise_application_state(status, None, default_applied_at=TODAY) == (
            status,
            None,
        )


def test_rejected_keeps_whatever_date_it_has_and_gains_none() -> None:
    assert normalise_application_state(
        ApplicationStatus.REJECTED, A_DATE, default_applied_at=TODAY
    ) == (ApplicationStatus.REJECTED, A_DATE)
    assert normalise_application_state(
        ApplicationStatus.REJECTED, None, default_applied_at=TODAY
    ) == (ApplicationStatus.REJECTED, None)


@pytest.mark.parametrize(
    ("status", "applied_at"), list(itertools.product(ALL_STATUSES, [None, A_DATE]))
)
def test_normalisation_is_idempotent(status: ApplicationStatus, applied_at: str | None) -> None:
    """Both repairs are one-directional, so applying them twice is applying
    them once. A normaliser that oscillated would make every stored state
    depend on how many times it had been saved."""
    once = normalise_application_state(status, applied_at, default_applied_at=TODAY)
    twice = normalise_application_state(*once, default_applied_at=TODAY)
    assert twice == once


@pytest.mark.parametrize(
    ("status", "applied_at"), list(itertools.product(ALL_STATUSES, [None, A_DATE]))
)
def test_status_and_has_applied_cannot_contradict(
    status: ApplicationStatus, applied_at: str | None
) -> None:
    """The invariant, stated over the whole space: after normalisation, the
    derived answer agrees with the two facts it was derived from, for all ten
    statuses and both date states.

    Unchanged by ADR-0012, and that is worth noticing: the invariant was never
    what the clearing branch protected. `has_applied` reads the pair it is
    given, so it cannot contradict a pair that keeps its date any more than it
    could contradict one that dropped it."""
    resolved_status, resolved_date = normalise_application_state(
        status, applied_at, default_applied_at=TODAY
    )
    expected = resolved_status in POST_APPLICATION_STATUSES or resolved_date is not None
    assert has_applied(resolved_status, resolved_date) == expected


@pytest.mark.parametrize("status", sorted(POST_APPLICATION_STATUSES))
def test_a_post_application_status_always_leaves_a_date_behind(
    status: ApplicationStatus,
) -> None:
    """Normalisation never leaves a dateless APPLIED: the date is what makes
    the state recoverable if the status is later edited by hand."""
    _, applied_at = normalise_application_state(status, None, default_applied_at=TODAY)
    assert applied_at == TODAY

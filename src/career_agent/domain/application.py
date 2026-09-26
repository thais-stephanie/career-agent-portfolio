"""Application tracking: where the human is with one job.

Deliberately separate from ``CollectionStatus``. Those two vocabularies both
contain the word ``DISCOVERED`` and they mean different things:

    CollectionStatus    where the PIPELINE has taken this posting
                        (FETCHED, NORMALISED, FINGERPRINTED, CLOSED...)

    ApplicationStatus   where the PERSON has taken this posting
                        (SHORTLISTED, APPLIED, INTERVIEW...)

A job can be ``CLOSED`` by the collector because the board took it down while
the candidate is still at ``INTERVIEW``. One field could not hold both facts,
so there are two.

**There is no `applied` boolean.** ``has_applied`` is derived, every time, from
the status and the date, which is what makes the two incapable of contradicting
each other. `§25 proof 16` is a test over this module, not a convention.

**The date outlives the status.** ``applied_at`` records that an application
was sent, so once set it survives every ordinary move in either direction, and
only an explicit "I did not actually apply" clears it. ADR-0012.
"""

from __future__ import annotations

from enum import StrEnum


class ApplicationStatus(StrEnum):
    """The canonical workflow vocabulary. One field, nine values, no synonyms.

    There was a tenth, ``TO_APPLY`` ("decided to apply; not yet applied").
    Nobody could say how it differed from Interested, so it was merged into
    ``SHORTLISTED`` by migration 0044. See ``LEGACY_STATUSES``.
    """

    #: Collected and scored. Nothing has been decided.
    DISCOVERED = "DISCOVERED"
    #: Interested: worth a real read, and possibly an application.
    SHORTLISTED = "SHORTLISTED"
    #: The application was submitted -- BY THE PERSON. This system never
    #: submits one, and no code path sets this status without a human action.
    APPLIED = "APPLIED"
    INTERVIEW = "INTERVIEW"
    OFFER = "OFFER"
    HIRED = "HIRED"
    #: Rejected. May be either side's rejection, and may happen before or
    #: after applying -- which is exactly why it is not in
    #: ``POST_APPLICATION_STATUSES``.
    REJECTED = "REJECTED"
    #: The candidate pulled out. Only meaningful after applying.
    WITHDRAWN = "WITHDRAWN"
    #: Filed away. Not interested, not rejected.
    ARCHIVED = "ARCHIVED"


#: Statuses that cannot be true unless an application was actually sent.
#: ``REJECTED`` is absent on purpose: "they turned me down" and "I dismissed
#: it" are both spelled REJECTED by real users, and only one of them implies
#: an application. For that one status the date decides.
POST_APPLICATION_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.APPLIED,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.OFFER,
        ApplicationStatus.HIRED,
        ApplicationStatus.WITHDRAWN,
    }
)

#: Statuses that mean the candidate is done with this job, one way or another.
TERMINAL_STATUSES: frozenset[ApplicationStatus] = frozenset(
    {
        ApplicationStatus.HIRED,
        ApplicationStatus.REJECTED,
        ApplicationStatus.WITHDRAWN,
        ApplicationStatus.ARCHIVED,
    }
)

#: The order the Table sorts by and a future Kanban would lay out.
STATUS_ORDER: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.DISCOVERED,
    ApplicationStatus.SHORTLISTED,
    ApplicationStatus.APPLIED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.HIRED,
    ApplicationStatus.REJECTED,
    ApplicationStatus.WITHDRAWN,
    ApplicationStatus.ARCHIVED,
)


#: Retired values that may still appear in the append-only history, and what
#: each one means now. Current rows never hold one (migration 0044 moved
#: them); `job_application_event` rows written before it still do, because
#: history is never rewritten. Every reader of a stored status goes through
#: `canonical_status`.
LEGACY_STATUSES: dict[str, ApplicationStatus] = {"TO_APPLY": ApplicationStatus.SHORTLISTED}


def canonical_status(value: str | None) -> str | None:
    """A stored status as today's vocabulary spells it (legacy values mapped)."""
    if value is None:
        return None
    legacy = LEGACY_STATUSES.get(str(value))
    return legacy.value if legacy is not None else str(value)


#: The rungs of a ladder, and only the rungs.
#:
#: `STATUS_ORDER` above is a DISPLAY order: it lists every status, terminal
#: ones included, so a board and a sorted column have somewhere to put them.
#: Using it to decide whether an application "progressed" would count
#: APPLIED -> REJECTED as forward movement, because REJECTED sorts after
#: APPLIED, and a dashboard would cheerfully report a week of rejections as
#: progress.
#:
#: REJECTED, WITHDRAWN and ARCHIVED are deliberately absent. They are OUTCOMES
#: rather than rungs: an application that reached one has stopped, and it did
#: not stop by advancing.
ADVANCEMENT_LADDER: tuple[ApplicationStatus, ...] = (
    ApplicationStatus.DISCOVERED,
    ApplicationStatus.SHORTLISTED,
    ApplicationStatus.APPLIED,
    ApplicationStatus.INTERVIEW,
    ApplicationStatus.OFFER,
    ApplicationStatus.HIRED,
)


def moved_forward(from_status: str | None, to_status: str | None) -> bool:
    """Did this move go UP the ladder?

    False for anything involving a status that is not on it, which is the
    honest answer: a move to REJECTED is news and it is not progress, and a
    move FROM it is somebody correcting a mistake rather than an application
    advancing.

    False for a move to the same rung, so re-saving a status is not an event
    a dashboard counts. That is section 23's "do not count arbitrary edits".
    """
    ladder = {status.value: index for index, status in enumerate(ADVANCEMENT_LADDER)}
    start = ladder.get(str(canonical_status(from_status)))
    end = ladder.get(str(canonical_status(to_status)))
    if start is None or end is None:
        return False
    return end > start


def has_applied(status: ApplicationStatus, applied_at: str | None) -> bool:
    """Was an application actually sent?

    Derived from the two stored facts, never stored itself. A caller cannot
    set ``has_applied`` to something the status and date disagree with,
    because there is nothing to set.
    """
    if status in POST_APPLICATION_STATUSES:
        return True
    return applied_at is not None


def requires_applied_at(status: ApplicationStatus) -> bool:
    """True when moving to this status without a date would lose information."""
    return status in POST_APPLICATION_STATUSES


def normalise_application_state(
    status: ApplicationStatus,
    applied_at: str | None,
    *,
    default_applied_at: str,
) -> tuple[ApplicationStatus, str | None]:
    """Return a (status, applied_at) pair that cannot contradict itself.

    ONE repair, one-directional, so the function is idempotent: a
    post-application status with no date gets ``default_applied_at``. The
    caller supplies the date rather than the function reading a clock, so this
    stays pure and testable.

    **A date is never dropped here, at any status.** It used to be: moving
    APPLIED back to SHORTLISTED or DISCOVERED set the column to
    NULL. The reasoning was that keeping it would make ``has_applied`` say yes
    about a job the person had stepped back from -- which reads sensibly until
    you notice what the two fields actually mean.

    ``applied_at`` is not a property of the status. It is the date an
    application was **sent**, and that is a fact about the past which no later
    move can make untrue. Reconsidering the stage is a statement about where
    the conversation is; it is not a statement that you never applied. On the
    board it is one drag, and a drag is not a claim about history.

    So the two questions are separated. The status says where this is now, the
    date says whether and when something was sent, and the only thing that
    clears the date is a person saying so --
    :meth:`ApplicationRepo.set_applied_at` with ``None``, which the interface
    puts behind a confirmation. See ADR-0012.

    ``REJECTED`` keeps whatever date it has and gains none, which is what lets
    it mean both "they said no" and "I passed"; that half is unchanged, and
    now every other status behaves the same way.
    """
    if requires_applied_at(status):
        return status, applied_at or default_applied_at
    return status, applied_at

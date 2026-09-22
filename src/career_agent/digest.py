"""What is worth looking at today, defined once for the terminal and the screen.

`career-agent daily` and `GET /api/daily` answer the same question, and before
this module they answered it twice. Two copies of a list of sections is how the
terminal and the interface start disagreeing about what "new" means -- and the
disagreement would be invisible, because each one is internally consistent.

THREE RULES, and they are the whole design.

**Nothing here ranks anything.** Every section is a FILTER over the same
deterministic match score the cards use, and the rows inside it come back in
that score's order. A digest that invented its own ordering would be a second
opinion nobody could trace, which is what ADR-0004 refuses. The first version
of this sorted by date, and the first thing a person read each morning was a
posting scoring zero.

**Recency decides MEMBERSHIP, never position.** "New" says which postings are
in the section. The score says which is at the top of it.

**A date says which kind of date it is.** `posted_at` is what a board
published; `first_seen_at` is when this machine first held it; `last_reviewed_at`
is when a person last looked. Three different facts, and no two of them may be
rendered as one word. The "since you last looked" section is built on
`first_seen_at` for exactly that reason: a board that publishes no dates would
otherwise have every one of its postings permanently invisible to the question.

**An empty section says so.** It keeps its heading and reports nothing today,
because a digest that silently omits a section is indistinguishable from one
that failed to look.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from career_agent.storage.mvp_repo import JobFilter


@dataclass(frozen=True, slots=True)
class Section:
    """One heading, one sentence, one filter."""

    key: str
    title: str
    lead: str
    job_filter: JobFilter


def default_filter(show: int) -> JobFilter:
    """The DEFAULT view: what a person sees when they open the product.

    ALL FOUR narrowings, because the digest should agree with the screen. A
    digest that counted postings the list hides would be describing a different
    product from the one in front of her.

    It carried two for a while and that was a real defect rather than an
    omission of degree. `include_unresolved` and `include_excluded_seniority`
    arrived in V1.5 as narrowings the web layer applies to every discovery
    view; this function was written before them and was never updated, so Home
    was offering postings at levels she had explicitly kept off the list --
    INTERN, STAFF, PRINCIPAL and LEAD in her own configuration -- and postings
    whose eligibility nobody has resolved, both of which the Jobs list hides.
    "Best matches right now" was answering a different question from the one
    the rest of the product answers.

    The unresolved ones are not lost, but WHERE they resurface depends on who
    is asking. `sections` below builds one for them that says they are
    unresolved and explains that one recruiter question each would settle it;
    the terminal digest prints it. The web Home asks for three sections by
    name and that is not one of them, so there they are reachable through the
    Jobs list's own `include_unresolved` control, which names itself and says
    what it is holding back.

    Either way the point is the same: a posting nobody can confirm she may
    take is shown as such, rather than mixed silently into the best matches.
    """
    return JobFilter(
        include_ineligible=False,
        include_off_target=False,
        include_unresolved=False,
        include_excluded_seniority=False,
        group_duplicates=True,
        limit=show,
    )


def sections(
    config: Any,
    *,
    days: int = 7,
    show: int = 8,
    last_reviewed_at: str | None = None,
) -> tuple[Section, ...]:
    """Every section, in reading order, for one configuration.

    `last_reviewed_at` turns the first section from "posted in the last N days"
    -- a guess about a calendar -- into "arrived since you last looked", which
    is a fact about this reader. When it is absent the calendar answer is used,
    because a person who has never marked a review has no checkpoint and
    inventing one would put a date on the screen that means nothing.
    """
    base = default_filter(show)
    shortlist = int(config.thresholds.shortlist_min_score)

    if last_reviewed_at:
        first = Section(
            key="since_last_review",
            title="Since you last looked",
            lead=(
                "Postings this machine first held after your last review. That is "
                "when WE noticed them, not when the employer wrote them -- the two "
                "are different facts and neither stands in for the other."
            ),
            job_filter=replace(base, first_seen_after=last_reviewed_at, sort="score"),
        )
    else:
        first = Section(
            key="recent",
            title=("New in the last day" if days == 1 else f"New in the last {days} days"),
            lead=(
                "Filtered by the date the board published, ORDERED by the same match "
                "score as everywhere else. Recency decides what is in this section; "
                "it does not decide what is at the top of it."
            ),
            job_filter=replace(base, posted_within_days=days, sort="score"),
        )

    return (
        first,
        Section(
            key="best",
            title="Best matches right now",
            lead="The same score the cards show, in the same order. Nothing is re-ranked here.",
            job_filter=replace(base, sort="score"),
        ),
        Section(
            key="unresolved",
            title="Waiting on one answer",
            lead=(
                "Strong matches whose eligibility nobody has resolved. One recruiter "
                "question each would settle them."
            ),
            job_filter=replace(
                base,
                eligibility=("UNRESOLVED",),
                min_score=shortlist,
                sort="score",
            ),
        ),
        # Migration 0020 gave `domestic.context` a column of its own, so this
        # section exists now. It could not before: the reading lived in
        # `result_json`, which no filter may point at, because that blob holds
        # evidence quotes and a posting could have satisfied the filter using
        # words from its own description.
        #
        # IT IS NOT A REJECTION LIST. Every posting in it has UNRESOLVED
        # eligibility -- the employer has said nothing about who it may hire.
        # The section exists so somebody abroad knows to ASK, and the heading
        # says verify rather than skip.
        Section(
            key="us_context",
            title="Likely United States employment -- verify before applying",
            lead=(
                "These offer something that usually accompanies employment inside "
                "the United States and do not say they hire elsewhere. That is "
                "CONTEXT, not a refusal: their eligibility is unresolved, they stay "
                "visible everywhere else, and one question to a recruiter settles "
                "each of them."
            ),
            job_filter=replace(
                base,
                employment_context=("LIKELY_US_DOMESTIC",),
                eligibility=("UNRESOLVED",),
                sort="score",
            ),
        ),
        Section(
            key="tracking",
            title="You are tracking",
            lead=("Anything you saved or moved. These stay visible whatever a rescore decides."),
            job_filter=replace(
                base,
                saved_only=True,
                include_ineligible=True,
                include_off_target=True,
                # Redundant beside the two above, and stated anyway: this is
                # the section that is ABOUT her decisions, so it is the one
                # section that asks for the tracking exemption. A narrowing
                # added here later must not quietly hide a saved posting.
                exempt_tracked=True,
            ),
        ),
    )

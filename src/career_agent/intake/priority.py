"""Where to start, when three hundred statements are waiting.

WHY THIS EXISTS
---------------
The owner's package holds 309 proposals and every one of them is unanswered.
"309 items to review" is not a task; it is a wall, and a wall is what people
close the tab on. The grouped overview already refuses to render them all at
once, which stops the screen from being a wall. It does not answer the
question underneath: **of these three hundred, which ones actually matter
before I can use this product at all?**

PRIORITY IS NAVIGATION, NEVER TRUTH
------------------------------------
Nothing here decides an answer, ranks a claim's credibility, or treats an
early step as more likely to be true than a late one. A step is a FILTER over
the same claims, in the same states, answered by the same four actions. Move
a claim between steps and its truth does not move; only the order she meets
it in does.

The one exception that is not an exception: `SETTLE_DISAGREEMENTS` is first
because it is MECHANICALLY blocking. `store.confirm` refuses a claim in an
unresolved conflict group, so those claims cannot be answered until the
disagreement is settled once. That is not an opinion about importance -- it
is the reason the other steps would fail.

EVERY CLAIM IS IN EXACTLY ONE STEP
-----------------------------------
First match wins, and the last step is a catch-all. That partition is what
makes the counts honest: the steps sum to the package, so "12 of 32 answered"
in a step and "40 of 309 answered" overall cannot disagree, and there is no
denominator anybody had to invent. `test_intake_priority.py` asserts the sum.

WHAT THE MISSION ASKED FOR AND THE DATA CANNOT SUPPORT
-------------------------------------------------------
A "tools and systems" step was specified. Measured on the owner's real
package: `tools` is present on all 309 claims and EMPTY on all 309, and the
`TOOL` claim type has no rows. A step that is always empty is a promise the
screen cannot keep, so tools are folded into `SKILLS_AND_TOOLS` and the label
says both. If a package ever carries them, they are already in that step.

"Principal projects" is the same story: `PROJECT` has no rows in her package.
It is named in `EARLIER_WORK`'s membership rather than given a step of its
own, so a package that does carry projects still routes them somewhere true.

WHY THE JOB SHE CAME FROM IS NOT A STEP
----------------------------------------
"Evidence required by a currently selected job" overlaps every other step by
nature -- the claims that mention a requirement are recent work, or outcomes,
or skills. Making it a step would steal them from the step they belong to and
break the partition, so it is reported BESIDE the steps as a lens over the
same claims. `focus_matches` is that lens.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from career_agent.intake.models import ReviewState

#: The steps, in reading order. The key is stable and travels to the client;
#: the sentence a reader sees is looked up from it in `i18n.js`, because a
#: label composed here would be product-authored text outside the gate.
SETTLE_DISAGREEMENTS = "SETTLE_DISAGREEMENTS"
NAME_THE_EMPLOYER = "NAME_THE_EMPLOYER"
RECENT_WORK = "RECENT_WORK"
MEASURABLE_OUTCOMES = "MEASURABLE_OUTCOMES"
EARLIER_WORK = "EARLIER_WORK"
SKILLS_AND_TOOLS = "SKILLS_AND_TOOLS"
STUDY_AND_CERTIFICATES = "STUDY_AND_CERTIFICATES"
ANYTHING_ELSE = "ANYTHING_ELSE"

ORDER: tuple[str, ...] = (
    SETTLE_DISAGREEMENTS,
    NAME_THE_EMPLOYER,
    RECENT_WORK,
    MEASURABLE_OUTCOMES,
    EARLIER_WORK,
    SKILLS_AND_TOOLS,
    STUDY_AND_CERTIFICATES,
    ANYTHING_ELSE,
)

#: What "enough to start" means, stated once and never computed twice.
#:
#: Settle the disagreements, name the employers that are missing one, and
#: answer the most recent job. That is a defensible first run: the product can
#: prepare an application from it, and every requirement it cannot evidence
#: will say so honestly rather than looking unmet because a review was
#: unfinished.
#:
#: It is a NAVIGATION claim and the screen says so in those words. Nothing
#: here asserts that the rest does not matter, and no step is ever hidden.
ESSENTIAL: tuple[str, ...] = (SETTLE_DISAGREEMENTS, NAME_THE_EMPLOYER, RECENT_WORK)

#: Claim types that describe WORK, as opposed to what she can do or where she
#: studied. `PROJECT` and `ACHIEVEMENT` have no rows in the owner's package
#: and are named anyway: a package that carries them must route them
#: somewhere true rather than falling through to the catch-all.
WORK_TYPES = frozenset({"EMPLOYMENT", "PROJECT", "ACHIEVEMENT"})
SKILL_TYPES = frozenset({"SKILL", "TOOL"})
STUDY_TYPES = frozenset({"EDUCATION", "CERTIFICATION"})


@dataclass(frozen=True, slots=True)
class Claim:
    """The little a step needs to know. Deliberately not the whole row.

    A step decides WHERE a claim is met, so it may read the claim's kind, its
    employer, its dates and whether it is disputed. It may not read the
    candidate's answer beyond whether one exists, and it never reads evidence
    text: a queue that ordered by what a quote says would be this program
    forming an opinion about which of her sentences is worth more.
    """

    claim_key: str
    claim_type: str
    review_state: str
    conflict_group: str | None
    employer: str | None
    period_start: str | None
    has_metrics: bool

    @property
    def waiting(self) -> bool:
        return self.review_state in ReviewState.ANSWERABLE


def claim_from(row: Mapping[str, Any], payload: Mapping[str, Any]) -> Claim:
    """One database row and its payload, as the little a step needs."""
    period = payload.get("period") or {}
    start = (period.get("start") or {}).get("normalized") if isinstance(period, dict) else None
    return Claim(
        claim_key=str(row["claim_key"]),
        claim_type=str(row["claim_type"]),
        review_state=str(row["review_state"]),
        conflict_group=row["conflict_group"] or None,
        employer=(payload.get("employer") or None),
        period_start=str(start) if start else None,
        has_metrics=bool(payload.get("metrics")),
    )


def most_recent_employer(claims: Iterable[Claim]) -> str | None:
    """The employer whose work started latest, or None when none is datable.

    Read off the claims rather than asked of the candidate, and it is a
    NAVIGATION fact: it decides which job she is offered first, not which job
    is hers. Ties break on the employer name so two roles starting in the same
    month give the same answer on every page load -- a queue that reshuffled
    itself between visits is one nobody can finish.
    """
    best: tuple[str, str] | None = None
    for claim in claims:
        if claim.claim_type not in WORK_TYPES or not claim.employer or not claim.period_start:
            continue
        candidate = (claim.period_start, claim.employer)
        if best is None or candidate > best:
            best = candidate
    return best[1] if best else None


def step_of(claim: Claim, *, unresolved: frozenset[str], newest_employer: str | None) -> str:
    """Which step this claim is met in. First match wins; see the header.

    `unresolved` is the set of conflict groups with NO resolution row. A
    settled group's claims leave this step and rejoin the ordinary queue,
    which is exactly what settling one is for.
    """
    if claim.conflict_group and claim.conflict_group in unresolved:
        return SETTLE_DISAGREEMENTS
    if claim.claim_type in WORK_TYPES and not claim.employer:
        return NAME_THE_EMPLOYER
    if claim.claim_type in WORK_TYPES and newest_employer and claim.employer == newest_employer:
        return RECENT_WORK
    if claim.claim_type in WORK_TYPES and claim.has_metrics:
        return MEASURABLE_OUTCOMES
    if claim.claim_type in WORK_TYPES:
        return EARLIER_WORK
    if claim.claim_type in SKILL_TYPES:
        return SKILLS_AND_TOOLS
    if claim.claim_type in STUDY_TYPES:
        return STUDY_AND_CERTIFICATES
    return ANYTHING_ELSE


def assign(claims: Sequence[Claim], *, unresolved: frozenset[str]) -> dict[str, str]:
    """Every claim's step, keyed by claim key. One pass, one answer each."""
    newest = most_recent_employer(claims)
    return {
        claim.claim_key: step_of(claim, unresolved=unresolved, newest_employer=newest)
        for claim in claims
    }


def plan(claims: Sequence[Claim], *, unresolved: frozenset[str]) -> dict[str, Any]:
    """The whole queue: every step with its counts, and what "enough" means.

    NO STEP IS EVER OMITTED, including the empty ones. A step that disappeared
    when it emptied would make "you have answered everything here" and "this
    kind of statement does not exist in your package" look identical, and
    those are opposite facts.

    Every number here has a denominator somebody can point at: a step's total
    is how many claims are in it, and the steps partition the package. There
    is no completeness score, no level and no percentage of a target nobody
    set.
    """
    assignment = assign(claims, unresolved=unresolved)
    by_key = {claim.claim_key: claim for claim in claims}

    steps: list[dict[str, Any]] = []
    for key in ORDER:
        members = [by_key[k] for k, step in assignment.items() if step == key]
        waiting = sum(1 for claim in members if claim.waiting)
        steps.append(
            {
                "key": key,
                "total": len(members),
                "waiting": waiting,
                "answered": len(members) - waiting,
                # MECHANICALLY blocking, not editorially first. `store.confirm`
                # refuses a claim in an unresolved group, so these really do
                # stop the others rather than merely deserving attention.
                "blocking": key == SETTLE_DISAGREEMENTS,
                "essential": key in ESSENTIAL,
            }
        )

    essential = [step for step in steps if step["essential"]]
    return {
        "steps": steps,
        "essential": {
            "total": sum(step["total"] for step in essential),
            "waiting": sum(step["waiting"] for step in essential),
            "answered": sum(step["answered"] for step in essential),
            "steps": list(ESSENTIAL),
        },
        "progress": {
            "total": len(claims),
            "waiting": sum(1 for claim in claims if claim.waiting),
            "answered": sum(1 for claim in claims if not claim.waiting),
        },
    }


def focus_matches(claims: Sequence[Claim], texts: Mapping[str, str], term: str) -> tuple[str, ...]:
    """Claim keys whose text mentions `term`. A LENS, never a step.

    Case-folded substring, and deliberately nothing cleverer: a fuzzy match
    would quietly decide that a requirement and a sentence are about the same
    thing, which is a judgement about her career. The screen says out loud
    that it is filtering and offers one button to stop.
    """
    needle = term.strip().casefold()
    if not needle:
        return ()
    return tuple(
        claim.claim_key
        for claim in claims
        if needle in (texts.get(claim.claim_key) or "").casefold()
    )

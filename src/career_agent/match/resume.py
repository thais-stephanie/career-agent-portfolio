"""What to lead with, for one posting. Selection and order, never new words.

The foundation section 26 of the V1.3 brief asks for, and it is deliberately
the smallest thing that is useful: given the requirements a posting fired and
the claims the candidate has confirmed, decide which of HER OWN SENTENCES to
put first and say why.

THREE THINGS IT DOES.

**Selects.** A claim that answers nothing this posting asked for is not
promoted to the top of a document about this posting.

**Orders.** A claim answering three requirements outranks one answering one,
and evidence of having DONE the work outranks evidence of having named a tool.

**Explains.** Every position carries the requirements it answers, so a reader
can disagree with the order rather than accept it.

FOUR THINGS IT WILL NOT DO, and none of them is a feature waiting to be built.

**It writes nothing.** Every string it returns came from a `VerifiedClaim` the
candidate personally confirmed or from a requirement label in her own
configuration. There is no rewriting, no summarising and no "strengthening": a
generated sentence would be a fact with no source, which is the failure this
whole product is built around avoiding.

**It never upgrades a claim.** "familiar with Salesforce" does not become
"Salesforce expert", and "helped automate" does not become "led automation",
because nothing here touches `claim.text` at all.

**It hides no gap.** What the posting asked for and nothing answers is returned
beside the selection, in the same object. A resume workspace that showed only
the strengths would be the flattering view `preparation` exists to refuse.

**It scores nothing.** There is no readiness percentage and no "resume
strength". A single figure over this would be a fourth measurement, and
ADR-0004 keeps three.
"""

from __future__ import annotations

from dataclasses import dataclass

from career_agent.domain.claims import VerifiedClaim
from career_agent.match.preparation import Preparation, Readiness

#: How strongly each readiness argues for putting a claim first.
#:
#: MATCHED outranks PARTIAL because having done the work and having used the
#: tool are different answers to the same question, and a document that led
#: with the weaker one would be overstating it. GAP and UNRESOLVED carry no
#: weight at all: no claim is attached to either.
_WEIGHT = {Readiness.MATCHED: 2, Readiness.PARTIAL: 1}


@dataclass(frozen=True, slots=True)
class Suggestion:
    """One confirmed claim, and what it answers in this posting."""

    claim: VerifiedClaim
    #: The requirement labels this claim answered, in the order they fired.
    answers: tuple[str, ...]
    #: The readiness of the strongest requirement it answered.
    strength: Readiness

    @property
    def rank(self) -> tuple[int, int, str]:
        """Sort key. Descending on the first two, ascending on the third.

        The claim key breaks ties, so two claims answering the same things come
        back in the same order every time. An unstable order would make the
        suggestion look like an opinion that changes.
        """
        return (-_WEIGHT.get(self.strength, 0), -len(self.answers), self.claim.claim_key)


@dataclass(frozen=True, slots=True)
class ResumePlan:
    """One posting, and which of her own sentences speak to it."""

    #: Claims that answer something, strongest first.
    lead_with: tuple[Suggestion, ...]
    #: Confirmed claims that answer nothing this posting asked for. Kept and
    #: shown: they are still true, and a person writing a document decides what
    #: belongs in it. This module only says which ones this posting argues for.
    not_relevant: tuple[VerifiedClaim, ...]
    #: Requirement labels with nothing confirmed behind them.
    gaps: tuple[str, ...]
    #: Requirement labels this system could not judge either way.
    unresolved: tuple[str, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "lead_with": len(self.lead_with),
            "not_relevant": len(self.not_relevant),
            "gaps": len(self.gaps),
            "unresolved": len(self.unresolved),
        }


def plan(preparation: Preparation, claims: list[VerifiedClaim]) -> ResumePlan:
    """Which confirmed claims this posting argues for, and in what order.

    Only `verified` claims are considered. A proposal read off a CV and never
    confirmed is a draft, and letting one into a document about somebody's
    career is the whole thing the review step exists to prevent.
    """
    confirmed = {claim.claim_key: claim for claim in claims if claim.verified}

    answered: dict[str, list[str]] = {}
    strength: dict[str, Readiness] = {}
    for requirement in preparation.requirements:
        key = requirement.evidence_key
        if key is None or key not in confirmed:
            continue
        answered.setdefault(key, []).append(requirement.label)
        current = strength.get(key)
        if current is None or _WEIGHT.get(requirement.readiness, 0) > _WEIGHT.get(current, 0):
            strength[key] = requirement.readiness

    suggestions = sorted(
        (
            Suggestion(
                claim=confirmed[key],
                answers=tuple(labels),
                strength=strength.get(key, Readiness.PARTIAL),
            )
            for key, labels in answered.items()
        ),
        key=lambda suggestion: suggestion.rank,
    )

    spare = tuple(claim for key, claim in sorted(confirmed.items()) if key not in answered)

    return ResumePlan(
        lead_with=tuple(suggestions),
        not_relevant=spare,
        gaps=tuple(r.label for r in preparation.of(Readiness.GAP)),
        unresolved=tuple(r.label for r in preparation.of(Readiness.UNRESOLVED)),
    )

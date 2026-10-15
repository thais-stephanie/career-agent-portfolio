"""What a piece of evidence is WORTH to one profile (2026-10-16).

The frame (`evidence.py`) is a fact about the text, shared by every profile.
The weight is a decision, and it is the profile's: versioned here as data,
so changing it makes `work_match` stale and never the shared facts (the
component graph, `components.py`).

The defaults are the owner's decisions of 2026-10-16:

* a duty the role performs is full evidence; a requirement less, an
  experience requirement less again, a tool used, a degree in the field and
  familiarity are weak but real;
* working WITH a team, a company's or a product's business, an artefact
  named, a benefit, the hiring process and boilerplate prove nothing;
* something given TO the role (`we'll teach you`, `com o suporte de`) proves
  nothing;
* pre-sales the role performs is sales; post-sales is sales only with an
  upsell, renewal, negotiation or revenue cue (`Sense.POST_SALES_COMMERCIAL`).

Weights are not dogma: `counted` is what A1+B1 receive (a hit or not), and
`weight` rides on the evidence for the explanation and for a future score.
"""

from __future__ import annotations

from typing import Any

from career_agent.matching_v2.evidence import (
    Certainty,
    Direction,
    EvidenceFrame,
    EvidenceType,
    Owner,
    Sense,
)

VERSION = "1"

DEFAULT: dict[str, Any] = {
    "type": {
        EvidenceType.DUTY: 1.0,
        EvidenceType.REQUIREMENT_SKILL: 0.8,
        EvidenceType.REQUIREMENT_EXPERIENCE: 0.65,
        EvidenceType.TOOL_EXPERIENCE: 0.45,
        EvidenceType.DEGREE_IN_FIELD: 0.35,
        EvidenceType.FAMILIARITY: 0.2,
        EvidenceType.COLLABORATION: 0.0,
        EvidenceType.ARTIFACT_ONLY: 0.0,
        EvidenceType.DOMAIN_CONTEXT: 0.0,
        EvidenceType.COMPANY_CONTEXT: 0.0,
        EvidenceType.PRODUCT_CONTEXT: 0.0,
        EvidenceType.BENEFIT: 0.0,
        EvidenceType.APPLICATION_PROCESS: 0.0,
        EvidenceType.BOILERPLATE: 0.0,
        EvidenceType.UNKNOWN: 0.0,
    },
    "sense": {
        Sense.NAMED: 1.0,
        Sense.UNKNOWN: 1.0,
        Sense.PRE_SALES: 1.0,
        Sense.POST_SALES_COMMERCIAL: 1.0,
        Sense.POST_SALES: 0.0,
        Sense.MERCHANDISE: 0.0,
        Sense.DEDUCTION: 0.0,
        Sense.PHYSICAL: 0.0,
        Sense.EDUCATION_LEVEL: 0.0,
        Sense.VERB: 0.0,
        Sense.PROPER_NAME: 0.0,
        Sense.OTHER: 0.0,
    },
    "owner": {
        Owner.ROLE: 1.0,
        Owner.CANDIDATE: 1.0,
        Owner.UNKNOWN: 1.0,
        Owner.COMPANY: 0.0,
        Owner.PRODUCT: 0.0,
        Owner.SYSTEM: 0.0,
        Owner.CUSTOMER: 0.0,
        Owner.PARTNER: 0.0,
        Owner.OTHER_TEAM: 0.0,
    },
    "direction": {
        Direction.ROLE_PROVIDES: 1.0,
        Direction.UNKNOWN: 1.0,
        Direction.NONE: 1.0,
        Direction.ROLE_RECEIVES: 0.0,
        Direction.OTHER_PROVIDES: 0.0,
        Direction.OTHER_RECEIVES: 0.0,
    },
    "certainty": {Certainty.HIGH: 1.0, Certainty.MEDIUM: 0.75, Certainty.LOW: 0.5},
    #: A frame counts (becomes a hit) at this weight or above: weak evidence
    #: that is also uncertain (a degree among alternatives, 0.35 x 0.5) does not.
    "counted_at": 0.2,
    #: Below this a counted hit is never a core duty in the score (its
    #: section is read as SECONDARY): A1+B1 unchanged, the weight reaches the
    #: score through the channel it already has.
    "core_at": 0.8,
}


def policy_for(config: object) -> dict[str, Any]:
    """The profile's policy: its own when it states one, else the default."""
    own = getattr(config, "evidence_policy", None)
    return own if isinstance(own, dict) else DEFAULT


def weight(frame: EvidenceFrame, policy: dict[str, Any] = DEFAULT) -> float:
    return (
        policy["type"][frame.evidence_type]
        * policy["sense"][frame.sense]
        * policy["owner"][frame.owner]
        * policy["direction"][frame.direction]
        * policy["certainty"][frame.certainty]
    )


def counted(frame: EvidenceFrame, policy: dict[str, Any] = DEFAULT) -> bool:
    return weight(frame, policy) >= policy["counted_at"]


def digest_view(policy: dict[str, Any]) -> dict[str, Any]:
    """JSON-ready form of a policy (enum keys as strings), for its digest."""
    return {
        k: ({str(kk): vv for kk, vv in v.items()} if isinstance(v, dict) else v)
        for k, v in policy.items()
    }


def explain(frame: EvidenceFrame, policy: dict[str, Any] = DEFAULT) -> str:
    """`sales -> owner OTHER_TEAM, COLLABORATION, weight 0.00, not counted`."""
    w = weight(frame, policy)
    verdict = "counted" if w >= policy["counted_at"] else "not counted"
    return (
        f"{frame.concept_id} -> type {frame.evidence_type}, owner {frame.owner}, "
        f"direction {frame.direction}, sense {frame.sense}, section {frame.section_context}, "
        f"certainty {frame.certainty} -> weight {w:.2f}, {verdict}"
        + (f" ({frame.because})" if frame.because else "")
    )

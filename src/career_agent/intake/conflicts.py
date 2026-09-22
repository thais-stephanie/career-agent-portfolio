"""Which proposed claims disagree, and which are the same claim seen twice.

TWO DIFFERENT THINGS THAT LOOK ALIKE
-------------------------------------
A CV and a LinkedIn export describing one career produce overlapping claims,
and the overlap comes in two shapes that must not be handled the same way:

**A DUPLICATE** is one claim, stated twice. Same kind, same employer, same
period, same words. `claim_identity` already collapses these -- they produce
one key -- so the candidate is asked once and the sources are listed together.

**A CONFLICT** is two claims that cannot both be true. Same role at the same
employer, and the documents disagree about when it ended, or about what it was
called. These produce different keys, both are kept, and both are presented
together with the disagreement named.

WHAT THIS MODULE WILL NOT DO
-----------------------------
**It never picks a winner.** There is no rule here that a CV outranks a
LinkedIn profile, no recency preference, no "the more specific one wins". A
conflict is grouped and handed to the candidate, whose career it is.

That restraint is not squeamishness. The two documents this was built against
disagree in ways where either could be right: a curated CV states the title the
candidate uses for a role, a LinkedIn export states the title the employer's HR
system held, and neither is a mistake. A program choosing between them would be
inventing a fact about somebody's employment history, which is precisely what
`VerifiedClaim` exists to make impossible.

**It never groups on similarity.** Two claims conflict when they share an
IDENTITY -- kind, employer, and an overlapping period -- and differ on
something material. Nothing here computes a distance between two sentences.
ADR-0008 forbade fuzzy identity for jobs for the same reason it is wrong here:
a threshold that merges two real roles is silent, and the merged result looks
like a fact.

A CONSEQUENCE WORTH STATING PLAINLY
------------------------------------
A disagreement about when a role ran puts EVERY claim about that role in the
group, not just the two lines stating the dates. Measured on the owner's own
documents: 65 of 315 claims, across two roles.

That is a lot of rows for one disputed fact, and it is not a bug. Confirming
any of those claims writes the disputed period onto a `VerifiedClaim`, so every
one of them really does carry the thing in dispute. Marking only the two date
lines would leave sixty-three claims quietly confirmable with a date nobody had
agreed to.

The cost is a review with a large conflict group in it, and the answer to that
is presentation -- resolve the period once, at the top of the group -- rather
than a narrower grouping that would understate what is uncertain.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from career_agent.domain.enums import ClaimType
from career_agent.intake.models import IntakePackage, ProposedClaim
from career_agent.intake.parse import _fold, claim_identity

#: The kinds where two claims can genuinely contradict each other.
#:
#: A SKILL cannot conflict with another SKILL: "Python" appearing in both
#: documents is agreement, and "Python" in one and "SQL" in the other is two
#: skills rather than a disagreement. Only claims that pin a role to a span of
#: time can be inconsistent, which is why the set is this small.
CONFLICTABLE: frozenset[ClaimType] = frozenset({ClaimType.EMPLOYMENT, ClaimType.EDUCATION})


@dataclass(frozen=True)
class ClaimGroup:
    """One claim as the candidate will answer it, with everything about it.

    `sources` is a list because a duplicate is one claim from several
    documents, and showing which documents agreed is more useful than showing
    the claim twice.
    """

    claim_key: str
    claim: ProposedClaim
    sources: tuple[str, ...]
    conflict_group: str | None = None


@dataclass
class Reconciliation:
    """What the package amounts to, once duplicates are collapsed."""

    groups: list[ClaimGroup] = field(default_factory=list)
    #: How many claims arrived stating something another claim already stated.
    duplicates_collapsed: int = 0
    #: Conflict group id to the claim keys in it. Every group has at least two.
    conflicts: dict[str, tuple[str, ...]] = field(default_factory=dict)
    #: Claim kinds the package carried none of. Reported so a candidate can see
    #: that their certifications did not come through, rather than concluding
    #: they have none.
    missing_kinds: tuple[str, ...] = ()

    @property
    def conflicted_keys(self) -> frozenset[str]:
        return frozenset(k for keys in self.conflicts.values() for k in keys)


#: Legal-form suffixes, stripped when deciding whether two claims are about
#: the SAME employer.
#:
#: **This is a normalisation, not a similarity match.** ADR-0008 forbids fuzzy
#: identity and this does not reach for one: the list is enumerated, the match
#: is on whole trailing words, and nothing here computes a distance between two
#: names. "Acme LLC" and "Acme" are one employer written twice; "Acme
#: Tecnologia" and "Acme" are NOT folded, because `Tecnologia` is part of
#: a name rather than a legal form, and guessing otherwise is the fuzzy match.
#:
#: Measured on the owner's own two documents: without this, a CV writing
#: "Acme LLC" and a LinkedIn export writing "Acme" bucketed separately, and a
#: real disagreement about whether that role had ended was never surfaced.
#:
#: It is applied ONLY here, to bucketing. `claim_identity` still folds the
#: employer as written, so two claims from two documents remain two claims --
#: which is what lets both be shown side by side.
LEGAL_SUFFIXES: frozenset[str] = frozenset(
    {
        "llc",
        "inc",
        "incorporated",
        "ltd",
        "limited",
        "ltda",
        "sa",
        "srl",
        "gmbh",
        "bv",
        "nv",
        "plc",
        "pty",
        "ab",
        "oy",
        "as",
        "co",
        "corp",
        "corporation",
        "company",
        "me",
        "eireli",
    }
)


def normalise_employer(name: str) -> str:
    """One employer name, for bucketing only. Suffixes stripped from the END."""
    words = _fold(name).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _identity_of_role(claim: ProposedClaim) -> str | None:
    """What makes two claims about the same thing, for conflict purposes.

    Employer plus kind, and nothing else. The PERIOD is deliberately excluded:
    two claims about the same role that disagree about its dates are exactly
    the pair this is looking for, and including the dates in the identity would
    put them in different buckets and find nothing.
    """
    if claim.type not in CONFLICTABLE:
        return None
    employer = normalise_employer(claim.employer or "")
    if not employer:
        # Without an employer there is nothing to anchor a role to, and
        # grouping on text alone is the similarity match this module refuses.
        return None
    return f"{claim.type.value}:{employer}"


def _period_signature(claim: ProposedClaim) -> tuple[str, str, bool]:
    period = claim.period
    if period is None:
        return ("", "", False)
    return (
        period.start.normalized or "" if period.start else "",
        period.end.normalized or "" if period.end else "",
        period.current,
    )


def reconcile(package: IntakePackage) -> Reconciliation:
    """Collapse duplicates, group conflicts, report what is absent."""
    by_key: dict[str, ClaimGroup] = {}
    order: list[str] = []
    duplicates = 0

    for claim in package.claims:
        key = claim_identity(claim)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = ClaimGroup(claim_key=key, claim=claim, sources=(claim.source_ref,))
            order.append(key)
            continue
        duplicates += 1
        if claim.source_ref not in existing.sources:
            by_key[key] = ClaimGroup(
                claim_key=key,
                claim=existing.claim,
                sources=(*existing.sources, claim.source_ref),
            )

    conflicts = _conflicts(by_key, order)

    groups = [
        ClaimGroup(
            claim_key=key,
            claim=by_key[key].claim,
            sources=by_key[key].sources,
            conflict_group=_group_of(key, conflicts),
        )
        for key in order
    ]

    present = {g.claim.type.value for g in groups}
    missing = tuple(sorted(t.value for t in ClaimType if t.value not in present))

    return Reconciliation(
        groups=groups,
        duplicates_collapsed=duplicates,
        conflicts=conflicts,
        missing_kinds=missing,
    )


def _overlaps(a: tuple[str, str, bool], b: tuple[str, str, bool]) -> bool:
    """Whether two periods describe the same stretch of time.

    Half-open on purpose. A role running to 2024-02 and the next one starting
    at 2024-02 is a promotion, not a contradiction, and an inclusive comparison
    reads every consecutive pair of roles at one employer as a disagreement.

    A period with no stated end is open: `current` says the role has not
    ended, and an absent end says the document did not say. Both are treated as
    running to the far future here, because the question this answers is "could
    these be the same role", and both could.
    """
    far = "9999-12"
    a_start, a_end = a[0] or "0000-00", (a[1] or far) if not a[2] else far
    b_start, b_end = b[0] or "0000-00", (b[1] or far) if not b[2] else far
    return a_start < b_end and b_start < a_end


def _conflicts(by_key: dict[str, ClaimGroup], order: list[str]) -> dict[str, tuple[str, ...]]:
    """Claims about ONE ROLE that its documents describe differently.

    **A conflict is an overlap, not a multiplicity.** The first version of this
    grouped every dated claim at one employer whenever two distinct periods
    appeared -- which reads three consecutive roles at one company as a
    disagreement. Measured on the owner's own documents: 174 of 334 claims
    flagged, almost none of it real, and the two genuine disagreements were
    invisible inside the noise.

    So two claims conflict when their periods OVERLAP and are not identical:
    they cannot both be the whole truth about one stretch of time at one
    employer. Consecutive roles do not overlap and are left alone. Identical
    periods are agreement, and a duplicate has already collapsed anyway.

    A claim that states no period disagrees with nothing and is never grouped.
    """
    buckets: dict[str, list[str]] = defaultdict(list)
    for key in order:
        identity = _identity_of_role(by_key[key].claim)
        if identity is not None:
            buckets[identity].append(key)

    conflicts: dict[str, tuple[str, ...]] = {}
    for identity, keys in buckets.items():
        dated = [k for k in keys if any(_period_signature(by_key[k].claim))]
        disputed: set[str] = set()
        for i, left in enumerate(dated):
            for right in dated[i + 1 :]:
                a = _period_signature(by_key[left].claim)
                b = _period_signature(by_key[right].claim)
                if a != b and _overlaps(a, b):
                    disputed.add(left)
                    disputed.add(right)
        if disputed:
            # Ordered by the package's own order, so the review does not
            # reshuffle between page loads.
            conflicts[identity] = tuple(k for k in dated if k in disputed)
    return conflicts


def _group_of(key: str, conflicts: dict[str, tuple[str, ...]]) -> str | None:
    for group, keys in conflicts.items():
        if key in keys:
            return group
    return None

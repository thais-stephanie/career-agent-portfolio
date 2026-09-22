# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Source strength: how much a record's *provenance* lets it do.

Every record already carries ``verification`` (primary / derived / conflicting /
user_verified) and ``sources``. This module turns those into one ordinal
strength so that a self-authored secondary source (the portfolio, LinkedIn, a
resume PDF) cannot outrank direct project documentation merely because its
wording is more concise or senior-sounding.

    user_verified > primary (case study) > corroborated (a secondary record
    whose claim primary or user-verified evidence corroborates, via
    ``corroborated_by``) > secondary multi-source (the same self-authored fact
    repeated across portfolio / LinkedIn / resume: useful provenance, not
    independent corroboration) > portfolio-only > LinkedIn-only > resume-only

Records at the four *secondary-only* strengths keep every ordinary use (they
exist, render as bullets when relevant, vouch for terms and technologies, count
in ranking) but carry ceilings enforced by the matcher, the planner, the value
model and the skills builder:

* never hero evidence, never the lead position's opener;
* never the sole evidence that makes a requirement DIRECT;
* never a years-of-experience span on their own;
* ownership capped at hands-on (a "led" claim needs primary or user-verified
  backing) and scope signals capped, so ownership and scope cannot exceed what
  primary evidence establishes.

The ceilings lift when the record is corroborated by primary evidence
(``corroborated_by``) or the candidate confirms it (``user_verified``). A
confirmation is clause-scoped: the override rewrites the record's effective
wording, so only the confirmed wording benefits.
"""

from __future__ import annotations

from enum import StrEnum

from resume_tailor.core.models import EvidenceRecord, Proficiency, Verification

# a class of positions treated as documentation of the work itself
PRIMARY_SOURCE_SUFFIXES = ("_case_study",)
SECONDARY_SOURCE_KINDS = {"portfolio": "portfolio", "linkedin_profile": "linkedin"}


class SourceStrength(StrEnum):
    USER_VERIFIED = "user_verified"
    PRIMARY = "primary"
    CORROBORATED = "corroborated"
    SECONDARY_MULTI_SOURCE = "secondary_multi_source"
    PORTFOLIO_ONLY = "portfolio_only"
    LINKEDIN_ONLY = "linkedin_only"
    RESUME_ONLY = "resume_only"


SECONDARY_ONLY = {
    SourceStrength.SECONDARY_MULTI_SOURCE,
    SourceStrength.PORTFOLIO_ONLY,
    SourceStrength.LINKEDIN_ONLY,
    SourceStrength.RESUME_ONLY,
}
_RANK = {s: i for i, s in enumerate(SourceStrength)}


def _substantive_sources(rec: EvidenceRecord) -> set[str]:
    return {s for s in rec.sources if s != "user_verified"}


def source_strength(rec: EvidenceRecord, by_id: dict[str, EvidenceRecord]) -> SourceStrength:
    if rec.verification == Verification.USER_VERIFIED:
        return SourceStrength.USER_VERIFIED
    sources = _substantive_sources(rec)
    if rec.verification == Verification.PRIMARY or any(
        s.endswith(PRIMARY_SOURCE_SUFFIXES) for s in sources
    ):
        return SourceStrength.PRIMARY
    for other_id in rec.corroborated_by:
        other = by_id.get(other_id)
        if (
            other is not None
            and other.id != rec.id
            and source_strength(other, by_id)
            in (SourceStrength.PRIMARY, SourceStrength.USER_VERIFIED)
        ):
            return SourceStrength.CORROBORATED
    # several self-authored documents repeating one fact are not independent votes for it
    if len(sources) >= 2:
        return SourceStrength.SECONDARY_MULTI_SOURCE
    kind = SECONDARY_SOURCE_KINDS.get(next(iter(sources), ""), "resume")
    return {
        "portfolio": SourceStrength.PORTFOLIO_ONLY,
        "linkedin": SourceStrength.LINKEDIN_ONLY,
    }.get(kind, SourceStrength.RESUME_ONLY)


def is_secondary_only(rec: EvidenceRecord, by_id: dict[str, EvidenceRecord]) -> bool:
    return source_strength(rec, by_id) in SECONDARY_ONLY


def stronger_or_equal(a: SourceStrength, b: SourceStrength) -> bool:
    return _RANK[a] <= _RANK[b]


_OWNERSHIP_CAP = {Proficiency.LED: Proficiency.HANDS_ON}


def effective_proficiency(
    rec: EvidenceRecord, term: str | None, by_id: dict[str, EvidenceRecord]
) -> Proficiency:
    """The record's proficiency for ``term`` after the secondary-source ceiling: a secondary-only
    record cannot establish "led" ownership on its own."""
    prof = rec.proficiency_for(term) if term else rec.proficiency
    if is_secondary_only(rec, by_id):
        return _OWNERSHIP_CAP.get(prof, prof)
    return prof

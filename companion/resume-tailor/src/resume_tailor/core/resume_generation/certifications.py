# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Which certifications a tailored resume may show.

Two rules, in order:

1. **Conflict rule.** A certification whose ``conflict_ids`` point at a conflict
   that no user override has resolved is *blocked*: the sources disagree about
   what was earned, and a resume must not assert either version. It renders
   only when the certification itself is ``user_verified`` (set by an override)
   or the conflict carries ``resolved_by``. The source statements are never
   altered by this module.
2. **Relevance policy.** Among the rest, rank by what the credential says to
   this job: the issuer or the credential name is a JD term (+3 / +2), the
   credential sits in an integration/automation family or names one (+1), and
   a credential whose terms are all already carried by rendered bullets at
   working depth adds no new information (-1). Everything with a positive
   score renders; zero-score credentials fill up to ``max_zero`` slots and the
   section never exceeds ``max_issuers`` lines, so it stays short.
"""

from __future__ import annotations

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import CONCEPT_GROUPS, canonical, find_terms
from resume_tailor.core.models import Certification, JobAnalysis, Verification

_INTEGRATION_FAMILIES = ("ipaas", "api", "automation_general")
_INTEGRATION_WORDS = ("integration", "automation", "connector", "sdk")


def blocked_reason(cert: Certification, index: EvidenceIndex) -> str | None:
    """Why a certification may not render, or None when it may."""
    if cert.verification == Verification.USER_VERIFIED:
        return None
    conflicts = {c.id: c for c in index.bank.conflicts}
    open_ids = [
        cid for cid in cert.conflict_ids if cid in conflicts and not conflicts[cid].resolved_by
    ]
    if open_ids:
        return f"conflict {', '.join(open_ids)} unresolved; not user_verified"
    return None


def relevance_score(cert: Certification, job: JobAnalysis, body_terms: set[str]) -> int:
    jd_terms = {canonical(t) for t in [*job.technologies, *job.keywords]} | set(
        find_terms(" ".join([*job.must_have, *job.nice_to_have, *job.responsibilities]))
    )
    issuer = canonical(cert.issuer)
    name_terms = set(find_terms(cert.name))
    score = 0
    if issuer in jd_terms:
        score += 3
    if name_terms & jd_terms:
        score += 2
    family_members = {m for f in _INTEGRATION_FAMILIES for m in CONCEPT_GROUPS.get(f, [])}
    low = f"{cert.issuer} {cert.name}".lower()
    if issuer in family_members or any(w in low for w in _INTEGRATION_WORDS):
        score += 1
    if name_terms and name_terms <= body_terms and issuer not in jd_terms:
        score -= 1  # nothing the bullets do not already show
    return score


def select_certifications(
    index: EvidenceIndex,
    job: JobAnalysis,
    body_terms: set[str],
    max_zero: int = 1,
    max_issuers: int = 5,
) -> tuple[list[Certification], list[str]]:
    """Certifications to render, in relevance order, plus notes explaining every omission."""
    notes: list[str] = []
    scored: list[tuple[int, int, Certification]] = []
    for i, c in enumerate(index.bank.certifications):
        if not c.include_by_default:
            continue
        why = blocked_reason(c, index)
        if why:
            notes.append(f"certification {c.id} not rendered: {why}")
            continue
        scored.append((relevance_score(c, job, body_terms), i, c))
    scored.sort(key=lambda t: (-t[0], t[1]))
    out: list[Certification] = []
    zero_used = 0
    for score, _, c in scored:
        issuers = {x.issuer for x in out}
        if c.issuer not in issuers and len(issuers) >= max_issuers:
            notes.append(
                f"certification {c.id} not rendered: section capped at {max_issuers} issuers (score {score})"
            )
            continue
        if score > 0:
            out.append(c)
        elif zero_used < max_zero:
            out.append(c)
            zero_used += 1
        else:
            notes.append(
                f"certification {c.id} not rendered: no relevance to this job (score {score})"
            )
    return out, notes

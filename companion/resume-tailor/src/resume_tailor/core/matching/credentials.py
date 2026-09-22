# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Named-credential requirements: "Hubspot Service Hub Software Certification".

A certification is a factual credential, not a continuum of experience. When a requirement
line *is* a credential ask (a short name plus "certification / certificate / certified /
credential / license"), the verdict is binary:

* the named credential is held (every distinctive word of the ask appears in one
  certification's issuer + name, the certification is not blocked by an unresolved
  conflict, and it is not excluded from the bank) -> DIRECT;
* otherwise -> UNSUPPORTED. Work experience with the platform, related technologies,
  a *different* credential from the same vendor, or a course with a different title
  never turn this into PARTIAL. Related experience may appear in the rationale as
  context, but it does not confer partial possession of a credential.

No equivalence is inferred between credentials. The user_verified Workato override
(override_005) is honoured through the same ``blocked_reason`` the render path uses.

Sentences that merely *mention* certification ("maintains vendor certifications for the
team") are not credential asks and keep the ordinary matching path.
"""

from __future__ import annotations

import re

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import GENERIC_TERMS, canonical
from resume_tailor.core.models import Certification, MatchedEvidence, MatchType, Proficiency

_CUE = re.compile(
    r"\b(certified|certifications?|certificates?|credentials?|licen[cs]ed?|licen[cs]es?)\b",
    re.IGNORECASE,
)
_LEAD_IN = re.compile(
    r"^(?:must (?:have|be|hold)|holds?|has|have|active|current|valid|a|an|the)\s+", re.IGNORECASE
)
_TRAIL = re.compile(r"\s*(?:preferred|required|is required|a plus|is a plus)\s*\.?$", re.IGNORECASE)
# verbs that make the line a duty about credentials, not a credential ask
_DUTY_VERBS = {
    "maintain",
    "manage",
    "support",
    "ensure",
    "obtain",
    "keep",
    "provide",
    "develop",
    "build",
    "own",
    "lead",
    "drive",
    "create",
    "document",
    "monitor",
    "renew",
    "track",
    "verify",
    "review",
}
# words that carry no identity: the rest of the ask must match the held credential's issuer + name
_FILLER = {
    "software",
    "certification",
    "certifications",
    "certificate",
    "certificates",
    "certified",
    "credential",
    "credentials",
    "license",
    "licence",
    "licensed",
    "licenced",
    "licenses",
    "licences",
    "active",
    "current",
    "valid",
    "the",
    "a",
    "an",
    "of",
    "in",
    "for",
    "as",
    "with",
    "and",
    "or",
    "similar",
    "equivalent",
    "preferred",
    "required",
    "plus",
}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9.+#-]*", text.lower())


def credential_ask(text: str) -> str | None:
    """The credential name when the requirement line is a named-credential ask, else None."""
    line = _TRAIL.sub("", text.strip().rstrip("."))
    for _ in range(3):
        stripped = _LEAD_IN.sub("", line)
        if stripped == line:
            break
        line = stripped
    words = line.split()
    if not words or len(words) > 8 or not _CUE.search(line):
        return None
    if words[0].lower() in _DUTY_VERBS:
        return None
    distinctive = [w for w in _words(line) if w not in _FILLER]
    return " ".join(distinctive) if distinctive else None


def _held(name: str, index: EvidenceIndex) -> list[Certification]:
    """Certifications whose issuer + name contain every distinctive word of the ask."""
    from resume_tailor.core.resume_generation.certifications import blocked_reason

    needed = [w for w in _words(name) if w not in _FILLER]
    out: list[Certification] = []
    for cert in index.bank.certifications:
        if cert.include_by_default is False or blocked_reason(cert, index) is not None:
            continue
        hay = f"{cert.issuer} {cert.name}".lower()
        if needed and all(
            re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", hay) for w in needed
        ):
            out.append(cert)
    return out


def match_credential(
    name: str, req_terms: list[str], ev: list[MatchedEvidence], index: EvidenceIndex
) -> tuple[MatchType, str, list[MatchedEvidence]]:
    """Binary verdict for a named-credential requirement (see module docstring)."""
    held = _held(name, index)
    if held:
        c = held[0]
        tag = f" ({c.verification.value})" if c.verification.value != "derived" else ""
        note = f"named credential held: {c.issuer} '{c.name}'{tag}"
        # the bank's certification records (proficiency=certification) carry the citation
        needed = [w for w in _words(name) if w not in _FILLER]
        cred_ev = [
            MatchedEvidence(
                evidence_id=r.id,
                score=3.0,
                match_type=MatchType.DIRECT,
                matched_terms=needed,
                reason=f"literal: credential {c.issuer} {c.name}",
            )
            for r in index.default_records()
            if r.proficiency == Proficiency.CERTIFICATION
            and all(w in index.record_text(r).lower() for w in _words(c.issuer))
        ]
        seen = {e.evidence_id for e in cred_ev}
        return MatchType.DIRECT, note, cred_ev + [e for e in ev if e.evidence_id not in seen]
    # related platform experience is context only: it never makes a credential partially held
    related = sorted(
        {
            canonical(t)
            for t in req_terms
            if canonical(t) not in GENERIC_TERMS and canonical(t) in index.all_terms
        }
    )
    context = (
        f"; related evidence exists (hands-on {', '.join(related)} experience), but experience does not confer the credential"
        if related
        else ""
    )
    return (
        MatchType.UNSUPPORTED,
        f"named credential '{name}' is not evidenced: a credential is held or it is not{context}",
        ev,
    )

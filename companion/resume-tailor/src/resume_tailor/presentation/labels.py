# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Central display-label mapping: internal engine vocabulary -> product language.

Every "simple" API payload and every UI string goes through these helpers. The rule
(product spec, Phase 20): normal users see Required / Preferred / Strong match /
Confirmed by you — never MatchType values, verification enums, source-strength names,
snake_case identifiers or evidence ids. Technical identifiers appear only in payload
fields explicitly marked for the Advanced view.
"""

from __future__ import annotations

from typing import Any

MATCH_LABELS = {
    "direct": "Strong match",
    "transferable": "Related experience",
    "partial": "Partial match",
    "unsupported": "Not evidenced",
}

CATEGORY_LABELS = {
    "must_have": "Required",
    "nice_to_have": "Preferred",
    "responsibility": "Responsibility",
    "technology": "Named tool",
    "business_capability": "Capability",
    "domain": "Field",
}

# verification / source-strength vocabulary -> how the product talks about trust
VERIFICATION_LABELS = {
    "user_verified": "Confirmed by you",
    "primary": "Supported by project documentation",
    "derived": "Supported by sources",
    "conflicting": "Needs review",
}

SOURCE_STRENGTH_LABELS = {
    "user_verified": "Confirmed by you",
    "primary": "Supported by project documentation",
    "corroborated": "Supported by multiple sources",
    "secondary_multi_source": "Supported by your own materials",
    "portfolio_only": "From your portfolio",
    "linkedin_only": "From your LinkedIn",
    "resume_only": "From a resume",
}

SOURCE_KIND_LABELS = {
    "resume": "Resume",
    "profile": "LinkedIn profile",
    "case_study": "Project documentation",
    "portfolio": "Portfolio",
    "confirmed": "Details you confirmed",
    "other": "Document",
}

APPLICATION_STATUSES = ["Considering", "Applied", "Interviewing", "Closed"]

CHECK_LABELS = {
    "page_length": "Fits the page limit",
    "page_length_tight": "Close to the page limit",
    "page_length_short": "Looks short",
    "keyword_stuffing": "A phrase repeats often",
    "missing_supported_keyword": "A skill you have is missing",
    "unsupported_keyword": "Wording without evidence",
    "jd_terms_without_evidence": "Job asks not in your experience (left out on purpose)",
    "duplicate_skills": "A skill is listed twice",
    "skill_not_evidenced_in_body": "Skills not shown in a bullet",
    "evidence_missing_from_skills": "Bullet skills missing from the Skills list",
    "metrics_dropped": "A strong number was left out",
    "role_scope_observation": "Something about the role itself",
    "years_claim_basis": "How the years claim is counted",
}


def match_label(match_type: str) -> str:
    return MATCH_LABELS.get(str(match_type).lower(), "Not evidenced")


def category_label(category: str) -> str:
    return CATEGORY_LABELS.get(str(category).lower(), "Detail")


def verification_label(verification: str) -> str:
    return VERIFICATION_LABELS.get(str(verification).lower(), "Supported by sources")


def source_strength_label(strength: str) -> str:
    return SOURCE_STRENGTH_LABELS.get(str(strength).lower(), "Supported by sources")


def source_kind_label(kind: str) -> str:
    return SOURCE_KIND_LABELS.get(str(kind).lower(), "Document")


def check_label(code: str) -> str:
    return CHECK_LABELS.get(code, code.replace("_", " ").capitalize())


def match_sentence(match_type: str, requirement: str, why: str = "") -> str:
    """A one-sentence, jargon-free explanation for a requirement verdict."""
    base = {
        "direct": "Your experience covers this directly.",
        "transferable": "You have closely related experience, not this exact thing.",
        "partial": "Part of this is covered; part is not.",
        "unsupported": "Nothing in your experience covers this yet.",
    }.get(str(match_type).lower(), "")
    return why or base


_FORBIDDEN_IN_SIMPLE = (
    "raw_terms",
    "evidence_bank",
    "must_have",
    "nice_to_have",
    "user_verified",
    "secondary_multi_source",
    "source_strength",
    "profile_id",
    "run_id",
    "corroborated_by",
    "EvidenceRecord",
    "MatchType",
    "Verification.",
    "match_type",
    "evidence_id",
)


def assert_simple_payload(payload: Any) -> None:
    """Test helper: raise if a simple-mode payload leaks internal vocabulary.

    Scope: vocabulary the APP writes (keys, labels, statuses, reasons, composed
    notes). Free text the USER typed (a confirmation note, a corrected detail) is
    theirs and is shown verbatim; a candidate who mentions an identifier in their
    own note is not a leak, and no code path rewrites user text."""
    import json

    text = json.dumps(payload, ensure_ascii=False) if not isinstance(payload, str) else payload
    for needle in _FORBIDDEN_IN_SIMPLE:
        if f'"{needle}"' in text or f" {needle} " in text:
            raise AssertionError(f"simple payload leaks internal term: {needle}")

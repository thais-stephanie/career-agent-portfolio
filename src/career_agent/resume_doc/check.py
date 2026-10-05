"""Deterministic findings about one ResumeDocument: named facts, never a score.

ONE finding model for the Editor and for Analyze. Each finding names what it
is about (`ref`, the same path the preview and the editor use), a stable
`key` (`<kind>:<ref>`, or with a fingerprint of the condition when the same
place can hold different facts) a dismissal can name, and, from `KINDS`:

* `category`: IDENTITY, STRUCTURE, READABILITY, CONTENT, EVIDENCE,
  CONSISTENCY, EXPORT or JOB;
* `severity`: BLOCKING (the resume is not usable as it is), WARNING (a
  problem worth fixing), OPPORTUNITY (could be better) or INFO;
* `nature`: OBJECTIVE (plainly true of the document) or ADVISORY (a
  heuristic, said as advice and never as fact);
* `action`: what to do next (EDIT, APPLY, OPEN_EVIDENCE, ADD_EVIDENCE,
  EXPORT_CHECK, TAILOR) or None.

There is no percentage, no "ATS score" and no prediction.
"""

from __future__ import annotations

import re
from typing import Any

from career_agent.resume_doc.models import (
    EVIDENCED_ORIGINS,
    Bullet,
    Origin,
    Override,
    ResumeDocument,
)

#: A bullet longer than this reads as a paragraph on a resume.
LONG_BULLET_CHARS = 300
_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")

B, W, O, I = "BLOCKING", "WARNING", "OPPORTUNITY", "INFO"  # noqa: E741
OBJ, ADV = "OBJECTIVE", "ADVISORY"
#: kind -> (category, severity, nature, action)
KINDS: dict[str, tuple[str, str, str, str | None]] = {
    "NAME_MISSING": ("IDENTITY", B, OBJ, "EDIT"),
    "NAME_PLACEHOLDER": ("IDENTITY", B, OBJ, "EDIT"),
    "EMAIL_MISSING": ("IDENTITY", W, ADV, "EDIT"),
    "NO_CONTACT": ("IDENTITY", B, OBJ, "EDIT"),
    "EMPTY_SECTION": ("STRUCTURE", W, OBJ, "EDIT"),
    "DUPLICATE_SECTION": ("STRUCTURE", W, OBJ, "EDIT"),
    "ENTRY_INCOMPLETE": ("STRUCTURE", W, OBJ, "EDIT"),
    "NO_VISIBLE_EXPERIENCE": ("STRUCTURE", W, ADV, "EDIT"),
    "LONG_BULLET": ("READABILITY", O, ADV, "EDIT"),
    "LONG_SUMMARY": ("READABILITY", O, ADV, "EDIT"),
    "MANY_BULLETS": ("READABILITY", O, ADV, "EDIT"),
    "TALLER_THAN_PAGE": ("READABILITY", W, OBJ, "EDIT"),
    "PAGES": ("READABILITY", O, ADV, None),
    "DUPLICATE_BULLET": ("CONTENT", W, OBJ, "APPLY"),
    "NEAR_DUPLICATE_BULLET": ("CONTENT", O, ADV, "EDIT"),
    "DUPLICATE_SKILL": ("CONTENT", O, OBJ, "EDIT"),
    "GENERIC_PHRASE": ("CONTENT", O, ADV, "EDIT"),
    "EDITED_EVIDENCE": ("EVIDENCE", I, ADV, "EDIT"),
    "NUMBER_NOT_IN_EVIDENCE": ("EVIDENCE", W, OBJ, "EDIT"),
    "UNSUPPORTED_NUMBER": ("EVIDENCE", W, OBJ, "EDIT"),
    "UNSUPPORTED_TOOL": ("EVIDENCE", W, OBJ, "EDIT"),
    "UNSUPPORTED_SENIORITY": ("EVIDENCE", W, OBJ, "EDIT"),
    "NOT_FROM_EVIDENCE": ("EVIDENCE", I, OBJ, "OPEN_EVIDENCE"),
    "STALE_EVIDENCE": ("EVIDENCE", W, OBJ, "EDIT"),
    "EVIDENCE_CHANGED": ("EVIDENCE", I, OBJ, "EDIT"),
    "CHECK_DATES": ("CONSISTENCY", I, ADV, "EDIT"),
    "DUPLICATE_ROLE": ("CONSISTENCY", W, OBJ, "EDIT"),
    "TITLE_RENAMED": ("CONSISTENCY", I, OBJ, "EDIT"),
    "EXPORT_NOT_CHECKED": ("EXPORT", I, OBJ, "EXPORT_CHECK"),
    "EXPORT_OUTDATED": ("EXPORT", I, OBJ, "EXPORT_CHECK"),
    "EXPORT_PROBLEM": ("EXPORT", W, OBJ, "EXPORT_CHECK"),
    "EXPORT_WORTH_A_LOOK": ("EXPORT", O, OBJ, "EXPORT_CHECK"),
    "JOB_EVIDENCE_NOT_SHOWN": ("JOB", O, OBJ, "APPLY"),
    "JOB_NOT_FOUND": ("JOB", I, OBJ, "ADD_EVIDENCE"),
    "JOB_TEXT_NOT_CONFIRMED": ("JOB", I, OBJ, "OPEN_EVIDENCE"),
}
SEVERITIES = (B, W, O, I)


def finding(kind: str, ref: str, *, key: str | None = None, **extra: Any) -> dict[str, Any]:
    category, severity, nature, action = KINDS[kind]
    return {
        "key": key or f"{kind}:{ref}", "kind": kind, "ref": ref, "category": category,
        "severity": severity, "nature": nature, "action": action, **extra,
    }  # fmt: skip


def _bullet_findings(bullet: Bullet, ref: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if bullet.hidden:
        return out
    if len(bullet.text) > LONG_BULLET_CHARS:
        out.append(finding("LONG_BULLET", ref))
    if bullet.override is Override.EDITED and bullet.origin in EVIDENCED_ORIGINS:
        added = set(_NUMBER.findall(bullet.text)) - set(_NUMBER.findall(bullet.original_text or ""))
        out.append(finding("NUMBER_NOT_IN_EVIDENCE" if added else "EDITED_EVIDENCE", ref))
    elif bullet.origin in (Origin.USER_AUTHORED, Origin.IMPORTED) and bullet.text.strip():
        out.append(finding("NOT_FROM_EVIDENCE", ref))
    return out


def findings(doc: ResumeDocument) -> list[dict[str, Any]]:
    """The findings the Editor shows as it goes: what one document says."""
    out: list[dict[str, Any]] = []
    name = doc.identity.name_finding()
    if name:
        out.append(finding(name, "identity/name"))
    if not doc.identity.email:
        out.append(finding("EMAIL_MISSING", "identity/email"))
    hidden = set(doc.layout.hidden_sections)
    entries: dict[str, list[Any]] = {
        "experience": doc.experience,
        "projects": doc.projects,
        "education": doc.education,
        "certifications": doc.certifications,
        "skills": doc.skills,
    }
    for section, items in entries.items():
        if section in hidden:
            continue  # a hidden section is not on the page: nothing in it is advice
        visible = [i for i in items if not i.hidden]
        if section in doc.layout.section_order and items and not visible:
            out.append(finding("EMPTY_SECTION", f"section/{section}"))
        for item in visible:
            for bullet in getattr(item, "bullets", []):
                out += _bullet_findings(bullet, f"{section}/{item.id}/bullet/{bullet.id}")
    for custom in doc.custom_sections:
        ref = f"custom:{custom.id}"
        if custom.hidden or ref in hidden:
            continue
        if not [b for b in custom.items if not b.hidden and b.text.strip()]:
            out.append(finding("EMPTY_SECTION", f"section/{ref}"))
        for bullet in custom.items:
            out += _bullet_findings(bullet, f"{ref}/bullet/{bullet.id}")
    return out

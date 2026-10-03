"""Deterministic findings about one ResumeDocument: named facts, never a score.

Each finding names what it is about (`ref`, the same path the preview and the
editor use) and a stable `key` (`<kind>:<ref>`) a dismissal can name. There
is no percentage, no "ATS score" and no prediction: only things that are
plainly true of the document as it stands.
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


def _finding(kind: str, ref: str, severity: str = "advice") -> dict[str, str]:
    return {"key": f"{kind}:{ref}", "kind": kind, "ref": ref, "severity": severity}


def _bullet_findings(bullet: Bullet, ref: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if bullet.hidden:
        return out
    if len(bullet.text) > LONG_BULLET_CHARS:
        out.append(_finding("LONG_BULLET", ref))
    if bullet.override is Override.EDITED and bullet.origin in EVIDENCED_ORIGINS:
        added = set(_NUMBER.findall(bullet.text)) - set(_NUMBER.findall(bullet.original_text or ""))
        out.append(_finding("NUMBER_NOT_IN_EVIDENCE" if added else "EDITED_EVIDENCE", ref))
    elif bullet.origin in (Origin.USER_AUTHORED, Origin.IMPORTED) and bullet.text.strip():
        out.append(_finding("NOT_FROM_EVIDENCE", ref, "info"))
    return out


def findings(doc: ResumeDocument) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    name = doc.identity.name_finding()
    if name:
        out.append(_finding(name, "identity/name", "problem"))
    if not doc.identity.email:
        out.append(_finding("EMAIL_MISSING", "identity/email"))
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
            out.append(_finding("EMPTY_SECTION", f"section/{section}"))
        for item in visible:
            for bullet in getattr(item, "bullets", []):
                out += _bullet_findings(bullet, f"{section}/{item.id}/bullet/{bullet.id}")
    for custom in doc.custom_sections:
        ref = f"custom:{custom.id}"
        if custom.hidden or ref in hidden:
            continue
        if not [b for b in custom.items if not b.hidden and b.text.strip()]:
            out.append(_finding("EMPTY_SECTION", f"section/{ref}"))
        for bullet in custom.items:
            out += _bullet_findings(bullet, f"{ref}/bullet/{bullet.id}")
    return out

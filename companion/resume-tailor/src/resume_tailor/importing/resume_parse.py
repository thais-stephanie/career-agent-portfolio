# Modified for the Career Agent public edition (2026-09-26). See NOTICE.
"""Parse an uploaded resume (DOCX / PDF / plain text) into a reviewable draft.

Deliberately honest about what a heuristic parser is: it produces a *draft* base
resume (headline, summary, skills — the parts a base resume actually contributes to
tailoring) plus a list of extracted DETAIL SUGGESTIONS the user can review one by
one. Nothing here touches the evidence bank: a suggestion only becomes experience
when the user explicitly accepts it (workspace/sources.py), and even then it enters
as ordinary resume-sourced (secondary) evidence — never as confirmed fact.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

_HEADINGS = {
    "summary": ("summary", "profile", "about", "professional summary", "objective"),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "work history",
    ),
    "skills": ("skills", "technical skills", "core skills", "skills & tools", "competencies"),
    "education": ("education", "academic background"),
    "certifications": ("certifications", "certificates", "licenses", "courses"),
    "languages": ("languages",),
}
_BULLET = re.compile(r"^\s*[\-\*•●▪◦·–—>]\s+")
_DATES = re.compile(
    r"\b((?:19|20)\d{2})\s*[-–—to]+\s*((?:19|20)\d{2}|present|current|now)\b", re.IGNORECASE
)
_ROLEISH = re.compile(
    r"\b(engineer|manager|analyst|specialist|developer|consultant|architect|director|designer|coordinator|lead|administrator|scientist|intern)\b",
    re.IGNORECASE,
)


@dataclass
class DetailSuggestion:
    text: str
    company: str = ""
    title: str = ""
    start: str = ""  # YYYY-MM when detected, else empty: the user supplies dates on accept
    end: str | None = None


@dataclass
class ParsedResume:
    name: str = ""
    headline: str = ""
    summary: str = ""
    skills: list[str] = field(default_factory=list)
    roles: list[dict[str, Any]] = field(default_factory=list)  # {company,title,start,end}
    details: list[DetailSuggestion] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    raw_text: str = ""

    def summary_counts(self) -> dict[str, int]:
        """What the onboarding review screen reports: '4 roles, 18 details, 23 skills…'."""
        return {
            "roles": len(self.roles),
            "details": len(self.details),
            "skills": len(self.skills),
            "certifications": len(self.certifications),
        }


def extract_text(filename: str, data: bytes) -> str:
    low = filename.lower()
    if low.endswith(".docx"):
        from docx import Document

        doc = Document(io.BytesIO(data))
        return "\n".join(p.text for p in doc.paragraphs)
    if low.endswith(".pdf"):
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    if low.endswith((".txt", ".md", ".markdown")):
        return data.decode("utf-8", errors="replace")
    raise ValueError("Please upload a PDF, Word (.docx) or Markdown (.md) resume.")


def _section_of(line: str) -> str | None:
    low = line.lower().strip(" :")
    for sec, cues in _HEADINGS.items():
        if low in cues:
            return sec
    return None


def _month(year: str, end: bool = False) -> str:
    return f"{year}-12" if end else f"{year}-01"


def parse_resume(filename: str, data: bytes) -> ParsedResume:
    text = extract_text(filename, data)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    out = ParsedResume(raw_text=text)
    if lines:
        out.name = lines[0] if len(lines[0].split()) <= 6 and not _BULLET.match(lines[0]) else ""
        if len(lines) > 1 and _ROLEISH.search(lines[1]) and len(lines[1]) <= 90:
            out.headline = lines[1]
    section: str | None = None
    current_role: dict[str, Any] = {}
    summary_parts: list[str] = []
    for line in lines[1:]:
        sec = _section_of(line)
        if sec:
            section = sec
            continue
        clean = _BULLET.sub("", line)
        if section == "summary" and not _BULLET.match(line):
            summary_parts.append(clean)
        elif section == "skills":
            out.skills.extend(
                s.strip() for s in re.split(r"[,•·|]", clean) if 1 < len(s.strip()) <= 40
            )
        elif section == "certifications":
            out.certifications.append(clean)
        elif section == "languages":
            out.languages.extend(s.strip() for s in re.split(r"[,•·|]", clean) if s.strip())
        elif section == "experience":
            dates = _DATES.search(line)
            if not _BULLET.match(line) and (dates or (_ROLEISH.search(line) and len(line) <= 90)):
                # a role heading line: "Senior Engineer — Initech (2019 - 2023)"
                start = _month(dates.group(1)) if dates else ""
                end_raw = dates.group(2).lower() if dates else ""
                end = (
                    None
                    if end_raw in ("present", "current", "now", "")
                    else _month(end_raw, end=True)
                )
                name_part = _DATES.sub("", clean).strip(" -–—|,(")
                bits = re.split(r"\s*[—–|@]\s*|\s+at\s+", name_part, maxsplit=1)
                current_role = {
                    "title": bits[0].strip(" ,"),
                    "company": (bits[1].strip(" ,)") if len(bits) > 1 else ""),
                    "start": start,
                    "end": end,
                }
                out.roles.append(current_role)
            elif _BULLET.match(line) and len(clean) > 15:
                out.details.append(
                    DetailSuggestion(
                        text=clean,
                        company=current_role.get("company", ""),
                        title=current_role.get("title", ""),
                        start=current_role.get("start", ""),
                        end=current_role.get("end"),
                    )
                )
    out.summary = " ".join(summary_parts)[:800]
    # deduplicate skills, preserve order and original capitalization
    seen: set[str] = set()
    out.skills = [s for s in out.skills if not (s.lower() in seen or seen.add(s.lower()))]
    return out


def to_base_resume_json(
    parsed: ParsedResume, resume_id: str, display_name: str, source_file: str
) -> dict[str, Any]:
    """A base-resume document from the parse. Structure and wording only — positions stay
    empty because experience always comes from the candidate's own evidence, never from an
    uploaded file's claims."""
    skills = parsed.skills[:24]
    groups: list[dict[str, Any]] = []
    if skills:
        half = (len(skills) + 1) // 2
        groups = [{"name": "Skills", "items": skills[:half]}]
        if skills[half:]:
            groups.append({"name": "Tools", "items": skills[half:]})
    return {
        "id": resume_id,
        "name": display_name,
        "headline": parsed.headline or display_name,
        "summary": [{"text": parsed.summary, "evidence_ids": []}] if parsed.summary else [],
        "positions": [],
        "skills": groups,
        "source_file": source_file,
    }

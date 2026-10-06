# Adapted from Resume Tailor (Apache-2.0, upstream eb22205d06de2975191fb06961b4b0677d4238d8;
# see licenses/resume-tailor-Apache-2.0.txt and licenses/resume-tailor-NOTICE.txt).
# Modified for Career Agent (2026-10-05): its workspace models, draft rules and
# export file names reduced to read-only readers for migration.
"""The retired Resume helper's files, read for migration only. FROZEN.

The Resume helper (the companion engine, retired in Resume Workspace V2
PR 12) wrote one folder per profile under that profile's Tailor home:

    candidates/<profile id, lowercased>/
      candidate.json            contact + schema_version (1 is the only one)
      settings.json             default_resume_id
      evidence/evidence_bank.json
      overrides/user_overrides.json   facts confirmed there, applied on read
      base_resumes/<id>.json
      applications/<run id>/run.json  one finished tailoring run
      drafts/<run id>.json      the person's edits to that run, as a stack
      exports/*.pdf|*.docx      files they exported

This module reads exactly the shapes v0.2.0-beta.2 wrote, and nothing newer:
the format will never change again, so it is not a model to evolve. A shape it
does not know (a newer `schema_version`, a missing or unexpected field in
what is migrated) is refused, never guessed; `legacy.py` names the unit that
failed and leaves every file as it is. The run's analysis, strategy and
reports are kept verbatim as untrusted history, so only their being objects
is checked. `tests/fixtures/legacy_resume_helper` is the frozen workspace
these readers are proved against.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

#: Written into candidate.json by the Career Agent bridge.
PROFILE_KEY = "career_agent_profile_id"
#: An evidence record that came from Career Agent's confirmed statements.
SOURCE = "career_agent"
#: The base resume the bridge made from the Career Profile.
BASE_RESUME_ID = "career_agent_profile"
#: The only workspace version the helper ever wrote.
SCHEMA_VERSION = 1
EMPTY_STATE: dict[str, Any] = {
    "headline": None,
    "summary": None,
    "bullets": {},
    "order": {},
    "skills": None,
    "hidden_skills": [],
    "certifications": None,
    "note": "",
}


class LegacyFormatError(ValueError):
    """A legacy file this reader does not know how to read safely."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- the evidence bank ---------------------------------------------------------


class Candidate(_Strict):
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio: str = ""
    languages: list[str] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)


class Position(_Strict):
    id: str
    company: str
    title: str
    start: str
    end: str | None = None
    location: str = ""
    company_blurb: str = ""
    kind: str = "employment"
    include_by_default: bool = True
    conflict_ids: list[str] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)


class Education(_Strict):
    id: str
    institution: str
    degree: str
    start: str | None = None
    end: str | None = None
    sources: list[str] = Field(default_factory=list)
    include_by_default: bool = True


class Certification(_Strict):
    id: str
    name: str
    issuer: str
    sources: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    include_by_default: bool = True
    verification: str = "derived"
    overrides_applied: list[str] = Field(default_factory=list)


class EvidenceRecord(BaseModel):
    """Only what migration reads; the record's other fields are kept as read."""

    model_config = ConfigDict(extra="allow")
    id: str
    resume_text: str
    source_file: str
    source_reference: str = ""


class EvidenceBank(_Strict):
    candidate: Candidate
    positions: list[Position]
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    records: list[EvidenceRecord]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)
    # Filled by the helper's loader from the overrides file; never read here.
    overrides: list[dict[str, Any]] = Field(default_factory=list)


# -- resumes and runs ----------------------------------------------------------


class Bullet(_Strict):
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    bindings: list[dict[str, str]] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    origin: str = "rewritten"
    status: str = "pending"


class ExperienceEntry(_Strict):
    position_id: str
    company: str
    title: str
    start: str
    end: str | None
    location: str = ""
    company_blurb: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class SkillGroup(_Strict):
    name: str
    items: list[str]


class GeneratedResume(_Strict):
    candidate: Candidate
    headline: str
    summary: list[Bullet] = Field(default_factory=list)
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)
    generation_source: str = "deterministic"
    estimated_pages: float = 0.0
    estimated_words: int = 0


class BaseResumeBullet(_Strict):
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class BaseResumePosition(_Strict):
    position_id: str
    bullets: list[BaseResumeBullet] = Field(default_factory=list)


class BaseResume(_Strict):
    id: str
    name: str
    headline: str
    summary: list[BaseResumeBullet] = Field(default_factory=list)
    positions: list[BaseResumePosition] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    source_file: str = ""
    default_profile: str | None = None


class TailorOptions(_Strict):
    evidence_only_claims: bool = True
    ats_friendly: bool = True
    max_two_pages: bool = True
    preserve_metrics: bool = True
    show_explanations: bool = True
    use_llm: bool = True
    resume_locale: str = "en-US"


class TailorRequest(_Strict):
    jd_text: str = Field(min_length=20)
    resume_id: str
    target_profile: str | None = None
    options: TailorOptions = Field(default_factory=TailorOptions)
    target_title: str = ""
    target_company: str = ""
    source: dict[str, str] = Field(default_factory=dict)


class TailorRun(_Strict):
    run_id: str
    created_at: str
    request: TailorRequest
    provider: dict[str, str] = Field(default_factory=dict)
    generated_resume: GeneratedResume
    # Kept verbatim as untrusted history: only their being objects is checked.
    job_analysis: dict[str, Any]
    evidence_matches: dict[str, Any]
    resume_strategy: dict[str, Any]
    claim_evidence_map: dict[str, Any]
    validation_report: dict[str, Any]
    lint_report: dict[str, Any]
    diff_report: dict[str, Any]
    page_measurement: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)


# -- reading a workspace -------------------------------------------------------


def _json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def meta(root: Path) -> dict[str, Any]:
    """candidate.json, refused when a newer helper wrote it."""
    data = _json(root / "candidate.json")
    if int(data.get("schema_version", 1)) != SCHEMA_VERSION:
        raise LegacyFormatError(
            f"candidate.json is data version {data.get('schema_version')}, not {SCHEMA_VERSION}"
        )
    return dict(data)


def default_resume_id(root: Path) -> str:
    path = root / "settings.json"
    return str(_json(path).get("default_resume_id") or "") if path.exists() else ""


def load_bank(root: Path) -> EvidenceBank | None:
    """The evidence bank with the person's confirmed overrides applied in
    memory, as the helper showed it; None when there is none."""
    path = root / "evidence" / "evidence_bank.json"
    if not path.exists():
        return None
    bank = EvidenceBank.model_validate(_json(path))
    overrides = root / "overrides" / "user_overrides.json"
    for o in _json(overrides).get("overrides", []) if overrides.exists() else []:
        _apply_override(bank, o)
    return bank


def _target(items: list[Any], target: str, what: str) -> Any:
    found = next((i for i in items if i.id == target), None)
    if found is None:
        raise LegacyFormatError(f"an override names a {what} that is not there")
    return found


def _apply_override(bank: EvidenceBank, o: dict[str, Any]) -> None:
    applies, target, field = o.get("applies_to"), o.get("target_id", ""), o.get("field", "")
    if applies == "position":
        setattr(_target(bank.positions, target, "role"), field, o.get("value"))
    elif applies == "candidate":
        setattr(bank.candidate, field, o.get("value"))
    elif applies == "record":
        record = _target(bank.records, target, "record")
        updates = dict(o.get("values") or {})
        if field:
            updates[field] = o.get("value")
        for key, value in updates.items():
            setattr(record, key, value)
        prefix = record.source_reference + " | " if record.source_reference else ""
        record.source_reference = f"{prefix}user_verified: {o['id']} ({o.get('topic', '')})"
    elif applies == "certification":
        setattr(_target(bank.certifications, target, "certification"), field, o.get("value"))
    elif applies != "conflict":
        raise LegacyFormatError(f"override {o.get('id')}: unknown applies_to {applies!r}")


def load_draft_state(root: Path, run_id: str) -> dict[str, Any]:
    """The draft state the person was looking at (EMPTY_STATE: never edited)."""
    path = root / "drafts" / f"{run_id}.json"
    if not path.exists():
        return deepcopy(EMPTY_STATE)
    doc = _json(path)
    return dict(doc["history"][doc["cursor"]])


def apply_draft(run: TailorRun, state: dict[str, Any]) -> GeneratedResume:
    """The run's resume with the draft applied: exactly what the helper
    exported for it (with "only evidenced wording" off, which refuses
    nothing it shows). A certification line that is not one of the person's
    own was refused by the helper too, and is refused here."""
    res = run.generated_resume.model_copy(deep=True)
    bullets: dict[str, Any] = state.get("bullets", {})
    known = {f"{c.issuer}: {c.name}": c for c in res.certifications}
    if any(line not in known for line in state.get("certifications") or []):
        raise LegacyFormatError("the draft lists a certification that is not one of theirs")
    if state.get("headline"):
        res.headline = state["headline"]
    if state.get("summary") is not None:
        lines = list(state["summary"])
        for i, text in enumerate(lines[: len(res.summary)]):
            res.summary[i].text = text
        res.summary = res.summary[: len(lines)] + [
            Bullet(id=f"s-edit-{n}", text=text, origin="manual")
            for n, text in enumerate(lines[len(res.summary) :], start=1)
        ]
    for entry in res.experience:
        kept = []
        for bullet in entry.bullets:
            edit = bullets.get(bullet.id, {})
            if edit.get("hidden"):
                continue
            if edit.get("text") is not None:
                bullet.text = edit["text"]
            kept.append(bullet)
        order = state.get("order", {}).get(entry.position_id)
        if order:
            by_id = {b.id: b for b in kept}
            kept = [by_id[i] for i in order if i in by_id] + [b for b in kept if b.id not in order]
        entry.bullets = kept
    if state.get("skills") is not None:
        res.skills = [
            SkillGroup(name=g["group"], items=list(g["items"]))
            for g in state["skills"]
            if g.get("items")
        ]
    elif state.get("hidden_skills"):
        hidden = set(state["hidden_skills"])
        for group in res.skills:
            group.items = [i for i in group.items if i not in hidden]
        res.skills = [g for g in res.skills if g.items]
    if state.get("certifications") is not None:
        res.certifications = [known[line] for line in state["certifications"]]
    return res


_PLACEHOLDER_NAMES = frozenset({"you", "candidate", "my profile", "meu perfil"})
_INVALID_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def export_filename(candidate_name: str, role_title: str, headline: str, extension: str) -> str:
    """The name the helper gave an exported file: "<Name> - <Role>.<ext>"."""

    def clean(part: str) -> str:
        return re.sub(r"\s+", " ", _INVALID_FS.sub(" ", part)).strip().rstrip(". ")

    name = " ".join(str(candidate_name or "").split())
    name = clean("" if name.casefold() in _PLACEHOLDER_NAMES else name) or "Resume"
    role = clean(role_title) or clean(headline) or "Resume"
    return f"{f'{name} - {role}'[:120].rstrip('. ')}.{extension}"

# Added for the Career Agent public edition (2026-09-26). See NOTICE.
"""Resume Tailor inside Career Agent: one person, one local profile, one workspace.

When Career Agent starts Resume Tailor it passes a bridge (see
``career_agent/web/tailor_bridge.py``); this module is everything Resume Tailor
does with it. Without a bridge, Resume Tailor is the standalone app it always
was, candidate selector included.

IDENTITY. The active Career Agent profile maps to exactly one Tailor
candidate, by the profile's stable id and never by a display name: the
candidate id IS the lower-cased profile id, and ``candidate.json`` records the
profile id it belongs to. Opening the same profile again finds the same
candidate; a second profile gets its own, in its own Tailor home.

SOURCE OF TRUTH. Application status belongs to Career Agent. Tailor shows the
profile's tracked postings and changes a status only through the bridge, and
every tailored resume records the Career Agent posting it was made for
(``career_job_id``) so it attaches to that posting.

EVIDENCE. Only CONFIRMED Career Agent statements are imported, verbatim, each
marked with where it came from (``source_file: career_agent``, the claim key
in ``source_reference``). Import replaces the previous Career Agent import and
never touches details that came from Tailor's own sources.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any, Protocol

from resume_tailor.core.models import BaseResume
from resume_tailor.workspace.store import _ID, CandidateWorkspace, WorkspaceError, WorkspaceStore

PROFILE_KEY = "career_agent_profile_id"
SOURCE = "career_agent"
#: Prefix of every position and record this module writes, so a re-import can
#: replace its own rows and leave everything else alone.
PREFIX = "ca-"
BASE_RESUME_ID = "career_agent_profile"


class CareerSource(Protocol):
    """What Resume Tailor needs from Career Agent. Implemented there."""

    def profile(self) -> dict[str, str]: ...
    def statuses(self) -> list[dict[str, str]]: ...
    def job(self, job_id: str) -> dict[str, Any]: ...
    def tracked_jobs(self) -> list[dict[str, Any]]: ...
    def set_status(self, job_id: str, status: str) -> dict[str, Any]: ...
    def evidence(self) -> dict[str, Any]: ...


def _now() -> str:
    return datetime.now(UTC).isoformat()


def candidate_id_for(profile_id: str) -> str:
    cid = (profile_id or "").strip().lower()
    if not _ID.match(cid):
        raise WorkspaceError("This Career Agent profile has an id Resume Tailor cannot use.")
    return cid


def profile_candidate(store: WorkspaceStore, profile: dict[str, str]) -> CandidateWorkspace:
    """The one Tailor candidate of this Career Agent profile, created on first use.

    A Tailor home that already holds exactly one candidate no profile has
    claimed (someone used Resume Tailor before it was connected) has that
    candidate ADOPTED rather than a second one created beside it: its base
    resumes and tailored resumes stay where they are. Several unclaimed
    candidates are left alone, since picking one would be a guess."""
    cid = candidate_id_for(profile["id"])
    root = store.home / "candidates" / cid
    if (root / "candidate.json").exists():
        ws = store.get(cid)
        meta = ws.meta()
        # The id is derived from the profile id, so it is the authority. A
        # backup restored over this candidate brings its own metadata; the
        # stamp is put back rather than trusted from the file.
        if meta.get(PROFILE_KEY) != profile["id"]:
            meta[PROFILE_KEY] = profile["id"]
            ws.save_meta(meta)
        return ws
    claimed = [m for m in store.list_candidates(include_archived=True) if m.get(PROFILE_KEY)]
    for meta in claimed:
        if meta[PROFILE_KEY] == profile["id"]:
            return store.get(meta["id"])
    unclaimed = [m for m in store.list_candidates(include_archived=False) if not m.get(PROFILE_KEY)]
    if len(unclaimed) == 1 and not claimed:
        ws = store.get(unclaimed[0]["id"])
        meta = ws.meta()
        meta[PROFILE_KEY] = profile["id"]
        meta["adopted_at"] = _now()
        ws.save_meta(meta)
        return ws
    for sub in CandidateWorkspace.SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    ws = CandidateWorkspace(root)
    from resume_tailor.workspace.migrations import CURRENT_SCHEMA

    ws.save_meta(
        {
            "schema_version": CURRENT_SCHEMA,
            "id": cid,
            "name": profile.get("label") or "My profile",
            PROFILE_KEY: profile["id"],
            "email": "",
            "phone": "",
            "location": "",
            "linkedin": "",
            "portfolio": "",
            "languages": [],
            "archived": False,
            "created_at": _now(),
        }
    )
    ws.save_settings({"advanced": False, "theme": "light", "default_resume_id": ""})
    store._workspaces[cid] = ws
    return ws


# ----------------------------------------------------------------- evidence


def _key(*parts: str) -> str:
    return PREFIX + hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:20]


def _month(value: Any) -> str:
    text = str(value or "").strip()
    return text[:7] if len(text) >= 7 else text


def evidence_bank(evidence: dict[str, Any], existing: dict[str, Any] | None) -> dict[str, Any]:
    """The workspace's evidence bank with the Career Agent part replaced.

    Positions and records written by an earlier import (``ca-`` ids) are
    dropped and written again from `evidence`; everything else, and the
    conflicts and sources Tailor recorded, is kept as it was."""
    bank = dict(existing or {})
    positions = [
        p for p in bank.get("positions", []) if not str(p.get("id", "")).startswith(PREFIX)
    ]
    records = [r for r in bank.get("records", []) if not str(r.get("id", "")).startswith(PREFIX)]
    for experience in evidence.get("experiences", []):
        if not experience.get("highlights"):
            continue
        pid = _key("position", str(experience["id"]))
        start = _month(experience.get("start"))
        # No end and not current is "not stated", never "present".
        end = None if experience.get("current") else _month(experience.get("end"))
        kind = {
            "INTERNSHIP": "internship",
            "INDEPENDENT": "independent",
        }.get(str(experience.get("kind") or "").upper(), "employment")
        positions.append(
            {
                "id": pid,
                "company": experience.get("company") or "",
                "title": experience.get("title") or "",
                "start": start,
                "end": end,
                "kind": kind,
            }
        )
        for highlight in experience["highlights"]:
            text = " ".join(str(highlight["text"]).split())
            records.append(
                {
                    "id": _key("record", str(highlight["key"])),
                    "position_id": pid,
                    "company": experience.get("company") or "",
                    "role": experience.get("title") or "",
                    "start": start,
                    "end": end,
                    "claim": text,
                    "resume_text": text,
                    "skills": list(experience.get("skills") or []),
                    "source_file": SOURCE,
                    "source_reference": str(highlight["key"]),
                    "sources": [SOURCE],
                    "verification": "user_verified",
                    "confidence": 1.0,
                }
            )
    sources = dict(bank.get("sources", {}))
    sources[SOURCE] = "Career Agent (confirmed Career Profile)"
    candidate = dict(bank.get("candidate") or {})
    candidate.setdefault("name", evidence.get("name") or "")
    if not candidate.get("name"):
        candidate["name"] = evidence.get("name") or "Candidate"
    bank.update(
        candidate=candidate,
        positions=positions,
        records=records,
        sources=sources,
    )
    bank.setdefault("education", [])
    bank.setdefault("certifications", [])
    bank.setdefault("conflicts", [])
    return bank


def import_evidence(ws: CandidateWorkspace, evidence: dict[str, Any]) -> dict[str, int]:
    existing = (
        json.loads(ws.evidence_file.read_text(encoding="utf-8"))
        if ws.evidence_file.exists()
        else None
    )
    bank = evidence_bank(evidence, existing)
    ws.write_evidence(bank)
    ws.load_index()  # refuse to leave a bank Tailor cannot read
    ours = [r for r in bank["records"] if str(r["id"]).startswith(PREFIX)]
    return {
        "experiences": len({r["position_id"] for r in ours}),
        "details": len(ours),
        "skipped_without_statements": sum(
            1 for e in evidence.get("experiences", []) if not e.get("highlights")
        ),
    }


def base_resume_from_profile(ws: CandidateWorkspace, evidence: dict[str, Any]) -> BaseResume:
    """A base resume made from the confirmed Career Profile, and only from it.

    Every bullet is one confirmed statement, verbatim, carrying its evidence
    id; the headline is the most recent role title as the person wrote it.
    Nothing is summarised or generated here."""
    import_evidence(ws, evidence)
    positions = []
    skills: dict[str, str] = {}
    headline = ""
    for experience in evidence.get("experiences", []):
        if not experience.get("highlights"):
            continue
        if not headline and experience.get("title"):
            headline = str(experience["title"])
        positions.append(
            {
                "position_id": _key("position", str(experience["id"])),
                "bullets": [
                    {
                        "text": " ".join(str(h["text"]).split()),
                        "evidence_ids": [_key("record", str(h["key"]))],
                    }
                    for h in experience["highlights"]
                ],
            }
        )
        for skill in experience.get("skills") or []:
            skills.setdefault(str(skill).casefold(), str(skill))
    if not positions:
        raise WorkspaceError(
            "Your Career Profile has no confirmed experience yet. Confirm some in Career Agent,"
            " or upload a resume here."
        )
    resume = BaseResume.model_validate(
        {
            "id": BASE_RESUME_ID,
            "name": "From my Career Profile",
            "headline": headline,
            "positions": positions,
            "skills": [{"name": "Skills", "items": list(skills.values())}] if skills else [],
            "source_file": SOURCE,
        }
    )
    ws.save_base_resume(resume)
    settings = ws.settings()
    if (
        not settings.get("default_resume_id")
        or settings["default_resume_id"] not in ws.load_resumes()
    ):
        settings["default_resume_id"] = resume.id
        ws.save_settings(settings)
    return resume

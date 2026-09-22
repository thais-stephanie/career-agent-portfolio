# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Editable resume drafts: a per-application edit layer over the generated resume.

The generated run output is canonical and never mutated. A draft is a stack of
STATES stored beside the application (``drafts/<application-id>.json``) with an
undo/redo cursor; the "effective" resume the user sees and exports is the generated
resume with the current state applied on top.

A state carries only the user's decisions:

* ``headline`` / ``summary`` — replacement text, or None for the automatic version;
* ``bullets`` — per bullet id: replacement ``text`` (None = automatic), ``hidden``,
  ``locked``;
* ``order`` — per position: the bullet-id order, when the user reordered;
* ``skills`` — replacement skill groups, or None; ``hidden_skills`` — item names;
* ``certifications`` — the selected certification lines, or None for automatic;
* ``note`` — a personal note.

Evidence safety: a factual edit is re-checked with the SAME validator machinery the
pipeline uses (`check_bullet`), against the bullet's own cited evidence. A failing
edit is stored (the user's draft is theirs) but marked unsupported; with
"Only use evidenced wording" on, exports substitute the last supported wording for
unsupported bullets rather than silently exporting an unvalidated claim.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.models import Bullet, TailorRun
from resume_tailor.core.validation.validator import check_bullet
from resume_tailor.presentation import labels
from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceError

_MAX_HISTORY = 100

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


def _path(ws: CandidateWorkspace, run_id: str):
    return ws.root / "drafts" / f"{run_id}.json"


def load_doc(ws: CandidateWorkspace, run_id: str) -> dict[str, Any]:
    p = _path(ws, run_id)
    if not p.exists():
        return {"version": 1, "history": [deepcopy(EMPTY_STATE)], "cursor": 0}
    return json.loads(p.read_text(encoding="utf-8"))


def save_doc(ws: CandidateWorkspace, run_id: str, doc: dict[str, Any]) -> None:
    p = _path(ws, run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")


def current_state(doc: dict[str, Any]) -> dict[str, Any]:
    return doc["history"][doc["cursor"]]


def push_state(doc: dict[str, Any], state: dict[str, Any]) -> None:
    doc["history"] = doc["history"][: doc["cursor"] + 1]
    doc["history"].append(state)
    if len(doc["history"]) > _MAX_HISTORY:
        doc["history"] = doc["history"][-_MAX_HISTORY:]
    doc["cursor"] = len(doc["history"]) - 1


def undo(doc: dict[str, Any]) -> bool:
    if doc["cursor"] > 0:
        doc["cursor"] -= 1
        return True
    return False


def redo(doc: dict[str, Any]) -> bool:
    if doc["cursor"] < len(doc["history"]) - 1:
        doc["cursor"] += 1
        return True
    return False


def restore_automatic(doc: dict[str, Any]) -> None:
    push_state(doc, deepcopy(EMPTY_STATE))


# ---------------------------------------------------------------- validation


def validate_edit(
    run: TailorRun, index: EvidenceIndex, bullet_id: str, new_text: str
) -> dict[str, Any]:
    """Re-check an edited bullet against its OWN cited evidence, with the pipeline's
    validator. Returns a product-language verdict; internals only under 'advanced'."""
    original = None
    for e in run.generated_resume.experience:
        for b in e.bullets:
            if b.id == bullet_id:
                original = b
    if original is None:
        raise WorkspaceError("Unknown resume line.")
    trial = Bullet(
        id=original.id,
        text=new_text,
        evidence_ids=list(original.evidence_ids),
        requirement_ids=list(original.requirement_ids),
        origin="manual",
    )
    checks = check_bullet(trial, index, index.tenure_years("total_relevant"))
    failing = [c for c in checks if not c.passed]
    recs = [index.by_id[i] for i in original.evidence_ids if i in index.by_id]
    supported = [r.resume_text or r.claim for r in recs][:2]
    problems = []
    for c in failing:
        problems.append(
            {
                "what": labels.check_label(c.check)
                if c.check in labels.CHECK_LABELS
                else {
                    "terms_supported": "Wording your sources do not show",
                    "numbers_supported": "A number your sources do not show",
                    "no_overstatement": "Stronger ownership or scope than your sources show",
                    "no_upgrade": "A bigger role than your sources show",
                    "years_plausible": "More years than your history covers",
                    "bindings": "The sentence mixes details from different experiences",
                    "number_scope": "A number used outside its real meaning",
                    "evidence_exists": "This line has no supporting experience attached",
                }.get(c.check, "Wording beyond your evidence"),
                "detail": c.detail,
            }
        )
    return {
        "ok": not failing,
        "message": ""
        if not failing
        else "This wording goes beyond the evidence currently attached to this experience.",
        "supported": supported,
        "not_evidenced": [p["what"] for p in problems],
        "advanced": {"checks": [c.model_dump() for c in checks]},
    }


# ------------------------------------------------------------- effective view


def effective_resume(run: TailorRun, state: dict[str, Any], index: EvidenceIndex) -> dict[str, Any]:
    """The generated resume with the draft applied: what the user sees and exports."""
    res = run.generated_resume
    bullets_state: dict[str, Any] = state.get("bullets", {})
    out_positions = []
    unsupported: list[str] = []
    for e in res.experience:
        rows = []
        for b in e.bullets:
            bs = bullets_state.get(b.id, {})
            text = bs.get("text") if bs.get("text") is not None else b.text
            row = {
                "id": b.id,
                "text": text,
                "auto_text": b.text,
                "edited": bs.get("text") is not None,
                "hidden": bool(bs.get("hidden")),
                "locked": bool(bs.get("locked")),
                "supported": True,
            }
            if row["edited"]:
                verdict = validate_edit(run, index, b.id, text)
                row["supported"] = verdict["ok"]
                if not verdict["ok"]:
                    unsupported.append(b.id)
            rows.append(row)
        order = state.get("order", {}).get(e.position_id)
        if order:
            by_id = {r["id"]: r for r in rows}
            rows = [by_id[i] for i in order if i in by_id] + [
                r for r in rows if r["id"] not in order
            ]
        out_positions.append(
            {
                "position_id": e.position_id,
                "company": e.company,
                "title": e.title,
                "start": e.start,
                "end": e.end,
                "bullets": rows,
            }
        )
    skills = (
        state.get("skills")
        if state.get("skills") is not None
        else [
            {
                "group": g.name,
                "items": [i for i in g.items if i not in set(state.get("hidden_skills", []))],
            }
            for g in res.skills
        ]
    )
    certs = (
        state.get("certifications")
        if state.get("certifications") is not None
        else [f"{c.issuer}: {c.name}" for c in res.certifications]
    )
    return {
        "headline": state.get("headline") or res.headline,
        "summary": state.get("summary")
        if state.get("summary") is not None
        else [b.text for b in res.summary],
        "experience": out_positions,
        "skills": skills,
        "certifications": certs,
        "note": state.get("note", ""),
        "edited": state != EMPTY_STATE,
        "unsupported_bullets": unsupported,
    }


def export_resume(
    run: TailorRun, state: dict[str, Any], index: EvidenceIndex, evidence_only: bool = True
):
    """A GeneratedResume clone with the draft applied, for the existing exporters.
    With evidence-only mode on, an unsupported edited bullet exports its last
    validated (automatic) wording instead — an unvalidated claim never ships silently."""
    res = run.generated_resume.model_copy(deep=True)
    bullets_state: dict[str, Any] = state.get("bullets", {})
    if state.get("headline"):
        res.headline = state["headline"]
    if state.get("summary") is not None:
        for i, text in enumerate(state["summary"]):
            if i < len(res.summary):
                res.summary[i].text = text
        res.summary = res.summary[: len(state["summary"])]
    for e in res.experience:
        kept = []
        for b in e.bullets:
            bs = bullets_state.get(b.id, {})
            if bs.get("hidden"):
                continue
            if bs.get("text") is not None:
                verdict = validate_edit(run, index, b.id, bs["text"])
                if verdict["ok"] or not evidence_only:
                    b.text = bs["text"]
                # else: keep the automatic, validated wording
            kept.append(b)
        order = state.get("order", {}).get(e.position_id)
        if order:
            by_id = {b.id: b for b in kept}
            kept = [by_id[i] for i in order if i in by_id] + [b for b in kept if b.id not in order]
        e.bullets = kept
    if state.get("skills") is not None:
        from resume_tailor.core.models import SkillGroup

        res.skills = [
            SkillGroup(name=g["group"], items=list(g["items"]))
            for g in state["skills"]
            if g.get("items")
        ]
    elif state.get("hidden_skills"):
        hidden = set(state["hidden_skills"])
        for g in res.skills:
            g.items = [i for i in g.items if i not in hidden]
        res.skills = [g for g in res.skills if g.items]
    if state.get("certifications") is not None:
        selected = set(state["certifications"])
        res.certifications = [c for c in res.certifications if f"{c.issuer}: {c.name}" in selected]
    return res

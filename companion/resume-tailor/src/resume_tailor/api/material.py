# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Candidate material routes: base resumes, sources, experience & evidence review.

Same contract as api/candidates.py: candidate-scoped, product language by default,
diagnostics only under ``advanced`` fields. The evidence-safety rules live in
workspace/sources.py — these routes only translate them to HTTP.
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from resume_tailor.core.models import BaseResume
from resume_tailor.importing.resume_parse import parse_resume, to_base_resume_json
from resume_tailor.presentation import labels
from resume_tailor.workspace import WorkspaceError, WorkspaceStore
from resume_tailor.workspace import sources as src
from resume_tailor.workspace.migrations import SchemaTooNew


class ConfirmIn(BaseModel):
    choice: str = ""  # one of the source versions, verbatim
    value: str = ""  # or the user's own corrected value
    note: str = ""


class ConfirmDetailIn(BaseModel):
    text: str = ""  # the user's corrected wording (empty = confirm as written)
    note: str = ""


class AcceptDetailIn(BaseModel):
    company: str = ""
    title: str = ""
    start: str = ""
    end: str | None = None


def build_material_router(store: WorkspaceStore) -> APIRouter:
    router = APIRouter(prefix="/api/candidates/{cid}")

    def ws_for(cid: str):
        try:
            return store.get(cid)
        except SchemaTooNew as e:
            raise HTTPException(409, str(e)) from e
        except WorkspaceError as e:
            raise HTTPException(404, str(e)) from e

    # ------------------------------------------------------------ base resumes
    @router.get("/resumes")
    def list_resumes(cid: str) -> list[dict[str, Any]]:
        ws = ws_for(cid)
        default = ws.settings().get("default_resume_id", "")
        out = []
        for r in ws.load_resumes().values():
            out.append(
                {
                    "id": r.id,
                    "name": r.name,
                    "headline": r.headline,
                    "roles": len(r.positions),
                    "skills": sum(len(g.items) for g in r.skills),
                    "default": r.id == default,
                }
            )
        return out

    @router.get("/resumes/{resume_id}")
    def resume_detail(cid: str, resume_id: str) -> dict[str, Any]:
        r = ws_for(cid).load_resumes().get(resume_id)
        if not r:
            raise HTTPException(404, "Unknown base resume.")
        return r.model_dump(mode="json")

    @router.post("/resumes/upload")
    async def upload_resume(
        cid: str, file: UploadFile = File(...), name: str = Form("")
    ) -> dict[str, Any]:
        """Upload creates a base resume ONLY. Reviewing it for experience details is a
        separate, optional step (`/sources` with the same file), never automatic."""
        ws = ws_for(cid)
        data = await file.read()
        try:
            parsed = parse_resume(file.filename or "resume", data)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        display = (
            name.strip()
            or parsed.headline
            or (file.filename or "Uploaded resume").rsplit(".", 1)[0]
        )
        rid = re.sub(r"[^a-z0-9]+", "_", display.lower()).strip("_")[:48] or "uploaded_resume"
        existing = ws.load_resumes()
        if rid in existing:
            rid = f"{rid}_{len(existing) + 1}"
        doc = to_base_resume_json(parsed, rid, display, file.filename or "")
        ws.save_base_resume(BaseResume.model_validate(doc))
        return {
            "id": rid,
            "name": display,
            "extracted": parsed.summary_counts(),
            "next_steps": {
                "use_for_tailoring": {"enabled": True, "label": "Use this resume for tailoring"},
                "review_for_experience": {
                    "enabled": False,
                    "label": "Also review this resume for new experience details",
                },
            },
        }

    @router.patch("/resumes/{resume_id}")
    def edit_resume(cid: str, resume_id: str, body: dict[str, Any]) -> dict[str, Any]:
        ws = ws_for(cid)
        r = ws.load_resumes().get(resume_id)
        if not r:
            raise HTTPException(404, "Unknown base resume.")
        data = r.model_dump(mode="json")
        for key in ("name", "headline", "summary", "skills", "positions"):
            if key in body:
                data[key] = body[key]
        try:
            updated = BaseResume.model_validate(data)
        except Exception as e:
            raise HTTPException(400, f"That change is not valid: {e}") from e
        ws.save_base_resume(updated)
        return updated.model_dump(mode="json")

    @router.post("/resumes/{resume_id}/duplicate")
    def duplicate_resume(cid: str, resume_id: str) -> dict[str, Any]:
        ws = ws_for(cid)
        r = ws.load_resumes().get(resume_id)
        if not r:
            raise HTTPException(404, "Unknown base resume.")
        data = r.model_dump(mode="json")
        data["id"] = f"{r.id}_copy"
        n = 2
        while data["id"] in ws.load_resumes():
            data["id"] = f"{r.id}_copy{n}"
            n += 1
        data["name"] = f"{r.name} (copy)"
        ws.save_base_resume(BaseResume.model_validate(data))
        return {"id": data["id"], "name": data["name"]}

    @router.post("/resumes/{resume_id}/default")
    def set_default_resume(cid: str, resume_id: str) -> dict[str, Any]:
        ws = ws_for(cid)
        if resume_id not in ws.load_resumes():
            raise HTTPException(404, "Unknown base resume.")
        settings = ws.settings()
        settings["default_resume_id"] = resume_id
        ws.save_settings(settings)
        return settings

    @router.delete("/resumes/{resume_id}")
    def delete_resume(cid: str, resume_id: str, confirm: bool = False) -> dict[str, str]:
        ws = ws_for(cid)
        p = ws.root / "base_resumes" / f"{resume_id}.json"
        if not p.exists() or resume_id not in ws.load_resumes():
            raise HTTPException(404, "Unknown base resume.")
        if not confirm:
            raise HTTPException(
                400, "Deleting a base resume cannot be undone. Confirm to continue."
            )
        p.unlink()
        ws.invalidate()
        return {"deleted": resume_id}

    # ----------------------------------------------------------------- sources
    @router.get("/sources")
    def list_sources(cid: str) -> list[dict[str, Any]]:
        ws = ws_for(cid)
        bank_conflicts = _open_conflicts(ws)
        out = []
        for entry in src.read_registry(ws):
            conflict_count = sum(
                1
                for c in bank_conflicts
                if any(entry["id"] in s.get("source", "") for s in c["statements"])
            )
            out.append(
                {
                    "id": entry["id"],
                    "name": entry["name"],
                    "kind": labels.source_kind_label(entry.get("kind", "other")),
                    "added": entry.get("added_at", ""),
                    "details": entry.get("detail_count", 0),
                    "conflicts": conflict_count,
                }
            )
        return out

    @router.post("/sources")
    async def add_source(
        cid: str, file: UploadFile = File(...), kind: str = Form("other"), name: str = Form("")
    ) -> dict[str, Any]:
        ws = ws_for(cid)
        data = await file.read()
        try:
            entry = src.add_source(ws, file.filename or "document", data, kind=kind, name=name)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {
            "id": entry["id"],
            "name": entry["name"],
            "extracted": entry["extracted"],
            "note": "Nothing was added to your experience yet. Review the extracted details to decide.",
        }

    @router.get("/sources/{source_id}/details")
    def source_details(cid: str, source_id: str) -> list[dict[str, Any]]:
        return src.suggestions(ws_for(cid), source_id)

    @router.post("/sources/{source_id}/details/{n}/accept")
    def accept_detail(cid: str, source_id: str, n: int, body: AcceptDetailIn) -> dict[str, str]:
        try:
            rid = src.accept_suggestion(
                ws_for(cid),
                source_id,
                n,
                company=body.company,
                title=body.title,
                start=body.start,
                end=body.end,
            )
        except WorkspaceError as e:
            raise HTTPException(400, str(e)) from e
        return {"added": rid, "status": "Supported by sources"}

    @router.delete("/sources/{source_id}")
    def remove_source(cid: str, source_id: str, force: bool = False) -> dict[str, Any]:
        try:
            return src.remove_source(ws_for(cid), source_id, force=force)
        except WorkspaceError as e:
            raise HTTPException(409 if "came from this source" in str(e) else 404, str(e)) from e

    # --------------------------------------------------- experience & evidence
    def _open_conflicts(ws) -> list[dict[str, Any]]:
        if not ws.evidence_file.exists():
            return []
        index = ws.load_index()
        return [
            {"id": c.id, "topic": c.topic, "statements": c.statements}
            for c in index.bank.conflicts
            if not c.resolved_by
        ]

    @router.get("/experience")
    def experience(cid: str, advanced: bool = False) -> dict[str, Any]:
        ws = ws_for(cid)
        if not ws.evidence_file.exists():
            return {
                "total": 0,
                "confirmed": 0,
                "supported": 0,
                "needs_review": 0,
                "conflicting": 0,
                "items": [],
            }
        index = ws.load_index()
        open_conflicts = {c["id"] for c in _open_conflicts(ws)}
        items = []
        counts = {"confirmed": 0, "supported": 0, "needs_review": 0, "conflicting": 0}
        for r in index.bank.records:
            strength = index.strength(r).value
            review = None
            if r.verification.value == "user_verified" or "user_verified" in r.sources:
                status, bucket = "Confirmed by you", "confirmed"
            elif set(r.conflict_ids) & open_conflicts:
                status, bucket = "Needs review", "conflicting"
                review = {
                    "kind": "conflict",
                    "why": "Your sources disagree about this. Resolve it above.",
                }
            elif not r.include_by_default:
                status, bucket = "Needs review", "needs_review"
                review = _review_reason(index, r)
            else:
                status, bucket = labels.source_strength_label(strength), "supported"
            counts[bucket] += 1
            item = {
                "company": r.company,
                "title": r.project or r.role,
                "text": r.resume_text or r.claim,
                "status": status,
                "sources": [
                    labels.source_kind_label(src_kind_of(index.bank.sources, s)) for s in r.sources
                ],
            }
            if review is not None:
                item["review"] = review
                if review["kind"] != "conflict":
                    item["ref"] = r.id  # a handle for the confirm action; never rendered
            if advanced:
                item["advanced"] = {
                    "evidence_id": r.id,
                    "source_strength": strength,
                    "verification": r.verification.value,
                    "proficiency": r.proficiency.value,
                }
            items.append(item)
        return {"total": len(items), **counts, "items": items}

    def _review_reason(index, r) -> dict[str, Any]:
        """Why a detail without a competing source still needs the user, in plain words.
        Every reason has an action: confirm (optionally with corrected wording) or leave it."""
        pos = index.positions.get(r.position_id)
        role_left_out = (
            pos is not None and not pos.include_by_default and pos.kind.value != "cross_cutting"
        )
        if not r.sources:
            why = "Its source was removed, so nothing backs it right now. Confirm it yourself, or leave it out."
        elif role_left_out:
            why = (
                f"It belongs to a role that is left out of your resumes ({pos.company}). "
                "Confirming it makes the detail and that role available to future resumes."
            )
        else:
            why = (
                "It was set aside from your resumes. Confirm it to make it usable, or leave it out."
            )
        return {"kind": "unconfirmed", "why": why, "includes_role": role_left_out}

    def _raw_record(ws, rid: str) -> dict[str, Any] | None:
        bank = json.loads(ws.evidence_file.read_text(encoding="utf-8"))
        return next((r for r in bank.get("records", []) if r.get("id") == rid), None)

    @router.post("/experience/{ref}/confirm")
    def confirm_detail(cid: str, ref: str, body: ConfirmDetailIn) -> dict[str, str]:
        """The user vouches for a detail that had no competing source: it becomes
        "Confirmed by you" through the same override mechanism as a resolved conflict.
        The source wording stays on disk; a corrected wording is stored beside it."""
        from datetime import UTC, datetime

        ws = ws_for(cid)
        index = ws.load_index()
        r = index.by_id.get(ref)
        if r is None or r.include_by_default or r.verification.value == "user_verified":
            raise HTTPException(404, "Nothing to review here any more.")
        raw = _raw_record(ws, ref) or {}
        original = raw.get("resume_text") or raw.get("claim") or r.resume_text
        text = body.text.strip()
        values: dict[str, Any] = {"include_by_default": True}
        if text and text != original:
            values.update({"resume_text": text, "claim": text, "superseded_text": original})
        doc = _overrides_doc(ws)
        oid = _next_confirmation_id(doc)
        topic = r.project or r.role
        today = datetime.now(UTC).date().isoformat()
        doc.setdefault("overrides", []).append(
            {
                "id": oid,
                "topic": topic,
                "applies_to": "record",
                "target_id": ref,
                "values": values,
                "record_verification": {ref: "user_verified"},
                "note": body.note.strip(),
                "confirmed_at": today,
            }
        )
        pos = index.positions.get(r.position_id)
        if pos is not None and not pos.include_by_default and pos.kind.value != "cross_cutting":
            # the detail can only appear if its role can: include the role with it, as a
            # companion entry that reopening the decision takes back too
            doc["overrides"].append(
                {
                    "id": f"{oid}_role",
                    "topic": f"{pos.company} role included",
                    "applies_to": "position",
                    "target_id": pos.id,
                    "field": "include_by_default",
                    "value": True,
                    "note": f"Included when the detail \u201c{topic}\u201d was confirmed.",
                    "confirmed_at": today,
                }
            )
        ws.write_overrides(doc)
        return {"status": "Confirmed by you", "topic": topic}

    @router.get("/conflicts")
    def conflicts(cid: str) -> list[dict[str, Any]]:
        out = []
        for c in _open_conflicts(ws_for(cid)):
            out.append(
                {
                    "id": c["id"],
                    "topic": c["topic"],
                    "message": "We found different versions of this detail.",
                    "versions": _versions_view(c["statements"]),
                }
            )
        return out

    # -------------------------------------------------- the user's decisions
    #
    # A decision is stored through the existing safe override mechanism
    # (``applies_to: "conflict"``): the conflict gains ``resolved_by`` at load
    # time and every original source statement is preserved verbatim. Editing a
    # decision archives the previous entry under the document's ``history`` list
    # (which the loader never applies) and appends a fresh one; reopening
    # archives the decision so the disagreement surfaces again. The normal UI
    # only ever sees "Confirmed by you" — never the mechanism.

    def _overrides_doc(ws) -> dict[str, Any]:
        if ws.overrides_file.exists():
            return json.loads(ws.overrides_file.read_text(encoding="utf-8"))
        return {"overrides": []}

    def _next_confirmation_id(doc: dict[str, Any]) -> str:
        taken = {o.get("id", "") for o in [*doc.get("overrides", []), *doc.get("history", [])]}
        n = len(taken) + 1
        while f"user_confirmation_{n:03d}" in taken:
            n += 1
        return f"user_confirmation_{n:03d}"

    def _append_decision(ws, conflict_id: str, topic: str, value: str, note: str) -> str:
        from datetime import UTC, datetime

        doc = _overrides_doc(ws)
        oid = _next_confirmation_id(doc)
        doc.setdefault("overrides", []).append(
            {
                "id": oid,
                "topic": topic,
                "applies_to": "conflict",
                "conflict_id": conflict_id,
                "value": value,
                "note": note,
                "confirmed_at": datetime.now(UTC).date().isoformat(),
            }
        )
        ws.write_overrides(doc)
        return oid

    def _decision_rows(ws, advanced: bool) -> list[dict[str, Any]]:
        if not ws.evidence_file.exists():
            return []
        index = ws.load_index()
        conflicts = {c.id: c for c in index.bank.conflicts}
        doc = _overrides_doc(ws)
        latest: dict[str, dict[str, Any]] = {}
        for o in doc.get("overrides", []):
            if o.get("applies_to") == "conflict" and o.get("conflict_id"):
                latest[o["conflict_id"]] = o  # later entries supersede earlier ones
        rows = []
        for conflict_id, o in latest.items():
            c = conflicts.get(conflict_id)
            row = {
                "ref": o["id"],
                "topic": o.get("topic", ""),
                "value": o.get("value", ""),
                "note": o.get("note", ""),
                "date": o.get("confirmed_at", ""),
                "status": "Confirmed by you",
                # the original source versions stay visible; nothing was rewritten
                "versions": _versions_view(c.statements) if c else [],
            }
            if advanced:
                row["advanced"] = {
                    "override_id": o["id"],
                    "conflict_id": conflict_id,
                    "resolved_by": c.resolved_by if c else None,
                }
            rows.append(row)
        # details the user confirmed directly (no competing source): the confirmed wording,
        # with the source wording kept visible when it was corrected
        raw_records = {
            r["id"]: r
            for r in json.loads(ws.evidence_file.read_text(encoding="utf-8")).get("records", [])
        }
        latest_rec: dict[str, dict[str, Any]] = {}
        for o in doc.get("overrides", []):
            # only confirmations made in the app (they carry include_by_default); an authored
            # clause-scoped confirmation is not a decision the app can edit or take back
            if (
                o.get("applies_to") == "record"
                and o.get("target_id") in raw_records
                and not o.get("conflict_id")
                and "include_by_default" in (o.get("values") or {})
            ):
                latest_rec[o["target_id"]] = o
        for rid, o in latest_rec.items():
            raw = raw_records[rid]
            r = index.by_id.get(rid)
            values = o.get("values") or {}
            original = raw.get("resume_text") or raw.get("claim") or ""
            confirmed = values.get("resume_text") or (r.resume_text if r else original)
            src_label = (
                ", ".join(
                    labels.source_kind_label(src_kind_of(index.bank.sources, k))
                    for k in raw.get("sources", [])
                    if k != "user_verified"
                )
                or "Source"
            )
            row = {
                "ref": o["id"],
                "topic": o.get("topic") or raw.get("project") or raw.get("role") or "",
                "value": confirmed,
                "note": o.get("note", ""),
                "date": o.get("confirmed_at", ""),
                "status": "Confirmed by you",
                "versions": [{"source": src_label, "statement": original}]
                if original and original != confirmed
                else [],
            }
            if advanced:
                row["advanced"] = {"override_id": o["id"], "evidence_id": rid, "values": values}
            rows.append(row)
        return rows

    def _versions_view(statements: list[dict[str, str]]) -> list[dict[str, str]]:
        def name(key: str) -> str:
            if key in labels.SOURCE_KIND_LABELS:
                return labels.source_kind_label(key)
            # a named source ("resume_gtm_cv", "linkedin_profile"): readable, brand spelt right
            return key.replace("_", " ").title().replace("Linkedin", "LinkedIn").replace("Cv", "CV")

        return [
            {"source": name(s.get("source", "")), "statement": s.get("statement", "")}
            for s in statements
        ]

    @router.post("/conflicts/{conflict_id}/confirm")
    def confirm_conflict(cid: str, conflict_id: str, body: ConfirmIn) -> dict[str, str]:
        """The user said which version is correct — or gave the correct value."""
        ws = ws_for(cid)
        open_ = {c["id"]: c for c in _open_conflicts(ws)}
        if conflict_id not in open_:
            raise HTTPException(404, "Nothing to review here any more.")
        value = (body.value or body.choice).strip()
        if not value:
            raise HTTPException(
                400, "Say which version is correct, or enter the correct information."
            )
        _append_decision(ws, conflict_id, open_[conflict_id]["topic"], value, body.note)
        return {"status": "Confirmed by you", "topic": open_[conflict_id]["topic"]}

    @router.get("/decisions")
    def decisions(cid: str, advanced: bool = False) -> list[dict[str, Any]]:
        """Details the user confirmed after reviewing a disagreement."""
        return _decision_rows(ws_for(cid), advanced)

    @router.patch("/decisions/{ref}")
    def edit_decision(cid: str, ref: str, body: ConfirmIn) -> dict[str, str]:
        """A new confirmation replaces the old one; the old one is kept as history."""
        ws = ws_for(cid)
        value = (body.value or body.choice).strip()
        if not value:
            raise HTTPException(400, "Enter the correct information.")
        doc = _overrides_doc(ws)
        entry = next(
            (
                o
                for o in doc.get("overrides", [])
                if o.get("id") == ref and o.get("applies_to") in ("conflict", "record")
            ),
            None,
        )
        if entry is None:
            raise HTTPException(404, "That decision no longer exists.")
        doc["overrides"] = [o for o in doc["overrides"] if o.get("id") != ref]
        doc.setdefault("history", []).append(entry)
        ws.write_overrides(doc)  # keep file consistent even if the append below fails
        if entry["applies_to"] == "record":
            from datetime import UTC, datetime

            rid = entry["target_id"]
            raw = _raw_record(ws, rid) or {}
            original = raw.get("resume_text") or raw.get("claim") or ""
            values = {**(entry.get("values") or {}), "include_by_default": True}
            if value != original:
                values.update({"resume_text": value, "claim": value, "superseded_text": original})
            else:
                for k in ("resume_text", "claim", "superseded_text"):
                    values.pop(k, None)
            doc = _overrides_doc(ws)
            doc.setdefault("overrides", []).append(
                {
                    "id": _next_confirmation_id(doc),
                    "topic": entry.get("topic", ""),
                    "applies_to": "record",
                    "target_id": rid,
                    "values": values,
                    "record_verification": {rid: "user_verified"},
                    "note": body.note.strip(),
                    "confirmed_at": datetime.now(UTC).date().isoformat(),
                }
            )
            ws.write_overrides(doc)
            return {"status": "Confirmed by you", "topic": entry.get("topic", "")}
        _append_decision(ws, entry["conflict_id"], entry.get("topic", ""), value, body.note)
        return {"status": "Confirmed by you", "topic": entry.get("topic", "")}

    @router.post("/decisions/{ref}/reopen")
    def reopen_decision(cid: str, ref: str) -> dict[str, str]:
        """Take the user's decision back: the detail returns to what the sources say,
        and if they still disagree the review surfaces again."""
        ws = ws_for(cid)
        doc = _overrides_doc(ws)
        entry = next(
            (
                o
                for o in doc.get("overrides", [])
                if o.get("id") == ref and o.get("applies_to") in ("conflict", "record")
            ),
            None,
        )
        if entry is None:
            raise HTTPException(404, "That decision no longer exists.")
        if entry["applies_to"] == "record":
            # the confirmation and any companion entries (the role it included) go back
            ids = {
                o["id"]
                for o in doc["overrides"]
                if o.get("id") == ref or str(o.get("id", "")).startswith(f"{ref}_")
            }
            moved = [o for o in doc["overrides"] if o.get("id") in ids]
            doc["overrides"] = [o for o in doc["overrides"] if o.get("id") not in ids]
            doc.setdefault("history", []).extend(moved)
            ws.write_overrides(doc)
            return {"status": "Back to review", "topic": entry.get("topic", "")}
        conflict_id = entry.get("conflict_id")
        moved = [
            o
            for o in doc["overrides"]
            if o.get("applies_to") == "conflict" and o.get("conflict_id") == conflict_id
        ]
        doc["overrides"] = [o for o in doc["overrides"] if o not in moved]
        doc.setdefault("history", []).extend(moved)
        ws.write_overrides(doc)
        return {"status": "Back to review", "topic": entry.get("topic", "")}

    return router


def src_kind_of(bank_sources: dict[str, str], key: str) -> str:
    low = key.lower()
    if "linkedin" in low:
        return "profile"
    if "portfolio" in low:
        return "portfolio"
    if "case" in low or "study" in low or "documentation" in low:
        return "case_study"
    if "resume" in low or "cv" in low:
        return "resume"
    if "verified" in low:
        return "confirmed"
    return "other"

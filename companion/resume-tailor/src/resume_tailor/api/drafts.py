# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Editable resume draft routes: view, edit, undo/redo, restore automatic.

The canonical generated run is never mutated; drafts live in the workspace
(workspace/drafts.py) and factual edits are re-validated with the pipeline's own
checks. Warnings speak product language; validator diagnostics only under
``advanced``.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resume_tailor.workspace import WorkspaceError, WorkspaceStore
from resume_tailor.workspace import drafts as dr
from resume_tailor.workspace.migrations import SchemaTooNew


class EditIn(BaseModel):
    op: str
    bullet_id: str = ""
    text: str | None = None
    position_id: str = ""
    order: list[str] = []
    hidden: bool | None = None
    locked: bool | None = None
    summary: list[str] | None = None
    skills: list[dict[str, Any]] | None = None
    hidden_skills: list[str] | None = None
    certifications: list[str] | None = None
    note: str | None = None


def build_drafts_router(store: WorkspaceStore) -> APIRouter:
    router = APIRouter(prefix="/api/candidates/{cid}/applications/{run_id}/draft")

    def load(cid: str, run_id: str):
        try:
            ws = store.get(cid)
        except SchemaTooNew as e:
            raise HTTPException(409, str(e)) from e
        except WorkspaceError as e:
            raise HTTPException(404, str(e)) from e
        run = ws.run_store().load(run_id)
        if run is None:
            raise HTTPException(404, "Unknown application.")
        return ws, run

    def view(ws, run, doc, advanced: bool = False) -> dict[str, Any]:
        index = ws.load_index()
        state = dr.current_state(doc)
        payload = {
            "resume": dr.effective_resume(run, state, index),
            "can_undo": doc["cursor"] > 0,
            "can_redo": doc["cursor"] < len(doc["history"]) - 1,
        }
        return payload

    @router.get("")
    def get_draft(cid: str, run_id: str, advanced: bool = False) -> dict[str, Any]:
        ws, run = load(cid, run_id)
        return view(ws, run, dr.load_doc(ws, run_id), advanced)

    @router.post("/edit")
    def edit(cid: str, run_id: str, body: EditIn, advanced: bool = False) -> dict[str, Any]:
        ws, run = load(cid, run_id)
        doc = dr.load_doc(ws, run_id)
        state = deepcopy(dr.current_state(doc))
        warning: dict[str, Any] | None = None
        if body.op == "headline":
            state["headline"] = body.text or None
        elif body.op == "summary":
            state["summary"] = body.summary
        elif body.op == "note":
            state["note"] = body.note or ""
        elif body.op == "skills":
            state["skills"] = body.skills
        elif body.op == "hidden_skills":
            state["hidden_skills"] = body.hidden_skills or []
        elif body.op == "certifications":
            state["certifications"] = body.certifications
        elif body.op == "reorder":
            if not body.position_id:
                raise HTTPException(400, "Which role are you reordering?")
            state.setdefault("order", {})[body.position_id] = body.order
        elif body.op in ("bullet_text", "bullet_hide", "bullet_lock"):
            if not body.bullet_id:
                raise HTTPException(400, "Which resume line?")
            bs = state.setdefault("bullets", {}).setdefault(
                body.bullet_id, {"text": None, "hidden": False, "locked": False}
            )
            if body.op == "bullet_text":
                bs["text"] = body.text
                if body.text is not None:
                    index = ws.load_index()
                    try:
                        warning = dr.validate_edit(run, index, body.bullet_id, body.text)
                    except WorkspaceError as e:
                        raise HTTPException(404, str(e)) from e
                    if not advanced:
                        warning = {k: v for k, v in warning.items() if k != "advanced"}
            elif body.op == "bullet_hide":
                bs["hidden"] = bool(body.hidden)
            else:
                bs["locked"] = bool(body.locked)
        else:
            raise HTTPException(400, f"Unknown edit '{body.op}'.")
        dr.push_state(doc, state)
        dr.save_doc(ws, run_id, doc)
        payload = view(ws, run, doc, advanced)
        if warning is not None:
            payload["edit_check"] = warning
        return payload

    @router.post("/undo")
    def undo(cid: str, run_id: str) -> dict[str, Any]:
        ws, run = load(cid, run_id)
        doc = dr.load_doc(ws, run_id)
        dr.undo(doc)
        dr.save_doc(ws, run_id, doc)
        return view(ws, run, doc)

    @router.post("/redo")
    def redo(cid: str, run_id: str) -> dict[str, Any]:
        ws, run = load(cid, run_id)
        doc = dr.load_doc(ws, run_id)
        dr.redo(doc)
        dr.save_doc(ws, run_id, doc)
        return view(ws, run, doc)

    @router.post("/restore")
    def restore(cid: str, run_id: str) -> dict[str, Any]:
        ws, run = load(cid, run_id)
        doc = dr.load_doc(ws, run_id)
        dr.restore_automatic(doc)
        dr.save_doc(ws, run_id, doc)
        return view(ws, run, doc)

    return router

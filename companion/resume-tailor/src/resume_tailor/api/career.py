# Added for the Career Agent public edition (2026-09-26). See NOTICE.
"""Routes that exist only when Career Agent started Resume Tailor.

``/api/workspace`` answers in both modes, so the page knows whether it
follows a Career Agent profile or is the standalone app. Everything under
``/api/career`` needs the bridge and answers 404 without it.

The bridge's own exceptions are matched by NAME, not imported: Resume Tailor
never imports Career Agent.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from resume_tailor.api import errors
from resume_tailor.api.errors import user_error
from resume_tailor.integration import career as ca
from resume_tailor.workspace import WorkspaceError, WorkspaceStore


class StatusIn(BaseModel):
    status: str


def _http(exc: Exception) -> HTTPException:
    name = type(exc).__name__
    if name == "BridgeRetired":
        return user_error(409, "stale_profile", str(exc))
    if name == "BridgeNotFound":
        return user_error(404, "posting_not_found", str(exc))
    if isinstance(exc, ca.ProfileNotReady):
        return user_error(400, exc.code, str(exc))
    if isinstance(exc, (ValueError, WorkspaceError)):
        return user_error(400, "invalid", str(exc))
    errors.log.exception("Career Agent bridge failed")
    return user_error(
        503, "career_unavailable", "Career Agent could not answer. Try again in a moment."
    )


def artefacts_by_job(ws: Any) -> dict[str, list[dict[str, Any]]]:
    """Tailored resumes of this workspace, grouped by the Career Agent posting
    they were made for."""
    out: dict[str, list[dict[str, Any]]] = {}
    for row in ws.run_store().list():
        run_id = row.get("run_id", "")
        meta_path = ws.root / "applications" / run_id / "application.json"
        if not meta_path.exists():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        job_id = meta.get("career_job_id")
        if not job_id:
            continue
        out.setdefault(str(job_id), []).append(
            {
                "id": run_id,
                "date": meta.get("created_at", ""),
                "state": row.get("status", ""),
                "match": row.get("overall_coverage"),
            }
        )
    return out


def build_career_router(store: WorkspaceStore, bridge: Any | None) -> APIRouter:
    router = APIRouter()

    def need_bridge() -> Any:
        if bridge is None:
            raise user_error(
                404, "not_connected", "Resume Tailor is not connected to Career Agent."
            )
        return bridge

    def workspace() -> Any:
        b = need_bridge()
        try:
            return ca.profile_candidate(store, b.profile())
        except Exception as exc:
            raise _http(exc) from exc

    @router.get("/api/workspace")
    def workspace_info() -> dict[str, Any]:
        if bridge is None:
            return {"mode": "standalone"}
        try:
            profile = bridge.profile()
            ws = ca.profile_candidate(store, profile)
        except Exception as exc:
            raise _http(exc) from exc
        return {
            "mode": "profile",
            "profile": profile,
            "candidate_id": ws.id,
            "candidate_name": ws.meta().get("name", ""),
            "statuses": bridge.statuses(),
        }

    @router.get("/api/career/jobs/{job_id}")
    def career_job(job_id: str) -> dict[str, Any]:
        b = need_bridge()
        try:
            return b.job(job_id)
        except Exception as exc:
            raise _http(exc) from exc

    @router.get("/api/career/applications")
    def career_applications() -> dict[str, Any]:
        b = need_bridge()
        ws = workspace()
        try:
            jobs = b.tracked_jobs()
        except Exception as exc:
            raise _http(exc) from exc
        artefacts = artefacts_by_job(ws)
        return {
            "statuses": b.statuses(),
            "jobs": [{**job, "tailored": artefacts.get(job["job_id"], [])} for job in jobs],
        }

    @router.patch("/api/career/applications/{job_id}")
    def career_set_status(job_id: str, body: StatusIn) -> dict[str, Any]:
        b = need_bridge()
        try:
            return b.set_status(job_id, body.status)
        except Exception as exc:
            raise _http(exc) from exc

    @router.get("/api/career/evidence")
    def career_evidence() -> dict[str, Any]:
        """A summary of what the Career Profile holds; the statements themselves
        reach Tailor only through an import the person asks for."""
        b = need_bridge()
        try:
            evidence = b.evidence()
        except Exception as exc:
            raise _http(exc) from exc
        usable = [e for e in evidence["experiences"] if e.get("highlights")]
        return {
            "experiences": len(usable),
            "details": sum(len(e["highlights"]) for e in usable),
            "waiting_or_empty": len(evidence["experiences"]) - len(usable),
        }

    @router.post("/api/career/evidence/import")
    def career_import() -> dict[str, Any]:
        b = need_bridge()
        ws = workspace()
        try:
            return ca.import_evidence(ws, b.evidence())
        except Exception as exc:
            raise _http(exc) from exc

    @router.post("/api/career/base-resume")
    def career_base_resume() -> dict[str, Any]:
        b = need_bridge()
        ws = workspace()
        try:
            resume = ca.base_resume_from_profile(ws, b.evidence())
        except Exception as exc:
            raise _http(exc) from exc
        return {
            "id": resume.id,
            "name": resume.name,
            "roles": len(resume.positions),
            "details": sum(len(p.bullets) for p in resume.positions),
        }

    return router

# Modified for the Career Agent public edition (2026-09-26). See NOTICE.
"""Candidate-scoped API.

Every route names its candidate explicitly (`/api/candidates/{cid}/...`); there is no
server-global active candidate, so two browser tabs on different candidates can never
leak context into each other. Responses come in two shapes:

* the default ("simple") payloads use product language from
  :mod:`resume_tailor.presentation.labels` — no evidence ids, enum values or
  snake_case vocabulary;
* fields for the Advanced view are grouped under ``"advanced"`` keys and are only
  included when the request asks for them (``?advanced=1``).
"""

from __future__ import annotations

import json
import threading
import traceback
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel

from resume_tailor.api.errors import user_error
from resume_tailor.core.models import TailorOptions, TailorRequest, TailorRun
from resume_tailor.core.pipeline import TailorService
from resume_tailor.export.exporters import EXPORTERS, export_filename
from resume_tailor.presentation import labels
from resume_tailor.storage.runs import new_run_id
from resume_tailor.workspace import WorkspaceError, WorkspaceStore
from resume_tailor.workspace.migrations import SchemaTooNew


class CandidateIn(BaseModel):
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio: str = ""
    languages: list[str] = []


class TailorIn(BaseModel):
    jd_text: str
    resume_id: str = ""
    #: The Career Agent posting this resume is for, when Tailor follows a
    #: profile. The resume attaches to that posting; the posting's own
    #: status stays Career Agent's.
    career_job_id: str = ""
    target_profile: str | None = None
    options: dict[str, Any] = {}


class ApplicationPatch(BaseModel):
    status: str | None = None
    company: str | None = None
    role: str | None = None
    note: str | None = None


def build_router(store: WorkspaceStore, get_llm, bridge: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/candidates")

    def ws_for(cid: str):
        try:
            return store.get(cid)
        except SchemaTooNew as e:
            raise user_error(409, "backup_too_new", str(e)) from e
        except WorkspaceError as e:
            raise HTTPException(404, str(e)) from e

    def service(cid: str) -> TailorService:
        ws = ws_for(cid)
        try:
            return TailorService(ws.load_index(), ws.load_resumes(), ws.load_profiles(), get_llm())
        except WorkspaceError as e:
            raise user_error(409, "no_experience_data", str(e)) from e

    # ------------------------------------------------------------- candidates
    @router.get("")
    def list_candidates(include_archived: bool = False) -> list[dict[str, Any]]:
        out = []
        for meta in store.list_candidates(include_archived=include_archived):
            ws = store.get(meta["id"])
            out.append(
                {
                    "id": meta["id"],
                    "name": meta["name"],
                    "archived": meta.get("archived", False),
                    "base_resumes": len(list((ws.root / "base_resumes").glob("*.json"))),
                    "applications": len(
                        [p for p in (ws.root / "applications").iterdir() if p.is_dir()]
                    )
                    if (ws.root / "applications").exists()
                    else 0,
                }
            )
        return out

    @router.post("")
    def create_candidate(body: CandidateIn) -> dict[str, Any]:
        try:
            ws = store.create(body.name, **body.model_dump(exclude={"name"}))
        except WorkspaceError as e:
            raise HTTPException(400, str(e)) from e
        return ws.meta()

    @router.post("/select")
    def select_candidate(body: dict[str, str]) -> dict[str, str]:
        cid = body.get("id", "")
        ws_for(cid)  # validates
        settings = store.app_settings()
        settings["last_selected_candidate"] = cid
        store.save_app_settings(settings)
        return {"selected": cid}

    @router.get("/{cid}")
    def candidate_detail(cid: str) -> dict[str, Any]:
        return ws_for(cid).meta()

    @router.patch("/{cid}")
    def update_candidate(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        ws = ws_for(cid)
        meta = ws.meta()
        for key in ("name", "email", "phone", "location", "linkedin", "portfolio", "languages"):
            if key in body:
                meta[key] = body[key]
        if not str(meta.get("name", "")).strip():
            raise HTTPException(400, "A candidate needs a name.")
        ws.save_meta(meta)
        return meta

    @router.post("/{cid}/archive")
    def archive_candidate(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        store.set_archived(cid, bool(body.get("archived", True)))
        return ws_for(cid).meta()

    @router.delete("/{cid}")
    def delete_candidate(cid: str, confirm_name: str = "") -> dict[str, str]:
        try:
            store.delete(cid, confirm_name)
        except WorkspaceError as e:
            raise HTTPException(400, str(e)) from e
        return {"deleted": cid}

    # ----------------------------------------------------------------- backup
    @router.get("/{cid}/backup")
    def export_backup_route(cid: str, sources: bool = True, applications: bool = True) -> Response:
        """Download this candidate as a backup ZIP. May contain personal and
        professional information - the UI warns before saving."""
        from resume_tailor.workspace.backup import backup_filename, export_backup

        ws = ws_for(cid)
        data = export_backup(ws, include_sources=sources, include_applications=applications)
        name = backup_filename(ws.meta().get("name", "Candidate"))
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    @router.post("/import")
    async def import_backup_route(
        file: UploadFile = File(...), replace_id: str = Form(""), confirm_name: str = Form("")
    ) -> dict[str, Any]:
        from resume_tailor.workspace.backup import import_backup

        data = await file.read()
        own = None
        if bridge is not None:
            # Following a profile, a backup restores INTO that profile's
            # workspace: a second candidate would be invisible here.
            from resume_tailor.integration import career as ca

            own = ca.profile_candidate(store, bridge.profile())
            if replace_id != own.id:
                raise user_error(
                    400,
                    "backup_this_profile_only",
                    "A backup can only be restored into this profile's Resume Tailor data.",
                )
        try:
            ws = import_backup(
                store, data, replace_id=replace_id or None, confirm_name=confirm_name
            )
        except SchemaTooNew as e:
            raise user_error(409, "backup_too_new", str(e)) from e
        except WorkspaceError as e:
            raise HTTPException(400, str(e)) from e
        if own is not None:
            # The backup's own profile stamp is not trusted: this workspace is
            # this profile's, whatever file it came from.
            from resume_tailor.integration.career import PROFILE_KEY

            meta = ws.meta()
            meta[PROFILE_KEY] = bridge.profile()["id"]
            ws.save_meta(meta)
        return {"id": ws.id, "name": ws.meta().get("name", "")}

    # ----------------------------------------------------------------- config
    @router.get("/{cid}/config")
    def config(cid: str) -> dict[str, Any]:
        ws = ws_for(cid)
        resumes = ws.load_resumes()
        profiles = ws.load_profiles()
        has_evidence = ws.evidence_file.exists()
        counts = {"details": 0, "conflicts": 0}
        if has_evidence:
            index = ws.load_index()
            counts = {
                "details": len(index.bank.records),
                "conflicts": len([c for c in index.bank.conflicts if not c.resolved_by]),
            }
        return {
            "candidate": ws.meta(),
            "settings": ws.settings(),
            "base_resumes": [
                {"id": r.id, "name": r.name, "headline": r.headline} for r in resumes.values()
            ],
            "focus_areas": [{"id": k, "name": v.get("name", k)} for k, v in profiles.items()],
            "experience": counts,
        }

    @router.get("/{cid}/settings")
    def get_settings(cid: str) -> dict[str, Any]:
        return ws_for(cid).settings()

    @router.patch("/{cid}/settings")
    def patch_settings(cid: str, body: dict[str, Any]) -> dict[str, Any]:
        ws = ws_for(cid)
        settings = ws.settings()
        settings.update(body)
        ws.save_settings(settings)
        return settings

    # ----------------------------------------------------------------- tailor
    @router.post("/{cid}/tailor")
    def tailor(cid: str, body: TailorIn) -> dict[str, str]:
        ws = ws_for(cid)
        if not ws.load_resumes():
            # Said before anything else: without a base resume nothing can be
            # tailored, whatever else the workspace holds.
            raise user_error(
                400,
                "no_base_resume",
                "There is no base resume yet. Create one from your Career Profile or upload one.",
            )
        svc = service(cid)
        resume_id = (
            body.resume_id or ws.settings().get("default_resume_id") or next(iter(svc.resumes), "")
        )
        if resume_id not in svc.resumes:
            raise user_error(
                400,
                "no_base_resume",
                "There is no base resume yet. Create one from your Career Profile or upload one.",
            )
        career_job: dict[str, Any] | None = None
        if body.career_job_id:
            if bridge is None:
                raise user_error(
                    400, "not_connected", "Resume Tailor is not connected to Career Agent."
                )
            try:
                career_job = bridge.job(body.career_job_id)
            except Exception as e:
                from resume_tailor.api.career import _http

                raise _http(e) from e
        req = TailorRequest(
            jd_text=body.jd_text,
            resume_id=resume_id,
            target_profile=body.target_profile,
            options=TailorOptions(**body.options) if body.options else TailorOptions(use_llm=False),
        )
        run_id = new_run_id()
        run_store = ws.run_store()
        run_store.set_status(run_id, "running", "queued")

        def work() -> None:
            try:
                run = svc.run(
                    req, run_id, progress=lambda s: run_store.set_status(run_id, "running", s)
                )
                run_store.save(run)
                _write_application_meta(ws, run_id, run, career_job)
            except Exception as e:
                run_store.set_status(
                    run_id, "error", "failed", f"{e}\n{traceback.format_exc()[-1500:]}"
                )

        threading.Thread(target=work, daemon=True).start()
        return {"application_id": run_id}

    def _write_application_meta(
        ws, run_id: str, run: TailorRun, career_job: dict[str, Any] | None = None
    ) -> None:
        meta = {
            "status": "Considering",
            "role": (career_job or {}).get("title")
            or run.job_analysis.role_title
            or run.generated_resume.headline,
            "company": (career_job or {}).get("company") or run.job_analysis.company or "",
            "created_at": datetime.now(UTC).isoformat(),
            "base_resume": run.request.resume_id,
            "note": "",
        }
        if career_job is not None:
            meta["career_job_id"] = career_job["job_id"]
        (ws.root / "applications" / run_id / "application.json").write_text(
            json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------ applications
    @router.get("/{cid}/applications")
    def applications(cid: str) -> list[dict[str, Any]]:
        ws = ws_for(cid)
        out = []
        for row in ws.run_store().list():
            run_id = row.get("run_id", "")
            meta_path = ws.root / "applications" / run_id / "application.json"
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
            resumes = ws.load_resumes()
            base = resumes.get(meta.get("base_resume", ""))
            out.append(
                {
                    "id": run_id,
                    "role": meta.get("role", ""),
                    "company": meta.get("company", ""),
                    "status": meta.get("status", "Considering"),
                    "date": meta.get("created_at", ""),
                    "base_resume": base.name if base else meta.get("base_resume", ""),
                    "match": row.get("overall_coverage"),
                    "state": row.get("status", ""),
                    "career_job_id": meta.get("career_job_id"),
                }
            )
        return out

    @router.patch("/{cid}/applications/{run_id}")
    def patch_application(cid: str, run_id: str, body: ApplicationPatch) -> dict[str, Any]:
        ws = ws_for(cid)
        meta_path = ws.root / "applications" / run_id / "application.json"
        if not meta_path.exists():
            raise user_error(404, "application_not_found", "Unknown application.")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if body.status is not None:
            if body.status not in labels.APPLICATION_STATUSES:
                raise HTTPException(400, f"Status must be one of {labels.APPLICATION_STATUSES}.")
            meta["status"] = body.status
        for key in ("company", "role", "note"):
            val = getattr(body, key)
            if val is not None:
                meta[key] = val
        meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        return meta

    @router.get("/{cid}/applications/{run_id}")
    def application_detail(cid: str, run_id: str, advanced: bool = False) -> JSONResponse:
        ws = ws_for(cid)
        run_store = ws.run_store()
        try:
            st = run_store.status(run_id)
        except json.JSONDecodeError as e:
            # a status file that stays unreadable after retries is a server problem,
            # never a client error — and never shaped like an invalid-id message
            raise HTTPException(
                500, "Could not read this application's progress. Try again in a moment."
            ) from e
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if st is None:
            raise user_error(404, "application_not_found", "Unknown application.")
        if st.get("status") != "done":
            return JSONResponse({"status": st})
        run = run_store.load(run_id)
        try:
            index = ws.load_index()
        except WorkspaceError:
            index = None
        payload: dict[str, Any] = {"status": st, "view": simple_run_view(run, index)}
        if advanced:
            payload["advanced"] = run.model_dump(mode="json")
        return JSONResponse(payload)

    @router.get("/{cid}/applications/{run_id}/export/{fmt}")
    def export(cid: str, run_id: str, fmt: str) -> Response:
        ws = ws_for(cid)
        run = ws.run_store().load(run_id)
        if run is None:
            raise user_error(404, "application_not_found", "Unknown application.")
        exp = EXPORTERS.get(fmt)
        if exp is None:
            raise HTTPException(400, f"unknown format {fmt}; choose from {sorted(EXPORTERS)}")
        # export what the user sees: the draft applied over the generated resume; with
        # evidence-only mode on, an unsupported edited line ships its validated wording instead
        from resume_tailor.workspace import drafts as dr

        doc = dr.load_doc(ws, run_id)
        state = dr.current_state(doc)
        resume = dr.export_resume(
            run, state, ws.load_index(), evidence_only=run.request.options.evidence_only_claims
        )
        try:
            body = exp.render(resume)
        except NotImplementedError as e:
            raise user_error(501, "pdf_unavailable", str(e)) from e
        name = export_filename(
            resume.candidate.name, run.job_analysis.role_title, resume.headline, exp.extension
        )
        (ws.root / "exports").mkdir(exist_ok=True)
        (ws.root / "exports" / name).write_bytes(body)
        headers = {"Content-Disposition": f'attachment; filename="{name}"'}
        if fmt == "pdf":
            # transparency: the DOCX Word measurement stays canonical; the PDF's own
            # count is reported so a renderer-metric difference is never hidden
            try:
                import io as _io

                from pypdf import PdfReader

                headers["X-Resume-Pages"] = str(len(PdfReader(_io.BytesIO(body)).pages))
            except Exception:
                pass
        return Response(content=body, media_type=exp.content_type, headers=headers)

    return router


def simple_run_view(run: TailorRun, index: Any = None) -> dict[str, Any]:
    """The product-language view of one tailoring run: no ids, enums or internals.

    With the candidate's evidence index at hand, each requirement row also carries
    ``backed_by``: the employer and wording of the experience that supports it, so
    "Why?" can answer in the user's own facts rather than in scores."""
    matches = []
    for m in run.evidence_matches.matches:
        backed_by: list[dict[str, str]] = []
        if index is not None:
            for hit in m.evidence[:3]:
                rec = index.by_id.get(hit.evidence_id)
                if rec is not None and not any(b["text"] == rec.resume_text for b in backed_by):
                    backed_by.append({"company": rec.company, "text": rec.resume_text})
        matches.append(
            {
                "requirement": m.requirement_text,
                "kind": labels.category_label(m.category.value),
                "result": labels.match_label(m.match_type.value),
                "why": labels.match_sentence(m.match_type.value, m.requirement_text),
                "backed_by": backed_by,
            }
        )
    strong = sum(1 for m in matches if m["result"] == "Strong match")
    related = sum(1 for m in matches if m["result"] == "Related experience")
    gaps = [m["requirement"] for m in matches if m["result"] == "Not evidenced"]
    res = run.generated_resume
    pm = run.page_measurement
    checks = [
        {
            "name": labels.check_label(f.code),
            "level": "warn" if f.severity in ("warning", "error") else "ok",
            "note": f.message,
        }
        for f in run.lint_report.findings
    ]
    return {
        "job": {
            "role": run.job_analysis.role_title or "This role",
            "company": run.job_analysis.company or "",
            "required": [m["requirement"] for m in matches if m["kind"] == "Required"],
            "preferred": [m["requirement"] for m in matches if m["kind"] == "Preferred"],
            "conditions": run.job_analysis.role_scope_observations,
        },
        "match": {"strong": strong, "related": related, "gaps": gaps, "rows": matches},
        "resume": {
            "headline": res.headline,
            "summary": [b.text for b in res.summary],
            "experience": [
                {"company": e.company, "title": e.title, "bullets": [b.text for b in e.bullets]}
                for e in res.experience
            ],
            "skills": [{"group": g.name, "items": g.items} for g in res.skills],
            "certifications": [f"{c.issuer}: {c.name}" for c in res.certifications],
        },
        "pages": {
            "estimate": res.estimated_pages,
            "verified": bool(pm and pm.actual_pages is not None and pm.limit_met),
            "actual": pm.actual_pages if pm else None,
            "how": (
                "Checked in Word"
                if pm and pm.source == "word"
                else "Checked in LibreOffice"
                if pm and pm.source == "libreoffice"
                else "Estimated"
            ),
        },
        "checks": checks,
        "filename": export_filename(
            res.candidate.name, run.job_analysis.role_title, res.headline, "docx"
        ),
    }

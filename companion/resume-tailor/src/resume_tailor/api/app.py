# Modified for the Career Agent public edition (2026-09-26). See NOTICE.
"""Local HTTP API + static UI. Thin: every endpoint delegates to core services.

Two route families:

* ``/api/candidates/...`` — the candidate-scoped product API (api/candidates.py).
  Every request names its candidate; there is no server-global active candidate.
* the original un-scoped routes (``/api/tailor``, ``/api/runs`` ...) — kept for
  backward compatibility, deprecated. They resolve through ONE unambiguous default
  candidate (the remembered selection, or the only candidate); with no workspace
  present they fall back to the repository ``data/`` layout so existing setups and
  the CLI keep working. They can never pick between several candidates.
"""

from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from resume_tailor.api.candidates import build_router
from resume_tailor.api.career import build_career_router
from resume_tailor.api.drafts import build_drafts_router
from resume_tailor.api.material import build_material_router
from resume_tailor.core.models import TailorRequest
from resume_tailor.core.pipeline import TailorService
from resume_tailor.export.exporters import EXPORTERS, export_filename
from resume_tailor.providers.llm.factory import build_provider
from resume_tailor.storage import paths
from resume_tailor.storage.runs import RunStore, new_run_id
from resume_tailor.workspace import WorkspaceStore

UI_DIR = Path(__file__).resolve().parents[1] / "ui" / "static"
UI_V2_DIR = Path(__file__).resolve().parents[1] / "ui" / "static_v2"


def create_app(home: Path | None = None, bridge: Any | None = None) -> FastAPI:
    """The Resume Tailor app. With `bridge` (Career Agent's, see
    integration/career.py) it follows that app's active local profile."""
    load_dotenv(paths.PACKAGE_ROOT / ".env")
    app = FastAPI(title="Resume Tailor", version="0.1.0b1")

    # Public edition: reject DNS rebinding and cross-origin requests, including forms.
    @app.middleware("http")
    async def local_boundary(request, call_next):
        from urllib.parse import urlsplit

        host = request.headers.get("host", "")
        try:
            hostname = urlsplit("http://" + host).hostname
        except ValueError:
            hostname = None
        if hostname not in (
            {"127.0.0.1", "localhost", "::1"} | ({"testserver"} if home is not None else set())
        ):
            return JSONResponse({"detail": "Local requests only"}, status_code=403)
        # A candidate-scoped call with no candidate in it is a page bug, and
        # it must never reach a route: `/api/candidates//resumes/upload` once
        # did, and nothing on screen said why the upload failed.
        path = request.url.path
        if path.startswith("/api/candidates/") and (
            "//" in path[len("/api/candidates") :]
            or path.split("/")[3] in {"", "undefined", "null"}
        ):
            return JSONResponse(
                {"detail": "No candidate is selected. Reload Resume Tailor and try again."},
                status_code=400,
            )
        # Following a Career Agent profile, a page names the profile it was
        # opened for. After a switch the server serves the NEW profile, so a
        # tab still showing the old one is refused rather than allowed to
        # read or write someone else's applications and evidence. The
        # profile's own routes need the header; /api/workspace is how a page
        # learns it.
        if bridge is not None and path.startswith("/api/") and path != "/api/workspace":
            claimed = (request.headers.get("x-local-profile") or "").strip()
            needs = path.startswith(("/api/career/", "/api/candidates"))
            if claimed or needs:
                try:
                    current = str(bridge.profile()["id"])
                except Exception:  # a retired bridge answers the same way
                    current = ""
                if not claimed or claimed != current:
                    return JSONResponse(
                        {
                            "detail": "Career Agent switched to another local profile."
                            " Reload Resume Tailor to follow it."
                        },
                        status_code=409,
                    )
        origin = request.headers.get("origin")
        if origin is not None and origin != "http://" + host:
            return JSONResponse({"detail": "Same-origin requests only"}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"detail": "Cross-site requests refused"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            content = request.headers.get("content-type", "").split(";")[0]
            if content == "multipart/form-data" and origin != "http://" + host:
                return JSONResponse(
                    {"detail": "Uploads require a same-origin browser"}, status_code=403
                )
            if content not in {"application/json", "multipart/form-data"}:
                return JSONResponse({"detail": "JSON required"}, status_code=415)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    state: dict[str, Any] = {}
    store = WorkspaceStore(home)

    def llm():
        if "llm" not in state:
            state["llm"] = build_provider(cache_dir=paths.cache_dir())
        return state["llm"]

    app.include_router(build_career_router(store, bridge))
    app.include_router(build_router(store, llm, bridge))
    app.include_router(build_material_router(store))
    app.include_router(build_drafts_router(store))

    # ------------------------------------------------- legacy (deprecated) routes
    def legacy_service() -> tuple[TailorService, RunStore]:
        """The one unambiguous legacy target: the default candidate's workspace, or the
        repository data/ layout when no workspace exists yet. Never a guess."""
        cid = store.default_candidate_id()
        if cid is not None:
            ws = store.get(cid)
            return TailorService(
                ws.load_index(), ws.load_resumes(), ws.load_profiles(), llm()
            ), ws.run_store()
        if not paths.evidence_path().exists():
            raise HTTPException(
                409,
                "Create a candidate first (or run `resume-tailor demo`), then use /api/candidates/{id}/...",
            )
        return TailorService(
            paths.load_evidence_index(), paths.load_resumes(), paths.load_profiles(), llm()
        ), RunStore(paths.runs_dir())

    @app.get("/")
    def root() -> FileResponse:
        index_v2 = UI_V2_DIR / "index.html"
        if index_v2.exists():
            return FileResponse(index_v2)
        legacy = UI_DIR / "index.html"
        if legacy.exists():
            return FileResponse(legacy)
        raise HTTPException(
            500,
            "The UI bundle is missing. Build it with: cd frontend && npm install && npm run build",
        )

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        svc, _ = legacy_service()
        info = svc.llm.info() if svc.llm else None
        return {
            "provider": info.provider if info else "none",
            "model": info.model if info else "deterministic",
            "available": info.available if info else False,
            "note": info.note if info else "",
            "resumes": [
                {"id": r.id, "name": r.name, "headline": r.headline} for r in svc.resumes.values()
            ],
            "profiles": [
                {"id": k, "name": v["name"], "titles": v.get("titles", [])}
                for k, v in svc.profiles.items()
            ],
            "evidence_records": len(svc.index.bank.records),
            "conflicts": len(svc.index.bank.conflicts),
            "tenure": svc.index.tenure_summary(),
            "overrides": [o.id for o in svc.index.bank.overrides],
            "deprecated": "use /api/candidates/{id}/...",
        }

    @app.get("/api/evidence")
    def evidence() -> dict[str, Any]:
        svc, _ = legacy_service()
        return svc.index.bank.model_dump(mode="json")

    @app.get("/api/resumes/{resume_id}")
    def resume(resume_id: str) -> dict[str, Any]:
        svc, _ = legacy_service()
        r = svc.resumes.get(resume_id)
        if not r:
            raise HTTPException(404, "unknown resume")
        return r.model_dump(mode="json")

    @app.post("/api/tailor")
    def tailor(req: TailorRequest) -> dict[str, str]:
        svc, run_store = legacy_service()
        if req.resume_id not in svc.resumes:
            raise HTTPException(400, f"unknown resume_id {req.resume_id}")
        run_id = new_run_id()
        run_store.set_status(run_id, "running", "queued")

        def work() -> None:
            try:
                run = svc.run(
                    req, run_id, progress=lambda s: run_store.set_status(run_id, "running", s)
                )
                run_store.save(run)
            except Exception as e:
                run_store.set_status(
                    run_id, "error", "failed", f"{e}\n{traceback.format_exc()[-1500:]}"
                )

        threading.Thread(target=work, daemon=True).start()
        return {"run_id": run_id}

    @app.get("/api/runs")
    def runs() -> list[dict[str, Any]]:
        _, run_store = legacy_service()
        return run_store.list()

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: str) -> JSONResponse:
        _, run_store = legacy_service()
        try:
            st = run_store.status(run_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        if st is None:
            raise HTTPException(404, "unknown run")
        if st.get("status") != "done":
            return JSONResponse({"status": st})
        run = run_store.load(run_id)
        return JSONResponse({"status": st, "run": run.model_dump(mode="json") if run else None})

    @app.get("/api/runs/{run_id}/export/{fmt}")
    def export(run_id: str, fmt: str) -> Response:
        _, run_store = legacy_service()
        run = run_store.load(run_id)
        if run is None:
            raise HTTPException(404, "unknown run")
        exp = EXPORTERS.get(fmt)
        if exp is None:
            raise HTTPException(400, f"unknown format {fmt}; choose from {sorted(EXPORTERS)}")
        try:
            body = exp.render(run.generated_resume)
        except NotImplementedError as e:
            raise HTTPException(501, str(e)) from e
        name = export_filename(
            run.generated_resume.candidate.name,
            run.job_analysis.role_title,
            run.generated_resume.headline,
            exp.extension,
        )
        return Response(
            content=body,
            media_type=exp.content_type,
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )

    if UI_DIR.exists():  # the legacy UI; absent in the public distribution
        app.mount("/static", StaticFiles(directory=str(UI_DIR)), name="static")
    if UI_V2_DIR.exists():
        # the V2 single-page app: its hashed bundles, local fonts and pixel icons
        app.mount("/assets", StaticFiles(directory=str(UI_V2_DIR / "assets")), name="v2-assets")
        if (UI_V2_DIR / "fonts").exists():
            app.mount("/fonts", StaticFiles(directory=str(UI_V2_DIR / "fonts")), name="v2-fonts")
        app.mount("/app", StaticFiles(directory=str(UI_V2_DIR), html=True), name="app")
    return app


app = create_app()

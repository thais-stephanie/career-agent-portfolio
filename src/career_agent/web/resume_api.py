"""Resume Workspace routes: render for the preview, read and edit documents.

`POST /api/resume/render` validates a whole ResumeDocument, renders it with
the one renderer and keeps the HTML in memory for a short while under a
random token; it writes nothing to disk and stores nothing in the database.
`GET /api/resume/preview/<token>` serves that HTML as its own document, under
a policy that allows no script, no network and no form, for the preview
frame. There is no route that renders arbitrary HTML or text.

Editing writes the WORKING COPY (`PATCH .../working`, with the hash the page
last read; a stale page gets 409 and overwrites nothing). A milestone is an
explicit `POST .../checkpoint`. Nothing here writes Career Evidence, search
settings or scores: a resume is resume material.

Every write and every export asks again whether each cited evidence id is a
confirmed, current claim of THIS profile (`resume_doc.evidence`); the
browser's word is never taken for it.

An export (`POST .../exports`) is made from the revision of exactly what the
page has saved; the file stays in the profile's private folder and is
downloaded through `GET /api/resume/exports/<id>/file`, which only ever
serves an export of this profile. The browser never names a path.
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from typing import Any

from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc.check import findings
from career_agent.resume_doc.export import (
    CONTENT_TYPES,
    ExportFailed,
    ExportRefused,
    export_revision,
    filename,
    stored_file,
)
from career_agent.resume_doc.models import (
    SCHEMA_VERSION,
    ResumeDocument,
    UnsupportedSchemaVersion,
    upgrade_resume_document,
)
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import (
    EvidenceNotConfirmed,
    NotFound,
    ResumeExport,
    ResumeStore,
    ResumeStoreError,
    StaleDocument,
    StoredDocument,
)
from career_agent.web.server import ApiError, Download, InlinePage, LocalApp, closing

#: Renders kept for the preview frame: the last few, in memory only.
KEEP_RENDERS = 8


class _Renders:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: OrderedDict[str, tuple[str | None, str]] = OrderedDict()

    def put(self, profile: str | None, html: str) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._held[token] = (profile, html)
            while len(self._held) > KEEP_RENDERS:
                self._held.popitem(last=False)
        return token

    def get(self, token: str, profile: str | None) -> str | None:
        with self._lock:
            held = self._held.get(token)
        return held[1] if held is not None and held[0] == profile else None


def _summary(doc: StoredDocument, last: ResumeExport | None = None) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kind": doc.kind.value,
        "title": doc.title,
        "version_number": doc.version_number,
        "preferred": doc.preferred,
        "updated_at": doc.updated_at,
        "last_export": _export(last) if last else None,
    }


def _export(e: ResumeExport) -> dict[str, Any]:
    """What the page may know of an export: never its path on disk."""
    return {
        "id": e.id,
        "format": e.format,
        "template": e.template,
        "page_count": e.page_count,
        "engine": e.engine,
        "created_at": e.created_at,
        "checks": e.ats_check.get("checks", []),
        "verified": bool(e.ats_check.get("verified")),
        "download": f"/api/resume/exports/{e.id}/file",
    }


def _unconfirmed(lines: list[str]) -> ApiError:
    return ApiError(
        400,
        "Some lines cite Career Evidence that is not confirmed now.",
        for_reader=True,
        code="evidence_not_confirmed",
        data={"lines": lines},
    )


def _validated(value: Any) -> ResumeDocument:
    if not isinstance(value, dict):
        raise ApiError(400, "Send the resume document.")
    try:
        return upgrade_resume_document(value)
    except (ValidationError, UnsupportedSchemaVersion) as exc:
        raise ApiError(
            400, "Some fields of this resume are not valid yet.", for_reader=True
        ) from exc


def _detail(doc: StoredDocument) -> dict[str, Any]:
    return {
        **_summary(doc),
        "sha256": doc.working_sha256,
        "document": doc.working.model_dump(mode="json"),
    }


#: The milestones a person can ask for. The others (GENERATED, IMPORTED...)
#: are written by the code that does those things.
ASKED_CHECKPOINTS = ("MANUAL_CHECKPOINT", "TEMPLATE_CHANGED")


def register_resume_routes(app: LocalApp) -> None:
    renders = _Renders()

    def profile() -> str | None:
        active = getattr(getattr(app, "profile_host", None), "active", None)
        return getattr(active, "id", None)

    def render(*, query: dict, body: dict) -> dict:
        """Render a document for the preview. Nothing is saved."""
        if set(body) != {"document"}:
            raise ApiError(400, "Send the resume document to render.")
        doc = _validated(body["document"])
        rendered = render_html(doc, mode="preview")
        token = renders.put(profile(), rendered.html)
        return {
            "url": f"/api/resume/preview/{token}",
            "page": rendered.page,
            "findings": findings(doc),
        }

    def preview(*, query: dict, body: dict, token: str) -> InlinePage:
        html = renders.get(token, profile())
        if html is None:
            raise ApiError(404, "This preview has expired.")
        return InlinePage(html.encode("utf-8"))

    def documents(*, query: dict, body: dict) -> list[dict[str, Any]]:
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            return [
                _summary(d, next(reversed(store.list_exports(d.id)), None))
                for d in store.list_documents()
            ]

    def document(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        with closing(app.connect()) as conn:
            try:
                return _detail(ResumeStore(conn).get_document(document_id))
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc

    def create(*, query: dict, body: dict) -> dict[str, Any]:
        """A new SCRATCH document: blank, or a copy of the document sent
        (`from`), which keeps that content under a new id. A copy is never a
        tailored version and never a Master: those have their own ways in."""
        if set(body) - {"title", "language", "from"}:
            raise ApiError(400, "Unknown field for a new resume.")
        if "from" in body:
            source = _validated(body["from"]).model_dump(mode="json")
            data = {
                **source,
                "id": new_id(),
                "kind": "SCRATCH",
                "target": None,
                "title": f"{source['title']} (copy)"[:300],
                "provenance": {"created_from": "DUPLICATE"},
            }
        else:
            data = {
                "schema_version": SCHEMA_VERSION,
                "id": new_id(),
                "kind": "SCRATCH",
                "title": str(body.get("title") or "New resume")[:300],
                "language": str(body.get("language") or "en"),
                "provenance": {"created_from": "SCRATCH"},
            }
        doc = _validated(data)
        with closing(app.connect()) as conn:
            try:
                return _detail(ResumeStore(conn).create_document(doc))
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc

    def save(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Autosave: the working copy, against the hash the page last read."""
        if set(body) != {"document", "expected_sha256"}:
            raise ApiError(400, "Send the resume and the version it edits.")
        doc = _validated(body["document"])
        with closing(app.connect()) as conn:
            try:
                sha = ResumeStore(conn).save_working_copy(
                    document_id, doc, expected_sha256=str(body["expected_sha256"])
                )
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc
            except StaleDocument as exc:
                raise ApiError(
                    409, "This resume changed in another window.", for_reader=True
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except ResumeStoreError as exc:
                raise ApiError(400, str(exc)) from exc
        return {"sha256": sha}

    def checkpoint(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """A milestone: the working copy as it is now, added to the history."""
        reason = body.get("reason")
        if set(body) != {"reason"} or reason not in ASKED_CHECKPOINTS:
            raise ApiError(400, "Unknown kind of version point.")
        with closing(app.connect()) as conn:
            try:
                revision = ResumeStore(conn).checkpoint_revision(document_id, reason)
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
        return {"revision": revision.seq, "reason": revision.reason}

    def export(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Make a file of exactly what the page has saved, and check it."""
        allowed = {"format", "expected_sha256", "preview_pages", "preview_overflow", "page_breaks"}
        fmt, pages = body.get("format"), body.get("preview_pages")
        overflow = body.get("preview_overflow") or []
        breaks = body.get("page_breaks") or []
        if (
            set(body) - allowed
            or fmt not in ("PDF", "DOCX", "JSON")
            or not isinstance(body.get("expected_sha256"), str)
            or not (pages is None or (type(pages) is int and pages > 0))
            or not all(
                isinstance(refs, list)
                and len(refs) <= 500
                and all(isinstance(r, str) and len(r) <= 200 for r in refs)
                for refs in (overflow, breaks)
            )
        ):
            raise ApiError(400, "Send the format and the version the page has saved.")
        with closing(app.connect()) as conn:
            try:
                made = export_revision(
                    conn,
                    document_id,
                    fmt,
                    expected_sha256=body["expected_sha256"],
                    preview_pages=pages,
                    preview_overflow=overflow[:50],
                    page_breaks=breaks,
                )
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc
            except StaleDocument as exc:
                raise ApiError(
                    409, "This resume changed in another window.", for_reader=True
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except ExportRefused as exc:
                raise ApiError(
                    400, "Add your name before downloading.", for_reader=True, code="name_missing"
                ) from exc
            except ExportFailed as exc:
                if str(exc) == "NO_BROWSER":
                    message = "A PDF needs Microsoft Edge or Chrome, and neither was found."
                else:
                    message = "The PDF could not be made. Nothing was saved; try again."
                raise ApiError(503, message, for_reader=True, code="export_failed") from exc
        return _export(made)

    def exports(*, query: dict, body: dict, document_id: str) -> list[dict[str, Any]]:
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            try:
                store.get_document(document_id)
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc
            return [_export(e) for e in reversed(store.list_exports(document_id))]

    def export_file(*, query: dict, body: dict, export_id: str) -> Download:
        """An export of THIS profile, by its id; the server finds the file."""
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            try:
                made = store.get_export(export_id)
                path = stored_file(conn, made)
            except (NotFound, FileNotFoundError) as exc:
                raise ApiError(404, "This file is no longer here.", for_reader=True) from exc
            doc = store.get_revision(made.revision_id).content
            return Download(
                path.read_bytes(), CONTENT_TYPES[made.format], filename(doc, made.format)
            )

    app.register("POST", r"/api/resume/render", render)
    app.register("GET", r"/api/resume/preview/(?P<token>[A-Za-z0-9_-]{16,64})", preview)
    one = r"/api/resume/documents/(?P<document_id>[0-9A-HJKMNP-TV-Z]{26})"
    app.register("GET", r"/api/resume/documents", documents)
    app.register("POST", r"/api/resume/documents", create)
    app.register("GET", one, document)
    app.register("PATCH", one + "/working", save)
    app.register("POST", one + "/checkpoint", checkpoint)
    app.register("POST", one + "/exports", export)
    app.register("GET", one + "/exports", exports)
    export_one = r"/api/resume/exports/(?P<export_id>[0-9A-HJKMNP-TV-Z]{26})/file"
    app.register("GET", export_one, export_file)

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
"""

from __future__ import annotations

import secrets
import threading
from collections import OrderedDict
from typing import Any

from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc.check import findings
from career_agent.resume_doc.models import (
    SCHEMA_VERSION,
    ResumeDocument,
    UnsupportedSchemaVersion,
    upgrade_resume_document,
)
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import (
    NotFound,
    ResumeStore,
    ResumeStoreError,
    StaleDocument,
    StoredDocument,
)
from career_agent.web.server import ApiError, InlinePage, LocalApp, closing

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


def _summary(doc: StoredDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kind": doc.kind.value,
        "title": doc.title,
        "version_number": doc.version_number,
        "preferred": doc.preferred,
        "updated_at": doc.updated_at,
    }


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
            return [_summary(d) for d in ResumeStore(conn).list_documents()]

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
            return _detail(ResumeStore(conn).create_document(doc))

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
        return {"revision": revision.seq, "reason": revision.reason}

    app.register("POST", r"/api/resume/render", render)
    app.register("GET", r"/api/resume/preview/(?P<token>[A-Za-z0-9_-]{16,64})", preview)
    one = r"/api/resume/documents/(?P<document_id>[0-9A-HJKMNP-TV-Z]{26})"
    app.register("GET", r"/api/resume/documents", documents)
    app.register("POST", r"/api/resume/documents", create)
    app.register("GET", one, document)
    app.register("PATCH", one + "/working", save)
    app.register("POST", one + "/checkpoint", checkpoint)

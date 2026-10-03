"""Resume Workspace routes: render a document for the preview, read documents.

`POST /api/resume/render` validates a whole ResumeDocument, renders it with
the one renderer and keeps the HTML in memory for a short while under a
random token; it writes nothing to disk and stores nothing in the database.
`GET /api/resume/preview/<token>` serves that HTML as its own document, under
a policy that allows no script, no network and no form, for the preview
frame. There is no route that renders arbitrary HTML or text.
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from typing import Any

from pydantic import ValidationError

from career_agent.resume_doc.models import UnsupportedSchemaVersion, upgrade_resume_document
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import NotFound, ResumeStore, StoredDocument
from career_agent.web.server import ApiError, InlinePage, LocalApp, closing

#: Renders kept for the preview frame: a few, briefly, in memory only.
KEEP_RENDERS = 8
KEEP_SECONDS = 600


class _Renders:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: OrderedDict[str, tuple[float, str | None, str]] = OrderedDict()

    def put(self, profile: str | None, html: str) -> str:
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._held[token] = (time.monotonic(), profile, html)
            while len(self._held) > KEEP_RENDERS:
                self._held.popitem(last=False)
        return token

    def get(self, token: str, profile: str | None) -> str | None:
        with self._lock:
            held = self._held.get(token)
        if held is None or time.monotonic() - held[0] > KEEP_SECONDS or held[1] != profile:
            return None
        return held[2]


def _summary(doc: StoredDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kind": doc.kind.value,
        "title": doc.title,
        "version_number": doc.version_number,
        "preferred": doc.preferred,
        "updated_at": doc.updated_at,
    }


def register_resume_routes(app: LocalApp) -> None:
    renders = _Renders()

    def profile() -> str | None:
        active = getattr(getattr(app, "profile_host", None), "active", None)
        return getattr(active, "id", None)

    def render(*, query: dict, body: dict) -> dict:
        """Render a document for the preview. Nothing is saved."""
        if set(body) != {"document"} or not isinstance(body["document"], dict):
            raise ApiError(400, "Send the resume document to render.")
        try:
            doc = upgrade_resume_document(body["document"])
        except (ValidationError, UnsupportedSchemaVersion) as exc:
            raise ApiError(400, "This resume cannot be shown: check its fields.") from exc
        rendered = render_html(doc, mode="preview")
        token = renders.put(profile(), rendered.html)
        return {"url": f"/api/resume/preview/{token}", "page": rendered.page}

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
                doc = ResumeStore(conn).get_document(document_id)
            except NotFound as exc:
                raise ApiError(404, "No such resume.") from exc
        return {
            **_summary(doc),
            "sha256": doc.working_sha256,
            "document": doc.working.model_dump(mode="json"),
        }

    app.register("POST", r"/api/resume/render", render)
    app.register("GET", r"/api/resume/preview/(?P<token>[A-Za-z0-9_-]{16,64})", preview)
    app.register("GET", r"/api/resume/documents", documents)
    app.register("GET", r"/api/resume/documents/(?P<document_id>[0-9A-HJKMNP-TV-Z]{26})", document)

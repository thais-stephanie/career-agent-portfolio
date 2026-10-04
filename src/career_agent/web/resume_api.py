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

import base64
import binascii
import json
import secrets
import threading
from collections import OrderedDict
from pathlib import Path
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
from career_agent.resume_doc.jd import MAX_AD
from career_agent.resume_doc.models import (
    SCHEMA_VERSION,
    DocumentKind,
    ResumeDocument,
    UnsupportedSchemaVersion,
    upgrade_resume_document,
)
from career_agent.resume_doc.render import render_html
from career_agent.resume_doc.store import (
    EvidenceNotConfirmed,
    MasterInPlace,
    NotFound,
    ResumeExport,
    ResumeStore,
    ResumeStoreError,
    StaleDocument,
    StoredDocument,
)
from career_agent.resume_doc.tailor import (
    TailorFailed,
    apply_suggestion,
    explain,
    job_ad,
    tailor,
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


def _summary(doc: StoredDocument) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kind": doc.kind.value,
        "title": doc.title,
        "version_number": doc.version_number,
        "preferred": doc.preferred,
        "archived": doc.archived_at is not None,
        "updated_at": doc.updated_at,
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


def _listed(row: dict[str, Any]) -> dict[str, Any]:
    """One row of My resumes, from `ResumeStore.summaries`: no document body."""
    checks = json.loads(row["ats_check_json"]) if row["export_id"] else {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "version_number": row["version_number"],
        "preferred": bool(row["preferred"]),
        "archived": row["archived_at"] is not None,
        "updated_at": row["updated_at"],
        "template": row["template"],
        "tailored": row["created_from"] == "TAILOR",
        "last_export": {
            "id": row["export_id"],
            "format": row["export_format"],
            "page_count": row["page_count"],
            "created_at": row["exported_at"],
            "checks": checks.get("checks", []),
            "verified": bool(checks.get("verified")),
        }
        if row["export_id"]
        else None,
    }


def _library(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Grouped as the person thinks of them: the Master, standalone resumes,
    and each job's versions newest first (the store's group and numbers)."""
    groups: dict[str, dict[str, Any]] = {}
    out: dict[str, Any] = {"master": None, "others": [], "jobs": []}
    for row in rows:
        item = _listed(row)
        if row["version_group"]:
            group = groups.setdefault(
                row["version_group"],
                {
                    "key": row["version_group"],
                    "job_id": row["job_id"],
                    "title": row["job_title"],
                    "company": row["job_company"],
                    "versions": [],
                },
            )
            group["versions"].append(item)
        elif row["kind"] == "MASTER" and out["master"] is None and row["archived_at"] is None:
            out["master"] = item
        else:
            out["others"].append(item)
    for group in groups.values():
        group["versions"].sort(key=lambda v: -v["version_number"])
    out["jobs"] = list(groups.values())
    return out


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


#: What a refused upload is, for the page to word (`ImportRefused.code`).
_REFUSED_STATUS = {"TOO_LARGE": 413, "NO_TEXT": 422}


def _read_import(body: dict) -> dict[str, Any]:
    """Read an uploaded PDF or DOCX into a proposal for review. Stores nothing:
    the bytes are parsed in memory and dropped; the proposal goes to the page."""
    from career_agent.cv.extract import safe_name
    from career_agent.resume_doc.intake import ImportRefused, read_upload
    from career_agent.resume_doc.parse import parse

    name, raw = body.get("filename"), body.get("content_base64")
    if set(body) != {"filename", "content_base64"} or not isinstance(name, str):
        raise ApiError(400, "Send the file name and its contents.")
    if not isinstance(raw, str) or not raw:
        raise ApiError(400, "Send the file name and its contents.")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ApiError(400, "The file did not arrive whole.") from exc
    filename = safe_name(name)
    try:
        return parse(read_upload(data, filename), filename).model_dump(mode="json")
    except ImportRefused as exc:
        raise ApiError(
            _REFUSED_STATUS.get(exc.code, 400),
            "This file could not be read.",
            for_reader=True,
            code="import_refused",
            data={"reason": exc.code},
        ) from exc


#: What `PATCH /api/resume/documents/<id>` may do, one per call.
MANAGE_ACTIONS = ("rename", "archive", "unarchive", "prefer", "make_master")

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

    def documents(*, query: dict, body: dict) -> dict[str, Any]:
        """My resumes, grouped; `?archived=1` for the archived ones instead."""
        if set(query) - {"archived"}:
            raise ApiError(400, "Unknown resume list filter.")
        archived = query.get("archived", ["0"])[0] == "1"
        with closing(app.connect()) as conn:
            return _library(ResumeStore(conn).summaries(archived=archived))

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
            listed = []
            for e in reversed(store.list_exports(document_id)):
                try:
                    available = bool(stored_file(conn, e))
                except FileNotFoundError:
                    available = False  # deleted, or never in this profile's folder
                listed.append({**_export(e), "available": available})
            return listed

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

    def import_read(*, query: dict, body: dict) -> dict[str, Any]:
        return _read_import(body)

    def import_save(*, query: dict, body: dict) -> dict[str, Any]:
        """Save a REVIEWED import. Only the values are trusted: the document
        is built here, every line IMPORTED, and validated like any write."""
        from career_agent.resume_doc.imports import (
            ImportConflict,
            ImportIncomplete,
            ImportProposal,
            MasterExists,
            save_import,
        )

        destination = body.get("destination")
        if set(body) != {"proposal", "destination"} or destination not in (
            "IMPORTED",
            "MASTER",
            "REPLACE_MASTER",
        ):
            raise ApiError(400, "Send the reviewed import and where to save it.")
        try:
            proposal = ImportProposal.model_validate(body["proposal"])
        except ValidationError as exc:
            raise ApiError(400, "Some fields are not valid yet.", for_reader=True) from exc
        with closing(app.connect()) as conn:
            try:
                return _detail(save_import(conn, proposal, destination))
            except ImportIncomplete as exc:
                raise ApiError(
                    400,
                    "Some fields need a value before saving.",
                    for_reader=True,
                    code="import_incomplete",
                    data={"fields": exc.fields},
                ) from exc
            except MasterExists as exc:
                raise ApiError(
                    409, "You already have a Master resume.", for_reader=True, code="master_exists"
                ) from exc
            except ImportConflict as exc:
                raise ApiError(
                    409, "This import was already saved differently.", code="import_conflict"
                ) from exc

    def _one(conn: Any, document_id: str) -> StoredDocument:
        try:
            return ResumeStore(conn).get_document(document_id)
        except NotFound as exc:
            raise ApiError(404, "No such resume.") from exc

    def manage(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Rename, archive, bring back, prefer, or make the Master: one act per call."""
        action = body.get("action")
        allowed = {"action", "title"} if action == "rename" else {"action"}
        if set(body) != allowed or action not in MANAGE_ACTIONS:
            raise ApiError(400, "Unknown change to this resume.")
        with closing(app.connect()) as conn:
            store, doc = ResumeStore(conn), _one(conn, document_id)
            try:
                if action == "rename":
                    title = " ".join(str(body.get("title") or "").split())
                    if not title or len(title) > 300:
                        raise ApiError(400, "Give the resume a name.", for_reader=True)
                    doc = store.rename(document_id, title)
                elif action == "archive":
                    if doc.kind is DocumentKind.MASTER and doc.archived_at is None:
                        raise ApiError(
                            409,
                            "Your Master stays: make another resume the Master first.",
                            for_reader=True,
                            code="master_kept",
                        )
                    store.archive_document(document_id)
                elif action == "unarchive":
                    store.unarchive(document_id)
                elif action == "prefer":
                    store.set_preferred(document_id)
                else:
                    doc = store.make_master(document_id)
            except MasterInPlace as exc:
                raise ApiError(
                    409, "You already have a Master resume.", for_reader=True, code="master_exists"
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except StaleDocument as exc:
                raise ApiError(
                    409, "This resume changed in another window.", for_reader=True
                ) from exc
            except ResumeStoreError as exc:
                raise ApiError(409, str(exc)) from exc
            return _summary(store.get_document(doc.id))

    def copy(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Duplicate a resume; for a job's version, the next version of that job."""
        title = body.get("title", "")
        if set(body) - {"title"} or not isinstance(title, str) or len(title) > 300:
            raise ApiError(400, "Unknown field for a copy.")
        with closing(app.connect()) as conn:
            _one(conn, document_id)
            try:
                made = ResumeStore(conn).copy_document(document_id, title=title.strip() or None)
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            return _detail(made)

    def history(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """The milestones of a resume, newest first; no content, no autosaves."""
        with closing(app.connect()) as conn:
            doc = _one(conn, document_id)
            log = ResumeStore(conn).revision_log(document_id)
        # The newest revision holding what is on screen is the current one.
        current = next((r["id"] for r in log if r["content_sha256"] == doc.working_sha256), None)
        return {
            "sha256": doc.working_sha256,
            "revisions": [
                {
                    "id": r["id"],
                    "seq": r["seq"],
                    "reason": r["reason"],
                    "created_at": r["created_at"],
                    "current": r["id"] == current,
                }
                for r in log
            ],
        }

    def restore(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """An earlier milestone back as the working copy, written as a NEW
        revision. Evidence it cites is not trusted again: what is no longer
        confirmed is named, for the person to decide."""
        from career_agent.resume_doc.evidence import unconfirmed_lines

        if set(body) != {"revision_id", "expected_sha256"}:
            raise ApiError(400, "Send the version to restore and the one it replaces.")
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            _one(conn, document_id)
            try:
                store.restore_revision(
                    document_id,
                    str(body["revision_id"]),
                    expected_sha256=str(body["expected_sha256"]),
                )
            except NotFound as exc:
                raise ApiError(404, "No such version of this resume.") from exc
            except StaleDocument as exc:
                raise ApiError(
                    409, "This resume changed in another window.", for_reader=True
                ) from exc
            except ResumeStoreError as exc:
                raise ApiError(400, str(exc)) from exc
            doc = store.get_document(document_id)
            return {**_detail(doc), "unconfirmed": unconfirmed_lines(conn, doc.working)}

    def compare_versions(*, query: dict, body: dict) -> dict[str, Any]:
        """Two versions of the SAME job, side by side in words. Reads only."""
        from career_agent.resume_doc.compare import compare

        if set(query) != {"a", "b"}:
            raise ApiError(400, "Choose two versions to compare.")
        a, b = query["a"][0], query["b"][0]
        if a == b:
            raise ApiError(400, "Choose two versions to compare.")
        with closing(app.connect()) as conn:
            first, second = _one(conn, a), _one(conn, b)
        if first.version_group is None or first.version_group != second.version_group:
            raise ApiError(400, "Only versions for the same job are compared.", for_reader=True)
        return {
            "a": _summary(first),
            "b": _summary(second),
            "changes": compare(first.working, second.working),
        }

    def job_resumes(*, query: dict, body: dict, job_id: str) -> dict[str, Any]:
        """This job's resume versions (newest first) and the one marked as used."""
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            rows = store.summaries(version_group=f"job:{job_id}")
            return {
                "versions": sorted((_listed(r) for r in rows), key=lambda v: -v["version_number"]),
                "used": store.used_for(job_id),
                "has_master": store.current_master() is not None,
            }

    def job_version(*, query: dict, body: dict, job_id: str) -> dict[str, Any]:
        """A version for this job, made by hand from the Master (or from
        another version of it, `from`): the ad is kept as it is now and
        nothing is rewritten, chosen or added."""
        if set(body) - {"from"}:
            raise ApiError(400, "Unknown field for a job version.")
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            try:
                if "from" in body:
                    source = _one(conn, str(body["from"]))
                    if source.version_group != f"job:{job_id}":
                        raise ApiError(400, "That resume is not a version for this job.")
                    return _detail(store.copy_document(source.id))
                ad = job_ad(conn, job_id)
                if ad is None:
                    raise ApiError(404, "This job is not in this profile.")
                return _detail(store.version_from_master(**ad))
            except NotFound as exc:
                raise ApiError(
                    409, "Make your Master resume first.", for_reader=True, code="no_master"
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc

    def run_tailor(ad: dict[str, Any]) -> dict[str, Any]:
        """Tailor from the Master for one ad; nothing is saved unless every check passes."""
        with closing(app.connect()) as conn:
            try:
                stored, _ = tailor(conn, ad=ad)
            except NotFound as exc:
                raise ApiError(
                    409, "Make your Master resume first.", for_reader=True, code="no_master"
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except TailorFailed as exc:
                raise ApiError(
                    422,
                    "This version could not be built safely, so nothing was saved.",
                    for_reader=True,
                    code="tailor_failed",
                    data={"checks": sorted({f["check"] for f in exc.findings})},
                ) from exc
            return _detail(stored)

    def job_tailor(*, query: dict, body: dict, job_id: str) -> dict[str, Any]:
        """Tailor from the Master for a Career Agent job, from a snapshot of its ad."""
        if body:
            raise ApiError(400, "Nothing is sent to tailor for a job.")
        with closing(app.connect()) as conn:
            ad = job_ad(conn, job_id)
        if ad is None:
            raise ApiError(404, "This job is not in this profile.")
        return run_tailor(ad)

    def pasted_tailor(*, query: dict, body: dict) -> dict[str, Any]:
        """Tailor from the Master for an ad the person pasted."""
        title, company, text = (body.get(k) for k in ("title", "company", "text"))
        if (
            set(body) - {"title", "company", "text"}
            or not isinstance(title, str)
            or not title.strip()
            or len(title) > 300
            or not isinstance(text, str)
            or len(text) > MAX_AD
            or len(text.strip()) < 20
            or not (company is None or (isinstance(company, str) and len(company) <= 200))
        ):
            raise ApiError(400, "Give the job's title and paste its ad.", for_reader=True)
        return run_tailor(
            {"title": title.strip(), "company": (company or "").strip() or None, "text": text}
        )

    def job_view(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """A job version against its ad: coverage, why it changed, gaps, suggestions."""
        with closing(app.connect()) as conn:
            return explain(conn, _one(conn, document_id))

    def dismiss(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Set one suggestion aside for THIS version only."""
        key = body.get("key")
        if set(body) != {"key"} or not isinstance(key, str) or not 1 <= len(key) <= 120:
            raise ApiError(400, "Say which suggestion to set aside.")
        with closing(app.connect()) as conn:
            suggestions = explain(conn, _one(conn, document_id)).get("suggestions", [])
            if key not in {s["key"] for s in suggestions}:
                raise ApiError(404, "No such suggestion for this resume.")
            ResumeStore(conn).dismiss_finding(document_id, key)
        return {"dismissed": key}

    def accept(*, query: dict, body: dict, document_id: str) -> dict[str, Any]:
        """Make one suggestion's change on the server, from the confirmed text now."""
        key, sha = body.get("key"), body.get("expected_sha256")
        if set(body) != {"key", "expected_sha256"} or not isinstance(key, str):
            raise ApiError(400, "Say which suggestion to apply.")
        with closing(app.connect()) as conn:
            _one(conn, document_id)
            try:
                return _detail(apply_suggestion(conn, document_id, key, expected_sha256=str(sha)))
            except StaleDocument as exc:
                raise ApiError(
                    409, "This resume changed in another window.", for_reader=True
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except ResumeStoreError as exc:
                raise ApiError(
                    409, "This suggestion no longer applies.", for_reader=True, code="stale"
                ) from exc

    def job_used(*, query: dict, body: dict, job_id: str) -> dict[str, Any]:
        """The person says which resume they used for this job, or none."""
        if set(body) != {"document_id"}:
            raise ApiError(400, "Say which resume you used.")
        with closing(app.connect()) as conn:
            store = ResumeStore(conn)
            if conn.execute("SELECT 1 FROM job WHERE id = ?", (job_id,)).fetchone() is None:
                raise ApiError(404, "This job is not in this profile.")
            if body["document_id"] is None:
                store.clear_used(job_id)
            else:
                _one(conn, str(body["document_id"]))
                try:
                    store.mark_used(job_id, str(body["document_id"]))
                except EvidenceNotConfirmed as exc:
                    raise _unconfirmed(exc.lines) from exc
            return {"used": store.used_for(job_id)}

    # -- the old Resume helper's resumes -------------------------------------

    def labels() -> list[str]:
        label = getattr(getattr(getattr(app, "profile_host", None), "active", None), "label", None)
        return [str(label)] if label else []

    def legacy_root() -> Any:
        """This profile's old workspace, or None (no launcher, so no profile)."""
        from career_agent.resume_doc.legacy import find_workspace

        host = getattr(app, "profile_host", None)
        active = getattr(host, "active", None)
        if host is None or active is None:
            return None
        return find_workspace(Path(host.root) / active.tailor_home, active.id)

    def legacy(*, query: dict, body: dict) -> dict[str, Any]:
        """Whether this profile has resumes in the old Resume helper, and what
        moving them would move. Reads only."""
        from career_agent.resume_doc.legacy import preflight

        root = legacy_root()
        if root is None:
            return {"state": "NONE"}
        with closing(app.connect()) as conn:
            return preflight(conn, root)

    def legacy_migrate(*, query: dict, body: dict) -> dict[str, Any]:
        """Back up the old workspace (verified), then move it. Without a
        verified backup nothing is moved; the old files are never changed."""
        from career_agent.resume_doc.legacy import (
            LegacyMigrationError,
            backup_legacy_workspace,
            migrate_legacy_workspace,
        )
        from career_agent.resume_doc.store import legacy_backup_dir

        if body:
            raise ApiError(400, "Nothing is sent to move the old resumes.")
        root = legacy_root()
        if root is None:
            raise ApiError(404, "There are no old resumes to move.", for_reader=True)
        with closing(app.connect()) as conn:
            try:
                backup = backup_legacy_workspace(root, legacy_backup_dir(conn))
            except (OSError, ValueError, LegacyMigrationError) as exc:
                raise ApiError(
                    409,
                    "The backup could not be made and checked, so nothing was moved.",
                    for_reader=True,
                    code="backup_failed",
                ) from exc
            try:
                report = migrate_legacy_workspace(conn, backup, labels=labels())
            except LegacyMigrationError as exc:
                raise ApiError(
                    409,
                    "These resumes could not be moved.",
                    for_reader=True,
                    code="migration_refused",
                ) from exc
            marks = ",".join("?" * len(report.created)) or "NULL"
            kinds = [
                r[0]
                for r in conn.execute(
                    f"SELECT kind FROM resume_document WHERE id IN ({marks})", report.created
                )
            ]
        return {
            "masters": kinds.count("MASTER"),
            "imported": kinds.count("IMPORTED"),
            "job_versions": kinds.count("TAILORED"),
            "exports": len(report.created) - len(kinds),
            "already": len(report.already),
            # How many units stayed behind; never their legacy names or ids.
            "failed": len(report.failures),
        }

    app.register("POST", r"/api/resume/import/read", import_read)
    app.register("POST", r"/api/resume/import/save", import_save)
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
    app.register("PATCH", one, manage)
    app.register("POST", one + "/copy", copy)
    app.register("GET", one + "/history", history)
    app.register("POST", one + "/restore", restore)
    app.register("GET", r"/api/resume/compare", compare_versions)
    job = r"/api/resume/jobs/(?P<job_id>[A-Za-z0-9_-]{1,64})"
    app.register("GET", job, job_resumes)
    app.register("POST", job + "/versions", job_version)
    app.register("POST", job + "/used", job_used)
    app.register("POST", job + "/tailor", job_tailor)
    app.register("POST", r"/api/resume/tailor", pasted_tailor)
    app.register("GET", one + "/job", job_view)
    app.register("POST", one + "/dismissals", dismiss)
    app.register("POST", one + "/accept", accept)
    app.register("GET", r"/api/resume/legacy", legacy)
    app.register("POST", r"/api/resume/legacy/migrate", legacy_migrate)

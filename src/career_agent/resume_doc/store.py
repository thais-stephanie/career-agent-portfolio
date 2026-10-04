"""The only code that reads or writes the Resume Workspace tables (migration 0047).

The store is bound to ONE connection, which is one profile's database: resume
rows never go anywhere shared, so a store cannot see another profile's
documents by construction.

Working copy and revisions are two acts. `save_working_copy` is the cheap
autosave and needs the hash the caller last read (`expected_sha256`): a
second window holding an older copy gets `StaleDocument`, never a silent
overwrite. `checkpoint_revision` appends a milestone; history rows are never
updated or deleted (triggers refuse both), and restoring an old revision
appends a new one rather than rewinding.

A tailored document is a separate row that copies from a named master
revision. Its target and provenance are fixed when it is created, and nothing
here writes a master as a side effect of tailoring.

Every document is validated again on the way in, so a model built without
validation (`model_copy`, `model_construct`) cannot store what the model forbids.

Evidence is checked at every ACCEPTANCE: creating a document, saving its
working copy and checkpointing it each ask `evidence.unconfirmed_lines` and
refuse with `EvidenceNotConfirmed` (nothing written). Reading never asks, so
a revision stays readable after a claim it cites is retired. Restoring one
puts its content back as it was and trusts nothing: the next save,
checkpoint or export asks again, and the person decides about the lines that
no longer hold.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager, nullcontext, suppress
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Literal, TypeVar

from career_agent.clock import new_id, now_utc
from career_agent.resume_doc.evidence import unconfirmed_lines
from career_agent.resume_doc.models import (
    DocumentKind,
    ResumeDocument,
    canonical_json,
    sha256_text,
    upgrade_resume_document,
)
from career_agent.storage.db import transaction

RevisionReason = Literal[
    "CREATED",
    "IMPORTED",
    "GENERATED",
    "AI_ACCEPTED",
    "MANUAL_CHECKPOINT",
    "RESTORED",
    "TEMPLATE_CHANGED",
    "EXPORTED",
    "PRE_MIGRATION",
]
RunStatus = Literal["PENDING", "RUNNING", "DONE", "ERROR"]
ChangeSource = Literal["RULE", "DRAFTER", "REVIEWER"]
Decision = Literal["ACCEPTED", "EDITED", "REJECTED"]
ExportFormat = Literal["PDF", "DOCX", "JSON"]

#: The tailoring-run columns a stage may fill, each a JSON object; `{}` is unfilled.
RUN_STAGES = (
    "prompt_digests",
    "options",
    "analysis",
    "retrieval",
    "strategy",
    "review",
    "validation",
    "token_usage",
)


class ResumeStoreError(ValueError):
    """A request the Resume Workspace storage refuses."""


class NotFound(ResumeStoreError):
    pass


class EvidenceNotConfirmed(ResumeStoreError):
    """Lines (or entries) cite Career Evidence that is not a confirmed,
    current claim of this profile now. `lines` are their ids."""

    def __init__(self, lines: list[str]) -> None:
        super().__init__("some lines cite evidence that is not confirmed now")
        self.lines = lines


class MasterInPlace(ResumeStoreError):
    """An archived Master cannot come back beside the current one."""


class StaleDocument(ResumeStoreError):
    """The working copy changed since the caller read it."""

    def __init__(self, document_id: str, current_sha256: str) -> None:
        super().__init__(f"resume document {document_id} changed since it was read")
        self.current_sha256 = current_sha256


@dataclass(frozen=True)
class StoredDocument:
    id: str
    kind: DocumentKind
    title: str
    parent_document_id: str | None
    master_document_id: str | None
    jd_snapshot_id: str | None
    version_group: str | None
    version_number: int | None
    label: str | None
    preferred: bool
    archived_at: str | None
    created_at: str
    updated_at: str
    working: ResumeDocument
    working_sha256: str


@dataclass(frozen=True)
class Revision:
    id: str
    document_id: str
    seq: int
    content: ResumeDocument
    content_sha256: str
    reason: RevisionReason
    base_revision_id: str | None
    created_at: str


@dataclass(frozen=True)
class JdSnapshot:
    id: str
    job_id: str | None
    title: str
    company: str | None
    url: str | None
    text: str
    text_sha256: str
    language: str | None
    captured_at: str


@dataclass(frozen=True)
class TailoringRun:
    id: str
    document_id: str
    jd_snapshot_id: str
    master_document_id: str | None
    master_revision_id: str | None
    mode: str
    provider: str | None
    model: str | None
    stages: dict[str, dict[str, Any]]
    status: RunStatus
    started_at: str
    finished_at: str | None


@dataclass(frozen=True)
class TailoringChange:
    id: str
    run_id: str
    op: dict[str, Any]
    evidence_ids: list[str]
    requirement_ids: list[str]
    source: ChangeSource
    reason: str | None
    decision: str
    decided_at: str | None


@dataclass(frozen=True)
class ResumeExport:
    id: str
    document_id: str
    revision_id: str
    format: ExportFormat
    template: str
    file_path: str
    file_sha256: str
    page_count: int | None
    engine: str
    ats_check: dict[str, Any]
    created_at: str


T = TypeVar("T")


def _from_row(cls: type[T], r: sqlite3.Row, **parsed: Any) -> T:
    """A record from its row: columns by name, parsed fields given explicitly."""
    plain = {f.name: r[f.name] for f in fields(cls) if f.name not in parsed}  # type: ignore[arg-type]
    return cls(**plain, **parsed)


def _object(value: dict[str, Any] | None) -> str:
    return json.dumps(value or {}, sort_keys=True, ensure_ascii=False)


def _body(doc: ResumeDocument) -> tuple[ResumeDocument, str, str]:
    """Validate again, then serialise: what is stored always loads."""
    checked = upgrade_resume_document(doc.model_dump(mode="json"))
    body = canonical_json(checked)
    return checked, body, sha256_text(body)


class ResumeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def _tx(self) -> AbstractContextManager[Any]:
        """A write transaction, or the caller's when one is already open: a
        migration groups several store calls into one all-or-nothing unit."""
        return nullcontext() if self.conn.in_transaction else transaction(self.conn)

    def _accept(self, doc: ResumeDocument) -> None:
        """Refuse a document citing evidence this profile has not confirmed now."""
        lines = unconfirmed_lines(self.conn, doc)
        if lines:
            raise EvidenceNotConfirmed(lines)

    # ------------------------------------------------------------ documents

    def create_document(
        self,
        doc: ResumeDocument,
        *,
        reason: RevisionReason = "CREATED",
        parent_document_id: str | None = None,
        label: str | None = None,
        now: str | None = None,
    ) -> StoredDocument:
        """Store a new document and its first revision. A TAILORED document
        takes the next version number of its job, inside the write lock."""
        now = now or now_utc()
        doc, body, sha = _body(doc)
        with self._tx():
            self._accept(doc)
            master = doc.provenance.master_document_id
            rev = doc.provenance.master_revision_id
            if rev is not None and master is None:
                raise ResumeStoreError("master_revision_id needs its master_document_id")
            if master is not None:
                if self._row("resume_document", master)["kind"] != DocumentKind.MASTER:
                    raise ResumeStoreError("master_document_id must name a MASTER document")
                if rev is not None and self._row("resume_revision", rev)["document_id"] != master:
                    raise ResumeStoreError("master_revision_id is not a revision of that master")
            group = number = None
            if doc.target is not None:
                snap = self.get_jd_snapshot(doc.target.jd_snapshot_id)
                target = (doc.target.job_id, doc.target.title, doc.target.company)
                if target != (snap.job_id, snap.title, snap.company):
                    raise ResumeStoreError("the target's job, title and company are the snapshot's")
                group = f"job:{snap.job_id}" if snap.job_id else f"jd:{snap.text_sha256}"
                # Inside BEGIN IMMEDIATE no other writer can interleave, and
                # UNIQUE (version_group, version_number) is the backstop.
                number = self.conn.execute(
                    "SELECT COALESCE(MAX(version_number), 0) + 1 FROM resume_document"
                    " WHERE version_group = ?",
                    (group,),
                ).fetchone()[0]
            self.conn.execute(
                "INSERT INTO resume_document (id, kind, title, language, parent_document_id,"
                " master_document_id, jd_snapshot_id, version_group, version_number, label,"
                " created_at, updated_at, working_json, working_sha256)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc.id,
                    doc.kind.value,
                    doc.title,
                    doc.language,
                    parent_document_id,
                    master,
                    doc.target.jd_snapshot_id if doc.target else None,
                    group,
                    number,
                    label,
                    now,
                    now,
                    body,
                    sha,
                ),
            )
            self._append_revision(doc.id, body, sha, reason, None, now)
        return self.get_document(doc.id)

    def current_master(self) -> StoredDocument | None:
        """The profile's one current MASTER document, if it has one."""
        row = self.conn.execute(
            "SELECT * FROM resume_document WHERE kind = 'MASTER' AND archived_at IS NULL"
        ).fetchone()
        return self._document(row) if row else None

    def get_document(self, document_id: str) -> StoredDocument:
        return self._document(self._row("resume_document", document_id))

    def list_documents(
        self, *, kind: DocumentKind | None = None, include_archived: bool = False
    ) -> list[StoredDocument]:
        sql = "SELECT * FROM resume_document WHERE 1 = 1"
        args: list[Any] = []
        if kind is not None:
            sql += " AND kind = ?"
            args.append(kind.value)
        if not include_archived:
            sql += " AND archived_at IS NULL"
        sql += " ORDER BY version_group, version_number, created_at, id"
        return [self._document(r) for r in self.conn.execute(sql, args)]

    def save_working_copy(
        self, document_id: str, doc: ResumeDocument, *, expected_sha256: str, now: str | None = None
    ) -> str:
        """Autosave. Returns the new hash; `StaleDocument` if another write came first."""
        doc, body, sha = _body(doc)
        with self._tx():
            self._accept(doc)
            current = self._row("resume_document", document_id)
            if current["working_sha256"] == sha:
                # Already exactly this: a retried save whose answer was lost.
                return sha
            if current["working_sha256"] != expected_sha256:
                raise StaleDocument(document_id, current["working_sha256"])
            stored = upgrade_resume_document(current["working_json"])
            fixed = ("id", "kind", "target", "provenance")
            if any(getattr(doc, name) != getattr(stored, name) for name in fixed):
                raise ResumeStoreError("a document's id, kind, target and provenance do not change")
            if sha != expected_sha256:
                self._write_working(document_id, doc, body, sha, now or now_utc())
        return sha

    def checkpoint_revision(
        self, document_id: str, reason: RevisionReason, *, now: str | None = None
    ) -> Revision:
        """Append the working copy to the history. A working copy identical to
        the latest revision is that revision: no duplicate row is written."""
        with self._tx():
            current = self._row("resume_document", document_id)
            self._accept(upgrade_resume_document(current["working_json"]))
            latest = self.conn.execute(
                "SELECT id, content_sha256 FROM resume_revision WHERE document_id = ?"
                " ORDER BY seq DESC LIMIT 1",
                (document_id,),
            ).fetchone()
            if latest is not None and latest["content_sha256"] == current["working_sha256"]:
                revision_id = latest["id"]
            else:
                revision_id = self._append_revision(
                    document_id,
                    current["working_json"],
                    current["working_sha256"],
                    reason,
                    latest["id"] if latest else None,
                    now or now_utc(),
                )
        return self.get_revision(revision_id)

    def restore_revision(
        self, document_id: str, revision_id: str, *, expected_sha256: str, now: str | None = None
    ) -> Revision:
        """Make an old revision the working copy again, as a NEW revision.

        History is put back as it was, never rewritten and never re-trusted:
        evidence it cites that is no longer confirmed stays as written, and the
        next save, checkpoint or export refuses it until the person decides
        (`EvidenceNotConfirmed` names the lines)."""
        now = now or now_utc()
        with self._tx():
            old = self._row("resume_revision", revision_id)
            if old["document_id"] != document_id:
                raise ResumeStoreError("that revision belongs to another document")
            current = self._row("resume_document", document_id)
            if current["working_sha256"] != expected_sha256:
                raise StaleDocument(document_id, current["working_sha256"])
            body, sha = old["content_json"], old["content_sha256"]
            self._write_working(document_id, upgrade_resume_document(body), body, sha, now)
            new = self._append_revision(document_id, body, sha, "RESTORED", old["id"], now)
        return self.get_revision(new)

    def get_revision(self, revision_id: str) -> Revision:
        return self._revision(self._row("resume_revision", revision_id))

    def list_revisions(self, document_id: str) -> list[Revision]:
        rows = self.conn.execute(
            "SELECT * FROM resume_revision WHERE document_id = ? ORDER BY seq", (document_id,)
        )
        return [self._revision(r) for r in rows]

    def archive_document(self, document_id: str, *, now: str | None = None) -> None:
        """Soft delete: the row, its history and its exports all stay."""
        with self._tx():
            self._row("resume_document", document_id)
            self.conn.execute(
                "UPDATE resume_document SET archived_at = COALESCE(archived_at, ?),"
                " preferred = 0 WHERE id = ?",
                (now or now_utc(), document_id),
            )

    def set_preferred(self, document_id: str) -> None:
        """This version becomes the one preferred for its job; any other stops being."""
        with self._tx():
            row = self._row("resume_document", document_id)
            if row["kind"] != DocumentKind.TAILORED or row["archived_at"] is not None:
                raise ResumeStoreError("only a current tailored version can be preferred")
            self.conn.execute(
                "UPDATE resume_document SET preferred = 0 WHERE version_group = ? AND id != ?",
                (row["version_group"], document_id),
            )
            self.conn.execute(
                "UPDATE resume_document SET preferred = 1 WHERE id = ?", (document_id,)
            )

    def unarchive(self, document_id: str) -> None:
        """Back among the current resumes. An old Master comes back only when
        there is no current one (`make_master` replaces it on purpose)."""
        with self._tx():
            row = self._row("resume_document", document_id)
            if row["kind"] == DocumentKind.MASTER and self.current_master() is not None:
                raise MasterInPlace(document_id)
            self.conn.execute(
                "UPDATE resume_document SET archived_at = NULL WHERE id = ?", (document_id,)
            )

    def rename(self, document_id: str, title: str, *, now: str | None = None) -> StoredDocument:
        """The document's own name. The job it is for, its source titles and
        every line stay as they are."""
        with self._tx():
            doc = self.get_document(document_id)
            renamed = doc.working.model_copy(update={"title": title})
            self.save_working_copy(
                document_id, renamed, expected_sha256=doc.working_sha256, now=now
            )
        return self.get_document(document_id)

    def copy_document(
        self, source_id: str, *, title: str | None = None, now: str | None = None
    ) -> StoredDocument:
        """A new document holding the source's working copy, line ids and all.
        A copy of a version for a job is the NEXT version of that job; a copy
        of the Master is a draft (there is one Master). Nothing is rewritten."""
        with self._tx():
            src = self.get_document(source_id)
            data = src.working.model_dump(mode="json")
            kind = DocumentKind.SCRATCH if src.kind is DocumentKind.MASTER else src.kind
            data.update(
                id=new_id(),
                kind=kind.value,
                title=(title or src.title)[:300],
                provenance={
                    **data["provenance"],
                    "created_from": "DUPLICATE",
                    "tailoring_run_id": None,
                },
            )
            doc = upgrade_resume_document(data)
            return self.create_document(doc, parent_document_id=src.id, now=now)

    def make_master(self, document_id: str, *, now: str | None = None) -> StoredDocument:
        """This resume becomes the profile's Master, in one transaction. The
        current Master is archived, never deleted. An archived Master simply
        comes back; any other resume is copied into a new Master (a kind never
        changes) and is archived itself, both keeping their whole history."""
        now = now or now_utc()
        with self._tx():
            src = self.get_document(document_id)
            current = self.current_master()
            if current is not None and current.id == src.id:
                return current
            if src.kind is DocumentKind.TAILORED:
                raise ResumeStoreError("a version for a job is not made the Master")
            if current is not None:
                self.archive_document(current.id, now=now)
            if src.kind is DocumentKind.MASTER:
                self.unarchive(src.id)
                return self.get_document(src.id)
            data = src.working.model_dump(mode="json")
            data.update(
                id=new_id(),
                kind=DocumentKind.MASTER.value,
                provenance={**data["provenance"], "created_from": "DUPLICATE"},
            )
            made = self.create_document(
                upgrade_resume_document(data), parent_document_id=src.id, now=now
            )
            self.archive_document(src.id, now=now)
            return made

    def version_from_master(
        self,
        *,
        job_id: str,
        title: str,
        company: str | None,
        url: str | None,
        text: str,
        now: str | None = None,
    ) -> StoredDocument:
        """A version for a job, BY HAND: the job ad kept as it is now, and the
        Master's current revision copied as it is. No line is chosen, rewritten
        or added; this is where the person starts editing for this job."""
        with self._tx():
            master = self.current_master()
            if master is None:
                raise NotFound("there is no Master resume yet")
            revision = self.checkpoint_revision(master.id, "MANUAL_CHECKPOINT", now=now)
            snap = self.create_jd_snapshot(
                text=text, title=title, company=company, job_id=job_id, url=url, now=now
            )
            data = revision.content.model_dump(mode="json")
            data.update(
                id=new_id(),
                kind=DocumentKind.TAILORED.value,
                title=" · ".join(filter(None, [snap.title, snap.company]))[:300],
                target={
                    "jd_snapshot_id": snap.id,
                    "job_id": snap.job_id,
                    "title": snap.title,
                    "company": snap.company,
                },
                provenance={
                    "created_from": "MASTER_COPY",
                    "master_document_id": master.id,
                    "master_revision_id": revision.id,
                },
            )
            return self.create_document(
                upgrade_resume_document(data), parent_document_id=master.id, now=now
            )

    def summaries(
        self, *, archived: bool = False, version_group: str | None = None
    ) -> list[dict[str, Any]]:
        """What a list of resumes shows, in ONE query: no document body is
        parsed here, and each row carries its job and its latest export.
        `version_group` narrows it to one job's versions."""
        rows = self.conn.execute(
            "SELECT d.id, d.kind, d.title, d.version_group, d.version_number, d.preferred,"
            " d.archived_at, d.created_at, d.updated_at,"
            " json_extract(d.working_json, '$.design.template') AS template,"
            " s.job_id, s.title AS job_title, s.company AS job_company,"
            " e.id AS export_id, e.format AS export_format, e.page_count,"
            " e.created_at AS exported_at, e.ats_check_json"
            " FROM resume_document d"
            " LEFT JOIN jd_snapshot s ON s.id = d.jd_snapshot_id"
            " LEFT JOIN resume_export e ON e.id = (SELECT x.id FROM resume_export x"
            "   WHERE x.document_id = d.id ORDER BY x.created_at DESC, x.id DESC LIMIT 1)"
            " WHERE (d.archived_at IS NOT NULL) = ? AND (? IS NULL OR d.version_group = ?)"
            " ORDER BY d.updated_at DESC, d.id",
            (1 if archived else 0, version_group, version_group),
        )
        return [dict(r) for r in rows]

    def revision_log(self, document_id: str) -> list[dict[str, Any]]:
        """A document's milestones, newest first, without their content."""
        self._row("resume_document", document_id)
        rows = self.conn.execute(
            "SELECT id, seq, reason, content_sha256, created_at FROM resume_revision"
            " WHERE document_id = ? ORDER BY seq DESC",
            (document_id,),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------- applications

    def mark_used(self, job_id: str, document_id: str, *, now: str | None = None) -> None:
        """The person says THIS resume, as it is now, is the one they used for
        the job. Recorded with the exact revision; never inferred."""
        now = now or now_utc()
        with self._tx():
            revision = self.checkpoint_revision(document_id, "MANUAL_CHECKPOINT", now=now)
            self.conn.execute(
                "INSERT INTO application_resume (job_id, document_id, revision_id, marked_at)"
                " VALUES (?, ?, ?, ?) ON CONFLICT (job_id) DO UPDATE SET"
                " document_id = excluded.document_id, revision_id = excluded.revision_id,"
                " marked_at = excluded.marked_at",
                (job_id, document_id, revision.id, now),
            )

    def clear_used(self, job_id: str) -> None:
        with self._tx():
            self.conn.execute("DELETE FROM application_resume WHERE job_id = ?", (job_id,))

    def used_for(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT a.document_id, a.marked_at, r.seq AS revision_seq, d.title,"
            " d.version_number, d.archived_at IS NOT NULL AS archived"
            " FROM application_resume a JOIN resume_revision r ON r.id = a.revision_id"
            " JOIN resume_document d ON d.id = a.document_id WHERE a.job_id = ?",
            (job_id,),
        ).fetchone()
        return {**dict(row), "archived": bool(row["archived"])} if row else None

    def has_legacy_documents(self) -> bool:
        """Whether resumes of the old Resume helper were moved here."""
        return (
            self.conn.execute(
                "SELECT 1 FROM resume_document WHERE"
                " json_extract(working_json, '$.provenance.import_id') LIKE 'legacy:%' LIMIT 1"
            ).fetchone()
            is not None
        )

    # --------------------------------------------------------- job ads

    def create_jd_snapshot(
        self,
        *,
        text: str,
        title: str,
        company: str | None = None,
        job_id: str | None = None,
        url: str | None = None,
        language: str | None = None,
        now: str | None = None,
    ) -> JdSnapshot:
        """Capture a job ad. Identical content returns the snapshot already held;
        any difference (text, title, company, job, URL, language) is a new one."""
        if not text.strip() or not title.strip():
            raise ResumeStoreError("a job ad snapshot needs the ad's text and title")
        job_id = job_id or None
        identity = json.dumps(
            [job_id, title, company, url, text, language], ensure_ascii=False, separators=(",", ":")
        )
        snapshot_sha = sha256_text(identity)
        with self._tx():
            found = self.conn.execute(
                "SELECT id FROM jd_snapshot WHERE snapshot_sha256 = ?", (snapshot_sha,)
            ).fetchone()
            snapshot_id = found["id"] if found else new_id()
            if found is None:
                self.conn.execute(
                    "INSERT INTO jd_snapshot (id, job_id, title, company, url, text, text_sha256,"
                    " language, snapshot_sha256, captured_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        snapshot_id,
                        job_id,
                        title,
                        company,
                        url,
                        text,
                        sha256_text(text),
                        language,
                        snapshot_sha,
                        now or now_utc(),
                    ),
                )
        return self.get_jd_snapshot(snapshot_id)

    def get_jd_snapshot(self, snapshot_id: str) -> JdSnapshot:
        return _from_row(JdSnapshot, self._row("jd_snapshot", snapshot_id))

    # ------------------------------------------------------- tailoring

    def create_tailoring_run(
        self,
        document_id: str,
        *,
        mode: str,
        provider: str | None = None,
        model: str | None = None,
        options: dict[str, Any] | None = None,
        now: str | None = None,
        run_id: str | None = None,
    ) -> TailoringRun:
        """A run belongs to one tailored document and records the job ad and
        the master revision that document was made from."""
        doc = self.get_document(document_id)
        if doc.kind is not DocumentKind.TAILORED or doc.jd_snapshot_id is None:
            raise ResumeStoreError("a tailoring run belongs to a tailored document")
        run_id = run_id or new_id()
        with self._tx():
            self.conn.execute(
                "INSERT INTO tailoring_run (id, document_id, jd_snapshot_id, master_document_id,"
                " master_revision_id, mode, provider, model, options_json, status, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (
                    run_id,
                    document_id,
                    doc.jd_snapshot_id,
                    doc.master_document_id,
                    doc.working.provenance.master_revision_id,
                    mode,
                    provider,
                    model,
                    _object(options),
                    now or now_utc(),
                ),
            )
        return self.get_tailoring_run(run_id)

    def update_tailoring_run_stage(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        now: str | None = None,
        **stages: dict[str, Any] | None,
    ) -> TailoringRun:
        unknown = set(stages) - set(RUN_STAGES)
        if unknown:
            raise ResumeStoreError(f"unknown tailoring stages: {sorted(unknown)}")
        sets = [f"{name}_json = ?" for name in stages]
        args: list[Any] = [_object(value) for value in stages.values()]
        if status is not None:
            sets.append("status = ?")
            args.append(status)
            if status in ("DONE", "ERROR"):
                sets.append("finished_at = ?")
                args.append(now or now_utc())
        if sets:
            with self._tx():
                self._row("tailoring_run", run_id)
                self.conn.execute(
                    f"UPDATE tailoring_run SET {', '.join(sets)} WHERE id = ?", [*args, run_id]
                )
        return self.get_tailoring_run(run_id)

    def get_tailoring_run(self, run_id: str) -> TailoringRun:
        r = self._row("tailoring_run", run_id)
        stages = {name: json.loads(r[f"{name}_json"]) for name in RUN_STAGES}
        return _from_row(TailoringRun, r, stages=stages)

    def record_tailoring_change(
        self,
        run_id: str,
        *,
        op: dict[str, Any],
        source: ChangeSource,
        evidence_ids: list[str] | None = None,
        requirement_ids: list[str] | None = None,
        reason: str | None = None,
    ) -> TailoringChange:
        """A proposed change, PENDING until the person decides. Proposing
        changes nothing in any document."""
        change_id = new_id()
        with self._tx():
            self._row("tailoring_run", run_id)
            self.conn.execute(
                "INSERT INTO tailoring_change (id, run_id, op_json, evidence_ids_json,"
                " requirement_ids_json, source, reason) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    change_id,
                    run_id,
                    _object(op),
                    json.dumps(list(evidence_ids or [])),
                    json.dumps(list(requirement_ids or [])),
                    source,
                    reason,
                ),
            )
        return self._change(self._row("tailoring_change", change_id))

    def decide_tailoring_change(
        self, change_id: str, decision: Decision, *, now: str | None = None
    ) -> TailoringChange:
        """Decided once. The accepted text itself lands in the document as a
        revision (AI_ACCEPTED), not here."""
        with self._tx():
            if self._row("tailoring_change", change_id)["decision"] != "PENDING":
                raise ResumeStoreError("this change was already decided")
            self.conn.execute(
                "UPDATE tailoring_change SET decision = ?, decided_at = ? WHERE id = ?",
                (decision, now or now_utc(), change_id),
            )
        return self._change(self._row("tailoring_change", change_id))

    def list_tailoring_changes(self, run_id: str) -> list[TailoringChange]:
        rows = self.conn.execute(
            "SELECT * FROM tailoring_change WHERE run_id = ? ORDER BY id", (run_id,)
        )
        return [self._change(r) for r in rows]

    # ------------------------------------------------- exports, findings

    def record_export(
        self,
        document_id: str,
        revision_id: str,
        *,
        format: ExportFormat,
        template: str,
        file_path: str,
        file_sha256: str,
        engine: str,
        page_count: int | None = None,
        ats_check: dict[str, Any] | None = None,
        now: str | None = None,
        export_id: str | None = None,
    ) -> ResumeExport:
        if page_count is not None and (
            isinstance(page_count, bool) or not isinstance(page_count, int) or page_count < 1
        ):
            raise ResumeStoreError("page_count is a whole number of pages, at least 1")
        export_id = export_id or new_id()
        with self._tx():
            if self._row("resume_revision", revision_id)["document_id"] != document_id:
                raise ResumeStoreError("an export names a revision of its own document")
            self.conn.execute(
                "INSERT INTO resume_export (id, document_id, revision_id, format, template,"
                " file_path, file_sha256, page_count, engine, ats_check_json, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    export_id,
                    document_id,
                    revision_id,
                    format,
                    template,
                    file_path,
                    file_sha256,
                    page_count,
                    engine,
                    _object(ats_check),
                    now or now_utc(),
                ),
            )
        return self.get_export(export_id)

    def get_export(self, export_id: str) -> ResumeExport:
        return self._export(self._row("resume_export", export_id))

    def list_exports(self, document_id: str) -> list[ResumeExport]:
        rows = self.conn.execute(
            "SELECT * FROM resume_export WHERE document_id = ? ORDER BY created_at, id",
            (document_id,),
        )
        return [self._export(r) for r in rows]

    def dismiss_finding(
        self,
        document_id: str,
        finding_key: str,
        *,
        reason: str | None = None,
        now: str | None = None,
    ) -> None:
        """Set a finding aside in THIS document only."""
        with self._tx():
            self._row("resume_document", document_id)
            self.conn.execute(
                "INSERT OR IGNORE INTO resume_finding_dismissal"
                " (document_id, finding_key, reason, created_at) VALUES (?, ?, ?, ?)",
                (document_id, finding_key, reason, now or now_utc()),
            )

    def dismissed_findings(self, document_id: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT finding_key FROM resume_finding_dismissal WHERE document_id = ?",
            (document_id,),
        )
        return {r["finding_key"] for r in rows}

    # ----------------------------------------------------------- internals

    def _row(self, table: str, row_id: str) -> sqlite3.Row:
        row: sqlite3.Row | None = self.conn.execute(
            f"SELECT * FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        if row is None:
            raise NotFound(f"no {table} {row_id}")
        return row

    def _write_working(
        self, document_id: str, doc: ResumeDocument, body: str, sha: str, now: str
    ) -> None:
        self.conn.execute(
            "UPDATE resume_document SET working_json = ?, working_sha256 = ?,"
            " title = ?, language = ?, updated_at = ? WHERE id = ?",
            (body, sha, doc.title, doc.language, now, document_id),
        )

    def _append_revision(
        self,
        document_id: str,
        body: str,
        sha: str,
        reason: RevisionReason,
        base_revision_id: str | None,
        now: str,
    ) -> str:
        revision_id = new_id()
        self.conn.execute(
            "INSERT INTO resume_revision (id, document_id, seq, content_json, content_sha256,"
            " reason, base_revision_id, created_at) VALUES (?, ?,"
            " (SELECT COALESCE(MAX(seq), 0) + 1 FROM resume_revision WHERE document_id = ?),"
            " ?, ?, ?, ?, ?)",
            (revision_id, document_id, document_id, body, sha, reason, base_revision_id, now),
        )
        return revision_id

    @staticmethod
    def _document(r: sqlite3.Row) -> StoredDocument:
        return _from_row(
            StoredDocument,
            r,
            kind=DocumentKind(r["kind"]),
            preferred=bool(r["preferred"]),
            working=upgrade_resume_document(r["working_json"]),
        )

    @staticmethod
    def _revision(r: sqlite3.Row) -> Revision:
        return _from_row(Revision, r, content=upgrade_resume_document(r["content_json"]))

    @staticmethod
    def _change(r: sqlite3.Row) -> TailoringChange:
        return _from_row(
            TailoringChange,
            r,
            op=json.loads(r["op_json"]),
            evidence_ids=json.loads(r["evidence_ids_json"]),
            requirement_ids=json.loads(r["requirement_ids_json"]),
        )

    @staticmethod
    def _export(r: sqlite3.Row) -> ResumeExport:
        return _from_row(ResumeExport, r, ats_check=json.loads(r["ats_check_json"]))


#: Every Resume Workspace table, children first, so a delete never orphans a row.
RESUME_TABLES = (
    "application_resume",
    "resume_finding_dismissal",
    "resume_export",
    "tailoring_change",
    "tailoring_run",
    "resume_revision",
    "resume_document",
    "jd_snapshot",
)
#: The triggers that keep history and job ads from ever being deleted.
_KEEP_TRIGGERS = ("resume_revision_kept", "jd_snapshot_kept")


def resume_row_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in RESUME_TABLES}


def forget_resume_data(conn: sqlite3.Connection) -> list[Path]:
    """Delete every resume row in THIS profile's database, on the person's
    explicit request (`career-agent forget everything`). The guards against
    deleting history are lifted inside the transaction and put back before
    it commits. Joins the caller's transaction when one is open. Returns the
    files exported from these documents: `delete_export_files` removes them
    once the caller has committed, so a rollback never leaves rows pointing
    at files that are gone."""
    files = [r[0] for r in conn.execute("SELECT file_path FROM resume_export")]
    root = export_root(conn) if files else None
    with nullcontext() if conn.in_transaction else transaction(conn):
        guards = [
            conn.execute(
                "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (name,)
            ).fetchone()[0]
            for name in _KEEP_TRIGGERS
        ]
        for name in _KEEP_TRIGGERS:
            conn.execute(f"DROP TRIGGER {name}")
        for table in RESUME_TABLES:
            conn.execute(f"DELETE FROM {table}")
        for sql in guards:
            conn.execute(sql)
    # The verified backups made before moving the old helper's resumes are
    # copies of resume data too, kept beside the database: they go as well.
    backups = sorted(legacy_backup_dir(conn).glob("*.zip")) if _has_folder(conn) else []
    if root is None:
        return backups
    paths = [(root / name).resolve() for name in files]
    return [p for p in paths if p.is_relative_to(root)] + backups


def _has_folder(conn: sqlite3.Connection) -> bool:
    return any(row[1] == "main" and row[2] for row in conn.execute("PRAGMA database_list"))


def legacy_backup_dir(conn: sqlite3.Connection) -> Path:
    """Where the old helper's workspace is backed up before a move."""
    return export_root(conn).parent / "resume_helper_backups"


def export_root(conn: sqlite3.Connection) -> Path:
    """This profile's private export folder, beside its database file."""
    for row in conn.execute("PRAGMA database_list"):
        if row[1] == "main" and row[2]:
            return (Path(row[2]).parent / "resume_exports").resolve()
    raise ResumeStoreError("this database has no folder of its own")


def delete_export_files(paths: list[Path]) -> None:
    """Remove forgotten export files, and the folders they leave empty."""
    for path in paths:
        path.unlink(missing_ok=True)
        with suppress(OSError):
            path.parent.rmdir()

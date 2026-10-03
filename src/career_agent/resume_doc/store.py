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
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, fields
from typing import Any, Literal, TypeVar

from career_agent.clock import new_id, now_utc
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
        """Make an old revision the working copy again, as a NEW revision."""
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


def forget_resume_data(conn: sqlite3.Connection) -> None:
    """Delete every resume row in THIS profile's database, on the person's
    explicit request (`career-agent forget everything`). The guards against
    deleting history are lifted inside the transaction and put back before
    it commits. Joins the caller's transaction when one is open."""
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

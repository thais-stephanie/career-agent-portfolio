"""The only code that reads or writes the Resume Workspace tables (migration 0047).

The store is bound to ONE connection, which is one profile's database: resume
rows never go anywhere shared, so a store cannot see another profile's
documents by construction.

Working copy and revisions are two acts. `save_working_copy` is the cheap
autosave and needs the hash the caller last read (`expected_sha256`): a
second window holding an older copy gets `StaleDocument`, never a silent
overwrite. `checkpoint_revision` appends a milestone; history rows are never
updated (a trigger refuses it), and restoring an old revision appends a new
one rather than rewinding.

A tailored document is a separate row that copies from a named master
revision. Nothing here writes a master as a side effect of tailoring.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

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

#: The tailoring-run columns a stage may fill, each a JSON object.
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
    stages: dict[str, dict[str, Any] | None]
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
    ats_check: dict[str, Any] | None
    created_at: str


def _object(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ResumeStoreError("a stage is stored as a JSON object")
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


class ResumeStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

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
        body = canonical_json(doc)
        sha = sha256_text(body)
        with transaction(self.conn):
            master = doc.provenance.master_document_id
            if master is not None:
                row = self._row("resume_document", master)
                if row["kind"] != DocumentKind.MASTER:
                    raise ResumeStoreError("master_document_id must name a MASTER document")
                rev = doc.provenance.master_revision_id
                if rev is not None and self._row("resume_revision", rev)["document_id"] != master:
                    raise ResumeStoreError("master_revision_id is not a revision of that master")
            group = number = None
            if doc.target is not None:
                snapshot = self.get_jd_snapshot(doc.target.jd_snapshot_id)
                if doc.target.job_id != snapshot.job_id:
                    raise ResumeStoreError("the target's job is not the snapshot's job")
                group = (
                    f"job:{snapshot.job_id}" if snapshot.job_id else f"jd:{snapshot.text_sha256}"
                )
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
        body = canonical_json(doc)
        sha = sha256_text(body)
        with transaction(self.conn):
            current = self._row("resume_document", document_id)
            if current["working_sha256"] != expected_sha256:
                raise StaleDocument(document_id, current["working_sha256"])
            stored = upgrade_resume_document(current["working_json"])
            if (doc.id, doc.kind, doc.target and doc.target.jd_snapshot_id) != (
                stored.id,
                stored.kind,
                stored.target and stored.target.jd_snapshot_id,
            ):
                raise ResumeStoreError("a document's id, kind and job ad do not change")
            if sha != expected_sha256:
                self.conn.execute(
                    "UPDATE resume_document SET working_json = ?, working_sha256 = ?,"
                    " title = ?, language = ?, updated_at = ? WHERE id = ?",
                    (body, sha, doc.title, doc.language, now or now_utc(), document_id),
                )
        return sha

    def checkpoint_revision(
        self, document_id: str, reason: RevisionReason, *, now: str | None = None
    ) -> Revision:
        """Append the working copy to the history. A working copy identical to
        the latest revision is that revision: no duplicate row is written."""
        with transaction(self.conn):
            current = self._row("resume_document", document_id)
            latest = self._latest(document_id)
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
        with transaction(self.conn):
            old = self._row("resume_revision", revision_id)
            if old["document_id"] != document_id:
                raise ResumeStoreError("that revision belongs to another document")
            current = self._row("resume_document", document_id)
            if current["working_sha256"] != expected_sha256:
                raise StaleDocument(document_id, current["working_sha256"])
            restored = upgrade_resume_document(old["content_json"])
            self.conn.execute(
                "UPDATE resume_document SET working_json = ?, working_sha256 = ?,"
                " title = ?, language = ?, updated_at = ? WHERE id = ?",
                (
                    old["content_json"],
                    old["content_sha256"],
                    restored.title,
                    restored.language,
                    now,
                    document_id,
                ),
            )
            new = self._append_revision(
                document_id, old["content_json"], old["content_sha256"], "RESTORED", old["id"], now
            )
        return self.get_revision(new)

    def get_revision(self, revision_id: str) -> Revision:
        r = self._row("resume_revision", revision_id)
        return Revision(
            id=r["id"],
            document_id=r["document_id"],
            seq=r["seq"],
            content=upgrade_resume_document(r["content_json"]),
            content_sha256=r["content_sha256"],
            reason=r["reason"],
            base_revision_id=r["base_revision_id"],
            created_at=r["created_at"],
        )

    def list_revisions(self, document_id: str) -> list[Revision]:
        rows = self.conn.execute(
            "SELECT id FROM resume_revision WHERE document_id = ? ORDER BY seq", (document_id,)
        ).fetchall()
        return [self.get_revision(r["id"]) for r in rows]

    def archive_document(self, document_id: str, *, now: str | None = None) -> None:
        """Soft delete: the row, its history and its exports all stay."""
        with transaction(self.conn):
            self._row("resume_document", document_id)
            self.conn.execute(
                "UPDATE resume_document SET archived_at = COALESCE(archived_at, ?),"
                " preferred = 0 WHERE id = ?",
                (now or now_utc(), document_id),
            )

    def set_preferred(self, document_id: str) -> None:
        """This version becomes the one preferred for its job; any other stops being."""
        with transaction(self.conn):
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
        if not text.strip():
            raise ResumeStoreError("a job ad snapshot needs the ad's text")
        identity = json.dumps(
            [job_id, title, company, url, text, language], ensure_ascii=False, separators=(",", ":")
        )
        snapshot_sha = sha256_text(identity)
        with transaction(self.conn):
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
        r = self._row("jd_snapshot", snapshot_id)
        return JdSnapshot(
            id=r["id"],
            job_id=r["job_id"],
            title=r["title"],
            company=r["company"],
            url=r["url"],
            text=r["text"],
            text_sha256=r["text_sha256"],
            language=r["language"],
            captured_at=r["captured_at"],
        )

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
    ) -> TailoringRun:
        """A run belongs to one tailored document and records the job ad and
        the master revision that document was made from."""
        doc = self.get_document(document_id)
        if doc.kind is not DocumentKind.TAILORED or doc.jd_snapshot_id is None:
            raise ResumeStoreError("a tailoring run belongs to a tailored document")
        run_id = new_id()
        with transaction(self.conn):
            self.conn.execute(
                "INSERT INTO tailoring_run (id, document_id, jd_snapshot_id, master_document_id,"
                " master_revision_id, mode, provider, model, options_json, status, started_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)",
                (
                    run_id,
                    document_id,
                    doc.jd_snapshot_id,
                    doc.working.provenance.master_document_id,
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
            with transaction(self.conn):
                self._row("tailoring_run", run_id)
                self.conn.execute(
                    f"UPDATE tailoring_run SET {', '.join(sets)} WHERE id = ?", [*args, run_id]
                )
        return self.get_tailoring_run(run_id)

    def get_tailoring_run(self, run_id: str) -> TailoringRun:
        r = self._row("tailoring_run", run_id)
        return TailoringRun(
            id=r["id"],
            document_id=r["document_id"],
            jd_snapshot_id=r["jd_snapshot_id"],
            master_document_id=r["master_document_id"],
            master_revision_id=r["master_revision_id"],
            mode=r["mode"],
            provider=r["provider"],
            model=r["model"],
            stages={
                name: json.loads(r[f"{name}_json"]) if r[f"{name}_json"] else None
                for name in RUN_STAGES
            },
            status=r["status"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
        )

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
        with transaction(self.conn):
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
        with transaction(self.conn):
            row = self._row("tailoring_change", change_id)
            if row["decision"] != "PENDING":
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
    ) -> ResumeExport:
        export_id = new_id()
        with transaction(self.conn):
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
        return self.list_exports(document_id)[-1]

    def list_exports(self, document_id: str) -> list[ResumeExport]:
        rows = self.conn.execute(
            "SELECT * FROM resume_export WHERE document_id = ? ORDER BY created_at, id",
            (document_id,),
        )
        return [
            ResumeExport(
                id=r["id"],
                document_id=r["document_id"],
                revision_id=r["revision_id"],
                format=r["format"],
                template=r["template"],
                file_path=r["file_path"],
                file_sha256=r["file_sha256"],
                page_count=r["page_count"],
                engine=r["engine"],
                ats_check=json.loads(r["ats_check_json"]) if r["ats_check_json"] else None,
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def dismiss_finding(
        self,
        document_id: str,
        finding_key: str,
        *,
        reason: str | None = None,
        now: str | None = None,
    ) -> None:
        """Set a finding aside in THIS document only."""
        with transaction(self.conn):
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

    def _latest(self, document_id: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self.conn.execute(
            "SELECT * FROM resume_revision WHERE document_id = ? ORDER BY seq DESC LIMIT 1",
            (document_id,),
        ).fetchone()
        return row

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

    def _document(self, r: sqlite3.Row) -> StoredDocument:
        return StoredDocument(
            id=r["id"],
            kind=DocumentKind(r["kind"]),
            title=r["title"],
            parent_document_id=r["parent_document_id"],
            master_document_id=r["master_document_id"],
            jd_snapshot_id=r["jd_snapshot_id"],
            version_group=r["version_group"],
            version_number=r["version_number"],
            label=r["label"],
            preferred=bool(r["preferred"]),
            archived_at=r["archived_at"],
            created_at=r["created_at"],
            updated_at=r["updated_at"],
            working=upgrade_resume_document(r["working_json"]),
            working_sha256=r["working_sha256"],
        )

    @staticmethod
    def _change(r: sqlite3.Row) -> TailoringChange:
        return TailoringChange(
            id=r["id"],
            run_id=r["run_id"],
            op=json.loads(r["op_json"]),
            evidence_ids=json.loads(r["evidence_ids_json"]),
            requirement_ids=json.loads(r["requirement_ids_json"]),
            source=r["source"],
            reason=r["reason"],
            decision=r["decision"],
            decided_at=r["decided_at"],
        )

"""Bring a retired Resume helper workspace into the Resume Workspace.

The helper's engine is gone (PR 12): its files are read here directly, by
the frozen readers in `legacy_format`, and nothing of the old engine runs.

READ-ONLY on the old files, PROFILE-SCOPED (one workspace into one profile's
database) and IDEMPOTENT: every migrated row has an id derived from what it
came from, so a second run finds it and writes nothing.

It needs a verified backup first. `backup_legacy_workspace` zips the whole
workspace and checks every file in the archive against the original;
`migrate_legacy_workspace` refuses a backup whose files no longer match the
workspace, so what is migrated is exactly what can be restored.

Each unit (the identity and base resumes, one run, one export) is its own
transaction. A unit that cannot be read is rolled back and named in the
report with the reason; the others still migrate, and a run after the cause
is fixed completes the rest. Nothing is skipped silently.

Mapping (nothing is regenerated):
* candidate.json contact -> the Master's identity (never "You" or a profile
  label as the name);
* the default base resume -> MASTER when the profile has none, any other
  base resume -> IMPORTED (revision PRE_MIGRATION);
* each finished run -> an immutable job ad snapshot, a TAILORED version
  numbered per job in run order (revision GENERATED) and a tailoring_run
  holding the old analysis marked as legacy and untrusted;
* an edited draft -> a MANUAL_CHECKPOINT revision that becomes the working
  copy (hidden lines stay in the GENERATED revision);
* an exported file -> a resume_export row pointing at the file where it is,
  never ATS- or page-checked after the fact.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import zipfile
from collections import defaultdict
from collections.abc import Iterable, Iterator
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc import legacy_format as fmt
from career_agent.resume_doc.legacy_format import (
    BASE_RESUME_ID,
    EMPTY_STATE,
    PROFILE_KEY,
    SOURCE,
    BaseResume,
    GeneratedResume,
    TailorRun,
    export_filename,
)
from career_agent.resume_doc.legacy_format import Bullet as LegacyBullet
from career_agent.resume_doc.legacy_format import ExperienceEntry as LegacyEntry
from career_agent.resume_doc.master import partial_date, real_name, resolve_identity
from career_agent.resume_doc.models import (
    DocumentKind,
    Identity,
    ResumeDocument,
    sha256_text,
    upgrade_resume_document,
)
from career_agent.resume_doc.store import NotFound, ResumeStore, StoredDocument
from career_agent.storage.db import transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import candidate_id_of

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ENGINE = "resume_tailor_legacy"
#: What a legacy export says about checks: none were run on it here.
NOT_CHECKED = {"checked": False, "source": "legacy", "page_count_measured": False}


class LegacyMigrationError(RuntimeError):
    """The migration cannot start (no backup, or one that does not match)."""


def stable_id(*parts: str) -> str:
    """A ULID-shaped id derived from where a row came from (128 bits of
    sha256), so the same legacy item always maps to the same row."""
    n = int.from_bytes(hashlib.sha256("\x1f".join(parts).encode("utf-8")).digest()[:16], "big")
    return "".join(_CROCKFORD[(n >> (5 * i)) & 31] for i in reversed(range(26)))


def _manifest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


@dataclass(frozen=True)
class LegacyBackup:
    root: Path
    archive: Path
    manifest: dict[str, str]


def backup_legacy_workspace(root: Path, destination: Path) -> LegacyBackup:
    """Zip the whole workspace and verify the archive file by file."""
    manifest = _manifest(root)
    destination.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    archive = destination / f"resume-helper-{root.name}-{stamp}.zip"
    with zipfile.ZipFile(archive, "x", zipfile.ZIP_DEFLATED) as zf:
        for name in manifest:
            zf.write(root / name, name)
    backup = LegacyBackup(root=root, archive=archive, manifest=manifest)
    verify_backup(backup)
    return backup


def verify_backup(backup: LegacyBackup) -> None:
    """The archive holds every file of the workspace as it is now."""
    if not backup.archive.is_file():
        raise LegacyMigrationError("the backup archive is missing")
    with zipfile.ZipFile(backup.archive) as zf:
        held = {n: hashlib.sha256(zf.read(n)).hexdigest() for n in zf.namelist()}
    if held != backup.manifest:
        raise LegacyMigrationError("the backup archive does not hold the workspace's files")
    if _manifest(backup.root) != backup.manifest:
        raise LegacyMigrationError("the workspace changed since its backup: back it up again")


def find_workspace(home: Path, profile_id: str) -> Path | None:
    """This profile's Resume helper workspace under its Tailor home, or None.
    Only looks: never creates or adopts one (the old engine does that)."""
    candidates = home / "candidates"
    own = candidates / profile_id.strip().lower()
    if (own / "candidate.json").is_file():
        return own
    for meta in sorted(candidates.glob("*/candidate.json")):
        try:
            if json.loads(meta.read_text("utf-8")).get(PROFILE_KEY) == profile_id:
                return meta.parent
        except (OSError, ValueError):
            continue
    return None


def _parses(path: Path) -> bool:
    try:
        json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    return True


def preflight(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    """What a migration of `root` would move, read without changing anything.
    `state` is NONE (nothing to move), FOUND (nothing moved yet) or MOVED (moved
    before; `remaining` counts the resumes a move could not bring, if any)."""
    meta: dict[str, Any] = {}
    with suppress(OSError, ValueError):
        meta = json.loads((root / "candidate.json").read_text("utf-8"))
    bases: list[str] = []
    for path in sorted((root / "base_resumes").glob("*.json")):
        with suppress(OSError, ValueError):
            bases.append(str(json.loads(path.read_text("utf-8"))["id"]))
    folders = sorted(p for p in (root / "applications").glob("*") if p.is_dir())
    # A run.json that does not even parse can never move: it counts as
    # unfinished, so it does not hold "could not be moved" open forever.
    runs = [p.name for p in folders if _parses(p / "run.json")]
    exports = [
        p for p in sorted((root / "exports").glob("*")) if p.suffix.lower() in (".pdf", ".docx")
    ]
    ids = [stable_id("legacy-base", b) for b in bases] + [stable_id("legacy-run", r) for r in runs]
    held = (
        {
            r[0]
            for r in conn.execute(
                f"SELECT id FROM resume_document WHERE id IN ({','.join('?' * len(ids))})", ids
            )
        }
        if ids
        else set()
    )
    state = "NONE" if not ids else "MOVED" if held else "FOUND"
    return {
        "state": state,
        "remaining": len(set(ids) - held),
        "contact": any(meta.get(k) for k in ("email", "phone", "location", "linkedin")),
        "base_resumes": len(bases),
        "job_versions": len(runs),
        "drafts": sum((root / "drafts" / f"{r}.json").is_file() for r in runs),
        "exports": len(exports),
        "unfinished": len(folders) - len(runs),
    }


@dataclass
class MigrationReport:
    created: list[str] = field(default_factory=list)
    already: list[str] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def migrate_legacy_workspace(
    conn: sqlite3.Connection, backup: LegacyBackup, *, labels: Iterable[str] = ()
) -> MigrationReport:
    verify_backup(backup)
    return _Migration(conn, backup.root, labels).run()


# ---------------------------------------------------------------- mapping


class _Migration:
    def __init__(self, conn: sqlite3.Connection, root: Path, labels: Iterable[str]) -> None:
        self.conn, self.root, self.labels = conn, root, list(labels)
        self.store = ResumeStore(conn)
        self.report = MigrationReport()
        self.identity = Identity()
        self.claims: dict[str, str] = {}
        self.verbatim: dict[str, str] = {}
        self.versions: list[tuple[str, TailorRun]] = []
        self.bank: fmt.EvidenceBank | None = None
        self.bases = sorted((root / "base_resumes").glob("*.json"))

    def run(self) -> MigrationReport:
        # Without the identity and the evidence it can cite, every later unit
        # would be written wrong and, being idempotent, never repaired.
        if not self._unit("identity", self._identity):
            return self.report
        for path in self.bases:
            self._unit(f"base resume {path.stem}", lambda p=path: self._base(p))
        runs = []
        for folder in sorted(p for p in (self.root / "applications").glob("*") if p.is_dir()):
            if not (folder / "run.json").exists():
                self.report.notes.append(f"run {folder.name}: never finished, nothing to migrate")
                continue
            try:
                runs.append(TailorRun.model_validate_json((folder / "run.json").read_text("utf-8")))
            except (ValueError, OSError) as exc:
                self._fail(f"run {folder.name}", exc)
        for run in sorted(runs, key=lambda r: (r.created_at, r.run_id)):
            self._unit(f"run {run.run_id}", lambda r=run: self._run(r))
        for path in sorted((self.root / "exports").glob("*")):
            if path.is_file():
                self._unit(f"export {path.name}", lambda p=path: self._export(p))
        return self.report

    def _unit(self, name: str, step: Any) -> bool:
        """One all-or-nothing unit. Any failure rolls it back and is named."""
        try:
            with transaction(self.conn):
                step()
        except Exception as exc:  # noqa: BLE001 -- a rollback boundary, reported
            self._fail(name, exc)
            return False
        return True

    def _fail(self, name: str, exc: BaseException) -> None:
        reason = exc.errors()[0]["msg"] if isinstance(exc, ValidationError) else str(exc)
        self.report.failures.append({"unit": name, "reason": reason[:300]})

    def _get(self, doc_id: str) -> StoredDocument | None:
        try:
            return self.store.get_document(doc_id)
        except NotFound:
            return None

    def _exists(self, doc_id: str) -> bool:
        if self._get(doc_id) is None:
            return False
        self.report.already.append(doc_id)
        return True

    # -- identity and the evidence it can cite ------------------------------
    def _identity(self) -> None:
        meta = fmt.meta(self.root) if (self.root / "candidate.json").exists() else {}
        owner = meta.get(PROFILE_KEY)
        row = self.conn.execute("SELECT profile_id FROM database_identity").fetchone()
        if owner and row and row[0] and str(owner).casefold() != str(row[0]).casefold():
            raise LegacyMigrationError("this Resume helper workspace belongs to another profile")
        self.bank = bank = fmt.load_bank(self.root)
        # `candidate.json`'s name is the WORKSPACE's name (a profile label,
        # or whatever it was renamed to): never a person's name. The name
        # comes from the evidence bank, else from Career Agent, else nobody.
        contact = {**meta, "name": real_name(bank.candidate.name if bank else "", self.labels)}
        self.identity, notes = resolve_identity(self.conn, contact=contact, labels=self.labels)
        self.report.notes.extend(f"identity {n}" for n in notes)
        candidate = candidate_id_of(self.conn)
        confirmed = (
            {c.claim_key for c in ClaimRepo(self.conn).current(candidate) if c.verified}
            if candidate
            else set()
        )
        for record in bank.records if bank else []:
            # Only a statement this profile still has CONFIRMED cites Career
            # Evidence; an uploaded file's line or a since-retired claim does not.
            if record.source_file == SOURCE and record.source_reference in confirmed:
                self.claims[record.id] = record.source_reference
                self.verbatim[record.id] = record.resume_text

    # -- base resumes ---------------------------------------------------------
    def _base(self, path: Path) -> None:
        base = BaseResume.model_validate_json(path.read_text("utf-8"))
        doc_id = stable_id("legacy-base", base.id)
        if self._exists(doc_id):
            return
        default = fmt.default_resume_id(self.root) or BASE_RESUME_ID
        if len(self.bases) == 1:
            default = base.id
        master = base.id == default and self.store.current_master() is None
        if self.bank is None:
            raise ValueError("no evidence bank: its roles cannot be read")
        bank = self.bank
        positions = {p.id: p for p in bank.positions}

        def bullet(n: str, text: str, ids: list[str]) -> LegacyBullet:
            verbatim = bool(ids) and all(self.verbatim.get(i) == text for i in ids)
            return LegacyBullet(
                id=n, text=text, evidence_ids=ids, origin="verbatim" if verbatim else "rewritten"
            )

        generated = GeneratedResume(
            candidate=bank.candidate,
            headline=base.headline,
            summary=[bullet(f"s{i}", b.text, b.evidence_ids) for i, b in enumerate(base.summary)],
            experience=[
                LegacyEntry(
                    position_id=p.position_id,
                    company=positions[p.position_id].company,
                    title=positions[p.position_id].title,
                    start=positions[p.position_id].start,
                    end=positions[p.position_id].end,
                    location=positions[p.position_id].location,
                    bullets=[
                        bullet(f"{p.position_id}-{i}", b.text, b.evidence_ids)
                        for i, b in enumerate(p.bullets)
                    ],
                )
                for p in base.positions
            ],
            skills=base.skills,
            education=bank.education,
            certifications=bank.certifications,
        )
        doc = self._document(
            generated,
            doc_id=doc_id,
            kind=DocumentKind.MASTER if master else DocumentKind.IMPORTED,
            title=base.name or "Imported resume",
            language="en",
            target=None,
            provenance={"created_from": "IMPORT", "import_id": f"legacy:base:{base.id}"},
            ids=defaultdict(new_id),
            used_model=False,
        )
        self.store.create_document(doc, reason="PRE_MIGRATION")
        self.report.created.append(doc_id)

    # -- runs -----------------------------------------------------------------
    def _run(self, run: TailorRun) -> None:
        doc_id = stable_id("legacy-run", run.run_id)
        run_id = stable_id("legacy-tailoring", run.run_id)
        state = fmt.load_draft_state(self.root, run.run_id)
        draft_sha = sha256_text(json.dumps(state, sort_keys=True, ensure_ascii=False))
        if self._exists(doc_id):
            self.versions.append((doc_id, run))
            held = self.store.get_tailoring_run(run_id).stages["review"].get("draft_sha256")
            if held != draft_sha:
                # Edited in the old helper after it was migrated: said, never applied.
                raise ValueError(
                    "its draft changed after it was migrated; the change is not applied"
                )
            return
        req = run.request
        title = req.target_title or run.job_analysis.get("role_title") or "Job ad"
        snapshot = self.store.create_jd_snapshot(
            text=req.jd_text,
            title=title,
            company=req.target_company or run.job_analysis.get("company") or None,
            job_id=req.source.get("career_job_id") or None,
            url=req.source.get("url") or None,
            now=run.created_at,
        )
        master_id = master_rev = None
        base_id = stable_id("legacy-base", req.resume_id)
        base_doc = self._get(base_id)
        if base_doc is not None and base_doc.kind is DocumentKind.MASTER:
            master_id, master_rev = base_id, self.store.list_revisions(base_id)[0].id
        used_model = run.provider.get("provider", "none") != "none" and req.options.use_llm
        locale = req.options.resume_locale
        common: dict[str, Any] = {
            "doc_id": doc_id,
            "kind": DocumentKind.TAILORED,
            "title": f"{snapshot.title} at {snapshot.company}" if snapshot.company else title,
            "language": locale if re.match(r"^[a-z]{2}(-[A-Z]{2})?$", locale) else "en",
            "target": {
                "jd_snapshot_id": snapshot.id,
                "job_id": snapshot.job_id,
                "title": snapshot.title,
                "company": snapshot.company,
            },
            "provenance": {
                "created_from": "TAILOR",
                "master_document_id": master_id,
                "master_revision_id": master_rev,
                "import_id": f"legacy:run:{run.run_id}",
                "tailoring_run_id": run_id,
            },
            "ids": defaultdict(new_id),
            "used_model": used_model,
        }
        generated = self._document(run.generated_resume, **common)
        stored = self.store.create_document(generated, reason="GENERATED", now=run.created_at)
        self.store.create_tailoring_run(
            doc_id,
            mode="DETERMINISTIC_V1" if not used_model else "LEGACY_MODEL_V1",
            provider=run.provider.get("provider"),
            model=run.provider.get("model"),
            options=req.options.model_dump(),
            now=run.created_at,
            run_id=run_id,
        )
        legacy = {"source": ENGINE, "trusted": False}
        self.store.update_tailoring_run_stage(
            run_id,
            status="DONE",
            now=run.created_at,
            analysis={
                **legacy,
                "job_analysis": run.job_analysis,
                "evidence_matches": run.evidence_matches,
            },
            strategy={**legacy, "resume_strategy": run.resume_strategy},
            validation={
                **legacy,
                "validation_report": run.validation_report,
                "lint_report": run.lint_report,
                "claim_evidence_map": run.claim_evidence_map,
            },
            review={
                **legacy,
                "diff_report": run.diff_report,
                "draft_sha256": draft_sha,
            },
        )
        if state != EMPTY_STATE:
            # The exact resume the old helper exported for this draft.
            effective = fmt.apply_draft(run, state)
            edited = _mark_edits(generated, self._document(effective, **common))
            if state.get("hidden_skills"):
                self.report.notes.append(
                    f"run {run.run_id}: skills hidden in the draft are left out of the"
                    " working copy and kept in the generated revision"
                )
            self.store.save_working_copy(doc_id, edited, expected_sha256=stored.working_sha256)
            self.store.checkpoint_revision(doc_id, "MANUAL_CHECKPOINT", now=run.created_at)
            if state.get("note"):
                self.report.notes.append(f"run {run.run_id}: its personal note was not migrated")
        self.versions.append((doc_id, run))
        self.report.created.append(doc_id)

    # -- exports --------------------------------------------------------------
    def _export(self, path: Path) -> None:
        body = path.read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        export_id = stable_id("legacy-export", path.name, digest)
        if self.conn.execute("SELECT 1 FROM resume_export WHERE id = ?", (export_id,)).fetchone():
            self.report.already.append(export_id)
            return
        fmt = path.suffix.lstrip(".").upper()
        if fmt not in ("PDF", "DOCX"):
            raise ValueError(f"not a resume export ({path.suffix})")
        # The old helper named a file after the person and the role and kept
        # no link to its run; a file is attached only to the ONE version
        # whose name it carries.
        name = self.bank.candidate.name if self.bank else ""
        owners = {
            doc_id
            for doc_id, run in self.versions
            if export_filename(
                name,
                str(run.job_analysis.get("role_title") or ""),
                run.generated_resume.headline,
                path.suffix[1:],
            )
            == path.name
        }
        if len(owners) != 1:
            raise ValueError(
                f"matches {len(owners)} migrated versions by name; it is left where it is"
            )
        doc_id = owners.pop()
        revision = self.store.list_revisions(doc_id)[-1]
        when = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")
        self.store.record_export(
            doc_id,
            revision.id,
            format=fmt,  # type: ignore[arg-type]
            template="legacy",
            file_path=str(path.resolve()),
            file_sha256=digest,
            engine=ENGINE,
            ats_check=NOT_CHECKED,
            now=when,
            export_id=export_id,
        )
        self.report.created.append(export_id)

    # -- one legacy resume -> one ResumeDocument -------------------------------
    def _document(
        self,
        gen: GeneratedResume,
        *,
        doc_id: str,
        kind: DocumentKind,
        title: str,
        language: str,
        target: dict[str, Any] | None,
        provenance: dict[str, Any],
        ids: defaultdict[str, str],
        used_model: bool,
    ) -> ResumeDocument:
        def cited(evidence: list[str]) -> list[str]:
            return [self.claims[e] for e in evidence if e in self.claims]

        def origin(b: LegacyBullet) -> str:
            if b.origin == "manual":
                return "USER_AUTHORED"
            if not cited(b.evidence_ids):
                return "IMPORTED"
            if b.origin == "verbatim":
                return "EVIDENCE_VERBATIM"
            return "AI_REWRITE" if used_model else "RULE_REWRITE"

        def bullet(b: LegacyBullet) -> dict[str, Any]:
            return {
                "id": ids[f"bullet:{b.id}"],
                "text": b.text,
                "origin": origin(b),
                "evidence_ids": cited(b.evidence_ids),
                "requirement_ids": list(b.requirement_ids),
            }

        summary_ids = [i for b in gen.summary for i in b.evidence_ids]
        summary_origin = "IMPORTED" if not cited(summary_ids) else origin(gen.summary[0])
        return upgrade_resume_document(
            {
                "schema_version": "1.0",
                "id": doc_id,
                "kind": kind.value,
                "title": title[:300],
                "language": language,
                "identity": self.identity.model_dump(mode="json"),
                "target": target,
                "headline": {
                    "id": ids["headline"],
                    "text": gen.headline,
                    "origin": "IMPORTED",
                }
                if gen.headline.strip()
                else None,
                "summary": {
                    "id": ids["summary"],
                    "text": " ".join(b.text for b in gen.summary),
                    "origin": "USER_AUTHORED"
                    if any(b.origin == "manual" for b in gen.summary)
                    else summary_origin,
                    "evidence_ids": cited(summary_ids),
                }
                if gen.summary
                else None,
                "experience": [
                    {
                        "id": ids[f"position:{e.position_id}"],
                        "employer": e.company,
                        "display_title": e.title,
                        "source_title": e.title,
                        "location": e.location or None,
                        "start": partial_date(e.start),
                        "end": partial_date(e.end),
                        "current": bool(e.start) and e.end is None,
                        "bullets": [bullet(b) for b in e.bullets],
                    }
                    for e in gen.experience
                ],
                "education": [
                    {
                        "id": ids[f"education:{e.id}"],
                        "institution": e.institution,
                        "degree": e.degree or None,
                        "start": partial_date(e.start),
                        "end": partial_date(e.end),
                    }
                    for e in gen.education
                ],
                "certifications": [
                    {"id": ids[f"cert:{c.id}"], "name": c.name, "issuer": c.issuer or None}
                    for c in gen.certifications
                ],
                "skills": [
                    {
                        "id": ids[f"skills:{n}:{g.name}"],
                        "name": g.name,
                        "items": [
                            {"id": ids[f"skill:{n}:{label}"], "label": label, "origin": "IMPORTED"}
                            for label in dict.fromkeys(g.items)
                        ],
                    }
                    for n, g in enumerate(gen.skills)
                    if g.items
                ],
                "provenance": provenance,
            }
        )


def _dicts(node: Any) -> Iterator[dict[str, Any]]:
    """Every dict in a dumped document, depth first."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _dicts(value)
    elif isinstance(node, list):
        for value in node:
            yield from _dicts(value)


def _mark_edits(generated: ResumeDocument, effective: ResumeDocument) -> ResumeDocument:
    """`effective` with each line the person changed marked as theirs (the
    generated wording kept as `original_text`) and each line they hid put
    back, hidden, where it was. Item ids are the generated ones."""
    before = generated.model_dump(mode="json")
    wording = {n["id"]: n["text"] for n in _dicts(before) if "origin" in n and "text" in n}
    data = effective.model_dump(mode="json")
    for node in _dicts(data):
        old = wording.get(node.get("id", ""))
        if old is not None and "origin" in node and node["text"] != old:
            node.update(origin="USER_AUTHORED", override="EDITED", original_text=old)
    shown = {e["id"]: e for e in data["experience"]}
    for entry in before["experience"]:
        kept = shown.get(entry["id"])
        if kept is None:
            continue
        ids = {b["id"] for b in kept["bullets"]}
        for position, bullet in enumerate(entry["bullets"]):
            if bullet["id"] not in ids:
                kept["bullets"].insert(
                    min(position, len(kept["bullets"])), {**bullet, "hidden": True}
                )
    return upgrade_resume_document(data)

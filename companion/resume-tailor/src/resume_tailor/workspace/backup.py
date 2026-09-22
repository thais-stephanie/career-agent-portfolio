# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Candidate backup: a versioned ZIP bundle a user can save and bring back.

The bundle contains ONLY the candidate's own material — profile, settings
(preferences, never secrets), evidence, confirmed details (overrides), target
profiles, base resumes, sources and application metadata. It never contains
API keys, ``.env`` files or machine-specific paths.

Import safety:

* the manifest's ``schema_version`` is checked (a newer version gives a
  human-readable error, nothing is written);
* every archive path is validated against a whitelist and rejected on absolute
  paths, drive letters or ``..`` segments (zip-slip);
* import never overwrites another candidate silently: "create as new candidate"
  is the default, and "replace" requires the target candidate's exact name;
* a duplicate display name imports as "<Name> (imported)".
"""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, datetime
from pathlib import PurePosixPath

from resume_tailor.workspace.migrations import CURRENT_SCHEMA, SchemaTooNew
from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceError, WorkspaceStore

FORMAT = "resume-tailor-candidate-backup"
_ALLOWED_PREFIXES = (
    "candidate.json",
    "settings.json",
    "profiles.json",
    "evidence/",
    "overrides/",
    "base_resumes/",
    "sources/",
    "applications/",
)
_SECRET_MARKERS = ("api_key", "apikey", "secret", "token", "password")


def backup_filename(candidate_name: str) -> str:
    from resume_tailor.export.exporters import _sanitize

    return f"{_sanitize(candidate_name) or 'Candidate'} - Resume Tailor Backup.zip"


def _clean_settings(settings: dict) -> dict:
    return {k: v for k, v in settings.items() if not any(m in k.lower() for m in _SECRET_MARKERS)}


def export_backup(
    ws: CandidateWorkspace, include_sources: bool = True, include_applications: bool = True
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        meta = ws.meta()
        manifest = {
            "format": FORMAT,
            "schema_version": CURRENT_SCHEMA,
            "candidate_name": meta.get("name", ""),
            "created_at": datetime.now(UTC).isoformat(),
        }
        z.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        z.writestr("candidate.json", json.dumps(meta, indent=2, ensure_ascii=False))
        z.writestr(
            "settings.json",
            json.dumps(_clean_settings(ws.settings()), indent=2, ensure_ascii=False),
        )
        for rel_dir, include in (
            ("evidence", True),
            ("overrides", True),
            ("base_resumes", True),
            ("sources", include_sources),
            ("applications", include_applications),
        ):
            if not include:
                continue
            root = ws.root / rel_dir
            if not root.exists():
                continue
            for p in sorted(root.rglob("*")):
                if p.is_file():
                    z.write(p, f"{rel_dir}/{p.relative_to(root).as_posix()}")
        if ws.profiles_file.exists():
            z.write(ws.profiles_file, "profiles.json")
    return buf.getvalue()


def _safe_member(name: str) -> str:
    """Validate one archive path; raises on traversal attempts."""
    pure = PurePosixPath(name.replace("\\", "/"))
    if pure.is_absolute() or any(part in ("..", "") for part in pure.parts) or ":" in name:
        raise WorkspaceError("This backup contains an unsafe file path and was not imported.")
    if name != "manifest.json" and not any(
        name == p or name.startswith(p) for p in _ALLOWED_PREFIXES
    ):
        raise WorkspaceError(
            f"This backup contains an unexpected file ({name}) and was not imported."
        )
    return name


def read_manifest(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            manifest = json.loads(z.read("manifest.json"))
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError) as e:
        raise WorkspaceError("This file does not look like a Resume Tailor backup.") from e
    if manifest.get("format") != FORMAT:
        raise WorkspaceError("This file does not look like a Resume Tailor backup.")
    if int(manifest.get("schema_version", 1)) > CURRENT_SCHEMA:
        raise SchemaTooNew(
            "This backup was made by a newer version of Resume Tailor. "
            "Please update Resume Tailor to import it. Nothing was changed."
        )
    return manifest


def import_backup(
    store: WorkspaceStore, data: bytes, replace_id: str | None = None, confirm_name: str = ""
) -> CandidateWorkspace:
    manifest = read_manifest(data)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            _safe_member(name)
        candidate = json.loads(z.read("candidate.json"))
        name = candidate.get("name") or manifest.get("candidate_name") or "Imported candidate"
        if replace_id is not None:
            ws = store.get(replace_id)
            if (confirm_name or "").strip() != ws.meta().get("name"):
                raise WorkspaceError("To replace a candidate, type their name exactly to confirm.")
            # wipe the replaceable content, keep the directory + id
            for rel in ("evidence", "overrides", "base_resumes", "sources", "applications"):
                target = ws.root / rel
                if target.exists():
                    import shutil

                    shutil.rmtree(target)
                target.mkdir(parents=True, exist_ok=True)
        else:
            existing = {m.get("name") for m in store.list_candidates(include_archived=True)}
            if name in existing:
                name = f"{name} (imported)"
            ws = store.create(name)
        for member in z.namelist():
            if member.endswith("/") or member == "manifest.json":
                continue
            target = ws.root / PurePosixPath(member)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(member))
        # the imported candidate keeps its NEW identity and display name
        meta = json.loads(ws.candidate_file.read_text(encoding="utf-8"))
        meta["id"] = ws.id
        meta["name"] = ws.meta().get("name", name) if replace_id else name
        meta["imported_at"] = datetime.now(UTC).isoformat()
        meta["schema_version"] = min(int(meta.get("schema_version", 1)), CURRENT_SCHEMA)
        ws.save_meta(meta)
        ws.invalidate()
    return ws

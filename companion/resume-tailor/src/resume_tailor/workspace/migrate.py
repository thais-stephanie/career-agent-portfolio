# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Migrate a repository-style ``data/`` directory into a local candidate workspace.

Used once to move the original private candidate out of the source tree, and by
tests to build workspaces from fixture data. The migration COPIES files verbatim —
evidence, overrides, base resumes, profiles — it never rewrites or "cleans" them,
so matching output over the migrated workspace is byte-for-byte driven by the same
inputs. Source data is left untouched; deleting it is a separate, human decision.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from resume_tailor.core.evidence.bank import load_bank
from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceError, WorkspaceStore


def migrate_data_dir(store: WorkspaceStore, data_dir: Path) -> CandidateWorkspace:
    """Copy ``data/evidence``, ``data/resumes`` and ``profiles.json`` into a new
    candidate workspace named after the bank's candidate. Idempotent by name: a
    second call for an already-migrated candidate raises instead of duplicating."""
    evidence = data_dir / "evidence" / "evidence_bank.json"
    if not evidence.exists():
        raise WorkspaceError(f"No evidence bank found under {data_dir}.")
    bank = load_bank(evidence)
    name = bank.candidate.name
    for meta in store.list_candidates(include_archived=True):
        if meta.get("name") == name and meta.get("migrated_from"):
            raise WorkspaceError(
                f"'{name}' was already migrated (candidate {meta['id']}). Delete that candidate first to migrate again."
            )
    ws = store.create(
        name,
        email=bank.candidate.email,
        phone=bank.candidate.phone,
        location=bank.candidate.location,
        linkedin=bank.candidate.linkedin,
        portfolio=bank.candidate.portfolio,
        languages=list(bank.candidate.languages),
    )
    shutil.copy(evidence, ws.evidence_file)
    overrides = evidence.with_name("user_overrides.json")
    if overrides.exists():
        ws.overrides_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(overrides, ws.overrides_file)
    profiles = data_dir / "profiles.json"
    if profiles.exists():
        shutil.copy(profiles, ws.profiles_file)
    for p in sorted((data_dir / "resumes").glob("*.json")):
        shutil.copy(p, ws.root / "base_resumes" / p.name)
    # a friendly source registry derived from the bank's declared sources (names only)
    registry = [
        {
            "id": key,
            "name": key.replace("_", " ").title(),
            "kind": _source_kind(key),
            "path": str(val),
            "added_at": datetime.now(UTC).isoformat(),
        }
        for key, val in (bank.sources or {}).items()
    ]
    ws.sources_registry.parent.mkdir(parents=True, exist_ok=True)
    ws.sources_registry.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    meta = ws.meta()
    meta["migrated_from"] = str(data_dir)
    ws.save_meta(meta)
    return ws


def _source_kind(key: str) -> str:
    low = key.lower()
    if "linkedin" in low:
        return "profile"
    if "portfolio" in low:
        return "portfolio"
    if "case" in low or "study" in low or "documentation" in low:
        return "case_study"
    if "resume" in low or "cv" in low:
        return "resume"
    if "verified" in low:
        return "confirmed"
    return "other"

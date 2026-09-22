# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Local candidate workspaces.

Every candidate owns one isolated directory under the application home
(``~/.resume-tailor`` by default, overridable with ``RESUME_TAILOR_HOME``):

    .resume-tailor/
      app_settings.json
      candidates/
        <internal-id>/
          candidate.json      profile + schema_version
          settings.json       per-candidate preferences (advanced mode, defaults)
          profiles.json       target-profile definitions the planner uses
          evidence/           evidence_bank.json
          overrides/          user_overrides.json (facts the candidate confirmed)
          sources/            uploaded source documents + sources.json registry
          base_resumes/       one JSON per base resume
          applications/       one directory per tailoring run (the RunStore root)
          exports/            files the user exported
          drafts/             editable resume drafts, one per application

Isolation rules:

* the internal id is a slug + random suffix, never shown to normal users;
* there is NO server-global "active candidate": every consumer must name the
  candidate id explicitly, and each ``CandidateWorkspace`` only ever reads and
  writes inside its own directory;
* per-candidate caches (the parsed evidence index, base resumes, profiles) are
  keyed by file modification times and invalidated per candidate only, so
  opening candidate B never reloads or mutates candidate A.

Schema versioning: ``candidate.json`` carries ``schema_version``. A newer,
unknown version raises a human-readable error instead of misreading data
(:mod:`resume_tailor.workspace.migrations`).
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex, load_index
from resume_tailor.core.models import BaseResume
from resume_tailor.storage.runs import RunStore
from resume_tailor.workspace.migrations import CURRENT_SCHEMA, check_schema

_ID = re.compile(r"^[a-z0-9][a-z0-9-]{1,80}$")


class WorkspaceError(Exception):
    """A human-readable workspace problem (safe to show in the UI)."""


def default_home() -> Path:
    env = os.environ.get("RESUME_TAILOR_HOME")
    return Path(env) if env else Path.home() / ".resume-tailor"


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "candidate"
    return s[:48]


def _now() -> str:
    return datetime.now(UTC).isoformat()


class CandidateWorkspace:
    """One candidate's isolated data directory. Never reads outside ``self.root``."""

    SUBDIRS = (
        "evidence",
        "overrides",
        "sources",
        "base_resumes",
        "applications",
        "exports",
        "drafts",
    )

    def __init__(self, root: Path):
        self.root = root
        self.id = root.name
        self._cache: dict[str, tuple[tuple, Any]] = {}

    # ------------------------------------------------------------------ paths
    @property
    def candidate_file(self) -> Path:
        return self.root / "candidate.json"

    @property
    def settings_file(self) -> Path:
        return self.root / "settings.json"

    @property
    def evidence_file(self) -> Path:
        return self.root / "evidence" / "evidence_bank.json"

    @property
    def overrides_file(self) -> Path:
        return self.root / "overrides" / "user_overrides.json"

    @property
    def profiles_file(self) -> Path:
        return self.root / "profiles.json"

    @property
    def sources_registry(self) -> Path:
        return self.root / "sources" / "sources.json"

    def run_store(self) -> RunStore:
        return RunStore(self.root / "applications")

    # ------------------------------------------------------------------- meta
    def meta(self) -> dict[str, Any]:
        data = json.loads(self.candidate_file.read_text(encoding="utf-8"))
        check_schema(data, self.candidate_file)
        return data

    def save_meta(self, data: dict[str, Any]) -> None:
        data["schema_version"] = data.get("schema_version", CURRENT_SCHEMA)
        data["updated_at"] = _now()
        self.candidate_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def settings(self) -> dict[str, Any]:
        if not self.settings_file.exists():
            return {"advanced": False, "theme": "light", "default_resume_id": ""}
        return json.loads(self.settings_file.read_text(encoding="utf-8"))

    def save_settings(self, data: dict[str, Any]) -> None:
        self.settings_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # ------------------------------------------------------------------ cache
    def _cached(self, key: str, files: list[Path], build):
        stamp = tuple((str(f), f.stat().st_mtime_ns if f.exists() else -1) for f in files)
        hit = self._cache.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        value = build()
        self._cache[key] = (stamp, value)
        return value

    def invalidate(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------- data
    def load_index(self) -> EvidenceIndex:
        """The candidate's evidence index (overrides applied in memory), cached until
        the evidence or overrides file changes."""
        if not self.evidence_file.exists():
            raise WorkspaceError(
                "This candidate has no experience data yet. Add a resume or a source first."
            )
        return self._cached(
            "index",
            [self.evidence_file, self.overrides_file],
            lambda: load_index(
                self.evidence_file, self.overrides_file if self.overrides_file.exists() else None
            ),
        )

    def load_resumes(self) -> dict[str, BaseResume]:
        files = sorted((self.root / "base_resumes").glob("*.json"))

        def build() -> dict[str, BaseResume]:
            out: dict[str, BaseResume] = {}
            for p in files:
                r = BaseResume.model_validate_json(p.read_text(encoding="utf-8"))
                out[r.id] = r
            return out

        return self._cached("resumes", files or [self.root / "base_resumes"], build)

    def load_profiles(self) -> dict[str, Any]:
        if not self.profiles_file.exists():
            return {}
        return self._cached(
            "profiles",
            [self.profiles_file],
            lambda: json.loads(self.profiles_file.read_text(encoding="utf-8")),
        )

    def save_base_resume(self, resume: BaseResume) -> None:
        (self.root / "base_resumes" / f"{resume.id}.json").write_text(
            resume.model_dump_json(indent=2), encoding="utf-8"
        )
        self.invalidate()

    def write_evidence(self, bank_json: dict[str, Any]) -> None:
        self.evidence_file.parent.mkdir(parents=True, exist_ok=True)
        self.evidence_file.write_text(
            json.dumps(bank_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.invalidate()

    def write_overrides(self, overrides_json: dict[str, Any]) -> None:
        self.overrides_file.parent.mkdir(parents=True, exist_ok=True)
        self.overrides_file.write_text(
            json.dumps(overrides_json, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.invalidate()


class WorkspaceStore:
    """The application home: candidate directories plus app-level settings.

    There is no ambient "current candidate" here — callers pass the candidate id for
    every operation, and the store hands back that candidate's isolated workspace."""

    def __init__(self, home: Path | None = None):
        self.home = home or default_home()
        (self.home / "candidates").mkdir(parents=True, exist_ok=True)
        self._workspaces: dict[str, CandidateWorkspace] = {}

    # ------------------------------------------------------------ app settings
    @property
    def app_settings_file(self) -> Path:
        return self.home / "app_settings.json"

    def app_settings(self) -> dict[str, Any]:
        if not self.app_settings_file.exists():
            return {"schema_version": CURRENT_SCHEMA, "last_selected_candidate": ""}
        return json.loads(self.app_settings_file.read_text(encoding="utf-8"))

    def save_app_settings(self, data: dict[str, Any]) -> None:
        data.setdefault("schema_version", CURRENT_SCHEMA)
        self.app_settings_file.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    # -------------------------------------------------------------- candidates
    def candidate_ids(self) -> list[str]:
        return sorted(
            p.name for p in (self.home / "candidates").iterdir() if (p / "candidate.json").exists()
        )

    def list_candidates(self, include_archived: bool = False) -> list[dict[str, Any]]:
        out = []
        for cid in self.candidate_ids():
            meta = self.get(cid).meta()
            if meta.get("archived") and not include_archived:
                continue
            out.append(meta)
        return out

    def get(self, candidate_id: str) -> CandidateWorkspace:
        if not _ID.match(candidate_id or ""):
            raise WorkspaceError("Unknown candidate.")
        root = self.home / "candidates" / candidate_id
        if not (root / "candidate.json").exists():
            raise WorkspaceError("Unknown candidate.")
        ws = self._workspaces.get(candidate_id)
        if ws is None:
            ws = self._workspaces[candidate_id] = CandidateWorkspace(root)
        return ws

    def create(self, name: str, **profile: Any) -> CandidateWorkspace:
        name = (name or "").strip()
        if not name:
            raise WorkspaceError("A candidate needs a name.")
        cid = f"{_slug(name)}-{secrets.token_hex(3)}"
        root = self.home / "candidates" / cid
        for sub in CandidateWorkspace.SUBDIRS:
            (root / sub).mkdir(parents=True, exist_ok=True)
        ws = CandidateWorkspace(root)
        ws.save_meta(
            {
                "schema_version": CURRENT_SCHEMA,
                "id": cid,
                "name": name,
                "email": profile.get("email", ""),
                "phone": profile.get("phone", ""),
                "location": profile.get("location", ""),
                "linkedin": profile.get("linkedin", ""),
                "portfolio": profile.get("portfolio", ""),
                "languages": profile.get("languages", []),
                "archived": False,
                "created_at": _now(),
            }
        )
        ws.save_settings({"advanced": False, "theme": "light", "default_resume_id": ""})
        self._workspaces[cid] = ws
        return ws

    def rename(self, candidate_id: str, name: str) -> None:
        name = (name or "").strip()
        if not name:
            raise WorkspaceError("A candidate needs a name.")
        ws = self.get(candidate_id)
        meta = ws.meta()
        meta["name"] = name
        ws.save_meta(meta)

    def set_archived(self, candidate_id: str, archived: bool) -> None:
        ws = self.get(candidate_id)
        meta = ws.meta()
        meta["archived"] = bool(archived)
        ws.save_meta(meta)

    def delete(self, candidate_id: str, confirm_name: str) -> None:
        """Permanent deletion requires typing the candidate's current name."""
        ws = self.get(candidate_id)
        meta = ws.meta()
        if (confirm_name or "").strip() != meta.get("name"):
            raise WorkspaceError("To delete a candidate, type their name exactly to confirm.")
        self._workspaces.pop(candidate_id, None)
        shutil.rmtree(ws.root)

    def default_candidate_id(self) -> str | None:
        """The candidate legacy (non-scoped) API routes resolve to: the remembered
        selection when it still exists, else the only candidate, else None. Never a guess
        between several candidates."""
        remembered = self.app_settings().get("last_selected_candidate", "")
        ids = [c["id"] for c in self.list_candidates()]
        if remembered in ids:
            return remembered
        if len(ids) == 1:
            return ids[0]
        return None

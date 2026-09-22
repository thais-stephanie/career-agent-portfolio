"""Where things live on disk. Overridable with RESUME_TAILOR_DATA."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex, load_index
from resume_tailor.core.models import BaseResume

PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def data_dir() -> Path:
    return Path(os.environ.get("RESUME_TAILOR_DATA") or PACKAGE_ROOT / "data")


def evidence_path() -> Path:
    return data_dir() / "evidence" / "evidence_bank.json"


def runs_dir() -> Path:
    return data_dir() / "runs"


def cache_dir() -> Path:
    return data_dir() / "cache"


def load_profiles() -> dict[str, Any]:
    return json.loads((data_dir() / "profiles.json").read_text(encoding="utf-8"))


def load_resumes() -> dict[str, BaseResume]:
    out: dict[str, BaseResume] = {}
    for p in sorted((data_dir() / "resumes").glob("*.json")):
        r = BaseResume.model_validate_json(p.read_text(encoding="utf-8"))
        out[r.id] = r
    return out


def load_evidence_index() -> EvidenceIndex:
    return load_index(evidence_path())

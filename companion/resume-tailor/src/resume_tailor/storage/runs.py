# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Run persistence: one directory per run under ``data/runs/<run_id>/`` with
the intermediate artifacts as separate JSON files so they can be inspected
and diffed by hand:

    request.json, parsed_job.json, evidence_matches.json, resume_strategy.json,
    generated_resume.json, claim_evidence_map.json, validation_report.json,
    lint_report.json, diff_report.json, run.json (everything), status.json
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from resume_tailor.core.models import TailorRun

ARTIFACTS = {
    "request.json": "request",
    "parsed_job.json": "job_analysis",
    "evidence_matches.json": "evidence_matches",
    "resume_strategy.json": "resume_strategy",
    "generated_resume.json": "generated_resume",
    "claim_evidence_map.json": "claim_evidence_map",
    "validation_report.json": "validation_report",
    "lint_report.json": "lint_report",
    "diff_report.json": "diff_report",
}

_RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}-[a-f0-9]{6}$")


def new_run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + secrets.token_hex(3)


class RunStore:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, run_id: str) -> Path:
        if not _RUN_ID.match(run_id):
            raise ValueError("invalid run id")
        return self.root / run_id

    def set_status(self, run_id: str, status: str, stage: str = "", error: str = "") -> None:
        # written atomically: the worker thread updates the status on every pipeline
        # stage while the API thread polls it, and a plain truncate-write would let a
        # concurrent reader see an empty or half-written file
        d = self._dir(run_id)
        d.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {
                "run_id": run_id,
                "status": status,
                "stage": stage,
                "error": error,
                "updated_at": datetime.now(UTC).isoformat(),
            }
        )
        tmp = d / f"status.json.{os.getpid()}-{threading.get_ident()}.tmp"
        tmp.write_text(payload, encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, d / "status.json")
                return
            except PermissionError:
                # Windows: replace can transiently fail while a reader holds the file
                if attempt == 4:
                    tmp.unlink(missing_ok=True)
                    raise
                time.sleep(0.02)

    def status(self, run_id: str) -> dict[str, Any] | None:
        p = self._dir(run_id) / "status.json"
        if not p.exists():
            return None
        last: Exception | None = None
        for _ in range(5):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                # transient read race (writer mid-replace, antivirus scan); retry
                # briefly, then surface the real error rather than hide it
                last = e
                time.sleep(0.02)
        raise last  # type: ignore[misc]

    def save(self, run: TailorRun) -> Path:
        d = self._dir(run.run_id)
        d.mkdir(parents=True, exist_ok=True)
        payload = run.model_dump(mode="json")
        for fname, key in ARTIFACTS.items():
            (d / fname).write_text(
                json.dumps(payload[key], indent=2, ensure_ascii=False), encoding="utf-8"
            )
        (d / "run.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        self.set_status(run.run_id, "done", "complete")
        return d

    def load(self, run_id: str) -> TailorRun | None:
        p = self._dir(run_id) / "run.json"
        if not p.exists():
            return None
        return TailorRun.model_validate_json(p.read_text(encoding="utf-8"))

    def list(self) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir(), reverse=True):
            if not d.is_dir() or not _RUN_ID.match(d.name):
                continue
            st = self.status(d.name) or {}
            row = {
                "run_id": d.name,
                "status": st.get("status", "unknown"),
                "stage": st.get("stage", ""),
                "created_at": st.get("updated_at", ""),
            }
            rp = d / "request.json"
            if rp.exists():
                try:
                    r = json.loads(rp.read_text(encoding="utf-8"))
                    row["resume_id"] = r.get("resume_id")
                    row["jd_preview"] = (r.get("jd_text") or "")[:80]
                except json.JSONDecodeError:
                    pass
            jp = d / "parsed_job.json"
            if jp.exists():
                try:
                    row["role_title"] = json.loads(jp.read_text(encoding="utf-8")).get(
                        "role_title", ""
                    )
                except json.JSONDecodeError:
                    pass
            mp = d / "evidence_matches.json"
            if mp.exists():
                try:
                    row["overall_coverage"] = (
                        json.loads(mp.read_text(encoding="utf-8"))
                        .get("coverage", {})
                        .get("overall_coverage")
                    )
                except json.JSONDecodeError:
                    pass
            out.append(row)
        return out

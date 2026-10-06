"""The frozen Resume helper workspace, and how a test reads what moving it wrote.

`tests/fixtures/legacy_resume_helper/` is a synthetic workspace exactly as the
retired Resume helper (v0.2.0-beta.2) wrote one: built once with that engine,
then frozen. Nothing here runs the old engine; the files are the contract.
Nothing in it is anybody's real career, job ad or resume.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "legacy_resume_helper"
#: The facts the frozen workspace was built from (its job ad, its label, and
#: the Career Evidence statements its lines cite).
FACTS: dict[str, Any] = json.loads((FIXTURE / "facts.json").read_text("utf-8"))
LABEL: str = FACTS["label"]
JOB_TITLE: str = FACTS["job_title"]
JOB_AD: str = FACTS["job_ad"]
PROFILE_ID: str = FACTS["profile_id"]
#: The candidate folder's name inside the frozen Tailor home.
CANDIDATE: str = PROFILE_ID.lower()


def workspace_copy(destination: Path) -> Path:
    """A writable copy of the frozen Tailor home; returns its candidate folder."""
    shutil.copytree(FIXTURE / "tailor", destination)
    return destination / "candidates" / CANDIDATE


def profile(
    tmp_path: Path, name: str = "p", display_name: str = "Riley Synthetic", *, claims: bool = True
) -> sqlite3.Connection:
    """A profile whose Career Evidence still holds the confirmed statements the
    old workspace cites (`claims=False`: none of them)."""
    conn = connect(tmp_path / name / "personal.db")
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, LABEL)
        candidate = ensure_candidate(conn)
        conn.execute(
            "UPDATE candidate SET display_name = ? WHERE id = ?", (display_name, candidate)
        )
        for claim in FACTS["claims"] if claims else []:
            ClaimRepo(conn).add(
                candidate,
                VerifiedClaim(
                    claim_key=claim["key"],
                    claim_type=ClaimType.EMPLOYMENT,
                    text=claim["text"],
                    source=ClaimSource.SELF_ATTESTED,
                    verified=True,
                ),
            )
    return conn


_TABLES = ("resume_document", "resume_revision", "jd_snapshot", "tailoring_run", "resume_export")
_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
#: Volatile: when this run happened, and hashes over random item ids.
_DROP = re.compile(r"(_at|^content_sha256|^working_sha256|^sha256)$")


def dump(conn: sqlite3.Connection, root: Path) -> dict[str, Any]:
    """Every row a migration wrote, comparable across runs: random ids become
    tokens in order of first appearance, `root` becomes `<ROOT>`, and times
    and hashes over random ids are dropped. Ids derived from the legacy files
    (`stable_id`) are random-looking too and are tokenised the same way, so a
    change in WHICH rows exist or how they link still shows."""
    tokens: dict[str, str] = {}
    prefix = str(root.resolve())

    def norm(value: Any, key: str = "") -> Any:
        if isinstance(value, dict):
            return {k: norm(v, k) for k, v in sorted(value.items()) if not _DROP.search(k)}
        if isinstance(value, list):
            return [norm(v) for v in value]
        if isinstance(value, str):
            if value[:1] in "{[":
                try:
                    return norm(json.loads(value), key)
                except ValueError:
                    pass
            value = value.replace(prefix, "<ROOT>").replace("\\", "/")
            return _ULID.sub(lambda m: tokens.setdefault(m[0], f"<id{len(tokens)}>"), value)
        return value

    out: dict[str, Any] = {}
    for table in _TABLES:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY rowid")  # noqa: S608
        names = [d[0] for d in rows.description]
        out[table] = [norm(dict(zip(names, row, strict=True))) for row in rows]
    return out


def digest(rows: dict[str, Any]) -> dict[str, list[str]]:
    """`dump`, one sha256 per row: small enough to freeze, and a mismatch
    still names the table and the row."""
    return {
        table: [
            hashlib.sha256(json.dumps(r, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            for r in found
        ]
        for table, found in rows.items()
    }

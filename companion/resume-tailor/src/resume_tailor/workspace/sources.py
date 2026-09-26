# Modified for the Career Agent public edition (2026-09-26). See NOTICE.
"""Sources and experience details inside a candidate workspace.

A *source* is a document the candidate added (resume, LinkedIn PDF, project
documentation, portfolio, other). Each source keeps:

* a registry entry (``sources/sources.json``) with friendly metadata;
* the original file (``sources/files/<id>.<ext>``);
* extracted DETAIL SUGGESTIONS (``sources/<id>.suggestions.json``) awaiting review.

Evidence safety, by construction:

* adding or re-processing a source NEVER touches the evidence bank;
* a suggestion enters the bank only through :func:`accept_suggestion`, as ordinary
  resume/document-sourced (secondary) evidence — never as confirmed fact;
* removing a source that still backs experience details requires ``force`` and even
  then keeps user-confirmed details, warning about everything affected; details
  left with no source are excluded from resumes rather than silently deleted.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from resume_tailor.importing.resume_parse import parse_resume
from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceError

_DATE = re.compile(r"^(19|20)\d{2}-(0[1-9]|1[0-2])$")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _slug(name: str) -> str:
    return (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "source")[:40]


def read_registry(ws: CandidateWorkspace) -> list[dict[str, Any]]:
    if not ws.sources_registry.exists():
        return []
    return json.loads(ws.sources_registry.read_text(encoding="utf-8"))


def write_registry(ws: CandidateWorkspace, registry: list[dict[str, Any]]) -> None:
    ws.sources_registry.parent.mkdir(parents=True, exist_ok=True)
    ws.sources_registry.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def _bank_json(ws: CandidateWorkspace) -> dict[str, Any]:
    if not ws.evidence_file.exists():
        return {
            "candidate": {"name": ws.meta().get("name", "")},
            "positions": [],
            "records": [],
            "sources": {},
            "certifications": [],
            "conflicts": [],
            "overrides": [],
        }
    return json.loads(ws.evidence_file.read_text(encoding="utf-8"))


def add_source(
    ws: CandidateWorkspace, filename: str, data: bytes, kind: str, name: str = ""
) -> dict[str, Any]:
    """Register a document and extract suggestions. The evidence bank is untouched.

    The same document again is the source already registered (returned with
    `already`), never a second copy. The id carries the content's hash: a
    count-based id reused a removed source's id and overwrote its file."""
    from resume_tailor.importing.upload_check import content_hash

    digest = content_hash(data)
    for known in read_registry(ws):
        if known.get("sha256") == digest:
            return {**known, "already": True}
    parsed = parse_resume(filename, data)
    base = _slug(name or filename.rsplit(".", 1)[0])
    sid = f"{base}-{digest[:10]}"
    files = ws.root / "sources" / "files"
    files.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix or ".bin"
    (files / f"{sid}{ext}").write_bytes(data)
    taken = {e.get("name") for e in read_registry(ws)}
    label = name or Path(filename).name
    n = 2
    shown = label
    while shown in taken:
        shown = f"{label} ({n})"
        n += 1
    entry = {
        "id": sid,
        "sha256": digest,
        "name": shown,
        "kind": kind,
        "file": f"files/{sid}{ext}",
        "added_at": _now(),
        "detail_count": 0,
        "extracted": parsed.summary_counts(),
    }
    registry = read_registry(ws)
    registry.append(entry)
    write_registry(ws, registry)
    suggestions = [
        {
            "n": i,
            "text": d.text,
            "company": d.company,
            "title": d.title,
            "start": d.start,
            "end": d.end,
            "state": "pending",
        }
        for i, d in enumerate(parsed.details)
    ]
    (ws.root / "sources" / f"{sid}.suggestions.json").write_text(
        json.dumps(suggestions, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return entry


def suggestions(ws: CandidateWorkspace, source_id: str) -> list[dict[str, Any]]:
    p = ws.root / "sources" / f"{source_id}.suggestions.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def accept_suggestion(
    ws: CandidateWorkspace,
    source_id: str,
    n: int,
    company: str = "",
    title: str = "",
    start: str = "",
    end: str | None = None,
) -> str:
    """Turn one reviewed suggestion into an ordinary evidence record backed by this
    source. It enters at resume-document strength — imported wording is NEVER marked
    as confirmed by the user."""
    items = suggestions(ws, source_id)
    row = next((s for s in items if s["n"] == n), None)
    if row is None:
        raise WorkspaceError("Unknown detail.")
    company = (company or row.get("company") or "").strip()
    title = (title or row.get("title") or "").strip()
    start = (start or row.get("start") or "").strip()
    end = end if end is not None else row.get("end")
    if not company or not title:
        raise WorkspaceError(
            "This detail needs the company and your role title before it can be added."
        )
    if not _DATE.match(start):
        raise WorkspaceError(
            "This detail needs a start month (like 2023-04) before it can be added."
        )
    if end is not None and not _DATE.match(str(end)):
        raise WorkspaceError(
            "The end month should look like 2026-01, or be empty for a current role."
        )
    bank = _bank_json(ws)
    bank.setdefault("sources", {})[source_id] = next(
        (s["file"] for s in read_registry(ws) if s["id"] == source_id), source_id
    )
    pos_id = next(
        (
            p["id"]
            for p in bank["positions"]
            if p["company"].lower() == company.lower() and p["title"].lower() == title.lower()
        ),
        None,
    )
    if pos_id is None:
        pos_id = f"{_slug(company)}-{_slug(title)}"[:60] or f"pos-{len(bank['positions']) + 1}"
        if any(p["id"] == pos_id for p in bank["positions"]):
            pos_id = f"{pos_id}-{len(bank['positions']) + 1}"
        bank["positions"].append(
            {
                "id": pos_id,
                "company": company,
                "title": title,
                "location": "",
                "start": start,
                "end": end,
                "kind": "employment",
                "include_by_default": True,
            }
        )
    rid = f"{source_id}-{n:03d}"
    if any(r["id"] == rid for r in bank["records"]):
        raise WorkspaceError("This detail was already added.")
    bank["records"].append(
        {
            "id": rid,
            "position_id": pos_id,
            "company": company,
            "role": title,
            "start": start,
            "end": end,
            "claim": row["text"],
            "resume_text": row["text"],
            "proficiency": "hands_on",
            "sources": [source_id],
            "source_file": source_id,
            "verification": "derived",
            "include_by_default": True,
        }
    )
    ws.write_evidence(bank)
    row["state"] = "accepted"
    (ws.root / "sources" / f"{source_id}.suggestions.json").write_text(
        json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    registry = read_registry(ws)
    for entry in registry:
        if entry["id"] == source_id:
            entry["detail_count"] = entry.get("detail_count", 0) + 1
    write_registry(ws, registry)
    return rid


def removal_impact(ws: CandidateWorkspace, source_id: str) -> dict[str, Any]:
    """What removing this source would affect, in plain language."""
    bank = _bank_json(ws)
    backed = [r for r in bank["records"] if source_id in (r.get("sources") or [])]
    confirmed = [
        r
        for r in backed
        if "user_verified" in (r.get("sources") or []) or r.get("verification") == "user_verified"
    ]
    orphaned = [
        r for r in backed if len([s for s in (r.get("sources") or []) if s != source_id]) == 0
    ]
    return {
        "details_backed": len(backed),
        "confirmed_details_kept": len(confirmed),
        "details_that_would_be_left_out": len([r for r in orphaned if r not in confirmed]),
    }


def remove_source(ws: CandidateWorkspace, source_id: str, force: bool = False) -> dict[str, Any]:
    registry = read_registry(ws)
    if not any(s["id"] == source_id for s in registry):
        raise WorkspaceError("Unknown source.")
    impact = removal_impact(ws, source_id)
    if impact["details_backed"] and not force:
        raise WorkspaceError(
            f"{impact['details_backed']} experience detail(s) came from this source. "
            "Removing it will leave them without this backing. Confirm to continue."
        )
    bank = _bank_json(ws)
    for r in bank["records"]:
        srcs = r.get("sources") or []
        if source_id in srcs:
            r["sources"] = [s for s in srcs if s != source_id]
            keeps_backing = bool(r["sources"]) or r.get("verification") == "user_verified"
            if not keeps_backing:
                r["include_by_default"] = False  # left out of resumes, never silently deleted
    bank.get("sources", {}).pop(source_id, None)
    ws.write_evidence(bank)
    write_registry(ws, [s for s in registry if s["id"] != source_id])
    sfile = ws.root / "sources" / f"{source_id}.suggestions.json"
    if sfile.exists():
        sfile.unlink()
    return impact

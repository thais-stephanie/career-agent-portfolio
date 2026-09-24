"""A CV read, organised as jobs: the payload, the lifecycle and the corrections.

Routes, all under `/api/cv/imports/<id>`:

    POST archive    put the read away. Reversible; every row stays.
    POST restore    bring it back, exactly as it was.
    POST delete     remove it for good. Without `confirm` it only answers
                    what WOULD be removed and kept, and changes nothing.
    POST organize   correct the structure: edit a job, create one, move
                    suggestions, merge two jobs, split a job, delete an
                    empty job, reject or delete suggestions in a batch.

WHAT NO ROUTE HERE DOES: confirm more than one suggestion. There is no
"accept all" and no batch confirmation, because every confirmed fact can
reach a real application and one click cannot honestly mean somebody read
142 sentences. Batch actions organise, reject or delete -- none of which
creates evidence.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from career_agent.web.server import ApiError
from career_agent.web.server import closing as _closing

#: A month, as a claim stores it.
_MONTH = re.compile(r"^(19|20)\d{2}-(0[1-9]|1[0-2])$")
#: Sections whose suggestions belong to a job.
_JOB_SECTIONS = frozenset({"experience", "volunteering", "internships", "freelance"})
#: The most suggestions one organising request may touch. A long CV is a few
#: hundred; this stops a malformed client, not a person.
_MAX_KEYS = 500


def _col(row: sqlite3.Row, name: str) -> Any:
    # `in row` would test the VALUES of an sqlite3.Row; the names are keys().
    return row[name] if name in row.keys() else None  # noqa: SIM118


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.casefold()).strip()


def entry_payload(row: sqlite3.Row) -> dict:
    return {
        "entry_key": str(row["entry_key"]),
        "section": str(row["section"]),
        "company": row["company"],
        "role_title": row["role_title"],
        "period_text": row["period_text"],
        "period_start": row["period_start"],
        "period_end": row["period_end"],
        "current_role": bool(row["current_role"]),
        "start_year": row["start_year"],
        "end_year": row["end_year"],
        "location": row["location"],
        "label": row["label"],
        # The heading and date lines that stated this job, verbatim. Text for
        # a person to read, never markup to render.
        "source": json.loads(row["source_json"] or "[]"),
        "unresolved": json.loads(row["unresolved_json"] or "[]"),
        "edited": bool(row["edited"]),
    }


def chronological(entries: list[dict]) -> list[dict]:
    """Newest first, from the dates alone. Undated last, in document order.

    The order is a function of the dates and the document, never of a number
    somebody typed: `display_order` is not a concept a person should manage.
    """
    from career_agent.cv.structure import chronology_key

    def key(entry: dict) -> tuple:
        return (
            *chronology_key(
                entry["period_start"],
                entry["start_year"],
                entry["period_end"],
                entry["end_year"],
                entry["current_role"],
            ),
            entry.get("ordinal", 0),
            entry["entry_key"],
        )

    return sorted(entries, key=key)


def cv_review_payload(
    record: sqlite3.Row, rows: list[sqlite3.Row], entry_rows: list[sqlite3.Row]
) -> dict:
    """The review of one read, summary first. See `WorkspaceRoutes.cv_review`."""
    from career_agent.web.workspace_api import proposal_payload

    proposals = [proposal_payload(row) for row in rows]
    # A duplicate is the same sentence twice in one read. The later one is
    # flagged, never removed: the person decides which to keep.
    seen: dict[str, str] = {}
    for item in proposals:
        norm = _norm(item["text"])
        if norm in seen:
            item["duplicate_of"] = seen[norm]
        else:
            seen[norm] = item["claim_key"]
            item["duplicate_of"] = None

    entries = []
    known = set()
    for ordinal, row in enumerate(entry_rows):
        entry = entry_payload(row)
        entry["ordinal"] = ordinal
        known.add(entry["entry_key"])
        entries.append(entry)
    by_entry: dict[str | None, list[dict]] = {}
    for item in proposals:
        owner = item["entry_key"] if item["entry_key"] in known else None
        item["entry_key"] = owner
        by_entry.setdefault(owner, []).append(item)

    def tally(items: list[dict]) -> dict[str, int]:
        return {
            "total": len(items),
            "waiting": sum(i["decision"] == "PENDING" for i in items),
            "confirmed": sum(i["decision"] in {"ACCEPTED", "EDITED"} for i in items),
            "rejected": sum(i["decision"] == "REJECTED" for i in items),
        }

    def attention(item: dict, entry: dict | None) -> list[str]:
        if item["decision"] != "PENDING":
            return []
        reasons = []
        if item["duplicate_of"]:
            reasons.append("duplicate")
        if entry is not None and entry["unresolved"]:
            reasons.append("structure")
        if entry is None and item["section"] in _JOB_SECTIONS:
            reasons.append("unplaced")
        return reasons

    for entry in entries:
        items = by_entry.get(entry["entry_key"], [])
        for item in items:
            item["attention"] = attention(item, entry)
        entry.update(tally(items))
        entry["attention"] = sum(bool(i["attention"]) for i in items) or (
            1 if entry["unresolved"] and not items else 0
        )
        entry["proposals"] = items
    entries = chronological(entries)

    # Suggestions that belong to no job: skills, education, certifications.
    # Grouped by section, and a job-section suggestion with no job (a read
    # from before jobs existed) is its own group, so nothing is hidden.
    sections: dict[str, list[dict]] = {}
    for item in by_entry.get(None, []):
        item["attention"] = attention(item, None)
        group = "unplaced" if item["section"] in _JOB_SECTIONS else item["section"]
        sections.setdefault(group, []).append(item)
    groups = [
        {
            "group": name,
            **tally(items),
            "attention": sum(bool(i["attention"]) for i in items),
            "proposals": items,
        }
        for name, items in sections.items()
    ]

    decided = {"ACCEPTED": 0, "EDITED": 0, "REJECTED": 0, "PENDING": 0}
    for item in proposals:
        decided[item["decision"]] += 1
    attention_total = sum(bool(i["attention"]) for i in proposals)
    return {
        "import_id": str(record["id"]),
        "source_name": str(record["source_name"]),
        "kind": str(record["kind"]),
        "pages": record["pages"],
        "characters": int(record["characters"]),
        "status": str(record["status"]),
        "created_at": str(record["created_at"]),
        "archived": _col(record, "archived_at") is not None,
        "archived_at": _col(record, "archived_at"),
        "summary": {
            "experiences": sum(e["section"] in _JOB_SECTIONS for e in entries),
            "suggestions": len(proposals),
            "waiting": decided["PENDING"],
            "attention": attention_total,
            "confirmed": decided["ACCEPTED"] + decided["EDITED"],
            "rejected": decided["REJECTED"],
        },
        "entries": entries,
        "sections": groups,
        # Kept for clients that read the flat shape: every proposal, by type.
        "groups": _by_type(proposals),
        "counts": decided,
        "total": len(proposals),
        "seen_before": [],
        "unread_lines": 0,
    }


def _by_type(proposals: list[dict]) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    for item in proposals:
        groups.setdefault(item["claim_type"], []).append(item)
    return [{"claim_type": kind, "proposals": items} for kind, items in groups.items()]


# =========================================================================
# validation
# =========================================================================


def _text_field(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(400, f"{name} must be text")
    value = " ".join(value.split())
    if len(value) > 200:
        raise ApiError(400, f"{name} is longer than 200 characters")
    return value or None


def entry_fields(raw: Any) -> dict:
    """What a person may set on a job, checked. Unknown keys are refused."""
    if not isinstance(raw, dict):
        raise ApiError(400, "fields must be an object")
    allowed = {"company", "role_title", "period_start", "period_end", "current_role"}
    unknown = set(raw) - allowed
    if unknown:
        raise ApiError(400, f"unknown fields: {', '.join(sorted(unknown))}")
    fields: dict[str, Any] = {}
    for name in ("company", "role_title"):
        if name in raw:
            fields[name] = _text_field(raw[name], name)
    for name in ("period_start", "period_end"):
        if name in raw:
            value = raw[name] or None
            if value is not None and (not isinstance(value, str) or not _MONTH.match(value)):
                raise ApiError(400, f"{name} must be a month, YYYY-MM", for_reader=True)
            fields[name] = value
    if "current_role" in raw:
        fields["current_role"] = bool(raw["current_role"])
        if fields["current_role"]:
            fields["period_end"] = None
    start, end = fields.get("period_start"), fields.get("period_end")
    if start and end and end < start:
        raise ApiError(400, "The end date is before the start date.", for_reader=True)
    if end and "period_start" in fields and not start:
        raise ApiError(400, "An end date needs a start date.", for_reader=True)
    return fields


def _keys(body: dict) -> list[str]:
    raw = body.get("keys")
    if not isinstance(raw, list) or not raw:
        raise ApiError(400, "keys must be a non-empty list")
    if len(raw) > _MAX_KEYS:
        raise ApiError(400, f"at most {_MAX_KEYS} suggestions at a time")
    keys = [str(k) for k in raw if isinstance(k, str) and k]
    if len(keys) != len(raw):
        raise ApiError(400, "keys must be strings")
    return list(dict.fromkeys(keys))


# =========================================================================
# routes
# =========================================================================


def register_cv_routes(app: Any) -> None:
    base = r"/api/cv/imports/(?P<import_id>[^/]+)"

    def owned(conn: sqlite3.Connection, import_id: str) -> tuple[Any, str, str]:
        from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of
        from career_agent.web.workspace_api import _claim_key

        candidate_id = candidate_id_of(conn)
        key = _claim_key(import_id)
        if candidate_id is None:
            raise ApiError(404, "no such import")
        repo = CvReviewRepo(conn)
        if repo.get_import(candidate_id, key) is None:
            raise ApiError(404, "no such import")
        return repo, candidate_id, key

    def archive(*, import_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.db import transaction

        with _closing(app.connect()) as conn:
            repo, candidate_id, key = owned(conn, import_id)
            with transaction(conn):
                repo.archive(candidate_id, key)
        return dict(app.cv_imports(query={}, body={}))

    def restore(*, import_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.db import transaction

        with _closing(app.connect()) as conn:
            repo, candidate_id, key = owned(conn, import_id)
            with transaction(conn):
                repo.restore(candidate_id, key)
        return dict(app.cv_imports(query={}, body={}))

    def delete(*, import_id: str, query: dict, body: dict) -> dict:
        """Without `confirm: true`, only the plan. With it, the deletion."""
        from career_agent.storage.db import transaction

        with _closing(app.connect()) as conn:
            repo, candidate_id, key = owned(conn, import_id)
            if body.get("confirm") is not True:
                return {"deleted": False, "plan": repo.delete_plan(candidate_id, key)}
            with transaction(conn):
                plan = repo.delete(candidate_id, key)
        return {"deleted": True, "plan": plan, **app.cv_imports(query={}, body={})}

    def organize(*, import_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.db import transaction

        action = body.get("action")
        with _closing(app.connect()) as conn:
            repo, candidate_id, key = owned(conn, import_id)
            record = repo.get_import(candidate_id, key)
            if record["archived_at"] is not None:
                raise ApiError(
                    409,
                    "This CV read is archived. Restore it before changing it.",
                    for_reader=True,
                )
            result: dict[str, Any] = {}
            try:
                with transaction(conn):
                    if action == "edit_entry":
                        if not repo.update_entry(
                            key, str(body.get("entry_key") or ""), entry_fields(body.get("fields"))
                        ):
                            raise ApiError(404, "no such experience")
                    elif action == "create_entry":
                        result["entry_key"] = repo.create_entry(
                            key, entry_fields(body.get("fields") or {})
                        )
                    elif action == "move":
                        target = body.get("entry_key") or None
                        result["moved"] = repo.move(key, _keys(body), target)
                    elif action == "merge":
                        result["moved"] = repo.merge_entries(
                            key, str(body.get("source") or ""), str(body.get("target") or "")
                        )
                    elif action == "split":
                        result["entry_key"] = repo.split(
                            key, _keys(body), entry_fields(body.get("fields") or {})
                        )
                    elif action == "delete_entry":
                        if not repo.delete_entry(key, str(body.get("entry_key") or "")):
                            raise ApiError(404, "no such experience")
                    elif action == "reject":
                        # Batch REJECT is safe: it creates nothing, and each
                        # answer can be changed back. A confirmed suggestion
                        # is left exactly as it is.
                        rejected = 0
                        for claim_key in _keys(body):
                            row = repo.proposal(key, claim_key)
                            if row is not None and str(row["decision"]) == "PENDING":
                                repo.record_decision(key, claim_key, decision="REJECTED")
                                rejected += 1
                        repo.close_if_finished(key)
                        result["rejected"] = rejected
                    elif action == "delete":
                        if body.get("confirm") is not True:
                            raise ApiError(400, "deleting suggestions needs confirm: true")
                        result["deleted"] = repo.delete_proposals(candidate_id, key, _keys(body))
                    else:
                        raise ApiError(
                            400,
                            "action must be edit_entry, create_entry, move, merge, split,"
                            " delete_entry, reject or delete",
                        )
            except ValueError as exc:
                raise ApiError(409, str(exc).capitalize() + ".", for_reader=True) from exc
        return {**app.cv_review(import_id=key, query={}, body={}), "result": result}

    app.register("POST", base + r"/archive", archive)
    app.register("POST", base + r"/restore", restore)
    app.register("POST", base + r"/delete", delete)
    app.register("POST", base + r"/organize", organize)

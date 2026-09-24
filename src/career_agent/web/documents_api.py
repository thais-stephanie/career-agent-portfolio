"""Documents: every import, and one guided review for all of them.

Two kinds of import exist, and a person should not have to know that:

    cv        a CV read by Career Agent (`cv_import`, `cv_entry`, `cv_proposal`)
    package   a reading of her documents as a package (`intake_package`)

This module is an ADAPTER. It stores nothing of its own and changes no rule:
answers go through the same code paths the older screens use
(`decide_proposal`, `intake.store`), organising goes through `CareerRepo`
with its history and undo, and every statement keeps its source line.

THE REVIEW MODEL
----------------
An import is read as the experiences it describes, reconciled against the
canonical profile (`career_experience`):

    NEW                        not in the profile yet
    EXISTING_UNCHANGED         in the profile, nothing new in this document
    EXISTING_WITH_NEW_DETAILS  in the profile, with statements not yet in it
    DATE_CONFLICT              in the profile with different dates
    STRUCTURE_UNCERTAIN        the reader could not tell the company or role

Derived on every request, never stored. The interface shows them in human
words; the enum names never reach a person.

WHAT THIS MODULE MAY NOT DO
---------------------------
Confirm more than one statement per request. There is no route here that
takes a list of statements to confirm: "Don't import" REJECTS (which creates
nothing), and placing an experience only organises. Each answer is saved as it
is given, so a long review survives a closed tab and resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any

from pydantic import ValidationError

from career_agent.web.server import ApiError
from career_agent.web.server import closing as _closing

#: What the review calls each statement state.
_CV_STATE = {
    "PENDING": "waiting",
    "ACCEPTED": "confirmed",
    "EDITED": "confirmed",
    "REJECTED": "rejected",
}
_PACKAGE_STATE = {
    "UNREVIEWED": "waiting",
    "CONFLICT": "waiting",
    "UNRESOLVED": "unsure",
    "CONFIRMED": "confirmed",
    "CORRECTED_BY_USER": "confirmed",
    "REJECTED": "rejected",
}
#: Sections that are not jobs, and which review step shows them.
_STEP_OF = {
    "skills": "skills",
    "tools": "skills",
    "SKILL": "skills",
    "TOOL": "skills",
    "certifications": "certifications",
    "CERTIFICATION": "certifications",
    "education": "education",
    "EDUCATION": "education",
    "languages": "education",
}
_ANSWERS = frozenset({"CONFIRM", "EDIT", "UNSURE", "REJECT", "REOPEN", "CHOOSE"})


def _norm(text: str | None) -> str:
    return re.sub(r"\W+", " ", (text or "").casefold()).strip()


def _key(*parts: object) -> str:
    return "g" + hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


# =========================================================================
# the profile the import is compared against
# =========================================================================


def _profile(conn: sqlite3.Connection, candidate_id: str | None) -> dict:
    from career_agent.storage.career_repo import CareerRepo

    repo = CareerRepo(conn, candidate_id)
    if not candidate_id or not repo.available:
        return {"experiences": [], "linked": {}, "confirmed": set(), "by_experience": {}}
    overview = repo.overview()
    items = repo.items()
    linked = {r["claim_key"]: r["experience_id"] for r in items if r["experience_id"]}
    confirmed = {_norm(r["text"]) for r in items if r["state"] == "CONFIRMED"}
    by_experience: dict[str, set[str]] = {}
    for item in items:
        if item["experience_id"] and item["state"] == "CONFIRMED":
            by_experience.setdefault(item["experience_id"], set()).add(_norm(item["text"]))
    return {
        "experiences": overview["experiences"],
        "linked": linked,
        "confirmed": confirmed,
        "by_experience": by_experience,
        "live_keys": {r["claim_key"] for r in items},
    }


def _months(period: dict) -> tuple[str | None, str | None]:
    """A period as two comparable strings: months when stated, else years."""
    start = period.get("start") or (str(period["start_year"]) if period.get("start_year") else None)
    if period.get("current"):
        return start, "present"
    end = period.get("end") or (str(period["end_year"]) if period.get("end_year") else None)
    return start, end


def _differs(document: dict, profile: dict) -> bool:
    """Whether two periods disagree about something both of them state."""
    a_start, a_end = _months(document)
    b_start, b_end = _months(profile)

    def clash(left: str | None, right: str | None) -> bool:
        if not left or not right:
            return False
        if "present" in (left, right):
            return left != right
        size = min(len(left), len(right))
        return left[:size] != right[:size]

    return clash(a_start, b_start) or clash(a_end, b_end)


def _profile_period(experience: dict) -> dict:
    start, end = experience.get("period_start"), experience.get("period_end")
    return {
        "start": start if start and len(start) > 4 else None,
        "end": end if end and len(end) > 4 else None,
        "start_year": int(start[:4]) if start else None,
        "end_year": int(end[:4]) if end else None,
        "current": bool(experience.get("current_role")),
    }


def _match(entry: dict, profile: dict) -> dict | None:
    """The profile experience this entry describes, if the words say so."""
    from career_agent.intake.conflicts import normalise_employer

    placed = {profile["linked"].get(item["career_key"]) for item in entry["items"]} - {None}
    experiences = {e["id"]: e for e in profile["experiences"]}
    if len(placed) == 1 and next(iter(placed)) in experiences:
        return experiences[next(iter(placed))]
    company = normalise_employer(entry.get("company") or "")
    role = _norm(entry.get("role"))
    if not company:
        return None
    best = None
    for experience in profile["experiences"]:
        if normalise_employer(experience.get("company") or "") != company:
            continue
        title = _norm(experience.get("title"))
        if role and title and role != title:
            continue
        # Same company and same title (or one side names none): the same job.
        if best is None or (title and title == role):
            best = experience
    return best


def _reconcile(entry: dict, profile: dict) -> None:
    """Fill `state`, `match`, `placed` and the new-detail count on an entry."""
    match = _match(entry, profile)
    live = [i for i in entry["items"] if i["state"] != "rejected"]
    known = profile["by_experience"].get(match["id"], set()) if match else set()
    for item in entry["items"]:
        item["already"] = _norm(item["text"]) in (known or profile["confirmed"])
    new = [i for i in live if i["state"] != "confirmed" and not i["already"]]
    entry["counts"]["new"] = len(new)
    entry["placed"] = match is not None and any(
        profile["linked"].get(i["career_key"]) == match["id"] for i in entry["items"]
    )
    entry["match"] = (
        {
            "experience_id": match["id"],
            "company": match.get("company"),
            "title": match.get("title"),
            "period_start": match.get("period_start"),
            "period_end": match.get("period_end"),
            "current_role": bool(match.get("current_role")),
        }
        if match
        else None
    )
    if (
        not entry.get("company")
        and not entry.get("role")
        or "structure" in entry.get("unresolved", [])
    ):
        entry["state"] = "STRUCTURE_UNCERTAIN"
    elif match is None:
        entry["state"] = "NEW"
    elif _differs(entry["period"], _profile_period(match)):
        entry["state"] = "DATE_CONFLICT"
    elif new:
        entry["state"] = "EXISTING_WITH_NEW_DETAILS"
    else:
        entry["state"] = "EXISTING_UNCHANGED"


def _tally(items: list[dict]) -> dict[str, int]:
    return {
        "total": len(items),
        "waiting": sum(i["state"] in {"waiting", "unsure"} for i in items),
        "unsure": sum(i["state"] == "unsure" for i in items),
        "confirmed": sum(i["state"] == "confirmed" for i in items),
        "rejected": sum(i["state"] == "rejected" for i in items),
    }


# =========================================================================
# CV reads
# =========================================================================


def _cv_item(row: dict) -> dict:
    return {
        "key": row["claim_key"],
        "career_key": row["claim_key"],
        "claim_type": row["claim_type"],
        "text": row["decided_text"] or row["text"],
        "suggested": row["text"],
        "quote": row["evidence"],
        "raw": row.get("source_text"),
        "line": row.get("source_line"),
        "state": _CV_STATE.get(row["decision"], "waiting"),
        "has_measurement": row["has_measurement"],
        "duplicate_of": row.get("duplicate_of"),
        "conflict": None,
    }


def _cv_model(conn: sqlite3.Connection, candidate_id: str, import_id: str) -> dict:
    from career_agent.storage.workspace_repo import CvReviewRepo
    from career_agent.web.cv_api import cv_review_payload

    repo = CvReviewRepo(conn)
    record = repo.get_import(candidate_id, import_id)
    if record is None:
        raise ApiError(404, "no such document")
    payload = cv_review_payload(record, repo.proposals(import_id), repo.entries(import_id))
    experiences = []
    for entry in payload["entries"]:
        items = [_cv_item(p) for p in entry["proposals"]]
        experiences.append(
            {
                "key": entry["entry_key"],
                "company": entry["company"],
                "role": entry["role_title"],
                "label": entry["label"],
                "where": entry["where"],
                "period": {
                    "text": entry["period_text"],
                    "start": entry["period_start"],
                    "end": entry["period_end"],
                    "current": entry["current_role"],
                    "start_year": entry["start_year"],
                    "end_year": entry["end_year"],
                },
                "unresolved": entry["unresolved"],
                "source": entry["source"],
                "editable_structure": True,
                "items": items,
                "counts": _tally(items),
            }
        )
    sections: dict[str, list[dict]] = {
        "skills": [],
        "certifications": [],
        "education": [],
        "other": [],
    }
    for group in payload["sections"]:
        step = _STEP_OF.get(group["group"], "other")
        sections[step].extend(_cv_item(p) for p in group["proposals"])
    return {
        "kind": "cv",
        "id": import_id,
        "name": str(record["source_name"]),
        "created_at": str(record["created_at"]),
        "status": "archived" if record["archived_at"] else "active",
        "editable": record["archived_at"] is None,
        "experiences": experiences,
        "sections": sections,
    }


# =========================================================================
# document packages
# =========================================================================


def _package_model(conn: sqlite3.Connection, package_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM intake_package WHERE id = ? AND deleted_at IS NULL", (package_id,)
    ).fetchone()
    if row is None:
        raise ApiError(404, "no such document")
    settled = {
        str(r["conflict_group"])
        for r in conn.execute(
            "SELECT conflict_group FROM intake_conflict_resolution WHERE package_id = ?",
            (package_id,),
        )
    }
    groups: dict[tuple, dict] = {}
    sections: dict[str, list[dict]] = {
        "skills": [],
        "certifications": [],
        "education": [],
        "other": [],
    }
    rows = conn.execute(
        "SELECT * FROM intake_claim WHERE package_id = ? ORDER BY rowid", (package_id,)
    ).fetchall()
    for claim in rows:
        payload = json.loads(str(claim["payload_json"]))
        evidence = payload.get("evidence") or {}
        conflict_group = claim["conflict_group"]
        item = {
            "key": str(claim["claim_key"]),
            "career_key": str(claim["resolved_claim_key"] or claim["claim_key"]),
            "claim_type": str(claim["claim_type"]),
            "text": claim["corrected_text"] or payload.get("text") or "",
            "suggested": payload.get("text") or "",
            "quote": evidence.get("quote"),
            "raw": None,
            "line": None,
            "locator": evidence.get("locator"),
            "state": _PACKAGE_STATE.get(str(claim["review_state"]), "waiting"),
            "has_measurement": bool(payload.get("metrics")),
            "duplicate_of": None,
            "conflict": (
                {"group": str(conflict_group)}
                if conflict_group and str(conflict_group) not in settled
                else None
            ),
        }
        employer = (payload.get("employer") or "").strip()
        if not employer:
            sections[_STEP_OF.get(str(claim["claim_type"]), "other")].append(item)
            continue
        period = payload.get("period") or {}
        start = period.get("start") or {}
        end = period.get("end") or {}
        role = (payload.get("role_title") or "").strip() or None
        signature = (
            employer.casefold(),
            (role or "").casefold(),
            start.get("normalized") or start.get("original"),
            end.get("normalized") or end.get("original"),
            bool(period.get("current")),
        )
        group = groups.setdefault(
            signature,
            {
                "key": _key(package_id, *signature),
                "company": employer,
                "role": role,
                "label": None,
                "where": None,
                "period": {
                    "text": " - ".join(p for p in (start.get("original"), end.get("original")) if p)
                    or None,
                    "start": start.get("normalized"),
                    "end": None if period.get("current") else end.get("normalized"),
                    "current": bool(period.get("current")),
                    "start_year": _year(start),
                    "end_year": None if period.get("current") else _year(end),
                },
                "unresolved": [
                    reason
                    for reason, missing in (("role", not role), ("dates", not start))
                    if missing
                ],
                "source": [],
                "editable_structure": False,
                "items": [],
            },
        )
        group["items"].append(item)
    experiences = list(groups.values())
    for entry in experiences:
        entry["counts"] = _tally(entry["items"])
    status = {
        "ACTIVE": "active",
        "DISCARDED": "archived",
        "SUPERSEDED": "superseded",
        "INCOMPLETE": "incomplete",
    }.get(str(row["status"]), "active")
    return {
        "kind": "package",
        "id": package_id,
        "name": _package_name(row),
        "created_at": str(row["created_at"]),
        "status": status,
        "editable": status == "active",
        "experiences": experiences,
        "sections": sections,
    }


def _year(point: dict) -> int | None:
    found = re.search(r"(19|20)\d{2}", str(point.get("normalized") or point.get("original") or ""))
    return int(found.group(0)) if found else None


def _package_name(row: sqlite3.Row) -> str:
    try:
        sources = json.loads(str(row["declared_sources"]))
    except ValueError:
        sources = []
    titles = [s.get("title") for s in sources if s.get("title")]
    return ", ".join(titles) or str(row["filename"] or "Document package")


# =========================================================================
# the full model
# =========================================================================


def review_model(conn: sqlite3.Connection, kind: str, doc_id: str) -> dict:
    from career_agent.cv.structure import chronology_key
    from career_agent.storage.workspace_repo import candidate_id_of

    candidate_id = candidate_id_of(conn)
    if kind == "cv":
        if candidate_id is None:
            raise ApiError(404, "no such document")
        model = _cv_model(conn, candidate_id, doc_id)
    elif kind == "package":
        model = _package_model(conn, doc_id)
    else:
        raise ApiError(404, "no such document")
    profile = _profile(conn, candidate_id)
    for entry in model["experiences"]:
        _reconcile(entry, profile)
    model["experiences"].sort(
        key=lambda e: (
            *chronology_key(
                e["period"]["start"],
                e["period"]["start_year"],
                e["period"]["end"],
                e["period"]["end_year"],
                bool(e["period"]["current"]),
            ),
            e["key"],
        )
    )
    for name, items in model["sections"].items():
        for item in items:
            item["already"] = _norm(item["text"]) in profile["confirmed"]
        model["sections"][name] = items
    every = [i for e in model["experiences"] for i in e["items"]] + [
        i for items in model["sections"].values() for i in items
    ]
    model["summary"] = {
        **_tally(every),
        "experiences": len(model["experiences"]),
        "new_experiences": sum(e["state"] == "NEW" for e in model["experiences"]),
        "need_help": sum(
            e["state"] in {"DATE_CONFLICT", "STRUCTURE_UNCERTAIN"} for e in model["experiences"]
        ),
        "sections": {name: _tally(items) for name, items in model["sections"].items()},
    }
    return model


def documents_list(conn: sqlite3.Connection) -> dict:
    """Every import she can see, newest first, in words she would use."""
    from career_agent.storage.review_counts import review_counts
    from career_agent.storage.workspace_repo import CvReviewRepo, candidate_id_of

    candidate_id = candidate_id_of(conn)
    documents = []
    if candidate_id:
        for item in CvReviewRepo(conn).imports(candidate_id):
            documents.append(
                {
                    "kind": "cv",
                    "id": item.import_id,
                    "name": item.source_name,
                    "created_at": item.created_at,
                    "status": "archived" if item.archived else "active",
                    "experiences": item.entries,
                    "waiting": item.pending,
                    "confirmed": item.confirmed,
                    "total": item.total,
                }
            )
    try:
        rows = conn.execute(
            "SELECT * FROM intake_package WHERE deleted_at IS NULL ORDER BY created_at DESC"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    for row in rows:
        model = _package_model(conn, str(row["id"]))
        every = [i for e in model["experiences"] for i in e["items"]] + [
            i for items in model["sections"].values() for i in items
        ]
        tally = _tally(every)
        documents.append(
            {
                "kind": "package",
                "id": str(row["id"]),
                "name": model["name"],
                "created_at": model["created_at"],
                "status": model["status"],
                "experiences": len(model["experiences"]),
                "waiting": tally["waiting"],
                "confirmed": tally["confirmed"],
                "total": tally["total"],
            }
        )
    documents.sort(key=lambda d: d["created_at"], reverse=True)
    return {"documents": documents, "counts": review_counts(conn, candidate_id).as_dict()}


# =========================================================================
# routes
# =========================================================================


def register_documents_routes(app: Any) -> None:
    base = r"/api/documents/(?P<kind>cv|package)/(?P<doc_id>[^/]+)"

    def listing(*, query: dict, body: dict) -> dict:
        with _closing(app.connect()) as conn:
            return documents_list(conn)

    def review(*, kind: str, doc_id: str, query: dict, body: dict) -> dict:
        from career_agent.web.workspace_api import _claim_key

        with _closing(app.connect()) as conn:
            return review_model(conn, kind, _claim_key(doc_id))

    def answer(*, kind: str, doc_id: str, query: dict, body: dict) -> dict:
        """One statement, one answer. Delegates to the path the answer always took."""
        from career_agent.web.workspace_api import _claim_key

        doc = _claim_key(doc_id)
        key = _claim_key(str(body.get("key") or ""))
        verb = body.get("answer")
        if verb not in _ANSWERS:
            raise ApiError(400, "answer must be CONFIRM, EDIT, UNSURE, REJECT, REOPEN or CHOOSE")
        text = body.get("text")
        if verb == "EDIT" and (not isinstance(text, str) or not text.strip()):
            raise ApiError(400, "an edit needs the corrected text", for_reader=True)
        if kind == "cv":
            decision = {
                "CONFIRM": "ACCEPTED",
                "EDIT": "EDITED",
                "REJECT": "REJECTED",
                "REOPEN": "PENDING",
                "UNSURE": None,
                "CHOOSE": None,
            }[verb]
            if decision is not None:
                payload: dict[str, Any] = {"claim_key": key, "decision": decision}
                if verb == "EDIT":
                    payload["text"] = text
                app.decide_proposal(import_id=doc, query={}, body=payload)
            # "Not sure yet" on a CV read leaves the statement waiting: it is
            # still unanswered, and the review moves on to the next one.
        else:
            package_answer = {
                "CONFIRM": "CONFIRM",
                "EDIT": "CORRECT",
                "REJECT": "REJECT",
                "REOPEN": "REOPEN",
                "UNSURE": "UNRESOLVED",
            }
            if verb == "CHOOSE":
                group = str(body.get("group") or "")
                app.intake_resolve(
                    package_id=doc, query={}, body={"group": group, "claim_key": key}
                )
            else:
                package_payload: dict[str, Any] = {"claim_key": key, "answer": package_answer[verb]}
                if verb == "EDIT":
                    package_payload["text"] = text
                app.intake_answer(package_id=doc, query={}, body=package_payload)
            _keep_placement(app, doc, key)
        with _closing(app.connect()) as conn:
            return review_model(conn, kind, doc)

    def place(*, kind: str, doc_id: str, query: dict, body: dict) -> dict:
        """Put one of the document's experiences on the profile.

        `choice: "new"` creates it (with the entry's words, or corrected
        ones); `choice: "existing"` places its statements in the matching
        experience, and `dates: "document"` takes the document's dates over
        the profile's. Organising only, recorded in history, undoable. It
        confirms nothing.
        """
        from career_agent.storage.career_repo import CareerError, CareerRepo
        from career_agent.storage.db import transaction
        from career_agent.storage.workspace_repo import candidate_id_of, ensure_candidate
        from career_agent.web.workspace_api import _claim_key

        doc = _claim_key(doc_id)
        with _closing(app.connect()) as conn:
            model = review_model(conn, kind, doc)
            if not model["editable"]:
                raise ApiError(
                    409, "Restore or use this document before changing it.", for_reader=True
                )
            entry = next((e for e in model["experiences"] if e["key"] == body.get("entry")), None)
            if entry is None:
                raise ApiError(404, "no such experience in this document")
            profile = _profile(conn, candidate_id_of(conn))
            keys = [
                i["career_key"]
                for i in entry["items"]
                if i["state"] != "rejected" and i["career_key"] in profile.get("live_keys", set())
            ]
            raw_fields = body.get("fields")
            fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
            choice = body.get("choice")
            try:
                with transaction(conn):
                    candidate = ensure_candidate(conn)
                    repo = CareerRepo(conn, candidate)
                    if choice == "new":
                        metadata = {
                            "company": fields.get("company", entry["company"] or entry["label"]),
                            "title": fields.get("title", entry["role"]),
                            "period_start": fields.get("period_start", _stored(entry, "start")),
                            "period_end": fields.get("period_end", _stored(entry, "end")),
                            "current_role": _flag(
                                fields.get("current_role", entry["period"]["current"])
                            ),
                        }
                        if fields.get("description"):
                            metadata["description"] = fields["description"]
                        command: dict = {"action": "create", "keys": keys, "metadata": metadata}
                    elif choice == "existing" and entry["match"]:
                        target = entry["match"]["experience_id"]
                        if body.get("dates") == "document":
                            # Only what the document states: a start-only
                            # document must not erase the profile's end, nor
                            # an unstated end its "I work here now".
                            period = entry["period"]
                            dated: dict[str, Any] = {}
                            if _stored(entry, "start"):
                                dated["period_start"] = _stored(entry, "start")
                            if period.get("current"):
                                dated["period_end"] = None
                                dated["current_role"] = True
                            elif _stored(entry, "end"):
                                dated["period_end"] = _stored(entry, "end")
                                dated["current_role"] = False
                            command = {"action": "edit", "experience_id": target, "metadata": dated}
                            preview = repo.preview(command)
                            repo.apply(command, preview["preview_hash"])
                        command = {"action": "move", "keys": keys, "experience_id": target}
                        if not keys:
                            command = {}
                    else:
                        raise ApiError(400, "choice must be new, or existing with a match")
                    result: dict = {}
                    if command:
                        preview = repo.preview(command)
                        result = repo.apply(command, preview["preview_hash"])
            except CareerError as exc:
                raise ApiError(409, str(exc), for_reader=True) from exc
            except ValidationError as exc:
                # A corrected field that is not a date, too long, or an end
                # before the start: the reader's mistake, said as such.
                problem = (
                    exc.errors()[0].get("msg", "invalid value") if exc.errors() else "invalid value"
                )
                raise ApiError(
                    400, str(problem).removeprefix("Value error, "), for_reader=True
                ) from exc
            fresh = review_model(conn, kind, doc)
        return {
            **fresh,
            "event_id": result.get("event_id"),
            "experience_id": result.get("experience_id"),
        }

    def skip(*, kind: str, doc_id: str, query: dict, body: dict) -> dict:
        """ "Don't import": reject the experience's unanswered statements.

        A batch REJECT, which creates nothing and can be reopened one by one.
        Confirmed statements are left exactly as they are.
        """
        from career_agent.web.workspace_api import _claim_key

        doc = _claim_key(doc_id)
        with _closing(app.connect()) as conn:
            model = review_model(conn, kind, doc)
        if not model["editable"]:
            raise ApiError(409, "Restore or use this document before changing it.", for_reader=True)
        entry = next((e for e in model["experiences"] if e["key"] == body.get("entry")), None)
        if entry is None:
            raise ApiError(404, "no such experience in this document")
        keys = [i["key"] for i in entry["items"] if i["state"] in {"waiting", "unsure"}]
        if kind == "cv" and keys:
            app.handle_api(
                "POST", f"/api/cv/imports/{doc}/organize", {}, {"action": "reject", "keys": keys}
            )
        elif kind == "package":
            for key in keys:
                try:
                    app.intake_answer(
                        package_id=doc, query={}, body={"claim_key": key, "answer": "REJECT"}
                    )
                except ApiError:
                    continue
        with _closing(app.connect()) as conn:
            return review_model(conn, kind, doc)

    app.register("GET", r"/api/documents", listing)
    app.register("GET", base, review)
    app.register("POST", base + r"/answer", answer)
    app.register("POST", base + r"/place", place)
    app.register("POST", base + r"/skip", skip)


def _flag(value: Any) -> bool:
    """A yes/no from a request body: a real boolean, never the string "false"."""
    if value is None:
        return False
    if not isinstance(value, bool):
        raise ApiError(400, "current_role must be true or false")
    return value


def _stored(entry: dict, end: str) -> str | None:
    """A date as the profile stores it: the month, or the year alone."""
    period = entry["period"]
    if end == "end" and period.get("current"):
        return None
    month = period.get(end)
    if month:
        return str(month)
    year = period.get(f"{end}_year")
    return str(year) if year else None


def _keep_placement(app: Any, package_id: str, key: str) -> None:
    """After a package answer, the claim it made stays in the same experience.

    A confirmed package statement is stored under its resolved key; if the
    suggestion was already placed in an experience, the claim is linked there
    too, so confirming never takes a highlight off the profile.
    """
    from career_agent.storage.career_repo import CareerRepo
    from career_agent.storage.db import transaction
    from career_agent.storage.workspace_repo import candidate_id_of

    with _closing(app.connect()) as conn:
        row = conn.execute(
            "SELECT claim_key, resolved_claim_key FROM intake_claim"
            " WHERE package_id = ? AND claim_key = ?",
            (package_id, key),
        ).fetchone()
        candidate = candidate_id_of(conn)
        if row is None or candidate is None:
            return
        keys = {str(row["claim_key"]), str(row["resolved_claim_key"] or row["claim_key"])}
        links = {
            str(r["claim_key"]): r["experience_id"]
            for r in conn.execute(
                "SELECT claim_key, experience_id FROM career_evidence_link"
                " WHERE candidate_id = ? AND experience_id IS NOT NULL",
                (candidate,),
            )
        }
        target = next((links[k] for k in keys if k in links), None)
        if target is None:
            return
        with transaction(conn):
            repo = CareerRepo(conn, candidate)
            for k in keys - set(links):
                repo.link(k, target)

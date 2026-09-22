"""Experience organization and review proposals. No posting or search writes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from itertools import combinations
from typing import Any

from career_agent.clock import new_id, now_utc
from career_agent.domain.enums import ClaimType
from career_agent.domain.experience import CATEGORIES, CATEGORY_TYPES, KINDS, ExperienceMetadata
from career_agent.intake.conflicts import normalise_employer
from career_agent.match.text import fold
from career_agent.storage.repositories import ClaimRepo

TABLES = ("career_company", "career_experience", "career_evidence_link", "career_company_decision")
ORG_ACTIONS = {
    "create",
    "edit",
    "move",
    "merge_experiences",
    "split",
    "merge_companies",
    "keep_separate",
}
ACTIONS = ORG_ACTIONS | {"category", "confirm", "retire", "undo"}


def encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def positions(organization: dict) -> dict:
    """Category review is independent of reversible experience associations."""
    return {
        **organization,
        "career_evidence_link": [
            {key: value for key, value in row.items() if key != "category"}
            for row in organization["career_evidence_link"]
            if row["experience_id"] is not None
        ],
    }


class CareerError(ValueError):
    pass


class CareerRepo:
    def __init__(self, conn: sqlite3.Connection, candidate_id: str | None):
        self.conn = conn
        self.candidate_id = candidate_id
        self.available = bool(
            conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'career_experience'").fetchone()
        )

    def organization(self) -> dict[str, list[dict]]:
        predicates = {
            "career_company": "archived = 0",
            "career_experience": "archived = 0",
            "career_evidence_link": "(experience_id IS NOT NULL OR category IS NOT NULL)",
            "career_company_decision": "decision != 'UNREVIEWED'",
        }
        return {
            table: [
                dict(row)
                for row in self.conn.execute(
                    f"SELECT * FROM {table} WHERE candidate_id = ?"
                    f" AND {predicates[table]} ORDER BY 1, 2",
                    (self.candidate_id,),
                )
            ]
            if self.available and self.candidate_id
            else []
            for table in TABLES
        }

    def items(self) -> list[dict]:
        """Current evidence plus the active reading. Confirmed keys appear once.

        Import provenance is attached even after confirmation, including the
        original employer and date strings. No source is chosen as authoritative.
        """
        records: dict[str, dict] = {}
        if self.candidate_id:
            for claim in ClaimRepo(self.conn).current(self.candidate_id):
                records[claim.claim_key] = {
                    **claim.model_dump(mode="json"),
                    "state": "CONFIRMED" if claim.verified else "RETIRED",
                    "origin": "claim",
                    "source_records": [],
                    "role_title": None,
                    "current_role": False,
                    "conflict": False,
                }
        rows = self.conn.execute(
            "SELECT c.*, p.status AS package_status, p.declared_sources FROM intake_claim c"
            " JOIN intake_package p ON p.id = c.package_id ORDER BY c.created_at, c.id"
        ).fetchall()
        resolved = {
            (r["package_id"], r["conflict_group"])
            for r in self.conn.execute(
                "SELECT package_id, conflict_group FROM intake_conflict_resolution"
            )
        }
        for row in rows:
            statement = json.loads(row["payload_json"])
            key = row["resolved_claim_key"] or row["claim_key"]
            period = statement.get("period") or {}
            source_record = {
                "package_id": row["package_id"],
                "source_ref": row["source_ref"],
                "employer": statement.get("employer"),
                "period": period,
                "evidence": statement.get("evidence"),
                "text": statement.get("text"),
                "role_title": statement.get("role_title"),
                "documents": json.loads(row["declared_sources"]),
            }
            if key not in records:
                if row["package_status"] != "ACTIVE":
                    continue
                conflict = bool(
                    row["conflict_group"]
                    and (row["package_id"], row["conflict_group"]) not in resolved
                )
                records[key] = {
                    "claim_key": key,
                    "revision": 0,
                    "claim_type": statement["type"],
                    "text": row["corrected_text"] or statement["text"],
                    "employer": statement.get("employer"),
                    "period_start": (period.get("start") or {}).get("normalized"),
                    "period_end": (period.get("end") or {}).get("normalized"),
                    "verified": False,
                    "state": "PENDING"
                    if row["review_state"] in {"UNREVIEWED", "CONFLICT"}
                    else row["review_state"],
                    "origin": "intake",
                    "package_id": row["package_id"],
                    "import_claim_key": row["claim_key"],
                    "source": "DOCUMENT",
                    "evidence_ref": (statement.get("evidence") or {}).get("quote"),
                    "tools": statement.get("tools", []),
                    "source_records": [],
                    "role_title": statement.get("role_title"),
                    "current_role": bool(period.get("current")),
                    "conflict": conflict,
                }
            record = records[key]
            record["source_records"].append(source_record)
            # These are organizing hints, never additions to the verified claim.
            if statement.get("role_title") and not record["role_title"]:
                record["role_title"] = statement["role_title"]
            if period.get("current") and not record["period_end"]:
                record["current_role"] = True
        if self.candidate_id:
            cv_rows = self.conn.execute(
                "SELECT p.*, i.source_name, i.status AS import_status FROM cv_proposal p"
                " JOIN cv_import i ON i.id = p.import_id"
                " WHERE i.candidate_id = ? ORDER BY p.ordinal",
                (self.candidate_id,),
            ).fetchall()
            for row in cv_rows:
                key = row["claim_key"]
                source_record = {
                    "import_id": row["import_id"],
                    "source_ref": "cv",
                    "employer": None,
                    "period": {},
                    "role_title": None,
                    "text": row["text"],
                    "evidence": {"quote": row["evidence"]},
                    "documents": [{"ref": "cv", "kind": "RESUME", "title": row["source_name"]}],
                }
                if key in records:
                    records[key]["source_records"].append(source_record)
                    continue
                if row["import_status"] == "DISCARDED":
                    continue
                records[key] = {
                    "claim_key": key,
                    "revision": 0,
                    "claim_type": row["claim_type"],
                    "text": row["decided_text"] or row["text"],
                    "employer": None,
                    "period_start": None,
                    "period_end": None,
                    "verified": False,
                    "state": "REJECTED" if row["decision"] == "REJECTED" else "PENDING",
                    "origin": "cv",
                    "import_id": row["import_id"],
                    "source": "RESUME",
                    "evidence_ref": row["evidence"],
                    "tools": [],
                    "source_records": [source_record],
                    "role_title": None,
                    "current_role": False,
                    "conflict": False,
                }
        links = {r["claim_key"]: r for r in self.organization()["career_evidence_link"]}
        for key, item in records.items():
            titles = {
                source["role_title"]
                for source in item["source_records"]
                if source.get("role_title")
            }
            item["role_ambiguous"] = len(titles) > 1
            if item["role_ambiguous"]:
                item["role_title"] = None
            link = links.get(key, {})
            item["experience_id"] = link.get("experience_id")
            item["category"] = link.get("category") or (
                "RESPONSIBILITY" if item["claim_type"] == "EMPLOYMENT" else item["claim_type"]
            )
            if item["category"] not in CATEGORIES:
                item["category"] = "OTHER"
            item["display_order"] = link.get("display_order", 0)
            item["organizing_reasons"] = [
                reason
                for reason, missing in (
                    ("company", not item["employer"]),
                    ("role", not item["role_title"]),
                    ("dates", not item["period_start"]),
                    ("conflict", item["conflict"]),
                )
                if missing
            ]
        return sorted(records.values(), key=lambda r: (r["display_order"], r["claim_key"]))

    def overview(self) -> dict:
        organization = self.organization()
        items = self.items()
        companies = {c["id"]: c for c in organization["career_company"]}

        def canonical(company_id: str | None) -> dict:
            seen = set()
            while company_id in companies and company_id not in seen:
                seen.add(company_id)
                company = companies[company_id]
                if not company["merged_into"]:
                    return company
                company_id = company["merged_into"]
            return {}

        experiences = []
        for entry in organization["career_experience"]:
            if entry["archived"]:
                continue
            owned = [r for r in items if r["experience_id"] == entry["id"]]
            experiences.append(
                {
                    **entry,
                    "company": canonical(entry["company_id"]).get("label"),
                    "company_original": companies.get(entry["company_id"], {}).get("label"),
                    "keys": [r["claim_key"] for r in owned],
                    "count": len(owned),
                    "confirmed": sum(r["verified"] for r in owned),
                }
            )
        experiences.sort(key=lambda e: (e["display_order"], e["created_at"], e["id"]))
        unassigned = [r for r in items if not r["experience_id"]]
        grouped: dict[tuple, list[dict]] = defaultdict(list)
        for item in unassigned:
            if (
                item["employer"]
                and item["period_start"]
                and item["state"] not in {"RETIRED", "REJECTED"}
            ):
                grouped[
                    (
                        item["employer"],
                        item["role_title"],
                        item["period_start"],
                        item["period_end"],
                        item["current_role"],
                    )
                ].append(item)
        proposals = []
        for signature, members in grouped.items():
            company, title, start, end, current = signature
            proposals.append(
                {
                    "id": digest(signature)[:20],
                    "company": company,
                    "title": title,
                    "period_start": start,
                    "period_end": end,
                    "current_role": current,
                    "count": len(members),
                    "keys": [r["claim_key"] for r in members],
                    "needs_role": not title,
                    "date_unknown": not end and not current,
                    "conflict": any(r["conflict"] for r in members),
                }
            )
        for proposal in proposals:
            proposal["overlap"] = any(
                other is not proposal
                and other["company"] == proposal["company"]
                and (other["period_end"] or "9999-12") >= proposal["period_start"]
                and (proposal["period_end"] or "9999-12") >= other["period_start"]
                for other in proposals
            )
        labels = sorted(
            {r["employer"] for r in items if r["employer"]}
            | {c["label"] for c in companies.values() if not c["archived"]}
        )
        decisions = {
            (r["first_label"], r["second_label"]): r["decision"]
            for r in organization["career_company_decision"]
        }
        aliases = []
        for left, right in combinations(labels, 2):
            a, b = normalise_employer(left), normalise_employer(right)
            # Prefix matches propose a question only. Even legal suffixes are
            # not identity proof; Keep separate is a durable answer.
            possible = a and b and (a == b or a.startswith(b + " ") or b.startswith(a + " "))
            if possible:
                aliases.append(
                    {
                        "first": left,
                        "second": right,
                        "decision": decisions.get((left, right)),
                        "reason": "name_variant",
                    }
                )
        history = self.history()
        return {
            "experiences": experiences,
            "proposals": proposals,
            "aliases": aliases,
            "unassigned": len(unassigned),
            "total": len(items),
            "confirmed": sum(r["verified"] for r in items),
            "categories": list(CATEGORIES),
            "kinds": list(KINDS),
            "history": history["items"],
            "history_total": history["total"],
            "migration_required": not self.available,
        }

    def history(self, offset: int = 0, limit: int = 20) -> dict:
        if not self.available or not self.candidate_id:
            return {"items": [], "total": 0}
        rows = self.conn.execute(
            "SELECT id, action, reversible, undone_by, created_at FROM career_history_event"
            " WHERE candidate_id = ? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            (self.candidate_id, limit, offset),
        ).fetchall()
        total = self.conn.execute(
            "SELECT count(*) FROM career_history_event WHERE candidate_id = ?", (self.candidate_id,)
        ).fetchone()[0]
        return {"items": [dict(row) for row in rows], "total": total}

    def page(
        self,
        *,
        experience: str = "",
        query: str = "",
        state: str = "",
        category: str = "",
        offset: int = 0,
        limit: int = 50,
    ) -> dict:
        needle = fold(query)[0]
        names = (
            {
                e["id"]: " ".join(
                    str(e.get(key) or "")
                    for key in (
                        "company",
                        "company_original",
                        "title",
                        "period_start",
                        "period_end",
                    )
                )
                for e in self.overview()["experiences"]
            }
            if needle
            else {}
        )

        def searchable(item: dict) -> str:
            original = " ".join(
                str(source.get(key) or "")
                for source in item["source_records"]
                for key in ("employer", "role_title", "text")
            )
            current = " ".join(
                str(item.get(key) or "")
                for key in (
                    "text",
                    "employer",
                    "role_title",
                    "evidence_ref",
                    "tools",
                    "period_start",
                    "period_end",
                )
            )
            return fold(" ".join((current, original, names.get(item["experience_id"], ""))))[0]

        rows = [
            r
            for r in self.items()
            if (experience != "inbox" or not r["experience_id"])
            and (not experience or experience == "inbox" or r["experience_id"] == experience)
            and (not state or r["state"] == state)
            and (not category or r["category"] == category)
            and (not needle or needle in searchable(r))
        ]
        return {
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "keys": [r["claim_key"] for r in rows],
            "offset": offset,
            "limit": limit,
        }

    def preview(self, command: dict) -> dict:
        if not isinstance(command, dict):
            raise CareerError("Choose a supported career action.")
        for field in ("action", "experience_id", "source_id", "category", "event_id"):
            if (
                field in command
                and command[field] is not None
                and not isinstance(command[field], str)
            ):
                raise CareerError("Career identifiers must be text.")
        if "metadata" in command and not isinstance(command["metadata"], dict):
            raise CareerError("Experience metadata must be an object.")
        allowed = {
            "action",
            "keys",
            "experience_id",
            "source_id",
            "metadata",
            "category",
            "first",
            "second",
            "event_id",
        }
        if set(command) - allowed or command.get("action") not in ACTIONS:
            raise CareerError("Choose a supported career action.")
        keys = command.get("keys", [])
        if (
            not isinstance(keys, list)
            or len(keys) > 1000
            or any(not isinstance(k, str) for k in keys)
            or len(set(keys)) != len(keys)
        ):
            raise CareerError("Select up to 1,000 distinct evidence items.")
        organization = self.organization()
        items = self.items()
        by_key = {r["claim_key"]: r for r in items}
        if any(k not in by_key for k in keys):
            raise CareerError("The selected evidence changed. Reload and review it again.")
        action = command["action"]
        experiences = {e["id"]: e for e in organization["career_experience"] if not e["archived"]}
        target = command.get("experience_id")
        if (
            action in {"edit", "merge_experiences"} or (action == "move" and target)
        ) and target not in experiences:
            raise CareerError("Choose an existing experience.")
        scope = list(keys)
        if action in {"edit", "merge_experiences"}:
            scope = [r["claim_key"] for r in items if r["experience_id"] == target]
        if action == "merge_experiences":
            source = command.get("source_id")
            if source not in experiences or source == target:
                raise CareerError("Choose two distinct experiences.")
            scope += [r["claim_key"] for r in items if r["experience_id"] == source]
        if action in {"create", "edit", "split"}:
            metadata = command.get("metadata", {})
            if action == "edit":
                previous = next(e for e in self.overview()["experiences"] if e["id"] == target)
                metadata = {**{k: previous[k] for k in ExperienceMetadata.model_fields}, **metadata}
            ExperienceMetadata.model_validate(metadata)
        if action in {"move", "split", "category", "confirm", "retire"} and not keys:
            raise CareerError("Select the evidence you want to review.")
        if action == "split":
            parents = {by_key[k]["experience_id"] for k in keys}
            if len(parents) != 1 or None in parents:
                raise CareerError("Split a selection from one experience.")
            if sum(r["experience_id"] in parents for r in items) == len(keys):
                raise CareerError("Leave some evidence in the original experience.")
        if action == "category" and command.get("category") not in CATEGORIES:
            raise CareerError("Choose an evidence category.")
        if action == "confirm" and any(by_key[k]["conflict"] for k in keys):
            raise CareerError(
                "Resolve the imported date disagreement before confirming these items."
            )
        if action == "confirm" and any(by_key[k]["state"] == "REJECTED" for k in keys):
            raise CareerError(
                "Reopen rejected imports in the source review before confirming them."
            )
        if action in {"merge_companies", "keep_separate"}:
            a, b = command.get("first"), command.get("second")
            labels = {r["employer"] for r in items} | {
                c["label"] for c in organization["career_company"]
            }
            if (
                not isinstance(a, str)
                or not isinstance(b, str)
                or a == b
                or a not in labels
                or b not in labels
            ):
                raise CareerError("Choose two existing company labels.")
            company_ids = {c["id"] for c in organization["career_company"] if c["label"] in {a, b}}
            while True:
                wider = (
                    company_ids
                    | {
                        c["id"]
                        for c in organization["career_company"]
                        if c["merged_into"] in company_ids
                    }
                    | {
                        c["merged_into"]
                        for c in organization["career_company"]
                        if c["id"] in company_ids and c["merged_into"]
                    }
                )
                if wider == company_ids:
                    break
                company_ids = wider
            exp_ids = {e["id"] for e in experiences.values() if e["company_id"] in company_ids}
            family_labels = {a, b} | {
                c["label"] for c in organization["career_company"] if c["id"] in company_ids
            }
            if action == "keep_separate":
                companies = {c["id"]: c for c in organization["career_company"]}

                def root(label: str) -> str | None:
                    current = next(
                        (c["id"] for c in companies.values() if c["label"] == label), None
                    )
                    seen = set()
                    while current in companies and companies[current]["merged_into"]:
                        if current in seen:
                            raise CareerError("Review the existing company associations first.")
                        seen.add(current)
                        current = companies[current]["merged_into"]
                    return current

                first_root, second_root = root(a), root(b)
                if first_root and first_root == second_root:
                    raise CareerError("Undo their company merge before keeping them separate.")
            scope = [
                r["claim_key"]
                for r in items
                if r["employer"] in family_labels or r["experience_id"] in exp_ids
            ]
        if action == "undo":
            event = self._undo_event(command.get("event_id"), organization)
            scope = [key for key in json.loads(event["scope_json"]) if key in by_key]
        return {
            "command": command,
            "count": len(scope),
            "items": [by_key[k] for k in scope],
            "preview_hash": digest(
                {"command": command, "organization": organization, "items": items}
            ),
        }

    def _company(self, label: str | None) -> str | None:
        if not label:
            return None
        row = self.conn.execute(
            "SELECT id FROM career_company WHERE candidate_id = ? AND label = ?",
            (self.candidate_id, label),
        ).fetchone()
        if row:
            self.conn.execute(
                "UPDATE career_company SET merged_into = CASE WHEN archived = 1 THEN NULL"
                " ELSE merged_into END, archived = 0 WHERE id = ?",
                (row["id"],),
            )
            return str(row["id"])
        key = new_id()
        self.conn.execute(
            "INSERT INTO career_company(id, candidate_id, label) VALUES (?, ?, ?)",
            (key, self.candidate_id, label),
        )
        return key

    def link(self, key: str, experience_id: str | None, category: str | None = None) -> None:
        self.conn.execute(
            "INSERT INTO career_evidence_link(candidate_id, claim_key, experience_id, category)"
            " VALUES (?, ?, ?, ?) ON CONFLICT(candidate_id, claim_key) DO UPDATE SET"
            " experience_id = excluded.experience_id,"
            " category = COALESCE(excluded.category, category)",
            (self.candidate_id, key, experience_id, category),
        )

    def _experience(self, metadata: dict, experience_id: str | None = None) -> str:
        previous = None
        if experience_id:
            previous = next(e for e in self.overview()["experiences"] if e["id"] == experience_id)
            metadata = {**{k: previous[k] for k in ExperienceMetadata.model_fields}, **metadata}
        data = ExperienceMetadata.model_validate(metadata).model_dump()
        company = data.pop("company")
        data["company_id"] = (
            previous["company_id"]
            if previous and previous["company"] == company
            else self._company(company)
        )
        data["current_role"] = int(data["current_role"])
        if experience_id:
            self.conn.execute(
                "UPDATE career_experience SET "
                + ", ".join(f"{k} = ?" for k in data)
                + " WHERE id = ? AND candidate_id = ?",
                (*data.values(), experience_id, self.candidate_id),
            )
        else:
            experience_id = new_id()
            data.update(id=experience_id, candidate_id=self.candidate_id, created_at=now_utc())
            self.conn.execute(
                "INSERT INTO career_experience("
                + ",".join(data)
                + ") VALUES ("
                + ",".join("?" for _ in data)
                + ")",
                tuple(data.values()),
            )
        return experience_id

    def apply(self, command: dict, preview_hash: str, *, reviewed: bool = False) -> dict:
        """Caller owns BEGIN IMMEDIATE. Validation completes before any write."""
        if not self.conn.in_transaction:
            raise CareerError("Career changes require an atomic transaction.")
        if not self.available or not self.candidate_id:
            raise CareerError("The career workspace needs its structural migration first.")
        preview = self.preview(command)
        if preview["preview_hash"] != preview_hash:
            raise CareerError("The evidence or its organization changed. Review a fresh preview.")
        action = command["action"]
        if action == "confirm" and reviewed is not True:
            raise CareerError("Read the selected statements and explicitly confirm your review.")
        before = self.organization()
        keys = command.get("keys", [])
        target = command.get("experience_id")
        if action in {"create", "split"}:
            target = self._experience(command["metadata"])
            for key in keys:
                self.link(key, target)
        elif action == "edit":
            self._experience(command["metadata"], target)
        elif action == "move":
            for key in keys:
                self.link(key, target)
        elif action == "merge_experiences":
            source = command["source_id"]
            self.conn.execute(
                "UPDATE career_evidence_link SET experience_id = ?"
                " WHERE candidate_id = ? AND experience_id = ?",
                (target, self.candidate_id, source),
            )
            self.conn.execute("UPDATE career_experience SET archived = 1 WHERE id = ?", (source,))
        elif action in {"merge_companies", "keep_separate"}:
            first, second = command["first"], command["second"]
            if action == "merge_companies":
                a, b = self._company(first), self._company(second)
                # Flatten the entire approved alias family to the selected label.
                companies = self.organization()["career_company"]
                family = {a, b}
                while True:
                    wider = family | {c["id"] for c in companies if c["merged_into"] in family}
                    wider |= {
                        c["merged_into"]
                        for c in companies
                        if c["id"] in family and c["merged_into"]
                    }
                    if wider == family:
                        break
                    family = wider
                for company_id in family:
                    self.conn.execute(
                        "UPDATE career_company SET merged_into = ? WHERE id = ?",
                        (None if company_id == b else b, company_id),
                    )
            a_label, b_label = sorted((first, second))
            self.conn.execute(
                "INSERT INTO career_company_decision VALUES (?, ?, ?, ?)"
                " ON CONFLICT(candidate_id, first_label, second_label) DO UPDATE"
                " SET decision = excluded.decision",
                (
                    self.candidate_id,
                    a_label,
                    b_label,
                    "MERGED" if action == "merge_companies" else "SEPARATE",
                ),
            )
        elif action in {"category", "confirm", "retire"}:
            self._review_items(command)
        elif action == "undo":
            event = self._undo_event(command["event_id"], before)
            self._restore(json.loads(event["before_json"]))
        event_id = new_id()
        self.conn.execute(
            "INSERT INTO career_history_event(id, candidate_id, action, before_json, after_json,"
            " scope_json, reversible, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event_id,
                self.candidate_id,
                action,
                encoded(before),
                encoded(self.organization()),
                encoded([r["claim_key"] for r in preview["items"]]),
                int(action in ORG_ACTIONS),
                now_utc(),
            ),
        )
        if action == "undo":
            self.conn.execute(
                "UPDATE career_history_event SET undone_by = ? WHERE id = ?",
                (event_id, command["event_id"]),
            )
        return {"event_id": event_id, "experience_id": target, "count": preview["count"]}

    def _review_items(self, command: dict) -> None:
        from career_agent.cv.propose import Proposal, to_claim
        from career_agent.intake import store
        from career_agent.storage.workspace_repo import CvReviewRepo

        assert self.candidate_id is not None
        records = {r["claim_key"]: r for r in self.items()}
        claims = ClaimRepo(self.conn)
        for key in command["keys"]:
            item = records[key]
            action = command["action"]
            if action == "category":
                category = command["category"]
                self.link(key, item["experience_id"], category)
                row = claims.current_row(self.candidate_id, key)
                if row and category in CATEGORY_TYPES:
                    current = claims._to_claim(row)
                    new_type = ClaimType(CATEGORY_TYPES[category])
                    if current.claim_type != new_type:
                        claims.supersede(
                            self.candidate_id, current.next_revision(claim_type=new_type)
                        )
                continue
            wanted = action == "confirm"
            if item["origin"] == "claim":
                row = claims.current_row(self.candidate_id, key)
                assert row is not None
                current = claims._to_claim(row)
                if current.verified != wanted:
                    claims.supersede(self.candidate_id, current.next_revision(verified=wanted))
            elif item["origin"] == "intake":
                if wanted:
                    store.confirm(
                        self.conn, item["package_id"], item["import_claim_key"], atomic=False
                    )
                else:
                    store.reject(
                        self.conn, item["package_id"], item["import_claim_key"], atomic=False
                    )
            else:
                repo = CvReviewRepo(self.conn)
                row = repo.proposal(item["import_id"], key)
                assert row is not None
                claim_id = None
                if wanted:
                    proposal = Proposal(
                        claim_key=key,
                        claim_type=ClaimType(row["claim_type"]),
                        text=row["text"],
                        section=row["section"],
                        evidence=row["evidence"],
                        has_measurement=bool(row["has_measurement"]),
                    )
                    claim_id = claims.add(self.candidate_id, to_claim(proposal))
                repo.record_decision(
                    item["import_id"],
                    key,
                    decision="ACCEPTED" if wanted else "REJECTED",
                    claim_id=claim_id,
                )
                repo.close_if_finished(item["import_id"])
            if wanted and item["category"] in CATEGORY_TYPES:
                row = claims.current_row(self.candidate_id, key)
                if row and row["claim_type"] != CATEGORY_TYPES[item["category"]]:
                    current = claims._to_claim(row)
                    claims.supersede(
                        self.candidate_id,
                        current.next_revision(
                            claim_type=ClaimType(CATEGORY_TYPES[item["category"]])
                        ),
                    )

    def _undo_event(self, event_id: str | None, organization: dict) -> dict:
        if not self.available:
            raise CareerError("No organization history exists yet.")
        row = self.conn.execute(
            "SELECT * FROM career_history_event WHERE id = ? AND candidate_id = ?",
            (event_id, self.candidate_id),
        ).fetchone()
        if not row or not row["reversible"] or row["undone_by"]:
            raise CareerError("Choose an organization change that can be undone.")
        if encoded(positions(organization)) != encoded(positions(json.loads(row["after_json"]))):
            raise CareerError("Organization changed afterwards. Undo the later changes first.")
        return dict(row)

    def _restore(self, before: dict) -> None:
        # Restore organization only. New rows are retained, inactive/unassigned.
        for table in TABLES:
            old = before[table]
            if table in {"career_company", "career_experience"}:
                self.conn.execute(
                    f"UPDATE {table} SET archived = 1 WHERE candidate_id = ?", (self.candidate_id,)
                )
            elif table == "career_evidence_link":
                self.conn.execute(
                    "UPDATE career_evidence_link SET experience_id = NULL WHERE candidate_id = ?",
                    (self.candidate_id,),
                )
            else:
                self.conn.execute(
                    "UPDATE career_company_decision SET decision = 'UNREVIEWED'"
                    " WHERE candidate_id = ?",
                    (self.candidate_id,),
                )
            for row in old:
                identity = (
                    ["id"]
                    if "id" in row
                    else (
                        ["candidate_id", "claim_key"]
                        if "claim_key" in row
                        else ["candidate_id", "first_label", "second_label"]
                    )
                )
                fields = [k for k in row if k not in identity]
                if table == "career_evidence_link":
                    fields.remove("category")
                self.conn.execute(
                    f"UPDATE {table} SET "
                    + ", ".join(f"{k} = ?" for k in fields)
                    + " WHERE "
                    + " AND ".join(f"{k} = ?" for k in identity),
                    tuple(row[k] for k in fields + identity),
                )

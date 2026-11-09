"""The canonical Master Resume and the resume identity, one per profile.

The Master is the broad document: every confirmed experience, every confirmed
statement verbatim, every confirmed skill. It is not tailored to any job and
nothing about a search (intent, fit, eligibility) decides what it holds.

Identity belongs to the Master. It is resume content: editing it never
writes Career Evidence, the search configuration, Search Fit or eligibility.

The Master is made from confirmed evidence once. Later evidence changes are
REPORTED (`evidence_changes`), never applied behind the person's back, so an
edited Master is never overwritten.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from typing import Any

from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc.models import (
    EVIDENCED_ORIGINS,
    PLACEHOLDER_NAMES,
    Bullet,
    CertificationEntry,
    CreatedFrom,
    DocumentKind,
    DocumentProvenance,
    ExperienceEntry,
    Identity,
    Link,
    LinkKind,
    Origin,
    Override,
    PartialDate,
    ResumeDocument,
    SkillGroup,
    SkillItem,
    sha256_text,
)
from career_agent.resume_doc.store import NotFound, ResumeStore, StoredDocument
from career_agent.storage.career_repo import HIGHLIGHT_CATEGORIES, SKILL_CATEGORIES, CareerRepo
from career_agent.storage.db import transaction
from career_agent.storage.workspace_repo import candidate_id_of, person_name

_PERIOD = re.compile(r"^(\d{4})(?:-(\d{2}))?$")

#: Where a skill was used, from its confirmed evidence only: JOB when some of
#: it sits in a job; otherwise LAB (a claim's own words say lab), PROJECT (a
#: project claim in no job) or UNKNOWN (nothing says where).
JOB, LAB, PROJECT, UNKNOWN = "JOB", "LAB", "PROJECT", "UNKNOWN"
# ponytail: a lab is read from the claim's own confirmed words; the evidence
# class proposed in RESUME_TAILOR_V1_DESIGN.md replaces this when it exists.
_LAB_WORDS = re.compile(r"(?i)\b(?:home\s?labs?|labs?|laborat[oó]rios?|laboratory)\b")
#: A skills group whose name already says its skills come from outside a job.
OUTSIDE_A_JOB = re.compile(
    r"(?i)\b(?:labs?|laborat\w*|independ\w*|personal|pessoa\w*|projects?|projetos?|proyectos?)\b"
)
#: Where such a skill goes. In a version for a job the UNKNOWN group is
#: hidden: off the page, still in the document, listed in the report. The
#: Master keeps the person's skills on the page, apart from a job's
#: (`KEPT_UNKNOWN`), and Analyze asks where each comes from.
SKILL_GROUPS = {
    LAB: {"en": "Lab", "pt": "Laboratório", "es": "Laboratorio"},
    PROJECT: {"en": "Independent projects", "pt": "Projetos independentes",
              "es": "Proyectos independientes"},
    UNKNOWN: {"en": "Origin not confirmed", "pt": "Origem não confirmada",
              "es": "Origen no confirmado"},
}  # fmt: skip
KEPT_UNKNOWN = {"en": "Other skills", "pt": "Outras competências", "es": "Otras habilidades"}


def claims_by_key(overview: dict[str, Any], rows: list[dict[str, Any]]) -> dict[str, dict]:
    """Confirmed claims by key, those of an archived experience left out."""
    placed = {str(e["id"]) for e in overview["experiences"]}
    return {
        r["claim_key"]: r for r in rows
        if r["state"] == "CONFIRMED"
        and (r.get("experience_id") is None or r["experience_id"] in placed)
    }  # fmt: skip


def skill_origin(keys: Iterable[str], claims: dict[str, Any]) -> str:
    """JOB, LAB, PROJECT or UNKNOWN for a skill citing `keys` (see above).
    `claims` maps a key to a row or object with experience_id, category, text."""
    rows = [claims[k] for k in keys if k in claims]

    def get(row: Any, name: str) -> Any:
        return row.get(name) if isinstance(row, dict) else getattr(row, name, None)

    if any(get(r, "experience_id") for r in rows):
        return JOB
    if any(_LAB_WORDS.search(str(get(r, "text") or "")) for r in rows):
        return LAB
    if any(get(r, "category") == "PROJECT" for r in rows):
        return PROJECT
    return UNKNOWN


def regroup_skills(
    data: dict[str, Any],
    claims: dict[str, Any],
    *,
    only: set[str] | None = None,
    keep_unknown: bool = False,
) -> list[dict[str, Any]]:
    """Move every evidenced skill (or those in `only`) that no job's evidence
    holds out of a shown group whose name does not say so: into Lab or
    Independent projects, or, when nothing says where, into a hidden group
    (a shown "Other skills" group with `keep_unknown`, the Master's choice).
    Nothing is deleted; the moves are returned as changes."""
    language = str(data.get("language") or "en")[:2]
    changes: list[dict[str, Any]] = []
    for group in list(data["skills"]):
        if group.get("hidden") or OUTSIDE_A_JOB.search(group["name"]):
            continue
        before = len(changes)
        for item in list(group["items"]):
            if (only is not None and item["id"] not in only) or not item.get("evidence_ids"):
                continue
            if Origin(item["origin"]) not in EVIDENCED_ORIGINS:
                continue  # the person's own words: the review asks about those
            origin = skill_origin(item["evidence_ids"], claims)
            if origin == JOB:
                continue
            names = KEPT_UNKNOWN if keep_unknown and origin == UNKNOWN else SKILL_GROUPS[origin]
            name = names.get(language, names["en"])
            if name == group["name"]:
                continue  # already where it belongs
            target = next((g for g in data["skills"] if g["name"] == name), None)
            if target is None:
                hidden = origin == UNKNOWN and not keep_unknown
                target = {"id": new_id(), "name": name, "hidden": hidden, "items": []}
                data["skills"].append(target)
            group["items"].remove(item)
            target["items"].append(item)
            changes.append({"op": "MOVE_SKILL", "line_id": item["id"], "after": item["label"],
                            "group": name, "origin": origin,
                            "evidence_ids": list(item["evidence_ids"])})  # fmt: skip
        if not group["items"] and len(changes) > before:
            data["skills"].remove(group)  # emptied by the moves
    return changes


def profile_labels(conn: sqlite3.Connection) -> set[str]:
    """The names this database goes by, which are not the person's name."""
    rows = conn.execute("SELECT label FROM database_identity").fetchall()
    return {str(r[0]) for r in rows if r[0]}


def real_name(value: Any, labels: Iterable[str] = ()) -> str:
    """`value` as a person's name, or "" when it is blank, a placeholder
    ("You") or one of the local profile's labels."""
    name = " ".join(str(value or "").split())
    taken = PLACEHOLDER_NAMES | {" ".join(label.split()).casefold() for label in labels}
    return "" if name.casefold() in taken else name


def resolve_identity(
    conn: sqlite3.Connection, *, contact: dict[str, Any] | None = None, labels: Iterable[str] = ()
) -> tuple[Identity, list[str]]:
    """The identity a new Master starts from, and what was left out and why.

    Name: an explicit resume contact name, else Career Agent's
    `candidate.display_name`, else blank. Neither may be a placeholder or a
    profile label. Email, phone, location and links come only from an
    explicit resume contact record, never from evidence prose."""
    labels = {*labels, *profile_labels(conn)}
    contact = contact or {}
    notes: list[str] = []
    name = real_name(contact.get("name"), labels) or real_name(person_name(conn), labels)
    fields: dict[str, Any] = {"full_name": name}
    for key in ("email", "phone"):
        value = " ".join(str(contact.get(key) or "").split())
        if value:
            fields[key] = value
    # The old record held one free-text place. It is kept whole, as typed,
    # rather than split into city, region and country by guesswork.
    place = " ".join(str(contact.get("location") or "").split())
    if place:
        fields["city"] = place
    links = []
    for key, kind in (
        ("linkedin", LinkKind.LINKEDIN),
        ("github", LinkKind.GITHUB),
        ("portfolio", LinkKind.PORTFOLIO),
    ):
        url = str(contact.get(key) or "").strip()
        if not url:
            continue
        if not re.match(r"^https?://", url, re.IGNORECASE):
            notes.append(f"{key}: no http(s) scheme, https:// was added")
            url = f"https://{url}"
        try:
            links.append(Link(id=new_id(), kind=kind, url=url))
        except ValidationError:
            notes.append(f"{key}: not a web address, left out")
    fields["links"] = links
    for key in [k for k in fields if k not in ("full_name", "links")]:
        try:
            Identity.model_validate({key: fields[key]})
        except ValidationError:
            notes.append(f"{key}: not a valid value, left out")
            del fields[key]
    if not name:
        notes.append("full_name: no real name on record, left blank")
    return Identity.model_validate(fields), notes


def _career(conn: sqlite3.Connection) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The Career Profile overview and its evidence items (empty with no candidate)."""
    candidate_id = candidate_id_of(conn)
    if not candidate_id:
        return {"experiences": []}, []
    repo = CareerRepo(conn, candidate_id)
    return repo.overview(), repo.items()


def partial_date(value: Any) -> PartialDate | None:
    """A "YYYY" or "YYYY-MM" period as a date; anything else is no date."""
    match = _PERIOD.match(str(value or ""))
    if not match:
        return None
    year, month = match.groups()
    return PartialDate(year=int(year), month=int(month) if month else None)


def master_from_career_profile(
    conn: sqlite3.Connection, identity: Identity, *, language: str = "en"
) -> tuple[ResumeDocument, list[str]]:
    """A MASTER document from CONFIRMED Career Profile data only, verbatim.

    Returns the document and a diagnostic line for every confirmed fact it
    could not place in a typed section. Nothing is guessed to make it fit.
    Experiences the person archived, and what they hold, are left out."""
    overview, items = _career(conn)
    confirmed = [r for r in items if r["state"] == "CONFIRMED"]
    notes: list[str] = []
    experience = []
    placed = {str(e["id"]) for e in overview["experiences"]}
    for entry in overview["experiences"]:
        start = partial_date(entry.get("period_start"))
        end = partial_date(entry.get("period_end"))
        current = bool(entry.get("current_role"))
        try:
            experience.append(
                ExperienceEntry(
                    id=new_id(),
                    employer=entry.get("company") or "",
                    display_title=entry.get("title") or "",
                    source_title=entry.get("title"),
                    start=start,
                    end=None if current else end,
                    current=current,
                    experience_id=str(entry["id"]),
                    bullets=[
                        Bullet(
                            id=new_id(),
                            text=h["text"],
                            origin=Origin.EVIDENCE_VERBATIM,
                            evidence_ids=[h["claim_key"]],
                        )
                        for h in entry.get("highlights", [])
                    ],
                )
            )
        except ValidationError as exc:
            notes.append(f"experience {entry['id']}: left out ({exc.errors()[0]['msg']})")
    skills: dict[str, dict[str, Any]] = {}
    certifications = []
    for item in confirmed:
        key, where = item["claim_key"], item.get("experience_id")
        if where is not None and where not in placed:
            continue  # an experience the person archived: theirs to leave out
        names = [item["text"]] if item["category"] in SKILL_CATEGORIES else []
        for name in [*names, *(item.get("tools") or [])]:
            label = " ".join(str(name).split())
            if not label:
                continue
            if len(label) > 60:
                notes.append(f"skill {key}: longer than a skill name, left out")
                continue
            fold = unicodedata.normalize("NFC", label).casefold()
            held = skills.setdefault(fold, {"label": label, "evidence": []})
            if key not in held["evidence"]:
                held["evidence"].append(key)
        if item["category"] == "CERTIFICATION":
            try:
                certifications.append(
                    CertificationEntry(id=new_id(), name=item["text"].strip(), claim_key=key)
                )
            except ValidationError:
                notes.append(f"certification {key}: not usable as a name, left out")
        elif item["category"] == "EDUCATION":
            notes.append(f"education {key}: no typed fields to place it, left out")
        elif item["category"] in HIGHLIGHT_CATEGORIES and where is None:
            notes.append(f"statement {key}: not in an experience, left out")
    groups = []
    if skills:
        groups.append(
            SkillGroup(
                id=new_id(),
                name="Skills",
                items=[
                    SkillItem(
                        id=new_id(),
                        label=s["label"],
                        origin=Origin.EVIDENCE_VERBATIM,
                        evidence_ids=s["evidence"],
                    )
                    for s in skills.values()
                ],
            )
        )
    doc = ResumeDocument(
        id=new_id(),
        kind=DocumentKind.MASTER,
        title="Master resume",
        language=language,
        identity=identity,
        experience=experience,
        certifications=certifications,
        skills=groups,
        provenance=DocumentProvenance(created_from=CreatedFrom.SCRATCH),
    )
    data = doc.model_dump(mode="json")
    for moved in regroup_skills(data, claims_by_key(overview, items), keep_unknown=True):
        if moved["origin"] == UNKNOWN:
            notes.append(
                f"skill {moved['after']}: no confirmed evidence says where; kept under"
                f" {moved['group']}, apart from a job's"
            )
    return ResumeDocument.model_validate(data), notes


def get_or_create_master(
    conn: sqlite3.Connection, *, labels: Iterable[str] = ()
) -> tuple[StoredDocument, list[str]]:
    """The profile's one current Master; made from confirmed evidence the
    first time. Never a second one (migration 0048 refuses it too)."""
    store = ResumeStore(conn)
    with transaction(conn):
        found = store.current_master()
        if found is not None:
            return found, []
        identity, notes = resolve_identity(conn, labels=labels)
        doc, more = master_from_career_profile(conn, identity)
        return store.create_document(doc), notes + more


def update_resume_identity(
    conn: sqlite3.Connection, identity: Identity, *, expected_sha256: str
) -> StoredDocument:
    """Replace the Master's identity and record the change as a revision.
    Only the resume changes: no Career Evidence, search setting or score."""
    store = ResumeStore(conn)
    with transaction(conn):
        master = store.current_master()
        if master is None:
            raise NotFound("there is no Master resume yet")
        doc = master.working.model_copy(update={"identity": identity})
        store.save_working_copy(master.id, doc, expected_sha256=expected_sha256)
        store.checkpoint_revision(master.id, "MANUAL_CHECKPOINT")
    return store.get_document(master.id)


def evidence_changes(conn: sqlite3.Connection, master: ResumeDocument) -> dict[str, list[str]]:
    """Which confirmed statements and experiences differ from what the Master
    was made from. A report only: applying it is the person's decision."""
    overview, _ = _career(conn)
    now = {
        h["claim_key"]: h["text"] for e in overview["experiences"] for h in e.get("highlights", [])
    }
    held: dict[str, str] = {}
    for entry in master.experience:
        for bullet in entry.bullets:
            if bullet.origin in EVIDENCED_ORIGINS:
                source = bullet.original_text if bullet.override is not Override.NONE else None
                for key in bullet.evidence_ids:
                    held[key] = source or bullet.text
    now_experiences = {str(e["id"]) for e in overview["experiences"]}
    held_experiences = {e.experience_id for e in master.experience if e.experience_id}
    return {
        "added": sorted(now.keys() - held.keys()),
        "removed": sorted(held.keys() - now.keys()),
        "changed": sorted(
            k for k in now.keys() & held.keys() if sha256_text(now[k]) != sha256_text(held[k])
        ),
        "experiences_added": sorted(now_experiences - held_experiences),
        "experiences_removed": sorted(held_experiences - now_experiences),
    }

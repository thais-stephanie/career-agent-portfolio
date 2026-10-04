"""What an import FOUND, and how a reviewed import becomes a ResumeDocument.

`ImportProposal` is not a ResumeDocument. It is what the parser
(`resume_doc.parse`) thinks it read: every value carries a confidence
(HIGH, MEDIUM or LOW: how sure the deterministic reader is that this source
text belongs to that field, never a percentage) and the source text it was
read from. Nothing is stored when it is made.

The page shows it for review, the person corrects it, and only Save sends it
back. The server trusts nothing in what comes back except the VALUES:
`to_document` builds the ResumeDocument itself, every line `origin=IMPORTED`
with no evidence ids, the kind from the person's choice, provenance
`created_from=IMPORT`; then the store validates it like any other write.
Importing is not confirmation: nothing here writes Career Evidence, the
candidate's identity, search settings or scores.

Idempotency: a proposal carries the document id it will be saved under,
drawn when the file was READ. Saving the same proposal again (a retried
request) returns the document already saved; reading the same file again is
a new proposal, a new id and a second document, as the person asked.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Annotated, Literal

from pydantic import Field

from career_agent.resume_doc.models import (
    DEFAULT_ORDER,
    EMAIL_SHAPE,
    DocumentKind,
    Id,
    LinkKind,
    ResumeDocument,
    _Model,
    content_sha256,
)
from career_agent.resume_doc.store import ResumeStore, ResumeStoreError, StoredDocument
from career_agent.storage.db import transaction

PARSER_VERSION = "resume-import-1"
Confidence = Literal["HIGH", "MEDIUM", "LOW"]
Destination = Literal["IMPORTED", "MASTER", "REPLACE_MASTER"]
Short = Annotated[str, Field(max_length=300)]
Long = Annotated[str, Field(max_length=5_000)]


class Source(_Model):
    """Where a value was read: the reader's place ("page 1, line 4") and the
    source text. A value is a substring of it (ignoring case), except a date,
    which is the "YYYY" or "YYYY-MM" reading of a date written there, and a
    link, whose source is the link target the file itself carries."""

    where: Annotated[str, Field(max_length=80)]
    text: Long


class Found(_Model):
    """One proposed value. `note` names why it needs a look (a code the page
    words): TWO_NAMES, LOCATION_OR_COMPANY, ROLE_OR_COMPANY, UNKNOWN_HEADING..."""

    value: Long
    confidence: Confidence = "HIGH"
    source: Source | None = None
    alternatives: list[Long] = Field(default=[], max_length=5)
    note: Annotated[str, Field(max_length=40)] | None = None


class FoundLine(_Model):
    id: Id
    text: Found


class FoundLink(_Model):
    id: Id
    kind: LinkKind
    url: Found


class FoundEntry(_Model):
    """An experience, education, project or certification entry. `title` is
    the role, degree, project or certificate; `org` the company, school,
    project role or issuer; dates are "YYYY" or "YYYY-MM" as the file states."""

    id: Id
    title: Found | None = None
    org: Found | None = None
    location: Found | None = None
    #: A project's address, as printed.
    url: Found | None = None
    start: Found | None = None
    end: Found | None = None
    current: bool = False
    lines: list[FoundLine] = Field(default=[], max_length=200)


class FoundGroup(_Model):
    """A skill group, or a section with no typed home ("Other content")."""

    id: Id
    name: Found
    lines: list[FoundLine] = Field(default=[], max_length=500)


class FoundIdentity(_Model):
    name: Found | None = None
    email: Found | None = None
    phone: Found | None = None
    location: Found | None = None
    links: list[FoundLink] = Field(default=[], max_length=10)


class ImportReport(_Model):
    filename: Short
    format: Literal["PDF", "DOCX"]
    pages: int
    characters: int
    parser_version: str = PARSER_VERSION
    sections: list[str] = []
    confidence: dict[Confidence, int] = {}
    warnings: list[str] = []
    unmapped: int = 0


class ImportProposal(_Model):
    document_id: Id
    import_id: Id
    title: Short
    language: Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
    identity: FoundIdentity = FoundIdentity()
    headline: FoundLine | None = None
    summary: FoundLine | None = None
    experience: list[FoundEntry] = Field(default=[], max_length=100)
    projects: list[FoundEntry] = Field(default=[], max_length=100)
    education: list[FoundEntry] = Field(default=[], max_length=50)
    certifications: list[FoundEntry] = Field(default=[], max_length=100)
    skills: list[FoundGroup] = Field(default=[], max_length=50)
    other: list[FoundGroup] = Field(default=[], max_length=50)
    report: ImportReport
    #: Every line read, for "View extracted text". Not needed to save.
    source: list[Source] = Field(default=[], max_length=5_000)


# ------------------------------------------------------------------- save


class ImportIncomplete(ResumeStoreError):
    """Fields the document needs and the review left empty or invalid."""

    def __init__(self, fields: list[str]) -> None:
        super().__init__("some fields need a value before saving")
        self.fields = fields


class MasterExists(ResumeStoreError):
    """Saving as the Master while one exists, without choosing to replace it."""


class ImportConflict(ResumeStoreError):
    """This proposal's document id is already a different document."""


_DATE = re.compile(r"^(?P<year>\d{4})(?:-(?P<month>\d{2}))?$")


def _date(found: Found | None, ref: str, missing: list[str]) -> dict[str, int | None] | None:
    if found is None or not found.value.strip():
        return None
    match = _DATE.match(found.value.strip())
    if not match or not 1900 <= int(match["year"]) <= 2100:
        missing.append(ref)
        return None
    month = int(match["month"]) if match["month"] else None
    if month is not None and not 1 <= month <= 12:
        missing.append(ref)
        return None
    return {"year": int(match["year"]), "month": month}


def _text(found: Found | None) -> str | None:
    return " ".join(found.value.split()) or None if found else None


def _line(line: FoundLine) -> dict[str, object]:
    return {"id": line.id, "text": line.text.value.strip(), "origin": "IMPORTED"}


def _one(line: FoundLine | None) -> dict[str, object] | None:
    return _line(line) if line is not None and line.text.value.strip() else None


def _place(found: Found | None) -> dict[str, str | None]:
    """City, region and country printed on the resume, from the reviewed
    location text. The country only when the places resolver names exactly one."""
    from career_agent.match.places import resolve_place

    text = _text(found)
    if not text:
        return {}
    parts = [p.strip() for p in text.split(",") if p.strip()]
    countries = resolve_place(text).countries
    return {
        "city": parts[0][:120] if len(parts) > 1 or not countries else None,
        "region": parts[1][:120] if len(parts) >= 3 else None,
        "country": countries[0] if len(countries) == 1 else None,
    }


def to_document(proposal: ImportProposal, kind: DocumentKind) -> ResumeDocument:
    """The ResumeDocument the reviewed proposal describes. Raises
    `ImportIncomplete` naming every field a document cannot do without."""
    missing: list[str] = []

    def needed(found: Found | None, ref: str) -> str:
        value = _text(found)
        if not value:
            missing.append(ref)
        return value or ""

    def lines(items: list[FoundLine]) -> list[dict[str, object]]:
        return [_line(item) for item in items if item.text.value.strip()]

    def dated(entry: FoundEntry) -> dict[str, object]:
        start = _date(entry.start, f"{entry.id}/start", missing)
        end = None if entry.current else _date(entry.end, f"{entry.id}/end", missing)
        if (
            start
            and end
            and (end["year"], end["month"] or 0)
            < (
                start["year"],
                start["month"] or 0,
            )
        ):
            missing.append(f"{entry.id}/end")
            end = None
        return {"start": start, "end": end}

    ident = proposal.identity
    email = _text(ident.email)
    if email and not re.match(EMAIL_SHAPE, email):
        missing.append("identity/email")
        email = None
    links = []
    for link in ident.links:
        url = _text(link.url)
        if url and not re.match(r"^https?://", url):
            url = "https://" + url
        if url and re.match(r"^https?://\S+$", url) and len(url) <= 500:
            links.append({"id": link.id, "kind": link.kind, "url": url})
        elif url:
            missing.append(f"identity/links/{link.id}")
    data: dict[str, object] = {
        "id": proposal.document_id,
        "kind": kind.value,
        "title": needed(Found(value=proposal.title), "title"),
        "language": proposal.language,
        "identity": {
            "full_name": _text(ident.name) or "",
            "email": email,
            "phone": (_text(ident.phone) or "")[:40] or None,
            **_place(ident.location),
            "links": links,
        },
        "headline": _one(proposal.headline),
        "summary": _one(proposal.summary),
        "experience": [
            {
                "id": e.id,
                "employer": needed(e.org, f"{e.id}/org"),
                "display_title": needed(e.title, f"{e.id}/title"),
                "location": _text(e.location),
                "current": e.current,
                **dated(e),
                "bullets": lines(e.lines),
            }
            for e in proposal.experience
        ],
        "projects": [
            {
                "id": e.id,
                "name": needed(e.title, f"{e.id}/title"),
                "role": _text(e.org),
                "url": _text(e.url),
                **dated(e),
                "bullets": lines(e.lines),
            }
            for e in proposal.projects
        ],
        "education": [
            {
                "id": e.id,
                "institution": needed(e.org, f"{e.id}/org"),
                "degree": _text(e.title),
                "location": _text(e.location),
                **dated(e),
                "bullets": lines(e.lines),
            }
            for e in proposal.education
        ],
        "certifications": [
            {
                "id": e.id,
                "name": needed(e.title, f"{e.id}/title"),
                "issuer": _text(e.org),
                "issued": _date(e.start, f"{e.id}/start", missing),
            }
            for e in proposal.certifications
        ],
        "skills": [
            {
                "id": g.id,
                "name": needed(g.name, f"{g.id}/name"),
                "items": [
                    {"id": i.id, "label": i.text.value.strip()[:300], "origin": "IMPORTED"}
                    for i in g.lines
                    if i.text.value.strip()
                ],
            }
            for g in proposal.skills
        ],
        "custom_sections": [
            {"id": g.id, "heading": needed(g.name, f"{g.id}/name"), "items": lines(g.lines)}
            for g in proposal.other
        ],
        "layout": {"section_order": [*DEFAULT_ORDER, *(f"custom:{g.id}" for g in proposal.other)]},
        "provenance": {
            "created_from": "IMPORT",
            "import_id": f"{PARSER_VERSION}:{proposal.report.format}:{proposal.import_id}",
        },
    }
    if missing:
        raise ImportIncomplete(missing)
    return ResumeDocument.model_validate(data)


def save_import(
    conn: sqlite3.Connection, proposal: ImportProposal, destination: Destination
) -> StoredDocument:
    """Store the reviewed import, once. `REPLACE_MASTER` archives the current
    Master (kept whole, with its history) and makes this one the Master in the
    same transaction; `MASTER` refuses when a Master exists."""
    kind = DocumentKind.IMPORTED if destination == "IMPORTED" else DocumentKind.MASTER
    doc = to_document(proposal, kind)
    sha = content_sha256(doc)
    store = ResumeStore(conn)
    with transaction(conn):
        row = conn.execute(
            "SELECT content_sha256 FROM resume_revision WHERE document_id = ? AND seq = 1",
            (doc.id,),
        ).fetchone()
        if row is not None:
            if row[0] != sha:
                raise ImportConflict(doc.id)
            return store.get_document(doc.id)  # the same save, retried
        master = store.current_master()
        if master is not None and destination == "MASTER":
            raise MasterExists(master.id)
        if master is not None and destination == "REPLACE_MASTER":
            store.archive_document(master.id)
        return store.create_document(doc, reason="IMPORTED")

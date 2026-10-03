"""ResumeDocument: the one resume model (schema 1.0).

Builder, import, tailoring, the editor, analysis and every exporter read and
write this model and nothing else. `schemas/resume-document.v1.json` is
generated from it (`scripts/make_resume_schema.py`); these classes are the
source of truth.

Rules enforced here rather than by convention:

* every item has a stable ULID; a positional id ("b001") is refused;
* text whose origin claims evidence (verbatim, rule rewrite, AI rewrite) names
  the evidence it rests on; typed or imported text may stand alone and is never
  Career Evidence because it was saved here;
* a TAILORED document has a target and the others do not; the target's title
  is the job's, never a rewrite of a role's `source_title`;
* dates are structured, a month is never invented, and an end is not before
  its start;
* an empty or placeholder name loads (a document may be unfinished) and is
  reported by `Identity.name_finding`, never replaced by a default.

Version metadata (V1, V2, preferred, archived) lives in the storage rows, not
in the content: see `career_agent.resume_doc.store`.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

SCHEMA_VERSION: Literal["1.0"] = "1.0"

#: A ULID in Crockford base32, the shape `career_agent.clock.new_id` produces.
Id = Annotated[str, Field(pattern=r"^[0-9A-HJKMNP-TV-Z]{26}$")]
Text = Annotated[str, Field(max_length=5_000)]
Name = Annotated[str, Field(min_length=1, max_length=300)]

#: Labels that stand in for a person and are not their name.
PLACEHOLDER_NAMES = frozenset({"you", "candidate", "my profile", "meu perfil"})


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentKind(StrEnum):
    MASTER = "MASTER"
    TAILORED = "TAILORED"
    IMPORTED = "IMPORTED"
    SCRATCH = "SCRATCH"


class Origin(StrEnum):
    EVIDENCE_VERBATIM = "EVIDENCE_VERBATIM"
    RULE_REWRITE = "RULE_REWRITE"
    AI_REWRITE = "AI_REWRITE"
    USER_AUTHORED = "USER_AUTHORED"
    IMPORTED = "IMPORTED"


#: Origins that claim to rest on confirmed evidence, and so must name it.
EVIDENCED_ORIGINS = frozenset({Origin.EVIDENCE_VERBATIM, Origin.RULE_REWRITE, Origin.AI_REWRITE})


class Override(StrEnum):
    NONE = "NONE"
    EDITED = "EDITED"
    LOCKED = "LOCKED"


class ClaimFlag(StrEnum):
    UNSUPPORTED_NUMBER = "UNSUPPORTED_NUMBER"
    UNSUPPORTED_TERM = "UNSUPPORTED_TERM"
    OVERSTATEMENT = "OVERSTATEMENT"
    UNEVIDENCED = "UNEVIDENCED"


class LinkKind(StrEnum):
    LINKEDIN = "LINKEDIN"
    GITHUB = "GITHUB"
    PORTFOLIO = "PORTFOLIO"
    WEBSITE = "WEBSITE"
    OTHER = "OTHER"


class CreatedFrom(StrEnum):
    SCRATCH = "SCRATCH"
    IMPORT = "IMPORT"
    MASTER_COPY = "MASTER_COPY"
    TAILOR = "TAILOR"
    DUPLICATE = "DUPLICATE"


# ------------------------------------------------------------------ identity


class Link(_Model):
    id: Id
    kind: LinkKind
    url: Annotated[str, Field(min_length=1, max_length=500)]
    label: str | None = None


class IdentityVisibility(_Model):
    """What this document prints of the identity; the values stay stored."""

    email: bool = True
    phone: bool = True
    location: bool = True
    links: bool = True


class Identity(_Model):
    # Deliberately absent: photo, age, gender, marital status, nationality.
    full_name: Annotated[str, Field(max_length=300)] = ""
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    region: str | None = None
    country: Annotated[str, Field(pattern=r"^[A-Z]{2}$")] | None = None
    links: list[Link] = []
    show: IdentityVisibility = IdentityVisibility()

    def name_finding(self) -> Literal["NAME_MISSING", "NAME_PLACEHOLDER"] | None:
        name = " ".join(self.full_name.split())
        if not name:
            return "NAME_MISSING"
        if name.casefold() in PLACEHOLDER_NAMES:
            return "NAME_PLACEHOLDER"
        return None


class Target(_Model):
    """The job a TAILORED version is for, copied from its JD snapshot."""

    jd_snapshot_id: Id
    job_id: str | None = None
    title: Name
    company: str | None = None


# --------------------------------------------------------------------- text


class TextBlock(_Model):
    id: Id
    text: Text
    origin: Origin
    evidence_ids: list[str] = []
    requirement_ids: list[str] = []
    override: Override = Override.NONE
    #: The wording before a person's edit; only an overridden block has one.
    original_text: Text | None = None

    @model_validator(mode="after")
    def _provenance(self) -> TextBlock:
        if self.origin in EVIDENCED_ORIGINS and not self.evidence_ids:
            raise ValueError(f"{self.origin.value} text must name the evidence it rests on")
        if self.override is Override.NONE and self.original_text is not None:
            raise ValueError("original_text belongs to an edited or locked block")
        return self


class Bullet(TextBlock):
    hidden: bool = False
    flags: list[ClaimFlag] = []


class PartialDate(_Model):
    """A year and, only when the source states it, a month."""

    year: Annotated[int, Field(ge=1900, le=2100)]
    month: Annotated[int, Field(ge=1, le=12)] | None = None

    def key(self) -> tuple[int, int]:
        return (self.year, self.month or 0)


class _Dated(_Model):
    start: PartialDate | None = None
    end: PartialDate | None = None

    @model_validator(mode="after")
    def _chronology(self) -> _Dated:
        if self.start and self.end and self.end.key() < self.start.key():
            raise ValueError("end is before start")
        return self


# ----------------------------------------------------------------- sections


class ExperienceEntry(_Dated):
    id: Id
    hidden: bool = False
    employer: Name
    #: How this resume presents the role. May be reworded; `source_title` may not.
    display_title: Name
    #: The title as confirmed in Career Evidence.
    source_title: str | None = None
    location: str | None = None
    current: bool = False
    claim_key: str | None = None
    bullets: list[Bullet] = []

    @model_validator(mode="after")
    def _current_has_no_end(self) -> ExperienceEntry:
        if self.current and self.end is not None:
            raise ValueError("a current role has no end date")
        return self


class ProjectEntry(_Dated):
    id: Id
    hidden: bool = False
    name: Name
    role: str | None = None
    url: str | None = None
    claim_key: str | None = None
    bullets: list[Bullet] = []


class EducationEntry(_Dated):
    id: Id
    hidden: bool = False
    institution: Name
    degree: str | None = None
    field_of_study: str | None = None
    location: str | None = None
    claim_key: str | None = None
    bullets: list[Bullet] = []


class CertificationEntry(_Model):
    id: Id
    hidden: bool = False
    name: Name
    issuer: str | None = None
    issued: PartialDate | None = None
    expires: PartialDate | None = None
    claim_key: str | None = None


class SkillItem(_Model):
    id: Id
    label: Name
    origin: Origin
    evidence_ids: list[str] = []

    @model_validator(mode="after")
    def _provenance(self) -> SkillItem:
        if self.origin in EVIDENCED_ORIGINS and not self.evidence_ids:
            raise ValueError(f"{self.origin.value} skill must name the evidence it rests on")
        return self


class SkillGroup(_Model):
    id: Id
    name: Name
    hidden: bool = False
    items: list[SkillItem] = []


class CustomSection(_Model):
    """The escape hatch for anything the typed sections do not cover."""

    id: Id
    heading: Name
    hidden: bool = False
    items: list[Bullet] = []


# ---------------------------------------------------------- layout, design

SectionRef = Annotated[
    str,
    Field(
        pattern=r"^(headline|summary|experience|projects|education|certifications|skills"
        r"|custom:[0-9A-HJKMNP-TV-Z]{26})$"
    ),
]
DEFAULT_ORDER = [
    "headline",
    "summary",
    "experience",
    "projects",
    "education",
    "certifications",
    "skills",
]


class Layout(_Model):
    """Which sections show, in what order, under what heading. Not content."""

    section_order: list[SectionRef] = DEFAULT_ORDER
    hidden_sections: list[SectionRef] = []
    headings: dict[SectionRef, Annotated[str, Field(max_length=80)]] = {}

    @model_validator(mode="after")
    def _sets(self) -> Layout:
        if len(set(self.section_order)) != len(self.section_order):
            raise ValueError("a section appears twice in section_order")
        # A set in meaning: stored sorted, so equal layouts hash equally.
        self.hidden_sections = sorted(set(self.hidden_sections))
        return self


class Page(_Model):
    size: Literal["A4", "LETTER"] = "A4"
    margins_mm: Annotated[float, Field(ge=8, le=30)] = 16


class Typography(_Model):
    #: A family the renderer maps to its own bundled fonts; never a file path.
    font: Literal["sans", "serif"] = "sans"
    base_pt: Annotated[float, Field(ge=9.5, le=12)] = 10.5
    line_height: Annotated[float, Field(ge=1.0, le=1.6)] = 1.25


class Design(_Model):
    template: Literal["clean", "modern", "compact"] = "clean"
    page: Page = Page()
    typography: Typography = Typography()
    spacing: Literal["tight", "normal", "airy"] = "normal"
    accent: Literal["black", "navy", "teal", "burgundy", "forest"] = "black"
    date_format: Literal["mon_yyyy", "mm_yyyy", "yyyy"] = "mon_yyyy"


class DocumentProvenance(_Model):
    created_from: CreatedFrom
    master_document_id: Id | None = None
    master_revision_id: Id | None = None
    import_id: str | None = None
    tailoring_run_id: Id | None = None


# ---------------------------------------------------------------- document


class ResumeDocument(_Model):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    id: Id
    kind: DocumentKind
    title: Name
    language: Annotated[str, Field(pattern=r"^[a-z]{2}(-[A-Z]{2})?$")]
    identity: Identity = Identity()
    target: Target | None = None
    headline: TextBlock | None = None
    summary: TextBlock | None = None
    experience: list[ExperienceEntry] = []
    projects: list[ProjectEntry] = []
    education: list[EducationEntry] = []
    certifications: list[CertificationEntry] = []
    skills: list[SkillGroup] = []
    custom_sections: list[CustomSection] = []
    layout: Layout = Layout()
    design: Design = Design()
    provenance: DocumentProvenance

    @model_validator(mode="after")
    def _whole(self) -> ResumeDocument:
        if (self.kind is DocumentKind.TAILORED) != (self.target is not None):
            raise ValueError("a TAILORED document has a target, and only a TAILORED one")
        ids = _item_ids(self.model_dump())
        if len(ids) != len(set(ids)):
            raise ValueError("two items in this document share an id")
        customs = {f"custom:{c.id}" for c in self.custom_sections}
        refs = [*self.layout.section_order, *self.layout.hidden_sections, *self.layout.headings]
        unknown = {r for r in refs if r.startswith("custom:")} - customs
        if unknown:
            raise ValueError(f"layout names custom sections this document lacks: {sorted(unknown)}")
        return self


def _item_ids(node: Any, top: bool = True) -> list[str]:
    """Every item id inside the document (not the document's own)."""
    found: list[str] = []
    if isinstance(node, dict):
        if not top and isinstance(node.get("id"), str):
            found.append(node["id"])
        for value in node.values():
            found.extend(_item_ids(value, top=False))
    elif isinstance(node, list):
        for value in node:
            found.extend(_item_ids(value, top=False))
    return found


# ------------------------------------------------------ loading and hashing


class UnsupportedSchemaVersion(ValueError):
    """A document written by a version of Career Agent this one cannot read."""


#: Old version -> function returning the payload one version newer. Empty
#: while 1.0 is the only version; `upgrade_resume_document` is still the only
#: way a stored document is read, so the first upgrade has one place to go.
UPGRADES: dict[str, Any] = {}


def upgrade_resume_document(payload: dict[str, Any] | str) -> ResumeDocument:
    """Read a stored or received document, upgrading an older schema first."""
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    version = data.get("schema_version")
    while version in UPGRADES:
        data = UPGRADES[version](data)
        version = data.get("schema_version")
    if version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"resume document schema {version!r} is not one this version reads"
            f" (it reads {SCHEMA_VERSION})"
        )
    return ResumeDocument.model_validate(data)


def canonical_json(doc: ResumeDocument) -> str:
    """The one serialisation that is stored and hashed: sorted keys, no spaces."""
    return json.dumps(
        doc.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def content_sha256(doc: ResumeDocument) -> str:
    return sha256_text(canonical_json(doc))

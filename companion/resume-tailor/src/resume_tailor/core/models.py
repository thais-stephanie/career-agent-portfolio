# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Domain models shared by every core service.

These are the contract between the pipeline stages and, later, between Career
Agent and this package. Nothing in here knows about HTTP, files or LLM vendors.

Two ideas matter more than the rest:

* An ``EvidenceRecord`` is the only source of truth a generated sentence may
  draw on. Every bullet in a ``GeneratedResume`` carries ``evidence_ids``; the
  validator rejects anything it cannot trace.
* A ``Position`` (employer, official title, dates) is deterministic data. The
  generator never writes those fields; it references ``position_id``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --------------------------------------------------------------------------
# Evidence bank
# --------------------------------------------------------------------------


class Verification(StrEnum):
    """How much the record can be trusted, by provenance."""

    PRIMARY = "primary"  # case study / detailed first-person account
    DERIVED = "derived"  # resume or LinkedIn wording; secondary
    CONFLICTING = "conflicting"  # sources disagree; see ``conflict_ids``
    UNVERIFIED = "unverified"
    USER_VERIFIED = "user_verified"  # confirmed by the candidate; outranks derived documents


class Proficiency(StrEnum):
    """Depth of the claim. Stops prose from upgrading familiarity into expertise."""

    LED = "led"  # designed, owned and delivered end to end
    HANDS_ON = "hands_on"  # built / used directly in production work
    INTEGRATION = "integration"  # connected to it via API; not an administrator of it
    EXPOSURE = "exposure"  # listed in a skills section or a POC only
    CERTIFICATION = "certification"


class NumberScope(BaseModel):
    """What a number in a record measures, and the words that must not be attached to it.

    ``122`` may describe the size of an environment without meaning the candidate owned all
    122 workflows: an ownership or totality word within ``window`` words of the number, with no
    ``unless_between`` word between them, widens the number's scope and fails validation."""

    model_config = ConfigDict(extra="forbid")
    number: str
    meaning: str
    not_near: list[str] = Field(
        default_factory=lambda: [
            "sole",
            "solely",
            "owner",
            "owned",
            "own",
            "owning",
            "all",
            "entire",
            "every",
            "each",
            "managed",
            "responsible",
        ]
    )
    unless_between: list[str] = Field(default_factory=list)
    window: int = 8


class EvidenceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    position_id: str
    company: str
    role: str
    start: str  # YYYY-MM
    end: str | None = None  # YYYY-MM or None for present
    project: str | None = None
    claim: str  # one factual sentence
    detailed_context: str = ""
    resume_text: str  # a bullet-ready phrasing that only uses source facts
    summary_text: str | None = None  # optional shorter phrasing of the same facts, for the summary
    superseded_text: str | None = (
        None  # the source wording an override replaced; kept verbatim, never rendered
    )
    skills: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    not_evidence_for: list[str] = Field(
        default_factory=list
    )  # terms mentioned as context/outcome only; never vouched for
    proficiency: Proficiency = Proficiency.HANDS_ON
    # per-tool depth when it differs from the record's overall proficiency, e.g. a hands-on delivery
    # record that merely lists Salesforce among the tools used: {"Salesforce": "exposure"}
    tool_proficiency: dict[str, Proficiency] = Field(default_factory=dict)
    # ids of primary/user-verified records whose facts corroborate this record's claim; lifts the
    # secondary-source ceilings (see evidence/provenance.py) without pretending the source is primary
    corroborated_by: list[str] = Field(default_factory=list)
    # numbers whose meaning must not be widened by ownership/scope words placed next to them
    number_scopes: list[NumberScope] = Field(default_factory=list)

    def proficiency_for(self, term: str) -> Proficiency:
        from resume_tailor.core.lexicon import (
            canonical,
        )

        for tool, prof in self.tool_proficiency.items():
            if canonical(tool) == canonical(term):
                return prof
        return self.proficiency

    source_file: str
    source_reference: str = ""
    sources: list[str] = Field(default_factory=list)
    verification: Verification = Verification.DERIVED
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    conflict_ids: list[str] = Field(default_factory=list)
    include_by_default: bool = True

    @property
    def terms(self) -> list[str]:
        """Every term this record is allowed to vouch for."""
        return [*self.skills, *self.technologies, *self.domains, *self.aliases]


class PositionKind(StrEnum):
    """What a position counts towards when computing tenure."""

    EMPLOYMENT = "employment"  # professional role
    INTERNSHIP = "internship"
    JUNIOR_ENTERPRISE = "junior_enterprise"  # university junior-enterprise consulting/project work
    INDEPENDENT = "independent"  # self-directed project work
    CROSS_CUTTING = "cross_cutting"  # container for tenure/education/skills facts; never rendered


class Position(BaseModel):
    """One line of employment history. Dates and titles are never generated."""

    model_config = ConfigDict(extra="forbid")

    id: str
    company: str
    title: str
    start: str
    end: str | None = None
    location: str = ""
    company_blurb: str = ""
    kind: PositionKind = PositionKind.EMPLOYMENT
    include_by_default: bool = True
    conflict_ids: list[str] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)


class Education(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    institution: str
    degree: str
    start: str | None = None
    end: str | None = None
    sources: list[str] = Field(default_factory=list)
    include_by_default: bool = True


class Certification(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    issuer: str
    sources: list[str] = Field(default_factory=list)
    conflict_ids: list[str] = Field(default_factory=list)
    include_by_default: bool = True
    verification: Verification = (
        Verification.DERIVED
    )  # user_verified via an override renders despite an open conflict
    overrides_applied: list[str] = Field(default_factory=list)


class SourceConflict(BaseModel):
    """Two source documents disagree. Both statements are preserved verbatim."""

    model_config = ConfigDict(extra="forbid")
    id: str
    topic: str
    statements: list[dict[str, str]]  # [{"source": ..., "statement": ...}]
    resolution: str = "unresolved"
    note: str = ""
    resolved_by: str | None = (
        None  # e.g. "user_verified:override_001"; statements are never altered
    )


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    portfolio: str = (
        ""  # professional portfolio URL; shown when the target profile allows and space permits
    )
    languages: list[str] = Field(default_factory=list)
    overrides_applied: list[str] = Field(default_factory=list)


class UserOverride(BaseModel):
    """A fact confirmed by the candidate. Stored separately from the source bank
    (``data/evidence/user_overrides.json``) and applied at load time, so original
    source statements keep their provenance and the override is auditable."""

    model_config = ConfigDict(extra="forbid")
    id: str
    topic: str
    applies_to: str  # position | candidate | record | certification | conflict
    target_id: str = ""
    field: str = ""
    value: Any = None
    conflict_id: str | None = None
    values: dict[str, Any] = Field(
        default_factory=dict
    )  # several fields of the target set at once (record overrides)
    record_verification: dict[str, str] = Field(
        default_factory=dict
    )  # record id -> verification after override
    certification_verification: dict[str, str] = Field(
        default_factory=dict
    )  # certification id -> verification after override
    verified_clauses: list[str] = Field(
        default_factory=list
    )  # what exactly the candidate confirmed (clause-scoped)
    confirmed_by: str = "user"
    confirmed_at: str = ""
    note: str = ""


class EvidenceBank(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: Candidate
    positions: list[Position]
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    records: list[EvidenceRecord]
    conflicts: list[SourceConflict] = Field(default_factory=list)
    sources: dict[str, str] = Field(default_factory=dict)  # source key -> file path
    overrides: list[UserOverride] = Field(
        default_factory=list
    )  # populated by the loader, not stored in the bank file


# --------------------------------------------------------------------------
# Job analysis
# --------------------------------------------------------------------------


class RequirementCategory(StrEnum):
    MUST_HAVE = "must_have"
    NICE_TO_HAVE = "nice_to_have"
    RESPONSIBILITY = "responsibility"
    TECHNOLOGY = "technology"
    DOMAIN = "domain"
    CAPABILITY = "business_capability"


class Requirement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str  # JD wording, preserved for ATS
    category: RequirementCategory
    terms: list[str] = Field(default_factory=list)  # lexicon hits (normalized)
    weight: float = 1.0


class JobAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_title: str = ""
    company: str = ""
    seniority: str = ""
    domain: list[str] = Field(default_factory=list)
    must_have: list[str] = Field(default_factory=list)
    nice_to_have: list[str] = Field(default_factory=list)
    responsibilities: list[str] = Field(default_factory=list)
    technologies: list[str] = Field(default_factory=list)
    business_capabilities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    customer_facing_expectations: list[str] = Field(default_factory=list)
    management_expectations: list[str] = Field(default_factory=list)
    potential_hard_filters: list[str] = Field(default_factory=list)
    years_required: int | None = None
    # statements about the role's shape (no-code only, location, schedule, not customer-facing):
    # they are conditions/preferences, not skills, so they never become requirements or gaps
    role_scope_observations: list[str] = Field(default_factory=list)
    requirements: list[Requirement] = Field(default_factory=list)
    analysis_source: str = "deterministic"  # deterministic | llm+deterministic


# --------------------------------------------------------------------------
# Matching
# --------------------------------------------------------------------------


class MatchType(StrEnum):
    DIRECT = "direct"
    TRANSFERABLE = "transferable"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"


MATCH_RANK = {
    MatchType.DIRECT: 3,
    MatchType.TRANSFERABLE: 2,
    MatchType.PARTIAL: 1,
    MatchType.UNSUPPORTED: 0,
}


class MatchedEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_id: str
    score: float
    match_type: MatchType
    matched_terms: list[str] = Field(default_factory=list)
    reason: str = ""


class RequirementMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    requirement_id: str
    requirement_text: str
    category: RequirementCategory
    match_type: MatchType
    evidence: list[MatchedEvidence] = Field(default_factory=list)
    rationale: str = ""
    deterministic_ceiling: MatchType = MatchType.UNSUPPORTED
    hard_capped: bool = False  # a literal hit was capped (admin/cert/years/exposure); LLM may not exceed the ceiling


class MatchReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    matches: list[RequirementMatch]
    gaps: list[str] = Field(default_factory=list)  # requirement ids with no support
    coverage: dict[str, float] = Field(default_factory=dict)
    matching_source: str = "deterministic"


# --------------------------------------------------------------------------
# Strategy
# --------------------------------------------------------------------------


class PositionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_id: str
    relevance: float
    max_bullets: int
    featured_evidence_ids: list[str] = Field(default_factory=list)
    note: str = ""


class ResumeStrategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_profile: str
    profile_inferred: bool = True
    profile_scores: dict[str, float] = Field(default_factory=dict)
    recommended_title: str
    positioning: str = ""
    position_plans: list[PositionPlan] = Field(default_factory=list)
    emphasized_projects: list[str] = Field(default_factory=list)
    evidence_to_feature: list[str] = Field(default_factory=list)
    content_to_reduce: list[str] = Field(default_factory=list)
    terms_to_introduce: list[dict[str, Any]] = Field(default_factory=list)
    underrepresented_skills: list[str] = Field(default_factory=list)
    irrelevant_content: list[str] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)
    target_length_pages: int = 2
    rationale: str = ""
    fit_notes: list[str] = Field(
        default_factory=list
    )  # role-scope / preference observations, never skill gaps
    hero_evidence_ids: list[str] = Field(
        default_factory=list
    )  # 1-3 strongest proof points; shape summary and first bullets; never trimmed
    evidence_value: dict[str, dict[str, float]] = Field(
        default_factory=dict
    )  # per-record value factors (transparent)
    strategy_source: str = "deterministic"


# --------------------------------------------------------------------------
# Generated resume
# --------------------------------------------------------------------------


class ClaimBinding(BaseModel):
    """One clause of a bullet bound to exactly one evidence record.

    Required whenever a bullet cites more than one record: every lexicon term
    and every number in the bullet must fall inside a clause, and each clause
    is validated against its own record only, so a number supported by record
    A can never ride along on a claim that only record B supports."""

    model_config = ConfigDict(extra="forbid")
    clause: str
    evidence_id: str


class Bullet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    text: str
    evidence_ids: list[str] = Field(default_factory=list)
    bindings: list[ClaimBinding] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)
    origin: str = "rewritten"  # rewritten | verbatim | kept | fallback
    status: str = "pending"  # pending | supported | rejected | replaced


class ExperienceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_id: str
    company: str
    title: str
    start: str
    end: str | None
    location: str = ""
    company_blurb: str = ""
    bullets: list[Bullet] = Field(default_factory=list)


class SkillGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    items: list[str]


class GeneratedResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate: Candidate
    headline: str
    summary: list[Bullet] = Field(default_factory=list)  # one entry per sentence
    experience: list[ExperienceEntry] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    education: list[Education] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)
    generation_source: str = "deterministic"
    estimated_pages: float = 0.0
    estimated_words: int = 0

    def all_bullets(self) -> list[tuple[str, Bullet]]:
        out: list[tuple[str, Bullet]] = [("summary", b) for b in self.summary]
        for e in self.experience:
            out.extend((e.position_id, b) for b in e.bullets)
        return out


# --------------------------------------------------------------------------
# Validation / claim map
# --------------------------------------------------------------------------


class ClaimCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str
    passed: bool
    detail: str = ""


class ClaimEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    location: str
    text: str
    evidence_ids: list[str]
    status: str  # supported | rejected | replaced | removed
    checks: list[ClaimCheck] = Field(default_factory=list)
    replacement_text: str | None = None
    bindings: list[ClaimBinding] = Field(default_factory=list)


class ClaimEvidenceMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[ClaimEntry]


class ValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: str  # error | warning | info
    code: str
    location: str
    message: str
    text: str = ""


class ValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: str  # pass | pass_with_replacements | fail
    total_claims: int
    supported: int
    rejected: int
    replaced: int
    issues: list[ValidationIssue] = Field(default_factory=list)
    unsupported_skills_removed: list[str] = Field(default_factory=list)
    structural_checks: list[ClaimCheck] = Field(default_factory=list)


# --------------------------------------------------------------------------
# Lint
# --------------------------------------------------------------------------


class LintFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    severity: str  # error | warning | info
    message: str
    location: str = ""


class LintReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    findings: list[LintFinding]
    metrics: dict[str, float | int]
    disclaimer: str = (
        "Internal coverage metrics computed from the evidence bank and the parsed "
        "job description. They are not hiring or ATS-acceptance probabilities."
    )


# --------------------------------------------------------------------------
# Diff
# --------------------------------------------------------------------------


class Change(BaseModel):
    model_config = ConfigDict(extra="forbid")
    section: str
    kind: str  # added | modified | removed | kept | reordered
    original: str | None = None
    tailored: str | None = None
    why: str = ""
    supported_by: list[str] = Field(default_factory=list)
    requirement_ids: list[str] = Field(default_factory=list)


class DiffReport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_resume_id: str
    changes: list[Change]
    summary: dict[str, int] = Field(default_factory=dict)


# --------------------------------------------------------------------------
# Base resume (structured), request, run
# --------------------------------------------------------------------------


class BaseResumeBullet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    evidence_ids: list[str] = Field(default_factory=list)


class BaseResumePosition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position_id: str
    bullets: list[BaseResumeBullet] = Field(default_factory=list)


class BaseResume(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    headline: str
    summary: list[BaseResumeBullet] = Field(default_factory=list)
    positions: list[BaseResumePosition] = Field(default_factory=list)
    skills: list[SkillGroup] = Field(default_factory=list)
    source_file: str = ""
    default_profile: str | None = None


class TailorOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_only_claims: bool = True
    ats_friendly: bool = True
    max_two_pages: bool = True
    preserve_metrics: bool = True
    show_explanations: bool = True
    use_llm: bool = True
    resume_locale: str = "en-US"  # spelling of generated prose; evidence text is never rewritten


class TailorRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jd_text: str = Field(min_length=20)
    resume_id: str
    target_profile: str | None = None  # None / "auto" -> inferred
    options: TailorOptions = Field(default_factory=TailorOptions)


class PageMeasurement(BaseModel):
    """How the page limit was checked. ``actual_pages`` is a real render (Word or LibreOffice);
    when ``source == "estimator"`` no renderer was available and only the layout estimate exists."""

    model_config = ConfigDict(extra="forbid")
    limit: int
    estimated_pages: float
    actual_pages: int | None = None
    source: Literal["word", "libreoffice", "estimator"] = "estimator"
    passes: int = 0
    trimmed: list[str] = Field(default_factory=list)
    limit_met: bool = True
    note: str = ""


class TailorRun(BaseModel):
    """Everything one Analyze & Tailor produced, in one inspectable object."""

    model_config = ConfigDict(extra="forbid")
    run_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    request: TailorRequest
    provider: dict[str, str] = Field(default_factory=dict)
    job_analysis: JobAnalysis
    evidence_matches: MatchReport
    resume_strategy: ResumeStrategy
    generated_resume: GeneratedResume
    claim_evidence_map: ClaimEvidenceMap
    validation_report: ValidationReport
    lint_report: LintReport
    diff_report: DiffReport
    page_measurement: PageMeasurement | None = None
    warnings: list[str] = Field(default_factory=list)

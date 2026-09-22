"""The durable JobFingerprint: what one posting says, and where it says it.

This model belongs to Career Agent. It is deliberately **not** a vendor response
schema, and no LLM provider's grammar limits are allowed to shape it. Adapters
convert between whatever a vendor can emit and this document; the conversion is
deterministic, lossless and validated, and swapping providers must never require
editing this file.

Three properties are load-bearing and easy to lose by accident:

**Nothing is a bare value.** Every observation is an ``ExtractedField``, which
carries the value together with how we came to know it. That is what makes
"absence is never permission" mechanically enforceable rather than aspirational.

**Two provenance channels never blend.** ``ProviderObservation`` records what the
ATS payload asserted; everything else records what the posting text said. A
provider field saying "Remote - United States" may not fill a description-side
hiring scope when the description itself is silent. M3 decides how the two
interact; M2 only records both truthfully.

**No conclusions.** There is no fit score, no eligibility verdict, no ranking
signal, and no model-written summary anywhere in this document. Those belong to
M3, M4 and M5, and putting a placeholder for one here would invite the
milestone boundary to erode.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from career_agent.domain.countries import country_of, is_subdivision
from career_agent.domain.enums import (
    METADATA_DIMENSION_VOCABULARY_VERSION,
    RESPONSIBILITY_VOCABULARY_VERSION,
    CodingIntensity,
    EvidenceSourceKind,
    ExposureLevel,
    ExtractionStatus,
    HiringScopeKind,
    LanguageRequirement,
    MetadataDimension,
    PresenceRequirement,
    Prominence,
    Region,
    RequirementStrength,
    ResponsibilityCategory,
    Seniority,
    SoftwareCentrality,
    TimezoneConstraintKind,
    TravelFrequency,
    WorkModel,
)
from career_agent.domain.extracted import EVIDENCE_ID_PATTERN, ExtractedField

#: Bumped when the shape of this document changes in a way that makes a stored
#: fingerprint no longer comparable with a new one. Part of the cache key, so a
#: bump is a re-extraction rather than a silent reinterpretation.
FINGERPRINT_SCHEMA_VERSION = 4


# =========================================================================
# EVIDENCE
# =========================================================================


class Evidence(BaseModel):
    """One citation, of one kind, verifiable without an LLM.

    A description citation carries a quote to be located in the archived
    ``job_raw.description_text``. A provider citation carries the JSON path and
    the raw value, to be resolved against the archived payload. The two sets of
    fields are mutually exclusive, and mixing them is rejected here rather than
    discovered later in the verifier.

    ``verified`` and ``match_kind`` are written by ``domain/verify.py`` after
    the model has spoken. They are never supplied by the model, which is why
    they default to unverified: a citation is guilty until checked.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=EVIDENCE_ID_PATTERN)
    source_kind: EvidenceSourceKind

    # JOB_DESCRIPTION
    quote: str | None = None

    # PROVIDER_FIELD
    provider: str | None = None
    payload_hash: str | None = None
    source_field: str | None = None
    source_value: str | None = None

    @model_validator(mode="after")
    def _check_shape(self) -> "Evidence":
        if self.source_kind is EvidenceSourceKind.JOB_DESCRIPTION:
            if not self.quote:
                raise ValueError("JOB_DESCRIPTION evidence requires a quote")
            if any((self.provider, self.source_field, self.source_value)):
                raise ValueError(
                    "JOB_DESCRIPTION evidence cannot carry provider fields: the two "
                    "provenance channels never blend"
                )
        elif self.source_kind is EvidenceSourceKind.PROVIDER_FIELD:
            if not (self.provider and self.source_field):
                raise ValueError("PROVIDER_FIELD evidence requires provider and source_field")
            if self.quote:
                raise ValueError(
                    "PROVIDER_FIELD evidence cannot carry a quote: a payload value is not "
                    "something the posting said"
                )
        else:
            raise ValueError(
                f"{self.source_kind} evidence is not permitted at M2; only JOB_DESCRIPTION "
                "and PROVIDER_FIELD exist as extraction sources"
            )
        return self


# =========================================================================
# META
# =========================================================================


class FingerprintMeta(BaseModel):
    """What produced this document, and from exactly which inputs.

    Every field here is part of the answer to "could this fingerprint be
    compared with that one?". ``content_hash`` and ``payload_hash`` pin the
    source; the three version fields pin the interpretation. A change to any of
    them is a different observation, not an update to this one.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = FINGERPRINT_SCHEMA_VERSION
    #: The wire format that produced this document. The value is owned by
    #: ``llm/transport.py``; the domain layer may not import it, so the default
    #: is mirrored here and a contract test fails if the two ever disagree.
    #: Recorded because a document assembled from a different transport shape
    #: is a different observation even when every version below it matches.
    transport_version: int = 3
    prompt_version: str
    responsibility_vocabulary_version: int = RESPONSIBILITY_VOCABULARY_VERSION
    metadata_vocabulary_version: int = METADATA_DIMENSION_VOCABULARY_VERSION
    model: str
    content_hash: str
    payload_hash: str | None = None
    truncated: bool = False
    partial: bool = False
    extraction_notes: str = ""


# =========================================================================
# ROLE
# =========================================================================


class RoleSignals(BaseModel):
    """What the posting says the role *is*.

    ``observed_title`` is recorded because it is a fact about the posting, not
    because it decides anything. The product exists partly because titles
    mislead: a Business Technology Analyst may be doing exactly the wanted work,
    and a Revenue Operations Manager may be doing none of it. Seniority is read
    from the body for the same reason.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_title: str
    seniority_signal: ExtractedField[Seniority]
    function_signals: list[str] = Field(default_factory=list, max_length=8)
    team_context: ExtractedField[str]


# =========================================================================
# RESPONSIBILITIES AND SOFTWARE
# =========================================================================


class ResponsibilityObservation(BaseModel):
    """One kind of work the posting describes, and how much of the role it is.

    Prominence is what keeps preferences proportional instead of binary:
    people_management as PRIMARY is a real objection, as INCIDENTAL it is barely
    one. M4 owns that comparison; here it is only observed.

    ``raw_phrase`` is mandatory when the category is the escape hatch. What
    accumulates in those phrases is how the vocabulary grows from evidence
    rather than from guesswork, and losing them would make the escape hatch a
    silent bin rather than a queue.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    category: ResponsibilityCategory
    prominence: Prominence
    status: ExtractionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_id: str | None = Field(default=None, pattern=EVIDENCE_ID_PATTERN)
    raw_phrase: str | None = None

    @model_validator(mode="after")
    def _check(self) -> "ResponsibilityObservation":
        if self.status is ExtractionStatus.EXPLICIT and self.evidence_id is None:
            raise ValueError("an EXPLICIT responsibility must cite where the posting says it")
        if self.category is ResponsibilityCategory.RESPONSIBILITY_OTHER and not self.raw_phrase:
            raise ValueError(
                "responsibility_other requires raw_phrase: an unmapped concept is only "
                "useful if the words that produced it survive"
            )
        if self.status in (ExtractionStatus.NOT_STATED, ExtractionStatus.NOT_APPLICABLE):
            raise ValueError(
                "responsibilities are a list of things the posting DOES describe; "
                "silence is expressed by the absence of an entry"
            )
        return self


class SoftwareObservation(BaseModel):
    """One tool the posting mentions, and how central it is to the role.

    Centrality is semantic, never frequency. "Own HubSpot end to end" is CORE
    however few times the word appears; "tools such as HubSpot" is MENTIONED
    however often it repeats.

    ``alternative_group`` is what makes "HubSpot, Salesforce or another CRM"
    behave correctly downstream: a refused tool offered as one option among
    several is not a conflict, because the employer already said an alternative
    is acceptable. Members of one such list share a group id.

    ``canonical_suggestion`` is a suggestion and nothing more. M2 never creates
    a permanent alias from it.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    raw_mention: str
    canonical_suggestion: str | None = None
    centrality: SoftwareCentrality
    alternative_group: str | None = None
    status: ExtractionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_id: str | None = Field(default=None, pattern=EVIDENCE_ID_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> "SoftwareObservation":
        if self.status is ExtractionStatus.EXPLICIT and self.evidence_id is None:
            raise ValueError("an EXPLICIT software mention must cite where the posting says it")
        if self.centrality is SoftwareCentrality.ALTERNATIVE and not self.alternative_group:
            raise ValueError(
                "ALTERNATIVE centrality requires alternative_group: the whole point is "
                "knowing which tools were offered as substitutes for each other"
            )
        if self.status in (ExtractionStatus.NOT_STATED, ExtractionStatus.NOT_APPLICABLE):
            raise ValueError(
                "software is a list of tools the posting DOES mention; silence is "
                "expressed by the absence of an entry"
            )
        return self


# =========================================================================
# EXPOSURE AND WORK ENVIRONMENT
# =========================================================================


class Exposure(BaseModel):
    """How much of the role is spent on things people feel strongly about.

    ``ExposureLevel.NONE`` here means the posting said there is none. A posting
    that never raises the subject is NOT_STATED on the wrapping field. The
    distinction is the whole design and it is easy to lose.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    people_management: ExtractedField[ExposureLevel]
    sales_exposure: ExtractedField[ExposureLevel]
    support_exposure: ExtractedField[ExposureLevel]
    on_call: ExtractedField[ExposureLevel]


class WorksiteRequirement(BaseModel):
    """Where the person must physically be, and how binding that is.

    **This is not hiring geography.** They are different questions and the
    product exists partly because they get conflated:

        HIRING SCOPE   where the employer says it may hire from
        WORKSITE       where the employee must physically perform the work

    "Hybrid, three days a week in our New York office" states a worksite and
    says nothing about hiring scope -- the employer may well hire
    internationally and require relocation. "We sponsor international
    candidates, but you must relocate to New York" is exactly that shape, and
    the two facts are not in conflict.

    So a city named here never becomes a country in ``hiring_scope``. A later
    milestone combines this with candidate residence and relocation
    willingness; deciding it here would bake one candidate into the document.

    ``level`` replaces what ``office_locations`` could not express. A list of
    place names cannot tell "you must be in this office three days a week"
    apart from "an office exists and you may use it", and those are opposite
    facts for anyone deciding whether a job is reachable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    level: PresenceRequirement
    #: As named by the posting. Cities, offices, metro areas -- whatever the
    #: employer wrote, not normalised to countries.
    locations: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _check(self) -> "WorksiteRequirement":
        if self.level is PresenceRequirement.REQUIRED and not self.locations:
            raise ValueError(
                "a REQUIRED worksite must name where. 'You must be in an office' with no "
                "office named is not a requirement anyone can act on, and recording it as "
                "one would block jobs on a fact the posting never supplied."
            )
        return self


class WorkEnvironment(BaseModel):
    """Remote, hybrid or onsite, as *stated*, not as concluded.

    ``work_model`` is what the description says. It is not eligibility, and it
    is not the provider's ``work_model_hint``, which lives in the separate
    provider channel precisely because the two disagree often enough to matter.

    ``worksite_requirement`` replaced ``office_locations`` at schema version 3.
    A bare list of place names could not distinguish a required office from one
    that merely exists, so "hybrid, three days a week in New York" and "remote,
    and you may use the New York office if you like" produced identical
    documents -- opposite facts for anyone deciding whether a job is reachable.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    work_model: ExtractedField[WorkModel]
    onsite_frequency: ExtractedField[str]
    worksite_requirement: ExtractedField[WorksiteRequirement]
    async_signals: ExtractedField[bool]


# =========================================================================
# GEOGRAPHY AND ELIGIBILITY FACTS
# =========================================================================


class HiringScope(BaseModel):
    """Where the EMPLOYER says the worker may live.

    The single most expensive mistake this system could make is turning the
    word "Remote" into WORLDWIDE. UNSTATED is the honest answer for a posting
    that says "Remote" and nothing else, and the cross-field validator enforces
    that separately because a model will otherwise be helpful about it.

    WORLDWIDE with exclusions is valid and common: it is the usual shape of a
    genuinely global posting, and must never be treated as a contradiction.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: HiringScopeKind
    regions: list[Region] = Field(default_factory=list, max_length=12)
    #: ISO 3166-1 alpha-2 codes, and ISO 3166-2 subdivision codes where the
    #: posting is narrower than a country. ``["US-AZ", "US-CA"]`` is 26 states
    #: worth of difference from ``["US"]``, and the difference is a false PASS
    #: for every candidate in one of the other 24.
    countries: list[str] = Field(default_factory=list, max_length=60)
    exclusions: list[str] = Field(default_factory=list, max_length=60)
    #: How binding the stated scope is. Separate axis from ``ExtractionStatus``
    #: on the wrapping field: "should preferably be based in Sao Paulo" is an
    #: EXPLICIT statement of a PREFERRED location, and a preference must never
    #: become a hard geography block.
    requirement_level: RequirementStrength = RequirementStrength.REQUIRED

    @model_validator(mode="after")
    def _check(self) -> "HiringScope":
        if self.kind is HiringScopeKind.REGION and not self.regions:
            raise ValueError("kind=REGION requires at least one region")
        if self.kind is HiringScopeKind.COUNTRY_LIST and not self.countries:
            raise ValueError("kind=COUNTRY_LIST requires at least one country")
        if self.kind is HiringScopeKind.UNSTATED and (self.regions or self.countries):
            raise ValueError(
                "kind=UNSTATED cannot carry regions or countries: if the posting named "
                "somewhere, the scope is not unstated"
            )
        if (
            self.kind is HiringScopeKind.UNSTATED
            and self.requirement_level is RequirementStrength.PREFERRED
        ):
            raise ValueError(
                "kind=UNSTATED cannot be PREFERRED: a preference for nowhere in "
                "particular is not a preference, and recording one would invite a "
                "downstream reader to treat silence as a soft signal"
            )

        # A country and its own subdivisions cannot both be listed. "US, US-AZ"
        # is either the whole country or one state, and whichever the posting
        # meant, storing both lets a later gate pick the convenient one.
        whole = {c for c in self.countries if not is_subdivision(c)}
        narrowed = {country_of(c) for c in self.countries if is_subdivision(c)}
        both = whole & narrowed
        if both:
            raise ValueError(
                f"{sorted(both)} appears both as a whole country and as named "
                "subdivisions. Those are different claims -- one opens the country, "
                "the other opens a list of states -- and keeping both would let a "
                "geography gate pass a candidate the posting excluded."
            )
        return self


class TimezoneRequirement(BaseModel):
    """A timezone the posting names, and what it actually constrains.

    ``kind`` is the field that stops a clock becoming a border. "You need to be
    able to cover EST working hours" constrains WHEN the person works and is
    satisfiable from Sao Paulo; "you must be located in a US timezone"
    constrains WHERE they live and is not. Both name EST-ish timezones, and a
    model that recorded only the anchor would leave a downstream gate to guess
    which was meant -- which, on this dimension, means guessing at geography.

    OVERLAP is the default because it is the safe direction. A residence
    reading has to be earned from the posting saying so; an hours reading
    never manufactures a place the employer did not name.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    anchor: str
    overlap_hours: int | None = Field(default=None, ge=0, le=24)
    kind: TimezoneConstraintKind = TimezoneConstraintKind.OVERLAP


class EligibilityFacts(BaseModel):
    """Facts the posting states about who may take the job.

    Every one of these is an observation, never a conclusion. Nothing here says
    whether a particular person qualifies: that requires a candidate, and M2 is
    candidate-independent by construction. M3 owns the gates.

    Six of these dimensions appear in ``NEVER_APPLICABLE_EXEMPT`` because
    silence about them is so easily misread as a negative answer: a posting that
    does not mention sponsorship has not said there is none.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    hiring_scope: ExtractedField[HiringScope]
    timezone_requirement: ExtractedField[TimezoneRequirement]
    work_authorization_required: ExtractedField[str]
    visa_sponsorship: ExtractedField[bool]
    eor_available: ExtractedField[bool]
    contractor_eligible: ExtractedField[bool]
    employment_type_restriction: ExtractedField[str]
    relocation_required: ExtractedField[bool]
    #: "Must reside in the DC area OR be willing to relocate" states that
    #: relocation is an ACCEPTED PATH, which is neither "relocation required"
    #: (it is not, for someone already local) nor silence. Without this field
    #: that sentence loses half its meaning, and the half it loses is the half
    #: that tells a candidate the job is reachable.
    relocation_allowed: ExtractedField[bool]
    relocation_support: ExtractedField[str]
    #: Whether travel is required at all, kept apart from how often. A posting
    #: that says "travel for customer meetings" and never says how often states
    #: one fact and withholds another. Folding the first into the lowest
    #: non-zero frequency bucket would invent the second.
    travel_required: ExtractedField[bool]
    travel_frequency: ExtractedField[TravelFrequency]
    travel_expenses_covered: ExtractedField[bool]
    business_visa_support: ExtractedField[bool]


class LanguageObservation(BaseModel):
    """A language the posting names, and how hard the requirement is.

    The level matters more than the language. A mandatory language the
    candidate does not speak can end the conversation; the same language as a
    nice-to-have should barely register. Conflating them would quietly discard
    good European roles.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    language_code: str = Field(min_length=2, max_length=8)
    requirement_level: LanguageRequirement
    status: ExtractionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_id: str | None = Field(default=None, pattern=EVIDENCE_ID_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> "LanguageObservation":
        if self.status is ExtractionStatus.EXPLICIT and self.evidence_id is None:
            raise ValueError("an EXPLICIT language requirement must cite the posting")
        return self


# =========================================================================
# COMPENSATION AND COMPANY CONTEXT
# =========================================================================


class Compensation(BaseModel):
    """A stated pay range, kept as separate observations rather than one blob.

    Each part is separately statusable because postings really do state some
    parts and not others: a currency and period with no numbers, or a range
    with no equity note. Arithmetic on these values is deterministic code's job,
    never the model's.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    min: ExtractedField[float]
    max: ExtractedField[float]
    currency: ExtractedField[str]
    period: ExtractedField[str]
    equity: ExtractedField[str]


class CompanyContext(BaseModel):
    """What the posting says about the company, from the posting alone.

    Not from a careers page, not from what we know about the employer, and not
    from collection metadata. Company history exists as a source kind for later
    milestones and is explicitly barred from informing a conclusion about the
    current posting.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    industry: ExtractedField[str]
    stage_signal: ExtractedField[str]
    contract_type: ExtractedField[str]


# =========================================================================
# THE PROVIDER CHANNEL
# =========================================================================


class ProviderObservation(BaseModel):
    """What the ATS payload asserted, interpreted but never promoted.

    This is a separate class rather than another field on the document because
    separation is the point. A provider hint is a claim by an employer's ATS
    record; a description observation is something the posting text says. They
    are recorded side by side, they may disagree, and neither wins here.

    Toptal is the standing example: a posting located "Anywhere" whose payload
    says the country is US. Both are true statements about different artefacts.

    Evidence is mandatory and must be PROVIDER_FIELD, because provider metadata
    is *more* verifiable than prose, not less: either the payload said it at
    that path or it did not.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    dimension: MetadataDimension
    value: str | HiringScope | None = None
    status: ExtractionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_id: str = Field(pattern=EVIDENCE_ID_PATTERN)

    @model_validator(mode="after")
    def _check(self) -> "ProviderObservation":
        if self.status is ExtractionStatus.EXPLICIT and self.value is None:
            raise ValueError("an EXPLICIT provider observation requires a value")
        if self.status is ExtractionStatus.NOT_APPLICABLE:
            raise ValueError(
                "a provider observation is only created when a mapped field was present; "
                "NOT_APPLICABLE has no meaning here"
            )
        return self


# =========================================================================
# THE DOCUMENT
# =========================================================================


class JobFingerprint(BaseModel):
    """Everything one posting says, with a citation for every explicit claim.

    Assembled by code from validated parts. The model never assembles this
    document, never merges across extraction families, and never sees the whole
    of it at once.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    meta: FingerprintMeta
    evidence: list[Evidence] = Field(default_factory=list)

    role: RoleSignals
    responsibilities: list[ResponsibilityObservation] = Field(default_factory=list)
    software: list[SoftwareObservation] = Field(default_factory=list)
    coding_intensity: ExtractedField[CodingIntensity]
    exposure: Exposure

    work_environment: WorkEnvironment
    eligibility: EligibilityFacts
    languages: list[LanguageObservation] = Field(default_factory=list)
    compensation: Compensation
    company_context: CompanyContext

    #: The second provenance channel. Never merged into the fields above.
    provider_observations: list[ProviderObservation] = Field(default_factory=list)

    # -- integrity ---------------------------------------------------------

    @model_validator(mode="after")
    def _evidence_ids_are_unique(self) -> "JobFingerprint":
        ids = [item.id for item in self.evidence]
        if len(ids) != len(set(ids)):
            duplicates = sorted({i for i in ids if ids.count(i) > 1})
            raise ValueError(
                f"duplicate evidence ids {duplicates}: assembly across extraction "
                "families must namespace ids, because separate calls each start at ev_01"
            )
        return self

    @model_validator(mode="after")
    def _every_citation_resolves(self) -> "JobFingerprint":
        """A cited id must exist. Dangling references are rejected, not ignored.

        This is referential integrity, not verification: whether the quote is
        actually *in* the posting is answered later, deterministically, by
        ``domain/verify.py``.
        """
        known = {item.id for item in self.evidence}
        for owner, cited in self._citations():
            if cited not in known:
                raise ValueError(f"{owner} cites {cited!r}, which is not in evidence[]")
        return self

    @model_validator(mode="after")
    def _channels_cite_their_own_source(self) -> "JobFingerprint":
        """Description observations may cite only JOB_DESCRIPTION, and provider
        observations only PROVIDER_FIELD.

        This is the leakage guard expressed as a type-level rule. Without it, a
        payload saying "Remote - United States" could quietly become the
        description's hiring scope, and the resulting fingerprint would claim
        the posting stated a geography it never mentioned. That is precisely the
        failure the whole product is built to avoid.
        """
        kinds = {item.id: item.source_kind for item in self.evidence}

        for owner, cited in self._citations():
            expected = (
                EvidenceSourceKind.PROVIDER_FIELD
                if owner.startswith("provider_observations")
                else EvidenceSourceKind.JOB_DESCRIPTION
            )
            actual = kinds.get(cited)
            if actual is not None and actual is not expected:
                raise ValueError(
                    f"{owner} cites {cited!r} which is {actual}, but this channel may only "
                    f"cite {expected}: provider metadata never fills a description "
                    "observation, and the reverse is equally forbidden"
                )
        return self

    # -- traversal ---------------------------------------------------------

    def _citations(self) -> list[tuple[str, str]]:
        """Every (owner, evidence_id) pair in the document.

        One traversal serves both integrity validators, so a newly added
        dimension cannot be checked by one and forgotten by the other.
        """
        found: list[tuple[str, str]] = []

        def scalar(owner: str, field: ExtractedField[Any]) -> None:
            if field.evidence_id:
                found.append((owner, field.evidence_id))

        scalar("coding_intensity", self.coding_intensity)
        scalar("role.seniority_signal", self.role.seniority_signal)
        scalar("role.team_context", self.role.team_context)

        for block_name in (
            "exposure",
            "work_environment",
            "eligibility",
            "compensation",
            "company_context",
        ):
            block = getattr(self, block_name)
            for name in type(block).model_fields:
                scalar(f"{block_name}.{name}", getattr(block, name))

        listed: list[tuple[str, list[Any]]] = [
            ("responsibilities", list(self.responsibilities)),
            ("software", list(self.software)),
            ("languages", list(self.languages)),
            ("provider_observations", list(self.provider_observations)),
        ]
        for label, entries in listed:
            for index, entry in enumerate(entries):
                if entry.evidence_id:
                    found.append((f"{label}[{index}]", entry.evidence_id))

        return found

    # -- queries -----------------------------------------------------------

    def scalar_fields(self) -> dict[str, ExtractedField[Any]]:
        """Every single-valued description observation, by dotted name.

        The precondition enforcer in ``domain/extracted.py`` works on a flat
        mapping and needs the same names the NOT_APPLICABLE rules are keyed on
        (``relocation_support``, ``travel_frequency``…), so eligibility fields
        are exposed unprefixed. Everything else keeps its block prefix, since
        nothing depends on those names.
        """
        fields: dict[str, ExtractedField[Any]] = {
            "coding_intensity": self.coding_intensity,
            "seniority_signal": self.role.seniority_signal,
            "team_context": self.role.team_context,
        }
        for name in type(self.eligibility).model_fields:
            fields[name] = getattr(self.eligibility, name)
        for block_name in ("exposure", "work_environment", "compensation", "company_context"):
            block = getattr(self, block_name)
            for name in type(block).model_fields:
                fields[f"{block_name}.{name}"] = getattr(block, name)
        return fields

    def status_counts(self) -> dict[ExtractionStatus, int]:
        """How the posting distributed across the four statuses.

        The headline evaluation metric in both directions: a fingerprint that is
        almost entirely NOT_STATED is over-cautious, and one that is almost
        entirely EXPLICIT on a thin posting is inventing.
        """
        counts: dict[ExtractionStatus, int] = dict.fromkeys(ExtractionStatus, 0)
        for field in self.scalar_fields().values():
            counts[field.status] += 1
        return counts

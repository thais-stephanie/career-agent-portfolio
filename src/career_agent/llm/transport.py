"""The vendor-facing transport schema, and why it is not the domain model.

`JobFingerprint` is Career Agent's durable document. This module holds what we
ask a *model* to produce, which is a different problem with different pressures:

    domain model      optimised for meaning, provenance and validation
    transport model   optimised for what a vendor's schema grammar can compile

Measured on the real generated schema (out/schema_measure.py), the durable
document as one strict schema is 191 optional and 107 union-typed properties
against Anthropic limits of 24 and 16 -- 8.0x and 6.7x over. That is not a near
miss to be tuned away, and the cause is structural: each of the ~30
`ExtractedField` dimensions contributes its own `value`, `evidence_id` and
`not_applicable_because` unions. Adding dimensions makes it worse, linearly.

THE SHAPE THAT FIXES IT
-----------------------
Dimensions become **rows, not properties**:

    observations: [ { dimension, status, value, confidence, evidence_id, ... } ]

One array-of-objects costs the same no matter how many dimensions the product
grows to. Schema complexity stops scaling with the domain, so a new dimension
becomes a vocabulary change rather than a transport crisis.

WHAT THIS IS NOT
----------------
Not a weakening of approved semantics. `value` stays genuinely nullable, because
`NOT_STATED` means no value was extracted and a sentinel string pretending
otherwise would be a lie no validator could catch. The four statuses, the closed
vocabularies, evidence and the NOT_APPLICABLE precondition table are unchanged;
they are enforced in `assemble.py` after transport rather than by a vendor's
compiler -- which is where they were always going to be enforced anyway, since
provider-side schema enforcement was never allowed to replace our own.

Vendor SDK types never appear here. This is plain Pydantic, so an OpenAI or
Gemini adapter reuses it unchanged.
"""

from pydantic import BaseModel, ConfigDict, Field

from career_agent.domain.enums import (
    EvidenceSourceKind,
    ExtractionStatus,
    LanguageRequirement,
    Prominence,
    ResponsibilityCategory,
    SoftwareCentrality,
)

#: Bumped when the transport shape changes what a model is being asked for.
#: Separate from FINGERPRINT_SCHEMA_VERSION on purpose: the durable document and
#: the wire format may version independently.
TRANSPORT_SCHEMA_VERSION = 3

#: The provider transport versions separately, because it narrowed at v2 while
#: the description transport did not change. v2 removed the dimensions a lookup
#: table now resolves; only free-text geography still reaches a model.
PROVIDER_TRANSPORT_VERSION = 2


# =========================================================================
# THE DIMENSION VOCABULARY
#
# Every scalar dimension of the durable document, as a value the model writes
# into a row rather than a property name it has to know. Kept in one list so
# the transport, the prompt and the assembler cannot drift apart.
# =========================================================================

#: Dimensions the ROLE family is responsible for.
ROLE_DIMENSIONS: tuple[str, ...] = (
    "seniority_signal",
    "team_context",
    "coding_intensity",
    "exposure.people_management",
    "exposure.sales_exposure",
    "exposure.support_exposure",
    "exposure.on_call",
)

#: Dimensions the CONDITIONS family is responsible for. These are the ones that
#: later become eligibility gates, which is why they are grouped together and
#: given their own prompt: they reward careful reading of the final third of a
#: posting, where legal and geographic language usually lives.
CONDITION_DIMENSIONS: tuple[str, ...] = (
    "work_environment.work_model",
    "work_environment.onsite_frequency",
    "work_environment.worksite_requirement",
    "work_environment.async_signals",
    "hiring_scope",
    "timezone_requirement",
    "work_authorization_required",
    "visa_sponsorship",
    "eor_available",
    "contractor_eligible",
    "employment_type_restriction",
    "relocation_required",
    "relocation_allowed",
    "relocation_support",
    "travel_required",
    "travel_frequency",
    "travel_expenses_covered",
    "business_visa_support",
    "compensation.min",
    "compensation.max",
    "compensation.currency",
    "compensation.period",
    "compensation.equity",
    "company_context.industry",
    "company_context.stage_signal",
    "company_context.contract_type",
)

ALL_DIMENSIONS: tuple[str, ...] = ROLE_DIMENSIONS + CONDITION_DIMENSIONS


# =========================================================================
# ROWS
# =========================================================================


class TEvidence(BaseModel):
    """A citation as the model writes it.

    Ids are per-call and the model is told to start at ev_01 every time. It is
    never asked to coordinate ids across calls -- code namespaces them during
    assembly, because asking two independent completions to agree on numbering
    is asking for a bug we would only notice downstream.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    source_kind: EvidenceSourceKind
    quote: str | None = None
    provider: str | None = None
    source_field: str | None = None
    source_value: str | None = None


class TObservation(BaseModel):
    """One dimension, one row.

    `value` is a string because the transport must accept a country list, a
    salary figure, an enum member and a timezone anchor in the same column.
    Parsing it back into the typed domain value is `assemble.py`'s job, and a
    value that will not parse is a validation failure rather than a silent
    coercion.

    `value` is nullable rather than "" because the domain distinction is real:
    NOT_STATED means nothing was extracted, and an empty string is a value.
    """

    model_config = ConfigDict(extra="forbid")

    dimension: str
    status: ExtractionStatus
    value: str | None = None
    confidence: float = 0.0
    evidence_id: str | None = None
    not_applicable_because: str | None = None
    reasoning: str = ""


class TResponsibility(BaseModel):
    """A kind of work the posting describes.

    Read from the body, not the title. This is the field the whole product
    exists for: a posting called "Business Technology Analyst" may describe
    exactly the wanted work, and one called "Revenue Operations Manager" may
    describe none of it.
    """

    model_config = ConfigDict(extra="forbid")

    category: ResponsibilityCategory
    prominence: Prominence
    confidence: float = 0.0
    evidence_id: str | None = None
    raw_phrase: str | None = None


class TSoftware(BaseModel):
    """A tool the posting mentions, and how central it is.

    Centrality is semantic, never a mention count. `alternative_group` ties
    together tools offered as substitutes -- "HubSpot, Salesforce or another
    CRM" -- so that a refused tool listed as one option among several is not
    later read as a conflict.
    """

    model_config = ConfigDict(extra="forbid")

    raw_mention: str
    canonical_suggestion: str | None = None
    centrality: SoftwareCentrality
    alternative_group: str | None = None
    confidence: float = 0.0
    evidence_id: str | None = None


class TLanguage(BaseModel):
    """A language the posting names, and how hard the requirement is."""

    model_config = ConfigDict(extra="forbid")

    language_code: str
    requirement_level: LanguageRequirement
    confidence: float = 0.0
    evidence_id: str | None = None


# =========================================================================
# FAMILY PAYLOADS
#
# Each is one request's output schema. The split is not cosmetic: the ROLE and
# CONDITIONS families receive the job description and may cite only
# JOB_DESCRIPTION, while PROVIDER receives the field-mapped metadata block and
# never sees the description at all.
#
# That makes provider-metadata leakage structurally impossible rather than a
# matter of the model following an instruction. A call cannot borrow from a
# source it was never given, so the leakage target of 0 becomes a property of
# the wiring instead of a behaviour we hope for and measure afterwards.
# =========================================================================


class TDescriptionFamily(BaseModel):
    """Everything the posting text says. **The approved default family.**

    One call, one evidence namespace, no duplicated description. Measured at 0
    optional and 13 of 16 union-typed properties, so it fits Anthropic's limits
    with headroom.
    """

    model_config = ConfigDict(extra="forbid")

    observed_title: str
    function_signals: list[str] = Field(default_factory=list)
    observations: list[TObservation] = Field(default_factory=list)
    responsibilities: list[TResponsibility] = Field(default_factory=list)
    software: list[TSoftware] = Field(default_factory=list)
    languages: list[TLanguage] = Field(default_factory=list)
    evidence: list[TEvidence] = Field(default_factory=list)


class TRoleFamily(BaseModel):
    """Benchmark arm only: the first half of a split description.

    Not the production default. The hypothesis it tests is that a narrower
    prompt reads a posting more carefully; the cost is a second copy of the
    description. Kept here so the arm can be measured rather than argued about.
    """

    model_config = ConfigDict(extra="forbid")

    observed_title: str
    function_signals: list[str] = Field(default_factory=list)
    observations: list[TObservation] = Field(default_factory=list)
    responsibilities: list[TResponsibility] = Field(default_factory=list)
    software: list[TSoftware] = Field(default_factory=list)
    evidence: list[TEvidence] = Field(default_factory=list)


class TConditionsFamily(BaseModel):
    """Benchmark arm only: the second half of a split description."""

    model_config = ConfigDict(extra="forbid")

    observations: list[TObservation] = Field(default_factory=list)
    languages: list[TLanguage] = Field(default_factory=list)
    evidence: list[TEvidence] = Field(default_factory=list)


class TProviderFamily(BaseModel):
    """Free-text ATS geography, interpreted into a hiring scope.

    Narrowed at v2 to the one dimension that needs a model. Employment type and
    work model are closed sets an audited lookup resolves; compensation is
    preserved verbatim by design. Keeping them here would mean paying ~2,000
    tokens of prompt and schema to have a model re-derive what code already
    knows, on a family measured at 99.4% static overhead.

    Sees only the mapped location values -- never the description. Its evidence
    is PROVIDER_FIELD, verified by resolving the JSON path against the archived
    payload: no fuzzy matching and no judgement, because either the payload said
    it or it did not.
    """

    model_config = ConfigDict(extra="forbid")

    observations: list[TObservation] = Field(default_factory=list)
    evidence: list[TEvidence] = Field(default_factory=list)


class TSingleCall(BaseModel):
    """Everything in one response, for the single-call topology.

    Measured as a candidate rather than assumed to be worse. It duplicates no
    input and costs one request, but it gives up structural source isolation:
    the description and the metadata block reach the model together, so
    leakage becomes a behaviour to measure rather than an impossibility.
    """

    model_config = ConfigDict(extra="forbid")

    observed_title: str
    function_signals: list[str] = Field(default_factory=list)
    observations: list[TObservation] = Field(default_factory=list)
    responsibilities: list[TResponsibility] = Field(default_factory=list)
    software: list[TSoftware] = Field(default_factory=list)
    languages: list[TLanguage] = Field(default_factory=list)
    provider_observations: list[TObservation] = Field(default_factory=list)
    evidence: list[TEvidence] = Field(default_factory=list)

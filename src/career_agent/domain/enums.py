"""Closed vocabularies for the Career Agent domain.

This module is the single source of truth for every value the system is allowed
to reason about. If a concept is not represented here, no other part of the
system may invent it: the LLM cannot emit it (the JSON schema is generated from
these members), the config loader will reject it, and the database CHECK
constraints will refuse it.

Two conventions, applied consistently:

* Member VALUES that appear in LLM output, in the database and in evidence are
  UPPERCASE, because they are system vocabulary.
* Member VALUES that appear in hand-edited YAML configuration are lowercase
  snake_case, because a human types them. Only ``ResponsibilityCategory`` and
  ``Region`` fall into that second group.

Each enum is annotated with the milestone that first consumes it. They all live
here from M0 because they are already decided; unlike the fingerprint model,
none of them benefits from waiting for real postings.
"""

from enum import StrEnum

# =========================================================================
# EXTRACTION AND PROVENANCE  (M2)
# The architectural commitments of the whole design.
# =========================================================================


class ExtractionStatus(StrEnum):
    """What ONE source says about ONE dimension.

    Note what is absent: there is no UNRESOLVED here. A single source is never
    "unresolved" -- it either states something, implies something, is silent, or
    the dimension does not apply. UNRESOLVED is a conclusion the *system* reaches
    after weighing sources, and it lives on GateResult and EligibilityStatus.
    """

    EXPLICIT = "EXPLICIT"  # stated, and we hold the exact quote/field
    INFERRED = "INFERRED"  # derived from context; may never open a gate
    NOT_STATED = "NOT_STATED"  # the source is silent
    NOT_APPLICABLE = "NOT_APPLICABLE"  # provably does not apply (precondition checked)


class ResolutionStatus(StrEnum):
    """The system's conclusion after combining description and provider sources."""

    CORROBORATED = "CORROBORATED"  # both sources agree
    EXPLICIT_DESCRIPTION = "EXPLICIT_DESCRIPTION"  # description explicit, provider silent
    EXPLICIT_PROVIDER = "EXPLICIT_PROVIDER"  # provider explicit, description silent
    CONTRADICTORY = "CONTRADICTORY"  # sources disagree; recorded, never hidden
    INFERRED_ONLY = "INFERRED_ONLY"  # nothing explicit anywhere
    NOT_STATED = "NOT_STATED"  # no source says anything
    NOT_APPLICABLE = "NOT_APPLICABLE"  # precondition satisfied


class EvidenceSourceKind(StrEnum):
    """Where a piece of evidence came from.

    CAREER_PAGE and CANDIDATE_CONFIRMED are reserved for later milestones. They
    are declared now because retrofitting a source kind would touch every
    verification path.

    COMPANY_HISTORY carries a permanent constraint: it may inform a context
    block in the digest, but it may never be attached to a conclusion about the
    CURRENT posting.
    """

    JOB_DESCRIPTION = "JOB_DESCRIPTION"  # M2
    PROVIDER_FIELD = "PROVIDER_FIELD"  # M2
    CAREER_PAGE = "CAREER_PAGE"  # deferred
    COMPANY_HISTORY = "COMPANY_HISTORY"  # deferred; never current-posting evidence
    CANDIDATE_CONFIRMED = "CANDIDATE_CONFIRMED"  # M6: an answer obtained from a recruiter


class MatchKind(StrEnum):
    """How a piece of evidence compared against its source.

    Only EXACT, NORMALISED and FIELD_MATCH are proof, and
    ``VERIFYING_MATCH_KINDS`` below is the machine-readable form of that rule.
    """

    EXACT = "EXACT"  # quote is a contiguous substring of the source
    NORMALISED = "NORMALISED"  # contiguous substring after typography canonicalisation
    FIELD_MATCH = "FIELD_MATCH"  # provider payload value matched exactly at the path
    NOT_FOUND = "NOT_FOUND"  # unverifiable -> the citing field is downgraded

    # There is deliberately no FUZZY member. An earlier M0 revision allowed
    # similarity >= 0.95 as a proof tier; M2 removed it, because two sentences
    # differing only in 'not', a country, a currency or an amount score well
    # above that and are exactly the tokens the later gates depend on.


#: The only outcomes that count as verified. Written as a set rather than left
#: implicit in an ``if`` so that adding a future match kind forces a deliberate
#: decision about whether it proves anything.
VERIFYING_MATCH_KINDS: frozenset[MatchKind] = frozenset(
    {MatchKind.EXACT, MatchKind.NORMALISED, MatchKind.FIELD_MATCH}
)


# =========================================================================
# PROVIDER METADATA  (M1B.1)
#
# The canonical dimensions an ATS payload can speak to. A provider's
# ProviderFieldMap declares which of its payload paths belong to which
# dimension; nothing here says what any value MEANS.
#
# Versioned for the same reason RESPONSIBILITY_VOCABULARY_VERSION is: adding
# a dimension after extraction has run is a re-extraction, and the version is
# what makes that visible instead of silent.
# =========================================================================

METADATA_DIMENSION_VOCABULARY_VERSION = 1


class MetadataDimension(StrEnum):
    """What a provider metadata field is *about*.

    The `_hint` suffix is load-bearing, not decoration. `WORK_MODEL_HINT` is
    what an employer's ATS record asserts; `WorkModel` is what M3 concludes
    after weighing that assertion against the description text -- and the two
    disagree often enough that letting them share a name would be a bug waiting
    to happen. Toptal is the standing example: a posting located "Anywhere"
    whose payload says the country is US.

    A hint is a claim to record with evidence, never a conclusion.
    """

    WORK_MODEL_HINT = "work_model_hint"  # remote / hybrid / onsite, as stated
    HIRING_LOCATION_HINT = "hiring_location_hint"  # where the posting says it is
    EMPLOYMENT_TYPE_HINT = "employment_type_hint"  # full-time, contract, intern...
    COMPENSATION_HINT = "compensation_hint"  # a stated range or salary text


# =========================================================================
# JOB CONTENT  (M2)
# =========================================================================


class SoftwareCentrality(StrEnum):
    """How central a tool is to the role.

    ALTERNATIVE is what makes "HubSpot, Salesforce or another CRM" behave
    correctly: a refused tool offered as one option among several is not a
    conflict, because the employer already accepted an alternative.
    """

    CORE = "CORE"
    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"
    MENTIONED = "MENTIONED"
    ALTERNATIVE = "ALTERNATIVE"


class Prominence(StrEnum):
    """How much of the role a responsibility actually is.

    This is what makes AVOID proportional rather than binary: people_management
    as PRIMARY is a real penalty, as INCIDENTAL it is barely one.
    """

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    INCIDENTAL = "INCIDENTAL"


class LanguageRequirement(StrEnum):
    """How hard a language requirement is."""

    HARD_REQUIREMENT = "HARD_REQUIREMENT"  # can block
    STRONG_PREFERENCE = "STRONG_PREFERENCE"  # penalises
    NICE_TO_HAVE = "NICE_TO_HAVE"  # negligible
    UNCLEAR = "UNCLEAR"  # stated but unclassifiable


class CodingIntensity(StrEnum):
    """How much software engineering the role actually involves."""

    NONE = "NONE"
    LIGHT_SCRIPTING = "LIGHT_SCRIPTING"
    MODERATE = "MODERATE"
    HEAVY_ENGINEERING = "HEAVY_ENGINEERING"


class WorkModel(StrEnum):
    """Remote / hybrid / onsite, as stated by the posting.

    UNCLEAR means the posting discusses work model but not decisively. A posting
    that never mentions it at all is ExtractionStatus.NOT_STATED instead.
    """

    REMOTE = "REMOTE"
    HYBRID = "HYBRID"
    ONSITE = "ONSITE"
    UNCLEAR = "UNCLEAR"


class RequirementStrength(StrEnum):
    """How binding a stated LOCATION constraint is. Not how well it is evidenced.

    The two axes get conflated constantly, and the conflation runs both ways:

        status  = EXPLICIT / INFERRED    how directly the source supports it
        strength = REQUIRED / PREFERRED  how mandatory the employer made it

    "The candidate should preferably be based in Sao Paulo" is an **EXPLICIT**
    statement -- the posting says it, in those words -- of a **PREFERRED**
    location. Recording it as INFERRED to signal softness would be the wrong
    axis: it would claim the evidence is weak when the evidence is a sentence,
    and it would still let the geography gate treat Sao Paulo as a wall.

    There is no OPTIONAL member. A hiring scope the employer does not care
    about is not a hiring scope; that case is ``HiringScopeKind.UNSTATED``.
    ``PresenceRequirement`` keeps its OPTIONAL member because an office that
    exists and may be used is a real and different thing.
    """

    REQUIRED = "REQUIRED"
    PREFERRED = "PREFERRED"


class TimezoneConstraintKind(StrEnum):
    """What a stated timezone actually constrains: hours, or address.

    "You need to be able to cover EST working hours" and "you must be located
    in a US timezone" both name a timezone and mean different things. The first
    constrains WHEN the person works and can be satisfied from Sao Paulo; the
    second constrains WHERE they live and cannot.

    Defaulting to OVERLAP is deliberate and is the safe direction: reading an
    hours requirement as a residence requirement manufactures geography out of
    a clock, which is the same class of error as reading WORLDWIDE out of the
    word remote. A residence reading has to be earned from the posting saying
    so.
    """

    OVERLAP = "OVERLAP"
    RESIDENCE = "RESIDENCE"


class PresenceRequirement(StrEnum):
    """How binding the employer made a physical-presence expectation.

    Deliberately separate from ``ExtractionStatus``. Status says how well the
    observation is evidenced -- EXPLICIT means the posting states it. This says
    how *binding* the stated thing is. "We prefer candidates near our London
    office" is an EXPLICIT statement of a PREFERRED presence, and collapsing
    those two axes would turn a preference into a wall, which is exactly the
    mistake ``LanguageRequirement`` exists to prevent for languages.

    There is no NOT_STATED member. Silence is a status, carried by the wrapping
    ``ExtractedField``, and duplicating it here would create two ways to say
    the same thing.
    """

    #: Attendance is a condition of the role.
    REQUIRED = "REQUIRED"
    #: Stated as a preference, and the posting says remote is still possible.
    PREFERRED = "PREFERRED"
    #: An office exists and may be used. Not a condition of anything.
    OPTIONAL = "OPTIONAL"


class ExposureLevel(StrEnum):
    """How much of something a role involves (support, sales, management).

    NONE here means "the posting says there is none", not "we do not know".
    Unknown is expressed by the wrapping field's ExtractionStatus.NOT_STATED.
    The same distinction applies to TravelFrequency.NONE and
    CodingIntensity.NONE.
    """

    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class TravelFrequency(StrEnum):
    """How often the role requires travel, when the posting says so."""

    NONE = "NONE"
    RARE = "RARE"
    QUARTERLY = "QUARTERLY"
    MONTHLY = "MONTHLY"
    FREQUENT = "FREQUENT"


class EmploymentRelationship(StrEnum):
    """How the worker is engaged, as a relationship rather than a local statute.

    Kept deliberately separate from `LocalContractRegime` below. A Brazilian
    posting saying "CLT" is telling you two things at once -- that the worker is
    an EMPLOYEE, and that the employment runs under one particular national
    statute -- and collapsing them produces a vocabulary that cannot describe a
    German employee or an American one without inventing a Brazilian word for
    them. The relationship is the portable half.
    """

    EMPLOYEE = "EMPLOYEE"
    CONTRACTOR_B2B = "CONTRACTOR_B2B"
    EOR = "EOR"
    INTERN = "INTERN"
    APPRENTICE = "APPRENTICE"
    TEMPORARY = "TEMPORARY"
    OTHER = "OTHER"
    UNRESOLVED = "UNRESOLVED"


class LocalContractRegime(StrEnum):
    """A named national engagement regime, recorded only when evidence says so.

    Members are added when a posting population actually uses the term, never
    to complete a set. `UNRESOLVED` is the honest answer for the overwhelming
    majority of postings and is not a defect.
    """

    CLT = "CLT"
    PJ = "PJ"
    UNRESOLVED = "UNRESOLVED"


class EmploymentSource(StrEnum):
    """What kind of thing produced an employment reading.

    The distinction the interface must never lose is EXPLICIT versus
    CONTEXTUAL. A posting that writes "regime CLT" has said so. A posting that
    offers `plano de saude` has offered a benefit that in Brazil usually
    accompanies employment -- which is a likelihood, not a statement, and
    presenting it as one would be this system putting words in an employer's
    mouth.
    """

    EXPLICIT_STATEMENT = "EXPLICIT_STATEMENT"
    #: The employer answered the BOARD'S OWN FORM FIELD, and this is that
    #: answer rather than a sentence.
    #:
    #: Gupy asks every employer to pick a `type` from a closed list written in
    #: Brazilian labour vocabulary -- `vacancy_legal_entity` is pessoa
    #: juridica, `vacancy_type_apprentice` is the jovem aprendiz contract,
    #: `vacancy_type_intermittent` is the contrato intermitente created by the
    #: 2017 reform. Programathor publishes schema.org `employmentType`. Those
    #: are statements by the employer, not inferences by this program, so
    #: `is_explicit` is True for them.
    #:
    #: What separates them from `EXPLICIT_STATEMENT` is that they carry NO
    #: QUOTE. ADR-0002 makes evidence a contiguous substring of the posting
    #: text, and a value selected from a dropdown is not in the posting text at
    #: all. `StructuredGeography` set this precedent for the same reason and in
    #: the same shape: the reading is real, and calling a form answer a quote
    #: would be the first lie in a chain of them.
    STRUCTURED_FIELD = "STRUCTURED_FIELD"
    BENEFIT_CONTEXT = "BENEFIT_CONTEXT"
    DEFAULT = "DEFAULT"

    @property
    def is_explicit(self) -> bool:
        """Whether the EMPLOYER said this, as opposed to this program reading it.

        Two members answer True and neither of them is about wording. A
        dropdown the employer filled in is the employer speaking; a health plan
        in a benefits list is this program noticing a correlation.
        """
        return self in (
            EmploymentSource.EXPLICIT_STATEMENT,
            EmploymentSource.STRUCTURED_FIELD,
        )


class DomesticContext(StrEnum):
    """Whether a posting reads as employment inside one country only.

    NOT an eligibility verdict, and the separation is the entire point. A
    posting offering a 401(k) and never mentioning international hiring is
    probably structured as United States domestic employment; that is worth
    telling a candidate abroad, and it is not the same as the employer having
    said no. `EligibilityStatus` remains the only place a refusal is recorded,
    and only from stated text.
    """

    LIKELY_US_DOMESTIC = "LIKELY_US_DOMESTIC"
    INTERNATIONAL_STATED = "INTERNATIONAL_STATED"
    UNRESOLVED = "UNRESOLVED"


class ContentCompleteness(StrEnum):
    """How much of the employer's own posting this system actually holds.

    A PROVENANCE fact, not a quality judgement and not a measurement of the
    job. It answers one question -- is the stored text the whole posting? --
    and it exists because two 400-character bodies can mean opposite things.

    FULL_CONTENT       the employer's description, as published. The source
                       supplies it, in the list response or through a detail
                       request the adapter actually makes. A short one is a
                       short posting.
    PARTIAL_CONTENT    an excerpt, and the rest cannot be obtained. Jooble
                       returns a `snippet` and its documented contract has no
                       detail endpoint to fetch the remainder from, so the
                       shortness is permanent and is a fact about the SOURCE
                       rather than about the employer.
    METADATA_ONLY      no body at all. A posting known to exist, with a title,
                       a company and a link, and nothing to read.
    UNKNOWN            no adapter answers for this provider. `manual_import`
                       is the standing case: a person pasted a description and
                       only they know whether they pasted all of it.

    WHY THIS IS NOT `data_confidence`. Confidence already falls for a short
    body -- `description_substantial` wants 1,200 characters -- and that is
    the right measurement of "how much did this posting tell us". It cannot
    distinguish a thin posting from a truncated one, and the difference
    matters to a reader deciding whether to open the link: one has nothing
    more to say and the other has plenty, elsewhere.

    WHY IT DOES NOT TOUCH `match_score`. Capping a compatibility number with a
    provenance fact would blend two of the three measurements ADR-0004 keeps
    apart. What a partial body may not do is claim the authority of a full
    one, and the answer to that is to SAY SO, on the card and in a filter,
    not to invent a discount.
    """

    FULL_CONTENT = "FULL_CONTENT"
    PARTIAL_CONTENT = "PARTIAL_CONTENT"
    METADATA_ONLY = "METADATA_ONLY"
    UNKNOWN = "UNKNOWN"


class Seniority(StrEnum):
    """The level of the ADVERTISED ROLE.

    `MANAGER` and `DIRECTOR` were members here once, which made the vocabulary
    answer two questions at the same time: how senior is this role, and does it
    manage people. Those are separate dimensions and a title says nothing
    reliable about the second -- `Lead Data Analyst` is LEAD and manages nobody.
    Organisational titles resolve to LEAD, and people management stays where it
    belongs, as its own observed signal read from the body.

    `UNCLEAR` is gone too, for a different reason: it was a VALUE standing in
    for the absence of one. What replaces it is a value plus a `SenioritySource`
    saying where it came from, so "the posting said mid-level" and "the posting
    said nothing and mid-level is our default" stop being the same row.

    **STAFF AND PRINCIPAL, ADDED 2026-09-07, AND WHY THEY HAD TO BE.**

    `staff`, `principal`, `distinguished` and `fellow` all resolved to SENIOR.
    That is not a rounding: they are the individual-contributor grades ABOVE
    senior, and collapsing them made the product recommend post-senior
    architecture roles to somebody asking for senior execution work. Measured
    on the real corpus: `Staff Engineer - Business Systems` and two
    `Staff Web Engineer` postings sat in the top twenty, every one reading
    SENIOR.

    Five values became seven, and the boundary they draw is one a candidate
    actually feels. It stays a scale of the ROLE'S LEVEL and still says nothing
    about people management, which is why `Staff` is not `Lead`: a staff
    engineer is an individual contributor with post-senior scope, and a lead is
    an organisational position. Both sit outside what somebody asking for
    mid-to-senior work wants, and they sit outside it for different reasons.
    """

    INTERN = "INTERN"
    JUNIOR = "JUNIOR"
    MID = "MID"
    SENIOR = "SENIOR"
    #: Post-senior INDIVIDUAL CONTRIBUTOR scope: owning architecture across
    #: domains, defining standards, mentoring. Not a people-management role.
    STAFF = "STAFF"
    #: Above staff, same track. `distinguished` and `fellow` land here too:
    #: they are the same grade wearing a company's own word for it.
    PRINCIPAL = "PRINCIPAL"
    #: An ORGANISATIONAL position -- lead, manager, director, head, VP, chief.
    #: See the note above on why those collapse and staff does not.
    LEAD = "LEAD"


class SenioritySource(StrEnum):
    """WHERE a seniority reading came from, in descending order of quality.

    The order is the precedence: a title that names an organisational role
    outranks a title that names a grade, which outranks a sentence in the body,
    which outranks an arithmetic band over required years. `DEFAULT` is last and
    is not evidence at all -- it is the operational fallback, and the scorer is
    required to tell it apart from the others.
    """

    TITLE_LEADERSHIP = "TITLE_LEADERSHIP"
    TITLE_GRADE = "TITLE_GRADE"
    DESCRIPTION_STATEMENT = "DESCRIPTION_STATEMENT"
    REQUIRED_YEARS = "REQUIRED_YEARS"
    DEFAULT = "DEFAULT"

    @property
    def is_evidence(self) -> bool:
        """Whether a reading from this source rests on something the posting said.

        The one place the DEFAULT/evidence distinction is expressed, so a caller
        can never re-derive it slightly differently.
        """
        return self is not SenioritySource.DEFAULT


# =========================================================================
# GEOGRAPHY  (M3)
# Three separate concepts, never one field. See milestone-0.md section 4.2.
# =========================================================================


class HiringScopeKind(StrEnum):
    """Where the EMPLOYER allows the worker to reside.

    UNSTATED is the honest output for a posting that says "Remote" and nothing
    more. It is emphatically not WORLDWIDE, and conflating the two is the single
    most expensive error this system could make.
    """

    WORLDWIDE = "WORLDWIDE"
    REGION = "REGION"
    COUNTRY_LIST = "COUNTRY_LIST"
    UNSTATED = "UNSTATED"


class Region(StrEnum):
    """Region tokens, expanded to country sets by `config/places.yaml`.

    REGION_UNKNOWN is what an unrecognised phrase becomes: it forces the
    geography gate to UNRESOLVED and queues the raw phrase for review, rather
    than guessing.
    """

    WORLDWIDE = "WORLDWIDE"
    NORAM = "NORAM"
    LATAM = "LATAM"
    AMERICAS = "AMERICAS"
    EMEA = "EMEA"
    EU = "EU"
    EEA = "EEA"
    APAC = "APAC"
    ANZ = "ANZ"
    MENA = "MENA"
    AFRICA = "AFRICA"
    REGION_UNKNOWN = "REGION_UNKNOWN"


# =========================================================================
# EVALUATION OUTPUTS  (M3 / M5)
# Three separate measurements. Nothing collapses them into one number.
# =========================================================================


class GateResult(StrEnum):
    """Three-valued, never boolean. A job is eliminated only on FAIL."""

    PASS = "PASS"
    FAIL = "FAIL"
    UNRESOLVED = "UNRESOLVED"


class HiddenReason(StrEnum):
    """Why she set a posting aside, when she says.

    OPTIONAL, always. Hiding without a reason is an ordinary thing to do and
    the commonest case; this exists so that a reason CAN be counted, not so
    that one is owed.

    **Evidence for a conversation, never an input to one.** A product that
    learned "she rejects Germany" from one hidden posting would be inventing a
    candidate fact, which invariant 2 forbids and which `config/ownership.py`
    places squarely with her. Nothing in `career_agent.match` may read this,
    and a test asserts it.

    Closed rather than free text because counting is the point: "eleven of the
    last twenty were the wrong seniority" is actionable and "eleven notes" is
    not. `job_application.notes` is already there for anything that does not
    fit, in whatever words she likes.

    The members name what was wrong with the POSTING for her, which is a
    different question from what is wrong with her search. A run of
    `WRONG_PLACE` might mean a geography preference needs changing, or that a
    source is publishing places it should not, or nothing at all -- and which
    of those it is stays hers to decide.
    """

    #: The country, region or on-site requirement does not work for her.
    WRONG_PLACE = "WRONG_PLACE"
    #: Too junior or too senior, whatever the title said.
    WRONG_LEVEL = "WRONG_LEVEL"
    #: Not the work she does, however well it scored.
    WRONG_WORK = "WRONG_WORK"
    #: The title promised her work and the body was something else. The
    #: distinct case ADR-0014 exists for, and worth its own member: it points
    #: at the taxonomy rather than at her preferences.
    TITLE_MISLEADING = "TITLE_MISLEADING"
    #: The compensation, stated or absent.
    PAY = "PAY"
    #: This employer specifically. Never generalised: one company is one
    #: company, and a rule about employers is not something a hide can imply.
    EMPLOYER = "EMPLOYER"
    #: Old, filled, or a repost of something already seen.
    STALE = "STALE"
    #: She said none of the above. Kept so that "I have a reason and it is not
    #: on your list" is expressible without forcing a wrong answer.
    OTHER = "OTHER"


class ScreeningState(StrEnum):
    """Did anything explicitly disqualify this job?"""

    NOT_BLOCKED = "NOT_BLOCKED"
    BLOCKED = "BLOCKED"


class EligibilityStatus(StrEnum):
    """Can the candidate actually work here?

    Deliberately separate from ScreeningState. NOT_BLOCKED + UNRESOLVED is the
    most common healthy outcome: the job survived filtering, and the system is
    not claiming the candidate is confirmed eligible for it.
    """

    # explicit verified evidence on every critical gate
    VERIFIED_ELIGIBLE = "VERIFIED_ELIGIBLE"
    # passes, but on weaker, provider-only or contradicted evidence
    LIKELY_ELIGIBLE = "LIKELY_ELIGIBLE"
    # a critical gate is unknown
    UNRESOLVED = "UNRESOLVED"
    # explicit verified disqualification
    VERIFIED_NOT_ELIGIBLE = "VERIFIED_NOT_ELIGIBLE"


class AnalysisConfidence(StrEnum):
    """How much do we actually know about this posting?"""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class FitBand(StrEnum):
    """Is this the work the candidate wants?

    Reported beside eligibility, never multiplied by it.
    """

    STRONG = "STRONG"
    GOOD = "GOOD"
    MODERATE = "MODERATE"
    WEAK = "WEAK"


# =========================================================================
# PIPELINE STATE  (M1A)
# =========================================================================


class CollectionStatus(StrEnum):
    """Where a job currently sits in the pipeline."""

    DISCOVERED = "DISCOVERED"
    FETCHED = "FETCHED"
    NORMALISED = "NORMALISED"
    PREFILTERED_OUT = "PREFILTERED_OUT"
    FINGERPRINTED = "FINGERPRINTED"
    EXTRACTION_FAILED = "EXTRACTION_FAILED"  # surfaced in the digest, never hidden
    RESOLVED = "RESOLVED"
    EVALUATED = "EVALUATED"
    CLOSED = "CLOSED"


class PipelineRunStatus(StrEnum):
    """Outcome of one stage execution."""

    RUNNING = "RUNNING"
    OK = "OK"
    FAILED = "FAILED"


# =========================================================================
# CANDIDATE CONFIGURATION  (M0)
# =========================================================================


class PreferenceBucket(StrEnum):
    """Strength of a career preference. Never collapsed into keywords."""

    WANT = "WANT"
    INTERESTED = "INTERESTED"
    AVOID = "AVOID"
    NEVER = "NEVER"


class Proficiency(StrEnum):
    """How well the candidate knows a tool."""

    BASIC = "BASIC"
    WORKING = "WORKING"
    ADVANCED = "ADVANCED"
    EXPERT = "EXPERT"


class LanguageLevel(StrEnum):
    """CEFR levels, plus NATIVE.

    A language absent from the profile is treated as not spoken by the language
    gate. Absence is never permission, in configuration as much as extraction.
    """

    NATIVE = "NATIVE"
    C2 = "C2"
    C1 = "C1"
    B2 = "B2"
    B1 = "B1"
    A2 = "A2"
    A1 = "A1"


class WorkAuthorizationBasis(StrEnum):
    """Why the candidate may legally work in a country."""

    CITIZEN = "CITIZEN"
    PERMANENT_RESIDENT = "PERMANENT_RESIDENT"
    RESIDENCE_PERMIT = "RESIDENCE_PERMIT"
    WORK_VISA = "WORK_VISA"


class ContractType(StrEnum):
    """How the candidate is willing to be engaged."""

    FULL_TIME_EMPLOYEE = "FULL_TIME_EMPLOYEE"
    EOR = "EOR"  # Employer of Record (Deel, Remote, Oyster)
    CONTRACTOR_B2B = "CONTRACTOR_B2B"  # invoicing through own entity


class EquityInterest(StrEnum):
    """How much equity matters to the candidate."""

    WANTED = "WANTED"
    NEUTRAL = "NEUTRAL"
    IRRELEVANT = "IRRELEVANT"


class ClaimType(StrEnum):
    """Kind of verified career claim. The evidence base for future resumes."""

    EMPLOYMENT = "EMPLOYMENT"
    SKILL = "SKILL"
    TOOL = "TOOL"
    ACHIEVEMENT = "ACHIEVEMENT"
    METRIC = "METRIC"
    EDUCATION = "EDUCATION"
    CERTIFICATION = "CERTIFICATION"
    PROJECT = "PROJECT"


class ClaimSource(StrEnum):
    """Where a claim came from. Provenance for the truthfulness guarantee."""

    RESUME = "RESUME"
    LINKEDIN = "LINKEDIN"
    SELF_ATTESTED = "SELF_ATTESTED"
    DOCUMENT = "DOCUMENT"


class FeedbackDecision(StrEnum):
    """The candidate's verdict on a recommended job. M6, declared now."""

    INTERESTED = "INTERESTED"
    MAYBE = "MAYBE"
    NOT_INTERESTED = "NOT_INTERESTED"


# =========================================================================
# RESPONSIBILITY VOCABULARY v1  (M0 config / M2 extraction)
#
# The single vocabulary shared by profile.local.yaml and every extraction.
# If those two ever disagree, alignment silently scores zero -- which is why
# there is one enum, imported by both sides.
#
# Values are lowercase snake_case because a human types them into YAML.
#
# This is an Alpha vocabulary, not the product ontology. See milestone-0.md
# section 4.8: the multi-user product must accept open-ended natural language
# mapped to a versioned canonical ontology, preserving unmapped raw concepts.
# The seams for that are RESPONSIBILITY_OTHER below and
# meta.responsibility_vocabulary_version on every fingerprint.
# =========================================================================

RESPONSIBILITY_VOCABULARY_VERSION = 1


class ResponsibilityCategory(StrEnum):
    """v1: 39 categories plus one escape hatch.

    Disambiguation rules for the near-synonymous members live in
    llm/prompts/fingerprint_v2.md at M2 and are documented in
    docs/setup/personal-alpha-environment.md section 8.3. Multiple categories
    may apply to the same posting when the evidence supports each one
    independently; the model must not choose between them arbitrarily.
    """

    # --- Systems and CRM ownership ---------------------------------------
    CRM_ADMINISTRATION = "crm_administration"
    CRM_ARCHITECTURE = "crm_architecture"
    BUSINESS_SYSTEMS_ADMINISTRATION = "business_systems_administration"
    GTM_SYSTEMS_OWNERSHIP = "gtm_systems_ownership"
    USER_ACCESS_AND_PERMISSIONS = "user_access_and_permissions"
    VENDOR_AND_TOOL_EVALUATION = "vendor_and_tool_evaluation"

    # --- Automation and integration --------------------------------------
    WORKFLOW_AUTOMATION = "workflow_automation"
    SYSTEM_INTEGRATION = "system_integration"
    API_INTEGRATION = "api_integration"
    DATA_SYNC_AND_MIGRATION = "data_sync_and_migration"
    INTERNAL_TOOLING = "internal_tooling"

    # --- Data ------------------------------------------------------------
    DATA_MODELING_FOR_BUSINESS_SYSTEMS = "data_modeling_for_business_systems"
    REPORTING_AND_DASHBOARDS = "reporting_and_dashboards"
    ANALYTICS_ENGINEERING = "analytics_engineering"
    DATA_QUALITY_MANAGEMENT = "data_quality_management"

    # --- Process and business --------------------------------------------
    BUSINESS_PROCESS_DESIGN = "business_process_design"
    REQUIREMENTS_GATHERING = "requirements_gathering"
    PROCESS_DOCUMENTATION = "process_documentation"
    CHANGE_MANAGEMENT_AND_ENABLEMENT = "change_management_and_enablement"

    # --- Engineering ------------------------------------------------------
    SCRIPTING_AND_DATA_TRANSFORMATION = "scripting_and_data_transformation"
    LIGHT_BACKEND_DEVELOPMENT = "light_backend_development"
    FULL_SOFTWARE_ENGINEERING = "full_software_engineering"
    TESTING_AND_QA = "testing_and_qa"
    DEVOPS_AND_DEPLOYMENT = "devops_and_deployment"

    # --- Leadership and architecture --------------------------------------
    # Split so that mentoring and delivery ownership are scored separately from
    # having direct reports. people_management = AVOID must not penalise a
    # strong IC role. See milestone-0.md section 4.6.
    PEOPLE_MANAGEMENT = "people_management"
    TECHNICAL_MENTORING = "technical_mentoring"
    PROJECT_LEADERSHIP = "project_leadership"
    CROSS_FUNCTIONAL_COORDINATION = "cross_functional_coordination"
    SOLUTION_ARCHITECTURE = "solution_architecture"

    # --- Customer and commercial exposure ---------------------------------
    TIER1_END_USER_SUPPORT = "tier1_end_user_support"
    TECHNICAL_SUPPORT_ENGINEERING = "technical_support_engineering"
    CUSTOMER_ONBOARDING_IMPLEMENTATION = "customer_onboarding_implementation"
    PRESALES_SOLUTION_CONSULTING = "presales_solution_consulting"
    ACCOUNT_MANAGEMENT = "account_management"
    QUOTA_CARRYING_SALES = "quota_carrying_sales"
    COLD_OUTBOUND_PROSPECTING = "cold_outbound_prospecting"

    # --- Operations and governance ----------------------------------------
    ON_CALL_ROTATION = "on_call_rotation"
    INCIDENT_RESPONSE = "incident_response"
    COMPLIANCE_AND_AUDIT_SUPPORT = "compliance_and_audit_support"

    # --- Escape hatch ------------------------------------------------------
    # Used when a genuine responsibility fits nothing above. The model's raw
    # phrase is preserved and queued for review; what accumulates here is how
    # this vocabulary grows empirically rather than by guesswork.
    RESPONSIBILITY_OTHER = "responsibility_other"


class ExperienceRequirement(StrEnum):
    """How hard a posting's ask for previous experience actually is.

    Listed from the most open to the most closed, which is the order a person
    scanning a list cares about. The order is documentation, not arithmetic:
    nothing in this product compares two of these with `<`.

    **REQUIRED, PREFERRED and NICE_TO_HAVE are three facts, not one.**
    Collapsing them is what turns a missing line on somebody's CV into a hard
    gap on a job that never asked for it.
    """

    #: The posting says in so many words that none is needed.
    NONE_REQUIRED = "NONE_REQUIRED"
    #: A figure of years, carrying a cue that makes it a MINIMUM.
    REQUIRED_MINIMUM = "REQUIRED_MINIMUM"
    #: Experience is asked for, without a figure, as a requirement.
    REQUIRED_UNQUANTIFIED = "REQUIRED_UNQUANTIFIED"
    #: Wanted, and said to be optional: "preferred", "desejavel".
    PREFERRED = "PREFERRED"
    #: Explicitly a bonus: "a plus", "nice to have", "diferencial".
    NICE_TO_HAVE = "NICE_TO_HAVE"
    #: The posting never addressed it. The commonest answer, and not a verdict:
    #: silence is not an invitation and it is not a refusal.
    NOT_STATED = "NOT_STATED"


class EntrySignal(StrEnum):
    """Invitations a posting extends to somebody who is starting out.

    Each one is a phrase an employer WROTE. None is inferred from the absence
    of something else, and none is a synonym for a low seniority band: a
    posting can be titled `Senior` and still say "training provided", and one
    titled `Junior` may say nothing at all.
    """

    NO_EXPERIENCE_REQUIRED = "NO_EXPERIENCE_REQUIRED"
    ENTRY_LEVEL = "ENTRY_LEVEL"
    RECENT_GRADUATE = "RECENT_GRADUATE"
    TRAINING_PROVIDED = "TRAINING_PROVIDED"
    CAREER_CHANGERS_WELCOME = "CAREER_CHANGERS_WELCOME"


class CareerStage(StrEnum):
    """Where the candidate says they are, right now.

    CONTEXT, never identity and never an ingestion constraint. It changes what
    the product EXPLAINS and what it offers to filter by; it changes nothing
    about which postings are collected, how they are fingerprinted, or whether
    a gate passes.

    `PREFER_NOT_TO_SAY` is a real member rather than the absence of a value,
    because "I would rather not answer" and "I have not been asked yet" are
    different facts and a first run has to be able to tell them apart.
    """

    CONTINUING = "CONTINUING"
    CHANGING_CAREERS = "CHANGING_CAREERS"
    FIRST_OPPORTUNITY = "FIRST_OPPORTUNITY"
    RECENTLY_QUALIFIED = "RECENTLY_QUALIFIED"
    RETURNING = "RETURNING"
    PREFER_NOT_TO_SAY = "PREFER_NOT_TO_SAY"

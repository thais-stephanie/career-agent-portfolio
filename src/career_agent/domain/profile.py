"""The search profile: who the candidate is, and what they want.

Three geographic concepts are modelled separately, and keeping them apart is
the point of this module rather than an implementation detail:

* :class:`CandidateGeography` -- where the candidate lives. A gate *input*.
* :class:`JobHiringGeography` -- where an employer permits residence. A hard
  *gate*.
* :class:`TargetCompanyMarkets` -- which countries' companies are interesting.
  A *ranking signal*, never a gate.

A single "preferred markets" field would collapse all three, and would get the
important case exactly wrong: a German company hiring Germany-only is
ineligible from Brazil no matter how attractive the German market is, while a
Swiss company hiring worldwide is exactly what the candidate is looking for.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.functional_validators import BeforeValidator

from career_agent.domain.countries import normalise_country
from career_agent.domain.enums import (
    CodingIntensity,
    ContractType,
    EquityInterest,
    GateResult,
    HiringScopeKind,
    LanguageLevel,
    PreferenceBucket,
    Proficiency,
    Region,
    ResponsibilityCategory,
    SoftwareCentrality,
    WorkAuthorizationBasis,
)

#: A country code that is normalised (and validated) as it is read.
Country = Annotated[str, BeforeValidator(normalise_country)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- geography -------------------------------------------------------------


class Relocation(_Model):
    willing: bool = False
    open_to_countries: list[Country] = Field(default_factory=list)
    requires_full_support: bool = True

    @model_validator(mode="after")
    def _check(self) -> "Relocation":
        if self.open_to_countries and not self.willing:
            raise ValueError(
                "open_to_countries is set but willing is false; set willing: true "
                "or clear the list, so the geography gate cannot be misread"
            )
        return self


class CandidateGeography(_Model):
    """Where the candidate is, and intends to remain. A gate input."""

    residence_country: Country
    residence_region: Region | None = None
    city: str | None = None
    timezone: str
    intends_to_remain: bool = True
    relocation: Relocation = Field(default_factory=Relocation)


class JobHiringGeography(_Model):
    """Which hiring scopes the candidate can accept. A hard gate."""

    prefer_worldwide_remote: bool = True
    acceptable_hiring_scopes: list[HiringScopeKind | Region | Country] = Field(min_length=1)
    accept_if_scope_includes_residence: bool = True
    #: What to do when a posting explicitly requires residence elsewhere.
    #: FAIL removes it from the feed; UNRESOLVED keeps it visible as
    #: "not available unless you relocate".
    scope_excluding_residence_is: GateResult = GateResult.FAIL

    @model_validator(mode="after")
    def _check(self) -> "JobHiringGeography":
        if self.scope_excluding_residence_is is GateResult.PASS:
            raise ValueError(
                "scope_excluding_residence_is must be FAIL or UNRESOLVED; PASS would "
                "assert eligibility for a job that excludes the candidate's country"
            )
        return self


class TargetCompanyMarkets(_Model):
    """Where interesting companies are based. Ranking and discovery only.

    Only NEVER may block, and it blocks on the company's headquarters, never on
    where the job allows the worker to live.
    """

    WANT: list[Country] = Field(default_factory=list)
    INTERESTED: list[Country] = Field(default_factory=list)
    AVOID: list[Country] = Field(default_factory=list)
    NEVER: list[Country] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_country_in_two_buckets(self) -> "TargetCompanyMarkets":
        seen: dict[str, str] = {}
        for bucket in PreferenceBucket:
            for country in getattr(self, bucket.value):
                if country in seen:
                    raise ValueError(
                        f"{country} appears in both {seen[country]} and {bucket.value}"
                    )
                seen[country] = bucket.value
        return self


# --- legal capacity --------------------------------------------------------


class WorkAuthorization(_Model):
    country: Country
    basis: WorkAuthorizationBasis


class Engagement(_Model):
    """How the candidate may legally be engaged. Distinct from geography:
    being allowed to live somewhere and being employable there are different
    questions."""

    citizenships: list[Country] = Field(min_length=1)
    work_authorizations: list[WorkAuthorization] = Field(min_length=1)
    needs_visa_sponsorship_for: list[Country] = Field(default_factory=list)
    can_invoice_as_contractor: bool = False
    has_local_entity: bool = False
    open_to_eor: bool = True
    contract_types_accepted: list[ContractType] = Field(min_length=1)


class LanguageSkill(_Model):
    code: str = Field(min_length=2, max_length=3)
    level: LanguageLevel


# --- career intent ---------------------------------------------------------


class ResponsibilityPreferences(_Model):
    """WANT / INTERESTED / AVOID / NEVER over the shared vocabulary.

    A category in two buckets would make its effect on ranking depend on
    dictionary ordering, so it is rejected outright.
    """

    WANT: list[ResponsibilityCategory] = Field(default_factory=list)
    INTERESTED: list[ResponsibilityCategory] = Field(default_factory=list)
    AVOID: list[ResponsibilityCategory] = Field(default_factory=list)
    NEVER: list[ResponsibilityCategory] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_category_in_two_buckets(self) -> "ResponsibilityPreferences":
        seen: dict[str, str] = {}
        for bucket in PreferenceBucket:
            for category in getattr(self, bucket.value):
                if category in seen:
                    raise ValueError(
                        f"responsibility {category.value!r} appears in both "
                        f"{seen[category]} and {bucket.value}"
                    )
                seen[category] = bucket.value
        return self

    def bucket_for(self, category: ResponsibilityCategory) -> PreferenceBucket | None:
        """Which bucket a category falls in, or None if it is neutral.

        Neutral is a real position, not a gap: the responsibility is recorded
        and shown, but neither rewarded nor penalised.
        """
        for bucket in PreferenceBucket:
            if category in getattr(self, bucket.value):
                return bucket
        return None


class StringPreferences(_Model):
    """Free-vocabulary preference buckets (work environment, industries)."""

    WANT: list[str] = Field(default_factory=list)
    INTERESTED: list[str] = Field(default_factory=list)
    AVOID: list[str] = Field(default_factory=list)
    NEVER: list[str] = Field(default_factory=list)


class CodingIntensityPreference(_Model):
    preferred: list[CodingIntensity] = Field(default_factory=list)
    acceptable: list[CodingIntensity] = Field(default_factory=list)
    never: list[CodingIntensity] = Field(default_factory=list)


class CareerIntent(_Model):
    #: Open-ended description of the work wanted. Feeds semantic similarity
    #: only; it is never a gate. In the multi-user product this becomes the
    #: input to the natural-language-to-ontology mapping layer.
    narrative: str = Field(default="", max_length=4000)
    responsibilities: ResponsibilityPreferences = Field(default_factory=ResponsibilityPreferences)
    work_environment: StringPreferences = Field(default_factory=StringPreferences)
    industries: StringPreferences = Field(default_factory=StringPreferences)
    coding_intensity: CodingIntensityPreference = Field(default_factory=CodingIntensityPreference)


# --- software --------------------------------------------------------------


class KnownSoftware(_Model):
    name: str
    proficiency: Proficiency
    years: float | None = Field(default=None, ge=0)


class AvoidedSoftware(_Model):
    name: str
    note: str | None = None


class RefusedWhenCentral(_Model):
    """A tool that blocks the job when it is central, and only then.

    CORE or REQUIRED is a hard conflict; PREFERRED is a warning; MENTIONED and
    ALTERNATIVE have no effect, because "HubSpot, Salesforce or another CRM"
    means the employer already accepted an alternative.
    """

    name: str
    max_acceptable_centrality: SoftwareCentrality = SoftwareCentrality.PREFERRED


class SoftwarePreferences(_Model):
    known: list[KnownSoftware] = Field(default_factory=list)
    central_ambition: list[str] = Field(default_factory=list)
    growth: list[str] = Field(default_factory=list)
    avoid: list[AvoidedSoftware] = Field(default_factory=list)
    refuse_when_central: list[RefusedWhenCentral] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ambition_and_refusal_are_disjoint(self) -> "SoftwarePreferences":
        refused = {r.name for r in self.refuse_when_central}
        clash = refused & set(self.central_ambition)
        if clash:
            raise ValueError(
                "software cannot be both a career ambition and refused when central: "
                f"{sorted(clash)}"
            )
        return self


# --- compensation, availability, targeting ---------------------------------


class Money(_Model):
    amount: float = Field(ge=0)
    currency: str = Field(min_length=3, max_length=3)


class Compensation(_Model):
    currency_preference: list[str] = Field(default_factory=list)
    annual_target: Money | None = None
    #: An amount of 0 means the compensation gate is SKIPPED entirely, not that
    #: it always passes. Compensation then affects ranking only.
    annual_minimum: Money | None = None
    equity_interest: EquityInterest = EquityInterest.NEUTRAL

    @property
    def has_hard_floor(self) -> bool:
        return self.annual_minimum is not None and self.annual_minimum.amount > 0


class TravelTolerance(_Model):
    max_trips_per_year: int = Field(default=0, ge=0)
    requires_expenses_covered: bool = True
    requires_visa_support: bool = True


class Availability(_Model):
    timezone_overlap_hours_max: int = Field(default=8, ge=0, le=24)
    earliest_start_hour_local: int = Field(default=8, ge=0, le=23)
    latest_end_hour_local: int = Field(default=19, ge=0, le=23)
    travel: TravelTolerance = Field(default_factory=TravelTolerance)

    @model_validator(mode="after")
    def _check_hours(self) -> "Availability":
        if self.latest_end_hour_local <= self.earliest_start_hour_local:
            raise ValueError("latest_end_hour_local must be after earliest_start_hour_local")
        return self


class CompanySizeRange(_Model):
    min: int = Field(default=1, ge=1)
    max: int = Field(default=1_000_000, ge=1)

    @model_validator(mode="after")
    def _check(self) -> "CompanySizeRange":
        if self.max < self.min:
            raise ValueError("company_size.max is below company_size.min")
        return self


class Targeting(_Model):
    company_stages: StringPreferences = Field(default_factory=StringPreferences)
    company_size: CompanySizeRange = Field(default_factory=CompanySizeRange)
    dream_companies: list[str] = Field(default_factory=list)
    excluded_companies: list[str] = Field(default_factory=list)


# --- the document ----------------------------------------------------------

SEARCH_PROFILE_SCHEMA_VERSION = 2


class SearchProfile(_Model):
    """The whole of profile.local.yaml, validated.

    Changing anything here produces a new content hash and therefore a new
    ``search_profile_version`` row, so every past evaluation remains
    reproducible against the preferences that actually produced it.
    """

    schema_version: int = Field(ge=1)
    candidate_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=200)

    candidate_geography: CandidateGeography
    job_hiring_geography: JobHiringGeography
    target_company_markets: TargetCompanyMarkets = Field(default_factory=TargetCompanyMarkets)
    engagement: Engagement
    languages: list[LanguageSkill] = Field(min_length=1)

    intent: CareerIntent = Field(default_factory=CareerIntent)
    software: SoftwarePreferences = Field(default_factory=SoftwarePreferences)
    compensation: Compensation = Field(default_factory=Compensation)
    availability: Availability = Field(default_factory=Availability)
    targeting: Targeting = Field(default_factory=Targeting)

    @model_validator(mode="after")
    def _check_document(self) -> "SearchProfile":
        if self.schema_version != SEARCH_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"schema_version {self.schema_version} is not supported; this build "
                f"expects {SEARCH_PROFILE_SCHEMA_VERSION}"
            )

        codes = [lang.code.lower() for lang in self.languages]
        duplicates = {c for c in codes if codes.count(c) > 1}
        if duplicates:
            raise ValueError(f"language listed more than once: {sorted(duplicates)}")

        # The candidate must be able to work where they live, or the geography
        # gate would pass jobs the candidate cannot legally take.
        home = self.candidate_geography.residence_country
        authorised = {auth.country for auth in self.engagement.work_authorizations}
        if home not in authorised:
            raise ValueError(
                f"residence_country {home} has no matching entry in "
                "engagement.work_authorizations; add one, or the eligibility gates "
                "would assume a right to work that has not been stated"
            )
        return self

    def speaks(self, language_code: str) -> bool:
        """Whether the candidate speaks a language at all.

        A language absent from the profile is treated as not spoken. Absence is
        never permission here either.
        """
        return any(lang.code.lower() == language_code.lower() for lang in self.languages)

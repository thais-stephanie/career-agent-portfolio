"""Turning validated family outputs into one durable JobFingerprint.

The model never does this. Two independent completions produced the two family
payloads; neither saw the other, neither knows what the other numbered its
evidence, and asking them to coordinate would be asking two separate
completions to agree -- a failure that would only surface downstream as a
citation pointing at the wrong sentence.

So assembly is pure code, and it owns four things the compact transport made
newly necessary:

**Evidence namespacing.** Every family is told to number from ``ev_01``, so
collisions are guaranteed by design. Ids become ``ev_d01`` and ``ev_p01`` here,
deterministically, and every citation is rewritten with them.

**Dimension vocabulary.** A row-shaped transport can carry
``{"dimension": "made_up_dimension"}`` in a way a property-per-dimension schema
never could. Compact means fewer JSON properties; it does not mean weaker
typing, so every dimension name is checked against the family's approved list.

**Duplicates.** A singular dimension appearing twice is rejected outright. Not
first-wins, not last-wins, not highest-confidence -- those all quietly pick an
answer, and which one is arbitrary.

**Missing dimensions.** The compact form solved the schema limit by turning
dimensions into rows, which means a forgotten dimension no longer looks
different from a stated one. Every required singular dimension must appear,
even as NOT_STATED. "The model said nothing was stated" and "the model forgot
this exists" are not the same fact, and the second must trigger a retry.
"""

from dataclasses import dataclass, field
from typing import Any

from career_agent.domain.enums import (
    CodingIntensity,
    EvidenceSourceKind,
    ExposureLevel,
    ExtractionStatus,
    HiringScopeKind,
    MetadataDimension,
    PresenceRequirement,
    Region,
    RequirementStrength,
    Seniority,
    TimezoneConstraintKind,
    TravelFrequency,
    WorkModel,
)
from career_agent.domain.extracted import (
    ExtractedField,
    enforce_not_applicable_preconditions,
)
from career_agent.domain.fingerprint import (
    CompanyContext,
    Compensation,
    EligibilityFacts,
    Evidence,
    Exposure,
    FingerprintMeta,
    HiringScope,
    JobFingerprint,
    LanguageObservation,
    ProviderObservation,
    ResponsibilityObservation,
    RoleSignals,
    SoftwareObservation,
    TimezoneRequirement,
    WorkEnvironment,
    WorksiteRequirement,
)
from career_agent.llm.transport import (
    ALL_DIMENSIONS,
    TDescriptionFamily,
    TObservation,
    TProviderFamily,
)

#: Prefixes that make evidence ids globally unique inside one fingerprint.
DESCRIPTION_PREFIX = "d"
PROVIDER_PREFIX = "p"


class AssemblyError(ValueError):
    """The families cannot be merged into a valid document.

    Always actionable: it names the family, the dimension and what was wrong, so
    the retry has something specific to feed back to the model.
    """


@dataclass
class AssemblyReport:
    """What assembly had to correct on the way through."""

    demotions: list[str] = field(default_factory=list)
    unmapped_responsibilities: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


# =========================================================================
# EVIDENCE NAMESPACING
# =========================================================================


def namespace_id(prefix: str, raw: str) -> str:
    """`ev_01` from the description family becomes `ev_d01`.

    Deterministic and reversible by eye, so a stored citation still reads as
    "the first thing the description call cited" months later during an audit.
    """
    body = raw[3:] if raw.startswith("ev_") else raw
    return f"ev_{prefix}{body}"


def _convert_evidence(
    rows: list[Any],
    prefix: str,
    expected: EvidenceSourceKind,
    family: str,
    provider_identity: str = "",
) -> list[Evidence]:
    """Rewrite one family's citations into the shared namespace.

    A family citing the wrong source kind is rejected here rather than
    corrected. The description call was never given the provider payload, so a
    PROVIDER_FIELD citation from it cannot be a mistake about formatting -- it
    is a claim about a source the call never saw.

    WHO OWNS `provider`
    -------------------
    The pipeline does, and this is where it is stamped. Which ATS a payload came
    from is not an observation about the posting -- it is provenance the caller
    already holds on `JobSource`, rendered into the very block the model reads
    and never asked for back. `provider_v3` accordingly asks for `source_field`
    and `source_value` and nothing else.

    M2 Stage 0 found the gap the hard way: the transport made `provider`
    optional, the domain required it, the prompt never requested it, and every
    one of 26 live extractions died here with "PROVIDER_FIELD evidence requires
    provider and source_field". The assisted development run had not caught it
    because the assisting agent could see the whole context and helpfully
    supplied a field no real model was ever asked for.

    A model that volunteers a *matching* provider is accepted and its value
    discarded in favour of the deterministic one. A model that volunteers a
    *different* one is rejected, matching how this function already treats a
    family citing the wrong source kind: a contradiction about provenance is not
    something to quietly correct, because whichever value we picked would be a
    guess about which of two sources the citation actually came from.
    """
    out: list[Evidence] = []
    for row in rows:
        if row.source_kind is not expected:
            raise AssemblyError(
                f"{family} family emitted {row.source_kind} evidence {row.id!r}, but this "
                f"family may only cite {expected}; it was never given the other source"
            )

        provider = row.provider
        if expected is EvidenceSourceKind.PROVIDER_FIELD:
            claimed = (provider or "").strip().casefold()
            if claimed and claimed != provider_identity.strip().casefold():
                raise AssemblyError(
                    f"{family} evidence {row.id!r} claims provider {provider!r}, but this "
                    f"posting was collected from {provider_identity!r}. Provider identity is "
                    "pipeline provenance, not model output, and a contradiction about it "
                    "cannot be resolved without guessing which source the citation names."
                )
            provider = provider_identity

        out.append(
            Evidence(
                id=namespace_id(prefix, row.id),
                source_kind=row.source_kind,
                quote=row.quote,
                provider=provider,
                source_field=row.source_field,
                source_value=row.source_value,
            )
        )
    return out


def _cite(prefix: str, raw: str | None) -> str | None:
    return namespace_id(prefix, raw) if raw else None


# =========================================================================
# DIMENSION ROWS
# =========================================================================


def index_observations(
    rows: list[TObservation], allowed: tuple[str, ...], family: str
) -> dict[str, TObservation]:
    """Check the vocabulary, reject duplicates, require completeness.

    All three failures are raised rather than repaired, because each one means
    the model produced something we cannot interpret without guessing:

    * an unknown dimension is a name we have no semantics for;
    * a duplicate is two answers where the domain allows one, and every
      tie-break rule (first, last, highest confidence) is arbitrary;
    * a missing dimension is indistinguishable from a stated NOT_STATED once
      stored, which is exactly the completeness guarantee the compact
      representation put at risk.
    """
    permitted = set(allowed)
    indexed: dict[str, TObservation] = {}

    for row in rows:
        if row.dimension not in permitted:
            raise AssemblyError(
                f"{family} family returned unknown dimension {row.dimension!r}; "
                f"a compact transport still has a closed vocabulary"
            )
        if row.dimension in indexed:
            raise AssemblyError(
                f"{family} family returned {row.dimension!r} twice; it is a singular "
                "dimension and choosing between the two answers would be arbitrary"
            )
        indexed[row.dimension] = row

    missing = sorted(permitted - set(indexed))
    if missing:
        raise AssemblyError(
            f"{family} family omitted {missing}; every dimension must be returned even "
            "when its status is NOT_STATED, so that 'nothing was stated' stays "
            "distinguishable from 'the model forgot'"
        )
    return indexed


# =========================================================================
# TYPED VALUES
#
# The transport carries every value as a string so the schema stays flat. Here
# each one is parsed back into the type its dimension actually has, and a value
# that will not parse is a failure rather than a silent coercion -- a salary
# that quietly becomes None is worse than one that fails loudly.
# =========================================================================


def _parse_bool(raw: str) -> bool:
    lowered = raw.strip().casefold()
    if lowered in {"true", "yes", "y", "1"}:
        return True
    if lowered in {"false", "no", "n", "0"}:
        return False
    raise AssemblyError(f"{raw!r} is not a boolean")


def _parse_float(raw: str) -> float:
    cleaned = raw.replace(",", "").replace("_", "").strip()
    for symbol in ("$", "€", "£", "R$", "USD", "EUR", "GBP", "BRL"):
        cleaned = cleaned.replace(symbol, "")
    try:
        return float(cleaned.strip())
    except ValueError as exc:
        raise AssemblyError(f"{raw!r} is not a number") from exc


def _parse_list(raw: str) -> list[str]:
    return [part.strip() for part in raw.split(",") if part.strip()]


def _parse_enum(enum: Any, raw: str, dimension: str) -> Any:
    """Enum members arrive as their names, case-insensitively.

    Vendors are documented to vary enum casing in structured output, so
    matching on the upper-cased name is a compatibility requirement rather than
    leniency.
    """
    wanted = raw.strip().upper().replace("-", "_").replace(" ", "_")
    for member in enum:
        if member.name == wanted or str(member.value).upper() == wanted:
            return member
    raise AssemblyError(
        f"{dimension}: {raw!r} is not a member of {enum.__name__} "
        f"({', '.join(m.name for m in enum)})"
    )


def _parse_hiring_scope(raw: str) -> HiringScope:
    """`WORLDWIDE`, `REGION:EMEA`, `COUNTRY_LIST:US,CA !CU,IR`, `UNSTATED`.

    Optionally prefixed with how binding the scope is:

        `PREFERRED:COUNTRY_LIST:BR`      the posting says "preferably based in"
        `REQUIRED:COUNTRY_LIST:US,CA`    the posting says "must be based in"
        `COUNTRY_LIST:US`                no prefix -- read as REQUIRED

    A bare scope means REQUIRED because that is what an unhedged statement of
    geography means, and because making the model write the prefix on every row
    would add a way to get the common case wrong. The prefix earns its place on
    the uncommon case, where the alternative is a preference silently becoming
    a wall.

    The prefix is unambiguous: no ``RequirementStrength`` member is also a
    ``HiringScopeKind`` member, so the first token identifies itself.

    Countries may be ISO 3166-1 alpha-2 (`US`) or ISO 3166-2 subdivisions
    (`US-AZ`). 12 postings in 18,549 restrict below country level, and
    flattening those to `US` would pass a candidate in any of the other 24
    states straight through the geography gate.

    A deliberately small grammar. The alternative -- a nested object -- would
    reintroduce exactly the union-typed properties the compact transport was
    adopted to avoid, on the single most important dimension in the system.
    """
    text = raw.strip()
    strength = RequirementStrength.REQUIRED
    head, sep, rest = text.partition(":")
    if sep and head.strip().upper() in set(RequirementStrength):
        strength = RequirementStrength(head.strip().upper())
        text = rest.strip()

    head, _, tail = text.partition(":")
    kind = _parse_enum(HiringScopeKind, head, "hiring_scope")

    body, _, excluded = tail.partition("!")
    exclusions = _parse_list(excluded)

    if kind is HiringScopeKind.REGION:
        regions = [_parse_enum(Region, token, "hiring_scope.region") for token in _parse_list(body)]
        return HiringScope(
            kind=kind, regions=regions, exclusions=exclusions, requirement_level=strength
        )
    if kind is HiringScopeKind.COUNTRY_LIST:
        return HiringScope(
            kind=kind,
            countries=[c.upper() for c in _parse_list(body)],
            exclusions=exclusions,
            requirement_level=strength,
        )
    return HiringScope(kind=kind, exclusions=exclusions, requirement_level=strength)


def _parse_timezone(raw: str) -> TimezoneRequirement:
    """`CET+4` means "anchored on CET, four hours of overlap"; `CET` alone
    means an anchor with no stated overlap.

    Optionally prefixed with what the timezone constrains:

        `OVERLAP:EST`      you must be able to cover these hours
        `RESIDENCE:EST`    you must be located in this timezone
        `EST+4`            no prefix -- read as OVERLAP

    Same prefix shape as the hiring-scope and worksite grammars, and for the
    same reason: one pattern is easier to teach a model than three. OVERLAP is
    the unprefixed default because it is the reading that cannot invent a
    place -- a person in Brazil covers EST hours comfortably, and turning that
    sentence into a location requirement would manufacture geography out of a
    clock.
    """
    text = raw.strip()
    kind = TimezoneConstraintKind.OVERLAP
    head, sep, rest = text.partition(":")
    if sep and head.strip().upper() in set(TimezoneConstraintKind):
        kind = TimezoneConstraintKind(head.strip().upper())
        text = rest.strip()

    anchor, sign, hours = text.partition("+")
    if sign and hours.strip().isdigit():
        return TimezoneRequirement(
            anchor=anchor.strip(), overlap_hours=int(hours.strip()), kind=kind
        )
    return TimezoneRequirement(anchor=text, kind=kind)


def _parse_worksite(raw: str) -> WorksiteRequirement:
    """`REQUIRED:New York` -- a level, and optionally where.

    Same shape as the hiring-scope grammar, and deliberately so: both are
    compound values riding a row-based transport, and one grammar is easier to
    teach a model than two.

    The level is what a bare list of places could never carry.
    `OPTIONAL:New York` and `REQUIRED:New York` name the same office and are
    opposite facts about whether the job is reachable.
    """
    level, _, body = raw.partition(":")
    requirement = _parse_enum(PresenceRequirement, level.strip(), "worksite_requirement.level")
    return WorksiteRequirement(level=requirement, locations=_parse_list(body))


#: How each dimension's string is turned back into a typed value. A dimension
#: absent from here is carried through as a plain string, which is correct for
#: free-text observations such as team_context.
_PARSERS: dict[str, Any] = {
    "seniority_signal": lambda v: _parse_enum(Seniority, v, "seniority_signal"),
    "coding_intensity": lambda v: _parse_enum(CodingIntensity, v, "coding_intensity"),
    "exposure.people_management": lambda v: _parse_enum(ExposureLevel, v, "people_management"),
    "exposure.sales_exposure": lambda v: _parse_enum(ExposureLevel, v, "sales_exposure"),
    "exposure.support_exposure": lambda v: _parse_enum(ExposureLevel, v, "support_exposure"),
    "exposure.on_call": lambda v: _parse_enum(ExposureLevel, v, "on_call"),
    "work_environment.work_model": lambda v: _parse_enum(WorkModel, v, "work_model"),
    "work_environment.worksite_requirement": _parse_worksite,
    "work_environment.async_signals": _parse_bool,
    "hiring_scope": _parse_hiring_scope,
    "timezone_requirement": _parse_timezone,
    "visa_sponsorship": _parse_bool,
    "eor_available": _parse_bool,
    "contractor_eligible": _parse_bool,
    "relocation_required": _parse_bool,
    "relocation_allowed": _parse_bool,
    "travel_required": _parse_bool,
    "travel_frequency": lambda v: _parse_enum(TravelFrequency, v, "travel_frequency"),
    "travel_expenses_covered": _parse_bool,
    "business_visa_support": _parse_bool,
    "compensation.min": _parse_float,
    "compensation.max": _parse_float,
}


def to_field(row: TObservation, prefix: str) -> ExtractedField[Any]:
    """One transport row becomes one durable observation.

    The status invariants in `ExtractedField` do the enforcing: a row claiming
    EXPLICIT without evidence, or NOT_STATED with a value, is rejected there
    rather than here, so there is exactly one place those rules live.
    """
    value: Any = None
    if row.value is not None and row.status in (
        ExtractionStatus.EXPLICIT,
        ExtractionStatus.INFERRED,
    ):
        parser = _PARSERS.get(row.dimension)
        value = parser(row.value) if parser else row.value

    return ExtractedField(
        value=value,
        status=row.status,
        confidence=row.confidence,
        evidence_id=_cite(prefix, row.evidence_id),
        not_applicable_because=row.not_applicable_because or None,
        reasoning=row.reasoning[:280],
    )


# =========================================================================
# ASSEMBLY
# =========================================================================


def assemble(
    description: TDescriptionFamily,
    provider: TProviderFamily,
    meta: FingerprintMeta,
    *,
    source_provider: str,
) -> tuple[JobFingerprint, AssemblyReport]:
    """Merge two validated family outputs into one durable document.

    `source_provider` is the ATS this posting was collected from, taken from
    `JobSource`. It is keyword-only and has no default on purpose: it is the
    provenance stamped onto every provider citation, and a caller that could
    forget it would produce a document whose second provenance channel names
    nobody.

    Raises `AssemblyError` on anything that cannot be resolved without guessing.
    Everything it *can* resolve safely -- an unearned NOT_APPLICABLE, an
    unmapped responsibility -- is corrected and reported rather than raised,
    because failing a whole extraction over one over-confident field would cost
    a retry to fix something the code already fixed correctly.
    """
    report = AssemblyReport()
    d, p = DESCRIPTION_PREFIX, PROVIDER_PREFIX

    rows = index_observations(description.observations, ALL_DIMENSIONS, "description")

    evidence = _convert_evidence(
        description.evidence, d, EvidenceSourceKind.JOB_DESCRIPTION, "description"
    ) + _convert_evidence(
        provider.evidence,
        p,
        EvidenceSourceKind.PROVIDER_FIELD,
        "provider",
        provider_identity=source_provider,
    )

    fields = {name: to_field(row, d) for name, row in rows.items()}

    # The precondition table runs before the document is built, so an unearned
    # NOT_APPLICABLE never reaches storage. It corrects toward NOT_STATED and
    # reports what it corrected; it never raises.
    fields, demotions = enforce_not_applicable_preconditions(fields)
    report.demotions = [f"{d.dimension}: {d.reason}" for d in demotions]

    responsibilities = []
    for item in description.responsibilities:
        if item.raw_phrase and item.category.name == "RESPONSIBILITY_OTHER":
            report.unmapped_responsibilities.append(item.raw_phrase)
        responsibilities.append(
            ResponsibilityObservation(
                category=item.category,
                prominence=item.prominence,
                status=ExtractionStatus.EXPLICIT if item.evidence_id else ExtractionStatus.INFERRED,
                confidence=item.confidence,
                evidence_id=_cite(d, item.evidence_id),
                raw_phrase=item.raw_phrase,
            )
        )

    software = [
        SoftwareObservation(
            raw_mention=item.raw_mention,
            canonical_suggestion=item.canonical_suggestion,
            centrality=item.centrality,
            alternative_group=item.alternative_group,
            status=ExtractionStatus.EXPLICIT if item.evidence_id else ExtractionStatus.INFERRED,
            confidence=item.confidence,
            evidence_id=_cite(d, item.evidence_id),
        )
        for item in description.software
    ]

    languages = [
        LanguageObservation(
            language_code=item.language_code,
            requirement_level=item.requirement_level,
            status=ExtractionStatus.EXPLICIT if item.evidence_id else ExtractionStatus.INFERRED,
            confidence=item.confidence,
            evidence_id=_cite(d, item.evidence_id),
        )
        for item in description.languages
    ]

    provider_observations = []
    for row in provider.observations:
        if not row.evidence_id:
            raise AssemblyError(
                f"provider observation {row.dimension!r} carries no evidence; a provider "
                "claim is only admissible if it names the payload path it came from"
            )
        provider_observations.append(
            ProviderObservation(
                dimension=_parse_enum(MetadataDimension, row.dimension, "provider.dimension"),
                value=row.value,
                status=row.status,
                confidence=row.confidence,
                evidence_id=namespace_id(p, row.evidence_id),
            )
        )

    document = JobFingerprint(
        meta=meta,
        evidence=evidence,
        role=RoleSignals(
            observed_title=description.observed_title,
            seniority_signal=fields["seniority_signal"],
            function_signals=description.function_signals[:8],
            team_context=fields["team_context"],
        ),
        responsibilities=responsibilities,
        software=software,
        coding_intensity=fields["coding_intensity"],
        exposure=Exposure(
            people_management=fields["exposure.people_management"],
            sales_exposure=fields["exposure.sales_exposure"],
            support_exposure=fields["exposure.support_exposure"],
            on_call=fields["exposure.on_call"],
        ),
        work_environment=WorkEnvironment(
            work_model=fields["work_environment.work_model"],
            onsite_frequency=fields["work_environment.onsite_frequency"],
            worksite_requirement=fields["work_environment.worksite_requirement"],
            async_signals=fields["work_environment.async_signals"],
        ),
        eligibility=EligibilityFacts(
            **{name: fields[name] for name in EligibilityFacts.model_fields}
        ),
        languages=languages,
        compensation=Compensation(
            **{f"{n}": fields[f"compensation.{n}"] for n in Compensation.model_fields}
        ),
        company_context=CompanyContext(
            **{f"{n}": fields[f"company_context.{n}"] for n in CompanyContext.model_fields}
        ),
        provider_observations=provider_observations,
    )
    return document, report

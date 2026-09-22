"""Assembly is where two independent completions become one document.

The compact transport bought schema headroom by turning dimensions into rows.
That trade has a price, and these tests are the price: a row-shaped payload can
carry a dimension name nobody defined, the same dimension twice, or quietly omit
one altogether -- none of which a property-per-dimension schema could express.

So the rules being pinned here are not ceremony. They are the completeness and
vocabulary guarantees that the property-based schema used to provide for free.
"""

import pytest

from career_agent.domain.enums import (
    EvidenceSourceKind,
    ExtractionStatus,
    HiringScopeKind,
    MetadataDimension,
    Prominence,
    ResponsibilityCategory,
    SoftwareCentrality,
)
from career_agent.domain.fingerprint import FINGERPRINT_SCHEMA_VERSION, FingerprintMeta
from career_agent.llm.assemble import AssemblyError, assemble, namespace_id
from career_agent.llm.transport import (
    ALL_DIMENSIONS,
    TDescriptionFamily,
    TEvidence,
    TObservation,
    TProviderFamily,
    TResponsibility,
    TSoftware,
)

META = FingerprintMeta(
    schema_version=FINGERPRINT_SCHEMA_VERSION,
    prompt_version="description_v1",
    model="test-model",
    content_hash="sha256:abc",
    payload_hash="sha256:def",
)


def rows(**overrides: TObservation) -> list[TObservation]:
    """Every required dimension as NOT_STATED, with named ones replaced.

    Completeness is mandatory, so a helper that starts from "the model said
    nothing about anything" keeps each test about the one thing it is testing.
    """
    base = {
        name: TObservation(dimension=name, status=ExtractionStatus.NOT_STATED)
        for name in ALL_DIMENSIONS
    }
    base.update({row.dimension: row for row in overrides.values()})
    return list(base.values())


def description(**kwargs) -> TDescriptionFamily:
    defaults = {
        "observed_title": "Business Technology Analyst",
        "observations": rows(),
        "responsibilities": [
            TResponsibility(
                category=ResponsibilityCategory.WORKFLOW_AUTOMATION,
                prominence=Prominence.PRIMARY,
                confidence=0.9,
                evidence_id="ev_01",
            )
        ],
        "evidence": [
            TEvidence(
                id="ev_01",
                source_kind=EvidenceSourceKind.JOB_DESCRIPTION,
                quote="You will automate our internal workflows.",
            )
        ],
    }
    defaults.update(kwargs)
    return TDescriptionFamily(**defaults)


def provider(**kwargs) -> TProviderFamily:
    return TProviderFamily(**kwargs)


def test_two_families_become_one_document() -> None:
    fingerprint, report = assemble(description(), provider(), META, source_provider="greenhouse")

    assert fingerprint.role.observed_title == "Business Technology Analyst"
    assert len(fingerprint.responsibilities) == 1
    assert fingerprint.evidence[0].id == "ev_d01"
    assert report.demotions == []


def test_evidence_ids_are_namespaced_by_family() -> None:
    """Both families number from ev_01, so collision is the default outcome.

    Without namespacing one citation silently overwrites the other and the
    fingerprint ends up pointing at the wrong source entirely.
    """
    fingerprint, _ = assemble(
        description(),
        provider(
            observations=[
                TObservation(
                    dimension=MetadataDimension.HIRING_LOCATION_HINT.value,
                    status=ExtractionStatus.EXPLICIT,
                    value="United States",
                    evidence_id="ev_01",
                )
            ],
            evidence=[
                TEvidence(
                    id="ev_01",
                    source_kind=EvidenceSourceKind.PROVIDER_FIELD,
                    provider="greenhouse",
                    source_field="location.name",
                    source_value="Remote - United States",
                )
            ],
        ),
        META,
        source_provider="greenhouse",
    )

    ids = {item.id for item in fingerprint.evidence}
    assert ids == {"ev_d01", "ev_p01"}
    assert fingerprint.provider_observations[0].evidence_id == "ev_p01"


def test_namespacing_is_readable_after_the_fact() -> None:
    assert namespace_id("d", "ev_07") == "ev_d07"
    assert namespace_id("p", "ev_01") == "ev_p01"


# --- the three guarantees the compact transport put at risk -----------------


def test_an_unknown_dimension_is_rejected() -> None:
    """A row-shaped payload can name a dimension nobody defined.

    Fewer JSON properties must not mean weaker domain typing.
    """
    bad = rows() + [TObservation(dimension="made_up_dimension", status=ExtractionStatus.NOT_STATED)]

    with pytest.raises(AssemblyError, match="unknown dimension"):
        assemble(description(observations=bad), provider(), META, source_provider="greenhouse")


def test_a_duplicated_singular_dimension_is_rejected() -> None:
    """Two answers where the domain allows one.

    First-wins, last-wins and highest-confidence are all arbitrary, and an
    arbitrary choice made silently is the kind of thing nobody can debug later.
    """
    duplicated = rows() + [
        TObservation(
            dimension="hiring_scope",
            status=ExtractionStatus.EXPLICIT,
            value="WORLDWIDE",
            evidence_id="ev_01",
        )
    ]

    with pytest.raises(AssemblyError, match="twice"):
        assemble(
            description(observations=duplicated), provider(), META, source_provider="greenhouse"
        )


def test_a_missing_dimension_is_rejected() -> None:
    """'Nothing was stated' and 'the model forgot' must stay distinguishable.

    This is the completeness guarantee that a property-per-dimension schema
    provided for free and the compact form has to enforce deliberately.
    """
    incomplete = [row for row in rows() if row.dimension != "hiring_scope"]

    with pytest.raises(AssemblyError, match="omitted"):
        assemble(
            description(observations=incomplete), provider(), META, source_provider="greenhouse"
        )


# --- the channel boundary ---------------------------------------------------


def test_the_description_family_may_not_cite_provider_evidence() -> None:
    """The description call was never given the payload.

    So a PROVIDER_FIELD citation from it is not a formatting mistake -- it is a
    claim about a source that call never saw, which is exactly the leakage the
    two-call topology exists to make impossible.
    """
    leaking = description(
        evidence=[
            TEvidence(
                id="ev_01",
                source_kind=EvidenceSourceKind.PROVIDER_FIELD,
                provider="greenhouse",
                source_field="location.name",
                source_value="Remote - United States",
            )
        ]
    )

    with pytest.raises(AssemblyError, match="may only cite"):
        assemble(leaking, provider(), META, source_provider="greenhouse")


def test_a_provider_observation_without_evidence_is_rejected() -> None:
    """Provider metadata is more verifiable than prose, not less.

    An observation that cannot name its payload path cannot be checked at all,
    so admitting it would create the one class of provider claim we cannot
    prove or disprove.
    """
    with pytest.raises(AssemblyError, match="carries no evidence"):
        assemble(
            description(),
            provider(
                observations=[
                    TObservation(
                        dimension=MetadataDimension.WORK_MODEL_HINT.value,
                        status=ExtractionStatus.EXPLICIT,
                        value="Remote",
                    )
                ]
            ),
            META,
            source_provider="greenhouse",
        )


# --- typed values -----------------------------------------------------------


def test_hiring_scope_parses_back_into_a_typed_value() -> None:
    """WORLDWIDE with exclusions is the shape of a genuinely global posting.

    It must survive the string round-trip intact, because it is the single most
    valuable result the corpus can produce.
    """
    scoped = [
        TObservation(
            dimension="hiring_scope",
            status=ExtractionStatus.EXPLICIT,
            value="WORLDWIDE:!CU,IR,KP",
            evidence_id="ev_01",
        )
        if row.dimension == "hiring_scope"
        else row
        for row in rows()
    ]

    fingerprint, _ = assemble(
        description(observations=scoped), provider(), META, source_provider="greenhouse"
    )
    scope = fingerprint.eligibility.hiring_scope.value

    assert scope.kind is HiringScopeKind.WORLDWIDE
    assert scope.exclusions == ["CU", "IR", "KP"]


def test_an_unparseable_value_fails_rather_than_coercing() -> None:
    """A salary that quietly becomes None is worse than one that fails loudly:
    downstream arithmetic would then be silently wrong instead of absent."""
    broken = [
        TObservation(
            dimension="compensation.min",
            status=ExtractionStatus.EXPLICIT,
            value="competitive",
            evidence_id="ev_01",
        )
        if row.dimension == "compensation.min"
        else row
        for row in rows()
    ]

    with pytest.raises(AssemblyError, match="not a number"):
        assemble(description(observations=broken), provider(), META, source_provider="greenhouse")


def test_an_unearned_not_applicable_is_demoted_not_raised() -> None:
    """The safe direction is always towards "we do not know".

    Failing a whole extraction over one over-confident field would cost a retry
    to fix something the precondition table already fixes correctly.
    """
    presumptuous = [
        TObservation(
            dimension="travel_expenses_covered",
            status=ExtractionStatus.NOT_APPLICABLE,
            not_applicable_because="travel_frequency",
        )
        if row.dimension == "travel_expenses_covered"
        else row
        for row in rows()
    ]

    fingerprint, report = assemble(
        description(observations=presumptuous), provider(), META, source_provider="greenhouse"
    )

    assert fingerprint.eligibility.travel_expenses_covered.status is ExtractionStatus.NOT_STATED
    assert any("travel_expenses_covered" in line for line in report.demotions)


def test_an_unmapped_responsibility_keeps_its_raw_phrase() -> None:
    """What accumulates in the escape hatch is how the vocabulary grows from
    evidence. A dropped phrase makes it a bin instead of a queue."""
    fingerprint, report = assemble(
        description(
            responsibilities=[
                TResponsibility(
                    category=ResponsibilityCategory.RESPONSIBILITY_OTHER,
                    prominence=Prominence.SECONDARY,
                    confidence=0.5,
                    evidence_id="ev_01",
                    raw_phrase="run the internal AI enablement guild",
                )
            ]
        ),
        provider(),
        META,
        source_provider="greenhouse",
    )

    assert fingerprint.responsibilities[0].raw_phrase == "run the internal AI enablement guild"
    assert report.unmapped_responsibilities == ["run the internal AI enablement guild"]


def test_software_centrality_and_alternative_groups_survive() -> None:
    fingerprint, _ = assemble(
        description(
            software=[
                TSoftware(
                    raw_mention="HubSpot",
                    canonical_suggestion="hubspot",
                    centrality=SoftwareCentrality.ALTERNATIVE,
                    alternative_group="grp_crm",
                    confidence=0.9,
                    evidence_id="ev_01",
                )
            ]
        ),
        provider(),
        META,
        source_provider="greenhouse",
    )

    assert fingerprint.software[0].centrality is SoftwareCentrality.ALTERNATIVE
    assert fingerprint.software[0].alternative_group == "grp_crm"

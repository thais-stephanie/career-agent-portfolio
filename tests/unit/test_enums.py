"""The vocabularies are contracts, so they are tested like contracts.

These tests are cheap and they pin down three things that would otherwise drift
silently: that the approved vocabulary really has the size that was approved,
that the statuses match the architecture, and that StrEnum behaves the way the
rest of the system assumes it does.
"""

import pytest

from career_agent.domain.enums import (
    RESPONSIBILITY_VOCABULARY_VERSION,
    EligibilityStatus,
    EvidenceSourceKind,
    ExtractionStatus,
    GateResult,
    HiringScopeKind,
    PreferenceBucket,
    Region,
    ResponsibilityCategory,
    ScreeningState,
)

# --- The approved vocabulary ---------------------------------------------


def test_responsibility_vocabulary_has_39_categories_plus_escape_hatch() -> None:
    """v1 was approved at exactly 39 categories. Adding or removing one should
    fail here and force a deliberate version bump rather than silent drift."""
    assert len(ResponsibilityCategory) == 40
    assert ResponsibilityCategory.RESPONSIBILITY_OTHER in ResponsibilityCategory


def test_responsibility_values_are_lowercase_snake_case() -> None:
    """These values are typed by hand into profile.local.yaml. If one ever
    arrives as 'CRM_Administration', the profile silently stops matching it."""
    for member in ResponsibilityCategory:
        assert member.value == member.value.lower()
        assert " " not in member.value
        assert "-" not in member.value


def test_leadership_is_split_so_avoid_does_not_catch_ic_roles() -> None:
    """people_management = AVOID must not penalise mentoring or delivery
    ownership. That only works because they are separate categories."""
    assert ResponsibilityCategory.PEOPLE_MANAGEMENT
    assert ResponsibilityCategory.TECHNICAL_MENTORING
    assert ResponsibilityCategory.PROJECT_LEADERSHIP
    assert ResponsibilityCategory.CROSS_FUNCTIONAL_COORDINATION


def test_engineering_intensity_is_split() -> None:
    """full_software_engineering = AVOID while light_backend_development =
    INTERESTED. A single 'software_development' category would have forced a
    choice between rejecting useful roles and accepting unwanted ones."""
    assert ResponsibilityCategory.FULL_SOFTWARE_ENGINEERING
    assert ResponsibilityCategory.LIGHT_BACKEND_DEVELOPMENT


def test_vocabulary_version_is_pinned() -> None:
    """Every fingerprint records this, so a change is a migration with a known
    blast radius rather than silent drift across the corpus."""
    assert RESPONSIBILITY_VOCABULARY_VERSION == 1


# --- The architectural commitments ---------------------------------------


def test_extraction_status_has_exactly_the_four_approved_values() -> None:
    assert {s.value for s in ExtractionStatus} == {
        "EXPLICIT",
        "INFERRED",
        "NOT_STATED",
        "NOT_APPLICABLE",
    }


def test_extraction_status_has_no_unresolved_member() -> None:
    """UNRESOLVED is a system conclusion, not something one source can be.
    Keeping it off ExtractionStatus is what stops the model choosing between
    two near-synonyms."""
    assert not hasattr(ExtractionStatus, "UNRESOLVED")
    # It lives on the two enums that express system conclusions instead:
    assert GateResult.UNRESOLVED.value == "UNRESOLVED"
    assert EligibilityStatus.UNRESOLVED.value == "UNRESOLVED"


def test_extraction_status_has_no_absent_member() -> None:
    """ABSENT was removed in revision 2. It conflated 'the posting is silent'
    with 'this does not apply', which is how 'no mention of travel' becomes
    'no travel'."""
    assert not hasattr(ExtractionStatus, "ABSENT")


def test_screening_and_eligibility_are_separate_types() -> None:
    """A job may survive filtering without the system claiming eligibility."""
    assert set(ScreeningState) == {ScreeningState.NOT_BLOCKED, ScreeningState.BLOCKED}
    assert len(EligibilityStatus) == 4
    assert not hasattr(ScreeningState, "ELIGIBLE")


def test_gates_are_three_valued() -> None:
    assert len(GateResult) == 3
    assert GateResult.UNRESOLVED in GateResult


def test_hiring_scope_distinguishes_unstated_from_worldwide() -> None:
    """'Remote' with no further qualification is UNSTATED. Treating it as
    WORLDWIDE is the most expensive error this system could make."""
    assert HiringScopeKind.UNSTATED != HiringScopeKind.WORLDWIDE


def test_unknown_region_is_representable() -> None:
    """An unrecognised region phrase must have somewhere to go, so the gate can
    return UNRESOLVED instead of guessing."""
    assert Region.REGION_UNKNOWN in Region


def test_company_history_is_a_declared_source_kind() -> None:
    """Declared now so the 'context, never current-posting evidence' rule is
    enforceable by a check rather than by discipline."""
    assert EvidenceSourceKind.COMPANY_HISTORY in EvidenceSourceKind


# --- StrEnum behaviour the rest of the system relies on -------------------


def test_members_are_real_strings() -> None:
    """StrEnum members ARE strings. This is why they can be written straight to
    SQLite, serialised to JSON, and compared against raw model output without
    conversion code anywhere."""
    assert ExtractionStatus.EXPLICIT == "EXPLICIT"
    assert isinstance(ExtractionStatus.EXPLICIT, str)
    assert PreferenceBucket.WANT.upper() == "WANT"


def test_invalid_values_are_rejected() -> None:
    """The whole point of a closed vocabulary."""
    with pytest.raises(ValueError):
        ExtractionStatus("MAYBE")
    with pytest.raises(ValueError):
        ResponsibilityCategory("crm_admin")

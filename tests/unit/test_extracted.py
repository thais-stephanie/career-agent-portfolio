"""Tests for the shape invariants and the NOT_APPLICABLE precondition table.

These are the tests that make "absence is never permission" a property of the
code rather than a paragraph in a document.
"""

import pytest
from pydantic import ValidationError

from career_agent.domain.enums import ExtractionStatus, TravelFrequency, WorkModel
from career_agent.domain.extracted import (
    NOT_APPLICABLE_RULES,
    ExtractedField,
    enforce_not_applicable_preconditions,
)

# --- Shape invariants, one test per status --------------------------------


def test_explicit_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="requires evidence_id"):
        ExtractedField[bool](value=True, status=ExtractionStatus.EXPLICIT, confidence=0.9)


def test_explicit_requires_a_value() -> None:
    with pytest.raises(ValidationError, match="requires a value"):
        ExtractedField[bool](status=ExtractionStatus.EXPLICIT, evidence_id="ev_01")


def test_explicit_with_evidence_is_accepted() -> None:
    field = ExtractedField[bool](
        value=True, status=ExtractionStatus.EXPLICIT, confidence=0.95, evidence_id="ev_01"
    )
    assert field.is_positive_evidence
    assert field.is_known


def test_inferred_requires_a_value_but_not_evidence() -> None:
    field = ExtractedField[str](
        value="MID", status=ExtractionStatus.INFERRED, confidence=0.6, reasoning="3+ years"
    )
    assert field.is_known
    with pytest.raises(ValidationError, match="requires a value"):
        ExtractedField[str](status=ExtractionStatus.INFERRED)


def test_inferred_may_never_open_a_gate() -> None:
    """The rule that stops a plausible guess becoming permission to apply."""
    field = ExtractedField[bool](value=True, status=ExtractionStatus.INFERRED, confidence=0.99)
    assert field.is_positive_evidence is False


def test_not_stated_must_be_empty() -> None:
    field = ExtractedField[bool].not_stated()
    assert field.value is None
    assert field.is_known is False
    assert field.is_positive_evidence is False

    with pytest.raises(ValidationError, match="silence is not information"):
        ExtractedField[bool](value=False, status=ExtractionStatus.NOT_STATED)
    with pytest.raises(ValidationError, match="cannot cite evidence"):
        ExtractedField[bool](status=ExtractionStatus.NOT_STATED, evidence_id="ev_01")


def test_not_applicable_requires_a_named_precondition() -> None:
    with pytest.raises(ValidationError, match="requires not_applicable_because"):
        ExtractedField[bool](status=ExtractionStatus.NOT_APPLICABLE)

    field = ExtractedField[bool](
        status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="relocation_required"
    )
    assert field.value is None
    assert field.is_positive_evidence is False


def test_not_applicable_because_is_rejected_on_other_statuses() -> None:
    with pytest.raises(ValidationError, match="only valid with status=NOT_APPLICABLE"):
        ExtractedField[bool](
            value=True,
            status=ExtractionStatus.EXPLICIT,
            evidence_id="ev_01",
            not_applicable_because="relocation_required",
        )


def test_evidence_id_must_look_like_an_evidence_id() -> None:
    """Catches the model returning a quote where an id belongs."""
    with pytest.raises(ValidationError):
        ExtractedField[bool](
            value=True,
            status=ExtractionStatus.EXPLICIT,
            evidence_id="This role is open worldwide.",
        )


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        ExtractedField[bool](
            value=True,
            status=ExtractionStatus.EXPLICIT,
            evidence_id="ev_01",
            certainty=0.9,  # type: ignore[call-arg]
        )


# --- The precondition table ------------------------------------------------


def test_earned_not_applicable_survives() -> None:
    """Posting explicitly says no relocation, so relocation_support genuinely
    does not apply."""
    fields = {
        "relocation_required": ExtractedField[bool](
            value=False, status=ExtractionStatus.EXPLICIT, confidence=0.9, evidence_id="ev_16"
        ),
        "relocation_support": ExtractedField[str](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="relocation_required"
        ),
    }
    corrected, demotions = enforce_not_applicable_preconditions(fields)
    assert demotions == []
    assert corrected["relocation_support"].status is ExtractionStatus.NOT_APPLICABLE


def test_silence_does_not_earn_not_applicable() -> None:
    """The posting never mentions relocation. relocation_support must fall back
    to NOT_STATED, not stay NOT_APPLICABLE."""
    fields = {
        "relocation_required": ExtractedField[bool].not_stated(),
        "relocation_support": ExtractedField[str](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="relocation_required"
        ),
    }
    corrected, demotions = enforce_not_applicable_preconditions(fields)
    assert [d.dimension for d in demotions] == ["relocation_support"]
    assert corrected["relocation_support"].status is ExtractionStatus.NOT_STATED


def test_no_mention_of_travel_never_means_no_travel() -> None:
    """The exact example from the architecture review."""
    fields = {
        "travel_frequency": ExtractedField[TravelFrequency].not_stated(),
        "travel_expenses_covered": ExtractedField[bool](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="travel_frequency"
        ),
        "business_visa_support": ExtractedField[bool](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="travel_frequency"
        ),
    }
    corrected, demotions = enforce_not_applicable_preconditions(fields)
    assert len(demotions) == 2
    assert corrected["travel_expenses_covered"].status is ExtractionStatus.NOT_STATED
    assert corrected["business_visa_support"].status is ExtractionStatus.NOT_STATED


def test_explicit_no_travel_does_earn_not_applicable() -> None:
    fields = {
        "travel_frequency": ExtractedField[TravelFrequency](
            value=TravelFrequency.NONE,
            status=ExtractionStatus.EXPLICIT,
            confidence=0.9,
            evidence_id="ev_11",
        ),
        "travel_expenses_covered": ExtractedField[bool](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="travel_frequency"
        ),
    }
    _, demotions = enforce_not_applicable_preconditions(fields)
    assert demotions == []


def test_inferred_precondition_is_not_enough() -> None:
    """Only EXPLICIT earns it. An inference cannot license a claim of
    inapplicability any more than it can open a gate."""
    fields = {
        "work_model": ExtractedField[WorkModel](
            value=WorkModel.REMOTE, status=ExtractionStatus.INFERRED, confidence=0.7
        ),
        "worksite_requirement": ExtractedField[list[str]](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="work_model"
        ),
    }
    corrected, demotions = enforce_not_applicable_preconditions(fields)
    assert len(demotions) == 1
    assert corrected["worksite_requirement"].status is ExtractionStatus.NOT_STATED


def test_dimension_outside_the_whitelist_is_always_demoted() -> None:
    """Default deny: visa_sponsorship may never be NOT_APPLICABLE."""
    fields = {
        "work_model": ExtractedField[WorkModel](
            value=WorkModel.REMOTE,
            status=ExtractionStatus.EXPLICIT,
            confidence=0.95,
            evidence_id="ev_01",
        ),
        "visa_sponsorship": ExtractedField[bool](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="work_model"
        ),
    }
    corrected, demotions = enforce_not_applicable_preconditions(fields)
    assert [d.dimension for d in demotions] == ["visa_sponsorship"]
    assert "silence is not" in demotions[0].reason
    assert corrected["visa_sponsorship"].status is ExtractionStatus.NOT_STATED


def test_citing_the_wrong_precondition_is_demoted() -> None:
    fields = {
        "relocation_required": ExtractedField[bool](
            value=False, status=ExtractionStatus.EXPLICIT, confidence=0.9, evidence_id="ev_16"
        ),
        "relocation_support": ExtractedField[str](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="work_model"
        ),
    }
    _, demotions = enforce_not_applicable_preconditions(fields)
    assert len(demotions) == 1
    assert "only permitted precondition" in demotions[0].reason


def test_enforcement_never_mutates_the_input() -> None:
    fields = {
        "travel_frequency": ExtractedField[TravelFrequency].not_stated(),
        "travel_expenses_covered": ExtractedField[bool](
            status=ExtractionStatus.NOT_APPLICABLE, not_applicable_because="travel_frequency"
        ),
    }
    corrected, _ = enforce_not_applicable_preconditions(fields)
    assert fields["travel_expenses_covered"].status is ExtractionStatus.NOT_APPLICABLE
    assert corrected["travel_expenses_covered"].status is ExtractionStatus.NOT_STATED


def test_every_rule_names_a_real_precondition_and_rationale() -> None:
    for dimension, rule in NOT_APPLICABLE_RULES.items():
        assert rule.depends_on, f"{dimension} has no precondition"
        assert rule.rationale, f"{dimension} has no stated rationale"
        assert rule.depends_on != dimension, f"{dimension} cannot depend on itself"

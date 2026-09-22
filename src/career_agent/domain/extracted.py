"""The universal wrapper for anything an LLM observes about a job posting.

Nothing extracted from a source is ever stored as a bare value. Every
observation is an :class:`ExtractedField`, which carries the value *and* how we
came to know it. That is what makes the central promise of this system
mechanically enforceable rather than aspirational:

    Absence is never permission.

Two layers of enforcement live here:

1. **Shape invariants** (:class:`ExtractedField` validators) -- what a single
   field is allowed to look like given its status. A field claiming EXPLICIT
   without evidence is rejected outright.
2. **The NOT_APPLICABLE precondition table** -- a field may only claim that a
   dimension does not apply when *another* dimension it names says so
   explicitly. A single field cannot check its siblings, so this is a
   document-level pass (:func:`enforce_not_applicable_preconditions`) rather
   than a validator.

The second layer is a *correction*, not a rejection: an unearned
NOT_APPLICABLE is rewritten to NOT_STATED and counted. The safe direction is
always toward "we do not know".
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from career_agent.domain.enums import ExtractionStatus, TravelFrequency, WorkModel

T = TypeVar("T")

#: Local evidence identifiers inside one extraction document, e.g. "ev_01".
#: Database evidence rows use ULIDs; these ids only have to be unique within
#: the document the model produced.
EVIDENCE_ID_PATTERN = r"^ev_[A-Za-z0-9_-]+$"


class ExtractedField(BaseModel, Generic[T]):
    """One observation, by one source, about one dimension.

    ============== ========= ================== ======================== ==============
    status         value     evidence_id        not_applicable_because   opens a gate?
    ============== ========= ================== ======================== ==============
    EXPLICIT       required  required           forbidden                yes
    INFERRED       required  optional           forbidden                no
    NOT_STATED     null      forbidden          forbidden                no
    NOT_APPLICABLE null      forbidden          required                 no
    ============== ========= ================== ======================== ==============
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: T | None = None
    status: ExtractionStatus
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    evidence_id: str | None = Field(default=None, pattern=EVIDENCE_ID_PATTERN)
    not_applicable_because: str | None = None
    reasoning: str = Field(default="", max_length=280)

    @model_validator(mode="after")
    def _check_status_invariants(self) -> "ExtractedField[T]":
        status = self.status

        if status is ExtractionStatus.EXPLICIT:
            if self.evidence_id is None:
                raise ValueError(
                    "status=EXPLICIT requires evidence_id: 'explicit' means the source "
                    "says so, and we must be able to point at where."
                )
            if self.value is None:
                raise ValueError("status=EXPLICIT requires a value")

        elif status is ExtractionStatus.INFERRED:
            if self.value is None:
                raise ValueError("status=INFERRED requires a value")

        elif status is ExtractionStatus.NOT_STATED:
            if self.value is not None:
                raise ValueError(
                    "status=NOT_STATED must have value=None: silence is not information"
                )
            if self.evidence_id is not None:
                raise ValueError("status=NOT_STATED cannot cite evidence")

        elif status is ExtractionStatus.NOT_APPLICABLE:
            if self.value is not None:
                raise ValueError("status=NOT_APPLICABLE must have value=None")
            if self.evidence_id is not None:
                raise ValueError("status=NOT_APPLICABLE cannot cite evidence")
            if not self.not_applicable_because:
                raise ValueError(
                    "status=NOT_APPLICABLE requires not_applicable_because naming the "
                    "dimension that proves it; see NOT_APPLICABLE_RULES"
                )

        if status is not ExtractionStatus.NOT_APPLICABLE and self.not_applicable_because:
            raise ValueError("not_applicable_because is only valid with status=NOT_APPLICABLE")

        return self

    # -- convenience constructors -----------------------------------------

    @classmethod
    def not_stated(cls, reasoning: str = "") -> "ExtractedField[T]":
        """The honest default whenever a source says nothing."""
        return cls(status=ExtractionStatus.NOT_STATED, reasoning=reasoning)

    # -- queries used by gates and resolution ------------------------------

    @property
    def is_positive_evidence(self) -> bool:
        """May this field, on its own, satisfy a hard gate positively?

        Only EXPLICIT qualifies. INFERRED is a ranking signal, never a gate
        opener; silence and inapplicability obviously are not either. Whether
        the cited evidence actually *verifies* is a separate question answered
        in domain/verify.py at M2.
        """
        return self.status is ExtractionStatus.EXPLICIT

    @property
    def is_known(self) -> bool:
        """Did any source tell us something, explicitly or by inference?"""
        return self.status in (ExtractionStatus.EXPLICIT, ExtractionStatus.INFERRED)


# =========================================================================
# THE NOT_APPLICABLE PRECONDITION TABLE
#
# Default deny. A dimension may only be NOT_APPLICABLE when it appears below
# AND the dimension it depends on is EXPLICIT with the required value.
#
# This is what stops "the posting never mentions travel" from becoming
# "this role has no travel". Silence is NOT_STATED; inapplicability has to be
# earned from something the posting actually said.
#
# Keys are fingerprint dimension names. They are plain strings at M0 because
# the full fingerprint field list is deliberately deferred to M2; they will be
# type-checked against that model when it exists.
# =========================================================================


@dataclass(frozen=True)
class NotApplicablePrecondition:
    """'X may be NOT_APPLICABLE only if Y is EXPLICIT and equals Z.'"""

    depends_on: str
    required_value: Any
    rationale: str


NOT_APPLICABLE_RULES: Mapping[str, NotApplicablePrecondition] = {
    "relocation_support": NotApplicablePrecondition(
        depends_on="relocation_required",
        required_value=False,
        rationale="no relocation, so there is nothing to support",
    ),
    "travel_expenses_covered": NotApplicablePrecondition(
        depends_on="travel_frequency",
        required_value=TravelFrequency.NONE,
        rationale="no trips to fund",
    ),
    "business_visa_support": NotApplicablePrecondition(
        depends_on="travel_frequency",
        required_value=TravelFrequency.NONE,
        rationale="no trips to document",
    ),
    "worksite_requirement": NotApplicablePrecondition(
        depends_on="work_model",
        required_value=WorkModel.REMOTE,
        rationale="explicitly remote, so no office is in scope",
    ),
}

#: Dimensions that may NEVER be NOT_APPLICABLE, however plausible it looks.
#: Listed explicitly so the reason is visible at the point of enforcement.
NEVER_APPLICABLE_EXEMPT: Mapping[str, str] = {
    "hiring_scope_exclusions": "worldwide roles routinely carry exclusions",
    "visa_sponsorship": "silence is not 'no sponsorship'",
    "work_authorization_required": "silence is not 'no requirement'",
    "contractor_eligible": "silence is not 'no'",
    "relocation_allowed": "silence is not 'relocation is not an option'",
    "travel_required": "silence is not 'this role involves no travel'",
    "eor_available": "silence is not 'no'",
    "languages": "silence is not 'no language requirement'",
}


@dataclass(frozen=True)
class PreconditionDemotion:
    """Record of one NOT_APPLICABLE that was not earned and got rewritten."""

    dimension: str
    reason: str


def enforce_not_applicable_preconditions(
    fields: Mapping[str, ExtractedField[Any]],
) -> tuple[dict[str, ExtractedField[Any]], list[PreconditionDemotion]]:
    """Rewrite every unearned NOT_APPLICABLE to NOT_STATED.

    Returns the corrected mapping and a list of what was demoted. Callers record
    the demotion count as a quality metric: a posting with several demotions
    usually means the model is using NOT_APPLICABLE as a synonym for silence.

    This never raises. Correcting toward "we do not know" is always safe;
    rejecting a whole extraction over one over-confident field would not be.
    """
    corrected: dict[str, ExtractedField[Any]] = dict(fields)
    demotions: list[PreconditionDemotion] = []

    for dimension, field in fields.items():
        if field.status is not ExtractionStatus.NOT_APPLICABLE:
            continue

        rule = NOT_APPLICABLE_RULES.get(dimension)
        if rule is None:
            why = NEVER_APPLICABLE_EXEMPT.get(
                dimension, "dimension is not in the NOT_APPLICABLE whitelist"
            )
            demotions.append(PreconditionDemotion(dimension, why))
            corrected[dimension] = ExtractedField.not_stated(
                reasoning=f"NOT_APPLICABLE not permitted here: {why}"
            )
            continue

        if field.not_applicable_because != rule.depends_on:
            why = (
                f"cited '{field.not_applicable_because}' but the only permitted "
                f"precondition is '{rule.depends_on}'"
            )
            demotions.append(PreconditionDemotion(dimension, why))
            corrected[dimension] = ExtractedField.not_stated(reasoning=why)
            continue

        source = fields.get(rule.depends_on)
        if (
            source is None
            or source.status is not ExtractionStatus.EXPLICIT
            or source.value != rule.required_value
        ):
            why = (
                f"requires {rule.depends_on} to be EXPLICIT and equal to "
                f"{rule.required_value!r} ({rule.rationale})"
            )
            demotions.append(PreconditionDemotion(dimension, why))
            corrected[dimension] = ExtractedField.not_stated(reasoning=why)

    return corrected, demotions

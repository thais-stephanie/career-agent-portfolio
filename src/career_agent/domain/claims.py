"""Verified career claims - the evidence base for future resume generation.

This exists at M0 even though Resume Intelligence is deferred, because the
truthfulness guarantee is only enforceable if there is a **closed set of true
statements** to draw from. The eventual rule is mechanical:

    Every sentence in a generated resume must be traceable to a claim_key.
    A sentence with no claim_key is a bug, not a style choice.

Claims deliberately do **not** belong to a search-profile version. Raising a
salary floor is a preference change; it must not duplicate every career fact
the candidate owns. Editing a claim instead creates a new revision and
supersedes the previous one, so history is preserved without duplication.
"""

import re

from pydantic import BaseModel, ConfigDict, Field, model_validator

from career_agent.domain.enums import ClaimSource, ClaimType, ResponsibilityCategory

#: Claim periods are month-precision: "2024-06". Day precision would imply a
#: confidence about start and end dates that resumes do not actually carry.
PERIOD_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class VerifiedClaim(BaseModel):
    """One factual statement about the candidate's career.

    ``verified`` means the candidate has personally confirmed it is true. Only
    verified claims may ever reach a generated document; unverified ones are
    drafts.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_key: str = Field(min_length=1, max_length=64)
    revision: int = Field(default=1, ge=1)
    claim_type: ClaimType
    text: str = Field(min_length=1, max_length=2000)
    employer: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    source: ClaimSource
    verified: bool = False
    evidence_ref: str | None = None
    tools: list[str] = Field(default_factory=list)
    tags: list[ResponsibilityCategory] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_period(self) -> "VerifiedClaim":
        for label, value in (("period_start", self.period_start), ("period_end", self.period_end)):
            if value is not None and not PERIOD_PATTERN.match(value):
                raise ValueError(f"{label} must be YYYY-MM, got {value!r}")

        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError(
                f"period_end ({self.period_end}) precedes period_start ({self.period_start})"
            )

        if self.period_end and not self.period_start:
            raise ValueError("period_end without period_start")
        return self

    @property
    def is_usable_for_generation(self) -> bool:
        """Whether a future resume generator may draw on this claim at all."""
        return self.verified

    def next_revision(self, **changes: object) -> "VerifiedClaim":
        """A corrected copy of this claim, one revision later.

        The claim_key is deliberately preserved: it is the stable identity of
        the fact, while the revision tracks how our statement of it has changed.
        """
        if "claim_key" in changes or "revision" in changes:
            raise ValueError("claim_key and revision are managed, not passed in")
        return self.model_copy(update={**changes, "revision": self.revision + 1})

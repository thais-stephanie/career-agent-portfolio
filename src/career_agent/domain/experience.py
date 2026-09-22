"""Candidate-authored organization, independent of original evidence wording."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from career_agent.domain.claims import PERIOD_PATTERN

CATEGORIES = (
    "ACHIEVEMENT",
    "RESPONSIBILITY",
    "PROJECT",
    "TOOL",
    "SKILL",
    "CERTIFICATION",
    "EDUCATION",
    "OTHER",
)
KINDS = ("EMPLOYMENT", "VOLUNTEER", "FREELANCE", "ACADEMIC", "PERSONAL")
CATEGORY_TYPES = {
    "ACHIEVEMENT": "ACHIEVEMENT",
    "RESPONSIBILITY": "EMPLOYMENT",
    "PROJECT": "PROJECT",
    "TOOL": "TOOL",
    "SKILL": "SKILL",
    "CERTIFICATION": "CERTIFICATION",
    "EDUCATION": "EDUCATION",
}


class ExperienceMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    period_start: str | None = None
    period_end: str | None = None
    current_role: bool = False
    kind: Literal["EMPLOYMENT", "VOLUNTEER", "FREELANCE", "ACADEMIC", "PERSONAL"] = "EMPLOYMENT"
    display_order: int = Field(default=0, ge=-100000, le=100000)

    @model_validator(mode="after")
    def valid_period(self) -> "ExperienceMetadata":
        for name in ("company", "title", "period_start", "period_end"):
            if getattr(self, name) == "":
                setattr(self, name, None)
        for value in (self.period_start, self.period_end):
            if value and not PERIOD_PATTERN.fullmatch(value):
                raise ValueError("Dates must use YYYY-MM.")
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("The end date precedes the start date.")
        if self.current_role and self.period_end:
            raise ValueError("A current experience cannot also have an end date.")
        if not self.company and not self.title:
            raise ValueError("Name a company, organization, role or project.")
        return self

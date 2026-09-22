"""What a Candidate Intake Package may contain, and every refusal.

THE CONTRACT IN ONE SENTENCE
----------------------------
A package is a set of PROPOSED claims about one person's career, each one
attributed to a document the package declares it was built from, carrying the
candidate's own words rather than a reading of them.

WHY THE REFUSALS ARE THE INTERESTING PART
------------------------------------------
This file's job is not to accept packages. A generous parser would accept a
file produced by any assistant, and every field it filled in charitably would
become a fact about somebody's career that nobody checked.

So the model is `extra="forbid"` from top to bottom, and the validators below
refuse six specific shapes that a well-meaning generator produces naturally:

1. **A claimed verification.** A package may not carry `verified`, `confirmed`
   or any synonym. Confirmation is an act by a person, and a file asserting it
   would be asserting it on their behalf.
2. **A normalised date with no original.** "2024-06" is a reading of "Jun 2024".
   Keeping only the reading throws away the evidence for it, and a wrong
   normalisation then looks exactly like a stated fact.
3. **A metric lifted out of its sentence.** "40%" alone is a figure this
   program cannot attribute; "increased revenue 40% in two quarters" is hers.
   `MetricMention` therefore has no numeric field at all -- there is nowhere to
   put a bare number, which is stronger than a rule against putting one there.
4. **A claim attributed to a source the package did not declare.** An
   unattributable claim makes every conflict unresolvable.
5. **A preference, an eligibility answer or a compensation expectation.** Those
   are Candidate Profile facts and are answered by the candidate directly. A
   document may say where somebody has WORKED; it cannot say where they are
   willing to work, and an assistant reading a CV will cheerfully conflate the
   two.
6. **An evidence quote longer than an excerpt.** A package is proposed
   evidence, not a copy of the documents. `MAX_QUOTE` bounds it.

WHAT IS DELIBERATELY NOT HERE
------------------------------
**No precedence between sources.** A CV and a LinkedIn export disagree about
when a role ended, and there is no field, ranking or rule here that settles it.
Both are kept, the disagreement is grouped, and the candidate decides. Deciding
it in code would be this program reconciling somebody's career for them, which
is the one thing §2.1 of the working brief forbids outright.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from career_agent.domain.enums import ClaimSource, ClaimType

#: The contract versions this build understands.
#:
#: A package declaring anything else is REFUSED rather than read leniently. A
#: field name that changed meaning between versions is invisible to a parser
#: that shrugs at the version number, and the resulting claims look perfectly
#: well-formed.
SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({"1.0"})

#: The version this build writes.
CURRENT_SCHEMA_VERSION = "1.0"

#: The longest evidence excerpt a package may carry, in characters.
#:
#: A package proposes evidence; it is not a second copy of the CV. The bound
#: also keeps a generator from pasting a whole document into one claim and
#: calling the result a quote.
MAX_QUOTE = 1200

#: Bounds on the package as a whole, so a malformed or hostile file cannot
#: become a memory problem before it becomes a validation error.
MAX_CLAIMS = 2000
MAX_SOURCES = 10

#: `YYYY-MM`, the only normalised date shape `VerifiedClaim` accepts.
NORMALISED_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: Keys whose presence means the generator tried to answer a Candidate Profile
#: question. Refused wherever they appear, at any depth.
#:
#: These are not fields we forgot to support. They are facts a document cannot
#: establish: a CV saying somebody worked remotely for a US company does not
#: say they are ELIGIBLE to, or that they WANT to, and an assistant asked to
#: extract a career will state both without noticing the difference.
FORBIDDEN_KEYS: frozenset[str] = frozenset(
    {
        "verified",
        "confirmed",
        "is_verified",
        "approved",
        "eligibility",
        "eligible",
        "eligible_countries",
        "eligible_scopes",
        "work_authorization",
        "work_authorisation",
        "visa_status",
        "citizenship",
        "salary_expectation",
        "expected_salary",
        "compensation_expectation",
        "desired_salary",
        "preferences",
        "desired_role",
        "willing_to_relocate",
        "relocation",
        "availability",
        "notice_period",
    }
)


class IntakeParseError(ValueError):
    """A package that cannot be read as one. Always names the field."""


class ReviewState:
    """Where one proposed claim has got to.

    Not a `StrEnum` in `domain/enums.py` because these are states of a REVIEW,
    which is a thing this package owns, rather than states of a claim, which
    the domain owns. A `VerifiedClaim` never carries one of these.

    UNREVIEWED         imported and not yet looked at. Every row starts here.
    CONFIRMED          the candidate accepted it as written.
    CORRECTED_BY_USER  the candidate rewrote it and accepted their own wording.
    CONFLICT           it disagrees with another claim in the same package.
                       Assigned by detection, cleared only by a person.
    UNRESOLVED         the candidate looked at it and could not answer yet.
                       Deliberately distinct from UNREVIEWED: "I do not know"
                       is an answer, and one that should stop the row being
                       presented as untouched.
    REJECTED           the candidate said no. Kept, never deleted, because it
                       is the only thing that stops the line reappearing.
    """

    UNREVIEWED = "UNREVIEWED"
    CONFIRMED = "CONFIRMED"
    CORRECTED_BY_USER = "CORRECTED_BY_USER"
    CONFLICT = "CONFLICT"
    UNRESOLVED = "UNRESOLVED"
    REJECTED = "REJECTED"

    #: The states from which a claim may still become a `VerifiedClaim`.
    ANSWERABLE = frozenset({UNREVIEWED, CONFLICT, UNRESOLVED})
    ALL = frozenset({UNREVIEWED, CONFIRMED, CORRECTED_BY_USER, CONFLICT, UNRESOLVED, REJECTED})


class PackageStatus:
    """Which reading of her documents is in force, and why the others are not.

    A sibling of `ReviewState` and deliberately not the same thing. That one is
    about ONE PROPOSAL and is a fact about what she decided. This one is about
    a WHOLE PACKAGE and is PRODUCT MACHINERY: which reading is on screen. No
    value here confirms anything, and moving between them touches no claim.

    ACTIVE       the reading in force. At most one, enforced by `select` inside
                 the transaction that sets it.
    SUPERSEDED   complete and valid, and not in force: a later package built
                 from her documents took over. THE MACHINE'S doing, and
                 `superseded_by` names which one, because "a newer one exists"
                 is not an answer to "why is this not in force".
    DISCARDED    she put it away. HERS. Kept rather than deleted, for migration
                 0023's reason: its REJECTED rows are the only thing stopping
                 those lines being proposed again by the next import.
    INCOMPLETE   the import produced no claims. It can never become ACTIVE and
                 it can never supersede anything, which is what stops a broken
                 re-import from retiring a review she has half finished.

    SUPERSEDED and DISCARDED are two values on purpose. A screen that says "you
    put this away" about something the product retired on her behalf is lying
    about who decided, and this codebase draws that line everywhere else it
    matters.
    """

    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    DISCARDED = "DISCARDED"
    INCOMPLETE = "INCOMPLETE"

    #: Packages a reader may still open and answer claims in. A DISCARDED one
    #: is readable -- history stays inspectable -- but it is not offered.
    LIVE = frozenset({ACTIVE, SUPERSEDED})
    #: Packages that may be put in force. INCOMPLETE may not: there is nothing
    #: in it to review. DISCARDED may not DIRECTLY; it is restored first, which
    #: is a separate act with its own button.
    SELECTABLE = frozenset({ACTIVE, SUPERSEDED})
    ALL = frozenset({ACTIVE, SUPERSEDED, DISCARDED, INCOMPLETE})


class SourceKind:
    """Which of the candidate's documents a claim came from.

    Mapped onto `ClaimSource` when a confirmed claim is created, so the
    provenance a reader sees on a claim is the one the package declared.
    """

    RESUME = "RESUME"
    LINKEDIN = "LINKEDIN"
    DOCUMENT = "DOCUMENT"

    ALL = frozenset({RESUME, LINKEDIN, DOCUMENT})

    @staticmethod
    def to_claim_source(kind: str) -> ClaimSource:
        return {
            SourceKind.RESUME: ClaimSource.RESUME,
            SourceKind.LINKEDIN: ClaimSource.LINKEDIN,
            SourceKind.DOCUMENT: ClaimSource.DOCUMENT,
        }[kind]


class DeclaredSource(BaseModel):
    """One document the package says it was built from."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: The handle claims use to point here. Short, and the package's own.
    ref: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: str = Field(min_length=1, max_length=20)
    #: What the candidate calls this document. A filename is fine; it is shown
    #: back to them so they can tell two documents apart.
    title: str = Field(min_length=1, max_length=200)

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, value: str) -> str:
        if value not in SourceKind.ALL:
            raise ValueError(
                f"unknown source kind {value!r}; expected one of {sorted(SourceKind.ALL)}"
            )
        return value


class DatePoint(BaseModel):
    """One end of a period, as WRITTEN and as READ.

    Both, always. `original` is what the document says and is the evidence;
    `normalized` is somebody's reading of it and is what the matcher can use.
    A package supplying only the reading has thrown away the thing that would
    let a candidate notice it was wrong.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    original: str = Field(min_length=1, max_length=60)
    normalized: str | None = None

    @field_validator("normalized")
    @classmethod
    def _shape(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not NORMALISED_PERIOD.match(value):
            raise ValueError(f"normalized date must be YYYY-MM, got {value!r}")
        return value


class Period(BaseModel):
    """When something happened, or as much of it as the document stated."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: DatePoint | None = None
    end: DatePoint | None = None
    #: A role somebody is still in. Kept as its own field rather than as an
    #: absent `end`, because "still there" and "the document did not say" are
    #: different facts and an absent end date cannot tell them apart.
    current: bool = False

    @model_validator(mode="after")
    def _ordered(self) -> Period:
        if self.current and self.end is not None:
            raise ValueError("a period cannot be current and also have an end date")
        a = self.start.normalized if self.start else None
        b = self.end.normalized if self.end else None
        if a and b and b < a:
            raise ValueError(f"period end ({b}) precedes start ({a})")
        return self


class MetricMention(BaseModel):
    """A figure the candidate stated, in the sentence she stated it in.

    **There is no numeric field, and that is the design.** "increased revenue
    40%" is her claim; "40%" on its own is a metric this program invented and
    cannot attribute to anything. A package has nowhere to put a bare number,
    which is a stronger guarantee than a rule saying not to.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    original: str = Field(min_length=1, max_length=400)


class Evidence(BaseModel):
    """Why the package believes this claim. A quote, a locator, or both.

    At least one is required. A claim with no evidence at all is an assertion
    from whichever model wrote the file, and the whole contract exists to stop
    those being indistinguishable from the candidate's own words.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: A bounded excerpt of the source document.
    quote: str | None = Field(default=None, max_length=MAX_QUOTE)
    #: Where in the document it sits: "page 2", "Experience, third role".
    locator: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _something(self) -> Evidence:
        if not (self.quote and self.quote.strip()) and not (self.locator and self.locator.strip()):
            raise ValueError(
                "evidence needs a quote or a locator; a claim with neither is unsupported"
            )
        return self


class ProposedClaim(BaseModel):
    """One statement about the candidate's career, proposed and not believed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    type: ClaimType
    text: str = Field(min_length=2, max_length=2000)
    source_ref: str = Field(min_length=1, max_length=40)
    employer: str | None = Field(default=None, max_length=200)
    # Optional organizing hint, never part of the established claim identity.
    # The original evidence/locator remains available for candidate review.
    role_title: str | None = Field(default=None, max_length=200)
    period: Period | None = None
    evidence: Evidence
    tools: list[str] = Field(default_factory=list, max_length=40)
    metrics: list[MetricMention] = Field(default_factory=list, max_length=20)

    @field_validator("tools")
    @classmethod
    def _tools(cls, value: list[str]) -> list[str]:
        cleaned = [t.strip() for t in value if isinstance(t, str) and t.strip()]
        for tool in cleaned:
            if len(tool) > 80:
                raise ValueError(f"tool name is longer than a tool name: {tool[:40]!r}...")
        return cleaned


class Generator(BaseModel):
    """What produced this package, as the file declares it.

    Never inferred, and never trusted for anything but display. It exists so a
    candidate reviewing forty claims can see that they came out of an assistant
    rather than out of this program, which is the single most useful thing to
    know while deciding how carefully to read them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: SELF for this program's own extractor; EXTERNAL_AI for an assistant the
    #: candidate chose to run; MANUAL for a file somebody wrote by hand.
    kind: str = Field(min_length=1, max_length=20)
    name: str = Field(default="", max_length=120)

    @field_validator("kind")
    @classmethod
    def _known(cls, value: str) -> str:
        if value not in {"SELF", "EXTERNAL_AI", "MANUAL"}:
            raise ValueError(f"unknown generator kind {value!r}")
        return value


def _pin_versions(schema: dict[str, Any]) -> None:
    """Put the accepted versions into the PUBLISHED schema, not just the model.

    Without it the schema says only "a string", so a v2 package validates
    cleanly against the v1 contract and its fields are then read as though they
    meant what v1 said.
    """
    schema["enum"] = sorted(SUPPORTED_SCHEMA_VERSIONS)


class IntakePackage(BaseModel):
    """A whole package, validated. Still nothing but proposals."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    #: `json_schema_extra` pins the accepted versions in the PUBLISHED schema
    #: as well as in the validator below. Without it the schema says only
    #: "a string", so a v2 package validates cleanly against the v1 contract
    #: and its fields are then read as though they meant what v1 said.
    schema_version: str = Field(json_schema_extra=_pin_versions)
    generator: Generator
    sources: list[DeclaredSource] = Field(min_length=1, max_length=MAX_SOURCES)
    claims: list[ProposedClaim] = Field(default_factory=list, max_length=MAX_CLAIMS)

    @field_validator("schema_version")
    @classmethod
    def _supported(cls, value: str) -> str:
        if value not in SUPPORTED_SCHEMA_VERSIONS:
            raise ValueError(
                f"unsupported schema_version {value!r}; this build reads "
                f"{sorted(SUPPORTED_SCHEMA_VERSIONS)}. A package written against another "
                "version may use the same field names for different things."
            )
        return value

    @model_validator(mode="after")
    def _sources_are_declared_and_distinct(self) -> IntakePackage:
        refs = [s.ref for s in self.sources]
        duplicates = {r for r in refs if refs.count(r) > 1}
        if duplicates:
            raise ValueError(f"two declared sources share a ref: {sorted(duplicates)}")

        known = set(refs)
        for index, claim in enumerate(self.claims):
            if claim.source_ref not in known:
                raise ValueError(
                    f"claims[{index}] cites source {claim.source_ref!r}, which the package "
                    f"does not declare. Declared: {sorted(known)}"
                )
        return self


def reject_forbidden_keys(raw: Any, path: str = "$") -> None:
    """Walk the raw document and refuse the keys a package may never carry.

    Done on the RAW mapping rather than on the model, because `extra="forbid"`
    reports an unexpected key without saying why it is unwelcome. "verified is
    not a field a package may set" is a different message from "unexpected
    field", and only the first tells a generator's author what to change.
    """
    if isinstance(raw, dict):
        for key, value in raw.items():
            if isinstance(key, str) and key.strip().casefold() in FORBIDDEN_KEYS:
                raise IntakeParseError(
                    f"{path}.{key}: a package may not carry {key!r}. "
                    "Confirmation is an act by the candidate, and preferences, eligibility "
                    "and pay expectations are answered by them directly rather than read "
                    "out of a document."
                )
            reject_forbidden_keys(value, f"{path}.{key}")
    elif isinstance(raw, list):
        for index, item in enumerate(raw):
            reject_forbidden_keys(item, f"{path}[{index}]")

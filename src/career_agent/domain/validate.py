"""Cross-field plausibility: the checks no single field can make about itself.

`ExtractedField` polices one observation. This module polices combinations --
the places where each part looks defensible and the whole does not.

Every rule here came from a real posting shape, not from intuition. The M0
architecture is explicit that new rules require real examples, because a
plausibility rule invented at a desk is a rule that will one day discard a good
job for a reason nobody can reconstruct.

THE DIRECTION OF EVERY CORRECTION IS TOWARDS UNCERTAINTY
--------------------------------------------------------
A failed check downgrades a claim; it never manufactures the opposite claim. A
suspicious `WORLDWIDE` becomes `UNSTATED`, not `COUNTRY_LIST`. Seven CORE tools
become seven REQUIRED tools, not six. The system is allowed to say "we do not
know"; it is not allowed to guess in the other direction and call it a finding.

ONE RULE THAT RUNS BACKWARDS FROM INTUITION
-------------------------------------------
`WORLDWIDE` with exclusions is **valid and must not be downgraded**. Revision 1
of the architecture treated it as suspicious; that was wrong, and it was
corrected before any code existed. "Open worldwide, except where we cannot
legally employ" is the single most common shape of a genuinely global posting --
exactly the jobs this product exists to find. Treating it as an error would have
degraded the best results in the corpus.
"""

from dataclasses import dataclass, field
from enum import StrEnum

from career_agent.domain.enums import (
    ExtractionStatus,
    HiringScopeKind,
    LanguageRequirement,
    SoftwareCentrality,
    WorkModel,
)
from career_agent.domain.verify import normalise

#: More than this many tools at CORE means the model has stopped distinguishing
#: "you will own this" from "we use this". Six is generous: a role that truly
#: centres on seven distinct systems is rare enough to be worth flagging.
MAX_CORE_TOOLS = 6

#: More than this many NOT_APPLICABLE on one posting suggests the model is using
#: it as a synonym for silence, which is exactly the failure the precondition
#: table exists to prevent.
MAX_NOT_APPLICABLE = 3


class CorrectionKind(StrEnum):
    """Which remedy a correction calls for.

    The `action` string is written for a human reading an audit trail months
    later. This is written for `domain/remediate.py`, which has to apply the
    remedy without re-deriving the condition that produced it -- re-deriving it
    would mean two implementations of one rule, and two implementations drift
    until the audit trail describes a change the document never received.
    """

    #: A WORLDWIDE hiring scope whose citation describes only how the work
    #: happens. The field goes to NOT_STATED -- a status, not a value -- because
    #: the posting did not state a hiring geography at all.
    SCOPE_TO_NOT_STATED = "SCOPE_TO_NOT_STATED"
    #: A tool named in the extraction that appears nowhere in the posting.
    DROP_SOFTWARE = "DROP_SOFTWARE"
    #: Implausibly many CORE tools; every CORE drops one rung.
    DEMOTE_CORE = "DEMOTE_CORE"
    #: A salary range whose floor exceeds its ceiling.
    CLEAR_COMPENSATION = "CLEAR_COMPENSATION"
    #: A hard language requirement with no explicit cited evidence.
    DEMOTE_LANGUAGE = "DEMOTE_LANGUAGE"


@dataclass(frozen=True)
class Correction:
    """One thing this layer changed, and the reason it changed it.

    `target` identifies *which* item, where the dimension alone does not: the
    raw mention for a dropped tool, the language code for a demoted
    requirement. Positional index would have been shorter and wrong -- the
    remedy runs after other corrections may have removed earlier items.
    """

    dimension: str
    action: str
    reason: str
    kind: CorrectionKind = CorrectionKind.SCOPE_TO_NOT_STATED
    target: str | None = None


@dataclass(frozen=True)
class Contradiction:
    """Two claims that cannot both be comfortable, recorded rather than resolved.

    A contradiction is not corrected here. It is surfaced so that M3 can send
    the relevant gate to UNRESOLVED, which is the honest outcome: the posting
    said two things, and picking one would be inventing a preference the
    employer never expressed.
    """

    dimensions: tuple[str, ...]
    detail: str


@dataclass
class ValidationOutcome:
    """What survived, what changed, and what must fail the extraction."""

    corrections: list[Correction] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    fatal: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Fatal problems mean retry. Corrections and contradictions do not.

        The distinction matters for cost: re-running a whole extraction because
        one tool was dropped as a hallucination would be paying twice for a
        result the validator already fixed correctly.
        """
        return not self.fatal


#: Statements that describe a WORKING ARRANGEMENT and nothing else.
#:
#: Every one of these proves `work_model = REMOTE`. None of them says a word
#: about who may be hired or where they may live. A hiring scope of WORLDWIDE
#: resting on one of these is the single most expensive error this system could
#: make, so the set is written down explicitly rather than left to judgement.
#:
#: "work from anywhere" belongs here and is the one that looks like it does
#: not: "anywhere" there is describing the desk, not the employment contract.
_WORK_ARRANGEMENT_ONLY = frozenset(
    {
        "remote",
        "fully remote",
        "100 remote",
        "remote role",
        "remote position",
        "remote job",
        "remote work",
        "remote working",
        "remotely",
        "work remotely",
        "work from home",
        "work from anywhere",
        "wfh",
        "home based",
        "home office",
        "distributed",
        "distributed team",
        "fully distributed",
        "remote-first",
        "remote first",
        "remote friendly",
    }
)

#: Words that carry no meaning of their own in a work-arrangement sentence.
#:
#: Stripping them is what makes the guard about *what the evidence supports*
#: rather than about the shape of the sentence. "Remote" and "This role is
#: fully remote." are the same claim, and a guard that caught the first and
#: missed the second would be catching a synthetic token while the real
#: citations walked past it -- which is exactly the defect this replaces.
_FILLER = (
    "this role is",
    "this position is",
    "this job is",
    "the role is",
    "the position is",
    "this is a",
    "this is an",
    "we are a",
    "we are an",
    "the team is",
    "fully",
    "completely",
    "entirely",
    "primarily",
    "mostly",
    "100%",
    "a",
    "an",
    "the",
    "is",
    "role",
    "position",
    "job",
)


def _residue(quote: str) -> str:
    """What the citation still claims once the filler is removed.

    Deliberately crude. This is a safety guard against one known-invalid
    evidence pattern, not a geography interpreter: interpreting natural
    language is the extractor's job, and building a parser here to police it
    would move semantics into code, where it cannot be evaluated against the
    golden set.

    The loop repeats because stripping one filler exposes the next --
    "this role is fully remote" needs two passes to reach "remote" -- and stops
    as soon as a pass changes nothing.
    """
    # punctuation-check: allow: a character class, not prose
    text = normalise(quote).strip(" .,;:!-–—()[]")
    for _ in range(len(_FILLER)):
        before = text
        for filler in _FILLER:
            if text.startswith(filler + " "):
                text = text[len(filler) + 1 :]
            if text.endswith(" " + filler):
                text = text[: -len(filler) - 1]
        text = text.strip(" .,;:!-–—()[]")  # punctuation-check: allow: a character class, not prose
        if text == before:
            return text
    return text


def evidence_is_work_arrangement_only(quote: str | None) -> bool:
    """Does this citation describe only how the work happens?

    'Remote' describes where the work happens. 'Worldwide' describes who may be
    hired. A posting that says only the first has not said the second, and
    treating it as though it had would send the candidate after jobs that will
    reject her on residence -- the exact failure the product is built to avoid.

    True is also the answer for a missing citation: a WORLDWIDE scope with
    nothing behind it has even less support than one resting on the word
    "remote".

    False does not mean the citation *is* a hiring scope. Deciding that is the
    extractor's job, judged against the golden set. This guard refuses one
    pattern that is provably insufficient, and refuses nothing else.
    """
    if quote is None:
        return True
    return _residue(quote) in _WORK_ARRANGEMENT_ONLY


def validate_fingerprint(fp: object, description_text: str) -> ValidationOutcome:
    """Run every cross-field rule. Returns what to change and what to fail.

    Takes the fingerprint structurally rather than by import so this module
    stays usable on a partially assembled document during retry.
    """
    outcome = ValidationOutcome()
    quotes = {e.id: (e.quote or "") for e in getattr(fp, "evidence", [])}

    _check_hiring_scope(fp, quotes, outcome)
    _check_remote_contradictions(fp, outcome)
    _check_software(fp, description_text, outcome)
    _check_compensation(fp, outcome)
    _check_languages(fp, outcome)
    _check_responsibilities(fp, outcome)
    _check_not_applicable_volume(fp, outcome)
    return outcome


def _check_hiring_scope(fp: object, quotes: dict[str, str], outcome: ValidationOutcome) -> None:
    scope_field = getattr(getattr(fp, "eligibility", None), "hiring_scope", None)
    if scope_field is None or scope_field.value is None:
        return
    scope = scope_field.value

    if scope.kind is not HiringScopeKind.WORLDWIDE:
        return

    # The rule that runs backwards from intuition: exclusions are fine, and are
    # themselves geographic content. A model that named the places excluded has
    # demonstrably read geography rather than read "remote" and guessed.
    if scope.exclusions:
        return

    quote = quotes.get(scope_field.evidence_id or "")
    if evidence_is_work_arrangement_only(quote):
        outcome.corrections.append(
            Correction(
                "eligibility.hiring_scope",
                "WORLDWIDE -> NOT_STATED",
                (
                    "the citation describes the working arrangement and nothing about who "
                    f"may be hired, which cannot support a worldwide scope (quote: {quote!r})"
                ),
                kind=CorrectionKind.SCOPE_TO_NOT_STATED,
            )
        )


def _check_remote_contradictions(fp: object, outcome: ValidationOutcome) -> None:
    """Remote plus a requirement that defeats remoteness.

    Both are recorded rather than reconciled. A posting really can say "remote"
    in the header and "in the office three days a week" in the body -- usually
    because the header is a template and the body is the truth -- and deciding
    which one wins is M3's job, on evidence, not this layer's on a hunch.
    """
    env = getattr(fp, "work_environment", None)
    elig = getattr(fp, "eligibility", None)
    if env is None or elig is None:
        return

    is_remote = env.work_model.value is WorkModel.REMOTE and env.work_model.status in (
        ExtractionStatus.EXPLICIT,
        ExtractionStatus.INFERRED,
    )
    if not is_remote:
        return

    onsite = env.onsite_frequency
    if onsite.value and normalise(str(onsite.value)) not in {"never", "none", "rarely"}:
        outcome.contradictions.append(
            Contradiction(
                ("work_environment.work_model", "work_environment.onsite_frequency"),
                f"stated remote, but onsite presence is {onsite.value!r}",
            )
        )

    if elig.relocation_required.value is True:
        outcome.contradictions.append(
            Contradiction(
                ("work_environment.work_model", "eligibility.relocation_required"),
                "stated remote, but relocation is required",
            )
        )


def _check_software(fp: object, description_text: str, outcome: ValidationOutcome) -> None:
    """Two independent software failures, checked separately.

    **Hallucination.** A tool the posting never names cannot have been observed
    in it. Checked case-insensitively against the normalised text, because the
    question is whether the string is present at all, not how it was cased.

    **Centrality inflation.** More than six CORE tools means the model has
    stopped distinguishing "you will own this" from "we happen to use this".
    Every CORE drops one rung to REQUIRED rather than being discarded: the
    mentions are real, the emphasis was not.
    """
    software = list(getattr(fp, "software", []))
    haystack = normalise(description_text)

    for index, item in enumerate(software):
        if normalise(item.raw_mention) not in haystack:
            outcome.corrections.append(
                Correction(
                    f"software[{index}]",
                    f"drop {item.raw_mention!r}",
                    "the tool does not appear anywhere in the posting text",
                    kind=CorrectionKind.DROP_SOFTWARE,
                    target=item.raw_mention,
                )
            )

    core = [i for i in software if i.centrality is SoftwareCentrality.CORE]
    if len(core) > MAX_CORE_TOOLS:
        outcome.corrections.append(
            Correction(
                "software[*].centrality",
                "CORE -> REQUIRED",
                (
                    f"{len(core)} tools marked CORE exceeds the plausible maximum of "
                    f"{MAX_CORE_TOOLS}; a role does not centre on that many systems"
                ),
                kind=CorrectionKind.DEMOTE_CORE,
            )
        )


def _check_compensation(fp: object, outcome: ValidationOutcome) -> None:
    """A range whose floor is above its ceiling is not a range.

    Both ends are rejected rather than swapped. Swapping assumes the model
    transposed two correct numbers; it may equally have read the wrong sentence
    entirely, and salary arithmetic downstream must never rest on a guess about
    which failure occurred.
    """
    comp = getattr(fp, "compensation", None)
    if comp is None:
        return
    low, high = comp.min.value, comp.max.value
    if low is not None and high is not None and low > high:
        outcome.corrections.append(
            Correction(
                "compensation.min/max",
                "both -> NOT_STATED",
                f"minimum {low} exceeds maximum {high}; the pair cannot be trusted",
                kind=CorrectionKind.CLEAR_COMPENSATION,
            )
        )


def _check_languages(fp: object, outcome: ValidationOutcome) -> None:
    """A hard language requirement must be quotable.

    HARD_REQUIREMENT is a gate that can end a candidacy. Allowing one to rest on
    inference would let a "German is a plus" become a wall. Unsupported hard
    requirements drop to UNCLEAR, which penalises nothing and asks a question.
    """
    for index, lang in enumerate(getattr(fp, "languages", [])):
        if lang.requirement_level is not LanguageRequirement.HARD_REQUIREMENT:
            continue
        if lang.status is not ExtractionStatus.EXPLICIT or not lang.evidence_id:
            outcome.corrections.append(
                Correction(
                    f"languages[{index}]",
                    "HARD_REQUIREMENT -> UNCLEAR",
                    (
                        f"{lang.language_code!r} is marked a hard requirement without "
                        "explicit cited evidence; a blocking requirement must be quotable"
                    ),
                    kind=CorrectionKind.DEMOTE_LANGUAGE,
                    target=lang.language_code,
                )
            )


def _check_responsibilities(fp: object, outcome: ValidationOutcome) -> None:
    """No responsibilities is a failure, not an observation.

    Every real job posting describes work. A fingerprint with an empty
    responsibility list means the extraction did not read the body -- the one
    thing this milestone exists to do -- so it is fatal and triggers a retry
    rather than being stored as a fingerprint that says nothing.

    The corpus does contain a handful of near-empty postings (test requisitions
    and talent-pool listings, 12 of 18,549). Those will fail here repeatedly and
    end as EXTRACTION_FAILED, which is the correct outcome: they are visible and
    countable rather than silently present as empty analyses.
    """
    if not list(getattr(fp, "responsibilities", [])):
        outcome.fatal.append(
            "responsibilities is empty: a posting that describes no work was not read"
        )


def _check_not_applicable_volume(fp: object, outcome: ValidationOutcome) -> None:
    """Heavy NOT_APPLICABLE use means the model is spelling 'silence' wrongly.

    The precondition table already rewrites each unearned instance
    individually. This counts the pattern, because a posting with several is a
    prompt problem rather than a posting problem, and it should be visible as
    one.
    """
    fields = fp.scalar_fields() if hasattr(fp, "scalar_fields") else {}
    offenders = [
        name for name, value in fields.items() if value.status is ExtractionStatus.NOT_APPLICABLE
    ]
    if len(offenders) > MAX_NOT_APPLICABLE:
        outcome.contradictions.append(
            Contradiction(
                tuple(offenders),
                (
                    f"{len(offenders)} dimensions marked NOT_APPLICABLE; the model is "
                    "likely using it as a synonym for silence"
                ),
            )
        )

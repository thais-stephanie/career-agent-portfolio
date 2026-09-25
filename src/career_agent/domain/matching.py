"""The result of deterministically matching one posting against one search configuration.

This module holds **only data**. It performs no matching, reads no config and
touches no database: it is the contract that `career_agent.match` (the producer)
and `career_agent.storage.mvp_repo` / the web API (the consumers) both import,
so neither of them has to know about the other.

Three commitments are visible in the shapes below, and each one is a decision
that was made elsewhere and is enforced here.

**Nothing collapses into one number** (ADR-0004). ``match_score`` says how
much this looks like the work the candidate wants. ``data_confidence`` says how
much of the posting we actually read. ``eligibility_status`` says whether the
candidate could take the job. They are three fields, and no method on this
module multiplies or averages them.

**Every point is traceable to a quote.** A ``ScoreComponent`` is not a number;
it is a number plus the list of ``ScoreContribution`` rows that produced it,
each carrying the signal that fired, the prominence it fired at, and the exact
sentence it fired on. The same is true of penalties and of blockers. An
interface can therefore always answer "why 68?" without re-running anything.

**Absence is never permission.** ``GateResult`` is three-valued. A posting that
says nothing about where it hires produces ``UNRESOLVED``, never ``PASS``, and
``unknowns`` names what was missing so the interface can show it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from career_agent.domain.enums import (
    AnalysisConfidence,
    DomesticContext,
    EligibilityStatus,
    EmploymentRelationship,
    EmploymentSource,
    EntrySignal,
    ExperienceRequirement,
    FitBand,
    GateResult,
    LocalContractRegime,
    Prominence,
    ResponsibilityCategory,
    ScreeningState,
    Seniority,
    SenioritySource,
)

#: Bumped when the meaning of a stored ``MatchResult`` changes. Stored beside
#: every persisted score, so a row computed under older semantics is visibly
#: stale rather than silently reinterpreted.
#:
#: 2 -- ``posting_facts`` gained ``countries`` and ``regions``, and migration
#: 0017 lifted ten filterable values out onto ``job_match``. A row written at
#: version 1 has empty facet columns, so `country`, `region`, `worksite` and
#: `min_salary` return NOTHING for it. That is precisely the "older semantics,
#: silently reinterpreted" this constant exists to make visible, and it was
#: found by running `serve` against a database scored before the migration --
#: four filters answering zero with nothing anywhere saying why.
#:
#: The digest drift check does not cover this: it fires when the CONFIGURATION
#: changed, and here the configuration is identical and the CODE moved.
#: 3: the title stopped buying compatibility points and seniority became a
#: reading with a source. A row written under 2 has a `role_family` component
#: that no longer exists, a bare string where the seniority reading belongs,
#: and a total that was a sum where this one is a percentage. Every one of
#: those is a silent reinterpretation if the number is compared across the
#: boundary, which is exactly what this constant is for.
#: 4: `title_class` became a resolved reading rather than a base class.
#: 5: migration 0020 added `employment_context` and `contract_regime`. A row
#: written under 4 has both columns NULL, so the employment-context filter and
#: the "likely United States employment" digest section return nothing for it
#: -- the SAME symptom version 2 was created for, one migration later, and the
#: reason this constant is bumped rather than a `--force` being remembered.
#: No score moves across this boundary: the arithmetic is identical and the
#: two columns are readings the matcher was already making and throwing away.
#: 6: migration 0022 added `content_completeness`. A row written under 5 has
#: it NULL, so the partial-content filter returns nothing for it and a card
#: cannot say whether its body is an excerpt -- the same symptom as 5, and the
#: same remedy: a version bump makes an ordinary rescore refresh the row
#: instead of somebody having to remember `--force`. No score moves across
#: this boundary either: the value is a reading the matcher was already able
#: to make and was throwing away.
#: 7: migration 0027 added `experience_requirement`, `experience_min_years`
#: and `entry_signals`. A row written under 6 has all three NULL or empty, so
#: every experience filter and every career-entry filter returns nothing for
#: it -- the same symptom as 5 and 6, and the same remedy. No score moves
#: across this boundary: the reading is one the matcher can already make from
#: the description it already had, and no scoring component consults it.
#: 8: the geography gate's MEANING moved on 2026-09-11 and its columns did
#: not. A country list is no longer widened to the region it implies, an
#: explicit allowlist that omits the candidate's country refuses, an
#: exclusion naming it refuses, a configured scope opens the gate only when
#: it geographically contains a country the candidate may work from, and
#: `fully remote` no longer resolves to WORLDWIDE. Rows written under 7 can
#: carry VERIFIED_ELIGIBLE for postings that list Argentina, Chile and Mexico
#: and never Brazil. Bumped so an ordinary `rescore` rewrites them; CLAUDE.md
#: records that the next change to a reader's meaning needs exactly this.
#: 9: the gates tuple gained `credential` (HCE01, the owner's ratified
#: constraint: a posting that REQUIRES a credential the candidate does not
#: hold is a hard requirement mismatch) and `requirement` (a hard exclusion
#: the person typed in `career-agent setup`, which the wizard wrote on a
#: gate named `other` that no code evaluated: every such exclusion closed
#: nothing until now). A row written under 8 carries five gate outcomes and
#: no answer on the sixth or seventh, so an ordinary `rescore` must rewrite
#: it. Silence on both new gates is the absence of a disqualification, as
#: on the other exclusionary gates; no score moves across this boundary for
#: a posting that states neither.
#: 10: Search Fit v5 (docs/SEMANTIC_MATCHING.md). A phrase component with no
#: configured phrase leaves the denominator (`configured: false`, max 0); a
#: configured one that found nothing stays 0/max. Each phrase component pays
#: its strongest few distinct signals (top-N), one sentence pays for at most half
#: of a component, tools
#: are capped at half when no desired work was found, seniority follows the
#: person's preferred levels, and soft penalties are subtracted magnitudes.
#: Contributions carry `counted`, `source` and `uncounted_reason`; a result
#: carries the validated `semantic` evidence it used, if any. Every reading is
#: unchanged, so a schema 9 row replays (REPLAY_MIN_SCHEMA) rather than being
#: read again.
MATCH_SCHEMA_VERSION = 10

#: The oldest result schema whose stored READINGS a replay may reuse. Separate
#: from MATCH_SCHEMA_VERSION on purpose: 10 changed arithmetic and provenance,
#: never what a reader observed.
REPLAY_MIN_SCHEMA = 9

#: The gates the matcher can answer, in the order they are reported. This is
#: the one vocabulary: `match.gates.GATE_ORDER` is this tuple, and the
#: configuration loader refuses a blocker declared on any other gate, so a
#: misspelt gate can never load and be silently ignored (HCE01 found that it
#: could: `gate: credential` loaded and closed nothing).
GATE_NAMES: tuple[str, ...] = (
    "geography",
    "work_authorization",
    "clearance",
    "worksite",
    "travel",
    "credential",
    "requirement",
)


class TitleClass(StrEnum):
    """How the title alone reads, before and after the ambiguity rules.

    ``UNCLASSIFIED`` is the honest answer for a title no rule recognised. It is
    deliberately not the same as ``EXCLUDED``: an unrecognised title still
    scores its description normally and still earns the small
    ``class_points.unclassified`` award, because
    ``docs/product/principles.md`` section 1 forbids a system that depends on a
    predefined title list.
    """

    PRIMARY = "PRIMARY"
    STRONG_ADJACENT = "STRONG_ADJACENT"
    CONDITIONAL = "CONDITIONAL"
    UNCLASSIFIED = "UNCLASSIFIED"
    EXCLUDED = "EXCLUDED"


class TitleAdjustment(StrEnum):
    """What an ambiguity rule did to a title's base classification."""

    NONE = "NONE"
    PROMOTED = "PROMOTED"
    DEMOTED = "DEMOTED"
    #: The rule existed and its evidence threshold was not met, so the title
    #: stayed where it started. Recorded rather than dropped, because "we
    #: looked and found nothing" is different from "we never looked".
    UNCHANGED_INSUFFICIENT_EVIDENCE = "UNCHANGED_INSUFFICIENT_EVIDENCE"


class SignalEffect(StrEnum):
    """What a lexicon signal is used for. One signal may serve several roles."""

    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    SCREENING = "SCREENING"
    BLOCKER = "BLOCKER"
    SCOPE = "SCOPE"


# =========================================================================
# EVIDENCE
# =========================================================================


@dataclass(frozen=True, slots=True)
class SignalHit:
    """One literal phrase, found once, in one field, with its exact quote.

    ``quote`` is cut from the ORIGINAL field text, never from the case-folded
    and accent-stripped copy the matcher compares against, so it remains a
    verifiable substring of the posting exactly as ADR-0002 requires of every
    other evidence quote in this system.
    """

    signal_id: str
    label: str
    #: ``"title"`` or ``"description"``. Named rather than enumerated because
    #: a later source may add fields (``requirements``, ``responsibilities``)
    #: without a migration of this vocabulary.
    field_name: str
    #: The configured phrase that matched, for "which rule fired".
    pattern: str
    #: The sentence the phrase sits in, cut from the original text.
    quote: str
    char_start: int
    char_end: int
    #: True when a negation cue was found in the guard window before the match.
    #: A negated hit is recorded and displayed, and contributes nothing.
    negated: bool = False
    #: The heading the hit fell under, when one was found. Drives prominence.
    section: str | None = None


@dataclass(frozen=True, slots=True)
class ObservedSignal:
    """Every hit for one lexicon signal, plus the prominence they add up to."""

    signal_id: str
    label: str
    responsibility: ResponsibilityCategory | None
    prominence: Prominence
    hits: tuple[SignalHit, ...] = ()
    negated_hits: tuple[SignalHit, ...] = ()

    @property
    def fired(self) -> bool:
        """True when at least one non-negated hit exists."""
        return bool(self.hits)

    @property
    def best_quote(self) -> str | None:
        return self.hits[0].quote if self.hits else None


# =========================================================================
# TITLE
# =========================================================================


@dataclass(frozen=True, slots=True)
class TitleClassification:
    """What the title says, and what the description did to that reading."""

    base_class: TitleClass
    resolved_class: TitleClass
    adjustment: TitleAdjustment
    #: The taxonomy rule that matched the title, when one did.
    rule_id: str | None = None
    rule_label: str | None = None
    #: The ambiguity rule consulted, when the matched rule declared one.
    ambiguity_rule: str | None = None
    #: Human-readable, always populated. This is what the UI shows.
    reason: str = ""
    #: The description signals that justified a promotion or demotion.
    supporting_signals: tuple[str, ...] = ()


# =========================================================================
# SCORE
# =========================================================================


@dataclass(frozen=True, slots=True)
class ScoreContribution:
    """One signal's contribution to one component."""

    signal_id: str
    label: str
    prominence: Prominence
    #: The configured weight before the prominence multiplier.
    weight: float
    #: What actually landed, after the multiplier and before the component cap.
    #: Zero when the contribution was found but not counted.
    points: float
    quote: str | None = None
    #: False when the signal was found and deliberately not paid: the same
    #: sentence already paid in this component, or it fell outside the
    #: strongest few this component counts. Shown, never hidden.
    counted: bool = True
    #: `lexical` (a configured phrase found in the body) or `semantic` (a
    #: provider finding that passed the publication gate).
    source: str = "lexical"
    uncounted_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ScoreComponent:
    """One of the six bounded components of the match score."""

    component_id: str
    label: str
    points: float
    max_points: float
    contributions: tuple[ScoreContribution, ...] = ()
    #: Set when the component was capped, so the UI can say "capped at 20/20"
    #: instead of implying nothing more was found.
    capped: bool = False
    note: str | None = None
    #: False when the person configured nothing for this component. It is then
    #: not part of Search Fit at all (max 0), which is different from a
    #: configured component the posting did not match (0 of its max).
    configured: bool = True


@dataclass(frozen=True, slots=True)
class SemanticMatch:
    """One published semantic finding, as scoring consumes it."""

    component_id: str
    signal_id: str
    intent_id: str
    #: `strong` or `partial`.
    strength: str
    #: Verbatim substring of the posting. The first published quote.
    quote: str


@dataclass(frozen=True, slots=True)
class SemanticEvidence:
    """Validated semantic findings and where they came from.

    Only what passed `semantic.gate.publish`. Provider confidence is not here:
    it is metadata of the evaluation and never becomes points.
    """

    evaluation_id: str
    provider: str
    model: str
    contract: str
    intent_digest: str
    matches: tuple[SemanticMatch, ...] = ()
    #: `((component_id, verdict), ...)` after the gate.
    verdicts: tuple[tuple[str, str], ...] = ()
    #: The provider the person or Auto asked for, when a different one answered.
    requested_provider: str | None = None
    fallback_reason: str | None = None


@dataclass(frozen=True, slots=True)
class Penalty:
    """A soft penalty. Subtracted from the total; never hides a job."""

    signal_id: str
    label: str
    prominence: Prominence
    weight: float
    points: float
    quote: str | None = None


# =========================================================================
# GATES AND CONFIDENCE
# =========================================================================


@dataclass(frozen=True, slots=True)
class GateOutcome:
    """One eligibility question, answered in three values.

    ``UNRESOLVED`` is the default and the most common healthy answer. It is
    reached by finding nothing, and it never becomes ``PASS`` through silence.
    """

    gate: str
    result: GateResult
    reason: str
    blocker_id: str | None = None
    quote: str | None = None
    char_start: int | None = None
    char_end: int | None = None


@dataclass(frozen=True, slots=True)
class ConfidenceItem:
    """One thing we either did or did not learn about the posting."""

    item_id: str
    label: str
    points: int
    awarded: bool
    note: str | None = None


@dataclass(frozen=True, slots=True)
class EmploymentReading:
    """How the worker is engaged, and the standing of the thing that says so.

    `regime` is the national statute when one is named and `UNRESOLVED`
    otherwise, which is the answer for almost every posting ever collected. A
    reading whose `source` is not explicit is a likelihood and must be rendered
    as one.
    """

    relationship: EmploymentRelationship
    regime: LocalContractRegime
    source: EmploymentSource
    confidence: AnalysisConfidence
    evidence: str | None = None

    @property
    def is_explicit(self) -> bool:
        return self.source.is_explicit


@dataclass(frozen=True, slots=True)
class DomesticReading:
    """Whether the posting reads as employment inside one country only.

    Never an eligibility verdict. `INTERNATIONAL_STATED` means the posting said
    something about hiring beyond one country; it does not mean the candidate
    qualifies, which remains the geography gate's question and only from stated
    scope.
    """

    context: DomesticContext
    confidence: AnalysisConfidence
    evidence: str | None = None
    #: The benefit or statement that produced the reading, for the interface to
    #: name. `None` where nothing fired.
    signal: str | None = None


@dataclass(frozen=True, slots=True)
class ExperienceReading:
    """What one posting said about previous experience, with its sentence.

    A fourth kind of thing, beside `employment` and `domestic`: evidence about
    the POSTING that no scorer consults. Nothing here reaches `match_score`,
    `data_confidence` or `eligibility_status`, and a test asserts it -- ADR-0004
    keeps three measurements and this is not a fourth one.

    `quote` is a contiguous substring of the ORIGINAL description whenever it
    is present (ADR-0002), so an interface can show the employer's own line
    beside the conclusion drawn from it. `None` for `NOT_STATED`, where there
    is nothing to quote.
    """

    requirement: ExperienceRequirement = ExperienceRequirement.NOT_STATED
    #: The figure, when the posting stated an unambiguous MINIMUM. `0` is a
    #: real answer and means the posting said none is needed; `None` means no
    #: figure was read, which is a different fact.
    min_years: int | None = None
    quote: str | None = None
    entry_signals: tuple[EntrySignal, ...] = ()

    @property
    def opens_to_beginners(self) -> bool:
        """Whether the posting itself invited somebody with no track record.

        True only on something the employer WROTE. A posting that merely fails
        to state a requirement is not counted: silence is not an invitation,
        and a filter built on that reading would offer a beginner thousands of
        senior roles that simply never mentioned years.
        """
        return bool(self.entry_signals) or (self.requirement is ExperienceRequirement.NONE_REQUIRED)


#: What a posting with no text at all reads as. Named so callers do not
#: construct their own and drift.
NOT_STATED_EXPERIENCE = ExperienceReading()


UNRESOLVED_EMPLOYMENT = EmploymentReading(
    relationship=EmploymentRelationship.UNRESOLVED,
    regime=LocalContractRegime.UNRESOLVED,
    source=EmploymentSource.DEFAULT,
    confidence=AnalysisConfidence.LOW,
)

UNRESOLVED_DOMESTIC = DomesticReading(
    context=DomesticContext.UNRESOLVED,
    confidence=AnalysisConfidence.LOW,
)


@dataclass(frozen=True, slots=True)
class SeniorityReading:
    """A level, and the standing of the thing that says so.

    The value on its own was the defect. `MID` meant both "the posting says
    mid-level" and "the posting says nothing, so we assume mid-level", and the
    scorer paid the same nine points for each -- absence buying permission, in
    the one dimension where it is easiest not to notice.

    So the reading carries its own provenance. `source` says which rule fired,
    `confidence` says how much that rule is worth, and `evidence` is the text it
    fired on: a contiguous substring of the title or the description exactly as
    ADR-0002 requires of every other quote in this system, and `None` for
    `DEFAULT`, where there is nothing to quote and inventing something would be
    the whole problem restated.
    """

    value: Seniority
    source: SenioritySource
    confidence: AnalysisConfidence
    evidence: str | None = None

    @property
    def is_evidence(self) -> bool:
        """Whether the posting actually said this."""
        return self.source.is_evidence

    @property
    def sentence(self) -> str:
        """One plain line for a reader, honest about which of the two it is."""
        if not self.is_evidence:
            return "The posting does not state a level. Treating it as mid-level."
        # `.get`, not a subscript, and the reason is a 500 this file served.
        # Adding STAFF and PRINCIPAL to `Seniority` left this table behind, and
        # the first real Principal posting to reach a card took the whole jobs
        # list down with a KeyError -- on the personal corpus, where the demo
        # has no such posting to catch it. A vocabulary is allowed to grow; a
        # display table falling behind it must degrade to the value's own name
        # rather than to an exception.
        word = LEVEL_WORDS.get(self.value, self.value.value.lower())
        return f"The posting states this is a {word} role."


#: The word a person reads, for each level. English here because every
#: user-facing string in this interface is English; the PT-BR spellings live in
#: the DETECTOR, which is a question about what postings say, not about what we
#: display. See `career_agent.match.seniority`.
LEVEL_WORDS: dict[Seniority, str] = {
    Seniority.INTERN: "intern",
    Seniority.JUNIOR: "junior",
    Seniority.MID: "mid-level",
    Seniority.SENIOR: "senior",
    Seniority.STAFF: "staff",
    Seniority.PRINCIPAL: "principal",
    Seniority.LEAD: "lead",
}

#: What a posting that stated nothing is treated as. It is a default, and the
#: reading that carries it says so in its `source`; nothing may read this value
#: without also reading that field.
DEFAULT_SENIORITY = SeniorityReading(
    value=Seniority.MID,
    source=SenioritySource.DEFAULT,
    confidence=AnalysisConfidence.LOW,
    evidence=None,
)


# =========================================================================
# THE RESULT
# =========================================================================


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Everything the deterministic matcher concluded about one posting.

    Reproducible: the same posting text and the same ``config_version`` always
    produce the same result. Nothing here consults a clock, a network or a
    model. ``computed_at`` is passed in by the caller precisely so the matcher
    itself stays a pure function of (posting, configuration).
    """

    # -- provenance ------------------------------------------------------
    config_id: str
    config_version: int
    schema_version: int = MATCH_SCHEMA_VERSION

    # -- the three measurements, kept apart (ADR-0004) --------------------
    match_score: int = 0
    data_confidence: int = 0
    eligibility_status: EligibilityStatus = EligibilityStatus.UNRESOLVED

    screening_state: ScreeningState = ScreeningState.NOT_BLOCKED
    screening_reason: str = ""
    fit_band: FitBand = FitBand.WEAK
    analysis_confidence: AnalysisConfidence = AnalysisConfidence.LOW

    # -- how each measurement was reached ---------------------------------
    title: TitleClassification | None = None
    components: tuple[ScoreComponent, ...] = ()
    penalties: tuple[Penalty, ...] = ()
    penalty_total: float = 0.0
    gates: tuple[GateOutcome, ...] = ()
    confidence_items: tuple[ConfidenceItem, ...] = ()
    signals: tuple[ObservedSignal, ...] = ()

    # -- what we did not learn --------------------------------------------
    #: Plain sentences naming what the posting never said. Shown in the UI so
    #: an unknown reads as an unknown rather than as an absence of problems.
    unknowns: tuple[str, ...] = ()

    seniority: SeniorityReading = DEFAULT_SENIORITY
    #: How the worker is engaged, and whether the job reads as domestic to one
    #: country. Both are evidence about the POSTING and neither reaches the
    #: score or the eligibility status; ADR-0004 keeps the three measurements
    #: apart and these are a fourth kind of thing, described rather than folded.
    employment: EmploymentReading = UNRESOLVED_EMPLOYMENT
    domestic: DomesticReading = UNRESOLVED_DOMESTIC
    #: What the posting asked for in the way of previous experience, and which
    #: invitations to beginners it extended. Read on every posting, consulted
    #: by no scorer, and filterable -- which is the whole point: a person
    #: entering a profession has to be able to ASK for the postings that said
    #: they would consider them.
    experience: ExperienceReading = NOT_STATED_EXPERIENCE

    #: The posting facts the matcher actually read: salary, employment type,
    #: work model. Recorded here rather than re-derived by the interface so the
    #: card shows the SAME salary the compensation component was scored from.
    #: Two readings of one posting drifting apart is the failure this prevents;
    #: it is why the facts travel with the result instead of beside it.
    posting_facts: dict[str, object] = field(default_factory=dict)

    #: The validated semantic evidence this score used, or None when the score
    #: is deterministic-only (semantic off, no provider, or nothing evaluated).
    semantic: SemanticEvidence | None = None

    #: ISO-8601 UTC, supplied by the caller.
    computed_at: str = ""

    # -- convenience, all derived, none stored separately ------------------
    @property
    def blockers(self) -> tuple[GateOutcome, ...]:
        """The gates that explicitly failed. Empty is the common case."""
        return tuple(g for g in self.gates if g.result is GateResult.FAIL)

    @property
    def unresolved_gates(self) -> tuple[GateOutcome, ...]:
        return tuple(g for g in self.gates if g.result is GateResult.UNRESOLVED)

    @property
    def is_eligible_blocked(self) -> bool:
        return self.eligibility_status is EligibilityStatus.VERIFIED_NOT_ELIGIBLE

    @property
    def matched_strengths(self) -> tuple[ScoreContribution, ...]:
        """Every positive contribution, strongest first. What the UI leads with."""
        rows = [c for comp in self.components for c in comp.contributions if c.points > 0]
        return tuple(sorted(rows, key=lambda c: c.points, reverse=True))

    @property
    def missing_signals(self) -> tuple[str, ...]:
        """Weighted signals the configuration wanted that the posting never showed."""
        return tuple(
            comp.note for comp in self.components if comp.note and comp.points < comp.max_points
        )


@dataclass(frozen=True, slots=True)
class ScoredJob:
    """A posting joined to its match result, as the API and the views see it.

    Deliberately flat and deliberately read-only. The web layer never assembles
    a job from several repository calls; it asks for this.
    """

    job_id: str
    title: str
    company_name: str
    company_slug: str
    provider: str
    access_method: str
    external_id: str
    url: str | None
    location_raw: str | None
    department: str | None
    posted_at: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    closed_at: str | None
    content_hash: str
    description_text: str = ""
    result: MatchResult | None = None
    # -- what this row stands for, when duplicates are grouped --------------
    #: How many postings this row represents, INCLUDING itself. 1 means "this
    #: is not a group", which is what every construction site that predates
    #: grouping means, and is why the default is 1 rather than 0.
    #:
    #: A grouped row must never hide what it collapsed. `sibling_locations`
    #: exists so the interface can SAY which places the group covers: a card
    #: that silently drops seven siblings is worse than eight cards, because
    #: the reader cannot tell that anything was dropped.
    duplicate_count: int = 1
    #: Distinct `location_raw` values across the group, this row's own first.
    #: Empty when the row is a singleton, so "no siblings" and "siblings with
    #: no location" are not the same value.
    sibling_locations: tuple[str, ...] = ()
    # -- human workflow state, never touched by the matcher ----------------
    application_status: str = "DISCOVERED"
    applied_at: str | None = None
    saved: bool = False
    #: When she hid this posting from the discovery views, or None.
    #:
    #: Beside `saved` because it is the same kind of fact -- a presentation
    #: choice she made about one row -- and NOT in `application_status`,
    #: which is a position in a workflow. A hidden posting she has applied to
    #: is still APPLIED.
    hidden_at: str | None = None
    notes: str | None = None
    enrichment: dict[str, object] = field(default_factory=dict)

"""The one public entry point: one posting, one configuration, one `MatchResult`.

`match_job` is a pure function of (posting, configuration). It reads no clock --
`computed_at` is a parameter for exactly that reason -- opens no file, makes no
network call and consults no global. Calling it twice on the same inputs returns
equal results, which is what makes a stored score re-derivable and a regression
in this package visible as a diff rather than as a mood.

The order below is fixed and each step depends only on the ones before it:

    observe -> classify -> gates -> screening -> score -> confidence -> assemble

Screening and eligibility are computed in the same pass and never touch each
other (ADR-0004). A posting can be BLOCKED on screening -- it is not the work
this candidate is looking for -- while its eligibility is UNRESOLVED, and a
posting the candidate is provably barred from can sail through screening. They
answer different questions and the interface shows both.
"""

from __future__ import annotations

from dataclasses import dataclass

from career_agent.config.search_config import SearchConfig
from career_agent.domain.enums import ContentCompleteness, GateResult, ScreeningState
from career_agent.domain.matching import MatchResult, ObservedSignal
from career_agent.match.employment import (
    read_domestic_context,
    read_employment,
)
from career_agent.match.experience import read_experience
from career_agent.match.gates import eligibility_status_from, evaluate_gates
from career_agent.match.lexicon import body_only, observe
from career_agent.match.places import resolve_place
from career_agent.match.score import (
    confidence_band_for,
    data_confidence,
    fit_band_for,
    match_score_from,
    score_components,
    soft_penalties,
)
from career_agent.match.seniority import read_seniority
from career_agent.match.taxonomy import classify_title
from career_agent.match.text import fold_field, split_sections
from career_agent.providers.base import CompensationBand


@dataclass(frozen=True, slots=True)
class JobFacts:
    """Everything the deterministic matcher is allowed to know about a posting.

    Only the title and the description are required, and the field order says
    why: those two are the posting, and everything else is metadata a board may
    or may not have supplied. Every optional field defaults to `None` rather
    than to a plausible value, because a missing salary must score as unknown
    and a missing date must read as unknown -- never as zero and never as today.
    """

    title: str
    description: str
    job_id: str | None = None
    location_raw: str | None = None
    employment_type: str | None = None
    #: HOW MUCH OF THE POSTING THIS IS. Provenance, never a judgement.
    #:
    #: A source that returns an excerpt and offers no way to fetch the rest
    #: produces a body that is short for a reason the employer had nothing to
    #: do with. `data_confidence` already falls for a short body and cannot
    #: tell the two apart; this can, and it is carried so a card can say so
    #: and a filter can ask.
    #:
    #: It is deliberately NOT read by any scorer. Discounting a compatibility
    #: number by a provenance fact would blend two of the three measurements
    #: ADR-0004 keeps apart, and `tests/integration/test_partial_content.py`
    #: asserts that the same text scores the same however it arrived.
    content_completeness: str = ContentCompleteness.UNKNOWN.value
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    salary_period: str | None = None
    #: The OTHER currencies this posting stated, if it stated more than one.
    #: A posting offering EUR 110,500-181,500 and USD 170,350-275,550 states
    #: two alternatives, not one range, and which of them is the relevant one
    #: is a preference -- so both travel and `match.score` chooses. Empty for
    #: the overwhelming majority of postings, which name one currency.
    salary_alternates: tuple[CompensationBand, ...] = ()
    posted_at: str | None = None
    provider: str | None = None
    access_method: str | None = None
    #: REMOTE / HYBRID / ONSITE as the BOARD'S OWN STRUCTURED FIELD stated it.
    #:
    #: Distinct from `_work_model`, which reads the same three words out of
    #: `location_raw` text. This one is the employer answering a form question
    #: -- Ashby's `workplaceType`, and any other board that asks -- and it is
    #: the half of the pair that makes a stated location readable.
    #:
    #: **Why it changes what `location_raw` MEANS.** A role whose workplace
    #: type is ONSITE or HYBRID has an office, so its location is that office
    #: and invariant 3 forbids reading it as a hiring scope. A role whose
    #: workplace type is REMOTE has no office, so a location on it cannot be
    #: one -- `Remote U.S.` is the employer saying where the remote role is
    #: open. Same column, opposite meanings, and the discriminator is this
    #: field rather than a guess.
    #:
    #: None when the board publishes no such field, which leaves the geography
    #: gate exactly where it was.
    workplace_type: str | None = None
    #: The employer's own answer to "where can you hire?", when the BOARD asked.
    #:
    #: Not `location_raw`, and the separation is the point. `San Francisco, CA`
    #: on a Greenhouse posting is an office; `Anywhere in the World` in a We
    #: Work Remotely `region` is a hiring scope, because that board asks for
    #: one. Reading the first as the second is the conflation invariant 3
    #: forbids.
    #:
    #: Filled in by the CALLER, never derived here. `career_agent.match` may
    #: not import `career_agent.providers` -- a test enforces it by walking the
    #: import graph -- so the layer that already knows which adapter produced a
    #: posting is the layer that answers, and the matcher receives a string it
    #: can read without knowing whose it is.
    declared_hiring_scope: str | None = None
    #: The engagement the BOARD'S OWN CONTRACT-TYPE FIELD declared.
    #:
    #: The pair to `workplace_type`, and it exists for the same reason. A board
    #: that asks the employer to pick a contract type from a closed list has
    #: obtained a statement; reading the same fact out of prose is a different
    #: and weaker act, and `match/employment.py` already does that separately.
    #:
    #: Two fields rather than one because a Brazilian posting answers two
    #: questions at once. The relationship is portable -- an employee is an
    #: employee anywhere. The regime names a NATIONAL STATUTE and is a much
    #: stronger claim, so it is filled only from vocabulary that IS that
    #: statute: `vacancy_legal_entity` is pessoa juridica, and `FullTime` on a
    #: Denver posting is not the CLT no matter how permanent the job is.
    #:
    #: Filled in by the CALLER, like `declared_hiring_scope` and for the same
    #: structural reason: `career_agent.match` may not import
    #: `career_agent.providers`, so the layer that knows which adapter produced
    #: a posting is the layer that answers.
    declared_relationship: str | None = None
    declared_contract_regime: str | None = None

    @property
    def salary_bands(self) -> tuple[CompensationBand, ...]:
        """Every band this posting stated, the four flat fields first."""
        return (
            CompensationBand(
                min_value=self.salary_min,
                max_value=self.salary_max,
                currency=self.salary_currency,
                period=self.salary_period,
            ),
            *self.salary_alternates,
        )


#: Title classes strong enough to admit a posting on their own.
#: `CONDITIONAL` is deliberately absent: those titles mean different work at
#: different employers, which is exactly the case the description must settle.
ADMITTING_TITLE_CLASSES = ("PRIMARY", "STRONG_ADJACENT")


def evaluate_screening(
    config: SearchConfig,
    observed: dict[str, ObservedSignal],
    title_class: str | None = None,
) -> tuple[ScreeningState, str, str]:
    """Does this posting describe work of the right kind at all?

    TWO CHANNELS, UNIONED. A posting is admitted when the required SIGNALS
    fired, OR when its TITLE is one this search is looking for. Either is
    sufficient; neither is required.

    That union is the point. A posting whose title is exactly `Integration
    Engineer` could be blocked because its body was thin or phrased unusually --
    the title said what the job was and nothing listened. Equally, a posting
    titled `Business Applications Builder` is a title nobody put in a taxonomy,
    and its description is the only thing that can admit it.

    A returned CHANNEL says which one let it through, because "why is this
    here" is a question the person will ask of exactly the postings the
    taxonomy did not predict -- and those are the ones worth reading.

    The signal channel is named for WHERE ITS HITS WERE. `observe()` runs every
    signal over the title and the body and returns one union, so a phrase in the
    title satisfies the required group exactly as a phrase in the body does. It
    was reported as `description` regardless, and measured: the title `Business
    Systems Analyst` over an unrelated body about a distribution centre was
    admitted and the channel said `description`. The word is now `signals`, and
    `title_and_signals` where both applied -- the union is honest about being a
    union.

    A BLOCKED posting is still scored, still gated and still stored. Screening
    decides what the digest leads with, not what exists.
    """
    signal_reason = _signal_channel(config, observed)
    by_description = signal_reason is None
    by_title = str(title_class or "").upper() in ADMITTING_TITLE_CLASSES

    if by_description and by_title:
        return ScreeningState.NOT_BLOCKED, "", "title_and_signals"
    if by_description:
        return ScreeningState.NOT_BLOCKED, "", "signals"
    if by_title:
        # The description did not carry the required signals and the title did.
        # Recorded rather than silently admitted: this is the case where the
        # score will be lower than the title suggests, and the person should
        # know the body did not back it up.
        return (
            ScreeningState.NOT_BLOCKED,
            f"Admitted on its title. {signal_reason}",
            "title",
        )
    return ScreeningState.BLOCKED, signal_reason or "", "none"


def _signal_channel(config: SearchConfig, observed: dict[str, ObservedSignal]) -> str | None:
    """None when the required signals fired, otherwise why they did not.

    "The required signals", not "the description": `observed` is the union of
    title and body hits, and naming this after one of its two inputs is what
    made the channel label wrong.

    The co-occurrence rule lives in the configuration, not here: a group is
    `any_of` a list of signals, and every group must fire. One generic word
    cannot satisfy a group that names a technology family and a responsibility
    family, which is what stops `automation` on its own from admitting a
    marketing role.
    """
    for group in config.screening.required_any_groups:
        if not any(observed[signal_id].fired for signal_id in group.any_of):
            return f"No signal in the required group {group.id!r} ({group.label}) was found."

    for signal_id in config.screening.required_all:
        if not observed[signal_id].fired:
            return f"The required signal {signal_id!r} was not found."

    return None


def match_job(config: SearchConfig, job: JobFacts, *, computed_at: str) -> MatchResult:
    """Match one posting. Pure: same inputs, same result, every time.

    The two fields are folded here, once, and every step below is handed the
    same two values. That is not a caching layer -- there is no key, no
    invalidation and nothing outlives the call. It is the observation that the
    folded copy of a posting is a property of the posting, so the lexicon, the
    gates and the seniority reader are all asking for the identical string.
    """
    sections = tuple(
        split_sections(
            job.description,
            config.prominence.primary_headings,
            config.prominence.secondary_headings,
        )
    )
    title_field = fold_field(job.title)
    description_field = fold_field(job.description, sections)

    observed = observe(config, title_field, description_field)
    title = classify_title(config, job.title, observed)
    # One reading, three consumers: the score, the confidence items and the
    # card. Made here so they cannot disagree about one posting.
    seniority = read_seniority(title_field, description_field)
    # Two readings ABOUT the posting, deliberately made after the gates and
    # deliberately not consulted by them. How a worker is engaged and whether a
    # job reads as domestic to one country are facts worth showing a candidate;
    # neither may fail a posting, and neither buys or costs a single point.
    employment = read_employment(
        description_field,
        # A DECLARED CONTRACT TYPE DECIDES, and the description is not
        # consulted -- the rule V1.5 had to learn on hiring scope, where
        # `declared_scope or prose` behaved as "fall back to the body whenever
        # the field is not the answer you wanted" and produced two verdicts for
        # one fact. A form the employer filled in outranks a word in a
        # paragraph, including when the paragraph is louder.
        declared_relationship=job.declared_relationship,
        declared_regime=job.declared_contract_regime,
    )
    domestic = read_domestic_context(description_field)
    # A THIRD reading about the posting, on the same terms as the two above:
    # read on every posting, consulted by no scorer and by no gate.
    #
    # It is read from `job.description` -- the ORIGINAL, not the folded copy --
    # because the reading carries a QUOTE and ADR-0002 makes a quote evidence
    # only while it is a contiguous substring of the text the employer wrote.
    experience = read_experience(job.description)
    place = resolve_place(job.location_raw)
    gates = evaluate_gates(
        config,
        observed,
        title_field,
        description_field,
        declared_scope=job.declared_hiring_scope,
        # **`job.workplace_type`, NOT `_work_model(job)`.** The structured
        # field only, never the one inferred from location text.
        #
        # The rule's whole justification is that the EMPLOYER ANSWERED A FORM:
        # a role whose workplace type is REMOTE has no office, so a location
        # on it cannot be one. An inferred answer carries none of that -- a
        # pasted posting whose location string happens to contain the word
        # "remote" is somebody's typing, and reading a country out of it as a
        # hiring restriction would refuse jobs on the strength of a guess.
        #
        # Caught by `tests/integration/test_web_api.py`, whose manual-import
        # fixtures went ineligible the moment the inferred value was passed.
        workplace_type=job.workplace_type,
        place=place,
        # The employer's OWN WORDS for where this is, so a refusal can quote
        # `Remote U.S.` rather than a sentence this product composed about it.
        location_raw=job.location_raw,
    )
    eligibility = eligibility_status_from(gates)
    screening_state, screening_reason, discovery_channel = evaluate_screening(
        config, observed, str(title.resolved_class)
    )

    # FIT is a claim about the work, and the work is described in the body.
    # Screening keeps the union above -- a title that says exactly what the job
    # is should still admit it -- but nothing below this line may be paid for
    # by a word in a name.
    scoring_signals = body_only(config, observed)
    components = score_components(
        config,
        observed_body=scoring_signals,
        seniority=seniority,
        # The SAME reading the card shows, for the same reason the seniority
        # one is passed rather than remade: two readings of one posting
        # drifting apart is a class of bug worth designing out.
        employment=employment,
        job_facts=job,
    )
    penalties = soft_penalties(config, scoring_signals)
    score = match_score_from(components, penalties)

    geography_resolved = any(
        gate.gate == "geography" and gate.result is not GateResult.UNRESOLVED for gate in gates
    )
    confidence, confidence_items, confidence_unknowns = data_confidence(
        config, job, seniority, geography_resolved=geography_resolved
    )

    compensation = next(c for c in components if c.component_id == "compensation_contract")
    unknowns = confidence_unknowns + ((compensation.note,) if compensation.note else ())

    return MatchResult(
        config_id=config.config_id,
        config_version=config.config_version,
        match_score=score,
        data_confidence=confidence,
        eligibility_status=eligibility,
        screening_state=screening_state,
        screening_reason=screening_reason,
        fit_band=fit_band_for(score, config),
        analysis_confidence=confidence_band_for(confidence, config),
        title=title,
        components=components,
        penalties=penalties,
        penalty_total=sum(p.points for p in penalties),
        gates=gates,
        confidence_items=confidence_items,
        # Only signals that were seen at all. A signal with neither a live nor a
        # negated hit says nothing about this posting, and 50 empty rows would
        # bury the handful that did fire.
        signals=tuple(s for s in observed.values() if s.hits or s.negated_hits),
        unknowns=unknowns,
        seniority=seniority,
        employment=employment,
        domestic=domestic,
        experience=experience,
        posting_facts=posting_facts(job),
        computed_at=computed_at,
    )


def _work_model(job: JobFacts) -> str | None:
    """REMOTE / HYBRID / ONSITE, read from what the board itself said.

    **The structured field first.** A board that asks the employer to pick one
    has an answer, and reading three words out of a free-text location when a
    form field already holds the answer is inference standing in for evidence.

    The text is the fallback, and only the board's own words -- never the
    description, because "remote" in a body paragraph is as likely to describe
    the team as the role. Nothing is inferred beyond that: a location that says
    none of these returns None, and the interface prints "not stated".

    This is emphatically NOT an eligibility answer. Remote does not mean
    worldwide; that question belongs to the geography gate and stays there.
    """
    if job.workplace_type:
        return job.workplace_type
    if not job.location_raw:
        return None
    folded = job.location_raw.casefold()
    if "hybrid" in folded or "hibrido" in folded or "híbrido" in folded:
        return "HYBRID"
    if "remote" in folded or "remoto" in folded:
        return "REMOTE"
    if "onsite" in folded or "on-site" in folded or "presencial" in folded:
        return "ONSITE"
    return None


def posting_facts(job: JobFacts) -> dict[str, object]:
    """The metadata the matcher read, travelling with the result it produced.

    Public because `match.replay` assembles the same `MatchResult` from stored
    readings and must derive this field the same way rather than carry a copy.
    A second implementation of it is how a replayed card and a scored card start
    disagreeing about the salary they were scored from.

    The interface renders these instead of re-deriving them, so the salary on
    the card is by construction the salary the compensation component was
    scored from. Absent stays absent: every value is None when the board did
    not supply it, and none is filled in with a plausible guess.
    """
    place = resolve_place(job.location_raw)
    salary: dict[str, object] | None = None
    if job.salary_min is not None or job.salary_max is not None:
        salary = {
            "min": job.salary_min,
            "max": job.salary_max,
            "currency": job.salary_currency,
            "period": job.salary_period,
        }
    # What the BOARD PRINTED about where this is, resolved to codes so the
    # interface can offer "postings in Brazil" without every query LIKE-ing
    # over free text. Emphatically NOT hiring scope: a posting in San Francisco
    # is not a posting that will hire in Brazil, and `eligibility_status`
    # continues to answer that question by itself (invariant 3).
    return {
        "salary": salary,
        "employment_type": job.employment_type,
        "work_model": _work_model(job),
        "location_raw": job.location_raw,
        "countries": list(place.countries),
        "regions": list(place.regions),
        "provider": job.provider,
        "access_method": job.access_method,
        # HOW MUCH OF THE POSTING THIS IS. It travels with the facts rather
        # than being re-derived by the interface, for the same reason the
        # salary does: the card must show the provenance of the text the score
        # was computed from, not the provenance of whatever is in the database
        # by the time somebody looks.
        "content_completeness": job.content_completeness,
    }

"""Turning observations into five bounded components, and one search-fit number.

Every point here is traceable: a component is a number plus the rows that
produced it, and each row names the signal that fired, the prominence it fired
at, and the sentence it fired on. "Why 68?" is answerable without re-running
anything, which is the whole reason `ScoreComponent` carries contributions
instead of a float.

Three rules are enforced in code rather than left to configuration.

**Salary is a preference, never a filter.** A posting that states no salary
scores `salary_unknown` and carries on.

**Two conversions, and only one of them needs an external fact.** Dividing an
annual figure by twelve is exact arithmetic inside one currency: it invents
nothing, so it is done, and the note says it was done. An exchange rate is an
observation about the world on a particular day, so it is applied only when the
configuration supplies both the rate AND `conversion_rates_dated`, and the note
carries the rate and the date it was recorded. A rate with no date is refused --
`salary_unknown`, and the reason said out loud -- because an undated rate is a
manufactured fact wearing a number's clothes. An hourly rate is never converted
at all: monthly-from-hourly needs an assumed FTE week, and that assumption is
not in any posting.

A converted comparison the reader cannot see the arithmetic of is not
acceptable, which is why every one of these paths writes its note whether the
answer was yes, no, or unknown.

**The title buys no compatibility points.** It used to buy 25 of 100 through a
`role_family` component that paid for the title RESEMBLING one the search wants.
Measured: a warehouse posting titled `Business Systems Analyst` scored 39 while
the identical body under `Operations Wizard` scored 14. That is the title
manufacturing fit, which is what "search for the work, not the title" exists to
forbid. The taxonomy still runs -- it names the role, it admits postings to the
digest, and it is displayed -- it simply no longer pays.

**Seniority is a reading, not a word.** It carries the source it came from, and
`_seniority_component` pays only for sources that are evidence. A posting that
stated no level is MID by DEFAULT and earns nothing, because a component that
paid for the fallback would be absence buying permission.

**Confidence counts what we did not learn, item by item.** Every configured item
produces a `ConfidenceItem` whether or not it was awarded, and every un-awarded
one also produces a plain sentence in `unknowns`. An unknown has to read as an
unknown; an absent row would read as an absence of problems.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING

from career_agent.config.search_config import (
    CompensationPreference,
    SearchConfig,
    WeightedComponent,
)
from career_agent.domain.enums import (
    AnalysisConfidence,
    EmploymentRelationship,
    FitBand,
    Prominence,
    Seniority,
)
from career_agent.domain.matching import (
    DEFAULT_SENIORITY,
    ConfidenceItem,
    EmploymentReading,
    ObservedSignal,
    Penalty,
    ScoreComponent,
    ScoreContribution,
    SemanticEvidence,
    SemanticMatch,
    SeniorityReading,
)
from career_agent.match.work_model import read_work_model
from career_agent.providers.base import CompensationBand

if TYPE_CHECKING:  # pragma: no cover - import cycle exists only for the type checker
    from career_agent.match.engine import JobFacts

#: How many unfired weighted signals a component names in its `note`. Enough to
#: be actionable, few enough to read.
MISSING_SIGNALS_SHOWN = 3

#: How many distinct signals each phrase component pays for, fixed by MEANING
#: rather than fitted to any corpus: four distinct desired activities make a
#: role clearly about the work someone wants, three tools or methods make its
#: toolset clearly theirs, three other desired signals make the rest clear.
#: More matches than this add nothing, so a verbose posting cannot buy fit by
#: mentioning everything, and nobody has to keyword-stuff a search to reach
#: the top. docs/SEMANTIC_MATCHING.md records the design study behind it.
COUNTED_SIGNALS: dict[str, int] = {
    "responsibilities": 4,
    "technologies": 3,
    "automation_integration": 3,
}

#: Tools without any of the desired work can earn at most this share of the
#: tools component. A posting that uses your tools for work you did not ask
#: for is not a fit for that work.
TOOLS_WITHOUT_WORK_SHARE = 0.5

#: The seniority ladder for "one step away". LEAD sits between SENIOR and
#: STAFF: past senior scope, short of the staff track.
SENIORITY_LADDER: tuple[Seniority, ...] = (
    Seniority.INTERN,
    Seniority.JUNIOR,
    Seniority.MID,
    Seniority.SENIOR,
    Seniority.LEAD,
    Seniority.STAFF,
    Seniority.PRINCIPAL,
)
#: Shares of the seniority maximum when the person stated preferred levels.
SENIORITY_ADJACENT_SHARE = 0.5
SENIORITY_OTHER_SHARE = 0.2

#: Semantic strength to the prominence whose multiplier it earns. A strong
#: finding is central to the role, like a phrase in the role's own section. A
#: partial one is, by the contract's own definition, a secondary duty OR a
#: close neighbour of what was asked for: an interpretation of adjacency, paid
#: like an incidental mention rather than like a statement in a secondary
#: section. Measured on the real corpus (2026-09-25): paid as SECONDARY,
#: generic full-stack and BI roles filled every slot on neighbour evidence and
#: read STRONG beside the genuine fits; as INCIDENTAL, false positives on the
#: labelled set fell from 3 to 1 (docs/SEMANTIC_MATCHING.md).
SEMANTIC_PROMINENCE: dict[str, Prominence] = {
    "strong": Prominence.PRIMARY,
    "partial": Prominence.INCIDENTAL,
}

#: Months in a year. Not a configurable rate and not an observation -- it is the
#: definition of the two words, which is exactly why an annual figure may be
#: compared against a monthly target and an hourly one may not.
MONTHS_PER_YEAR = 12.0

#: The periods that convert into each other without assuming anything. HOUR is
#: deliberately absent from both sides: hourly-to-monthly needs a full-time
#: hours figure, no posting states one, and picking 160 would put an invented
#: number into a threshold comparison.
_PERIOD_FACTORS: dict[tuple[str, str], float] = {
    ("YEAR", "MONTH"): 1.0 / MONTHS_PER_YEAR,
    ("MONTH", "YEAR"): MONTHS_PER_YEAR,
}


def _amount(value: float) -> str:
    """A money figure as a person reads it. Exact, never rounded into a claim."""
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _select_band(job_facts: JobFacts, wanted_currency: str) -> CompensationBand:
    """The band to score, out of every band the posting stated.

    A payload may name several currencies -- Ashby writes one regional offer
    per currency, Greenhouse one custom field per country -- and the reader
    cannot choose between them, because which one matters is a preference it
    must not know. It carries all of them and this is where the choice happens:
    the band in the candidate's own currency if the posting stated one, and the
    lead band otherwise.

    The lead used to be the only thing that survived, which meant payload order
    decided. Ashby posting `01M0XZKEGAPG0EY02HK36QBTW8` states EUR
    110,500-181,500 first and USD 170,350-275,550 second, so a USD-targeting
    candidate was told it stated nothing comparable. Order in a JSON array is
    not relevance.
    """
    bands = job_facts.salary_bands
    for band in bands:
        if band.has_amounts and (band.currency or "").upper() == wanted_currency:
            return band
    return bands[0]


def _in_target_period(amount: float, stated: str, target: str) -> tuple[float | None, str | None]:
    """`amount` restated per the target period, and the sentence explaining it."""
    if stated == target:
        return amount, None
    factor = _PERIOD_FACTORS.get((stated, target))
    if factor is None:
        return None, (
            f"The salary is stated per {stated or 'an unnamed period'} and the target is per "
            f"{target}, so the two were not compared."
        )
    restated = amount * factor
    return restated, (
        f"Stated per {stated}; compared as {_amount(restated)} per {target} "
        f"({MONTHS_PER_YEAR:.0f} months to the year, so no rate was needed)."
    )


def _in_target_currency(
    amount: float, stated: str, preference: CompensationPreference
) -> tuple[float | None, str | None]:
    """`amount` restated in the target currency, and the sentence explaining it.

    Three outcomes, and the middle one is the finding this function exists for.
    A configured rate used to make a currency "comparable" and was then never
    applied: the posting was compared as if its own numbers were already in the
    target currency, so USD 4,000 was measured against a BRL 15,000 target and
    rejected. Either a rate converts or it does not exist; there is no third
    state where it counts for the gate and not for the arithmetic.
    """
    if stated == preference.currency.upper():
        return amount, None

    rate = next(
        (
            value
            for key, value in preference.conversion_rates.items()
            if key.upper() == stated and stated
        ),
        None,
    )
    if rate is None:
        return None, (
            f"The salary is stated in {stated or 'an unnamed currency'} and no dated "
            f"conversion rate is configured, so currencies were not compared."
        )
    if not preference.conversion_rates_dated:
        return None, (
            f"A conversion rate for {stated} is configured but "
            f"`conversion_rates_dated` is empty, so it was not applied: an "
            f"undated rate is a number nobody can check against a day."
        )

    converted = amount * rate
    return converted, (
        f"Converted at {rate} {preference.currency} per {stated}, a rate recorded "
        f"{preference.conversion_rates_dated}: {stated} {_amount(amount)} is "
        f"{preference.currency} {_amount(converted)}."
    )


def _sentence_key(quote: str | None, signal_id: str) -> str:
    """What makes two contributions the same sentence. Case and spacing aside."""
    if not quote:
        return f"__{signal_id}"
    return " ".join(quote.casefold().split())


def _weighted_component(
    component_id: str,
    component: WeightedComponent,
    observed: dict[str, ObservedSignal],
    semantic: Sequence[SemanticMatch] | None = None,
) -> ScoreComponent:
    """One phrase component: the strongest few distinct signals, each once.

    UNCONFIGURED IS NOT UNMATCHED. A component with no positive weight is not
    part of this search, so it leaves the denominator (max 0, `configured`
    false). A configured component that found nothing stays 0 of its max.

    Each candidate signal is a lexical hit (a configured phrase in the body,
    scaled by where it appeared) or a published semantic finding (scaled by its
    strength), whichever is stronger: one intent item never pays twice. Then:

    * one sentence pays for at most HALF of this component's counted signals
      (rounded up) through the person's own phrases, and for ONE semantic
      finding, strongest first. A compound bullet ("build workflow
      automation and REST API integrations") states two activities and pays
      for both; one line can never fill a component on its own, which is the
      stuffing this rule exists to stop. A literal "one sentence pays once"
      was tried first and scored the demo's canonical fit posting MODERATE
      because its bullets are compound (docs/SEMANTIC_MATCHING.md);
    * only the strongest `COUNTED_SIGNALS` pay, each worth max / N at full
      strength, so N central matches fill the component and more add nothing.

    Nothing found is dropped from view: a signal that was found and not paid is
    kept with `counted` false and the reason, so the drawer can say "already
    counted" instead of implying the posting never said it.
    """
    positive = {sid: w for sid, w in component.weights.items() if w > 0}
    if not positive:
        return ScoreComponent(
            component_id=component_id,
            label=component.label,
            points=0.0,
            max_points=0.0,
            note="Not part of your search: no phrases are configured here.",
            configured=False,
        )
    # Never more slots than the person has phrases: someone whose whole work
    # intent is "patient care" fills the component by the posting showing it,
    # instead of being capped at a quarter forever.
    counted_signals = min(COUNTED_SIGNALS.get(component_id, 3), len(positive))
    unit = component.max / counted_signals
    heaviest = max(positive.values())

    by_signal = {m.signal_id: m for m in semantic or () if m.component_id == component_id}
    # (strength, contribution, [(quote, sentence key), ...] it may pay through)
    candidates: list[tuple[float, ScoreContribution, list[tuple[str | None, str]]]] = []
    for signal_id, weight in positive.items():
        relative = weight / heaviest
        best: tuple[float, ScoreContribution, list[tuple[str | None, str]]] | None = None
        signal = observed.get(signal_id)
        if signal is not None and signal.fired:
            strength = component.prominence_multipliers[signal.prominence] * relative
            # Every sentence the phrase appeared in, in order: when the first
            # already paid for another signal, a later one may still pay.
            places: list[tuple[str | None, str]] = list(
                dict.fromkeys(
                    (hit.quote, _sentence_key(hit.quote, signal_id)) for hit in signal.hits
                )
            ) or [(signal.best_quote, _sentence_key(signal.best_quote, signal_id))]
            best = (
                strength,
                ScoreContribution(
                    signal_id=signal_id,
                    label=signal.label,
                    prominence=signal.prominence,
                    weight=weight,
                    points=unit * strength,
                    quote=signal.best_quote,
                ),
                places,
            )
        found = by_signal.get(signal_id)
        if found is not None:
            prominence = SEMANTIC_PROMINENCE.get(found.strength, Prominence.SECONDARY)
            strength = component.prominence_multipliers[prominence] * relative
            if best is None or strength > best[0]:
                label = observed[signal_id].label if signal_id in observed else signal_id
                best = (
                    strength,
                    ScoreContribution(
                        signal_id=signal_id,
                        label=label,
                        prominence=prominence,
                        weight=weight,
                        points=unit * strength,
                        quote=found.quote,
                        source="semantic",
                    ),
                    # Keyed by the SENTENCE the gate found the quote in, so two
                    # fragments of one bullet are one sentence.
                    list[tuple[str | None, str]](
                        [(found.quote, _sentence_key(found.sentence or found.quote, signal_id))]
                    ),
                )
        if best is not None:
            candidates.append(best)

    candidates.sort(key=lambda row: (-row[0], row[1].signal_id))
    contributions: list[ScoreContribution] = []
    per_sentence = -(-counted_signals // 2)
    paid_sentences: dict[str, int] = {}
    paid = 0
    total = 0.0
    for _strength, row, where in candidates:
        # A literal phrase is a statement the posting made; an interpretation
        # of a sentence is one reading of it. A sentence may pay for up to half
        # the component through phrases the person wrote, and for only one
        # semantic finding: providers cite one sentence for several items far
        # more loosely than postings state them (docs/SEMANTIC_MATCHING.md).
        limit = per_sentence if row.source == "lexical" else 1
        open_places = [(q, k) for q, k in where if paid_sentences.get(k, 0) < limit]
        if not open_places:
            contributions.append(
                _uncounted(row, "This sentence already paid for as much as one sentence can.")
            )
            continue
        if paid >= counted_signals:
            contributions.append(
                _uncounted(row, f"Already counted the {counted_signals} strongest matches here.")
            )
            continue
        quote, key = open_places[0]
        paid_sentences[key] = paid_sentences.get(key, 0) + 1
        paid += 1
        total += row.points
        contributions.append(row if quote == row.quote else replace(row, quote=quote))

    fired = {row.signal_id for row in contributions}
    missing = [signal_id for signal_id in positive if signal_id not in fired]
    note: str | None = None
    if missing and paid < counted_signals:
        # The LABEL, never the id: the drawer is read by a person.
        named = [
            observed[signal_id].label if signal_id in observed else signal_id
            for signal_id in missing
        ]
        shown = ", ".join(named[:MISSING_SIGNALS_SHOWN])
        suffix = (
            f" and {len(missing) - MISSING_SIGNALS_SHOWN} more"
            if len(missing) > MISSING_SIGNALS_SHOWN
            else ""
        )
        note = (
            f"No configured body phrase was recognized for {shown}{suffix}."
            if semantic is None
            else f"Neither a configured phrase nor a checked finding showed {shown}{suffix}."
        )

    return ScoreComponent(
        component_id=component_id,
        label=component.label,
        points=min(total, component.max),
        max_points=component.max,
        contributions=tuple(contributions),
        # Capped means what the drawer says it means: what was paid exceeded
        # the component. Signals left unpaid by the rules above are marked on
        # themselves ("already counted"), not on the component.
        capped=total > component.max,
        note=note,
    )


def _uncounted(row: ScoreContribution, reason: str) -> ScoreContribution:
    return ScoreContribution(
        signal_id=row.signal_id,
        label=row.label,
        prominence=row.prominence,
        weight=row.weight,
        points=0.0,
        quote=row.quote,
        counted=False,
        source=row.source,
        uncounted_reason=reason,
    )


def _guard_tools(work: ScoreComponent, tools: ScoreComponent) -> ScoreComponent:
    """Tools without the desired work earn at most half the tools component.

    Applies only when the person DID say what work they want (the work
    component is configured) and none of it was found. Tools alone then say
    this role uses the person's toolset for something else, which is worth
    knowing and is not a fit for the work. An unconfigured work component never
    triggers the guard: nothing was asked, so nothing is missing.
    """
    ceiling = tools.max_points * TOOLS_WITHOUT_WORK_SHARE
    if not work.configured or not tools.configured or work.points > 0:
        return tools
    if tools.points <= ceiling:
        return tools
    # The rows are scaled with the component, so what the drawer lists adds up
    # to what the component actually scored.
    factor = ceiling / tools.points
    return ScoreComponent(
        component_id=tools.component_id,
        label=tools.label,
        points=ceiling,
        max_points=tools.max_points,
        contributions=tuple(
            replace(row, points=row.points * factor) if row.counted else row
            for row in tools.contributions
        ),
        capped=False,
        note="Capped at half: none of the work you want was found in this posting.",
        configured=tools.configured,
        guarded=True,
    )


def _seniority_component(config: SearchConfig, reading: SeniorityReading) -> ScoreComponent:
    """Alignment with the level the posting asked for, when it asked for one.

    The `source` decides whether anything is paid at all. A reading the posting
    did not support is worth `unevidenced` -- zero, in the shipped configuration
    -- because these are EVIDENCE points and there is no evidence. Paying the
    MID rate for the MID fallback is how a posting that said nothing about its
    level used to collect nine of its points from our own default.
    """
    component = config.scoring.components.seniority
    if reading.is_evidence:
        points = _seniority_points(config, reading.value)
        label = f"The posting states a {reading.value.value} role"
    else:
        points = component.unevidenced
        label = "The posting did not state a level"
    return ScoreComponent(
        component_id="seniority",
        label=component.label,
        points=points,
        max_points=component.max,
        contributions=(
            ScoreContribution(
                signal_id=f"seniority:{reading.value.value}",
                label=label,
                prominence=Prominence.SECONDARY,
                weight=points,
                points=points,
                quote=reading.evidence,
            ),
        ),
        note=None if reading.is_evidence else reading.sentence,
    )


def _seniority_points(config: SearchConfig, level: Seniority) -> float:
    """Evidence points for a stated level, from what the person prefers.

    With preferred levels stated: a preferred level earns the full component,
    one step away on the ladder earns half, a level the person excluded earns
    nothing (it is also hidden from Discover, which is a visibility choice and
    never an eligibility failure), and any other level earns a fifth. Without
    preferred levels, the configured points table decides, as it always did.
    """
    component = config.scoring.components.seniority
    preference = config.preferences.seniority
    preferred = set(preference.preferred)
    if not preferred:
        return component.points_for(level)
    if level in preferred:
        return component.max
    if level in set(preference.excluded):
        return 0.0
    if level in SENIORITY_LADDER:
        at = SENIORITY_LADDER.index(level)
        steps = [abs(at - SENIORITY_LADDER.index(p)) for p in preferred if p in SENIORITY_LADDER]
        if steps and min(steps) == 1:
            return component.max * SENIORITY_ADJACENT_SHARE
    return component.max * SENIORITY_OTHER_SHARE


#: Engagement spellings a configuration may use that are not enum members.
#:
#: One entry, and it is not a convenience. Every `preferences.contract` file
#: this project has ever shipped -- the worked example, and therefore the
#: owner's private copy derived from it -- asks for `FULL_TIME_EMPLOYEE`, which
#: has never been a member of anything. The scorer compared it against
#: `JobFacts.employment_type`, whose vocabulary is `FULL_TIME` / `PART_TIME` /
#: `CONTRACT`, so the two sets never intersected and `contract_preferred` could
#: not fire for any posting under any configuration. Measured 2026-09-09: the
#: overlap between what the configuration asks for and what the scorer could
#: answer was EMPTY.
#:
#: Renaming the value in the committed file would leave every private file
#: still broken and silent. Recording the historical spelling repairs both, and
#: says out loud that it is historical.
_CONTRACT_ALIASES: dict[str, EmploymentRelationship] = {
    "FULL_TIME_EMPLOYEE": EmploymentRelationship.EMPLOYEE,
    "FULL_TIME": EmploymentRelationship.EMPLOYEE,
    "PERMANENT": EmploymentRelationship.EMPLOYEE,
    "CONTRACT": EmploymentRelationship.CONTRACTOR_B2B,
    "CONTRACTOR": EmploymentRelationship.CONTRACTOR_B2B,
}


def _contract_vocabulary(
    values: Sequence[str],
) -> tuple[frozenset[EmploymentRelationship], tuple[str, ...]]:
    """A configured contract preference as engagements, and what could not be read.

    Both halves are returned because the second one used to be invisible. A
    value this system cannot resolve is a question asked in a language nobody
    speaks, and the honest response is to answer nothing AND say so -- which is
    what the caller does with the leftovers.
    """
    resolved: set[EmploymentRelationship] = set()
    unreadable: list[str] = []
    for value in values:
        key = value.strip().upper()
        if not key:
            continue
        alias = _CONTRACT_ALIASES.get(key)
        if alias is not None:
            resolved.add(alias)
            continue
        try:
            resolved.add(EmploymentRelationship(key))
        except ValueError:
            unreadable.append(key)
    return frozenset(resolved), tuple(unreadable)


def _compensation_component(
    config: SearchConfig, job_facts: JobFacts, employment: EmploymentReading
) -> ScoreComponent:
    component = config.scoring.components.compensation_contract
    preference = config.preferences.compensation
    contributions: list[ScoreContribution] = []
    notes: list[str] = []

    band = _select_band(job_facts, preference.currency.upper())
    offered = band.max_value if band.max_value is not None else band.min_value

    if offered is None:
        salary_id, salary_points = "salary_unknown", component.salary_unknown
    else:
        # Period first, then currency: restating a yearly figure as a monthly
        # one is exact and free, and doing it before the rate keeps the note
        # readable as one chain of arithmetic rather than two.
        per_period, period_note = _in_target_period(
            offered, (band.period or "").upper(), preference.period.upper()
        )
        if period_note:
            notes.append(period_note)

        converted: float | None = None
        if per_period is not None:
            converted, currency_note = _in_target_currency(
                per_period, (band.currency or "").upper(), preference
            )
            if currency_note:
                notes.append(currency_note)

        if converted is None:
            salary_id, salary_points = "salary_unknown", component.salary_unknown
        else:
            meets = converted >= preference.target_monthly_amount
            salary_id = "salary_meets_target" if meets else "salary_below_target"
            salary_points = (
                component.salary_meets_target if meets else component.salary_below_target
            )

    contributions.append(
        ScoreContribution(
            signal_id=salary_id,
            label="Compensation preference",
            prominence=Prominence.INCIDENTAL,
            weight=salary_points,
            points=salary_points,
        )
    )

    preferred, preferred_unknown = _contract_vocabulary(config.preferences.contract.preferred)
    unwanted, unwanted_unknown = _contract_vocabulary(config.preferences.contract.unwanted)
    unreadable = preferred_unknown + unwanted_unknown
    if unreadable:
        # A PREFERENCE NOBODY CAN ANSWER SAYS SO, on the card, rather than
        # quietly scoring every posting `unknown` forever. That is what the old
        # code did for the entire life of this configuration.
        notes.append(
            "The contract preference names "
            + ", ".join(sorted(unreadable))
            + ", which is not an engagement this system recognises, so it was not applied."
        )

    engaged = employment.relationship
    if engaged is EmploymentRelationship.UNRESOLVED:
        contract_id, contract_points = "contract_unknown", component.contract_unknown
    elif engaged in preferred:
        contract_id, contract_points = "contract_preferred", component.contract_preferred
    elif engaged in unwanted:
        contract_id, contract_points = "contract_unwanted", component.contract_unwanted
    else:
        contract_id, contract_points = "contract_unknown", component.contract_unknown

    contributions.append(
        ScoreContribution(
            signal_id=contract_id,
            label="Contract preference",
            prominence=Prominence.INCIDENTAL,
            weight=contract_points,
            points=contract_points,
            # The quote when the posting SAID it in words, and None when the
            # employer picked it from the board's dropdown. A structured
            # reading is explicit and has nothing to quote; ADR-0002 means an
            # invented quote would not be evidence, it would be a fabrication.
            quote=employment.evidence,
        )
    )

    total = salary_points + contract_points
    return ScoreComponent(
        component_id="compensation_contract",
        label=component.label,
        points=min(total, component.max),
        max_points=component.max,
        contributions=tuple(contributions),
        capped=total > component.max,
        note=" ".join(notes) or None,
    )


def _work_model_component(config: SearchConfig, job_facts: JobFacts) -> ScoreComponent | None:
    """Her work-model preference, or None when she has stated none.

    A posting that did not say how the work is done earns the neutral points:
    nothing was stated, so nothing is either rewarded or held against it.
    """
    remote = config.preferences.remote
    preferred = {model.upper() for model in remote.accepted_work_models}
    avoided = {model.upper() for model in remote.avoided_work_models}
    avoided |= {model.upper() for model in remote.excluded_work_models}
    if not preferred and not avoided:
        return None
    component = config.scoring.components.work_model
    stated = read_work_model(job_facts)
    if stated is None:
        signal_id, points = "work_model_unknown", component.neutral
    elif stated in avoided:
        signal_id, points = "work_model_avoided", component.avoided
    elif stated in preferred:
        signal_id, points = "work_model_preferred", component.preferred
    else:
        signal_id, points = "work_model_neutral", component.neutral
    return ScoreComponent(
        component_id="work_model",
        label=component.label,
        points=min(points, component.max),
        max_points=component.max,
        contributions=(
            ScoreContribution(
                signal_id=signal_id,
                label="Work model preference",
                prominence=Prominence.INCIDENTAL,
                weight=points,
                points=points,
            ),
        ),
        capped=points > component.max,
        note=None,
    )


def score_components(
    config: SearchConfig,
    *,
    observed_body: dict[str, ObservedSignal],
    seniority: SeniorityReading,
    employment: EmploymentReading,
    job_facts: JobFacts,
    semantic: SemanticEvidence | None = None,
) -> tuple[ScoreComponent, ...]:
    """The five components, in the order the configuration declares them.

    Five, not six. `role_family` used to sit at the front and pay up to 25 for
    the TITLE resembling one the search was built around, which is the one thing
    "search for the work, not the title" forbids. It is gone, and the taxonomy
    that fed it still runs for every other purpose it has.

    `observed_body` is deliberately named. It must be the BODY-ONLY view from
    `lexicon.body_only`: a title hit reaching a weighted component is the same
    defect as `role_family`, wearing the clothes of ordinary evidence.

    The seniority READING arrives already made, because the engine needs the
    same one for the confidence items and the card, and two readings of one
    posting drifting apart is a class of bug worth designing out.
    """
    components = config.scoring.components
    found = semantic.matches if semantic is not None else None
    work = _weighted_component(
        "responsibilities", components.responsibilities, observed_body, found
    )
    tools = _guard_tools(
        work,
        _weighted_component("technologies", components.technologies, observed_body, found),
    )
    fixed = (
        work,
        tools,
        _weighted_component(
            "automation_integration", components.automation_integration, observed_body, found
        ),
        _seniority_component(config, seniority),
        _compensation_component(config, job_facts, employment),
    )
    # A sixth, only when she stated a way-of-working preference.
    work_model = _work_model_component(config, job_facts)
    return fixed + ((work_model,) if work_model else ())


def soft_penalties(
    config: SearchConfig,
    observed: dict[str, ObservedSignal],
) -> tuple[Penalty, ...]:
    """Deprioritisation, proportional to prominence. Never hides a job.

    Every row here comes from an observed SIGNAL, which is a phrase found in the
    posting with the sentence it sat in. There used to be one exception:
    `salesforce_centred` fired on the words in the TITLE alone and subtracted 15
    points, so `Salesforce Administrator` scored 30 where the identical body
    under a plain title scored 48. A penalty for what a role is CALLED is a
    title deciding fit as surely as a bonus for it, and it is gone. Salesforce
    as a tool still reaches the score through `tool_stack`, and a Salesforce-
    centred BODY still reaches it through the ordinary weighted signals.
    """
    penalties = config.scoring.soft_penalties
    rows: list[Penalty] = []

    for signal_id, weight in penalties.weights.items():
        signal = observed.get(signal_id)
        if signal is None or not signal.fired:
            continue
        multiplier = penalties.prominence_multipliers[signal.prominence]
        # A penalty is a MAGNITUDE that is subtracted. The loader already
        # normalises a legacy negative weight; `abs` is the last line of
        # defence, because a negative here used to ADD points to the score.
        magnitude = abs(weight)
        rows.append(
            Penalty(
                signal_id=signal_id,
                label=signal.label,
                prominence=signal.prominence,
                weight=magnitude,
                points=magnitude * multiplier,
                quote=signal.best_quote,
            )
        )

    rows.sort(key=lambda row: row.points, reverse=True)
    return tuple(rows)


def match_score_from(components: tuple[ScoreComponent, ...], penalties: tuple[Penalty, ...]) -> int:
    """Components minus penalties, as a percentage of what was achievable.

    The denominator is the sum of the components' own maxima rather than a
    literal 100, and that is not a cosmetic choice. Removing `role_family` took
    25 points off the top; without a real denominator every threshold, band edge
    and stored score in the product would quietly have started meaning something
    else, while still being printed out of 100. Scaling keeps "72" the same
    claim it was: this posting earned 72% of the fit it could have earned.

    The relative weight of every surviving component is untouched. Penalties are
    subtracted before scaling, so a penalty still costs its configured points on
    the same scale the components are measured in.
    """
    achievable = sum(c.max_points for c in components)
    if achievable <= 0:
        return 0
    earned = sum(c.points for c in components) - sum(p.points for p in penalties)
    return round(min(100.0, max(0.0, 100.0 * earned / achievable)))


def measurable_items(
    *,
    description: str,
    location_raw: str,
    employment_type: str,
    salary_stated: bool,
    geography_resolved: bool,
    seniority: SeniorityReading,
    posted_at: str,
) -> dict[str, tuple[bool, str, str]]:
    """Every confidence item this matcher can measure: awarded, and both notes.

    Hoisted out of `data_confidence` and given plain arguments rather than a
    `JobFacts` for one reason: it can then be called at import time with empty
    values, which is what makes `MEASURABLE_CONFIDENCE_ITEMS` below the dict's
    own key set BY CONSTRUCTION rather than a second hand-maintained list.

    That matters because the ids live in two places -- here and in
    `config/search.worked-example.yaml` -- and nothing used to check that they agreed.
    A key misspelled on this side silently produced `awarded=False` and the note
    "<label> is not measurable by the deterministic matcher", forever, on every
    posting, with the item still counted as reported. The test that looked like
    it covered this built its expectation FROM the config, so it passed with any
    spelling on the Python side; renaming `seniority_determinable` broke nothing.
    """
    return {
        "description_present": (
            bool(description.strip()),
            "The full description text was available.",
            "No description text was stored for this posting.",
        ),
        "location_stated": (
            bool(location_raw.strip()),
            "The posting states a location.",
            "The posting does not state a location.",
        ),
        "hiring_scope_explicit": (
            geography_resolved,
            "The posting states where it hires.",
            "The posting never says where it hires, so the geography gate is unresolved.",
        ),
        # WHAT THE BOARD DECLARED, and the sentences say so.
        #
        # This reads `JobFacts.employment_type`, which is filled in only from a
        # provider's own structured field -- Ashby's `employmentType`, Lever's
        # `categories.commitment` -- through that provider's field map. It is
        # NOT the engagement the body states. `match/employment.py` reads that
        # separately and independently, and a Brazilian posting whose text says
        # `Contratacao CLT` has a regime here while this item is unawarded.
        #
        # The old wording claimed otherwise: "The posting does not say what
        # kind of engagement this is" was printed about a posting that says
        # exactly that, in the market this product exists to serve. Whether the
        # ITEM should also count a regime read from prose is a scoring question
        # and is not settled by renaming a sentence; what is settled is that
        # the sentence now describes what is measured.
        "employment_type_known": (
            bool(employment_type.strip()),
            "The job board states what kind of engagement this is.",
            "The job board does not state what kind of engagement this is.",
        ),
        "salary_known": (
            salary_stated,
            "The posting states compensation.",
            "The posting states no compensation.",
        ),
        "seniority_determinable": (
            seniority.is_evidence,
            f"The body reads as {seniority.value}.",
            "Nothing in the body indicates a seniority level.",
        ),
        "posted_date_known": (
            bool(posted_at.strip()),
            "The posting date is known.",
            "The posting date is unknown, so freshness cannot be judged.",
        ),
    }


#: The item ids `measurable_items` answers, derived from the function itself so
#: the two cannot drift. `tests/unit/test_match_score.py` asserts this is a
#: subset of `confidence.components` in the configuration, which is the check
#: that was missing: an id here that no configured item uses is dead code, and
#: an id there that this cannot measure reads as an unknown on every posting.
MEASURABLE_CONFIDENCE_ITEMS: frozenset[str] = frozenset(
    measurable_items(
        description="",
        location_raw="",
        employment_type="",
        salary_stated=False,
        geography_resolved=False,
        seniority=DEFAULT_SENIORITY,
        posted_at="",
    )
)

#: The one configured item measured in the loop below rather than by
#: `measurable_items`, because it is the only one whose rule is a NUMBER in the
#: configuration (`min_chars`) rather than the presence of a field.
LENGTH_CONFIDENCE_ITEM = "description_substantial"


def data_confidence(
    config: SearchConfig,
    job_facts: JobFacts,
    seniority: SeniorityReading,
    *,
    geography_resolved: bool = False,
) -> tuple[int, tuple[ConfidenceItem, ...], tuple[str, ...]]:
    """How much of this posting we actually read.

    Deliberately not multiplied by the match score (ADR-0004). A thin posting
    for exactly the right work keeps its high score and reports low confidence,
    and the interface shows both.

    The seniority READING is passed in rather than made here. It used to be
    computed a second time from the same body, which was one posting answering
    the same question twice, and the `seniority_determinable` item now asks
    about the reading's SOURCE rather than about its value.
    """
    description = job_facts.description or ""

    awarded_by_item = measurable_items(
        description=description,
        location_raw=job_facts.location_raw or "",
        employment_type=job_facts.employment_type or "",
        salary_stated=any(band.has_amounts for band in job_facts.salary_bands),
        geography_resolved=geography_resolved,
        seniority=seniority,
        posted_at=job_facts.posted_at or "",
    )

    items: list[ConfidenceItem] = []
    unknowns: list[str] = []
    total = 0

    for item_id, item in config.confidence.components.items():
        if item_id == LENGTH_CONFIDENCE_ITEM:
            minimum = item.min_chars or 0
            awarded = len(description) >= minimum
            note = (
                f"The description is {len(description)} characters, at or above the "
                f"{minimum}-character bar."
                if awarded
                else f"The description is {len(description)} characters, below the "
                f"{minimum}-character bar, so parts of the role may simply be missing."
            )
        else:
            known = awarded_by_item.get(item_id)
            if known is None:
                # A configured item this matcher cannot measure is reported
                # un-awarded rather than quietly skipped.
                awarded, note = (
                    False,
                    f"{item.label} is not measurable by the deterministic matcher.",
                )
            else:
                awarded, yes, no = known
                note = yes if awarded else no

        items.append(
            ConfidenceItem(
                item_id=item_id, label=item.label, points=item.points, awarded=awarded, note=note
            )
        )
        if awarded:
            total += item.points
        else:
            unknowns.append(note)

    return min(100, max(0, total)), tuple(items), tuple(unknowns)


def fit_band_for(score: int, config: SearchConfig) -> FitBand:
    bands = config.thresholds.fit_bands
    if score >= bands.get("STRONG", 75):
        return FitBand.STRONG
    if score >= bands.get("GOOD", 55):
        return FitBand.GOOD
    if score >= bands.get("MODERATE", 35):
        return FitBand.MODERATE
    return FitBand.WEAK


def confidence_band_for(value: int, config: SearchConfig) -> AnalysisConfidence:
    bands = config.thresholds.confidence_bands
    if value >= bands.get("HIGH", 70):
        return AnalysisConfidence.HIGH
    if value >= bands.get("MEDIUM", 45):
        return AnalysisConfidence.MEDIUM
    return AnalysisConfidence.LOW

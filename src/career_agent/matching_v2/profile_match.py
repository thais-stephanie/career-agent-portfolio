"""What one posting is for one profile, from the posting's shared facts.

The person's phrases are matched against the canonical tokens of
`JobFeatures` (no new scan of the advert), in each sentence's context:

* a phrase in a duty, a requirement or a tool list is evidence (MATCH);
* in "About us" or the benefits it proves nothing about the work and is
  set aside (EXCLUDED_CONTEXT), with its quote;
* words of the phrase that are present but apart, in a sentence of no
  known purpose, have more than one plausible reading: UNCERTAIN, worth
  nothing, shown (ADR-0037).

The matches become the same `ObservedSignal` values `match.lexicon.observe`
produces, and the EXISTING `match_job` turns them into gates, screening and
the score. A1 and B1 (ADR-0036) therefore hold by construction.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from career_agent.config.search_config import SearchConfig
from career_agent.domain.matching import MatchResult, ObservedSignal, SignalHit
from career_agent.match.engine import JobFacts, match_job, read_shared
from career_agent.match.lexicon import (
    context_allows,
    context_rule_for,
    is_negated_before,
    prominence_of,
)
from career_agent.match.phrase_forms import EXTRA_TOKENS
from career_agent.match.text import PRIMARY_SECTION, SECONDARY_SECTION, sentence_at
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2.context import NOT_WORK, Context
from career_agent.matching_v2.features import JobFeatures, job_inputs
from career_agent.matching_v2.language import AUTOMATIC, Relation, concept_readings

MATCH = "MATCH"
UNCERTAIN = "UNCERTAIN"
EXCLUDED_CONTEXT = "EXCLUDED_CONTEXT"

#: Contexts in which a phrase whose words are present but apart is UNCERTAIN
#: rather than a match. Only OTHER (a sentence of no known purpose): in a duty
#: or a requirement the production reader (`phrase_forms`, readers-9) grants
#: words up to three tokens apart, and v2 keeps that. A stricter rule was
#: measured on the synthetic corpus and is reported, not adopted (ADR-0037).
UNCERTAIN_WHEN_APART = frozenset({Context.OTHER})

_SECTION_OF = {
    Context.RESPONSIBILITY: PRIMARY_SECTION,
    Context.REQUIREMENT: SECONDARY_SECTION,
    Context.TOOL: SECONDARY_SECTION,
}


@dataclass(frozen=True, slots=True)
class Evidence:
    signal_id: str
    pattern: str
    state: str
    relation: str
    context: str
    quote: str
    start: int
    end: int


@dataclass
class ProfileJobMatch:
    job_id: str
    result: MatchResult
    evidence: list[Evidence] = field(default_factory=list)
    #: profile component -> stamp
    stamps: dict[str, str] = field(default_factory=dict)

    @property
    def uncertain(self) -> list[Evidence]:
        return [e for e in self.evidence if e.state == UNCERTAIN]


def profile_inputs(config: SearchConfig) -> dict[str, str]:
    """One digest per preference section, so a component reads only its own."""
    dumped = config.model_dump(mode="json")
    prefs = dumped["preferences"]
    return {
        "profile.lexicon": comp.digest(
            [dumped[k] for k in ("lexicon", "screening", "taxonomy", "ambiguous_titles")]
            + [dumped["negation"], dumped["prominence"]]
        ),
        "profile.eligibility": comp.digest([dumped["eligibility"], dumped["negation"]]),
        "profile.salary": comp.digest(prefs["compensation"]),
        "profile.seniority": comp.digest(prefs["seniority"]),
        "profile.contract": comp.digest(prefs["contract"]),
        "profile.work_model": comp.digest([prefs["remote"], prefs["travel"]]),
        "profile.weights": comp.digest(
            [dumped["scoring"], dumped["thresholds"], dumped["confidence"], prefs["recency"]]
        ),
    }


@lru_cache(maxsize=8192)
def _readings(pattern: str) -> tuple[tuple[str, ...], ...]:
    return concept_readings(pattern)


def _find(toks: list[list[Any]], reading: tuple[str, ...]) -> list[tuple[int, int, int, Relation]]:
    """(start, end, gap, strongest relation) where every root of `reading`
    sits within `len + EXTRA_TOKENS` tokens. Same rule as `phrase_forms`."""
    needed = set(reading)
    width = len(reading) + EXTRA_TOKENS
    found = []
    i = 0
    while i < len(toks):
        if toks[i][0] not in needed:
            i += 1
            continue
        missing = set(needed)
        last = i
        rel = Relation.IDENTICAL
        for j in range(i, min(len(toks), i + width)):
            if toks[j][0] in missing:
                missing.discard(toks[j][0])
                last = j
                candidate = Relation(toks[j][3])
                if candidate is not Relation.IDENTICAL:
                    rel = candidate
                if not missing:
                    break
        if missing:
            i += 1
            continue
        gap = (last - i + 1) - len(reading)
        found.append((int(toks[i][1]), int(toks[last][2]), gap, rel))
        i = last + 1
    return found


def observe_from_features(
    config: SearchConfig, features: JobFeatures, job: JobFacts
) -> tuple[dict[str, ObservedSignal], list[Evidence]]:
    """`match.lexicon.observe`, answered from shared facts."""
    work = features.payloads["work_facts"]
    description = job.description
    folded = features.shared.description_field if features.shared is not None else None
    units: list[tuple[str, Context, list[list[Any]]]] = [("title", Context.TITLE, work["title"])]
    units += [("description", Context(ctx), toks) for _s, _e, ctx, toks in work["sentences"]]
    observed: dict[str, ObservedSignal] = {}
    evidence: list[Evidence] = []
    for signal_id, signal_cfg in config.lexicon.items():
        live: list[SignalHit] = []
        negated: list[SignalHit] = []
        for pattern in signal_cfg.patterns:
            for reading in _readings(pattern):
                for field_name, context, toks in units:
                    for start, end, gap, relation in _find(toks, reading):
                        if relation not in AUTOMATIC:
                            continue
                        text = job.title if field_name == "title" else description
                        quote = sentence_at(text, start, end)
                        if context in NOT_WORK:
                            state = EXCLUDED_CONTEXT
                        elif gap > 0 and context in UNCERTAIN_WHEN_APART:
                            state = UNCERTAIN
                        else:
                            state = MATCH
                        rule = context_rule_for(signal_cfg, pattern)
                        if (
                            state == MATCH
                            and rule
                            and not context_allows(rule, quote, text[start:end])
                        ):
                            continue
                        evidence.append(
                            Evidence(
                                signal_id, pattern, state, relation.value, context.value,
                                quote, start, end,
                            )
                        )  # fmt: skip
                        if state != MATCH:
                            continue
                        is_negated = False
                        if (
                            signal_cfg.negation_sensitive
                            and field_name == "description"
                            and folded is not None
                        ):
                            at = bisect_left(folded.offsets, start)
                            is_negated = is_negated_before(folded, at, config.negation)
                        hit = SignalHit(
                            signal_id=signal_id,
                            label=signal_cfg.label,
                            field_name=field_name,
                            pattern=pattern,
                            quote=quote,
                            char_start=start,
                            char_end=end,
                            negated=is_negated,
                            section=_SECTION_OF.get(context),
                        )
                        (negated if is_negated else live).append(hit)
        # One hit per place: two patterns of one signal finding the same words.
        live = list({(h.field_name, h.char_start): h for h in live}.values())
        observed[signal_id] = ObservedSignal(
            signal_id=signal_id,
            label=signal_cfg.label,
            responsibility=signal_cfg.responsibility,
            prominence=prominence_of(config, tuple(live)),
            hits=tuple(sorted(live, key=lambda h: (h.field_name != "title", h.char_start))),
            negated_hits=tuple(negated),
        )
    # One line per place and state, whichever pattern or reading found it.
    unique = {(e.signal_id, e.start, e.end, e.state): e for e in evidence}
    return observed, list(unique.values())


def match(
    config: SearchConfig,
    job: JobFacts,
    features: JobFeatures,
    *,
    computed_at: str = "frozen",
) -> ProfileJobMatch:
    """One profile's answer for one posting, over its shared facts."""
    if features.shared is None:
        shared = read_shared(
            job, config.prominence.primary_headings, config.prominence.secondary_headings
        )
        features = JobFeatures(
            features.job_id, features.content_hash, features.payloads, features.stamps,
            features.reads, shared,
        )  # fmt: skip
    observed, evidence = observe_from_features(config, features, job)
    result = match_job(
        config, job, computed_at=computed_at, shared=features.shared, observed=observed
    )
    headings = (
        tuple(config.prominence.primary_headings),
        tuple(config.prominence.secondary_headings),
    )
    inputs = {**job_inputs(job, features.content_hash, headings), **profile_inputs(config)}
    stamps = {name: comp.stamp(name, inputs) for name in comp.PROFILE_COMPONENTS}
    return ProfileJobMatch(str(job.job_id or ""), result, evidence, stamps)

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

import re
from bisect import bisect_left
from dataclasses import dataclass, field
from functools import lru_cache

from career_agent.config.search_config import SearchConfig
from career_agent.domain.matching import MatchResult, ObservedSignal, SignalHit
from career_agent.match.engine import JobFacts, SemanticEvidence, match_job, read_shared
from career_agent.match.gates import negation_key
from career_agent.match.lexicon import (
    context_allows,
    context_rule_for,
    is_negated_before,
    prominence_of,
)
from career_agent.match.phrase_forms import EXTRA_TOKENS, STOPWORDS
from career_agent.match.text import (
    PRIMARY_SECTION,
    SECONDARY_SECTION,
    fold,
    section_kind_at,
    sentence_at,
)
from career_agent.matching_v2 import components as comp
from career_agent.matching_v2 import evidence as ev
from career_agent.matching_v2 import evidence_policy as policy_mod
from career_agent.matching_v2 import structure
from career_agent.matching_v2.context import NOT_WORK, Context, Sentence
from career_agent.matching_v2.features import (
    JobFeatures,
    job_inputs,
    shared_from_facts,
    work_units,
)
from career_agent.matching_v2.language import (
    AUTOMATIC,
    Relation,
    Token,
    concept_readings,
    load_table,
)

_WORD = re.compile(r"[0-9a-z]+")

MATCH = "MATCH"
UNCERTAIN = "UNCERTAIN"
EXCLUDED_CONTEXT = "EXCLUDED_CONTEXT"

#: Contexts in which a phrase whose words are present but apart is UNCERTAIN
#: rather than a match. Only OTHER (a sentence of no known purpose): in a duty
#: or a requirement the production reader (`phrase_forms`, readers-9) grants
#: words up to three tokens apart, and v2 keeps that. A stricter rule was
#: measured on the synthetic corpus and is reported, not adopted (ADR-0037).
UNCERTAIN_WHEN_APART: frozenset[Context] = frozenset()

#: Why words present and close together were not taken as the preference.
APART_IN_UNKNOWN_CONTEXT = "APART_IN_UNKNOWN_CONTEXT"


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
    #: For UNCERTAIN: the cause (`structure` or APART_IN_UNKNOWN_CONTEXT).
    reason: str | None = None
    #: What the evidence means (`EvidenceFrame.code()`) and what this
    #: profile's policy makes it worth; None for the title.
    frame: str | None = None
    weight: float | None = None


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


#: `profile_inputs` per configuration object: a pass asks it once per
#: posting, and a configuration does not change while it is held.
_INPUTS: dict[int, tuple[SearchConfig, dict[str, str]]] = {}


def profile_inputs(config: SearchConfig) -> dict[str, str]:
    """One digest per preference section, so a component reads only its own."""
    held = _INPUTS.get(id(config))
    if held is not None and held[0] is config:
        return held[1]
    if len(_INPUTS) > 64:
        _INPUTS.clear()
    _INPUTS[id(config)] = (config, _profile_inputs(config))
    return _INPUTS[id(config)][1]


def _profile_inputs(config: SearchConfig) -> dict[str, str]:
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
        "profile.evidence_policy": comp.digest(
            policy_mod.digest_view(policy_mod.policy_for(config))
        ),
    }


def _stamps(inputs: tuple[tuple[str, str], ...]) -> dict[str, str]:
    """Profile components' stamps. Cached by the inputs AND the registry's
    versions, so a version bump is never answered from the cache."""
    versions = tuple(sorted((n, c.version) for n, c in comp.REGISTRY.items()))
    return _stamps_for(inputs, versions)


@lru_cache(maxsize=4096)
def _stamps_for(
    inputs: tuple[tuple[str, str], ...], versions: tuple[tuple[str, str], ...]
) -> dict[str, str]:
    del versions  # part of the cache key only
    given = dict(inputs)
    return {name: comp.stamp(name, given) for name in comp.PROFILE_COMPONENTS}


@lru_cache(maxsize=8192)
def _readings(pattern: str) -> tuple[tuple[str, ...], ...]:
    return concept_readings(pattern)


@lru_cache(maxsize=8192)
def _single_word(pattern: str) -> str | None:
    """The one content word of a one-word preference, folded, else None."""
    words = [w for w in _WORD.findall(fold(pattern)[0]) if w not in STOPWORDS]
    return words[0] if len(words) == 1 else None


_INFLECTIONS = ("s", "es", "d", "ed", "ing")


def _inflection_of(word: str | None, single: str) -> bool:
    """`designed` of `design`; not of a light verb (`supporting`)."""
    if not word or word == single or single in ev.load().light_verbs:
        return False
    rest = word[len(single) :] if word.startswith(single) else None
    if rest in _INFLECTIONS:
        return True
    # `designing` of `design`, `managing` of `manage`: a final `e` dropped.
    return (
        single.endswith("e")
        and word.startswith(single[:-1])
        and word[len(single) - 1 :] in ("ing", "ed")
    )


def _find(
    toks: list[Token], reading: tuple[str, ...], single: str | None = None
) -> list[tuple[list[Token], int, Relation]]:
    """(matched tokens, gap, strongest relation) where every root of `reading`
    sits within `len + EXTRA_TOKENS` tokens. Same rule as `phrase_forms`,
    which reads only phrases of two words or more: a ONE-word preference
    matches its own word or a listed translation, never a shared root."""
    if len(reading) == 1 and single is not None:
        surface = load_table().surface
        return [
            ([t], 0, t.relation)
            for t in toks
            if t.root == reading[0]
            and (
                t.word == single
                or _inflection_of(t.word, single)
                or t.word in surface
                or t.relation in (Relation.EXACT_EQUIVALENT, Relation.FIXED_EXPRESSION)
            )
        ]
    needed = set(reading)
    width = len(reading) + EXTRA_TOKENS
    # Every window that holds the reading, then the tightest first and no two
    # sharing a token (2026-10-15): read left to right and greedily, a loose
    # window (`Management, Customer Experience, Inventory`) consumed the words
    # of the phrase written whole right after it (`Inventory Management`).
    candidates = []
    for i, tok in enumerate(toks):
        if tok.root not in needed:
            continue
        missing = set(needed)
        used: list[Token] = []
        last = i
        rel = Relation.IDENTICAL
        for j in range(i, min(len(toks), i + width)):
            if toks[j].root in missing:
                missing.discard(toks[j].root)
                used.append(toks[j])
                last = j
                if toks[j].relation is not Relation.IDENTICAL:
                    rel = toks[j].relation
                if not missing:
                    break
        if not missing:
            candidates.append(((last - i + 1) - len(reading), i, last, used, rel))
    taken: set[int] = set()
    kept = []
    for gap, i, last, used, rel in sorted(candidates, key=lambda c: (c[0], c[1])):
        span = set(range(i, last + 1))
        if span & taken:
            continue
        taken |= span
        kept.append((i, (used, gap, rel)))
    return [window for _, window in sorted(kept, key=lambda k: k[0])]


def observe_from_features(
    config: SearchConfig, features: JobFeatures, job: JobFacts
) -> tuple[dict[str, ObservedSignal], list[Evidence]]:
    """`match.lexicon.observe`, answered from shared facts.

    The SECTION a hit carries (what makes it a core duty in the score) is the
    one the configured headings give, exactly as in production. v2's own
    headings and sentence cues decide only the CONTEXT: whether a phrase is
    evidence at all, set aside (About us, benefits) or uncertain. A heading
    is evidence of what a sentence is for; on its own it never promotes a
    secondary activity to a core one (owner's decision, 2026-10-14)."""
    units = work_units(job, features)
    unit_roots = [frozenset(t.root for t in toks) for _, _, _, toks in units]
    ends = {s.start: s.end for s in features.sentences}
    description = job.description
    shared = features.shared
    folded = shared.description_field if shared is not None else None
    spans = shared.sections if shared is not None else ()
    observed: dict[str, ObservedSignal] = {}
    evidence: list[Evidence] = []
    policy = policy_mod.policy_for(config)
    notes = features.notes
    for signal_id, signal_cfg in config.lexicon.items():
        live: list[SignalHit] = []
        negated: list[SignalHit] = []
        for pattern in signal_cfg.patterns:
            single = _single_word(pattern)
            for reading in _readings(pattern):
                needed = set(reading)
                for (field_name, context, sentence_start, toks), roots in zip(
                    units, unit_roots, strict=True
                ):
                    if not needed <= roots:
                        continue  # `_find` would find nothing: skipped, same answer
                    for used, gap, relation in _find(toks, reading, single):
                        if relation not in AUTOMATIC:
                            continue
                        start = min(t.start for t in used)
                        end = max(t.end for t in used)
                        text = job.title if field_name == "title" else description
                        quote = sentence_at(text, start, end)
                        sentence_end = ends.get(sentence_start, len(description))
                        state, reason, frame, weight = _classify(
                            description, field_name, context, sentence_start,
                            sentence_end, used, gap, pattern,
                            features.language, one_word=single is not None,
                            concept_id=f"{signal_id}:{pattern}", policy=policy,
                            notes=notes.within(sentence_start, sentence_end)
                            if field_name == "description" else [],
                        )  # fmt: skip
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
                                quote, start, end, reason,
                                None if frame is None else frame.code(), weight,
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
                        section = (
                            section_kind_at(spans, start) if field_name == "description" else None
                        )
                        # Weak evidence (a degree, a tool, familiarity) is never a
                        # core duty in the score: the policy's weight reaches A1+B1
                        # through the channel they already read.
                        if (
                            section == PRIMARY_SECTION
                            and weight is not None
                            and weight < policy["core_at"]
                        ):
                            section = SECONDARY_SECTION
                        hit = SignalHit(
                            signal_id=signal_id,
                            label=signal_cfg.label,
                            field_name=field_name,
                            pattern=pattern,
                            quote=quote,
                            char_start=start,
                            char_end=end,
                            negated=is_negated,
                            section=section,
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


def _classify(
    description: str,
    field_name: str,
    context: Context,
    sentence_start: int,
    sentence_end: int,
    used: list[Token],
    gap: int,
    pattern: str,
    language: str,
    *,
    one_word: bool = False,
    concept_id: str = "",
    policy: dict | None = None,
    notes: list | None = None,
) -> tuple[str, str | None, ev.EvidenceFrame | None, float | None]:
    """MATCH, UNCERTAIN (with its cause) or EXCLUDED_CONTEXT, the evidence
    frame and its weight under the profile's policy (2026-10-16).

    The structural rules (`structure.check`, `structure.one_word`) still find
    their causes; a cause no longer ends the reading, it sets one dimension
    of the frame (a team is an owner, a school level a sense, a qualification
    a kind of evidence), and the policy decides what the frame is worth."""
    policy = policy or policy_mod.DEFAULT
    if field_name != "description":
        return MATCH, None, None, None
    start, end = min(t.start for t in used), max(t.end for t in used)
    sentence = Sentence(sentence_start, sentence_end, context)
    if context in NOT_WORK:
        frame = ev.frame(description, sentence, [], start, end, concept_id, language, None)
        return EXCLUDED_CONTEXT, None, frame, 0.0
    cause = structure.check(description, used, pattern, sentence_start, sentence_end, language)
    if cause is None and one_word:
        cause = structure.one_word(description, start, end, sentence_start, sentence_end, language)
    frame = ev.frame(description, sentence, notes or [], start, end, concept_id, language, cause)
    weight = policy_mod.weight(frame, policy)
    if weight >= policy["counted_at"]:
        if gap > 0 and context in UNCERTAIN_WHEN_APART:
            return UNCERTAIN, APART_IN_UNKNOWN_CONTEXT, frame, weight
        return MATCH, None, frame, weight
    return UNCERTAIN, cause or frame.because or str(frame.evidence_type), frame, weight


def match(
    config: SearchConfig,
    job: JobFacts,
    features: JobFeatures,
    *,
    computed_at: str = "frozen",
    semantic: SemanticEvidence | None = None,
) -> ProfileJobMatch:
    """One profile's answer for one posting, over its shared facts."""
    if features.shared is None:
        features.shared = (
            shared_from_facts(job, features)
            if features.payloads.get("seniority_facts") is not None
            else read_shared(
                job,
                config.prominence.primary_headings,
                config.prominence.secondary_headings,
                config.negation,
            )
        )
    observed, evidence = observe_from_features(config, features, job)
    result = match_job(
        config,
        job,
        computed_at=computed_at,
        semantic=semantic,
        shared=features.shared,
        observed=observed,
    )
    headings = (
        tuple(config.prominence.primary_headings),
        tuple(config.prominence.secondary_headings),
    )
    key = negation_key(config.negation)
    inputs = {**job_inputs(job, features.content_hash, headings, key), **profile_inputs(config)}
    stamps = _stamps(tuple(sorted(inputs.items())))
    return ProfileJobMatch(str(job.job_id or ""), result, evidence, stamps)

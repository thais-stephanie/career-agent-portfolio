"""Finding configured phrases in a posting, and deciding how central each one is.

Three decisions live here.

**Token boundaries are lookarounds, not `\\b`.** The vocabulary this profile
cares about is full of names Python's word-boundary escape mishandles: `make.com`
ends in a word character but `\\bmake.com\\b` also matches inside `remake.combine`;
`n8n` and `c++` sit on either side of the problem. `(?<![0-9a-z])` / `(?![0-9a-z])`
say what is actually meant -- do not start or end in the middle of a token -- and
they behave identically for every pattern in the file, punctuation or not.

**A negated hit is recorded, not discarded.** "No cold calling required" is a
*good* sentence for this candidate, and a matcher that silently dropped it would
look identical to one that never read the posting. The hit is kept, marked, and
contributes nothing, so the interface can show the sentence that made the
difference.

**Prominence is computed, never guessed.** It answers "how much of this role is
this?" from where the phrase appeared and how often -- the title and a
responsibilities heading are PRIMARY, repetition or a requirements heading is
SECONDARY, a single passing mention is INCIDENTAL. That is what keeps
`people_management` mentioned once from reading like a management job.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

from career_agent.config.search_config import LexiconSignal, Negation, SearchConfig
from career_agent.domain.enums import Prominence
from career_agent.domain.matching import ObservedSignal, SignalHit
from career_agent.match.text import (
    PRIMARY_SECTION,
    SECONDARY_SECTION,
    FoldedText,
    Section,
    fold,
    fold_field,
    section_kind_at,
    sentence_at,
    sentence_start,
    split_sections,
)

#: PRIMARY > SECONDARY > INCIDENTAL, as an integer so `min_prominence` in an
#: ambiguity branch is a comparison rather than a chain of `if`s.
PROMINENCE_RANK: dict[Prominence, int] = {
    Prominence.PRIMARY: 3,
    Prominence.SECONDARY: 2,
    Prominence.INCIDENTAL: 1,
}

#: Matches nothing at all. Used for an empty pattern list, where an empty
#: alternation would otherwise match at every position.
_NEVER = re.compile(r"(?!)")

#: The leading `[0-9a-z]` run of a folded phrase -- the token a field must
#: contain for that phrase to occur in it. See `CompiledPatterns._worth_reading`.
_HEAD = re.compile(r"[0-9a-z]+")


@dataclass(frozen=True)
class CompiledPatterns:
    """One alternation for a whole signal, plus the way back to the configured phrase."""

    regex: re.Pattern[str]
    #: folded phrase -> the phrase as written in YAML, for `SignalHit.pattern`.
    by_folded: dict[str, str]
    #: The folded phrases alone, shortest first, for the candidate search.
    literals: tuple[str, ...] = ()
    #: Each phrase's leading `[0-9a-z]` run, deduplicated, for `_worth_reading`.
    heads: tuple[str, ...] = ()
    #: True when some phrase has no such run and can only be found by searching.
    headless: bool = False

    def configured(self, matched: str) -> str:
        return self.by_folded.get(matched, matched)

    def _worth_reading(self, field: FoldedText) -> bool:
        """Whether any phrase could occur at all, decided in set lookups.

        If a phrase L matches at position p, then the whole `[0-9a-z]` token of
        the field at p is exactly L's own leading `[0-9a-z]` run. The lookbehind
        says nothing of that class sits to the left of p; the run itself is that
        class; and what follows it is either L's first non-class character or,
        when L is all class, whatever the lookahead has just forbidden from
        being in the class. Left edge, right edge -- the token IS the head. So a
        head absent from `field.tokens` cannot start a match anywhere, and a
        signal whose every head is absent has nothing to look for.

        `text._TOKEN` is written in the same `[0-9a-z]` as `_bounded`, and it has
        to stay that way: this argument holds only while the two agree exactly.

        A phrase beginning with punctuation has no head, and then this refuses
        to answer -- the search decides, as it did before.
        """
        if self.headless:
            return True
        tokens = field.tokens
        return any(head in tokens for head in self.heads)

    def matches_in(self, field: FoldedText) -> list[re.Match[str]]:
        """Exactly what ``self.regex.finditer(folded)`` yields, without the walk.

        Every branch of the alternation is `re.escape`d in `_bounded`, so a
        match ALWAYS consumes one of the configured phrases verbatim; the two
        lookarounds are zero-width and can only ever reject. So the pattern can
        begin only where some phrase occurs as a plain substring, and `str.find`
        -- a C string search -- enumerates those positions far faster than the
        regex engine can walk twelve thousand characters trying eight branches
        and a lookbehind assertion at every single one of them.

        Having the candidates, the engine is asked the question it is good at,
        anchored: `match` at a position evaluates the same alternation in the
        same order, and its lookbehind still sees the characters BEFORE that
        position, so boundaries and longest-first ordering are untouched. The
        `start < end` skip reproduces `finditer` refusing to overlap a match it
        has already yielded.

        The comparison is folded-against-folded, which is also why the
        alternation needs no case-insensitive flag: both sides have been
        through the same `casefold` and neither can hold a case the other does
        not.
        """
        if not self._worth_reading(field):
            return []

        folded = field.folded
        starts: list[int] = []
        for literal in self.literals:
            at = folded.find(literal)
            while at >= 0:
                starts.append(at)
                at = folded.find(literal, at + 1)
        if not starts:
            return []

        starts.sort()
        anchored = self.regex.match
        matches: list[re.Match[str]] = []
        end = 0
        for start in starts:
            if start < end:
                continue
            found = anchored(folded, start)
            if found is None:
                continue
            matches.append(found)
            end = found.end()
        return matches


def _bounded(folded_pattern: str) -> str:
    return f"(?:(?<![0-9a-z]){re.escape(folded_pattern)}(?![0-9a-z]))"


#: Every pattern compiled here is matched against FOLDED text and is itself the
#: output of `fold`, so both sides have already been through the same
#: `casefold` and no case difference can survive to be ignored. `IGNORECASE`
#: was therefore doing nothing -- except costing: it disables the literal and
#: character-set optimisations in `re`, and a case-insensitive comparison runs
#: at every position of a twelve-thousand-character description.
#:
#: The claim that folded text carries no case is exact, not statistical. NFKD
#: replaces the two characters that fold across the ASCII range (LONG S folds
#: to "s", KELVIN SIGN to "K"), and `casefold` -- applied last, to the whole
#: decomposed string -- lowercases every ASCII capital. So `[0-9a-z]` in the
#: lookarounds excludes exactly the same characters either way.
_PATTERN_FLAGS = 0


@lru_cache(maxsize=4096)
def compile_patterns(patterns: tuple[str, ...]) -> CompiledPatterns:
    """Compile one signal's phrases into a single alternation.

    Longest first: `re` alternation is leftmost-first at a given position, so
    without the ordering "cold call" would win over "cold calling" and the
    recorded evidence would be a phrase the posting does not contain.
    """
    by_folded: dict[str, str] = {}
    for pattern in patterns:
        folded, _ = fold(pattern)
        if folded:
            by_folded.setdefault(folded, pattern)

    if not by_folded:
        return CompiledPatterns(_NEVER, {})

    ordered = sorted(by_folded, key=len, reverse=True)
    alternation = "|".join(_bounded(folded) for folded in ordered)
    heads: dict[str, None] = {}
    headless = False
    for phrase in ordered:
        head = _HEAD.match(phrase)
        if head is None:
            headless = True
        else:
            heads[head.group(0)] = None
    # Shortest first for the candidate search: a short phrase occurs oftener,
    # and finding its occurrences first costs the same either way.
    return CompiledPatterns(
        re.compile(alternation, _PATTERN_FLAGS),
        by_folded,
        tuple(reversed(ordered)),
        tuple(heads),
        headless,
    )


@lru_cache(maxsize=64)
def _compile_cues(cues: tuple[str, ...]) -> re.Pattern[str]:
    """Negation cues, matched as whole tokens so "notable" is not "not"."""
    folded = sorted({fold(cue)[0] for cue in cues if fold(cue)[0]}, key=len, reverse=True)
    if not folded:
        return _NEVER
    return re.compile("|".join(_bounded(cue) for cue in folded), _PATTERN_FLAGS)


def is_negated_before(field: FoldedText, match_start: int, negation: Negation) -> bool:
    """Look back from the match for a cue, without leaving the sentence.

    The window is clipped at the enclosing sentence start because the previous
    bullet ending in "no travel" says nothing about this one.
    """
    offsets = field.offsets
    guard_start = sentence_start(field.original, offsets[match_start])
    # `offsets` is non-decreasing, so the first folded index at or after the
    # sentence start is a bisect rather than a scan.
    sentence_floor = bisect_left(offsets, guard_start)
    window_start = max(match_start - negation.window_chars, sentence_floor, 0)
    window = field.folded[window_start:match_start]
    return _compile_cues(negation.cues).search(window) is not None


def find_hits(
    signal_id: str,
    signal_cfg: LexiconSignal,
    field_name: str,
    text: str | FoldedText,
    sections: Sequence[Section] = (),
    negation: Negation | None = None,
) -> list[SignalHit]:
    """Every occurrence of one signal in one field, quoted from the original.

    Hand it a `FoldedText` whenever the caller already has one. That is the
    whole point of the type: forty-nine signals asking the same description the
    same question should fold it once between them, not forty-nine times. A
    bare `str` still works and is folded here, with `sections` supplying the
    spans; a `FoldedText` carries its own, and then `sections` is not read.
    """
    field = text if isinstance(text, FoldedText) else fold_field(text, sections)
    if not field.original:
        return []

    compiled = compile_patterns(tuple(signal_cfg.patterns))
    found_matches = compiled.matches_in(field)
    if not found_matches:
        return []

    original = field.original
    offsets = field.offsets
    spans = field.sections
    reads_negation = signal_cfg.negation_sensitive and negation is not None

    hits: list[SignalHit] = []
    for match in found_matches:
        char_start = offsets[match.start()]
        char_end = offsets[match.end() - 1] + 1
        pattern = compiled.configured(match.group(0))
        context_rule = context_rule_for(signal_cfg, pattern)
        quote = sentence_at(original, char_start, char_end)
        if context_rule and not context_allows(context_rule, quote, original[char_start:char_end]):
            continue
        negated = False
        if reads_negation and negation is not None:
            negated = is_negated_before(field, match.start(), negation)
        hits.append(
            SignalHit(
                signal_id=signal_id,
                label=signal_cfg.label,
                field_name=field_name,
                pattern=pattern,
                quote=quote,
                char_start=char_start,
                char_end=char_end,
                negated=negated,
                section=section_kind_at(spans, char_start),
            )
        )
    return hits


def context_rule_for(signal: LexiconSignal, pattern: str) -> str | None:
    """Case-only phrase edits must not silently remove a context guard.

    Prefer the exact binding. The fallback mirrors case-insensitive phrase
    recognition and also supports the generic provenance review editor.
    """
    exact = signal.context_rules.get(pattern)
    if exact is not None:
        return exact
    return next(
        (
            rule
            for key, rule in signal.context_rules.items()
            if key.casefold() == pattern.casefold()
        ),
        None,
    )


_ENGINEERING_REQUIREMENT = re.compile(
    r"\b(?:apex|lwc|lightning web components|soql|custom salesforce development|"
    r"salesforce development|software engineering|production code)\b"
)
_DELIVERY_ACTION = re.compile(
    r"\b(?:build|building|create|creating|design|designing|develop|developing|"
    r"maintain|maintaining|implement|implementing|configure|configuring|own|owning|"
    r"deliver|delivering|automate|automating|connect|connecting|prototype|ship)\b[^!?;\n]{0,180}$"
)
_OTHER_ACTOR = re.compile(r"\b(?:customers?|users?|another team|supplied by|provided by)\b")


def context_allows(rule: str, quote: str, matched: str) -> bool:
    """Bounded, explicit context; no fuzzy similarity or candidate-specific rules.

    Salesforce alone names a platform, not engineering depth. New delivery
    paraphrases name work only with a nearby delivery verb, not merely using a
    tool that another team built. These rules operate on the original quote.
    """
    folded = fold(quote)[0]
    if rule == "engineering_requirement":
        for technical in _ENGINEERING_REQUIREMENT.finditer(folded):
            before = folded[max(0, technical.start() - 45) : technical.start()]
            after = folded[technical.end() : technical.end() + 45]
            if re.search(r"\b(?:no|without|optional|nice to have)\b[^.;]*$", before):
                continue
            if re.match(
                r"(?: development| experience)?\s+(?:is |are )?(?:not required|optional)", after
            ):
                continue
            return True
        return False
    target = fold(matched)[0]
    prefix = folded[: folded.find(target)]
    action = _DELIVERY_ACTION.search(prefix)
    if action is None or _OTHER_ACTOR.search(action.group()) is not None:
        return False
    subject = prefix[max(0, action.start() - 45) : action.start()]
    return (
        re.search(r"\b(?:customers?|users?|another team)\s+(?:can |will |who )?$", subject) is None
    )


def body_only(
    config: SearchConfig, observed: dict[str, ObservedSignal]
) -> dict[str, ObservedSignal]:
    """The same observations with every TITLE hit dropped, prominence re-measured.

    `observe()` returns one union of title and body hits, which is right for
    screening -- a posting whose title says exactly what it is should be
    admitted -- and wrong for SCORING. A phrase in a title was worth full
    weight, and at PRIMARY prominence, because `prominence.primary_fields`
    names the title: `Salesforce Administrator` over a body it shares with
    `Operations Wizard` scored 64 against 60 purely on the word in its name.

    That is the last channel by which a title bought compatibility points, and
    it is subtler than the `role_family` component was, because it looks like
    ordinary evidence. It is not: fit is a claim about the WORK, and the only
    place a posting describes the work is its description.

    Prominence is recomputed rather than carried over, since it is a property
    of where the surviving hits landed. A signal left with nothing is kept, so
    a caller never has to tell "absent from this dict" from "found in the title
    and therefore not counted here".
    """
    trimmed: dict[str, ObservedSignal] = {}
    for signal_id, signal in observed.items():
        live = tuple(h for h in signal.hits if h.field_name != "title")
        negated = tuple(h for h in signal.negated_hits if h.field_name != "title")
        if len(live) == len(signal.hits) and len(negated) == len(signal.negated_hits):
            trimmed[signal_id] = signal
            continue
        trimmed[signal_id] = ObservedSignal(
            signal_id=signal.signal_id,
            label=signal.label,
            responsibility=signal.responsibility,
            prominence=prominence_of(config, live),
            hits=live,
            negated_hits=negated,
        )
    return trimmed


def prominence_of(config: SearchConfig, hits: tuple[SignalHit, ...]) -> Prominence:
    """How central the signal is, from where its live hits landed.

    Only non-negated hits count. A negated hit is evidence about the posting,
    not evidence that the posting is about this.
    """
    if not hits:
        return Prominence.INCIDENTAL

    primary_fields = set(config.prominence.primary_fields)
    if any(h.field_name in primary_fields or h.section == PRIMARY_SECTION for h in hits):
        return Prominence.PRIMARY
    if len(hits) >= config.prominence.secondary_min_hits or any(
        h.section == SECONDARY_SECTION for h in hits
    ):
        return Prominence.SECONDARY
    return Prominence.INCIDENTAL


def observe(
    config: SearchConfig,
    title: str | FoldedText,
    description: str | FoldedText,
) -> dict[str, ObservedSignal]:
    """Run every lexicon signal over the title and the description.

    Both fields are folded ONCE, before the loop, and every signal then reads
    the same two values. A caller holding them already -- the engine does,
    because the gates want the same two -- passes them straight through.

    Signals that fired nowhere are still returned, so a caller never has to
    distinguish "absent from this dict" from "found and negated".
    """
    title_field = title if isinstance(title, FoldedText) else fold_field(title)
    description_field = (
        description
        if isinstance(description, FoldedText)
        else fold_field(
            description,
            split_sections(
                description,
                config.prominence.primary_headings,
                config.prominence.secondary_headings,
            ),
        )
    )
    observed: dict[str, ObservedSignal] = {}

    for signal_id, signal_cfg in config.lexicon.items():
        found = find_hits(signal_id, signal_cfg, "title", title_field, negation=config.negation)
        found += find_hits(
            signal_id, signal_cfg, "description", description_field, negation=config.negation
        )

        live = tuple(hit for hit in found if not hit.negated)
        negated = tuple(hit for hit in found if hit.negated)
        observed[signal_id] = ObservedSignal(
            signal_id=signal_id,
            label=signal_cfg.label,
            responsibility=signal_cfg.responsibility,
            prominence=prominence_of(config, live),
            hits=live,
            negated_hits=negated,
        )
    return observed


def fired_at_least(
    observed: dict[str, ObservedSignal], signal_id: str, minimum: Prominence
) -> bool:
    """True when the signal fired and did so at `minimum` prominence or above."""
    signal = observed.get(signal_id)
    if signal is None or not signal.fired:
        return False
    return PROMINENCE_RANK[signal.prominence] >= PROMINENCE_RANK[minimum]


#: The signals that name a TOOL rather than a responsibility.
#:
#: Here rather than in `web/presenter.py`, where it started, because the
#: repository now needs it too -- to count the technology facet -- and storage
#: importing the web layer would invert the dependency the whole package is
#: arranged around. The presenter re-exports it, so nothing that already read
#: it there has to move.
TECHNOLOGY_SIGNALS: frozenset[str] = frozenset(
    {
        "hubspot_platform",
        "ipaas",
        "tool_stack",
        "scripting",
        "custom_objects",
        "private_apps",
        "api_integration",
        "data_sync",
        "operational_analytics",
    }
)

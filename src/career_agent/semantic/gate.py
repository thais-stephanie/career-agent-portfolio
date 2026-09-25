"""The deterministic publication gate. Every provider is untrusted.

A finding is published only when:

* the answer parsed into the contract's shape;
* its intent id is one we sent, in the list it was answered under;
* at least one of its quotes is text the posting really contains.

Anything else is rejected and counted by reason. A rejected positive never
becomes a weaker positive: it becomes nothing. A list whose verdict claimed
alignment but whose every match was rejected is published as UNRESOLVED, which
the scorer reads as "no semantic evidence", never as "no fit" and never as fit.

QUOTE MATCHING
--------------
Exact substring first. Models routinely collapse a line break into a space, so
a second pass compares with every whitespace run folded to one space and, when
that finds the quote, stores the ORIGINAL span of the posting. The published
quote is therefore always a verbatim substring of the posting, which is the
property the drawer and every later audit rely on. Nothing else is forgiven:
no case folding, no punctuation repair, no fuzzy matching.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from career_agent.semantic.contract import Strength, TAnswer, TAspect, Verdict
from career_agent.semantic.intent import Aspect, SearchIntent

#: Shorter quotes are too easy to find by accident to prove anything about the
#: work; a tool name is the one legitimate short quote, and three characters
#: still admits "SQL" and "n8n".
MIN_QUOTE_CHARS = 3
MAX_QUOTE_CHARS = 400
MAX_QUOTES_PER_MATCH = 3

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class PublishedMatch:
    intent_id: str
    signal_id: str
    aspect: Aspect
    strength: Strength
    #: Verbatim substrings of the posting.
    quotes: tuple[str, ...]


@dataclass(frozen=True)
class PublishedAspect:
    aspect: Aspect
    verdict: Verdict
    matches: tuple[PublishedMatch, ...] = ()


@dataclass
class GateReport:
    """Why anything was refused. Counted, never silently dropped."""

    rejected: dict[str, int] = field(default_factory=dict)

    def refuse(self, reason: str) -> None:
        self.rejected[reason] = self.rejected.get(reason, 0) + 1

    @property
    def total(self) -> int:
        return sum(self.rejected.values())


@dataclass(frozen=True)
class Published:
    aspects: tuple[PublishedAspect, ...]
    report: GateReport

    def aspect(self, aspect: Aspect) -> PublishedAspect:
        for row in self.aspects:
            if row.aspect is aspect:
                return row
        return PublishedAspect(aspect=aspect, verdict=Verdict.UNRESOLVED)

    @property
    def matches(self) -> tuple[PublishedMatch, ...]:
        return tuple(m for a in self.aspects for m in a.matches)


def locate(quote: str, posting: str) -> str | None:
    """The verbatim posting span this quote cites, or None."""
    quote = quote.strip()
    if len(quote) < MIN_QUOTE_CHARS or len(quote) > MAX_QUOTE_CHARS:
        return None
    if quote in posting:
        return quote
    folded_quote = _WS.sub(" ", quote)
    # Map each folded position back to the original text.
    pieces: list[str] = []
    origin: list[int] = []
    previous_space = False
    for index, char in enumerate(posting):
        if char.isspace():
            if previous_space:
                continue
            pieces.append(" ")
            previous_space = True
        else:
            pieces.append(char)
            previous_space = False
        origin.append(index)
    folded = "".join(pieces)
    at = folded.find(folded_quote)
    if at < 0:
        return None
    start = origin[at]
    end = origin[at + len(folded_quote) - 1] + 1
    return posting[start:end]


def publish(answer: TAnswer, intent: SearchIntent, posting: str) -> Published:
    """Only what can be checked survives."""
    report = GateReport()
    known = intent.by_id()
    aspects: list[PublishedAspect] = []
    for aspect, raw in (
        (Aspect.WORK, answer.work),
        (Aspect.TOOLS, answer.tools),
        (Aspect.OTHER, answer.other),
    ):
        aspects.append(_publish_aspect(aspect, raw, known, posting, intent, report))
    return Published(aspects=tuple(aspects), report=report)


def _publish_aspect(
    aspect: Aspect,
    raw: TAspect,
    known: dict,
    posting: str,
    intent: SearchIntent,
    report: GateReport,
) -> PublishedAspect:
    if not intent.configured(aspect):
        # Nothing was asked. Whatever came back cannot be about this person.
        if raw.matches:
            report.refuse("match_on_unconfigured_list")
        return PublishedAspect(aspect=aspect, verdict=Verdict.UNRESOLVED)

    best: dict[str, PublishedMatch] = {}
    for match in raw.matches:
        item = known.get(match.intent_id.strip())
        if item is None:
            report.refuse("unknown_intent_id")
            continue
        if item.aspect is not aspect:
            report.refuse("intent_id_in_wrong_list")
            continue
        quotes: list[str] = []
        for quote in match.quotes[:MAX_QUOTES_PER_MATCH]:
            span = locate(quote, posting)
            if span is None:
                report.refuse("quote_not_in_posting")
                continue
            if span not in quotes:
                quotes.append(span)
        if len(match.quotes) > MAX_QUOTES_PER_MATCH:
            report.refuse("extra_quotes_ignored")
        if not quotes:
            report.refuse("match_without_verifiable_quote")
            continue
        published = PublishedMatch(
            intent_id=item.intent_id,
            signal_id=item.signal_id,
            aspect=aspect,
            strength=match.strength,
            quotes=tuple(quotes),
        )
        # One intent item appears once. The stronger reading wins.
        current = best.get(item.intent_id)
        if current is None or (
            current.strength is Strength.PARTIAL and published.strength is Strength.STRONG
        ):
            if current is not None:
                report.refuse("duplicate_intent_id")
            best[item.intent_id] = published
        else:
            report.refuse("duplicate_intent_id")

    matches = tuple(sorted(best.values(), key=lambda m: m.intent_id))
    verdict = _verdict(raw.verdict, matches, report)
    return PublishedAspect(aspect=aspect, verdict=verdict, matches=matches)


def _verdict(claimed: Verdict, matches: tuple[PublishedMatch, ...], report: GateReport) -> Verdict:
    """The verdict the published matches can support, never more than claimed."""
    if matches:
        derived = (
            Verdict.STRONG
            if any(m.strength is Strength.STRONG for m in matches)
            else Verdict.PARTIAL
        )
        if claimed in (Verdict.NONE, Verdict.UNRESOLVED):
            # Matches with a "none" verdict is an incoherent answer. The
            # checked matches are evidence; the verdict is recomputed from them.
            report.refuse("verdict_contradicts_matches")
        return derived
    if claimed in (Verdict.STRONG, Verdict.PARTIAL):
        # Claimed alignment with nothing checkable behind it.
        report.refuse("positive_verdict_without_evidence")
        return Verdict.UNRESOLVED
    return claimed

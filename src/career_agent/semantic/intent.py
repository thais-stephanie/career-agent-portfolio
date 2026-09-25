"""The normalized Search Intent a provider is allowed to see.

Only what the person said they WANT from a next role: the work phrases, the
tools and methods, and any other desired signals. Never the Career Profile,
never evidence, never a CV, never application history. Those answer "what has
this person done", which is a different question and a different contract.

Each item gets a short, stable id (`W1`, `T3`, `O2`). Short ids are easier for a
model to copy exactly than a slug, and the gate refuses any id that is not in
this table, so a mangled id is a rejected finding rather than a wrong one.
The table itself is part of the intent identity: renumbering items changes the
digest, so a cached answer can never be read against a different numbering.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from career_agent.config.search_config import SearchConfig


class Aspect(StrEnum):
    """The three positive, phrase-driven parts of Search Fit."""

    WORK = "work"
    TOOLS = "tools"
    OTHER = "other"


#: Which scoring component each aspect feeds, and the id prefix it uses.
COMPONENT_OF: dict[Aspect, str] = {
    Aspect.WORK: "responsibilities",
    Aspect.TOOLS: "technologies",
    Aspect.OTHER: "automation_integration",
}
ASPECT_OF: dict[str, Aspect] = {component: aspect for aspect, component in COMPONENT_OF.items()}
PREFIX: dict[Aspect, str] = {Aspect.WORK: "W", Aspect.TOOLS: "T", Aspect.OTHER: "O"}


@dataclass(frozen=True)
class IntentItem:
    intent_id: str
    aspect: Aspect
    #: The scoring signal this item stands for. Never sent to a provider.
    signal_id: str
    #: The person's own phrase, as the configuration labels it.
    text: str


@dataclass(frozen=True)
class SearchIntent:
    items: tuple[IntentItem, ...]

    @property
    def digest(self) -> str:
        """Identity of exactly what a provider is asked about."""
        canonical = json.dumps(
            [[i.intent_id, i.aspect.value, i.signal_id, i.text] for i in self.items],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]

    def by_id(self) -> dict[str, IntentItem]:
        return {item.intent_id: item for item in self.items}

    def of(self, aspect: Aspect) -> tuple[IntentItem, ...]:
        return tuple(item for item in self.items if item.aspect is aspect)

    def configured(self, aspect: Aspect) -> bool:
        return bool(self.of(aspect))

    def for_provider(self) -> dict[str, list[dict[str, str]]]:
        """The only intent payload that leaves the machine."""
        return {
            aspect.value: [{"id": i.intent_id, "text": i.text} for i in self.of(aspect)]
            for aspect in Aspect
        }


def search_intent(config: SearchConfig) -> SearchIntent:
    """Read the person's positive intent from the scoring configuration.

    An item exists for every signal with a POSITIVE weight in one of the three
    phrase components. A zero weight is a phrase the person switched off, and
    an unweighted lexicon entry is vocabulary rather than intent.
    """
    items: list[IntentItem] = []
    components = config.scoring.components
    for aspect, component_id in COMPONENT_OF.items():
        weights = getattr(components, component_id).weights
        active = [signal_id for signal_id, weight in weights.items() if weight > 0]
        for index, signal_id in enumerate(sorted(active), start=1):
            signal = config.lexicon.get(signal_id)
            text = _intent_text(signal_id, signal)
            items.append(
                IntentItem(
                    intent_id=f"{PREFIX[aspect]}{index}",
                    aspect=aspect,
                    signal_id=signal_id,
                    text=text,
                )
            )
    return SearchIntent(items=tuple(items))


def _intent_text(signal_id: str, signal: Any) -> str:
    if signal is None:
        return signal_id.replace("_", " ")
    label = str(signal.label).strip()
    patterns = [str(p).strip() for p in signal.patterns if str(p).strip()]
    # The label is what the person typed; a different pattern adds wording they
    # also accept. Both are their words, never ours.
    extra = [p for p in patterns if p.casefold() != label.casefold()][:2]
    return f"{label} ({'; '.join(extra)})" if extra else label

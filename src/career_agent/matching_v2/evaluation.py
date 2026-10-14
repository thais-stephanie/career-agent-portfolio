"""Read a labelled corpus through the v2 matcher and count what it answered.

Each case is one sentence under one heading, matched against ONE phrase filed
the way the setup wizard files it (one pattern equal to its own label). The
answer is the strongest state any match reached: MATCH, UNCERTAIN,
EXCLUDED_CONTEXT or NONE. Used by the tests and by the report script; never
by the product.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from career_agent.config.search_config import LexiconSignal, SearchConfig
from career_agent.match.engine import JobFacts
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2.profile_match import (
    EXCLUDED_CONTEXT,
    MATCH,
    UNCERTAIN,
    observe_from_features,
)

NONE = "NONE"
_ORDER = {MATCH: 3, UNCERTAIN: 2, EXCLUDED_CONTEXT: 1, NONE: 0}
#: What a correct reader answers, per category.
EXPECTED = {"T": MATCH, "X": MATCH, "F": MATCH, "S": NONE, "P": NONE, "N": NONE, "C": NONE}


@dataclass(frozen=True)
class Case:
    phrase: str
    field: str
    cat: str
    lang: str
    text: str
    heading: str


def load_corpus(path: Path) -> list[Case]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [
        Case(
            c["phrase"],
            c["field"],
            k["cat"],
            k["lang"],
            k["text"],
            k.get("heading", "Responsibilities"),
        )  # fmt: skip
        for c in raw["concepts"]
        for k in c["cases"]
    ]


def answer(base: SearchConfig, phrase: str, text: str, heading: str) -> str:
    signal = LexiconSignal(label=phrase, patterns=[phrase])
    config = base.model_copy(update={"lexicon": {"v2_phrase": signal}})
    description = f"{heading}\n{text}\n"
    job = JobFacts(title="Role", description=description, job_id="case")
    headings = (
        tuple(base.prominence.primary_headings),
        tuple(base.prominence.secondary_headings),
    )
    features = feat.extract(job, "case", headings)
    observed, evidence = observe_from_features(config, features, job)
    states = [e.state for e in evidence] or [NONE]
    if observed["v2_phrase"].fired:
        states.append(MATCH)
    return max(states, key=lambda s: _ORDER[s])


def run(base: SearchConfig, cases: list[Case]) -> dict[str, Any]:
    by_cat: dict[str, Counter] = defaultdict(Counter)
    by_lang: dict[str, Counter] = defaultdict(Counter)
    misses: list[dict[str, str]] = []
    for case in cases:
        got = answer(base, case.phrase, case.text, case.heading)
        correct = got == EXPECTED[case.cat] or (EXPECTED[case.cat] == NONE and got != MATCH)
        by_cat[case.cat]["n"] += 1
        by_cat[case.cat]["correct"] += correct
        by_cat[case.cat][got] += 1
        by_lang[case.lang]["n"] += 1
        by_lang[case.lang]["correct"] += correct
        if not correct:
            misses.append({"cat": case.cat, "phrase": case.phrase, "text": case.text, "got": got})
    return {"by_cat": dict(by_cat), "by_lang": dict(by_lang), "misses": misses}

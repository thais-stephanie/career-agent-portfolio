"""Does Career Agent know enough about what the person wants to score?

A low Search Fit must mean "this posting does not fit what you asked for",
never "you have not told us what you want yet". So readiness is judged from
the Search Intent alone, and the interface shows it instead of a score when
the answer is no.

NOT READY  no work phrase. Search Fit is mostly about the WORK, and with no
           work intent every posting would score as though it failed to match
           something nobody specified.
PARTIAL    the work is known; the preferred level or the way of working is
           not. Scores are meaningful but coarser: an unstated level earns no
           seniority evidence points, and without a work-model preference
           that component is not part of the score.
READY      the work, the level and the way of working are all stated.

Tools and other desired signals are NOT required. An unconfigured component
leaves the denominator; it does not make a score less meaningful, and plenty
of occupations have no tool list worth stating. Nor does readiness depend on
the corpus: a phrase that no collected posting has used yet is rare intent,
not incomplete setup.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from career_agent.config.search_config import SearchConfig


class Readiness(StrEnum):
    NOT_READY = "NOT_READY"
    PARTIAL = "PARTIAL"
    READY = "READY"


#: Stable codes the interface translates. Never a sentence here.
MISSING_WORK = "work"
MISSING_LEVEL = "level"
MISSING_WORK_MODEL = "work_model"


@dataclass(frozen=True)
class SearchFitReadiness:
    state: Readiness
    missing: tuple[str, ...]
    work_phrases: int
    tool_phrases: int
    other_phrases: int

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "missing": list(self.missing),
            "work_phrases": self.work_phrases,
            "tool_phrases": self.tool_phrases,
            "other_phrases": self.other_phrases,
        }


def _positive(weights: dict[str, float]) -> int:
    return sum(1 for weight in weights.values() if weight > 0)


def search_fit_readiness(config: SearchConfig) -> SearchFitReadiness:
    components = config.scoring.components
    work = _positive(components.responsibilities.weights)
    tools = _positive(components.technologies.weights)
    other = _positive(components.automation_integration.weights)
    remote = config.preferences.remote
    missing: list[str] = []
    if work == 0:
        missing.append(MISSING_WORK)
    if not config.preferences.seniority.preferred:
        missing.append(MISSING_LEVEL)
    if not (
        remote.accepted_work_models or remote.avoided_work_models or remote.excluded_work_models
    ):
        missing.append(MISSING_WORK_MODEL)
    if work == 0:
        state = Readiness.NOT_READY
    elif missing:
        state = Readiness.PARTIAL
    else:
        state = Readiness.READY
    return SearchFitReadiness(state, tuple(missing), work, tools, other)

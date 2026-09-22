"""The frozen free-challenger screen: which cases, and what advancing means.

A screen is only worth running if its membership and its pass mark were fixed
before any output existed. Both were, on 2026-09-03, and both live in
`evaluation/free-challenger-screen.yaml` rather than in a conversation -- which
is the point. A gate agreed in chat and applied afterwards is a gate that can be
remembered generously.

This module reads that file and refuses the ways it could quietly rot: a case
that is not in the golden set, a case whose assertions are not human-reviewed,
or a baseline that no longer matches the arm it claims to describe.

The screen ELIMINATES. It does not qualify. `docs/architecture/` carries the
funnel; the short version is that clearing this earns the full 27-case
benchmark and nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

SCREEN_ARTIFACT = Path("evaluation/free-challenger-screen.yaml")


class ScreenError(ValueError):
    """The screen definition is malformed, or disagrees with the golden set."""


@dataclass(frozen=True)
class ScreenCase:
    case_id: str
    covers: tuple[str, ...]
    why: str


@dataclass(frozen=True)
class Screen:
    """The frozen membership, the baseline it is measured against, and the gate."""

    version: int
    frozen_at: str
    purpose: str
    cases: tuple[ScreenCase, ...]
    baseline: dict[str, Any]
    gate: dict[str, Any]

    @property
    def case_ids(self) -> tuple[str, ...]:
        return tuple(case.case_id for case in self.cases)


def load_screen(path: Path = SCREEN_ARTIFACT) -> Screen:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return Screen(
        version=int(raw["version"]),
        frozen_at=str(raw["frozen_at"]),
        purpose=str(raw.get("purpose", "")),
        cases=tuple(
            ScreenCase(
                case_id=str(row["case_id"]),
                covers=tuple(row.get("covers", ())),
                why=str(row.get("why", "")),
            )
            for row in raw.get("cases", [])
        ),
        baseline=dict(raw.get("baseline", {})),
        gate=dict(raw.get("gate", {})),
    )


def validate_screen(screen: Screen, golden_root: Path) -> list[str]:
    """Everything wrong with the screen's own definition. Empty means sound.

    Checks the definition, never a challenger. A screen that named a case the
    golden set does not have, or one whose assertions nobody reviewed, would
    fail an arm for a reason that says nothing about the arm.
    """
    from career_agent.evaluation.golden import load_cases

    problems: list[str] = []
    by_id = {case.case_id: case for case in load_cases(golden_root)}

    if len(set(screen.case_ids)) != len(screen.case_ids):
        problems.append("a case appears twice in the screen")

    for case_id in screen.case_ids:
        case = by_id.get(case_id)
        if case is None:
            problems.append(f"{case_id} is not in the golden set")
            continue
        if not case.scoreable:
            problems.append(
                f"{case_id} has no human-reviewed assertion, so it can eliminate a "
                "challenger without measuring anything"
            )

    if not screen.baseline:
        problems.append("no baseline: a gate with nothing to be better than is not a gate")
    if not screen.gate.get("hard_invariants") or not screen.gate.get("thresholds"):
        problems.append("the gate must state both hard invariants and thresholds")

    return problems

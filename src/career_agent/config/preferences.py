"""Editing the search preferences without opening a YAML file.

The product's whole scoring policy lives in `config/search.worked-example.yaml`, and
until now changing a single phrase meant finding it in 1,100 lines, editing it
by hand, remembering to bump `config_version`, and re-running `rescore`. That
is a developer's workflow wearing a product's clothes.

Three rules shape this module.

**Writes go to `search.local.yaml`, never to the committed example.** The
example is the shipped default and the thing `git checkout` restores when an
edit goes wrong; overwriting it would destroy the only copy of the baseline.
The local file is gitignored, is already the documented override, and is read
in preference to the example when it exists.

**A change bumps `config_version`.** Scores are only true relative to the
configuration that produced them. Editing the phrases without a bump would
leave 18,549 rows scored against bytes that no longer exist, with nothing
saying so -- which is precisely the drift `health` already reports on.

**Nothing is deleted that the person did not delete.** The local file is
written whole from the example plus the edits, so a signal the editor does not
know about survives untouched.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from career_agent.config.search_config import (
    SEARCH_STEM,
    effective_search_path,
    local_search_path,
)
from career_agent.yaml_io import safe_load

#: The categories a person can edit, and where each one lives in the YAML.
#: Named here so the API, the interface and the writer cannot disagree about
#: what "a negative signal" means.
CATEGORIES: dict[str, dict[str, str]] = {
    "desired": {
        "label": "Desired signals",
        "help": "Phrases that add match value when a posting contains them.",
        "path": "lexicon",
    },
    "negative": {
        "label": "Negative signals",
        "help": "Phrases that reduce the match without excluding the job.",
        # Also the lexicon. A negative signal is not stored anywhere else: it
        # is a lexicon entry whose id carries a weight under
        # `scoring.soft_penalties.weights`. Pointing this category at that
        # section instead offered `prominence_multipliers` and `weights`
        # themselves -- a tuning knob and a mapping of numbers, neither of
        # which is a phrase group. Two tests caught it.
        "path": "lexicon",
    },
    "excluded": {
        "label": "Hard exclusions",
        "help": (
            "Phrases that remove a job from the eligible view. A posting must "
            "SAY one of these; silence never excludes."
        ),
        "path": "eligibility.blockers",
    },
}


class PreferenceError(ValueError):
    """The edit does not describe something that exists."""


@dataclass(frozen=True, slots=True)
class Signal:
    """One editable group of phrases."""

    category: str
    signal_id: str
    label: str
    patterns: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "signal_id": self.signal_id,
            "label": self.label,
            "patterns": list(self.patterns),
        }


def _dig(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def read_signals(config_dir: Path) -> list[Signal]:
    """Every editable phrase group, from whichever file is in force."""
    raw = _load_effective(config_dir)
    penalised = _penalised_ids(raw)
    signals: list[Signal] = []
    for category, meta in CATEGORIES.items():
        node = _dig(raw, meta["path"])
        if isinstance(node, dict):
            for signal_id, entry in sorted(node.items()):
                if not _carries_phrases(entry):
                    continue
                # The lexicon holds both kinds. Which one a signal is depends
                # on whether the scoring section penalises it.
                if meta["path"] == "lexicon":
                    is_negative = str(signal_id) in penalised
                    if is_negative != (category == "negative"):
                        continue
                signals.append(_signal(category, str(signal_id), entry))
        elif isinstance(node, list):
            # `eligibility.blockers` is a list of entries carrying their own id.
            for entry in node:
                if isinstance(entry, dict) and entry.get("id") and _carries_phrases(entry):
                    signals.append(_signal(category, str(entry["id"]), entry))
    return signals


def _penalised_ids(raw: dict[str, Any]) -> set[str]:
    """Lexicon ids the scoring section gives a penalty weight to."""
    weights = _dig(raw, "scoring.soft_penalties.weights")
    return {str(k) for k in weights} if isinstance(weights, dict) else set()


def _carries_phrases(entry: Any) -> bool:
    """Is this an editable phrase group, or a tuning knob sharing the section?

    `scoring.soft_penalties` holds both: named penalties with `patterns`, and
    `prominence_multipliers`, which is a mapping of numbers. Offering the
    second one in a phrase editor would render an empty box that saves
    nothing -- caught by a test asserting every offered signal has something
    to edit.
    """
    return isinstance(entry, dict) and isinstance(entry.get("patterns"), list)


def _signal(category: str, signal_id: str, entry: Any) -> Signal:
    label = signal_id
    patterns: tuple[str, ...] = ()
    if isinstance(entry, dict):
        label = str(entry.get("label") or signal_id)
        raw = entry.get("patterns")
        if isinstance(raw, list):
            patterns = tuple(str(p) for p in raw)
    return Signal(category=category, signal_id=signal_id, label=label, patterns=patterns)


def set_patterns(
    config_dir: Path,
    category: str,
    signal_id: str,
    patterns: list[str],
) -> tuple[Path, int]:
    """Replace one signal's phrases. Returns the file written and the new version.

    Refuses a category or signal that does not exist rather than creating one:
    a typo that silently invents a signal nothing scores against is the same
    class of defect as a filter parameter nobody reads.
    """
    if category not in CATEGORIES:
        raise PreferenceError(
            f"unknown category {category!r}; expected one of {', '.join(sorted(CATEGORIES))}"
        )

    cleaned: list[str] = []
    for pattern in patterns:
        text = str(pattern).strip()
        if not text:
            continue
        if text.lower() not in {c.lower() for c in cleaned}:
            cleaned.append(text)
    if not cleaned:
        raise PreferenceError(
            "a signal with no phrases would match nothing and be invisible; "
            "remove the signal instead, or leave at least one phrase"
        )

    raw = copy.deepcopy(_load_effective(config_dir))
    node = _dig(raw, CATEGORIES[category]["path"])

    if isinstance(node, dict):
        if signal_id not in node:
            raise PreferenceError(f"{signal_id!r} is not a signal in {category!r}")
        entry = node[signal_id]
        if not isinstance(entry, dict):
            raise PreferenceError(f"{signal_id!r} does not carry phrases")
        entry["patterns"] = cleaned
    elif isinstance(node, list):
        for entry in node:
            if isinstance(entry, dict) and str(entry.get("id")) == signal_id:
                entry["patterns"] = cleaned
                break
        else:
            raise PreferenceError(f"{signal_id!r} is not a signal in {category!r}")
    else:
        raise PreferenceError(f"{category!r} holds nothing editable")

    version = int(raw.get("config_version", 1)) + 1
    raw["config_version"] = version

    target = local_search_path(config_dir)
    target.write_text(
        "# Written by the preference editor. Safe to edit by hand.\n"
        f"# This file overrides {SEARCH_STEM}.example.yaml and is gitignored:\n"
        "# it holds YOUR preferences and never leaves this machine.\n"
        "#\n"
        "# `config_version` is bumped on every change, so scores computed under\n"
        "# an older version stay readable instead of being silently reinterpreted.\n"
        "# Re-run `career-agent rescore` to score against this version.\n\n"
        + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True, width=100),
        encoding="utf-8",
    )
    return target, version


def _load_effective(config_dir: Path) -> dict[str, Any]:
    """The local search when present, otherwise neutral policy, never the example."""
    path = effective_search_path(config_dir)
    data = safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PreferenceError(f"{path} does not contain a configuration mapping")
    return data

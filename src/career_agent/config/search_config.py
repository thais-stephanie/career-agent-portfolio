"""Loading `config/search*.yaml`, and the one place its cross-references are checked.

Two rules govern this module, and both differ deliberately from `loader.py`.

**A new search starts neutral.** Local configuration wins when present;
otherwise the neutral starter is loaded. The worked example requires explicit
opt-in and is never silently promoted into another person's search model.
The resolved path is returned so callers can identify what produced a score.

**Every reference is resolved at load time, never at match time.** A `$FRAGMENT`
that names nothing, a weight keyed by a signal the lexicon never defines, an
`ambiguity_rule` pointing at a rule that was renamed: each of those is a typo
whose only symptom at match time would be a job quietly scoring a few points
lower than it should. They are errors here, named by their YAML path, so the
matcher downstream can assume every name it reads resolves.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from career_agent.domain.enums import Prominence, ResponsibilityCategory, Seniority
from career_agent.domain.matching import GATE_NAMES, TitleClass
from career_agent.yaml_io import safe_load

SEARCH_STEM = "search"

#: `$NAME` in a taxonomy rule. Uppercase-only so a literal phrase containing a
#: dollar sign can never be mistaken for a reference.
FRAGMENT_REFERENCE = re.compile(r"^\$([A-Z0-9_]+)$")

#: The taxonomy sections, in the order `classify_title` consults them. Excluded
#: is first because an off-target title must never be rescued by also matching a
#: primary rule.
TAXONOMY_SECTIONS: tuple[str, ...] = ("excluded", "primary", "strong_adjacent", "conditional")

#: YAML spells the title classes in lowercase because a human types them.
CLASS_BY_NAME: dict[str, TitleClass] = {member.value.lower(): member for member in TitleClass}


class SearchConfigError(RuntimeError):
    """The search configuration is missing, unreadable, invalid or inconsistent."""


class _Section(BaseModel):
    """Every block in this file rejects unknown keys and refuses reassignment.

    `extra="forbid"` is what turns a misspelled key into an error instead of a
    setting that silently does nothing.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


# =========================================================================
# 1. NORMALISATION AND NEGATION
# =========================================================================


class Normalisation(_Section):
    casefold: bool = True
    strip_accents: bool = True
    collapse_whitespace: bool = True
    word_boundary: bool = True


class Negation(_Section):
    window_chars: int = Field(ge=1)
    cues_en: list[str] = Field(default_factory=list)
    cues_pt: list[str] = Field(default_factory=list)

    @property
    def cues(self) -> tuple[str, ...]:
        """Both languages in one list. The matcher never asks which language a
        posting is in; it asks whether a cue is present."""
        return (*self.cues_en, *self.cues_pt)


# =========================================================================
# 2. TAXONOMY
# =========================================================================


class TitleRule(_Section):
    """A title rule after fragment expansion: literal phrases, no references.

    `all_of` is a list of groups; every group must contribute at least one of
    its alternatives. That is what gives word-order and bilingual tolerance
    without enumerating literal titles.
    """

    id: str
    label: str
    all_of: list[list[str]]
    none_of: list[str] = Field(default_factory=list)
    ambiguity_rule: str | None = None


class Taxonomy(_Section):
    fragments: dict[str, list[str]] = Field(default_factory=dict)
    primary: list[TitleRule] = Field(default_factory=list)
    strong_adjacent: list[TitleRule] = Field(default_factory=list)
    conditional: list[TitleRule] = Field(default_factory=list)
    excluded: list[TitleRule] = Field(default_factory=list)

    def rules(self, section: str) -> list[TitleRule]:
        value: list[TitleRule] = getattr(self, section)
        return value


class AmbiguityBranch(_Section):
    any_of: list[str]
    min_signals: int = Field(ge=1)
    min_prominence: Prominence


class AmbiguityRule(_Section):
    note: str | None = None
    promote_to: str | None = None
    promote_if: AmbiguityBranch | None = None
    demote_to: str | None = None
    demote_if: AmbiguityBranch | None = None


# =========================================================================
# 3. LEXICON AND PROMINENCE
# =========================================================================


class LexiconSignal(_Section):
    label: str
    #: Binds the signal to the vocabulary a future LLM observation also speaks.
    responsibility: ResponsibilityCategory | None = None
    patterns: list[str]
    negation_sensitive: bool = False
    #: Selected phrases require a local work context, rather than a tool mention.
    context_rules: dict[str, Literal["engineering_requirement", "systems_delivery"]] = Field(
        default_factory=dict
    )


class ProminenceConfig(_Section):
    primary_fields: list[str] = Field(default_factory=list)
    primary_headings: list[str] = Field(default_factory=list)
    secondary_min_hits: int = Field(ge=1)
    secondary_headings: list[str] = Field(default_factory=list)


# =========================================================================
# 4. SCREENING AND ELIGIBILITY
# =========================================================================


class ScreeningGroup(_Section):
    id: str
    label: str
    any_of: list[str]


class Screening(_Section):
    required_any_groups: list[ScreeningGroup] = Field(default_factory=list)
    required_all: list[str] = Field(default_factory=list)


class Blocker(_Section):
    id: str
    label: str
    gate: str
    patterns: list[str]
    negation_sensitive: bool = False

    @field_validator("gate")
    @classmethod
    def _known_gate(cls, value: str) -> str:
        # A blocker on a gate the matcher does not evaluate would load and
        # close nothing, silently (HCE01 found exactly that). Refuse it.
        if value not in GATE_NAMES:
            raise ValueError(f"unknown gate {value!r}; the gates are {', '.join(GATE_NAMES)}")
        return value


class Eligibility(_Section):
    candidate_country: str
    candidate_country_label: str
    blockers: list[Blocker] = Field(default_factory=list)
    eligible_scopes: list[str] = Field(default_factory=list)
    eligible_countries: list[str] = Field(default_factory=list)
    positive_scope_patterns: dict[str, list[str]] = Field(default_factory=dict)


# =========================================================================
# 5. SCORING
# =========================================================================


class WeightedComponent(_Section):
    max: float
    label: str
    prominence_multipliers: dict[Prominence, float]
    weights: dict[str, float]
    # Last generated values; ownership metadata, not scoring input.
    setup_weights: dict[str, float] = Field(default_factory=dict)


class SeniorityComponent(_Section):
    max: float
    label: str
    points: dict[Seniority, float]
    #: What a reading with no evidence behind it is worth. Separate from
    #: `points` on purpose: the fallback level is MID, and paying it the MID
    #: rate is what let a posting that stated nothing collect the same points as
    #: one that said "this is a mid-level position".
    unevidenced: float = 0.0

    def points_for(self, level: Seniority) -> float:
        """What this configuration pays for a stated level, or nothing.

        **A level the file does not price is worth zero, and that is a
        deliberate reading rather than a shrug.** `points` is a list of the
        levels somebody said they want. STAFF and PRINCIPAL were added to the
        vocabulary on 2026-09-07, so every configuration written before that
        date prices neither -- and the honest meaning of a level absent from
        the list is that this search did not ask for it.

        The alternative was a direct subscript, which is what was here: a
        `KeyError` the moment a real Staff posting reached the scorer. And the
        other alternative, refusing to load a file that predates a vocabulary
        change, would make every new level a breaking change to somebody's
        private configuration.

        `config/consistency.py` is where a candidate is TOLD that a level is
        unpriced; scoring it as zero is what keeps the product working while
        she decides.
        """
        return self.points.get(level, 0.0)

    def unpriced_levels(self) -> tuple[Seniority, ...]:
        """Levels this configuration says nothing about, in vocabulary order."""
        return tuple(level for level in Seniority if level not in self.points)


class CompensationComponent(_Section):
    max: float
    label: str
    salary_meets_target: float
    salary_below_target: float
    salary_unknown: float
    contract_preferred: float
    contract_unknown: float
    contract_unwanted: float


class ScoringComponents(_Section):
    responsibilities: WeightedComponent
    technologies: WeightedComponent
    automation_integration: WeightedComponent
    seniority: SeniorityComponent
    compensation_contract: CompensationComponent

    @property
    def maxima(self) -> dict[str, float]:
        return {name: getattr(self, name).max for name in type(self).model_fields}


class SoftPenalties(_Section):
    prominence_multipliers: dict[Prominence, float]
    weights: dict[str, float]


class Scoring(_Section):
    components: ScoringComponents
    soft_penalties: SoftPenalties


# =========================================================================
# 6. CONFIDENCE, PREFERENCES, THRESHOLDS
# =========================================================================


class ConfidenceItemConfig(_Section):
    points: int
    label: str
    min_chars: int | None = None


class Confidence(_Section):
    components: dict[str, ConfidenceItemConfig]


class RemotePreference(_Section):
    accepted_work_models: list[str] = Field(default_factory=list)
    require_remote: bool = False


class ContractPreference(_Section):
    preferred: list[str] = Field(default_factory=list)
    unwanted: list[str] = Field(default_factory=list)


class SeniorityPreference(_Section):
    #: Levels this search WANTS. A scoring preference: a posting at one of
    #: these earns its points, and a posting outside them earns fewer.
    preferred: list[Seniority] = Field(default_factory=list)
    #: Levels this search does not want to be SHOWN, which is a different
    #: question and used not to be askable at all.
    #:
    #: `preferred` only ever moved a score. On the real corpus that meant a
    #: Director role earned eight of ten seniority points and sat in the top
    #: twenty of a list somebody had built to find mid-to-senior work: not
    #: preferred, and recommended anyway.
    #:
    #: **Empty by default, and empty means exclude nothing.** "Not preferred"
    #: is not "prohibited", and inferring one from the other would decide for
    #: every candidate that the levels they did not list are levels they refuse.
    #: A candidate who wants to see staff roles and merely rank them lower
    #: leaves this empty and keeps exactly the behaviour they had.
    #:
    #: Hidden by DEFAULT, never deleted, and revealable: the same treatment as
    #: an ineligible posting, because it is the same kind of fact -- something
    #: set aside for a reason she can see and undo.
    excluded: list[Seniority] = Field(default_factory=list)


class TravelPreference(_Section):
    max_tolerated_pct: int


class CompensationPreference(_Section):
    target_monthly_amount: float
    currency: str
    period: str
    #: Empty by default, and that emptiness is load-bearing: with no dated rate
    #: a differing currency is never converted, it is reported as unknown.
    conversion_rates: dict[str, float] = Field(default_factory=dict)
    conversion_rates_dated: str | None = None


class RecencyPreference(_Section):
    fresh_days: int
    stale_days: int


class Preferences(_Section):
    remote: RemotePreference
    contract: ContractPreference
    seniority: SeniorityPreference
    travel: TravelPreference
    compensation: CompensationPreference
    recency: RecencyPreference


class Thresholds(_Section):
    shortlist_min_score: int
    local_ai_min_score: int
    display_min_score: int
    fit_bands: dict[str, int]
    confidence_bands: dict[str, int]


class SourceEntry(_Section):
    enabled: bool
    access: str
    region: str


class Sources(_Section):
    priority: list[str] = Field(default_factory=list)
    enabled: dict[str, SourceEntry] = Field(default_factory=dict)


class Regions(_Section):
    target: list[str] = Field(default_factory=list)
    opportunistic: list[str] = Field(default_factory=list)


# =========================================================================
# THE WHOLE FILE
# =========================================================================


class SearchConfig(_Section):
    """The entire search configuration, fragments already expanded."""

    schema_version: int
    config_version: int
    config_id: str
    label: str
    normalisation: Normalisation
    negation: Negation
    taxonomy: Taxonomy
    ambiguous_titles: dict[str, AmbiguityRule] = Field(default_factory=dict)
    lexicon: dict[str, LexiconSignal]
    prominence: ProminenceConfig
    screening: Screening
    eligibility: Eligibility
    scoring: Scoring
    confidence: Confidence
    preferences: Preferences
    thresholds: Thresholds
    sources: Sources
    regions: Regions

    @property
    def digest(self) -> str:
        """Content address of the loaded configuration.

        Stored beside a score so a row computed under different weights reads as
        stale rather than as a disagreement. Computed from the canonical JSON
        dump rather than the file bytes, because a reformatted comment is not a
        change of meaning and a re-expanded fragment is.
        """
        payload = json.dumps(
            self.model_dump(
                mode="json",
                exclude={
                    "scoring": {
                        "components": {
                            name: {"setup_weights"}
                            for name in (
                                "responsibilities",
                                "technologies",
                                "automation_integration",
                            )
                        }
                    }
                },
            ),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def all_rules(self) -> list[tuple[str, TitleRule]]:
        """Every taxonomy rule with its section name, in evaluation order."""
        return [
            (section, rule)
            for section in TAXONOMY_SECTIONS
            for rule in self.taxonomy.rules(section)
        ]


# =========================================================================
# LOADING
# =========================================================================


#: The neutral starting point. See `starter_search_path`.
STARTER_STEM = "search.starter.yaml"


def local_search_path(config_dir: Path) -> Path:
    return config_dir / f"{SEARCH_STEM}.local.yaml"


def starter_search_path(config_dir: Path) -> Path:
    """The NEUTRAL starting point, generated from the example.

    `search.worked-example.yaml` is a worked example and therefore somebody's actual
    search. This is the same machine with the person taken out, so a first run
    on a fresh clone does not begin by describing a career nobody here has.

    Generated by `scripts/make_starter_config.py`, and a test loads it through
    this loader, because a starting point that does not load is worse than no
    starting point at all.
    """
    return config_dir / STARTER_STEM


def example_search_path(config_dir: Path) -> Path:
    """The WORKED example: a complete, real search, and somebody's real search.

    Named `search.worked-example.yaml` rather than `search.worked-example.yaml`
    because the old name read as "the default one" and it is not: it is the
    owner's own Business Systems / Automation search, with their phrases, their
    country and the kinds of work they will not do. A stranger cloning this
    repository should be able to see that from the filename alone, in every
    place the filename appears.
    """
    return config_dir / f"{SEARCH_STEM}.worked-example.yaml"


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SearchConfigError(f"could not read {path}: {exc}") from exc

    try:
        parsed = safe_load(raw)
    except yaml.YAMLError as exc:
        raise SearchConfigError(f"{path.name} is not valid YAML: {exc}") from exc

    if parsed is None:
        raise SearchConfigError(f"{path.name} is empty")
    if not isinstance(parsed, dict):
        raise SearchConfigError(f"{path.name} must contain a mapping at the top level")
    return parsed


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"{path.name} is not valid:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<document>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)


def _expand_alternatives(values: Any, fragments: dict[str, Any], where: str) -> list[str]:
    """Replace every `$NAME` with the phrases it stands for.

    Done here rather than at match time so an unknown fragment is a load error
    with a path, instead of a rule that silently stops matching anything.
    """
    if not isinstance(values, list):
        raise SearchConfigError(f"{where}: expected a list of phrases, got {type(values).__name__}")

    expanded: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise SearchConfigError(f"{where}: expected a phrase, got {value!r}")
        reference = FRAGMENT_REFERENCE.match(value)
        if reference is None:
            expanded.append(value)
            continue
        name = reference.group(1)
        phrases = fragments.get(name)
        if phrases is None:
            known = ", ".join(sorted(fragments)) or "<none defined>"
            raise SearchConfigError(
                f"{where}: unknown fragment ${name}. taxonomy.fragments defines: {known}"
            )
        if not isinstance(phrases, list):
            raise SearchConfigError(f"taxonomy.fragments.{name}: expected a list of phrases")
        expanded.extend(str(phrase) for phrase in phrases)
    return expanded


def _expand_taxonomy(raw: dict[str, Any]) -> None:
    """Rewrite `raw["taxonomy"]` in place so validation sees literal phrases only."""
    taxonomy = raw.get("taxonomy")
    if not isinstance(taxonomy, dict):
        raise SearchConfigError("taxonomy: expected a mapping of rule sections")
    fragments = taxonomy.get("fragments") or {}
    if not isinstance(fragments, dict):
        raise SearchConfigError("taxonomy.fragments: expected a mapping of NAME -> phrases")

    for section in TAXONOMY_SECTIONS:
        rules = taxonomy.get(section) or []
        if not isinstance(rules, list):
            raise SearchConfigError(f"taxonomy.{section}: expected a list of rules")
        for index, rule in enumerate(rules):
            if not isinstance(rule, dict):
                raise SearchConfigError(f"taxonomy.{section}[{index}]: expected a mapping")
            rule_id = rule.get("id", index)
            where = f"taxonomy.{section}.{rule_id}"
            groups = rule.get("all_of")
            if not isinstance(groups, list):
                raise SearchConfigError(f"{where}.all_of: expected a list of alternative groups")
            rule["all_of"] = [
                _expand_alternatives(group, fragments, f"{where}.all_of[{position}]")
                for position, group in enumerate(groups)
            ]
            if "none_of" in rule:
                rule["none_of"] = _expand_alternatives(
                    rule["none_of"], fragments, f"{where}.none_of"
                )


def _require_signals(config: SearchConfig, names: list[tuple[str, str]]) -> None:
    for where, signal_id in names:
        if signal_id not in config.lexicon:
            raise SearchConfigError(
                f"{where}: {signal_id!r} is not a signal defined under `lexicon`. "
                "A weight or group keyed by an undefined signal can never fire."
            )


def _check_references(config: SearchConfig) -> None:
    """Everything that names something else, checked once, at load."""
    referenced: list[tuple[str, str]] = []

    for group in config.screening.required_any_groups:
        referenced += [
            (f"screening.required_any_groups.{group.id}.any_of", s) for s in group.any_of
        ]
    referenced += [("screening.required_all", s) for s in config.screening.required_all]

    for name in ("responsibilities", "technologies", "automation_integration"):
        component: WeightedComponent = getattr(config.scoring.components, name)
        referenced += [(f"scoring.components.{name}.weights", s) for s in component.weights]
    referenced += [
        ("scoring.soft_penalties.weights", s) for s in config.scoring.soft_penalties.weights
    ]

    for rule_name, ambiguity in config.ambiguous_titles.items():
        branches = (("promote_if", ambiguity.promote_if), ("demote_if", ambiguity.demote_if))
        for branch_name, branch in branches:
            if branch is None:
                continue
            where = f"ambiguous_titles.{rule_name}.{branch_name}.any_of"
            referenced += [(where, signal_id) for signal_id in branch.any_of]

    _require_signals(config, referenced)

    for section, rule in config.all_rules():
        if rule.ambiguity_rule is None:
            continue
        if rule.ambiguity_rule not in config.ambiguous_titles:
            raise SearchConfigError(
                f"taxonomy.{section}.{rule.id}.ambiguity_rule: "
                f"{rule.ambiguity_rule!r} is not a key under `ambiguous_titles`."
            )

    for rule_name, ambiguity in config.ambiguous_titles.items():
        targets = (("promote_to", ambiguity.promote_to), ("demote_to", ambiguity.demote_to))
        for field_name, target in targets:
            if target is not None and target not in CLASS_BY_NAME:
                known = ", ".join(sorted(CLASS_BY_NAME))
                raise SearchConfigError(
                    f"ambiguous_titles.{rule_name}.{field_name}: {target!r} is not a title class. "
                    f"Expected one of: {known}"
                )


def _check_totals(config: SearchConfig) -> None:
    """The fit budget must be positive; the confidence budget must be exactly 100.

    They differ because they are measured differently. `data_confidence` counts
    points out of a literal hundred, so its items have to sum to one. The match
    score is a PERCENTAGE of whatever budget the components declare -- see
    `match_score_from` -- so the budget may be any positive number and the
    thresholds keep their meaning. It was pinned at exactly 100 while the score
    was a bare sum; removing `role_family` is what separated the two rules.
    """
    maxima = config.scoring.components.maxima
    component_total = sum(maxima.values())
    if component_total <= 0:
        breakdown = ", ".join(f"{name}={value:g}" for name, value in maxima.items())
        raise SearchConfigError(
            f"scoring.components: maxima sum to {component_total:g}, which is not a "
            f"budget anything can be a percentage of ({breakdown})."
        )

    confidence_total = sum(item.points for item in config.confidence.components.values())
    if confidence_total != 100:
        raise SearchConfigError(
            f"confidence.components: points sum to {confidence_total}, not 100."
        )


def effective_search_path(config_dir: Path, *, use_example: bool = False) -> Path:
    """An example is explicit opt-in, never a new person's implicit profile."""
    if use_example:
        return example_search_path(config_dir)
    local = local_search_path(config_dir)
    return local if local.exists() else starter_search_path(config_dir)


def load_search_config(
    config_dir: Path,
    *,
    use_example: bool = False,
) -> tuple[SearchConfig, Path]:
    """Load a local search, otherwise the neutral starter; examples require opt-in.

    Returns the path actually read. Callers are expected to display it: the
    example is a legitimate configuration, but which file produced a score must
    never be a guess.
    """
    path = effective_search_path(config_dir, use_example=use_example)
    if not path.exists():
        raise SearchConfigError(
            f"no search configuration found in {config_dir}. Expected "
            f"{SEARCH_STEM}.local.yaml or {STARTER_STEM}. "
            "The worked example requires an explicit setup --example choice."
        )

    raw = _read_yaml(path)
    _expand_taxonomy(raw)
    try:
        config = SearchConfig.model_validate(raw)
    except ValidationError as exc:
        raise SearchConfigError(_format_validation_error(path, exc)) from exc

    _check_references(config)
    _check_totals(config)
    return config, path

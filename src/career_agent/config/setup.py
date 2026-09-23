"""First run: turning a person's answers into a search configuration.

WHAT THIS EXISTS FOR
--------------------
Until V3 this product had exactly one user, and the way you configured it was
to open a 1,117-line YAML file and edit it. That is a fine interface for the
person who wrote the file and an impossible one for anybody else, and the
project can no longer assume its owner is the only possible user.

WHAT IT WRITES, AND WHY IT WRITES THE WHOLE FILE
-------------------------------------------------
`config/search.local.yaml`, which is gitignored.

`load_search_config` reads the local file INSTEAD of the example, not on top of
it. There is no merge. So a setup that wrote only the answered keys would
produce a configuration missing its taxonomy, its lexicon and its scoring
weights, and it would fail validation rather than fall back. The wizard
therefore starts from a complete base (the example, or the person's existing
local file) and applies answers onto it.

That non-merging design is deliberate elsewhere and it stays: a configuration
assembled from two files at load time makes "which file produced this score"
unanswerable, and `search-config` exists precisely to answer it.

THE SHAPE: A PURE FUNCTION AND A THIN SHELL
--------------------------------------------
`apply_answers` takes a base mapping and an `Answers` value and returns a new
mapping. It prompts for nothing, reads no file and writes none. Everything
that could be got wrong is in there, and it is testable without a terminal.

The interactive part lives in the CLI and does one thing: fill in an `Answers`.
A wizard whose logic is tangled with its prompts can only be tested by driving
a terminal, which means in practice it is not tested.

WHAT IT NEVER DOES
------------------
It never writes to `search.worked-example.yaml`. The example is committed, it is
documentation of the schema, and a person's real salary target has no business
in a public repository. `write_local` refuses any path that is not the local
one, which is a cheap guard against a future caller passing the wrong argument.

ROLE EXAMPLES ARE SIGNALS, NOT A WHITELIST
-------------------------------------------
The single most important thing here. When a person says they are looking for
"Revenue Operations" work, that phrase becomes a LEXICON ENTRY matched against
the whole posting, not a filter on the job title. The founding complaint of
this product is that title search misses the work: the same job is posted as
"Business Systems Analyst", "RevOps Engineer" and "Sales Systems Manager", and
a title whitelist finds one of the three.

`taxonomy` and `ambiguous_titles` are left exactly as the base had them.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from career_agent.config.search_config import (
    SearchConfigError,
    example_search_path,
    load_search_config,
    local_search_path,
    starter_search_path,
)
from career_agent.yaml_io import safe_load

#: Where a person's own answers go. Gitignored, and the only file this writes.
LOCAL_STEM = "search.local.yaml"

#: The work models a person can accept. Our vocabulary, not a vendor's.
WORK_MODELS: tuple[str, ...] = ("REMOTE", "HYBRID", "ONSITE")

#: The contract shapes the scorer already knows about.
CONTRACT_TYPES: tuple[str, ...] = (
    "FULL_TIME_EMPLOYEE",
    "CONTRACTOR_B2B",
    "EOR",
    "PART_TIME",
    "INTERNSHIP",
)

SENIORITIES: tuple[str, ...] = ("JUNIOR", "MID", "SENIOR", "LEAD", "PRINCIPAL", "MANAGER")

#: Pay periods that can be compared. HOURLY is deliberately absent: comparing an
#: hourly rate to a monthly target needs an assumed working week, which no
#: posting states, so the scorer refuses it rather than guessing.
PERIODS: tuple[str, ...] = ("MONTH", "YEAR")


class SetupError(ValueError):
    """An answer does not describe something this configuration can express."""


@dataclass(frozen=True, slots=True)
class Answers:
    """Everything the first run asks, in one value.

    Every field has a default that means "leave the base alone". A person who
    presses Enter through the whole wizard gets a working configuration
    identical to the shipped example, which is the correct outcome: the example
    IS a valid configuration, and pretending otherwise would force somebody to
    invent preferences before they have seen a single posting.
    """

    #: Kinds of work, in the person's own words. Become lexicon entries matched
    #: against the whole posting. NOT a title filter. See the module docstring.
    role_examples: tuple[str, ...] | None = None
    #: Tools and systems. Also lexicon entries, in the technology family.
    skills: tuple[str, ...] | None = None
    #: Extra phrases worth points wherever they appear.
    keywords: tuple[str, ...] = ()
    #: Phrases that make a posting less interesting without excluding it.
    negative_keywords: tuple[str, ...] = ()
    #: Phrases that a posting must SAY for it to be ruled out. Silence never
    #: excludes, which is why these are patterns and not the absence of one.
    hard_exclusions: tuple[str, ...] = ()

    #: ISO 3166-1 alpha-2 of where the person lives and intends to remain.
    residence_country: str | None = None
    #: Region labels the scorer already knows: LATAM, EMEA, AMERICAS, and so on.
    target_regions: tuple[str, ...] = ()
    accepted_work_models: tuple[str, ...] = ()
    require_remote: bool | None = None

    #: True when the person needs an employer to sponsor or arrange the right
    #: to work. It does NOT create a blocker on its own: it decides whether the
    #: sponsorship blockers in the base are kept or dropped.
    needs_visa_sponsorship: bool | None = None

    preferred_contracts: tuple[str, ...] = ()
    preferred_seniorities: tuple[str, ...] = ()

    target_amount: int | None = None
    currency: str | None = None
    period: str | None = None

    #: At or above this a job is worth a human look.
    shortlist_min_score: int | None = None
    #: Days after which a posting is shown but marked as ageing.
    fresh_days: int | None = None

    label: str | None = None

    def is_empty(self) -> bool:
        """True when nothing was answered. Used to say so rather than to guess."""
        return (
            all(
                not value
                for value in (
                    self.role_examples,
                    self.skills,
                    self.keywords,
                    self.negative_keywords,
                    self.hard_exclusions,
                    self.residence_country,
                    self.target_regions,
                    self.accepted_work_models,
                    self.preferred_contracts,
                    self.preferred_seniorities,
                    self.target_amount,
                    self.currency,
                    self.period,
                    self.shortlist_min_score,
                    self.fresh_days,
                    self.label,
                )
            )
            and self.role_examples is None
            and self.skills is None
            and self.require_remote is None
            and self.needs_visa_sponsorship is None
        )


@dataclass
class SetupResult:
    """What the wizard changed, so it can report rather than claim."""

    path: Path
    config_version: int
    started_from: str
    signals_added: list[str] = field(default_factory=list)
    #: Phrases that were NOT added, because the configuration already scores
    #: them, as `(what you typed, the entry that already covers it)`.
    #:
    #: Reported rather than swallowed. Skipping is the right thing to do -- a
    #: second entry for one phrase fires on the same postings and adds its
    #: points again -- but a person who typed "HubSpot" and is told nothing
    #: happened will type it again. Told "already covered by HubSpot platform
    #: ownership", they have learnt something about their own configuration.
    signals_already_present: list[tuple[str, str]] = field(default_factory=list)
    blockers_added: list[str] = field(default_factory=list)
    blockers_removed: list[str] = field(default_factory=list)
    changed_sections: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "config_version": self.config_version,
            "started_from": self.started_from,
            "signals_added": self.signals_added,
            "signals_already_present": [list(pair) for pair in self.signals_already_present],
            "blockers_added": self.blockers_added,
            "blockers_removed": self.blockers_removed,
            "changed_sections": self.changed_sections,
        }


def slugify(text: str) -> str:
    """A stable lexicon id from a phrase a person typed.

    Lowercase, non-alphanumerics collapsed to underscores. Deliberately plain:
    this id appears in `job_match.membership`, in a facet and in a filter URL,
    so it has to survive a round trip through all three without escaping.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    return slug or "unnamed"


def _phrase_index(entries: Any) -> dict[str, str]:
    """`folded pattern -> the id of the entry carrying it`.

    What `_unique_id` could not answer. Minting `base_2` is correct when two
    DIFFERENT phrases slugify the same way; it is wrong when the phrase is
    already present, which on a second run is every phrase a person typed the
    first time, because the base is now their own file.

    Accepts both shapes the configuration uses: `lexicon` is a mapping of
    id -> entry, `eligibility.blockers` is a list of entries carrying an `id`.
    """
    index: dict[str, str] = {}
    items: list[tuple[str, Any]]
    if isinstance(entries, dict):
        items = list(entries.items())
    elif isinstance(entries, list):
        items = [(str(e.get("id")), e) for e in entries if isinstance(e, dict)]
    else:
        return index
    for entry_id, entry in items:
        if not isinstance(entry, dict):
            continue
        for pattern in entry.get("patterns") or []:
            folded = " ".join(str(pattern).split()).lower()
            if folded:
                index.setdefault(folded, entry_id)
    return index


def _unique_id(base: str, taken: set[str]) -> str:
    """`base`, or `base_2`, `base_3`. Never silently overwrites another signal."""
    if base not in taken:
        return base
    for suffix in range(2, 100):
        candidate = f"{base}_{suffix}"
        if candidate not in taken:
            return candidate
    raise SetupError(f"could not find a free id for {base!r}")


# =========================================================================
# the transform
# =========================================================================


def _as_blockers(config: dict[str, Any]) -> list[dict[str, Any]]:
    """`eligibility.blockers` out of a configuration mapping, defensively."""
    eligibility = config.get("eligibility")
    if not isinstance(eligibility, dict):
        return []
    blockers = eligibility.get("blockers")
    if not isinstance(blockers, list):
        return []
    return [b for b in blockers if isinstance(b, dict)]


def apply_answers(
    base: dict[str, Any],
    answers: Answers,
    *,
    restore_from: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], SetupResult]:
    """A new configuration mapping, with the answers applied onto `base`.

    Pure: no file is read, no file is written, and `base` is not mutated. The
    result is a deep copy, so a caller holding the base still holds the base.

    Every unanswered field leaves its section exactly as it was, and every
    ANSWERED field is applied to the same end state whatever the base holds.
    Both halves are what makes the wizard safe to re-run: running it twice
    with the same answers must produce the same configuration and must not
    bump `config_version` the second time.

    `restore_from` is the shipped example, and it is needed for exactly one
    thing: answering "yes, I need sponsorship" after having answered "no" has
    to put back the work_authorization blockers that the "no" removed, and by
    then they are in no file the wizard can see. Passing it keeps this
    function pure; `run_setup` is what reads the file.
    """
    import copy

    config = copy.deepcopy(base)
    result = SetupResult(
        path=Path(LOCAL_STEM),
        config_version=int(config.get("config_version", 1)),
        started_from="",
    )

    _apply_signals(config, answers, result)
    _apply_exclusions(config, answers, result, restore_from)
    _apply_place(config, answers, result)
    _apply_shape(config, answers, result)
    _apply_pay(config, answers, result)
    _apply_thresholds(config, answers, result)

    if answers.label:
        config["label"] = answers.label
        result.changed_sections.append("label")

    # The version is bumped whenever anything changed, and only then. Every
    # stored score is keyed on `(config_id, config_version)`, so a bump is what
    # says "the scores you have describe the previous question". Bumping it for
    # a run that changed nothing would invalidate a whole corpus for free.
    #
    # The test is the CONFIGURATION, not the bookkeeping. Reporting a section
    # as changed and changing it are two different things, and an independent
    # functional review caught three consecutive runs with identical answers
    # bumping 2 -> 3 -> 4 on the strength of the bookkeeping alone. `config`
    # is a deep copy of `base`, so this comparison is exactly the question:
    # would writing this file change anything?
    if config != base:
        result.config_version = int(config.get("config_version", 1)) + 1
        config["config_version"] = result.config_version
    else:
        # Nothing moved, so nothing is reported as having moved either.
        result.changed_sections.clear()
        result.signals_added.clear()
        # `signals_already_present` is deliberately NOT cleared: it is not a
        # record of a change, it is the answer to "why did nothing happen?".
        result.blockers_added.clear()
        result.blockers_removed.clear()

    return config, result


def _allocate_intent(config: dict[str, Any], category: str, phrases: tuple[str, ...]) -> bool:
    """Replace only weights whose last generated value is still owned by setup."""
    component = config["scoring"]["components"][category]
    weights = component["weights"]
    receipt = component.get("setup_weights", {})
    previous_weights = dict(weights)
    for signal_id, generated in receipt.items():
        if weights.get(signal_id) == generated:
            del weights[signal_id]
    index = _phrase_index(config["lexicon"])
    active = sorted(
        {
            index[folded]
            for phrase in phrases
            if (folded := " ".join(phrase.split()).lower()) in index
        }
        - weights.keys()
    )
    generated_weights: dict[str, float] = {}
    if active:
        maximum = float(component["max"])
        if not math.isfinite(maximum) or maximum < 0:
            raise SetupError("component maximum must be finite and nonnegative")
        weight = maximum / len(active)
        while sum([weight] * len(active)) > maximum:
            weight = math.nextafter(weight, 0.0)
        generated_weights = dict.fromkeys(active, weight)
        weights.update(generated_weights)
    if generated_weights:
        component["setup_weights"] = generated_weights
    else:
        component.pop("setup_weights", None)
    return weights != previous_weights or generated_weights != receipt


def _apply_signals(config: dict[str, Any], answers: Answers, result: SetupResult) -> None:
    """Role examples, skills and keywords all become lexicon entries.

    All three land in the same place, because to the scorer they are the same
    kind of thing: a named group of phrases matched against the whole posting.
    What differs is what each carries beside its patterns, which is what lets
    the interface tell a tool apart from a kind of work.
    """
    lexicon = config.setdefault("lexicon", {})
    if not isinstance(lexicon, dict):
        raise SetupError("lexicon must be a mapping")
    taken = set(lexicon)

    # Folded phrases already added in THIS run. Without it, answering
    # "Python", "python" and "  PYTHON  " minted three signals that can never
    # behave differently, because every matcher folds case before comparing.
    # The FIRST spelling wins the label, so the person sees what they typed.
    added_phrases: set[str] = set()

    # And the phrases the base ALREADY holds, which is the same question asked
    # of a previous run. Without it, re-running the wizard with the same
    # answers minted `revenue_operations_2`, then `_3`, scoring one phrase
    # three times and bumping `config_version` -- which invalidates every
    # stored score in the corpus -- for a run that changed nothing.
    held = _phrase_index(lexicon)

    def add(phrase: str, extra: dict[str, Any]) -> None:
        cleaned = " ".join(str(phrase).split())
        if not cleaned:
            return
        folded = cleaned.lower()
        if folded in added_phrases:
            return
        added_phrases.add(folded)
        # Already scored, under whatever id it was first given. Not re-added
        # and not renamed: a second entry for one phrase is a second copy of
        # its points, and the person did not ask to be scored twice.
        if folded in held:
            result.signals_already_present.append((cleaned, held[folded]))
            return
        entry_id = _unique_id(slugify(cleaned), taken)
        taken.add(entry_id)
        entry: dict[str, Any] = {"label": cleaned, "patterns": [cleaned.lower()]}
        # The phrase itself is the only pattern that can honestly be added. A
        # wizard that invented synonyms would be putting words in a person's
        # mouth and then scoring postings against them. The preference editor
        # is where more patterns get added, by the person, with the corpus in
        # front of them.
        entry.update(extra)
        lexicon[entry_id] = entry
        held[folded] = entry_id
        result.signals_added.append(entry_id)

    # `responsibility_other`, not a new category minted from the phrase.
    # `ResponsibilityCategory` is a CLOSED enum shared with the extraction
    # contract: a model observing a posting emits one of those thirty-nine
    # values, and a configuration inventing a fortieth would name something no
    # observation can ever carry. `responsibility_other` exists for exactly
    # this, and the validator rejected the alternative before anything was
    # written, which is why `run_setup` validates before it writes.
    for value in sorted(answers.role_examples or (), key=lambda v: v.lower()):
        add(value, {"responsibility": "responsibility_other"})
    # Skills carry no extra key at all. `LexiconSignal` has four fields and
    # `software` is not one of them.
    #
    # KNOWN LIMITATION, recorded rather than hidden: a skill added here scores
    # correctly, appears in the drawer and can be filtered as a signal, but it
    # does NOT join the "Tools named in the posting" facet.
    # `match.lexicon.TECHNOLOGY_SIGNALS` is a frozen set of nine ids belonging
    # to this repository's original owner, and the facet is computed in
    # `ScoredJobQuery.facets`, which holds a connection and no configuration.
    # Making it configurable means tagging technology entries into
    # `job_match.membership` at score time, the way `|fired:id|` already works.
    # That is a scorer change with a rescore behind it, and it is in the
    # backlog rather than smuggled into a setup wizard.
    for value in sorted(answers.skills or (), key=lambda v: v.lower()):
        add(value, {})
    for category, values in (
        ("responsibilities", answers.role_examples),
        ("technologies", answers.skills),
    ):
        if values is not None and _allocate_intent(config, category, values):
            result.changed_sections.append("intent weights")
    for value in answers.keywords:
        add(value, {})

    # Reported only when something was actually added. It used to fire
    # whenever the ANSWER was non-empty, so re-running with the same answers
    # reported a change that had not happened -- and a reported change bumps
    # `config_version`, which invalidates every score in the corpus.
    if result.signals_added:
        result.changed_sections.append("what you are looking for")

    if answers.negative_keywords:
        weights = (
            config.setdefault("scoring", {})
            .setdefault("soft_penalties", {})
            .setdefault("weights", {})
        )
        for value in answers.negative_keywords:
            cleaned = " ".join(str(value).split())
            if not cleaned:
                continue
            folded = cleaned.lower()
            # An entry for this phrase already exists -- from a previous run,
            # or from the shipped example. The weight is set on THAT id rather
            # than on a second entry, so re-running applies one penalty rather
            # than one per run. Setting it again is the same number, so the
            # section is only reported as changed when something moved.
            entry_id = held.get(folded)
            if entry_id is not None:
                result.signals_already_present.append((cleaned, entry_id))
            if entry_id is None:
                entry_id = _unique_id(slugify(cleaned), taken)
                taken.add(entry_id)
                lexicon[entry_id] = {"label": cleaned, "patterns": [folded]}
                held[folded] = entry_id
                result.signals_added.append(entry_id)
            # A negative signal IS a lexicon entry that carries a negative
            # weight; it is not a separate list. `config/preferences.py` already
            # records why: pointing that category anywhere else offered tuning
            # knobs and a mapping of numbers instead of phrases.
            if weights.get(entry_id) != -3:
                weights[entry_id] = -3
                result.changed_sections.append("what you want less of")


def _apply_exclusions(
    config: dict[str, Any],
    answers: Answers,
    result: SetupResult,
    restore_from: dict[str, Any] | None = None,
) -> None:
    """Hard exclusions, and the sponsorship blockers that stop applying.

    A blocker is a phrase a posting must SAY. There is no way to express "rule
    this out if the posting does not mention X", and that is invariant 2 rather
    than a missing feature: silence never excludes.
    """
    eligibility = config.setdefault("eligibility", {})
    blockers = eligibility.setdefault("blockers", [])
    if not isinstance(blockers, list):
        raise SetupError("eligibility.blockers must be a list")

    taken = {str(b.get("id")) for b in blockers if isinstance(b, dict)}
    # The same correction as in `_apply_signals`, for the same reason: a
    # re-run was appending `security_clearance_required_2`, `_3`, `_4`, each
    # one a second copy of a rule the person stated once.
    held = _phrase_index(blockers)
    for value in answers.hard_exclusions:
        phrase = " ".join(str(value).split())
        if not phrase:
            continue
        if phrase.lower() in held:
            continue
        blocker_id = _unique_id(slugify(phrase), taken)
        taken.add(blocker_id)
        held[phrase.lower()] = blocker_id
        blockers.append(
            {
                "id": blocker_id,
                "label": phrase,
                # `requirement`: a requirement the person named. Filing it
                # under geography or clearance would put it in a gate whose
                # reasoning it does not follow and whose UNRESOLVED sentence
                # would be wrong. It used to be written as `other`, a gate no
                # code evaluated, so a typed hard exclusion closed nothing;
                # the loader refuses an unknown gate now.
                "gate": "requirement",
                "patterns": [phrase.lower()],
                "negation_sensitive": True,
            }
        )
        result.blockers_added.append(blocker_id)
    if result.blockers_added:
        result.changed_sections.append("hard exclusions")

    # Somebody who does NOT need sponsorship is not blocked by a posting that
    # declines to sponsor. Keeping those blockers would rule out jobs they can
    # actually take, which is the expensive direction to be wrong in.
    #
    # BOTH answers do something definite, and that is the correction. `False`
    # used to delete the work_authorization blockers and `True` used to do
    # nothing -- so a person who answered "no" and then changed their mind got
    # silence, because the base was by then their own file without them. An
    # answer whose effect depends on what you answered last time is not an
    # answer to a question.
    #
    # `True` restores them from `restore_from`, which is the shipped example.
    # That is the only honest source: the blockers are a piece of shared
    # vocabulary the project ships, not something the person wrote, so there
    # is nothing of theirs to lose and nothing to invent.
    if answers.needs_visa_sponsorship is False:
        keep = []
        for blocker in blockers:
            if isinstance(blocker, dict) and blocker.get("gate") == "work_authorization":
                result.blockers_removed.append(str(blocker.get("id")))
                continue
            keep.append(blocker)
        if result.blockers_removed:
            eligibility["blockers"] = keep
            result.changed_sections.append("work authorization")
    elif answers.needs_visa_sponsorship is True and restore_from is not None:
        present = {str(b.get("id")) for b in blockers if isinstance(b, dict)}
        source = _as_blockers(restore_from)
        for blocker in source:
            if blocker.get("gate") != "work_authorization":
                continue
            if str(blocker.get("id")) in present:
                continue
            import copy as _copy

            blockers.append(_copy.deepcopy(blocker))
            result.blockers_added.append(str(blocker.get("id")))
            result.changed_sections.append("work authorization")


def _apply_place(config: dict[str, Any], answers: Answers, result: SetupResult) -> None:
    if answers.residence_country:
        country = str(answers.residence_country).strip().upper()
        if len(country) != 2 or not country.isalpha():
            raise SetupError(
                "residence country must be a two-letter ISO 3166-1 code, got "
                f"{answers.residence_country!r}"
            )
        config.setdefault("eligibility", {})["candidate_country"] = country
        result.changed_sections.append("where you live")

    if answers.target_regions:
        config.setdefault("regions", {})["target"] = [
            str(region).strip().upper() for region in answers.target_regions if str(region).strip()
        ]
        result.changed_sections.append("regions you would work in")

    remote = config.setdefault("preferences", {}).setdefault("remote", {})
    if answers.accepted_work_models:
        models = [str(model).strip().upper() for model in answers.accepted_work_models]
        unknown = [model for model in models if model not in WORK_MODELS]
        if unknown:
            raise SetupError(f"unknown work model(s): {', '.join(unknown)}")
        remote["accepted_work_models"] = models
        result.changed_sections.append("office or remote")
    if answers.require_remote is not None:
        remote["require_remote"] = bool(answers.require_remote)
        result.changed_sections.append("remote requirement")


def _apply_shape(config: dict[str, Any], answers: Answers, result: SetupResult) -> None:
    preferences = config.setdefault("preferences", {})
    if answers.preferred_contracts:
        contracts = [str(value).strip().upper() for value in answers.preferred_contracts]
        unknown = [value for value in contracts if value not in CONTRACT_TYPES]
        if unknown:
            raise SetupError(f"unknown contract type(s): {', '.join(unknown)}")
        preferences.setdefault("contract", {})["preferred"] = contracts
        result.changed_sections.append("contract types")
    if answers.preferred_seniorities:
        levels = [str(value).strip().upper() for value in answers.preferred_seniorities]
        unknown = [value for value in levels if value not in SENIORITIES]
        if unknown:
            raise SetupError(f"unknown level(s): {', '.join(unknown)}")
        preferences.setdefault("seniority", {})["preferred"] = levels
        result.changed_sections.append("levels")


def _apply_pay(config: dict[str, Any], answers: Answers, result: SetupResult) -> None:
    """A target, and the currency it is meaningless without.

    The two move together on purpose. Nothing in this system converts between
    currencies without a dated rate, so an amount with no currency compares
    figures that are not comparable, and the filter API already refuses that.
    Refusing it here too means the refusal happens where a person can still
    answer the question.
    """
    if answers.target_amount is None and not answers.currency and not answers.period:
        return
    if answers.target_amount is not None and not answers.currency:
        raise SetupError(
            "a pay target needs a currency. Nothing here converts between currencies, "
            "so an amount on its own would compare figures that are not comparable."
        )
    compensation = config.setdefault("preferences", {}).setdefault("compensation", {})
    if answers.target_amount is not None:
        if answers.target_amount < 0:
            raise SetupError("a pay target cannot be negative")
        compensation["target_monthly_amount"] = int(answers.target_amount)
    if answers.currency:
        currency = str(answers.currency).strip().upper()
        if len(currency) != 3 or not currency.isalpha():
            raise SetupError(f"currency must be a three-letter code, got {answers.currency!r}")
        compensation["currency"] = currency
    if answers.period:
        period = str(answers.period).strip().upper()
        if period not in PERIODS:
            raise SetupError(f"pay period must be one of {', '.join(PERIODS)}, got {period}")
        compensation["period"] = period
    result.changed_sections.append("pay")


def _apply_thresholds(config: dict[str, Any], answers: Answers, result: SetupResult) -> None:
    if answers.shortlist_min_score is not None:
        score = int(answers.shortlist_min_score)
        if not 0 <= score <= 100:
            raise SetupError(f"the shortlist score must be between 0 and 100, got {score}")
        config.setdefault("thresholds", {})["shortlist_min_score"] = score
        result.changed_sections.append("shortlist score")
    if answers.fresh_days is not None:
        days = int(answers.fresh_days)
        if days < 1:
            raise SetupError(f"freshness must be at least one day, got {days}")
        config.setdefault("preferences", {}).setdefault("recency", {})["fresh_days"] = days
        result.changed_sections.append("how fresh is fresh")


# =========================================================================
# reading and writing, and the guards on both
# =========================================================================


def base_config(config_dir: Path, *, worked_example: bool = False) -> tuple[dict[str, Any], str]:
    """The mapping to apply answers onto, and a word for where it came from.

    Three sources, in this order.

    **An existing `search.local.yaml` always wins.** Re-running the wizard is
    an edit, not a reset, and a first run that started from a shipped file
    would silently discard every preference tuned since M3. Neither flag
    overrides this: `forget settings` is the command that discards, and it
    asks first.

    **Otherwise `search.starter.yaml`, which is the neutral one.** Same
    machinery, no person: no phrases, no title rules, no country, no blockers,
    no penalties. This is the DEFAULT, and it did not use to be. A stranger
    cloning this repository and running `setup` inherited Brazil, forty-nine
    phrases about HubSpot and n8n, fourteen title rules and twenty penalties
    for work the owner does not want -- and then, because the wizard only ever
    ADDS to the lexicon, their own answers landed on top of all of it. Their
    search was the owner's search plus their words.

    **`worked_example` asks for the owner's search on purpose.** It is a real
    one, and starting from a real one and editing it is genuinely easier than
    starting from nothing when the thing being described is a kind of work.
    That is a choice somebody can now make rather than one made for them.
    """
    local = local_search_path(config_dir)
    if local.exists():
        return _read(local), local.name

    if worked_example:
        example = example_search_path(config_dir)
        if not example.exists():
            raise SetupError(f"{example.name} does not exist in {config_dir}")
        return _read(example), example.name

    starter = starter_search_path(config_dir)
    if not starter.exists():
        raise SetupError(
            f"no configuration to start from: neither {local.name} nor {starter.name} "
            f"exists in {config_dir}"
        )
    return _read(starter), starter.name


def _read(path: Path) -> dict[str, Any]:
    try:
        parsed = safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise SetupError(f"could not read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise SetupError(f"{path.name} is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise SetupError(f"{path.name} must contain a mapping at the top level")
    return parsed


HEADER = """\
# =====================================================================
# YOUR search configuration. Written by `career-agent setup`.
#
# THIS FILE IS YOURS AND IS NEVER COMMITTED. `config/*.local.yaml` is
# gitignored, and it stays on this machine. It holds what you are looking
# for, where you can work and what you are paid, which is nobody else's
# business.
#
# It REPLACES `search.worked-example.yaml` rather than merging with it, so this
# file is complete on its own. Editing it by hand is expected and fine.
#
# After changing anything here, re-score:
#     uv run career-agent rescore
#
# Scores are stored against `config_version`, so the ones you already have
# describe the previous version of this file until you do.
# =====================================================================

"""


def write_local(config_dir: Path, config: dict[str, Any]) -> Path:
    """Write `search.local.yaml`, and refuse to write anything else.

    The failure this prevents is not cheap: a caller passing the EXAMPLE path
    would overwrite committed documentation with somebody's real salary
    target, and the mistake would be invisible until a `git status` nobody ran.

    The guard used to read `if local_search_path(config_dir).name != LOCAL_STEM`,
    which cannot be false -- `local_search_path` builds the name itself -- so
    the check was unreachable and the test covering it was a tautology, as an
    independent functional review pointed out. The mistake it was written for
    is passing a FILE where a directory belongs, and that is what it now
    catches: `write_local(config_dir / "search.worked-example.yaml", ...)` would
    otherwise take `mkdir(parents=True)` down a path with a file in the middle
    of it and fail on an error naming neither the caller nor the cause.
    """
    if config_dir.is_file():
        raise SetupError(
            f"setup writes {LOCAL_STEM} into a directory, and {config_dir.name} is a file. "
            "Nothing was written."
        )
    path = local_search_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(config, sort_keys=False, allow_unicode=True, width=100)
    from career_agent.config.candidate_writer import _write_atomically

    _write_atomically(path, HEADER + body)
    return path


def run_setup(config_dir: Path, answers: Answers, *, worked_example: bool = False) -> SetupResult:
    """Apply answers, validate the result, and only then write it.

    Validation before writing is the whole point of the order. A configuration
    this wizard produced that does not load would leave the product broken with
    no obvious way back, and the person who just answered twelve questions has
    no reason to suspect the file. So it is validated in memory, and a failure
    leaves the previous file exactly where it was.
    """
    base, started_from = base_config(config_dir, worked_example=worked_example)
    # The shipped example, read here rather than inside `apply_answers`, which
    # stays pure. It is only consulted to put back the work_authorization
    # blockers when somebody changes their answer about sponsorship; a missing
    # example is therefore not an error, it just means there is nothing to
    # restore from.
    example = example_search_path(config_dir)
    restore_from = _read(example) if example.exists() else None
    config, result = apply_answers(base, answers, restore_from=restore_from)
    result.started_from = started_from

    _validate(config_dir, config)

    result.path = write_local(config_dir, config)
    return result


#: How a failed validation is explained, per caller.
#:
#: Two callers, two situations, and one message for both was wrong: importing a
#: file somebody else wrote reported "the answers produced a configuration",
#: which names the wrong culprit and sends the person back to a wizard they did
#: not use.
_WHY_INVALID = {
    "setup": "the answers produced a configuration that does not load",
    "import": "that file is not a valid search configuration",
}


def _validate(config_dir: Path, config: dict[str, Any], *, doing: str = "setup") -> None:
    """Load the candidate configuration through the REAL loader, in a temp dir.

    The real one, not a re-implementation. A private validator would drift from
    the thing that actually reads the file, and the first sign of the drift
    would be a configuration this module called valid and the product refused.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        probe = Path(tmp)
        # Nothing else is copied in. The loader reads exactly one file, and a
        # probe directory holding only that file proves the file stands alone,
        # which is what `search.local.yaml` has to do: it REPLACES the example
        # rather than merging with it.
        (probe / LOCAL_STEM).write_text(
            yaml.safe_dump(config, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        try:
            load_search_config(probe)
        except SearchConfigError as exc:
            raise SetupError(
                f"{_WHY_INVALID.get(doing, _WHY_INVALID['setup'])}, so nothing was "
                f"written and your existing settings are untouched.\n\n{exc}"
            ) from exc


# =========================================================================
# export and import
# =========================================================================


def export_profile(config_dir: Path, destination: Path) -> Path:
    """Copy the local configuration somewhere the person chose.

    A plain copy with a provenance header, not a new format. The file IS the
    profile; inventing an export envelope would mean a second schema to keep in
    step with the first, and the first already validates.
    """
    local = local_search_path(config_dir)
    if not local.exists():
        raise SetupError(
            f"there is no {LOCAL_STEM} to export. Run `career-agent setup` first, or "
            "you are still using the shipped example, which is already in the repository."
        )
    stamp = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    note = (
        f"# Exported from Career Agent on {stamp}.\n"
        "# This is your own search configuration. It contains your preferences,\n"
        "# and it may contain a pay target. Treat it the way you would treat that.\n"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(note + local.read_text(encoding="utf-8"), encoding="utf-8")
    return destination


def import_profile(config_dir: Path, source: Path, *, overwrite: bool = False) -> Path:
    """Install a configuration file as the local one, after checking it loads.

    Refuses to overwrite without being told to, and refuses a file that does
    not load at all. Importing a broken profile over a working one would break
    the product with a file the person did not write and cannot read.
    """
    if not source.exists():
        raise SetupError(f"no such file: {source}")
    local = local_search_path(config_dir)
    if local.exists() and not overwrite:
        raise SetupError(
            f"{local.name} already exists. Export it first if you want to keep it, then "
            "import again with --overwrite."
        )
    config = _read(source)
    _validate(config_dir, config, doing="import")
    return write_local(config_dir, config)

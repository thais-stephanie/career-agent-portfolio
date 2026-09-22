"""Reading a job title, and letting the description overrule it.

A title is the cheapest signal in a posting and the least reliable one. "GTM
Engineer" is systems work at one company and outbound sales at another; "Software
Engineer, Shared Services" is an internal business platform at one and a
distributed backend at another. So the title is never the answer here -- it opens
a door, and the description decides whether to walk through it.

Two ordering decisions carry the weight.

**Excluded is consulted first.** An off-target title that also happens to contain
a primary rule's words must not be rescued by the primary rule. This is
deprioritisation, not disqualification (ADR-0004): an excluded title zeroes the
role-family component and takes the configured penalty, and the job stays
visible and explainable.

**Demotion is evaluated before promotion.** A strong off-target signal beats a
weak on-target one, and evaluating in the other order would let two passing
mentions of `api_integration` promote a posting whose responsibilities section
is entirely cold calling.

A conditional title that is not promoted stays CONDITIONAL. There is no path in
this module that upgrades a title on the strength of the title alone.
"""

from __future__ import annotations

from career_agent.config.search_config import (
    CLASS_BY_NAME,
    TAXONOMY_SECTIONS,
    AmbiguityBranch,
    SearchConfig,
    TitleRule,
)
from career_agent.domain.matching import (
    ObservedSignal,
    TitleAdjustment,
    TitleClass,
    TitleClassification,
)
from career_agent.match.lexicon import compile_patterns, fired_at_least
from career_agent.match.text import fold


def _contains_any(folded_title: str, alternatives: list[str]) -> bool:
    """Token-boundary containment, so `$AI` never fires inside "chain"."""
    if not alternatives:
        return False
    return compile_patterns(tuple(alternatives)).regex.search(folded_title) is not None


def rule_matches(folded_title: str, rule: TitleRule) -> bool:
    """Every `all_of` group contributes an alternative, and no `none_of` one does."""
    if not all(_contains_any(folded_title, group) for group in rule.all_of):
        return False
    return not _contains_any(folded_title, rule.none_of)


def _branch_signals(
    branch: AmbiguityBranch, observed: dict[str, ObservedSignal]
) -> tuple[str, ...]:
    return tuple(
        signal_id
        for signal_id in branch.any_of
        if fired_at_least(observed, signal_id, branch.min_prominence)
    )


def _named(signal_ids: tuple[str, ...], observed: dict[str, ObservedSignal]) -> str:
    """Signal ids as the LABELS the configuration gave them.

    The reason string is read by a person in the drawer, and it used to say
    "the description centres on quota_carrying_sales, cold_outbound,
    sales_development". Three identifiers this system minted, in the sentence
    that is supposed to explain a decision about somebody's career.

    `observed` carries an entry for every signal in the lexicon, each with its
    label, so this is a lookup rather than a change of behaviour: the SIGNALS
    that fired and the class they resolved to are both untouched.
    """
    return ", ".join(
        observed[signal_id].label if signal_id in observed else signal_id
        for signal_id in signal_ids
    )


def _branch_fires(
    branch: AmbiguityBranch | None, observed: dict[str, ObservedSignal]
) -> tuple[str, ...] | None:
    if branch is None:
        return None
    supporting = _branch_signals(branch, observed)
    return supporting if len(supporting) >= branch.min_signals else None


def classify_title(
    config: SearchConfig, title: str, observed: dict[str, ObservedSignal]
) -> TitleClassification:
    """Classify the title, then let the description promote or demote it."""
    folded, _ = fold(title)

    matched_section: str | None = None
    matched_rule: TitleRule | None = None
    for section in TAXONOMY_SECTIONS:
        for rule in config.taxonomy.rules(section):
            if rule_matches(folded, rule):
                matched_section, matched_rule = section, rule
                break
        if matched_rule is not None:
            break

    if matched_rule is None or matched_section is None:
        return TitleClassification(
            base_class=TitleClass.UNCLASSIFIED,
            resolved_class=TitleClass.UNCLASSIFIED,
            adjustment=TitleAdjustment.NONE,
            reason=(
                "No configured title rule recognised this title. The posting is scored on "
                "its description alone."
            ),
        )

    base_class = CLASS_BY_NAME[matched_section]
    if matched_rule.ambiguity_rule is None:
        # An EXCLUDED match is not a neutral classification and should not be
        # reported as one. "The title matches 'Salesforce-centred specialist
        # role', which needs no interpretation" is exactly right about the
        # rule that fired and says nothing about WHY the posting was set
        # aside, which is the only thing a person reading it wants to know.
        reason = (
            f"The title matches {matched_rule.label!r}, which is a kind of role you "
            "asked to leave out."
            if base_class is TitleClass.EXCLUDED
            else f"The title matches {matched_rule.label!r}, which needs no interpretation."
        )
        return TitleClassification(
            base_class=base_class,
            resolved_class=base_class,
            adjustment=TitleAdjustment.NONE,
            rule_id=matched_rule.id,
            rule_label=matched_rule.label,
            reason=reason,
        )

    ambiguity = config.ambiguous_titles[matched_rule.ambiguity_rule]

    # Demotion first: a strong off-target signal wins over a weak on-target one.
    demoting = _branch_fires(ambiguity.demote_if, observed)
    if demoting is not None and ambiguity.demote_to is not None:
        resolved = CLASS_BY_NAME[ambiguity.demote_to]
        return TitleClassification(
            base_class=base_class,
            resolved_class=resolved,
            adjustment=TitleAdjustment.DEMOTED,
            rule_id=matched_rule.id,
            rule_label=matched_rule.label,
            ambiguity_rule=matched_rule.ambiguity_rule,
            reason=(
                f"The title reads as {matched_rule.label!r}, but the description is mostly "
                f"about {_named(demoting, observed)}, which is not the work you described."
            ),
            supporting_signals=demoting,
        )

    promoting = _branch_fires(ambiguity.promote_if, observed)
    if promoting is not None and ambiguity.promote_to is not None:
        resolved = CLASS_BY_NAME[ambiguity.promote_to]
        return TitleClassification(
            base_class=base_class,
            resolved_class=resolved,
            adjustment=TitleAdjustment.PROMOTED,
            rule_id=matched_rule.id,
            rule_label=matched_rule.label,
            ambiguity_rule=matched_rule.ambiguity_rule,
            reason=(
                f"The title {matched_rule.label!r} could mean several things, and the "
                f"description is about {_named(promoting, observed)}, which is the work "
                "you described."
            ),
            supporting_signals=promoting,
        )

    return TitleClassification(
        base_class=base_class,
        resolved_class=base_class,
        adjustment=TitleAdjustment.UNCHANGED_INSUFFICIENT_EVIDENCE,
        rule_id=matched_rule.id,
        rule_label=matched_rule.label,
        ambiguity_rule=matched_rule.ambiguity_rule,
        reason=(
            f"The title {matched_rule.label!r} is ambiguous and the description carried too "
            f"little evidence either way, so it stayed {base_class.value}."
        ),
    )

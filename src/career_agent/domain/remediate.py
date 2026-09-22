"""Applying what the validator decided, and nothing it did not.

`domain/validate.py` decides. This applies. They are separate modules because
the decision is the interesting part and must stay readable on its own, and
because a reporter that also mutated would make "what would this change?"
impossible to ask without changing it.

THE APPLIER NEVER RE-DERIVES A CONDITION
----------------------------------------
Every remedy here is driven by `Correction.kind` and `Correction.target`, never
by re-checking whether the rule still holds. Re-checking would be a second
implementation of the same rule, and two implementations drift until the audit
trail describes a change the document never actually received -- which is the
worst possible outcome for a system whose entire claim is that its conclusions
are auditable.

EVERY REMEDY MOVES TOWARDS UNCERTAINTY
--------------------------------------
A downgraded claim, never an opposite claim. WORLDWIDE becomes "names nowhere",
not a country list. A bad salary range becomes silence, not a repaired range.
Seven CORE tools become seven REQUIRED tools, not six. The system is allowed to
say it does not know; it is never allowed to guess in the other direction and
call the guess a finding.
"""

from typing import Any, TypeVar

from pydantic import BaseModel

from career_agent.domain.enums import (
    LanguageRequirement,
    SoftwareCentrality,
)
from career_agent.domain.extracted import ExtractedField
from career_agent.domain.fingerprint import HiringScope, JobFingerprint
from career_agent.domain.validate import Correction, CorrectionKind

#: Remedies run in a fixed order so that the same set of corrections always
#: produces the same document. Drops run before demotions: demoting a tool that
#: is about to be dropped would be work with no effect, and the reverse order
#: would make the result depend on list position.
_ORDER: tuple[CorrectionKind, ...] = (
    CorrectionKind.DROP_SOFTWARE,
    CorrectionKind.DEMOTE_CORE,
    CorrectionKind.DEMOTE_LANGUAGE,
    CorrectionKind.CLEAR_COMPENSATION,
    CorrectionKind.SCOPE_TO_NOT_STATED,
)


#: Any frozen document node. `_rebuild` preserves the exact type it was given,
#: which is what lets one helper serve the fingerprint, the eligibility block
#: and a single software row without any of them losing their type on the way
#: through.
TNode = TypeVar("TNode", bound=BaseModel)


def _rebuild(model: TNode, **changes: Any) -> TNode:
    """Replace fields on a frozen pydantic model, re-running its validators.

    `model_copy(update=...)` would be shorter and would skip validation, which
    is exactly what must not happen: a remedy that produced an invalid document
    would be stored as a valid one, and the integrity checks on `JobFingerprint`
    exist precisely to catch a malformed document before it becomes a row.
    """
    values = {name: getattr(model, name) for name in type(model).model_fields}
    values.update(changes)
    return type(model)(**values)


def apply_corrections(fp: JobFingerprint, corrections: list[Correction]) -> JobFingerprint:
    """Return the corrected document. The original is never mutated.

    Unknown or unmatched corrections are ignored rather than raising: a
    correction naming a tool that a previous remedy already removed is not an
    error, it is the same outcome reached twice.
    """
    by_kind: dict[CorrectionKind, list[Correction]] = {}
    for correction in corrections:
        by_kind.setdefault(correction.kind, []).append(correction)

    for kind in _ORDER:
        found = by_kind.get(kind)
        if not found:
            continue
        fp = _REMEDIES[kind](fp, found)
    return fp


# =========================================================================
# THE REMEDIES
# =========================================================================


def _drop_software(fp: JobFingerprint, corrections: list[Correction]) -> JobFingerprint:
    """Remove tools the posting never names.

    The cited evidence is deliberately left in place. Evidence is an archive of
    what the model claimed, not a index of what survived, and pruning it would
    destroy the record of a hallucination at the moment it is most worth
    keeping. Nothing points at it any more, which is what unused evidence means.
    """
    doomed = {c.target for c in corrections if c.target is not None}
    if not doomed:
        return fp
    kept = [item for item in fp.software if item.raw_mention not in doomed]
    return _rebuild(fp, software=kept)


def _demote_core(fp: JobFingerprint, _: list[Correction]) -> JobFingerprint:
    """Every CORE drops one rung to REQUIRED.

    Demoted rather than discarded: the mentions are real, the emphasis was not.
    Dropping them would lose tools the posting genuinely names, which is a
    larger error than overstating how central they are.
    """
    demoted = [
        _rebuild(item, centrality=SoftwareCentrality.REQUIRED)
        if item.centrality is SoftwareCentrality.CORE
        else item
        for item in fp.software
    ]
    return _rebuild(fp, software=demoted)


def _demote_language(fp: JobFingerprint, corrections: list[Correction]) -> JobFingerprint:
    """An unquotable hard requirement becomes UNCLEAR.

    UNCLEAR penalises nothing and asks a question, which is the right outcome
    for a claim that could end a candidacy and cannot be pointed at.
    """
    targets = {c.target for c in corrections if c.target is not None}
    updated = [
        _rebuild(lang, requirement_level=LanguageRequirement.UNCLEAR)
        if lang.language_code in targets
        else lang
        for lang in fp.languages
    ]
    return _rebuild(fp, languages=updated)


def _clear_compensation(fp: JobFingerprint, _: list[Correction]) -> JobFingerprint:
    """Both ends of an impossible range go to silence.

    Not swapped. Swapping assumes the model transposed two correct numbers; it
    may equally have read the wrong sentence entirely, and downstream salary
    arithmetic must never rest on a guess about which failure occurred.
    """
    comp = _rebuild(
        fp.compensation,
        min=ExtractedField[float].not_stated("range rejected: minimum exceeded maximum"),
        max=ExtractedField[float].not_stated("range rejected: minimum exceeded maximum"),
    )
    return _rebuild(fp, compensation=comp)


def _scope_to_not_stated(fp: JobFingerprint, corrections: list[Correction]) -> JobFingerprint:
    """A worldwide scope with work-arrangement-only support becomes silence.

    NOT_STATED, a *status* -- not an EXPLICIT scope whose value happens to name
    nowhere. The posting did not state a hiring geography, and the honest
    record of that is the field saying so.

    The citation goes with it, because the field invariants forbid NOT_STATED
    from citing anything, and rightly: the quote was never evidence about
    hiring geography. It survives in `fp.evidence` as an archived claim that
    nothing points at any more, and `reasoning` carries why -- which keeps the
    downgrade auditable without letting a work-model sentence sit in the
    document dressed as geographic proof.
    """
    scope = fp.eligibility.hiring_scope
    if scope.value is None:
        return fp
    reason = corrections[0].reason if corrections else ""
    corrected: ExtractedField[HiringScope] = ExtractedField[HiringScope].not_stated(
        reasoning=f"worldwide claim withdrawn: {reason}"[:280]
    )
    return _rebuild(fp, eligibility=_rebuild(fp.eligibility, hiring_scope=corrected))


_REMEDIES = {
    CorrectionKind.DROP_SOFTWARE: _drop_software,
    CorrectionKind.DEMOTE_CORE: _demote_core,
    CorrectionKind.DEMOTE_LANGUAGE: _demote_language,
    CorrectionKind.CLEAR_COMPENSATION: _clear_compensation,
    CorrectionKind.SCOPE_TO_NOT_STATED: _scope_to_not_stated,
}

"""Shadow mode: v2 beside the current reader, explained component by component.

`compare` runs the production reader (`match_job`, readers-12) and v2 on the
same posting under the same configuration and says, for every score
component that differs, WHICH signals moved, in which direction and WHY:

* a hit production made and v2 did not: v2's evidence at the same place
  says UNCERTAIN with its structural cause (`structure.OTHER_SENSE`, ...),
  or EXCLUDED_CONTEXT with the context (About us, benefits), or nothing at
  all (`NOT_FOUND_BY_V2`: the words did not reach the phrase in v2);
* a hit v2 made and production did not: the relation that made it
  (EXACT_EQUIVALENT, FIXED_EXPRESSION, MORPHOLOGICAL_VARIANT) or IDENTICAL
  when the words were the same and only the reading of them differed;
* the same hits worth a different amount: PROMINENCE (the section a hit
  sits in) or WEIGHTING (the arithmetic of a component).

So "why did v2 give 68 where production gave 74?" is answered per
component, with the evidence, not by the two numbers. NOTHING HERE WRITES:
no `job_match`, no `profile_job_match`; a report is a value the caller may
print or save. Production stays the truth while v2 is in shadow.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from career_agent.config.search_config import SearchConfig
from career_agent.domain.matching import MatchResult
from career_agent.match.engine import JobFacts, match_job
from career_agent.matching_v2 import features as feat
from career_agent.matching_v2 import profile_match as pm

NOT_FOUND_BY_V2 = "NOT_FOUND_BY_V2"
PROMINENCE = "PROMINENCE"
WEIGHTING = "WEIGHTING"


@dataclass(frozen=True)
class SignalChange:
    signal_id: str
    #: LOST (production counted it, v2 does not), GAINED, or REWEIGHTED.
    change: str
    reason: str
    start: int | None = None
    end: int | None = None
    quote: str | None = None


@dataclass
class ComponentDiff:
    component_id: str
    current: float
    v2: float
    signals: list[SignalChange] = field(default_factory=list)

    @property
    def delta(self) -> float:
        return round(self.v2 - self.current, 2)


@dataclass
class ShadowRow:
    job_id: str
    current_score: int
    v2_score: int
    current_band: str
    v2_band: str
    current_eligibility: str
    v2_eligibility: str
    #: gate -> (production result, v2 result), only where they differ.
    gates: dict[str, tuple[str, str]] = field(default_factory=dict)
    components: list[ComponentDiff] = field(default_factory=list)
    #: UNCERTAIN evidence v2 showed (and gave no point to), by cause.
    uncertain: dict[str, int] = field(default_factory=dict)

    @property
    def same(self) -> bool:
        return (
            self.current_score == self.v2_score
            and self.current_eligibility == self.v2_eligibility
            and not self.gates
        )

    def as_dict(self, quotes: bool = True) -> dict[str, Any]:
        out = asdict(self)
        if not quotes:
            for c in out["components"]:
                for s in c["signals"]:
                    s["quote"] = None
        return out


def _hits(result: MatchResult) -> dict[str, dict[tuple[str, int, int], Any]]:
    return {
        s.signal_id: {(h.field_name, h.char_start, h.char_end): h for h in s.hits}
        for s in result.signals
    }


def _overlaps(a: tuple[str, int, int], b: tuple[str, int, int]) -> bool:
    return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


def _signal_changes(
    signal_id: str,
    current: dict[tuple[str, int, int], Any],
    v2: dict[tuple[str, int, int], Any],
    evidence: Iterable[pm.Evidence],
) -> list[SignalChange]:
    out: list[SignalChange] = []
    mine = [e for e in evidence if e.signal_id == signal_id]
    for key, hit in current.items():
        if any(_overlaps(key, k) for k in v2):
            continue
        field_name = key[0]
        seen = [
            e
            for e in mine
            if (field_name == "title") == (e.context == "TITLE")
            and e.start < key[2]
            and key[1] < e.end
        ]
        if any(e.state == pm.UNCERTAIN for e in seen):
            e = next(e for e in seen if e.state == pm.UNCERTAIN)
            reason = f"UNCERTAIN:{e.reason}"
        elif any(e.state == pm.EXCLUDED_CONTEXT for e in seen):
            e = next(e for e in seen if e.state == pm.EXCLUDED_CONTEXT)
            reason = f"EXCLUDED_CONTEXT:{e.context}"
        else:
            reason = NOT_FOUND_BY_V2
        out.append(SignalChange(signal_id, "LOST", reason, key[1], key[2], hit.quote))
    for key, hit in v2.items():
        if any(_overlaps(key, k) for k in current):
            continue
        made = next(
            (e for e in mine if e.state == pm.MATCH and e.start == key[1] and e.end == key[2]),
            None,
        )
        reason = f"GAINED_BY:{made.relation}" if made is not None else "GAINED"
        out.append(SignalChange(signal_id, "GAINED", reason, key[1], key[2], hit.quote))
    return out


def compare(
    config: SearchConfig,
    job: JobFacts,
    features: feat.JobFeatures,
    current: MatchResult | None = None,
) -> ShadowRow:
    """Production's answer and v2's for one posting, and why they differ.
    `current` may be handed in (a stored readers-N result, or one computed
    by the caller); it is computed here otherwise."""
    current = current or match_job(config, job, computed_at="shadow")
    v2 = pm.match(config, job, features, computed_at="shadow")
    row = ShadowRow(
        job_id=str(job.job_id or ""),
        current_score=current.match_score,
        v2_score=v2.result.match_score,
        current_band=str(current.fit_band),
        v2_band=str(v2.result.fit_band),
        current_eligibility=str(current.eligibility_status),
        v2_eligibility=str(v2.result.eligibility_status),
    )
    v2_gates = {g.gate: str(g.result) for g in v2.result.gates}
    for g in current.gates:
        if v2_gates.get(g.gate) != str(g.result):
            row.gates[g.gate] = (str(g.result), v2_gates.get(g.gate, "MISSING"))
    for e in v2.uncertain:
        key = e.reason or "UNKNOWN"
        row.uncertain[key] = row.uncertain.get(key, 0) + 1
    cur_hits, v2_hits = _hits(current), _hits(v2.result)
    v2_components = {c.component_id: c for c in v2.result.components}
    for c in current.components:
        other = v2_components.get(c.component_id)
        if other is None or round(other.points, 2) == round(c.points, 2):
            continue
        diff = ComponentDiff(c.component_id, c.points, other.points)
        cur_contrib = {x.signal_id: x for x in c.contributions}
        v2_contrib = {x.signal_id: x for x in other.contributions}
        for signal_id in sorted(set(cur_contrib) | set(v2_contrib)):
            a, b = cur_contrib.get(signal_id), v2_contrib.get(signal_id)
            pa = a.points if a is not None else 0.0
            pb = b.points if b is not None else 0.0
            if round(pa, 2) == round(pb, 2):
                continue
            changes = _signal_changes(
                signal_id, cur_hits.get(signal_id, {}), v2_hits.get(signal_id, {}), v2.evidence
            )
            if not changes:
                prominence_moved = a is not None and b is not None and a.prominence != b.prominence
                changes = [
                    SignalChange(
                        signal_id, "REWEIGHTED", PROMINENCE if prominence_moved else WEIGHTING
                    )
                ]
            diff.signals.extend(changes)
        row.components.append(diff)
    return row


def explain(row: ShadowRow) -> str:
    """One paragraph a person can read: the two scores and every reason."""
    head = f"{row.job_id}: v2 {row.v2_score} vs current {row.current_score}"
    if row.current_eligibility != row.v2_eligibility:
        head += f"; eligibility {row.current_eligibility} -> {row.v2_eligibility}"
    lines = [head]
    for c in row.components:
        reasons = ", ".join(f"{s.signal_id} {s.change.lower()} ({s.reason})" for s in c.signals)
        lines.append(f"  {c.component_id} {c.delta:+g}: {reasons or 'arithmetic only'}")
    for gate, (a, b) in row.gates.items():
        lines.append(f"  gate {gate}: {a} -> {b}")
    return "\n".join(lines)

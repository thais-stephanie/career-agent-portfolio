# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Recruiter value of an evidence record for one job.

Lexical relevance (how many JD requirements a record's terms answer) is not
resume value: a role-scope statement with one metric can be worth more than a
task record that names eight tools. This module keeps relevance as the base and
multiplies it by a few explicit, inspectable factors. Every factor is derived
from data that already exists on the record; nothing here reads the JD text.

    value = relevance * (1 + 0.3*ownership + 0.3*impact + 0.3*scope + 0.1*depth) * recency
            + 1.5 * must_have_direct

ownership  proficiency: led 1.0 / hands_on 0.6 / integration 0.3 / exposure 0
impact     1 if the record carries metrics, else 0
scope      ownership signals in the record's own text ("end to end",
           "independently", "sole developer", "delivered and operated" ...),
           capped at 1; role-scope records (project is None) get 1
depth      number of named technologies, capped at 4, /4
recency    1.0 if the position ended within 24 months (or is current),
           0.7 within 60 months, 0.4 older
must_have_direct  how many must-have requirements list the record among their
           top-3 evidence with a DIRECT verdict (0..3)

Hero evidence: the 1-3 records with the highest value that (a) are led or
hands-on, (b) carry metrics, (c) directly support at least one requirement,
(d) do not duplicate each other's requirement coverage and (e) are not
secondary-only provenance (evidence/provenance.py). Heroes shape the
summary and the first bullets and are never trimmed for length.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.models import (
    EvidenceRecord,
    MatchReport,
    MatchType,
    Proficiency,
    RequirementCategory,
)

OWNERSHIP_SIGNALS = [
    "end to end",
    "end-to-end",
    "independently",
    "sole developer",
    "built alone",
    "delivered and operated",
    "designed, delivered",
    "designed and built",
    "owned",
    "as sole",
    "primary architect",
    "i built all of it",
]
_OWNERSHIP = {
    Proficiency.LED: 1.0,
    Proficiency.HANDS_ON: 0.6,
    Proficiency.INTEGRATION: 0.3,
    Proficiency.EXPOSURE: 0.0,
    Proficiency.CERTIFICATION: 0.0,
}


@dataclass
class ValueScore:
    evidence_id: str
    relevance: float
    ownership: float
    impact: float
    scope: float
    depth: float
    recency: float
    must_have_direct: int
    requirement_ids: list[str] = field(default_factory=list)
    value: float = 0.0
    role_scope: bool = False

    def as_dict(self) -> dict[str, float]:
        return {
            "relevance": round(self.relevance, 2),
            "ownership": self.ownership,
            "impact": self.impact,
            "scope": round(self.scope, 2),
            "depth": round(self.depth, 2),
            "recency": self.recency,
            "must_have_direct": float(self.must_have_direct),
            "value": round(self.value, 2),
            "hero_score": hero_score(self),
        }


def _recency(index: EvidenceIndex, rec: EvidenceRecord, today: date | None = None) -> float:
    pos = index.positions[rec.position_id]
    if pos.end is None:
        return 1.0
    today = today or date.today()  # noqa: DTZ011 - calendar recency, matches text.py
    months = (today.year - int(pos.end[:4])) * 12 + (today.month - int(pos.end[5:7]))
    return 1.0 if months < 24 else (0.7 if months < 60 else 0.4)


def _scope(rec: EvidenceRecord) -> float:
    if rec.project is None:
        return 1.0
    text = f"{rec.claim} {rec.detailed_context} {rec.resume_text}".lower()
    hits = sum(1 for s in OWNERSHIP_SIGNALS if s in text)
    return min(1.0, hits / 2)


def score_records(
    report: MatchReport, relevance: dict[str, float], index: EvidenceIndex
) -> dict[str, ValueScore]:
    """Value for every record that has any relevance to the job."""
    must_direct: dict[str, int] = {}
    req_ids: dict[str, list[str]] = {}
    for m in report.matches:
        for e in m.evidence[:3]:
            req_ids.setdefault(e.evidence_id, []).append(m.requirement_id)
            # a record earns must-have credit when it contributes a literal hit to a must-have the
            # requirement-level verdict calls DIRECT (alone or jointly with other records)
            if (
                m.category == RequirementCategory.MUST_HAVE
                and m.match_type == MatchType.DIRECT
                and e.reason.startswith("literal")
            ):
                must_direct[e.evidence_id] = must_direct.get(e.evidence_id, 0) + 1
    out: dict[str, ValueScore] = {}
    for rid, rel in relevance.items():
        rec = index.by_id[rid]
        secondary = index.secondary_only(rec)
        # secondary-only provenance: ownership is capped at hands-on and scope signals at half,
        # so a self-authored sentence cannot claim more than the project documentation does
        vs = ValueScore(
            evidence_id=rid,
            relevance=rel,
            ownership=_OWNERSHIP[index.effective_proficiency(rec)],
            impact=1.0 if rec.metrics else 0.0,
            scope=min(_scope(rec), 0.5) if secondary else _scope(rec),
            depth=min(len(rec.technologies), 4) / 4,
            recency=_recency(index, rec),
            must_have_direct=min(3, must_direct.get(rid, 0)),
            requirement_ids=req_ids.get(rid, []),
            role_scope=rec.project is None,
        )
        vs.value = round(
            rel
            * (1 + 0.3 * vs.ownership + 0.3 * vs.impact + 0.3 * vs.scope + 0.1 * vs.depth)
            * vs.recency
            + 1.5 * vs.must_have_direct,
            2,
        )
        out[rid] = vs
    return out


def hero_score(v: ValueScore) -> float:
    """Heroes are proof of ownership with an outcome, so ownership and scope count twice as much
    as they do in the ordinary value, and recency counts twice: value * (0.5 + ownership) * (1 + scope) * recency."""
    return round(v.value * (0.5 + v.ownership) * (1 + v.scope) * v.recency, 2)


def pick_heroes(
    scores: dict[str, ValueScore], index: EvidenceIndex, max_heroes: int = 3
) -> list[str]:
    """The strongest distinct proof points: led/hands-on, with metrics, directly relevant, non-overlapping."""
    ranked = sorted(scores.values(), key=lambda v: -hero_score(v))
    heroes: list[ValueScore] = []
    for v in ranked:
        rec = index.by_id[v.evidence_id]
        if (
            rec.proficiency not in (Proficiency.LED, Proficiency.HANDS_ON)
            or not rec.metrics
            or not v.requirement_ids
        ):
            continue
        if not index.positions[rec.position_id].include_by_default:
            continue
        if index.secondary_only(rec):
            continue  # a portfolio/LinkedIn/resume-only sentence is not proof enough to lead with
        mine = set(v.requirement_ids)
        if any(_jaccard(mine, set(h.requirement_ids)) > 0.5 for h in heroes):
            continue
        heroes.append(v)
        if len(heroes) >= max_heroes:
            break
    return [h.evidence_id for h in heroes]


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def redundant(candidate: ValueScore, chosen: list[ValueScore], index: EvidenceIndex) -> bool:
    """A record proving the same requirements as an already chosen one adds little."""
    mine = set(candidate.requirement_ids)
    if not mine:
        return False
    return any(_jaccard(mine, set(c.requirement_ids)) >= 0.75 and len(mine) >= 2 for c in chosen)


_NUMBER = re.compile(r"\d")


def has_number(text: str) -> bool:
    return bool(_NUMBER.search(text))

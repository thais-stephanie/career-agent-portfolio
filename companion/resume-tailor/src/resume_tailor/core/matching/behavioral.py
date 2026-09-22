# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Compound behavioural requirements, matched clause by clause.

"Ability to identify opportunities, take ownership, and drive projects
independently without requiring step-by-step direction" names no tool, so the
lexical matcher sees nothing. It does describe behaviour the bank documents:
records that say "sole developer", "independently designed, delivered and
operated", "surfaced 157 issues". This module splits such a requirement into
clauses, looks for *demonstrated behaviour* behind each clause, and returns a
verdict that says which clauses are supported and which stay unverified.

Rules:
* a clause counts as supported only when a record's own text shows the
  behaviour (cue table below); employer personality wording ("self-starter",
  "without supervision", "highly proactive") has no behavioural cue and stays
  unverified - ownership evidence never implies it;
* every material clause supported -> DIRECT; more than half -> PARTIAL;
  fewer -> UNSUPPORTED. A clause that only restates another ("take ownership"
  and "drive projects independently" both map to ownership) is one clause;
* secondary-only records (provenance ceilings) may support a clause but cannot
  be the only support for the requirement's DIRECT verdict;
* the rationale lists supported clauses with their records and the unverified
  clauses verbatim.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.models import (
    EvidenceRecord,
    MatchedEvidence,
    MatchType,
    Proficiency,
    Requirement,
)

# clause cue (in the requirement) -> behaviour signal (in the record text), and whether the
# record must be led/hands-on for the signal to count
_CUES: list[tuple[str, re.Pattern[str], re.Pattern[str], bool]] = [
    (
        "ownership",
        re.compile(
            r"\b(take|takes|taking) ownership|\bownership\b|\bown (and|the|projects|systems|initiatives)\b|\baccountab",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(sole (technical )?(owner|developer)|owned|independently (designed|delivered|built|operated)|end to end|end-to-end|delivered and operated)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "independent delivery",
        re.compile(
            r"\b(drive|drives|driving|lead|leads|deliver|delivers|run|runs) (projects|initiatives|work|implementations?|programs?)|\bindependently\b|\bself-directed\b|\bautonomous(ly)?\b|\bworking independently\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(independently|sole (technical )?(owner|developer)|as sole|end to end|end-to-end|delivered and operated|from requirements)\b",
            re.IGNORECASE,
        ),
        True,
    ),
    (
        "identifying problems or opportunities",
        re.compile(
            r"\bidentif(y|ies|ying) (opportunities|inefficiencies|gaps|improvements|problems|issues|bottlenecks)|\bproactively (design|identify|recommend|find)|\bspot(ting)?\b|\brecommend(s|ing)? improvements",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(identified|surfaced|found|discovered|audited|uncovered|flagged) (\w+ ){0,4}(issues|findings|defects|bottlenecks|drift|gaps|opportunities|inefficienc\w+|errors|bugs)|\bfirst sweep\b|\bidentified (the )?priority bottlenecks\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "troubleshooting and root cause",
        re.compile(r"\btroubleshoot\w*|\broot[- ]cause|\bdebug\w*|\bdiagnos\w*", re.IGNORECASE),
        re.compile(
            r"\b(troubleshoot\w*|root[- ]cause|fixed (the |two |a )?(\w+ ){0,3}(bugs?|defects?|errors?)|found and fixed|debugg\w*|diagnos\w*)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "cross-functional collaboration",
        re.compile(
            r"\b(cross-functional|work(ing)? (closely )?with (leadership|stakeholders|teams)|partner with|collaborat\w+ with)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(with (finance|sales|customer success|engineering|operations|stakeholders|leadership)|partnered with|led requirements gathering with|elicited (\w+ ){0,3}from)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "documentation and enablement",
        re.compile(
            r"\b(document\w*|sops?|runbooks?|quick-reference|training|enablement|knowledge base)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(documentation|documented|training material|trained|runbook|decision log|raci|sop)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        "communicating requirements",
        re.compile(
            r"\b(translate|translating|communicat\w+) (business )?(requirements|needs)|\bliaison\b|\bescalat\w+ bugs\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(translated|turned) (\w+ ){0,3}requirements|\brequirements (gathering|elicit\w+)|\bstakeholder requirements\b",
            re.IGNORECASE,
        ),
        False,
    ),
]
# clause wording that describes disposition rather than behaviour: never inferred from evidence
_PERSONALITY = re.compile(
    r"\b(self-starter|self starter|self-motivated|proactive|highly motivated|without (requiring )?(step-by-step |close |direct |much )?(direction|supervision|guidance|hand-holding)|needs? no supervision|minimal (supervision|direction|oversight)|go-getter|hustle|passion\w*|positive attitude|team player|fast-paced|startup|start-up|small-company|small company|entrepreneurial)\b",
    re.IGNORECASE,
)
_SPLIT = re.compile(r",|;|\band\b|\bwhile\b|\bas well as\b|(?=\bwithout\b)|\bplus\b|\bor\b")
_LEAD_IN = re.compile(
    r"^(ability to|able to|capable of|experience|proven ability to|demonstrated ability to|comfortable|willing(ness)? to|track record of)\s*",
    re.IGNORECASE,
)


@dataclass
class Clause:
    text: str
    cue: str | None = None  # behavioural cue name, None when the clause is disposition or unmapped
    personality: bool = False
    support: list[str] = field(default_factory=list)


def decompose(text: str) -> list[Clause]:
    body = _LEAD_IN.sub("", text.strip().rstrip("."))
    parts = [p.strip(" .,;:") for p in _SPLIT.split(body)]
    clauses: list[Clause] = []
    seen_cues: set[str] = set()
    for p in parts:
        if len(p.split()) < 2:
            continue
        piece = p
        cue = next((name for name, req_re, _, _ in _CUES if req_re.search(piece)), None)
        # disposition wording ("without step-by-step direction", "self-starter", "fast-paced") is a
        # clause of its own with no behavioural cue; a clause that also names behaviour keeps its cue
        personality = cue is None and bool(_PERSONALITY.search(piece))
        if cue and cue in seen_cues:
            continue  # restates an earlier clause
        if cue:
            seen_cues.add(cue)
        clauses.append(Clause(text=piece, cue=cue, personality=personality))
    return clauses


def is_behavioural(req: Requirement) -> bool:
    """A requirement worth clause matching: two or more clauses, at least one behavioural cue,
    and no specific technology to anchor the lexical matcher."""
    clauses = decompose(req.text)
    return len(clauses) >= 2 and any(c.cue for c in clauses)


def _record_shows(
    rec: EvidenceRecord, index: EvidenceIndex, signal: re.Pattern[str], needs_ownership: bool
) -> bool:
    if needs_ownership and index.effective_proficiency(rec) not in (
        Proficiency.LED,
        Proficiency.HANDS_ON,
    ):
        return False
    return bool(signal.search(index.record_text(rec)))


def match_behavioural(
    req: Requirement, index: EvidenceIndex
) -> tuple[MatchType, str, list[MatchedEvidence]]:
    clauses = decompose(req.text)
    signals = {name: (sig, own) for name, _, sig, own in _CUES}
    evidence: dict[str, MatchedEvidence] = {}
    for c in clauses:
        if not c.cue:
            continue
        sig, own = signals[c.cue]
        for rec in index.default_records():
            if not index.positions[rec.position_id].include_by_default:
                continue
            if _record_shows(rec, index, sig, own):
                c.support.append(rec.id)
                me = evidence.get(rec.id)
                if me is None:
                    evidence[rec.id] = MatchedEvidence(
                        evidence_id=rec.id,
                        score=1.0,
                        match_type=MatchType.DIRECT,
                        matched_terms=[c.cue],
                        reason=f"behaviour: {c.cue}",
                    )
                else:
                    me.score += 1.0
                    me.matched_terms.append(c.cue)
                    me.reason += f"; {c.cue}"
    supported = [c for c in clauses if c.support]
    unverified = [c for c in clauses if not c.support]
    material = list(clauses)
    if not supported:
        return MatchType.UNSUPPORTED, "", []
    # a secondary-only record may support a clause; the verdict needs a non-secondary carrier
    carriers = [rid for rid in evidence if not index.secondary_only(index.by_id[rid])]
    if len(supported) == len(material) and carriers:
        verdict = MatchType.DIRECT
    elif len(supported) * 2 >= len(material) or (len(supported) == len(material) and not carriers):
        verdict = MatchType.PARTIAL
    else:
        verdict = MatchType.UNSUPPORTED
    parts = [f"'{c.text}': {', '.join(c.support[:3])}" for c in supported]
    note = "clauses supported by demonstrated behaviour: " + "; ".join(parts)
    if unverified:
        note += " | unverified: " + "; ".join(
            f"'{c.text}'"
            + (" (disposition wording, not inferable from evidence)" if c.personality else "")
            for c in unverified
        )
    if verdict == MatchType.PARTIAL and len(supported) == len(material) and not carriers:
        note += " | only secondary-source evidence supports this"
    ranked = sorted(
        evidence.values(),
        key=lambda e: (-e.score, index.secondary_only(index.by_id[e.evidence_id]), e.evidence_id),
    )
    for e in ranked:
        e.match_type = verdict if verdict != MatchType.UNSUPPORTED else MatchType.PARTIAL
    return verdict, note, ranked

# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Mixed AND/OR requirement lists: "Tech Stack: HubSpot, ClickUp, and ChurnZero or Planhat."

Such a line is neither a cumulative list nor a pure OR-list. It parses into conjunctive
groups where an OR group fills one slot:

    GROUP 1: HubSpot        GROUP 2: ClickUp        GROUP 3: ChurnZero OR Planhat

and the requirement holds only when every group holds. Within an OR group the strongest
supported alternative carries the group (same as the pure-OR matcher); across groups the
aggregation is conservative:

* every group DIRECT                          -> DIRECT
* every group supported, weakest lower        -> that weakest verdict
* any group UNSUPPORTED, others supported     -> PARTIAL (never DIRECT)
* every group UNSUPPORTED                     -> UNSUPPORTED

One strong group therefore never makes the whole requirement DIRECT. Each alternative is
evaluated by the pure-OR machinery (``disjunctive._evaluate``), so proficiency caps,
secondary-source ceilings and the no-equivalence rules apply unchanged.

The parser deliberately stays out of:
* pure OR-lists ("RingCentral or Aircall or CloudTalk"): no "and", handled by disjunctive.py;
* pure cumulative lists ("Power BI, SQL and Supabase"): no "or", handled by the lexical
  matcher's majority/union rules;
* example listings ("tools such as Zapier, Make, or n8n", "Platforms (A, B, C)", "or similar"):
  representative platforms keep their signal semantics;
* prose sentences: everything longer than a short list of named items.
"""

from __future__ import annotations

import re

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import find_terms, known_term
from resume_tailor.core.models import MATCH_RANK, MatchedEvidence, MatchType, Requirement

_LABEL = re.compile(r"^[A-Za-z][A-Za-z &/-]{0,24}:\s*")
_LEAD_IN = re.compile(
    r"^(experience|background|familiarity|knowledge|proficiency|exposure|hands-on experience|strong experience|prior experience|working knowledge)\s+(with|in|of|managing|using|building|working with|working in|across)?\s*",
    re.IGNORECASE,
)
_EXAMPLES = re.compile(
    r"\b(such as|e\.g\.|for example|including|like)\b|\bor (similar|equivalent|comparable)\b",
    re.IGNORECASE,
)
# "platforms (A, B, C)": a parenthetical after a family noun is an example listing, not grouping
_FAMILY_PAREN = re.compile(
    r"\b(?:platforms?|tools?|systems?|technologies|software|stack|suites?)\s*\(", re.IGNORECASE
)
_OR_SPLIT = re.compile(r"\bor\b|/", re.IGNORECASE)


def _body(text: str) -> str:
    body = _LABEL.sub("", text.strip().rstrip("."))
    body = _LEAD_IN.sub("", body)
    body = re.sub(r"\band/or\b", " or ", body, flags=re.IGNORECASE)
    body = re.sub(r"\bwith either\b", " and ", body, flags=re.IGNORECASE)
    body = re.sub(r"\beither\b", " ", body, flags=re.IGNORECASE)
    body = re.sub(r"\bplus\b|&", " and ", body, flags=re.IGNORECASE)
    body = body.replace("(", ", ").replace(")", " ")
    return re.sub(r"\s+", " ", body).strip(" ,")


def _alts(atom: str) -> list[str]:
    """One atom's alternatives: "ChurnZero or Planhat" -> two, "CI/CD" (a known term) -> one."""
    atom = atom.strip(" .,;:")
    if known_term(atom):
        return [atom]
    parts = [p.strip(" .,;:") for p in _OR_SPLIT.split(atom)]
    return [p for p in parts if p]


def parse_groups(text: str) -> list[list[str]] | None:
    """Conjunctive groups of a mixed AND/OR platform list, or None when the line is not one
    (pure OR, pure cumulative, example listing, or prose)."""
    if _EXAMPLES.search(text) or _FAMILY_PAREN.search(text):
        return None
    body = _body(text)
    if len(body.split()) > 14:
        return None  # a sentence, not a list of named items
    low = f" {body.lower()} "
    if not re.search(r"\band\b", low) or not re.search(r"\bor\b|/", low):
        return None  # pure cumulative or pure OR: existing semantics apply
    groups: list[list[str]] = []
    for segment in body.split(","):
        segment = re.sub(r"^\s*(?:and|or)\s+", "", segment.strip(" .,;:"), flags=re.IGNORECASE)
        for atom in re.split(r"\band\b", segment, flags=re.IGNORECASE):
            alts = _alts(atom)
            if alts and alts not in groups:
                groups.append(alts)
    all_alts = [a for g in groups for a in g]
    if (
        len(groups) < 2
        or not any(len(g) >= 2 for g in groups)
        or not any(len(g) == 1 for g in groups)
    ):
        return None
    if any(not 1 <= len(a.split()) <= 5 for a in all_alts):
        return None
    named = sum(1 for a in all_alts if find_terms(a) or re.search(r"[A-Z]", a))
    if named * 2 < len(all_alts):
        return None  # mostly ordinary prose words: leave the lexical verdict alone
    return groups


def is_mixed_group(text: str) -> bool:
    return parse_groups(text) is not None


def match_grouped(
    req: Requirement, index: EvidenceIndex
) -> tuple[MatchType, str, list[MatchedEvidence]]:
    """Requirement-level verdict for a mixed list, with a group-by-group rationale."""
    from resume_tailor.core.matching.disjunctive import _evaluate  # shared alternative evaluator

    groups = parse_groups(req.text) or []
    labels: list[str] = []
    verdicts: list[MatchType] = []
    evidence: list[MatchedEvidence] = []
    seen: set[str] = set()
    parts: list[str] = []
    for g in groups:
        evaluated = [_evaluate(a, index) for a in g]
        best = max(evaluated, key=lambda a: MATCH_RANK[a.verdict])
        label = " or ".join(g)
        labels.append(label)
        verdicts.append(best.verdict)
        if best.verdict == MatchType.UNSUPPORTED:
            parts.append(f"'{label}': unsupported (no evidence)")
            continue
        ids = list(dict.fromkeys(e.evidence_id for e in best.evidence[:3]))
        via = f" via {', '.join(ids)}" if ids else ""
        carried = f" (carried by '{best.text}')" if len(g) > 1 else ""
        parts.append(f"'{label}': {best.verdict.value}{carried}{via}")
        for e in best.evidence:
            if e.evidence_id not in seen:
                seen.add(e.evidence_id)
                evidence.append(e)
    ranks = [MATCH_RANK[v] for v in verdicts]
    if max(ranks) == MATCH_RANK[MatchType.UNSUPPORTED]:
        overall = MatchType.UNSUPPORTED
        why = "no group is evidenced"
    elif MATCH_RANK[MatchType.UNSUPPORTED] in ranks:
        overall = MatchType.PARTIAL
        missing = [
            label for label, v in zip(labels, verdicts, strict=False) if v == MatchType.UNSUPPORTED
        ]
        why = "required group(s) unevidenced: " + ", ".join(f"'{m}'" for m in missing)
    else:
        weakest = min(ranks)
        overall = next(mt for mt, rank in MATCH_RANK.items() if rank == weakest)
        why = (
            "every group holds" if overall == MatchType.DIRECT else "weakest group sets the ceiling"
        )
    note = (
        "all conjunctive groups must hold: "
        + "; ".join(parts)
        + f" | overall {overall.value}: {why}"
    )
    evidence.sort(key=lambda e: (-MATCH_RANK[e.match_type], -e.score, e.evidence_id))
    return overall, note, evidence

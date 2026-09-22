# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Disjunctive requirements: "website backend tasks or CMS platforms".

An OR-list is satisfied by its strongest sufficiently supported alternative.
The lexical matcher scores the sentence as a whole, so a sentence whose only
evidenced item is a platform category (CMS) or a background word (SaaS,
operations) can end UNSUPPORTED although one alternative is plainly met.
This module splits a genuinely disjunctive sentence into alternatives,
evaluates each one on its own, and reports which alternative carried the
verdict and which stay unverified.

What it will not do:
* treat a cumulative list ("APIs, webhooks, scripts, and internal tools",
  "Power BI, SQL and Supabase") as alternatives: a sentence whose list
  conjunction is "and" is not disjunctive;
* infer one alternative from another ("CMS" never becomes "website backend",
  "SaaS" never becomes "startup");
* bypass the existing rules: a term alternative goes through the same
  proficiency caps and provenance ceilings as any literal hit; an alternative
  met only by employer context (a position title or company descriptor, for
  "background in operations / SaaS") is PARTIAL at most.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import GENERIC_TERMS, canonical, find_terms
from resume_tailor.core.models import MATCH_RANK, MatchedEvidence, MatchType, Requirement

_LEAD_IN = re.compile(
    r"^(experience|background|familiarity|knowledge|proficiency|exposure|hands-on experience|strong experience|prior experience|working knowledge)\s+(with|in|of|managing|using|building|working with|working in|across)?\s*",
    re.IGNORECASE,
)
_SUCH_AS = re.compile(r"\b(such as|e\.g\.|for example|including|like)\b", re.IGNORECASE)
_SPLIT = re.compile(r",|;|/|\bor\b|\band/or\b", re.IGNORECASE)
_IGNORE_WORDS = {
    "similar",
    "platform",
    "platforms",
    "company",
    "companies",
    "tool",
    "tools",
    "task",
    "tasks",
    "environment",
    "environments",
    "experience",
    "background",
    "other",
    "related",
    "equivalent",
    "system",
    "systems",
    "work",
    "the",
    "a",
    "an",
    "in",
    "with",
    "of",
    "and",
    "driven",
}


def is_disjunctive(text: str) -> bool:
    """A list whose conjunction is "or" (or "/") and not "and"; "and/or" counts as "or"."""
    body = _LEAD_IN.sub("", text.strip())
    low = body.lower().replace("and/or", " or ")
    if not re.search(r"\bor\b|/", low):
        return False
    if re.search(r"\band\b", low):
        return False  # a cumulative list, or a mixed sentence: leave the lexical verdict alone
    return len(alternatives(text)) >= 2


def alternatives(text: str) -> list[str]:
    body = _LEAD_IN.sub("", text.strip().rstrip("."))
    parts = [p.strip(" .,;:") for p in _SPLIT.split(body)]
    out: list[str] = []
    for p in parts:
        p = _SUCH_AS.sub("", p).strip(" .,;:")
        if p and p.lower() not in {x.lower() for x in out}:
            out.append(p)
    return out


@dataclass
class Alternative:
    text: str
    verdict: MatchType = MatchType.UNSUPPORTED
    evidence: list[MatchedEvidence] = field(default_factory=list)
    how: str = ""


def _subject_terms(alt: str) -> list[str]:
    """The alternative's lexicon terms; when it names only a category (CMS, CRM) the category is
    the subject and counts, unlike in a full sentence where generic words are noise."""
    terms = [canonical(t) for t in find_terms(alt)]
    specific = [t for t in terms if t not in GENERIC_TERMS]
    return specific or terms


def _content_words(alt: str) -> list[str]:
    words = [w for w in re.findall(r"[a-z0-9][a-z0-9+.-]*", alt.lower()) if w not in _IGNORE_WORDS]
    return [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words]


def _evaluate(alt: str, index: EvidenceIndex) -> Alternative:
    from resume_tailor.core.matching.matcher import (  # local import: matcher imports this module
        _group_terms,
        _proficiency_cap,
    )

    out = Alternative(text=alt)
    terms = _subject_terms(alt)
    req = Requirement(id="alt", text=alt, category="nice_to_have", terms=terms)  # type: ignore[arg-type]
    best_rank = MATCH_RANK[MatchType.UNSUPPORTED]
    if terms:
        for rec in index.default_records():
            if not index.positions[rec.position_id].include_by_default:
                continue
            rec_terms = index.record_terms(rec)
            exact = [t for t in terms if t in rec_terms]
            if exact:
                mt, why = _proficiency_cap(rec, req, index, exact)
                if index.secondary_only(rec) and mt == MatchType.DIRECT:
                    mt, why = MatchType.PARTIAL, "secondary-source evidence"
                out.evidence.append(
                    MatchedEvidence(
                        evidence_id=rec.id,
                        score=2.0 if mt == MatchType.DIRECT else 1.0,
                        match_type=mt,
                        matched_terms=exact,
                        reason=f"alternative '{alt}': literal {', '.join(exact)}"
                        + (f"; {why}" if why else ""),
                    )
                )
            elif any(_group_terms(t) & rec_terms for t in terms):
                out.evidence.append(
                    MatchedEvidence(
                        evidence_id=rec.id,
                        score=0.5,
                        match_type=MatchType.TRANSFERABLE,
                        matched_terms=terms,
                        reason=f"alternative '{alt}': related tool",
                    )
                )
    if not out.evidence:
        # background alternatives ("SaaS", "operations"): employer context is evidence of background,
        # never of a skill, so it caps at PARTIAL
        words = _content_words(alt)
        if words and len(words) <= 2:
            for pos in index.bank.positions:
                if not pos.include_by_default or pos.kind.value == "cross_cutting":
                    continue
                ctx = f"{pos.title} {pos.company_blurb}".lower()
                if all(re.search(rf"\b{re.escape(w)}", ctx) for w in words):
                    recs = index.by_position.get(pos.id, [])
                    anchor = next((r for r in recs if r.project is None), recs[0] if recs else None)
                    if anchor is not None:
                        out.evidence.append(
                            MatchedEvidence(
                                evidence_id=anchor.id,
                                score=1.0,
                                match_type=MatchType.PARTIAL,
                                matched_terms=words,
                                reason=f"alternative '{alt}': employer context, {pos.company} ({pos.title}"
                                + (
                                    f"; {pos.company_blurb.rstrip('.')}"
                                    if pos.company_blurb
                                    else ""
                                )
                                + ")",
                            )
                        )
    for e in out.evidence:
        best_rank = max(best_rank, MATCH_RANK[e.match_type])
    out.verdict = next(mt for mt, rank in MATCH_RANK.items() if rank == best_rank)
    out.evidence.sort(key=lambda e: (-MATCH_RANK[e.match_type], -e.score, e.evidence_id))
    return out


def match_disjunctive(
    req: Requirement, index: EvidenceIndex
) -> tuple[MatchType, str, list[MatchedEvidence]]:
    alts = [_evaluate(a, index) for a in alternatives(req.text)]
    supported = [a for a in alts if a.verdict != MatchType.UNSUPPORTED]
    if not supported:
        return MatchType.UNSUPPORTED, "", []
    supported.sort(key=lambda a: -MATCH_RANK[a.verdict])
    best = supported[0]

    def how(a: Alternative) -> str:
        first = a.evidence[0].reason.split(": ", 1)[-1]
        return f" ({first})" if first.startswith(("employer context", "literal", "related")) else ""

    parts = [
        f"'{a.text}' {a.verdict.value} via {', '.join(dict.fromkeys(e.evidence_id for e in a.evidence[:3]))}{how(a)}"
        for a in supported
    ]
    note = "OR-list satisfied by its strongest alternative: " + "; ".join(parts)
    missing = [a for a in alts if a.verdict == MatchType.UNSUPPORTED]
    if missing:
        note += " | not separately evidenced: " + ", ".join(f"'{a.text}'" for a in missing)
    evidence: list[MatchedEvidence] = []
    seen: set[str] = set()
    for a in supported:
        for e in a.evidence:
            if e.evidence_id not in seen:
                seen.add(e.evidence_id)
                evidence.append(e)
    return best.verdict, note, evidence

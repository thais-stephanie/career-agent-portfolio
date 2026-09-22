# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Category-bounded "or similar" alternatives: "FastAPI or similar frameworks".

"similar <category>" is not a generic capability that neighbouring vocabulary can
satisfy. It means the named product, OR another CONCRETE evidenced product from the
same tool family (Flask/Django for FastAPI; n8n/Workato for Zapier). REST-API work,
Python, "automation" or generic AI-agent experience never satisfy the framework
clause, and the placeholder "similar frameworks" never carries evidence itself.

Scope, deliberately narrow:

* only a DIRECT verdict is capped (to PARTIAL, with the missing peer named): a
  TRANSFERABLE or UNSUPPORTED line is already honest;
* an example listing ("automation platforms **such as** Zapier, Make, n8n, or
  similar tools") keeps its existing signal semantics — representative examples
  are not category-bounded hard clauses;
* the category is a concrete tool family (a ``CONCEPT_GROUPS`` vendor family),
  never a capability group and never the mixed "ai" group, so a practice term
  ("automation", "systems integration") is never a peer;
* a peer counts only with working-depth evidence (integration or better).
"""

from __future__ import annotations

import re

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import (
    CAPABILITY_GROUPS,
    CONCEPT_GROUPS,
    GENERIC_TERMS,
    LIST_CONTEXT_ALIASES,
    canonical,
    find_terms,
)
from resume_tailor.core.models import Proficiency

_SIM = re.compile(
    r",?\s+or\s+(?:an?\s+)?(?:other\s+)?(?:similar|equivalent|comparable)\s+[\w /-]{2,40}",
    re.IGNORECASE,
)
_EXAMPLE_INTRO = re.compile(r"\b(?:such as|like|including|e\.g\.)\b", re.IGNORECASE)
_PREP = re.compile(r"\s+(?:with|using|in|on|via)\s+", re.IGNORECASE)
# groups that cannot define a concrete tool category: practices, and the mixed "ai" group
# (generic vocabulary like "llm" / "ai agents" sits beside products there)
_NOT_CATEGORIES = CAPABILITY_GROUPS | {"ai"}
# umbrella members inside tool families: category words, not concrete products, never peers
_UMBRELLA = {
    "ipaas",
    "no-code automation",
    "dashboards",
    "data warehouse",
    "hr technology",
    "subscription billing",
    "quote-to-cash",
    "order-to-cash",
    "serverless",
    "etl",
}


def similar_products(text: str) -> list[str] | None:
    """The named products of a category-bounded "X, Y, or similar <category>" clause, or None
    when the sentence has no such clause (or introduces the list as examples with "such as")."""
    m = _SIM.search(text)
    if m is None:
        return None
    left = text[: m.start()]
    if _EXAMPLE_INTRO.search(left):
        return None  # "platforms such as A, B, or similar tools": example semantics, existing rules
    tail = _PREP.split(left)[-1]
    segments: list[str] = []
    for seg in reversed([s.strip(" .,;:()") for s in tail.split(",")]):
        if not seg or len(seg.split()) > 4:
            break
        segments.insert(0, seg)
    products: list[str] = []
    for seg in segments:
        terms = [canonical(t) for t in find_terms(seg) if canonical(t) not in GENERIC_TERMS]
        # "X or similar platforms" is list context: "Make" names the product make.com here
        for p in terms or [LIST_CONTEXT_ALIASES.get(seg.lower(), seg)]:
            if p and p.lower() not in {x.lower() for x in products}:
                products.append(p)
    return products or None


def _evidenced_at_depth(term: str, index: EvidenceIndex) -> bool:
    return any(
        index.effective_proficiency(index.by_id[i], term)
        in (Proficiency.LED, Proficiency.HANDS_ON, Proficiency.INTEGRATION)
        for i in index.term_index.get(canonical(term), set())
    )


def similar_alternative_supported(products: list[str], index: EvidenceIndex) -> tuple[bool, str]:
    """(supported, the concrete tool that carries it). A named product with working-depth
    evidence satisfies the clause; otherwise a concrete evidenced peer from a shared tool
    family does; capability vocabulary never does."""
    for p in products:
        if _evidenced_at_depth(p, index):
            return True, canonical(p)
    peers: set[str] = set()
    for p in products:
        c = canonical(p)
        for g, members in CONCEPT_GROUPS.items():
            if g in _NOT_CATEGORIES or c not in members:
                continue
            peers |= {
                m for m in members if m != c and m not in GENERIC_TERMS and m not in _UMBRELLA
            }
    for peer in sorted(peers):
        if _evidenced_at_depth(peer, index):
            return True, peer
    return False, ""


def similar_clause_cap(text: str, index: EvidenceIndex) -> str | None:
    """The PARTIAL note when a DIRECT verdict rests on neighbouring vocabulary while the
    category-bounded clause itself is unevidenced, else None."""
    products = similar_products(text)
    if not products:
        return None
    ok, _via = similar_alternative_supported(products, index)
    if ok:
        return None
    named = ", ".join(products)
    return (
        f"'or similar' is category-bounded: neither {named} nor a concrete evidenced category peer is held; "
        f"the surrounding capability evidence supports the rest of the line only"
    )

# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Cross-context claim stitching: a compound requirement must not become DIRECT by
assembling its clauses from unrelated evidence.

Two checks run on a would-be DIRECT verdict for a long compound sentence:

* **Unverified clause.** The sentence is split into material clauses (em-dash
  elaborations, "; ", ", <gerund>", "and an ability to ...", and compositional
  connectors). A clause no cited record supports — by a literal lexicon term or by
  real token overlap — caps the requirement at PARTIAL: "well-scoped code changes in
  a large codebase" is not proven by Git and CI/CD hits elsewhere in the sentence.
* **Unverified relationship.** When a compositional connector says one capability
  was performed USING / VIA / TO ENSURE another ("build financial partner
  integrations using agentic tooling"), every material clause must be supported
  within ONE coherent evidence context: the same project, the same scope-level
  record, or records explicitly linked through ``corroborated_by``. The same
  employer alone is not a context; a billing-pipeline record plus an unrelated AI
  project never proves the composed claim.

What this module never touches:

* simple skill inventories ("Experience with Python, SQL and APIs") and pure
  AND / OR / mixed lists: without a compositional connector the context check does
  not apply, and clauses carried by matched lexicon terms stay supported;
* PARTIAL / TRANSFERABLE / UNSUPPORTED verdicts (it only ever lowers DIRECT);
* the union rule for plain term lists — records may still jointly cover a list,
  they just may not be stitched into a composed historical claim.

The rationale names the supported clauses and the missing connective tissue.
"""

from __future__ import annotations

import re

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import GENERIC_TERMS, canonical, find_terms
from resume_tailor.core.models import EvidenceRecord, MatchedEvidence, MatchType, Requirement
from resume_tailor.core.text import content_tokens

# one capability performed by means of / in service of another: the relationship itself needs evidence
_COMPOSED = re.compile(
    r"\b(?:using|leveraging|by using|powered by|with the help of|via|to ensure|in order to)\b",
    re.IGNORECASE,
)
# deliberately narrow boundaries: an em-dash elaboration, an "and an ability to ..." conjunction,
# and the composed connectors themselves. Plain comma lists, "; " asides and ", <gerund>"
# elaborations are NOT boundaries: simple multi-skill sentences keep their existing verdicts.
_SPLIT = re.compile(
    r"\s*[—–]\s*"  # em-dash elaboration
    r"|\s+and\s+(?=(?:an?|the)\s+(?:ability|instinct|capacity|willingness)\b)"  # "and an ability to ..."
    r"|\s+(?=(?:using|leveraging|by using|powered by|with the help of|via)\b)"  # means
    r"|\s+(?=to ensure\b|in order to\b)",  # purpose
    re.IGNORECASE,
)
_MIN_WORDS = 12  # shorter lines are term inventories, handled by the existing rules


def material_clauses(text: str) -> list[str]:
    """The sentence's material clauses; fragments of fewer than two content words are noise."""
    out = []
    for c in _SPLIT.split(text.strip().rstrip(".")):
        c = c.strip(" .,;:")
        if re.match(r"^you(?:['’]|\s)", c, re.IGNORECASE):
            continue  # second-person disposition prose ("you verify before you trust") is not a clause to evidence
        if c and len(content_tokens(c)) >= 2 and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out


def _supports(rec: EvidenceRecord, clause: str, index: EvidenceIndex) -> bool:
    """The record vouches for this clause: a specific lexicon term of the clause is the
    record's, or at least half of the clause's content words appear in the record's text."""
    rec_terms = index.record_terms(rec)
    terms = [canonical(t) for t in find_terms(clause) if canonical(t) not in GENERIC_TERMS]
    if terms and any(t in rec_terms for t in terms):
        return True
    toks = content_tokens(clause)
    hit = toks & content_tokens(index.record_text(rec))
    return len(hit) >= 2 and len(hit) * 2 >= len(toks)


def _contexts(recs: list[EvidenceRecord]) -> dict[str, set[str]]:
    """Record id -> its coherence context (a set of record ids). A context is one project,
    or one scope-level record (no project), widened by explicit corroborated_by links.
    Employer alone never merges contexts."""
    key = {r.id: (r.position_id, r.project) if r.project else (r.position_id, r.id) for r in recs}
    groups: dict[tuple, set[str]] = {}
    for r in recs:
        groups.setdefault(key[r.id], set()).add(r.id)
    # corroborated_by is an explicit same-fact link: merge the two records' contexts
    changed = True
    while changed:
        changed = False
        for r in recs:
            for other in getattr(r, "corroborated_by", []) or []:
                if other in key and key[other] != key[r.id]:
                    a, b = groups[key[r.id]], groups[key[other]]
                    merged = a | b
                    for rid in merged:
                        key[rid] = key[r.id]
                    groups[key[r.id]] = merged
                    changed = True
    return {rid: groups[k] for rid, k in key.items()}


def compound_claim_check(
    req: Requirement, ev: list[MatchedEvidence], index: EvidenceIndex
) -> tuple[MatchType, str] | None:
    """None when a DIRECT verdict stands; otherwise (PARTIAL, note) naming the supported
    clauses and the unverified clause or relationship."""
    if len(req.text.split()) < _MIN_WORDS and not _COMPOSED.search(req.text):
        return None  # a short line without a composed relationship is a term inventory
    clauses = material_clauses(req.text)
    if len(clauses) < 2:
        return None
    recs = [index.by_id[e.evidence_id] for e in ev if e.evidence_id in index.by_id]
    if not recs:
        return None
    supporters = {c: [r.id for r in recs if _supports(r, c, index)] for c in clauses}
    unverified = [c for c, ids in supporters.items() if not ids]
    relationship = ""
    if not unverified and _COMPOSED.search(req.text):
        ctx = _contexts(recs)
        coherent = any(all(set(supporters[c]) & ctx[r.id] for c in clauses) for r in recs)
        if not coherent:
            relationship = "the clauses are evidenced only in unrelated projects; no single evidence context connects them"
    if not unverified and not relationship:
        return None
    supported = [c for c in clauses if supporters[c]]
    note = "compound claim: supported clauses: " + (
        "; ".join(f"'{c[:60]}'" for c in supported) or "none"
    )
    if unverified:
        note += " | unverified clause(s): " + "; ".join(f"'{c[:80]}'" for c in unverified)
    if relationship:
        note += f" | unverified relationship: {relationship}"
    return MatchType.PARTIAL, note

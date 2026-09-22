# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Evidence matching: for every JD requirement, rank supporting evidence and
classify the match as direct / transferable / partial / unsupported.

The deterministic pass computes a *ceiling* for each requirement:

* DIRECT      - a lexicon term of the requirement is literally vouched for by
                an evidence record (skills/technologies/aliases/domains/text),
                and the record's proficiency is hands-on or led. Integration-only
                evidence against a requirement worded as administration/config
                is capped at PARTIAL; exposure-only evidence is capped at PARTIAL.
* TRANSFERABLE- no literal term, but a term in the same concept group
                (e.g. Tableau asked, Power BI evidenced).
* PARTIAL     - meaningful word overlap between requirement and evidence text.
* UNSUPPORTED - nothing.

An optional LLM pass may re-rank evidence, add semantic matches and write the
rationale, but it can never raise a requirement above its deterministic
ceiling, never reference evidence ids outside the bank, and its "direct"
verdict is ignored unless the deterministic pass also found a literal hit.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import (
    CONCEPT_GROUPS,
    GENERIC_TERMS,
    canonical,
    capability_products,
    evidenced_terms,
    has_admin_cue,
)
from resume_tailor.core.matching.behavioral import is_behavioural, match_behavioural
from resume_tailor.core.matching.composition import compound_claim_check
from resume_tailor.core.matching.credentials import credential_ask, match_credential
from resume_tailor.core.matching.disjunctive import is_disjunctive, match_disjunctive
from resume_tailor.core.matching.groups import is_mixed_group, match_grouped
from resume_tailor.core.matching.similar import similar_clause_cap
from resume_tailor.core.models import (
    MATCH_RANK,
    EvidenceRecord,
    JobAnalysis,
    MatchedEvidence,
    MatchReport,
    MatchType,
    Proficiency,
    Requirement,
    RequirementCategory,
    RequirementMatch,
)
from resume_tailor.core.text import content_tokens, years_claims
from resume_tailor.providers.llm.base import LLMError, LLMProvider

SYSTEM_CATEGORIES = {"crm", "erp", "hris", "ats"}

# Domains the candidate worked *with* (teams, processes, automation for them) rather than *as* a
# practitioner. A bare requirement for the domain itself is PARTIAL unless the requirement is
# about collaborating with / automating for those teams.
# domains the bank records only as teams/processes worked with (``domains``), never as the candidate's own practice:
# asked for as a skill in itself they are "domain exposure", not practitioner experience
PRACTITIONER_DOMAINS = {
    "tax",
    "bookkeeping",
    "accounting",
    "payroll",
    "compliance",
    "recruiting",
    "compensation",
    "general ledger",
    "chart of accounts",
    "customer success",
}
_COLLAB_CUES = re.compile(
    r"\b(teams?|stakeholders?|partner|collaborat\w*|across|environments?|processes|operations|workflows?|for the|with the|support(ing)?|automat\w*|integrat\w*)\b",
    re.IGNORECASE,
)
_OPERATE_CUES = re.compile(
    r"\b(maintain|manage|own|administer|configure|build and maintain|data integrity)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You match job requirements to a candidate's evidence bank for a resume tool.
You will receive requirements (with ids) and evidence records (with ids). For each requirement,
return the evidence ids that genuinely support it, ranked best first, with a match type:
- "direct": the evidence shows the candidate did this / used this tool hands-on.
- "transferable": closely related tool or capability (e.g. Power BI when Tableau is asked).
- "partial": only part of the requirement is covered, or the depth is lower than asked.
- "unsupported": nothing in the evidence supports it. Say so plainly. Do not stretch.
Never invent evidence. Never treat integration with a platform as administering it.
Return STRICT JSON: {"matches": [{"requirement_id": "...", "match_type": "...", "evidence_ids": ["..."], "rationale": "one sentence"}]}"""


def _group_terms(term: str) -> set[str]:
    out: set[str] = set()
    for members in CONCEPT_GROUPS.values():
        if term in members:
            out.update(members)
    out.discard(term)
    return out


def _proficiency_cap(
    rec: EvidenceRecord,
    req: Requirement,
    index: EvidenceIndex | None = None,
    exact: list[str] | None = None,
) -> tuple[MatchType, str]:
    """Deterministic ceiling for a literal term hit, with the reason when it is not DIRECT.

    The proficiency that matters is the record's depth *for the matched terms*: a delivery record
    that merely lists Salesforce among its tools carries ``tool_proficiency`` for it."""
    low = req.text.lower()
    wants_cert = "certif" in low
    profs = {
        (index.effective_proficiency(rec, t) if index is not None else rec.proficiency_for(t))
        for t in (exact or [])
    } or {(index.effective_proficiency(rec) if index is not None else rec.proficiency)}
    weakest = min(
        profs,
        key=lambda p: {
            Proficiency.LED: 4,
            Proficiency.HANDS_ON: 3,
            Proficiency.INTEGRATION: 2,
            Proficiency.EXPOSURE: 1,
            Proficiency.CERTIFICATION: 0,
        }[p],
    )
    if weakest == Proficiency.CERTIFICATION:
        return (MatchType.DIRECT, "") if wants_cert else (MatchType.PARTIAL, "certification only")
    if wants_cert:
        return (
            MatchType.PARTIAL,
            "requirement asks for a certification; evidence is work experience",
        )
    if weakest == Proficiency.EXPOSURE:
        return MatchType.PARTIAL, "exposure-level evidence"
    if weakest == Proficiency.INTEGRATION and has_admin_cue(req.text):
        return (
            MatchType.PARTIAL,
            "requirement implies platform administration; evidence is integration",
        )
    if weakest == Proficiency.INTEGRATION and _OPERATE_CUES.search(low) and "integrat" not in low:
        return (
            MatchType.PARTIAL,
            "requirement is about operating the platform; evidence is integrating with it",
        )
    # "N+ years of X": the evidence must span at least N years
    yrs = years_claims(req.text)
    if yrs and index is not None:
        need = max(yrs)
        have = _term_years(rec, req, index)
        if have + 0.5 < need:
            return (
                MatchType.PARTIAL,
                f"{need}+ years asked; matching evidence spans ~{have:.1f} years",
            )
    return MatchType.DIRECT, ""


def _term_years(rec: EvidenceRecord, req: Requirement, index: EvidenceIndex) -> float:
    """Years covered by every record that vouches for any literal term of the requirement."""
    terms = {canonical(t) for t in req.terms} - GENERIC_TERMS
    terms |= {p for t in list(terms) for p in capability_products(t)}
    ids: set[str] = set()
    for t in terms:
        ids |= index.term_index.get(t, set())
    ids.add(rec.id)
    spans = []
    for i in ids:
        r = index.by_id[i]
        if not (r.include_by_default or r.id == rec.id):
            continue
        # a record only counts toward years for a term it holds at integration depth or better
        if any(
            r.proficiency_for(t) in (Proficiency.EXPOSURE, Proficiency.CERTIFICATION)
            for t in terms
            if t in index.record_terms(r)
        ):
            continue
        # a secondary-only record (portfolio / LinkedIn / resume alone) cannot establish a years span
        if index.secondary_only(r):
            continue
        spans.append((r.start, r.end))
    if not spans:
        return 0.0
    from datetime import date

    now = date.today()  # noqa: DTZ011 - tenure uses the local calendar date.
    now_k = now.year * 12 + now.month

    def key(s):
        return int(s[:4]) * 12 + int(s[5:7])

    ranges = sorted((key(s), key(e) if e else now_k) for s, e in spans)
    merged: list[list[int]] = []
    for s, e in ranges:
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return sum(e - s for s, e in merged) / 12


def _min(a: MatchType, b: MatchType) -> MatchType:
    return a if MATCH_RANK[a] <= MATCH_RANK[b] else b


def score_requirement(req: Requirement, index: EvidenceIndex) -> list[MatchedEvidence]:
    """Deterministic scoring of every default record against one requirement."""
    req_terms = [canonical(t) for t in req.terms]
    specific_terms = [t for t in req_terms if t not in GENERIC_TERMS]
    generic_terms = [t for t in req_terms if t in GENERIC_TERMS]
    req_tokens = content_tokens(req.text)
    results: list[MatchedEvidence] = []
    for rec in index.default_records():
        rec_terms = index.record_terms(rec)
        # a system category (crm/erp/hris/ats) counts as a specific term when the record
        # declares it explicitly (aliases/domains), not merely mentions it in prose
        explicit = {canonical(t) for t in rec.terms}
        promoted = [t for t in generic_terms if t in SYSTEM_CATEGORIES and t in explicit]
        exact = [t for t in specific_terms if t in rec_terms] + promoted
        # a general capability ("analytics") is a literal hit when the record used a product that
        # delivers it; depth is the record's depth for that product (see lexicon.CAPABILITY_PRODUCTS)
        via_product = {
            t: sorted(capability_products(t) & rec_terms)
            for t in specific_terms
            if t not in rec_terms
        }
        via_product = {t: ps for t, ps in via_product.items() if ps}
        exact += list(via_product)
        depth_terms = [t for t in exact if t not in via_product] + [
            p for ps in via_product.values() for p in ps
        ]
        exact_generic = [t for t in generic_terms if t in rec_terms and t not in promoted]
        transfer = [t for t in specific_terms if not exact and (_group_terms(t) & rec_terms)]
        rec_tokens = content_tokens(
            f"{rec.claim} {rec.resume_text} {' '.join(rec.responsibilities)} {' '.join(rec.skills)}"
        )
        overlap = req_tokens & rec_tokens
        overlap_score = len(overlap) / max(4, len(req_tokens)) if req_tokens else 0.0

        # an explicitly declared system category is usually the subject of the requirement: weight it above a plain tool hit
        score = (
            3.0 * (len(exact) - len(promoted))
            + 4.0 * len(promoted)
            + 1.0 * len(exact_generic)
            + 1.5 * len(transfer)
            + 4.0 * overlap_score
        )
        if rec.verification.value == "primary":
            score *= 1.1
        score *= 0.6 + 0.4 * rec.confidence
        if score < 0.75:
            continue
        cap_reason = ""
        if exact:
            mt, cap_reason = _proficiency_cap(rec, req, index, depth_terms)
            # a literal hit on a minority of the requirement's specific terms is only PARTIAL,
            # unless the requirement is an "or" list where any one item satisfies it
            is_or_list = re.search(r"\b(or|and/or)\b", req.text.lower()) is not None
            required = specific_terms + [t for t in generic_terms if t in SYSTEM_CATEGORIES]
            if (
                mt == MatchType.DIRECT
                and len(required) >= 2
                and not is_or_list
                and len(exact) * 2 <= len(required)
            ):
                missing = [t for t in required if t not in exact]
                mt, cap_reason = (
                    MatchType.PARTIAL,
                    f"covers {len(exact)} of {len(required)} required terms; missing: {', '.join(missing[:5])}",
                )
            # a specialization of a matched term that nothing evidences ("salesforce flow" vs "salesforce")
            if mt == MatchType.DIRECT:
                for t in specific_terms:
                    if (
                        t not in rec_terms
                        and t not in index.all_terms
                        and any(t.startswith(m + " ") for m in exact)
                    ):
                        mt, cap_reason = (
                            MatchType.PARTIAL,
                            f"asks specifically for '{t}'; only '{next(m for m in exact if t.startswith(m + ' '))}' is evidenced",
                        )
                        break
            # domain the candidate worked with, asked for as a practitioner skill
            if (
                mt == MatchType.DIRECT
                and exact
                and all(t in PRACTITIONER_DOMAINS for t in exact)
                and not _COLLAB_CUES.search(req.text)
            ):
                explicit_skill = {canonical(t) for t in (rec.skills + rec.technologies)} | {
                    canonical(t) for t in rec.responsibilities
                }
                if not any(t in explicit_skill for t in exact):
                    mt, cap_reason = (
                        MatchType.PARTIAL,
                        f"domain exposure ({', '.join(exact)}: worked with those teams/processes), not practitioner experience",
                    )
        elif transfer:
            mt = MatchType.TRANSFERABLE
            if has_admin_cue(req.text) or "certif" in req.text.lower():
                mt, cap_reason = (
                    MatchType.PARTIAL,
                    "related tool only; requirement asks for administration/certification",
                )
            elif (
                len(specific_terms) >= 2
                and not re.search(r"\b(or|and/or)\b", req.text.lower())
                and len(transfer) * 2 <= len(specific_terms)
            ):
                mt, cap_reason = (
                    MatchType.PARTIAL,
                    f"related tools cover {len(transfer)} of {len(specific_terms)} required terms",
                )
        elif exact_generic and overlap_score >= 0.25 and len(overlap) >= 2:
            mt = MatchType.PARTIAL
            score += 0.5
        elif overlap_score >= 0.3:
            mt = MatchType.PARTIAL
        else:
            continue
        reason_bits = []
        if exact:
            reason_bits.append(
                "literal: "
                + ", ".join(
                    f"{t} (via {', '.join(via_product[t])})" if t in via_product else t
                    for t in exact
                )
            )
        if transfer:
            reason_bits.append("related: " + ", ".join(transfer))
        if overlap and not exact and not transfer:
            reason_bits.append("overlap: " + ", ".join(sorted(overlap)[:6]))
        if cap_reason:
            reason_bits.append(f"capped: {cap_reason}")
        results.append(
            MatchedEvidence(
                evidence_id=rec.id,
                score=round(score, 2),
                match_type=mt,
                matched_terms=[*exact, *transfer, *exact_generic],
                reason="; ".join(reason_bits),
            )
        )
    results.sort(key=lambda m: (-MATCH_RANK[m.match_type], -m.score))
    return results


PLATFORM_TERMS = {
    t
    for g in ("crm", "erp", "hris", "ats", "billing", "marketing_automation", "ecommerce")
    for t in CONCEPT_GROUPS[g]
} - GENERIC_TERMS


def _evidenced(term: str, index: EvidenceIndex) -> bool:
    """The bank holds the term itself, or a product that delivers it when the term is a general capability."""
    return term in evidenced_terms(index.all_terms)


def _requirement_level_verdict(
    req: Requirement, ev: list[MatchedEvidence], index: EvidenceIndex
) -> tuple[MatchType, str]:
    """Combine per-record verdicts into one requirement verdict.

    * Different records may cover different concepts of one requirement ("Power BI, SQL and
      Supabase"): if the union of literal hits across the listed records covers more than half of
      the specific terms, and every unmet record cap is only the majority rule, the requirement
      is DIRECT (the resume cites each record for its own clause).
    * A specific term that no record in the bank evidences ("salesforce flow", "prompt design")
      caps a multi-term requirement at PARTIAL: related tools cannot stand in for it.
    * A platform the candidate only integrated with, asked for as something to operate
      ("maintain Salesforce data integrity"), caps at PARTIAL.
    """
    if not ev:
        return MatchType.UNSUPPORTED, ""
    best = ev[0].match_type
    specific = [canonical(t) for t in req.terms if canonical(t) not in GENERIC_TERMS]
    low = req.text.lower()
    is_or = re.search(r"\b(or|and/or)\b", low) is not None
    unevidenced = [t for t in specific if not _evidenced(t, index)]
    # "N+ years of <specialization>" where the specialization (e.g. "salesforce flow") is unevidenced and
    # only its parent platform is: the years ask has zero support, so the requirement is UNSUPPORTED
    if unevidenced and years_claims(req.text):
        parents = [m for u in unevidenced for m in specific if m != u and u.startswith(m + " ")]
        if parents:
            ev.clear()
            return (
                MatchType.UNSUPPORTED,
                f"asks for years of '{unevidenced[0]}', which nothing evidences; '{parents[0]}' alone is evidenced only at integration/exposure depth",
            )
    if (
        len(specific) >= 2
        and not is_or
        and unevidenced
        and best in (MatchType.DIRECT, MatchType.TRANSFERABLE)
    ):
        return MatchType.PARTIAL, f"'{unevidenced[0]}' is not evidenced anywhere in the bank"
    # "N+ years of <platform>": years apply to the named platform; a related platform cannot supply them
    if years_claims(low) and best == MatchType.TRANSFERABLE:
        literal_years_capped = [
            e for e in ev if e.reason.startswith("literal") and "years asked" in e.reason
        ]
        if literal_years_capped:
            return MatchType.PARTIAL, literal_years_capped[0].reason.split("capped: ")[-1]
        if any(t in PLATFORM_TERMS for t in specific):
            return (
                MatchType.PARTIAL,
                f"years of {next(t for t in specific if t in PLATFORM_TERMS)} asked; only related platforms are evidenced",
            )
    # a practitioner domain asked for as a skill in itself: related finance domains do not make her a practitioner
    if (
        specific
        and all(t in PRACTITIONER_DOMAINS for t in specific)
        and not _COLLAB_CUES.search(req.text)
        and best in (MatchType.DIRECT, MatchType.TRANSFERABLE)
    ):
        return (
            MatchType.PARTIAL,
            f"domain exposure ({', '.join(specific)}: worked with those teams/processes), not practitioner experience",
        )
    # platform asked for as something to operate, evidenced only as integration/exposure
    if (
        best in (MatchType.DIRECT, MatchType.TRANSFERABLE)
        and _OPERATE_CUES.search(low)
        and "integrat" not in low
    ):
        for t in specific:
            if t in PLATFORM_TERMS:
                vouchers = [index.by_id[i] for i in index.term_index.get(t, set())]
                if vouchers and all(
                    v.proficiency_for(t)
                    in (Proficiency.INTEGRATION, Proficiency.EXPOSURE, Proficiency.CERTIFICATION)
                    for v in vouchers
                ):
                    return (
                        MatchType.PARTIAL,
                        f"{t} evidence is integration-level; the requirement is about operating the platform",
                    )
    # union coverage across records lifts a majority-rule PARTIAL to DIRECT
    if (
        best in (MatchType.PARTIAL, MatchType.TRANSFERABLE)
        and len(specific) >= 2
        and not is_or
        and not unevidenced
    ):
        # only records whose sole cap is the majority rule may contribute; proficiency/years/admin/
        # certification caps are real ceilings and those records stay out of the union
        literal_evs = [
            e
            for e in ev
            if e.reason.startswith("literal")
            and ("capped:" not in e.reason or "capped: covers" in e.reason)
        ]
        union: set[str] = set()
        contributors: list[str] = []
        for e in literal_evs:
            rec_terms = index.record_terms(index.by_id[e.evidence_id])
            hits = {t for t in specific if t in rec_terms or (capability_products(t) & rec_terms)}
            if hits - union:
                contributors.append(e.evidence_id)
            union |= hits
        if literal_evs and len(union) * 2 > len(specific):
            return MatchType.DIRECT, "covered jointly by " + ", ".join(contributors[:4])
    return best, ""


def deterministic_match(job: JobAnalysis, index: EvidenceIndex, top_k: int = 8) -> MatchReport:
    matches: list[RequirementMatch] = []
    for req in job.requirements:
        ev = score_requirement(req, index)
        best, note = _requirement_level_verdict(req, ev, index)
        best, note = _secondary_source_ceiling(best, note, ev, index)
        # a compound behavioural requirement ("take ownership and drive projects independently") has
        # no tool to anchor on; match it clause by clause against demonstrated behaviour instead
        weak = (
            not ev
            or best == MatchType.UNSUPPORTED
            or (best == MatchType.PARTIAL and not any(e.reason.startswith("literal") for e in ev))
        )
        # an OR-list ("website backend tasks or CMS platforms") is met by its strongest alternative
        if weak and is_disjunctive(req.text):
            d_best, d_note, d_ev = match_disjunctive(req, index)
            if MATCH_RANK[d_best] > MATCH_RANK[best] or (d_best == best and d_ev and not ev):
                best, note, ev = d_best, d_note, d_ev
                weak = best == MatchType.UNSUPPORTED
        if weak and is_behavioural(req):
            b_best, b_note, b_ev = match_behavioural(req, index)
            if MATCH_RANK[b_best] > MATCH_RANK[best] or (b_best == best and b_ev):
                best, note, ev = b_best, b_note, b_ev
        # a mixed AND/OR list ("HubSpot, ClickUp, and ChurnZero or Planhat") is conjunctive groups
        # and every group must hold: one strong group never carries the whole requirement, so the
        # grouped verdict caps the lexical one (and may lift it only when the lexical pass found
        # nothing, under the same per-alternative caps)
        if is_mixed_group(req.text):
            g_best, g_note, g_ev = match_grouped(req, index)
            if MATCH_RANK[g_best] < MATCH_RANK[best] or (
                weak and MATCH_RANK[g_best] > MATCH_RANK[best]
            ):
                best, note, ev = g_best, g_note, g_ev
        # a named credential is a fact, held or not: work experience never makes it PARTIAL
        cred = credential_ask(req.text)
        if cred is not None:
            best, note, ev = match_credential(cred, req.terms, ev, index)
        # "X or similar frameworks" is category-bounded: the named product or a concrete evidenced
        # peer of its tool family; neighbouring vocabulary (REST APIs, Python, automation) never
        # satisfies the clause, so a DIRECT resting on it caps at PARTIAL with the gap named
        if best == MatchType.DIRECT:
            sim_note = similar_clause_cap(req.text, index)
            if sim_note is not None:
                best, note = MatchType.PARTIAL, sim_note
        # a compound claim must not be stitched together from unrelated evidence: every material
        # clause needs support, and a composed relationship ("X using Y") needs one coherent context
        # (a behavioural verdict already verified its clauses against demonstrated behaviour)
        if best == MatchType.DIRECT and "demonstrated behaviour" not in note:
            capped = compound_claim_check(req, ev, index)
            if capped is not None:
                best, note = capped
        if best == MatchType.DIRECT and not note.startswith("covered jointly by "):
            # the record that carries the DIRECT verdict must be visible in the reported evidence
            carrier = next(
                (
                    e
                    for e in ev
                    if e.match_type == MatchType.DIRECT
                    and e.reason.startswith("literal")
                    and not index.secondary_only(index.by_id[e.evidence_id])
                ),
                None,
            )
            if carrier is not None and carrier not in ev[:top_k]:
                ev.remove(carrier)
                ev.insert(min(len(ev), top_k - 1), carrier)
        hard = (
            bool(ev)
            and best != MatchType.DIRECT
            and any("capped:" in e.reason and "literal" in e.reason for e in ev)
        )
        if note and best == MatchType.PARTIAL:
            hard = True  # a requirement-level cap is a hard ceiling for the LLM pass as well
        if note.startswith("covered jointly by "):  # show the contributing literal records first
            contributors = [c.strip() for c in note[len("covered jointly by ") :].split(",")]
            ev.sort(
                key=lambda e: (
                    e.evidence_id not in contributors,
                    -MATCH_RANK[e.match_type],
                    -e.score,
                )
            )
        rationale = _rationale(best, ev[:3], index) + (f" [{note}]" if note else "")
        matches.append(
            RequirementMatch(
                requirement_id=req.id,
                requirement_text=req.text,
                category=req.category,
                match_type=best,
                evidence=ev[:top_k],
                deterministic_ceiling=best,
                hard_capped=hard,
                rationale=rationale,
            )
        )
    return _finish(matches, job, "deterministic")


def _secondary_source_ceiling(
    best: MatchType, note: str, ev: list[MatchedEvidence], index: EvidenceIndex
) -> tuple[MatchType, str]:
    """A requirement is DIRECT only when at least one literal DIRECT hit comes from primary,
    corroborated or user-verified evidence; secondary-only records may join, not carry, it."""
    if best != MatchType.DIRECT:
        return best, note
    if note.startswith("covered jointly by "):
        contributors = [c.strip() for c in note[len("covered jointly by ") :].split(",")]
        carriers = [
            c for c in contributors if c in index.by_id and not index.secondary_only(index.by_id[c])
        ]
    else:
        carriers = [
            e.evidence_id
            for e in ev
            if e.match_type == MatchType.DIRECT
            and e.reason.startswith("literal")
            and not index.secondary_only(index.by_id[e.evidence_id])
        ]
    if carriers:
        return best, note
    only = [
        e.evidence_id
        for e in ev
        if e.match_type == MatchType.DIRECT and e.reason.startswith("literal")
    ]
    return (
        MatchType.PARTIAL,
        f"only secondary-source evidence supports this ({', '.join(only[:3])}); needs primary, corroborated or user-verified evidence",
    )


def _rationale(mt: MatchType, ev: list[MatchedEvidence], index: EvidenceIndex) -> str:
    if mt == MatchType.UNSUPPORTED or not ev:
        return "No evidence in the bank supports this requirement."
    names = [
        f"{index.by_id[e.evidence_id].company}: {index.by_id[e.evidence_id].project or index.by_id[e.evidence_id].claim[:60]}"
        for e in ev[:3]
    ]
    return f"{mt.value.title()} match via " + "; ".join(names)


def _finish(matches: list[RequirementMatch], job: JobAnalysis, source: str) -> MatchReport:
    gaps = [m.requirement_id for m in matches if m.match_type == MatchType.UNSUPPORTED]
    return MatchReport(
        matches=matches, gaps=gaps, coverage=coverage(matches), matching_source=source
    )


def coverage(matches: list[RequirementMatch]) -> dict[str, float]:
    """Reproducible coverage metrics. Direct + transferable count as covered; partial counts half."""

    def cov(cat: RequirementCategory | None) -> float:
        rel = [m for m in matches if cat is None or m.category == cat]
        if not rel:
            return 0.0
        credit = {
            MatchType.DIRECT: 1.0,
            MatchType.TRANSFERABLE: 1.0,
            MatchType.PARTIAL: 0.5,
            MatchType.UNSUPPORTED: 0.0,
        }
        return round(sum(credit[m.match_type] for m in rel) / len(rel), 3)

    def strict(cat: RequirementCategory) -> float:
        rel = [m for m in matches if m.category == cat]
        return (
            round(sum(1 for m in rel if m.match_type == MatchType.DIRECT) / len(rel), 3)
            if rel
            else 0.0
        )

    return {
        "must_have_evidence_coverage": cov(RequirementCategory.MUST_HAVE),
        "must_have_direct_coverage": strict(RequirementCategory.MUST_HAVE),
        "nice_to_have_evidence_coverage": cov(RequirementCategory.NICE_TO_HAVE),
        "responsibility_coverage": cov(RequirementCategory.RESPONSIBILITY),
        "technology_coverage": cov(RequirementCategory.TECHNOLOGY),
        "overall_coverage": cov(None),
        "requirements_total": float(len(matches)),
        "gaps_total": float(sum(1 for m in matches if m.match_type == MatchType.UNSUPPORTED)),
    }


def _evidence_digest(index: EvidenceIndex) -> str:
    """Compact one-line-per-record digest; kept small so local models can hold it in context."""
    rows = []
    for r in index.default_records():
        claim = r.claim if len(r.claim) <= 160 else r.claim[:157] + "..."
        rows.append(f"{r.id} | {r.company} | {r.proficiency.value} | {claim}")
    return "\n".join(rows)


def apply_llm_refinement(
    det: MatchReport, data: dict[str, Any], index: EvidenceIndex, job: JobAnalysis
) -> tuple[MatchReport, list[str]]:
    """Merge model output under the deterministic ceiling rules."""
    warnings: list[str] = []
    by_req = {m.requirement_id: m for m in det.matches}
    if isinstance(data, dict) and "requirement_id" in data and "matches" not in data:
        data = {"matches": [data]}  # small models sometimes return a single row instead of the list
    llm_rows = data.get("matches") if isinstance(data, dict) else None
    if not isinstance(llm_rows, list):
        return det, ["matching: LLM reply had no 'matches' list; deterministic result used"]
    for row in llm_rows:
        if not isinstance(row, dict):
            continue
        rid = str(row.get("requirement_id", ""))
        m = by_req.get(rid)
        if not m:
            continue
        try:
            proposed = MatchType(str(row.get("match_type", "unsupported")).lower())
        except ValueError:
            proposed = MatchType.UNSUPPORTED
        ids = [str(i) for i in row.get("evidence_ids", []) if isinstance(i, str | int)]
        valid_ids = [i for i in ids if i in index.by_id]
        if len(valid_ids) != len(ids):
            warnings.append(
                f"matching: dropped unknown evidence ids for {rid}: {sorted(set(ids) - set(valid_ids))}"
            )
        # LLM can propose transferable/partial from any record; direct only where the literal hit exists
        ceiling = m.deterministic_ceiling
        # semantic matching may reach TRANSFERABLE for anything with evidence; DIRECT needs a literal hit;
        # a literal hit that the rules explicitly capped (admin/cert/years/exposure) is a hard ceiling
        if ceiling == MatchType.DIRECT:
            allowed = MatchType.DIRECT
        elif m.hard_capped:
            allowed = ceiling
        else:
            allowed = MatchType.TRANSFERABLE
        if proposed == MatchType.DIRECT and ceiling != MatchType.DIRECT:
            proposed = MatchType.TRANSFERABLE if valid_ids else MatchType.UNSUPPORTED
        final = _min(proposed, allowed) if valid_ids else MatchType.UNSUPPORTED
        # never go from unsupported to anything without at least one evidence id
        existing = {e.evidence_id: e for e in m.evidence}
        merged: list[MatchedEvidence] = []
        for i in valid_ids:
            if i in existing:
                merged.append(existing[i])
            else:
                mt = _min(final, MatchType.TRANSFERABLE)
                merged.append(
                    MatchedEvidence(
                        evidence_id=i, score=1.0, match_type=mt, reason="semantic (LLM)"
                    )
                )
        for e in m.evidence:
            if e.evidence_id not in {x.evidence_id for x in merged}:
                merged.append(e)
        if not merged:
            final = MatchType.UNSUPPORTED
        elif final == MatchType.UNSUPPORTED:
            # LLM says unsupported but deterministic found something: keep deterministic verdict
            final = m.match_type
        m.match_type = final
        m.evidence = merged[:8]
        rationale = str(row.get("rationale", "")).strip()
        if rationale:
            m.rationale = rationale + (
                f" [capped at {final.value} by rules]" if proposed != final else ""
            )
    return _finish(det.matches, job, "llm+deterministic"), warnings


class EvidenceMatcher:
    """Career Agent integration point: ``match(job, index) -> MatchReport``."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def match(self, job: JobAnalysis, index: EvidenceIndex) -> tuple[MatchReport, list[str]]:
        det = deterministic_match(job, index)
        warnings: list[str] = []
        if self.llm is not None and self.llm.name != "none":
            reqs = [
                {"id": r.id, "category": r.category.value, "text": r.text} for r in job.requirements
            ]
            user = f"REQUIREMENTS:\n{json.dumps(reqs, ensure_ascii=False)}\n\nEVIDENCE:\n{_evidence_digest(index)}"
            try:
                resp = self.llm.complete_json(SYSTEM_PROMPT, user, max_tokens=6000)
                if resp.data:
                    det, w = apply_llm_refinement(det, resp.data, index, job)
                    warnings.extend(w)
            except LLMError as e:
                warnings.append(f"matching: LLM unavailable, deterministic matches used ({e})")
        return det, warnings


def evidence_relevance(report: MatchReport, index: EvidenceIndex | None = None) -> dict[str, float]:
    """evidence_id -> summed weighted relevance across requirements it supports.

    Records that carry no metrics (tool lists, role statements) are dampened so that
    achievement records lead each position when relevance is otherwise similar."""
    rel: dict[str, float] = defaultdict(float)
    for m in report.matches:
        w = {
            RequirementCategory.MUST_HAVE: 3.0,
            RequirementCategory.RESPONSIBILITY: 2.0,
            RequirementCategory.TECHNOLOGY: 2.0,
            RequirementCategory.CAPABILITY: 2.0,
            RequirementCategory.DOMAIN: 1.5,
            RequirementCategory.NICE_TO_HAVE: 1.0,
        }[m.category]
        for rank, e in enumerate(m.evidence):
            credit = {
                MatchType.DIRECT: 1.0,
                MatchType.TRANSFERABLE: 0.7,
                MatchType.PARTIAL: 0.4,
                MatchType.UNSUPPORTED: 0.0,
            }[e.match_type]
            rel[e.evidence_id] += w * credit / (1 + 0.35 * rank)
    if index is not None:
        for eid in list(rel):
            r = index.by_id.get(eid)
            if r is not None and not r.metrics:
                rel[eid] *= 0.7
    return dict(rel)

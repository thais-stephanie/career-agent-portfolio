# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Claim validation: the gate between generation and anything a human sees.

Every bullet / summary sentence / skill item is checked in pure code:

  evidence_exists    every cited evidence id is in the bank (and non-empty)
  bindings           a bullet citing more than one record must bind each clause
                     to exactly one record; every lexicon term and number in
                     the text must fall inside a bound clause
  terms_supported    every lexicon term in a clause is vouched for by *that
                     clause's* record
  numbers_supported  every number in a clause exists in *that clause's* record
                     (a number cannot ride on a claim another record supports)
  no_upgrade         expertise / administration / people-management cue words
                     require matching proficiency or responsibility
  no_overstatement   scope words ("company-wide", "infrastructure", "global" ...)
                     and ownership verbs ("owned", "led", "spearheaded" ...)
                     must appear in the bound record's own text, or the record
                     must have LED proficiency for ownership verbs
  years_plausible    "N+ years" claims do not exceed total relevant experience
  structure          company / title / dates of every experience entry equal the
                     Position record; no unknown positions; chronology intact

An optional LLM faithfulness judge can additionally *reject* bullets it finds
unsupported by their cited records; it can never approve something the
deterministic checks rejected.

A failing bullet is replaced by the verbatim ``resume_text`` of its first cited
record when that passes, otherwise removed. Both outcomes are recorded in the
claim-to-evidence map. Skill items that no record vouches for are removed.
"""

from __future__ import annotations

import json
import re

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import (
    ADMIN_PROSE_CUES,
    EXPERTISE_CUES,
    MANAGEMENT_PROSE_CUES,
    OWNERSHIP_VERBS,
    SCOPE_CUES,
    canonical,
    evidenced_terms,
    find_terms,
    term_pattern,
)
from resume_tailor.core.models import (
    Bullet,
    ClaimBinding,
    ClaimCheck,
    ClaimEntry,
    ClaimEvidenceMap,
    EvidenceRecord,
    GeneratedResume,
    JobAnalysis,
    Proficiency,
    SkillGroup,
    TailorOptions,
    ValidationIssue,
    ValidationReport,
)
from resume_tailor.core.text import number_variants, numbers_in, years_claims
from resume_tailor.providers.llm.base import LLMError, LLMProvider

# numbers that are ordinary prose, not metrics
_TRIVIAL_NUMBERS = {"1", "2", "3", "one", "two", "three"}


# --------------------------------------------------------------------------
# per-clause checks
# --------------------------------------------------------------------------


def _terms_check(text: str, rec: EvidenceRecord, index: EvidenceIndex, where: str) -> ClaimCheck:
    allowed = index.record_terms(rec)
    bad = [t for t in find_terms(text) if t not in allowed]
    return ClaimCheck(
        check="terms_supported",
        passed=not bad,
        detail=(f"{where}unsupported terms: " + ", ".join(bad)) if bad else "",
    )


def _numbers_check(text: str, rec: EvidenceRecord, index: EvidenceIndex, where: str) -> ClaimCheck:
    allowed = index.record_numbers(rec)
    bad = [
        n
        for n in numbers_in(text)
        if n not in _TRIVIAL_NUMBERS and not (number_variants(n) & allowed)
    ]
    return ClaimCheck(
        check="numbers_supported",
        passed=not bad,
        detail=(f"{where}numbers not in evidence {rec.id}: " + ", ".join(bad)) if bad else "",
    )


def _overstatement_check(
    text: str, rec: EvidenceRecord, index: EvidenceIndex, where: str
) -> ClaimCheck:
    low = text.lower()
    rec_text = index.record_text(rec)
    issues = []
    for cue in SCOPE_CUES:
        if re.search(rf"\b{re.escape(cue)}\b", low) and not re.search(
            rf"\b{re.escape(cue)}\b", rec_text
        ):
            issues.append(f"scope word '{cue}' not in evidence {rec.id}")
    for verb in OWNERSHIP_VERBS:
        if (
            re.search(rf"\b{re.escape(verb)}\b", low)
            and rec.proficiency != Proficiency.LED
            and not re.search(rf"\b{re.escape(verb)}\b", rec_text)
        ):
            issues.append(
                f"ownership verb '{verb}' beyond {rec.proficiency.value} evidence {rec.id}"
            )
    return ClaimCheck(
        check="no_overstatement",
        passed=not issues,
        detail=(where + "; ".join(issues)) if issues else "",
    )


_WORD = re.compile(r"[A-Za-z0-9$%.,+-]+")


def _number_scope_check(
    text: str, rec: EvidenceRecord, index: EvidenceIndex, where: str
) -> ClaimCheck:
    """A guarded number keeps its declared meaning: an ownership/totality word within the window,
    with no restoring word between them, widens the scope and fails."""
    if not rec.number_scopes:
        return ClaimCheck(check="number_scope", passed=True, detail="")
    words = [w.strip(".,;:()").lower() for w in _WORD.findall(text)]
    issues: list[str] = []
    notes: list[str] = []
    for g in rec.number_scopes:
        positions = [
            i for i, w in enumerate(words) if w == g.number or w.startswith(g.number + "-")
        ]
        if not positions:
            continue
        notes.append(f"'{g.number}' = {g.meaning}")
        for pos in positions:
            lo, hi = max(0, pos - g.window), min(len(words), pos + g.window + 1)
            for j in range(lo, hi):
                if j != pos and words[j] in g.not_near:
                    between = words[min(j, pos) + 1 : max(j, pos)]
                    if not any(b in g.unless_between for b in between):
                        issues.append(
                            f"'{words[j]}' next to '{g.number}' widens its scope ({g.meaning}) in {rec.id}"
                        )
                        break
    detail = (where + "; ".join(issues)) if issues else ("; ".join(notes) if notes else "")
    return ClaimCheck(check="number_scope", passed=not issues, detail=detail)


def _upgrade_check(text: str, recs: list[EvidenceRecord]) -> ClaimCheck:
    low = text.lower()
    profs = {r.proficiency for r in recs}
    resp_blob = " ".join(x for r in recs for x in r.responsibilities).lower()
    issues = []
    if any(c in low for c in EXPERTISE_CUES) and not (
        profs & {Proficiency.LED, Proficiency.HANDS_ON}
    ):
        issues.append("expertise wording without hands-on evidence")
    if (
        any(c in low for c in ADMIN_PROSE_CUES)
        and "administration" not in resp_blob
        and "administer" not in resp_blob
    ):
        issues.append("administration wording; evidence is integration/usage only")
    if any(c in low for c in MANAGEMENT_PROSE_CUES) and "people management" not in resp_blob:
        issues.append("people-management wording without evidence")
    return ClaimCheck(check="no_upgrade", passed=not issues, detail="; ".join(issues))


def _bindings_check(b: Bullet, index: EvidenceIndex) -> tuple[ClaimCheck, list[ClaimBinding]]:
    """Multi-record bullets need atomic bindings covering every term and number."""
    ids = [e for e in b.evidence_ids if e in index.by_id]
    if len(ids) <= 1:
        return ClaimCheck(check="bindings", passed=True, detail="single record"), []
    if not b.bindings:
        return ClaimCheck(
            check="bindings",
            passed=False,
            detail=f"{len(ids)} records cited without clause bindings",
        ), []
    text = b.text
    low = text.lower()
    problems = []
    spans: list[tuple[int, int, ClaimBinding]] = []
    for bd in b.bindings:
        if bd.evidence_id not in ids:
            problems.append(f"binding cites {bd.evidence_id} which is not in evidence_ids")
            continue
        clause = bd.clause.strip()
        pos = low.find(clause.lower())
        if not clause or pos < 0:
            problems.append(f"clause not found in text: {bd.clause[:50]!r}")
            continue
        spans.append((pos, pos + len(clause), bd))
    if problems:
        return ClaimCheck(check="bindings", passed=False, detail="; ".join(problems)), []

    def covered(start: int, end: int) -> bool:
        return any(s <= start and end <= e for s, e, _ in spans)

    unbound = []
    for m in term_pattern().finditer(text):
        if not covered(m.start(), m.end()):
            unbound.append(m.group(0))
    for m in re.finditer(r"\d[\d,]*(?:\.\d+)?%?", text):
        if m.group(0).replace(",", "") in _TRIVIAL_NUMBERS:
            continue
        if not covered(m.start(), m.end()):
            unbound.append(m.group(0))
    if unbound:
        return ClaimCheck(
            check="bindings",
            passed=False,
            detail="terms/numbers outside any bound clause: " + ", ".join(sorted(set(unbound))),
        ), []
    return ClaimCheck(check="bindings", passed=True, detail=f"{len(spans)} clauses bound"), [
        bd for _, _, bd in spans
    ]


def check_bullet(b: Bullet, index: EvidenceIndex, tenure_years: float) -> list[ClaimCheck]:
    checks: list[ClaimCheck] = []
    ids = [e for e in b.evidence_ids if e in index.by_id]
    unknown = [e for e in b.evidence_ids if e not in index.by_id]
    checks.append(
        ClaimCheck(
            check="evidence_exists",
            passed=bool(ids) and not unknown,
            detail=("unknown ids: " + ", ".join(unknown))
            if unknown
            else ("no evidence ids" if not ids else ""),
        )
    )
    if not ids:
        return checks
    recs = [index.by_id[i] for i in ids]

    binding_check, bindings = _bindings_check(b, index)
    checks.append(binding_check)
    if not binding_check.passed:
        return checks

    if len(ids) == 1:
        units: list[tuple[str, EvidenceRecord, str]] = [(b.text, recs[0], "")]
    else:
        units = [
            (bd.clause, index.by_id[bd.evidence_id], f"clause {bd.clause[:40]!r}: ")
            for bd in bindings
        ]

    fns = {
        "terms_supported": _terms_check,
        "numbers_supported": _numbers_check,
        "no_overstatement": _overstatement_check,
        "number_scope": _number_scope_check,
    }
    for kind, fn in fns.items():
        failures = []
        notes = []
        for text, rec, where in units:
            c = fn(text, rec, index, where)
            if not c.passed:
                failures.append(c.detail)
            elif c.detail:
                notes.append(c.detail)  # a passing guarded number still states what it measures
        checks.append(
            ClaimCheck(
                check=kind,
                passed=not failures,
                detail="; ".join(failures) if failures else "; ".join(notes),
            )
        )

    checks.append(_upgrade_check(b.text, recs))

    yrs = years_claims(b.text)
    bad_years = [y for y in yrs if y > tenure_years + 0.5]
    checks.append(
        ClaimCheck(
            check="years_plausible",
            passed=not bad_years,
            detail=(f"claims {bad_years} years; total relevant experience {tenure_years}")
            if bad_years
            else "",
        )
    )
    return checks


def _fallback_bullet(b: Bullet, index: EvidenceIndex) -> Bullet | None:
    for eid in b.evidence_ids:
        r = index.by_id.get(eid)
        if r and r.include_by_default:
            return Bullet(
                id=b.id,
                text=r.resume_text,
                evidence_ids=[eid],
                requirement_ids=b.requirement_ids,
                origin="fallback",
                status="replaced",
            )
    return None


def _validate_skills(
    groups: list[SkillGroup], index: EvidenceIndex
) -> tuple[list[SkillGroup], list[str]]:
    removed: list[str] = []
    out: list[SkillGroup] = []
    # a general capability (analytics) is vouched for by a product that delivers it; a named product only by itself
    evidenced = evidenced_terms(index.all_terms)
    for g in groups:
        keep = []
        for item in g.items:
            c = canonical(item)
            parts = find_terms(item) or [c]
            if c in evidenced or (parts and all(p in evidenced for p in parts)):
                keep.append(item)
            else:
                removed.append(item)
        if keep:
            out.append(SkillGroup(name=g.name, items=keep))
    return out, removed


def _structural_checks(res: GeneratedResume, index: EvidenceIndex) -> list[ClaimCheck]:
    checks = []
    seen = set()
    prev_start = "9999-99"
    for e in res.experience:
        p = index.positions.get(e.position_id)
        if p is None:
            checks.append(
                ClaimCheck(
                    check="position_known", passed=False, detail=f"unknown position {e.position_id}"
                )
            )
            continue
        ok = (e.company, e.title, e.start, e.end) == (p.company, p.title, p.start, p.end)
        checks.append(
            ClaimCheck(
                check="position_fields_unaltered",
                passed=ok,
                detail=""
                if ok
                else f"{e.position_id}: {e.company}/{e.title}/{e.start}/{e.end} != {p.company}/{p.title}/{p.start}/{p.end}",
            )
        )
        if e.position_id in seen:
            checks.append(ClaimCheck(check="position_unique", passed=False, detail=e.position_id))
        seen.add(e.position_id)
        checks.append(
            ClaimCheck(
                check="chronology",
                passed=e.start <= prev_start,
                detail="" if e.start <= prev_start else f"{e.position_id} out of order",
            )
        )
        prev_start = e.start
    return checks


# --------------------------------------------------------------------------
# optional LLM faithfulness judge (reject-only)
# --------------------------------------------------------------------------

JUDGE_PROMPT = """You are a strict fact-checker for resume bullets. For each item you get the generated sentence and the
evidence records it cites (claim + context). A sentence is FAITHFUL only if every factual assertion in it - scope,
ownership, seniority, outcome, scale - is stated or directly entailed by the evidence. Rephrasing and shortening are fine.
Broadening ("built a billing workflow" -> "owned company-wide payments infrastructure"), upgrading the role, adding
outcomes or implying larger scale is NOT faithful.
Return STRICT JSON: {"verdicts": [{"id": "<bullet id>", "faithful": true|false, "reason": "<short>"}]}"""


def judge_faithfulness(
    bullets: list[tuple[str, Bullet]], index: EvidenceIndex, llm: LLMProvider
) -> dict[str, tuple[bool, str]]:
    items = []
    for _, b in bullets:
        ev = [
            {
                "id": e,
                "claim": index.by_id[e].claim,
                "context": index.by_id[e].detailed_context[:400],
            }
            for e in b.evidence_ids
            if e in index.by_id
        ]
        items.append({"id": b.id, "sentence": b.text, "evidence": ev})
    resp = llm.complete_json(
        JUDGE_PROMPT, json.dumps({"items": items}, ensure_ascii=False), max_tokens=3000
    )
    out: dict[str, tuple[bool, str]] = {}
    for v in (resp.data or {}).get("verdicts", []):
        if isinstance(v, dict) and "id" in v:
            out[str(v["id"])] = (bool(v.get("faithful", True)), str(v.get("reason", "")))
    return out


# Words a tailored headline may never introduce: they assert an identity the evidence does not
# support (people management, platform administration, licensure, specializations).
FORBIDDEN_HEADLINE_TERMS = [
    "administrator",
    "admin",
    "accountant",
    "bookkeeper",
    "cpa",
    "tax preparer",
    "tax specialist",
    "tax advisor",
    "security engineer",
    "people manager",
    "head of",
    "director",
    "vp ",
    "vice president",
    "chief",
    "cto",
    "cfo",
    "data scientist",
    "machine learning engineer",
    "salesforce developer",
]


def check_headline(
    headline: str, job: JobAnalysis, fallback: str
) -> tuple[str, list[ValidationIssue]]:
    """A headline may be tailored but must not rewrite who the candidate is. "Manager" is
    allowed only when the JD's own title uses it for implementation/project ownership and the
    JD has no people-management expectations."""
    issues: list[ValidationIssue] = []
    low = headline.lower()
    hits = [t for t in FORBIDDEN_HEADLINE_TERMS if t in low]
    manager_ok = "manager" in job.role_title.lower() and not job.management_expectations
    if "manager" in low and not manager_ok:
        hits.append("manager")
    if hits:
        issues.append(
            ValidationIssue(
                severity="error",
                code="headline_identity_overstatement",
                location="headline",
                message=f"headline implies an unsupported identity ({', '.join(hits)}); replaced with profile default",
                text=headline,
            )
        )
        return fallback, issues
    return headline, issues


class ClaimValidator:
    """Career Agent integration point: ``validate(resume, index, options)``."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def validate(
        self, res: GeneratedResume, index: EvidenceIndex, options: TailorOptions | None = None
    ) -> tuple[GeneratedResume, ClaimEvidenceMap, ValidationReport]:
        options = options or TailorOptions()
        # a years claim may rest on total relevant experience (junior enterprise + internship + professional);
        # the linter separately reports which basis it relies on
        tenure = index.tenure_years("total_relevant")
        entries: list[ClaimEntry] = []
        issues: list[ValidationIssue] = []
        supported = rejected = replaced = 0
        verdicts: dict[str, tuple[bool, str]] = {}
        if self.llm is not None and self.llm.name != "none" and options.use_llm:
            try:
                verdicts = judge_faithfulness(res.all_bullets(), index, self.llm)
            except LLMError as e:
                issues.append(
                    ValidationIssue(
                        severity="info",
                        code="llm_judge_unavailable",
                        location="validation",
                        message=str(e)[:200],
                    )
                )

        def process(location: str, bullets: list[Bullet]) -> list[Bullet]:
            nonlocal supported, rejected, replaced
            kept: list[Bullet] = []
            for b in bullets:
                checks = check_bullet(b, index, tenure)
                if b.id in verdicts and not verdicts[b.id][0]:
                    checks.append(
                        ClaimCheck(check="llm_faithfulness", passed=False, detail=verdicts[b.id][1])
                    )
                ok = all(c.passed for c in checks)
                if ok:
                    b.status = "supported"
                    supported += 1
                    kept.append(b)
                    entries.append(
                        ClaimEntry(
                            claim_id=b.id,
                            location=location,
                            text=b.text,
                            evidence_ids=b.evidence_ids,
                            status="supported",
                            checks=checks,
                            bindings=b.bindings,
                        )
                    )
                    continue
                detail = "; ".join(c.detail for c in checks if not c.passed)
                issues.append(
                    ValidationIssue(
                        severity="error",
                        code="unsupported_claim",
                        location=f"{location}/{b.id}",
                        message=detail,
                        text=b.text,
                    )
                )
                fb = _fallback_bullet(b, index)
                if fb is not None:
                    fb_checks = check_bullet(fb, index, tenure)
                    if all(c.passed for c in fb_checks):
                        replaced += 1
                        kept.append(fb)
                        entries.append(
                            ClaimEntry(
                                claim_id=b.id,
                                location=location,
                                text=b.text,
                                evidence_ids=b.evidence_ids,
                                status="replaced",
                                checks=checks,
                                replacement_text=fb.text,
                                bindings=b.bindings,
                            )
                        )
                        continue
                rejected += 1
                if not options.evidence_only_claims:
                    b.status = "rejected"
                    b.text = "[UNVERIFIED] " + b.text
                    kept.append(b)
                entries.append(
                    ClaimEntry(
                        claim_id=b.id,
                        location=location,
                        text=b.text,
                        evidence_ids=b.evidence_ids,
                        status="rejected",
                        checks=checks,
                        bindings=b.bindings,
                    )
                )
            return kept

        res.summary = process("summary", res.summary)
        for e in res.experience:
            e.bullets = process(e.position_id, e.bullets)
        res.skills, removed = _validate_skills(res.skills, index)
        for s in removed:
            issues.append(
                ValidationIssue(
                    severity="warning",
                    code="unevidenced_skill_removed",
                    location="skills",
                    message=f"'{s}' is not vouched for by any evidence record",
                    text=s,
                )
            )
        structural = _structural_checks(res, index)
        for c in structural:
            if not c.passed:
                issues.append(
                    ValidationIssue(
                        severity="error", code=c.check, location="experience", message=c.detail
                    )
                )
        bad_head = [t for t in find_terms(res.headline) if t not in index.all_terms]
        if bad_head:
            issues.append(
                ValidationIssue(
                    severity="error",
                    code="headline_unsupported_terms",
                    location="headline",
                    message=", ".join(bad_head),
                    text=res.headline,
                )
            )
            res.headline = re.sub(
                "|".join(re.escape(t) for t in bad_head), "", res.headline, flags=re.IGNORECASE
            ).strip(" |,-")
        status = (
            "pass"
            if rejected == 0 and replaced == 0 and all(c.passed for c in structural)
            else (
                "pass_with_replacements"
                if rejected == 0 and all(c.passed for c in structural)
                else "fail"
            )
        )
        report = ValidationReport(
            status=status,
            total_claims=supported + rejected + replaced,
            supported=supported,
            rejected=rejected,
            replaced=replaced,
            issues=issues,
            unsupported_skills_removed=removed,
            structural_checks=structural,
        )
        return res, ClaimEvidenceMap(entries=entries), report

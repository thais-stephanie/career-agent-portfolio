# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""ATS / resume-quality linter. Entirely deterministic.

Produces findings (error / warning / info) and *internal coverage metrics*.
It deliberately does not produce an "ATS acceptance probability".
"""

from __future__ import annotations

import re
from collections import Counter

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import canonical, evidenced_terms, find_terms
from resume_tailor.core.models import (
    GeneratedResume,
    JobAnalysis,
    LintFinding,
    LintReport,
    MatchReport,
    MatchType,
    RequirementCategory,
    ResumeStrategy,
    ValidationReport,
)
from resume_tailor.core.text import first_verb, word_count, years_claims

CONVENTIONAL_HEADINGS = {"summary", "experience", "skills", "education", "certifications"}
_ACRONYM = re.compile(r"\b[A-Z]{3,6}\b")
_KNOWN_ACRONYMS = {
    "API",
    "APIs",
    "SQL",
    "DAX",
    "CRM",
    "ERP",
    "HRIS",
    "ATS",
    "REST",
    "JSON",
    "HTML",
    "CSS",
    "AWS",
    "GCP",
    "LLM",
    "LLMS",
    "RAG",
    "MRR",
    "ROI",
    "SAP",
    "USA",
    "LLC",
    "EIN",
    "ITIN",
    "UAT",
    "RACI",
    "POC",
    "POCS",
    "CMS",
    "ETL",
    "GTM",
    "UFC",
    "BBA",
    "SDK",
    "AP",
    "AR",
    "BI",
    "PDF",
    "UTC",
    "IANA",
    "CSSGB",
    "HUBL",
    "SAAS",
}


def lint(
    res: GeneratedResume,
    job: JobAnalysis,
    matches: MatchReport,
    index: EvidenceIndex,
    validation: ValidationReport,
    strategy: ResumeStrategy | None = None,
    max_pages: int = 2,
) -> LintReport:
    f: list[LintFinding] = []

    # -- headings / structure ------------------------------------------------
    for s in res.section_order:
        if s not in CONVENTIONAL_HEADINGS:
            f.append(
                LintFinding(
                    code="unconventional_heading",
                    severity="warning",
                    message=f"Section '{s}' is not a conventional ATS heading",
                    location="sections",
                )
            )
    if not res.experience:
        f.append(
            LintFinding(code="no_experience", severity="error", message="No experience entries")
        )
    prev = "9999-99"
    for e in res.experience:
        if not e.company or not e.title or not e.start:
            f.append(
                LintFinding(
                    code="entry_parse",
                    severity="error",
                    message="Entry missing company/title/start",
                    location=e.position_id,
                )
            )
        if e.start > prev:
            f.append(
                LintFinding(
                    code="chronology",
                    severity="warning",
                    message=f"{e.company} appears out of reverse-chronological order",
                    location=e.position_id,
                )
            )
        prev = e.start
        if not e.bullets:
            f.append(
                LintFinding(
                    code="empty_entry",
                    severity="warning",
                    message=f"{e.company} has no bullets",
                    location=e.position_id,
                )
            )

    # -- length ---------------------------------------------------------------
    if res.estimated_pages > max_pages:
        f.append(
            LintFinding(
                code="page_length",
                severity="error",
                message=f"Estimated {res.estimated_pages} pages > target {max_pages}",
                location="document",
            )
        )
    elif res.estimated_pages > max_pages - 0.15:
        f.append(
            LintFinding(
                code="page_length_tight",
                severity="info",
                message=f"Estimated {res.estimated_pages} pages; close to the {max_pages}-page target",
                location="document",
            )
        )
    if res.estimated_pages < 0.8:
        f.append(
            LintFinding(
                code="page_length_short",
                severity="warning",
                message=f"Estimated {res.estimated_pages} pages; likely too thin",
                location="document",
            )
        )

    # -- bullets ------------------------------------------------------------------
    all_bullets = [(loc, b) for loc, b in res.all_bullets() if loc != "summary"]
    verbs = Counter(first_verb(b.text) for _, b in all_bullets)
    for v, c in verbs.items():
        if c >= 4:
            f.append(
                LintFinding(
                    code="repetitive_opening",
                    severity="warning",
                    message=f"{c} bullets start with '{v}'",
                    location="experience",
                )
            )
    for loc, b in all_bullets:
        wc = word_count(b.text)
        if wc > 42:
            f.append(
                LintFinding(
                    code="long_bullet",
                    severity="warning",
                    message=f"{wc} words",
                    location=f"{loc}/{b.id}",
                )
            )
        if re.match(r"^(I|We|My)\b", b.text):
            f.append(
                LintFinding(
                    code="first_person",
                    severity="warning",
                    message="First-person bullet",
                    location=f"{loc}/{b.id}",
                )
            )
        for a in _ACRONYM.findall(b.text):
            if a not in _KNOWN_ACRONYMS and a.upper() not in _KNOWN_ACRONYMS:
                f.append(
                    LintFinding(
                        code="unexplained_acronym",
                        severity="info",
                        message=f"Acronym '{a}' may need expanding",
                        location=f"{loc}/{b.id}",
                    )
                )
    for e in res.experience:
        if len(e.bullets) > 8:
            f.append(
                LintFinding(
                    code="bullet_density",
                    severity="warning",
                    message=f"{len(e.bullets)} bullets under {e.company}",
                    location=e.position_id,
                )
            )

    # -- keyword coverage vs JD ------------------------------------------------
    resume_text = " ".join(
        [
            res.headline,
            *[b.text for _, b in res.all_bullets()],
            *[i for g in res.skills for i in g.items],
        ]
    )
    resume_terms = set(find_terms(resume_text)) | {
        canonical(i) for g in res.skills for i in g.items
    }
    # languages, certifications and education are bank facts, not generated claims: they count
    # toward JD coverage (an ATS reads them) but are not checked as prose
    peripheral = " ".join(
        [
            *res.candidate.languages,
            *[f"{c.issuer} {c.name}" for c in res.certifications],
            *[f"{e.degree} {e.institution}" for e in res.education],
        ]
    )
    covered_terms = resume_terms | set(find_terms(peripheral))
    jd_terms = set(
        find_terms(
            " ".join(
                [
                    *job.must_have,
                    *job.nice_to_have,
                    *job.responsibilities,
                    *job.technologies,
                    *job.keywords,
                ]
            )
        )
    )
    evidenced = evidenced_terms(index.all_terms)
    supported_jd = {t for t in jd_terms if t in evidenced}
    missing_supported = sorted(t for t in supported_jd if t not in covered_terms)
    for t in missing_supported:
        f.append(
            LintFinding(
                code="missing_supported_keyword",
                severity="warning",
                message=f"JD term '{t}' is evidenced but absent from the resume",
                location="keywords",
            )
        )
    unsupported_present = sorted(t for t in resume_terms if t not in evidenced)
    for t in unsupported_present:
        f.append(
            LintFinding(
                code="unsupported_keyword",
                severity="error",
                message=f"'{t}' appears in the resume but no evidence record vouches for it",
                location="keywords",
            )
        )
    unsupported_jd = sorted(t for t in jd_terms if t not in evidenced)
    if unsupported_jd:
        f.append(
            LintFinding(
                code="jd_terms_without_evidence",
                severity="info",
                message="JD terms with no evidence (gaps, correctly absent): "
                + ", ".join(unsupported_jd),
                location="keywords",
            )
        )

    # -- stuffing / repetition ------------------------------------------------
    term_counts = Counter(t for _, b in res.all_bullets() for t in find_terms(b.text))
    for t, c in term_counts.items():
        if c >= 7:
            f.append(
                LintFinding(
                    code="keyword_stuffing",
                    severity="warning",
                    message=f"'{t}' appears in {c} bullets",
                    location="keywords",
                )
            )
    skill_items = [canonical(i) for g in res.skills for i in g.items]
    dup = [t for t, c in Counter(skill_items).items() if c > 1]
    if dup:
        f.append(
            LintFinding(
                code="duplicate_skills",
                severity="info",
                message="Duplicated skills: " + ", ".join(dup),
                location="skills",
            )
        )

    # -- skills vs experience consistency -----------------------------------------
    exp_terms = evidenced_terms(
        set(find_terms(" ".join(b.text for loc, b in res.all_bullets())))
    )  # a bullet naming Power BI shows analytics work
    listed_not_shown = sorted(
        t for t in set(skill_items) if t not in exp_terms and t in evidenced and len(t) > 2
    )
    if listed_not_shown:
        f.append(
            LintFinding(
                code="skill_not_evidenced_in_body",
                severity="info",
                message="Listed in Skills but not shown in any bullet: "
                + ", ".join(listed_not_shown[:15]),
                location="skills",
            )
        )
    shown_not_listed = sorted(t for t in exp_terms & jd_terms if t not in set(skill_items))
    if shown_not_listed:
        f.append(
            LintFinding(
                code="evidence_missing_from_skills",
                severity="info",
                message="JD terms used in bullets but missing from Skills: "
                + ", ".join(shown_not_listed[:15]),
                location="skills",
            )
        )

    # -- years / hard filters -------------------------------------------------
    t = index.tenure_summary()  # total_relevant / employment / professional
    for y in years_claims(resume_text):
        if y > t["total_relevant"] + 0.5:
            f.append(
                LintFinding(
                    code="years_mismatch",
                    severity="error",
                    message=f"Resume claims {y}+ years; total relevant experience is {t['total_relevant']}",
                    location="document",
                )
            )
        elif y > t["professional"] + 0.5:
            basis = (
                "employment incl. internship"
                if y <= t["employment"] + 0.5
                else "total relevant experience incl. junior-enterprise work (Feb 2018)"
            )
            f.append(
                LintFinding(
                    code="years_claim_basis",
                    severity="info",
                    message=f"'{y}+ years' rests on {basis}: total relevant {t['total_relevant']}y, employment {t['employment']}y, post-internship professional {t['professional']}y",
                    location="document",
                )
            )
    tenure = t["professional"]
    if job.years_required and job.years_required > tenure:
        f.append(
            LintFinding(
                code="years_required_gap",
                severity="warning",
                message=f"JD asks for {job.years_required}+ years; computed tenure is {tenure}",
                location="hard_filters",
            )
        )

    # -- role-scope observations (conditions, never gaps) ----------------------------
    if strategy:
        for note in strategy.fit_notes:
            f.append(
                LintFinding(
                    code="role_scope_observation",
                    severity="info",
                    message=note[:300],
                    location="fit",
                )
            )

    # -- validation passthrough ------------------------------------------------
    for i in validation.issues:
        if i.code == "unsupported_claim":
            f.append(
                LintFinding(
                    code="unsupported_claim",
                    severity="error",
                    message=i.message,
                    location=i.location,
                )
            )

    # -- metrics preserved ---------------------------------------------------------
    dropped_metrics = 0
    if strategy:
        featured = set(strategy.evidence_to_feature[:8])
        used_ids = {e for _, b in res.all_bullets() for e in b.evidence_ids}
        for eid in featured:
            r = index.by_id.get(eid)
            if r and r.metrics and eid not in used_ids:
                dropped_metrics += 1
        if dropped_metrics:
            f.append(
                LintFinding(
                    code="metrics_dropped",
                    severity="info",
                    message=f"{dropped_metrics} high-relevance record(s) with metrics not used in any bullet",
                    location="experience",
                )
            )

    # -- metrics (internal, not probabilities) -----------------------------------
    cov = matches.coverage
    must = [m for m in matches.matches if m.category == RequirementCategory.MUST_HAVE]
    nice = [m for m in matches.matches if m.category == RequirementCategory.NICE_TO_HAVE]
    under = 0
    if strategy:
        rel_ids = set(strategy.evidence_to_feature[:10])
        used_ids = {e for _, b in res.all_bullets() for e in b.evidence_ids}
        under = len(rel_ids - used_ids)
    metrics: dict[str, float | int] = {
        "must_have_evidence_coverage": cov.get("must_have_evidence_coverage", 0.0),
        "must_have_direct_coverage": cov.get("must_have_direct_coverage", 0.0),
        "nice_to_have_evidence_coverage": cov.get("nice_to_have_evidence_coverage", 0.0),
        "supported_keyword_coverage": round(len(supported_jd & resume_terms) / len(supported_jd), 3)
        if supported_jd
        else 1.0,
        "jd_terms_total": len(jd_terms),
        "jd_terms_evidenced": len(supported_jd),
        "jd_terms_in_resume": len(jd_terms & resume_terms),
        "unsupported_claims_count": validation.rejected + validation.replaced,
        "unsupported_keywords_in_resume": len(unsupported_present),
        "underrepresented_evidence_count": under,
        "must_have_gaps": sum(1 for m in must if m.match_type == MatchType.UNSUPPORTED),
        "nice_to_have_gaps": sum(1 for m in nice if m.match_type == MatchType.UNSUPPORTED),
        "estimated_pages": res.estimated_pages,
        "estimated_words": res.estimated_words,
        "errors": sum(1 for x in f if x.severity == "error"),
        "warnings": sum(1 for x in f if x.severity == "warning"),
    }
    order = {"error": 0, "warning": 1, "info": 2}
    f.sort(key=lambda x: (order[x.severity], x.code))
    return LintReport(findings=f, metrics=metrics)


class AtsLinter:
    """Career Agent integration point."""

    def lint(self, *args, **kwargs) -> LintReport:  # type: ignore[no-untyped-def]
        return lint(*args, **kwargs)

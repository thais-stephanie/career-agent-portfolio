# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""The end-to-end Analyze & Tailor pipeline.

``TailorService.run(request)`` is the single entry point Career Agent would
call. It owns no I/O beyond what the injected ``RunStore`` does; every stage
is a separate service with its own interface so they can also be called
individually (e.g. analyze a JD without generating anything).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from resume_tailor.core.ats_lint.linter import lint
from resume_tailor.core.diff.differ import diff
from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.job_analysis.analyzer import JobAnalysisService
from resume_tailor.core.matching.matcher import EvidenceMatcher
from resume_tailor.core.models import (
    BaseResume,
    GeneratedResume,
    PageMeasurement,
    TailorRequest,
    TailorRun,
)
from resume_tailor.core.resume_generation.generator import (
    PAGE_ESTIMATE_BUFFER,
    ResumeGenerator,
    estimate_length,
)
from resume_tailor.core.resume_strategy.planner import StrategyService
from resume_tailor.core.validation.validator import ClaimValidator, check_headline
from resume_tailor.providers.llm.base import LLMProvider

ProgressFn = Callable[[str], None]


def trim_to_pages(
    res: GeneratedResume,
    max_pages: float,
    relevance: dict[str, float],
    protected: dict[str, int] | None = None,
    never_trim: set[str] | None = None,
    must_have_cover: dict[str, set[str]] | None = None,
    cert_scores: dict[str, int] | None = None,
    lead_position: str | None = None,
) -> tuple[GeneratedResume, list[str]]:
    """Drop the lowest-value bullets until the estimate fits.

    Never trimmed: hero evidence and position openers (``never_trim``), the first bullet of any
    position, and a bullet that is the only remaining evidence for a must-have requirement.
    Among the rest the lowest value goes first, and older experience is compressed before core
    recent evidence: a bullet of the most relevant position (``lead_position``) is a candidate
    only once every other position is at its floor. Positions keep at least ``protected``
    bullets. When no bullet can go safely, a certification line the JD does not ask for
    (``cert_scores`` at most 1: not a JD term, at best an automation-family credential) is
    dropped; after that the resume is left as it is."""
    notes: list[str] = []
    protected = protected or {}
    never_trim = never_trim or set()
    must_have_cover = must_have_cover or {}
    cert_scores = cert_scores or {}
    guard = 0
    while res.estimated_pages > max_pages and guard < 40:
        guard += 1
        # requirement -> bullets currently covering it
        covering: dict[str, int] = {}
        for e in res.experience:
            for b in e.bullets:
                for eid in b.evidence_ids:
                    for rq in must_have_cover.get(eid, ()):
                        covering[rq] = covering.get(rq, 0) + 1
        candidates = []
        for e in res.experience:
            if len(e.bullets) <= max(1, protected.get(e.position_id, 1)):
                continue
            for b in e.bullets[1:]:  # the first bullet of a position is its opener
                if any(x in never_trim for x in b.evidence_ids):
                    continue
                if any(
                    covering.get(rq, 0) <= 1
                    for x in b.evidence_ids
                    for rq in must_have_cover.get(x, ())
                ):
                    continue  # sole remaining proof of a must-have
                score = sum(relevance.get(x, 0.0) for x in b.evidence_ids)
                candidates.append((score, e, b))
        if lead_position and any(e.position_id != lead_position for _, e, _ in candidates):
            candidates = [c for c in candidates if c[1].position_id != lead_position]
        if not candidates:
            issuers = sorted(
                (s, issuer)
                for issuer, s in cert_scores.items()
                if s <= 1 and any(c.issuer == issuer for c in res.certifications)
            )
            if not issuers:
                break
            score, issuer = issuers[0]
            res.certifications = [c for c in res.certifications if c.issuer != issuer]
            notes.append(
                f"trimmed certifications/{issuer} (relevance {score}, not asked for by the JD) to meet {max_pages}-page target"
            )
            estimate_length(res)
            continue
        candidates.sort(key=lambda t: t[0])
        _, e, b = candidates[0]
        e.bullets.remove(b)
        notes.append(f"trimmed {e.position_id}/{b.id} to meet {max_pages}-page target")
        estimate_length(res)
    return res, notes


MAX_PAGE_PASSES = 3
PAGE_LIMIT_NOT_MET = "PAGE LIMIT NOT MET SAFELY"
_TIGHTEN_STEPS = (
    0.05,
    0.10,
)  # of a page below the current estimate per failed verification: ~2-3 lines, then ~5


def enforce_page_limit(
    res: GeneratedResume,
    limit: int,
    trim: Callable[[GeneratedResume, float], tuple[GeneratedResume, list[str]]],
    measure: Callable[[GeneratedResume], tuple[int, str] | None] | None = None,
) -> tuple[GeneratedResume, PageMeasurement]:
    """GENERATE -> RENDER/MEASURE -> TRIM -> REGENERATE -> VERIFY, bounded to ``MAX_PAGE_PASSES``.

    ``measure`` returns the DOCX's real page count and its source ("word" / "libreoffice") or None
    when no renderer exists. With a renderer the resume is trimmed to the estimate first, rendered,
    and re-trimmed a little tighter each time the real count exceeds the limit; every trim goes
    through the same evidence-aware ``trim`` (heroes, openers and sole must-have proof are never
    removed). Without a renderer the estimate is the only signal: it is labelled as such and the
    target carries ``PAGE_ESTIMATE_BUFFER`` of headroom. When the bounded loop cannot fit the
    document safely the result says ``PAGE LIMIT NOT MET SAFELY`` instead of cutting deeper."""
    trimmed: list[str] = []
    if measure is None:
        target = limit - PAGE_ESTIMATE_BUFFER
        res, notes = trim(res, target)
        trimmed += notes
        met = res.estimated_pages <= target
        note = f"no DOCX renderer available: {res.estimated_pages} pages is an estimate, target {target:.2f} (limit {limit} minus {PAGE_ESTIMATE_BUFFER} headroom)"
        return res, PageMeasurement(
            limit=limit,
            estimated_pages=res.estimated_pages,
            source="estimator",
            passes=0,
            trimmed=trimmed,
            limit_met=met,
            note=note
            if met
            else f"{PAGE_LIMIT_NOT_MET}: {note}; nothing else can be trimmed without removing protected evidence",
        )
    res, notes = trim(res, float(limit))
    trimmed += notes
    actual, source = None, "estimator"
    for n in range(1, MAX_PAGE_PASSES + 1):
        m = measure(res)
        if m is None:  # the renderer failed mid-run: fall back to the labelled estimate
            return enforce_page_limit(res, limit, trim, None)
        actual, source = m
        if actual <= limit:
            return res, PageMeasurement(
                limit=limit,
                estimated_pages=res.estimated_pages,
                actual_pages=actual,
                source=source,
                passes=n,
                trimmed=trimmed,
                limit_met=True,
                note=f"verified: {actual} page(s) rendered by {source}"
                + (f" after {len(trimmed)} trim(s)" if trimmed else ""),
            )
        if n == MAX_PAGE_PASSES:
            break
        target = round(
            min(float(limit), res.estimated_pages)
            - _TIGHTEN_STEPS[min(n, len(_TIGHTEN_STEPS)) - 1],
            2,
        )
        res, notes = trim(res, target)
        trimmed += notes
        if not notes:
            break  # nothing more can be removed safely
    return res, PageMeasurement(
        limit=limit,
        estimated_pages=res.estimated_pages,
        actual_pages=actual,
        source=source,
        passes=min(n, MAX_PAGE_PASSES),
        trimmed=trimmed,
        limit_met=False,
        note=f"{PAGE_LIMIT_NOT_MET}: {actual} pages rendered by {source} after bounded trimming; remaining content is protected evidence",
    )


def measure_docx_pages(res: GeneratedResume) -> tuple[int, str] | None:
    """Real page count of the DOCX export, or None when no renderer is installed."""
    from resume_tailor.export.exporters import EXPORTERS
    from resume_tailor.export.pagination import count_pages

    pc = count_pages(EXPORTERS["docx"].render(res))
    return (pc.pages, pc.source) if pc else None


class TailorService:
    def __init__(
        self,
        index: EvidenceIndex,
        resumes: dict[str, BaseResume],
        profiles: dict[str, Any],
        llm: LLMProvider | None = None,
    ):
        self.index = index
        self.resumes = resumes
        self.profiles = profiles
        self.llm = llm
        self.analyzer = JobAnalysisService(llm)
        self.matcher = EvidenceMatcher(llm)
        self.strategist = StrategyService(llm)
        self.generator = ResumeGenerator(llm)
        self.validator = ClaimValidator(llm)

    def run(self, req: TailorRequest, run_id: str, progress: ProgressFn | None = None) -> TailorRun:
        say = progress or (lambda _s: None)
        warnings: list[str] = []
        base = self.resumes[req.resume_id]
        llm = self.llm if req.options.use_llm else None
        if not req.options.use_llm:
            self.analyzer.llm = self.matcher.llm = self.strategist.llm = self.generator.llm = (
                self.validator.llm
            ) = None
        else:
            self.analyzer.llm = self.matcher.llm = self.strategist.llm = self.generator.llm = (
                self.validator.llm
            ) = self.llm

        say("analyzing job description")
        job, w = self.analyzer.analyze(req.jd_text, self.index.all_terms)
        warnings += w
        say("matching evidence")
        matches, w = self.matcher.match(job, self.index)
        warnings += w
        say("planning strategy")
        max_pages = 2 if req.options.max_two_pages else 3
        strategy, w = self.strategist.plan(
            job, matches, self.index, base, self.profiles, req.target_profile, max_pages
        )
        warnings += w
        say("generating resume")
        pconf = self.profiles.get(strategy.target_profile, {})
        titles = pconf.get("titles", [])
        resume, w = self.generator.generate(
            job, matches, strategy, self.index, base, req.options, titles, pconf.get("descriptor")
        )
        warnings += w
        say("validating claims")
        resume, claim_map, validation = self.validator.validate(resume, self.index, req.options)
        safe_headline, head_issues = check_headline(
            resume.headline, job, titles[0] if titles else base.headline
        )
        if head_issues:
            resume.headline = safe_headline
            validation.issues.extend(head_issues)
        # the portfolio link goes on the contact line for profiles that ask for it (solutions,
        # implementation, systems, integration, AI, business-systems roles); it costs one line and
        # is placed before trimming, so the page budget is settled with it in place
        if self.index.bank.candidate.portfolio and pconf.get("portfolio", True):
            resume.candidate.portfolio = self.index.bank.candidate.portfolio
            estimate_length(resume)
        elif self.index.bank.candidate.portfolio:
            warnings.append("portfolio link omitted: profile does not list it")
        if req.options.max_two_pages:
            from resume_tailor.core.matching.matcher import evidence_relevance

            # the most relevant position keeps at least min(3, budget) bullets, the next two at least
            # min(2, budget): older experience is compressed before core recent evidence
            ranked = sorted(strategy.position_plans, key=lambda p: -p.relevance)[:3]
            protected = {
                p.position_id: min(3 if i == 0 else 2, p.max_bullets) for i, p in enumerate(ranked)
            }
            lead_position = ranked[0].position_id if ranked else None
            value = {rid: v["value"] for rid, v in strategy.evidence_value.items()}
            never = set(strategy.hero_evidence_ids)
            must_cover: dict[str, set[str]] = {}
            for m in matches.matches:
                if m.category.value == "must_have" and m.match_type.value == "direct":
                    for e in m.evidence[:3]:
                        must_cover.setdefault(e.evidence_id, set()).add(m.requirement_id)
            relevance = value or evidence_relevance(matches, self.index)
            from resume_tailor.core.lexicon import find_terms
            from resume_tailor.core.resume_generation.certifications import relevance_score

            body_terms = set(find_terms(" ".join(b.text for _, b in resume.all_bullets())))
            cert_scores: dict[str, int] = {}
            for c in resume.certifications:
                cert_scores[c.issuer] = max(
                    cert_scores.get(c.issuer, -9), relevance_score(c, job, body_terms)
                )

            def trim(r, target):
                return trim_to_pages(
                    r, target, relevance, protected, never, must_cover, cert_scores, lead_position
                )

            resume, notes = trim(resume, 2.0)
            warnings += notes
        page_measurement = None
        if req.options.max_two_pages:
            say("verifying page count")
            from resume_tailor.export.pagination import available_renderer

            measure = measure_docx_pages if available_renderer() else None
            resume, page_measurement = enforce_page_limit(resume, 2, trim, measure)
            warnings += page_measurement.trimmed
            if not page_measurement.limit_met:
                warnings.append(page_measurement.note)
        say("linting")
        lint_report = lint(resume, job, matches, self.index, validation, strategy, max_pages)
        say("diffing")
        diff_report = diff(base, resume, job, strategy)
        info = llm.info() if llm else None
        provider = {
            "provider": info.provider if info else "none",
            "model": info.model if info else "deterministic",
            "available": str(info.available if info else False),
        }
        return TailorRun(
            run_id=run_id,
            request=req,
            provider=provider,
            job_analysis=job,
            evidence_matches=matches,
            resume_strategy=strategy,
            generated_resume=resume,
            claim_evidence_map=claim_map,
            validation_report=validation,
            lint_report=lint_report,
            diff_report=diff_report,
            page_measurement=page_measurement,
            warnings=warnings,
        )

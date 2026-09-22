# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Resume Strategy: decide positioning, space allocation and emphasis before
any prose is written.

Deterministic core:
* infer the target profile from JD terms (user override wins);
* relevance per evidence record = weighted requirement support (from matching);
* relevance per position = sum of its records' relevance, recency-boosted;
* bullet budget per position from relevance rank and target length;
* terms_to_introduce = JD terms that are evidenced but absent from the base
  resume; underrepresented_skills = evidenced JD terms missing from base skills.

The LLM (optional) only writes ``positioning`` and ``rationale`` prose and may
suggest a title from the allowed list; it cannot change the numbers.
"""

from __future__ import annotations

import json
import re
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import canonical, find_terms
from resume_tailor.core.matching.matcher import evidence_relevance
from resume_tailor.core.models import (
    BaseResume,
    JobAnalysis,
    MatchReport,
    MatchType,
    PositionKind,
    PositionPlan,
    Proficiency,
    ResumeStrategy,
)
from resume_tailor.core.resume_strategy.value import pick_heroes, redundant, score_records
from resume_tailor.core.text import months_between, similarity
from resume_tailor.providers.llm.base import LLMError, LLMProvider

SYSTEM_PROMPT = """You write the positioning rationale for a truthful, evidence-grounded resume.
You receive the parsed job, the requirement-to-evidence matches (including explicit gaps),
the deterministic space plan, and the allowed headline options.
Return STRICT JSON: {"recommended_title": "<one of the allowed titles>", "positioning": "<2-3 sentences on how to position the candidate truthfully for this job>", "rationale": "<3-6 sentences explaining what to emphasize, what to compress and why; name the gaps plainly>", "emphasized_projects": ["project names from the evidence"]}
Do not claim any skill, tool, or experience that is not in the evidence. Name gaps rather than hiding them."""


def infer_profile(job: JobAnalysis, profiles: dict[str, Any]) -> tuple[str, dict[str, float]]:
    """Profile = the family the job is mostly about. Three signals, each bounded:
    * vocabulary: which of the profile's cues/terms appear anywhere in the JD (2 / 1 / 1.5 each);
    * the role title: at most one hit per profile (4), +2 when the cue leads the title;
    * distribution: the share of must-have and responsibility lines the family touches (0-6),
      so a family named in one line with many synonyms does not outscore one that runs
      through half the requirements."""
    blob = " ".join(
        [
            job.role_title,
            *job.must_have,
            *job.nice_to_have,
            *job.responsibilities,
            *job.technologies,
            *job.keywords,
            *job.domain,
        ]
    ).lower()
    terms = set(find_terms(blob))
    lines = [*job.must_have, *job.responsibilities]
    title = job.role_title.lower()
    scores: dict[str, float] = {}
    for pid, p in profiles.items():
        cues = [c.lower() for c in p.get("cues", [])]
        pterms = {canonical(t) for t in p.get("terms", [])}
        s = 0.0
        for c in cues:
            if c in blob:
                s += 2.0 if len(c) > 6 else 1.0
        s += 1.5 * len(pterms & terms)
        title_hits = [c.lower() for c in p.get("title_cues", []) if c.lower() in title]
        if title_hits:
            s += 4.0 + (2.0 if min(title.find(c) for c in title_hits) <= 2 else 0.0)
        touched = sum(
            1
            for text in lines
            if any(c in text.lower() for c in cues) or (pterms & set(find_terms(text.lower())))
        )
        s += 6.0 * touched / max(1, len(lines))
        scores[pid] = round(s, 2)
    best = max(scores, key=lambda k: (scores[k], k)) if scores else next(iter(profiles))
    if scores and scores[best] == 0:
        best = "business_systems" if "business_systems" in profiles else best
    return best, scores


def plan(
    job: JobAnalysis,
    matches: MatchReport,
    index: EvidenceIndex,
    base: BaseResume,
    profiles: dict[str, Any],
    profile_override: str | None,
    max_pages: int = 2,
) -> ResumeStrategy:
    inferred, scores = infer_profile(job, profiles)
    profile = (
        profile_override
        if profile_override and profile_override in profiles and profile_override != "auto"
        else inferred
    )
    pconf = profiles[profile]
    rel = evidence_relevance(matches, index)
    vscores = score_records(matches, rel, index)
    heroes = pick_heroes(vscores, index)
    value = {rid: v.value for rid, v in vscores.items()}
    # records that contribute a literal hit to at least one requirement judged DIRECT (alone or jointly)
    direct_support: set[str] = set()
    for m in matches.matches:
        if m.match_type != MatchType.DIRECT:
            continue
        for e in m.evidence:
            if e.reason.startswith("literal"):
                direct_support.add(e.evidence_id)

    # position relevance with recency boost; only default positions
    pos_scores: dict[str, float] = {}
    for p in index.bank.positions:
        if not p.include_by_default:
            continue
        recs = index.by_position.get(p.id, [])
        # side projects / junior work are rendered only when they are direct evidence for something asked
        if p.kind in (PositionKind.INDEPENDENT, PositionKind.JUNIOR_ENTERPRISE) and not any(
            r.id in direct_support for r in recs
        ):
            continue
        # top-k so a position with many records does not outrank one with a few strong ones
        top_rel = sorted((value.get(r.id, 0.0) for r in recs), reverse=True)[:5]
        s = sum(top_rel)
        # profile affinity: records whose domains/technologies hit the profile terms (top-k as well)
        pterms = {canonical(t) for t in pconf.get("terms", [])}
        s += min(5, sum(1 for r in recs if pterms & index.record_terms(r))) * 0.5
        age_months = months_between(p.end or "2100-01", None) if p.end else 0
        recency = 1.0 if age_months < 24 else (0.75 if age_months < 60 else 0.5)
        pos_scores[p.id] = round(s * recency + (0.2 if p.end is None else 0.0), 2)

    ordered = sorted(
        pos_scores, key=lambda k: (-pos_scores[k], index.positions[k].start), reverse=False
    )
    # bullet budgets: top gets most; total roughly bounded by pages
    budget_total = 20 if max_pages >= 2 else 11
    plans: list[PositionPlan] = []
    n = len(ordered)
    for rank, pid in enumerate(ordered):
        share = max(1, round(budget_total * (0.5**rank) / (2 - 0.5 ** (n - 1)))) if n else 0
        recs = index.by_position.get(pid, [])
        share = min(share, 7, max(1, len(recs)))
        if rank >= 4:
            share = min(share, 2)
        featured = sorted(recs, key=lambda r: -value.get(r.id, 0.0))
        featured_ids: list[str] = []
        chosen_scores = []
        # 1. a role-scope record (project is None) that is led, carries metrics and directly supports a
        #    requirement opens the position: it tells the reader what level of problems she owned
        opener = next(
            (
                r
                for r in sorted(recs, key=lambda r: -value.get(r.id, 0.0))
                if r.project is None
                and r.id in direct_support
                and r.metrics
                and index.effective_proficiency(r) == Proficiency.LED
                and not index.secondary_only(r)
            ),
            None,
        )
        if opener is not None and rank == 0:
            featured_ids.append(opener.id)
            chosen_scores.append(vscores[opener.id])
        # 2. heroes belonging to this position, 3. everything else by value, skipping redundant proofs
        by_value = [
            index.by_id[h]
            for h in heroes
            if index.by_id[h].position_id == pid and h not in featured_ids
        ] + sorted(
            (r for r in recs if r.id not in heroes and r.id not in featured_ids),
            key=lambda r: -value.get(r.id, 0.0),
        )
        for r in by_value:
            if value.get(r.id, 0.0) <= 0 or len(featured_ids) >= share:
                break
            if any(
                similarity(r.resume_text, index.by_id[f].resume_text) > 0.55 for f in featured_ids
            ):
                continue
            if (
                r.id not in heroes
                and r.id in vscores
                and redundant(vscores[r.id], chosen_scores, index)
            ):
                continue
            featured_ids.append(r.id)
            if r.id in vscores:
                chosen_scores.append(vscores[r.id])
        # fill with high-confidence primary records if the job matched little
        for r in featured:
            if len(featured_ids) >= share:
                break
            if r.id not in featured_ids and r.verification.value == "primary":
                featured_ids.append(r.id)
        if not featured_ids and recs:  # never leave a position without a single bullet
            featured_ids.append(max(recs, key=lambda r: r.confidence).id)
        note = (
            "lead with this experience"
            if rank == 0
            else ("supporting experience" if rank < 3 else "compress: 1-2 bullets")
        )
        plans.append(
            PositionPlan(
                position_id=pid,
                relevance=pos_scores[pid],
                max_bullets=share,
                featured_evidence_ids=featured_ids,
                note=note,
            )
        )

    # chronological order for the resume body; relevance drives bullet count
    plans.sort(
        key=lambda pp: (
            index.positions[pp.position_id].end is None,
            index.positions[pp.position_id].end or "9999-99",
            index.positions[pp.position_id].start,
        ),
        reverse=True,
    )

    # terms present in the JD and evidenced, but missing from the base resume
    base_text = " ".join(
        [
            base.headline,
            *[b.text for b in base.summary],
            *[bl.text for p in base.positions for bl in p.bullets],
            *[i for g in base.skills for i in g.items],
        ]
    )
    base_terms = set(find_terms(base_text)) | {canonical(i) for g in base.skills for i in g.items}
    evidenced = index.all_terms
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
    intro = []
    for t in sorted(jd_terms):
        if t in evidenced and t not in base_terms:
            ids = sorted(index.term_index.get(t, set()))[:4]
            intro.append({"term": t, "evidence_ids": ids})
    base_skill_terms = {canonical(i) for g in base.skills for i in g.items}
    under = sorted(t for t in jd_terms & evidenced if t not in base_skill_terms)

    # irrelevant content: base bullets whose evidence has zero relevance
    irrelevant = []
    for bp in base.positions:
        for bl in bp.bullets:
            if bl.evidence_ids and all(rel.get(e, 0.0) == 0.0 for e in bl.evidence_ids):
                irrelevant.append(f"{bp.position_id}: {bl.text[:90]}")
    reduce = [
        f"{pp.position_id} -> {pp.max_bullets} bullet(s)" for pp in plans if pp.max_bullets <= 2
    ]

    gaps = [m.requirement_text for m in matches.matches if m.match_type == MatchType.UNSUPPORTED]
    top_evidence = (
        heroes
        + [eid for eid, _ in sorted(value.items(), key=lambda kv: -kv[1]) if eid not in heroes][
            : 12 - len(heroes)
        ]
    )
    projects = []
    for eid in top_evidence:
        pr = index.by_id[eid].project
        if pr and pr not in projects:
            projects.append(pr)
    titles = pconf.get("titles", [])
    title = _closest_title(titles, job.role_title) if titles else base.headline
    fit_notes = _fit_notes(job, index)
    positioning = f"Position as {title}: lead with {index.positions[ordered[0]].company if ordered else 'the most relevant role'} evidence that answers the must-haves; keep unsupported requirements ({len(gaps)}) visible as gaps rather than papering over them."
    return ResumeStrategy(
        target_profile=profile,
        profile_inferred=(
            profile == inferred and not (profile_override and profile_override != "auto")
        ),
        profile_scores=scores,
        recommended_title=title,
        positioning=positioning,
        position_plans=plans,
        emphasized_projects=projects[:6],
        evidence_to_feature=top_evidence,
        content_to_reduce=reduce,
        terms_to_introduce=intro,
        underrepresented_skills=under,
        irrelevant_content=irrelevant[:12],
        section_order=pconf.get(
            "section_order", ["summary", "experience", "skills", "education", "certifications"]
        ),
        target_length_pages=max_pages,
        rationale="Deterministic plan: bullet budgets follow weighted requirement coverage per position; gaps: "
        + ("; ".join(gaps[:5]) if gaps else "none"),
        fit_notes=fit_notes,
        hero_evidence_ids=heroes,
        evidence_value={rid: v.as_dict() for rid, v in vscores.items()},
        strategy_source="deterministic",
    )


_NOCODE = re.compile(
    r"(everything is no-code|no-code only|no code only|zero code|writes zero code|no code required|non-coding role)",
    re.IGNORECASE,
)
_LOCATION = re.compile(
    r"\b(on-site|onsite|in-office|hybrid|relocat\w*|latin america|time zones?|business hours|working hours|overlap)\b",
    re.IGNORECASE,
)


def _closest_title(titles: list[str], role_title: str) -> str:
    """Among the profile's allowed titles, the one sharing the most words with the JD's title;
    the profile's first title on a tie. Every candidate is already an allowed title, so the
    headline-safety check applies unchanged."""
    words = {w for w in re.findall(r"[a-z]+", role_title.lower()) if len(w) > 1}
    if not words:
        return titles[0]
    return max(
        titles, key=lambda t: (len(words & set(re.findall(r"[a-z]+", t.lower()))), -titles.index(t))
    )


def _fit_notes(job: JobAnalysis, index: EvidenceIndex) -> list[str]:
    """Role-scope / preference observations. These are conditions of the role, not skill gaps,
    so they are reported separately and never counted in coverage."""
    notes: list[str] = []
    nocode = [o for o in job.role_scope_observations if _NOCODE.search(o)]
    location = [o for o in job.role_scope_observations if o not in nocode and _LOCATION.search(o)]
    other = [o for o in job.role_scope_observations if o not in nocode and o not in location]
    if nocode:
        nocode_ids = sorted(
            {
                i
                for t in ("zapier", "workato", "n8n", "no-code automation")
                for i in index.term_index.get(t, set())
            }
        )[:6]
        eng = sorted(
            t
            for t in ("python", "javascript", "typescript", "sql", "rest apis")
            if t in index.all_terms
        )
        quotes = "; ".join(f'"{o[:110]}"' for o in nocode[:3])
        notes.append(
            "ROLE SCOPE / PREFERENCE MISMATCH (not a skill gap): the role is no-code only - "
            f"{quotes}. The candidate has no-code/low-code automation evidence ({', '.join(nocode_ids)}) "
            f"and an engineering-heavy profile ({', '.join(eng)}); confirm she wants a no-code-only scope before applying."
        )
    if location:
        quotes = "; ".join(f'"{o[:110]}"' for o in location[:3])
        notes.append(
            f"LOCATION / SCHEDULE CONDITION: {quotes} - candidate is Brazil-based and remote (positions list Remote); verify eligibility."
        )
    for o in other:
        notes.append(f'ROLE SCOPE STATEMENT: "{o[:140]}"')
    return notes


class StrategyService:
    """Career Agent integration point: ``plan(...) -> ResumeStrategy``."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def plan(
        self,
        job: JobAnalysis,
        matches: MatchReport,
        index: EvidenceIndex,
        base: BaseResume,
        profiles: dict[str, Any],
        profile_override: str | None,
        max_pages: int = 2,
    ) -> tuple[ResumeStrategy, list[str]]:
        strat = plan(job, matches, index, base, profiles, profile_override, max_pages)
        warnings: list[str] = []
        if self.llm is not None and self.llm.name != "none":
            allowed_titles = profiles[strat.target_profile].get("titles", [strat.recommended_title])
            user = json.dumps(
                {
                    "job": {
                        "role_title": job.role_title,
                        "seniority": job.seniority,
                        "must_have": job.must_have,
                        "nice_to_have": job.nice_to_have,
                        "responsibilities": job.responsibilities[:12],
                    },
                    "matches": [
                        {
                            "requirement": m.requirement_text,
                            "match_type": m.match_type.value,
                            "evidence": [
                                f"{e.evidence_id}: {index.by_id[e.evidence_id].claim}"
                                for e in m.evidence[:2]
                            ],
                        }
                        for m in matches.matches
                    ],
                    "space_plan": [
                        {
                            "position": f"{index.positions[p.position_id].company} - {index.positions[p.position_id].title}",
                            "max_bullets": p.max_bullets,
                        }
                        for p in strat.position_plans
                    ],
                    "allowed_titles": allowed_titles,
                    "target_profile": strat.target_profile,
                },
                ensure_ascii=False,
            )
            try:
                resp = self.llm.complete_json(SYSTEM_PROMPT, user, max_tokens=1500)
                d = resp.data or {}
                title = str(d.get("recommended_title", "")).strip()
                if title in allowed_titles:
                    strat.recommended_title = title
                if d.get("positioning"):
                    strat.positioning = str(d["positioning"]).strip()
                if d.get("rationale"):
                    strat.rationale = str(d["rationale"]).strip()
                if isinstance(d.get("emphasized_projects"), list):
                    known = {r.project for r in index.bank.records if r.project}
                    ep = [str(p) for p in d["emphasized_projects"] if str(p) in known]
                    if ep:
                        strat.emphasized_projects = ep[:6]
                strat.strategy_source = "llm+deterministic"
            except LLMError as e:
                warnings.append(f"strategy: LLM unavailable, deterministic rationale used ({e})")
        return strat, warnings

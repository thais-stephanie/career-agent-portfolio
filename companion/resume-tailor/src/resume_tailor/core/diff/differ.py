# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Diff / explanation: what changed between the base resume and the tailored
one, and why. Pairing is deterministic (shared evidence ids first, then text
similarity). The "why" is built from the requirement ids each bullet answers
and the strategy's space plan, so it is traceable rather than eloquent.
"""

from __future__ import annotations

from resume_tailor.core.models import (
    BaseResume,
    Change,
    DiffReport,
    GeneratedResume,
    JobAnalysis,
    ResumeStrategy,
)
from resume_tailor.core.text import similarity


def _why(req_ids: list[str], job: JobAnalysis, extra: str = "") -> str:
    by_id = {r.id: r for r in job.requirements}
    texts = [by_id[r].text for r in req_ids if r in by_id][:3]
    if texts:
        w = "JD asks for: " + " | ".join(t[:90] for t in texts) + "."
    else:
        w = "Kept for relevance to the target profile; no specific JD requirement cited."
    return (w + " " + extra).strip()


def diff(
    base: BaseResume, res: GeneratedResume, job: JobAnalysis, strategy: ResumeStrategy
) -> DiffReport:
    changes: list[Change] = []
    plans = {p.position_id: p for p in strategy.position_plans}

    # headline
    if base.headline.strip() != res.headline.strip():
        changes.append(
            Change(
                section="headline",
                kind="modified",
                original=base.headline,
                tailored=res.headline,
                why=f"Target profile '{strategy.target_profile}' recommends this positioning.",
                supported_by=[],
            )
        )
    else:
        changes.append(
            Change(section="headline", kind="kept", original=base.headline, tailored=res.headline)
        )

    # summary: compare as blocks
    base_sum = " ".join(b.text for b in base.summary)
    new_sum = " ".join(b.text for b in res.summary)
    if similarity(base_sum, new_sum) < 0.95:
        ev = sorted({e for b in res.summary for e in b.evidence_ids})
        changes.append(
            Change(
                section="summary",
                kind="modified",
                original=base_sum,
                tailored=new_sum,
                why="Summary rewritten to lead with evidence answering the must-haves. "
                + strategy.positioning,
                supported_by=ev,
            )
        )
    else:
        changes.append(Change(section="summary", kind="kept", original=base_sum, tailored=new_sum))

    base_pos = {p.position_id: p for p in base.positions}
    for e in res.experience:
        bp = base_pos.get(e.position_id)
        base_bullets = list(bp.bullets) if bp else []
        used: set[int] = set()
        plan = plans.get(e.position_id)
        plan_note = f"Space plan: {plan.max_bullets} bullet(s), {plan.note}." if plan else ""
        for nb in e.bullets:
            best_i, best_s = -1, 0.0
            for i, ob in enumerate(base_bullets):
                if i in used:
                    continue
                s = similarity(ob.text, nb.text)
                if set(ob.evidence_ids) & set(nb.evidence_ids):
                    s += 0.35
                if s > best_s:
                    best_i, best_s = i, s
            if best_i >= 0 and best_s >= 0.45:
                used.add(best_i)
                ob = base_bullets[best_i]
                kind = "kept" if similarity(ob.text, nb.text) > 0.97 else "modified"
                changes.append(
                    Change(
                        section=e.position_id,
                        kind=kind,
                        original=ob.text,
                        tailored=nb.text,
                        why=_why(nb.requirement_ids, job, plan_note) if kind == "modified" else "",
                        supported_by=nb.evidence_ids,
                        requirement_ids=nb.requirement_ids,
                    )
                )
            else:
                changes.append(
                    Change(
                        section=e.position_id,
                        kind="added",
                        original=None,
                        tailored=nb.text,
                        why=_why(nb.requirement_ids, job, plan_note),
                        supported_by=nb.evidence_ids,
                        requirement_ids=nb.requirement_ids,
                    )
                )
        for i, ob in enumerate(base_bullets):
            if i not in used:
                why = (
                    "Omitted: lower relevance to this JD"
                    + (f" ({plan_note.lower()})" if plan_note else "")
                    + "."
                )
                if any(ob.text[:90] in x for x in strategy.irrelevant_content):
                    why = "Omitted: its evidence answers no requirement in this JD."
                changes.append(
                    Change(
                        section=e.position_id,
                        kind="removed",
                        original=ob.text,
                        tailored=None,
                        why=why,
                        supported_by=ob.evidence_ids,
                    )
                )
    # positions dropped entirely
    gen_pos = {e.position_id for e in res.experience}
    for bp in base.positions:
        if bp.position_id not in gen_pos:
            for ob in bp.bullets:
                changes.append(
                    Change(
                        section=bp.position_id,
                        kind="removed",
                        original=ob.text,
                        tailored=None,
                        why="Position not included in this tailored version.",
                        supported_by=ob.evidence_ids,
                    )
                )

    # skills
    base_sk = {i for g in base.skills for i in g.items}
    new_sk = {i for g in res.skills for i in g.items}
    for s in sorted(new_sk - base_sk):
        changes.append(
            Change(
                section="skills", kind="added", tailored=s, why="Evidenced term relevant to the JD."
            )
        )
    for s in sorted(base_sk - new_sk):
        changes.append(
            Change(
                section="skills",
                kind="removed",
                original=s,
                why="Not relevant to this JD or not vouched for by evidence.",
            )
        )

    summary = {
        k: sum(1 for c in changes if c.kind == k) for k in ("added", "modified", "removed", "kept")
    }
    return DiffReport(base_resume_id=base.id, changes=changes, summary=summary)


class DiffService:
    def diff(
        self, base: BaseResume, res: GeneratedResume, job: JobAnalysis, strategy: ResumeStrategy
    ) -> DiffReport:
        return diff(base, res, job, strategy)

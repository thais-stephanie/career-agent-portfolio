"""The tailoring report of spec v1 (`report`), computed, never asked of a model.

For one version and one job ad it says, from Tailor V2's own requirements and
retrieval (`jd`, `tailor`):

* `coverage`: each ask the ad makes (eligibility apart), `must` for a required
  one, `nice` for a preferred one and `unstated` when the ad does not say
  (absence is never "must"), matched `strong`, `partial` or `none` with the
  confirmed claims shown for it;
* `gaps`: asks no confirmed evidence answers; never filled in;
* `omitted_evidence`: confirmed claims that answer an ask and are not shown,
  and lines the page budget hid;
* `risk_flags`: style findings on visible lines, visible lines no confirmed
  evidence backs, lines citing evidence that is not confirmed now, phrases a
  claim's limits forbid, a page-limit conflict, no summary when the
  evidence could not support one (`SUMMARY_MISSING`; a short summary is not a
  fault), and ad lines written to a model (`ADDRESSED_TO_A_MODEL`), which
  are never asks, gaps or questions;
* `questions_for_user`: statements still waiting for the person's review that
  would answer an ask. They never reach the resume; confirming them is the
  person's act, in Career Evidence;
* `word_count` of the visible resume, and the page limit it was held to.

Nothing here writes anything.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from typing import Any

from career_agent.resume_doc import jd, style
from career_agent.resume_doc.budget import WORD_BUDGET, Fit, word_count
from career_agent.resume_doc.evidence import unconfirmed_lines
from career_agent.resume_doc.master import (
    JOB,
    OUTSIDE_A_JOB,
    UNKNOWN,
    _career,
    claims_by_key,
    skill_origin,
)
from career_agent.resume_doc.models import EVIDENCED_ORIGINS, Origin, ResumeDocument
from career_agent.resume_doc.tailor import (
    ADDRESSED_TO_A_MODEL,
    Source,
    _lines,
    addressed_to_a_model,
    retrieve,
    sources,
    strength,
)
from career_agent.storage.career_repo import SKILL_CATEGORIES

VERSION = "resume-tailor-report-v1"
__all__ = ["ADDRESSED_TO_A_MODEL", "OUTSIDE_A_JOB", "build"]
_TYPE = {"REQUIRED": "must", "PREFERRED": "nice"}
_MATCH = {"COVERED": "strong", "PARTLY": "partial"}


def _literals(doc: ResumeDocument, pool: list[Source]) -> set[str]:
    """Names a line may hold without choosing their words: employers, project
    and certification names, skill labels, tools, the job's title and company."""
    out = {e.employer for e in doc.experience} | {p.name for p in doc.projects}
    out |= {c.name for c in doc.certifications} | {c.issuer or "" for c in doc.certifications}
    out |= {i.label for g in doc.skills for i in g.items}
    out |= {t for s in pool for t in s.tools}
    if doc.target:
        out |= {doc.target.title, doc.target.company or ""}
    return out


def _outside(claim: Source) -> bool:
    """A confirmed claim of work outside any job: no experience, and not a
    skill or tool claim of its own (those stand alone by nature)."""
    return claim.experience_id is None and claim.category not in SKILL_CATEGORIES


def _pending(conn: sqlite3.Connection) -> list[Source]:
    _, rows = _career(conn)
    return [
        Source(id=r["claim_key"], text=str(r["text"]), claim_key=r["claim_key"],
               tools=tuple(r.get("tools") or ()))
        for r in rows if r["state"] == "PENDING" and str(r.get("text") or "").strip()
    ]  # fmt: skip


def build(
    conn: sqlite3.Connection,
    doc: ResumeDocument,
    ad_text: str,
    *,
    page_limit: int,
    fit: Fit | None = None,
    limits: Mapping[str, list[str]] | None = None,
) -> dict[str, Any]:
    """The report for `doc` against `ad_text`. `limits` maps a claim key to the
    phrases its limits forbid (Career Evidence holds no such field yet)."""
    analysis = jd.analyse(ad_text)
    aimed = addressed_to_a_model(analysis)
    pool = sources(conn, doc)
    supports = retrieve(analysis, pool)  # no ask written to a model
    by_key = {s.claim_key: s for s in pool if s.claim_key}
    shown_keys = {k for _, line, shown in _lines(doc) if shown for k in line.evidence_ids}
    coverage, gaps, eligibility = [], [], []
    omitted: dict[str, str] = {}
    counts = {"must": 0, "nice": 0, "unstated": 0}
    for sup in supports:
        req = sup.requirement
        if req.eligibility:
            eligibility.append({"requirement_id": req.id, "requirement": req.source_quote})
            continue
        kind = _TYPE.get(req.hardness, "unstated")
        counts[kind] += 1
        cited = sorted({k for s, _ in sup.shown for k in (s.claim_key, *s.evidence_ids) if k})
        backing = [by_key[k] for k in cited if k in by_key]
        coverage.append({
            "requirement_id": req.id, "requirement": req.source_quote, "type": kind,
            "match": _MATCH.get(sup.coverage, "none"), "evidence_ids": cited,
            # Only a project's or a lab's work answers it: never a job's.
            "only_outside_a_job": bool(backing) and all(_outside(c) for c in backing),
        })  # fmt: skip
        if sup.coverage in ("NOT_FOUND", "SAID") and not sup.unshown:
            gaps.append({"requirement_id": req.id, "requirement": req.source_quote, "type": kind})
        for s, _ in sup.unshown:
            if s.claim_key and s.claim_key not in shown_keys:
                omitted.setdefault(s.claim_key, "ANSWERS_AN_ASK_NOT_SHOWN")
    lines = {line.id: line for _, line, _ in _lines(doc)}
    for cut in fit.cuts if fit else []:
        line = lines.get(cut["ref"].removeprefix("bullet/"))
        for key in line.evidence_ids if line else []:
            omitted[key] = "HIDDEN_FOR_PAGE_LIMIT"
    claims = claims_by_key(*_career(conn))
    skills_off = "skills" in doc.layout.hidden_sections
    for group in doc.skills:
        for item in group.items:
            if (group.hidden or skills_off) and skill_origin(item.evidence_ids, claims) == UNKNOWN:
                for key in item.evidence_ids:
                    omitted.setdefault(key, "SKILL_ORIGIN_UNKNOWN")

    literals = _literals(doc, pool)
    said = {s.claim_key: s.text for s in pool if s.claim_key}
    flags: list[dict[str, Any]] = [
        {"code": "AD_TEXT_ADDRESSED_TO_A_MODEL", "ref": "job", "detail": quote}
        for quote in aimed.values()
    ]
    for _, line, shown in _lines(doc):
        if not shown or not line.text.strip():
            continue
        # The newer confirmed wording wins over the resume's copy of it.
        if line.origin is Origin.EVIDENCE_VERBATIM and any(
            k in said and jd.folded(said[k]).strip(" .") != jd.folded(line.text).strip(" .")
            for k in line.evidence_ids
        ):
            flags.append({"code": "EVIDENCE_CHANGED_SINCE", "ref": line.id})
        for f in style.findings(line.text, literals):
            flags.append({"code": f"STYLE_{f['code']}", "ref": line.id, "detail": f["match"],
                          "origin": line.origin.value})  # fmt: skip
        if line.origin not in EVIDENCED_ORIGINS:
            flags.append({"code": "NO_CONFIRMED_EVIDENCE", "ref": line.id,
                          "origin": line.origin.value})  # fmt: skip
        for key in line.evidence_ids:
            for f in style.limit_findings(line.text, (limits or {}).get(key, [])):
                flags.append({"code": "CLAIM_LIMIT", "ref": line.id, "detail": f["match"],
                              "evidence_id": key})  # fmt: skip
    # A skill no job's evidence holds (a lab's or a project's tool) is not
    # production experience: its group's name must say so; one nothing places
    # stays off the page (`master.regroup_skills`).
    for group in doc.skills:
        if group.hidden or skills_off or OUTSIDE_A_JOB.search(group.name):
            continue
        for item in group.items:
            if not item.evidence_ids or item.origin not in EVIDENCED_ORIGINS:
                continue
            origin = skill_origin(item.evidence_ids, claims)
            if origin != JOB:
                code = "SKILL_ORIGIN_UNKNOWN" if origin == UNKNOWN else "SKILL_ONLY_OUTSIDE_A_JOB"
                flags.append({"code": code, "ref": item.id, "detail": item.label})
    flags += [
        {"code": "EVIDENCE_NOT_CONFIRMED", "ref": ref} for ref in unconfirmed_lines(conn, doc)
    ]
    if fit and fit.status == "CONFLICT":
        flags.append({"code": "PAGE_LIMIT_CONFLICT", "ref": "document",
                      "detail": f"{fit.pages} pages for a limit of {fit.limit}"})  # fmt: skip
    # Resume Tailor v1.1: a summary is recommended, never required. Its
    # absence is said with the reason (the evidence could not support one);
    # a summary the person hid is their choice, and a short one is no fault.
    if doc.summary is None:
        flags.append({"code": "SUMMARY_MISSING", "ref": "summary", "detail": ""})

    # The words a person reads for each flag: the line, the skill, or the ad's.
    texts = {line.id: line.text for line in lines.values()}
    texts |= {i.id: i.label for g in doc.skills for i in g.items}
    for flag in flags:
        flag["text"] = texts.get(flag["ref"], flag.get("detail", ""))

    questions = []
    for source in _pending(conn):
        asks = [r.id for r in analysis.requirements
                if not r.eligibility and r.id not in aimed and strength(r, source) > 0]  # fmt: skip
        if asks:
            questions.append({"code": "CONFIRM_BEFORE_USE", "claim_key": source.claim_key,
                              "text": source.text, "requirement_ids": asks})  # fmt: skip

    return {
        "version": VERSION,
        "job_summary": {
            "title": doc.target.title if doc.target else None,
            "company": doc.target.company if doc.target else None,
            "asks": counts,
            "eligibility": len(eligibility),
        },
        "coverage": coverage,
        "gaps": gaps,
        "eligibility": eligibility,
        "omitted_evidence": [{"evidence_id": k, "reason": v, "text": said.get(k, "")}
                             for k, v in sorted(omitted.items())],
        "risk_flags": flags,
        "questions_for_user": questions,
        "word_count": word_count(doc),
        "page_limit": page_limit,
        "word_budget": list(WORD_BUDGET[page_limit]),
        "fit": None if fit is None else {
            "status": fit.status, "pages": fit.pages, "rounds": fit.rounds,
            "cuts": fit.cuts, "protected": fit.protected, "notes": fit.notes,
        },
    }  # fmt: skip

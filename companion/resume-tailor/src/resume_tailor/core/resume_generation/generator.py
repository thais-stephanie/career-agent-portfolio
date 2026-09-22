# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Resume generation.

Deterministic mode assembles the resume from ``resume_text`` of the featured
evidence records (verbatim source phrasing), a templated summary composed only
of evidenced terms, and a skills section built from evidenced JD terms plus
the base resume's evidenced skills.

LLM mode asks the model to rewrite the *same* featured records into concise
bullets that echo JD terminology where equivalent. The model receives only
records it may use and must attach ``evidence_ids`` to every bullet and
summary sentence. Structural fields (company, title, dates) are never sent
for generation; they are copied from ``Position``.

Either way the output goes through ``ClaimValidator`` before anyone sees it.
"""

from __future__ import annotations

import json
from typing import Any

from resume_tailor.core.evidence.bank import EvidenceIndex
from resume_tailor.core.lexicon import (
    CONCEPT_GROUPS,
    GENERIC_TERMS,
    canonical,
    capability_products,
    find_terms,
    term_kind,
)
from resume_tailor.core.locale import normalize_prose
from resume_tailor.core.models import (
    BaseResume,
    Bullet,
    ClaimBinding,
    ExperienceEntry,
    GeneratedResume,
    JobAnalysis,
    MatchReport,
    MatchType,
    PositionKind,
    Proficiency,
    ResumeStrategy,
    SkillGroup,
    TailorOptions,
)
from resume_tailor.core.resume_generation.certifications import select_certifications
from resume_tailor.core.resume_generation.grouping import group_experience
from resume_tailor.core.text import word_count
from resume_tailor.providers.llm.base import LLMError, LLMProvider

SYSTEM_PROMPT = """You rewrite a candidate's evidence into a tailored, ATS-friendly resume. You are a careful editor, not a storyteller.

HARD RULES (violations are rejected by a validator, so do not bother):
1. Every bullet and every summary sentence cites the evidence it was written from. DEFAULT: exactly ONE evidence_id per bullet. If you genuinely must combine two records, add "bindings": a list of {"clause": "<exact substring of the bullet>", "evidence_id": "..."} so that every tool name and every number sits inside a clause bound to the record that contains it. Use ONLY facts, tools, numbers and responsibilities present in those records.
1b. Do not widen scope: no "company-wide", "global", "infrastructure", "transformation", "owned/led" unless the record itself says so.
2. Never add a tool, platform, language, metric, year count, team size, or responsibility that is not in the cited records.
3. Never upgrade depth: "integrated with X" stays integration, not administration; "exposure" stays exposure. No "expert", "advanced", "deep expertise" unless the record says so.
4. Do not write company names, titles or dates; they are inserted by the system.
5. Use the job description's terminology ONLY where it is genuinely equivalent to the evidence (e.g. say "HRIS integration" for a Workday integration; do not say "HRIS administration").
6. Concise bullets (max ~30 words), strong varied verbs, no first person, no filler adjectives, keep the metrics exactly as written in the evidence.
7. Respect max_bullets per position. Prefer the records listed first (they are ranked by relevance).

Return STRICT JSON:
{
 "headline": "<use recommended_title unless a listed alternative fits better>",
 "summary": [{"text": "<one sentence>", "evidence_ids": ["<one id>"], "bindings": []}, ...],   // 2-4 sentences
 "experience": [{"position_id": "...", "bullets": [{"text": "...", "evidence_ids": ["<one id>"], "bindings": [], "requirement_ids": ["..."]}]}],
 "skills": [{"group": "<group name>", "items": ["<only evidenced terms>"]}]
}"""


# Skills groups are concept families from the lexicon, so the section reads the same way for
# every target profile; items are ordered JD-named first, then tools used in the resume body,
# then the base resume's own skills, and capped so the section stays a shortlist.
SKILL_FAMILIES: list[tuple[str, tuple[str, ...]]] = [
    (
        "Systems & Platforms",
        (
            "crm",
            "erp",
            "hris",
            "ats",
            "billing",
            "ecommerce",
            "esign_forms",
            "collab",
            "ticketing",
            "spreadsheets",
            "product_analytics",
            "marketing_automation",
        ),
    ),
    (
        "Automation & Integration",
        ("ipaas", "api", "automation_general", "devops", "hosting", "cloud", "error_handling_fam"),
    ),
    (
        "Data & Reporting",
        (
            "bi",
            "database",
            "warehouse",
            "transform",
            "data_modeling",
            "quality",
            "close_ops",
            "spreadsheet_skills",
        ),
    ),
    ("Engineering & AI", ("languages", "ai", "agent_quality")),
    ("Delivery & Practices", ("process", "delivery", "reliability_ops", "implementation", "soft")),
]
_MAX_PER_GROUP = 6
# tool-vendor families: membership says which market a tool is in, not that the JD wants the sibling
_VENDOR_FAMILIES = {
    "crm",
    "ipaas",
    "erp",
    "hris",
    "ats",
    "billing",
    "ecommerce",
    "esign_forms",
    "collab",
    "ticketing",
    "spreadsheets",
    "product_analytics",
    "marketing_automation",
    "database",
    "warehouse",
    "hosting",
    "cloud",
    "languages",
    "transform",
    "api_frameworks",
    "agent_frameworks",
}
# evidenced terms outside every concept family, placed by what they are
_TERM_FAMILY = {
    "dax": "Data & Reporting",
    "cms": "Systems & Platforms",
    "root cause analysis": "Delivery & Practices",
    "internal tools": "Automation & Integration",
    "front-end development": "Engineering & AI",
    "llm evaluation": "Engineering & AI",
    "analytics": "Data & Reporting",
}
# general capabilities that are too broad to list on their own account: they enter Skills only when the JD
# names them and the evidence supports them at DIRECT level (literally, or through a product that delivers them)
_JD_NAMED_CAPABILITIES = {"systems integration", "analytics", "marketing automation"}
_CATEGORY_SKILLS = {"cms", "crm", "erp", "hris", "ats"}
_BASE_ONLY_FILL = (
    4  # a term only the base resume lists is added only while a group has fewer items than this
)


def _skills_from_evidence(
    index: EvidenceIndex,
    job: JobAnalysis,
    matches: MatchReport,
    base: BaseResume,
    featured_ids: list[str],
    lead_ids: list[str] | None = None,
) -> list[SkillGroup]:
    """A recruiter-facing shortlist: evidenced tools/practices, grouped by lexicon family, in
    this order: JD-named; JD-family tools that the lead evidence (heroes + lead position) uses;
    other tools the lead evidence uses; tools other featured bullets use; JD-family tools the
    resume does not otherwise show; the base resume's own list. A tool never ranks highly merely
    because it exists in the bank. Practitioner domains and umbrella words never appear."""
    evidenced = index.all_terms
    # every lexicon term the JD names (technologies, keywords and the requirement lines themselves)
    jd_terms = {canonical(t) for t in [*job.technologies, *job.keywords]} | set(
        find_terms(" ".join([*job.must_have, *job.nice_to_have, *job.responsibilities]))
    )
    body_terms: set[str] = set()
    lead_terms: set[str] = set()
    for eid in featured_ids:
        r = index.by_id.get(eid)
        if r:
            body_terms |= {canonical(t) for t in r.technologies} | {canonical(t) for t in r.skills}
    for eid in lead_ids or []:
        r = index.by_id.get(eid)
        if r:
            lead_terms |= {canonical(t) for t in r.technologies} | {canonical(t) for t in r.skills}
    base_terms = {canonical(i) for g in base.skills for i in g.items}
    # a platform category the JD asks for by name (CMS, CRM) is a skill when the bank evidences it
    candidates = {
        t
        for t in (jd_terms | body_terms | base_terms)
        if t in evidenced
        and (t not in GENERIC_TERMS or (t in jd_terms and t in _CATEGORY_SKILLS))
        and t not in _NOT_SKILLS
        and t not in _JD_NAMED_CAPABILITIES
        and len(t) > 2
    }

    # a tool held only at exposure depth (listed in a skills section or a POC) is not a skill to advertise
    def _depth_ok(term: str) -> bool:
        vouchers = [index.by_id[i] for i in index.term_index.get(term, set())]
        return (
            any(
                index.effective_proficiency(r, term)
                in (Proficiency.LED, Proficiency.HANDS_ON, Proficiency.INTEGRATION)
                for r in vouchers
            )
            if vouchers
            else False
        )

    candidates = {t for t in candidates if _depth_ok(t)}
    # a general capability the JD names (analytics, systems integration) is a skill only when the evidence
    # supports it at DIRECT level: literally, or through hands-on work with a product that delivers it
    # (lexicon.CAPABILITY_PRODUCTS). A capability the match report holds below DIRECT stays out, and a
    # named product the JD asks for (Adobe Analytics) is never listed on the strength of a sibling.
    verdicts = {canonical(m.requirement_text): m.match_type for m in matches.matches}

    def _capability_supported(term: str) -> bool:
        if term not in jd_terms or term_kind(term) != "capability" or term in _NOT_SKILLS:
            return False
        if verdicts.get(term, MatchType.DIRECT) != MatchType.DIRECT:
            return False
        return _depth_ok(term) or any(_depth_ok(p) for p in capability_products(term))

    candidates |= {t for t in jd_terms if t in _JD_NAMED_CAPABILITIES and _capability_supported(t)}
    candidates |= {t for t in jd_terms if capability_products(t) and _capability_supported(t)}
    # terms whose *capability* family the JD names (APIs, automation, BI, AI, delivery practices) rank
    # next to JD-named tools; a vendor family does not promote its siblings (the JD naming Google
    # Workspace or HubSpot says nothing about Slack or Salesforce)
    jd_families = {
        g
        for g, members in CONCEPT_GROUPS.items()
        if g not in _VENDOR_FAMILIES and any(m in jd_terms for m in members)
    }
    family_terms = {
        t for t in candidates if any(t in CONCEPT_GROUPS.get(g, []) for g in jd_families)
    }

    def family_of(term: str) -> str | None:
        if term in _TERM_FAMILY:
            return _TERM_FAMILY[term]
        for name, groups in SKILL_FAMILIES:
            if any(term in CONCEPT_GROUPS.get(g, []) for g in groups):
                return name
        # a term outside every concept family: a technology (by how the bank records it) joins
        # Automation & Integration, a practice joins Delivery & Practices; it then competes for the cap
        vouchers = [index.by_id[i] for i in index.term_index.get(term, set())]
        as_tech = sum(1 for r in vouchers if term in {canonical(t) for t in r.technologies})
        as_skill = sum(1 for r in vouchers if term in {canonical(t) for t in r.skills})
        if not vouchers or (as_tech == 0 and as_skill == 0):
            return None
        return "Automation & Integration" if as_tech >= as_skill else "Delivery & Practices"

    def weight(term: str) -> int:
        # how much of the bank stands behind the term at working depth: the better-evidenced tool leads its group
        # (a general capability counts every record that used one of the products delivering it)
        backing = {(i, term) for i in index.term_index.get(term, set())} | {
            (i, p) for p in capability_products(term) for i in index.term_index.get(p, set())
        }
        return len(
            {
                i
                for i, t in backing
                if index.effective_proficiency(index.by_id[i], t)
                in (Proficiency.LED, Proficiency.HANDS_ON, Proficiency.INTEGRATION)
            }
        )

    def tier(term: str) -> int:
        if term in jd_terms:
            return 0
        if term in family_terms and term in lead_terms:
            return 1
        if term in lead_terms:
            return 2
        if term in body_terms:
            return 3
        return 4 if term in family_terms else 5

    def priority(term: str) -> tuple[int, int, str]:
        return (tier(term), -weight(term), term)

    out: list[SkillGroup] = []
    placed: set[str] = set()
    for name, _ in SKILL_FAMILIES:
        ranked = sorted((t for t in candidates if family_of(t) == name), key=priority)
        items: list[str] = []
        for t in ranked:
            filler = (
                tier(t) >= 4
            )  # a tool the resume does not show: family sibling or base-resume list
            if len(items) >= _MAX_PER_GROUP or (filler and len(items) >= _BASE_ONLY_FILL):
                break
            items.append(t)
        if items:
            out.append(SkillGroup(name=name, items=[_display(t) for t in items]))
            placed |= set(items)
    return out


# terms that must never be listed as skills: practitioner domains and vague umbrella words
_NOT_SKILLS = {
    "tax",
    "bookkeeping",
    "accounting",
    "payroll",
    "compliance",
    "recruiting",
    "compensation",
    "general ledger",
    "chart of accounts",
    "automation",
    "english",
    "insurance",
    "ai",
    "finance",
    "financial operations",
    "revenue operations",
    "customer success",
    "marketing operations",
    "sales operations",
    "business administration",
    "pre-sales",
    "training",
    "reporting logic",
    "communication",
    "documentation",
    "api integration",
}


_DISPLAY = {
    "power bi": "Power BI",
    "dax": "DAX",
    "power query": "Power Query",
    "sql": "SQL",
    "rest apis": "REST APIs",
    "graphql": "GraphQL",
    "hubspot": "HubSpot",
    "netsuite": "NetSuite",
    "sap": "SAP",
    "quickbooks": "QuickBooks",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "node.js": "Node.js",
    "ci/cd": "CI/CD",
    "aws": "AWS",
    "gcp": "GCP",
    "llm": "LLMs",
    "rag": "RAG",
    "ai agents": "AI agents",
    "n8n": "n8n",
    "hris": "HRIS",
    "erp": "ERP",
    "crm": "CRM",
    "ats": "ATS",
    "bi": "BI",
    "etl": "ETL",
    "uat": "UAT",
    "raci": "RACI",
    "html": "HTML",
    "css": "CSS",
    "json": "JSON",
    "docusign": "DocuSign",
    "jotform": "Jotform",
    "ipaas": "iPaaS",
    "oracle database": "Oracle Database",
    "postgresql": "PostgreSQL",
    "microsoft teams": "Microsoft Teams",
    "google analytics": "Google Analytics",
    "google sheets": "Google Sheets",
    "tailwind css": "Tailwind CSS",
    "hubl": "HubL",
    "anthropic": "Claude",
    "no-code automation": "No-code automation",
    "dependency mapping": "Dependency mapping",
    "process mapping": "Process mapping",
    "production monitoring": "Production monitoring",
    "technical documentation": "Technical documentation",
    "stakeholder management": "Stakeholder management",
    "saas implementation": "SaaS implementation",
    "dashboards": "Dashboard development",
    "reporting logic": "Reporting logic",
    "troubleshooting": "Troubleshooting",
    "data quality": "Data quality",
    "project delivery": "Project delivery",
    "client enablement": "Client enablement",
    "proof of concept": "Proofs of concept",
    "system of record": "System-of-record design",
    "error handling": "Error handling",
    "incident management": "Incident management",
    "risk management": "Risk management",
    "cms": "CMS",
    "data flows": "Data flows",
    "cdp": "CDP",
    "llm evaluation": "LLM evaluation",
    "front-end development": "Front-end development",
    "root cause analysis": "Root-cause analysis",
    "internal tools": "Internal tools",
    "data governance": "Data governance",
    "data modeling": "Data modelling",
    "dimensional modeling": "Dimensional modelling",
    "data reconciliation": "Data reconciliation",
    "salesforce": "Salesforce",
    "workday": "Workday",
    "analytics": "Analytics",
    "systems integration": "Systems integration",
    "marketing automation": "Marketing automation",
}


def _display(term: str) -> str:
    return _DISPLAY.get(term, term.title() if " " in term or term.islower() else term)


def _bindings(raw: dict[str, Any]) -> list[ClaimBinding]:
    out = []
    for bd in raw.get("bindings", []) or []:
        if isinstance(bd, dict) and str(bd.get("clause", "")).strip() and bd.get("evidence_id"):
            out.append(
                ClaimBinding(clause=str(bd["clause"]).strip(), evidence_id=str(bd["evidence_id"]))
            )
    return out


def _lead_ids(strategy: ResumeStrategy) -> list[str]:
    """Heroes plus the featured evidence of the most relevant position."""
    lead = (
        max(strategy.position_plans, key=lambda p: p.relevance) if strategy.position_plans else None
    )
    return list(
        dict.fromkeys([*strategy.hero_evidence_ids, *(lead.featured_evidence_ids if lead else [])])
    )


def _featured(strategy: ResumeStrategy) -> list[str]:
    ids: list[str] = []
    for p in strategy.position_plans:
        ids.extend(p.featured_evidence_ids)
    return ids


def deterministic_generate(
    job: JobAnalysis,
    matches: MatchReport,
    strategy: ResumeStrategy,
    index: EvidenceIndex,
    base: BaseResume,
    options: TailorOptions,
    profile_descriptor: str | None = None,
) -> GeneratedResume:
    req_by_evidence: dict[str, list[str]] = {}
    for m in matches.matches:
        for e in m.evidence:
            req_by_evidence.setdefault(e.evidence_id, []).append(m.requirement_id)
    entries: list[ExperienceEntry] = []
    n = 0
    for pp in strategy.position_plans:
        pos = index.positions[pp.position_id]
        bullets = []
        for eid in pp.featured_evidence_ids[: pp.max_bullets]:
            r = index.by_id[eid]
            n += 1
            bullets.append(
                Bullet(
                    id=f"b{n:03d}",
                    text=r.resume_text,
                    evidence_ids=[eid],
                    requirement_ids=req_by_evidence.get(eid, [])[:4],
                    origin="verbatim",
                )
            )
        entries.append(
            ExperienceEntry(
                position_id=pos.id,
                company=pos.company,
                title=pos.title,
                start=pos.start,
                end=pos.end,
                location=pos.location,
                company_blurb=pos.company_blurb,
                bullets=bullets,
            )
        )

    summary = _build_summary(job, strategy, index, profile_descriptor)
    skills = _skills_from_evidence(
        index, job, matches, base, _featured(strategy), _lead_ids(strategy)
    )
    certs, _ = select_certifications(index, job, _body_terms(index, _featured(strategy)))
    res = GeneratedResume(
        candidate=index.bank.candidate.model_copy(update={"portfolio": ""}),
        headline=strategy.recommended_title,
        summary=summary,
        experience=entries,
        skills=skills,
        education=[e for e in index.bank.education if e.include_by_default],
        certifications=certs,
        section_order=strategy.section_order,
        generation_source="deterministic",
    )
    return estimate_length(localize(res, options.resume_locale))


def _body_terms(index: EvidenceIndex, featured_ids: list[str]) -> set[str]:
    out: set[str] = set()
    for eid in featured_ids:
        r = index.by_id.get(eid)
        if r:
            out |= {canonical(t) for t in r.technologies} | {canonical(t) for t in r.skills}
    return out


def localize(res: GeneratedResume, locale: str) -> GeneratedResume:
    """Spell generated prose in one locale. Evidence ids, bindings and numbers are untouched;
    the validator still checks every sentence against its records afterwards."""
    res.headline = normalize_prose(res.headline, locale)
    for _, b in res.all_bullets():
        b.text = normalize_prose(b.text, locale)
    for e in res.experience:
        e.company_blurb = normalize_prose(e.company_blurb, locale)
    for g in res.skills:
        g.items = [normalize_prose(i, locale) for i in g.items]
    return res


def _build_summary(
    job: JobAnalysis, strategy: ResumeStrategy, index: EvidenceIndex, descriptor: str | None
) -> list[Bullet]:
    """Three to four short sentences, every one evidence-bound:
    1. who she is (profile descriptor + tenure record)
    2. the strongest proof point at the lead position (hero #1, or the lead position's opener)
    3. a second, different proof point from another position when one exists
    4. a short line of JD-named hands-on tools (bound per tool), dropped if the summary runs long
    """
    summary: list[Bullet] = []
    tenure_rec = next((r for r in index.bank.records if r.id == "career_tenure_001"), None)
    if tenure_rec:
        who = tenure_rec.summary_text or tenure_rec.resume_text
        # the identity line may only use terms the tenure record itself vouches for: a descriptor
        # naming a specialty the record does not carry ("AI ...") would claim 8+ years of it
        if descriptor and set(find_terms(descriptor)) <= index.record_terms(tenure_rec):
            who = (
                f"{descriptor} with {who[0].lower() + who[1:]}"
                if who.lower().startswith("8+") or who[:1].isdigit()
                else who
            )
        summary.append(
            Bullet(
                id="s001",
                text=who.rstrip(".") + ".",
                evidence_ids=[tenure_rec.id],
                origin="verbatim",
            )
        )

    plans_by_rel = sorted(strategy.position_plans, key=lambda p: -p.relevance)
    lead_pos = plans_by_rel[0] if plans_by_rel else None
    # "Most recently" is only true when no other position that could appear on a resume ends later,
    # rendered or not: a hidden later position would make the phrase misleading
    latest = max(
        (p for p in index.bank.positions if p.include_by_default), key=lambda p: p.end or "9999"
    )

    def phrase(rec_id: str, prefix_pos: bool) -> Bullet:
        r = index.by_id[rec_id]
        pos = index.positions[r.position_id]
        text = (r.summary_text or r.resume_text).rstrip(".")
        if prefix_pos:
            when = "Most recently at" if pos.id == latest.id else "At"
            span = (
                ""
                if pos.id == latest.id
                else f" ({pos.start[:4]}\u2013{('present' if pos.end is None else pos.end[:4])})"
            )
            text = f"{when} {pos.company}{span}, {text[0].lower() + text[1:]}"
        return Bullet(id="", text=text + ".", evidence_ids=[rec_id], origin="verbatim")

    used: list[str] = []
    heroes = [
        h
        for h in strategy.hero_evidence_ids
        if index.positions[index.by_id[h].position_id].include_by_default
    ]
    # sentence 2: hero at the lead position (a metric-bearing proof), else the lead position's opener
    first = next(
        (h for h in heroes if lead_pos and index.by_id[h].position_id == lead_pos.position_id), None
    )
    if first is None and lead_pos:
        first = next(
            (e for e in lead_pos.featured_evidence_ids if index.by_id[e].metrics),
            lead_pos.featured_evidence_ids[0] if lead_pos.featured_evidence_ids else None,
        )
    if first:
        b = phrase(first, True)
        b.id = "s002"
        summary.append(b)
        used.append(first)
    # sentence 3: a hero from a different position (breadth); else the strongest record of the
    # second most relevant position, so the summary shows more than one employer
    second = (
        next(
            (
                h
                for h in heroes
                if h not in used and index.by_id[h].position_id != index.by_id[used[0]].position_id
            ),
            None,
        )
        if used
        else None
    )
    if second is None and used and len(plans_by_rel) > 1:
        other = next(
            (
                p
                for p in plans_by_rel[1:]
                if p.position_id != index.by_id[used[0]].position_id and p.featured_evidence_ids
            ),
            None,
        )
        if other:
            second = other.featured_evidence_ids[0]
    if second:
        r = index.by_id[second]
        pos = index.positions[r.position_id]
        text = (r.summary_text or r.resume_text).rstrip(".")
        lead_end = index.positions[index.by_id[used[0]].position_id].end or "9999"
        span = f"{pos.start[:4]}\u2013{('present' if pos.end is None else pos.end[:4])}"
        body = text[0].lower() + text[1:]
        # the second proof may come from a later position, not only an earlier employer; an
        # independent project is not "at" a company
        if (pos.end or "9999") > lead_end:
            text = (
                f"Currently, in an independent project ({span}), {body}."
                if pos.kind == PositionKind.INDEPENDENT
                else f"Since {pos.start[:4]} at {pos.company}, {body}."
            )
        elif pos.kind == PositionKind.INDEPENDENT:
            text = f"In an independent project ({span}), {body}."
        else:
            text = f"Earlier, at {pos.company} ({span}), {body}."
        summary.append(Bullet(id="s003", text=text, evidence_ids=[second], origin="verbatim"))
        used.append(second)
    # sentence 4: hands-on tools the JD names, each bound to the record that vouches for it
    jd_terms = {canonical(t) for t in job.technologies}
    featured_pool = [e for p in plans_by_rel[:3] for e in p.featured_evidence_ids]
    pool = [
        index.by_id[e]
        for e in [*heroes, *featured_pool, *strategy.evidence_to_feature]
        if e in index.by_id
    ]
    api_family = set(CONCEPT_GROUPS.get("api", []))
    tools: list[tuple[str, str]] = []
    for r in pool:
        for t in r.technologies:
            c = canonical(t)
            depth = r.proficiency_for(t)
            ok = depth in (Proficiency.LED, Proficiency.HANDS_ON) or (
                c in api_family and depth == Proficiency.INTEGRATION
            )
            if c in jd_terms and ok and c not in [canonical(x) for x, _ in tools]:
                tools.append((t, r.id))
    tools = tools[:6]
    total = sum(len(b.text) for b in summary)
    if len(tools) >= 3 and total < 460:
        ids: list[str] = []
        for _, rid in tools:
            if rid not in ids:
                ids.append(rid)
        summary.append(
            Bullet(
                id="s004",
                text="Hands-on with " + ", ".join(t for t, _ in tools) + ".",
                evidence_ids=ids,
                bindings=[ClaimBinding(clause=t, evidence_id=rid) for t, rid in tools]
                if len(ids) > 1
                else [],
                origin="verbatim",
            )
        )
    return summary


# Layout model of the DOCX export (export/exporters.py): Letter page, 40pt top/bottom and 48pt side
# margins (712pt usable), Calibri 10.5pt on the python-docx default template (1.15 line spacing,
# 10pt after every paragraph that sets nothing else). Calibrated against Microsoft Word's own
# pagination of 30 real exports (acceptance results and unseen-job runs, with and without the
# two-page cap, 2026-09-20): estimate minus Word's continuous page position is -0.03 to +0.08
# page (mean +0.03). A renderer, when installed, verifies the limit; the buffer below covers
# the rest.
_PAGE_USABLE_PT = 792 - 80
_LINE_PT = 14.5  # 10.5pt x 1.15 line spacing x Calibri's natural line height
_CHARS_PER_LINE = 115  # full-width paragraph (516pt text width)
_BULLET_CHARS_PER_LINE = 111  # List Bullet indent
_PARA_AFTER_PT = 10  # template default for paragraphs the exporter does not tighten
# Headroom applied to the *estimate* when no renderer can verify the DOCX: the calibration spread
# above (0.08 page), so a document that the model puts at the limit still fits under layout variance.
PAGE_ESTIMATE_BUFFER = 0.08


def estimate_length(res: GeneratedResume) -> GeneratedResume:
    """Points-based page estimate that mirrors the DOCX export paragraph by paragraph. It is an
    estimate: the pipeline verifies the real page count with Word or LibreOffice when one is
    installed (export/pagination.py) and labels the number accordingly."""

    def lines(text, cpl):
        return max(1, -(-len(text) // cpl))

    heading = 10 + 11.5 * 1.38 + 2
    c = res.candidate
    h = 17 * 1.38 + _PARA_AFTER_PT + _LINE_PT + _PARA_AFTER_PT  # name, headline
    contact = " | ".join(x for x in [c.location, c.phone, c.email, c.linkedin, c.portfolio] if x)
    h += lines(contact, _CHARS_PER_LINE) * _LINE_PT + _PARA_AFTER_PT
    words = sum(word_count(b.text) for b in res.summary)
    if res.summary:
        h += (
            heading
            + lines(" ".join(b.text for b in res.summary), _CHARS_PER_LINE) * _LINE_PT
            + _PARA_AFTER_PT
        )
    h += heading
    for g in group_experience(res.experience):
        if g.grouped:  # one company line per employer, one title line per role
            h += 6 + _LINE_PT + (_LINE_PT if g.company_blurb else 0)
            h += sum(3 + _LINE_PT for _ in g.roles)
        else:
            e = g.roles[0]
            h += 6 + _LINE_PT + _LINE_PT + (_LINE_PT if e.company_blurb else 0)
        for e in g.roles:
            for b in e.bullets:
                h += lines(b.text, _BULLET_CHARS_PER_LINE) * _LINE_PT + 1
                words += word_count(b.text)
    if res.skills:
        h += heading + sum(
            lines(f"{g.name}: {', '.join(g.items)}", _CHARS_PER_LINE) * _LINE_PT + 1
            for g in res.skills
        )
    if res.education:
        h += heading + len(res.education) * (_LINE_PT + _PARA_AFTER_PT)
    if res.certifications:
        by_issuer: dict[str, list[str]] = {}
        for cert in res.certifications:
            by_issuer.setdefault(cert.issuer, []).append(cert.name)
        h += heading + sum(
            lines(f"{issuer}: {'; '.join(names)}", _CHARS_PER_LINE) * _LINE_PT
            for issuer, names in by_issuer.items()
        )
    if c.languages:
        h += heading + _LINE_PT + _PARA_AFTER_PT
    res.estimated_pages = round(h / _PAGE_USABLE_PT, 2)
    res.estimated_words = words
    return res


def _records_payload(
    index: EvidenceIndex, ids: list[str], req_by_evidence: dict[str, list[str]]
) -> list[dict[str, Any]]:
    out = []
    for eid in ids:
        r = index.by_id[eid]
        out.append(
            {
                "id": r.id,
                "project": r.project,
                "claim": r.claim,
                "resume_text": r.resume_text,
                "context": r.detailed_context[:300],
                "technologies": r.technologies,
                "skills": r.skills,
                "domains": r.domains,
                "metrics": r.metrics,
                "responsibilities": r.responsibilities,
                "proficiency": r.proficiency.value,
                "supports_requirements": req_by_evidence.get(eid, [])[:5],
            }
        )
    return out


def llm_generate(
    job: JobAnalysis,
    matches: MatchReport,
    strategy: ResumeStrategy,
    index: EvidenceIndex,
    base: BaseResume,
    options: TailorOptions,
    llm: LLMProvider,
    profile_titles: list[str],
) -> GeneratedResume:
    req_by_evidence: dict[str, list[str]] = {}
    for m in matches.matches:
        for e in m.evidence:
            req_by_evidence.setdefault(e.evidence_id, []).append(m.requirement_id)
    reqs = {r.id: r.text for r in job.requirements}
    positions_payload = []
    for pp in strategy.position_plans:
        pos = index.positions[pp.position_id]
        positions_payload.append(
            {
                "position_id": pp.position_id,
                "employer": pos.company,
                "title": pos.title,
                "max_bullets": pp.max_bullets,
                "records": _records_payload(index, pp.featured_evidence_ids, req_by_evidence),
            }
        )
    user = json.dumps(
        {
            "target_profile": strategy.target_profile,
            "recommended_title": strategy.recommended_title,
            "alternative_titles": profile_titles,
            "positioning": strategy.positioning,
            "job": {
                "role_title": job.role_title,
                "must_have": job.must_have,
                "nice_to_have": job.nice_to_have,
                "responsibilities": job.responsibilities[:12],
                "keywords": job.keywords[:25],
            },
            "requirements": reqs,
            "terms_to_introduce": strategy.terms_to_introduce[:20],
            "positions": positions_payload,
            "base_resume_style_reference": [
                bl.text for p in base.positions[:1] for bl in p.bullets[:2]
            ],
            "options": {
                "preserve_metrics": options.preserve_metrics,
                "max_pages": strategy.target_length_pages,
            },
        },
        ensure_ascii=False,
    )
    resp = llm.complete_json(SYSTEM_PROMPT, user, max_tokens=6000)
    d = resp.data or {}
    n = 0
    entries: list[ExperienceEntry] = []
    llm_pos = {str(p.get("position_id")): p for p in d.get("experience", []) if isinstance(p, dict)}
    for pp in strategy.position_plans:
        pos = index.positions[pp.position_id]
        bullets: list[Bullet] = []
        raw = llm_pos.get(pp.position_id, {}).get("bullets", [])
        for b in raw[: pp.max_bullets]:
            if not isinstance(b, dict) or not str(b.get("text", "")).strip():
                continue
            n += 1
            ev = [str(x) for x in b.get("evidence_ids", []) if isinstance(x, str | int)]
            rq = [
                str(x)
                for x in b.get("requirement_ids", [])
                if isinstance(x, str | int) and str(x) in reqs
            ]
            bullets.append(
                Bullet(
                    id=f"b{n:03d}",
                    text=str(b["text"]).strip(),
                    evidence_ids=ev,
                    bindings=_bindings(b),
                    requirement_ids=rq,
                    origin="rewritten",
                )
            )
        if not bullets:  # model skipped the position: fall back to verbatim evidence
            for eid in pp.featured_evidence_ids[: pp.max_bullets]:
                n += 1
                bullets.append(
                    Bullet(
                        id=f"b{n:03d}",
                        text=index.by_id[eid].resume_text,
                        evidence_ids=[eid],
                        requirement_ids=req_by_evidence.get(eid, [])[:4],
                        origin="fallback",
                    )
                )
        entries.append(
            ExperienceEntry(
                position_id=pos.id,
                company=pos.company,
                title=pos.title,
                start=pos.start,
                end=pos.end,
                location=pos.location,
                company_blurb=pos.company_blurb,
                bullets=bullets,
            )
        )
    summary = []
    for i, s in enumerate(d.get("summary", [])[:4]):
        if isinstance(s, dict) and str(s.get("text", "")).strip():
            summary.append(
                Bullet(
                    id=f"s{i + 1:03d}",
                    text=str(s["text"]).strip(),
                    evidence_ids=[str(x) for x in s.get("evidence_ids", [])],
                    bindings=_bindings(s),
                    origin="rewritten",
                )
            )
    skills: list[SkillGroup] = []
    for g in d.get("skills", []):
        if isinstance(g, dict) and isinstance(g.get("items"), list) and g.get("group"):
            skills.append(
                SkillGroup(
                    name=str(g["group"]), items=[str(i) for i in g["items"] if str(i).strip()]
                )
            )
    if not skills:
        skills = _skills_from_evidence(
            index, job, matches, base, _featured(strategy), _lead_ids(strategy)
        )
    headline = str(d.get("headline", "")).strip()
    if headline not in {strategy.recommended_title, *profile_titles}:
        headline = strategy.recommended_title
    res = GeneratedResume(
        candidate=index.bank.candidate.model_copy(update={"portfolio": ""}),
        headline=headline,
        summary=summary
        or deterministic_generate(job, matches, strategy, index, base, options).summary,
        experience=entries,
        skills=skills,
        education=[e for e in index.bank.education if e.include_by_default],
        certifications=select_certifications(index, job, _body_terms(index, _featured(strategy)))[
            0
        ],
        section_order=strategy.section_order,
        generation_source=f"llm:{llm.name}:{llm.model}",
    )
    return estimate_length(localize(res, options.resume_locale))


class ResumeGenerator:
    """Career Agent integration point: ``generate(...) -> GeneratedResume`` (unvalidated)."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def generate(
        self,
        job: JobAnalysis,
        matches: MatchReport,
        strategy: ResumeStrategy,
        index: EvidenceIndex,
        base: BaseResume,
        options: TailorOptions,
        profile_titles: list[str] | None = None,
        profile_descriptor: str | None = None,
    ) -> tuple[GeneratedResume, list[str]]:
        warnings: list[str] = []
        if options.use_llm and self.llm is not None and self.llm.name != "none":
            try:
                return llm_generate(
                    job, matches, strategy, index, base, options, self.llm, profile_titles or []
                ), warnings
            except LLMError as e:
                warnings.append(
                    f"generation: LLM unavailable, verbatim evidence assembly used ({e})"
                )
        return deterministic_generate(
            job, matches, strategy, index, base, options, profile_descriptor
        ), warnings

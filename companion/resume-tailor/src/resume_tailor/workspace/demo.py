# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""A completely synthetic demo candidate: Alex Morgan, Demo Systems Engineer.

Every company, project, metric, certification and document here is invented for
demonstration and public testing. The data deliberately exercises the engine's
honesty machinery: source strengths (primary case study vs portfolio-only),
corroboration links, scoped numbers, tool-level proficiency, an OPEN source
conflict awaiting review, a conflict-blocked certification, and a clause-scoped
confirmed detail — so the major product flows and public tests work from a
clean install with no private data anywhere.
"""

from __future__ import annotations

import json
from typing import Any

from resume_tailor.workspace.store import CandidateWorkspace, WorkspaceStore

DEMO_NAME = "Alex Morgan"


def demo_bank() -> dict[str, Any]:
    rec = _record
    return {
        "candidate": {
            "name": DEMO_NAME,
            "email": "alex@example.test",
            "location": "Springfield (Remote)",
            "linkedin": "linkedin.example.test/alex-morgan",
            "portfolio": "alex-morgan.example.test",
            "languages": ["English (native)", "Spanish (conversational)"],
        },
        "positions": [
            {
                "id": "harborlight_se",
                "company": "Harborlight Systems",
                "title": "Systems Engineer",
                "location": "Remote",
                "start": "2023-01",
                "end": None,
                "kind": "employment",
                "company_blurb": "Fictional logistics SaaS: subscriptions, fleet data, customer portal.",
            },
            {
                "id": "cobaltpeak_as",
                "company": "Cobalt Peak Software",
                "title": "Automation Specialist",
                "location": "Remote",
                "start": "2020-03",
                "end": "2022-12",
                "kind": "employment",
                "conflict_ids": ["conflict_demo_dates"],
                "company_blurb": "Fictional workflow-tools vendor for mid-market operations teams.",
            },
            {
                "id": "indie_lab",
                "company": "Independent",
                "title": "AI Tooling Project",
                "location": "Remote",
                "start": "2024-06",
                "end": None,
                "kind": "independent",
            },
        ],
        "records": [
            rec(
                "hl_billing_001",
                "harborlight_se",
                "Harborlight Systems",
                "Systems Engineer",
                "2023-01",
                None,
                "Built the HubSpot-to-Stripe billing sync as sole owner, reconciling 4,200 subscriptions a month.",
                project="Billing Sync",
                technologies=["HubSpot", "Stripe", "REST APIs", "Webhooks"],
                skills=["systems integration"],
                metrics=["4,200 subscriptions a month"],
                sources=["billing_case_study"],
                verification="primary",
                proficiency="led",
                number_scopes=[
                    {
                        "number": "4,200",
                        "meaning": "subscriptions reconciled monthly by the billing sync",
                        "unless_between": ["reconciling"],
                    }
                ],
            ),
            rec(
                "hl_billing_002",
                "harborlight_se",
                "Harborlight Systems",
                "Systems Engineer",
                "2023-01",
                None,
                "Added monitoring and alerting for the billing sync, cutting unnoticed failures to zero.",
                project="Billing Sync",
                technologies=["n8n"],
                skills=["production monitoring", "troubleshooting"],
                sources=["billing_case_study"],
                verification="primary",
            ),
            rec(
                "hl_dash_001",
                "harborlight_se",
                "Harborlight Systems",
                "Systems Engineer",
                "2023-01",
                None,
                "Built the operations dashboard in Power BI over SQL models covering 18 fleet metrics.",
                project="Ops Dashboard",
                technologies=["Power BI", "SQL"],
                skills=["data modeling", "dashboards"],
                metrics=["18 fleet metrics"],
                sources=["demo_resume"],
                verification="derived",
            ),
            rec(
                "hl_dash_002",
                "harborlight_se",
                "Harborlight Systems",
                "Systems Engineer",
                "2023-01",
                None,
                "Automated weekly reporting with Python scripts feeding the dashboard.",
                project="Ops Dashboard",
                technologies=["Python", "SQL"],
                skills=["automation"],
                sources=["demo_resume", "linkedin_profile"],
                verification="derived",
            ),
            rec(
                "hl_general_001",
                "harborlight_se",
                "Harborlight Systems",
                "Systems Engineer",
                "2023-01",
                None,
                "Sole owner of the automation layer connecting HubSpot, Stripe and n8n, within a wider estate of 60 active workflows.",
                technologies=["HubSpot", "Stripe", "n8n"],
                skills=["systems integration", "production monitoring"],
                sources=["demo_resume", "user_verified"],
                verification="user_verified",
                metrics=["60 active workflows (estate size, not workflows owned)"],
                number_scopes=[
                    {
                        "number": "60",
                        "meaning": "active workflows in the wider estate, not all owned",
                        "not_near": ["owned", "built"],
                    }
                ],
                corroborated_by=["hl_billing_001", "hl_billing_002"],
            ),
            rec(
                "cp_flows_001",
                "cobaltpeak_as",
                "Cobalt Peak Software",
                "Automation Specialist",
                "2020-03",
                "2022-12",
                "Delivered client workflow automations with Zapier and internal tools across 30 accounts.",
                technologies=["Zapier"],
                skills=["workflow automation", "client enablement"],
                metrics=["30 accounts"],
                sources=["demo_resume"],
                verification="derived",
                conflict_ids=["conflict_demo_dates"],
            ),
            rec(
                "cp_flows_002",
                "cobaltpeak_as",
                "Cobalt Peak Software",
                "Automation Specialist",
                "2020-03",
                "2022-12",
                "Integrated client CRMs with billing tools through REST APIs and webhooks.",
                technologies=["REST APIs", "Webhooks", "HubSpot"],
                skills=["systems integration"],
                sources=["demo_resume"],
                verification="derived",
                tool_proficiency={"HubSpot": "integration"},
            ),
            rec(
                "cp_support_001",
                "cobaltpeak_as",
                "Cobalt Peak Software",
                "Automation Specialist",
                "2020-03",
                "2022-12",
                "Ran technical discovery and requirements gathering with operations stakeholders.",
                skills=["requirements gathering", "technical discovery", "stakeholder management"],
                sources=["demo_resume"],
                verification="derived",
            ),
            rec(
                "cp_tableau_001",
                "cobaltpeak_as",
                "Cobalt Peak Software",
                "Automation Specialist",
                "2020-03",
                "2022-12",
                "Evaluated Tableau in a two-week proof of concept for a client reporting request.",
                technologies=["Tableau"],
                sources=["demo_resume"],
                verification="derived",
                proficiency="exposure",
            ),
            rec(
                "il_agent_001",
                "indie_lab",
                "Independent",
                "AI Tooling Project",
                "2024-06",
                None,
                "Building a local-first AI assistant with Python, LLMs, RAG and AI agents over a small tool registry.",
                technologies=["Python", "LLM", "RAG", "AI agents"],
                skills=["ai automation"],
                sources=["portfolio"],
                verification="derived",
            ),
            rec(
                "il_agent_002",
                "indie_lab",
                "Independent",
                "AI Tooling Project",
                "2024-06",
                None,
                "Designed evaluation checks that score assistant answers against source documents.",
                technologies=["LLM"],
                skills=["llm evaluation", "testing"],
                sources=["portfolio"],
                verification="derived",
            ),
            # a detail with one source and no disagreement that was set aside from resumes:
            # it needs the candidate's say-so, not a conflict resolution
            rec(
                "cp_training_001",
                "cobaltpeak_as",
                "Cobalt Peak Software",
                "Automation Specialist",
                "2020-03",
                "2022-12",
                "Ran onboarding sessions for new client admins on the workflow tooling.",
                skills=["training", "client onboarding"],
                sources=["linkedin_profile"],
                verification="derived",
                include_by_default=False,
            ),
        ],
        "sources": {
            "demo_resume": "sources/files/demo_resume.txt",
            "linkedin_profile": "sources/files/linkedin_profile.txt",
            "billing_case_study": "sources/files/billing_sync_case_study.md",
            "portfolio": "alex-morgan.example.test",
        },
        "certifications": [
            {"id": "cert_flowstack", "issuer": "FlowStack", "name": "Automation Professional"},
            {
                "id": "cert_cloudline",
                "issuer": "Cloudline",
                "name": "Integration Developer",
                "conflict_ids": ["conflict_demo_cert"],
            },
            {"id": "cert_codeworks", "issuer": "Codeworks", "name": "SQL Basics"},
        ],
        "conflicts": [
            {
                "id": "conflict_demo_dates",
                "topic": "Cobalt Peak end date",
                "statements": [
                    {
                        "source": "demo_resume",
                        "statement": "Automation Specialist, Cobalt Peak Software — Current",
                    },
                    {
                        "source": "linkedin_profile",
                        "statement": "Automation Specialist, Cobalt Peak Software — ended December 2022",
                    },
                ],
            },
            {
                "id": "conflict_demo_cert",
                "topic": "Cloudline certification level",
                "statements": [
                    {
                        "source": "demo_resume",
                        "statement": "Cloudline Integration Developer (Advanced)",
                    },
                    {
                        "source": "linkedin_profile",
                        "statement": "Cloudline Integration Developer (Foundation)",
                    },
                ],
            },
        ],
        "overrides": [],
    }


def demo_overrides() -> dict[str, Any]:
    return {
        "note": "Facts the demo candidate confirmed. Applied in memory at load time.",
        "overrides": [
            {
                "id": "demo_override_001",
                "topic": "Automation layer ownership scope",
                "applies_to": "record",
                "target_id": "hl_general_001",
                "record_verification": {"hl_general_001": "user_verified"},
                "verified_clauses": [
                    "sole owner of the automation layer connecting HubSpot, Stripe and n8n",
                    "60 = active workflows in the wider estate, not workflows owned",
                ],
                "confirmed_at": "2026-01-15",
            },
        ],
    }


def demo_profiles() -> dict[str, Any]:
    return {
        "systems_automation": {
            "name": "Systems & Automation",
            "titles": [
                "Systems & Automation Engineer",
                "Business Systems Engineer",
                "Automation Engineer",
            ],
            "title_cues": ["systems engineer", "automation engineer", "business systems"],
            "cues": [
                "automation",
                "integration",
                "hubspot",
                "stripe",
                "workflows",
                "internal tools",
            ],
            "terms": ["hubspot", "stripe", "n8n", "zapier", "rest apis", "webhooks"],
            "descriptor": "Systems engineer focused on reliable integrations and automation.",
            "portfolio": True,
        },
        "ai_engineering": {
            "name": "AI Engineering",
            "titles": ["AI Automation Engineer", "AI Tooling Engineer"],
            "title_cues": ["ai engineer", "ai automation"],
            "cues": ["ai", "llm", "rag", "agents", "evaluation"],
            "terms": ["python", "llm", "rag", "ai agents"],
            "descriptor": "Engineer building dependable AI-assisted tooling.",
            "portfolio": True,
        },
    }


def demo_base_resume() -> dict[str, Any]:
    return {
        "id": "demo_systems_engineer",
        "name": "Systems Engineer",
        "headline": "Systems & Automation Engineer",
        "summary": [
            {
                "text": "Systems engineer who keeps integrations, billing data and automations reliable, with a growing AI tooling practice.",
                "evidence_ids": [],
            }
        ],
        "positions": [],
        "skills": [
            {"name": "Systems", "items": ["HubSpot", "Stripe", "n8n", "Zapier"]},
            {"name": "Data & Code", "items": ["Python", "SQL", "Power BI", "REST APIs"]},
        ],
        "source_file": "demo",
    }


def _record(
    rid: str,
    position_id: str,
    company: str,
    role: str,
    start: str,
    end: str | None,
    claim: str,
    project: str | None = None,
    technologies: list[str] | None = None,
    skills: list[str] | None = None,
    metrics: list[str] | None = None,
    sources: list[str] | None = None,
    verification: str = "derived",
    proficiency: str = "hands_on",
    tool_proficiency: dict[str, str] | None = None,
    corroborated_by: list[str] | None = None,
    number_scopes: list[dict] | None = None,
    conflict_ids: list[str] | None = None,
    include_by_default: bool = True,
) -> dict[str, Any]:
    return {
        "id": rid,
        "position_id": position_id,
        "company": company,
        "role": role,
        "start": start,
        "end": end,
        "project": project,
        "claim": claim,
        "resume_text": claim,
        "technologies": technologies or [],
        "skills": skills or [],
        "metrics": metrics or [],
        "proficiency": proficiency,
        "tool_proficiency": tool_proficiency or {},
        "sources": sources or ["demo_resume"],
        "source_file": (sources or ["demo_resume"])[0],
        "verification": verification,
        "corroborated_by": corroborated_by or [],
        "number_scopes": number_scopes or [],
        "conflict_ids": conflict_ids or [],
        "include_by_default": include_by_default,
    }


DEMO_SOURCE_FILES = {
    "demo_resume.txt": (
        "Alex Morgan\nSystems & Automation Engineer\n\nSummary\n"
        "Systems engineer focused on reliable integrations, billing data and automation.\n\n"
        "Experience\nSystems Engineer — Harborlight Systems (2023 - present)\n"
        "- Built the HubSpot-to-Stripe billing sync reconciling 4,200 subscriptions a month.\n"
        "- Built the operations dashboard in Power BI over SQL models.\n"
        "Automation Specialist — Cobalt Peak Software (2020 - 2022)\n"
        "- Delivered client workflow automations with Zapier across 30 accounts.\n\n"
        "Skills\nHubSpot, Stripe, n8n, Zapier, Python, SQL, Power BI, REST APIs\n"
    ),
    "linkedin_profile.txt": (
        "Alex Morgan — Systems Engineer at Harborlight Systems\n"
        "Previously Automation Specialist at Cobalt Peak Software (ended December 2022).\n"
    ),
    "billing_sync_case_study.md": (
        "# Billing Sync case study (fictional)\n\n"
        "Alex Morgan designed, built and owned the HubSpot-to-Stripe billing sync at\n"
        "Harborlight Systems, reconciling 4,200 subscriptions a month with monitoring\n"
        "and alerting that cut unnoticed failures to zero.\n"
    ),
}


def create_demo_candidate(store: WorkspaceStore) -> CandidateWorkspace:
    for meta in store.list_candidates(include_archived=True):
        if meta.get("name") == DEMO_NAME and meta.get("demo"):
            return store.get(meta["id"])
    bank = demo_bank()
    ws = store.create(
        DEMO_NAME,
        email=bank["candidate"]["email"],
        location=bank["candidate"]["location"],
        linkedin=bank["candidate"]["linkedin"],
        portfolio=bank["candidate"]["portfolio"],
        languages=bank["candidate"]["languages"],
    )
    ws.write_evidence(bank)
    ws.write_overrides(demo_overrides())
    ws.profiles_file.write_text(
        json.dumps(demo_profiles(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    (ws.root / "base_resumes" / "demo_systems_engineer.json").write_text(
        json.dumps(demo_base_resume(), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    files = ws.root / "sources" / "files"
    files.mkdir(parents=True, exist_ok=True)
    for name, content in DEMO_SOURCE_FILES.items():
        (files / name).write_text(content, encoding="utf-8")
    registry = [
        {
            "id": "demo_resume",
            "name": "Demo resume",
            "kind": "resume",
            "file": "files/demo_resume.txt",
            "added_at": "2026-01-10T00:00:00+00:00",
            "detail_count": 6,
        },
        {
            "id": "linkedin_profile",
            "name": "LinkedIn profile",
            "kind": "profile",
            "file": "files/linkedin_profile.txt",
            "added_at": "2026-01-10T00:00:00+00:00",
            "detail_count": 1,
        },
        {
            "id": "billing_case_study",
            "name": "Billing Sync case study",
            "kind": "case_study",
            "file": "files/billing_sync_case_study.md",
            "added_at": "2026-01-12T00:00:00+00:00",
            "detail_count": 2,
        },
        {
            "id": "portfolio",
            "name": "Portfolio",
            "kind": "portfolio",
            "file": "alex-morgan.example.test",
            "added_at": "2026-01-12T00:00:00+00:00",
            "detail_count": 2,
        },
    ]
    ws.sources_registry.write_text(
        json.dumps(registry, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    # one extracted detail still waiting for review: its start month is missing, so adding
    # it asks the candidate for that one fact first
    (ws.root / "sources" / "linkedin_profile.suggestions.json").write_text(
        json.dumps(
            [
                {
                    "n": 0,
                    "text": "Mentored two junior automation specialists on client workflow builds.",
                    "company": "Cobalt Peak Software",
                    "title": "Automation Specialist",
                    "start": "",
                    "end": "2022-12",
                    "state": "pending",
                },
            ],
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    ws.invalidate()
    meta = ws.meta()
    meta["demo"] = True
    ws.save_meta(meta)
    settings = ws.settings()
    settings["default_resume_id"] = "demo_systems_engineer"
    ws.save_settings(settings)
    return ws


DEMO_JD = """Automation Engineer, Internal Platforms

About the role
We keep our billing and customer systems reliable and automated.

What you'll do
Own and improve integrations between our CRM and billing systems.
Build monitoring so failures never go unnoticed.
Automate reporting for the operations team.

Requirements
Experience integrating HubSpot or a similar CRM with billing tools.
Strong SQL and reporting skills.
Experience building automations with n8n, Zapier, or similar tools.
Experience with LLM-powered tooling is a plus.

Preferred qualifications
Experience with Salesforce Flows.
Tableau certification.
"""

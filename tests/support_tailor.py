"""Synthetic people and job ads for Tailor V2 (PR 8). Nobody's real career.

`senior(conn)` builds a five-role Career Profile with confirmed statements,
tools, skills, a certification and metrics, makes the Master from it, and then
REMOVES a few confirmed lines from the Master, so the profile has evidence the
Master does not show. `thin(conn)` is a first job and a skill. The ads carry
required and preferred asks, responsibilities, named tools the person has and
does not have, a certification gap, attractive numbers and a senior title.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from tests.integration.test_resume_master import claim, experience

from career_agent.domain.enums import ClaimType
from career_agent.resume_doc.master import get_or_create_master
from career_agent.resume_doc.store import ResumeStore
from career_agent.storage.db import transaction

E, S, T, C = ClaimType.EMPLOYMENT, ClaimType.SKILL, ClaimType.TOOL, ClaimType.CERTIFICATION

#: Confirmed statements left OUT of the Master: the Tailor may add them back.
NOT_IN_MASTER = ("k-n8n-flows", "k-hubspot-workflows", "k-sql-models")


def senior(conn: sqlite3.Connection) -> dict[str, Any]:
    with transaction(conn):
        roles = [
            experience(
                conn,
                "Northwind Systems",
                "Revenue Operations Analyst",
                "2022-03",
                None,
                current=True,
            ),
            experience(conn, "Contoso Labs", "Sales Operations Specialist", "2019-01", "2022-02"),
            experience(conn, "Fabrikam Retail", "CRM Coordinator", "2016-06", "2018-12"),
            experience(
                conn, "Tailspin Media", "Marketing Operations Analyst", "2014-02", "2016-05"
            ),
            experience(conn, "Adventure Works", "Business Analyst", "2011-08", "2014-01"),
        ]
        statements = [
            (
                "k-hubspot-routing",
                roles[0],
                "Built lead routing in HubSpot for 4 regional teams.",
                ["HubSpot"],
            ),
            (
                "k-hubspot-workflows",
                roles[0],
                "Built HubSpot workflow automation that cut manual data entry by 30%.",
                ["HubSpot"],
            ),
            (
                "k-n8n-flows",
                roles[0],
                "I built n8n integrations between HubSpot and the billing system.",
                ["n8n", "HubSpot"],
            ),
            (
                "k-forecast",
                roles[0],
                "Owned the weekly pipeline reporting for sales leadership.",
                [],
            ),
            (
                "k-sql-models",
                roles[1],
                "Wrote SQL models for revenue reporting in BigQuery.",
                ["SQL", "BigQuery"],
            ),
            (
                "k-territories",
                roles[1],
                "Redesigned sales territories for 30 account executives.",
                [],
            ),
            ("k-crm-cleanup", roles[2], "Cleaned up 120,000 CRM records before a migration.", []),
            ("k-campaign-ops", roles[3], "Ran campaign operations for 12 product launches.", []),
            ("k-requirements", roles[4], "Gathered requirements for internal reporting tools.", []),
        ]
        for key, role, text, tools in statements:
            claim(conn, key, E, text, experience=role, tools=tools)
        claim(conn, "k-skill-sql", S, "SQL")
        claim(conn, "k-tool-hubspot", T, "HubSpot")
        claim(conn, "k-cert-hubspot", C, "HubSpot Revenue Operations Certification")
        claim(conn, "k-draft", E, "Led a Salesforce Apex rewrite.", experience=roles[0],
              verified=False)  # fmt: skip
    master, _ = get_or_create_master(conn)
    store = ResumeStore(conn)
    data = master.working.model_dump(mode="json")
    for entry in data["experience"]:
        entry["bullets"] = [
            b for b in entry["bullets"] if not set(b["evidence_ids"]) & set(NOT_IN_MASTER)
        ]
    data["identity"]["full_name"] = "Robin Synthetic"
    from career_agent.resume_doc.models import upgrade_resume_document

    store.save_working_copy(
        master.id, upgrade_resume_document(data), expected_sha256=master.working_sha256
    )
    store.checkpoint_revision(master.id, "MANUAL_CHECKPOINT")
    return {"master_id": master.id, "roles": roles}


def thin(conn: sqlite3.Connection) -> dict[str, Any]:
    with transaction(conn):
        role = experience(conn, "Exemplo Digital", "Assistente Administrativo", "2024-02", None,
                          current=True)  # fmt: skip
        claim(
            conn, "k-planilhas", E, "Organizei as planilhas de pedidos da equipe.", experience=role
        )
        claim(conn, "k-excel", T, "Excel")
    master, _ = get_or_create_master(conn)
    return {"master_id": master.id, "roles": [role]}


SENIOR_AD = """Director of Revenue Operations

About us
We are a fast-growing company that has raised $50M. We value curious people.

Responsibilities
- Build workflow automation across HubSpot and Salesforce
- Own pipeline reporting for sales leadership
- Manage a $10M pipeline and reduce churn 20%

Requirements
- 10+ years of revenue operations experience
- Experience with HubSpot
- HubSpot experience required
- Strong SQL skills
- Salesforce Apex is a must
- Experience building integrations with n8n
- Fluent English

Nice to have
- Workato certification
- Experience with dbt
- Must be authorized to work in the United States

Benefits
- Health insurance
"""

PT_AD = (
    "Especialista Sênior em Operações de Receita. Somos uma empresa de tecnologia em crescimento."
    " Responsabilidades: Construir automações de processos comerciais no HubSpot; Criar relatórios"
    " de pipeline para a liderança de vendas; Integrar o CRM com o sistema de cobrança."
    " Requisitos: Experiência com HubSpot e n8n; Conhecimento em SQL; Inglês avançado;"
    " Experiência com Salesforce é obrigatória. Diferenciais: Certificação Workato;"
    " Experiência com Power BI."
)

MIXED_AD = """Vaga: Revenue Operations Specialist (remoto)

O que você vai fazer
- Build HubSpot workflows e automações de processo
- Manter os modelos SQL de receita

Requisitos
- Experiência com n8n ou Zapier
- Strong communication in English
"""

NUMBERS_AD = """Requirements
- Manage a $10M pipeline
- Reduce churn 20% year over year
- 10+ years of experience in revenue operations
"""

TOOLS_AD = """Requirements
- Salesforce administration
- Apex development
- Workato recipes
- dbt models
- HubSpot workflows
- n8n integrations
"""

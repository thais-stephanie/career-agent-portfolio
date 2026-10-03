"""Synthetic ResumeDocuments for renderer, preview and editor tests.

Invented people and employers only. Ids are derived from a name, so the same
fixture always renders the same bytes (golden files depend on it).
"""

from __future__ import annotations

from typing import Any

from career_agent.resume_doc.legacy import stable_id
from career_agent.resume_doc.models import ResumeDocument, upgrade_resume_document


def rid(name: str) -> str:
    return stable_id("fixture", name)


def _bullets(prefix: str, texts: list[str], origin: str = "EVIDENCE_VERBATIM") -> list[dict]:
    return [
        {
            "id": rid(f"{prefix}-b{n}"),
            "text": text,
            "origin": origin,
            "evidence_ids": [f"claim-{prefix}-{n}"] if origin != "USER_AUTHORED" else [],
        }
        for n, text in enumerate(texts)
    ]


ROLES: list[tuple[str, str, str, dict, dict | None, list[str]]] = [
    (
        "northwind",
        "Northwind Systems",
        "Principal Automation Engineer",
        {"year": 2021, "month": 4},
        None,
        [
            "Designed the integration platform joining billing, CRM and support for 40 teams.",
            "Replaced nightly batch jobs with event-driven workflows, cutting delay to minutes.",
            "Set the review standard for automation changes and mentored six engineers through it.",
            "Led the incident review practice and wrote the runbooks the on-call rota uses.",
        ],
    ),
    (
        "contoso",
        "Contoso Automation",
        "Senior Systems Engineer",
        {"year": 2017, "month": 2},
        {"year": 2021, "month": 3},
        [
            "Built **reconciliation** jobs that matched payments across three ledgers every night.",
            "Moved 120 scheduled scripts into a monitored orchestration service.",
            "Wrote the data contracts between finance and operations systems.",
        ],
    ),
    (
        "fabrikam",
        "Fabrikam Labs",
        "Systems Analyst",
        {"year": 2013, "month": 9},
        {"year": 2017, "month": 1},
        [
            "Mapped order-to-cash processes and documented every system hand-off.",
            "Automated weekly reporting for the finance team.",
        ],
    ),
]


def rich(**design: Any) -> ResumeDocument:
    """A senior, multi-role systems and automation career (invented)."""
    return upgrade_resume_document(
        {
            "schema_version": "1.0",
            "id": rid("rich"),
            "kind": "MASTER",
            "title": "Master resume",
            "language": "en",
            "identity": {
                "full_name": "Morgan Example",
                "email": "morgan@example.invalid",
                "phone": "+1 555 0100",
                "city": "Springfield",
                "country": "US",
                "links": [
                    {
                        "id": rid("link-li"),
                        "kind": "LINKEDIN",
                        "url": "https://www.linkedin.example/in/morgan-example",
                    },
                    {
                        "id": rid("link-gh"),
                        "kind": "GITHUB",
                        "url": "https://github.example/morgan-example",
                    },
                ],
            },
            "headline": {
                "id": rid("headline"),
                "text": "Automation and integration engineer",
                "origin": "USER_AUTHORED",
            },
            "summary": {
                "id": rid("summary"),
                "text": "Engineer who connects business systems so nobody copies data by hand.",
                "origin": "USER_AUTHORED",
            },
            "experience": [
                {
                    "id": rid(key),
                    "employer": employer,
                    "display_title": title,
                    "source_title": title,
                    "location": "Remote",
                    "start": start,
                    "end": end,
                    "current": end is None,
                    "bullets": _bullets(key, texts),
                }
                for key, employer, title, start, end, texts in ROLES
            ],
            "projects": [
                {
                    "id": rid("project"),
                    "name": "Open data connector",
                    "role": "Maintainer",
                    "url": "https://example.invalid/connector",
                    "bullets": _bullets("project", ["Kept a small open source connector working."]),
                }
            ],
            "education": [
                {
                    "id": rid("edu"),
                    "institution": "Example State University",
                    "degree": "BSc",
                    "field_of_study": "Information Systems",
                    "start": {"year": 2009},
                    "end": {"year": 2013},
                }
            ],
            "certifications": [
                {
                    "id": rid("cert"),
                    "name": "Synthetic Cloud Practitioner",
                    "issuer": "Example Board",
                    "issued": {"year": 2022, "month": 6},
                },  # fmt: skip
            ],
            "skills": [
                {
                    "id": rid("skills"),
                    "name": "Tools",
                    "items": [
                        {"id": rid(f"skill-{s}"), "label": s, "origin": "USER_AUTHORED"}
                        for s in ("Python", "SQL", "REST APIs", "Event streams")
                    ],
                }
            ],
            "design": design,
            "provenance": {"created_from": "SCRATCH"},
        }
    )


def sparse(**design: Any) -> ResumeDocument:
    """A first draft: a name, one job, no contact details."""
    return upgrade_resume_document(
        {
            "schema_version": "1.0",
            "id": rid("sparse"),
            "kind": "SCRATCH",
            "title": "Draft",
            "language": "pt-BR",
            "identity": {"full_name": "Alex Exemplo"},
            "experience": [
                {
                    "id": rid("sparse-job"),
                    "employer": "Exemplo Digital",
                    "display_title": "Analista",
                    "start": {"year": 2024, "month": 2},
                    "current": True,
                    "bullets": _bullets(
                        "sparse", ["Organizei os relatórios semanais."], "USER_AUTHORED"
                    ),
                }
            ],
            "design": design,
            "provenance": {"created_from": "SCRATCH"},
        }
    )


LONG_BULLET = " ".join(
    f"Coordinated release {n} across finance, operations and support, writing the plan,"
    " the checklist and the rollback note each team followed."
    for n in range(1, 9)
)


def long(**design: Any) -> ResumeDocument:
    """Three pages and more: many roles, a very long bullet, a long title, a
    long skills list."""
    roles = []
    for n in range(16):
        texts = [
            f"Delivered synthetic outcome {n}.{k} for an invented team of analysts."
            for k in range(6)
        ]
        if n == 2:
            texts.insert(1, LONG_BULLET)
        roles.append(
            {
                "id": rid(f"long-{n}"),
                "employer": f"Invented Company {n}",
                "display_title": (
                    "Senior Principal Staff Automation, Integration and Reliability Engineer"
                    if n == 4
                    else f"Engineer {n}"
                ),
                "start": {"year": 2000 + n, "month": 1},
                "end": {"year": 2001 + n, "month": 1},
                "bullets": _bullets(f"long{n}", texts),
            }
        )
    return upgrade_resume_document(
        {
            "schema_version": "1.0",
            "id": rid("long"),
            "kind": "SCRATCH",
            "title": "Long",
            "language": "en",
            "identity": {"full_name": "Jordan Longform"},
            "experience": roles,
            "skills": [
                {
                    "id": rid("long-skills"),
                    "name": "Skills",
                    "items": [
                        {
                            "id": rid(f"long-skill-{n}"),
                            "label": f"Skill number {n}",
                            "origin": "USER_AUTHORED",
                        }
                        for n in range(80)
                    ],
                }
            ],
            "design": design,
            "provenance": {"created_from": "SCRATCH"},
        }
    )


def confirm_cited(conn: Any, *docs: ResumeDocument) -> None:
    """Confirm, as synthetic Career Evidence of this database's candidate,
    every claim the documents cite: their lines then pass the evidence check
    every write and export makes."""
    from career_agent.domain.claims import VerifiedClaim
    from career_agent.domain.enums import ClaimSource, ClaimType
    from career_agent.resume_doc.evidence import _cited
    from career_agent.storage.repositories import ClaimRepo
    from career_agent.storage.workspace_repo import ensure_candidate

    candidate = ensure_candidate(conn)
    repo = ClaimRepo(conn)
    keys = sorted({key for doc in docs for _, ids in _cited(doc) for key in ids})
    for key in keys:
        if repo.current_row(candidate, key) is None:
            repo.add(
                candidate,
                VerifiedClaim(
                    claim_key=key,
                    claim_type=ClaimType.EMPLOYMENT,
                    text=f"Synthetic evidence {key}",
                    source=ClaimSource.SELF_ATTESTED,
                    verified=True,
                ),
            )


#: Words only a HIDDEN line or section holds: never in a PDF or DOCX.
HIDDEN_LINE = "Quietly archived the legacy fax gateway nobody remembers."
HIDDEN_PROJECT = "Shelved prototype exporter"


def exportable(**design: Any) -> ResumeDocument:
    """The rich career with what an export must get right: accents, a
    portfolio link, a line typed by the person, a hidden line and a hidden
    section (kept in the document, absent from every rendered file)."""
    data = rich(**design).model_dump(mode="json")
    data["identity"]["full_name"] = "Morgan Conceição Exemplo"
    data["identity"]["links"].append(
        {"id": rid("link-pf"), "kind": "PORTFOLIO", "url": "https://portfolio.example/morgan"}
    )
    role = data["experience"][0]
    role["bullets"][1]["hidden"] = True
    role["bullets"][1]["text"] = HIDDEN_LINE
    role["bullets"].append(
        {
            "id": rid("typed"),
            "text": "Mentorei a equipe de integração em São Paulo, reduzindo 35% do retrabalho.",
            "origin": "USER_AUTHORED",
        }
    )
    data["projects"].append({"id": rid("shelved"), "name": HIDDEN_PROJECT, "bullets": []})
    data["layout"] = {"hidden_sections": ["projects"]}
    return upgrade_resume_document(data)

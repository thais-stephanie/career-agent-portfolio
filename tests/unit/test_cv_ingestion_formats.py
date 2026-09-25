"""Three layouts of one career, one canonical reading.

Career material arrives as a conventional resume, a LinkedIn-like export and a
Markdown master file with nested headings and technology tables. The same
person, the same skills: this pins that all three normalise to the same
skills, the same job and the same certificate, while each skill keeps the
context the document gave it and the line it literally came from.

All three fixtures are synthetic (`tests/fixtures/cv/format_*`).
"""

from __future__ import annotations

import pytest
from tests.support_cv import load_cv

from career_agent.cv.certifications import read_certificate
from career_agent.cv.propose import read_cv
from career_agent.domain.enums import ClaimType

FORMATS = (
    "format_conventional_resume.txt",
    "format_linkedin_like_export.txt",
    "format_master_career_file.md",
)
CANONICAL = {"HubSpot", "n8n", "Python", "SQL", "Workato", "Salesforce", "CI/CD"}
SKILLISH = {ClaimType.SKILL, ClaimType.TOOL}


def skill_proposals(name: str):
    return [p for p in read_cv(load_cv(name)).proposals if p.claim_type in SKILLISH]


@pytest.mark.parametrize("name", FORMATS)
def test_every_layout_yields_the_same_skills(name: str) -> None:
    assert {p.text for p in skill_proposals(name)} == CANONICAL


@pytest.mark.parametrize("name", FORMATS)
def test_every_layout_yields_the_same_job(name: str) -> None:
    entries = read_cv(load_cv(name)).entries
    assert [(e.company, e.role) for e in entries] == [("Contoso Ltd", "Operations Engineer")]


@pytest.mark.parametrize("name", FORMATS)
def test_every_layout_yields_the_same_certificate(name: str) -> None:
    (cert,) = [
        p for p in read_cv(load_cv(name)).proposals if p.claim_type is ClaimType.CERTIFICATION
    ]
    read = read_certificate(cert.text)
    assert read is not None
    assert (read.title, read.issuer, read.issued) == (
        "Certified Administrator",
        "Salesforce",
        "2026-09",
    )


@pytest.mark.parametrize("name", FORMATS)
def test_every_skill_keeps_the_line_it_literally_came_from(name: str) -> None:
    for proposal in skill_proposals(name):
        assert proposal.source_line is not None
        assert proposal.source_text and proposal.text in proposal.source_text, proposal
        assert proposal.evidence and proposal.text in proposal.evidence


def test_the_contexts_the_documents_state_are_kept_apart() -> None:
    def contexts(name: str) -> dict[str, tuple[str, bool]]:
        return {p.text: (p.section, p.entry_key is not None) for p in skill_proposals(name)}

    # A conventional resume: one global list.
    assert set(contexts("format_conventional_resume.txt").values()) == {("skills", False)}
    linkedin = contexts("format_linkedin_like_export.txt")
    assert linkedin["HubSpot"] == ("skills", False)  # Top Skills: global
    assert linkedin["Workato"] == ("skills", True)  # the role's Skills & Tools
    assert linkedin["CI/CD"] == ("keywords", True)  # the role's Keywords
    master = contexts("format_master_career_file.md")
    assert master["Salesforce"] == ("skills", True)  # inline under the role
    assert master["n8n"] == ("matrix", False)  # a technology table row


def test_a_technology_table_keeps_its_other_columns_as_context() -> None:
    (n8n,) = [p for p in skill_proposals("format_master_career_file.md") if p.text == "n8n"]
    assert "Built & owned" in n8n.evidence and "Billing pipeline" in n8n.evidence


def test_table_header_rows_are_structure_not_claims() -> None:
    texts = [p.text for p in read_cv(load_cv("format_master_career_file.md")).proposals]
    assert not any(t.startswith(("Technology", "Certification | Issuer")) for t in texts), texts


def test_a_certification_is_never_mined_for_skills() -> None:
    text = (
        "Riley\n\nSKILLS\nPython\n\nCERTIFICATIONS\n"
        "AWS Certified Developer | Amazon | 2024\nSkills: Lambda, DynamoDB\n"
        "\nEXPERIENCE\nAnalyst, Contoso, Jan 2020 - Dec 2021\n- Built reports.\n"
    )
    skills = {p.text for p in read_cv(text).proposals if p.claim_type in SKILLISH}
    assert skills == {"Python"}, "certified-only technology became a skill"


def test_a_slash_never_splits_a_skill() -> None:
    text = (
        "Riley\n\nSKILLS\nCI/CD, REST/GraphQL; AS-IS / TO-BE\n"
        "\nEXPERIENCE\nAnalyst, Contoso, Jan 2020 - Dec 2021\n- Built reports.\n"
    )
    skills = [p.text for p in read_cv(text).proposals if p.claim_type in SKILLISH]
    assert skills == ["CI/CD", "REST/GraphQL", "AS-IS / TO-BE"]


def test_a_sentence_with_a_colon_under_a_job_stays_a_sentence() -> None:
    text = (
        "Riley\n\nEXPERIENCE\nAnalyst, Contoso, Jan 2020 - Dec 2021\n"
        "- Result: cut the month-end close from ten days to four.\n"
    )
    (only,) = read_cv(text).proposals
    assert only.claim_type is ClaimType.EMPLOYMENT


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("Google: Data Analytics Certificate", ("Data Analytics Certificate", "Google", None)),
        ("Data Analytics Certificate (Sep 2021)", ("Data Analytics Certificate", None, "2021-09")),
        ("Data Analytics Certificate, 2021", ("Data Analytics Certificate", None, "2021")),
        ("Inbound | HubSpot Academy | HB-2024-77", ("Inbound", "HubSpot Academy", None)),
    ],
)
def test_certificates_written_without_a_table(line: str, expected: tuple) -> None:
    read = read_certificate(line)
    assert read is not None and (read.title, read.issuer, read.issued) == expected

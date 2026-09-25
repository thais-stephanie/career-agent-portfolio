"""Ordinary text must not become skills or certificates.

The adversarial review of the heterogeneous skill reader (2026-09-25) found
that a bare generic line inside a job ("Technology", a department) opened a
tools section and turned the job's role and bullets into tool proposals; that
a technology table's "Certified only" could be flattened into a plain skill;
that a spaced hyphen split "Boomi - Certified only" into two skills; and that
certificate display could structure prose as an issuer. Each case is pinned
here. Structure must establish a skills section before a skill is read.
"""

from __future__ import annotations

import pytest

from career_agent.cv.certifications import read_certificate
from career_agent.cv.propose import read_cv
from career_agent.domain.enums import ClaimType

SKILLISH = {ClaimType.SKILL, ClaimType.TOOL}
JOB = "EXPERIENCE\nAnalyst, Contoso, Jan 2020 - Dec 2021\n"


def skill_texts(text: str) -> list[str]:
    return [p.text for p in read_cv(text).proposals if p.claim_type in SKILLISH]


# =========================================================================
# 1. a generic word never steals a job
# =========================================================================


@pytest.mark.parametrize(
    "word",
    [
        "Technology",
        "Software",
        "Systems",
        "Platforms",
        "Stack",
        "Toolkit",
        "Expertise",
        "Capabilities",
        "Core Systems",
        "Key Platforms",
    ],
)
def test_a_generic_word_inside_a_job_stays_part_of_the_job(word: str) -> None:
    text = (
        "Riley\n\nEXPERIENCE\nContoso Ltd\n"
        f"{word}\n"
        "Senior Analyst\nJan 2020 - Dec 2021\n"
        "- Built the pipeline, cutting latency in half.\n"
        "- Led the migration to the new platform.\n"
    )
    proposals = read_cv(text).proposals
    assert not [p for p in proposals if p.claim_type in SKILLISH], (word, proposals)
    bullets = {p.text for p in proposals if p.entry_key is not None}
    assert bullets == {
        "Built the pipeline, cutting latency in half.",
        "Led the migration to the new platform.",
    }, (word, bullets)


def test_explicitly_marked_sections_still_read() -> None:
    text = (
        "# Riley\n\n# Technical Skills\nPython, SQL\n\n## Tools & Platforms\nn8n\n\n"
        "Top Skills\nHubSpot\n\n" + JOB + "- Built reports.\n"
        "- Skills & Tools: Workato, dbt\n- Keywords: Process Improvement\n"
    )
    assert skill_texts(text) == [
        "Python",
        "SQL",
        "n8n",
        "HubSpot",
        "Workato",
        "dbt",
        "Process Improvement",
    ]


# =========================================================================
# 2. a label must be followed by a list, not a sentence
# =========================================================================


@pytest.mark.parametrize(
    "line",
    [
        "Skills improved through mentoring.",
        "Keywords were analyzed: churn, retention",
        "Systems: migrated the billing system to the new vendor",
        "Worked with Finance and Operations using Python and SQL.",
    ],
)
def test_prose_inside_a_job_is_never_a_skill_list(line: str) -> None:
    assert skill_texts("Riley\n\n" + JOB + f"- {line}\n") == []


# =========================================================================
# 3. depth is kept; certified-only is never a plain skill
# =========================================================================

MATRIX = (
    "# Riley\n\n## Top Skills\nBoomi\nPython\n\n## Technology Matrix\n"
    "| Technology | Depth | Where |\n|---|---|---|\n"
    "| Boomi | Certified only | Professional Integration Developer |\n"
    "| n8n | Built & owned | Production billing system |\n\n"
    "## Experience\n### Contoso\n#### Analyst (Jan 2020 - Dec 2021)\n- Built reports.\n"
)


def test_certified_only_is_kept_and_boomi_is_not_a_plain_skill() -> None:
    texts = skill_texts(MATRIX)
    assert "Boomi (Certified only)" in texts
    assert "Boomi" not in texts, "a context-free Top Skills entry flattened the qualifier"
    # Python has no richer occurrence and stays as the document listed it.
    assert "Python" in texts


def test_a_richer_row_keeps_its_depth_and_context() -> None:
    (n8n,) = [p for p in read_cv(MATRIX).proposals if p.text.startswith("n8n")]
    assert n8n.text == "n8n (Built & owned)"
    assert "Production billing system" in n8n.evidence
    assert n8n.source_text and "Built & owned" in n8n.source_text


# =========================================================================
# 4. a spaced hyphen is not a separator; a slash never was
# =========================================================================


def test_a_qualifier_after_a_hyphen_stays_with_its_skill() -> None:
    text = (
        "Riley\n\nSKILLS\nBoomi - Certified only, Make - formerly Integromat; "
        "CI/CD, REST/GraphQL, AS-IS / TO-BE\n\n" + JOB + "- Built reports.\n"
    )
    texts = skill_texts(text)
    assert texts == [
        "Boomi - Certified only",
        "Make - formerly Integromat",
        "CI/CD",
        "REST/GraphQL",
        "AS-IS / TO-BE",
    ]
    assert "Certified only" not in texts and "formerly Integromat" not in texts


# =========================================================================
# 5. certificate display never structures prose
# =========================================================================


@pytest.mark.parametrize(
    "line",
    [
        "Led the AWS migration | reduced cost by 30%",
        "Boomi Associate | Boomi | 2023 | Certified only",
    ],
)
def test_prose_in_a_certificate_row_leaves_it_unstructured(line: str) -> None:
    assert read_certificate(line) is None


@pytest.mark.parametrize("line", ["Note: renewal pending", "AWS Certified: Solutions Architect"])
def test_a_colon_that_is_not_an_issuer_keeps_the_line_whole(line: str) -> None:
    read = read_certificate(line)
    assert read is not None and read.title == line and read.issuer is None


def test_a_real_issuer_still_reads() -> None:
    read = read_certificate("Google: Data Analytics Certificate")
    assert read is not None and (read.issuer, read.title) == (
        "Google",
        "Data Analytics Certificate",
    )

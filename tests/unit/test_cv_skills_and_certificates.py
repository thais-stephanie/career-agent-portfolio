"""Skills a CV lists, and certificates read as the fields they state.

WHAT HAPPENED (2026-09-25, the owner's own CV): the read proposed no skills at
all -- the section's heading was not one of the exact phrases this reader knew
-- and every certification reached Career Profile as one raw pipe-delimited
line. These pin the skills heading rule, one proposal per skill, and a
certificate reader that states only what a line states.
"""

from __future__ import annotations

import pytest

from career_agent.cv.certifications import read_certificate
from career_agent.cv.propose import read_cv
from career_agent.domain.enums import ClaimType

TAIL = "\nEXPERIENCE\nAnalyst, Contoso, Jan 2020 - Dec 2021\n- Built reports.\n"


def skills(text: str) -> list[tuple[str, int | None, str | None]]:
    return [
        (p.text, p.source_line, p.source_text)
        for p in read_cv(text).proposals
        if p.claim_type in {ClaimType.SKILL, ClaimType.TOOL}
    ]


# =========================================================================
# skills
# =========================================================================


def test_a_comma_separated_line_is_one_skill_each() -> None:
    found = skills("Riley\n\nSKILLS\nHubSpot, n8n, Python, SQL, Power BI\n" + TAIL)
    assert [name for name, _, _ in found] == ["HubSpot", "n8n", "Python", "SQL", "Power BI"]


def test_a_pipe_separated_line_is_one_skill_each() -> None:
    found = skills("Riley\n\nSKILLS\nSalesforce | Looker | dbt\n" + TAIL)
    assert [name for name, _, _ in found] == ["Salesforce", "Looker", "dbt"]


def test_a_group_label_is_not_a_skill() -> None:
    found = skills("Riley\n\nSKILLS\nAutomation: n8n, Make, Zapier\n" + TAIL)
    assert [name for name, _, _ in found] == ["n8n", "Make", "Zapier"]


def test_a_skill_listed_twice_is_proposed_once() -> None:
    found = skills("Riley\n\nSKILLS\nAutomation: n8n, Make\nIntegration: n8n, Workato\n" + TAIL)
    names = [name for name, _, _ in found]
    assert names == ["n8n", "Make", "Workato"], names


def test_each_skill_keeps_the_line_it_came_from() -> None:
    text = "Riley\n\nSKILLS\nHubSpot, n8n\n" + TAIL
    found = skills(text)
    assert {line for _, line, _ in found} == {4}
    assert {source for _, _, source in found} == {"HubSpot, n8n"}


@pytest.mark.parametrize(
    "heading",
    [
        "Technical Skills",
        "Skills & Tools",
        "Core Competencies",
        "Key Skills",
        "Tools & Platforms",
        "Technical Stack",
        "Competências Técnicas",
        "Ferramentas e Tecnologias",
        "Areas of Expertise",
        "Toolkit",
    ],
)
def test_a_qualified_skills_heading_is_read_as_one(heading: str) -> None:
    plain = skills(f"Riley\n\n{heading}\nHubSpot, n8n, Python\n" + TAIL)
    marked = skills(f"# Riley\n\n## {heading}\nHubSpot, n8n, Python\n" + TAIL)
    assert len(plain) == 3 and len(marked) == 3, (heading, plain, marked)


def test_a_sentence_about_tools_is_not_a_heading() -> None:
    text = "Riley\n\nSUMMARY\nBuilt tools for the finance team\nHubSpot, n8n\n" + TAIL
    assert skills(text) == []


# =========================================================================
# certificates
# =========================================================================


def test_title_issuer_issued_expiry_and_id() -> None:
    cert = read_certificate(
        "Certified Administrator | Salesforce | Sep 2026 | Sep 2027 | ABC-12345"
    )
    assert cert is not None
    assert (cert.title, cert.issuer, cert.issued, cert.expires, cert.credential_id) == (
        "Certified Administrator",
        "Salesforce",
        "2026-09",
        "2027-09",
        "ABC-12345",
    )


def test_title_issuer_and_issued_only() -> None:
    cert = read_certificate("Certified Administrator | Salesforce | Sep 2026")
    assert cert is not None
    assert (cert.issued, cert.expires, cert.no_expiry, cert.credential_id) == (
        "2026-09",
        None,
        False,
        None,
    )


def test_a_year_alone_is_kept_as_a_year() -> None:
    cert = read_certificate("Data Analytics | Coursera | 2021")
    assert cert is not None and cert.issued == "2021"


def test_missing_dates_are_not_invented() -> None:
    cert = read_certificate("Inbound | HubSpot Academy")
    assert cert is not None and cert.issuer == "HubSpot Academy"
    assert (cert.issued, cert.expires, cert.credential_id) == (None, None, None)
    title_only = read_certificate("PMP")
    assert title_only is not None and title_only.title == "PMP" and title_only.issuer is None


def test_no_expiration_is_read_only_when_the_line_says_it() -> None:
    stated = read_certificate("Course | Provider | Mar 2024 | No expiration | ID-4432")
    assert stated is not None and stated.no_expiry and stated.expires is None
    assert stated.credential_id == "ID-4432"
    # An empty column is "not stated", never "does not expire".
    dash = chr(0x2014)  # an em dash: the empty cell of a table
    empty = read_certificate(f"Course | Provider | Mar 2024 | {dash} | ID-4432")
    assert empty is not None and not empty.no_expiry and empty.expires is None


@pytest.mark.parametrize(
    "line",
    [
        "Course | Provider | Sep 2027 | Sep 2026 | ID9999",  # expiry before issue
        "Course | Provider | Sep 2024 | Oct 2024 | Nov 2024",  # a third date
        "Course | Provider | Other text | Sep 2026",  # a second free-text field
        "Course | Provider | Sep 2026 | Some words here | X1",
        "Certification | Issuer | Issued | Expires | Credential ID",  # a header row
    ],
)
def test_an_ambiguous_line_stays_unstructured(line: str) -> None:
    assert read_certificate(line) is None


def test_reading_a_certificate_leaves_its_text_as_it_was() -> None:
    line = "Certified Administrator | Salesforce | Sep 2026 | Sep 2027 | ABC-12345"
    original = str(line)
    read_certificate(line)
    assert line == original

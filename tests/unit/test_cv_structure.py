"""Markdown normalisation and CV structure, one rule per test.

Each rule was reproduced as a failure of the flat reader on `main` before it
was written. Synthetic text only.
"""

from __future__ import annotations

import pytest
from tests.support_cv import EM_DASH, EN_DASH, load_cv

from career_agent.cv.markdown import classify, inline
from career_agent.cv.propose import read_cv
from career_agent.cv.structure import chronology_key, is_date_line, read_header

# =========================================================================
# 1. MARKDOWN BECOMES WORDS; PUNCTUATION STAYS
# =========================================================================


@pytest.mark.parametrize(
    ("raw", "plain"),
    [
        ("**Integration:** Workato, MuleSoft", "Integration: Workato, MuleSoft"),
        ("__bold__ and _italic_ and *emphasis*", "bold and italic and emphasis"),
        ("***both at once***", "both at once"),
        ("[a link](https://example.invalid/a_(b)) here", "a link here"),
        ("![a logo](logo.png)", "a logo"),
        ("uses `lead-routing` daily", "uses lead-routing daily"),
        ("~~struck~~ through", "struck through"),
        ("**unclosed bold", "unclosed bold"),
        ("\\*literally starred\\*", "*literally starred*"),
        ("line<br>break", "line break"),
    ],
)
def test_inline_markdown_becomes_plain_text(raw: str, plain: str) -> None:
    assert inline(raw) == plain


@pytest.mark.parametrize(
    "text",
    [
        "5 * 3 * 2 = 30",
        "snake_case_name and __init__.py",
        "C# and F# with R&D",
        "Grew revenue 40%* (audited)",
        "Q3/Q4 2025: +12% YoY",
        "S/4HANA, Co-founder, e-mail",
    ],
)
def test_ordinary_punctuation_is_not_corrupted(text: str) -> None:
    assert inline(text) == text


def test_lines_are_classified_and_nothing_is_dropped() -> None:
    text = "\n".join(
        [
            "# Name",
            "Subtitle",
            "========",
            "- item one",
            "  * nested item",
            "3. numbered",
            "> quoted words",
            "---",
            "```",
            "code line",
            "```",
            "| a | b |",
            "|---|---|",
            "plain",
        ]
    )
    kinds = [(line.kind, line.level, line.text) for line in classify(text)]
    assert kinds[0] == ("heading", 1, "Name")
    assert kinds[1] == ("heading", 1, "Subtitle")
    assert ("item", 0, "item one") in kinds
    assert ("item", 1, "nested item") in kinds
    assert ("item", 0, "numbered") in kinds
    assert ("text", 0, "quoted words") in kinds
    assert ("code", 0, "code line") in kinds
    assert ("text", 0, "a | b") in kinds
    assert [k for k, _l, _t in kinds].count("rule") >= 2
    assert len(kinds) == len(text.splitlines())


# =========================================================================
# 2. STRUCTURE
# =========================================================================


def test_headings_are_never_proposals() -> None:
    read = read_cv(load_cv("markdown_complex.md"))
    texts = {p.text for p in read.proposals}
    for entry in read.entries:
        for line in entry.lines:
            assert line.text not in texts


def test_a_nested_heading_is_a_role_inside_a_company() -> None:
    text = (
        "## Experience\n### Teem\n#### Business Operations / RevOps\n"
        f"2025{EN_DASH}2026\n- Built it.\n"
    )
    (entry,) = read_cv(text).entries
    assert (entry.company, entry.role, entry.span.text) == (
        "Teem",
        "Business Operations / RevOps",
        f"2025{EN_DASH}2026",
    )


def test_uncertain_structure_is_left_unresolved() -> None:
    text = f"EXPERIENCE\nAcme {EM_DASH} Globex, 2019 - 2020\n- Did a thing.\n"
    (entry,) = read_cv(text).entries
    assert entry.company is None and entry.role is None
    assert entry.label == "Acme"
    assert "structure" in entry.unresolved


@pytest.mark.parametrize(
    ("line", "company", "role"),
    [
        (
            f"Fabrikam Cloud {EM_DASH} Senior Solutions Consultant",
            "Fabrikam Cloud",
            "Senior Solutions Consultant",
        ),
        ("Consultant, Company A00, Jan 2000 - Dec 2001", "Company A00", "Consultant"),
        ("Analista na Acme, 2020 - 2023", "Acme", "Analista"),
        ("Data Analyst at Northwind, 2019 - 2021", "Northwind", "Data Analyst"),
        (
            "Initech, Operations Analyst (contract), Jun 2015 - Jan 2016",
            "Initech",
            "Operations Analyst",
        ),
        ("Especialista em automacao, 2019 - 2020", None, "Especialista em automacao"),
    ],
)
def test_header_lines(line: str, company: str | None, role: str) -> None:
    header = read_header(line)
    assert (header.company, header.role) == (company, role)


def test_date_lines_with_a_place_are_structure() -> None:
    dated = is_date_line(f"Jun 2017 {EN_DASH} Dec 2019 | São Paulo")
    assert dated is not None
    span, place = dated
    assert (span.start, span.end, place) == ("2017-06", "2019-12", "São Paulo")
    assert is_date_line("Recognised as operator of the quarter in Q3 2025.") is None
    assert is_date_line("Implementation Analyst, 2015 - 2017") is None


def test_a_current_role_has_no_end() -> None:
    span, _place = is_date_line("mar. 2021 – atual")  # punctuation-check: allow a CV's own dash
    assert (span.start, span.end, span.current) == ("2021-03", None, True)


def test_a_year_only_span_keeps_its_years_and_invents_no_month() -> None:
    span, _place = is_date_line("2015 - 2017")
    assert (span.start, span.end, span.start_year, span.end_year) == (None, None, 2015, 2017)


def test_chronology_puts_current_first_and_undated_last() -> None:
    entries = {
        "undated": chronology_key(None, None, None, None, False),
        "old": chronology_key("2015-01", 2015, "2017-12", 2017, False),
        "current": chronology_key("2022-03", 2022, None, None, True),
        "recent": chronology_key(None, 2025, None, 2026, False),
    }
    assert sorted(entries, key=entries.__getitem__) == ["current", "recent", "old", "undated"]


def test_every_proposal_keeps_its_raw_line() -> None:
    text = load_cv("markdown_complex.md")
    raw_lines = text.splitlines()
    for proposal in read_cv(text).proposals:
        assert proposal.source_line is not None
        assert raw_lines[proposal.source_line - 1] == proposal.source_text


def test_a_package_built_from_a_markdown_cv_has_clean_jobs() -> None:
    """The intake builder used to carry the last dated line forward as the
    employer, which read `### Fabrikam Cloud` syntax into employer names and
    never reset between sections. It uses the reader's jobs now."""
    from career_agent.intake.build import IntakeDocument, build_package

    package = build_package(
        [
            IntakeDocument(
                name="riley.md",
                data=load_cv("markdown_complex.md").encode("utf-8"),
                ref="cv",
                kind="RESUME",
            )
        ]
    )
    jobs = {(c.employer, c.role_title) for c in package.claims if c.employer}
    assert jobs == {
        ("Fabrikam Cloud", "Senior Solutions Consultant"),
        ("Fabrikam Cloud", "Solutions Consultant"),
        ("Contoso Health", "Implementation Lead"),
        ("Northwind Retail", "Implementation Analyst"),
        ("Teem", "Business Operations / RevOps"),
    }
    skills = [c for c in package.claims if c.type.value == "SKILL"]
    assert skills and all(c.employer is None for c in skills)
    assert not any("**" in c.text or c.text.startswith("#") for c in package.claims)


# =========================================================================
# 3. THE LAYOUT OF A REAL SENIOR CV (found in the real-workspace QA, invented here)
# =========================================================================


def test_a_company_heading_owns_the_role_lines_beneath_it() -> None:
    """`### COMPANY - City / Remote`, then a bold `Role | Specialty` line and an
    italic date line. The specialty after the bar is part of the title, never
    a company; the rest of the heading is where."""
    read = read_cv(load_cv("company_headings.md"))
    contoso = read.entries[0]
    assert contoso.company == "CONTOSO LLC"
    assert contoso.role == "Senior Revenue Engineer | Billing Systems, Automation & Data Quality"
    assert contoso.span is not None and contoso.span.start == "2025-03"
    assert contoso.location == "Springfield, ST, Invented Country, Remote"


def test_a_company_tenure_with_several_roles_is_not_a_job_of_its_own() -> None:
    read = read_cv(load_cv("company_headings.md"))
    fabrikam = [e for e in read.entries if e.company == "FABRIKAM INC"]
    assert [e.role for e in fabrikam] == [
        "Product Manager | Product Systems & Data",
        "Senior Solutions Engineer | Finance Automation",
    ]
    # The heading and its tenure line travel with each role as its source.
    assert all(any("FABRIKAM" in line.raw for line in e.lines) for e in fabrikam)
    assert all(e.location for e in fabrikam)


def test_several_titles_on_one_line_stay_one_role() -> None:
    read = read_cv(load_cv("company_headings.md"))
    northwind = next(e for e in read.entries if e.company == "NORTHWIND JR.")
    assert northwind.role == "Process Consultant · Project Manager · Brand Analyst"


def test_bold_sub_headings_inside_a_job_are_not_claims() -> None:
    read = read_cv(load_cv("company_headings.md"))
    texts = {p.text for p in read.proposals}
    assert "Reliability and monitoring" not in texts
    assert "Subscription billing pipeline (CRM to payments)" not in texts
    contoso = read.entries[0]
    assert any("Reliability and monitoring" in line.raw for line in contoso.lines)


def test_section_headings_with_more_words_are_recognised() -> None:
    read = read_cv(load_cv("company_headings.md"))
    by_section = {p.section for p in read.proposals}
    assert {"projects", "certifications", "education"} <= by_section
    projects = [p.text for p in read.proposals if p.section == "projects"]
    # The project's date line dates it; it is not a claim.
    assert projects == ["Built a local matching tool with an evidence review workflow."]
    assert read.unread_lines == ["Morgan Invented"]


def test_a_title_keeps_its_own_parentheses() -> None:
    text = (
        "## Experience\n### Initech {EM} Harbor Town\n"
        "**Business Process Analyst (Intern)** · *August 2019 {EM} March 2020*\n"
        "- Mapped a process.\n"
    ).replace("{EM}", EM_DASH)
    (entry,) = read_cv(text).entries
    assert entry.role == "Business Process Analyst (Intern)"

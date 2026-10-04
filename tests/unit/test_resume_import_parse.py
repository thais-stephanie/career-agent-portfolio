"""Resume Workspace V2 PR 6: what the import reader proposes, and how sure it is.

Synthetic resumes only (`tests/support_import.py`). The reader is
deterministic; these pin what it finds, what it refuses to guess, and that
every value it proposes is text the document holds.
"""

from __future__ import annotations

from typing import Any

import pytest
from tests.support_import import (
    AMBIGUOUS,
    EM,
    EN,
    PORTUGUESE,
    SIMPLE,
    SPANISH,
    SPARSE,
    TABLE_HEADER,
    UPPERCASE,
    docx,
    lines,
    long_senior,
)

from career_agent.resume_doc.imports import ImportProposal, to_document
from career_agent.resume_doc.intake import Extracted, SourceLine, read_upload
from career_agent.resume_doc.models import DocumentKind, LinkKind, Origin
from career_agent.resume_doc.parse import _date, _span, parse


def read(spec: Any, name: str = "resume.docx") -> ImportProposal:
    return parse(read_upload(docx(spec), name), name)


def of(source: list[SourceLine], fmt: str = "DOCX") -> ImportProposal:
    return parse(Extracted(fmt, 1, source), "resume.docx")


# ------------------------------------------------------------------- dates


@pytest.mark.parametrize(
    ("written", "start", "end", "current"),
    [
        ("2024", "2024", None, False),
        ("Jan 2024", "2024-01", None, False),
        ("January 2024 to March 2025", "2024-01", "2025-03", False),
        ("01/2024 - 06/2024", "2024-01", "2024-06", False),
        ("2024-01 - 2024-06", "2024-01", "2024-06", False),
        (f"2019 {EN} 2021", "2019", "2021", False),
        (f"Mar 2019 {EM} Present", "2019-03", None, True),
        ("Apr 2021 - Current", "2021-04", None, True),
        ("jan 2024 - atual", "2024-01", None, True),
        ("janeiro de 2021 até presente", "2021-01", None, True),
        ("ene 2020 - actualidad", "2020-01", None, True),
        ("marzo de 2019 hasta diciembre 2020", "2019-03", "2020-12", False),
    ],
)
def test_dates_are_read_as_written_and_a_month_is_never_invented(
    written: str, start: str, end: str | None, current: bool
) -> None:
    span = _span(f"Analyst | Example Co | {written}")
    assert span is not None
    assert (span.start, span.end, span.current) == (start, end, current)


def test_a_year_alone_stays_a_year() -> None:
    assert _date("2019") == "2019"
    assert _date("Q3 2019") is None  # not a date this reads: nothing invented
    proposal = read(SPARSE)
    education = to_document(proposal, DocumentKind.IMPORTED).education[0]
    assert education.start is not None and education.start.month is None


# ---------------------------------------------------------------- sections


@pytest.mark.parametrize(
    ("spec", "language", "sections"),
    [
        (SIMPLE, "en", ["summary", "experience", "education", "skills"]),
        (PORTUGUESE, "pt", ["summary", "experience", "education", "skills", "other"]),
        (SPANISH, "es", ["experience", "education", "certifications"]),
        (UPPERCASE, "en", ["experience", "education"]),
    ],
)
def test_sections_are_found_in_english_portuguese_and_spanish(
    spec: Any, language: str, sections: list[str]
) -> None:
    proposal = read(spec)
    assert proposal.report.sections == sections
    assert proposal.language == language


def test_an_unknown_heading_is_kept_as_its_own_section_never_dropped() -> None:
    proposal = read(AMBIGUOUS)
    [other] = proposal.other
    assert other.name.value == "Things I Like" and other.name.confidence == "LOW"
    assert other.name.note == "UNKNOWN_HEADING"
    assert [line.text.value for line in other.lines] == ["Chess and long walks."]


def test_a_bold_entry_line_is_not_a_section() -> None:
    source = lines("#Experience", "!Northwind Systems", "Analyst, 2019 - 2021", "*Did things.")
    proposal = of(source)
    assert proposal.report.sections == ["experience"]
    assert proposal.experience[0].org and proposal.experience[0].org.value == "Northwind Systems"


# ---------------------------------------------------------------- identity


@pytest.mark.parametrize(
    "phone", ["(11) 99999-9999", "+55 11 99999-9999", "+44 20 7946 0000", "+1 555 0100"]
)
def test_international_phones_are_kept_as_typed(phone: str) -> None:
    proposal = of(lines("@Robin Example", f"robin@example.invalid | {phone}", "#Skills", "SQL"))
    assert proposal.identity.phone is not None
    assert proposal.identity.phone.value == phone


def test_identity_links_and_location() -> None:
    proposal = read(SIMPLE)
    ident = proposal.identity
    assert ident.name and (ident.name.value, ident.name.confidence) == ("Morgan Example", "HIGH")
    assert ident.email and ident.email.value == "morgan@example.invalid"
    assert [link.kind for link in ident.links] == [LinkKind.LINKEDIN]
    assert ident.location and ident.location.confidence == "MEDIUM"
    doc = to_document(proposal, DocumentKind.IMPORTED)
    assert (doc.identity.city, doc.identity.country) == ("Springfield", "US")


def test_a_contact_table_is_read_as_source_text() -> None:
    ident = read(TABLE_HEADER).identity
    assert ident.name and ident.name.value == "Riley Synthetic"
    assert ident.email and ident.email.value == "riley@example.invalid"
    assert ident.phone and ident.phone.value == "+44 20 7946 0000"


def test_two_lines_that_could_be_the_name_are_never_a_confident_choice() -> None:
    proposal = read(AMBIGUOUS)
    name = proposal.identity.name
    assert name and name.confidence == "LOW" and name.alternatives == ["Jamie Sample"]
    assert proposal.headline and proposal.headline.text.confidence == "LOW"


def test_you_and_section_words_are_never_a_name() -> None:
    proposal = of(lines("You", "you@example.invalid", "#Summary", "Hello there."))
    assert proposal.identity.name is None
    proposal = of(lines("!CURRICULUM VITAE", "@Robin Example", "#Skills", "SQL"))
    assert proposal.identity.name and proposal.identity.name.value == "Robin Example"


# ------------------------------------------------------------------ entries


def test_a_place_that_could_be_the_company_is_flagged_not_chosen() -> None:
    first = read(AMBIGUOUS).experience[0]
    assert first.org and first.org.value == "Curitiba"
    assert first.org.confidence == "LOW" and first.org.note == "LOCATION_OR_COMPANY"
    assert first.location is None, "never the candidate's or the job's location by guess"


def test_two_names_with_no_role_word_are_flagged() -> None:
    second = read(AMBIGUOUS).experience[1]
    assert second.title and second.title.confidence == "LOW"
    assert second.title.note == "ROLE_OR_COMPANY"
    assert second.title.alternatives == ["Example Insurance"]


def test_entries_dates_and_bullets_survive_whole() -> None:
    proposal = read(SIMPLE)
    first, second = proposal.experience
    assert first.title and first.title.value == "Operations Analyst"
    assert first.org and first.org.value == "Northwind Traders"
    assert first.start and first.start.value == "2021-01" and first.current
    assert [line.text.value for line in first.lines] == [
        "Rebuilt the weekly sales report so it runs in ten minutes instead of a day.",
        "Documented the returns process for the support team.",
    ]
    assert (
        second.start
        and second.end
        and (second.start.value, second.end.value)
        == (
            "2018",
            "2020",
        )
    )


@pytest.mark.parametrize("glyph", ["•", "-", EN, "▪", "◦"])  # noqa: RUF001
def test_bullet_glyphs_go_and_the_words_stay(glyph: str) -> None:
    text = "Kept the on-call runbook current - every week, without fail."
    proposal = of(lines("#Experience", "!Analyst | Example Co | 2020 - 2021", f"{glyph} {text}"))
    assert [line.text.value for line in proposal.experience[0].lines] == [text]


def test_a_wrapped_pdf_line_is_joined_and_two_bullets_are_not() -> None:
    def pdf(text: str, n: int, indent: float = 12, gap: float = 1.25) -> SourceLine:
        return SourceLine(text=text, where=f"page 1, line {n}", indent=indent, gap=gap)

    long = "Planned weekly crew rosters for forty routes and kept them inside the"
    source = [
        SourceLine(text="EXPERIENCE", where="page 1, line 1", bold=True, size=10),
        pdf("Planner | Example Airlines | 2019 - 2021", 2, indent=0),
        pdf(long, 3),
        pdf("rest rules the regulator publishes.", 4),
        pdf("Cut late changes by a third.", 5, gap=1.43),
    ]
    proposal = of(source, "PDF")
    assert [line.text.value for line in proposal.experience[0].lines] == [
        f"{long} rest rules the regulator publishes.",
        "Cut late changes by a third.",
    ]


def test_long_senior_resume_keeps_every_entry_and_line() -> None:
    proposal = read(long_senior())
    assert len(proposal.experience) == 8
    assert all(len(e.lines) == 5 for e in proposal.experience)


# ------------------------------------------------------------------- skills


def test_skills_split_on_list_punctuation_never_on_spaces() -> None:
    proposal = read(SIMPLE)
    groups = {g.name.value: [i.text.value for i in g.lines] for g in proposal.skills}
    assert groups == {
        "Analysis": ["SQL", "Excel", "Revenue Operations"],
        "Languages": ["English", "Spanish"],
    }


def test_a_bare_skill_list_is_one_group_named_after_its_heading() -> None:
    [group] = read(TABLE_HEADER).skills
    assert group.name.value == "Skills"
    assert [i.text.value for i in group.lines] == ["Python", "Event streams"]


def test_category_values_become_a_skill_group() -> None:
    proposal = of(lines("#Skills", "Automation: n8n, Workato, Zapier"))
    [group] = proposal.skills
    assert group.name.value == "Automation"
    assert [i.text.value for i in group.lines] == ["n8n", "Workato", "Zapier"]


# -------------------------------------------------------- truth, provenance


def _values(node: Any, path: str = "") -> list[tuple[str, dict[str, Any]]]:
    if isinstance(node, dict):
        own = [(path, node)] if "value" in node and "confidence" in node else []
        return own + [x for k, v in node.items() for x in _values(v, f"{path}/{k}")]
    if isinstance(node, list):
        return [x for i, v in enumerate(node) for x in _values(v, f"{path}/{i}")]
    return []


@pytest.mark.parametrize(
    "spec", [SIMPLE, TABLE_HEADER, UPPERCASE, PORTUGUESE, SPANISH, SPARSE, AMBIGUOUS]
)
def test_every_value_is_text_the_document_holds(spec: Any) -> None:
    proposal = read(spec)
    for path, found in _values(proposal.model_dump(exclude={"report", "source"})):
        if path.endswith(("/start", "/end")):
            continue  # a date's value is its YYYY or YYYY-MM reading
        if not found["value"]:
            continue  # a section with no heading asks for one; nothing claimed
        assert found["source"], path
        assert found["value"].casefold() in found["source"]["text"].casefold(), path


@pytest.mark.parametrize("spec", [SIMPLE, PORTUGUESE, SPARSE, AMBIGUOUS])
def test_imported_text_is_imported_and_cites_no_evidence(spec: Any) -> None:
    doc = to_document(read(spec), DocumentKind.IMPORTED)
    data = doc.model_dump()
    origins = [n for _, n in _origins(data)]
    assert origins and set(origins) == {Origin.IMPORTED}
    assert all(not ids for ids in _all(data, "evidence_ids"))
    assert doc.provenance.created_from == "IMPORT" and doc.kind == DocumentKind.IMPORTED


def _origins(node: Any, path: str = "") -> list[tuple[str, Any]]:
    if isinstance(node, dict):
        own = [(path, node["origin"])] if "origin" in node else []
        return own + [x for k, v in node.items() for x in _origins(v, f"{path}/{k}")]
    if isinstance(node, list):
        return [x for v in node for x in _origins(v, path)]
    return []


def _all(node: Any, key: str) -> list[Any]:
    if isinstance(node, dict):
        own = [node[key]] if key in node else []
        return own + [x for v in node.values() for x in _all(v, key)]
    if isinstance(node, list):
        return [x for v in node for x in _all(v, key)]
    return []


def test_markup_in_a_resume_is_text_never_markup() -> None:
    proposal = of(lines("@Robin Example", "#Summary", '<script>alert("x")</script> <b>bold</b>'))
    assert proposal.summary
    assert proposal.summary.text.value == '<script>alert("x")</script> <b>bold</b>'
    from career_agent.resume_doc.render import render_html

    html = render_html(to_document(proposal, DocumentKind.IMPORTED), mode="print").html
    assert "<script>alert" not in html and "&lt;script&gt;" in html

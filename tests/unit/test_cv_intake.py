"""Reading a CV without believing it.

The rule every test here circles: **nothing is true because it was read.** A
proposal carries the line it came from and becomes a fact only when a person
accepts it, and `to_claim` is the single place in the package that sets
`verified=True`.
"""

from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path

import pytest

from career_agent.cv.extract import MAX_BYTES, SUPPORTED, CvError, extract
from career_agent.cv.propose import Proposal, read_cv, to_claim
from career_agent.domain.enums import ClaimSource, ClaimType

CV = """Ana Ribeiro
Sao Paulo, Brazil

SUMMARY
Business systems analyst with eight years across CRM work.

EXPERIENCE
Acme Ltda - Senior Systems Analyst, 2021 - present
- Rebuilt the lead routing pipeline, cutting handoff time 40%
- Owned the HubSpot to NetSuite integration end to end

SKILLS
Process design, data modelling, requirements gathering

TOOLS
HubSpot | Salesforce | n8n

EDUCATION
BSc Information Systems, USP, 2017
"""


@pytest.fixture
def cv(tmp_path: Path) -> Path:
    path = tmp_path / "ana-cv.txt"
    path.write_text(CV, encoding="utf-8")
    return path


# =========================================================================
# 1. EXTRACTION READS, AND DECIDES NOTHING
# =========================================================================


def test_a_text_cv_is_read(cv: Path) -> None:
    found = extract(cv)
    assert found.kind == "txt"
    assert "Rebuilt the lead routing pipeline" in found.text
    assert not found.looks_empty


def test_a_file_that_is_not_there_is_refused(tmp_path: Path) -> None:
    with pytest.raises(CvError, match="not a file"):
        extract(tmp_path / "nothing.pdf")


def test_an_unsupported_kind_is_refused_by_whitelist(tmp_path: Path) -> None:
    """A whitelist rather than sniffing. The point is to refuse surprises, and
    a kind this list does not name is a surprise."""
    path = tmp_path / "cv.exe"
    path.write_bytes(b"MZ\x90\x00")
    with pytest.raises(CvError, match="not a kind this reads"):
        extract(path)


def test_an_empty_file_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "cv.txt"
    path.write_bytes(b"")
    with pytest.raises(CvError, match="empty"):
        extract(path)


def test_a_file_over_the_limit_is_refused_before_it_is_read(tmp_path: Path) -> None:
    """Checked from the filesystem. A size read after loading is a size read
    too late, and this file is untrusted input from outside the program."""
    path = tmp_path / "huge.txt"
    with path.open("wb") as handle:
        handle.seek(MAX_BYTES + 1)
        handle.write(b"\0")
    with pytest.raises(CvError, match="limit"):
        extract(path)


def test_an_error_never_quotes_the_document(tmp_path: Path) -> None:
    """An error message is the easiest way for a CV to reach a terminal
    history, a bug report or a screenshot."""
    secret = "Ana Ribeiro, +55 11 99999-0000, ana@example.com"
    path = tmp_path / "cv.rtf"
    path.write_text(secret, encoding="utf-8")
    with pytest.raises(CvError) as raised:
        extract(path)
    assert secret not in str(raised.value)
    assert "Ana" not in str(raised.value)


def test_a_scanned_pdf_says_so_rather_than_looking_broken(tmp_path: Path) -> None:
    """A PDF whose pages are images has no text layer. Returning an empty
    string with no explanation looks like a bug in this program rather than a
    property of the document."""
    path = tmp_path / "short.txt"
    path.write_text("Ana Ribeiro\n", encoding="utf-8")
    assert extract(path).looks_empty


def test_a_damaged_docx_is_an_error_not_a_crash(tmp_path: Path) -> None:
    path = tmp_path / "broken.docx"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", "<not-really-ooxml/>")
    with pytest.raises(CvError, match="could not be read"):
        extract(path)


def test_every_supported_kind_has_a_reader() -> None:
    """A kind advertised and not readable is a promise the product breaks at
    the worst moment, which is the first time somebody uses it."""
    for suffix in SUPPORTED:
        assert suffix.startswith(".")


# =========================================================================
# 2. NOTHING IS CONFIRMED BY BEING READ
# =========================================================================


def test_every_proposal_starts_unverified(cv: Path) -> None:
    read = read_cv(extract(cv).text)
    assert read.proposals
    assert all(not proposal.is_verified for proposal in read.proposals)


def test_only_to_claim_confirms_anything() -> None:
    """The single place in the package that sets `verified=True`.

    Counted as ARGUMENTS in the syntax tree, not as text. A grep finds the
    docstring promising exactly this and fails on it -- the same mistake the
    backup module's network test made, and a check that cannot tell a promise
    from a violation is not a check.
    """
    import ast

    root = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "cv"
    confirming: list[str] = []
    for module in sorted(root.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "verified"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                ):
                    function = node.func
                    name = getattr(function, "id", None) or getattr(function, "attr", "?")
                    confirming.append(f"{module.name}:{name}")

    assert confirming == ["propose.py:VerifiedClaim"], (
        f"something other than to_claim confirms a claim: {confirming}"
    )


def test_an_accepted_proposal_keeps_the_line_it_came_from(cv: Path) -> None:
    """`evidence_ref` is what lets a preparation view say WHY it believes
    something about the candidate."""
    read = read_cv(extract(cv).text)
    proposal = next(p for p in read.proposals if p.claim_type is ClaimType.EMPLOYMENT)
    claim = to_claim(proposal)
    assert claim.verified is True
    assert claim.source is ClaimSource.RESUME
    assert claim.evidence_ref
    assert claim.evidence_ref in CV


def test_a_proposal_can_be_edited_before_it_is_accepted(cv: Path) -> None:
    """The third answer, and the one that matters most: a CV line is often
    nearly right, and accept-or-reject alone pushes somebody into accepting
    something they would have corrected."""
    read = read_cv(extract(cv).text)
    proposal = read.proposals[0]
    claim = to_claim(proposal, text="  A shorter, truer sentence  ")
    assert claim.text == "A shorter, truer sentence"
    assert claim.evidence_ref == proposal.evidence, "the original line was lost"


# =========================================================================
# 3. WHAT IT READS, AND WHAT IT REFUSES TO READ
# =========================================================================


def test_sections_are_found_in_both_languages() -> None:
    text = "EXPERIENCIA\nAnalista na Acme, 2020 - 2023\n\nFERRAMENTAS\nSalesforce | SQL\n"
    read = read_cv(text)
    assert "experience" in read.sections
    assert "tools" in read.sections


#: A CV with no employment history at all. Every line under a heading that
#: exists precisely because somebody has not been paid for work yet.
GRADUATE_CV = """Ana Silva

Education
BA Communications, University of Sao Paulo, 2025

Volunteering
Ran the weekend food bank rota for a team of nine.
Answered the helpline three evenings a week for two years.

Internships
Communications intern at a local charity, drafting newsletters.

Activities
Treasurer of the student debating society.

Awards
Dean's list, 2024.
"""


def test_a_cv_with_no_job_history_is_not_an_empty_cv() -> None:
    """The ingestion-shaped version of the defect the experience reading fixed.

    A heading this reader does not recognise proposes NOTHING, so before these
    sections existed a graduate's whole CV was read, counted as unread lines
    and silently dropped -- and the product then concluded there was no
    evidence, because it could not see any.

    Six proposals from a document with not one employer in it.
    """
    read = read_cv(GRADUATE_CV)
    sections = set(read.sections)
    assert {"volunteering", "internships", "activities", "awards"} <= sections, sections
    assert len(read.proposals) >= 6, [p.text for p in read.proposals]


def test_unpaid_work_is_read_as_work() -> None:
    """Volunteering and an internship are EMPLOYMENT.

    The person did the work. Whether they were paid for it is a fact about the
    arrangement rather than about what they can now do, and reading them as a
    lesser kind of claim would be this module deciding whose experience counts.
    """
    read = read_cv(GRADUATE_CV)
    kinds = {p.section: p.claim_type.value for p in read.proposals}
    assert kinds["volunteering"] == "EMPLOYMENT"
    assert kinds["internships"] == "EMPLOYMENT"
    assert kinds["awards"] == "ACHIEVEMENT"


def test_the_new_sections_are_found_in_portuguese_too() -> None:
    """The one market this product exists to serve. An English-only reader
    here would have exactly the effect the bilingual hiring anchor was added
    to prevent, one layer earlier."""
    text = (
        "VOLUNTARIADO\nCoordenei a escala do banco de alimentos.\n\n"
        "ESTAGIOS\nEstagiaria de comunicacao em uma ONG local.\n"
    )
    read = read_cv(text)
    assert "volunteering" in read.sections
    assert "internships" in read.sections


def test_a_heading_is_a_whole_line_not_a_word_in_a_sentence() -> None:
    """The same intent-anchoring lesson the matcher learned about job
    descriptions: "skills" alone on a line is a heading, "skills in Python" is
    prose."""
    read = read_cv("SKILLS\nProcess design\n")
    assert "skills" in read.sections

    read = read_cv("I have strong skills in Python\n")
    assert "skills" not in read.sections


def test_a_list_section_yields_one_proposal_per_item(cv: Path) -> None:
    read = read_cv(extract(cv).text)
    tools = [p.text for p in read.proposals if p.claim_type is ClaimType.TOOL]
    assert tools == ["HubSpot", "Salesforce", "n8n"]


def test_an_experience_bullet_is_kept_whole(cv: Path) -> None:
    """Splitting a sentence on its commas turns one accomplishment into
    fragments that mean nothing apart."""
    read = read_cv(extract(cv).text)
    employment = [p.text for p in read.proposals if p.claim_type is ClaimType.EMPLOYMENT]
    assert "Rebuilt the lead routing pipeline, cutting handoff time 40%" in employment


def test_a_figure_is_flagged_and_never_lifted_out(cv: Path) -> None:
    """ "increased revenue 40%" is the candidate's sentence. "40%" on its own is
    a metric this program invented and cannot attribute."""
    read = read_cv(extract(cv).text)
    measured = [p for p in read.proposals if p.has_measurement]
    assert measured
    for proposal in measured:
        assert "40%" in proposal.text
        assert proposal.text != "40%"


def test_a_summary_proposes_nothing() -> None:
    """Prose. Turning a paragraph into a career CLAIM would be this code
    deciding what it asserts."""
    read = read_cv("SUMMARY\nEight years of experience across CRM work.\n")
    assert "summary" in read.sections
    assert not read.proposals


def test_lines_under_no_heading_are_reported_not_discarded(cv: Path) -> None:
    """A CV whose headings this code does not know is still readable by a
    person. Silently dropping half of it is the worst possible outcome."""
    read = read_cv(extract(cv).text)
    assert read.unread_lines
    assert any("Ana Ribeiro" in line for line in read.unread_lines)


def test_re_reading_the_same_cv_proposes_the_same_keys(cv: Path) -> None:
    """So a claim already accepted is recognised rather than offered again as
    though it were new."""
    first = [p.claim_key for p in read_cv(extract(cv).text).proposals]
    second = [p.claim_key for p in read_cv(extract(cv).text).proposals]
    assert first == second
    assert len(set(first)) == len(first), "two proposals share a key"


def test_nothing_in_this_package_reaches_the_network() -> None:
    """A CV is the most private thing this product will ever hold."""
    import ast

    forbidden = {"httpx", "requests", "urllib", "socket", "http", "smtplib"}
    root = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "cv"
    for module in sorted(root.glob("*.py")):
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert not (imported & forbidden), f"{module.name} imports {sorted(imported & forbidden)}"


def test_nothing_in_this_package_writes_a_file() -> None:
    """The extracted text exists in memory and in the claims a person accepts.
    A cache of it on disk would be a copy of somebody's CV nobody asked for.

    `open` is READ-CHECKED rather than banned outright. `extract` opens the CV
    to read it, which it has to; what may never appear is a write mode. The
    check is on the mode argument, so `open(path, "w")` fails here whether it
    is spelled with a builtin or through `Path.open`.
    """
    import ast

    root = Path(__file__).resolve().parents[2] / "src" / "career_agent" / "cv"
    for module in sorted(root.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for verb in ("write_text(", "write_bytes(", ".dump(", "mkdir(", "NamedTemporary"):
            assert verb not in source, f"{module.name} writes with {verb}"

        for node in ast.walk(ast.parse(source)):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.attr if isinstance(node.func, ast.Attribute) else None
            if isinstance(node.func, ast.Name):
                name = node.func.id
            if name != "open":
                continue
            modes = [
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            ] + [
                kw.value.value
                for kw in node.keywords
                if kw.arg == "mode"
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, str)
            ]
            assert modes, f"{module.name} opens a file without saying in which mode"
            for mode in modes:
                assert not (set(mode) & set("wxa+")), f"{module.name} opens for writing: {mode!r}"


def test_a_proposal_is_a_frozen_record() -> None:
    """It travels from a reader to a reviewer to a writer and must not be
    edited quietly on the way."""
    proposal = Proposal(
        claim_key="k", claim_type=ClaimType.SKILL, text="t", section="skills", evidence="e"
    )
    # `FrozenInstanceError` specifically, not any exception: a proposal that
    # raised an AttributeError for some other reason would pass a blind check
    # while being perfectly mutable.
    with pytest.raises(dataclasses.FrozenInstanceError):
        proposal.text = "changed"  # type: ignore[misc]

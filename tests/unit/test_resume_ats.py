"""The export checks: each named check fails on the damage it names, and
nothing anywhere is a score. Files are built here from synthetic text."""

from __future__ import annotations

import io
import json

import pytest
from tests.support_resume import HIDDEN_LINE, HIDDEN_PROJECT, exportable

from career_agent.resume_doc.ats import blocks, check_export
from career_agent.resume_doc.export import docx_bytes, filename, json_bytes
from career_agent.resume_doc.models import content_sha256, upgrade_resume_document
from career_agent.resume_doc.render import render_html

DOC = exportable()
HTML = render_html(DOC, mode="print").html
SHA = content_sha256(DOC)


def _docx(paragraphs: list[str]) -> bytes:
    from docx import Document

    word = Document()
    for text in paragraphs:
        word.add_paragraph(text)
    out = io.BytesIO()
    word.save(out)
    return out.getvalue()


def _status(paragraphs: list[str]) -> dict[str, str]:
    report = check_export("DOCX", DOC, HTML, _docx(paragraphs), revision_sha256=SHA)
    return {c["check"]: c["status"] for c in report["checks"]}


SHOWN = [b.text for b in blocks(HTML)]


def test_the_real_docx_passes_and_says_what_it_did_not_measure() -> None:
    report = check_export("DOCX", DOC, HTML, docx_bytes(DOC, HTML), revision_sha256=SHA)
    status = {c["check"]: c["status"] for c in report["checks"]}
    assert report["verified"] and status["PAGES"] == status["LAYOUT"] == "NOT_MEASURED"
    assert status["SUPPORTED_TERMS"] == "NOT_MEASURED", "no line is linked to a job yet"


@pytest.mark.parametrize(
    ("damage", "check"),
    [
        (lambda p: p[1:], "CONTACT"),
        (lambda p: [*p[:3], *p[4:], p[3]], "READING_ORDER"),
        (lambda p: [x for x in p if x != "Experience"], "HEADINGS"),
        (lambda p: [x.replace("2021", "2O21") if "2021" in x else x for x in p], "DATES"),
        (lambda p: [*p, "Bro�en glyph"], "GLYPHS"),
        (lambda p: [*p, " icon"], "GLYPHS"),
        (lambda p: [*p, "(cid:42)"], "GLYPHS"),
        (lambda p: [*p, "Rockstar ninja guru"], "ONLY_VISIBLE_TEXT"),
        (lambda p: [*p, HIDDEN_LINE], "HIDDEN_ABSENT"),
        (lambda p: [*p, HIDDEN_PROJECT], "HIDDEN_ABSENT"),
        (lambda p: [*p, "Open data connector"], "HIDDEN_ABSENT"),
    ],
)
def test_each_check_fails_on_the_damage_it_names(damage, check: str) -> None:  # type: ignore[no-untyped-def]
    status = _status(damage(list(SHOWN)))
    assert status[check] == "FAIL", status


def test_a_line_linked_to_a_requirement_must_survive() -> None:
    data = DOC.model_dump(mode="json")
    line = data["experience"][0]["bullets"][0]
    line["requirement_ids"] = ["req-1"]
    doc = upgrade_resume_document(data)
    html = render_html(doc, mode="print").html
    kept = check_export("DOCX", doc, html, docx_bytes(doc, html), revision_sha256=SHA)
    dropped = check_export(
        "DOCX",
        doc,
        html,
        _docx([b.text for b in blocks(html) if b.text != line["text"]]),
        revision_sha256=SHA,
    )
    assert {c["check"]: c["status"] for c in kept["checks"]}["SUPPORTED_TERMS"] == "PASS"
    assert {c["check"]: c["status"] for c in dropped["checks"]}["SUPPORTED_TERMS"] == "FAIL"
    assert dropped["verified"] is False


def test_json_round_trip_fails_on_any_change() -> None:
    good = json_bytes(DOC)
    assert check_export("JSON", DOC, HTML, good, revision_sha256=SHA)["verified"]
    changed = good.replace(b"Morgan", b"Morgen")
    for data in (changed, b"{not json", b'{"schema_version": "9.9"}'):
        report = check_export("JSON", DOC, HTML, data, revision_sha256=SHA)
        assert report["checks"] == [{"check": "ROUND_TRIP", "status": "FAIL"}]


def test_there_is_no_score_anywhere() -> None:
    report = check_export("DOCX", DOC, HTML, docx_bytes(DOC, HTML), revision_sha256=SHA)
    text = json.dumps(report).lower()
    assert "score" not in text and "%" not in text and "percent" not in text
    assert {c["status"] for c in report["checks"]} <= {"PASS", "WARNING", "FAIL", "NOT_MEASURED"}


def test_a_filename_is_the_persons_name_never_you() -> None:
    assert filename(DOC, "PDF") == "Morgan_Conceição_Exemplo_Resume.pdf"
    tailored = DOC.model_dump(mode="json")
    tailored.update(
        kind="TAILORED",
        target={"jd_snapshot_id": DOC.id, "title": "Engenheiro GenIA / Sênior"},
    )
    name = filename(upgrade_resume_document(tailored), "DOCX")
    assert name == "Morgan_Conceição_Exemplo_Engenheiro_GenIA_Sênior_Resume.docx"

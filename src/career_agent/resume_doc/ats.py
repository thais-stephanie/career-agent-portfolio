"""Re-read an exported resume and say, check by check, what survived.

A real applicant tracking system is not available here and they all differ,
so there is NO percentage, no "ATS score" and no prediction of passing.
Each check is a named, plain fact about the file: PASS, WARNING, FAIL, or
NOT_MEASURED when this format or this run cannot tell (never shown as a
pass). The file is read back the way a parser would: pypdf for a PDF,
python-docx for a DOCX, `upgrade_resume_document` for JSON.

What the file SHOULD say comes from the renderer's own print HTML (`blocks`),
the one place that decides what is visible, in what order, with which
headings and dates. Text is compared by its letters: NFKC (a ligature is
its letters), case folded, whitespace removed, because a PDF breaks lines
wherever it likes, including after a hyphen.
"""

from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from pydantic import ValidationError

from career_agent.resume_doc.models import (
    ResumeDocument,
    UnsupportedSchemaVersion,
    content_sha256,
    upgrade_resume_document,
)

PASS, WARNING, FAIL, NOT_MEASURED = "PASS", "WARNING", "FAIL", "NOT_MEASURED"
#: A last page holding fewer letters than this is called nearly empty.
SHORT_LAST_PAGE = 200

# ---------------------------------------------------------------- blocks


@dataclass
class Block:
    """One paragraph of the rendered resume, in reading order."""

    kind: str
    ref: str
    runs: list[tuple[str, bool]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(text for text, _ in self.runs)


#: The renderer's paragraph classes, and what each is in a document.
_KINDS = {
    "rv-name": "name",
    "rv-contact": "contact",
    "rv-headline": "headline",
    "rv-h2": "heading",
    "rv-h3": "title",
    "rv-meta": "meta",
    "rv-text": "text",
    "rv-skills": "skills",
}
_SKIPPED = ("head", "style", "title")


class _Reader(HTMLParser):
    """The renderer's print HTML as paragraphs. It reads only the renderer's
    own fixed vocabulary; it is not a general HTML converter."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[Block] = []
        self.current: Block | None = None
        self.depth = self.bold = self.skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIPPED:
            self.skip += 1
        elif self.current is not None:
            self.depth += 1
            self.bold += tag == "strong"
        else:
            values = dict(attrs)
            kind = "bullet" if tag == "li" else _KINDS.get(values.get("class") or "")
            if kind:
                self.current, self.depth = Block(kind, values.get("data-ref") or ""), 0

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIPPED:
            self.skip -= 1
        elif self.current is not None and self.depth == 0:
            self.blocks.append(self.current)
            self.current = None
        elif self.current is not None:
            self.depth -= 1
            self.bold -= tag == "strong"

    def handle_data(self, data: str) -> None:
        if self.current is not None and not self.skip:
            self.current.runs.append((data, self.bold > 0))


def blocks(html: str) -> list[Block]:
    reader = _Reader()
    reader.feed(html)
    reader.close()
    return [b for b in reader.blocks if b.text.strip()]


# --------------------------------------------------------------- reading


def squash(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text).casefold())


def words(text: str) -> set[str]:
    return set(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold()))


def read_pdf(data: bytes) -> list[str]:
    """The text layer, one string per page."""
    from pypdf import PdfReader

    return [page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages]


def read_docx(data: bytes) -> list[str]:
    """Every paragraph of the body, in order."""
    from docx import Document

    return [p.text for p in Document(io.BytesIO(data)).paragraphs]


def _lines(node: Any) -> list[dict[str, Any]]:
    """Every line object (anything with `text`) anywhere in a document dump."""
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if isinstance(node.get("text"), str):
            found.append(node)
        for value in node.values():
            found += _lines(value)
    elif isinstance(node, list):
        for value in node:
            found += _lines(value)
    return found


#: The words a hidden node would print.
_SHOWN_KEYS = (
    "text", "label", "name", "heading", "employer", "display_title", "institution", "degree",
)  # fmt: skip


def _hidden(doc: ResumeDocument) -> list[tuple[str, str]]:
    """(id, words) of everything the document holds hidden: hidden nodes and
    everything inside a hidden section."""
    data = doc.model_dump(mode="json")
    off = set(data["layout"]["hidden_sections"])
    found: list[tuple[str, str]] = []

    def walk(node: Any, hidden: bool, owner: str) -> None:
        if isinstance(node, dict):
            hidden = hidden or node.get("hidden") is True
            owner = node.get("id") or owner
            if hidden:
                found.extend((owner, node[k]) for k in _SHOWN_KEYS if isinstance(node.get(k), str))
            for value in node.values():
                walk(value, hidden, owner)
        elif isinstance(node, list):
            for value in node:
                walk(value, hidden, owner)

    for section in ("headline", "summary", "experience", "projects", "education"):
        walk(data[section], section in off, section)
    walk(data["certifications"], "certifications" in off, "certifications")
    walk(data["skills"], "skills" in off, "skills")
    for custom in data["custom_sections"]:
        walk(custom, f"custom:{custom['id']}" in off, custom["id"])
    return found


_BAD_GLYPHS = re.compile(r"[�-\x00-\x08\x0b\x0c\x0e-\x1f]|\(cid:\d+\)")
_YEAR = re.compile(r"\b(19|20)\d\d\b")

# ---------------------------------------------------------------- checks


def _check(name: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"check": name, "status": status, **{k: v for k, v in extra.items() if v}}


def check_export(
    fmt: str,
    doc: ResumeDocument,
    html: str,
    data: bytes,
    *,
    revision_sha256: str,
    preview_pages: int | None = None,
    preview_overflow: list[str] | None = None,
) -> dict[str, Any]:
    """The named checks for one exported file. `verified` is true only when
    no check FAILED; NOT_MEASURED is said as such and never counted as a pass."""
    if fmt == "JSON":
        try:
            same = content_sha256(upgrade_resume_document(data.decode("utf-8"))) == revision_sha256
        except (ValueError, ValidationError, UnsupportedSchemaVersion):
            same = False
        return _report(fmt, [_check("ROUND_TRIP", PASS if same else FAIL)], None)

    shown = blocks(html)
    pages = read_pdf(data) if fmt == "PDF" else None
    text = "\n".join(pages if pages is not None else read_docx(data))
    flat = squash(text)
    visible = "\n".join(b.text for b in shown)

    def found(value: str) -> bool:
        return squash(value) in flat

    checks: list[dict[str, Any]] = []

    # CONTACT: the name at the top; email and phone when the resume shows them.
    who = doc.identity
    missing = [] if 0 <= flat.find(squash(who.full_name)) <= 40 else ["name"]
    for key, value in (("email", who.email), ("phone", who.phone)):
        if value and getattr(who.show, key) and not found(value):
            missing.append(key)
    checks.append(_check("CONTACT", FAIL if missing else PASS, missing=missing))

    # READING ORDER: every visible paragraph, in the layout's order.
    cursor, bad = 0, []
    for block in shown:
        at = flat.find(squash(block.text), cursor)
        if at < 0:
            bad.append(block.ref)
        else:
            cursor = at + len(squash(block.text))
    checks.append(_check("READING_ORDER", FAIL if bad else PASS, refs=bad))

    absent = [b.ref for b in shown if b.kind == "heading" and not found(b.text)]
    checks.append(_check("HEADINGS", FAIL if absent else PASS, refs=absent))

    dates = [p for b in shown if b.kind == "meta" for p in b.text.split(" | ") if _YEAR.search(p)]
    if not dates:
        checks.append(_check("DATES", NOT_MEASURED))
    else:
        unsafe = [d for d in dates if not re.fullmatch(r"[\w ./-]+", d)]
        lost = [d for d in dates if not found(d)]
        status = FAIL if lost else WARNING if unsafe else PASS
        checks.append(_check("DATES", status, params={"n": len(dates)}))

    glyphs = {m.group() for m in _BAD_GLYPHS.finditer(text)}
    checks.append(_check("GLYPHS", FAIL if glyphs else PASS, params={"n": len(glyphs)}))

    # Nothing in the file that the visible resume does not say...
    extra = words(text) - words(visible)
    checks.append(_check("ONLY_VISIBLE_TEXT", FAIL if extra else PASS, params={"n": len(extra)}))
    # ...and in particular nothing it holds hidden: a hidden line, entry,
    # item or section, except words the visible resume also says.
    lines = _lines(doc.model_dump(mode="json"))
    shown_flat = squash(visible)
    hidden = sorted(
        {
            ref
            for ref, value in _hidden(doc)
            if len(squash(value)) >= 3 and squash(value) not in shown_flat and found(value)
        }
    )
    checks.append(_check("HIDDEN_ABSENT", FAIL if hidden else PASS, refs=hidden))

    # A visible line linked to a job requirement keeps its words in the file.
    linked = [x for x in lines if x.get("requirement_ids") and squash(x["text"]) in shown_flat]
    if not linked:
        checks.append(_check("SUPPORTED_TERMS", NOT_MEASURED))
    else:
        dropped = [x["id"] for x in linked if not found(x["text"])]
        checks.append(_check("SUPPORTED_TERMS", FAIL if dropped else PASS, refs=dropped))

    if pages is None:
        checks += [_check("PAGES", NOT_MEASURED), _check("LAYOUT", NOT_MEASURED)]
        return _report(fmt, checks, None)

    if preview_pages is None:
        checks.append(_check("PAGES", NOT_MEASURED, params={"n": len(pages)}))
    else:
        same = preview_pages == len(pages)
        params = {"n": len(pages), "preview": preview_pages}
        checks.append(_check("PAGES", PASS if same else FAIL, params=params))

    issues = ["TALLER_THAN_PAGE"] if preview_overflow else []
    if len(pages) > 1 and len(squash(pages[-1])) < SHORT_LAST_PAGE:
        issues.append("LAST_PAGE_NEARLY_EMPTY")
    ends = [squash(b.text) for b in shown if b.kind in ("heading", "title")]
    if any(squash(page).endswith(end) for page in pages[:-1] for end in ends):
        issues.append("HEADING_AT_PAGE_END")
    checks.append(
        _check("LAYOUT", WARNING if issues else PASS, issues=issues, refs=preview_overflow)
    )
    return _report(fmt, checks, len(pages))


def _report(fmt: str, checks: list[dict[str, Any]], pages: int | None) -> dict[str, Any]:
    return {
        "format": fmt,
        "pages": pages,
        "checks": checks,
        "verified": not any(c["status"] == FAIL for c in checks),
    }

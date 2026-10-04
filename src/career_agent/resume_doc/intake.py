"""An uploaded resume, read into source lines: the import's untrusted edge.

Bytes in, `SourceLine`s out, and nothing decided about what any line means
(`resume_doc.parse` does that). PDF and DOCX become the SAME line shape, so
one parser serves both formats.

Untrusted input, treated as such:

* the bytes are read in memory and never written to disk;
* the extension must name the container AND the bytes must be one: a PDF
  starts with `%PDF-`, a DOCX is a ZIP holding a WordprocessingML main part
  (a macro-enabled `.docm` renamed `.docx` is refused, so is any `vbaProject`);
* sizes are bounded before parsing: the file, the PDF's page count, the
  DOCX's member count, each member's declared size and their total (the ZIP
  reader never inflates a member past its declared size), and the text kept;
* a PDF needing a password is refused with that reason; nothing in a PDF is
  run (no JavaScript, no actions), and link targets are read as text, never
  fetched; a DOCX's external relationships are never followed;
* a PDF with no text layer is refused as `NO_TEXT`, never returned as an
  empty resume. There is no OCR.

Every refusal is an `ImportRefused` whose `code` the page words; no message
quotes the file's contents.
"""

from __future__ import annotations

import io
import re
import unicodedata
import zipfile
from dataclasses import dataclass, field, replace
from typing import Any

#: The file itself. A resume is small; this refuses a mis-picked file early.
MAX_BYTES = 10 * 1024 * 1024
#: A resume is a few pages; more is not a resume or is meant to be expensive.
MAX_PAGES = 20
#: A DOCX's ZIP: entries, any one member, and all members, as declared.
MAX_MEMBERS = 400
MAX_MEMBER_BYTES = 20 * 1024 * 1024
MAX_UNPACKED_BYTES = 60 * 1024 * 1024
#: Characters kept across the whole document; the rest is reported as cut.
MAX_TEXT = 200_000
#: Less text than this across the document is not a text layer.
MIN_TEXT = 40

FORMATS = {".pdf": "PDF", ".docx": "DOCX"}
_DOCX_MAIN = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
#: A bullet glyph opening a line (hyphen, en and em dash built from code
#: points: they are other people's text, not ours).
GLYPH = re.compile(
    "^[\u2022\u25aa\u25e6\u2023\u25cf\u00b7*\\-" + chr(0x2013) + chr(0x2014) + "]\\s"
)
#: Ligatures a PDF text layer carries for "fi", "fl"...: the letters are the text.
_LIGATURES = {chr(c): unicodedata.normalize("NFKC", chr(c)) for c in range(0xFB00, 0xFB07)}


class ImportRefused(ValueError):
    """The file was not read. `code` says why, for the page to word."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class SourceLine:
    """One line of the document as it was laid out, with its typographic cues."""

    text: str
    #: Where it is, in the reader's words: "page 1, line 4", "paragraph 7",
    #: "table 1, row 1, cell 2".
    where: str
    page: int = 1
    #: Font size in points, when the file says.
    size: float | None = None
    bold: bool = False
    #: Left offset from the page's leftmost text (PDF points), or the list
    #: level (DOCX). 0 is the margin.
    indent: float = 0
    #: Space above, in multiples of the line's own size; 0 when not known.
    gap: float = 0
    #: "title", "heading" (a top-level heading), "subheading" or "list" when
    #: the file itself says so (a DOCX style or numbering, a bullet glyph).
    style: str = ""
    #: Link targets this line carries (PDF link annotations, DOCX hyperlinks).
    links: tuple[str, ...] = ()


@dataclass
class Extracted:
    format: str
    pages: int
    lines: list[SourceLine] = field(default_factory=list)
    #: Reader notes for the import report: "TEXT_CUT", "EMBEDDED_IGNORED"...
    warnings: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    text = "".join(_LIGATURES.get(ch, ch) for ch in text)
    return " ".join("".join(ch for ch in text if ch.isprintable() or ch == " ").split())


def read_upload(data: bytes, filename: str) -> Extracted:
    """Read an uploaded PDF or DOCX into lines. Raises `ImportRefused`."""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    fmt = FORMATS.get(suffix)
    if fmt is None:
        raise ImportRefused("UNSUPPORTED_TYPE")
    if not data:
        raise ImportRefused("EMPTY")
    if len(data) > MAX_BYTES:
        raise ImportRefused("TOO_LARGE")
    found = _read_pdf(data) if fmt == "PDF" else _read_docx(data)
    kept, total = [], 0
    for line in found.lines:
        if total + len(line.text) > MAX_TEXT:
            found.warnings.append("TEXT_CUT")
            break
        kept.append(line)
        total += len(line.text)
    found.lines = kept
    if total < MIN_TEXT:
        raise ImportRefused("NO_TEXT")
    return found


# ------------------------------------------------------------------- PDF


def _read_pdf(data: bytes) -> Extracted:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    if b"%PDF-" not in data[:1024]:
        raise ImportRefused("NOT_THE_TYPE")
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted and not reader.decrypt(""):
            # A viewer opens an owner-password-only PDF without asking; one
            # that needs a password to OPEN is the person's to unlock.
            raise ImportRefused("ENCRYPTED")
        if len(reader.pages) > MAX_PAGES:
            raise ImportRefused("TOO_MANY_PAGES")
        out = Extracted("PDF", len(reader.pages))
        for number, page in enumerate(reader.pages, start=1):
            out.lines += _pdf_page(page, number)
    except ImportRefused:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ImportRefused("CORRUPT") from exc
    except Exception as exc:  # noqa: BLE001  -- a third-party parser on hostile bytes
        raise ImportRefused("CORRUPT") from exc
    return out


@dataclass
class _Run:
    text: str
    x: float
    y: float
    size: float
    bold: bool


def _pdf_page(page: Any, number: int) -> list[SourceLine]:
    """pypdf's own lines (its spacing is good), with the font, size and place
    of the runs each line was built from, matched character by character."""
    runs: list[_Run] = []

    def visit(text: str, cm: list[float], tm: list[float], font: Any, size: float) -> None:
        if not text.strip():
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        scale = abs(tm[3] or tm[0]) * abs(cm[3] or cm[0])
        name = str((font or {}).get("/BaseFont", ""))
        bold = bool(re.search(r"bold|black|heavy|semibold", name, re.IGNORECASE))
        runs.append(_Run(text, x, y, size * scale, bold))

    text = page.extract_text(visitor_text=visit) or ""
    # Each squashed character of the runs, pointing at its run.
    owner: list[int] = []
    for index, run in enumerate(runs):
        owner += [index] * len("".join(run.text.split()))
    lines: list[SourceLine] = []
    cursor, left = 0, min((r.x for r in runs), default=0.0)
    previous: _Run | None = None
    for raw in text.splitlines():
        cleaned = _clean(raw)
        if not cleaned:
            continue
        width = len("".join(raw.split()))
        mine = [runs[i] for i in owner[cursor : cursor + width]] if cursor < len(owner) else []
        cursor += width
        first = mine[0] if mine else None
        size = max((r.size for r in mine), default=0) or None
        bold = bool(mine) and sum(r.bold for r in mine) * 2 > len(mine)
        gap = 0.0
        if first and previous and size:
            gap = max(0.0, (previous.y - first.y) / size)
        lines.append(
            SourceLine(
                text=cleaned,
                where=f"page {number}, line {len(lines) + 1}",
                page=number,
                size=round(size, 1) if size else None,
                bold=bold,
                indent=round(first.x - left, 1) if first else 0,
                gap=round(gap, 2),
                style="list" if GLYPH.match(cleaned) else "",
            )
        )
        previous = first or previous
    return _attach_pdf_links(page, lines)


def _attach_pdf_links(page: Any, lines: list[SourceLine]) -> list[SourceLine]:
    """A link annotation's target, on the line whose visible text names it.
    The target is read as text; nothing is fetched."""
    targets: list[str] = []
    for ref in page.get("/Annots") or []:
        try:
            note = ref.get_object()
            action = note.get("/A") or {}
            uri = action.get("/URI") if hasattr(action, "get") else None
        except Exception:  # noqa: BLE001  -- a damaged annotation is not the resume
            continue
        if isinstance(uri, str) and re.match(r"^https?://\S+$", uri) and uri not in targets:
            targets.append(uri)
    out = []
    for line in lines:
        bare = "".join(line.text.split()).casefold()
        # Printed links wrap: the line showing the start of the address holds
        # it, where an address starts (not inside an email's domain).
        mine = tuple(
            t for t in targets if re.search(r"(?<![@\w.])" + re.escape(_visible(t)[:12]), bare)
        )
        out.append(replace(line, links=mine) if mine else line)
    return out


def _visible(uri: str) -> str:
    """How a link is usually printed: no scheme, no `www.`, no trailing slash."""
    return re.sub(r"^https?://(www\.)?", "", uri).rstrip("/").casefold()


# ------------------------------------------------------------------ DOCX


def _check_zip(data: bytes) -> zipfile.ZipFile:
    if not data.startswith(b"PK"):
        raise ImportRefused("NOT_THE_TYPE")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
        members = archive.infolist()
    except (zipfile.BadZipFile, ValueError, OSError) as exc:
        raise ImportRefused("CORRUPT") from exc
    if len(members) > MAX_MEMBERS:
        raise ImportRefused("UNSAFE_CONTAINER")
    total = 0
    for member in members:
        total += member.file_size
        # A member declaring far more than it holds is how a ZIP bomb looks.
        ratio = member.file_size / max(member.compress_size, 1)
        if member.file_size > MAX_MEMBER_BYTES or (member.file_size > 1_000_000 and ratio > 200):
            raise ImportRefused("UNSAFE_CONTAINER")
    if total > MAX_UNPACKED_BYTES:
        raise ImportRefused("UNSAFE_CONTAINER")
    names = {m.filename for m in members}
    if "[Content_Types].xml" not in names or "word/document.xml" not in names:
        raise ImportRefused("NOT_THE_TYPE")
    if any(n.lower().endswith("vbaproject.bin") for n in names):
        raise ImportRefused("MACROS")
    types = archive.read("[Content_Types].xml").decode("utf-8", "replace")
    if _DOCX_MAIN not in types:
        # A macro-enabled or template main part is not a .docx.
        raise ImportRefused("MACROS" if "macroEnabled" in types else "NOT_THE_TYPE")
    return archive


def _read_docx(data: bytes) -> Extracted:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    archive = _check_zip(data)
    out = Extracted("DOCX", 1)
    if any(n.startswith(("word/embeddings/", "word/activeX/")) for n in archive.namelist()):
        out.warnings.append("EMBEDDED_IGNORED")
    try:
        word = Document(io.BytesIO(data))
        body = word.element.body
        blank, tables = 0, 0
        for child in body.iterchildren():
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "p":
                line = _docx_line(Paragraph(child, word), f"paragraph {len(out.lines) + 1}", blank)
                if line is None:
                    blank += 1
                    continue
                out.lines.append(line)
                blank = 0
            elif tag == "tbl":
                tables += 1
                # A table is source text (a header grid, a skills grid); its
                # layout is not kept, the cells are read in reading order.
                for r, row in enumerate(Table(child, word).rows, start=1):
                    for c, cell in enumerate(row.cells, start=1):
                        for paragraph in cell.paragraphs:
                            where = f"table {tables}, row {r}, cell {c}"
                            line = _docx_line(paragraph, where, blank)
                            if line is not None and (
                                not out.lines
                                or out.lines[-1].where != where
                                or out.lines[-1].text != line.text
                            ):
                                out.lines.append(line)
                                blank = 0
    except ImportRefused:
        raise
    except Exception as exc:  # noqa: BLE001  -- a third-party parser on hostile bytes
        raise ImportRefused("CORRUPT") from exc
    return out


def _docx_line(paragraph: Any, where: str, blank_before: int) -> SourceLine | None:
    text = _clean(paragraph.text)
    if not text:
        return None
    style_name = (paragraph.style.name if paragraph.style is not None else "").lower()
    numbered = paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    if style_name == "title":
        style = "title"
    elif style_name in ("heading", "heading 1"):
        style = "heading"
    elif style_name.startswith("heading"):
        style = "subheading"
    elif numbered or "list" in style_name or GLYPH.match(text):
        style = "list"
    else:
        style = ""
    runs = [r for r in paragraph.runs if r.text.strip()]
    style_font = paragraph.style.font if paragraph.style is not None else None
    bold = bool(runs) and all(
        r.bold or (r.bold is None and style_font is not None and style_font.bold) for r in runs
    )
    sizes = [r.font.size.pt for r in runs if r.font.size is not None]
    if not sizes and style_font is not None and style_font.size is not None:
        sizes = [style_font.size.pt]
    level = 0
    if numbered and paragraph._p.pPr.numPr.ilvl is not None:
        level = int(paragraph._p.pPr.numPr.ilvl.val or 0)
    links = tuple(
        h.address
        for h in paragraph.hyperlinks
        if h.address and re.match(r"^https?://\S+$", h.address)
    )
    return SourceLine(
        text=text,
        where=where,
        size=max(sizes) if sizes else None,
        bold=bold,
        indent=level,
        gap=2.0 if blank_before else 0,
        style=style,
        links=links,
    )

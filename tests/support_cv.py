"""Synthetic CVs for tests, in every format the reader accepts.

The fixture files in `tests/fixtures/cv` write `{EN}` and `{EM}` where a real
CV has an en or em dash (see the README there). `load_cv` puts the real
characters back, so the reader is tested against what people actually write.

`as_docx` and `as_pdf` build the same text as a Word document and a PDF in
memory, so one fixture proves all four formats.
"""

from __future__ import annotations

import io
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "cv"
EN_DASH = chr(0x2013)
EM_DASH = chr(0x2014)


def load_cv(name: str) -> str:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    return text.replace("{EN}", EN_DASH).replace("{EM}", EM_DASH)


def long_cv(companies: int = 8, bullets: int = 13, *, markdown: bool = False) -> str:
    """A long career: `companies` jobs with `bullets` achievements each."""
    lines = ["# Sam Invented" if markdown else "Sam Invented", ""]
    lines.append("## Experience" if markdown else "EXPERIENCE")
    for company in range(companies):
        start, end = 2000 + company * 2, 2001 + company * 2
        name = f"Company {chr(65 + company % 26)}{company:02d}"
        if markdown:
            lines += ["", f"### {name} {EM_DASH} Consultant", f"*Jan {start} {EN_DASH} Dec {end}*"]
        else:
            lines += ["", f"Consultant, {name}, Jan {start} - Dec {end}"]
        for bullet in range(bullets):
            lines.append(
                f"- Delivered workstream {bullet} for client {company} with measurable results"
            )
    lines += ["", "## Skills" if markdown else "SKILLS", "SQL, Python, Excel, Salesforce, HubSpot"]
    return "\n".join(lines) + "\n"


def as_docx(text: str) -> bytes:
    """The same CV as a Word document: one paragraph per line."""
    import docx

    document = docx.Document()
    for line in text.splitlines():
        document.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _pdf_escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def as_pdf(text: str) -> bytes:
    """The same CV as a minimal one-font PDF with a real text layer.

    Written by hand rather than with a PDF library, because the project does
    not depend on one for writing. WinAnsi covers Portuguese accents; a line is
    one text-showing operator, which is how most exported CVs come out.
    """
    lines = text.splitlines()
    pages = [lines[i : i + 50] for i in range(0, max(len(lines), 1), 50)]
    objects: list[bytes] = []
    font_id = 3 + 2 * len(pages)
    kids = " ".join(f"{3 + 2 * n} 0 R" for n in range(len(pages)))
    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    for n, page in enumerate(pages):
        body = ["BT", "/F1 10 Tf", "12 TL", "40 800 Td"]
        for line in page:
            body.append(f"({_pdf_escape(line)}) Tj T*")
        body.append("ET")
        stream = "\n".join(body).encode("cp1252", errors="replace")
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842]"
            f" /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {4 + 2 * n} 0 R >>".encode()
        )
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, obj in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode())
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    trailer = f"<< /Size {len(objects) + 1} /Root 1 0 R >>"
    out.write(f"trailer\n{trailer}\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()

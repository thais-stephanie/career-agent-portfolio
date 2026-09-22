# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Actual page counts for the DOCX export, and the local DOCX → PDF conversion.

The pipeline's page estimate is a layout model; the "Max 2 pages" promise is verified against a
real render when one is available on this machine, in this order:

* **Microsoft Word** (COM automation through a PowerShell subprocess: no Python binding needed).
  The document is opened invisibly and read-only, repaginated, its page statistic read, and Word
  is closed in a ``finally`` block. The source file is never edited.
* **LibreOffice** headless: DOCX → PDF in a temporary directory, pages counted with pypdf.
* nothing: the caller keeps the estimator and must say so (``source == "estimator"``).

The same two renderers produce the PDF export (:func:`docx_to_pdf`): Word's own PDF export
(``ExportAsFixedFormat``) or LibreOffice's headless conversion, always from a temporary copy of
the DOCX, always on this machine — there is no cloud converter. Word's DOCX measurement remains
the canonical page constraint; a PDF's own count is reported, never forced.

Neither renderer is a dependency of the core pipeline. ``RESUME_TAILOR_PAGE_RENDERER`` selects
``auto`` (default), ``word``, ``libreoffice`` or ``none``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_WORD_SCRIPT = r"""
param([string]$Path)
$w = $null; $d = $null
try {
  $w = New-Object -ComObject Word.Application
  $w.Visible = $false
  $w.DisplayAlerts = 0
  $d = $w.Documents.Open($Path, $false, $true, $false)
  $d.Repaginate()
  $n = $d.ComputeStatistics(2)
  Write-Output "PAGES=$n"
} catch {
  Write-Output "ERROR=$($_.Exception.Message)"
} finally {
  if ($d) { $d.Close(0) }
  if ($w) { $w.Quit() }
}
"""
_WORD_PDF_SCRIPT = r"""
param([string]$Path, [string]$Out)
$w = $null; $d = $null
try {
  $w = New-Object -ComObject Word.Application
  $w.Visible = $false
  $w.DisplayAlerts = 0
  $d = $w.Documents.Open($Path, $false, $true, $false)
  $d.ExportAsFixedFormat($Out, 17)  # 17 = wdExportFormatPDF; source stays untouched
  Write-Output "OK"
} catch {
  Write-Output "ERROR=$($_.Exception.Message)"
} finally {
  if ($d) { $d.Close(0) }
  if ($w) { $w.Quit() }
}
"""
_TIMEOUT = 120


@dataclass(frozen=True)
class PageCount:
    pages: int
    source: str  # "word" | "libreoffice"


def _soffice() -> str | None:
    for cand in ("soffice", "libreoffice"):
        p = shutil.which(cand)
        if p:
            return p
    for p in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
        "/usr/bin/soffice",
        "/usr/lib/libreoffice/program/soffice",
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ):
        if Path(p).exists():
            return p
    return None


def _word_pages(path: Path) -> int | None:
    if sys.platform != "win32":
        return None
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "word_pages.ps1"
        script.write_text(_WORD_SCRIPT, encoding="utf-8")
        try:
            r = subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    str(path),
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
    m = re.search(r"PAGES=(\d+)", r.stdout)
    return int(m.group(1)) if m else None


def _libreoffice_pages(path: Path) -> int | None:
    exe = _soffice()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                [exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(path)],
                capture_output=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        pdf = Path(td) / (path.stem + ".pdf")
        if not pdf.exists():
            return None
        from pypdf import PdfReader

        return len(PdfReader(str(pdf)).pages)


def _mode() -> str:
    return os.environ.get("RESUME_TAILOR_PAGE_RENDERER", "auto").strip().lower() or "auto"


@lru_cache(maxsize=1)
def available_renderer() -> str | None:
    """'word', 'libreoffice' or None, probed once per process with a one-paragraph document."""
    mode = _mode()
    if mode == "none":
        return None
    from docx import Document

    with tempfile.TemporaryDirectory() as td:
        probe = Path(td) / "probe.docx"
        d = Document()
        d.add_paragraph("probe")
        d.save(str(probe))
        if mode in ("auto", "word") and _word_pages(probe) == 1:
            return "word"
        if mode in ("auto", "libreoffice") and _libreoffice_pages(probe) == 1:
            return "libreoffice"
    return None


def count_pages(docx_bytes: bytes) -> PageCount | None:
    """Actual page count of a DOCX, or None when no renderer is available or it fails."""
    renderer = available_renderer()
    if renderer is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "resume.docx"
        path.write_bytes(docx_bytes)
        n = _word_pages(path) if renderer == "word" else _libreoffice_pages(path)
    return PageCount(pages=n, source=renderer) if n else None


def _word_pdf(path: Path, out: Path) -> bool:
    if sys.platform != "win32":
        return False
    with tempfile.TemporaryDirectory() as td:
        script = Path(td) / "word_pdf.ps1"
        script.write_text(_WORD_PDF_SCRIPT, encoding="utf-8")
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-File",
                    str(script),
                    str(path),
                    str(out),
                ],
                capture_output=True,
                text=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
    return out.exists() and out.stat().st_size > 0


def _libreoffice_pdf(path: Path) -> bytes | None:
    exe = _soffice()
    if not exe:
        return None
    with tempfile.TemporaryDirectory() as td:
        try:
            subprocess.run(
                [exe, "--headless", "--convert-to", "pdf", "--outdir", td, str(path)],
                capture_output=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        pdf = Path(td) / (path.stem + ".pdf")
        return pdf.read_bytes() if pdf.exists() else None


class PdfConversionError(RuntimeError):
    """A renderer is installed but this conversion did not produce a PDF."""


def docx_to_pdf(docx_bytes: bytes) -> tuple[bytes, str] | None:
    """Convert a DOCX to PDF with a local renderer (Word, else LibreOffice).

    Fully local — nothing is uploaded anywhere. Returns (pdf_bytes, renderer), or
    None when neither renderer is available; raises :class:`PdfConversionError`
    when a renderer exists but the conversion failed, so the two cases never share
    one error message. The source document is never modified."""
    renderer = available_renderer()
    if renderer is None:
        return None
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "resume.docx"
        src.write_bytes(docx_bytes)
        if renderer == "word":
            out = Path(td) / "resume.pdf"
            if _word_pdf(src, out):
                return out.read_bytes(), "word"
            pdf = _libreoffice_pdf(src)  # Word present but export failed: try the fallback
            if pdf:
                return pdf, "libreoffice"
        else:
            pdf = _libreoffice_pdf(src)
            if pdf:
                return pdf, "libreoffice"
    raise PdfConversionError(f"{renderer} did not produce a PDF for this document")

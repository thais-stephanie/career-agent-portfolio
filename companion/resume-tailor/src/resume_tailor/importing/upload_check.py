# Added for the Career Agent public edition (2026-09-26). See NOTICE.
"""One check for every document a person uploads (base resumes and sources).

Both paths read files through `resume_parse.extract_text`, so both accept
exactly what it can read: PDF, Word (.docx), Markdown and plain text. The
extension, the declared type and the file's own bytes must agree, so a text
file renamed `.pdf` is refused before any parser sees it.

A refusal is an `UploadRefused` with a stable `code` and the values the
sentence needs; the page words it in the reader's language (see
`api/errors.py`). The English `message` stays for any other caller.
"""

from __future__ import annotations

import hashlib
import os.path
from typing import Any

#: Extension -> (declared types a browser may send, first bytes of a real file).
DOCUMENT_TYPES: dict[str, tuple[frozenset[str], bytes | None]] = {
    ".pdf": (frozenset({"application/pdf", "application/x-pdf"}), b"%PDF-"),
    ".docx": (
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
        b"PK\x03\x04",
    ),
    ".md": (frozenset({"text/markdown", "text/x-markdown", "text/plain"}), None),
    ".markdown": (frozenset({"text/markdown", "text/x-markdown", "text/plain"}), None),
    ".txt": (frozenset({"text/plain"}), None),
}
#: What a browser sends when it does not know a file's type (common for .md).
UNDECLARED = frozenset({"", "application/octet-stream"})
MAX_DOCUMENT_BYTES = 10 * 1024 * 1024
#: For the page's file picker; the server checks the same list.
ACCEPT = ",".join(sorted(DOCUMENT_TYPES))


class UploadRefused(ValueError):
    """A document that will not be read, with a code the page can word."""

    def __init__(self, code: str, message: str, **params: Any) -> None:
        super().__init__(message)
        self.code = code
        self.params = params


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check_document(filename: str, content_type: str, data: bytes) -> None:
    """Refuse, with a reason a person can act on, anything that is not a real
    PDF, Word document or UTF-8 text/Markdown file of a sensible size."""
    name = os.path.basename(filename or "document")
    ext = os.path.splitext(name.lower())[1]
    if ext not in DOCUMENT_TYPES:
        raise UploadRefused(
            "unsupported_format",
            "Upload a PDF, Word (.docx) or Markdown (.md) file. Other files are not read.",
            file=name,
        )
    if not data:
        raise UploadRefused("empty_file", f"“{name}” is empty.", file=name)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise UploadRefused(
            "too_large", f"“{name}” is larger than 10 MB. Upload a smaller copy.", file=name, mb=10
        )
    declared, magic = DOCUMENT_TYPES[ext]
    kind = (content_type or "").split(";")[0].strip().lower()
    if kind not in declared and kind not in UNDECLARED:
        raise UploadRefused(
            "type_mismatch",
            f"“{name}” says it is {kind}, not a {ext} file. Save it again and retry.",
            file=name,
            ext=ext,
        )
    if magic is not None and not data.startswith(magic):
        raise UploadRefused(
            "not_really", f"“{name}” is not really a {ext} file.", file=name, ext=ext
        )
    if magic is None:
        if b"\x00" in data[:4096]:
            raise UploadRefused("not_text", f"“{name}” is not a text file.", file=name)
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as e:
            raise UploadRefused("not_utf8", f"“{name}” is not UTF-8 text.", file=name) from e


def unreadable(filename: str) -> UploadRefused:
    name = os.path.basename(filename or "document")
    return UploadRefused(
        "unreadable",
        f"“{name}” could not be read. Is it a complete, unprotected file?",
        file=name,
    )


def no_text(filename: str) -> UploadRefused:
    name = os.path.basename(filename or "document")
    return UploadRefused(
        "no_text",
        f"No text was found in “{name}”. A scanned PDF has no text to read;"
        " upload the Word or Markdown version instead.",
        file=name,
    )

"""A CV file, turned into text, on this machine and nowhere else.

TEXT EXTRACTION ONLY. This module reads bytes and returns characters. It does
not decide what any of them mean, and the separation is deliberate: extraction
is a mechanical operation that either works or does not, while deciding that a
line is somebody's job title is interpretation, and interpretation must be
reviewed before it becomes a fact. `cv/propose.py` does the second half and
nothing accepted here is verified by having been read.

**The file is private and stays private.** Nothing here writes the text
anywhere, logs it, or hands it to a network client. `FileTooLarge` and the
other errors deliberately name the FILE and never quote its contents, because
an error message is the easiest way for a CV to end up in a terminal history.

**Two ways in, one reader.** `extract` takes a path, for the command line.
`extract_bytes` takes bytes already in memory, for the upload the interface
makes. Both go through the same table of readers and the same limits, so the
web path cannot quietly accept something the terminal path refuses. The bytes
form never writes a temporary file: `pypdf` and `python-docx` each read a
stream, and a CV left in the system temp directory is a CV readable by
everything else on the machine for as long as the operating system keeps it.

**Untrusted input, treated as such.** A CV arrives as a binary from outside the
program and gets parsed by a third-party library:

* bounded size, checked before a single byte is read into memory;
* bounded page count, because a small file can declare very many pages;
* no rendering, no scripts, no embedded content of any kind executed;
* `python-docx` reads OOXML through `lxml` with entity resolution off by
  default, which is what closes the XML external entity door.

`pypdf` and `python-docx` are imported INSIDE the readers that need them, so a
person who never imports a CV never loads either.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import BinaryIO

#: 25 MB. Generous for a CV and small enough that a mis-selected file is
#: refused rather than read. A person who genuinely has a larger one can
#: export a smaller copy; nobody should discover the limit by watching memory.
MAX_BYTES = 25 * 1024 * 1024

#: A CV is not a book. A file declaring more pages than this is either not a
#: CV or is trying to make extraction expensive.
MAX_PAGES = 60

#: What can be read, by extension. A whitelist rather than sniffing: the point
#: is to refuse surprises, and a file this list does not name is a surprise.
SUPPORTED = frozenset({".pdf", ".docx", ".txt", ".md"})


class CvError(RuntimeError):
    """Something about the file, said without quoting a word of it."""


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """The characters, and enough about where they came from to be checked.

    `text` is the only thing carrying candidate data and it is never written
    to disk by this module. What a caller does with it is that caller's
    responsibility, and the CLI keeps it in memory.
    """

    text: str
    source_name: str
    kind: str
    pages: int
    characters: int

    @property
    def looks_empty(self) -> bool:
        """A file that yielded almost nothing.

        Usually a scanned CV: a PDF whose pages are images has no text layer,
        and returning an empty string with no explanation would look like a
        bug in this program rather than a property of the document.
        """
        return self.characters < 200


def _check_file(path: Path) -> str:
    if not path.exists() or not path.is_file():
        raise CvError(f"{path.name} is not a file that exists.")

    suffix = path.suffix.lower()
    if suffix not in SUPPORTED:
        readable = ", ".join(sorted(SUPPORTED))
        raise CvError(f"{path.name} is not a kind this reads. Supported: {readable}.")

    size = path.stat().st_size
    if size > MAX_BYTES:
        # Checked from the filesystem, before opening. A size read after
        # loading is a size read too late.
        raise CvError(
            f"{path.name} is {size // (1024 * 1024)} MB, over the "
            f"{MAX_BYTES // (1024 * 1024)} MB limit."
        )
    if size == 0:
        raise CvError(f"{path.name} is empty.")
    return suffix


def _read_pdf(stream: BinaryIO, name: str) -> tuple[str, int]:
    from pypdf import PdfReader

    reader = PdfReader(stream)
    if len(reader.pages) > MAX_PAGES:
        raise CvError(f"{name} has {len(reader.pages)} pages, over the {MAX_PAGES} limit.")

    parts: list[str] = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 - a damaged page is data, not a crash
            # Named without quoting. One unreadable page in a CV is common and
            # the rest of the document is still worth having.
            raise CvError(f"{name} has a page this could not read: {type(exc).__name__}") from exc
    return "\n".join(parts), len(reader.pages)


def _read_docx(stream: BinaryIO, name: str) -> tuple[str, int]:
    import docx

    document = docx.Document(stream)
    parts = [paragraph.text for paragraph in document.paragraphs]
    # Tables carry real content in a CV -- skills grids, dated employment
    # rows -- and reading only paragraphs silently loses them.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts), 1


def _read_text(stream: BinaryIO, name: str) -> tuple[str, int]:
    # `errors="replace"` rather than a crash: a CV saved in an unusual encoding
    # is still worth reading, and one mangled character is a smaller problem
    # than refusing the document.
    return stream.read().decode("utf-8", errors="replace"), 1


#: Which reader answers for which extension. ONE table, so the path form and
#: the bytes form cannot drift into supporting different kinds.
_READERS: dict[str, object] = {
    ".pdf": _read_pdf,
    ".docx": _read_docx,
    ".txt": _read_text,
    ".md": _read_text,
}


def safe_name(raw: str) -> str:
    """The file's own name, with every trace of a path removed.

    A browser sends whatever the operating system put in the file input, and
    some send a full path. Nothing here ever opens this string -- the bytes
    arrive separately, and that is the property that makes traversal
    impossible rather than merely unlikely -- but it is stored and displayed,
    and `C:/Users/someone/Documents/cv.pdf` on a screen or inside a backup is
    a fact about somebody's filesystem that a CV review has no reason to carry.
    """
    bare = PureWindowsPath(PurePosixPath(raw.strip()).name).name.strip()
    # A leading dot would let `..` through as a name. A control character would
    # let a terminal reading a backup do something other than print.
    cleaned = "".join(ch for ch in bare if ch.isprintable()).lstrip(".").strip()
    return cleaned[:120] or "cv"


def _suffix_of(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        readable = ", ".join(sorted(SUPPORTED))
        raise CvError(f"{name} is not a kind this reads. Supported: {readable}.")
    return suffix


def _assemble(text: str, *, source_name: str, kind: str, pages: int) -> ExtractedText:
    normalised = "\n".join(line.rstrip() for line in text.splitlines())
    return ExtractedText(
        text=normalised,
        source_name=source_name,
        kind=kind,
        pages=pages,
        characters=len(normalised.strip()),
    )


def _read(suffix: str, stream: BinaryIO, name: str) -> tuple[str, int]:
    reader = _READERS[suffix]
    return reader(stream, name)  # type: ignore[operator, no-any-return]


def extract(path: Path) -> ExtractedText:
    """Read one CV into text. Makes no network call and writes nothing."""
    suffix = _check_file(path)

    try:
        with path.open("rb") as stream:
            text, pages = _read(suffix, stream, path.name)
    except CvError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party parsers raise widely
        raise CvError(f"{path.name} could not be read: {type(exc).__name__}") from exc

    return _assemble(text, source_name=path.name, kind=suffix.lstrip("."), pages=pages)


def extract_bytes(data: bytes, filename: str) -> ExtractedText:
    """One CV, read from bytes already in memory. Writes no temporary file."""
    name = safe_name(filename)
    suffix = _suffix_of(name)
    if not data:
        raise CvError(f"{name} is empty.")
    if len(data) > MAX_BYTES:
        raise CvError(
            f"{name} is {len(data) // (1024 * 1024)} MB, over the "
            f"{MAX_BYTES // (1024 * 1024)} MB limit."
        )

    try:
        text, pages = _read(suffix, io.BytesIO(data), name)
    except CvError:
        raise
    except Exception as exc:  # noqa: BLE001 - third-party parsers raise widely
        raise CvError(f"{name} could not be read: {type(exc).__name__}") from exc

    return _assemble(text, source_name=name, kind=suffix.lstrip("."), pages=pages)

"""A CV line, as the words it says rather than the syntax it was written in.

People write CVs in Markdown, and some export them that way from a note-taking
tool without knowing it. Before this module the reader treated every character
as content: `### Fabrikam Cloud` became a claim with three hashes in it,
`**Integration:** Workato` became `*Integration:** Workato`, and a line the
person would read as "a heading" arrived in their review as a statement about
their career.

Two outputs per line, and the separation is the point:

* `text` is the plain words, for a person to read and confirm;
* `raw` is the line exactly as the document had it, kept as provenance.

Nothing here renders Markdown. The output is plain text and the interface
shows it as text; an imported document never becomes HTML.

**Ordinary punctuation survives.** Only syntax that is unambiguously Markdown
is removed: paired emphasis markers around a word, a link's brackets, a code
span's backticks, a heading's hashes at the start of a line. A lone asterisk
in "5 * 3", the underscores in `snake_case`, "C#" and "R&D" are characters the
person wrote and stay exactly as written.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: `# Title` to `###### Title`, with optional closing hashes.
_ATX = re.compile(r"^\s{0,3}(#{1,6})(?:\s+(.*?))?(?:\s+#+)?\s*$")
#: A setext underline: a line of `=` makes the line above a level-1 heading.
#: A line of `-` directly under text is the level-2 form, handled with care
#: because the same characters alone are a horizontal rule.
_SETEXT_1 = re.compile(r"^\s{0,3}=+\s*$")
_SETEXT_2 = re.compile(r"^\s{0,3}-+\s*$")
#: `---`, `***`, `___`, optionally spaced. Decoration, never content.
_RULE = re.compile(r"^\s{0,3}([-*_])(?:\s*\1){2,}\s*$")
#: A fenced code block opens and closes with three backticks or tildes.
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
#: `> quoted`, possibly nested.
_QUOTE = re.compile(r"^\s{0,3}(?:>\s?)+")
#: A list item: `-`, `*`, `+`, a bullet glyph a PDF leaves, or `1.` / `1)`.
_BULLET = re.compile(r"^(\s*)(?:[-*+•‣●▪·]|\d{1,3}[.)])\s+")
#: A bullet glyph glued to its text, which PDF text layers often produce.
_GLYPH = re.compile(r"^(\s*)[•‣●▪]\s*")
#: A table's separator row, `|---|:--:|`.
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")

#: Inline syntax, in the order it is undone. Images before links, because an
#: image IS a link with a `!` in front of it.
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_LINK = re.compile(r"\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)")
_REF_LINK = re.compile(r"\[([^\]]+)\]\[[^\]]*\]")
_AUTOLINK = re.compile(r"<((?:https?://|mailto:)[^>\s]+)>")
_CODE = re.compile(r"(`+)(.+?)\1")
#: Emphasis. `**x**` and `__x__` are strong, `*x*` and `_x_` are emphasis. The
#: underscore forms must not touch a word on either side, which is what keeps
#: `snake_case_name` intact; the asterisk forms must not open onto a space,
#: which is what keeps "5 * 3 * 2" intact.
_STRONG_STAR = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_STRONG_UNDER = re.compile(r"(?<![\w_])__(?=\S)(.+?)(?<=\S)__(?![\w_]|\.\w)")
_EM_STAR = re.compile(r"(?<![\w*])\*(?=[^\s*])(.+?)(?<=[^\s*])\*(?![\w*])")
_EM_UNDER = re.compile(r"(?<![\w_])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![\w_]|\.\w)")
_STRIKE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~")
#: A doubled marker left over from malformed Markdown -- `**bold` with no end.
#: Two asterisks or underscores in a row are not punctuation anybody writes.
_STRAY_DOUBLE = re.compile(r"(?:\*\*|(?<![\w_.])__(?![\w_])|(?<![\w_])__(?=\s|$))")
#: A backslash escape, `\*`, which means the character itself.
_ESCAPE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|>~])")
#: Inline HTML a Markdown export sometimes carries. Removed as markup; the
#: words between the tags stay.
_TAG = re.compile(r"</?(?:br|b|i|em|strong|u|span|sup|sub|small|p|div)\b[^>]*>", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Line:
    """One source line, classified, with its words and its original.

    `kind` is one of:

    * ``heading`` -- `level` is 1 to 6;
    * ``item``    -- a list item; `level` is its indentation depth;
    * ``text``    -- an ordinary line;
    * ``rule``, ``fence``, ``code``, ``table_rule``, ``blank`` -- structure or
      decoration, never a claim.
    """

    number: int
    raw: str
    kind: str
    level: int
    text: str
    #: Whether the line was written with explicit Markdown heading syntax. A
    #: `#` heading is a strong structural signal; a short line in capitals is
    #: only a weak one.
    marked: bool = False


#: The longest line whose inline syntax is read. The emphasis patterns are
#: quadratic when a marker never closes, and a CV is untrusted input up to
#: 25 MB with no line limit: a 64 KB line of "**a " took over 20 seconds.
#: A line longer than any claim (`propose._MAX_LENGTH` is 2000) is kept as
#: written, whitespace collapsed, and never proposed.
MAX_INLINE = 2000


def inline(text: str) -> str:
    """One run of Markdown inline syntax, as plain text."""
    if len(text) > MAX_INLINE:
        return re.sub(r"\s+", " ", text).strip()
    out = _ESCAPE.sub(lambda m: "\x00" + str(ord(m.group(1))) + "\x00", text)
    out = _TAG.sub(" ", out)
    out = _IMAGE.sub(r"\1", out)
    out = _LINK.sub(r"\1", out)
    out = _REF_LINK.sub(r"\1", out)
    out = _AUTOLINK.sub(r"\1", out)
    out = _CODE.sub(lambda m: m.group(2).strip(), out)
    # Strong before emphasis, repeated, because `***x***` is both.
    for _ in range(3):
        before = out
        out = _STRONG_STAR.sub(r"\1", out)
        out = _STRONG_UNDER.sub(r"\1", out)
        out = _STRIKE.sub(r"\1", out)
        out = _EM_STAR.sub(r"\1", out)
        out = _EM_UNDER.sub(r"\1", out)
        if out == before:
            break
    out = _STRAY_DOUBLE.sub("", out)
    out = re.sub("\x00(\\d+)\x00", lambda m: chr(int(m.group(1))), out)
    return re.sub(r"\s+", " ", out).strip()


def _table_row(text: str) -> str:
    cells = [cell.strip() for cell in text.strip().strip("|").split("|")]
    return " | ".join(cell for cell in cells if cell)


def classify(text: str) -> list[Line]:
    """Every line of a document, classified. Nothing is dropped.

    Lines inside a fenced code block are `code`: a CV that pastes a snippet is
    showing something, and a line of it is not a statement about a career.
    """
    raw_lines = text.splitlines()
    out: list[Line] = []
    fence: str | None = None

    for number, raw in enumerate(raw_lines, start=1):
        if len(raw) > MAX_INLINE and fence is None:
            # Too long to be a heading, an item or a claim: plain text, read
            # without any of the patterns below.
            out.append(Line(number, raw, "text", 0, inline(raw)))
            continue
        if fence is not None:
            if raw.strip().startswith(fence):
                fence = None
                out.append(Line(number, raw, "fence", 0, ""))
            else:
                out.append(Line(number, raw, "code", 0, raw.strip()))
            continue
        opened = _FENCE.match(raw)
        if opened:
            fence = opened.group(1)[0] * 3
            out.append(Line(number, raw, "fence", 0, ""))
            continue
        if not raw.strip():
            out.append(Line(number, raw, "blank", 0, ""))
            continue

        quoted = bool(_QUOTE.match(raw))
        body = _QUOTE.sub("", raw) if quoted else raw
        previous = out[-1] if out else None
        if previous is not None and _QUOTE.match(previous.raw) and not quoted:
            # A setext underline never reaches back into a quotation.
            previous = None

        if _SETEXT_1.match(body) and previous is not None and previous.kind == "text":
            out[-1] = Line(previous.number, previous.raw, "heading", 1, previous.text, True)
            out.append(Line(number, raw, "rule", 0, ""))
            continue
        if (
            _SETEXT_2.match(body)
            and previous is not None
            and previous.kind == "text"
            and len(body.strip()) >= 2
        ):
            out[-1] = Line(previous.number, previous.raw, "heading", 2, previous.text, True)
            out.append(Line(number, raw, "rule", 0, ""))
            continue
        if _RULE.match(body):
            out.append(Line(number, raw, "rule", 0, ""))
            continue
        if _TABLE_RULE.match(body) and "|" in body:
            out.append(Line(number, raw, "table_rule", 0, ""))
            continue

        heading = _ATX.match(body)
        if heading:
            words = inline(heading.group(2) or "")
            kind = "heading" if words else "rule"
            out.append(Line(number, raw, kind, len(heading.group(1)), words, True))
            continue

        bullet = _BULLET.match(body) or _GLYPH.match(body)
        if bullet:
            depth = len(bullet.group(1).expandtabs(4)) // 2
            words = inline(body[bullet.end() :])
            out.append(Line(number, raw, "item" if words else "rule", depth, words))
            continue

        stripped = body.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            stripped = _table_row(stripped)
        words = inline(stripped)
        out.append(Line(number, raw, "text" if words else "rule", 0, words))
    return out


def plain(text: str) -> str:
    """A whole document as plain text, one line per source line with content."""
    return "\n".join(line.text for line in classify(text) if line.text)

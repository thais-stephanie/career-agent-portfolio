"""Deterministic HTML-to-text conversion for job descriptions.

This lives in the pure domain layer, using only the standard library, because
M2 evidence verification has to reproduce *exactly* this text. A quote is
"verified" by being found in `job_raw.description_text`; if normalisation were
non-deterministic or lived somewhere that could drift, verification would start
failing for reasons that have nothing to do with the model.

The job here is conversion, never interpretation. It decodes entities, turns
markup into readable text, and preserves structure. It does not decide what any
of it means.

**Structure is the point.** This has to survive intact:

    Requirements
    - HubSpot administration
    - API integrations

    Nice to have
    - Salesforce

Flatten the section break or drop the bullets and "Salesforce is nice-to-have,
not required" becomes unreadable to M2 -- which is exactly the distinction the
product turns on. So block elements produce line breaks and list items keep a
marker, even though it would be simpler to emit a single paragraph.
"""

import hashlib
import re
from html import unescape
from html.parser import HTMLParser

#: Elements that end the current line.
BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "tr",
        "section",
        "article",
        "header",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "ul",
        "ol",
        "table",
        "blockquote",
        "pre",
        "hr",
    }
)

#: Elements that additionally leave a blank line after them, so sections stay
#: visually separated the way the author wrote them.
PARAGRAPH_TAGS = frozenset(
    {
        "p",
        "div",
        "section",
        "article",
        "ul",
        "ol",
        "table",
        "blockquote",
        "pre",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

#: Elements whose content is markup machinery, not job description text.
SKIP_CONTENT_TAGS = frozenset({"script", "style", "head", "noscript"})

_MULTI_SPACE = re.compile(r"[ \t ]+")
_MULTI_NEWLINE = re.compile(r"\n{3,}")


#: The marker a list item opens with. Kept as a constant because the parser
#: both appends it and, for an item that turns out to be empty, removes it.
LIST_MARKER = "\n- "


class _TextExtractor(HTMLParser):
    """Two pieces of state beyond the skip depth, and both earn their place.

    ``_li_depth``: how deep we are inside list items. A list item is one
    logical line, so block elements *inside* one do not open paragraph breaks.
    Without this, ``<li><p>a</p></li><li><p>b</p></li>`` renders with a blank
    line between every bullet while ``<li>a</li><li>b</li>`` does not, and the
    same document written two ways stops normalising the same way.

    ``_marker_pending``: a list marker has been emitted and its item has not
    produced any text yet. Until it does, nothing may push that marker onto a
    line of its own: not a block wrapper, not the whitespace a pretty-printer
    leaves between tags, not a ``<br>``. This is the M1C.1 fix, and it is
    deliberately expressed as a structural condition rather than as a rule about
    ``<p>`` or about any particular vendor.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._li_depth = 0
        self._marker_pending = False

    def _open_item(self) -> None:
        # A nested list inside an item that has produced nothing means the outer
        # marker belongs to no text. Drop it rather than leave it stranded.
        self._drop_unused_marker()
        self.parts.append(LIST_MARKER)
        self._li_depth += 1
        self._marker_pending = True

    def _drop_unused_marker(self) -> None:
        if self._marker_pending and self.parts and self.parts[-1] == LIST_MARKER:
            self.parts.pop()
        self._marker_pending = False

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in SKIP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "li":
            self._open_item()
        elif tag in BLOCK_TAGS:
            if self._marker_pending:
                return  # the marker already opened this line
            self.parts.append("\n")

    def handle_startendtag(self, tag: str, attrs: object) -> None:
        if tag in ("br", "hr") and not self._skip_depth and not self._marker_pending:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in SKIP_CONTENT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return

        if tag == "li":
            self._drop_unused_marker()
            self._li_depth = max(0, self._li_depth - 1)
            self.parts.append("\n")
            return
        if self._marker_pending:
            return  # still nothing between the marker and its text

        if tag in PARAGRAPH_TAGS:
            # Inside a list item a block break is a line break, not a paragraph
            # break: the item is one entry in a list, not a section of prose.
            self.parts.append("\n" if self._li_depth else "\n\n")
        elif tag in BLOCK_TAGS:
            self.parts.append("\n")
        elif tag in ("td", "th"):
            # Cells within a row are separated, not concatenated. Compensation
            # bands are frequently laid out as tables, and "80,000100,000" would
            # be worse than useless.
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._marker_pending:
            # Whitespace between a marker and its content is markup formatting,
            # not content. Emitting it would strand the marker just as surely as
            # a block tag would.
            if not data.strip():
                return
            self._marker_pending = False
        self.parts.append(data)


def _looks_escaped(value: str) -> bool:
    """True when the string carries escaped markup rather than markup.

    Greenhouse returns `content` as HTML that has been entity-escaped inside the
    JSON string, so it arrives as `&lt;p&gt;` rather than `<p>`. Some boards
    escape twice, giving `&amp;lt;p&amp;gt;`, so the test is "no real tags but
    there are entities" rather than looking for `&lt;` specifically.
    """
    return "<" not in value and "&" in value


def html_to_text(raw_html: str) -> str:
    """Readable plain text, with block structure and list markers preserved."""
    if not raw_html:
        return ""

    markup = raw_html
    # Unescape at most twice: once for the usual single-escaped case, once for
    # boards that double-escape. Bounded so a pathological input cannot loop.
    for _ in range(2):
        if _looks_escaped(markup):
            markup = unescape(markup)
        else:
            break

    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    return collapse_whitespace("".join(parser.parts))


def collapse_whitespace(text: str) -> str:
    """Tidy spacing without destroying line structure.

    Runs of spaces and non-breaking spaces collapse to one; trailing spaces go;
    three or more consecutive newlines become two. Single and double newlines
    are left alone, because they are what carries section and bullet structure.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _MULTI_SPACE.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _MULTI_NEWLINE.sub("\n\n", text)
    return text.strip()


def content_hash(description_text: str) -> str:
    """The content address of a job description.

    Deterministic and computed from the normalised text, so an identical
    posting reposted or duplicated across companies costs one stored row and,
    later, one extraction.
    """
    return hashlib.sha256(description_text.encode("utf-8")).hexdigest()

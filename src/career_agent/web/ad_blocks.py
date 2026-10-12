"""A posting's ad as blocks the page can lay out. Presentation only.

The ad is stored twice: the plain text every reader, score and citation works
from, and (for most boards) the markup the employer published. The page used
to lay out the plain text by splitting it on blank lines, which turned every
list item of an HTML ad into its own paragraph with a paragraph's margin, and
glued the "Label: value" lines at the top of an ad into one run of text.

This module builds a list of BLOCKS instead:

    {"t": "h", "x": "About the role"}
    {"t": "p", "r": [<run>, ...]}
    {"t": "ul" | "ol", "i": [{"r": [<run>...], "s": <block>|None}, ...], "plain": bool}
    {"t": "meta", "rows": [{"k": "Location", "r": [<run>...]}, ...]}

A run is a string, or `{"x": text, "b": 1, "i": 1, "a": href}`. The page lays
the blocks out with DOM text nodes only; no markup from the employer ever
reaches `innerHTML`. Safety is therefore structural: tags outside the small
set below are dropped (their text kept), script and style content is dropped,
images and other embedded resources are never represented, and a link keeps
its address only for http, https and mailto.

NOTHING HERE CHANGES WHAT THE AD SAYS. The text stored for matching and for
quotes is untouched; this only decides spacing, lists, headings and metadata,
from structure the employer wrote. It does not summarise, reword or drop a
line, and it does not invent a heading: in plain text a heading is a line that
ends in a colon, one written in capitals, or a short unpunctuated line sitting
directly above a longer text.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from typing import Any

__all__ = ["ad_blocks"]

MAX_BLOCKS = 800
MAX_CHARS = 300_000
_SAFE_HREF = re.compile(r"^(?:https?://|mailto:)", re.IGNORECASE)
_SKIP = {
    "script",
    "style",
    "iframe",
    "object",
    "embed",
    "svg",
    "noscript",
    "template",
    "head",
    "title",
}
_BLOCK = {
    "p", "div", "section", "article", "blockquote", "tr", "table", "tbody", "thead", "header",
    "footer", "aside", "main", "figure", "dl", "dt", "dd", "pre", "form", "fieldset",
}  # fmt: skip
_HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
_META_LINE = re.compile(r"^([A-Za-zÀ-ÿ][^:\n]{1,40}):\s*(\S.*)$")
_BULLET = re.compile(r"^\s*(?:[-*•·▪●–]|\d{1,2}[.)])\s+(\S.*)$")
_END_PUNCT = ".!?;,"


# ------------------------------------------------------------------- runs


def _text_of(runs: list[Any]) -> str:
    return "".join(r if isinstance(r, str) else r["x"] for r in runs)


def _with_text(run: Any, text: str) -> Any:
    return text if isinstance(run, str) else {**run, "x": text}


def _tidy(runs: list[Any]) -> list[Any]:
    """Collapse whitespace inside runs (keeping line breaks), trim both ends."""
    out: list[Any] = []
    for run in runs:
        text = run if isinstance(run, str) else run["x"]
        text = re.sub(r"[ \t\r\f\v ]+", " ", text)
        text = re.sub(r" ?\n ?", "\n", text)
        out.append(_with_text(run, text))
    while out and not _text_of([out[0]]).strip():
        out.pop(0)
    while out and not _text_of([out[-1]]).strip():
        out.pop()
    if out:
        out[0] = _with_text(out[0], _text_of([out[0]]).lstrip())
        out[-1] = _with_text(out[-1], _text_of([out[-1]]).rstrip())
    merged: list[Any] = []
    for run in out:
        if merged and isinstance(run, str) and isinstance(merged[-1], str):
            merged[-1] += run
        else:
            merged.append(run)
    return [r for r in merged if _text_of([r])]


def _lines_of(runs: list[Any]) -> list[list[Any]]:
    """Split runs at line breaks, keeping each run's formatting."""
    lines: list[list[Any]] = [[]]
    for run in runs:
        for n, part in enumerate(_text_of([run]).split("\n")):
            if n:
                lines.append([])
            if part:
                lines[-1].append(_with_text(run, part))
    kept = [line for line in lines if _text_of(line).strip()]
    for line in kept:
        line[0] = _with_text(line[0], _text_of([line[0]]).lstrip())
    return [line for line in kept if _text_of(line)]


def _after_label(line: list[Any], skip: int) -> list[Any]:
    """The runs of a line after its `Label:` prefix (`skip` characters)."""
    out: list[Any] = []
    left = skip
    for run in line:
        text = _text_of([run])
        if left >= len(text):
            left -= len(text)
            continue
        out.append(_with_text(run, text[left:]))
        left = 0
    return out


def _classify(runs: list[Any]) -> dict[str, Any] | None:
    """A paragraph of runs as the block it is: metadata, a bold sub-heading, or text."""
    runs = _tidy(runs)
    if not runs:
        return None
    lines = _lines_of(runs)
    texts = [_text_of(line).strip() for line in lines]
    if len(lines) >= 2 and all(_META_LINE.match(t) for t in texts):
        rows = []
        for line, text in zip(lines, texts, strict=True):
            label = _META_LINE.match(text).group(1).strip()  # type: ignore[union-attr]
            value = _tidy(_after_label(line, len(text.split(":", 1)[0]) + 1))
            rows.append({"k": label, "r": value})
        return {"t": "meta", "rows": rows}
    whole = _text_of(runs).strip()
    bold_all = all(isinstance(r, dict) and r.get("b") for r in runs if _text_of([r]).strip())
    if bold_all and len(lines) == 1 and len(whole) <= 90 and not whole.endswith(tuple(_END_PUNCT)):
        return {"t": "h", "x": whole}
    return {"t": "p", "r": runs}


# ------------------------------------------------------------------- HTML


class _Ad(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict[str, Any]] = []
        self.runs: list[Any] = []
        self.bold = 0
        self.italic = 0
        self.hrefs: list[str | None] = []
        self.skip = 0
        self.heading = False
        # Each open list: {"t": "ul"|"ol", "i": [items]}; the current item of
        # the innermost list collects inline runs.
        self.lists: list[dict[str, Any]] = []
        self.item: list[dict[str, Any] | None] = []

    def _target(self) -> list[Any]:
        if self.lists and self.item and self.item[-1] is not None:
            return self.item[-1]["r"]  # type: ignore[index]
        return self.runs

    def _add(self, text: str) -> None:
        if not text:
            return
        href = next((h for h in reversed(self.hrefs) if h), None)
        if self.bold or self.italic or href:
            run: dict[str, Any] = {"x": text}
            if self.bold:
                run["b"] = 1
            if self.italic:
                run["i"] = 1
            if href:
                run["a"] = href
            self._target().append(run)
        else:
            self._target().append(text)

    def _flush(self) -> None:
        if self.heading:
            text = re.sub(r"\s+", " ", _text_of(_tidy(self.runs))).strip()
            if text:
                self.blocks.append({"t": "h", "x": text})
            self.heading = False
        else:
            block = _classify(self.runs)
            if block is not None:
                self.blocks.append(block)
        self.runs = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self.skip += 1
            return
        if self.skip:
            return
        if tag in _HEADINGS:
            self._flush()
            self.heading = True
        elif tag in ("ul", "ol"):
            if not self.lists:
                self._flush()
            self.lists.append({"t": tag, "i": []})
            self.item.append(None)
        elif tag == "li":
            if not self.lists:
                self._flush()
                self.lists.append({"t": "ul", "i": []})
                self.item.append(None)
            entry: dict[str, Any] = {"r": [], "s": None}
            self.lists[-1]["i"].append(entry)
            self.item[-1] = entry
        elif tag == "br":
            self._add("\n")
        elif tag in ("strong", "b"):
            self.bold += 1
        elif tag in ("em", "i"):
            self.italic += 1
        elif tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            self.hrefs.append(href if _SAFE_HREF.match(href) else None)
        elif tag in _BLOCK and not self.lists:
            self._flush()
        elif tag in ("td", "th"):
            self._add(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if tag in _HEADINGS:
            self._flush()
        elif tag in ("ul", "ol") and self.lists:
            self._close_list()
        elif tag in ("strong", "b"):
            self.bold = max(0, self.bold - 1)
        elif tag in ("em", "i"):
            self.italic = max(0, self.italic - 1)
        elif tag == "a" and self.hrefs:
            self.hrefs.pop()
        elif tag in _BLOCK and not self.lists:
            self._flush()

    def _close_list(self) -> None:
        done = self.lists.pop()
        self.item.pop()
        items = [_item(e) for e in done["i"] if _text_of(e["r"]).strip() or e["s"]]
        block = {"t": done["t"], "i": items}
        if self.lists and self.item and self.item[-1] is not None:
            self.item[-1]["s"] = block  # type: ignore[index]
        elif items:
            self.blocks.append(block)

    def handle_data(self, data: str) -> None:
        if self.skip or not data:
            return
        # Source line feeds are whitespace; only <br> breaks a line.
        self._add(re.sub(r"\s+", " ", data))

    def finish(self) -> list[dict[str, Any]]:
        while self.lists:
            self._close_list()
        self._flush()
        return self.blocks


def _item(entry: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {"r": _tidy(entry["r"])}
    if entry["s"]:
        out["s"] = entry["s"]
    return out


def _from_html(html: str) -> list[dict[str, Any]]:
    parser = _Ad()
    parser.feed(html)
    parser.close()
    return parser.finish()


# ------------------------------------------------------------------- text


def _is_heading(line: str) -> bool:
    """A heading in plain text by its own marks: a trailing colon, or capitals."""
    if len(line) > 70 or line.endswith(tuple(".!?;,")):
        return False
    if line.endswith(":"):
        return len(line) > 1
    letters = [c for c in line if c.isalpha()]
    return len(letters) >= 3 and line.upper() == line


def _heading(line: str) -> dict[str, Any]:
    return {"t": "h", "x": line.rstrip(":").strip()}


def _short_stack(group: list[str]) -> bool:
    """A run of single-line paragraphs: a list the source separated with blank
    lines (`Zapier`, `Make`, `n8n`, or four one-sentence duties in a row)."""
    return len(group) >= 4 and all(len(g) <= 120 and len(g.split()) <= 20 for g in group)


def _title_over_text(lines: list[str]) -> bool:
    """A short unpunctuated first line directly above a longer text: a heading by
    layout (`About the role`, then a paragraph)."""
    return (
        len(lines) >= 2
        and len(lines[0]) <= 50
        and not lines[0].endswith(tuple(_END_PUNCT))
        and sum(len(x) for x in lines[1:]) >= 80
    )


def _is_meta_pair(lines: list[str], i: int) -> bool:
    return (
        bool(_META_LINE.match(lines[i]))
        and i + 1 < len(lines)
        and bool(_META_LINE.match(lines[i + 1]))
    )


def _segments(lines: list[str]) -> list[dict[str, Any]]:
    """One blank-line paragraph, split where its lines change kind."""
    out: list[dict[str, Any]] = []
    i = 0
    while i < len(lines):
        if _BULLET.match(lines[i]):
            j = i
            while j < len(lines) and _BULLET.match(lines[j]):
                j += 1
            out.append(
                {
                    "t": "ol" if re.match(r"^\s*\d", lines[i]) else "ul",
                    "i": [{"r": [_BULLET.match(x).group(1)]} for x in lines[i:j]],  # type: ignore[union-attr]
                }
            )
            i = j
        elif _is_meta_pair(lines, i):
            j = i
            rows = []
            while j < len(lines) and _META_LINE.match(lines[j]):
                m = _META_LINE.match(lines[j])
                rows.append({"k": m.group(1).strip(), "r": [m.group(2).strip()]})  # type: ignore[union-attr]
                j += 1
            out.append({"t": "meta", "rows": rows})
            i = j
        else:
            j = i + 1
            while j < len(lines) and not _BULLET.match(lines[j]) and not _is_meta_pair(lines, j):
                j += 1
            chunk = lines[i:j]
            if _is_heading(chunk[0]):
                out.append(_heading(chunk[0]))
                chunk = chunk[1:]
            elif _title_over_text(chunk):
                out.append({"t": "h", "x": chunk[0]})
                chunk = chunk[1:]
            if chunk:
                out.append({"t": "p", "r": ["\n".join(chunk)]})
            i = j
    return out


def _from_text(text: str) -> list[dict[str, Any]]:
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = [p for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    blocks: list[dict[str, Any]] = []
    i = 0
    while i < len(paragraphs):
        lines = [ln.strip() for ln in paragraphs[i].split("\n") if ln.strip()]
        if len(lines) == 1 and not _BULLET.match(lines[0]) and not _is_heading(lines[0]):
            j = i
            group: list[str] = []
            while j < len(paragraphs):
                one = [ln.strip() for ln in paragraphs[j].split("\n") if ln.strip()]
                if len(one) != 1 or _BULLET.match(one[0]) or _is_heading(one[0]):
                    break
                group.append(one[0])
                j += 1
            if _short_stack(group):
                blocks.append({"t": "ul", "plain": True, "i": [{"r": [g]} for g in group]})
                i = j
                continue
        blocks.extend(_segments(lines))
        i += 1
    return blocks


# ------------------------------------------------------------------- entry


def _block_text(block: dict[str, Any]) -> str:
    if block["t"] == "h":
        return str(block["x"])
    if block["t"] == "p":
        return _text_of(block["r"])
    if block["t"] == "meta":
        return " ".join(_text_of(r["r"]) for r in block["rows"])
    return " ".join(_text_of(i["r"]) for i in block["i"])


def ad_blocks(html: str | None, text: str) -> list[dict[str, Any]]:
    """The ad's layout: the employer's own markup when the board published
    structure, otherwise the plain text; empty when there is nothing to show."""
    html = html or ""
    text = text or ""
    blocks: list[dict[str, Any]] = []
    structured = re.search(r"<\s*(?:p|ul|ol|li|h\d|br|div)\b", html, re.IGNORECASE)
    if html.strip() and len(html) <= MAX_CHARS and structured:
        blocks = _from_html(html)
        # Markup that yielded almost nothing (an empty shell) falls back to text.
        if sum(len(_block_text(b)) for b in blocks) < min(40, len(text.strip())):
            blocks = []
    if not blocks and text.strip():
        blocks = _from_text(text[:MAX_CHARS])
    return blocks[:MAX_BLOCKS]

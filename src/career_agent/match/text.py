"""Folding text for comparison while keeping every offset into the original.

The matcher compares against a case-folded, accent-stripped, whitespace-collapsed
copy of the posting, because "Integração de Sistemas" and "integracao de sistemas"
are the same sentence and a Portuguese posting is a first-class posting. But
ADR-0002 says an evidence quote is a quote that exists: it must be verifiable as
a contiguous substring of the text the employer actually wrote.

Those two requirements pull in opposite directions, and the offset map is what
reconciles them. `fold` returns the comparison copy *and* an index from every
folded character back to the original character it came from, so a match found
in the folded copy is always cut out of the original. Nothing downstream ever
quotes the folded text.

The map cannot be arithmetic. Casefolding changes length ("ß" folds to "ss"),
NFKD decomposition changes length, and collapsing whitespace changes length --
each by a different amount at a different place. So the general map is built
character by character as the fold happens.

**A folded field is a value, not a step.** `FoldedText` exists because folding
is the expensive part of matching and the answer does not depend on who is
asking: fifty lexicon signals, a dozen blockers and the seniority reader all
want the same folded copy of the same description. Folding once per FIELD and
handing the result around is the difference between a rescore of the corpus
that takes minutes and one that takes hours. Nothing in `FoldedText` is derived
from the configuration, which is what makes it safe to share across every
signal, gate and component that reads the same posting.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache

#: What ends a sentence, for the purpose of cutting a quote. Newline and
#: semicolon are included because job descriptions are mostly bullet lists, and
#: a bullet is a sentence whether or not it ends in a full stop.
SENTENCE_BREAKS = frozenset(".!?\n;")

#: A quote longer than this is elided. Long enough to carry a compound
#: eligibility statement, short enough that a whole "About us" paragraph pasted
#: into one line does not become the evidence.
MAX_QUOTE_CHARS = 240

#: Single character, so a truncated quote is exactly MAX_QUOTE_CHARS long and a
#: caller can tell truncation from content.
ELLIPSIS = "…"

#: What `split_sections` reports. Deliberately strings rather than an enum: they
#: are compared against `SignalHit.section`, which the domain declares as a
#: `str | None` so a later source can add headings without a vocabulary change.
PRIMARY_SECTION = "PRIMARY"
SECONDARY_SECTION = "SECONDARY"

Section = tuple[int, int, str | None]

#: The ten codepoints below U+0080 for which `str.isspace()` is true: tab, LF,
#: VT, FF, CR, the four information separators, and the space itself. Spelled
#: out rather than written `\s`, because `re` widens `\s` to Unicode whitespace
#: and this class is only ever applied to text already known to be ASCII.
_ASCII_RUN = re.compile(r"[^ \t\n\v\f\r\x1c\x1d\x1e\x1f]+")

#: A maximal stretch of a posting that holds nothing above U+007F. Real
#: descriptions are not "ASCII" or "Unicode" -- they are ASCII with a curly
#: apostrophe in it, and 295 of 300 corpus postings sampled were non-ASCII by a
#: handful of characters in six thousand. Splitting on this lets the fast path
#: do the other 5,994.
_ASCII_SPAN = re.compile(r"[\x00-\x7f]+")

#: A maximal run of the character class the pattern lookarounds are written in.
#: The class is `[0-9a-z]` and not `\w` on purpose: it must be the SAME class,
#: because `lexicon.CompiledPatterns` uses these tokens to prove a phrase cannot
#: occur, and a wider or narrower notion of "inside a token" would break that
#: proof in one direction or the other. Folded text carries no ASCII capital
#: (see `_fold_ascii_span`), so the class needs no case-insensitive twin.
_TOKEN = re.compile(r"[0-9a-z]+")

#: The folded form of one character, memoised. The key space is the alphabet of
#: the postings actually read -- a few hundred codepoints across an English and
#: a Portuguese corpus -- so it converges within the first posting and every
#: character after that is a dict hit instead of two `unicodedata` calls.
#: Capped, so that pathological input cannot turn a cache into a leak.
_FOLDED_CHARACTER: dict[str, str] = {}
_FOLDED_CHARACTER_CAP = 8192


@dataclass(frozen=True, slots=True)
class FoldedText:
    """One field of a posting, folded once, carrying everything read from it.

    `folded` is what patterns are matched against; `original` is what quotes are
    cut from; `offsets[i]` is the index in `original` of folded character `i`.
    `sections` is empty for a field that has no headings -- a title -- which is
    exactly what those call sites used to pass as a bare `()`.

    `tokens` is every `[0-9a-z]` run in `folded`, which is what lets a caller
    dismiss a phrase in a set lookup instead of a search over the whole field.
    """

    original: str
    folded: str
    offsets: tuple[int, ...]
    sections: tuple[Section, ...] = ()
    tokens: frozenset[str] = frozenset()


def _fold_character(character: str) -> str:
    decomposed = unicodedata.normalize("NFKD", character)
    without_marks = "".join(part for part in decomposed if not unicodedata.combining(part))
    return without_marks.casefold()


def _folded_form(character: str) -> str:
    produced = _FOLDED_CHARACTER.get(character)
    if produced is None:
        produced = _fold_character(character)
        if len(_FOLDED_CHARACTER) < _FOLDED_CHARACTER_CAP:
            _FOLDED_CHARACTER[character] = produced
    return produced


def _fold_ascii_span(
    text: str,
    start: int,
    end: int,
    glyphs: list[str],
    offsets: list[int],
    space_pending: bool,
) -> bool:
    """Fold `text[start:end]`, known to hold nothing above U+007F, in bulk.

    SAFE because of three properties of ASCII, not because it is close enough.
    No codepoint below U+0080 has a canonical or a compatibility decomposition,
    so NFKD is the identity on every one of them and no combining mark can be
    produced for the general path to strip. `casefold` and `lower` agree on all
    of them, and both are length-preserving: there is no ASCII counterpart of
    "ß" folding to "ss". So the folded span is exactly `lower()` with its
    whitespace runs collapsed, and the offset map across any surviving run is
    the identity -- which is what lets a whole word be copied and indexed
    wholesale instead of a character at a time.

    That turns the general path's `unicodedata` lookup and two list appends per
    CHARACTER into one slice and one `range` extension per WORD.

    Appends into `glyphs` / `offsets` and returns the carried whitespace flag,
    because a span is a fragment of a document and a whitespace run may straddle
    its edges.
    """
    lowered = text[start:end].lower()
    cursor = 0
    for run in _ASCII_RUN.finditer(lowered):
        run_start, run_end = run.span()
        if run_start > cursor:
            space_pending = True
        if space_pending and glyphs:
            # A run of any whitespace -- newlines included -- becomes one space,
            # so a phrase written across a line break still matches. It carries
            # the offset of the character that FOLLOWS it, which is what the
            # character-by-character path recorded.
            glyphs.append(" ")
            offsets.append(start + run_start)
        space_pending = False
        glyphs.append(lowered[run_start:run_end])
        offsets.extend(range(start + run_start, start + run_end))
        cursor = run_end
    return space_pending or cursor < len(lowered)


def _fold_character_span(
    text: str,
    start: int,
    end: int,
    glyphs: list[str],
    offsets: list[int],
    space_pending: bool,
) -> bool:
    """Fold `text[start:end]` one character at a time: accents, ligatures, "ß"."""
    for index in range(start, end):
        for glyph in _folded_form(text[index]):
            if glyph.isspace():
                space_pending = True
                continue
            if space_pending and glyphs:
                glyphs.append(" ")
                offsets.append(index)
            space_pending = False
            glyphs.append(glyph)
            offsets.append(index)
    return space_pending


def _fold_ascii(text: str) -> tuple[str, list[int]]:
    glyphs: list[str] = []
    offsets: list[int] = []
    _fold_ascii_span(text, 0, len(text), glyphs, offsets, False)
    return "".join(glyphs), offsets


def _fold_mixed(text: str) -> tuple[str, list[int]]:
    """Fold text that has at least one character above U+007F.

    Not by giving up on the fast path: a posting is not "Unicode text", it is
    ASCII with three curly apostrophes and an em dash in it. Each maximal ASCII
    stretch is folded in bulk and only the handful of characters between them
    pay for `unicodedata`. The pending-whitespace flag is threaded across the
    boundaries so a run of spaces split between two spans still collapses to
    the single space the character-by-character reading produced.
    """
    glyphs: list[str] = []
    offsets: list[int] = []
    space_pending = False
    cursor = 0

    for span in _ASCII_SPAN.finditer(text):
        start, end = span.span()
        if start > cursor:
            space_pending = _fold_character_span(
                text, cursor, start, glyphs, offsets, space_pending
            )
        space_pending = _fold_ascii_span(text, start, end, glyphs, offsets, space_pending)
        cursor = end
    if cursor < len(text):
        _fold_character_span(text, cursor, len(text), glyphs, offsets, space_pending)

    return "".join(glyphs), offsets


def _fold(text: str) -> tuple[str, list[int]]:
    """The one door onto folding, so a test can count how often it opens."""
    return _fold_ascii(text) if text.isascii() else _fold_mixed(text)


def fold(text: str) -> tuple[str, tuple[int, ...]]:
    """Return the comparison copy of `text` and a map back into `text`.

    ``offset_map[i]`` is the index in the ORIGINAL string of folded character
    ``i``. The map is always exactly as long as the folded string.
    """
    folded, offsets = _fold(text)
    return folded, tuple(offsets)


def fold_field(text: str, sections: Sequence[Section] = ()) -> FoldedText:
    """Fold one field of a posting once, for every reader of it."""
    folded, offsets = _fold(text)
    return FoldedText(
        original=text,
        folded=folded,
        offsets=tuple(offsets),
        sections=tuple(sections),
        tokens=frozenset(_TOKEN.findall(folded)),
    )


def _fold_plain(text: str) -> str:
    return _fold(text)[0]


@lru_cache(maxsize=64)
def _folded_headings(headings: tuple[str, ...]) -> tuple[str, ...]:
    """The configured headings, folded once per configuration rather than per posting."""
    return tuple(_fold_plain(heading) for heading in headings)


def _heading_kind(
    folded_line: str, primary: tuple[str, ...], secondary: tuple[str, ...]
) -> str | None:
    if any(folded_line.startswith(heading) for heading in primary):
        return PRIMARY_SECTION
    if any(folded_line.startswith(heading) for heading in secondary):
        return SECONDARY_SECTION
    return None


def _may_be_a_heading(stripped: str, initials: frozenset[str]) -> bool:
    """Whether the line could possibly start with a configured heading.

    `startswith` can only succeed if the first character of the folded line is
    the first character of some heading, so one dict lookup dismisses the
    bullet lines and prose sentences that make up almost all of a posting
    without folding them. When the first character folds to nothing or to
    whitespace -- a soft hyphen, a figure space -- the shortcut declines to
    answer and the full fold decides, because then the folded line begins
    somewhere this test cannot see.
    """
    produced = _folded_form(stripped[0])
    if not produced or produced[0].isspace():
        return True
    return produced[0] in initials


def split_sections(
    text: str,
    primary_headings: list[str],
    secondary_headings: list[str],
) -> list[Section]:
    """Cut `text` into `(start, end, kind)` spans at its headings.

    A heading is a line whose folded, stripped content *starts with* one of the
    configured headings, because real postings write "Responsibilities:" and
    "What you'll do at Acme" rather than the bare phrase. Text before the first
    heading is a span with `kind=None`: it exists and can be quoted, it just
    does not lend prominence to anything found in it.
    """
    primary = _folded_headings(tuple(primary_headings))
    secondary = _folded_headings(tuple(secondary_headings))
    initials = frozenset(heading[0] for heading in primary + secondary if heading)

    boundaries: list[tuple[int, str | None]] = []
    position = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped and _may_be_a_heading(stripped, initials):
            kind = _heading_kind(_fold_plain(stripped), primary, secondary)
            if kind is not None:
                boundaries.append((position, kind))
        position += len(line)

    if not boundaries or boundaries[0][0] != 0:
        boundaries.insert(0, (0, None))

    sections: list[Section] = []
    for index, (start, kind) in enumerate(boundaries):
        end = boundaries[index + 1][0] if index + 1 < len(boundaries) else len(text)
        sections.append((start, end, kind))
    return sections


def section_kind_at(sections: Sequence[Section], offset: int) -> str | None:
    for start, end, kind in sections:
        if start <= offset < end:
            return kind
    return None


def sentence_start(text: str, offset: int) -> int:
    left = min(offset, len(text))
    while left > 0 and text[left - 1] not in SENTENCE_BREAKS:
        left -= 1
    return left


def sentence_at(text: str, start: int, end: int, *, max_chars: int = MAX_QUOTE_CHARS) -> str:
    """The sentence containing `text[start:end]`, cut from the ORIGINAL text.

    The result is a contiguous substring of `text` -- leading and trailing
    whitespace is stripped, which preserves contiguity -- unless it exceeded
    `max_chars`, in which case it ends in a single ellipsis character and is
    exactly `max_chars` long.
    """
    left = sentence_start(text, start)
    right = max(end, left)
    while right < len(text) and text[right] not in SENTENCE_BREAKS:
        right += 1

    quote = text[left:right].strip()
    if len(quote) > max_chars:
        quote = quote[: max_chars - 1] + ELLIPSIS
    return quote

"""Where an advert written without line breaks had its lines (2026-10-16).

Gupy adverts reach the corpus as one run of text: the template's headings
(`Responsabilidades e atribuicoes`, `Requisitos e qualificacoes`,
`Informacoes adicionais`) and every bullet glued to the words around them
(`...no banco.Requisitos e qualificacoesDominio de Kotlin`). Read as one
"sentence", the first benefit word anywhere in it set aside the whole advert
(package 3: 29 of 30 Portuguese duties lost by context sat in such units).

This module never rewrites the advert. It returns OFFSETS where a line would
have broken, a derived structure the sentence cutter reads as virtual line
ends; the canonical text, its hash and every evidence offset are unchanged.

High precision first, and only where the advert carries the template:

* it applies only to a text holding at least two of the template's headings
  (the Gupy signature), so no other source is touched;
* a template heading is cut out on both sides;
* a sentence end glued to a capitalised word (`continuas.Atuar`) is a line
  end, unless the run is a URL, an e-mail or a dotted name (`Node.JS`: the
  next word must be a capitalised WORD, a capital then a lower-case letter);
* a bullet glyph or a run of emoji glued to text starts a line.

Anything else is left as it was: an unsure cut is not made.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

#: The template headings, as written (case and accents folded when found).
TEMPLATE_HEADINGS: tuple[str, ...] = (
    "Responsabilidades e atribuições",
    "Requisitos e qualificações",
    "Informações adicionais",
    "Etapas do processo",
    "Responsabilidades y atribuciones",
    "Requisitos y calificaciones",
    "Información adicional",
    "Etapas del proceso",
    "Responsibilities and assignments",
    "Requirements and qualifications",
    "Additional information",
)


@lru_cache(maxsize=4096)
def _fold_char(ch: str) -> str:
    base = unicodedata.normalize("NFD", ch)[0]
    folded = base.lower() if len(base) == 1 else ch.lower()
    return folded if len(folded) == 1 else ch


def _fold(text: str) -> str:
    """Lower-case without accents, one character per character (offsets hold)."""
    if text.isascii():
        return text.lower()
    return "".join(map(_fold_char, text))


#: Words every template heading holds one of (folded): a text with none of
#: them cannot carry the template, and is not folded at all.
_ANCHORS = ("responsab", "requisit", "informa", "responsib", "requirement", "additional",
            "etapas")  # fmt: skip


_HEADINGS = re.compile("|".join(re.escape(_fold(h)) for h in TEMPLATE_HEADINGS))
#: A sentence end glued to the next sentence's first word.
_GLUED_END = re.compile(r"(?<=[a-zà-ÿ0-9)%\"”])[.!?](?=[A-ZÀ-Ý][a-zà-ÿ])")
#: A bullet glyph with text right before it.
_GLUED_BULLET = re.compile(r"(?<=\S)[•·▪●‣⁃](?=\s*\S)")
#: A dash bullet right after a sentence end (`seguras.- Desenvolver`).
_GLUED_DASH = re.compile(r"(?<=[.!?:;])-(?=\s?[A-ZÀ-Ý])")
_URLISH = re.compile(r"(?:https?://|www\.|@)\S*$")


def _is_emoji(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F000 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or cp in (0xFE0F, 0x200D)


def is_template(text: str) -> bool:
    """Whether `text` carries the Gupy template (two of its headings)."""
    return len({m.group(0) for m in _HEADINGS.finditer(_fold(text))}) >= 2


def cuts(text: str, *, require_template: bool = True) -> tuple[int, ...]:
    """Offsets at which a virtual line starts. Empty for a text without the
    template (`require_template=False` is for measuring the glue rules on
    other text only). Never an offset already at a line start."""
    if require_template:
        lowered = text.lower()
        if sum(a in lowered for a in _ANCHORS) < 2:
            return ()
    folded = _fold(text)
    if require_template and len({m.group(0) for m in _HEADINGS.finditer(folded)}) < 2:
        return ()
    found: set[int] = set()
    for m in _HEADINGS.finditer(folded):
        # The template writes its headings capitalised; `for additional
        # information, please` is a sentence.
        if text[m.start()].isupper():
            found.update((m.start(), m.end()))
    for m in _GLUED_END.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        word_start = max(text.rfind(" ", line_start, m.start()) + 1, line_start)
        if _URLISH.search(text[word_start : m.start() + 1]):
            continue
        found.add(m.end())
    for m in _GLUED_BULLET.finditer(text):
        found.add(m.start())
    for m in _GLUED_DASH.finditer(text):
        found.add(m.start())
    i = 0
    while i < len(text):
        if _is_emoji(text[i]):
            j = i
            while j < len(text) and (_is_emoji(text[j]) or text[j] == " "):
                j += 1
            if i > 0 and not text[i - 1].isspace():
                found.add(i)
            if j < len(text) and j > i:
                found.add(j)
            i = j
        else:
            i += 1
    return tuple(
        sorted(c for c in found if 0 < c < len(text) and text[c - 1] != "\n" and text[c] != "\n")
    )


def encode(offsets: tuple[int, ...]) -> str:
    """Compact: gaps between offsets, space separated."""
    out, last = [], 0
    for c in offsets:
        out.append(str(c - last))
        last = c
    return " ".join(out)


def decode(text: str) -> tuple[int, ...]:
    out, last = [], 0
    for gap in text.split():
        last += int(gap)
        out.append(last)
    return tuple(out)

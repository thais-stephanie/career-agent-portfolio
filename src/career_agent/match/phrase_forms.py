"""A person's own phrase, recognised in the other ways a posting writes the same words.

The first-run wizard files each activity a person types as ONE literal
pattern, its own label: "AI implementation and adoption" matched only a posting
that wrote exactly those five words, so on the owner's corpus 99 of the top 100
recommendations carried at most one activity and three tool names filled the
rest of the score. The wizard was right not to invent synonyms ("putting words
in a person's mouth", `config/setup.py`), and nothing here does.

What this adds is the SAME WORDS, written differently:

* inflection, in either language: implementation / implementing / implement,
  automation / automate / automating, integracao / integrar;
* order and a little distance inside ONE sentence: "automating complex
  workflows" for "workflow automation";
* coordination the way people mean it: "CRM implementation and optimization"
  is CRM implementation, or CRM optimization; "systems and API integration" is
  systems integration, or API integration.

Every content word of a reading must appear, within `EXTRA_TOKENS` of each
other, in one sentence. The quote is that sentence, a contiguous substring of
the posting (ADR-0002). No dictionary of meanings, no model, no new intent.

It applies to WIZARD-SHAPED entries only: one pattern equal to the entry's own
label, of two or more content words. An entry somebody curated with several
explicit patterns is matched exactly as they wrote it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from functools import lru_cache

from career_agent.match.text import SENTENCE_BREAKS, FoldedText, fold

#: Words that carry no activity. Structural, in both languages.
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "with",
        "or",
        "by",
        "at",
        "as",
        "&",
        "e",
        "de",
        "da",
        "do",
        "das",
        "dos",
        "em",
        "no",
        "na",
        "nos",
        "nas",
        "para",
        "com",
        "ou",
        "por",
    }
)
_COORDINATORS = frozenset({"and", "&", "e"})

#: How far apart, in tokens, the words of one reading may sit beyond being
#: adjacent. Three admits "improve engineering speed and business processes"
#: for "business process improvement" and refuses a paragraph's worth of drift.
EXTRA_TOKENS = 3

#: Suffixes stripped, repeatedly, to reach a shared root. Both sides of every
#: comparison go through the same function, so the only requirement is that
#: two forms of one word land on one root; the root need not be a word.
_SUFFIXES = (
    "izations", "isations", "ization", "isation", "izacoes", "izacao", "acoes", "acao",
    "ations", "ation", "ements", "ement", "ments", "ment", "mentos", "mento",
    "izing", "ising", "ings", "ing", "ions", "ion", "ized", "ised", "izes", "ises",
    "ize", "ise", "ating", "ated", "ates", "ate", "ando", "ados", "adas", "ado", "ada",
    "ar", "at", "ed", "es", "s", "e", "o", "a",
)  # fmt: skip
_MIN_ROOT = 4
_TOKEN = re.compile(r"[0-9a-z]+")


@lru_cache(maxsize=65536)
def root(word: str) -> str:
    """The shared root of a folded word, or the word itself when it is short."""
    current = word
    if 4 <= len(current) <= 5 and current.endswith("s"):
        current = current[:-1]  # apis -> api, crms -> crm, llms -> llm
    changed = True
    while changed:
        changed = False
        for suffix in _SUFFIXES:
            if current.endswith(suffix) and len(current) - len(suffix) >= _MIN_ROOT:
                current = current[: -len(suffix)]
                changed = True
                break
    return current


@lru_cache(maxsize=4096)
def readings(phrase: str) -> tuple[tuple[str, ...], ...]:
    """The phrase as one or more sets of roots, each a complete reading.

    One coordinator splits the phrase in two. A multi-word side keeps the
    other side's single word attached the way the phrase shares it: "AI
    implementation and adoption" -> (ai implement, ai adopt); "systems and API
    integration" -> (system integr, api integr). Two single words, or anything
    longer, stay one reading that needs every word.
    """
    tokens = _TOKEN.findall(fold(phrase)[0].replace("&", " and "))
    coordinators = [i for i, token in enumerate(tokens) if token in _COORDINATORS]
    content = [t for t in tokens if t not in STOPWORDS]
    whole = tuple(root(t) for t in content)
    if len(coordinators) != 1:
        return (whole,) if len(whole) >= 2 else ()
    at = coordinators[0]
    left = [t for t in tokens[:at] if t not in STOPWORDS]
    right = [t for t in tokens[at + 1 :] if t not in STOPWORDS]
    if len(left) >= 2 and len(right) == 1:
        first, second = left, [*left[:-1], *right]
    elif len(left) == 1 and len(right) >= 2:
        first, second = [*left, *right[1:]], right
    else:
        return (whole,) if len(whole) >= 2 else ()
    out = tuple(tuple(root(t) for t in side) for side in (first, second) if len(side) >= 2)
    return out or ((whole,) if len(whole) >= 2 else ())


def is_wizard_shaped(label: str, patterns: tuple[str, ...]) -> bool:
    """One pattern, equal to the entry's own label, with two content words or more."""
    if len(patterns) != 1:
        return False
    return fold(patterns[0])[0].strip() == fold(label)[0].strip() and bool(readings(label))


@lru_cache(maxsize=8)
def _indexed(
    original: str, folded: str, offsets: tuple[int, ...]
) -> tuple[tuple[int, int, str, int], ...]:
    """Every token as (folded start, folded end, root, sentence number).

    Sentences are cut on the ORIGINAL text, where line breaks still exist:
    folding turns them into spaces, and a bullet list would read as one line.
    """
    breaks = [i for i, ch in enumerate(original) if ch in SENTENCE_BREAKS]
    return tuple(
        (m.start(), m.end(), root(m.group(0)), bisect_right(breaks, offsets[m.start()]))
        for m in _TOKEN.finditer(folded)
    )


def find_forms(field: FoldedText, phrase: str) -> list[tuple[int, int]]:
    """Folded spans where every word of one reading of `phrase` occurs close together.

    Spans never cross a sentence break and never overlap each other.
    """
    found: list[tuple[int, int]] = []
    options = readings(phrase)
    if not options:
        return found
    tokens = _indexed(field.original, field.folded, field.offsets)
    roots_here = {token[2] for token in tokens}
    for reading in options:
        needed = set(reading)
        if not needed <= roots_here:
            continue
        width = len(reading) + EXTRA_TOKENS
        index = 0
        while index < len(tokens):
            start, _, first, sentence = tokens[index]
            if first not in needed:
                index += 1
                continue
            missing = set(needed)
            last = index
            for offset in range(index, min(len(tokens), index + width)):
                _, _, word, where = tokens[offset]
                if where != sentence:
                    break
                if word in missing:
                    missing.discard(word)
                    last = offset
                    if not missing:
                        break
            if missing:
                index += 1
                continue
            found.append((start, tokens[last][1]))
            index = last + 1
    found.sort()
    merged: list[tuple[int, int]] = []
    for span in found:
        if merged and span[0] < merged[-1][1]:
            continue
        merged.append(span)
    return merged

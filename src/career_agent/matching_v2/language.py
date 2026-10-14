"""Words of different languages proven to be the same concept, and nothing more.

Generic: no profession, product or person appears here. The relations are
DATA (`language_data.yaml`), and only three of them ever match (ADR-0037):

* EXACT_EQUIVALENT      `sistema` is `system`;
* MORPHOLOGICAL_VARIANT `automatizar` is a form of `automacao`, by a rule
                        on roots whose examples are checked at load;
* FIXED_EXPRESSION      `levantamento de requisitos` is `requirements
                        gathering`, as a unit, never word by word.

RELATED_CONCEPT, BROADER and NARROWER are loaded so a test can say why they
do not match, and are never consulted when matching.

A token keeps its offsets in the ORIGINAL text, so any quote cut from a
match is a contiguous substring of the advert (ADR-0002).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from functools import cached_property, lru_cache
from pathlib import Path

import yaml

from career_agent.match.phrase_forms import STOPWORDS, root
from career_agent.match.text import SENTENCE_BREAKS, fold

DATA = Path(__file__).with_name("language_data.yaml")
_TOKEN = re.compile(r"[0-9a-z]+")
_MIN_ROOT = 4


class Relation(StrEnum):
    IDENTICAL = "IDENTICAL"
    EXACT_EQUIVALENT = "EXACT_EQUIVALENT"
    MORPHOLOGICAL_VARIANT = "MORPHOLOGICAL_VARIANT"
    FIXED_EXPRESSION = "FIXED_EXPRESSION"
    RELATED_CONCEPT = "RELATED_CONCEPT"
    BROADER = "BROADER"
    NARROWER = "NARROWER"


#: The relations allowed to make a match on their own.
AUTOMATIC = frozenset(
    {
        Relation.IDENTICAL,
        Relation.EXACT_EQUIVALENT,
        Relation.MORPHOLOGICAL_VARIANT,
        Relation.FIXED_EXPRESSION,
    }
)

#: Strength order, for naming the relation a match leaned on.
_RANK = {
    Relation.IDENTICAL: 0,
    Relation.MORPHOLOGICAL_VARIANT: 1,
    Relation.EXACT_EQUIVALENT: 2,
    Relation.FIXED_EXPRESSION: 3,
}

#: Function words, for telling the language of an advert. Not a model: a
#: count. An advert with too few of any is `und`.
_FUNCTION_WORDS = {
    "en": frozenset(
        {
            "the",
            "and",
            "of",
            "to",
            "in",
            "for",
            "with",
            "you",
            "we",
            "our",
            "will",
            "are",
            "is",
            "be",
            "on",
            "this",
            "that",
        }
    ),
    "pt": frozenset(
        {
            "de",
            "da",
            "do",
            "das",
            "dos",
            "para",
            "com",
            "voce",
            "nossa",
            "nosso",
            "sera",
            "e",
            "uma",
            "um",
            "em",
            "no",
            "na",
            "os",
            "as",
        }
    ),
    "es": frozenset(
        {
            "de",
            "la",
            "el",
            "los",
            "las",
            "para",
            "con",
            "usted",
            "nuestra",
            "nuestro",
            "sera",
            "y",
            "una",
            "un",
            "en",
            "del",
            "se",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class Token:
    """One canonical root, where it came from, and how it was reached."""

    root: str
    start: int  # in the ORIGINAL text
    end: int
    sentence: int
    relation: Relation = Relation.IDENTICAL
    #: The folded word as written ("" for a multi-word fixed expression).
    #: A one-word preference matches the word itself or a listed form, never
    #: a bare root: `workato` reduces to `work` (2026-10-14).
    word: str = ""


@dataclass(frozen=True)
class NonAutomatic:
    relation: Relation
    a: str
    b: str


@dataclass(frozen=True)
class LanguageTable:
    version: int
    words: dict[str, tuple[str, Relation]]  # root -> (canonical root, relation)
    fixed: dict[tuple[str, ...], tuple[str, ...]]  # root sequence -> canonical roots
    morphology: tuple[tuple[str, str], ...]  # (lang, root suffix to strip)
    non_automatic: tuple[NonAutomatic, ...] = field(default_factory=tuple)
    #: A listed form, folded, -> canonical root. Consulted before the root,
    #: because two concepts can share a root (`negocio` business,
    #: `negociacao` negotiation: both `negoci`) and the form tells them apart.
    surface: dict[str, str] = field(default_factory=dict)
    #: Roots claimed by more than one concept. Never mapped: an unlisted form
    #: reaching one of them keeps its own root and matches nothing translated.
    ambiguous: tuple[str, ...] = ()

    @cached_property
    def fixed_heads(self) -> frozenset[str]:
        """The first root of every fixed expression of two words or more: a
        token whose root is not one can open no such expression, so
        `tokens` skips the window search for it (same tokens, measured
        2026-10-14: the search was a third of tokenising)."""
        return frozenset(k[0] for k in self.fixed if len(k) > 1)

    def canonical(self, word_root: str, word: str | None = None) -> tuple[str, Relation]:
        """The canonical root of one word, through automatic relations only."""
        if word is not None and word in self.surface:
            target = self.surface[word]
            return target, (
                Relation.IDENTICAL if target == word_root else Relation.EXACT_EQUIVALENT
            )
        relation = Relation.IDENTICAL
        current = word_root
        for _lang, suffix in self.morphology:
            if current.endswith(suffix) and len(current) - len(suffix) >= _MIN_ROOT:
                current = current[: -len(suffix)]
                relation = Relation.MORPHOLOGICAL_VARIANT
                break
        # No translation by ROOT: a root can be shared across languages by
        # words that are not the same concept (`cliente` is a translation of
        # customer; `client` in English is a synonym, which would widen the
        # preference). A word is translated only when its form is listed
        # (`surface`, above). `words` stays as the record of which roots a
        # listed concept claims (`ambiguous`, `invalidation.diff`), and for the
        # one narrow case below.
        if relation is Relation.MORPHOLOGICAL_VARIANT and current in self.words:
            # A proven rule reached the root of a LISTED form's family
            # (`sistematizar` -> `sistem`, the root of the listed `sistema`).
            return self.words[current][0], relation
        return current, relation


def _content_roots(text: str) -> tuple[str, ...]:
    folded = fold(text)[0]
    return tuple(root(t) for t in _TOKEN.findall(folded) if t not in STOPWORDS)


def _first_english(entry: dict) -> str:
    return str(entry["en"][0])


@lru_cache(maxsize=4)
def load_table(path: Path = DATA) -> LanguageTable:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    claims: dict[str, set[str]] = {}
    surface: dict[str, str] = {}
    for entry in raw.get("exact_equivalent", []):
        target = _content_roots(_first_english(entry))
        if len(target) != 1:
            raise ValueError(f"an exact equivalent is one word: {entry}")
        for forms in entry.values():
            for form in forms:
                roots = _content_roots(str(form))
                if len(roots) != 1:
                    raise ValueError(f"{form!r} is not one word; make it a fixed expression")
                claims.setdefault(roots[0], set()).add(target[0])
                folded = fold(str(form))[0].strip()
                if surface.get(folded, target[0]) != target[0]:
                    raise ValueError(f"{form!r} is listed for two concepts")
                surface[folded] = target[0]
    words = {
        r: (next(iter(t)), Relation.EXACT_EQUIVALENT) for r, t in claims.items() if len(t) == 1
    }
    ambiguous = tuple(sorted(r for r, t in claims.items() if len(t) > 1))
    fixed: dict[tuple[str, ...], tuple[str, ...]] = {}
    for entry in raw.get("fixed_expression", []):
        target = _content_roots(_first_english(entry))
        for forms in entry.values():
            for form in forms:
                fixed[_content_roots(str(form))] = target
    morphology = []
    for rule in raw.get("morphology", []):
        if not rule.get("examples"):
            raise ValueError(f"a morphology rule needs the examples that prove it: {rule}")
        morphology.append((str(rule["lang"]), str(rule["strip_root_suffix"])))
    non_automatic = tuple(
        NonAutomatic(Relation(n["relation"]), str(n["a"]), str(n["b"]))
        for n in raw.get("non_automatic", [])
    )
    if any(n.relation in AUTOMATIC for n in non_automatic):
        raise ValueError("an automatic relation cannot be listed as non-automatic")
    return LanguageTable(
        version=int(raw["version"]),
        words=words,
        fixed=fixed,
        morphology=tuple(morphology),
        non_automatic=non_automatic,
        surface=surface,
        ambiguous=ambiguous,
    )


def morphology_examples(path: Path = DATA) -> list[tuple[str, str]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [tuple(pair) for rule in raw.get("morphology", []) for pair in rule["examples"]]


def detect_language(text: str) -> str:
    """`en`, `pt`, `es` or `und`, by function words. Never a guess past that."""
    words = _TOKEN.findall(fold(text)[0])
    if len(words) < 8:
        return "und"
    counts = {lang: sum(1 for w in words if w in fw) for lang, fw in _FUNCTION_WORDS.items()}
    best = max(counts, key=lambda k: counts[k])
    ranked = sorted(counts.values(), reverse=True)
    if ranked[0] < 3 or ranked[0] < 1.3 * ranked[1]:
        return "und"
    return best


def tokens(text: str, table: LanguageTable | None = None) -> list[Token]:
    """Canonical content tokens of `text`, with original offsets and sentence
    numbers. Fixed expressions are folded into their canonical roots first,
    then each remaining word goes through `LanguageTable.canonical`."""
    table = table or load_table()
    folded, offsets = fold(text)
    breaks = [i for i, ch in enumerate(text) if ch in SENTENCE_BREAKS]
    raw: list[tuple[str, int, int, int, str]] = []
    from bisect import bisect_right

    for m in _TOKEN.finditer(folded):
        word = m.group(0)
        if word in STOPWORDS:
            continue
        start = offsets[m.start()]
        end = offsets[m.end() - 1] + 1
        raw.append((root(word), start, end, bisect_right(breaks, start), word))
    out: list[Token] = []
    longest = max((len(k) for k in table.fixed), default=0)
    heads = table.fixed_heads
    i = 0
    while i < len(raw):
        matched = False
        for size in range(min(longest, len(raw) - i) if raw[i][0] in heads else 1, 1, -1):
            window = raw[i : i + size]
            if len({w[3] for w in window}) != 1:
                continue
            target = table.fixed.get(tuple(w[0] for w in window))
            if target is not None:
                for r in target:
                    out.append(
                        Token(
                            r,
                            window[0][1],
                            window[-1][2],
                            window[0][3],
                            Relation.FIXED_EXPRESSION,
                            window[0][4] if size == 1 else "",
                        )
                    )
                i += size
                matched = True
                break
        if matched:
            continue
        word_root, start, end, sentence, word = raw[i]
        if (word_root,) in table.fixed:
            # A one-word form of a fixed expression (`workflow`, `nomina`).
            target = table.fixed[(word_root,)]
            relation = Relation.IDENTICAL if target == (word_root,) else Relation.FIXED_EXPRESSION
            for r in target:
                out.append(Token(r, start, end, sentence, relation, word))
        else:
            canonical, relation = table.canonical(word_root, word)
            out.append(Token(canonical, start, end, sentence, relation, word))
        i += 1
    return out


def concept_readings(
    phrase: str, table: LanguageTable | None = None
) -> tuple[tuple[str, ...], ...]:
    """A person's phrase as canonical root readings (`phrase_forms.readings`
    semantics: a coordination gives two readings), in whatever language it
    was written."""
    from career_agent.match.phrase_forms import _COORDINATORS

    table = table or load_table()
    folded = fold(phrase)[0].replace("&", " and ")
    words = _TOKEN.findall(folded)
    coordinators = [i for i, w in enumerate(words) if w in _COORDINATORS]

    def canon(part: list[str]) -> tuple[str, ...]:
        return tuple(t.root for t in tokens(" ".join(part), table))

    whole = canon(words)
    if len(coordinators) != 1:
        return (whole,) if whole else ()
    at = coordinators[0]
    left, right = canon(words[:at]), canon(words[at + 1 :])
    if len(left) >= 2 and len(right) == 1:
        first, second = left, (*left[:-1], *right)
    elif len(left) == 1 and len(right) >= 2:
        first, second = (*left, *right[1:]), right
    else:
        return (whole,) if whole else ()
    out = tuple(side for side in (first, second) if len(side) >= 2)
    return out or ((whole,) if whole else ())

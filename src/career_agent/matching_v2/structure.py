"""How the words of a matched phrase relate in their sentence (ADR-0037).

Words of a preference found close together are not yet the preference.
Measured on the synthetic corpus (2026-10-14), six of seven sentences using
the words in another sense still matched, every one for a structural reason
the sentence states in its function words:

* INVERTED_HEAD       `Dados de migracao`, `Data on staff migration`, `dados
                      ... para analise`: the preposition makes the OTHER word
                      the head (data ABOUT migration, data FOR analysis);
* SPLIT_COORDINATION  `Gestao de pessoas e campanha interna`: the first word
                      is closed by its own complement before the `e`, so the
                      second word belongs to another item of the list;
* THIRD_PARTY_SUBJECT `Our suppliers handle all price negotiation`: the
                      first word opens the sentence as somebody else's
                      subject and the second is what THEY do;
* CLAUSE_BOUNDARY     a colon or a parenthesis between the words;
* OTHER_SENSE         a word with a second common meaning, with a word of
                      that meaning in the same sentence (`integracao` in a
                      sentence about the welcome week is onboarding).

Each reads closed lists of ordinary grammar (`language_data.yaml`,
`structure` and `other_senses`), never a profession or a phrase of the
corpus. A finding makes the match UNCERTAIN: shown, worth nothing, never
turned into NONE (evidence is insufficient, not absent). A sentence with none
of these keeps the production proximity rule, which is what keeps
translations and inflections that are written with the words apart.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from career_agent.match.phrase_forms import STOPWORDS
from career_agent.match.text import fold
from career_agent.matching_v2.language import DATA, LanguageTable, Token, load_table, tokens

INVERTED_HEAD = "INVERTED_HEAD"
SPLIT_COORDINATION = "SPLIT_COORDINATION"
THIRD_PARTY_SUBJECT = "THIRD_PARTY_SUBJECT"
SEPARATE_PHRASES = "SEPARATE_PHRASES"
CLAUSE_BOUNDARY = "CLAUSE_BOUNDARY"
OTHER_SENSE = "OTHER_SENSE"

_WORD = re.compile(r"[0-9a-z]+")
#: How far into a sentence its opening noun phrase may reach.
_OPENER_REACH = 2


@dataclass(frozen=True)
class Structure:
    prepositions: frozenset[str]
    #: English prepositions: the head of their object comes LAST in its noun
    #: phrase (`on staff migration`); in Portuguese and Spanish it comes first.
    head_final_prepositions: frozenset[str]
    coordinators: frozenset[str]
    openers: frozenset[str]
    second_person: frozenset[str]
    clause_marks: tuple[str, ...]
    nominal_endings: tuple[str, ...]
    english_nominal_endings: tuple[str, ...]
    verb_endings: tuple[str, ...]
    #: folded form -> folded cue phrases of its other sense
    senses: dict[str, tuple[str, ...]]
    #: folded listed forms that are NOT English (for a phrase's word order)
    non_english: frozenset[str]


def _words(text: str) -> list[str]:
    return _WORD.findall(fold(text)[0])


@lru_cache(maxsize=2)
def load_structure(path: Path = DATA) -> Structure:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    s = raw["structure"]

    def flat(value: Any) -> frozenset[str]:
        items = value.values() if isinstance(value, dict) else [value]
        words = [w for group in items for w in group]
        # YAML 1.1 reads a bare `on`, `y` or `no` as a boolean (the
        # `norway: NO` lesson). A function word is always a string.
        if not all(isinstance(w, str) for w in words):
            raise ValueError(f"quote every word in `structure`: {words}")
        return frozenset(fold(w)[0].strip() for w in words)

    senses: dict[str, tuple[str, ...]] = {}
    for entry in raw.get("other_senses", []):
        cues = tuple(" ".join(_words(c)) for c in entry["cues"])
        for form in entry["forms"]:
            key = fold(str(form))[0].strip()
            senses[key] = senses.get(key, ()) + cues
    english = {
        fold(str(f))[0].strip()
        for entry in raw.get("exact_equivalent", [])
        for f in entry.get("en", [])
    }
    non_english = {
        fold(str(f))[0].strip()
        for entry in raw.get("exact_equivalent", [])
        for lang, forms in entry.items()
        if lang != "en"
        for f in forms
    } - english
    return Structure(
        prepositions=flat(s["prepositions"]),
        head_final_prepositions=flat(s["prepositions"]["en"]),
        coordinators=flat(s["coordinators"]),
        openers=flat(s["third_party_openers"]),
        second_person=flat(s["second_person"]),
        clause_marks=tuple(str(m) for m in s["clause_marks"]),
        nominal_endings=tuple(sorted(flat(s["nominal_endings"]), key=len, reverse=True)),
        # Without `ing`: in a compound a gerund governs like a verb
        # (`managing campaigns`).
        english_nominal_endings=tuple(e for e in flat(s["nominal_endings"]["en"]) if e != "ing"),
        verb_endings=tuple(flat(s["verb_endings"])),
        senses=senses,
        non_english=frozenset(non_english),
    )


def _head_initial(pattern: str, st: Structure) -> bool:
    """Word order of a phrase without a preposition: English compounds put
    the head last (`data migration`), Portuguese and Spanish first (`vendas
    consultivas`). Read from the phrase's own words."""
    return any(ord(ch) > 127 for ch in pattern if ch.isalpha()) or any(
        w in st.non_english for w in _words(pattern)
    )


@lru_cache(maxsize=4096)
def phrase_relations(pattern: str) -> frozenset[tuple[str, str]]:
    """(governor root, dependent root) pairs a phrase states, closed.

    `migracao de dados` and `data migration` both say migration governs data;
    `user interface design` says design governs interface governs user."""
    st = load_structure()
    table = load_table()
    toks = tokens(pattern, table)
    groups: list[tuple[int, int, set[str]]] = []
    for t in toks:
        if groups and groups[-1][0] == t.start and groups[-1][1] == t.end:
            groups[-1][2].add(t.root)
        else:
            groups.append((t.start, t.end, {t.root}))
    initial = _head_initial(pattern, st)
    pairs: set[tuple[str, str]] = set()
    for (_s1, e1, a), (s2, _e2, b) in zip(groups, groups[1:], strict=False):
        between = set(_words(pattern[e1:s2]))
        governor, dependent = (a, b) if (between & st.prepositions or initial) else (b, a)
        pairs |= {(g, d) for g in governor for d in dependent}
    grew = True
    while grew:
        grew = False
        for g, d in list(pairs):
            for g2, d2 in list(pairs):
                if d == g2 and (g, d2) not in pairs:
                    pairs.add((g, d2))
                    grew = True
    return frozenset(pairs)


def _nominal(word: str, st: Structure) -> bool:
    """A noun by its ending; an empty word is a fixed expression (a noun).
    A listed Portuguese or Spanish word is a noun unless it ends like a verb."""
    if not word or any(word.endswith(e) for e in st.nominal_endings):
        return True
    return word in st.non_english and not any(word.endswith(e) for e in st.verb_endings)


def _sense_cued(word: str, sentence_folded: str, st: Structure) -> bool:
    cues = st.senses.get(word)
    return bool(cues) and any(f" {c} " in sentence_folded for c in cues or ())


def check(
    text: str,
    matched: list[Token],
    pattern: str,
    sentence_start: int,
    sentence_end: int,
) -> str | None:
    """The structural reason the matched words are not the preference, or
    None. `matched` are the tokens a reading was found in, in text order."""
    st = load_structure()
    sentence = text[sentence_start:sentence_end]
    sentence_words = _words(sentence)
    folded = f" {' '.join(sentence_words)} "
    for t in matched:
        if t.word and _sense_cued(t.word, folded, st):
            return OTHER_SENSE
    words_at = {(t.start, t.end): t.word for t in matched}
    spans = sorted({(t.start, t.end, t.root) for t in matched})
    if len({(s, e) for s, e, _ in spans}) < 2:
        return None
    first, last = spans[0], spans[-1]
    if any(m in text[first[1] : last[0]] for m in st.clause_marks):
        return CLAUSE_BOUNDARY
    relations = phrase_relations(pattern)
    for (s1, e1, a), (s2, _e2, b) in zip(spans, spans[1:], strict=False):
        nominal = _nominal(words_at.get((s1, e1), ""), st)
        if e1 >= s2:
            continue  # one fixed expression
        gap_raw = text[e1:s2]
        gap = _words(gap_raw)
        coordinated = [i for i, w in enumerate(gap) if w in st.coordinators] or (
            [len(gap)] if ("&" in gap_raw or "/" in gap_raw) else []
        )
        if coordinated:
            left = gap[: coordinated[0]]
            closed = any(
                w in st.prepositions and any(x not in STOPWORDS for x in left[i + 1 :])
                for i, w in enumerate(left)
            )
            if closed and nominal:
                return SPLIT_COORDINATION
            continue
        preps = [i for i, w in enumerate(gap) if w in st.prepositions]
        if not preps:
            # English compound order: the LAST noun is the head (`migration
            # status data` is data). A first word that is an English noun and
            # the phrase's head reads as a modifier here.
            word_a = words_at.get((s1, e1), "")
            if (
                word_a
                and word_a not in st.non_english
                and words_at.get((s2, _e2), "") not in st.non_english
                and any(word_a.endswith(e) for e in st.english_nominal_endings)
                and (a, b) in relations
                and (b, a) not in relations
            ):
                return INVERTED_HEAD
            continue
        # English: a noun followed by another noun before the preposition is
        # a modifier of that noun (`analysis tools for data teams`), so the
        # preposition links two OTHER words.
        if (
            nominal
            and gap[preps[0]] in st.head_final_prepositions
            and any(w not in STOPWORDS for w in gap[: preps[0]])
        ):
            return SEPARATE_PHRASES
        if (b, a) not in relations or (a, b) in relations:
            continue
        # Is `b` the object of the last preposition? In English the object's
        # head ends its noun phrase (`on staff migration`); in Portuguese and
        # Spanish it opens it, so a content word between the preposition and
        # `b` is the object and `b` is something else (`processos de cobranca
        # automatizados`: automated processes, not processes of automation).
        last_prep = gap[preps[-1]]
        after = [w for w in gap[preps[-1] + 1 :] if w not in STOPWORDS]
        if last_prep in st.head_final_prepositions or not after:
            return INVERTED_HEAD
    opening = _words(text[sentence_start : first[0]])
    if (
        opening
        and opening[0] in st.openers
        and len(opening) <= _OPENER_REACH
        and not (set(sentence_words) & st.second_person)
    ):
        second = spans[1]
        between = [w for w in _words(text[first[1] : second[0]]) if w not in STOPWORDS]
        if between:
            return THIRD_PARTY_SUBJECT
    return None


def causes() -> Iterable[str]:
    return (
        INVERTED_HEAD,
        SEPARATE_PHRASES,
        SPLIT_COORDINATION,
        THIRD_PARTY_SUBJECT,
        CLAUSE_BOUNDARY,
        OTHER_SENSE,
    )


__all__ = ["LanguageTable", "check", "phrase_relations", "load_structure", "causes"]

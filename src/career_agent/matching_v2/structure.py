"""How the words of a matched phrase relate in their sentence (ADR-0037).

Words of a preference found close together are not yet the preference.
Measured on the synthetic corpus (2026-10-14), six of seven sentences using
the words in another sense still matched. The readings kept are the ones
that held on REAL postings (a development half of 840 adverts, read by hand;
the other half was kept for the evaluation):

* THIRD_PARTY_SUBJECT `Our suppliers handle all price negotiation`: the first
                 word opens the sentence as somebody else's subject;
* CLAUSE_BOUNDARY a `|` between the words (two labels, not one phrase);
* OTHER_SENSE    a word with a second common meaning, with a word of that
                 meaning in the same sentence (`integracao` beside the
                 welcome week is onboarding).

Measured and NOT kept, because real adverts use the same shapes for the work
far more often than for something else: a coordination closed by a
complement (`automation of tasks and workflows` is workflow automation), two
noun phrases around a preposition (`integrating LLMs into production
systems`), a parenthesis or a colon (`integrations (APIs, webhooks)`,
`Integracoes entre servicos: APIs REST`), a comma list (`automate complex,
recurring accounting workflows`), and English compound order without a
preposition (`automation workflows`).

INVERTED_HEAD (a preposition making the other word the head: `Dados de
migracao`, `Data on staff migration`) was kept from the first reading and
REMOVED on 2026-10-14: on a new development set (65 sentences where it fired,
read by hand, none from the first evaluation) it held 40 of the role's own
work UNCERTAIN to set aside 22 others, and none of three narrower variants
(only an adjacent preposition; only outside a duty or a requirement) did
clearly better than no rule (estimated F1 0.8138 without, 0.8117 with).

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
from career_agent.matching_v2.language import DATA, LanguageTable, Token

THIRD_PARTY_SUBJECT = "THIRD_PARTY_SUBJECT"
CLAUSE_BOUNDARY = "CLAUSE_BOUNDARY"
OTHER_SENSE = "OTHER_SENSE"
#: The words of a phrase are present but not linked as the phrase
#: (2026-10-15): `lead the generation of reports`, `demand generation, lead
#: nurturing`, `leads generated through campaigns`, `next-generation`.
UNLINKED = "UNLINKED"

_WORD = re.compile(r"[0-9a-z]+")
#: How far into a sentence its opening noun phrase may reach.
_OPENER_REACH = 2


@dataclass(frozen=True)
class Structure:
    prepositions: frozenset[str]
    coordinators: frozenset[str]
    openers: frozenset[str]
    second_person: frozenset[str]
    clause_marks: tuple[str, ...]
    #: folded form -> folded cue phrases of its other sense
    senses: dict[str, tuple[str, ...]]
    #: Units of an organisation (`team`, `equipe`) and words saying the role
    #: works with somebody (`with`, `partner`): one-word preferences only.
    team_nouns: frozenset[str] = frozenset()
    collaboration: frozenset[str] = frozenset()
    #: English words after which, or before which, a word is a verb.
    verb_openers: frozenset[str] = frozenset()
    determiners: frozenset[str] = frozenset()
    #: One-word preferences, 2026-10-15 (see `language_data.yaml`).
    education_levels: frozenset[str] = frozenset()
    qualification_heads: frozenset[str] = frozenset()
    first_person_plural: frozenset[str] = frozenset()
    object_pronouns: frozenset[str] = frozenset()
    artifact_heads_after: frozenset[str] = frozenset()
    artifact_heads_before: frozenset[str] = frozenset()
    #: Phrases of two words or more, 2026-10-15.
    agent_prepositions: frozenset[str] = frozenset()
    #: Units that close a list of functions (not `leadership`, `partners`).
    unit_nouns: frozenset[str] = frozenset()
    noun_endings: tuple[str, ...] = ()


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
    return Structure(
        prepositions=flat(s["prepositions"]),
        coordinators=flat(s["coordinators"]),
        openers=flat(s["third_party_openers"]),
        second_person=flat(s["second_person"]),
        clause_marks=tuple(str(m) for m in s["clause_marks"]),
        senses=senses,
        team_nouns=flat(s.get("team_nouns", {})),
        collaboration=flat(s.get("collaboration_markers", {})),
        verb_openers=flat(s.get("verb_openers", [])),
        determiners=flat(s.get("determiners", [])),
        education_levels=flat(s.get("education_levels", {})),
        qualification_heads=flat(s.get("qualification_heads", {})),
        first_person_plural=flat(s.get("first_person_plural", [])),
        object_pronouns=flat(s.get("object_pronouns", [])),
        artifact_heads_after=flat(s.get("artifact_heads", {}).get("en", [])),
        artifact_heads_before=flat(
            {k: v for k, v in s.get("artifact_heads", {}).items() if k != "en"}
        ),
        agent_prepositions=flat(s.get("agent_prepositions", {})),
        unit_nouns=flat(s.get("unit_nouns", {})),
        noun_endings=tuple(str(e) for e in s.get("noun_endings", [])),
    )


def _sense_cued(word: str, sentence_folded: str, st: Structure) -> bool:
    cues = st.senses.get(word)
    return bool(cues) and any(f" {c} " in sentence_folded for c in cues or ())


def check(
    text: str,
    matched: list[Token],
    pattern: str,
    sentence_start: int,
    sentence_end: int,
    language: str = "und",
) -> str | None:
    """The structural reason the matched words are not the preference, or
    None. `matched` are the tokens a reading was found in, in text order.

    The grammar is English, Portuguese and Spanish (`language_data.yaml`):
    in a posting in another language, or one whose language is not known,
    only a word's other sense is read and proximity decides the rest."""
    st = load_structure()
    sentence = text[sentence_start:sentence_end]
    sentence_words = _words(sentence)
    folded = f" {' '.join(sentence_words)} "
    for t in matched:
        if t.word and _sense_cued(t.word, folded, st):
            return OTHER_SENSE
    spans = sorted({(t.start, t.end, t.root) for t in matched})
    if len({(s, e) for s, e, _ in spans}) < 2 or language not in ("en", "pt", "es"):
        return None
    first, last = spans[0], spans[-1]
    if any(m in text[first[1] : last[0]] for m in st.clause_marks):
        return CLAUSE_BOUNDARY
    if _unlinked(text, spans, st):
        return UNLINKED
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


_PARTICIPLE = ("ed", "ado", "ados", "ada", "adas", "ido", "idos", "ida", "idas")


def _is_noun(surface: str, st: Structure) -> bool:
    return surface.endswith(st.noun_endings)


def _unlinked(text: str, spans: list[tuple[int, int, str]], st: Structure) -> bool:
    """Whether a word of a phrase, found close to the others, is not the
    phrase's word at all (2026-10-15, a new development set):

    * a participle with an agent is somebody else's act (`leads generated
      through campaigns`, `campaign-generated leads`);
    * the second half of another hyphenated compound is another word
      (`next-generation`: a noun or a participle after the hyphen).

    Measured and NOT kept (the same set, plus a regression sample of 281
    sentences of the other phrases of two words or more): the words' order
    and what stands between them. Rejecting a determiner between the words in
    the phrase's order (`lead the generation`) removed 3 false matches of
    `lead generation` and about 48 true ones elsewhere (`processes,
    automations, and workflows`, `dados para analises`); rejecting a noun of
    action coordinated with a word that heads its own compound in the other
    order (`demand generation and lead nurturing`) removed 2 and about 41.
    Grammar only (`language_data.yaml`), never a phrase of the corpus."""
    for i, (start, end, _root) in enumerate(spans):
        surface = fold(text[start:end])[0].strip()
        neighbour_ends = {e for _, e, _ in spans[:i]}
        if (
            start >= 2
            and text[start - 1] == "-"
            and text[start - 2].isalnum()
            and start - 1 not in neighbour_ends
            and (_is_noun(surface, st) or surface.endswith(_PARTICIPLE))
        ):
            return True
        if surface.endswith(_PARTICIPLE):
            following = _words(text[end : end + 40])[:2]
            if set(following) & st.agent_prepositions:
                return True
    return False


#: One-word preferences (2026-10-14): the word names a team the role works
#: with, is the second half of a hyphenated compound (`pre-sales`), or is
#: used as a verb (`support the launch`) where the person named the work.
ONE_WORD_TEAM = "ONE_WORD_TEAM"
ONE_WORD_COMPOUND = "ONE_WORD_COMPOUND"
ONE_WORD_VERB = "ONE_WORD_VERB"
#: 2026-10-15, on a new development set (none of the labelled packages).
ONE_WORD_QUALIFICATION = "ONE_WORD_QUALIFICATION"
ONE_WORD_RECIPIENT = "ONE_WORD_RECIPIENT"
ONE_WORD_NAME = "ONE_WORD_NAME"
ONE_WORD_MODIFIER = "ONE_WORD_MODIFIER"
#: A school level after the word (`ensino medio`): split from QUALIFICATION
#: on 2026-10-16, because a degree IN the field is weak evidence of it (the
#: owner's policy) while a school level is another sense of the word.
ONE_WORD_LEVEL = "ONE_WORD_LEVEL"

#: Words that link a qualification to its subject (`degree IN sales`).
_LINKS = frozenset({"in", "of", "em", "en", "de", "da", "do"})
#: How far before the word a qualification may stand (`Bachelor's degree in
#: finance or sales`).
_QUALIFICATION_REACH = 5
#: How far a list of functions may run to its unit noun (`between customers,
#: sales, product, and engineering teams`).
_LIST_REACH_AFTER = 10

#: How many words a list of team names may run before its unit noun
#: (`Sales, Support, and Product teams`).
_TEAM_LIST_REACH = 4
#: How many words before the preference a word of working together may sit.
_MARKER_REACH = 8


def _raw_words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+|&", text)


def one_word(text: str, start: int, end: int, sentence_start: int, sentence_end: int,
             language: str = "und") -> str | None:  # fmt: skip
    """Why ONE word of a one-word preference, found at `start:end`, is not
    shown to be the work the person named, or None.

    A phrase of two words carries its own evidence of meaning; one word
    (`sales`, `support`, `data`) also names teams, compounds and actions.
    Measured on a development set (2026-10-14, 280 labelled sentences), the
    context of the sentence did NOT separate the work from the rest (a duty
    heading holds `support the launch` as often as `customer support`); the
    word's own grammar did:

    * ONE_WORD_COMPOUND  the second half of a hyphenated compound: `pre-sales`
      is another activity than `sales`;
    * ONE_WORD_TEAM      the word, alone or in a list of names, names a unit
      of the organisation the role works with (`partner with the Sales,
      Support and Product teams`, `com a equipe de vendas`);
    * ONE_WORD_VERB      English only: the word used as a verb, right after
      `to` or a modal, or right before a determiner (`to support safe
      delivery`, `support the wedding party`). A person who names a kind of
      work names a noun.

    Closed lists of ordinary grammar only (`language_data.yaml`), never a
    profession."""
    st = load_structure()
    if start >= 2 and text[start - 1] == "-" and text[start - 2].isalnum():
        return ONE_WORD_COMPOUND
    before = _words(text[sentence_start:start])
    after_raw = _raw_words(text[end:sentence_end])
    after = [fold(w)[0] for w in after_raw]
    found = _qualification_recipient_name_modifier(
        text, start, end, sentence_start, sentence_end, before, after, st
    )
    if found is not None:
        return found
    team = False
    for i, word in enumerate(after[:_TEAM_LIST_REACH]):
        if word in st.team_nouns:
            # Every word before the unit is another name in the list:
            # capitalised, or a coordinator.
            between = after_raw[:i]
            team = all(w[:1].isupper() or fold(w)[0] in st.coordinators for w in between)
            break
    if len(before) >= 2 and before[-1] in st.prepositions and before[-2] in st.team_nouns:
        team = True
    # One name in a list of functions the role works with (`partner with
    # Legal, Operations and Sales`): a separator touches the word.
    gap_before = text[max(sentence_start, start - 3) : start]
    gap_after = text[end : end + 5]
    listed = any(m in gap_before for m in (",", "&", "/")) or any(
        m in gap_after for m in (",", "&", "/")
    )
    listed = listed or bool(
        (before and before[-1] in st.coordinators) or (after and after[0] in st.coordinators)
    )
    # The words of working together must be near (`partner closely with the
    # sales team`), not anywhere in a long sentence (`con formacion en ...
    # para integrarse a nuestro equipo de Soporte` JOINS that team).
    markers = set(before[-_MARKER_REACH:]) & st.collaboration
    if team and markers:
        return ONE_WORD_TEAM
    # One name in a list of functions that a unit noun closes (`between
    # customers, sales, product, and engineering teams`): every word up to the
    # last item is a name, a coordinator or followed by a comma.
    if listed and _closed_by_unit(text[end:sentence_end], st):
        return ONE_WORD_TEAM
    # In a list, a bare preposition is not collaboration (`experience with
    # dados, metadados`; `experience with ToolA, ToolB` lists TOOLS):
    # it takes a verb of working together (`partner with Legal, Sales`).
    if listed and markers - st.prepositions:
        return ONE_WORD_TEAM
    # A gerund names the activity itself (`Teaching mathematics`), never the
    # helping verb; a capitalised word mid-sentence is a name (`connect
    # workflows to ToolA`), never a verb.
    surface = text[start:end]
    if language == "en" and not fold(surface)[0].endswith("ing"):
        if surface[:1].islower() and before and before[-1] in st.verb_openers:
            return ONE_WORD_VERB
        if surface[:1].islower() and after and after[0] in st.determiners:
            return ONE_WORD_VERB
        # An imperative opening a duty (`Support month-end invoicing`): the
        # capitalised first word of its sentence, followed by a lower-case
        # word that is not a preposition, a coordinator or `to`.
        if (
            not before
            and surface[:1].isupper()
            and after_raw
            and after_raw[0][:1].islower()
            and after[0] not in st.prepositions
            and after[0] not in st.coordinators
            and after[0] not in st.verb_openers
        ):
            return ONE_WORD_VERB
    return None


_LIST_WORD = re.compile(r"([^\W_]+)(\s*,)?")


def _closed_by_unit(rest: str, st: Structure) -> bool:
    """Whether a list starting right after the word ends in a unit noun:
    `, product, and engineering teams`, `or operations teams`, `, Value
    Engineering, Product Management, ... and Information Security teams`.
    The list ends at a semicolon or a full stop."""
    rest = re.split(r"[;.]", rest, maxsplit=1)[0]
    words = [(m.group(1), bool(m.group(2))) for m in _LIST_WORD.finditer(rest)]
    for i, (word, _comma) in enumerate(words[:_LIST_REACH_AFTER]):
        if fold(word)[0] in st.unit_nouns:
            # The unit noun must END the list's noun phrase: `teams.` or
            # `teams to`, not `Unit Administration` or `lideranca de equipes`
            # (leadership of teams is an activity).
            following = fold(words[i + 1][0])[0] if i + 1 < len(words) else ""
            if following and not (comma_after(words, i) or following in _UNIT_FOLLOWERS):
                return False
            items = words[: max(0, i - 1)]
            return i > 0 and all(
                w[:1].isupper() or comma or fold(w)[0] in st.coordinators for w, comma in items
            )
    return False


#: Words that may follow a unit noun closing a list (`teams to`, `teams
#: and`, `teams in`); `of`/`de` are not among them.
_UNIT_FOLLOWERS = frozenset(
    {"to", "and", "or", "that", "who", "which", "in", "at", "across", "on", "with", "for",
     "e", "ou", "y", "o", "para", "com", "con", "em", "en", "no", "na"}
)  # fmt: skip


def comma_after(words: list[tuple[str, bool]], i: int) -> bool:
    return words[i][1]


def _qualification_recipient_name_modifier(
    text: str,
    start: int,
    end: int,
    sentence_start: int,
    sentence_end: int,
    before: list[str],
    after: list[str],
    st: Structure,
) -> str | None:
    """Four readings of ONE word that name something other than the work
    the person does (2026-10-15):

    * ONE_WORD_QUALIFICATION  a school level after the word (`ensino medio
      completo`), or a qualification before it with a link word between
      (`Bachelor's degree in finance or sales`, `formacao em vendas`);
    * ONE_WORD_RECIPIENT      the company as the subject (`we'll teach`, `What
      we can't teach`) or the hire as the object (`teach you the rest`): what
      is done TO the person;
    * ONE_WORD_NAME           written in capitals in a sentence that is not
      (`support TEACH`): a programme or a brand;
    * ONE_WORD_MODIFIER       the word names what another noun is for: the
      noun after it in English (`sales software`, `Sales Hub`, `teaching
      affiliate`), before `de`/`para` in Portuguese and Spanish
      (`instituicoes de ensino`, `herramientas de ventas`).

    Closed lists of ordinary words (`language_data.yaml`), never a profession."""
    if after and after[0] in st.education_levels:
        return ONE_WORD_LEVEL
    clause_start = max(
        text.rfind(";", sentence_start, start), text.rfind(".", sentence_start, start)
    )
    near = _words(text[max(sentence_start, clause_start + 1, start - 80) : start])
    near = near[-_QUALIFICATION_REACH:]
    for i, word in enumerate(near):
        heads = st.qualification_heads | st.education_levels
        tail = near[i + 1 :]
        if word in heads and set(tail) & _LINKS and not any(w.isdigit() for w in tail):
            return ONE_WORD_QUALIFICATION
    touching_after = re.match(r"[ \t]+([^\W_]+)", text[end:sentence_end])
    if touching_after and fold(touching_after.group(1))[0] in st.object_pronouns:
        return ONE_WORD_RECIPIENT
    if before and before[-1] in ("te", "lhe"):
        return ONE_WORD_RECIPIENT
    if before and before[-1] in st.first_person_plural:
        return ONE_WORD_RECIPIENT
    modal = len(before) >= 2 and (before[-1] in st.verb_openers or before[-1] == "ll")
    if modal and before[-2] in st.first_person_plural:
        return ONE_WORD_RECIPIENT
    if len(before) >= 3 and before[-3] in st.first_person_plural and before[-1] == "t":
        return ONE_WORD_RECIPIENT
    surface = text[start:end]
    sentence = text[sentence_start:sentence_end]
    if len(surface) >= 3 and surface.isupper() and any(c.islower() for c in sentence):
        prev_raw = _raw_words(text[sentence_start:start])[-1:]
        next_raw = _raw_words(text[end:sentence_end])[:1]
        if not any(w.isupper() and len(w) > 1 for w in prev_raw + next_raw):
            return ONE_WORD_NAME
    if touching_after and fold(touching_after.group(1))[0] in st.artifact_heads_after:
        return ONE_WORD_MODIFIER
    # Two words on (`Sales Engagement Platforms`), spaces only between.
    two = re.match(r"\s+([^\W_]+)\s+([^\W_]+)", text[end:sentence_end])
    if (
        two
        and fold(two.group(2))[0] in st.artifact_heads_after
        and fold(two.group(1))[0] not in st.coordinators
        and fold(two.group(1))[0] not in st.determiners
    ):
        return ONE_WORD_MODIFIER
    if (
        len(before) >= 2
        and before[-1] in ("de", "da", "do", "del", "para")
        and before[-2] in st.artifact_heads_before
    ):
        return ONE_WORD_MODIFIER
    return None


def after_raw_second(text: str, end: int, sentence_end: int) -> str:
    """The second word after `end`, as written (for the punctuation test)."""
    words = _raw_words(text[end:sentence_end])
    return words[1] if len(words) > 1 else "\x00"


def causes() -> Iterable[str]:
    return (
        THIRD_PARTY_SUBJECT,
        CLAUSE_BOUNDARY,
        OTHER_SENSE,
        ONE_WORD_TEAM,
        ONE_WORD_COMPOUND,
        ONE_WORD_VERB,
        ONE_WORD_QUALIFICATION,
        ONE_WORD_RECIPIENT,
        ONE_WORD_NAME,
        ONE_WORD_MODIFIER,
        ONE_WORD_LEVEL,
        UNLINKED,
    )


__all__ = ["LanguageTable", "check", "one_word", "load_structure", "causes"]

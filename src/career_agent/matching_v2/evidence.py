"""What a piece of evidence MEANS, before it proves a preference (2026-10-16).

Package 3 showed that the words of a preference being present, in a
sentence of the right section, is not yet evidence: the same words name the
company's business, a product's feature, another team, something given TO
the person, a school level, a thing for sale. v2 answered each of those with
its own rule and its own UNCERTAIN cause. This module names the dimensions
those rules were answering, once, in a frame per matched place:

    EVIDENCE = concept + sense + owner + direction + evidence type
               (+ the section it stands in, + how sure the reading is)

The frame is a FACT about the text and is the same for every profile. What
it is WORTH is a profile's policy (`evidence_policy.py`): a degree in the
field may count for one person and not another, and changing that never
re-reads an advert.

Two halves, so that what is shared is computed once:

* `annotate` (shared, `semantic_evidence_facts`): per sentence, the spans of
  the cues that say what kind of statement it is (experience, familiarity,
  skill, degree, alternatives, receiving, commercial) and whether its
  subject is the company or a product. A few spans per posting, stored as
  offsets and a letter.
* `frame` (per match): the matched words' own grammar (the causes of
  `structure`), a word's known other senses (`semantic_data.yaml`) and the
  annotations of its sentence, assembled into an `EvidenceFrame`.

Grammar for English, Portuguese and Spanish only. In any other language the
frame is built from the section alone, with LOW certainty: an unknown reading
is UNKNOWN, never an English rule applied to German.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

from career_agent.match.text import FoldedText, fold
from career_agent.matching_v2 import structure
from career_agent.matching_v2.context import NOT_WORK, Context, Sentence

DATA = Path(__file__).with_name("semantic_data.yaml")
GRAMMAR_LANGUAGES = frozenset({"en", "pt", "es"})


class Owner(StrEnum):
    ROLE = "ROLE"
    COMPANY = "COMPANY"
    PRODUCT = "PRODUCT"
    SYSTEM = "SYSTEM"
    CUSTOMER = "CUSTOMER"
    PARTNER = "PARTNER"
    OTHER_TEAM = "OTHER_TEAM"
    CANDIDATE = "CANDIDATE"
    UNKNOWN = "UNKNOWN"


class Direction(StrEnum):
    ROLE_PROVIDES = "ROLE_PROVIDES"
    ROLE_RECEIVES = "ROLE_RECEIVES"
    OTHER_PROVIDES = "OTHER_PROVIDES"
    OTHER_RECEIVES = "OTHER_RECEIVES"
    NONE = "NONE"
    UNKNOWN = "UNKNOWN"


class EvidenceType(StrEnum):
    DUTY = "DUTY"
    REQUIREMENT_SKILL = "REQUIREMENT_SKILL"
    REQUIREMENT_EXPERIENCE = "REQUIREMENT_EXPERIENCE"
    DEGREE_IN_FIELD = "DEGREE_IN_FIELD"
    TOOL_EXPERIENCE = "TOOL_EXPERIENCE"
    FAMILIARITY = "FAMILIARITY"
    COLLABORATION = "COLLABORATION"
    ARTIFACT_ONLY = "ARTIFACT_ONLY"
    DOMAIN_CONTEXT = "DOMAIN_CONTEXT"
    COMPANY_CONTEXT = "COMPANY_CONTEXT"
    PRODUCT_CONTEXT = "PRODUCT_CONTEXT"
    BENEFIT = "BENEFIT"
    APPLICATION_PROCESS = "APPLICATION_PROCESS"
    BOILERPLATE = "BOILERPLATE"
    UNKNOWN = "UNKNOWN"


class Sense(StrEnum):
    #: The sense the person named (no other reading is known here).
    NAMED = "NAMED"
    PRE_SALES = "PRE_SALES"
    POST_SALES = "POST_SALES"
    #: Post-sales with an upsell, renewal, negotiation or revenue cue.
    POST_SALES_COMMERCIAL = "POST_SALES_COMMERCIAL"
    MERCHANDISE = "MERCHANDISE"
    DEDUCTION = "DEDUCTION"
    PHYSICAL = "PHYSICAL"
    EDUCATION_LEVEL = "EDUCATION_LEVEL"
    VERB = "VERB"
    PROPER_NAME = "PROPER_NAME"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class Certainty(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


@dataclass(frozen=True, slots=True)
class EvidenceFrame:
    concept_id: str
    sense: Sense
    owner: Owner
    direction: Direction
    evidence_type: EvidenceType
    section_context: str
    certainty: Certainty
    #: The rule that set the deciding field, for the explanation.
    because: str | None = None

    def code(self) -> str:
        """One short line: `DUTY ROLE ROLE_PROVIDES NAMED HIGH`."""
        return " ".join(
            (self.evidence_type, self.owner, self.direction, self.sense, self.certainty)
        )


_ORDER = (EvidenceType, Owner, Direction, Sense, Certainty)


def pack(code: str) -> str:
    """`DUTY ROLE ROLE_PROVIDES NAMED HIGH` -> `0.0.0.0.0`: each field as its
    position in its enum, for storage (a few bytes per piece of evidence)."""
    return ".".join(str(list(e).index(e(v))) for e, v in zip(_ORDER, code.split(), strict=True))


def unpack(packed: str) -> str:
    return " ".join(list(e)[int(i)].value for e, i in zip(_ORDER, packed.split("."), strict=True))


# -- data -------------------------------------------------------------------

#: Annotation letters (shared facts).
EXPERIENCE, FAMILIARITY, SKILL, DEGREE, ALTERNATIVES, RECEIVING, COMMERCIAL = "EFSGARM"
COMPANY_SUBJECT, PRODUCT_SUBJECT = "C", "P"
#: The object of `partner with`, `colaborar com` (K), or of `work with` (W,
#: a team only when capitalised or followed by a unit noun).
COLLABORATION_OBJECT, WEAK_COLLABORATION_OBJECT = "K", "W"
_CUE_KINDS = {
    "experience": EXPERIENCE,
    "familiarity": FAMILIARITY,
    "skill": SKILL,
    "degree": DEGREE,
    "receiving": RECEIVING,
    "commercial": COMMERCIAL,
}
_TYPE_OF_CUE = {
    EXPERIENCE: EvidenceType.REQUIREMENT_EXPERIENCE,
    FAMILIARITY: EvidenceType.FAMILIARITY,
    SKILL: EvidenceType.REQUIREMENT_SKILL,
    DEGREE: EvidenceType.DEGREE_IN_FIELD,
}

_WORD = re.compile(r"[0-9a-z]+")
#: The word for `or`, per language (not `o` in Portuguese: an article).
_OR = {"en": "or", "pt": "ou", "es": "o"}


def _seq(text: str) -> tuple[str, ...]:
    return tuple(_WORD.findall(fold(text)[0]))


@dataclass(frozen=True)
class _SenseEntry:
    sense: Sense
    forms: frozenset[str]
    before: tuple[tuple[str, ...], ...]
    after: frozenset[str]
    clause: frozenset[str]


@dataclass(frozen=True)
class _Data:
    #: language -> (letter, cue words), longest first
    cues: dict[str, tuple[tuple[str, tuple[str, ...]], ...]]
    alternatives: tuple[tuple[str, ...], ...]
    product_nouns: frozenset[str]
    product_dets: frozenset[str]
    product_not: frozenset[str]
    company_openers: dict[str, tuple[tuple[str, ...], ...]]
    copulas: dict[str, tuple[tuple[str, ...], ...]]
    heads_after: frozenset[str]
    heads_before: frozenset[str]
    links: frozenset[str]
    role_voice: tuple[tuple[str, ...], ...]
    senses: tuple[_SenseEntry, ...]
    not_names: frozenset[str] = frozenset()
    light_verbs: frozenset[str] = frozenset()
    #: language -> (letter, marker words)
    collaboration: dict[str, tuple[tuple[str, tuple[str, ...]], ...]] | None = None
    object_ends: frozenset[str] = frozenset()
    object_reach: int = 8
    product_links: frozenset[str] = frozenset()
    product_heads: frozenset[str] = frozenset()
    unit_nouns: frozenset[str] = frozenset()


def _flat(value: dict | list) -> list[str]:
    items = value.values() if isinstance(value, dict) else [value]
    words = [w for group in items for w in group]
    if not all(isinstance(w, str) for w in words):
        raise ValueError(f"quote every word in semantic_data.yaml: {words}")
    return words


@lru_cache(maxsize=2)
def load(path: Path = DATA) -> _Data:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cues: dict[str, list[tuple[str, tuple[str, ...]]]] = {lang: [] for lang in GRAMMAR_LANGUAGES}
    for name, letter in _CUE_KINDS.items():
        for lang, items in raw["evidence_cues"][name].items():
            cues[lang].extend((letter, _seq(c)) for c in items)
    o = raw["owner"]
    senses = tuple(
        _SenseEntry(
            Sense(e["sense"]),
            frozenset(_seq(f)[0] for f in e["forms"]),
            tuple(sorted({_seq(b) for b in e.get("before", [])}, key=len, reverse=True)),
            frozenset(w for a in e.get("after", []) for w in _seq(a)),
            frozenset(w for c in e.get("clause", []) for w in _seq(c)),
        )
        for e in raw["senses"]
    )
    return _Data(
        cues={k: tuple(sorted(v, key=lambda c: -len(c[1]))) for k, v in cues.items()},
        alternatives=tuple(_seq(a) for a in raw["evidence_cues"]["alternatives"]),
        product_nouns=frozenset(_flat(o["product_nouns"])),
        product_dets=frozenset(_flat(o["product_determiners"])),
        product_not=frozenset(_flat(o["product_not_subject"])),
        company_openers={k: tuple(_seq(c) for c in v) for k, v in o["company_openers"].items()},
        copulas={k: tuple(_seq(c) for c in v) for k, v in o["company_copulas"].items()},
        heads_after=frozenset(_flat(o["company_heads_after"])),
        heads_before=frozenset(_flat(o["company_heads_before"])),
        links=frozenset(_flat(o["company_links"])),
        role_voice=tuple(_seq(c) for c in o["role_voice"]),
        senses=senses,
        not_names=frozenset(_flat(o["not_names"])),
        light_verbs=frozenset(_flat(raw["light_verbs"])),
        collaboration={
            lang: tuple(
                sorted(
                    [(COLLABORATION_OBJECT, _seq(c)) for c in raw["collaboration"]["strong"][lang]]
                    + [
                        (WEAK_COLLABORATION_OBJECT, _seq(c))
                        for c in raw["collaboration"]["weak"][lang]
                    ],
                    key=lambda c: -len(c[1]),
                )
            )
            for lang in GRAMMAR_LANGUAGES
        },  # fmt: skip
        object_ends=frozenset(_seq(w)[0] for w in raw["collaboration"]["object_ends"]),
        object_reach=int(raw["collaboration"]["object_reach"]),
        product_links=frozenset(_flat(o["product_links"])),
        product_heads=frozenset(_flat(o["product_nouns"]))
        | frozenset(_flat(o["product_heads_extra"])),
        unit_nouns=structure.load_structure().unit_nouns,
    )


# -- shared facts -------------------------------------------------------------

Note = tuple[int, int, str]


def _words(text: str, offset: int) -> list[tuple[str, int, int]]:
    """(folded word, start, end) with offsets into the advert."""
    folded, offsets = fold(text)
    out = []
    for m in _WORD.finditer(folded):
        start = offsets[m.start()] + offset
        end = offsets[m.end() - 1] + 1 + offset
        out.append((m.group(0), start, end))
    return out


def _find_all(words: list[str], seq: tuple[str, ...]) -> list[int]:
    n = len(seq)
    if not n:
        return []
    first = seq[0]
    return [i for i, w in enumerate(words) if w == first and tuple(words[i : i + n]) == seq]


def _find_at(words: list[str], seq: tuple[str, ...], at: dict[str, list[int]]) -> list[int]:
    """`_find_all`, starting only where the first word stands."""
    n = len(seq)
    return [i for i in at.get(seq[0], ()) if tuple(words[i : i + n]) == seq] if n else []


def _contains(words: list[str], seqs: tuple[tuple[str, ...], ...]) -> bool:
    return any(_find_all(words, s) for s in seqs)


def _clauses(text: str, start: int, end: int) -> list[tuple[int, int]]:
    """A sentence's clauses: cut at `;` and at a colon or a comma followed by
    a word opening a new clause is NOT attempted (lists of tools are joined by
    commas); only `;`, `|` and the sentence's own ends."""
    out, last = [], start
    for m in re.finditer(r"[;|]", text[start:end]):
        out.append((last, start + m.start()))
        last = start + m.end()
    out.append((last, end))
    return [(a, b) for a, b in out if b > a]


def _folded_words(folded: FoldedText, start: int, end: int) -> list[tuple[str, int, int]]:
    """`_words(text[start:end], start)` read from the posting's folded field."""
    offsets = folded.offsets
    lo, hi = bisect_left(offsets, start), bisect_left(offsets, end)
    return [
        (m.group(0), offsets[m.start()], offsets[m.end() - 1] + 1)
        for m in _WORD.finditer(folded.folded, lo, hi)
    ]


@lru_cache(maxsize=8)
def _cue_index(language: str) -> dict[str, tuple[tuple[int, str, tuple[str, ...]], ...]]:
    """Cues by their first word, each with its rank in the longest-first order."""
    out: dict[str, list[tuple[int, str, tuple[str, ...]]]] = {}
    for rank, (letter, seq) in enumerate(load().cues[language]):
        out.setdefault(seq[0], []).append((rank, letter, seq))
    return {k: tuple(v) for k, v in out.items()}


def annotate(
    text: str, cut: list[Sentence], language: str, folded: FoldedText | None = None
) -> list[Note]:
    """The shared semantic facts of a posting: (start, end, letter) spans.
    `folded` is the posting's folded description when the caller holds it
    (the same words, one fold per posting)."""
    if language not in GRAMMAR_LANGUAGES:
        return []
    data = load()
    notes: list[Note] = []
    index = _cue_index(language)
    for s in cut:
        if s.context in NOT_WORK:
            continue  # set aside whatever it says; nothing to qualify
        words = (
            _folded_words(folded, s.start, s.end)
            if folded is not None
            else _words(text[s.start : s.end], s.start)
        )
        plain = [w for w, _, _ in words]
        first_note = len(notes)
        taken: set[int] = set()
        at: dict[str, list[int]] = {}
        for i, w in enumerate(plain):
            at.setdefault(w, []).append(i)
        # Every cue that occurs, in the longest-first order a full scan of
        # the cue list would meet them (rank, then position).
        found = sorted(
            (rank, i, letter, seq)
            for w, places in at.items()
            for rank, letter, seq in index.get(w, ())
            for i in places
            if tuple(plain[i : i + len(seq)]) == seq
        )
        for _rank, i, letter, seq in found:
            span = range(i, i + len(seq))
            if taken.intersection(span):
                continue  # a longer cue already holds these words
            taken.update(span)
            notes.append((words[i][1], words[i + len(seq) - 1][2], letter))
        for seq in data.alternatives:
            for i in _find_at(plain, seq, at):
                notes.append((words[i][1], words[i + len(seq) - 1][2], ALTERNATIVES))
        # A degree's field followed by `or` names one field among several
        # (`degree in physics or chemistry`).
        alternative = _OR.get(language)
        for start, _end, _letter in [n for n in notes[first_note:] if n[2] == DEGREE]:
            i = next(j for j, w in enumerate(words) if w[1] == start)
            for j in range(i + 1, min(len(words), i + 12)):
                if words[j][0] == alternative:
                    notes.append((words[j][1], words[j][2], ALTERNATIVES))
                    break
        _collaboration_objects(text, words, plain, at, language, data, notes)
        voiced = any(seq[0] in at and _find_at(plain, seq, at) for seq in data.role_voice)
        if not voiced and _company_subject(text, s, plain, language, data):
            notes.append((s.start, s.end, COMPANY_SUBJECT))
        if not voiced:
            for a, b in _clauses(text, s.start, s.end):
                head = [w for w, ws, _ in words if a <= ws < b][:4]
                if (
                    len(head) >= 3
                    and head[0] in data.product_dets
                    and head[1] in data.product_nouns
                    and head[2] not in data.product_not
                    and head[2] not in data.product_nouns
                ):
                    notes.append((a, b, PRODUCT_SUBJECT))
    return sorted(notes)


_NAME = re.compile(r"^(?:[A-ZÀ-Ý][\w&.'\-]*\s+){1,5}")


_INFINITIVE = ("ar", "er", "ir")


def _collaboration_objects(
    text: str,
    words: list[tuple[str, int, int]],
    plain: list[str],
    at: dict[str, list[int]],
    language: str,
    data: _Data,
    notes: list[Note],
) -> None:
    """(start of the marker, end of its object, K or W): the object runs up
    to a word that opens something else, a coordinated verb (`and lead`,
    `e liderar`), or `object_reach` words."""
    taken: set[int] = set()
    for letter, seq in (data.collaboration or {}).get(language, ()):
        for i in _find_at(plain, seq, at):
            if i in taken:
                continue
            taken.update(range(i, i + len(seq)))
            j = i + len(seq)
            last = None
            while j < len(words) and j < i + len(seq) + data.object_reach:
                w = plain[j]
                if w in data.object_ends:
                    break
                if w in ("and", "e", "y") and j + 1 < len(words):
                    nxt = words[j + 1]
                    surface = text[nxt[1] : nxt[2]]
                    verb = (
                        surface[:1].islower() if language == "en" else nxt[0].endswith(_INFINITIVE)
                    )
                    if verb:
                        break
                last = j
                j += 1
            if last is not None:
                notes.append((words[i][1], words[last][2], letter))


def _company_subject(text: str, s: Sentence, plain: list[str], language: str, data: _Data) -> bool:
    """Whether the sentence is about the company: it opens with a company
    opener (`Somos`, `Founded in`) or with a capitalised name and a copula
    (`Acme e uma consultoria`, `Acme is a leading`)."""
    for seq in data.company_openers.get(language, ()):
        if tuple(plain[: len(seq)]) == seq:
            return True
    raw = text[s.start : s.end].lstrip(" -*•·")
    m = _NAME.match(raw)
    if m is None or _seq(m.group(0))[:1] and _seq(m.group(0))[0] in data.not_names:
        return False
    rest = _seq(raw[m.end() : m.end() + 40])
    return any(tuple(rest[: len(c)]) == c for c in data.copulas.get(language, ()))


def encode(notes: list[Note]) -> str:
    """Compact: `E12.14 G300.10`, offsets as gaps from the previous start."""
    out, last = [], 0
    for start, end, letter in notes:
        out.append(f"{letter}{start - last}.{end - start}")
        last = start
    return " ".join(out)


def decode(text: str) -> list[Note]:
    out, last = [], 0
    for item in text.split():
        gap, length = item[1:].split(".")
        start = last + int(gap)
        out.append((start, start + int(length), item[0]))
        last = start
    return out


# -- one frame per match ----------------------------------------------------

#: How far before the matched words a kind-of-statement cue may stand: a
#: degree names its field right after it, an experience cue may head a list.
_CUE_REACH_WORDS = 15
_DEGREE_REACH_WORDS = 6
#: Words that open another clause between a cue and the matched words
#: (`formacion en sistemas ... para integrarse a nuestro equipo de soporte`).
_CLAUSE_OPENERS = frozenset(
    {"to", "that", "which", "who", "where", "while", "para", "que", "onde", "quem", "donde",
     "quien", "cuando", "quando", "when"}
)  # fmt: skip
#: How far before the matched words a receiving cue may end.
_RECEIVE_REACH_WORDS = 2


def _between_words(text: str, a: int, b: int) -> int:
    return len(_WORD.findall(fold(text[a:b])[0])) if b > a else 0


def _reaches(text: str, note: Note, start: int) -> bool:
    between = _WORD.findall(fold(text[note[1] : start])[0])
    reach = _DEGREE_REACH_WORDS if note[2] == DEGREE else _CUE_REACH_WORDS
    return len(between) <= reach and not _CLAUSE_OPENERS.intersection(between)


def _sense_of_word(text: str, start: int, end: int, s: Sentence, data: _Data) -> Sense | None:
    word = _seq(text[start:end])
    if not word:
        return None
    form = word[0]
    before = [w for w, _, _ in _words(text[max(s.start, start - 60) : start], 0)]
    after = [w for w, _, _ in _words(text[end : min(s.end, end + 40)], 0)]
    clause_words: set[str] | None = None
    for e in data.senses:
        if form not in e.forms:
            continue
        if any(tuple(before[-len(b) :]) == b for b in e.before if len(before) >= len(b)):
            return e.sense
        if after and after[0] in e.after:
            return e.sense
        if e.clause:
            if clause_words is None:
                clause_words = {w for w, _, _ in _words(text[s.start : s.end], 0)}
            if clause_words & e.clause:
                return e.sense
    return None


def _company_head(text: str, start: int, end: int, s: Sentence, data: _Data) -> bool:
    """`payroll company`, `consultoria brasileira de recrutamento`,
    `referencia em vendas`: the words name the company's business."""
    after = re.match(r"[ \t]+([^\W_]+)", text[end : min(s.end, end + 30)])
    if after and fold(after.group(1))[0] in data.heads_after:
        return True
    before = [w for w, _, _ in _words(text[max(s.start, start - 60) : start], 0)]
    if before and before[-1] in data.links:
        return any(w in data.heads_before for w in before[-3:-1])
    return False


def _in_collaboration(text: str, start: int, end: int, here: list[Note], data: _Data) -> bool:
    for a, b, letter in here:
        if not a < start < b:
            continue
        if letter == COLLABORATION_OBJECT:
            return True
        if letter == WEAK_COLLABORATION_OBJECT:
            after = _seq(text[end : end + 30])[:1]
            if text[start : start + 1].isupper() or (after and after[0] in data.unit_nouns):
                return True
    return False


def _product_for(text: str, start: int, s: Sentence, data: _Data) -> bool:
    """`HR technology for payroll`, `software de folha`: a product or a
    technology whose purpose is the words."""
    before = [w for w, _, _ in _words(text[max(s.start, start - 50) : start], 0)]
    return (
        len(before) >= 2 and before[-1] in data.product_links and before[-2] in data.product_heads
    )


def _prefix(text: str, start: int) -> str:
    before = _seq(text[max(0, start - 12) : start])
    return before[-1] if before else ""


def frame(
    text: str,
    sentence: Sentence,
    notes: list[Note],
    start: int,
    end: int,
    concept_id: str,
    language: str,
    cause: str | None,
) -> EvidenceFrame:
    """The frame of the words at `start:end`, in `sentence` (whose context
    is already decided), given the sentence's shared `notes` and the
    structural `cause` the existing rules found (or None)."""
    ctx = sentence.context
    section = ctx.value
    data = load()
    if ctx in NOT_WORK:
        set_aside = {
            Context.BENEFIT: EvidenceType.BENEFIT,
            Context.CONDITION: EvidenceType.APPLICATION_PROCESS,
            Context.COMPANY_DESCRIPTION: EvidenceType.COMPANY_CONTEXT,
        }[ctx]
        whose = Owner.COMPANY if ctx is Context.COMPANY_DESCRIPTION else Owner.UNKNOWN
        return EvidenceFrame(concept_id, Sense.NAMED, whose, Direction.NONE, set_aside, section,
                             Certainty.HIGH, "section")  # fmt: skip
    supported = language in GRAMMAR_LANGUAGES
    sense, owner, direction = Sense.NAMED, Owner.ROLE, Direction.ROLE_PROVIDES
    kind: EvidenceType | None = None
    certainty = Certainty.HIGH
    because: str | None = cause
    if not supported:
        owner, direction, certainty = Owner.UNKNOWN, Direction.UNKNOWN, Certainty.LOW
    # The words' own grammar (structure.py), each cause on its dimension.
    if cause == structure.OTHER_SENSE:
        sense = Sense.OTHER
    elif cause == structure.ONE_WORD_LEVEL:
        sense = Sense.EDUCATION_LEVEL
    elif cause == structure.ONE_WORD_VERB:
        word = _seq(text[start:end])
        sense = Sense.VERB if word and word[0] in data.light_verbs else Sense.NAMED
    elif cause == structure.ONE_WORD_NAME:
        sense = Sense.PROPER_NAME
    elif cause == structure.ONE_WORD_COMPOUND:
        prefix = _prefix(text, start)
        sense = (
            Sense.PRE_SALES if prefix == "pre"
            else Sense.POST_SALES if prefix in ("pos", "post", "after", "apos")
            else Sense.OTHER
        )  # fmt: skip
    elif cause == structure.ONE_WORD_TEAM:
        owner, kind = Owner.OTHER_TEAM, EvidenceType.COLLABORATION
    elif cause == structure.THIRD_PARTY_SUBJECT:
        owner, direction = Owner.OTHER_TEAM, Direction.OTHER_PROVIDES
    elif cause == structure.ONE_WORD_RECIPIENT:
        direction = Direction.ROLE_RECEIVES
    elif cause in (structure.UNLINKED, structure.CLAUSE_BOUNDARY):
        kind, certainty = EvidenceType.UNKNOWN, Certainty.LOW
    elif cause == structure.ONE_WORD_QUALIFICATION:
        kind = EvidenceType.DEGREE_IN_FIELD
    elif cause == structure.ONE_WORD_MODIFIER:
        kind = EvidenceType.ARTIFACT_ONLY
    if supported and sense is Sense.NAMED:
        found = _sense_of_word(text, start, end, sentence, data)
        if found is not None:
            sense, because = found, f"sense:{found}"
    here = [n for n in notes if sentence.start <= n[0] < sentence.end]
    if sense is Sense.POST_SALES and any(n[2] == COMMERCIAL for n in here):
        sense = Sense.POST_SALES_COMMERCIAL
    clause = next(((a, b) for a, b in _clauses(text, sentence.start, sentence.end)
                   if a <= start < b), (sentence.start, sentence.end))  # fmt: skip
    if supported and owner is Owner.ROLE and _in_collaboration(text, start, end, here, data):
        owner, kind, because = Owner.OTHER_TEAM, EvidenceType.COLLABORATION, "collaboration object"
    if supported and owner is Owner.ROLE:
        if any(n[2] == COMPANY_SUBJECT for n in here):
            owner, kind, because = Owner.COMPANY, EvidenceType.COMPANY_CONTEXT, "company subject"
        elif any(n[2] == PRODUCT_SUBJECT and n[0] <= start < n[1] for n in here):
            owner, kind, because = Owner.PRODUCT, EvidenceType.PRODUCT_CONTEXT, "product subject"
        elif _company_head(text, start, end, sentence, data):
            owner, kind, because = Owner.COMPANY, EvidenceType.DOMAIN_CONTEXT, "company business"
        elif _product_for(text, start, sentence, data):
            owner, kind, because = Owner.PRODUCT, EvidenceType.PRODUCT_CONTEXT, "product for it"
    if supported and direction is Direction.ROLE_PROVIDES:
        for a, b, letter in here:
            # Before the words (`contar com suporte`), or holding them
            # (`com o suporte de`).
            if (
                letter == RECEIVING
                and clause[0] <= a < start
                and (b > start or _between_words(text, b, start) <= _RECEIVE_REACH_WORDS)
            ):
                direction, because = Direction.ROLE_RECEIVES, "receiving cue"
    # The kind of statement: the nearest cue before the words, in the clause.
    cue = None
    if supported:
        before = [n for n in here if clause[0] <= n[0] and n[1] <= start
                  and n[2] in _TYPE_OF_CUE and _reaches(text, n, start)]  # fmt: skip
        cue = max(before, default=None, key=lambda n: n[1])
    if kind is EvidenceType.ARTIFACT_ONLY:
        if cue is not None:
            kind, because = EvidenceType.TOOL_EXPERIENCE, "artifact with a skill cue"
    elif kind is None:
        if cue is not None:
            kind = _TYPE_OF_CUE[cue[2]]
        elif ctx is Context.REQUIREMENT:
            kind = EvidenceType.REQUIREMENT_SKILL
        elif ctx is Context.TOOL:
            kind = EvidenceType.TOOL_EXPERIENCE
        else:
            kind = EvidenceType.DUTY
    if kind is EvidenceType.DEGREE_IN_FIELD and any(
        n[2] == ALTERNATIVES and clause[0] <= n[0] < clause[1] for n in here
    ):
        certainty, because = Certainty.LOW, "one field among alternatives"
    return EvidenceFrame(concept_id, sense, owner, direction, kind, section, certainty, because)


class NoteIndex:
    """A posting's notes, found by position."""

    def __init__(self, notes: list[Note]) -> None:
        self.notes = notes
        self.starts = [n[0] for n in notes]

    def within(self, start: int, end: int) -> list[Note]:
        """Notes starting in `start:end` (a sentence's, or a clause's)."""
        i = bisect_right(self.starts, start - 1)
        return self.notes[i : bisect_right(self.starts, end - 1)]

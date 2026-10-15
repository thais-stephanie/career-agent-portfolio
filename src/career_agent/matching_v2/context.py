"""What each sentence of an advert is for (ADR-0037, part H).

A phrase in a duty is evidence that the person would do that work; the same
phrase in "About us" or among the benefits is not. Each sentence gets one
context from the heading above it or, under no heading, from its own words.
Unknown is OTHER, never a guess. The text is not rewritten: a sentence is a
pair of offsets into the original advert.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

import yaml

from career_agent.match.text import fold

DATA = Path(__file__).with_name("context_data.yaml")


class Context(StrEnum):
    RESPONSIBILITY = "RESPONSIBILITY"
    REQUIREMENT = "REQUIREMENT"
    TOOL = "TOOL"
    CONDITION = "CONDITION"
    COMPENSATION = "COMPENSATION"
    LOCATION = "LOCATION"
    BENEFIT = "BENEFIT"
    COMPANY_DESCRIPTION = "COMPANY_DESCRIPTION"
    OTHER = "OTHER"
    TITLE = "TITLE"


#: Contexts in which a phrase is evidence of the work itself.
WORK_EVIDENCE = frozenset(
    {Context.RESPONSIBILITY, Context.REQUIREMENT, Context.TOOL, Context.TITLE}
)
#: Contexts in which a phrase proves nothing about the work: the company, the
#: benefits, and the conditions of the hiring (process, contract, notices).
NOT_WORK = frozenset({Context.BENEFIT, Context.COMPANY_DESCRIPTION, Context.CONDITION})


@dataclass(frozen=True, slots=True)
class Sentence:
    start: int
    end: int
    context: Context
    heading: str | None = None


_WORDS = re.compile(r"[0-9a-z]+")


@lru_cache(maxsize=8192)
def _norm(text: str) -> str:
    """Folded words of `text`, joined. Pure, and asked up to three times of
    the same sentence (notices, the role's voice, sentence cues): cached."""
    return " ".join(_WORDS.findall(fold(text)[0]))


@lru_cache(maxsize=2)
def _cues(path: Path = DATA) -> tuple[dict[str, Context], tuple[tuple[str, Context], ...]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    headings = {_norm(cue): Context(kind) for kind, cues in raw["headings"].items() for cue in cues}
    sentence = tuple(
        (_norm(cue), Context(kind)) for kind, cues in raw["sentence_cues"].items() for cue in cues
    )
    return headings, sentence


@lru_cache(maxsize=2)
def _voices(path: Path = DATA) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(hiring-notice cues, role-voice cues), normalised."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    return (
        tuple(_norm(c) for c in raw["hiring_notice_cues"]),
        tuple(_norm(c) for c in raw["role_voice_cues"]),
    )


@lru_cache(maxsize=16)
def _cue_pattern(cues: tuple[str, ...]) -> re.Pattern[str]:
    """One alternation for a tuple of cues: found at some position exactly
    when one cue, between spaces, is (the same answer as testing each)."""
    return re.compile(" (?:" + "|".join(re.escape(c) for c in cues) + ") ")


def _has(cues: tuple[str, ...], text: str) -> bool:
    if not cues:
        return False
    return _cue_pattern(cues).search(f" {_norm(text)} ") is not None


@dataclass(frozen=True)
class _BenefitDuty:
    duties: tuple[str, ...]
    intros: tuple[str, ...]
    subject_verb: re.Pattern[str]
    receiving: frozenset[str]


@lru_cache(maxsize=2)
def _benefit_duty(path: Path = DATA) -> _BenefitDuty:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))["benefit_duty"]
    subjects = "|".join(re.escape(_norm(c)) for c in raw["subjects"])
    adverbs = "|".join(re.escape(_norm(c)) for c in raw["adverbs"])
    return _BenefitDuty(
        tuple(_norm(c) for c in raw["duty_cues"]),
        tuple(_norm(c) for c in raw["hiring_intro_cues"]),
        re.compile(rf" (?:{subjects}) (?:(?:{adverbs}) )*([a-z]+)(?= )"),
        frozenset(_norm(c) for c in raw["receiving_verbs"]),
    )


def benefit_states_duty(text: str) -> bool:
    """Whether a sentence under a benefits heading says, in its own words,
    that the person hired will do or must know some work: a duty cue
    (`responsible for`), the role being introduced (`we are looking for`), or
    the candidate as the subject of a verb that is not something given to
    them (`you will teach`, never `you will receive`). Anything else under
    that heading proves no work, the role's own voice included (`you will
    have support from the data team`)."""
    d = _benefit_duty()
    if _has(d.duties, text) or _has(d.intros, text):
        return True
    return any(m.group(1) not in d.receiving for m in d.subject_verb.finditer(f" {_norm(text)} "))


_BULLET = re.compile(r"^\s*(?:[-*\u2022\u25cf\u25aa>]|\d{1,2}[.)])\s")


def _unknown_heading(line: str) -> bool:
    """A line shaped like a heading that no table knows (`About the role`,
    `The opportunity:`). It closes the section above it: what follows is of
    no known purpose until a known heading says otherwise. A bullet or a
    sentence is never one."""
    raw = line.strip()
    if not raw or _BULLET.match(raw) or len(raw) > 60:
        return False
    text = _heading_text(raw)
    if not text or text[-1:] in ".!?;," or len(text.split()) > 6:
        return False
    marked = raw.startswith(("#", "**", "__", "<")) or raw.rstrip("*_ ").endswith(":")
    words = [w for w in re.findall(r"[^\W\d_]+", text) if len(w) > 3]
    titled = bool(words) and all(w[0].isupper() for w in words)
    return marked or titled


_TAG = re.compile(r"<[^>]*>")
_NUMBERING = re.compile(r"^\s*(?:\d{1,2}|[ivx]{1,4})[.)]\s+", re.IGNORECASE)
_MARKS = "#*:-=\u2022\u25cf\u25aa\u2013\u2014> \t"
#: Words a heading may carry after a known heading without being a sentence
#: (`Requirements for this role`, `Responsabilidades da vaga`).
_HEADING_TAIL = frozenset(
    [
        "for",
        "this",
        "the",
        "a",
        "an",
        "our",
        "your",
        "role",
        "position",
        "job",
        "team",
        "of",
        "and",
        "in",
        "at",
        "with",
        "you",
        "we",
        "ll",
        "da",
        "do",
        "de",
        "das",
        "dos",
        "desta",
        "dessa",
        "deste",
        "desse",
        "vaga",
        "cargo",
        "posicao",
        "funcao",
        "time",
        "equipe",
        "para",
        "na",
        "no",
        "e",
        "o",
        "os",
        "as",
        "la",
        "el",
        "del",
        "puesto",
    ]
)


def _heading_text(line: str) -> str:
    """A line without markup: HTML tags, Markdown marks, numbering, bullets."""
    text = _TAG.sub(" ", line)
    text = text.replace("**", " ").replace("__", " ")
    text = _NUMBERING.sub("", text.strip())
    return text.strip().strip(_MARKS).strip()


def heading_context(line: str) -> Context | None:
    """The context a heading line opens, or None when the line is not one.

    A heading is a short line, with or without markup (`## Responsibilities`,
    `**What you'll do:**`, `<h3>Requisitos</h3>`, `1. Benefits`), that is a
    known heading, or a known heading followed only by words a heading
    carries (`Requirements for this role`) or closed by a colon. A bullet that
    merely starts with a heading word (`Experience with SQL`) is a sentence."""
    raw = line.strip()
    stripped = _heading_text(raw)
    if not stripped or len(stripped) > 60 or stripped[-1:] in ".!?;,":
        return None
    norm = _norm(stripped)
    if not norm or len(norm.split()) > 8:
        return None
    headings, _ = _cues()
    if norm in headings:
        return headings[norm]
    ends_with_colon = _TAG.sub("", raw).rstrip("*_ \t").endswith(":")
    # A known heading that is a proper prefix of the line, by whole words,
    # tried in the table's own order (the first that fits wins, as before):
    # looked up per prefix instead of testing all 156 headings per line.
    order = _heading_order()
    words = norm.split()
    prefixes = (" ".join(words[:k]) for k in range(1, len(words)))
    found = sorted(
        (order[cue][0], k, order[cue][1]) for k, cue in enumerate(prefixes, start=1) if cue in order
    )
    for _rank, k, kind in found:
        if ends_with_colon or all(w in _HEADING_TAIL for w in words[k:]):
            return kind
    return None


@lru_cache(maxsize=2)
def _heading_order(path: Path = DATA) -> dict[str, tuple[int, Context]]:
    headings, _ = _cues(path)
    return {cue: (i, kind) for i, (cue, kind) in enumerate(headings.items())}


def inline_heading(line: str) -> tuple[Context, int] | None:
    """`Responsibilities: manage the warehouse.` A known heading before a
    colon on the line's own text: its context, and where the text after the
    colon starts (an offset into `line`)."""
    at = line.find(":")
    if at <= 0 or at > 60:
        return None
    kind = heading_context(line[: at + 1])
    if kind is None:
        return None
    rest = at + 1
    while rest < len(line) and line[rest] in " \t":
        rest += 1
    return (kind, rest) if line[rest:].strip() else None


def _sentence_cue(text: str) -> Context | None:
    norm = f" {_norm(text)} "
    _, cues = _cues()
    for cue, kind in cues:
        if f" {cue} " in norm:
            return kind
    return None


#: A hiring notice is a sentence. A unit longer than this is a section an
#: advert wrote without breaks (seen on a Brazilian source, 2026-10-15), and
#: one notice inside it must not set aside every duty around it.
_NOTICE_REACH = 400

_BREAK = re.compile(r"[.!?;]+(?=\s)|\n")


def _lines(text: str, cuts: tuple[int, ...]) -> list[tuple[int, str]]:
    """(offset, line) for every line of `text`, a line also ending at each of
    `cuts` (the derived structure of an advert written without breaks,
    `text_structure`): each piece is still a substring at its own offset."""
    out = []
    bounds = sorted(set(cuts))
    for line_match in re.finditer(r"[^\n]*\n?", text):
        line = line_match.group(0)
        if not line:
            break
        start = line_match.start()
        lo = bisect_right(bounds, start)
        hi = bisect_left(bounds, start + len(line), lo)
        inner = [c - start for c in bounds[lo:hi]]
        last = 0
        for c in [*inner, len(line)]:
            if c > last:
                out.append((start + last, line[last:c]))
            last = c
    return out


def sentences(text: str, cuts: tuple[int, ...] = ()) -> list[Sentence]:
    """The advert cut into sentences, each with its context. Headings are not
    sentences: they set the context of what follows them. `cuts` are derived
    line ends (`text_structure.cuts`); the text itself is never rewritten."""
    out: list[Sentence] = []
    current: Context | None = None
    heading: str | None = None
    for start, line in _lines(text, cuts):
        opened = heading_context(line)
        if opened is not None:
            current, heading = opened, line.strip()
            continue
        if not line.strip():
            continue
        if _unknown_heading(line):
            current, heading = None, None
        cursor = start
        body = line.rstrip("\n")
        inline = inline_heading(body)
        if inline is not None:
            current, heading = inline[0], body[: inline[1]].strip()
            cursor = start + inline[1]
            body = body[inline[1] :]
        for piece in _BREAK.split(body) + [""]:
            if not piece:
                continue
            at = text.find(piece, cursor)
            if at < 0:
                continue
            cursor = at + len(piece)
            stripped = piece.strip()
            if len(stripped) < 2:
                continue
            lead = at + (len(piece) - len(piece.lstrip()))
            context = current if current is not None else (_sentence_cue(stripped) or Context.OTHER)
            notices, voices = _voices()
            if len(stripped) <= _NOTICE_REACH and _has(notices, stripped):
                context = Context.CONDITION
            elif context is Context.BENEFIT:
                if benefit_states_duty(stripped):
                    context = Context.OTHER
            elif context in NOT_WORK and _has(voices, stripped):
                context = Context.OTHER
            out.append(Sentence(lead, lead + len(stripped), context, heading))
    return out

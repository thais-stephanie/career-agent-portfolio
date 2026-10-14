"""What each sentence of an advert is for (ADR-0037, part H).

A phrase in a duty is evidence that the person would do that work; the same
phrase in "About us" or among the benefits is not. Each sentence gets one
context from the heading above it or, under no heading, from its own words.
Unknown is OTHER, never a guess. The text is not rewritten: a sentence is a
pair of offsets into the original advert.
"""

from __future__ import annotations

import re
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
#: Contexts in which a phrase proves nothing about the work.
NOT_WORK = frozenset({Context.BENEFIT, Context.COMPANY_DESCRIPTION})


@dataclass(frozen=True, slots=True)
class Sentence:
    start: int
    end: int
    context: Context
    heading: str | None = None


_WORDS = re.compile(r"[0-9a-z]+")


def _norm(text: str) -> str:
    return " ".join(_WORDS.findall(fold(text)[0]))


@lru_cache(maxsize=2)
def _cues(path: Path = DATA) -> tuple[dict[str, Context], tuple[tuple[str, Context], ...]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    headings = {_norm(cue): Context(kind) for kind, cues in raw["headings"].items() for cue in cues}
    sentence = tuple(
        (_norm(cue), Context(kind)) for kind, cues in raw["sentence_cues"].items() for cue in cues
    )
    return headings, sentence


def heading_context(line: str) -> Context | None:
    """The context a heading line opens, or None when the line is not one."""
    stripped = line.strip().strip("#*:- \t").strip()
    if not stripped or len(stripped) > 60 or stripped[-1:] in ".!?;,":
        return None
    norm = _norm(stripped)
    if not norm or len(norm.split()) > 8:
        return None
    headings, _ = _cues()
    if norm in headings:
        return headings[norm]
    # "Responsibilities and duties", "Requisitos e qualificacoes": a known
    # heading at the start of a short line.
    for cue, kind in headings.items():
        if norm.startswith(cue + " ") and len(cue) >= 5:
            return kind
    return None


def _sentence_cue(text: str) -> Context | None:
    norm = f" {_norm(text)} "
    _, cues = _cues()
    for cue, kind in cues:
        if f" {cue} " in norm:
            return kind
    return None


_BREAK = re.compile(r"[.!?;]+(?=\s)|\n")


def sentences(text: str) -> list[Sentence]:
    """The advert cut into sentences, each with its context. Headings are not
    sentences: they set the context of what follows them."""
    out: list[Sentence] = []
    current: Context | None = None
    heading: str | None = None
    for line_match in re.finditer(r"[^\n]*\n?", text):
        line = line_match.group(0)
        if not line:
            break
        start = line_match.start()
        opened = heading_context(line)
        if opened is not None:
            current, heading = opened, line.strip()
            continue
        if not line.strip():
            continue
        cursor = start
        body = line.rstrip("\n")
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
            out.append(Sentence(lead, lead + len(stripped), context, heading))
    return out

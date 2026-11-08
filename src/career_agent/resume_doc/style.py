"""Style rules of the resume-tailoring spec v1, read deterministically.

A line of resume text is checked for what the spec forbids: the em dash; an en
dash outside a date or number range; a banned word or phrase; a contrastive
construction ("not X, but Y"); an exclamation mark, a question, an emoji; an
adjective that rates the candidate. Each problem is a finding with a code and
the words that raised it. Nothing here says a line is true or good.

A banned word inside a literal the person did not choose to write (an
employer's, tool's, certification's or project's official name, or text the
person typed as a literal) is not the line's own wording, so `literals` are
blanked out before the words are read: "Robust Systems Inc." never raises
"robust".

`limit_findings` reads the one kind of claim limit that can be read
mechanically: a phrase the claim's limits say never to write. A limit that
forbids what a line IMPLIES cannot be decided by code and is the person's to
answer.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

EM_DASH = "\N{EM DASH}"
EN_DASH = "\N{EN DASH}"

#: Banned words, as patterns over folded text (hyphens read as spaces).
BANNED_WORDS = {
    "spearheaded": r"spearhead\w*",
    "leveraged": r"leverag\w*",
    "utilized": r"utili[sz]\w*",
    "passionate": r"passionate\w*",
    "results-driven": r"results? driven",
    "dynamic": r"dynamic",
    "cutting-edge": r"cutting edge",
    "seamless": r"seamless\w*",
    "robust": r"robust\w*",
    "synergy": r"synerg\w*",
    "best-in-class": r"best in class",
    "world-class": r"world class",
    "game-changer": r"game changer\w*|game changing",
    "detail-oriented": r"detail oriented",
    "team player": r"team players?",
    "fast-paced": r"fast paced",
    "messy": r"messy",
    "bottleneck": r"bottleneck\w*",
}
BANNED_PHRASES = {
    "innovative solutions": r"innovative solutions?",
    "proven track record": r"proven track record",
    "what caught my attention": r"what caught my attention",
    "got stuck with me": r"(?:got|gets) stuck with me",
    "the interesting part": r"the interesting part",
}
#: Adjectives that rate the candidate.
RATING = {"exceptional": r"exceptional\w*", "outstanding": r"outstanding"}
#: "not X, but Y", "this isn't about X, it's about Y", "more than just X".
CONTRASTIVE = (
    r"\bnot (?:just |only |merely )?[^.;:]{1,60}? but\b",
    r"\bis ?n ?t about\b[^.]{0,80}\bit ?s about\b",
    r"\bmore than just\b",
)
_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec|present|current"


def fold(text: str) -> str:
    """Lower case, accents off, apostrophes and hyphens read as spaces."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", re.sub(r"[-'\N{RIGHT SINGLE QUOTATION MARK}]", " ", text))


def _blank(text: str, literals: Iterable[str]) -> str:
    """`text` folded, with every literal it holds replaced by spaces."""
    out = fold(text)
    for literal in sorted({fold(x).strip() for x in literals if x and x.strip()}, key=len,
                          reverse=True):  # fmt: skip
        out = re.sub(rf"(?<![a-z0-9]){re.escape(literal)}(?![a-z0-9])", " ", out)
    return out


def _emoji(ch: str) -> bool:
    cp = ord(ch)
    return 0x1F300 <= cp <= 0x1FAFF or 0x2600 <= cp <= 0x27BF or 0x1F000 <= cp <= 0x1F2FF


def _range_dash(text: str, i: int) -> bool:
    """The en dash at `i` joins a range: a number or year before it, and a
    number, a month or "Present" after it ("2019 - 2021", "60-80")."""
    before = text[max(0, i - 3) : i].rstrip()
    after = fold(text[i + 1 : i + 12]).lstrip()
    return bool(before[-1:].isdigit()) and bool(re.match(rf"(?:\d|(?:{_MONTHS}))", after))


def findings(text: str, literals: Iterable[str] = ()) -> list[dict[str, str]]:
    """Every style problem of one line, as `{code, match}`. Empty: none found."""
    out: list[dict[str, str]] = []
    if EM_DASH in text:
        out.append({"code": "EM_DASH", "match": EM_DASH})
    for i, ch in enumerate(text):
        if ch == EN_DASH and not _range_dash(text, i):
            out.append({"code": "EN_DASH_OUTSIDE_RANGE", "match": text[max(0, i - 10) : i + 10]})
    if "!" in text:
        out.append({"code": "EXCLAMATION", "match": "!"})
    if "?" in text:
        out.append({"code": "QUESTION", "match": "?"})
    if any(_emoji(ch) for ch in text):
        out.append({"code": "EMOJI", "match": next(ch for ch in text if _emoji(ch))})
    words = _blank(text, literals)
    for code, table in (("BANNED_WORD", BANNED_WORDS), ("BANNED_PHRASE", BANNED_PHRASES),
                        ("RATING_ADJECTIVE", RATING)):  # fmt: skip
        for name, pattern in table.items():
            if re.search(rf"(?<![a-z0-9])(?:{pattern})(?![a-z0-9])", words):
                out.append({"code": code, "match": name})
    for pattern in CONTRASTIVE:
        hit = re.search(pattern, words)
        if hit:
            out.append({"code": "CONTRASTIVE", "match": hit.group(0)})
    return out


def limit_findings(text: str, forbidden: Iterable[str]) -> list[dict[str, str]]:
    """A phrase a claim's limits say never to write, found in `text` as whole
    words (case, accents and hyphens aside). Implication is not read here."""
    words = fold(text)
    return [
        {"code": "LIMIT", "match": phrase}
        for phrase in forbidden
        if phrase.strip()
        and re.search(rf"(?<![a-z0-9]){re.escape(fold(phrase).strip())}(?![a-z0-9])", words)
    ]

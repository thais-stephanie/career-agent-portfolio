# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Small deterministic text utilities: tokenizing, number extraction, dates, similarity."""

from __future__ import annotations

import difflib
import re
from datetime import date

from resume_tailor.core.lexicon import STOPWORDS

_WORD = re.compile(r"[a-z0-9][a-z0-9+#./-]*")
_NUMBER = re.compile(
    r"(?<![A-Za-z])(?:[$€£R]\$?\s?)?~?\d[\d,]*(?:\.\d+)?\s?(?:%|k\b|K\b|M\b|MM\b|million\b|bn\b|B\b)?",
)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(])")
_MONTHS = {
    m: i + 1
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    )
}


def tokens(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in STOPWORDS and len(t) > 1]


def content_tokens(text: str) -> set[str]:
    """Lightly stemmed content words for overlap scoring."""
    out = set()
    for t in tokens(text):
        t = re.sub(r"[.,;:]+$", "", t)
        if t.isdigit():
            continue
        # crude stemming: plural / -ing / -ed
        for suf in ("ing", "ed", "es", "s"):
            if len(t) > 5 and t.endswith(suf):
                t = t[: -len(suf)]
                break
        out.add(t)
    return out


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text.strip()) if s.strip()]


def normalize_number(tok: str) -> str:
    """'$1.85M' -> '1.85m', '~668' -> '668', '60–80' handled by caller (split)."""
    t = tok.strip().lower().replace(",", "").replace(" ", "")
    t = t.lstrip("~").lstrip("$€£").replace("r$", "")
    t = t.replace("million", "m").replace("mm", "m")
    return t


def numbers_in(text: str) -> list[str]:
    """Normalized numeric tokens in text (ranges split on – / - / to)."""
    text = text.replace("–", "-").replace("—", "-")
    out: list[str] = []
    for m in _NUMBER.finditer(text):
        raw = m.group(0)
        if not re.search(r"\d", raw):
            continue
        n = normalize_number(raw)
        if n:
            out.append(n)
    return out


def number_variants(n: str) -> set[str]:
    """Equivalent spellings so '1.85m' matches '1,850,000' and '$1.85M'."""
    v = {n}
    base = n.rstrip("%")
    unit = ""
    m = re.match(r"^([\d.]+)([km]?)$", base)
    if m:
        num, unit = m.group(1), m.group(2)
        try:
            f = float(num)
        except ValueError:
            return v
        if unit == "m":
            v.add(f"{int(f * 1_000_000)}")
            v.add(f"{f * 1000:g}k")
        elif unit == "k":
            v.add(f"{int(f * 1000)}")
        else:
            if f >= 1_000_000 and f % 10_000 == 0:
                v.add(f"{f / 1_000_000:g}m")
            if f >= 1000 and f % 100 == 0:
                v.add(f"{f / 1000:g}k")
        v.add(f"{f:g}")
        if f == int(f):
            v.add(str(int(f)))
    return v


def parse_ym(s: str | None) -> tuple[int, int] | None:
    if not s:
        return None
    m = re.match(r"^(\d{4})-(\d{2})$", s.strip())
    if m:
        return int(m.group(1)), int(m.group(2))
    return None


def months_between(start: str, end: str | None, today: date | None = None) -> int:
    s = parse_ym(start)
    if not s:
        return 0
    if end is None:
        today = today or date.today()  # noqa: DTZ011 - tenure uses the local calendar date.
        e = (today.year, today.month)
    else:
        e = parse_ym(end) or (s[0], s[1])
    return max(0, (e[0] - s[0]) * 12 + (e[1] - s[1]))


def format_ym(s: str | None, present: str = "Present") -> str:
    p = parse_ym(s)
    if not p:
        return present if s is None else s
    names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    return f"{names[p[1] - 1]} {p[0]}"


def years_claims(text: str) -> list[int]:
    """'8+ years', '5 years of', 'over 10 years' -> [8, 5, 10]."""
    out = []
    for m in re.finditer(
        r"(?:over\s+|more than\s+|at least\s+)?(\d{1,2})\s?\+?\s?(?:-\s?\d{1,2}\s?)?(?:years|yrs|year)",
        text.lower(),
    ):
        out.append(int(m.group(1)))
    return out


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def first_verb(text: str) -> str:
    w = re.findall(r"[A-Za-z]+", text)
    return w[0].lower() if w else ""


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text))

"""Role-family aliases for an anchor: bounded, deduplicated, occupation-agnostic.

The deterministic planner. It never needs a network, a key or a model, so the
targeted lane works for everybody; an AI planner may add aliases later under
its own `generator` name, and these remain the fallback.

Rules, in order, each producing at most a few candidates:

1. **Alternatives in the title.** "GTM Engineer / AI Engineer" is two titles,
   and so is "Hairdresser or Stylist".
2. **Qualifiers removed.** "(Remote)", "- LATAM", seniority and grade words
   ("Senior", "Sr.", "Lead", "II", "Pleno"): a search for the base title finds
   every level, and levels are a preference the scorer reads, not a query.
3. **Abbreviations both ways.** "SDR" and "Sales Development Representative",
   "RN" and "Registered Nurse". Only abbreviations whose expansion is the same
   job in every field are listed; "PM" (product? project?) and "AM" are not.
4. **Established equivalents.** A short table of titles the market uses for
   the same work ("Hairdresser" and "Hair Stylist", "Software Engineer" and
   "Software Developer"). Whole titles only: swapping a head noun
   ("Engineer" to "Developer") would turn a civil engineer into a developer.

Aliases are SEARCH TERMS. They are never shown as something the person said,
never become Search Fit phrases, and never rule out or score a posting.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from career_agent.discovery.anchors import (
    MAX_ALIASES_PER_ANCHOR,
    MAX_TEXT,
    Alias,
    Anchor,
    clean,
    folded,
)

GENERATOR = "rule"

_ALTERNATIVES = re.compile(r"\s*(?:/|\||\bor\b|\bou\b)\s*", re.IGNORECASE)
_PARENTHESES = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_TRAILING_QUALIFIER = re.compile(r"\s+[-,]\s+[^-,]+$")
_LEVEL_WORDS = (
    "senior",
    "sr",
    "sr.",
    "junior",
    "jr",
    "jr.",
    "lead",
    "principal",
    "staff",
    "associate",
    "entry level",
    "entry-level",
    "mid-level",
    "mid level",
    "pleno",
    "sênior",
    "júnior",
    "trainee",
)
_LEVEL_PREFIX = re.compile(
    r"^(?:" + "|".join(re.escape(w) for w in sorted(_LEVEL_WORDS, key=len, reverse=True)) + r")\s+",
    re.IGNORECASE,
)
#: Only grades and level words that never end a real title: "Team Lead" and
#: "Staff" are titles, "Accountant II" and "Analista Pleno" are levels.
_LEVEL_SUFFIX = re.compile(
    r"\s+(?:i{1,3}|iv|v|[1-5]|senior|sr\.?|junior|jr\.?|pleno|sênior|júnior|trainee)$",
    re.IGNORECASE,
)

#: Abbreviation -> expansion. The same job in every field that uses it.
ABBREVIATIONS: dict[str, str] = {
    "sdr": "Sales Development Representative",
    "bdr": "Business Development Representative",
    "ae": "Account Executive",
    "csm": "Customer Success Manager",
    "rn": "Registered Nurse",
    "lpn": "Licensed Practical Nurse",
    "cna": "Certified Nursing Assistant",
    "cpa": "Certified Public Accountant",
    "hrbp": "HR Business Partner",
    "qa engineer": "Quality Assurance Engineer",
    "ux designer": "User Experience Designer",
    "ux researcher": "User Experience Researcher",
    "sre": "Site Reliability Engineer",
    "esl teacher": "English as a Second Language Teacher",
}
_EXPANSIONS = {folded(v): k for k, v in ABBREVIATIONS.items()}

#: Whole titles the market uses for the same work. Symmetric.
EQUIVALENTS: tuple[tuple[str, ...], ...] = (
    ("Software Engineer", "Software Developer"),
    ("Hair Stylist", "Hairdresser"),
    ("Account Executive", "Sales Executive"),
    ("Customer Success Manager", "Client Success Manager"),
    ("Sales Development Representative", "Business Development Representative"),
    ("ICU Nurse", "Critical Care Nurse", "Intensive Care Nurse"),
    ("Bookkeeper", "Accounting Clerk"),
    ("Teacher", "Educator"),
    ("Customer Support Specialist", "Customer Service Representative"),
    ("Executive Assistant", "Personal Assistant"),
    ("Recruiter", "Talent Acquisition Specialist"),
    ("Data Analyst", "Business Intelligence Analyst"),
)
_EQUIVALENT_OF: dict[str, tuple[str, ...]] = {}
for _family in EQUIVALENTS:
    for _title in _family:
        _EQUIVALENT_OF[folded(_title)] = tuple(t for t in _family if t != _title)


def _titlecase_abbreviation(text: str) -> str:
    return text.upper() if len(text) <= 5 and " " not in text else text


def _strip_qualifiers(title: str) -> str:
    text = _PARENTHESES.sub("", title)
    text = _TRAILING_QUALIFIER.sub("", text)
    for _ in range(2):
        text = _LEVEL_PREFIX.sub("", text)
        text = _LEVEL_SUFFIX.sub("", text)
    return clean(text)


def _variants(title: str) -> Iterable[str]:
    base = _strip_qualifiers(title)
    if base and folded(base) != folded(title):
        yield base
    for candidate in (title, base):
        key = folded(candidate)
        if key in ABBREVIATIONS:
            yield ABBREVIATIONS[key]
        # Contracted only to three letters or more: "AE" or "RN" alone as a
        # search term matches too much unrelated text to be worth a query.
        if key in _EXPANSIONS and len(_EXPANSIONS[key]) >= 3:
            yield _titlecase_abbreviation(_EXPANSIONS[key])
        yield from _EQUIVALENT_OF.get(key, ())
        # "Senior SDR" -> "SDR" above; the abbreviation inside a longer
        # title ("SDR Team Lead") is expanded in place.
        words = candidate.split()
        for i, word in enumerate(words):
            expansion = ABBREVIATIONS.get(word.casefold())
            if expansion and len(words) > 1:
                yield " ".join([*words[:i], expansion, *words[i + 1 :]])


def aliases_for(anchor: Anchor, taken: set[str] | None = None) -> list[Alias]:
    """At most `MAX_ALIASES_PER_ANCHOR` aliases for one anchor, none repeating
    the anchor, each other, or anything in `taken` (folded texts)."""
    seen = set(taken or ())
    seen.add(folded(anchor.text))
    out: list[Alias] = []

    def offer(text: str) -> None:
        text = clean(text)
        key = folded(text)
        if (
            len(out) >= MAX_ALIASES_PER_ANCHOR
            or not text
            or len(text) > MAX_TEXT
            or key in seen
            or len(key) < 2
        ):
            return
        seen.add(key)
        out.append(Alias(text=text, anchor=anchor.text, source="rule", generator=GENERATOR))

    parts = [p for p in _ALTERNATIVES.split(anchor.text) if clean(p)]
    titles = parts if len(parts) > 1 else [anchor.text]
    for part in titles:
        if len(titles) > 1:
            offer(part)
    for part in titles:
        for variant in _variants(clean(part)):
            offer(variant)
    return out


def plan_aliases(anchors: Iterable[Anchor]) -> tuple[Alias, ...]:
    """Aliases for every anchor, deduplicated across the whole set."""
    anchors = tuple(anchors)
    taken = {folded(a.text) for a in anchors}
    out: list[Alias] = []
    for anchor in anchors:
        made = aliases_for(anchor, taken)
        taken.update(folded(a.text) for a in made)
        out.extend(made)
    return tuple(out)

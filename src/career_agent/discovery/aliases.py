"""Role-family aliases for an anchor: bounded, deduplicated, occupation-agnostic.

The deterministic planner. It never needs a network, a key or a model, so the
targeted lane works for everybody; an AI planner may add aliases later under
its own `generator` name, and these remain the fallback.

Rules, in order, each producing at most a few candidates:

1. **Alternatives in the title.** "GTM Coordinator / AI Engineer" is two titles,
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
    RoleAnchors,
    clean,
    folded,
)

GENERATOR = "rule"
#: Titles are read at most this long: every rule is linear on that.
MAX_TITLE = 200

_ALTERNATIVES = re.compile(r"\s*(?:/|\||\bor\b|\bou\b)\s*", re.IGNORECASE)
_PARENTHESES = re.compile(r"\s*[\(\[][^)\]]*[\)\]]")
_TRAILING_QUALIFIER = re.compile(r"\s+[-,]\s+[^-,]+$")
#: Removed from the FRONT of a title. "Lead", "Staff", "Principal" and
#: "Associate" are not here: "Lead Generation Specialist", "Staff Accountant"
#: and "Associate Attorney" name jobs, not levels.
_LEVEL_WORDS = (
    "senior",
    "sr",
    "sr.",
    "junior",
    "jr",
    "jr.",
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

#: Abbreviations that QUALIFY a role inside a longer title, with the casing
#: the market writes them in. Expanded and contracted in place, both ways:
#: "GTM Coordinator" and "Go-to-Market Coordinator", "Revenue Operations Analyst"
#: and "RevOps Analyst". Only abbreviations with one meaning in a job title.
IN_TITLE: dict[str, str] = {
    "HR": "Human Resources",
    "UX": "User Experience",
    "ML": "Machine Learning",
    "BI": "Business Intelligence",
    "GTM": "Go-to-Market",
    "RevOps": "Revenue Operations",
    "SalesOps": "Sales Operations",
    "MarTech": "Marketing Technology",
}
#: Matched as the market writes it, or in capitals: "bi" in "Bi Lingual" or
#: "ml" in a sentence is not an abbreviation.
_IN_TITLE_SHORT = {
    **{k: (k, v) for k, v in IN_TITLE.items()},
    **{k.upper(): (k, v) for k, v in IN_TITLE.items()},
}


def _in_title(title: str) -> Iterable[str]:
    """Expand a qualifying abbreviation in place, or contract its phrase."""
    words = title.split()
    if len(words) < 2:
        return
    for i, word in enumerate(words):
        hit = _IN_TITLE_SHORT.get(word)
        if hit:
            yield " ".join([*words[:i], hit[1], *words[i + 1 :]])
    for short, long in IN_TITLE.items():
        # "Go-to-Market" is also written "Go to Market".
        spelled = re.escape(long).replace("\\-", "[- ]")
        pattern = re.compile(r"\b" + spelled + r"\b", re.IGNORECASE)
        # Only inside a longer title: "Go to Market" alone is not a role.
        match = pattern.search(title)
        if match and len(match.group(0).split()) < len(words):
            yield clean(pattern.sub(short, title, count=1))


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
    """ "sdr" -> "SDR", "qa engineer" -> "QA Engineer"."""
    first, _, rest = text.partition(" ")
    return " ".join(p for p in (first.upper(), rest.title()) if p)


#: Never a search term on its own: a level ("Sr."), a grade ("II") or a
#: fragment too short to name a role ("AI", "UX") unless it is a known
#: abbreviation. Splitting and trimming only ever leave two-word titles, so a
#: single word here is the person's own role without its level ("Teacher").
def _worth_searching(text: str) -> bool:
    words = [w.casefold().strip(".") for w in text.split()]
    if not words or all(w in _LEVEL_WORDS or w in _GRADES for w in words):
        return False
    return len(words) >= 2 or len(words[0]) >= 3 or text.casefold() in ABBREVIATIONS


_GRADES = frozenset({"i", "ii", "iii", "iv", "v", "1", "2", "3", "4", "5"})


def _strip_qualifiers(title: str) -> str:
    text = _PARENTHESES.sub("", clean(title)[:MAX_TITLE])
    trimmed = _TRAILING_QUALIFIER.sub("", text)
    # "Manager - Customer Success": the part after the dash is the role, so
    # a trim that leaves one word is not a qualifier removed.
    if len(trimmed.split()) >= 2:
        text = trimmed
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
        yield from _in_title(candidate)


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
            or not _worth_searching(text)
        ):
            return
        seen.add(key)
        out.append(Alias(text=text, anchor=anchor.text, source="rule", generator=GENERATOR))

    parts = [clean(p) for p in _ALTERNATIVES.split(anchor.text) if clean(p)]
    # Alternatives only when every side is a title of its own: "GTM Coordinator /
    # AI Engineer" is two, "AI/ML Engineer" and "UX/UI Designer" are one.
    titles = parts if len(parts) > 1 and all(len(p.split()) >= 2 for p in parts) else [anchor.text]
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


def effective(anchors: RoleAnchors) -> RoleAnchors:
    """The anchors with their stored aliases PLUS what the current rules make.

    Aliases are derived data: anchors saved before a rule existed get its
    aliases too, without anybody re-saving and without their words changing.
    Stored aliases come first (a later AI planner's, for example), and the
    per-anchor cap still holds.
    """
    current = {folded(a.text) for a in anchors.anchors}
    merged = (*anchors.aliases, *plan_aliases(anchors.anchors))
    seen: set[str] = set(current)
    counts: dict[str, int] = {}
    unique = []
    for alias in merged:
        owner, text = folded(alias.anchor), folded(alias.text)
        if owner not in current or text in seen:
            continue
        if counts.get(owner, 0) >= MAX_ALIASES_PER_ANCHOR:
            continue
        counts[owner] = counts.get(owner, 0) + 1
        seen.add(text)
        unique.append(alias)
    return RoleAnchors(anchors=anchors.anchors, aliases=tuple(unique))

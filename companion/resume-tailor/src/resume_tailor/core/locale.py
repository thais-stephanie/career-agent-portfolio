# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Output locale for generated resume prose.

The evidence bank is written in mixed British/US spelling because it quotes
source documents. Rendered prose is normalised to one locale so a resume does
not say "centralised" in one bullet and "modeling" in the next. Only generated
text is touched (summary, bullets, skills display names, company descriptors,
headline); evidence records, conflict statements and provenance are never
rewritten. The map is an explicit word list, not a suffix regex, so words such
as "enterprise", "expertise", "analysis" or "supervise" cannot be damaged.
"""

from __future__ import annotations

import re

DEFAULT_RESUME_LOCALE = "en-US"

# stem -> the US spelling of the stem; the suffix that follows is kept
_IZE_STEMS = [
    "central",
    "organ",
    "priorit",
    "optim",
    "normal",
    "standard",
    "synchron",
    "real",
    "recogn",
    "util",
    "minim",
    "maxim",
    "summar",
    "categor",
    "author",
    "custom",
    "special",
    "visual",
    "initial",
    "serial",
    "modern",
    "operational",
    "digit",
    "monet",
    "final",
    "formal",
    "material",
    "item",
    "parameter",
    "token",
    "capital",
    "harmon",
    "local",
    "general",
    "rational",
    "character",
    "modular",
    "contextual",
    "personal",
]
# whole-word map (British -> US); case handled by _match_case
_WORDS = {
    "modelling": "modeling",
    "modelled": "modeled",
    "labelling": "labeling",
    "labelled": "labeled",
    "cancelling": "canceling",
    "cancelled": "canceled",
    "travelling": "traveling",
    "travelled": "traveled",
    "behaviour": "behavior",
    "behaviours": "behaviors",
    "behavioural": "behavioral",
    "catalogue": "catalog",
    "catalogues": "catalogs",
    "analyse": "analyze",
    "analysed": "analyzed",
    "analysing": "analyzing",
    "analyses": "analyzes",
    "licence": "license",
    "licences": "licenses",
    "programme": "program",
    "programmes": "programs",
    "colour": "color",
    "colours": "colors",
    "favour": "favor",
    "favourite": "favorite",
    "defence": "defense",
    "fulfil": "fulfill",
    "fulfilment": "fulfillment",
    "enrol": "enroll",
    "enrolment": "enrollment",
    "judgement": "judgment",
    "practise": "practice",
    "practised": "practiced",
    "practising": "practicing",
    "offence": "offense",
    "towards": "toward",
    "artefact": "artifact",
    "artefacts": "artifacts",
    "cheque": "check",
    "cheques": "checks",
    "grey": "gray",
    "manoeuvre": "maneuver",
    "aluminium": "aluminum",
    "learnt": "learned",
    "spelt": "spelled",
}
_IZE_RE = re.compile(
    r"\b("
    + "|".join(sorted(_IZE_STEMS, key=len, reverse=True))
    + r")is(e|es|ed|ing|ation|ations|er|ers)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(
    r"\b(" + "|".join(sorted(_WORDS, key=len, reverse=True)) + r")\b", re.IGNORECASE
)


def _match_case(src: str, out: str) -> str:
    if src.isupper():
        return out.upper()
    if src[:1].isupper():
        return out[:1].upper() + out[1:]
    return out


def normalize_prose(text: str, locale: str = DEFAULT_RESUME_LOCALE) -> str:
    """Return ``text`` in the requested locale's spelling. Only en-US rewrites anything;
    any other locale returns the text untouched."""
    if not text or locale.lower() != "en-us":
        return text

    def ize(m: re.Match[str]) -> str:
        stem, suffix = m.group(1), m.group(2)
        return _match_case(stem, stem.lower() + "iz" + suffix.lower())

    def word(m: re.Match[str]) -> str:
        return _match_case(m.group(1), _WORDS[m.group(1).lower()])

    return _WORD_RE.sub(word, _IZE_RE.sub(ize, text))

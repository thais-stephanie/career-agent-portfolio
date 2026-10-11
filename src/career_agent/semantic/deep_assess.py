"""Three answers from one stored deep reading, decided by code.

The reading (`deep.verify`) says, per requirement, "supported", "partial",
"not_shown" or "unclear" and which evidence ids it leaned on. That single word
over-reads: a declared skill named "Claude Code" was taken as "deep
experience with Claude Code", and a list of four technologies was "shown"
because two of them were. This module re-reads the SAME stored observations
and answers three separate questions (ADR-0035, addendum 1):

1. alignment: is the main activity the work the person asked for;
2. history: how far the evidence SENT shows each mandatory requirement, at
   the strength the posting asks for;
3. to confirm: hiring conditions, languages, and personal dispositions, which
   become questions instead of diluting the technical comparison.

What it never does: read the posting again, ask a model, or treat evidence
that was not sent as evidence of absence. Where the stored reading does not
carry enough to decide safely (a requirement whose list was not quoted, a
composite whose parts the cited evidence does not name), the requirement is
marked for review instead of being reinterpreted.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

ASSESSMENT_VERSION = "assess-1"

#: Recommendation codes, in display order. None of them is a probability of
#: being hired, and none of them is the local %.
RECOMMENDATIONS = (
    "STRONG",  # main activity is the person's work, history well shown
    "PREPARE",  # aligned; history partly shown
    "CHECK_FIRST",  # aligned, but a central requirement is not shown
    "NOT_COMPARED",  # aligned; evidence was not sent, history not compared
    "TO_CONFIRM",  # the reading could not tell what the job is
    "LOW",  # neighbouring job, or secondary with weak history
    "OTHER_WORK",  # main activity is none of the person's work
)

#: Declared, not described: a skill or tool the person listed.
DECLARED_TYPES = frozenset({"SKILL", "TOOL"})

#: Credit per status for the history summary. Shown counts always travel
#: with it; the summary never replaces them.
CREDIT = {"shown": 1.0, "partial": 0.5, "declared": 0.5, "review": 0.5, "not_shown": 0.0}
SHOWN_AT = 0.75
PARTLY_AT = 0.4


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _has(pattern: str, text: str) -> bool:
    return re.search(pattern, text) is not None


_LANGUAGE = (
    r"\b(english|ingles|spanish|espanhol|espanol|portuguese|portugues|french|frances"
    r"|german|alemao|italian|italiano|mandarin|bilingual|bilingue)\b"
)
_CREDENTIAL = r"\b(certif\w*|credly|licen[cs]\w*|licenca)\b"
_DISPOSITION = (
    r"\b(proactive|proativ\w*|self[- ]starter|self[- ]motivated|problem[- ]solver|curious|curiosity"
    r"|passion\w*|team player|detail[- ]oriented|mindset|attitude|comfortable being"
    r"|works? independently|trabalh\w+ de forma autonoma|autonom\w*|writer"
    r"|(written|verbal) communication|communication skills|comunicacao)\b"
)
_YEARS = r"\d+\s*(?:[-\u2013]\s*\d+\s*)?\+?\s*(?:years?|yrs|anos?)\b"
_LEVEL = (
    r"\b(deep|deeply|strong|expert\w*|advanced|extensive|mastery|excellent|exceptional"
    r"|in-depth|profund\w*|avancad\w*|solid|solida|forte|senior)\b"
)
_PRACTICE = (
    r"\b(experience|experiencia|hands-on|proven|track record|demonstrated|built|build\w*"
    r"|design\w*|implement\w*|deploy\w*|operat\w*|production|ownership|own(ed)?|led|managing"
    r"|have run|have taken|have personally|ability to|able to|practical|usage|use of"
    r"|working with|integrat\w*|run)\b"
)
_FAMILIARITY = (
    r"\b(familiar\w*|knowledge of|understanding of|basics?|exposure|comfort working"
    r"|conhecimento\w*|nocoes?)\b"
)
_ALTERNATIVE = (
    r"\b(or|ou|such as|like|e\.g\.|one or more|equivalent|similar|similares|equivalente)\b"
)

#: Words that ask for a strength or describe the asking, never the thing asked.
_STOP_WORDS = """
    a an the of in on for to with and or as at by via from into within across alongside
    is are be being have has you your our we they that this these those who which hand
    experience experienced experiencia hands strong deep deeply proven track record
    demonstrated practical working work worked ability able skills skill knowledge
    understanding proficiency proficient advanced expert level familiarity familiar
    solid excellent exceptional build building built design designing designed
    implement implementing implementation using use usage including include
    essential required preferred plus etc other tools tool similar modern
    de da do das dos em com para por e ou um uma
"""
_STOP = frozenset(_STOP_WORDS.split())


def _tokens(text: str) -> set[str]:
    out = set()
    for token in re.findall(r"[a-z0-9][a-z0-9.+#]*", _fold(text)):
        token = token.rstrip(".")
        if len(token) > 3 and token.endswith("s"):
            token = token[:-1]
        if len(token) >= 2 and token not in _STOP:
            out.add(token)
    return out


def _linked(part: set[str], evidence: set[str]) -> bool:
    for a in part:
        for b in evidence:
            if a == b:
                return True
            short, long = (a, b) if len(a) <= len(b) else (b, a)
            if len(short) >= 4 and long.startswith(short):
                return True
    return False


def _parts(quote: str) -> list[str]:
    """A conjunctive list's items, or [] when the requirement is one thing.

    A list is two commas, or one comma before "and". A requirement offering
    alternatives ("or", "such as", "equivalent") is one thing: any item
    meets it. Parenthesised asides are not items.
    """
    text = re.sub(r"\s+", " ", re.sub(r"\([^)]*\)", " ", quote))
    folded = _fold(text)
    if _has(_ALTERNATIVE, folded):
        return []
    if not (folded.count(",") >= 2 or _has(r",\s*(and|e)\s", folded)):
        return []
    pieces = [p.strip(" .:-\u2013") for p in re.split(r",|;|\band\b|\be\b|&", text)]
    # A piece that is only verbs ("debugging") describes the doing, not an item.
    items = [p for p in pieces if any(not t.endswith("ing") for t in _tokens(p))]
    return items if len(items) >= 2 else []


def _demand(folded: str) -> str:
    if _has(_LEVEL, folded) or _has(_YEARS, folded):
        return "level"
    if _has(_PRACTICE, folded):
        return "practice"
    if _has(_FAMILIARITY, folded):
        return "familiarity"
    return "familiarity"


def _category(folded: str) -> str:
    if _has(_LANGUAGE, folded):
        return "language"
    if _has(_CREDENTIAL, folded):
        return "credential"
    if _has(_DISPOSITION, folded):
        return "disposition"
    return "technical"


def _core_names(review: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tool in review.get("tools", []):
        if tool.get("importance") != "core":
            continue
        for name in re.split(r"\s+(?:on|and|or|with)\s+|[/,()]", tool.get("name", "")):
            name = _fold(name).strip()
            if len(name) >= 2:
                names.add(name)
    return names


def _names_core(folded: str, core: set[str]) -> bool:
    return any(re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", folded) for n in core)


@dataclass(frozen=True)
class Assessment:
    recommendation: str
    alignment: str
    history: str
    requirements: tuple[dict[str, Any], ...]
    counts: dict[str, int]
    central_gaps: tuple[int, ...]
    conditions: tuple[dict[str, Any], ...]
    languages: tuple[str, ...]
    questions: tuple[str, ...]
    to_review: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": ASSESSMENT_VERSION,
            "recommendation": self.recommendation,
            "alignment": self.alignment,
            "history": self.history,
            "requirements": list(self.requirements),
            "counts": self.counts,
            "central_gaps": list(self.central_gaps),
            "conditions": list(self.conditions),
            "languages": list(self.languages),
            "questions": list(self.questions),
            "to_review": self.to_review,
        }


def _requirement(
    req: dict[str, Any], evidence: dict[str, dict[str, Any]] | None, core: set[str]
) -> dict[str, Any]:
    quote = req["quote"]
    folded = _fold(quote)
    category = _category(folded)
    demand = _demand(folded)
    cited = [i for i in req.get("evidence_ids", []) if evidence and i in evidence]
    types = {str(evidence[i].get("type", "")) for i in cited} if evidence else set()
    declared_only = bool(types) and types <= DECLARED_TYPES
    notes: list[str] = []
    parts: list[dict[str, Any]] = []
    support = req["support"]
    if evidence is None or support == "unclear":
        status = "unknown"
    elif support == "not_shown":
        status = "not_shown"
    elif quote.rstrip().endswith(":"):
        # The items live under this heading and were not quoted with it.
        status, notes = "review", ["list_not_quoted"]
    else:
        status = "shown" if support == "supported" else "partial"
        items = _parts(quote)
        if items:
            words = set().union(
                *(
                    _tokens(f"{evidence[i].get('text', '')} {evidence[i].get('tools', '')}")
                    for i in cited
                )
            )
            parts = [{"text": p, "linked": _linked(_tokens(p), words)} for p in items]
            linked = sum(1 for p in parts if p["linked"])
            if not linked:
                status, notes = "review", ["parts_not_named"]
            elif linked < len(parts):
                status, notes = "partial", ["some_parts_unknown"]
        if status in ("shown", "partial"):
            if declared_only and not (status == "shown" and demand == "familiarity"):
                # A listed skill states familiarity; it does not show
                # experience, a level, years, authorship or production use.
                status = "declared"
                notes.append("declared_only")
            elif _has(_LEVEL, folded):
                status = "partial"
                notes.append("level_not_confirmed")
        if _has(_YEARS, folded) and status != "review":
            notes.append("years_not_computed")
            if status == "shown":
                status = "partial"
    return {
        "quote": quote,
        "kind": req["kind"],
        "category": category,
        "demand": demand,
        "reading_said": support,
        "status": status,
        "notes": notes,
        "parts": parts,
        "evidence_ids": cited,
        "central": category in ("technical", "credential")
        and (category == "credential" or _names_core(folded, core)),
    }


def assess(review: dict[str, Any], evidence: dict[str, dict[str, Any]] | None) -> Assessment:
    """`evidence` maps each SENT alias to {"type", "text", "tools"}; None when
    nothing was sent. A cited alias missing from it counts as not cited."""
    relation = review["work_relation"]["value"]
    core = _core_names(review)
    reqs = tuple(_requirement(r, evidence, core) for r in review["requirements"])
    counted = [
        r for r in reqs if r["kind"] == "mandatory" and r["category"] in ("technical", "credential")
    ]
    counts = {s: sum(1 for r in counted if r["status"] == s) for s in (*CREDIT, "unknown")}
    counts["mandatory"] = len(counted)
    gaps = tuple(
        i
        for i, r in enumerate(reqs)
        if r in counted and r["central"] and r["status"] == "not_shown"
    )
    judged = [r for r in counted if r["status"] in CREDIT]
    if evidence is None:
        history = "NOT_SENT"
    elif not judged:
        history = "NONE_STATED"
    else:
        ratio = sum(CREDIT[r["status"]] for r in judged) / len(judged)
        history = (
            "SHOWN"
            if ratio >= SHOWN_AT and not gaps
            else "PARTLY"
            if ratio >= PARTLY_AT
            else "LITTLE"
        )
    alignment = "UNCLEAR" if review["insufficient"] else relation.upper()
    if alignment == "UNCLEAR":
        rec = "TO_CONFIRM"
    elif alignment == "UNRELATED":
        rec = "OTHER_WORK"
    elif alignment == "NEIGHBOUR":
        rec = "LOW"
    elif history == "NOT_SENT":
        rec = "NOT_COMPARED"
    elif gaps:
        rec = "CHECK_FIRST"
    elif alignment == "CENTRAL":
        rec = {"SHOWN": "STRONG", "NONE_STATED": "STRONG", "PARTLY": "PREPARE"}.get(
            history, "CHECK_FIRST"
        )
    else:  # SECONDARY
        rec = "PREPARE" if history in ("SHOWN", "NONE_STATED") else "LOW"
    seen = {c["quote"] for c in review["conditions"]}
    languages = [c["quote"] for c in review["conditions"] if c["topic"] == "language"]
    languages += [
        r["quote"] for r in reqs if r["category"] == "language" and r["quote"] not in seen
    ]
    return Assessment(
        recommendation=rec,
        alignment=alignment,
        history=history,
        requirements=reqs,
        counts=counts,
        central_gaps=gaps,
        conditions=tuple(c for c in review["conditions"] if c["topic"] != "language"),
        languages=tuple(languages),
        questions=tuple(r["quote"] for r in reqs if r["category"] == "disposition"),
        to_review=sum(1 for r in reqs if r["status"] == "review"),
    )


def comparison(recommendation: str, local_band: str | None) -> str:
    """How the reading sits against the local band, as a CODE. Never applied."""
    high_local = local_band in ("GOOD", "STRONG")
    if recommendation in ("STRONG", "PREPARE") and not high_local:
        return "raises"
    if recommendation in ("LOW", "OTHER_WORK") and high_local:
        return "lowers"
    if recommendation == "TO_CONFIRM":
        return "open"
    return "agrees"

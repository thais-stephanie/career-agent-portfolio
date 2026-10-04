"""Source lines in, an `ImportProposal` out: what an uploaded resume appears to say.

Deterministic and conservative. Every value is a substring of the source
text it names (ignoring case), or it is not proposed; a value the reader is
unsure of says so (MEDIUM or LOW, with a `note` naming the doubt) instead of
being chosen quietly. Nothing is invented: no month for a year-only date, no
end for an open one, no name when no line looks like one ("You" is never a
name), no location when a line could be a company's.

The stages, one pass each:

* sections: a line naming a section (the EN/PT/ES vocabulary shared with the
  Career Evidence CV reader, `cv.propose`) or shaped like a heading (a heading
  style, or short, mostly upper case and bold or larger than the body); an
  unknown heading starts "other content", never a lost one;
* the top of the document (before the first section): name, contact,
  location, links, headline;
* entries (experience, education, projects): a header block (lines with the
  role, the organisation, the dates) then its lines (bullets: a list style or
  glyph, or an indent past the section's margin);
* lines a PDF wrapped are joined back: a line continues the one before when
  that one is full width, ends mid-sentence and is not followed by a bullet;
* skills: "Group: a, b, c" is a group; a bare list is one group named after
  the section's heading. Items split only on list punctuation, never on spaces.

Material with no typed home is kept as other content under its own heading.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from career_agent.clock import new_id
from career_agent.cv.propose import _heading
from career_agent.cv.structure import _DASHES, _SPAN, fold, has_role_word
from career_agent.intake.build import _MONTHS
from career_agent.resume_doc.imports import (
    Confidence,
    Found,
    FoundEntry,
    FoundGroup,
    FoundIdentity,
    FoundLine,
    FoundLink,
    ImportProposal,
    ImportReport,
    Source,
)
from career_agent.resume_doc.intake import GLYPH, Extracted, SourceLine
from career_agent.resume_doc.models import PLACEHOLDER_NAMES, LinkKind

#: The typed home of each section the shared vocabulary names. Any other
#: (languages, awards, activities...) is kept as other content.
HOMES = {
    "summary": "summary",
    "experience": "experience",
    "volunteering": "experience",
    "internships": "experience",
    "freelance": "experience",
    "skills": "skills",
    "tools": "skills",
    "education": "education",
    "certifications": "certifications",
    "projects": "projects",
}
EMAIL = re.compile(r"[\w.+'-]+@[\w-]+(?:\.[\w-]+)+")
URL = re.compile(
    r"(?:https?://|www\.)\S+|(?<![@\w.])[a-z0-9-]+(?:\.[a-z0-9-]+)+/[^\s|,;]*", re.IGNORECASE
)
PHONE = re.compile(r"(?<![\w/])\+?\(?\d[\d\s().-]{6,20}\d(?![\w/])")
#: Separators between the parts of a header line. Spaced dashes only: a
#: hyphen inside a word ("Co-founder") is part of it.
SEP = re.compile(rf"\s+[|•·]\s+|\s*\|\s*|\s+[{_DASHES}]\s+|\s+(?:at|@|na|no)\s+(?=[A-Z0-9À-Þ])")
#: "Resume of", "CV de": what is left is the name.
_OF = re.compile(
    r"^(?:r[eé]sum[eé]|cv|curr[ií]cul(?:um|o)(?: vitae)?)\s+(?:of|de|do|da)\s+",
    re.IGNORECASE,
)
#: What a document calls itself at the top; never a section, never a name.
DOC_TITLES = frozenset(
    {"curriculum vitae", "curriculum", "resume", "cv", "curriculo", "hoja de vida"}
)
REMOTE = frozenset({"remote", "remoto", "hybrid", "hibrido", "on-site", "onsite", "presencial"})
DEGREE = re.compile(
    r"\b(b\.?sc?|b\.?a|m\.?sc?|m\.?a|mba|ph\.?d|bachelor|master|doctor|degree|diploma|associate"
    r"|bacharel(ado)?|licenciatur[ao]|tecnolog[oa]|mestrado|doutorado|graduacao|pos|especializacao"
    r"|grado|maestria|ingenieria|engenharia)\b"
)
SCHOOL = re.compile(r"univers|college|school|institut|faculdade|escola|academ|politecn")
_STOP = {
    "en": {"the", "and", "with", "for", "of", "to", "in"},
    "pt": {"de", "e", "com", "para", "em", "do", "da", "na", "no"},
    "es": {"de", "y", "con", "para", "en", "del", "la", "el", "los"},
}


# ----------------------------------------------------------------- values


LIMIT = 5000  # characters a text field holds


def _src(lines: list[SourceLine], text: str | None = None) -> Source:
    where = lines[0].where if len(lines) == 1 else f"{lines[0].where} to {lines[-1].where}"
    return Source(where=where[:80], text=(text or " ".join(x.text for x in lines))[:LIMIT])


def _chunks(text: str) -> list[str]:
    """Text a field cannot hold, in pieces it can, cut between words."""
    out = []
    while len(text) > LIMIT:
        cut = text.rfind(" ", 0, LIMIT) if " " in text[:LIMIT] else LIMIT
        out.append(text[:cut].strip())
        text = text[cut:].strip()
    return [*out, text] if text else out


def _found(
    value: str,
    lines: list[SourceLine],
    confidence: Confidence = "HIGH",
    note: str | None = None,
    alternatives: tuple[str, ...] = (),
) -> Found | None:
    """A value, only when the source text holds it."""
    value = " ".join(value.split()).strip(" ,;|")
    source = _src(lines, value if len(value) > LIMIT - 200 else None)
    if not value or value.casefold() not in source.text.casefold():
        return None
    return Found(
        value=value,
        confidence=confidence,
        source=source,
        note=note,
        alternatives=[a for a in alternatives if a and a != value],
    )


def _line(lines: list[SourceLine], text: str | None = None) -> FoundLine | None:
    found = _found(text if text is not None else " ".join(x.text for x in lines), lines)
    return FoundLine(id=new_id(), text=found) if found else None


def _lines(lines: list[SourceLine], text: str) -> list[FoundLine]:
    """One line, or several when it is longer than a line holds."""
    return [found for piece in _chunks(" ".join(text.split())) if (found := _line(lines, piece))]


def _bare(text: str) -> str:
    return GLYPH.sub("", text, count=1).strip()


def _upper(text: str) -> bool:
    letters = [c for c in text if c.isalpha()]
    return len(letters) >= 3 and sum(c.isupper() for c in letters) >= 0.8 * len(letters)


# ------------------------------------------------------------------ dates


def _date(token: str) -> str | None:
    """'YYYY' or 'YYYY-MM' for one written date; never a month not written."""
    t = fold(token).strip().rstrip(".")
    if m := re.fullmatch(r"(\d{4})-(\d{2})", t):
        # "2010-11" is the academic year 2010 to 2011, not November.
        return m[1] if int(m[2]) == (int(m[1][2:]) + 1) % 100 else f"{m[1]}-{m[2]}"
    if m := re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", t):
        return f"{m[2]}-{int(m[1]):02d}"
    if m := re.fullmatch(r"([a-z]+)\.?\s*(?:de\s+)?(\d{4})", t):
        month = _MONTHS.get(m[1])
        return f"{m[2]}-{month:02d}" if month else m[2]
    if re.fullmatch(r"\d{4}", t):
        return t
    return None


@dataclass
class _Span:
    start: str | None
    end: str | None
    current: bool
    text: str


def _span(text: str) -> _Span | None:
    match = _SPAN.search(fold(text))
    if match is None:
        return None
    start = _date(text[match.start("start") : match.end("start")])
    end_text = text[match.start("end") : match.end("end")] if match.group("end") else ""
    current = bool(end_text) and not re.search(r"\d", end_text)
    written = text[match.start() : match.end()]
    return _Span(start, None if current else _date(end_text), current, written)


# --------------------------------------------------------------- sections


@dataclass
class _Section:
    key: str  # "top", a HOMES value, or "other"
    heading: SourceLine | None = None
    confidence: Confidence = "HIGH"
    lines: list[SourceLine] = field(default_factory=list)


def _heading_of(line: SourceLine, body: float) -> tuple[str, Confidence] | None:
    text = line.text.strip().rstrip(":").strip()
    if not text or len(text) > 50 or len(text.split()) > 5 or line.style in ("title", "list"):
        return None
    if fold(text).strip(" .") in DOC_TITLES:
        return None
    if _SPAN.search(fold(text)) or EMAIL.search(text) or URL.search(text):
        return None
    if re.search(rf"[,|•·]|\s[{_DASHES}]\s", text):  # an entry header's separators
        return None
    larger = bool(line.size and body and line.size > body * 1.08)
    shaped = line.style == "heading" or (_upper(text) and (line.bold or larger))
    known = _heading(text)
    if known:
        return HOMES.get(known, "other"), "HIGH" if shaped or _upper(text) else "MEDIUM"
    if shaped and not has_role_word(text):
        return "other", "LOW"
    return None


def _sections(lines: list[SourceLine], body: float) -> list[_Section]:
    out = [_Section("top")]
    for line in lines:
        found = _heading_of(line, body)
        # A heading nobody uses starts a section only once a known one has:
        # above that, a bold upper-case line is as likely to be the name.
        if found and (found[1] != "LOW" or len(out) > 1):
            out.append(_Section(found[0], line, found[1]))
        else:
            out[-1].lines.append(line)
    return out


# -------------------------------------------------------------- wrapping


def _items(lines: list[SourceLine]) -> list[list[SourceLine]]:
    """Lines a PDF wrapped, joined back into the item they belong to."""
    widest: dict[int, int] = {}
    for line in lines:
        key = round(line.indent)
        widest[key] = max(widest.get(key, 0), len(line.text))
    items: list[list[SourceLine]] = []
    for line in lines:
        if items and _continues(items[-1][-1], line, widest):
            items[-1].append(line)
        else:
            items.append([line])
    return items


def _continues(prev: SourceLine, cur: SourceLine, widest: dict[int, int]) -> bool:
    if not prev.where.startswith("page") or cur.page != prev.page:
        return False  # a DOCX paragraph is never wrapped across two
    glyph = GLYPH.match(cur.text)
    if glyph or (cur.style == "list" and cur.indent <= prev.indent):
        return False
    hanging = prev.style == "list" and cur.indent > prev.indent
    if abs(cur.indent - prev.indent) > 2 and not hanging:
        return False
    if len(prev.text) < 0.8 * widest.get(round(prev.indent), len(prev.text)):
        return False  # a line ending short ends its item
    if prev.text.endswith((".", "!", "?", ";", ":")) and cur.text[:1].isupper():
        return False
    return not (prev.gap and cur.gap > max(prev.gap, 1.0) * 1.12)


# ---------------------------------------------------------------- top/id


def _parts(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"\s+[|•·]\s+|\s*\|\s*|\s{3,}", text) if p.strip()]


def _identity(top: list[SourceLine], body: float) -> tuple[FoundIdentity, list[SourceLine]]:
    """Name, contact, location and links from the lines above the first
    section. Returns them and the top lines nothing claimed."""
    from career_agent.match.places import resolve_place

    ident = FoundIdentity()
    used: set[int] = set()
    links: dict[str, FoundLink] = {}
    for i, line in enumerate(top):
        parts = _parts(line.text)
        contact = False
        for target in line.links:
            _add_link(links, target, Source(where=f"{line.where} (link)"[:80], text=target), "HIGH")
            contact = True
        for part in parts:
            if ident.email is None and (m := EMAIL.search(part)):
                ident.email, contact = _found(m[0], [line]), True
                continue
            if m := URL.search(part):
                if not EMAIL.search(part):
                    found = _found(m[0], [line], "HIGH" if "://" in m[0] else "MEDIUM")
                    if found and not any(_same(found.value, k) for k in links):
                        _add_link(links, found.value, found.source, found.confidence)
                    contact = True
                continue
            digits = re.sub(r"\D", "", part)
            phone = PHONE.search(part) if ident.phone is None else None
            if phone and 8 <= len(digits) <= 15 and not _span(part):
                ident.phone, contact = _found(phone[0], [line]), True
                continue
            if ident.location is None and len(parts) > 1 and not re.search(r"\d", part):
                place = resolve_place(part)
                if len(place.countries) == 1 or fold(part) in REMOTE:
                    # Beside an email or phone it is where the person is;
                    # beside anything else it could be a company's office.
                    personal = bool(EMAIL.search(line.text) or PHONE.search(line.text))
                    ident.location = _found(
                        part,
                        [line],
                        "MEDIUM" if personal else "LOW",
                        note=None if personal else "LOCATION_OR_COMPANY",
                    )
                    contact = True
        if contact:
            used.add(i)
    ident.links = list(links.values())
    # The name: a prominent line near the top that is nothing else.
    candidates = []
    for i, line in enumerate(top[:8]):
        text = _OF.sub("", line.text.strip())  # "Resume of Jane Doe": the name is Jane Doe
        words = text.split()
        if i in used or not 2 <= len(words) <= 5 or re.search(r"[\d@/:]", text):
            continue
        if fold(text) in PLACEHOLDER_NAMES | DOC_TITLES or has_role_word(text) or _heading(text):
            continue
        if sum(c.isalpha() for c in text) < 0.75 * len(text.replace(" ", "")):
            continue
        big = bool(line.size and body and line.size >= body * 1.3)
        score = 3 * (line.style == "title") + 2 * big + line.bold + (i == 0)
        candidates.append((score, i, line, text))
    candidates.sort(key=lambda c: (-c[0], c[1]))
    if candidates:
        score, i, line, text = candidates[0]
        rivals = [c for c in candidates[1:] if c[0] >= score - 1]
        prominent = line.style == "title" or bool(line.size and body and line.size >= body * 1.3)
        # HIGH only when nothing else near the top could be a name: a company
        # set in a title style would otherwise win on style alone.
        sole = len(candidates) == 1 and text == line.text.strip()
        confidence: Confidence = "LOW" if rivals else "HIGH" if prominent and sole else "MEDIUM"
        ident.name = _found(
            text,
            [line],
            confidence,
            note="TWO_NAMES" if rivals else None,
            alternatives=tuple(c[3] for c in candidates[1:4]),
        )
        used.add(i)
    rest = [line for i, line in enumerate(top) if i not in used and not _link_tail(top, i)]
    return ident, rest


def _same(url: str, other: str) -> bool:
    def bare(u: str) -> str:
        return re.sub(r"^https?://(www\.)?", "", u.casefold()).rstrip("/")

    return bare(url) in bare(other) or bare(other) in bare(url)


def _link_tail(top: list[SourceLine], i: int) -> bool:
    """A line holding only the wrapped end of a link above it, which the
    link's own target spells whole."""
    tail = top[i].text.strip()
    if i == 0 or " " in tail or not top[i - 1].text.rstrip().endswith(("-", "/")):
        return False
    return any(t.rstrip("/").casefold().endswith(tail.casefold()) for t in top[i - 1].links)


def _add_link(links: dict[str, FoundLink], url: str, source: Source | None, conf: str) -> None:
    for key in list(links):
        if _same(url, key):
            if "://" in url and "://" not in key:  # the full target outranks printed text
                del links[key]
            else:
                return
    host = fold(re.sub(r"^https?://(www\.)?", "", url).split("/")[0])
    kind = (
        LinkKind.LINKEDIN
        if re.search(r"(^|\.)linkedin\.", host)
        else LinkKind.GITHUB
        if re.search(r"(^|\.)github\.", host)
        else LinkKind.PORTFOLIO
    )
    confidence: Confidence = "HIGH" if conf == "HIGH" else "MEDIUM"
    links[url] = FoundLink(
        id=new_id(), kind=kind, url=Found(value=url, confidence=confidence, source=source)
    )


# ---------------------------------------------------------------- entries


def _bullet(line: SourceLine, margin: float) -> bool:
    if line.style == "list" or GLYPH.match(line.text):
        return True
    return line.where.startswith("page") and line.indent > margin + 3


def _entries(lines: list[SourceLine], kind: str) -> list[FoundEntry]:
    margin = min((line.indent for line in lines), default=0)
    blocks: list[tuple[list[SourceLine], list[SourceLine]]] = []
    for line in lines:
        head, body = blocks[-1] if blocks else ([], [])
        if _bullet(line, margin):
            if not blocks:
                blocks.append(([], []))
            blocks[-1][1].append(line)
            continue
        prose = len(line.text.split()) >= 12 and not _span(line.text)
        # A header that already names something AND has its dates is whole;
        # a date standing alone first (a date-first layout) is not.
        dated = any(_span(h.text) for h in head) and any(_named(h.text) for h in head)
        if not blocks or (body or dated) and not prose and not _place_only(line.text, dated):
            # After bullets, or after a header that already has its dates,
            # a header line starts the next entry (older roles often have
            # no bullets at all).
            blocks.append(([line], []))
        elif prose and head:
            body.append(line)
        else:
            head.append(line)
    return [_entry(head, body, kind) for head, body in blocks]


def _entry(head: list[SourceLine], body: list[SourceLine], kind: str) -> FoundEntry:
    from career_agent.match.places import resolve_place

    entry = FoundEntry(id=new_id())
    parts: list[tuple[str, SourceLine]] = []
    for line in head:
        span = _span(line.text)
        text = line.text
        if span and entry.start is None and span.start:
            entry.start = Found(value=span.start, source=_src([line]))
            if span.end:
                entry.end = Found(value=span.end, source=_src([line]))
            entry.current = span.current
            text = text.replace(span.text, " | ")
        if kind == "projects" and URL.search(text) and len(text.split()) == 1:
            entry.url = _found(text, [line], "MEDIUM")
            continue
        for part in SEP.split(text):
            part = part.strip(" ,;|()")
            if part and re.search(r"\w", part):
                parts.append((part, line))
    # A location: a part naming a place (or remote work), never the only part
    # left for the organisation.
    location = [
        (p, ln)
        for p, ln in parts
        if not has_role_word(p)
        and (fold(p) in REMOTE or len(resolve_place(p).countries) == 1)
        and len(p.split()) <= 5
    ]
    named = [(p, ln) for p, ln in parts if (p, ln) not in location]
    if kind in ("experience", "education") and location:
        if len(named) >= 2 or (named and kind == "education"):
            entry.location = _found(location[0][0], [location[0][1]], "MEDIUM")
        else:
            named += location  # could be the company: the person decides
    if kind == "experience":
        title: tuple[str, SourceLine] | None
        org: tuple[str, SourceLine] | None
        roles = [x for x in named if has_role_word(x[0])]
        others = [x for x in named if not has_role_word(x[0])]
        if len(roles) == 1 and others:
            title, org, conf, note = roles[0], others[0], "MEDIUM", None
        elif len(named) >= 2:
            title, org, conf, note = named[0], named[1], "LOW", "ROLE_OR_COMPANY"
        else:
            title = roles[0] if roles else None
            org = None if roles else (named[0] if named else None)
            conf, note = "LOW", "ROLE_OR_COMPANY"
        if location and org and org in location:
            conf, note = "LOW", "LOCATION_OR_COMPANY"
        entry.title = _pick(title, conf, note, org)
        entry.org = _pick(org, conf, note, title)
    elif kind == "education":
        degree = next((x for x in named if DEGREE.search(fold(x[0]))), None)
        school = next((x for x in named if x != degree and SCHOOL.search(fold(x[0]))), None)
        school = school or next((x for x in named if x != degree), None)
        conf = "MEDIUM" if degree and school else "LOW"
        entry.title = _pick(degree, conf, None, None)
        entry.org = _pick(school, conf, None if degree else "DEGREE_OR_SCHOOL", None)
    else:  # projects: the name, then a role
        entry.title = _pick(named[0] if named else None, "MEDIUM", None, None)
        entry.org = _pick(named[1] if len(named) > 1 else None, "MEDIUM", None, None)
    taken = {f.value for f in (entry.title, entry.org, entry.location) if f}
    kept = [
        FoundLine(id=new_id(), text=found)
        for p, ln in [*named, *location]
        if p not in taken and (found := _found(p, [ln], "LOW", note="HEADER_PART"))
    ]
    entry.lines = kept + _bullets(body)
    return entry


def _named(text: str) -> bool:
    """Does a header line name anything besides its dates?"""
    span = _span(text)
    return bool(re.search(r"[^\W\d_]", text.replace(span.text, "") if span else text))


def _place_only(text: str, dated: bool) -> bool:
    """A line naming only where a dated entry was: it belongs to that entry."""
    from career_agent.match.places import resolve_place

    if not dated or _span(text) or len(text.split()) > 5:
        return False
    return fold(text.strip()) in REMOTE or len(resolve_place(text).countries) == 1


def _bullets(lines: list[SourceLine]) -> list[FoundLine]:
    """One line per item, its bullet glyph dropped, its wrapped lines joined."""
    return [found for item in _items(lines) for found in _lines(item, _bare(_join(item)))]


def _join(item: list[SourceLine]) -> str:
    return " ".join(x.text for x in item)


def _pick(
    chosen: tuple[str, SourceLine] | None,
    conf: str,
    note: str | None,
    other: tuple[str, SourceLine] | None,
) -> Found | None:
    if chosen is None:
        return None
    confidence: Confidence = "LOW" if conf == "LOW" else "MEDIUM" if conf == "MEDIUM" else "HIGH"
    return _found(
        chosen[0], [chosen[1]], confidence, note, alternatives=(other[0],) if other and note else ()
    )


def _certifications(lines: list[SourceLine]) -> list[FoundEntry]:
    out = []
    for item in _items(lines):
        text = _bare(" ".join(x.text for x in item))
        span = _span(text)
        rest = text.replace(span.text, ",") if span else text
        pieces = re.split(rf",\s+|\s*\|\s*|\s+[{_DASHES}]\s+", rest)
        parts = [p.strip(" ,;|()") for p in pieces]
        parts = [p for p in parts if p and re.search(r"\w", p)]
        entry = FoundEntry(id=new_id())
        if len(parts) > 2:  # more parts than fields: the whole text, to split by hand
            whole = text[: text.find(span.text)] if span and text.find(span.text) > 0 else text
            entry.title = _found(whole, item, "LOW", note="CHECK_SPLIT")
        else:
            entry.title = _found(parts[0], item, "MEDIUM") if parts else None
            entry.org = _found(parts[1], item, "MEDIUM") if len(parts) > 1 else None
        if span and span.start:
            entry.start = Found(value=span.start, source=_src(item))
        out.append(entry)
    return out


def _skills(section: _Section) -> list[FoundGroup]:
    groups: list[FoundGroup] = []
    loose: list[FoundLine] = []
    for item in _items(section.lines):
        text = _bare(" ".join(x.text for x in item))
        labelled = re.match(r"^([^:,;]{2,40}):\s*(.+)$", text)
        values = labelled[2] if labelled else text
        found = [
            line for part in _list_items(values) if (line := _line(item, part.strip().rstrip(".")))
        ]
        name = _found(labelled[1], item) if labelled else None
        if name:
            groups.append(FoundGroup(id=new_id(), name=name, lines=found))
        else:
            loose += found
    if loose and section.heading is not None:
        heading = section.heading.text.strip().rstrip(":")
        value = heading.capitalize() if _upper(heading) else heading
        name = _found(value, [section.heading], "MEDIUM")
        if name:
            groups.insert(0, FoundGroup(id=new_id(), name=name, lines=loose))
    return groups


def _list_items(text: str) -> list[str]:
    """Split a list on , ; | and bullets, never inside parentheses:
    "Excel (advanced, VBA)" is one item."""
    out, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch in "([") - (ch in ")]")
        if depth <= 0 and ch in ",;|\u2022\u00b7":
            out.append(cur)
            cur, depth = "", 0
        else:
            cur += ch
    return [p.strip() for p in [*out, cur] if p.strip()]


def _other(section: _Section) -> FoundGroup:
    if section.heading is not None:
        heading = section.heading.text.strip().rstrip(":")
        # A heading this reader knows reads better capitalised; one it does
        # not is kept exactly as written.
        known = section.confidence != "LOW"
        value = heading.capitalize() if _upper(heading) and known else heading
        name = _found(value, [section.heading], section.confidence, note=(
            "UNKNOWN_HEADING" if section.confidence == "LOW" else None
        ))  # fmt: skip
    else:
        name = None
    lines = _bullets(section.lines)
    return FoundGroup(
        id=new_id(),
        name=name or Found(value="", confidence="LOW", note="NO_HEADING"),
        lines=lines,
    )


# ------------------------------------------------------------------ whole


def _language(lines: list[SourceLine]) -> str:
    return language_of(" ".join(line.text for line in lines))


def language_of(text: str) -> str:
    """en, pt or es, by a vote of each language's stop words; en when none vote."""
    words = Counter(re.findall(r"[a-z]+", fold(text)))
    scores = {lang: sum(words[w] for w in stop) for lang, stop in _STOP.items()}
    best = max(scores, key=lambda lang: scores[lang])
    return best if scores[best] else "en"


def parse(extracted: Extracted, filename: str) -> ImportProposal:
    """What the document appears to say, for review. Stores nothing."""
    lines = extracted.lines
    sizes = sorted(line.size for line in lines if line.size)
    body = sizes[len(sizes) // 2] if sizes else 0.0
    sections = _sections(lines, body)
    identity, rest = _identity(sections[0].lines, body)
    proposal = ImportProposal(
        document_id=new_id(),
        import_id=new_id(),
        title=re.sub(r"\.(pdf|docx)$", "", filename, flags=re.IGNORECASE)[:300] or "Resume",
        language=_language(lines),
        identity=identity,
        report=ImportReport(
            filename=filename[:300],
            format=extracted.format,  # type: ignore[arg-type]
            pages=extracted.pages,
            characters=sum(len(line.text) for line in lines),
            warnings=list(extracted.warnings),
        ),
        source=[Source(where=line.where[:80], text=line.text[:5000]) for line in lines],
    )
    # Above the first section: a short line is the headline; anything else is
    # the summary when the document has no summary section, else other content.
    has_summary = any(s.key == "summary" for s in sections)
    leftover: list[SourceLine] = []
    rivals = set(identity.name.alternatives) if identity.name else set()
    for item in _items(rest):
        text = " ".join(x.text for x in item)
        if proposal.headline is None and len(text.split()) <= 15 and not text.endswith("."):
            proposal.headline = _line(item)
            if proposal.headline:
                # The other line that could be the name is no confident headline.
                rival = text in rivals
                proposal.headline.text.confidence = "LOW" if rival else "MEDIUM"
                proposal.headline.text.note = "TWO_NAMES" if rival else None
        else:
            leftover += item
    if leftover and not has_summary:
        pieces = _lines(leftover, _join(leftover))
        if pieces:
            proposal.summary, rest_of_it = pieces[0], pieces[1:]
            proposal.summary.text.confidence = "MEDIUM"
            if rest_of_it:
                more = _other(_Section("other"))
                more.lines = rest_of_it
                proposal.other.append(more)
    elif leftover:
        proposal.other.append(_other(_Section("other", lines=leftover)))
    for section in sections[1:]:
        if section.key == "summary" and proposal.summary is None and section.lines:
            pieces = _lines(section.lines, _join(section.lines))
            proposal.summary = pieces[0] if pieces else None
            if len(pieces) > 1:
                more = _other(section)
                more.lines = pieces[1:]
                proposal.other.append(more)
        elif section.key in ("experience", "education", "projects"):
            getattr(proposal, section.key).extend(_entries(section.lines, section.key))
        elif section.key == "certifications":
            proposal.certifications.extend(_certifications(section.lines))
        elif section.key == "skills":
            proposal.skills.extend(_skills(section))
        elif section.lines or section.key != "summary":
            proposal.other.append(_other(section))
    report = proposal.report
    report.sections = [s.key for s in sections[1:]]
    report.unmapped = len(proposal.other)
    if not report.sections:
        report.warnings.append("NO_SECTIONS")
    counts = Counter(_confidences(proposal.model_dump(exclude={"report", "source"})))
    report.confidence = {c: counts.get(c, 0) for c in ("HIGH", "MEDIUM", "LOW")}  # type: ignore[misc]
    return proposal


def _confidences(node: object) -> list[str]:
    if isinstance(node, dict):
        own = [node["confidence"]] if "confidence" in node and "value" in node else []
        return own + [c for v in node.values() for c in _confidences(v)]
    if isinstance(node, list):
        return [c for v in node for c in _confidences(v)]
    return []

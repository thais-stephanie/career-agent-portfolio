"""Which company, which role, which dates: a CV's structure, read before its claims.

A CV is not a list of sentences. It is a list of JOBS, each with a company, a
title and a span of time, and the sentences belong to the job they sit under.
The first reader ignored that and proposed every line on its own, so a
heading became a claim, a date line became a claim, and a bullet arrived in
review with no idea whose job it described. A person facing 142 of those
could not tell which role any of them belonged to.

This module reads the structure and nothing else. It produces ENTRIES --
company, role, dates, and the source lines that stated them -- and it never
proposes a claim; `propose.read_cv` does that, attaching each claim to the
entry above it.

**It never invents structure.** Every field is either read from a line of the
document or left empty. When a heading could be a company or a role and
nothing in the document settles it, the entry keeps the heading as its label
and says the structure is unresolved; the review asks the person, who knows.

**Headings are strong signals.** `### Teem` followed by `#### RevOps` is a
company with a role inside it, because that is what the nesting says. A
heading with a title word in it ("Implementation Lead") is a role. A line with
a date range and a name on it is a job header. Nothing cleverer than that.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from career_agent.cv.markdown import Line

#: Words that name a JOB rather than an organisation. A heading carrying one
#: is read as a role. English and Portuguese, folded to ASCII.
ROLE_WORDS = frozenset(
    {
        # English
        "manager",
        "director",
        "head",
        "lead",
        "leader",
        "engineer",
        "developer",
        "consultant",
        "analyst",
        "specialist",
        "coordinator",
        "associate",
        "assistant",
        "intern",
        "internship",
        "trainee",
        "apprentice",
        "officer",
        "executive",
        "president",
        "vp",
        "founder",
        "cofounder",
        "co-founder",
        "owner",
        "partner",
        "architect",
        "designer",
        "scientist",
        "administrator",
        "representative",
        "advisor",
        "adviser",
        "strategist",
        "supervisor",
        "teacher",
        "researcher",
        "technician",
        "accountant",
        "clerk",
        "volunteer",
        "tutor",
        "mentor",
        "writer",
        "editor",
        "cto",
        "ceo",
        "coo",
        "cfo",
        "sdr",
        "bdr",
        "recruiter",
        "controller",
        "programmer",
        "operator",
        "agent",
        "producer",
        "chief",
        "principal",
        "staff",
        "freelancer",
        "contractor",
        "instructor",
        "professor",
        "nurse",
        "attendant",
        "auditor",
        # Portuguese
        "gerente",
        "diretor",
        "diretora",
        "coordenador",
        "coordenadora",
        "analista",
        "consultor",
        "consultora",
        "especialista",
        "engenheiro",
        "engenheira",
        "desenvolvedor",
        "desenvolvedora",
        "estagiario",
        "estagiaria",
        "assistente",
        "supervisora",
        "lider",
        "chefe",
        "socio",
        "socia",
        "fundador",
        "fundadora",
        "executivo",
        "executiva",
        "arquiteto",
        "arquiteta",
        "cientista",
        "administrador",
        "administradora",
        "representante",
        "auxiliar",
        "tecnico",
        "tecnica",
        "pesquisador",
        "pesquisadora",
        "voluntario",
        "voluntaria",
        "presidente",
        "vendedor",
        "vendedora",
        "programador",
        "programadora",
        "operador",
        "operadora",
        "atendente",
        "contador",
        "contadora",
        "redator",
        "redatora",
        "instrutor",
        "instrutora",
    }
)

#: Month names a CV writes, both languages, folded.
_MONTH_NAMES = (
    "january|february|march|april|may|june|july|august|september|october|november|december"
    "|janeiro|fevereiro|marco|abril|maio|junho|julho|agosto|setembro|outubro|novembro|dezembro"
    "|jan|feb|fev|mar|apr|abr|jun|jul|aug|ago|sep|sept|set|oct|out|nov|dec|dez|mai"
)
#: One date: an optional month, then a four-digit year. `05/2021` too.
_DATE = rf"(?:(?:{_MONTH_NAMES})\.?\s*(?:de\s+)?|(?:0?[1-9]|1[0-2])\s*/\s*)?(?:19|20)\d{{2}}"
#: Words meaning a role has not ended.
_NOW = r"present|current|now|today|atual|atualmente|presente|hoje|o momento|ongoing"
#: A span: a date, a separator, and a date or a "still there" word. Or a date
#: alone. The dash class is DATA -- what other people's documents write --
#: and is built from code points so this file contains none of it.
_DASHES = "\\-" + chr(0x2013) + chr(0x2014) + chr(0x2012)
_SPAN = re.compile(
    rf"(?P<start>{_DATE})(?:\s*(?:[{_DASHES}]|\bto\b|\bate\b|\ba\b)\s*(?P<end>{_DATE}|{_NOW}))?",
    re.IGNORECASE,
)
#: Separators between a company and a role on one line. Spaced dashes only: a
#: hyphen inside a word ("Co-founder", "S/4HANA") is part of the word.
_PART_SPLIT = re.compile(rf"\s+[{_DASHES}|·•]\s+|\s*[|·•]\s*|,\s+|\s+[{_DASHES}]\s*$")
#: "Role at Company", "Role @ Company", "Analista na Acme". The organisation
#: after the word must start with a capital: "Consultant at scale" and
#: "Especialista em automacao" are titles, not a title and a company.
_AT = re.compile(r"\s+(?:at|@|na|no)\s+(?=[A-Z0-9À-Þ])")
#: A heading that NAMES a section in prose-form: "Additional experience",
#: "Experiencia relevante". Only ever applied to marked headings.
SECTION_WORDS = {
    "experience": "experience",
    "experiences": "experience",
    "experiencia": "experience",
    "experiencias": "experience",
    "employment": "experience",
    "career": "experience",
    "carreira": "experience",
}


def fold(text: str) -> str:
    """Lower case, accents removed, ONE character out per character in.

    For comparison only; never shown. Length-preserving on purpose: a match
    found in the folded copy is sliced out of the original at the same
    offsets, and a fold that turned one character into two would slice the
    wrong words.
    """
    out = []
    for ch in text:
        base = [c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c)]
        low = (base[0] if base else ch).lower()
        out.append(low if len(low) == 1 else ch)
    return "".join(out)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]*", fold(text))


def has_role_word(text: str) -> bool:
    return any(word in ROLE_WORDS for word in _words(text))


@dataclass(frozen=True, slots=True)
class Span:
    """A period as WRITTEN, and as much of it as can be read."""

    text: str
    start: str | None
    end: str | None
    current: bool
    #: A year to sort by when no month was stated. "2015 - 2017" is a real
    #: span with no month in it, and chronology must still place it.
    start_year: int | None
    end_year: int | None


def find_span(text: str) -> tuple[Span, int, int] | None:
    """The first date span in `text`, with where it sits, or None."""
    from career_agent.intake.build import read_period

    for match in _SPAN.finditer(fold(text)):
        start_text = match.group("start")
        # "a" and "at" are words too; a lone year inside a sentence ("40 stores
        # in 2019") is still a date, which is why a header also needs a name.
        original = text[match.start() : match.end()].strip()
        try:
            period = read_period(original)
        except ValueError:
            # "Dec 2021 - Jan 2021": a typo in the document. The words and
            # the years are kept; no month is read out of a span that
            # contradicts itself, and the job says its dates need a look.
            period = None
        years = [int(y) for y in re.findall(r"(?:19|20)\d{2}", original)]
        end_word = (match.group("end") or "").strip()
        current = bool(end_word) and not re.search(r"\d", end_word)
        del start_text
        start = period.start.normalized if period and period.start else None
        end = period.end.normalized if period and period.end else None
        if start and end and end < start:
            end = None
        return (
            Span(
                text=original,
                start=start,
                end=None if current else end,
                current=current,
                start_year=years[0] if years else None,
                end_year=(years[1] if len(years) > 1 else None) if not current else None,
            ),
            match.start(),
            match.end(),
        )
    return None


def _parts(text: str) -> list[str]:
    # A parenthesis on a header line is a qualifier -- "(contract)", "(remote)"
    # -- and never the company or the title. It stays in the source lines.
    cleaned = re.sub(r"\s*\([^()]*\)", "", text)
    parts = [p.strip(" ,;:|()" + _DASHES) for p in _PART_SPLIT.split(cleaned)]
    return [p for p in parts if p and re.search(r"\w", p)]


@dataclass
class Header:
    """What one header line says: any of company, role, dates, location."""

    company: str | None = None
    role: str | None = None
    span: Span | None = None
    location: str | None = None
    #: A name the line gives that could be either a company or a role.
    name: str | None = None
    ambiguous: bool = False


#: Longer than any real job header. A line past this is prose, and parsing
#: it as a header is only a way to spend time on untrusted input.
MAX_HEADER = 300


def read_header(text: str) -> Header:
    """Company, role and dates from one line, or as much as it states."""
    header = Header()
    if len(text) > MAX_HEADER:
        return header
    rest = text
    found = find_span(text)
    if found is not None:
        span, start, end = found
        header.span = span
        rest = (text[:start] + " " + text[end:]).strip()
        rest = re.sub(r"\(\s*\)", "", rest)
    parts = _parts(rest)
    if not parts:
        return header

    if len(parts) == 1:
        single = parts[0]
        at = _AT.split(single, maxsplit=1)
        if len(at) == 2 and has_role_word(at[0]) and not has_role_word(at[1]):
            header.role, header.company = at[0].strip(), at[1].strip()
        elif has_role_word(single):
            header.role = single
        else:
            header.name = single
        return header

    roles = [p for p in parts if has_role_word(p)]
    others = [p for p in parts if not has_role_word(p)]
    if len(roles) == 1 and others:
        header.role = roles[0]
        header.company = others[0]
        # Whatever else the line says -- a city, "Remote" -- is where, not who.
        if len(others) > 1:
            header.location = ", ".join(others[1:])
        return header
    if len(roles) == 1 and not others:
        header.role = roles[0]
        return header
    # No title word, or several: the line names things this cannot tell apart.
    # The first part is kept as a label and the structure is left for a person.
    header.name = parts[0]
    header.ambiguous = True
    return header


def is_date_line(text: str) -> tuple[Span, str | None] | None:
    """A line that is a date span, optionally with a place. None otherwise."""
    if len(text) > MAX_HEADER:
        return None
    found = find_span(text)
    if found is None:
        return None
    span, start, end = found
    if re.search(r"\w", text[:start]):
        # A date AFTER a name is a job header ("Acme, 2019 - 2020"), not a
        # date line. A date line starts with its date.
        return None
    rest = (text[:start] + " " + text[end:]).strip()
    parts = _parts(rest)
    if not parts:
        return span, None
    if any(has_role_word(p) for p in parts):
        return None
    joined = ", ".join(parts)
    # A short remainder is a place or a way of working ("Remote"). A long one
    # is a sentence that happens to contain a year, and is a claim.
    if len(joined) <= 48 and len(parts) <= 3 and not joined.endswith("."):
        return span, joined
    return None


@dataclass
class Entry:
    """One role at one organisation, as the document laid it out."""

    key: str
    section: str
    company: str | None = None
    role: str | None = None
    span: Span | None = None
    location: str | None = None
    #: A heading or header name this could not place as company or role.
    label: str | None = None
    #: The source lines that stated this entry's structure, verbatim.
    lines: list[Line] = field(default_factory=list)
    #: Why a person should look at the structure itself, if they should.
    unresolved: list[str] = field(default_factory=list)

    def finish(self) -> None:
        self.unresolved = [
            reason
            for reason, missing in (
                ("company", not self.company),
                ("role", not self.role),
                ("dates", self.span is None),
            )
            if missing
        ]
        if self.label and not (self.company and self.role):
            self.unresolved.insert(0, "structure")

    @property
    def title(self) -> str:
        """How the entry is named on screen, from its own words only."""
        return " / ".join(p for p in (self.company, self.role) if p) or self.label or ""


def _name_it(entry: Entry, header: Header, *, as_company: bool | None = None) -> None:
    if header.company:
        entry.company = header.company
    if header.role:
        entry.role = header.role
    if header.span and entry.span is None:
        entry.span = header.span
    if header.location and not entry.location:
        entry.location = header.location
    if header.name:
        if as_company is True and not entry.company:
            entry.company = header.name
        elif as_company is False and not entry.role:
            entry.role = header.name
        else:
            entry.label = header.name


class ExperienceReader:
    """Walks one employment-like section, line by line, keeping context.

    `entry_for(line)` is called for every line and returns the entry a claim
    on that line belongs to, or None when the line was structure itself.
    """

    def __init__(self, section: str, section_level: int, lines: list[Line], counter: list[int]):
        self.section = section
        self.section_level = section_level
        self.lines = lines
        self.counter = counter
        self.entries: list[Entry] = []
        self.company: str | None = None
        self.company_level: int | None = None
        self.company_lines: list[Line] = []
        self.current: Entry | None = None
        #: Whether the current entry has any claim under it yet. A header that
        #: follows claims opens a new entry; one directly under another header
        #: (a role under a company) adds to it.
        self.has_claims = False

    def _new(self) -> Entry:
        self.counter[0] += 1
        entry = Entry(key=f"{self.section}-e{self.counter[0]:02d}", section=self.section)
        if self.company:
            entry.company = self.company
            entry.lines.extend(self.company_lines)
        self.entries.append(entry)
        self.current = entry
        self.has_claims = False
        return entry

    def _has_child_heading(self, index: int, level: int) -> bool:
        """Whether a deeper heading follows before any claim line or sibling."""
        for line in self.lines[index + 1 :]:
            if line.kind == "heading":
                return line.level > level
            if line.kind in {"item"}:
                return False
            if line.kind == "text" and not is_date_line(line.text):
                return False
        return False

    def _next_is_date(self, index: int) -> bool:
        for line in self.lines[index + 1 :]:
            if line.kind == "blank":
                continue
            return line.kind == "text" and is_date_line(line.text) is not None
        return False

    def _next_is_role_header(self, index: int) -> bool:
        """Whether the next line names a role and has a date on it or under it."""
        position = next(
            (n for n in range(index + 1, len(self.lines)) if self.lines[n].kind != "blank"), None
        )
        if position is None:
            return False
        first = self.lines[position]
        if first.kind != "text" or first.text.rstrip().endswith("."):
            return False
        header = read_header(first.text)
        if not header.role:
            return False
        return header.span is not None or self._next_is_date(position)

    def read(self) -> list[tuple[Line, Entry | None, bool]]:
        """Every line, the job it belongs to, and whether it is a CLAIM.

        A structure line (a heading, a date line, a job header) is `False`.
        A claim line carries its job, or None when the document put it under
        no job at all -- which is left that way rather than given an empty,
        invented one.
        """
        out: list[tuple[Line, Entry | None, bool]] = []
        for index, line in enumerate(self.lines):
            if line.kind == "heading":
                self._heading(index, line)
                out.append((line, None, False))
                continue
            if line.kind not in {"item", "text"}:
                out.append((line, None, False))
                continue

            if line.kind == "text":
                dated = is_date_line(line.text)
                if dated is not None:
                    span, place = dated
                    entry = self.current
                    if entry is None or (entry.span is not None and self.has_claims):
                        entry = self._new()
                    if entry.span is None:
                        entry.span = span
                        if place and not entry.location:
                            entry.location = place
                        entry.lines.append(line)
                        out.append((line, None, False))
                        continue
                    # A second date under one heading, with no claim between:
                    # the document is saying something this cannot place.
                    entry.lines.append(line)
                    entry.unresolved.append("dates")
                    out.append((line, None, False))
                    continue
                header = self._header_line(index, line)
                if header is not None:
                    out.append((line, None, False))
                    continue

            if self.current is None and self.company:
                # Under a company heading with no role yet: the company is
                # known, so the job is real even though its title is not.
                self._new()
            self.has_claims = self.current is not None
            out.append((line, self.current, True))
        for entry in self.entries:
            extra = [r for r in entry.unresolved if r == "dates"]
            entry.finish()
            for reason in extra:
                if reason not in entry.unresolved:
                    entry.unresolved.append(reason)
        return out

    def _heading(self, index: int, line: Line) -> None:
        header = read_header(line.text)
        parent = self._has_child_heading(index, line.level)
        if parent:
            # A heading with headings under it is the ORGANISATION, and the
            # headings under it are the roles held there -- when its own words
            # do not say otherwise.
            self.company = header.company or (header.name if not header.role else None) or line.text
            self.company_level = line.level
            self.company_lines = [line]
            if header.role:
                # "Acme - Consultant" with roles beneath: the line is a role.
                self.company = header.company or header.name or None
                entry = self._new()
                _name_it(entry, header)
                entry.lines.append(line)
            else:
                self.current = None
            return
        if self.company_level is not None and line.level <= self.company_level:
            self.company, self.company_level, self.company_lines = None, None, []
        entry = self._new()
        under_company = self.company is not None
        _name_it(entry, header, as_company=None if not under_company else False)
        if header.name and not under_company and not header.role:
            # A lone name as a heading, with no role in it: most CVs put the
            # ORGANISATION here and the title on the next line.
            entry.label = None
            entry.company = header.name
        entry.lines.append(line)

    def _header_line(self, index: int, line: Line) -> Header | None:
        """A plain line that names a job, or None when it is a claim."""
        text = line.text
        if len(text) > 160 or text.rstrip().endswith((".", ";", "!")):
            return None
        header = read_header(text)
        dated = header.span is not None
        named = header.company or header.role or header.name
        if not named:
            return None
        if (
            not dated
            and header.name
            and not header.ambiguous
            and len(text.split()) <= 6
            and self._next_is_role_header(index)
        ):
            # "Globex Logistics" alone, then "Operations Director" and a date:
            # the plain-text form of a company heading with roles under it.
            self.company, self.company_level, self.company_lines = header.name, None, [line]
            self.current = None
            return header
        if not dated and not self._next_is_date(index):
            return None
        if header.company and self.company and header.company != self.company:
            # A header naming its own organisation ends the one above it.
            self.company, self.company_level, self.company_lines = None, None, []
        if not dated and not (header.role or header.company) and len(text.split()) > 8:
            return None
        entry = self.current
        opens_new = (
            entry is None
            or self.has_claims
            or (header.company and entry.company and header.company != entry.company)
            or (header.role and entry.role)
            or (dated and entry.span is not None)
        )
        if opens_new:
            entry = self._new()
        assert entry is not None
        if header.company or header.role:
            _name_it(entry, header)
        else:
            # A name alone next to a date: under a company it is the role; with
            # no company around it, nothing on the page says which it is.
            if entry.company and not entry.role or header.name and has_role_word(header.name):
                entry.role = header.name
            elif not entry.company:
                entry.company = None
                entry.label = header.name
            if header.span and entry.span is None:
                entry.span = header.span
            if header.location and not entry.location:
                entry.location = header.location
        entry.lines.append(line)
        return header


def chronology_key(
    start: str | None, start_year: int | None, end: str | None, end_year: int | None, current: bool
) -> tuple:
    """Newest first. Current roles first, then by end, then by start.

    A role with no dates sorts after every dated one, and ties break on the
    caller's stable identifier -- so the order never depends on insertion.
    """

    def month(normalized: str | None, year: int | None, default: str) -> str:
        if normalized:
            return normalized
        if year:
            return f"{year:04d}-{default}"
        return ""

    ending = "9999-99" if current else month(end, end_year, "12") or month(start, start_year, "12")
    beginning = month(start, start_year, "01")
    undated = not ending and not beginning
    # Negated strings do not sort, so the tuple sorts ascending on an inverted
    # key: undated last, then later endings first, then later starts first.
    return (undated, _invert(ending), _invert(beginning))


def _invert(value: str) -> str:
    return "".join(chr(0x10FFFF - ord(ch)) for ch in value) if value else "\U0010ffff"

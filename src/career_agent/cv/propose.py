"""What a CV appears to say, offered as PROPOSALS and never as facts.

The rule that shapes this module, and the reason it is a separate file from
`extract.py`: **nothing here is true because it was read.** Every proposal
arrives unverified, carries the line it came from, and becomes a
`VerifiedClaim` only when a person says so. A CV is a document somebody wrote
about themselves, often years ago, and reading a line off one is not the same
as confirming it.

Deterministic, and that is a choice rather than a limitation. A model could
read a CV more fluently and would also invent -- a plausible metric, a tool the
person never used, a year that rounds nicely. Every proposal below can be
traced to a line of the document by string equality, so a review is a
comparison rather than an act of faith. `docs/product/cv-llm-rfc.md` describes
what an optional model-assisted path would have to guarantee before it earned
the same trust.

**Recall is not the goal. Reviewability is.** A section detector that finds
eight of ten skills and quotes each one is more useful than one that finds ten
and cannot say where any came from. Anything unrecognised stays in the text for
a person to read; nothing is discarded and nothing is guessed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from career_agent.cv.markdown import Line, classify, inline
from career_agent.cv.structure import SECTION_WORDS, Entry, ExperienceReader, fold
from career_agent.domain.enums import ClaimSource, ClaimType

#: Section headings, in the two languages this product speaks. Matched on a
#: whole line, because "skills" inside a sentence is prose and "SKILLS" alone
#: on a line is a heading -- the same intent-anchoring lesson the matcher
#: learned about job descriptions.
HEADINGS: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "profile", "about", "objective", "resumo", "perfil", "sobre"),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "experiencia",
        "experiencia profissional",
        "atuacao",
    ),
    "skills": ("skills", "competencies", "core skills", "competencias", "habilidades"),
    "tools": ("tools", "technologies", "tech stack", "ferramentas", "tecnologias"),
    "education": ("education", "academic", "formacao", "formacao academica", "educacao"),
    "certifications": (
        "certifications",
        "certificates",
        "licences",
        "licenses",
        "licences and certifications",
        "certificacoes",
        "cursos",
        "licencas",
        "registro profissional",
    ),
    "languages": ("languages", "idiomas"),
    "projects": ("projects", "projetos", "portfolio", "portfolio de projetos"),
    # -- WHERE A CAREER STARTS, WHEN IT DID NOT START WITH A SALARY ---------
    #
    # A CV with no employment history is not an empty CV. Somebody entering a
    # profession, returning after a break or changing field writes their
    # evidence under headings this reader had never heard of -- and a heading
    # it does not recognise proposes NOTHING, so the lines under it were read,
    # counted as unread, and silently dropped.
    #
    # That is the ingestion-shaped version of the same defect the experience
    # reading fixed downstream: the product could not see the work, so it
    # concluded there was none.
    #
    # Each of these maps to the claim type that describes what the line IS,
    # not to a weaker one. Volunteering and an internship are EMPLOYMENT: the
    # person did the work, and whether they were paid for it is a fact about
    # the arrangement rather than about what they can now do. Reading them as
    # a lesser kind of claim would be this file deciding whose experience
    # counts.
    "volunteering": (
        "volunteering",
        "volunteer experience",
        "volunteer work",
        "community work",
        "voluntariado",
        "trabalho voluntario",
    ),
    "internships": (
        "internships",
        "internship",
        "placements",
        "work placements",
        "estagios",
        "estagio",
    ),
    "freelance": (
        "freelance",
        "freelance work",
        "independent work",
        "consulting",
        "autonomo",
        "trabalho autonomo",
    ),
    "activities": (
        "activities",
        "extracurricular",
        "extracurricular activities",
        "student organizations",
        "societies",
        "atividades",
        "atividades extracurriculares",
    ),
    "awards": ("awards", "honours", "honors", "premios", "reconhecimentos"),
}

#: Which claim a section's lines become. A section with no entry here is read
#: and shown but proposes nothing, which is the honest treatment of a heading
#: this code does not understand.
SECTION_CLAIMS: dict[str, ClaimType] = {
    "experience": ClaimType.EMPLOYMENT,
    "skills": ClaimType.SKILL,
    "tools": ClaimType.TOOL,
    "education": ClaimType.EDUCATION,
    "certifications": ClaimType.CERTIFICATION,
    "projects": ClaimType.PROJECT,
    # See the headings above: work is work. `ACHIEVEMENT` for awards, because
    # an award is a thing that happened rather than a job that was held.
    "volunteering": ClaimType.EMPLOYMENT,
    "internships": ClaimType.EMPLOYMENT,
    "freelance": ClaimType.EMPLOYMENT,
    "activities": ClaimType.PROJECT,
    "awards": ClaimType.ACHIEVEMENT,
}

#: A line that is a list of things rather than a sentence. Skills and tools
#: sections are usually written this way and splitting them yields one
#: proposal per item instead of one per line.
_SEPARATORS = re.compile(r"\s*[|,;/]\s*|\s+[-•]\s+")

#: Leading bullet glyphs and numbering, stripped before a line is read.
_BULLET = re.compile(r"^\s*(?:[-*•‣●▪·]|\d+[.)])\s*")

#: A figure that could be an achievement's measurement. Used ONLY to mark a
#: proposal as carrying one, never to extract the number as a fact of its own:
#: "increased revenue 40%" is the candidate's sentence, and "40%" on its own is
#: this program inventing a metric it cannot attribute.
_MEASURED = re.compile(
    r"\d+\s*%|\bR\$\s*[\d.,]+|\bUS\$\s*[\d.,]+|\$\s*[\d.,]+|\b\d[\d.,]*\s*(?:k|mil|million|milhoes)\b",
    re.IGNORECASE,
)

#: An employment line usually carries a period. Two four-digit years, or one
#: and a present-tense word, in either language.
_PERIOD = re.compile(
    # punctuation-check: allow a CV writes 2021 - present with a real en dash
    r"(19|20)\d{2}\s*[-–—a]{1,3}\s*((19|20)\d{2}|present|current|atual|hoje)",
    re.IGNORECASE,
)

#: Too short to be a claim about anything.
_MIN_LENGTH = 3
#: Long enough that it is a paragraph rather than an item. Kept whole -- a
#: truncated proposal is a proposal whose quote no longer matches.
_MAX_LENGTH = 2000


@dataclass(frozen=True, slots=True)
class Proposal:
    """One thing the CV appears to say, with the line it came from.

    `evidence` is the line as a person reads it: its words, with any Markdown
    syntax removed. A reviewer compares the proposal against it, which is the
    whole mechanism by which this can be trusted without trusting the code
    that produced it. `source_text` is the same line VERBATIM, syntax and all,
    and `source_line` is where it sits: the raw provenance, kept so anybody can
    see exactly what the document said.
    """

    claim_key: str
    claim_type: ClaimType
    text: str
    section: str
    evidence: str
    #: Whether the line carries a figure. Not the figure itself: a number
    #: lifted out of its sentence is a metric this program invented.
    has_measurement: bool = False
    #: The job this line sits under, when the document put it under one.
    entry_key: str | None = None
    source_line: int | None = None
    source_text: str | None = None

    @property
    def is_verified(self) -> bool:
        """Always False. Here so the answer is stated rather than assumed."""
        return False


@dataclass
class ReadCv:
    """Everything one document produced, and everything it did not."""

    sections: dict[str, list[str]] = field(default_factory=dict)
    proposals: list[Proposal] = field(default_factory=list)
    #: Lines under no recognised heading. Reported, never discarded: a CV whose
    #: headings this code does not know is still readable by a person, and
    #: silently dropping half of it would be the worst possible outcome.
    unread_lines: list[str] = field(default_factory=list)
    #: The jobs the document laid out: company, role, dates. Structure, never
    #: claims: a heading is how a CV is organised, not something it asserts.
    entries: list[Entry] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for proposal in self.proposals:
            out[proposal.claim_type.value] = out.get(proposal.claim_type.value, 0) + 1
        return out

    def entry(self, key: str | None) -> Entry | None:
        return next((entry for entry in self.entries if entry.key == key), None)


#: Section names, folded, for matching. Built once from `HEADINGS`.
_FOLDED_HEADINGS = {name: section for section, names in HEADINGS.items() for name in names}

#: Sections whose lines are jobs, read with the structure reader.
EMPLOYMENT_SECTIONS = frozenset({"experience", "volunteering", "internships", "freelance"})

#: Lines that carry no words a person wrote.
_DECORATION = frozenset({"rule", "fence", "table_rule", "blank", "code"})


def _bare(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", fold(text))).strip()


def _heading(line: str) -> str | None:
    """Which section a line NAMES, if it names one.

    A whole-line match, lower-cased, accents and punctuation trimmed.
    "Skills:" is a heading; "skills in Python" is a sentence about skills and
    is not. "Experiência Profissional" is a heading however it is accented.
    """
    bare = _bare(inline(_BULLET.sub("", line).strip()))
    if not bare or len(bare) > 40:
        return None
    return _FOLDED_HEADINGS.get(bare)


def _loose_heading(text: str) -> str | None:
    """A MARKED heading that names a section in a phrase.

    "Additional experience", "Experiencias anteriores". Only ever asked of a
    line written with heading syntax, where the document has already said it
    is a heading and the only question is which one.
    """
    exact = _heading(text)
    if exact:
        return exact
    for word in _bare(text).split():
        if word in SECTION_WORDS:
            return SECTION_WORDS[word]
    return None


def _structure(text: str) -> tuple[list[tuple[str | None, Line, Entry | None, bool]], list[Entry]]:
    """Every line, with the section it is in and the job it belongs to."""
    lines = classify(text)
    blocks: list[tuple[str | None, int, list[Line]]] = []
    headings: dict[int, str | None] = {}
    current: str | None = None
    level = 0
    body: list[Line] = []

    for line in lines:
        named: str | None = None
        opens = False
        if line.kind == "heading":
            if current is None or level == 0 or line.level <= level:
                named = _loose_heading(line.text)
                # A marked heading at the section's own level or above ends
                # the section, named or not. An unknown one starts a section
                # this does not read: reported, never guessed at.
                opens = named is not None or (current is not None and line.level <= level)
        elif line.kind == "text":
            named = _heading(line.raw)
            opens = named is not None
        if opens:
            blocks.append((current, level, body))
            headings[line.number] = named
            current = named
            level = line.level if line.kind == "heading" else 0
            body = [line]
            continue
        body.append(line)
    blocks.append((current, level, body))

    out: list[tuple[str | None, Line, Entry | None, bool]] = []
    entries: list[Entry] = []
    counter = [0]
    for section, section_level, block in blocks:
        head = [line for line in block if line.number in headings]
        rest = [line for line in block if line.number not in headings]
        out.extend((None, line, None, False) for line in head)
        if section in EMPLOYMENT_SECTIONS:
            reader = ExperienceReader(section, section_level, rest, counter)
            # A structure line (job header, date line) is never proposed; a
            # claim keeps its section whether or not it sits under a job.
            out.extend((section, line, entry, claim) for line, entry, claim in reader.read())
            entries.extend(reader.entries)
        else:
            out.extend((section, line, None, True) for line in rest)
    return out, entries


def split_sections(text: str) -> tuple[dict[str, list[str]], list[str]]:
    """Group lines under the headings they follow.

    Returns the sections and the lines that preceded any heading. Both, because
    a CV with no headings at all is common and would otherwise read as empty.
    Lines are plain text: Markdown syntax is removed and the words are kept.
    """
    return _sections_of(_structure(text)[0])


def _sections_of(
    structured: list[tuple[str | None, Line, Entry | None, bool]],
) -> tuple[dict[str, list[str]], list[str]]:
    sections: dict[str, list[str]] = {}
    unread: list[str] = []
    for section, line, _entry, _claim in structured:
        if section is None and line.kind in {"heading", "text"}:
            named = _loose_heading(line.text) if line.marked else _heading(line.raw)
            if named:
                sections.setdefault(named, [])
                continue
        if not line.text or line.kind in _DECORATION:
            continue
        if section is None:
            unread.append(line.text)
        else:
            sections.setdefault(section, []).append(line.text)
    return sections, unread


def _items(line: str, section: str) -> list[str]:
    """One line, as the things it lists.

    Only for sections that are written as lists. An experience bullet is a
    sentence and splitting it on commas would turn one accomplishment into
    four fragments that mean nothing apart.

    A list line often starts with a group label, "Integration: Workato,
    MuleSoft", and the label is how the list is organised rather than a skill.
    """
    if section not in {"skills", "tools", "languages"}:
        return [line]
    labelled = re.match(r"^([^:,;|]{1,40}):\s+(.+)$", line)
    if labelled and len(labelled.group(1).split()) <= 4:
        line = labelled.group(2)
    parts = [part.strip() for part in _SEPARATORS.split(line)]
    return [part for part in parts if len(part) >= _MIN_LENGTH]


def _key(section: str, index: int, text: str) -> str:
    """A stable identifier for one proposal.

    Derived from the content so that re-importing the same CV proposes the same
    keys, and a claim the person already accepted is recognised rather than
    offered again as though it were new.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40]
    return f"{section}-{index:02d}-{slug}" if slug else f"{section}-{index:02d}"


def read_cv(text: str) -> ReadCv:
    """Everything one CV appears to say, organised by job. Nothing is verified.

    Headings, date lines and job headers are STRUCTURE: they become entries
    and never proposals. Only the lines under them, the things the person says
    they did, knew or earned, are proposed, each carrying the entry it sits
    under and the source line it came from.
    """
    structured, entries = _structure(text)
    sections, unread = _sections_of(structured)
    result = ReadCv(sections=sections, unread_lines=unread, entries=entries)

    counters: dict[str, int] = {}
    for section, line, entry, claim in structured:
        if section is None or not claim or line.kind not in {"item", "text"} or not line.text:
            continue
        claim_type = SECTION_CLAIMS.get(section)
        if claim_type is None:
            # A section that is read and shown but proposes nothing. `summary`
            # and `languages` are prose and a free-text preference; turning
            # either into a career CLAIM would be this code deciding what a
            # paragraph asserts.
            continue
        for item in _items(line.text, section):
            if not (_MIN_LENGTH <= len(item) <= _MAX_LENGTH):
                continue
            counters[section] = counters.get(section, 0) + 1
            result.proposals.append(
                Proposal(
                    claim_key=_key(section, counters[section], item),
                    claim_type=claim_type,
                    text=item,
                    section=section,
                    # The line as it stood, before splitting. A reviewer
                    # sees the context the item was taken from.
                    evidence=line.text,
                    has_measurement=bool(_MEASURED.search(item)),
                    entry_key=entry.key if entry is not None else None,
                    source_line=line.number,
                    source_text=line.raw[:_MAX_LENGTH],
                )
            )
    return result


def to_claim(
    proposal: Proposal,
    *,
    text: str | None = None,
    employer: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
):
    """One accepted proposal, as a claim the person has confirmed.

    `text` lets a reviewer EDIT before accepting, which is the third of the
    three answers and the one that matters most: a CV line is often nearly
    right, and forcing accept-or-reject would push somebody into accepting
    something they would have corrected.

    `verified=True` is set HERE and nowhere else in this package. Reading
    proposes; only this function, called from a review, confirms.
    """
    from career_agent.domain.claims import VerifiedClaim

    return VerifiedClaim(
        claim_key=proposal.claim_key,
        claim_type=proposal.claim_type,
        text=(text or proposal.text).strip(),
        source=ClaimSource.RESUME,
        verified=True,
        # The job the suggestion sat under, as the review showed it. Months
        # only, and only a coherent span: a claim's period is a fact about
        # when, and a half-read one is left off rather than guessed.
        employer=(employer or None),
        period_start=period_start if period_start else None,
        period_end=(
            period_end if period_start and period_end and period_end >= period_start else None
        ),
        # The line the proposal came from, kept with the claim. This is what
        # makes an application-preparation view able to say WHY it believes
        # something about the candidate.
        evidence_ref=proposal.evidence[:2000],
    )


def has_period(line: str) -> bool:
    """Whether a line carries dates, for the interface to hint with."""
    return bool(_PERIOD.search(line))

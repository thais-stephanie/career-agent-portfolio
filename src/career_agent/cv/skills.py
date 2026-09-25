"""Skills, however a career document writes them.

INPUT FORMATS VARY; THE MODEL DOES NOT
--------------------------------------
A resume lists "Skills: Python, SQL". A LinkedIn export has "Top Skills" and,
under a role, "Skills & Tools: Workato, Salesforce" or "Keywords: Process
Improvement". A master career file has a technology table: "Technology |
Depth | Where". The owner's own CV proposed no skills at all (2026-09-25),
because this reader knew one presentation.

`cv.markdown` and `cv.propose._structure` answer the STRUCTURAL question --
which lines are headings, items, table rows, and which job a line sits
under. This module answers the SEMANTIC one: which of those lines name skills,
and in what context. Every skill it finds is a proposal like any other:
nothing is confirmed, and each keeps its document, section, line and the line
exactly as written.

THE CONTEXTS, kept apart in the stored `section` and `entry_key`:

    skills / tools     a skills or tools list
    keywords           a named capability list ("Keywords", "Capabilities")
    matrix             a technology table row; the other columns are context
    entry_key          set when the list sits inside one job ("Skills & Tools:"
                       under a role): the document states that relationship;
                       unset, the list is global

A certification line is never mined for skills: being certified in something
is not having used it in production, and this reader does not say otherwise.
"""

from __future__ import annotations

import re

from career_agent.cv.structure import fold

#: Between skills: comma, semicolon, pipe, a bullet or a middle dot. NEVER a
#: slash ("CI/CD", "REST/GraphQL", "AS-IS / TO-BE" are one skill each) and
#: NEVER a spaced hyphen: "Boomi - Certified only" is one skill with its
#: qualifier, and splitting it would state a plain "Boomi" and invent a skill
#: called "Certified only".
_SEPARATORS = re.compile(r"\s*[,;|•·]\s*")

#: What a skills, tools or keywords heading is made of. A heading made ONLY
#: of these words, with at least one head word, names that section.
SKILL_HEADS = frozenset(
    {"skills", "skill", "competencies", "competences", "competencias", "habilidades", "expertise"}
)
TOOL_HEADS = frozenset(
    {
        "tools",
        "technologies",
        "technology",
        "stack",
        "platforms",
        "toolkit",
        "systems",
        "software",
        "ferramentas",
        "tecnologias",
        "plataformas",
        "sistemas",
    }
)
KEYWORD_HEADS = frozenset({"keywords", "keyword", "capabilities", "capacidades", "chave"})
QUALIFIERS = frozenset(
    {
        "technical",
        "core",
        "key",
        "top",
        "hard",
        "soft",
        "professional",
        "areas",
        "area",
        "of",
        "and",
        "my",
        "matrix",
        "e",
        "de",
        "palavras",
        "tecnicas",
        "tecnicos",
        "principais",
    }
)

#: A technology table's header cells, and which of them names the skill.
_SKILL_COLUMNS = frozenset(
    {
        "technology",
        "technologies",
        "tool",
        "tools",
        "skill",
        "skills",
        "stack",
        "platform",
        "tecnologia",
        "ferramenta",
        "habilidade",
        "competencia",
    }
)
_CONTEXT_COLUMNS = frozenset(
    {
        "depth",
        "level",
        "proficiency",
        "where",
        "context",
        "experience",
        "years",
        "used",
        "notes",
        "category",
        "area",
        "evidence",
        "nivel",
        "onde",
        "contexto",
        "experiencia",
        "anos",
        "notas",
        "categoria",
    }
)
#: A certification table's header cells: a header row is structure, not a
#: certification.
_CERT_COLUMNS = frozenset(
    {
        "certification",
        "certifications",
        "certificate",
        "title",
        "name",
        "issuer",
        "provider",
        "issued",
        "date",
        "expires",
        "expiry",
        "expiration",
        "credential",
        "credential id",
        "id",
        "certificacao",
        "emissor",
        "emitido",
        "validade",
        "credencial",
    }
)

#: One letter is too short to tell from noise; "Go", "AI" and "BI" are skills.
MIN_LENGTH = 2
MAX_LENGTH = 80


def _bare(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z ]+", " ", fold(text))).strip()


#: The words that name a skills section ON THEIR OWN, on a line with no
#: heading syntax. A bare generic noun -- "Technology", "Systems",
#: "Platforms", "Expertise" -- is also a department, an industry or a team
#: name, and inside a job it must stay part of that job (the adversarial
#: review, 2026-09-25: a "Technology" line turned a job's bullets into tools).
EXPLICIT_HEADS = frozenset(
    {
        "skills",
        "skill",
        "competencies",
        "competences",
        "competencias",
        "habilidades",
        "keywords",
        "keyword",
        "tools",
        "ferramentas",
    }
)


def heading_section(bare: str, *, marked: bool = False) -> str | None:
    """ "skills", "tools" or "keywords" for a line made only of their words.

    `marked` is STRUCTURAL EVIDENCE: a Markdown heading, or a label followed
    by ":" and a list. With it, the whole vocabulary applies ("## Tools &
    Platforms", "Technologies: a, b"). Without it, the line must carry an
    explicit head word ("Top Skills", "Technical Skills", "Keywords"): a
    generic noun alone never opens a section.
    """
    words = bare.split()
    vocabulary = SKILL_HEADS | TOOL_HEADS | KEYWORD_HEADS | QUALIFIERS
    if not words or any(w not in vocabulary for w in words):
        return None
    if not marked and not any(w in EXPLICIT_HEADS for w in words):
        return None
    if any(w in KEYWORD_HEADS for w in words):
        return "keywords"
    if any(w in SKILL_HEADS for w in words):
        return "skills"
    if any(w in TOOL_HEADS for w in words):
        return "tools"
    return None


def split_skills(text: str) -> list[str]:
    """One list line, as the skills it names: trimmed, deduplicated, in order."""
    out: list[str] = []
    seen: set[str] = set()
    for part in _SEPARATORS.split(text):
        name = part.strip().strip(".").strip()
        if not (MIN_LENGTH <= len(name) <= MAX_LENGTH):
            continue
        if fold(name) in seen:
            continue
        seen.add(fold(name))
        out.append(name)
    return out


def inline_list(text: str) -> tuple[str, list[str]] | None:
    """ "Skills & Tools: Workato, Salesforce" -> ("skills", [...]).

    Only when the LABEL is a skills, tools or keywords heading. "Built: a
    pipeline" is a sentence with a colon, and stays one.
    """
    labelled = re.match(r"^\s*([^:]{1,40}):\s+(.+)$", text)
    if not labelled:
        return None
    # The colon is the structure; the label must still be only list words.
    section = heading_section(_bare(labelled.group(1)), marked=True)
    if section is None:
        return None
    names = split_skills(labelled.group(2))
    # And what follows must be a LIST of short names, not a sentence:
    # "Systems: migrated the billing system" is a sentence with a colon.
    if not names or any(len(name.split()) > 5 for name in names):
        return None
    if len(names) == 1 and len(names[0].split()) > 3:
        return None
    return section, names


def cells(raw: str) -> list[str]:
    """A table row's cells, EMPTY ONES KEPT, so columns stay in place."""
    return [cell.strip() for cell in raw.strip().strip("|").split("|")]


def is_table_row(raw: str) -> bool:
    return raw.count("|") >= 1 and len([c for c in cells(raw) if c]) >= 2


def skill_column(header: list[str]) -> int | None:
    """Which column of a technology table names the skill, if it is one.

    A header row is ONLY skill and context words: "Technology | Depth |
    Where". Anything else is a row of data, not a header.
    """
    folded = [_bare(cell) for cell in header]
    if not folded or any(not cell for cell in folded):
        return None
    if any(cell not in _SKILL_COLUMNS | _CONTEXT_COLUMNS for cell in folded):
        return None
    for index, cell in enumerate(folded):
        if cell in _SKILL_COLUMNS:
            return index
    return None


#: A technology table's column that says HOW a skill is held. It changes what
#: can honestly be claimed ("Certified only" is not hands-on use), so it is
#: kept in the statement itself and never dropped.
_DEPTH_COLUMNS = frozenset({"depth", "level", "proficiency", "nivel", "profundidade"})


def matrix_skills(header: list[str] | None, row: list[str], column: int) -> list[tuple[str, str]]:
    """(skill name, statement) for one technology-table row.

    The statement carries the row's depth when the table states one:
    "Boomi (Certified only)", "n8n (Built & owned)". Every other column
    (Where) stays as context in the evidence line.
    """
    if column >= len(row):
        return []
    qualifier = ""
    if header:
        folded = [_bare(cell) for cell in header]
        depth = next((i for i, cell in enumerate(folded) if cell in _DEPTH_COLUMNS), None)
        if depth is not None and depth < len(row):
            value = row[depth].strip()
            if value and set(value) - {"-", chr(0x2013), chr(0x2014)}:
                qualifier = value
    return [
        (name, f"{name} ({qualifier})" if qualifier else name) for name in split_skills(row[column])
    ]


def is_certification_header(raw: str) -> bool:
    folded = [_bare(cell) for cell in cells(raw) if cell.strip()]
    return len(folded) >= 2 and all(cell in _CERT_COLUMNS for cell in folded)

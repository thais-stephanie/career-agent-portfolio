"""What a job ad asks for, read from its immutable snapshot (Tailor stages 1-3).

DETERMINISTIC, and every requirement QUOTES THE AD. A requirement is a span of
the snapshot's own text: its `source_quote` is `text[start:end]`, never a
paraphrase, so a test can always find it in the ad. Normalized `concepts` and
`named` terms exist only to compare it with evidence.

Segmenting: the ad is cut into lines, a line into items on bullets and
semicolons, a long item into sentences; a heading on its own line, or a
heading followed by ":" at the start of a line ("Requisitos: ..."), opens a
section. Flattened text (one long paragraph from a PDF or a stripped page) is
cut on inline bullets and sentence ends the same way.

Classifying is conservative and written down:
* hardness: REQUIRED only on an explicit cue in the item ("required", "must",
  "obrigatório"...) or under a requirements heading; PREFERRED on a nice-to-have
  cue or heading ("diferenciais"); everything else UNKNOWN. A responsibility
  is never REQUIRED just for being listed.
* kind: from cue words (language, education, certification, location, work
  authorization, schedule, years of experience), the section, and whether the
  item is made of named tools. Location, work authorization and schedule are
  ELIGIBILITY questions: they are reported, never covered or gapped by a resume.
* importance: REQUIRED 3, a responsibility 2, anything else 1, plus 1 when the
  same ask appears twice. The job title earns nothing.

Named terms (tools, products, certifications) are words written with a capital
inside a sentence, in capitals, in mixed case, or with a digit or dot ("HubSpot",
"SQL", "n8n", "Node.js"), plus a short list of tools usually written in lower
case. They are what a resume may never claim without evidence naming them.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from career_agent.match.text import fold

REQUIRED_CUE = re.compile(
    r"\b(required|requirements?|must|mandatory|minimum|need to|needs to|you need|essential"
    r"|obrigat[oó]ri[oa]s?|necess[aá]ri[oa]s?|imprescind[ií]ve(?:l|is)|requisitos?"
    r"|indispens[aá]ve(?:l|is)|requerid[oa]s?|excluyente)\b",
    re.IGNORECASE,
)
#: The work itself, said outside a heading ("You will build...").
DUTY_CUE = re.compile(
    r"\b(you will|you'll|you would|responsible for|in this role you|voc[eê] vai|voc[eê] ir[aá]"
    r"|ser[aá] respons[aá]vel|ser[aá]s responsable)\b",
    re.IGNORECASE,
)
PREFERRED_CUE = re.compile(
    r"\b(preferred|preferably|nice to have|nice-to-have|bonus|a plus|is a plus|ideally"
    r"|desej[aá]ve(?:l|is)|diferencia(?:l|is)|ser[aá] um diferencial|plus|deseable|valorado)\b",
    re.IGNORECASE,
)

#: Headings, folded, to their section. A line that IS one of these (or starts
#: with one and a colon) opens that section.
HEADINGS: dict[str, str] = {
    **dict.fromkeys(
        (
            "requirements",
            "qualifications",
            "basic qualifications",
            "minimum qualifications",
            "what you need",
            "what you'll need",
            "what you will need",
            "what you bring",
            "what we're looking for",
            "what we are looking for",
            "who you are",
            "you have",
            "must have",
            "must-haves",
            "skills and experience",
            "about you",
            "requisitos",
            "requisitos obrigatorios",
            "pre-requisitos",
            "qualificacoes",
            "o que buscamos",
            "o que esperamos",
            "o que voce precisa ter",
            "perfil",
            "requisitos y calificaciones",
            "lo que buscamos",
            "competencias tecnicas",
            "conhecimentos",
            "habilidades",
            "required skills",
            "requirements and skills",
        ),
        "REQUIRED",
    ),
    **dict.fromkeys(
        (
            "nice to have",
            "nice-to-have",
            "preferred",
            "preferred qualifications",
            "bonus",
            "bonus points",
            "pluses",
            "diferenciais",
            "desejavel",
            "desejaveis",
            "sera um diferencial",
            "requisitos desejaveis",
            "deseable",
            "deseables",
            "valorado",
        ),
        "PREFERRED",
    ),
    **dict.fromkeys(
        (
            "responsibilities",
            "key responsibilities",
            "what you'll do",
            "what you will do",
            "the role",
            "your role",
            "duties",
            "day to day",
            "in this role",
            "responsabilidades",
            "principais responsabilidades",
            "atribuicoes",
            "atividades",
            "o que voce vai fazer",
            "o que voce fara",
            "no dia a dia",
            "suas responsabilidades",
            "responsabilidades y funciones",
            "funciones",
        ),
        "RESPONSIBILITIES",
    ),
    **dict.fromkeys(
        (
            "benefits",
            "perks",
            "what we offer",
            "about us",
            "about the company",
            "who we are",
            "compensation",
            "salary",
            "equal opportunity",
            "how to apply",
            "our mission",
            "beneficios",
            "o que oferecemos",
            "sobre nos",
            "sobre a empresa",
            "quem somos",
            "remuneracao",
            "salario",
            "etapas do processo",
            "processo seletivo",
            "sobre nosotros",
            "que ofrecemos",
        ),
        "IGNORE",
    ),
}

#: Kinds, by cue, in the order they are asked. The first that fires wins.
_KIND_CUES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "WORK_AUTHORIZATION",
        re.compile(
            r"\b(authori[sz]ed to work|work authori[sz]ation|visas?|sponsor\w*|citizen(ship)?"
            r"|right to work|autoriza[cç][aã]o de trabalho|cidadania|permiso de trabajo)\b",
            re.I,
        ),
    ),
    (
        "LOCATION",
        re.compile(
            r"\b(based in|located (?:in|within)|be located|must live|reside|relocat\w*"
            r"|on-?site|onsite"
            r"|in-office|hybrid"
            r"|presencial|h[ií]brido|residir|morar em|residencia|remote within)\b",
            re.I,
        ),
    ),
    (
        "SCHEDULE",
        re.compile(
            r"\b(shifts?|weekends?|night|overtime|business hours|time ?zone|hor[aá]rio|turnos?"
            r"|plant[aã]o|fins de semana|jornada)\b",
            re.I,
        ),
    ),
    (
        "LANGUAGE",
        re.compile(
            r"\b(english|spanish|portuguese|french|german|fluent|fluency|bilingual|native speaker"
            r"|ingl[eê]s|espanhol|portugu[eê]s|flu[eê]ncia|fluente|idioma|ingl[eé]s|espa[nñ]ol)\b",
            re.I,
        ),
    ),
    (
        "CERTIFICATION",
        re.compile(
            r"\b(certif\w*|certificad[oa]s?|certifica[cç][aã]o|certificaciones?|license[sd]?)\b",
            re.I,
        ),
    ),
    (
        "EDUCATION",
        re.compile(
            r"\b(degree|bachelor'?s?|master'?s degree|mba|phd|diploma|graduation|university"
            r"|gradua[cç][aã]o|ensino superior|forma[cç][aã]o|bacharel|licenciatura|titulo)\b",
            re.I,
        ),
    ),
    (
        "EXPERIENCE",
        re.compile(
            r"\b(\d+\+?\s*(years?|anos?|a[nñ]os)|years? of|anos de|track record)\b",
            re.I,
        ),
    ),
)
ELIGIBILITY_KINDS = frozenset({"LOCATION", "WORK_AUTHORIZATION", "SCHEDULE"})

_STOP = frozenset(
    """
    a an and are as at be by for from has have in is it of on or our the their this to we
    will with you your able ability strong excellent good great solid deep hands-on
    knowledge understanding experience experienced skills skill proficiency proficient
    familiarity familiar working work plus preferred required requirement requirements must
    should would including etc e o os um uma de do da dos das em no na nos nas para por com
    sem que se ser ter sua seu suas seus como mais muito boa bom forte conhecimento
    conhecimentos experiencia habilidade habilidades vivencia desejavel diferencial
    obrigatorio necessario requisito y el la los las en con del al es tener conocimiento
    conocimientos manejo years year anos ano minimum least advanced basic intermediate level
    across within between
    nivel
    """.split()  # noqa: SIM905
)

#: The same concept in Portuguese or Spanish, folded, to its English form.
#: Short on purpose: tool names need no translation, and a guess here would
#: be a false "covered".
CONCEPTS: dict[str, str] = {
    "automacao": "automation",
    "automacoes": "automation",
    "automatizar": "automation",
    "automatizacion": "automation",
    "automate": "automation",
    "automated": "automation",
    "automating": "automation",
    "integracao": "integration",
    "integracoes": "integration",
    "integracion": "integration",
    "integrations": "integration",
    "integrate": "integration",
    "dados": "data",
    "datos": "data",
    "relatorios": "reporting",
    "relatorio": "reporting",
    "reports": "reporting",
    "report": "reporting",
    "informes": "reporting",
    "vendas": "sales",
    "ventas": "sales",
    "receita": "revenue",
    "ingresos": "revenue",
    "processos": "process",
    "processo": "process",
    "procesos": "process",
    "processes": "process",
    "clientes": "customer",
    "cliente": "customer",
    "customers": "customer",
    "client": "customer",
    "gestao": "management",
    "gerenciamento": "management",
    "gestion": "management",
    "manage": "management",
    "managing": "management",
    "analise": "analysis",
    "analisis": "analysis",
    "analyze": "analysis",
    "analytics": "analysis",
    "desenvolvimento": "development",
    "desarrollo": "development",
    "develop": "development",
    "projetos": "project",
    "projeto": "project",
    "proyectos": "project",
    "projects": "project",
    "equipe": "team",
    "equipes": "team",
    "equipo": "team",
    "teams": "team",
    "operacoes": "operations",
    "operacao": "operations",
    "operaciones": "operations",
    "operation": "operations",
    "fluxos": "workflow",
    "fluxo": "workflow",
    "workflows": "workflow",
    "flujos": "workflow",
    "ferramentas": "tools",
    "herramientas": "tools",
}

#: Tools usually written in lower case, so capitalisation cannot find them.
LOWERCASE_TOOLS = frozenset({"dbt", "n8n", "git", "jira", "make.com", "zapier", "excel"})

_TOKEN = re.compile(r"[a-z0-9][a-z0-9.+#/-]*[a-z0-9+#]|[a-z0-9]")
_NAMED = re.compile(
    r"(?<![\w.])(?:[A-Z][a-z]*[A-Z][\w.]*|[A-Z]{2,}[\w.+#]*|\w*\d\w*(?:\.\w+)?|[\w]+\.(?:js|io|com|ai|net)"
    r"|[A-Z][a-zà-ÿ]+(?:\s+[A-Z][a-zà-ÿ]+)*)(?![\w])"
)
_COMMON_CAPS = frozenset(
    """
    I We You Our The This That They It A An If In On At For To As And Or Your Their Eu Nos
    Voce Voces O Os Um Uma Para Com Em No Na Se Ser Sua Seu El La Los Las Y Con Senior
    Junior Pleno Lead Manager Director Analyst Engineer Specialist Coordinator Head Remote
    Remoto Hybrid Monday Friday January February March April May June July August September
    October November December Experience Strong Excellent Knowledge Ability Bonus Plus Must
    Nice Preferred Required Requirements Responsibilities Qualifications Benefits Requisitos
    Diferenciais Responsabilidades Conhecimento Experiencia Experiência Vivência
    """.split()  # noqa: SIM905
)

_LINE_BREAK = re.compile(r"\n+")
_ITEM_BREAK = re.compile(r"(?:^|\s)[•·▪●◦■□‣∙*]\s+|;\s+|(?:^|\s)[-\u2013]\s+(?=[A-ZÀ-Ý])")
_SENTENCE = re.compile(r"(?<=[a-zà-ÿ0-9)][.!?])\s+(?=[A-ZÀ-Ý])")
_HEAD_COLON = re.compile(r"^\s*([^\n:]{3,60}):\s*")
_BULLET_LEAD = re.compile(r"^\s*(?:[•·▪●◦■□‣∙*-]|\d+[.)])\s*")  # punctuation-check: allow
MAX_WORDS = 60
#: A line at least this long that does not end a sentence was wrapped, not ended.
WRAPPED = 50
#: What may lead an item and is not part of it.
_TRIM = " \t;•·▪●◦■□‣∙*-\u2013,"


def folded(text: str) -> str:
    return fold(text)[0]


def tokens(text: str) -> set[str]:
    """Comparison tokens: folded, stop words out, a few forms joined, English concepts."""
    out = set()
    for raw in _TOKEN.findall(folded(text)):
        if raw in _STOP or raw in _OPENERS or len(raw) < 3 or raw[0].isdigit():
            continue
        word = raw[:-1] if len(raw) > 4 and raw.endswith("s") and not raw.endswith("ss") else raw
        out.add(CONCEPTS.get(raw, CONCEPTS.get(word, word)))
    return out


#: Words that open a sentence with a capital and are not names: the verbs and
#: nouns an ad or a resume line starts with. A capitalised first word NOT
#: here, and not shaped like a verb or abstract noun, is read as a name
#: ("Workato certification", "Salesforce Apex").
_OPENERS = frozenset(
    """
    build own manage create lead drive support design develop deliver run partner work help
    define implement maintain write improve ensure collaborate coordinate analyse analyze
    strong excellent proven solid deep hands experience knowledge familiarity understanding
    ability advanced fluent great good responsible must nice bonus plus preferred required
    construir criar gerenciar liderar apoiar desenvolver implementar manter garantir atuar
    conhecimento experiencia vivencia dominio forte boa bom ingles espanhol
    """.split()  # noqa: SIM905
)
_ABSTRACT = re.compile(r"(ing|ed|ly|ar|er|ir|cao|coes|mento|ment|ness|ity|dade|ive|ous)$")


def _opener(word: str) -> bool:
    plain = folded(word)
    return plain in _OPENERS or plain in _STOP or bool(_ABSTRACT.search(plain))


def named_terms(text: str, *, sentence_start: bool = True) -> set[str]:
    """Named things in `text`, folded: what a resume may not claim without evidence."""
    found: set[str] = set()
    starts = {0} | {
        m.end() for m in re.finditer(r"[.!?:;\n•]\s*|^\s*[-*]\s*", text)
    }  # punctuation-check: allow
    for match in _NAMED.finditer(text):
        word = match.group(0).strip()
        first = word.split()[0]
        at_start = sentence_start and match.start() in starts
        if at_start and word.istitle() and first.isalpha() and _opener(first):
            word = word[len(first) :].strip()  # "Build", "Experiência": an opener, not a name
        elif at_start and " " in word and first in _COMMON_CAPS:
            word = word[len(first) :].strip()
        if not word or word in _COMMON_CAPS or word[0].isdigit() or word[0] == "$":
            continue
        found.add(folded(word).rstrip("."))
    for word in _TOKEN.findall(folded(text)):
        if word in LOWERCASE_TOOLS:
            found.add(word)
    return found


@dataclass(frozen=True)
class Requirement:
    id: str
    source_quote: str
    start: int
    section: str
    kind: str
    hardness: str
    importance: int
    language: str
    concepts: tuple[str, ...]
    named: tuple[str, ...]
    #: Further places the same ask is quoted (a merged duplicate).
    also_quoted: tuple[str, ...] = ()

    @property
    def eligibility(self) -> bool:
        return self.kind in ELIGIBILITY_KINDS


@dataclass
class Analysis:
    requirements: list[Requirement] = field(default_factory=list)
    language: str = "en"

    def by_id(self) -> dict[str, Requirement]:
        return {r.id: r for r in self.requirements}


def _language(text: str) -> str:
    words = set(folded(text).split())
    pt = len(words & {"de", "com", "para", "voce", "em", "uma", "dos", "das", "experiencia", "e"})
    es = len(words & {"con", "para", "usted", "los", "las", "una", "del", "experiencia", "y"})
    en = len(words & {"the", "with", "and", "you", "for", "experience", "of", "to", "in"})
    best = max((en, "en"), (pt, "pt"), (es, "es"))
    return best[1]


def _heading(line: str) -> str | None:
    key = folded(line).strip(" :.-*#").strip()
    return HEADINGS.get(key)


_INLINE_HEAD = re.compile(
    r"(?<![a-z])(" + "|".join(sorted(map(re.escape, HEADINGS), key=len, reverse=True)) + r")\s*:"
)


def _lines(text: str) -> list[tuple[int, int]]:
    """Lines of the ad, also cut before a heading written inline ("... Requisitos: ...")."""
    flat, offsets = fold(text)
    # A line break inside a sentence (text wrapped at a fixed width) is not a
    # new item: a long line that does not end a sentence runs on, unless the
    # next line opens a list item.
    breaks = [
        m
        for m in _LINE_BREAK.finditer(text)
        if not (
            len(text[text.rfind("\n", 0, m.start()) + 1 : m.start()]) >= WRAPPED
            and text[: m.start()].rstrip()[-1:] not in ".!?:;"
            and not _BULLET_LEAD.match(text[m.end() :])
            and len(m.group(0)) == 1
        )
    ]
    cuts = {0, len(text)} | {m.end() for m in breaks}
    cuts |= {offsets[m.start()] for m in _INLINE_HEAD.finditer(flat)}
    ordered = sorted(cuts)
    return [(a, b) for a, b in zip(ordered, ordered[1:], strict=False)]


def _spans(text: str) -> list[tuple[int, int, str]]:
    """(start, end, section) of every item in the ad, in order, on the original text."""
    out: list[tuple[int, int, str]] = []
    section = "OTHER"
    for start, pos in _lines(text):
        line = text[start:pos].rstrip("\n")
        pos = start + len(line)
        if not line.strip():
            continue
        stripped = _BULLET_LEAD.sub("", line)
        lead = len(line) - len(stripped)
        head = _heading(stripped)
        if head is not None:
            section = head
            continue
        colon = _HEAD_COLON.match(stripped)
        if colon and _heading(colon.group(1)) is not None:
            section = _heading(colon.group(1)) or section
            lead += colon.end()
        body_start = start + lead
        body = text[body_start:pos]
        cuts = [0, *(m.end() for m in _ITEM_BREAK.finditer(body)), len(body)]
        for a, b in zip(cuts, cuts[1:], strict=False):
            piece = body[a:b]
            inner = [0, *(m.end() for m in _SENTENCE.finditer(piece)), len(piece)]
            for x, y in zip(inner, inner[1:], strict=False):
                s, e = body_start + a + x, body_start + a + y
                # Trim spaces and list punctuation from the ends, never inside.
                while s < e and text[s] in _TRIM:
                    s += 1
                while e > s and text[e - 1] in " \t;,":
                    e -= 1
                if e - s >= 3:
                    out.append((s, e, section))
    return out


def _kind(quote: str, section: str, named: set[str], concepts: set[str]) -> str:
    for kind, cue in _KIND_CUES:
        if cue.search(quote):
            return kind
    if section == "RESPONSIBILITIES" or DUTY_CUE.search(quote):
        return "RESPONSIBILITY"
    if named and len(named) >= max(1, len(concepts) - len(named)):
        return "TOOL"
    if section in ("REQUIRED", "PREFERRED"):
        return "SKILL"
    return "OTHER"


def _hardness(quote: str, section: str) -> str:
    if PREFERRED_CUE.search(quote):
        return "PREFERRED"
    if REQUIRED_CUE.search(quote):
        return "REQUIRED"
    return {"REQUIRED": "REQUIRED", "PREFERRED": "PREFERRED"}.get(section, "UNKNOWN")


def _same_ask(a: Requirement, b: Requirement) -> bool:
    """Clearly the same ask: the same named things and nearly the same words."""
    if a.kind != b.kind or set(a.named) != set(b.named):
        return False
    x, y = set(a.concepts), set(b.concepts)
    return bool(x | y) and len(x & y) / len(x | y) >= 0.75


def analyse(text: str) -> Analysis:
    """Requirements, quoted from `text` exactly, de-duplicated conservatively."""
    found: list[Requirement] = []
    for start, end, section in _spans(text):
        quote = text[start:end]
        if section == "IGNORE" or len(quote.split()) < 2 and not named_terms(quote):
            continue
        if len(quote.split()) > MAX_WORDS:
            continue  # prose about the company, not an ask
        concepts, named = tokens(quote), named_terms(quote)
        if not concepts and not named:
            continue
        if section == "OTHER" and not (
            REQUIRED_CUE.search(quote)
            or PREFERRED_CUE.search(quote)
            or DUTY_CUE.search(quote)
            or any(cue.search(quote) for _, cue in _KIND_CUES)
        ):
            continue  # an unheaded line with no cue: the company talking, not asking
        kind = _kind(quote, section, named, concepts)
        hardness = _hardness(quote, section)
        importance = 3 if hardness == "REQUIRED" else 2 if kind == "RESPONSIBILITY" else 1
        rid = "r" + hashlib.sha256(folded(quote).encode("utf-8")).hexdigest()[:10]
        req = Requirement(
            id=rid, source_quote=quote, start=start, section=section, kind=kind,
            hardness=hardness, importance=importance, language=_language(quote),
            concepts=tuple(sorted(concepts)), named=tuple(sorted(named)),
        )  # fmt: skip
        twin = next((r for r in found if r.id == rid or _same_ask(r, req)), None)
        if twin is None:
            found.append(req)
            continue
        index = found.index(twin)
        found[index] = Requirement(
            **{
                **twin.__dict__,
                "importance": max(twin.importance, req.importance) + 1,
                "hardness": "REQUIRED"
                if "REQUIRED" in (twin.hardness, req.hardness)
                else twin.hardness,
                "also_quoted": (*twin.also_quoted, quote),
            }
        )
    return Analysis(requirements=found, language=_language(text))

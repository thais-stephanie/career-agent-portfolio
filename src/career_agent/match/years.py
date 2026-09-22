"""Figures of years in a posting, and which of them are about somebody's career.

WHY THIS FILE EXISTS
--------------------
Two readers ask the same question of the same paragraph. `match/seniority.py`
asks "how senior is this role" for a scorer; `match/experience.py` asks "will
they consider me" for a filter. Both need to find a figure of years, and both
need to know which figures are not about experience at all.

Until now each spelled that out for itself, deliberately, with a comment in both
files saying the two spellings must stay identical so one posting cannot produce
two different numbers. That comment was doing the work a module should do, and it
failed twice:

  1. Both said `anos` where Brazilian postings write `ano`, so `1 ano na area`
     matched nothing in either reader.

  2. **Both read somebody's AGE as a demand about their career.** 1,488 postings
     in the corpus stored a required minimum of twelve years or more, 863 of them
     at eighteen, because `Faixa etaria: minimo 18 anos` and `Must be 21 years of
     age` put a required-sounding word right beside a number.

A number beside a cue is not a statement about a career. That sentence is true
for every reader of a posting, and it belongs in one place.

THE TWO LANGUAGES DO NOT WRITE THIS THE SAME WAY, AND THAT IS MEASURED
-----------------------------------------------------------------------
English names the subject in the figure's own phrase often enough to be read
negatively: find the figure, and withhold it when the phrase says it is an age.
`Minimum 12 years of relevant customer success, strategy consulting` never says
"experience" and is still plainly a requirement, so a positive rule would throw
it away.

Portuguese does the opposite. Measured over 5,284 postings reading
`REQUIRED_MINIMUM` on 2026-09-10, 912 carried a Portuguese figure, and of those
**390 sat in a sentence that never mentioned experience at all**:

    Ter no minimo 18 anos                                   an age
    Idade entre 18 e 21 anos e 10 meses                     an age
    maiores de 18 anos, e obrigatoria a apresentacao do
        certificado de reservista                           military paperwork
    Ter no minimo 1 ano de empresa                          tenure, internal vacancy
    possuir no minimo 1 ano de admissao                     tenure, internal vacancy
    Disponibilidade para estagiar por no minimo 1 ano       availability, not history
    Possuir no minimo 1 ano para concluir a sua graduacao   time left in a degree
    CNH categoria B ha no minimo 1 ano                      a driving licence
    empresa com quase 40 anos de historia                   the EMPLOYER's age
    Ha mais de 30 anos distribuindo qualidade               the EMPLOYER's age
    Criamos produtos para meninas entre 10 e 16 anos        the product

Every one of those, printed as "this job asks for N years of experience", is a
sentence the employer never wrote. And the direction of the damage is the same
one this whole reading exists to undo: `estagiar por no minimo 1 ano` and `1 ano
para concluir a sua graduacao` appear on INTERNSHIP and trainee adverts, so the
postings aimed squarely at somebody with no history were the ones being labelled
as wanting a year of it.

So Portuguese is read POSITIVELY: a figure counts when its own sentence says it
is about experience, or when the figure itself is followed by a role. Everything
else is withheld, and a withheld figure costs a `NOT_STATED` -- which shows the
posting rather than hiding it, and is the safe direction by the same argument
`match/gates.py` uses for an unrecognised hiring scope.

WHAT IT DOES NOT DO
-------------------
It does not decide what a figure MEANS -- required, preferred, a wish, a level.
That is each reader's own job and the two answer it differently on purpose.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from career_agent.match.text import SENTENCE_BREAKS, sentence_start

__all__ = ["YEARS", "career_years"]


#: A figure of years, in either language.
#:
#: `anos?` and not `anos`: `1 ano na area` matched nothing while the `?` was
#: missing, so a Brazilian posting asking for a single year read as one that had
#: never mentioned years at all.
YEARS = re.compile(r"\b(\d{1,2})\s*(?:\+|plus)?\s*(?:years?|yrs?|anos?)\b", re.IGNORECASE)


#: How far around a figure to look for a statement that it is an age. Wider than
#: `experience._CUE_WINDOW` on the right because the disclaimer trails the
#: figure: `22 anos, conforme estabelecido pela Lei da Aprendizagem` puts the
#: decisive words forty-five characters after the number.
AGE_WINDOW = 80


#: A statement that the number beside it is how old somebody is.
#:
#: **`\bages?\b` AND NOT `age`, and the boundary is load bearing.** The bare
#: spelling sits inside language, manager, package, average, coverage,
#: percentage, storage, stage, message and engagement, which between them appear
#: in most postings ever written. Unanchored, this would not remove ages -- it
#: would silence both readers almost everywhere, and quietly, because "the
#: posting did not say" is a legitimate answer.
#:
#: `lei da aprendizagem` and `jovem aprendiz` are here because a Brazilian
#: apprenticeship states its age band without ever using the word for age:
#: `Ter entre 14 e 22 anos, conforme estabelecido pela Lei da Aprendizagem`. It
#: is the most entry-level category this market has, and it was being read as a
#: job demanding twenty-two years of experience.
#:
#: `under N years` is the one English form that is neither an age nor a
#: requirement: `Health insurance for your children under 25 years`.
AGE_CONTEXT = re.compile(
    r"(?:"
    r"years?\s+of\s+age"
    r"|years?\s+old"
    r"|years?\s+or\s+older"
    r"|older\s+than\s+\d"
    r"|under\s+\d{1,2}\s+years?"
    r"|\bages?\b"
    r"|\baged\b"
    r"|\bidade\b"
    r"|faixa\s+et[áa]ria"
    r"|anos?\s+de\s+idade"
    r"|anos?\s+(?:completos|incompletos)"
    r"|lei\s+da\s+aprendizagem"
    r"|\b(?:jovem|menor)\s+aprendiz\b"
    r")",
    re.IGNORECASE,
)


#: Whether the figure was written in Portuguese, asked of the matched text rather
#: than of the posting: one advert can carry both languages.
_PORTUGUESE = re.compile(r"anos?\Z", re.IGNORECASE)


#: What makes a Portuguese sentence one about the candidate's experience.
#:
#: Deliberately SHORT. `no mercado`, `no segmento` and `atuamos` were all
#: candidates and all three are how a company describes ITSELF -- `Atuamos ha 40
#: anos nos setores de projetos`, `empresa com 40 anos de historia, consolidada
#: no mercado`. A vocabulary that admits the employer's own marketing is the V1.2
#: eligibility defect in a second place.
_ABOUT_EXPERIENCE = re.compile(
    r"(?:experi[êe]nc|viv[êe]nc|na\s+[áa]rea|nas\s+[áa]reas)",
    re.IGNORECASE,
)


#: ...or the figure names a role directly after itself: `2 anos como vendedor em
#: concessionarias`. The only shape in the measured sample that is a genuine
#: requirement with no experience word in its sentence.
#: ...or the figure names the work directly after itself: `2 anos como vendedor
#: em concessionarias`, `3 anos em bancos relacionais`, `5 anos de atuacao em
#: logistica`. `em` is the one that matters -- naming a field after the figure is
#: how Portuguese states a requirement whose sentence never says "experience".
#:
#: NO `\A`. `Pattern.match(text, pos)` anchors at `pos` on its own, and `\A`
#: anchors at the start of the WHOLE string regardless of `pos` -- so the first
#: version of this matched nothing except on a posting beginning with the word,
#: silently, while every other case in the file still passed.
_CAREER_TAIL = re.compile(
    r"\s*(?:como\b|em\b|de\s+atua[çc][ãa]o|atuando\b|trabalhando\b|de\s+carreira\b)",
    re.IGNORECASE,
)


#: HOW OLD THE EMPLOYER IS, which `em` above would otherwise let back in.
#:
#: Admitting `em` after the figure is what makes `3 anos em bancos relacionais`
#: readable, and it also admits `40 anos em atividade`. A company describing its
#: own history is not a requirement, and in Portuguese it has a handful of fixed
#: shapes: `ha mais de 30 anos`, `com quase 40 anos`, `50 anos de historia`.
#: Naming those is narrower and safer than refusing `em`.
_EMPLOYER_AGE = re.compile(
    r"(?:"
    r"h[áa]\s+(?:mais\s+de\s+|quase\s+|cerca\s+de\s+)?\d{1,2}\s*anos"
    r"|com\s+(?:mais\s+de\s+|quase\s+|cerca\s+de\s+)\d{1,2}\s*anos"
    # `empresa com quase 40 anos` is measured; `empresa de 40 anos` is the same
    # construction with the other preposition, which is a generalisation of the
    # evidence rather than an invented sentence.
    r"|empresa\s+(?:de|com)\s+(?:mais\s+de\s+|quase\s+|cerca\s+de\s+)?\d{1,2}\s*anos"
    r"|\d{1,2}\s*anos\s+de\s+(?:hist[óo]ria|mercado|funda[çc][ãa]o|tradi[çc][ãa]o)"
    r")",
    re.IGNORECASE,
)


def _sentence(description: str, start: int, end: int) -> str:
    """The whole sentence holding `description[start:end]`, uncut.

    Not `text.sentence_at`, which strips and elides for quoting. This is for
    reading, and a sentence is a BULLET here: `SENTENCE_BREAKS` includes the
    newline and the semicolon, which is what keeps `Ter no minimo 18
    anos;Ensino Medio completo;Ter experiencia em vendas` from letting the
    third bullet vouch for the first.
    """
    left = sentence_start(description, start)
    right = end
    while right < len(description) and description[right] not in SENTENCE_BREAKS:
        right += 1
    return description[left:right]


def _age_window(description: str, start: int, end: int) -> str:
    """The text around one figure that may say it is an age, clipped to its sentence.

    THE CLIP IS THE HALF THAT TOOK A SECOND ATTEMPT. `Must be 18 years of age.
    We require a minimum of 5 years of experience` is one advert making two
    unrelated statements, and a blind eighty-character reach backwards let the
    first one silence the second. An age in a NEIGHBOURING sentence says nothing
    about this number.
    """
    left = max(sentence_start(description, start), start - AGE_WINDOW)
    limit = end
    stop = min(end + AGE_WINDOW, len(description))
    while limit < stop and description[limit] not in SENTENCE_BREAKS:
        limit += 1
    return description[left:limit]


def career_years(description: str) -> Iterator[re.Match[str]]:
    """Every figure of years in `description` that is about somebody's career.

    Yields the matches themselves, so a caller keeps the offsets it needs to cut
    a verifiable quote out of the original text (ADR-0002).

    A figure is skipped rather than the posting abandoned: an advert can state a
    legal minimum age in one bullet and a genuine experience requirement in the
    next, and both readers must still see the second.
    """
    for match in YEARS.finditer(description):
        window = _age_window(description, match.start(), match.end())
        if AGE_CONTEXT.search(window) or _EMPLOYER_AGE.search(window):
            continue
        if _PORTUGUESE.search(match.group(0)) and not _about_experience(description, match):
            continue
        yield match


def _about_experience(description: str, match: re.Match[str]) -> bool:
    if _CAREER_TAIL.match(description, match.end()):
        return True
    return bool(_ABOUT_EXPERIENCE.search(_sentence(description, match.start(), match.end())))

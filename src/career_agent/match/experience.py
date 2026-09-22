"""What a posting asks for in the way of previous experience.

WHY THIS IS NOT `seniority.py`
------------------------------
`match/seniority.py` answers "how senior is this role", and it answers it for
a scorer: one value, one source, one quote, and a DEFAULT of MID when the
posting said nothing. That is the right shape for a weighted component.

It is the wrong shape for the question a career changer, a new graduate and a
person looking for their first job actually ask, which is not "how senior" but
"will they consider me". Those are three different readings of one paragraph:

    "3+ years of experience required"        -> a stated minimum
    "Experience with X is a plus"            -> a wish
    "No previous experience necessary"       -> an explicit invitation
    (the posting never mentions experience)  -> nothing was asked

**REQUIRED, PREFERRED and NICE-TO-HAVE are not the same fact**, and collapsing
them is what turns a missing line on somebody's CV into a hard gap on a job
that never asked for it. `seniority.required_years` already refuses to read a
PREFERRED figure as a minimum; this module keeps the distinction instead of
discarding it, and carries the entry-level invitations beside it.

WHAT IT DOES NOT DO
-------------------
It does not decide whether the candidate qualifies. It reads the POSTING, in
the same vocabulary for every reader, so the fingerprint stays candidate
independent (invariant 4). Whether four years is a wall or a formality is a
question for a filter somebody sets, not for this file.

**Silence is NOT_STATED, never NONE_REQUIRED.** A posting that does not mention
experience has not invited anybody; it has failed to say. Invariant 2.
"""

from __future__ import annotations

import re

from career_agent.domain.matching import (
    NOT_STATED_EXPERIENCE,
    EntrySignal,
    ExperienceReading,
    ExperienceRequirement,
)
from career_agent.match.text import sentence_at
from career_agent.match.years import career_years

__all__ = [
    "NOT_STATED_EXPERIENCE",
    "EntrySignal",
    "ExperienceReading",
    "ExperienceRequirement",
    "read_experience",
]


# =========================================================================
# the patterns
# =========================================================================


#: `career_years` and not `YEARS.finditer`, and that is where the age veto
#: lives. `match/years.py` yields the figures that are about somebody's CAREER
#: and withholds the ones about their birthday: `Faixa etaria: minimo 18 anos`
#: and `Must be 21 years of age` put two of the required cues below right beside
#: a number, and this reader turned 863 postings into a demand for eighteen years
#: of experience. `seniority.py` had the identical defect from the identical
#: duplicated regex, which is why the answer is one module rather than a third
#: synchronised copy.

#: Cues making the figure beside them a MINIMUM the employer requires.
_REQUIRED_CUE = re.compile(
    r"\b(?:require[sd]?|required|must have|minimum|at least|no m[ií]nimo|"
    r"m[ií]nimo de|necess[áa]ri|obrigat[óo]ri|exig)",
    re.IGNORECASE,
)

#: Cues making it a WISH. A figure carrying one of these is never a minimum,
#: even when a required cue also sits in the window -- two contradicting cues
#: about one number is exactly where guessing is unaffordable.
#: `desej[áa]ve` rather than `desej[áa]vel`, because Portuguese pluralises it:
#: `2 anos de experiencia sao DESEJAVEIS` is the ordinary way to write this and
#: the singular spelling missed every one of them.
_PREFERRED_CUE = re.compile(
    r"\b(?:preferred|preferably|ideal|ideally|desirable|desej[áa]ve|preferencialmente)",
    re.IGNORECASE,
)

#: Cues making it an explicit BONUS, which is weaker again than preferred.
_BONUS_CUE = re.compile(
    r"\b(?:nice to have|a plus|bonus|differential|diferencial|conta pontos)",
    re.IGNORECASE,
)

#: How far back a cue may sit and still be about this figure. One bullet.
_CUE_WINDOW = 120

#: An ask for experience with no figure attached: "experience required",
#: "experiencia comprovada". The word for experience has to be there; a bare
#: "required" belongs to whatever noun it was actually about.
_UNQUANTIFIED_REQUIRED = re.compile(
    r"(?:"
    r"(?:proven|previous|prior|professional|relevant|demonstrable)\s+experience"
    r"|experience\s+(?:is\s+)?(?:required|essential|mandatory)"
    r"|experi[êe]ncia\s+(?:comprovada|pr[ée]via|anterior|obrigat[óo]ria|necess[áa]ria)"
    r"|exig[e-]\s*se\s+experi[êe]ncia"
    r")",
    re.IGNORECASE,
)

#: The employer saying none is needed.
#:
#: **THE PORTUGUESE FORMS WERE WRONG, AND THEY WERE WRONG ABOUT THE MARKET THIS
#: PRODUCT EXISTS TO SERVE.** The first version expected the English sentence
#: translated -- `sem experiencia previa NECESSARIA` -- and Brazilian employers
#: do not write that. Measured over the corpus on 2026-09-10, what they write
#: is a hiring verb and a plain noun phrase:
#:
#:     Contratamos pessoas sem experiencia profissional
#:     Estamos buscando pessoas com ou sem experiencia
#:
#: Not one posting in two samples of two thousand read `NONE_REQUIRED`, and two
#: of the sentences above read `REQUIRED_MINIMUM` -- because the same advert
#: also said `sera um diferencial ter experiencia em vendas` and carried a
#: figure. **An explicit invitation was being reported as a wall**, which is the
#: exact inversion this whole reading exists to prevent, in the exact language
#: it most needed to work in.
#:
#: The anchors are all VERB-LED or contrastive on purpose. A bare `sem
#: experiencia` would match `mesmo sem experiencia previa especifica nela`,
#: which is a sentence about one TOOL and not about the job, and reading it as
#: an invitation would be manufacturing the opposite error.
_NO_EXPERIENCE = re.compile(
    r"(?:"
    # -- English -------------------------------------------------------
    r"no\s+(?:prior\s+|previous\s+|professional\s+|work\s+)?experience"
    r"\s+(?:is\s+)?(?:required|necessary|needed|expected)"
    r"|without\s+(?:prior|previous)\s+experience"
    r"|with\s+or\s+without\s+experience"
    r"|we\s+hire\s+people\s+with\s+no\s+experience"
    # -- Portuguese, as it is actually written -------------------------
    r"|n[ãa]o\s+(?:[ée]\s+)?(?:necess[áa]ri[oa]|exigid[oa]|preciso|precisa)"
    r"\s+(?:ter\s+)?experi[êe]ncia"
    r"|sem\s+experi[êe]ncia\s+(?:pr[ée]via\s+)?(?:necess[áa]ria|obrigat[óo]ria|exigida)"
    r"|sem\s+necessidade\s+de\s+experi[êe]ncia"
    r"|n[ãa]o\s+exigimos\s+experi[êe]ncia"
    r"|com\s+ou\s+sem\s+experi[êe]ncia"
    r"|(?:contratamos|aceitamos|buscamos|procuramos)\s+(?:pessoas|candidatos|profissionais)"
    r"\s+sem\s+experi[êe]ncia"
    # THE FIELD LABEL, which is how a Brazilian board prints it. `Experiencia:
    # Nao e necessario` puts the noun FIRST, and every pattern above expects it
    # last -- so the plainest statement in the market, written by the employer
    # into a structured field, was the one form this reader could not see.
    r"|experi[êe]ncia\s*[:.-]{0,2}\s*n[ãa]o\s+(?:[ée]\s+|s[ãa]o\s+)?"
    r"(?:necess[áa]ri|obrigat[óo]ri|exigid|preciso|precisa)"
    r")",
    re.IGNORECASE,
)

#: Entry level, named as such by the employer.
_ENTRY_LEVEL = re.compile(
    r"(?:\bentry[- ]level\b|\bprimeiro emprego\b|\bn[íi]vel de entrada\b"
    r"|\bin[íi]cio de carreira\b|\bcome[çc]ando na carreira\b)",
    re.IGNORECASE,
)

#: Recent graduates, students and the Brazilian `recem-formado`.
_RECENT_GRADUATE = re.compile(
    r"(?:\brecent(?:ly)?\s+grad(?:uate)?s?\b|\bnew\s+grad(?:uate)?s?\b"
    r"|\bgraduating\s+students?\b|\brec[ée]m[- ]formad|\brec[ée]m[- ]graduad"
    r"|\bestudantes?\s+de\s+gradua[çc][ãa]o\b)",
    re.IGNORECASE,
)

#: The employer offering to teach the job.
_TRAINING_PROVIDED = re.compile(
    r"(?:"
    r"training\s+(?:is\s+)?(?:provided|will be provided|included)"
    r"|on[- ]the[- ]job\s+training"
    r"|we(?:'ll| will)\s+train\s+you"
    r"|full\s+training"
    r"|treinamento\s+(?:ser[áa]\s+)?(?:fornecid|oferecid|complet|inclu[íi]d)"
    r"|oferecemos\s+treinamento"
    r"|capacita[çc][ãa]o\s+(?:fornecid|oferecid)"
    r")",
    re.IGNORECASE,
)

#: The employer inviting people from another field. Rare, and unmistakable
#: when present.
_CAREER_CHANGERS = re.compile(
    r"(?:"
    r"career\s+changers?\s+(?:are\s+)?welcome"
    r"|changing\s+careers?\s+(?:are\s+)?welcome"
    r"|from\s+(?:a\s+)?(?:different|another|non[- ]traditional)\s+(?:field|background)"
    r"|transi[çc][ãa]o\s+de\s+carreira"
    r"|mudan[çc]a\s+de\s+carreira"
    r"|de\s+outras?\s+[áa]reas?\s+s[ãa]o\s+bem[- ]vind"
    r")",
    re.IGNORECASE,
)

_ENTRY_PATTERNS: tuple[tuple[EntrySignal, re.Pattern[str]], ...] = (
    (EntrySignal.NO_EXPERIENCE_REQUIRED, _NO_EXPERIENCE),
    (EntrySignal.ENTRY_LEVEL, _ENTRY_LEVEL),
    (EntrySignal.RECENT_GRADUATE, _RECENT_GRADUATE),
    (EntrySignal.TRAINING_PROVIDED, _TRAINING_PROVIDED),
    (EntrySignal.CAREER_CHANGERS_WELCOME, _CAREER_CHANGERS),
)


# =========================================================================
# the reading
# =========================================================================


def _entry_signals(description: str) -> tuple[EntrySignal, ...]:
    return tuple(signal for signal, pattern in _ENTRY_PATTERNS if pattern.search(description))


def read_experience(description: str) -> ExperienceReading:
    """Read one posting's ask for experience. Pure, and candidate independent.

    PRECEDENCE, strongest evidence first:

    1. an explicit "no experience required" -- the employer answered the
       question outright, and no figure elsewhere in the posting overrides a
       sentence that plain;
    2. a figure carrying a REQUIRED cue and no wish cue -> REQUIRED_MINIMUM;
    3. a figure carrying a BONUS cue -> NICE_TO_HAVE, and a wish cue ->
       PREFERRED, because "3 years preferred" is not a wall;
    4. an ask for experience with no figure -> REQUIRED_UNQUANTIFIED;
    5. otherwise NOT_STATED.

    **Two different required minima produce NOT_STATED with the signals still
    read**, exactly as `seniority.required_years` does: which of them is THE
    minimum is a judgement about which skill matters most, and this layer does
    not make judgements. The entry signals survive because they are independent
    observations rather than a reading of the same number.
    """
    if not description:
        return NOT_STATED_EXPERIENCE

    signals = _entry_signals(description)

    explicit_none = _NO_EXPERIENCE.search(description)
    if explicit_none is not None:
        return ExperienceReading(
            requirement=ExperienceRequirement.NONE_REQUIRED,
            min_years=0,
            quote=sentence_at(description, explicit_none.start(), explicit_none.end()),
            entry_signals=signals,
        )

    required: list[tuple[int, int, int]] = []
    softened: list[tuple[ExperienceRequirement, int, int, int]] = []
    for match in career_years(description):
        value = int(match.group(1))
        window = description[max(0, match.start() - _CUE_WINDOW) : match.end() + 40]
        if _BONUS_CUE.search(window):
            softened.append((ExperienceRequirement.NICE_TO_HAVE, value, match.start(), match.end()))
            continue
        if _PREFERRED_CUE.search(window):
            softened.append((ExperienceRequirement.PREFERRED, value, match.start(), match.end()))
            continue
        if _REQUIRED_CUE.search(window):
            required.append((value, match.start(), match.end()))

    if required and len({value for value, _, _ in required}) == 1:
        value, start, end = required[0]
        return ExperienceReading(
            requirement=ExperienceRequirement.REQUIRED_MINIMUM,
            min_years=value,
            quote=sentence_at(description, start, end),
            entry_signals=signals,
        )

    if not required and softened:
        # The WEAKEST reading wins when a posting softens the ask in two ways:
        # "a plus" and "preferred" in one advert is an employer being generous
        # twice, and reporting the stronger of the two would narrow a list on
        # the strength of the wording rather than the requirement.
        order = {
            ExperienceRequirement.NICE_TO_HAVE: 0,
            ExperienceRequirement.PREFERRED: 1,
        }
        requirement, value, start, end = min(softened, key=lambda row: order[row[0]])
        return ExperienceReading(
            requirement=requirement,
            min_years=value,
            quote=sentence_at(description, start, end),
            entry_signals=signals,
        )

    unquantified = _UNQUANTIFIED_REQUIRED.search(description)
    if unquantified is not None and not required:
        window = description[max(0, unquantified.start() - _CUE_WINDOW) : unquantified.end() + 40]
        if _BONUS_CUE.search(window):
            requirement = ExperienceRequirement.NICE_TO_HAVE
        elif _PREFERRED_CUE.search(window):
            requirement = ExperienceRequirement.PREFERRED
        else:
            requirement = ExperienceRequirement.REQUIRED_UNQUANTIFIED
        return ExperienceReading(
            requirement=requirement,
            quote=sentence_at(description, unquantified.start(), unquantified.end()),
            entry_signals=signals,
        )

    return ExperienceReading(entry_signals=signals)

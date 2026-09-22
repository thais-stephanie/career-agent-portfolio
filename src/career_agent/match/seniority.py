"""Reading the level of the advertised role, and saying where the reading came from.

This module replaced a function that searched the description for eleven bare
words and returned whichever appeared first. It was wrong in three separate
ways, and each one is a rule here.

**A word is not a claim about the role.** "You will own lead routing between
marketing and sales" scored LEAD, because `lead` is a seniority word and also a
noun meaning a sales prospect. Measured on the corpus: 94 postings took a LEAD
reading from a GTM noun phrase alone, `Sales Development Representative` among
them. The fix is not a growing blacklist of phrases to ignore -- it is to stop
searching prose for bare words at all. The description is read only through
POSITIVE ROLE-LEVEL STATEMENTS: `senior position`, `we are looking for a lead`,
`vaga sênior`. A phrase that does not state the level of the job on offer says
nothing here, and saying nothing is a supported answer.

**Earliest does not mean best.** The old reader took whichever word appeared
first in the text. A posting titled `Senior Engineer` whose body mentions
`junior colleagues` in its second sentence read JUNIOR. Precedence is by the
QUALITY of the evidence now, and the title outranks the body because a title is
about the role by construction while a body sentence may be about anybody.

**Absence is not a middle value.** `UNCLEAR` used to be a seventh member of the
vocabulary, and the scorer paid it five points -- a posting that said nothing
scoring halfway. A reading now carries a `SenioritySource`, `DEFAULT` is one of
them, and `SeniorityReading.is_evidence` is the single place that distinction is
expressed.

The design owes its shape to `strelov1/freehire`, which met the same problem and
answered it with intent-anchored description phrases and a mask over role names
that contain a grade word. That is a CONCEPTUAL debt, recorded in
`docs/PRIOR_ART.md`: no code, table or test material was taken, the vocabulary
below is ours, and the Portuguese half has no counterpart upstream.
"""

from __future__ import annotations

import re

from career_agent.domain.enums import AnalysisConfidence, Seniority, SenioritySource
from career_agent.domain.matching import DEFAULT_SENIORITY, SeniorityReading
from career_agent.match import years
from career_agent.match.lexicon import compile_patterns
from career_agent.match.text import FoldedText, fold_field, sentence_at

# =========================================================================
# 1. THE MASK
# =========================================================================

#: Phrases that CONTAIN a grade word while naming something else entirely. They
#: are cut from the title before any grade or leadership match, so the word is
#: never available to be misread.
#:
#: Two families. The first is job-family names that happen to end in a rank
#: word: an `Account Manager` is an individual contributor in most companies,
#: and reading `manager` there as leadership is the single most common way to
#: get this wrong. The second is business nouns built on `lead`, which is where
#: the defect this module replaces actually lived.
#:
#: Only phrases that would otherwise shadow a real marker belong here. Masking
#: something that carries no rank word removes nothing and costs a scan.
GRADE_BLIND_PHRASES: tuple[str, ...] = (
    # -- job families whose head noun is a rank word, but which are not ranks --
    "account manager",
    "customer success manager",
    "customer experience manager",
    "product manager",
    "product marketing manager",
    "project manager",
    "program manager",
    "programme manager",
    "partner manager",
    "partnerships manager",
    "vendor manager",
    "community manager",
    "brand manager",
    "content manager",
    "social media manager",
    "category manager",
    "office manager",
    "account executive",
    "gerente de contas",
    "gerente de produto",
    "gerente de projeto",
    "gerente de projetos",
    "gerente de marketing",
    "gerente de sucesso do cliente",
    # -- `lead` as a business noun. This is the measured defect.
    #
    # Only FUNCTION names belong here. `lead data` and `lead source` are prose
    # nouns and were listed once, which masked the rank out of `Lead Data
    # Analyst` -- a title the product is required to read as LEAD. The mask
    # runs over the TITLE only, and the description is never searched for a
    # bare `lead` at all, so prose nouns need no masking anywhere. --
    "lead generation",
    "lead-generation",
    "lead gen",
    "lead routing",
    "lead scoring",
    "lead qualification",
    "lead enrichment",
    "lead nurturing",
    "lead management",
    "sales lead",
    "sales leads",
    "demand generation",
    "geracao de leads",
    "gestao de leads",
    # -- `staff` and `principal` owned by another meaning --
    "member of technical staff",
    "member of the technical staff",
    "staff augmentation",
    "principal component analysis",
    "principal investigator",
    # -- geography and stages that contain a grade word --
    "middle east",
    "middle-east",
    "mid-market",
    "mid market",
    "mid-training",
)


# =========================================================================
# 2. THE TITLE
# =========================================================================

#: Organisational roles. These describe a position in a hierarchy rather than a
#: craft, and they resolve to LEAD -- the top of our five-value vocabulary --
#: because the vocabulary deliberately has no rung above it.
#:
#: `head` and `chefe` require their preposition. A bare `head` is a body part, a
#: `head chef` is a cook, and `heads of terms` is a contract; `head of` is the
#: only form that reliably names a function somebody leads.
LEADERSHIP_ABSOLUTE: tuple[str, ...] = (
    "head of",
    "chefe de",
    "director",
    "diretor",
    "diretora",
    "vp",
    "vice president",
    "vice-president",
    "chief",
    "cto",
    "cpo",
    "ceo",
    "cio",
    "cfo",
    "coo",
)

#: Leadership stated as `<organisational function> manager`, listed explicitly
#: rather than derived from a rule.
#:
#: The rule shape was tempting -- "manager preceded by any noun" -- and it is
#: exactly what produces `Account Manager -> LEAD`. Every entry here names a
#: function an organisation has, never a book of business or a product surface,
#: and anything not on this list does not establish leadership. Precision is the
#: point: a missed lead reads as an unstated level, which is honest, while a
#: manufactured one tells somebody a job manages people when it does not.
LEADERSHIP_FUNCTION_FORMS: tuple[str, ...] = (
    "manager of",
    "gerente de",
    "engineering manager",
    "software engineering manager",
    "technology manager",
    "technical manager",
    "development manager",
    "operations manager",
    "infrastructure manager",
    "platform manager",
    "data manager",
    "analytics manager",
    "support manager",
    "delivery manager",
    "people manager",
    "team manager",
    "team lead",
    "tech lead",
    "technical lead",
    "squad lead",
    "gerente de engenharia",
    "gerente de operacoes",
    "gerente de tecnologia",
    "lider tecnico",
    "lider de equipe",
    "lider de time",
    "coordenador de equipe",
    "supervisor de equipe",
)

#: Explicit grades, highest first. The order IS the precedence: a title reading
#: `Senior Staff Engineer` is senior either way, and one reading `Lead Data
#: Analyst` must not be pulled down by a later rung.
#:
#: `staff`, `principal`, `distinguished` and `fellow` are industry rungs above
#: senior that this five-value vocabulary does not represent separately. They
#: map to SENIOR because that is the nearest true thing it can say; inventing
#: public grades to hold them was considered and rejected.
#:
#: `assistant`, `associate`, `specialist`, `analyst`, `consultant`, `engineer`,
#: `architect`, `developer` and `administrator` are deliberately ABSENT. They
#: name what somebody does, not how senior they are, and an `Associate` is a
#: partner-track lawyer in one industry and a shop assistant in another.
TITLE_GRADES: tuple[tuple[Seniority, tuple[str, ...]], ...] = (
    (Seniority.LEAD, ("lead", "leader", "lider", "líder")),
    # **ABOVE SENIOR, AND ORDER IS PRECEDENCE.** The first level whose alias
    # appears wins, so `Senior Staff Engineer` must meet STAFF before it meets
    # SENIOR -- a title naming two grades means the higher one, and a reading
    # that took the first word would call post-senior architecture work senior
    # execution work.
    (Seniority.PRINCIPAL, ("principal", "distinguished", "fellow")),
    (Seniority.STAFF, ("staff",)),
    (
        Seniority.SENIOR,
        (
            "senior",
            "sênior",
            "senior-level",
            "sr",
            "sr.",
        ),
    ),
    (Seniority.MID, ("mid", "mid-level", "midlevel", "intermediate", "pleno", "plena", "pl")),
    (
        Seniority.JUNIOR,
        (
            "junior",
            "júnior",
            "jr",
            "jr.",
            "entry-level",
            "entry level",
            "new grad",
            "new graduate",
            "graduate",
            "trainee",
        ),
    ),
    (
        Seniority.INTERN,
        (
            "intern",
            "internship",
            "estagio",
            "estágio",
            "estagiario",
            "estagiário",
            "estagiaria",
            "estagiária",
            "apprentice",
            "aprendiz",
        ),
    ),
)


# =========================================================================
# 3. THE DESCRIPTION
# =========================================================================

#: Sentences that state the level of the job on offer, highest grade first.
#:
#: Every one of these is anchored to an intent: a noun that means "this posting"
#: (`position`, `role`, `vaga`) or a verb of hiring (`we are looking for`,
#: `hiring a`). None is a bare grade word, because a bare grade word in prose is
#: about somebody else roughly as often as it is about the job -- `senior
#: management`, `junior colleagues`, `our staff`, `report to the head of
#: product`. Those are the traps, they have explicit negative tests, and they
#: are not a blacklist this file consults: they simply match nothing here.
DESCRIPTION_ANCHORS: tuple[tuple[Seniority, tuple[str, ...]], ...] = (
    (
        Seniority.LEAD,
        (
            "lead role",
            "lead position",
            "looking for a lead",
            "hiring a lead",
            "seeking a lead",
            "team lead position",
            "team lead role",
            "as our tech lead",
            "as our technical lead",
            "vaga de lideranca",
            "posicao de lideranca",
        ),
    ),
    # Above senior, and ordered above it for the same reason `TITLE_GRADES` is:
    # a body naming both grades means the higher one.
    (
        Seniority.PRINCIPAL,
        (
            "principal engineer",
            "principal position",
            "principal role",
            "distinguished engineer",
        ),
    ),
    (
        Seniority.STAFF,
        (
            "staff engineer",
            "staff position",
            "staff role",
            "this is a staff",
        ),
    ),
    (
        Seniority.SENIOR,
        (
            "senior-level",
            "senior level position",
            "senior level role",
            "senior position",
            "senior role",
            "looking for a senior",
            "hiring a senior",
            "seeking a senior",
            "this is a senior",
            "vaga senior",
            "posicao senior",
            "nivel senior",
        ),
    ),
    (
        Seniority.MID,
        (
            "mid-level",
            "mid level position",
            "mid level role",
            "intermediate-level",
            "this is a mid",
            "vaga pleno",
            "posicao pleno",
            "nivel pleno",
        ),
    ),
    (
        Seniority.JUNIOR,
        (
            "entry-level",
            "entry level position",
            "entry level role",
            "junior position",
            "junior role",
            "junior-level",
            "graduate position",
            "graduate programme",
            "new grad",
            "vaga junior",
            "posicao junior",
            "nivel junior",
            "programa de trainee",
        ),
    ),
    (
        Seniority.INTERN,
        (
            "internship",
            "intern position",
            "intern role",
            "trainee position",
            "trainee role",
            "vaga de estagio",
            "programa de estagio",
        ),
    ),
)


# =========================================================================
# 4. REQUIRED YEARS
# =========================================================================

#: A figure of years, re-exported from `match/years.py`.
#:
#: IT USED TO BE A SECOND COPY, with a comment in both files saying the two
#: spellings must stay identical so one posting cannot produce two different
#: numbers. That comment was doing a module's job and it failed twice: both said
#: `anos` where Brazilian postings write `ano`, and -- far worse -- both read
#: somebody's AGE as a demand about their career. `Faixa etaria: minimo 18 anos`
#: and `Must be 21 years of age` put `minimo` and `must` right beside a number,
#: and `REQUIRED_CUE` below reads both as a requirement.
#:
#: Still exported under this name: the tests and `match/preparation.py` use it.
YEARS = years.YEARS

#: Cues that the figure beside them is a MINIMUM the employer requires.
REQUIRED_CUE = re.compile(
    r"\b(?:require[sd]?|required|must have|minimum|at least|no m[ií]nimo|"
    r"m[ií]nimo de|necess[áa]rio|obrigat[óo]ri)",
    re.IGNORECASE,
)

#: Cues that it is a wish. A figure carrying one of these is never a minimum,
#: even when a `required` cue also sits in the window: two contradicting cues
#: about one number is precisely the case where guessing is unaffordable.
PREFERRED_CUE = re.compile(
    r"\b(?:preferred|preferably|nice to have|bonus|a plus|ideal|ideally|desirable|"
    r"desej[áa]vel|diferencial)",
    re.IGNORECASE,
)

#: How far back a cue may sit and still be about this figure. Measured on the
#: corpus: a requirement and its cue live in one bullet, and 120 characters is
#: about one bullet.
CUE_WINDOW = 120

#: Statements that the employer wants nobody with experience.
NO_EXPERIENCE_REQUIRED = re.compile(
    r"\b(?:no (?:prior |previous |professional )?experience (?:is )?(?:required|necessary)|"
    r"sem experi[êe]ncia (?:pr[ée]via )?(?:necess[áa]ria|obrigat[óo]ria))",
    re.IGNORECASE,
)


def _band_for_years(count: int) -> Seniority:
    """The approved bands. Years never reach LEAD, at any figure.

    A decade of experience says how long somebody has worked, and nothing at all
    about whether the job runs a team. Letting the number climb into LEAD is how
    an arithmetic fallback starts asserting an organisational fact.
    """
    if count <= 2:
        return Seniority.JUNIOR
    if count <= 5:
        return Seniority.MID
    return Seniority.SENIOR


def required_years(description: str) -> tuple[int, str] | None:
    """The single unambiguous minimum this posting requires, with its sentence.

    `None` whenever the answer would be a guess, which on the real corpus is most
    of the time: 72.3% of postings state a figure, and only 26.4% of those state
    exactly one that is marked as a requirement. The other shapes -- a bare
    number under "What we're looking for", a required figure and a preferred one
    in the same breath, two different required minima for two different skills --
    all return `None` here and fall through to the next rule.

    That is the intended trade. A wrong level is worse than an absent one,
    because an absent one is visibly absent.
    """
    if not description:
        return None

    if NO_EXPERIENCE_REQUIRED.search(description):
        match = NO_EXPERIENCE_REQUIRED.search(description)
        assert match is not None
        return 0, sentence_at(description, match.start(), match.end())

    found: list[tuple[int, int, int]] = []  # (value, start, end)
    # `career_years`, not `YEARS.finditer`: a figure its own sentence says is an
    # AGE is not a statement about anybody's career, and reading one as a
    # required minimum sent a Brazilian apprenticeship -- fourteen to twenty-four
    # years old -- through here as a twenty-two-year requirement.
    for match in years.career_years(description):
        window = description[max(0, match.start() - CUE_WINDOW) : match.end() + 40]
        if PREFERRED_CUE.search(window):
            continue
        if not REQUIRED_CUE.search(window):
            continue
        found.append((int(match.group(1)), match.start(), match.end()))

    if not found:
        return None
    if len({value for value, _, _ in found}) > 1:
        # Several different required minima. Which one is THE minimum is a
        # judgement about which skill matters most, and this layer does not
        # make judgements.
        return None

    value, start, end = found[0]
    return value, sentence_at(description, start, end)


# =========================================================================
# 5. THE READING
# =========================================================================


def _masked(field: FoldedText) -> str:
    """The folded title with every grade-blind phrase blanked out.

    Each phrase is replaced by spaces of the SAME LENGTH rather than removed, so
    every offset into the original still lands where it did and a quote cut
    afterwards is still the posting's own text.
    """
    text = field.folded
    for phrase in GRADE_BLIND_PHRASES:
        compiled = compile_patterns((phrase,))
        while True:
            match = compiled.regex.search(text)
            if match is None:
                break
            text = text[: match.start()] + " " * (match.end() - match.start()) + text[match.end() :]
    return text


def _first_match(haystack: str, phrases: tuple[str, ...]) -> re.Match[str] | None:
    """The earliest occurrence of any phrase, or None.

    Earliest WITHIN one rung only. Which rung wins is decided by the caller's
    ordering, never by position in the text.

    For a TITLE, which is a few dozen characters. The description rungs go
    through :func:`_first_match_in`, which can reject a whole rung without
    touching the text at all.
    """
    earliest: re.Match[str] | None = None
    for phrase in phrases:
        match = compile_patterns((phrase,)).regex.search(haystack)
        if match is not None and (earliest is None or match.start() < earliest.start()):
            earliest = match
    return earliest


def _first_match_in(field: FoldedText, phrases: tuple[str, ...]) -> re.Match[str] | None:
    """:func:`_first_match` over a folded field, without scanning it N times.

    WHAT WAS SLOW. The description rungs hold 59 phrases between them, and
    `_first_match` searched the whole folded advert once per phrase -- 59 full
    scans of several thousand characters per posting, whether or not any phrase
    could possibly be there. Measured on 2,000 real postings, this reader was
    the single largest line item in a score.

    WHAT WAS TRIED FIRST AND DID NOT WORK. Compiling one alternation per rung,
    so seven searches instead of fifty-nine: **1.09x, which is nothing.** An
    alternation of a dozen branches behind a lookbehind cannot use `re`'s
    literal-prefix scan, so it walks the text character by character and costs
    about what the individual searches cost. The saving is not in searching
    fewer times. It is in not searching.

    WHAT WORKS IS ALREADY IN THE LEXICON. `CompiledPatterns._worth_reading`
    answers "could any phrase of this rung occur anywhere in this field" in set
    lookups against the field's own token set, touching no text; `matches_in`
    then enumerates candidate positions with `str.find`, a C string search,
    before the regex is asked anything. Both have been there since the lexicon
    was written. This reader could not reach either, because it was handed
    `body.folded` -- a bare `str` -- and the machinery needs the `FoldedText`
    the caller already has.

    EXACTLY THE SAME ANSWER. `matches_in` yields what `regex.finditer` yields,
    so its first match starts where the earliest phrase starts; the phrase
    actually returned is then resolved by asking each phrase, in the caller's
    order, to match AT that position. `Pattern.match(s, pos)` still lets the
    lookbehind see the characters before `pos`, which a slice would not.
    Measured: 8.67x on this reader, and byte-identical value, source, confidence
    and evidence over 2,000 real postings.
    """
    if not phrases:
        return None
    rung = compile_patterns(phrases)
    found = rung.matches_in(field)
    if not found:
        return None
    position = found[0].start()
    for phrase in phrases:
        match = compile_patterns((phrase,)).regex.match(field.folded, position)
        if match is not None:
            return match
    # The alternation matched where no single phrase does. That cannot happen
    # while both are built by `compile_patterns` from the same strings; if it
    # ever does, the slow reader decides rather than a wrong answer being
    # returned.
    return _first_match(field.folded, phrases)


def _quote(field: FoldedText, match: re.Match[str]) -> str:
    """The posting's own words around a match found in the folded copy."""
    start = field.offsets[match.start()]
    end = field.offsets[match.end() - 1] + 1
    return sentence_at(field.original, start, end)


def read_seniority(
    title: str | FoldedText,
    description: str | FoldedText,
) -> SeniorityReading:
    """The level of the advertised role, and where that came from.

    Five rules in descending order of evidence quality. The first that answers
    wins; nothing below it is consulted.

    1. an organisational role in the TITLE          -> LEAD
    2. an explicit grade in the TITLE
    3. a role-level statement in the DESCRIPTION
    4. an unambiguous required minimum of YEARS     -> never LEAD
    5. nothing                                      -> MID, source DEFAULT

    Pure, and cheap enough to run over the whole corpus: the two fields are
    folded once by the caller in the ordinary path.
    """
    title_field = title if isinstance(title, FoldedText) else fold_field(title)
    body = description if isinstance(description, FoldedText) else fold_field(description)

    # --- 1 and 2: the title, with the job-family names cut out ------------
    masked = _masked(title_field)

    if masked.strip():
        found = _first_match(masked, LEADERSHIP_ABSOLUTE) or _first_match(
            masked, LEADERSHIP_FUNCTION_FORMS
        )
        if found is not None:
            return SeniorityReading(
                value=Seniority.LEAD,
                source=SenioritySource.TITLE_LEADERSHIP,
                confidence=AnalysisConfidence.HIGH,
                evidence=_quote(title_field, found),
            )

        for level, aliases in TITLE_GRADES:
            found = _first_match(masked, aliases)
            if found is not None:
                return SeniorityReading(
                    value=level,
                    source=SenioritySource.TITLE_GRADE,
                    confidence=AnalysisConfidence.HIGH,
                    evidence=_quote(title_field, found),
                )

    # --- 3: the body, through role-level statements only ------------------
    if body.folded:
        for level, anchors in DESCRIPTION_ANCHORS:
            found = _first_match_in(body, anchors)
            if found is not None:
                return SeniorityReading(
                    value=level,
                    source=SenioritySource.DESCRIPTION_STATEMENT,
                    confidence=AnalysisConfidence.HIGH,
                    evidence=_quote(body, found),
                )

    # --- 4: arithmetic over a stated requirement --------------------------
    years = required_years(body.original)
    if years is not None:
        count, quote = years
        return SeniorityReading(
            value=_band_for_years(count),
            source=SenioritySource.REQUIRED_YEARS,
            confidence=AnalysisConfidence.MEDIUM,
            evidence=quote,
        )

    # --- 5: the posting did not say ---------------------------------------
    return DEFAULT_SENIORITY

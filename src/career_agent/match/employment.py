"""How the worker is engaged, and whether the job is domestic to one country.

Three readings, all of them evidence about the POSTING, none of them a verdict
about the candidate. Each carries the quote it fired on, verified the way
ADR-0002 requires of every other quote in this system.

**Explicit outranks contextual, always.** The rule this module exists to keep
is that a benefit is not a statement. Offering `plano de saude` in Brazil
usually accompanies employment under the CLT, and saying so is useful; saying
"the employer states CLT" when the employer stated a health plan is inventing
text. So the reading carries an `EmploymentSource`, and the interface renders
`is_explicit` differently. The same rule runs the other way: a posting that
writes `contratacao PJ` and also offers a health plan is PJ, because it said
so, and no accumulation of benefit signals may outvote that.

**A 401(k) is not a refusal.** 4,470 postings in the corpus offer one, 21 per
cent of it, and 3,515 of those never mention international hiring in any form.
For a candidate living in Brazil that is worth knowing, and it is emphatically
not the employer having said no: 3,323 of them are `UNRESOLVED` on eligibility
and stay that way. `DomesticContext` is a separate axis for exactly this
reason, and `evaluate_gates` never reads it. What it changes is what the card
says, not whether the posting is eligible.

**Absence is not a reading.** Silence produces `UNRESOLVED` with no quote,
never a value with an invented one, and never a default that a later component
can mistake for evidence.
"""

from __future__ import annotations

import re

from career_agent.domain.enums import (
    AnalysisConfidence,
    DomesticContext,
    EmploymentRelationship,
    EmploymentSource,
    LocalContractRegime,
)
from career_agent.domain.matching import (
    UNRESOLVED_DOMESTIC,
    UNRESOLVED_EMPLOYMENT,
    DomesticReading,
    EmploymentReading,
)
from career_agent.match.text import FoldedText, fold_field, sentence_at

# =========================================================================
# Explicit engagement statements
# =========================================================================

#: Phrases in which an employer NAMES the engagement. Ordered most specific
#: first within each group, because the quote shown is the phrase that matched
#: and a longer one reads better.
#:
#: `\bPJ\b` is deliberately absent from the loose end of this list and appears
#: only in anchored forms. Measured on the corpus: bare `PJ` matched exactly
#: once in 21,202 postings, and that once was Nubank describing a customer
#: segment. Two letters are not a contract.
_EXPLICIT_PJ = (
    r"contrata[çc][ãa]o\s+pj",
    r"regime\s+pj",
    r"modelo\s+pj",
    r"prestador\s+pj",
    r"prestador\s+de\s+servi[çc]os\s+pj",
    r"pessoa\s+jur[íi]dica",
    r"\bpj\b(?=\s*(?:/|\bou\b|\be\b)?\s*(?:contrato|contrata|regime|full[- ]?time))",
)

_EXPLICIT_CLT = (
    r"contrata[çc][ãa]o\s+clt",
    r"regime\s+clt",
    r"modelo\s+clt",
    r"carteira\s+assinada",
    r"registro\s+em\s+carteira",
    r"\bclt\b",
)

_EXPLICIT_EOR = (
    r"employer\s+of\s+record",
    r"\beor\b",
    r"via\s+deel",
    r"through\s+an?\s+employer\s+of\s+record",
)

_EXPLICIT_CONTRACTOR = (
    r"independent\s+contractor",
    r"\bb2b\s+contract",
    r"contractor\s+agreement",
    r"as\s+a\s+contractor",
)

_EXPLICIT_INTERN = (r"\binternship\b", r"\best[áa]gio\b", r"\bintern\s+program\b")
_EXPLICIT_APPRENTICE = (r"\bapprenticeship\b", r"\bjovem\s+aprendiz\b", r"\baprendiz\b")
_EXPLICIT_TEMPORARY = (
    r"\btemporary\s+(?:contract|position|role)\b",
    r"\bcontrato\s+tempor[áa]rio\b",
)


# =========================================================================
# Benefit context -- a likelihood, never a statement
# =========================================================================

#: Brazilian employee benefits. The owner's product knowledge, recorded as
#: what it is: in Brazil a health plan usually accompanies CLT employment.
#: "Usually" is the whole content of the claim, so this can only ever produce
#: `BENEFIT_CONTEXT`.
_BR_EMPLOYEE_BENEFITS: tuple[tuple[str, str], ...] = (
    (r"plano\s+de\s+sa[úu]de", "plano de saude"),
    (r"assist[êe]ncia\s+m[ée]dica", "assistencia medica"),
    (r"conv[êe]nio\s+m[ée]dico", "convenio medico"),
    (r"seguro\s+sa[úu]de", "seguro saude"),
    (r"plano\s+odontol[óo]gico", "plano odontologico"),
    (r"vale\s+alimenta[çc][ãa]o", "vale alimentacao"),
    (r"vale\s+refei[çc][ãa]o", "vale refeicao"),
    (r"vale\s+transporte", "vale transporte"),
    (r"13[º°o]\s*sal[áa]rio", "13o salario"),
    (r"\bfgts\b", "FGTS"),
    (r"\binss\b", "INSS"),
    (r"\bplr\b", "PLR"),
    (r"aux[íi]lio\s+creche", "auxilio creche"),
)

#: United States employee benefits. A 401(k) is a plan defined by the US
#: Internal Revenue Code and offered to employees on a US payroll, which is why
#: it carries information about where the employment sits.
_US_EMPLOYEE_BENEFITS: tuple[tuple[str, str], ...] = (
    (r"401\s*\(?\s*k\s*\)?", "401(k)"),
    (r"\bw-?2\b", "W-2"),
    (r"\bhsa\b", "HSA"),
    (r"\bfsa\b", "FSA"),
    (r"us\s+payroll", "US payroll"),
    (r"u\.s\.\s+payroll", "US payroll"),
    (r"\bmedicare\b", "Medicare"),
)

#: Text that says hiring reaches beyond one country. Any of these disables the
#: domestic reading, because an employer describing international hiring has
#: told you more than a benefit list did.
_INTERNATIONAL: tuple[tuple[str, str], ...] = (
    (r"\bworldwide\b", "worldwide"),
    (r"work\s+from\s+anywhere", "work from anywhere"),
    (r"anywhere\s+in\s+the\s+world", "anywhere in the world"),
    (r"global(?:ly)?\s+remote", "globally remote"),
    (r"remote\s*[,-]?\s*global", "remote, global"),
    (r"international\s+candidates", "international candidates"),
    (r"hir(?:e|ing)\s+globally", "hiring globally"),
    (r"\blatam\b", "LATAM"),
    (r"\bbrazil\b", "Brazil"),
    (r"\bbrasil\b", "Brasil"),
    (r"employer\s+of\s+record", "Employer of Record"),
    (r"\beor\b", "EOR"),
    (r"contractors?\s+outside\s+(?:the\s+)?u\.?\s?s\.?", "contractors outside the US"),
    (r"outside\s+(?:of\s+)?the\s+united\s+states", "outside the United States"),
    (r"outside\s+(?:of\s+)?the\s+u\.?s\.?", "outside the US"),
    (r"any\s+country", "any country"),
)


def _quote(field: FoldedText, match: re.Match[str]) -> str:
    """The posting's own words around a match found in the folded copy."""
    start = field.offsets[match.start()]
    end = field.offsets[match.end() - 1] + 1
    return sentence_at(field.original, start, end)


def _first(field: FoldedText, patterns: tuple[str, ...]) -> re.Match[str] | None:
    for pattern in patterns:
        found = re.search(pattern, field.folded)
        if found is not None:
            return found
    return None


def _first_labelled(
    field: FoldedText, patterns: tuple[tuple[str, str], ...]
) -> tuple[re.Match[str], str] | None:
    for pattern, label in patterns:
        found = re.search(pattern, field.folded)
        if found is not None:
            return found, label
    return None


def _as_field(text: str | FoldedText) -> FoldedText:
    return text if isinstance(text, FoldedText) else fold_field(text)


def _declared(relationship: str | None, regime: str | None) -> EmploymentReading | None:
    """The board's own contract-type answer as a reading, or None.

    Defensive about its inputs on purpose. These two strings cross a layer
    boundary -- `career_agent.match` may not import `career_agent.providers`,
    so what arrives is a bare string rather than an enum -- and a value that is
    not in the vocabulary must produce nothing rather than an exception on a
    rescore of 60,000 postings.

    A relationship is required and a regime is optional, which is the shape of
    the fact: `FullTime` says the worker is an employee and says nothing about
    which statute employs them.
    """
    if not relationship:
        return None
    try:
        engaged = EmploymentRelationship(relationship)
    except ValueError:
        return None
    statute = LocalContractRegime.UNRESOLVED
    if regime:
        try:
            statute = LocalContractRegime(regime)
        except ValueError:
            statute = LocalContractRegime.UNRESOLVED
    return EmploymentReading(
        relationship=engaged,
        regime=statute,
        source=EmploymentSource.STRUCTURED_FIELD,
        confidence=AnalysisConfidence.HIGH,
        #: No quote, and that is the honest answer rather than a missing one.
        evidence=None,
    )


def read_employment(
    description: str | FoldedText,
    *,
    declared_relationship: str | None = None,
    declared_regime: str | None = None,
) -> EmploymentReading:
    """How the worker is engaged, and where that reading came from.

    Precedence, and the order is the argument:

    0. THE BOARD'S OWN CONTRACT-TYPE FIELD, when the employer filled one in.
       It decides, and the description is not consulted -- the same rule
       `evaluate_gates` had to learn about hiring scope, where consulting
       prose alongside a declared field produced two verdicts for one fact.
       An employer picking `vacancy_legal_entity` from a dropdown has said PJ;
       the word `PJ` further down a benefits list has not said anything like as
       much. The reading carries NO QUOTE, because a dropdown value is not a
       contiguous substring of the posting and ADR-0002 means it is therefore
       not evidence. `StructuredGeography` set that precedent.
    1. an explicit contractor or PJ statement. Checked FIRST, and before CLT,
       because a posting that offers a health plan AND says PJ is PJ. Letting
       the benefit sweep run first would have produced the exact error this
       module is written to prevent.
    2. an explicit EOR, intern, apprentice or temporary statement.
    3. an explicit CLT statement -> EMPLOYEE under the CLT regime.
    4. Brazilian employee benefits -> EMPLOYEE, `BENEFIT_CONTEXT`, MEDIUM. The
       regime stays `UNRESOLVED`: a health plan suggests employment, and
       naming the statute on its behalf is a second invention on top of the
       first.
    5. nothing -> `UNRESOLVED`, with no quote.

    An UNRECOGNISED declared value is not a refusal and does not shortcut
    anything: the caller passes None for it, prose is read as before, and the
    posting is exactly where it was. That asymmetry is the Jabalpur rule, and
    it is why `vacancy_type_talent_pool` costs nothing.
    """
    structured = _declared(declared_relationship, declared_regime)
    if structured is not None:
        return structured

    field = _as_field(description)

    contractor = _first(field, _EXPLICIT_PJ)
    if contractor is not None:
        return EmploymentReading(
            relationship=EmploymentRelationship.CONTRACTOR_B2B,
            regime=LocalContractRegime.PJ,
            source=EmploymentSource.EXPLICIT_STATEMENT,
            confidence=AnalysisConfidence.HIGH,
            evidence=_quote(field, contractor),
        )

    b2b = _first(field, _EXPLICIT_CONTRACTOR)
    if b2b is not None:
        return EmploymentReading(
            relationship=EmploymentRelationship.CONTRACTOR_B2B,
            regime=LocalContractRegime.UNRESOLVED,
            source=EmploymentSource.EXPLICIT_STATEMENT,
            confidence=AnalysisConfidence.HIGH,
            evidence=_quote(field, b2b),
        )

    for patterns, relationship in (
        (_EXPLICIT_EOR, EmploymentRelationship.EOR),
        (_EXPLICIT_INTERN, EmploymentRelationship.INTERN),
        (_EXPLICIT_APPRENTICE, EmploymentRelationship.APPRENTICE),
        (_EXPLICIT_TEMPORARY, EmploymentRelationship.TEMPORARY),
    ):
        found = _first(field, patterns)
        if found is not None:
            return EmploymentReading(
                relationship=relationship,
                regime=LocalContractRegime.UNRESOLVED,
                source=EmploymentSource.EXPLICIT_STATEMENT,
                confidence=AnalysisConfidence.HIGH,
                evidence=_quote(field, found),
            )

    clt = _first(field, _EXPLICIT_CLT)
    if clt is not None:
        return EmploymentReading(
            relationship=EmploymentRelationship.EMPLOYEE,
            regime=LocalContractRegime.CLT,
            source=EmploymentSource.EXPLICIT_STATEMENT,
            confidence=AnalysisConfidence.HIGH,
            evidence=_quote(field, clt),
        )

    benefit = _first_labelled(field, _BR_EMPLOYEE_BENEFITS)
    if benefit is not None:
        found, _label = benefit
        return EmploymentReading(
            relationship=EmploymentRelationship.EMPLOYEE,
            regime=LocalContractRegime.UNRESOLVED,
            source=EmploymentSource.BENEFIT_CONTEXT,
            confidence=AnalysisConfidence.MEDIUM,
            evidence=_quote(field, found),
        )

    return UNRESOLVED_EMPLOYMENT


def read_domestic_context(description: str | FoldedText) -> DomesticReading:
    """Whether the posting reads as employment inside the United States only.

    1. any international hiring language -> `INTERNATIONAL_STATED`. Checked
       first, so `401(k)` alongside "work from anywhere" never produces a
       domestic reading. The employer described its reach; a benefit did not.
    2. a United States employee benefit and no such language ->
       `LIKELY_US_DOMESTIC`, MEDIUM. Never HIGH: this is an inference from how
       the compensation is structured, and the posting has not been asked.
    3. nothing -> `UNRESOLVED`.

    An explicit United States restriction is NOT read here. That is stated
    eligibility text and belongs to the geography gate, which can fail a
    posting; this reading never can. A posting saying "US only" therefore
    arrives here as whatever its benefits say, and the gate is what the
    interface leads with.
    """
    field = _as_field(description)

    international = _first_labelled(field, _INTERNATIONAL)
    if international is not None:
        found, label = international
        return DomesticReading(
            context=DomesticContext.INTERNATIONAL_STATED,
            confidence=AnalysisConfidence.MEDIUM,
            evidence=_quote(field, found),
            signal=label,
        )

    benefit = _first_labelled(field, _US_EMPLOYEE_BENEFITS)
    if benefit is not None:
        found, label = benefit
        return DomesticReading(
            context=DomesticContext.LIKELY_US_DOMESTIC,
            confidence=AnalysisConfidence.MEDIUM,
            evidence=_quote(field, found),
            signal=label,
        )

    return UNRESOLVED_DOMESTIC

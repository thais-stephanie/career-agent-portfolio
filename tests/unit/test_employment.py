"""How the worker is engaged, and whether the job is domestic to one country.

The tests are organised around the one rule that makes this module worth
having: an EXPLICIT statement and a CONTEXTUAL likelihood are different kinds
of thing, and the second may never be rendered as the first.
"""

from __future__ import annotations

import pytest

from career_agent.domain.enums import (
    AnalysisConfidence,
    DomesticContext,
    EmploymentRelationship,
    EmploymentSource,
    LocalContractRegime,
)
from career_agent.match.employment import read_domestic_context, read_employment

# =========================================================================
# 1. EXPLICIT ENGAGEMENT
# =========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Vaga efetiva, regime CLT.",
        "Contratacao CLT com todos os beneficios.",
        "Modelo CLT, home office tres vezes por semana.",
        "Oferecemos carteira assinada desde o primeiro dia.",
        "Registro em carteira e plano de saude.",
        "Contrato CLT full-time.",
    ],
)
def test_an_employer_that_names_the_clt_has_said_so(text: str) -> None:
    reading = read_employment(text)
    assert reading.relationship is EmploymentRelationship.EMPLOYEE
    assert reading.regime is LocalContractRegime.CLT
    assert reading.source is EmploymentSource.EXPLICIT_STATEMENT
    assert reading.is_explicit
    assert reading.evidence is not None


@pytest.mark.parametrize(
    "text",
    [
        "Contratacao PJ.",
        "Regime PJ, sem beneficios.",
        "Modelo PJ com contrato de 12 meses.",
        "Buscamos prestador PJ.",
        "Contratacao como Pessoa Juridica.",
    ],
)
def test_an_employer_that_names_pj_has_said_so(text: str) -> None:
    reading = read_employment(text)
    assert reading.relationship is EmploymentRelationship.CONTRACTOR_B2B
    assert reading.regime is LocalContractRegime.PJ
    assert reading.is_explicit


def test_pj_wins_over_a_health_plan_in_the_same_posting() -> None:
    """The case the ordering exists for.

    A benefit sweep running before the explicit check would read this as an
    employee, which is the employer being contradicted by its own perks list.
    """
    reading = read_employment("Contratacao PJ. Oferecemos plano de saude e vale refeicao.")
    assert reading.relationship is EmploymentRelationship.CONTRACTOR_B2B
    assert reading.regime is LocalContractRegime.PJ
    assert reading.is_explicit


def test_an_independent_contractor_is_not_given_a_brazilian_regime() -> None:
    """A relationship without a named statute leaves the statute alone.

    An American posting saying "independent contractor" is a contractor. It is
    not PJ, which is a Brazilian arrangement, and stamping one on it would be
    this system inventing a jurisdiction.
    """
    reading = read_employment("This role is engaged as an independent contractor.")
    assert reading.relationship is EmploymentRelationship.CONTRACTOR_B2B
    assert reading.regime is LocalContractRegime.UNRESOLVED


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We hire through an Employer of Record.", EmploymentRelationship.EOR),
        ("A 6-month internship with a mentor.", EmploymentRelationship.INTERN),
        ("Vaga de estagio para estudantes.", EmploymentRelationship.INTERN),
        ("Programa de jovem aprendiz.", EmploymentRelationship.APPRENTICE),
        ("This is a temporary contract covering parental leave.", EmploymentRelationship.TEMPORARY),
    ],
)
def test_the_other_named_relationships(text: str, expected: EmploymentRelationship) -> None:
    reading = read_employment(text)
    assert reading.relationship is expected
    assert reading.is_explicit


# =========================================================================
# 2. BENEFIT CONTEXT IS A LIKELIHOOD
# =========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Oferecemos plano de saude e vale refeicao.",
        "Beneficios: assistencia medica, vale transporte.",
        "Convenio medico e plano odontologico.",
        "Seguro saude para voce e dependentes.",
        "Vale alimentacao mensal.",
        "13o salario e ferias remuneradas.",
        "Deposito de FGTS.",
        "Auxilio creche.",
    ],
)
def test_an_employee_benefit_suggests_employment_without_claiming_a_statute(text: str) -> None:
    """The owner's product knowledge, held to exactly what it says.

    A health plan in Brazil usually accompanies CLT employment. "Usually" is
    the whole content of the claim, so the relationship moves and the REGIME
    does not: naming the statute on the employer's behalf would be a second
    invention stacked on the first.
    """
    reading = read_employment(text)
    assert reading.relationship is EmploymentRelationship.EMPLOYEE
    assert reading.regime is LocalContractRegime.UNRESOLVED
    assert reading.source is EmploymentSource.BENEFIT_CONTEXT
    assert not reading.is_explicit
    assert reading.confidence is AnalysisConfidence.MEDIUM


def test_a_benefit_reading_is_never_reported_as_explicit() -> None:
    """The single assertion this whole module exists to make true."""
    reading = read_employment("Plano de saude, vale refeicao, vale transporte e PLR.")
    assert reading.is_explicit is False


def test_silence_produces_no_reading_and_no_quote() -> None:
    reading = read_employment("We build data pipelines with Python and dbt.")
    assert reading.relationship is EmploymentRelationship.UNRESOLVED
    assert reading.regime is LocalContractRegime.UNRESOLVED
    assert reading.source is EmploymentSource.DEFAULT
    assert reading.evidence is None


def test_two_letters_are_not_a_contract() -> None:
    """Bare `PJ` matched once in 21,202 postings, describing a customer segment.

    So the loose form is not in the vocabulary at all, and prose that happens
    to contain those letters reads as nothing.
    """
    reading = read_employment("Nubank serves PJ customers across Brazil.")
    assert reading.relationship is not EmploymentRelationship.CONTRACTOR_B2B


def test_the_quote_is_the_postings_own_words() -> None:
    text = "Sobre a vaga. Contratacao CLT com plano de saude. Trabalho remoto."
    reading = read_employment(text)
    assert reading.evidence is not None
    assert reading.evidence in text, "the quote is not a contiguous substring of the posting"


# =========================================================================
# 3. DOMESTIC CONTEXT IS NOT A REFUSAL
# =========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "We offer a competitive 401(k) with company match.",
        "Benefits include 401k, medical, dental and vision.",
        "You will be a W-2 employee.",
        "We offer an HSA and commuter benefits.",
        "Paid through US payroll.",
    ],
)
def test_a_us_employee_benefit_with_no_international_language_reads_as_domestic(
    text: str,
) -> None:
    reading = read_domestic_context(text)
    assert reading.context is DomesticContext.LIKELY_US_DOMESTIC
    assert reading.confidence is AnalysisConfidence.MEDIUM
    assert reading.evidence is not None
    assert reading.signal is not None


@pytest.mark.parametrize(
    "text",
    [
        "Remote, worldwide. We offer a 401(k) to US employees.",
        "Work from anywhere. 401(k) available.",
        "We hire globally and offer a 401(k) where applicable.",
        "Open to international candidates. 401k for US staff.",
        "We hire across LATAM. US employees get a 401(k).",
        "Contractors outside the US are welcome; 401(k) for US staff.",
        "We hire through an Employer of Record. 401(k) in the US.",
    ],
)
def test_international_hiring_language_outranks_a_benefit(text: str) -> None:
    """The false positive that would have made this feature harmful.

    890 of the 4,470 postings offering a 401(k) also describe international
    hiring. Calling those US-only would have hidden the best openings this
    candidate has from her, using her own tooling.
    """
    reading = read_domestic_context(text)
    assert reading.context is DomesticContext.INTERNATIONAL_STATED


def test_a_401k_is_never_an_eligibility_verdict() -> None:
    """`DomesticContext` has no member that refuses anybody.

    The vocabulary itself is the guarantee: there is no `NOT_ELIGIBLE` here to
    reach for, and `EligibilityStatus` remains the only place a refusal lives.
    """
    assert not any("NOT_ELIGIBLE" in member.value for member in DomesticContext)
    assert set(DomesticContext) == {
        DomesticContext.LIKELY_US_DOMESTIC,
        DomesticContext.INTERNATIONAL_STATED,
        DomesticContext.UNRESOLVED,
    }


def test_silence_about_benefits_and_geography_reads_as_unresolved() -> None:
    reading = read_domestic_context("We build data pipelines with Python and dbt.")
    assert reading.context is DomesticContext.UNRESOLVED
    assert reading.evidence is None
    assert reading.signal is None


def test_the_domestic_quote_is_the_postings_own_words() -> None:
    text = "About us. We offer a competitive 401(k) with match. Apply today."
    reading = read_domestic_context(text)
    assert reading.evidence is not None
    assert reading.evidence in text

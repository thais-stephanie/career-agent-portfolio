"""The board's own contract-type field, read as an engagement.

`contract_regime` has been a column since migration 0020 and NULL on every
scored row in the corpus ever since. The measurement that explained why was
taken twice, in V1.2 and again on 2026-09-06: over 21,202 descriptions, `CLT`
appeared zero times and every Portuguese and English spelling of PJ appeared
zero times except one, which was Nubank naming a customer segment. **The
blocker was never the reader.** No source in the corpus published the field.

Gupy publishes it, as a closed list the employer picks from, and this is the
path from that dropdown to the column.

The two things it must not do:

* claim a national statute from a word that is not that statute's vocabulary
  -- `FullTime` on a Denver posting is not the CLT;
* claim a QUOTE. The value is not in the posting text, so ADR-0002 makes it
  not evidence, and the reading carries None the way `StructuredGeography`
  does.
"""

from __future__ import annotations

import pytest

from career_agent.domain.enums import (
    EmploymentRelationship,
    EmploymentSource,
    LocalContractRegime,
)
from career_agent.domain.provider_values import normalise_engagement
from career_agent.match.employment import read_employment

# -- the table ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "relationship", "regime"),
    [
        # Gupy's Brazilian statute vocabulary. Counts are the corpus on
        # 2026-09-09, over 36,925 archived Gupy payloads.
        ("vacancy_type_effective", EmploymentRelationship.EMPLOYEE, LocalContractRegime.CLT),
        (
            "vacancy_legal_entity",
            EmploymentRelationship.CONTRACTOR_B2B,
            LocalContractRegime.PJ,
        ),
        (
            "vacancy_type_apprentice",
            EmploymentRelationship.APPRENTICE,
            LocalContractRegime.CLT,
        ),
        (
            "vacancy_type_intermittent",
            EmploymentRelationship.EMPLOYEE,
            LocalContractRegime.CLT,
        ),
        # Stated relationship, and deliberately NO regime.
        (
            "vacancy_type_internship",
            EmploymentRelationship.INTERN,
            LocalContractRegime.UNRESOLVED,
        ),
        (
            "vacancy_type_temporary",
            EmploymentRelationship.TEMPORARY,
            LocalContractRegime.UNRESOLVED,
        ),
        (
            "vacancy_type_autonomous",
            EmploymentRelationship.CONTRACTOR_B2B,
            LocalContractRegime.UNRESOLVED,
        ),
        # The portable spellings every ATS uses.
        ("FullTime", EmploymentRelationship.EMPLOYEE, LocalContractRegime.UNRESOLVED),
        (
            "CONTRACTOR",
            EmploymentRelationship.CONTRACTOR_B2B,
            LocalContractRegime.UNRESOLVED,
        ),
        ("Intern", EmploymentRelationship.INTERN, LocalContractRegime.UNRESOLVED),
    ],
)
def test_a_declared_value_says_what_it_says_and_no_more(
    raw: str, relationship: EmploymentRelationship, regime: LocalContractRegime
) -> None:
    engagement = normalise_engagement(raw)
    assert engagement is not None, raw
    assert engagement.relationship is relationship
    assert engagement.regime is regime


def test_a_permanent_job_in_denver_is_not_a_clt_contract() -> None:
    """The single most tempting wrong entry in that table.

    `vacancy_type_effective` means efetivo, and in Gupy's vocabulary -- which
    sits alongside jovem aprendiz and contrato intermitente -- efetivo is the
    CLT. `FullTime` is the same idea in a vocabulary that belongs to no
    country, and thousands of postings in this corpus carry it from American
    boards. Mapping the second the way the first is mapped would put a
    Brazilian statute on a Colorado job.
    """
    brazilian = normalise_engagement("vacancy_type_effective")
    portable = normalise_engagement("FullTime")
    assert brazilian is not None and portable is not None
    assert brazilian.relationship is portable.relationship
    assert brazilian.regime is LocalContractRegime.CLT
    assert portable.regime is LocalContractRegime.UNRESOLVED


def test_a_talent_pool_is_not_a_contract_type() -> None:
    """1,158 rows in the corpus, and the honest answer is nothing.

    A banco de talentos is a standing invitation to be considered later. It is
    COLLECTED, because deciding what somebody wants to see belongs to the
    filter layer and not to a collector, and it describes no engagement.
    """
    assert normalise_engagement("vacancy_type_talent_pool") is None


@pytest.mark.parametrize(
    "raw",
    ["vacancy_type_outsource", "vacancy_type_associate", "vacancy_type_parter", "wat"],
)
def test_an_uncatalogued_spelling_yields_nothing(raw: str) -> None:
    assert normalise_engagement(raw) is None


# -- the reading -------------------------------------------------------------


def test_the_declared_field_decides_and_the_description_is_not_consulted() -> None:
    """The rule V1.5 had to learn about hiring scope, applied here in advance.

    `declared_scope or prose` behaved as "fall back to the body whenever the
    field is not the answer you wanted", and one scope string produced two
    verdicts depending on marketing copy. A posting whose employer selected
    pessoa juridica is PJ even where the body lists a health plan.
    """
    reading = read_employment(
        "Oferecemos plano de saude, vale refeicao e vale transporte.",
        declared_relationship=EmploymentRelationship.CONTRACTOR_B2B.value,
        declared_regime=LocalContractRegime.PJ.value,
    )
    assert reading.relationship is EmploymentRelationship.CONTRACTOR_B2B
    assert reading.regime is LocalContractRegime.PJ
    assert reading.source is EmploymentSource.STRUCTURED_FIELD


def test_a_structured_reading_carries_no_quote_and_is_still_explicit() -> None:
    """Two assertions that pull in opposite directions, and both are the point.

    The employer SAID this, so the interface must not render it as a guess the
    way it renders a health plan. And there is nothing to quote, because a
    dropdown value is not a contiguous substring of the posting -- so inventing
    an evidence string would be the first lie in a chain of them.
    """
    reading = read_employment(
        "We are hiring.",
        declared_relationship=EmploymentRelationship.EMPLOYEE.value,
        declared_regime=LocalContractRegime.CLT.value,
    )
    assert reading.is_explicit is True
    assert reading.evidence is None


def test_nothing_declared_leaves_the_prose_reader_exactly_where_it_was() -> None:
    reading = read_employment("Contratacao PJ para o projeto.")
    assert reading.regime is LocalContractRegime.PJ
    assert reading.source is EmploymentSource.EXPLICIT_STATEMENT
    assert reading.evidence is not None


@pytest.mark.parametrize(
    ("relationship", "regime"),
    [(None, "CLT"), ("", "CLT"), ("NOT_A_MEMBER", None), ("EMPLOYEE", "NOT_A_MEMBER")],
)
def test_a_value_outside_the_vocabulary_never_raises_on_a_rescore(
    relationship: str | None, regime: str | None
) -> None:
    """These two strings cross a layer boundary as bare text.

    `career_agent.match` may not import `career_agent.providers`, so what
    arrives here is a string rather than an enum, and a rescore walks tens of
    thousands of postings in one pass. A value nobody catalogued must degrade
    to the prose reading rather than end the run.
    """
    reading = read_employment(
        "Contratacao CLT com carteira assinada.",
        declared_relationship=relationship,
        declared_regime=regime,
    )
    assert reading.relationship is EmploymentRelationship.EMPLOYEE
    if relationship == "EMPLOYEE":
        assert reading.source is EmploymentSource.STRUCTURED_FIELD
        assert reading.regime is LocalContractRegime.UNRESOLVED
    else:
        assert reading.source is EmploymentSource.EXPLICIT_STATEMENT


# -- and it buys nothing -----------------------------------------------------


def test_a_declared_engagement_moves_no_score_at_all() -> None:
    """THE POINT OF THIS BEING ITS OWN COMMIT.

    How a worker is engaged is a fact worth showing a candidate. It is
    emphatically not a compatibility signal, and `match_job` says so in a
    comment above the call: neither this reading nor the domestic one "may fail
    a posting, and neither buys or costs a single point".

    Making the reading available and letting it change the ranking are two
    changes, and shipping them together is how nobody can tell afterwards which
    one moved the numbers. So this asserts the first one alone, and the row
    that a PJ contract produces is byte-identical to the row it produced
    yesterday.
    """
    from tests.support import committed_config_dir

    from career_agent.config.search_config import load_search_config
    from career_agent.match import JobFacts, match_job

    config, _ = load_search_config(committed_config_dir())
    body = (
        "Estamos contratando uma pessoa para atuar em nosso time de vendas. "
        "Voce vai cuidar do relacionamento com clientes e do funil comercial. "
        "Oferecemos plano de saude e vale refeicao."
    )
    silent = match_job(
        config,
        JobFacts(title="Executivo de Vendas", description=body),
        computed_at="2026-09-09T00:00:00Z",
    )
    declared = match_job(
        config,
        JobFacts(
            title="Executivo de Vendas",
            description=body,
            declared_relationship=EmploymentRelationship.CONTRACTOR_B2B.value,
            declared_contract_regime=LocalContractRegime.PJ.value,
        ),
        computed_at="2026-09-09T00:00:00Z",
    )

    assert declared.employment.regime is LocalContractRegime.PJ
    assert silent.employment.regime is LocalContractRegime.UNRESOLVED

    assert declared.match_score == silent.match_score
    assert declared.data_confidence == silent.data_confidence
    assert declared.eligibility_status == silent.eligibility_status
    assert [g.result for g in declared.gates] == [g.result for g in silent.gates]


# -- the coarse vocabulary too -----------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("vacancy_type_effective", "FULL_TIME"),
        ("vacancy_legal_entity", "CONTRACT"),
        ("vacancy_type_internship", "INTERNSHIP"),
        ("vacancy_type_apprentice", "INTERNSHIP"),
        ("vacancy_type_temporary", "TEMPORARY"),
        ("vacancy_type_autonomous", "CONTRACT"),
    ],
)
def test_the_filter_chip_vocabulary_reaches_the_same_values(raw: str, expected: str) -> None:
    """40,595 postings carried a declared contract type that resolved to
    nothing, so the confidence item said the board had stated no engagement
    above a posting where the employer picked one from a dropdown."""
    from career_agent.domain.provider_values import normalise_employment_type

    assert normalise_employment_type(raw) == expected


def test_the_coarse_table_and_the_engagement_table_never_disagree() -> None:
    """They are lossy in different directions and must not contradict.

    `vacancy_legal_entity` is CONTRACT here and CONTRACTOR_B2B/PJ there; that
    is the coarse answer and the precise one, not two answers. What would be a
    defect is a spelling the coarse table calls FULL_TIME while the engagement
    table calls it a contractor, and nothing but a test stops the two drifting.
    """
    from career_agent.domain.provider_values import _ENGAGEMENTS, normalise_employment_type

    compatible = {
        "FULL_TIME": {EmploymentRelationship.EMPLOYEE},
        "PART_TIME": {EmploymentRelationship.EMPLOYEE},
        "CONTRACT": {EmploymentRelationship.CONTRACTOR_B2B},
        "TEMPORARY": {EmploymentRelationship.TEMPORARY},
        "INTERNSHIP": {EmploymentRelationship.INTERN, EmploymentRelationship.APPRENTICE},
        "VOLUNTEER": {EmploymentRelationship.OTHER},
    }
    for spelling, engagement in _ENGAGEMENTS.items():
        coarse = normalise_employment_type(spelling)
        if coarse is None:
            continue
        assert engagement.relationship in compatible[coarse], spelling

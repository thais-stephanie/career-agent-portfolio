"""An age is not experience, and both readers of a posting must agree about that.

WHY THIS FILE EXISTS
--------------------
`match/seniority.py` and `match/experience.py` both look for a figure of years in
the same paragraph. Each used to carry its own copy of the pattern, with a
comment in both saying the two spellings had to stay identical so that one
posting could not produce two different numbers.

The comment was doing a module's job, and it failed twice. Both said `anos` where
Brazilian postings write `ano`. And both read somebody's AGE as a statement about
their career: the corpus held 1,488 postings stored as requiring twelve years or
more, 863 of them at eighteen, and what those postings said was `Faixa etaria:
minimo 18 anos` and `Must be 21 years of age`.

That second one is why `match/years.py` exists rather than a third synchronised
copy, and why these tests sit beside it rather than in either reader's file.
"""

from __future__ import annotations

import pytest

from career_agent.match.years import YEARS, career_years


def _values(description: str) -> list[int]:
    return [int(m.group(1)) for m in career_years(description)]


# =========================================================================
# 1. THE FIGURE
# =========================================================================


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        ("5 years of experience", [5]),
        ("5+ years of experience", [5]),
        ("5 yrs of experience", [5]),
        ("3 anos de experiencia", [3]),
        # `ano`, singular, and the missing `?` that made both readers blind to it.
        ("1 ano na area", [1]),
        ("2 to 4 years in a similar role", [4]),
        ("no figure at all", []),
    ],
)
def test_a_figure_of_years_in_either_language(description: str, expected: list[int]) -> None:
    assert _values(description) == expected


def test_the_pattern_is_still_exported() -> None:
    """`YEARS` is named by the tests and by `match/preparation.py`."""
    assert YEARS.search("4 years") is not None


# =========================================================================
# 2. AN AGE IS WITHHELD
# =========================================================================


@pytest.mark.parametrize(
    "description",
    [
        "Faixa etaria: minimo 18 anos",
        "Must be 21 years of age",
        "Candidates must be at least 18 years old",
        "Ter entre 14 e 22 anos, conforme estabelecido pela Lei da Aprendizagem",
        "Necessario ter de 17 a 21 anos incompletos",
        "E necessario ter entre 14 e 24 anos de idade",
        "Vaga de jovem aprendiz: de 16 a 22 anos",
    ],
)
def test_an_age_is_not_a_figure_about_anybody_s_career(description: str) -> None:
    assert _values(description) == []


def test_the_veto_is_per_figure_and_clipped_to_its_sentence() -> None:
    """Two unrelated statements in one advert, and the first must not eat the second.

    This is the assertion a blind window failed. An eighty-character reach
    backwards from `5 years` lands inside `18 years of age`, so the real
    requirement disappeared along with the age.
    """
    text = "Must be 18 years of age. We require a minimum of 5 years of experience in sales."
    assert _values(text) == [5]


def test_the_word_age_inside_an_ordinary_word_withholds_nothing() -> None:
    """The word boundary, which is the most dangerous character in this module.

    `age` sits inside language, manager, package, average, coverage, percentage,
    storage, stage, message and engagement. Unanchored, this veto would not have
    removed ages -- it would have silenced both readers on nearly every posting
    ever written, and silently, because each has a legitimate "nothing was said"
    answer to fall back on.
    """
    text = (
        "We manage a package of languages across our storage, and our coverage "
        "averages well. Minimum 6 years of experience required."
    )
    assert _values(text) == [6]


def test_a_posting_can_state_an_age_band_and_a_requirement_in_two_bullets() -> None:
    text = (
        "Requirements:\n"
        "- Must be 18 years of age\n"
        "- At least 4 years of experience with industrial machinery\n"
        "- A valid licence\n"
    )
    assert _values(text) == [4]


# =========================================================================
# 3. PORTUGUESE IS READ POSITIVELY, AND THAT IS A MEASUREMENT
# =========================================================================
#
# The age anchors in section 2 are written the way English writes an age, and
# they left 390 Portuguese readings standing. Measured over the 5,284 postings
# reading REQUIRED_MINIMUM on 2026-09-10, 912 carried a Portuguese figure, and
# 390 of those sat in a sentence that never mentioned experience at all. The ten
# commonest shapes were an age, military paperwork, tenure at the CURRENT
# employer, availability for an internship, time left in a degree, how long
# somebody had held a driving licence, how old the EMPLOYER was, and the age
# range of the clothes the employer makes.
#
# So the two languages are read differently, on evidence. English names the
# subject in the figure's own phrase often enough to read negatively -- `Minimum
# 12 years of relevant customer success` never says "experience" and is plainly a
# requirement. Portuguese does not, so a Portuguese figure counts only when its
# own sentence says it is about experience, or the figure itself is followed by
# the work.
#
# The direction of the failure is the same one the rest of this module argues
# for: a withheld figure is a NOT_STATED, which shows the posting. `estagiar por
# no minimo 1 ano` read as a year of required experience is the opposite, and it
# appears on internship adverts.


@pytest.mark.parametrize(
    ("description", "what_it_really_is"),
    [
        ("Requisitos e qualificacoesTer no minimo 18 anos;Ensino Medio completo", "an age"),
        ("Ter 21 anos ou mais", "an age"),
        ("Ter entre 18 e 22 anos", "an age band"),
        ("Oportunidade para jovens de 14 a 24 anos", "an age band"),
        ("Ter no minimo 1 ano de empresa", "tenure at the current employer"),
        ("Sera necessario possuir no minimo 1 ano de admissao", "tenure"),
        ("Disponibilidade para estagiar por no minimo 1 ano", "availability"),
        ("Possuir no minimo 1 ano para concluir a sua graduacao", "time left in a degree"),
        ("CNH categoria B ha no minimo 1 ano", "a driving licence"),
        (
            "Para candidatos do sexo masculino, maiores de 18 anos, e obrigatoria a "
            "apresentacao do certificado de reservista",
            "military paperwork",
        ),
        ("Somos uma empresa com quase 40 anos de historia", "the employer's age"),
        ("Ha mais de 30 anos distribuindo qualidade e resultados", "the employer's age"),
        ("Atuamos ha 40 anos nos setores de projetos e execucoes de obras", "the employer's age"),
        ("Com 50 anos de historia, nossa trajetoria comecou", "the employer's age"),
        (
            "Criamos produtos com qualidade e conforto para meninas entre 10 e 16 anos",
            "the product",
        ),
    ],
)
def test_a_portuguese_figure_in_a_sentence_about_something_else(
    description: str, what_it_really_is: str
) -> None:
    assert _values(description) == [], what_it_really_is


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # the sentence says experience
        ("2 anos de experiencia sao desejaveis", [2]),
        ("Experiencia minima de 3 anos", [3]),
        ("5 anos ou mais de experiencia em vendas", [5]),
        # ...or says the area
        ("Minimo de 2 anos na area de atendimento ao cliente", [2]),
        ("1 ano na area", [1]),
        # ...or the figure itself is followed by the work. `em` is the one that
        # matters: it is how a requirement is written when the sentence never
        # says "experience", and it is why `Exigimos no minimo 3 anos em bancos
        # relacionais` is not thrown away with the ages.
        ("Exigimos no minimo 3 anos em bancos relacionais", [3]),
        ("2 anos como vendedor em concessionarias de veiculos", [2]),
        ("5 anos de atuacao em logistica", [5]),
        ("4 anos trabalhando com folha de pagamento", [4]),
    ],
)
def test_a_portuguese_figure_that_is_about_the_work(description: str, expected: list[int]) -> None:
    assert _values(description) == expected


def test_the_employer_s_own_age_does_not_ride_in_on_the_preposition() -> None:
    """Admitting `em` after the figure is what lets `40 anos em atividade` back in.

    A company describing its own history has a handful of fixed shapes in
    Portuguese, and naming those is narrower and safer than refusing `em` -- which
    would cost every `N anos em <field>` requirement in the market.
    """
    assert _values("Ha 50 anos em atividade no segmento") == []
    assert _values("Somos uma empresa de 40 anos em atividade") == []
    assert _values("Exigimos 4 anos em atividades de auditoria") == [4]


def test_a_later_bullet_cannot_vouch_for_an_earlier_figure() -> None:
    """A bullet is a sentence, which is why `SENTENCE_BREAKS` holds the semicolon.

    `Ter no minimo 18 anos;Ensino Medio completo;Ter experiencia de 3 anos em
    vendas` is three statements. The age must not be rescued by the third one,
    and the third one must still be read.
    """
    text = "Ter no minimo 18 anos;Ensino Medio completo;Ter experiencia de 3 anos em vendas"
    assert _values(text) == [3]


def test_english_is_not_read_positively_and_that_is_deliberate() -> None:
    """The measurement that decided this.

    `Minimum 12 years of relevant customer success, strategy consulting` does not
    contain the word experience. 4,372 of the 5,284 measured minima were written
    in English, and a positive rule applied to them would have thrown away the
    genuine ones along with nothing.
    """
    assert _values("- Minimum 12 years of relevant customer success, strategy consulting") == [12]

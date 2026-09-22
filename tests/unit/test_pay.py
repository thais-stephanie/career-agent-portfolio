"""Money in a posting, and the far larger amount of money that is not a salary.

WHY THIS FILE EXISTS
--------------------
Compensation reached `JobFacts` from one place: the provider's archived payload.
A board with a structured pay field was understood; an employer who wrote the
figure in the advert was not.

Measured over the corpus on 2026-09-10, the second is most of the market.
**10,958 postings put a currency amount within seventy characters of a pay
anchor**, against 4,858 rows that carried a structured field. Greenhouse writes
its band into the description in plain sight:

    salary range: $130,100 - $187,000 USD

And Brazil is the extreme case: 78,806 postings located there, and **37 rows in
the whole corpus with a salary in reais**, because no Brazilian board here
publishes a structured pay field.

THE HARD HALF IS THE REFUSALS
-----------------------------
A reader that took the first currency amount in a description would have
reported the employer's own revenue as the candidate's salary. `We have
surpassed $400M in ARR` appears in thousands of postings, and so do a 401(k)
match, a funding round and a market-size claim. Half the parametrised cases here
are things this must NOT read, which is the right proportion: a wrong salary on
screen is a number the employer never wrote about a person's livelihood.
"""

from __future__ import annotations

import pytest

from career_agent.match.pay import read_pay


def _band(text: str) -> tuple[float, float, str, str | None] | None:
    r = read_pay(text)
    return None if r is None else (r.min_value, r.max_value, r.currency, r.period)


# =========================================================================
# 1. THE FORMS THE CORPUS ACTUALLY CONTAINS
# =========================================================================


@pytest.mark.parametrize(
    ("description", "expected"),
    [
        # Greenhouse, verbatim shapes from the corpus.
        (
            "The salary range: $130,100 - $187,000 USD A note on pay",
            (130100.0, 187000.0, "USD", None),
        ),
        ("salary range: $25.77 - $37.02 USD per hour", (25.77, 37.02, "USD", "HOUR")),
        # A TRAILING CODE THAT IS NOT DOLLARS, and reading it as dollars would
        # understate this band by a third.
        ("salary range: $56,560 - $66,500 CAD", (56560.0, 66500.0, "CAD", None)),
        # Brazil, which had 37 salaries in the whole corpus before this.
        ("Salario: R$ 4.500", (4500.0, 4500.0, "BRL", None)),
        ("Salario R$ 4.500,00 por mes", (4500.0, 4500.0, "BRL", "MONTH")),
        ("Faixa salarial: R$ 3.000 a R$ 5.000", (3000.0, 5000.0, "BRL", None)),
        ("Remuneracao: R$ 8.000,00 mensal", (8000.0, 8000.0, "BRL", "MONTH")),
        # Other real shapes.
        ("Salary $90k per year", (90000.0, 90000.0, "USD", "YEAR")),
        ("Compensation: EUR 60k - EUR 75k per year", (60000.0, 75000.0, "EUR", "YEAR")),
        ("Base pay: $30/hour", (30.0, 30.0, "USD", "HOUR")),
        (
            "Pay range: CAD 90,000 - CAD 110,000 annually",
            (90000.0, 110000.0, "CAD", "YEAR"),
        ),
        ("Annual salary: £55,000 - £70,000", (55000.0, 70000.0, "GBP", "YEAR")),
    ],
)
def test_the_forms_this_market_writes(
    description: str, expected: tuple[float, float, str, str | None]
) -> None:
    assert _band(description) == expected


def test_a_code_before_the_number_is_a_band_and_not_its_floor() -> None:
    """`EUR 60k - EUR 75k` broke the first version, silently and in one direction.

    The amount pattern knew symbols and not codes, so it matched `60k`, failed to
    reach the separator, and fell through to the single-figure branch -- reporting
    a range as its own minimum. A salary reported low is worse than none: it
    reads as a real offer.
    """
    r = read_pay("Compensation: EUR 60k - EUR 75k per year")
    assert r is not None
    assert (r.min_value, r.max_value) == (60000.0, 75000.0)


def test_between_x_and_y_is_a_band_and_not_its_floor() -> None:
    """The separator list without `and` reported thousands of bands as their floor.

    `salary for this role is between $113,000 and $153,000` is how one whole ATS
    family's customers write it, measured on the corpus. Without `and` the
    pattern matched the first figure only and reported 113,000 as both ends --
    and a salary reported low is worse than no salary, because it reads as a real
    offer somebody might turn down a better one for.
    """
    r = read_pay("The salary for this role is between $113,000 and $153,000 USD")
    assert r is not None
    assert (r.min_value, r.max_value) == (113000.0, 153000.0)


def test_the_portuguese_twin_of_between_and() -> None:
    r = read_pay("Faixa salarial: entre R$ 3.000 e R$ 5.000")
    assert r is not None
    assert (r.min_value, r.max_value, r.currency) == (3000.0, 5000.0, "BRL")


def test_a_single_figure_is_a_band_of_one() -> None:
    """So a caller comparing against a range never special-cases it."""
    r = read_pay("Salario: R$ 4.500")
    assert r is not None
    assert r.min_value == r.max_value == 4500.0


# =========================================================================
# 2. THE THOUSANDS SEPARATOR IS DECIDED BY SHAPE, NOT BY LOCALE
# =========================================================================


@pytest.mark.parametrize(
    ("description", "value"),
    [
        ("Salario: R$ 4.500", 4500.0),
        ("Salary: $4,500", 4500.0),
        ("Salario: R$ 4.500,00", 4500.0),
        ("Salary: $4,500.00", 4500.0),
        # And the case that must NOT become four thousand: an hourly rate.
        ("Base pay: $25.77 per hour", 25.77),
        ("Base pay: $37.02/hour", 37.02),
    ],
)
def test_a_group_of_three_is_a_thousand_and_anything_else_is_a_decimal(
    description: str, value: float
) -> None:
    """One posting can carry both conventions, so no configuration is consulted.

    `4.500` is four thousand five hundred and `25.77` is twenty-five point seven
    seven, and the only thing that tells them apart is how many digits follow the
    last separator.
    """
    r = read_pay(description)
    assert r is not None
    assert r.min_value == value


# =========================================================================
# 3. WHAT IS NOT A SALARY
# =========================================================================


@pytest.mark.parametrize(
    ("description", "what_it_really_is"),
    [
        (
            "We have surpassed $400M in ARR and our compensation is competitive",
            "the employer's revenue",
        ),
        ("401(k) with a 4% match. Compensation is reviewed annually.", "a benefit"),
        ("Series C: we raised $150M. Salary is competitive.", "a funding round"),
        ("A $2B market opportunity. Pay is above market.", "marketing"),
        ("Nosso faturamento passou de R$ 90 milhoes. Salario a combinar.", "revenue, in PT"),
        ("Salary: competitive, reviewed in 2026", "a year"),
        ("You will manage a budget of $5,000,000 for campaigns", "a budget, with no anchor"),
        ("5+ years of experience required", "experience"),
        ("Ter no minimo 18 anos. Salario a combinar.", "an age"),
    ],
)
def test_money_that_is_not_anybody_s_pay(description: str, what_it_really_is: str) -> None:
    assert read_pay(description) is None, what_it_really_is


def test_a_figure_with_no_currency_is_not_a_salary() -> None:
    """`Salary: competitive` followed by a number is not a number about pay.

    Without a currency there is nothing to anchor the figure to, and a bare
    integer after the word "salary" is as likely to be a year, a headcount or a
    reference number as an amount.
    """
    assert read_pay("Salary band 3, reviewed every 12 months") is None


def test_an_anchor_with_no_money_reads_nothing() -> None:
    assert read_pay("Salary: competitive and reviewed annually") is None
    assert read_pay("Remuneracao compativel com o mercado") is None


# =========================================================================
# 4. WHAT THE READING CARRIES
# =========================================================================


def test_the_reading_quotes_what_it_came_from() -> None:
    """ADR-0002's spirit: a number on screen traces to what the employer wrote."""
    text = "Details follow. The salary range: $130,100 - $187,000 USD applies to this role."
    r = read_pay(text)
    assert r is not None
    assert r.raw in text, "the raw text must be a contiguous substring of the posting"
    assert "130,100" in r.raw
    assert r.anchor == "salary range"


def test_the_period_is_never_guessed_from_the_magnitude() -> None:
    """A 90,000 is a yearly salary in dollars and a monthly one in yen.

    Inventing the period from the size of the number would make every comparison
    across currencies quietly wrong, so silence stays silence.
    """
    r = read_pay("Salary: $90,000")
    assert r is not None
    assert r.period is None

"""Salary correctness (2026-10-16): figures that were plausible and wrong.

Measured on the real catalogue before this was written (read-only):

* `Compensation paid in USD. Paid bi-monthly on the 15th and 30th` read as
  USD 15 a month on 13 postings: the `15` of an ordinal, a currency met
  anywhere in the window, and a payroll cadence as the period.
* `Pay Range $141,162-$176,452.50 USD` read as USD 141.1 on 1,990 postings:
  a weaker anchor's seventy-character window cut the text inside `$141,1`,
  and a two-digit decimal accepted the stump.
* `$176,452.50` read as 176,452: the decimals after a thousands group were
  dropped.
* `Pay Range $29-$34.75 USD` has no period and is an hourly rate; `/mo` on a
  card came from the owner's Latin America default, never from the text.

Every fixture below is a shape seen in real adverts, written fresh; none is
copied from an employer.
"""

from __future__ import annotations

import random
from dataclasses import replace

import pytest

from career_agent.match.engine import JobFacts, effective_pay
from career_agent.match.pay import read_pay

EN, EM = chr(0x2013), chr(0x2014)


def _read(text: str):
    r = read_pay(text)
    return None if r is None else (r.min_value, r.max_value, r.currency, r.period, r.period_hint)


# =========================================================================
# 1. VALID: amount, currency, stated period, and the heuristic kept apart
# =========================================================================


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"Pay Range\n\n$29{EM}$34.75 USD", (29.0, 34.75, "USD", None, "HOUR")),
        (
            f"OTE (On Target Earning)\n\n$86,173.15{EM}$103,259.21 USD",
            None,  # `OTE` is not a pay anchor: on its own it is not read
        ),
        (f"Pay Range\n\n$141,162{EM}$176,452.50 USD", (141162.0, 176452.5, "USD", None, "YEAR")),
        (f"Base salary range: $210,800{EM}$248,000 USD", (210800.0, 248000.0, "USD", None, "YEAR")),
        (f"Base salary range: $214,200{EM}$308,000 USD", (214200.0, 308000.0, "USD", None, "YEAR")),
        ("Salary: USD 3,000/month", (3000.0, 3000.0, "USD", "MONTH", None)),
        ("Pay rate: $25/hour", (25.0, 25.0, "USD", "HOUR", None)),
        ("Salary: $120,000 per year", (120000.0, 120000.0, "USD", "YEAR", None)),
        (f"Salary: $120k{EN}$150k annually", (120000.0, 150000.0, "USD", "YEAR", None)),
        ("Salary: $75-95k", (75000.0, 95000.0, "USD", None, "YEAR")),
        ("Monthly salary: R$ 4.500,00", (4500.0, 4500.0, "BRL", "MONTH", None)),
        ("Annual base salary is $65,000-$85,000", (65000.0, 85000.0, "USD", "YEAR", None)),
        ("Salary Range: $96,288 - $124,608 (CAD)", (96288.0, 124608.0, "CAD", None, None)),
        ("Compensation: $20.00/hour CAD base wage", (20.0, 20.0, "CAD", "HOUR", None)),
        ("Pay Rate $18hr-$$$ DOE", (18.0, 18.0, "USD", "HOUR", None)),
        (
            "The salary range is $90,000 - $110,000depending on location",
            (90000.0, 110000.0, "USD", None, "YEAR"),
        ),
    ],
)
def test_valid_forms(text, expected) -> None:
    assert _read(text) == expected


def test_the_real_fivetran_block_reads_the_pay_range_not_the_ote() -> None:
    text = (
        f"Pay Range\n\n$29{EM}$34.75 USD\n\nOTE (On Target Earning)\n\n"
        f"$86,173.15{EM}$103,259.21 USD"
    )
    assert _read(text) == (29.0, 34.75, "USD", None, "HOUR")


def test_a_weak_anchor_before_the_band_no_longer_cuts_it() -> None:
    text = (
        "Your recruiter can share more about the specific compensation range for your "
        f"location during the hiring process.\n\nPay Range\n\n$141,162{EM}$176,452.50 USD"
    )
    assert _read(text) == (141162.0, 176452.5, "USD", None, "YEAR")


def test_base_beats_ote_when_both_follow_one_anchor() -> None:
    text = (
        "Compensation & Benefits\n\n$20/hour base pay + uncapped commission\n\n"
        "OTE: $70,000-$120,000"
    )
    assert _read(text)[:4] == (20.0, 20.0, "USD", "HOUR")
    text = "annual salary of $50,000 and total on-target earnings of up to $120,000-$275,000"
    assert _read(text)[:4] == (50000.0, 50000.0, "USD", "YEAR")


# =========================================================================
# 2. NEGATIVES: money that is nobody's pay, and numbers that are not money
# =========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Compensation & Time Off\n\nCompensation paid in USD\n"
        "Paid bi-monthly on the 15th and 30th\n"
        "Paid Time Off according to company policy\n"
        "Holidays observed according to company guidelines",
        "Compensation paid in USD. Paid bi-monthly on the 15th and 30th.",
        "Benefits include a monthly cell phone stipend.",
        "Compensation: competitive. 30 days PTO.",
        "Salary: competitive, 15 days vacation",
        "Pay: 100% employer-paid medical insurance",
        "Compensation is paid on the 1st of every month.",
        "Pay: payroll runs twice monthly",
        "Salary paid in USD, based on experience.\n\n- 15 days of vacation",
        "Pay: competitive. Tuition reimbursement (up to $5,250 per year)",
        "Compensation: $500 sign-on bonus at 6 months",
        "Salary: $25.7137/hourly",
        "Salary:\n\nFor employees: 5 300 EUR",
        "We have surpassed $400M in ARR. Salary: competitive",
    ],
)
def test_negatives_read_nothing(text) -> None:
    assert read_pay(text) is None


def test_payroll_cadence_is_never_the_period() -> None:
    text = "Salary: $5,000. Paid bi-monthly on the 15th and 30th"
    reading = read_pay(text)
    assert reading is not None and reading.period is None
    text = "Salary: USD 3,000\nPayroll runs twice monthly; paid on the 1st of every month"
    assert read_pay(text).period is None


def test_the_401k_line_under_a_salary_does_not_refuse_it() -> None:
    text = (
        "- Compensation:\n  - This role offers a salary range of $150,000 - $200,000 USD per year\n"
        "  - 401K plan with up to 5% employer contribution"
    )
    assert _read(text) == (150000.0, 200000.0, "USD", "YEAR", None)


def test_a_period_word_inside_another_word_is_not_a_period() -> None:
    assert _read(f"Compensation\n\nIndia/Pakistan: $15{EN}$19 USD/hour")[3] == "HOUR"
    assert _read("Premium Pay\n\nWeekday Overnights: +$2/hour")[3] == "HOUR"
    assert _read("Home daily\nPay\n$22.00 - 28.00 per hour")[3] == "HOUR"


# =========================================================================
# 3. NUMBER FORMATS AND DASHES
# =========================================================================


@pytest.mark.parametrize(
    ("figure", "value"),
    [
        ("141,162", 141162.0),
        ("176,452.50", 176452.5),
        ("86,173.15", 86173.15),
        ("103,259.21", 103259.21),
        ("29", 29.0),
        ("34.75", 34.75),
        ("4.500,00", 4500.0),
        ("4.500", 4500.0),
        ("25.77", 25.77),
    ],
)
def test_number_formats(figure, value) -> None:
    reading = read_pay(f"Salary: ${figure} USD")
    assert reading is not None and reading.min_value == value == reading.max_value


@pytest.mark.parametrize("dash", ["-", EN, EM, chr(0x2212), " to ", " and "])
def test_dash_variants(dash) -> None:
    assert _read(f"Base salary range: $210,800{dash}$248,000 USD")[:3] == (
        210800.0,
        248000.0,
        "USD",
    )


# =========================================================================
# 4. PROPERTY: a band survives anything around it
# =========================================================================

_NOISE_BEFORE = [
    "",
    "Paid bi-monthly on the 15th and 30th. ",
    "We offer 30 days PTO and a 401(k). ",
    "Compensation paid in USD.\n",
    "Founded in 2012, 300 employees.\n",
]
_NOISE_AFTER = [
    "",
    "\nPaid on the 15th and 30th.",
    "\nPayroll runs twice monthly.",
    "\n15 days vacation, 100% employer-paid medical insurance.",
    "\nSign-on Bonus: up to $10,000",
    "\nMonthly cell phone stipend.",
]


def _figure(value: int, style: str) -> str:
    whole = f"{value:,}"
    return {"plain": whole, "cents": whole + ".00", "k": f"{value // 1000}k"}[style]


def test_property_bands_are_read_whole_and_never_from_noise() -> None:
    rng = random.Random(20261016)
    for _ in range(400):
        low = rng.randrange(20, 300) * 1000
        high = low + rng.randrange(1, 100) * 1000
        style = rng.choice(["plain", "cents", "k"])
        dash = rng.choice(["-", EN, EM, " to ", " - "])
        anchor = rng.choice(
            ["Base salary range: ", "Pay Range\n\n", "Salary: ", "Compensation range: "]
        )
        code_first = rng.random() < 0.3
        lo, hi = _figure(low, style), _figure(high, style)
        band = f"USD {lo}{dash}USD {hi}" if code_first else f"${lo}{dash}${hi} USD"
        text = rng.choice(_NOISE_BEFORE) + anchor + band + rng.choice(_NOISE_AFTER)
        reading = read_pay(text)
        assert reading is not None, text
        assert (reading.min_value, reading.max_value, reading.currency) == (
            float(low),
            float(high),
            "USD",
        ), text
        assert reading.period is None and reading.period_hint == "YEAR", text


def test_the_critical_invariant() -> None:
    text = f"Base salary range: $210,800{EM}$248,000 USD.\nPaid on the 15th and 30th."
    assert _read(text) == (210800.0, 248000.0, "USD", None, "YEAR")


# =========================================================================
# 5. WHERE THE PERIOD COMES FROM (`effective_pay`)
# =========================================================================


def _job(text: str, **salary) -> JobFacts:
    reading = read_pay(text)
    if not salary and reading is not None:
        salary = {
            "salary_min": reading.min_value,
            "salary_max": reading.max_value,
            "salary_currency": reading.currency,
            "salary_period": reading.period,
            "salary_source": "TEXT",
        }
    return JobFacts(title="Analyst", description=text, **salary)


def test_a_salary_band_takes_the_heuristic_period_and_says_so() -> None:
    _, period, basis = effective_pay(_job(f"Pay Range\n\n$141,162{EM}$176,452.50 USD"), ())
    assert (period, basis) == ("YEAR", "SALARY_HEURISTIC")
    _, period, basis = effective_pay(_job(f"Pay Range\n\n$29{EM}$34.75 USD"), ())
    assert (period, basis) == ("HOUR", "SALARY_HEURISTIC")


def test_a_stated_period_is_never_replaced() -> None:
    _, period, basis = effective_pay(_job("Salary: USD 3,000/month"), ())
    assert (period, basis) == (None, None)


def test_the_latam_default_keeps_precedence_over_the_heuristic() -> None:
    """The owner's rule (2026-10-13) for a posting hiring in Latin America is
    applied first: there, a USD figure under 7,000 is read as monthly."""
    text = "Pay Range" + chr(10) * 2 + f"$29{EM}$34.75 USD"
    job = replace(_job(text), declared_hiring_scope="Latin America")
    _, period, basis = effective_pay(job, ())
    assert (period, basis) == ("MONTH", "LATAM_DEFAULT")


def test_a_structured_field_is_not_overridden_by_the_text() -> None:
    text = f"Pay Range\n\n$141,162{EM}$176,452.50 USD"
    job = _job(
        text,
        salary_min=150000.0,
        salary_max=170000.0,
        salary_currency="USD",
        salary_period="YEAR",
        salary_source="STRUCTURED",
    )
    scored, period, basis = effective_pay(job, ())
    assert (scored.salary_min, scored.salary_period, period, basis) == (
        150000.0,
        "YEAR",
        None,
        None,
    )


def test_the_heuristic_applies_only_to_the_band_the_text_states() -> None:
    text = f"Pay Range\n\n$141,162{EM}$176,452.50 USD"
    job = _job(
        text,
        salary_min=100000.0,
        salary_max=120000.0,
        salary_currency="USD",
        salary_period=None,
        salary_source="STRUCTURED",
    )
    assert effective_pay(job, ())[1:] == (None, None)

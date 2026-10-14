"""What a posting says about money, when only the posting says it.

WHY THIS FILE EXISTS
--------------------
Until now compensation reached `JobFacts` from ONE place: the provider's
archived payload, through `pipeline/facts.py`. If a board published a structured
salary field, the product knew the salary. If the employer wrote it in the
advert instead, the product knew nothing.

Measured over the corpus on 2026-09-10, that is most of it. **10,958 postings
put a currency amount within seventy characters of a pay anchor**, and the
anchors they used are:

    salary          3,546        salario            22
    compensation    2,738        base pay          243
    salario (PT)    2,020        annual salary      201
    pay range         966        faixa salarial      85
    remuneracao       603        wage                 4
    pay               530

Greenhouse is the loudest case and it is not subtle:

    salary range: $130,100 - $187,000 USD
    salary range: $25.77 - $37.02 USD
    salary range: $56,560 - $66,500 CAD

Every one of those sat in a description this product had already stored, beside
a `job_match` row whose `salary_min` was NULL. And in Brazil -- 78,806 postings
-- the corpus held **37 rows with a salary in reais**, because no Brazilian board
here publishes a structured pay field and every `Salario: R$ 4.500` in the
market was invisible.

WHAT THIS IS NOT
----------------
**It is a FALLBACK and it says so at the call site.** A structured field the
provider published is a better fact than a sentence a parser understood, and
`pipeline/facts.py` asks this only when the payload said nothing. ADR-0002's
spirit applies: `raw` carries the substring the reading came from, so a number
on screen can always be traced to what the employer wrote.

**A NUMBER NEAR A CURRENCY SIGN IS NOT A SALARY**, which is the whole difficulty
and the reason this is anchored rather than greedy. The corpus is full of money
that is nobody's pay:

    We have surpassed $400M in ARR              the employer's revenue
    401(k) with a 4% match                      a benefit
    a $2B market opportunity                    marketing
    Series C, $150M raised                      funding

Those appear in thousands of postings, and a reader that took the first currency
amount in the text would have reported the employer's ARR as the candidate's
salary. So a figure counts only inside a window that starts at a pay anchor, the
same shape `match/gates.py` uses for hiring intent and `match/years.py` uses for
an age.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["PayReading", "default_period", "read_pay"]


@dataclass(frozen=True)
class PayReading:
    """One posting's stated pay, as the text stated it.

    `min_value` and `max_value` are the same number for a single figure, because
    "R$ 4.500" is a band of one and a caller comparing against a range should not
    have to special-case it.
    """

    min_value: float
    max_value: float
    currency: str
    #: YEAR / MONTH / HOUR, or None when the text did not say. Never guessed
    #: from the magnitude: a 90,000 could be a yearly salary in dollars or a
    #: monthly one in yen, and inventing the period would make every comparison
    #: between currencies quietly wrong.
    period: str | None
    #: The contiguous substring this was read from. Traceable, like a quote.
    raw: str
    #: Which anchor word licensed the reading, for a details view and for
    #: diagnosing a false positive without re-running the parser.
    anchor: str


#: The words that make a nearby number somebody's pay, measured from the corpus
#: rather than imagined. Ordered longest-first so `pay range` and `base pay` win
#: over the bare `pay` they contain.
_ANCHOR = re.compile(
    r"(?:"
    # A rate is pay when the ad calls it a rate of pay. `rate` alone is not an
    # anchor: interest rates, exchange rates and growth rates are everywhere.
    r"hourly\s+(?:rate|pay|wage|compensation)"
    r"|(?:daily|day|pay)\s+rate"
    r"|rate\s+per\s+hour"
    r"|taxa\s+hor[aá]ria"
    r"|valor\s+(?:por\s+)?hora"
    r"|faixa\s+salarial"
    r"|annual\s+salary"
    r"|remunera[çc][ãa]o"
    r"|compensation"
    r"|salary\s+range"
    r"|pay\s+range"
    r"|base\s+pay"
    r"|sal[áa]rio"
    r"|\bsalary\b"
    r"|\bwage\b"
    r"|\bpay\b"
    r")",
    re.IGNORECASE,
)

#: How far past the anchor a figure may sit. Seventy characters is what the
#: measurement used and it is generous enough for `salary range: $130,100 -
#: $187,000 USD` while stopping well short of the next paragraph.
_WINDOW = 70

#: Symbols and codes, and what they mean. `$` is deliberately ambiguous and is
#: resolved by a trailing code where the text gives one -- Greenhouse writes
#: `$56,560 - $66,500 CAD`, and reading that as US dollars would understate it by
#: a third. With no code, `$` is recorded as USD, which is what it means in the
#: overwhelming majority of this corpus and is stated here rather than hidden.
_SYMBOL = {"R$": "BRL", "US$": "USD", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_CODES = ("USD", "BRL", "EUR", "GBP", "CAD", "AUD", "NZD", "SGD", "CHF", "SEK", "MXN", "ARS")

#: One amount: an optional symbol, digits with either thousands convention, an
#: optional decimal part, and an optional `k`.
#:
#: `[\d]{1,3}(?:[.,]\d{3})*` and NOT `[\d.,]+`: the second matches `4.500,00`
#: and also `1.2.3`, and more importantly it cannot tell `1.234` (a thousand,
#: Brazilian) from `1.234` (one point two three four). The grouping is what
#: makes `R$ 4.500,00` read as 4500 rather than as 4.5.
#: THE PREFIX IS A SYMBOL **OR** A CODE. `EUR 60k - EUR 75k` and `CAD 90,000 -
#: CAD 110,000` are how a code-first market writes a band, and a pattern that
#: only knew symbols matched the first figure, missed the separator, and reported
#: a single value -- silently halving a range into its floor.
_PREFIX = r"R\$|US\$|\$|€|£|¥|" + r"|".join(_CODES)

_AMOUNT = (
    r"(?:(" + _PREFIX + r")\s*)?"
    r"(\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d{1,2})?)"
    r"\s*([kK])?"
)

#: WHAT SEPARATES THE TWO ENDS OF A BAND, and `and` is not optional.
#:
#: Ashby's customers write `salary for this role is between $113,000 and
#: $153,000`, and the first version of this list did not have `and` -- so the
#: band read as a single figure equal to its own FLOOR. Measured on the corpus,
#: that shape appears in thousands of postings, and a salary reported low is
#: worse than none because it reads as a real offer.
#:
#: `e` is the Portuguese twin (`entre R$ 3.000 e R$ 5.000`) and `a` the ordinary
#: `de X a Y`. Both are safe here because this pattern only ever runs inside a
#: window that already began at a pay anchor.
#: THE DASHES ARE BUILT RATHER THAN TYPED, and that is not fussiness.
#:
#: An en dash and an em dash are what the EMPLOYER writes -- Greenhouse's
#: bands use one -- so this pattern must match those bytes even though the
#: punctuation gate forbids them in text this project authors. A line-level
#: allowance worked until `ruff format` split the expression and moved the
#: marker off the line holding the characters, at which point the gate went
#: red for a reason no reader could see. Constructing them from code points
#: means no formatter can separate the two again.
_DASHES = chr(0x2D) + chr(0x2013) + chr(0x2014)

_RANGE = re.compile(
    _AMOUNT + r"\s*(?:[" + _DASHES + r"]|to|and|a|e|at[ée]|\.\.\.)\s*" + _AMOUNT,
    re.IGNORECASE,
)
_SINGLE = re.compile(_AMOUNT)

#: A trailing currency code, as Greenhouse and Ashby write it.
_TRAILING_CODE = re.compile(r"\b(" + "|".join(_CODES) + r")\b", re.IGNORECASE)

#: The period, in either language. `/ano`, `per year`, `anual`, `hora`.
_PERIOD = (
    # `hour` or `hourly`, never `hours`: "40 hours per week" is a workload, and
    # reading it as the period of a nearby amount turned a yearly salary into
    # an hourly rate.
    (
        re.compile(
            r"(?:/|per\s+|por\s+|an?\s+)?(?:hour(?:ly)?\b|hora\b|hor[aá]ri[oa]\b|hr\b|/h\b)",
            re.IGNORECASE,
        ),
        "HOUR",
    ),
    (re.compile(r"(?:/|per\s+|por\s+)?(?:month|m[êe]s|mensal|monthly)", re.IGNORECASE), "MONTH"),
    (
        re.compile(r"(?:/|per\s+|por\s+)?(?:year|ano|annual|anual|annually|yr\b)", re.IGNORECASE),
        "YEAR",
    ),
    # Last, so a monthly or yearly figure that mentions "daily" elsewhere keeps
    # its own period.
    (re.compile(r"(?:/|per\s+|por\s+)?(?:day|daily|dia|di[aá]ri[oa])\b", re.IGNORECASE), "DAY"),
)

#: WHAT THE MONEY IS NOT. Each of these appears in thousands of postings beside a
#: figure large enough to look like a salary, and every one of them would be a
#: sentence the employer never wrote about the candidate's pay.
_NOT_PAY = re.compile(
    r"(?:"
    r"\bARR\b"
    r"|\bMRR\b"
    r"|\brevenue\b"
    r"|\bvaluation\b"
    r"|\bfunding\b"
    r"|\braised\b"
    r"|\bseries\s+[a-e]\b"
    r"|\bmarket\s+(?:opportunity|size|cap)\b"
    r"|401\s*\(?k\)?"
    r"|\bfaturamento\b"
    r"|\bcaptou\b"
    r")",
    re.IGNORECASE,
)


def _number(digits: str, kilo: str | None) -> float | None:
    """`4.500`, `4,500`, `4.500,00`, `90k` -> a number, or None if incoherent.

    THE THOUSANDS SEPARATOR IS THE HARD PART and it is decided by SHAPE rather
    than by locale, because one posting can carry both conventions. A group of
    exactly three digits after the last separator is a thousands group; anything
    else is a decimal part. `4.500` is four thousand five hundred and `25.77` is
    twenty-five point seven seven, and no configuration is consulted to tell them
    apart.
    """
    text = digits.strip()
    if not text:
        return None
    # Normalise: the LAST separator decides, by the length of what follows it.
    last_sep = max(text.rfind("."), text.rfind(","))
    if last_sep == -1:
        whole, frac = text, ""
    else:
        tail = text[last_sep + 1 :]
        if len(tail) == 3:
            whole, frac = text.replace(".", "").replace(",", ""), ""
        else:
            whole = text[:last_sep].replace(".", "").replace(",", "")
            frac = tail
    if not whole.isdigit() or (frac and not frac.isdigit()):
        return None
    value = float(f"{whole}.{frac}") if frac else float(whole)
    if kilo:
        value *= 1000
    return value


def _currency(prefix: str | None, window: str) -> str | None:
    """The trailing code wins over the symbol, because `$ ... CAD` means CAD.

    A prefix that IS a code is already the answer and needs no lookup, which is
    what `EUR 60k` and `CAD 90,000` supply.
    """
    if prefix and prefix.upper() in _CODES:
        return prefix.upper()
    code = _TRAILING_CODE.search(window)
    if code:
        return code.group(1).upper()
    if prefix:
        return _SYMBOL.get(prefix)
    return None


def _period(window: str, anchor: str = "") -> str | None:
    """The period can be in the ANCHOR as easily as after the figure.

    `Annual salary: 55,000 - 70,000` says "year" before it says any number, and
    a reader that only looked forward reported the band with no period at all --
    which then compares wrongly against every yearly preference.
    """
    for pattern, name in _PERIOD:
        if pattern.search(window) or (anchor and pattern.search(anchor)):
            return name
    return None


def _with_code(description: str, start: int, end: int) -> str:
    """The quote, extended over a currency code that follows the figure
    (`$40-$85 USD`), so the citation is the whole of what the ad wrote."""
    tail = re.match(r"\s*(?:" + "|".join(_CODES) + r")\b", description[end:], re.IGNORECASE)
    return description[start : end + (tail.end() if tail else 0)].strip()


def read_pay(description: str) -> PayReading | None:
    """The pay this posting states, or None. Pure, and candidate independent.

    None is the ordinary answer and is never a defect: most postings say nothing
    about money, and `match/score.py` scores that `salary_unknown` and rejects
    nobody. Compensation is a preference here, never a filter.
    """
    if not description:
        return None

    for anchor in _ANCHOR.finditer(description):
        window = description[anchor.end() : anchor.end() + _WINDOW]
        # The sentence around the anchor, so `$400M in ARR` is refused even when
        # the words sit before the anchor rather than after it.
        context = description[max(0, anchor.start() - 40) : anchor.end() + _WINDOW]
        if _NOT_PAY.search(context):
            continue

        band = _RANGE.search(window)
        if band:
            low = _number(band.group(2), band.group(3))
            high = _number(band.group(5), band.group(6))
            if low is None or high is None or low <= 0 or high < low:
                continue
            currency = _currency(band.group(1) or band.group(4), window)
            if not currency:
                continue
            return PayReading(
                min_value=low,
                max_value=high,
                currency=currency,
                period=_period(window, anchor.group(0)),
                raw=_with_code(description, anchor.start(), anchor.end() + band.end()),
                anchor=anchor.group(0).lower(),
            )

        one = _SINGLE.search(window)
        if one:
            value = _number(one.group(2), one.group(3))
            if value is None or value <= 0:
                continue
            currency = _currency(one.group(1), window)
            # A BARE NUMBER AFTER "salary" IS NOT A SALARY. `Salary: competitive`
            # followed by `2026` is a year; without a currency there is nothing
            # to anchor the figure to and silence is the honest answer.
            if not currency:
                continue
            return PayReading(
                min_value=value,
                max_value=value,
                currency=currency,
                period=_period(window, anchor.group(0)),
                raw=_with_code(description, anchor.start(), anchor.end() + one.end()),
                anchor=anchor.group(0).lower(),
            )
    return None


#: THE OWNER'S PERIOD DEFAULTS (2026-10-13), for USD only. A positive figure
#: below the first is a month's pay, one above the second a year's; anything
#: from one to the other, both included, stays unknown. Measured on postings
#: that hire in Latin America, where a dollar figure under 7,000 is a monthly
#: contractor rate and one over 30,000 a yearly salary. Another currency has
#: other magnitudes and gets no default until it has its own rule.
MONTHLY_BELOW_USD = 7_000.0
YEARLY_ABOVE_USD = 30_000.0


def default_period(
    minimum: float | None,
    maximum: float | None,
    currency: str | None,
    *,
    hires_in_latam: bool,
) -> str | None:
    """The period the app ASSUMES for a USD figure stated without one, or None.

    Only for a posting that hires in Latin America, only in USD, and only
    when every stated figure falls on the same side: a band from 5,000 to
    9,000 crosses the monthly limit and stays unknown, so a default can never
    split one band into two periods. The caller keeps the stated (empty)
    period and marks this one as inferred; a stated period always wins, and
    this is never called when one exists.
    """
    if not hires_in_latam or (currency or "").upper() != "USD":
        return None
    figures = [value for value in (minimum, maximum) if value is not None]
    if not figures or any(value <= 0 for value in figures):
        return None
    if all(value < MONTHLY_BELOW_USD for value in figures):
        return "MONTH"
    if all(value > YEARLY_ABOVE_USD for value in figures):
        return "YEAR"
    return None


#: "The provided range is the expected salary for candidates in the U.S.",
#: "This range applies to U.S.-based candidates", "US salary range": the
#: employer saying a band is the American one. Kept, never generalised.
#: Each alternative is a leading `[^.\n]*` (the run back to the last `.` or
#: line break) followed by its anchored REST, which starts at a keyword.
_US_ONLY_REST = (
    r"\b(?:range|salary|compensation|pay)\b[^.\n]{0,80}?"
    r"\b(?:for|to|applies\s+to)\s+(?:candidates|applicants|roles|employees|people|those)\s+"
    r"(?:(?:based|located|living|hired)\s+)?in\s+the\s+(?:U\.S\.?|US|USA|United\s+States)",
    r"\b(?:U\.S\.|US)[- ]based\s+(?:candidates|applicants|roles|employees)\b"
    r"[^.\n]{0,40}?\b(?:range|salary|compensation)\b",
)
_US_ONLY_BAND = re.compile("|".join(r"[^.\n]*" + rest for rest in _US_ONLY_REST), re.IGNORECASE)
_US_ONLY_ANCHORS = tuple(re.compile(rest, re.IGNORECASE) for rest in _US_ONLY_REST)


def us_only_band(description: str | None) -> str | None:
    """The advert's sentence saying its pay band is for the United States.

    The answer is `_US_ONLY_BAND.search(description).group(0).strip()`,
    computed without the search. `search` tries every start position, and
    from each one the leading `[^.\\n]*` runs to the end of the line before
    backtracking: quadratic in a long line, about 24 ms per advert with a
    salary (2026-10-14). But a match can only start where its run starts:
    right after the last `.` or line break before the earliest place either
    alternative's REST matches (no earlier start reaches a match without
    crossing a break, and every later start in that run is beaten by it). So
    the earliest REST is found first (linear: each is anchored at a keyword
    and bounded), and the original pattern is matched once, there; it picks
    the alternative and the keyword exactly as `search` did.
    """
    text = description or ""
    starts = [m.start() for anchor in _US_ONLY_ANCHORS if (m := anchor.search(text))]
    if not starts:
        return None
    first = min(starts)
    run_start = max(text.rfind(".", 0, first), text.rfind("\n", 0, first)) + 1
    # Never None by the argument above; `search` is kept as the definition.
    found = _US_ONLY_BAND.match(text, run_start) or _US_ONLY_BAND.search(text)
    return found.group(0).strip() if found else None

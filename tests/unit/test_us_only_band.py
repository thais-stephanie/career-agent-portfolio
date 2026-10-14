"""`us_only_band` without the quadratic search: identical answers, always.

The ORACLE is the implementation as it stood (readers-12): the same pattern,
written out again here, and `search`. Every case compares the two strictly:
None against a string, the exact substring, case, whitespace, punctuation,
which of several matches. Generated cases come from a fixed seed (no
dependency is added for property testing).
"""

from __future__ import annotations

import random
import re

import pytest

from career_agent.match.pay import _US_ONLY_BAND, us_only_band

_LEGACY = re.compile(
    r"[^.\n]*\b(?:range|salary|compensation|pay)\b[^.\n]{0,80}?"
    r"\b(?:for|to|applies\s+to)\s+(?:candidates|applicants|roles|employees|people|those)\s+"
    r"(?:(?:based|located|living|hired)\s+)?in\s+the\s+(?:U\.S\.?|US|USA|United\s+States)"
    r"|[^.\n]*\b(?:U\.S\.|US)[- ]based\s+(?:candidates|applicants|roles|employees)\b"
    r"[^.\n]{0,40}?\b(?:range|salary|compensation)\b",
    re.IGNORECASE,
)


def _legacy_us_only_band(description: str | None) -> str | None:
    found = _LEGACY.search(description or "")
    return found.group(0).strip() if found else None


def test_the_pattern_itself_did_not_change() -> None:
    assert _US_ONLY_BAND.pattern == _LEGACY.pattern
    assert _US_ONLY_BAND.flags == _LEGACY.flags


US = "The salary range for this role is $120,000 - $150,000 for candidates based in the U.S."
CAVEAT = "This range applies to US-based candidates only; other locations differ"
WORLD = "Pay is competitive worldwide."
FIXTURES = [
    None,
    "",
    US,
    CAVEAT,
    "We pay globally. " + US,
    WORLD + " " + CAVEAT + ". Benefits follow.",
    US + " " + CAVEAT,  # two matches, one sentence each
    "Compensation: the range applies to applicants in the United States, and the "
    "salary applies to candidates in the US",  # two in one sentence
    "Our pay range\napplies to candidates in the US",  # \s+ crosses a line break
    "The base pay range for roles in the U.S. is listed below.",
    "pay. to candidates in the US",  # a dot between: no match
    "x" * 5000 + " salary for candidates in the USA",
    "a" * 20000,
    ("word " * 3000) + "compensation for employees located in the United States",
    "Salary for candidates in the U.S\nNext line.",
    "US-based roles: the salary is set per state. Range for people in the USA",
    "Our U.S.-based candidates see a salary band; others do not.",
    "São Paulo \u2014 salário. Salary for candidates hired in the US 🇺🇸",
    "payrange for candidates in the US",  # no word boundary
    "The salary is for candidates in the UK.",
    "   leading spaces salary to those in the united states   ",
]


@pytest.mark.parametrize("text", FIXTURES)
def test_real_shaped_fixtures(text: str | None) -> None:
    assert us_only_band(text) == _legacy_us_only_band(text)


_WORDS = [
    "salary", "Salary", "SALARY", "range", "pay", "payroll", "compensation", "compensations",
    "for", "to", "applies", "applies to", "candidates", "applicants", "roles", "employees",
    "people", "those", "based", "located", "living", "hired", "in", "the", "U.S.", "U.S",
    "US", "USA", "United States", "united  states", "UK", "Brazil", "US-based", "U.S.-based",
    "US based", "USbased", "$120,000", "USD", "150k", "-", "\u2013", "and", "is", "band", "only",
    "étendue", "salário", "remote", "x", "lorem",
]  # fmt: skip
_GLUE = [" ", "  ", "\n", ". ", ".", ", ", "; ", " - ", "\t", "\r\n", ""]


def _generated(rng: random.Random) -> str:
    parts = []
    for _ in range(rng.randint(0, 40)):
        parts.append(rng.choice(_WORDS))
        parts.append(rng.choice(_GLUE))
    if rng.random() < 0.3:
        parts.insert(rng.randint(0, len(parts)), rng.choice(FIXTURES[2:12]) or "")
    return "".join(parts)


def test_twenty_thousand_generated_texts() -> None:
    rng = random.Random(20261014)
    matched = 0
    for _ in range(20_000):
        text = _generated(rng)
        expected = _legacy_us_only_band(text)
        assert us_only_band(text) == expected, text
        matched += expected is not None
    assert matched > 2_000, "the generator must reach the pattern often"


def test_scale_a_long_single_line_with_the_anchor_at_the_end() -> None:
    """The new reader is linear: a million characters on one line, anchor at
    the end, answered at once (the old search is quadratic there, so the
    oracle is compared on a length it can still finish)."""
    tail = " salary for candidates in the US"
    small = "lorem ipsum " * 400 + tail
    assert us_only_band(small) == _legacy_us_only_band(small)
    big = "lorem ipsum " * 90_000 + tail
    assert us_only_band(big) == big.strip()

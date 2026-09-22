"""Country-code normalisation.

The whole point: a plausible-looking country typo must fail loudly at load
time instead of silently matching nothing for an entire job search.
"""

import pytest

from career_agent.domain.countries import (
    ISO_3166_1_ALPHA_2,
    UnknownCountryError,
    normalise_country,
)


def test_the_target_markets_are_all_valid_codes() -> None:
    for code in ["CH", "DE", "JP", "US", "GB", "NL", "CA", "AU", "SG", "SE", "BR"]:
        assert code in ISO_3166_1_ALPHA_2


def test_uk_normalises_to_gb() -> None:
    """The specific alias that prompted this module."""
    assert normalise_country("UK") == "GB"
    assert normalise_country("uk") == "GB"
    assert normalise_country("United Kingdom") == "GB"


def test_case_and_whitespace_are_forgiven() -> None:
    assert normalise_country(" br ") == "BR"
    assert normalise_country("Ch") == "CH"


def test_three_letter_codes_are_rejected_with_a_hint() -> None:
    with pytest.raises(UnknownCountryError, match="two letters"):
        normalise_country("BRZ")


def test_unknown_two_letter_code_is_rejected() -> None:
    with pytest.raises(UnknownCountryError):
        normalise_country("XQ")


def test_empty_is_rejected() -> None:
    with pytest.raises(UnknownCountryError):
        normalise_country("")


def test_the_set_looks_complete() -> None:
    """A smoke test on the constant itself: roughly 250 officially assigned
    codes, all two uppercase letters."""
    assert 240 <= len(ISO_3166_1_ALPHA_2) <= 260
    assert all(len(code) == 2 and code.isupper() for code in ISO_3166_1_ALPHA_2)

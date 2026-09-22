"""Provider values a rule can settle, and the ones it must not try to.

Measuring the provider family found it was 99.4% static overhead -- ~2,015
tokens of prompt and schema to interpret a median of 12 tokens. Three of its
four dimensions turned out not to need a model at all, and one of those was
being asked to echo its input.

The tests that matter most here are the negative ones. A lookup table that
resolves the easy values and silently guesses the hard ones is worse than no
table, because the guesses arrive wearing the same confidence as the facts.
"""

import pytest

from career_agent.domain.enums import MetadataDimension, WorkModel
from career_agent.domain.provider_values import (
    DETERMINISTIC_DIMENSIONS,
    INTERPRETED_DIMENSIONS,
    needs_a_model,
    normalise_employment_type,
    normalise_work_model,
    resolve_deterministically,
)

# --- the spellings the corpus actually contains ------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("FullTime", "FULL_TIME"),
        ("Full-Time", "FULL_TIME"),
        ("Full Time", "FULL_TIME"),
        ("Full-time", "FULL_TIME"),
        ("Permanent", "FULL_TIME"),
        ("Part Time", "PART_TIME"),
        ("Contract", "CONTRACT"),
    ],
)
def test_measured_employment_spellings_normalise(raw: str, expected: str) -> None:
    """Every one of these appears in the 18,551-job corpus. The table was built
    from what employers write, not from what they might plausibly write."""
    assert normalise_employment_type(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Remote", WorkModel.REMOTE),
        ("remote", WorkModel.REMOTE),
        ("Hybrid", WorkModel.HYBRID),
        ("hybrid", WorkModel.HYBRID),
        ("onsite", WorkModel.ONSITE),
        ("OnSite", WorkModel.ONSITE),
        ("unspecified", WorkModel.UNCLEAR),
    ],
)
def test_measured_work_model_spellings_normalise(raw: str, expected: WorkModel) -> None:
    assert normalise_work_model(raw) is expected


# --- what the table must refuse to decide ------------------------------------


@pytest.mark.parametrize(
    "raw",
    ["Mid-Senior Level", "Director", "Executive", "Remote", "Mid Level"],
)
def test_a_value_that_is_not_an_employment_type_is_kept_raw(raw: str) -> None:
    """These are real values from the employment-type field, and none of them is
    an employment type -- employers put seniority and work model in it.

    595 observations across 16 such spellings. Mapping "Director" onto the
    nearest enum member would manufacture a fact from a data-entry mistake, so
    the value is recorded exactly as the provider stored it and marked
    un-normalised.
    """
    resolved = resolve_deterministically(MetadataDimension.EMPLOYMENT_TYPE_HINT, raw)

    assert resolved is not None
    assert resolved.value == raw
    assert resolved.normalised is False


def test_a_compound_employment_type_is_not_collapsed() -> None:
    """`Full Time/Part Time` appears 349 times. The provider is describing a
    role that is either, and choosing one would be a decision, not a
    normalisation."""
    resolved = resolve_deterministically(
        MetadataDimension.EMPLOYMENT_TYPE_HINT, "Full Time/Part Time"
    )

    assert resolved is not None
    assert resolved.normalised is False


# --- geography is never guessed ----------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "United States",
        "US",
        "Remote - United States",
        "Remote (EMEA)",
        "US or Canada, remote",
        "San Francisco, CA",
        "Multiple locations",
        "Anywhere",
    ],
)
def test_geography_always_goes_to_a_model(raw: str) -> None:
    """`US` looks trivially mappable and `San Francisco` looks obvious.

    But `Remote - United States`, `US or Canada, remote` and `Multiple
    locations` are not, and hiring scope is the single highest-stakes dimension
    in the system. A rule that gets the easy ones right and the hard ones
    silently wrong is worse than no rule, so the whole dimension is left to
    interpretation.
    """
    assert resolve_deterministically(MetadataDimension.HIRING_LOCATION_HINT, raw) is None


def test_the_two_dimension_sets_do_not_overlap() -> None:
    assert not (DETERMINISTIC_DIMENSIONS & INTERPRETED_DIMENSIONS)
    assert set(MetadataDimension) == DETERMINISTIC_DIMENSIONS | INTERPRETED_DIMENSIONS


def test_a_new_dimension_would_default_to_the_model() -> None:
    """Safe direction. A dimension nobody has written a rule for must not fall
    through to a rule that happens to be nearby."""

    class Invented:
        value = "something_new_hint"

    assert resolve_deterministically(Invented(), "anything") is None  # type: ignore[arg-type]


# --- what reaches the model --------------------------------------------------


class Observation:
    def __init__(self, dimension: str, value: str) -> None:
        self.dimension = dimension
        self.source_value = value


def test_only_geography_survives_the_filter() -> None:
    """The measurement that justified the whole module.

    A posting whose mapped fields are employment type, work model and
    compensation needs no model at all; one carrying a location hint needs a
    call for that hint alone.
    """
    observations = [
        Observation("employment_type_hint", "FullTime"),
        Observation("work_model_hint", "Remote"),
        Observation("compensation_hint", "$120K - $150K"),
        Observation("hiring_location_hint", "Remote (EMEA)"),
    ]

    remaining = needs_a_model(observations)

    assert len(remaining) == 1
    assert remaining[0].dimension == "hiring_location_hint"


def test_a_posting_without_geography_needs_no_call_at_all() -> None:
    """Zero calls, not a call returning an empty list.

    The current corpus never hits this -- every posting carries a location hint
    -- but nothing assumes it, and a provider or corpus without one costs
    nothing rather than ~2,015 tokens to be told there is nothing to do.
    """
    observations = [
        Observation("employment_type_hint", "Contract"),
        Observation("compensation_hint", "$90K"),
    ]

    assert needs_a_model(observations) == []

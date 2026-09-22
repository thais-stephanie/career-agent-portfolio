"""The planning estimator: measured where a measurement exists, honest where not.

The numbers this produces guard a quota ceiling, so the direction of its error
matters more than its size. Every test here is about that direction.
"""

from career_agent.llm.client import Family, ModelConfig
from career_agent.llm.prompts import DESCRIPTION_PROMPT_VERSION, PROVIDER_PROMPT_VERSION
from career_agent.llm.requests import build_description_request, build_provider_request
from career_agent.llm.tokens import (
    CHARS_PER_TOKEN,
    MEASURED_OUTPUT_TOKENS,
    MEASURED_PROMPT_TOKENS,
    MEASURED_SCHEMA_TOKENS,
    estimate_request,
    from_characters,
)

CONFIG = ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite")

#: The corpus median posting, as measured in M2.2: 5,811 characters is 1,088
#: tokens under `o200k_base`. The one real (characters, tokens) pair for the
#: kind of text this ratio is ever applied to.
MEDIAN_POSTING_CHARS = 5_811
MEDIAN_POSTING_TOKENS = 1_088


def test_the_ratio_over_estimates_real_posting_text() -> None:
    """The whole reason the ratio is 4.0 and not the 5.34 that was measured.

    If this ever inverts, the limiter starts under-estimating the largest term
    in the run and the ceiling it guards stops being a ceiling.
    """
    assert from_characters("x" * MEDIAN_POSTING_CHARS) > MEDIAN_POSTING_TOKENS
    assert CHARS_PER_TOKEN < MEDIAN_POSTING_CHARS / MEDIAN_POSTING_TOKENS


def test_the_static_half_of_a_live_prompt_comes_from_a_measurement() -> None:
    built = build_description_request("sha256:abc", "Some posting text.", CONFIG)
    estimate = estimate_request(built)

    assert estimate.static_measured
    assert estimate.static == (
        MEASURED_PROMPT_TOKENS[DESCRIPTION_PROMPT_VERSION]
        + MEASURED_SCHEMA_TOKENS[Family.DESCRIPTION]
    )
    assert estimate.output == MEASURED_OUTPUT_TOKENS[Family.DESCRIPTION]


def test_both_live_prompt_versions_are_measured() -> None:
    """A prompt version nobody measured must not be silently guessed at.

    This fails the moment a prompt is bumped without re-running the token audit,
    which is exactly when the planning numbers would otherwise go quietly stale.
    """
    assert DESCRIPTION_PROMPT_VERSION in MEASURED_PROMPT_TOKENS
    assert PROVIDER_PROMPT_VERSION in MEASURED_PROMPT_TOKENS


def test_an_unmeasured_prompt_version_says_so_rather_than_borrowing_a_number() -> None:
    from dataclasses import replace

    built = build_description_request("sha256:abc", "Some posting text.", CONFIG)
    invented = replace(built, prompt_version="description_v99")
    estimate = estimate_request(invented)

    assert not estimate.static_measured
    assert estimate.static > 0


def test_the_dynamic_half_tracks_the_text_the_model_is_actually_given() -> None:
    short = estimate_request(build_description_request("sha256:a", "short", CONFIG))
    long = estimate_request(build_description_request("sha256:b", "x" * 40_000, CONFIG))

    assert long.dynamic > short.dynamic
    assert long.static == short.static
    assert long.input == long.static + long.dynamic
    assert long.total == long.input + long.output


def test_the_provider_family_is_almost_entirely_static() -> None:
    """The measurement that took the provider prompt from four dimensions to one.

    98.8% static is not a curiosity; it is why the family is cached on the
    rendered block rather than on the posting.
    """
    from career_agent.domain.enums import MetadataDimension
    from career_agent.providers.base import ProviderMetadataObservation

    observation = ProviderMetadataObservation(
        dimension=MetadataDimension.HIRING_LOCATION_HINT,
        provider="greenhouse",
        source_field="location.name",
        source_value="Remote - United States",
    )
    built = build_provider_request("greenhouse", (observation,), "sha256:map", CONFIG)
    assert built is not None

    estimate = estimate_request(built)
    assert estimate.static_measured
    assert estimate.static / estimate.input > 0.9

"""What the model is actually given, and what identifies it.

Two properties are load-bearing here and neither is obvious from reading the
builders:

**The user message is the source, and nothing else.** Anything else we add is
text the model can quote, and a quote containing our own scaffolding would be
evidence no posting ever contained.

**The provider family's model-visible input and its cache key are the same
artefact.** That is what makes cross-job reuse safe: two postings share an
answer exactly when they were asking one question, word for word.
"""

import pytest

from career_agent.domain.enums import MetadataDimension
from career_agent.llm.cache import (
    description_key,
    field_map_version,
    observation_block,
    static_digest,
)
from career_agent.llm.client import Family, ModelConfig
from career_agent.llm.prompts import DESCRIPTION_PROMPT_VERSION, PROVIDER_PROMPT_VERSION
from career_agent.llm.requests import (
    ELISION,
    MAX_DESCRIPTION_CHARS,
    build_description_request,
    build_provider_request,
    description_schema,
    provider_schema,
    truncate_description,
)
from career_agent.llm.transport import PROVIDER_TRANSPORT_VERSION, TRANSPORT_SCHEMA_VERSION
from career_agent.providers.base import ProviderMetadataObservation

SONNET = ModelConfig(vendor="anthropic", identifier="claude-sonnet-5")

MAP = field_map_version(
    "greenhouse",
    ("location.name", "metadata.employment_type"),
    ("hiring_location_hint", "employment_type_hint"),
)

POSTING = (
    "Business Technology Analyst\n\n"
    "You will own our internal systems.\n\n"
    "This role is open to candidates residing in Brazil or Portugal."
)


def obs(dimension: MetadataDimension, field: str, value: str) -> ProviderMetadataObservation:
    return ProviderMetadataObservation(
        dimension=dimension, provider="greenhouse", source_field=field, source_value=value
    )


GEOGRAPHY = obs(MetadataDimension.HIRING_LOCATION_HINT, "location.name", "Remote - United States")
EMPLOYMENT = obs(MetadataDimension.EMPLOYMENT_TYPE_HINT, "metadata.employment_type", "FullTime")


# --- the description family -------------------------------------------------


def test_the_user_message_is_the_posting_and_nothing_else() -> None:
    """No wrapper tags, no preamble, no restatement of the task.

    Every character we add is a character the model may quote back, and the
    verifier -- correctly -- checks quotes against the stored posting, where our
    scaffolding does not appear.
    """
    built = build_description_request("sha256:aaa", POSTING, SONNET)

    assert built.request.user == POSTING


def test_the_description_call_is_never_given_provider_metadata() -> None:
    """Source isolation is structural, not an instruction the model follows."""
    built = build_description_request("sha256:aaa", POSTING, SONNET)
    whole_request = built.model_visible_input

    assert "location.name" not in whole_request
    assert "Remote - United States" not in whole_request


def test_an_empty_posting_is_refused_rather_than_sent() -> None:
    """A model given nothing returns a well-formed object full of NOT_STATED.

    That object validates, stores, and reads forever after as "we examined this
    posting and it said nothing" -- a manufactured fact rather than a missing
    one, and one no downstream check could catch.
    """
    with pytest.raises(ValueError, match="empty posting"):
        build_description_request("sha256:aaa", "   \n\t ", SONNET)


def test_the_description_key_is_the_documented_one() -> None:
    built = build_description_request("sha256:aaa", POSTING, SONNET)
    expected = description_key(
        SONNET,
        DESCRIPTION_PROMPT_VERSION,
        TRANSPORT_SCHEMA_VERSION,
        "sha256:aaa",
        static_digest(built.request.system, built.request.schema, "STRICT_SCHEMA"),
    )

    assert built.request.family is Family.DESCRIPTION
    assert built.cache_key == expected
    assert built.prompt_version == DESCRIPTION_PROMPT_VERSION
    assert built.transport_version == TRANSPORT_SCHEMA_VERSION


def test_two_postings_with_one_content_hash_share_a_key() -> None:
    """The same posting cross-listed on two boards is one extraction."""
    first = build_description_request("sha256:same", POSTING, SONNET)
    second = build_description_request("sha256:same", POSTING, SONNET)

    assert first.cache_key == second.cache_key


# --- truncation -------------------------------------------------------------


def test_a_normal_posting_is_untouched() -> None:
    body, truncated = truncate_description(POSTING)

    assert body == POSTING
    assert truncated is False
    assert build_description_request("sha256:aaa", POSTING, SONNET).truncated is False


def test_truncation_keeps_both_ends() -> None:
    """The tail carries the eligibility language.

    A head-only truncation would discard visa, residence and pay statements --
    the dimensions that later become hard gates -- and bias every long posting
    toward "the posting did not say" on precisely the facts that decide
    whether the job is reachable at all.
    """
    opening = "OPENING MARKER. "
    closing = " CLOSING MARKER: this role hires only in Brazil."
    long_posting = opening + ("filler sentence. " * 5000) + closing

    body, truncated = truncate_description(long_posting)

    assert truncated is True
    assert len(body) <= MAX_DESCRIPTION_CHARS
    assert body.startswith(opening)
    assert body.endswith(closing)
    assert ELISION in body


def test_truncation_is_recorded_on_the_request() -> None:
    """An extraction that read part of a posting must stay distinguishable from
    one that read all of it."""
    long_posting = "filler sentence. " * 5000

    assert build_description_request("sha256:aaa", long_posting, SONNET).truncated is True


def test_the_length_budget_is_pinned() -> None:
    """Changing this is a prompt-version change, and this test is the reminder.

    The cache key cannot see the budget, so lowering it without bumping
    DESCRIPTION_PROMPT_VERSION would serve cached answers read from text the
    new budget would have withheld. The value is 2.2x the longest normalised
    description in the 18,549-job corpus (18,509 characters), so it fires on
    nothing we hold and exists only to make a pathological posting fail
    predictably.
    """
    assert MAX_DESCRIPTION_CHARS == 40_000


# --- the provider family, and the calls it does not make --------------------


def test_nothing_needing_interpretation_means_no_call_at_all() -> None:
    """The zero-call rule.

    A record holding only an employment type is fully resolved by
    `domain/provider_values.py`. Paying ~1,800 tokens to have a model agree
    with a lookup table is the waste measurement exists to catch.
    """
    assert build_provider_request("greenhouse", [EMPLOYMENT], MAP, SONNET) is None


def test_an_empty_record_means_no_call_at_all() -> None:
    assert build_provider_request("greenhouse", [], MAP, SONNET) is None


def test_only_the_values_needing_interpretation_are_sent() -> None:
    """Geography reaches the model; the closed sets do not."""
    built = build_provider_request("greenhouse", [GEOGRAPHY, EMPLOYMENT], MAP, SONNET)

    assert built is not None
    assert "Remote - United States" in built.request.user
    assert "FullTime" not in built.request.user
    assert "employment_type_hint" not in built.request.user


def test_the_model_visible_input_is_exactly_the_cache_identity() -> None:
    """The property that makes cross-job reuse safe.

    `observation_block` is not a summary of the input -- it *is* the input. If
    these two ever diverge, two postings could share a key while asking
    different questions, and the wrong answer would be served with no error
    anywhere.
    """
    built = build_provider_request("greenhouse", [GEOGRAPHY, EMPLOYMENT], MAP, SONNET)

    assert built is not None
    assert built.request.user == observation_block("greenhouse", [GEOGRAPHY])


def test_two_jobs_differing_only_outside_geography_share_one_call() -> None:
    """A direct consequence of filtering before keying.

    One posting is full-time and one is a contract; both are remote in the
    United States. The geography question is identical, so it is asked once.
    """
    contract = obs(MetadataDimension.EMPLOYMENT_TYPE_HINT, "metadata.employment_type", "Contract")

    first = build_provider_request("greenhouse", [GEOGRAPHY, EMPLOYMENT], MAP, SONNET)
    second = build_provider_request("greenhouse", [GEOGRAPHY, contract], MAP, SONNET)

    assert first is not None and second is not None
    assert first.cache_key == second.cache_key


def test_the_provider_call_is_never_given_the_posting_text() -> None:
    built = build_provider_request("greenhouse", [GEOGRAPHY], MAP, SONNET)

    assert built is not None
    assert "Business Technology Analyst" not in built.model_visible_input
    assert "own our internal systems" not in built.model_visible_input


def test_the_provider_request_carries_its_own_versions() -> None:
    """The provider transport versions independently of the description one,
    because it narrowed at v2 while the description transport did not change."""
    built = build_provider_request("greenhouse", [GEOGRAPHY], MAP, SONNET)

    assert built is not None
    assert built.request.family is Family.PROVIDER
    assert built.prompt_version == PROVIDER_PROMPT_VERSION
    assert built.transport_version == PROVIDER_TRANSPORT_VERSION


def test_the_field_map_digest_reaches_the_key() -> None:
    """A mapping change alters the call's input while the payload is unchanged.

    Without this the stale extraction survives a mapping-contract change
    invisibly, which is the failure mode `field_map_version` exists for.
    """
    remapped = field_map_version("greenhouse", ("offices.0.name",), ("hiring_location_hint",))

    first = build_provider_request("greenhouse", [GEOGRAPHY], MAP, SONNET)
    second = build_provider_request("greenhouse", [GEOGRAPHY], remapped, SONNET)

    assert first is not None and second is not None
    assert first.cache_key != second.cache_key


# --- schemas ----------------------------------------------------------------


def test_each_request_gets_its_own_schema_object() -> None:
    """Adapters legitimately rewrite the schema they are handed -- inlining
    `$defs`, adding a vendor's required flags. A shared mutable dict would let
    one vendor's rewrite leak into the next request."""
    first = description_schema()
    first["properties"]["observed_title"]["description"] = "mutated by a vendor adapter"

    assert "description" not in description_schema()["properties"]["observed_title"]


def test_the_two_families_ask_for_different_shapes() -> None:
    """The provider family narrowed to geography at v2. If it ever grew back to
    the description family's shape, the split would have lost its point."""
    assert "responsibilities" in description_schema()["properties"]
    assert "responsibilities" not in provider_schema()["properties"]

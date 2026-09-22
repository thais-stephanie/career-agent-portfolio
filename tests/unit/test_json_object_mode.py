"""The third enforcement mode, and why it is not a rounding of its neighbours.

Two free routes have now failed on the ENVELOPE rather than on the shape:
Gemma appended a markdown fence after a complete JSON document, and Nemotron
opened with two braces. Neither is a schema violation. A JSON-mode guarantee --
"one valid JSON document, shape unenforced" -- is aimed exactly at that class of
failure, and several free endpoints advertise `response_format` while not
advertising `structured_outputs`.

Reading such an endpoint as PLAIN_JSON would give up a guarantee it offers.
Reading it as STRICT_SCHEMA would claim one it does not.

    STRICT_SCHEMA   this shape, enforced
    JSON_OBJECT     one valid JSON document, shape unenforced
    PLAIN_JSON      nothing enforced; the instructions are the only ask

Everything here is offline.
"""

from __future__ import annotations

import json

import pytest

from career_agent.llm.cache import static_digest
from career_agent.llm.client import Family, ModelConfig, StructuredOutput
from career_agent.llm.requests import build_description_request
from career_agent.llm.vendors import anthropic as anthropic_adapter
from career_agent.llm.vendors import google as google_adapter
from career_agent.llm.vendors import openai as openai_adapter
from career_agent.llm.vendors import openai_compatible

POSTING = "We are hiring a Business Technology Analyst in Amsterdam."
MINIMAX = "minimax/minimax-m3:free"


def arm(vendor: str, identifier: str, mode: StructuredOutput) -> ModelConfig:
    return ModelConfig(vendor=vendor, identifier=identifier, structured_output=mode)


def body(config: ModelConfig, builder) -> dict:
    built = build_description_request("sha256:a", POSTING, config)
    return builder(built.request, config)


# =========================================================================
# 1. THE CHAT-COMPLETIONS ROUTE -- the one this round needs
# =========================================================================


def test_json_mode_sends_the_envelope_guarantee_and_not_the_schema() -> None:
    """`{"type": "json_object"}` and nothing else. The schema stays home."""
    payload = body(
        arm("openrouter", MINIMAX, StructuredOutput.JSON_OBJECT),
        openai_compatible.build_payload,
    )

    assert payload["response_format"] == {"type": "json_object"}
    # The schema must not travel. Checked on `response_format` alone: the
    # system prompt names our dimensions legitimately, and searching the whole
    # body for a property name would only find description_v6 doing its job.
    enforcement = json.dumps(payload["response_format"])
    assert "json_schema" not in enforcement
    assert "observed_title" not in enforcement
    assert payload["max_tokens"] == 8_000
    assert "max_completion_tokens" not in payload


def test_plain_json_still_sends_no_response_format_at_all() -> None:
    """The neighbour that must not move. PLAIN_JSON asks for nothing."""
    payload = body(
        arm("cerebras", "gpt-oss-120b", StructuredOutput.PLAIN_JSON),
        openai_compatible.build_payload,
    )
    assert "response_format" not in payload


def test_strict_schema_still_sends_the_whole_enforced_schema() -> None:
    """The other neighbour. Unchanged by the arrival of a third mode."""
    payload = body(
        arm("openrouter", "z-ai/glm-5.2:free", StructuredOutput.STRICT_SCHEMA),
        openai_compatible.build_payload,
    )
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["strict"] is True


def test_no_mode_filters_provider_routing_by_parameter_name() -> None:
    """The GLM 404, avoided rather than re-learned.

    `require_parameters` filters on parameter NAMES, and on a single-endpoint
    free slug that is one more way to be filtered to zero candidates -- which is
    exactly what the 404 was. It is sent under no mode now, for no model.

    MiniMax has no recorded endpoint, so it gets no `provider` block at all: a
    pin nobody observed would be a claim about a machine nobody looked at.
    """
    for mode in StructuredOutput:
        payload = body(arm("openrouter", MINIMAX, mode), openai_compatible.build_payload)
        assert "require_parameters" not in json.dumps(payload)
        assert "provider" not in payload.get("extra_body", {})
        assert "provider" not in payload


# =========================================================================
# 2. EVERY OTHER ADAPTER EITHER SPEAKS IT OR REFUSES IT
# =========================================================================


def test_gemini_spells_the_envelope_guarantee_as_a_mime_type() -> None:
    """Same guarantee, different grammar. The schema is deliberately absent."""
    generation = body(
        arm("google", "gemma-4-31b-it", StructuredOutput.JSON_OBJECT),
        google_adapter.build_payload,
    )["generationConfig"]

    assert generation["responseMimeType"] == "application/json"
    assert "responseSchema" not in generation, "that is the whole difference between the modes"


def test_the_responses_api_spells_it_as_a_text_format() -> None:
    payload = body(
        arm("openai", "gpt-5.6-luna", StructuredOutput.JSON_OBJECT),
        openai_adapter.build_payload,
    )
    assert payload["text"] == {"format": {"type": "json_object"}}


def test_anthropic_refuses_a_mode_it_cannot_express() -> None:
    """Refusal, not the nearest thing it could send.

    Anthropic's structured output IS the forced tool call: the whole schema or
    nothing, with no "valid JSON, any shape" in between. Quietly sending the
    plain request would record a benchmark row claiming an envelope guarantee
    that was never in force -- the same class of lie as reporting a reasoning
    setting the model never received.
    """
    with pytest.raises(ValueError, match="no JSON_OBJECT mode"):
        body(
            arm("anthropic", "claude-sonnet-5", StructuredOutput.JSON_OBJECT),
            anthropic_adapter.build_payload,
        )


# =========================================================================
# 3. IDENTITY -- three modes, three arms
# =========================================================================


def test_every_mode_is_its_own_cache_identity() -> None:
    """An answer given under one guarantee may never be served as another.

    The metric this protects is first-attempt pass rate, which is exactly the
    number the difference shows up in.

    Counted against the enum rather than against a literal, so a fourth mode
    cannot be added without being distinct -- `FUNCTION_CALL` arrived after this
    was written and had to earn its own identity like the other three.
    """
    built = build_description_request(
        "sha256:a", POSTING, arm("openrouter", MINIMAX, StructuredOutput.JSON_OBJECT)
    )
    digests = {
        mode.value: static_digest(built.request.system, built.request.schema, mode.value)
        for mode in StructuredOutput
    }
    assert len(set(digests.values())) == len(StructuredOutput), digests

    keys = {
        mode.value: build_description_request(
            "sha256:a", POSTING, arm("openrouter", MINIMAX, mode)
        ).cache_key.key
        for mode in StructuredOutput
    }
    assert len(set(keys.values())) == len(StructuredOutput), keys


def test_the_arm_label_stays_the_model_and_not_the_mode() -> None:
    """The mode is per family; folding it into the label would give one run two
    arm names and make every per-model report ambiguous."""
    labels = {arm("openrouter", MINIMAX, mode).arm for mode in StructuredOutput}
    assert labels == {MINIMAX}


def test_a_family_cannot_be_given_two_modes_at_once() -> None:
    """One family, one mode, and it is part of the arm's identity."""
    import typer

    from career_agent.cli_extract import _resolve_output_modes

    assert _resolve_output_modes([], ["description"]) == (
        (Family.DESCRIPTION, StructuredOutput.JSON_OBJECT),
    )
    assert _resolve_output_modes(["provider"], ["description"]) == (
        (Family.PROVIDER, StructuredOutput.PLAIN_JSON),
        (Family.DESCRIPTION, StructuredOutput.JSON_OBJECT),
    )
    with pytest.raises(typer.BadParameter, match="two output modes"):
        _resolve_output_modes(["description"], ["description"])
    with pytest.raises(typer.BadParameter, match="not a family"):
        _resolve_output_modes([], ["descriptions"])

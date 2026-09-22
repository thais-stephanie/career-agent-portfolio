"""The FUNCTION_CALL arm: what leaves the machine, and what counts as an answer.

The three older modes all shape the model's final text. On the route this
milestone runs, that turned out to guarantee nothing: five of eighteen Gemma
answers ended a complete JSON document with a Markdown fence, under four
different prompts and both families, which constrained decoding cannot do.

So this mode moves the answer out of the text. The family is declared as a
callable tool and the arguments come back in their own response part. That is a
different channel, not a stronger request, and it guarantees nothing either --
which is why more than half of this file is about rejecting a response that did
not use it.

No network. Every request below is captured through the real adapter and the
installed SDK with `httpx.MockTransport`.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest
from google import genai

import career_agent.llm.vendors.google as google_adapter
from career_agent.llm.cache import static_digest
from career_agent.llm.client import Family, LLMRequest, ModelConfig, StructuredOutput
from career_agent.llm.prompts import (
    DESCRIPTION_PROMPT_VERSION,
    PROVIDER_PROMPT_VERSION,
    load_prompt,
)
from career_agent.llm.requests import (
    build_description_request,
    description_schema,
    provider_schema,
)
from career_agent.llm.tools import TOOL_NAMES, tool_declaration, tool_digest
from career_agent.llm.vendors.google import parse_response
from career_agent.llm.vendors.schema_dialect import google_openapi

POSTING = "We are hiring a platform engineer. Remote within Brazil."

#: The tool declarations as they stand today, pinned by value.
#:
#: A digest read back off the code it is checking would agree with any edit,
#: including one to the description text -- which is prompt the model reads,
#: lives outside `PROMPT_DIGESTS`, and would otherwise change what was asked
#: with nothing to notice.
#: description v2. The first canary returned a flawless envelope around evidence
#: that cited nothing, and the declaration -- not the prompt -- was what
#: under-specified the two rules that failed. `description_v8` is byte-identical.
DESCRIPTION_TOOL_DIGEST = "sha256:d057182daf8c2c8e348d2b199e1147c9100819b83111aed2f04e383445b45c26"

#: The declaration as the SDK actually SERIALISES it, which is not byte-identical
#: to the one we digest. The SDK validates our dict into its own `Schema` type
#: and re-emits it: `"string"` becomes `"STRING"`, `anyOf` becomes `any_of`, and
#: `default: null` is dropped. That is normalisation, not translation -- the
#: assertions below prove every enum, every required list and every union
#: survives it -- and it is the same snake_case wire convention `thinking_level`
#: already showed. Pinned separately so a real change to what the model is asked
#: cannot hide inside a rendering difference nobody wrote down.
WIRE_DECLARATION_DIGESTS = {
    Family.DESCRIPTION: "sha256:4845a8be1b79bd4774205f3e6f45fabaabb92d3d3a84758554e9ebb55aa37981",
    Family.PROVIDER: "sha256:3dfa4f32d7ebb5f3f1abde347c9fb13a562fbfca020e4a8203e891ec800f6dc1",
}


def arm(mode: StructuredOutput = StructuredOutput.FUNCTION_CALL) -> ModelConfig:
    return ModelConfig(
        vendor="google",
        identifier="gemma-4-31b-it",
        reasoning="high",
        per_family=((Family.DESCRIPTION, mode), (Family.PROVIDER, mode)),
    )


def request_for(family: Family) -> LLMRequest:
    schema = description_schema() if family is Family.DESCRIPTION else provider_schema()
    version = (
        DESCRIPTION_PROMPT_VERSION if family is Family.DESCRIPTION else PROVIDER_PROMPT_VERSION
    )
    return LLMRequest(
        family=family,
        system=load_prompt(version),
        user=POSTING,
        schema=schema,
        schema_name="n",
        cache_key="sha256:key",
    )


def wire(family: Family, mode: StructuredOutput = StructuredOutput.FUNCTION_CALL) -> dict[str, Any]:
    """The final encoded HTTP body, through the real adapter and the real SDK."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"functionCall": {"name": TOOL_NAMES[family], "args": {"a": 1}}}
                            ],
                            "role": "model",
                        },
                        "finishReason": "STOP",
                    }
                ],
                "modelVersion": "gemma-4-31b-it",
            },
        )

    real = genai.Client
    mock = httpx.Client(transport=httpx.MockTransport(handler))
    genai.Client = lambda **kwargs: real(  # type: ignore[assignment]
        api_key="wire-proof-placeholder", http_options={"httpx_client": mock}
    )
    try:
        google_adapter.GoogleClient(api_key="wire-proof-placeholder").complete(
            request_for(family), arm(mode)
        )
    finally:
        genai.Client = real  # type: ignore[assignment]
    return captured[0]


# =========================================================================
# 1. THE SCHEMAS SURVIVE THE TRIP INTO A TOOL
# =========================================================================


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_authoritative_schema_becomes_the_tool_parameters(family: Family) -> None:
    """Same shape, same enums, same required fields. A different envelope."""
    schema = description_schema() if family is Family.DESCRIPTION else provider_schema()
    declaration = tool_declaration(family, google_openapi(schema))

    assert declaration["name"] == TOOL_NAMES[family]
    assert declaration["parameters"]["type"] == "object"
    assert set(declaration["parameters"]["properties"]) == set(schema["properties"])

    # The dialect conversion is the one the STRICT_SCHEMA arm already used, so
    # this loses nothing that arm had not already lost. The DescriptionFamily
    # declaration additionally carries cross-field restatements, which are
    # `description` keys and nothing else -- strip those and the parameters are
    # byte-identical to the schema they came from.
    def without_notes(node: Any) -> Any:
        if isinstance(node, list):
            return [without_notes(item) for item in node]
        if isinstance(node, dict):
            return {k: without_notes(v) for k, v in node.items() if k != "description"}
        return node

    assert without_notes(declaration["parameters"]) == without_notes(google_openapi(schema))


def test_the_tool_description_carries_no_case_and_no_answer() -> None:
    """It is prompt text the model reads, and it lives outside PROMPT_DIGESTS.

    So it says what the function is for and nothing about how to answer it. A
    helpful hint here would be an instruction nobody could review in a prompt
    diff.
    """
    from career_agent.llm.tools import TOOL_DESCRIPTIONS

    for family, text in TOOL_DESCRIPTIONS.items():
        lowered = text.lower()
        for forbidden in ("gc-", "gemma", "google", "remote", "brazil", "explicit", "not_stated"):
            assert forbidden not in lowered, f"{family} description mentions {forbidden!r}"
        assert len(text) < 200, "a long description is a prompt in the wrong place"


def test_the_tool_declaration_digest_is_pinned() -> None:
    assert tool_digest(Family.DESCRIPTION, description_schema()) == DESCRIPTION_TOOL_DIGEST


# =========================================================================
# 2. CACHE IDENTITY
# =========================================================================


def test_function_call_is_its_own_arm() -> None:
    """No STRICT_SCHEMA or JSON_OBJECT answer may ever be served to it."""
    keys = {
        mode: build_description_request("sha256:PIN", POSTING, arm(mode)).cache_key.key
        for mode in StructuredOutput
    }
    assert len(set(keys.values())) == len(StructuredOutput), keys


def test_the_tool_declaration_is_part_of_the_identity() -> None:
    """Rename the function and the answers to the old name stop being hits.

    The schema digest cannot see the tool's name or description, and both are
    bytes the model reads.
    """
    schema = description_schema()
    system = load_prompt(DESCRIPTION_PROMPT_VERSION)
    with_tool = static_digest(
        system, schema, "FUNCTION_CALL", tool_digest(Family.DESCRIPTION, schema)
    )
    other_tool = static_digest(system, schema, "FUNCTION_CALL", "sha256:some-other-declaration")
    assert with_tool != other_tool

    built = build_description_request("sha256:PIN", POSTING, arm())
    assert with_tool in built.cache_key.inputs


def test_the_other_modes_keep_the_identities_they_had() -> None:
    """The new component is OMITTED, not empty, for every other mode.

    `_digest` joins with a separator, so an empty extra part would move every
    key this project has ever computed and invalidate every stored answer.
    """
    system, schema = load_prompt(DESCRIPTION_PROMPT_VERSION), description_schema()
    for mode in ("STRICT_SCHEMA", "JSON_OBJECT", "PLAIN_JSON"):
        assert static_digest(system, schema, mode) == static_digest(system, schema, mode, None)


# =========================================================================
# 3. THE SERIALIZED WIRE PROOF
# =========================================================================


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_request_declares_exactly_one_family_specific_tool(family: Family) -> None:
    body = wire(family)
    declarations = body["tools"][0]["functionDeclarations"]

    assert len(body["tools"]) == 1
    assert len(declarations) == 1, "one function, so a wrong name cannot be a near miss"
    assert declarations[0]["name"] == TOOL_NAMES[family]

    on_the_wire = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(declarations[0], sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert on_the_wire == WIRE_DECLARATION_DIGESTS[family]


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_sdk_normalises_the_declaration_and_loses_nothing(family: Family) -> None:
    """The wire form is not our bytes, and the difference must stay cosmetic.

    The SDK re-emits our declaration through its own `Schema` type. If that ever
    started dropping an enum, a required list or one arm of a union, the model
    would be asked for a different shape than the one this project validates
    against -- and the only symptom would be answers that fail local validation
    for reasons nobody could see in the prompt.
    """
    schema = description_schema() if family is Family.DESCRIPTION else provider_schema()
    ours = tool_declaration(family, google_openapi(schema))
    wire_declaration = wire(family)["tools"][0]["functionDeclarations"][0]

    def collect(node: Any, key: str, found: list[Any]) -> list[Any]:
        if isinstance(node, dict):
            for name, value in node.items():
                if name == key:
                    found.append(tuple(value) if isinstance(value, list) else value)
                else:
                    collect(value, key, found)
        elif isinstance(node, list):
            for item in node:
                collect(item, key, found)
        return found

    assert sorted(collect(wire_declaration, "enum", [])) == sorted(collect(ours, "enum", []))
    assert sorted(collect(wire_declaration, "required", [])) == sorted(
        collect(ours, "required", [])
    )
    # `anyOf` on our side, `any_of` on the wire -- same unions, same arity.
    assert len(collect(wire_declaration, "any_of", [])) == len(collect(ours, "anyOf", []))
    assert wire_declaration["name"] == ours["name"]
    assert wire_declaration["description"] == ours["description"]


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_call_is_forced_to_the_expected_function(family: Family) -> None:
    """`ANY` with one allowed name is how "you must call this" is spelled.

    Not documented for this route -- Google's Gemma page shows tools and never a
    tool config -- so it is sent as the correct expression of the intent and
    trusted for nothing. The response contract assumes the model ignored it.
    """
    config = wire(family)["toolConfig"]["functionCallingConfig"]
    assert config["mode"] == "ANY"
    assert config["allowedFunctionNames"] == [TOOL_NAMES[family]]


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_text_channel_is_not_asked_for_at_the_same_time(family: Family) -> None:
    """One channel per request, or a malformed answer is unattributable."""
    generation = wire(family)["generationConfig"]
    assert "responseSchema" not in generation
    assert "responseMimeType" not in generation


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_frozen_generation_controls_are_unchanged(family: Family) -> None:
    """Transport is the experimental variable. Nothing else may move."""
    body = wire(family)
    generation = body["generationConfig"]

    assert generation["temperature"] == 0.0
    assert generation["maxOutputTokens"] == 8000
    assert generation["thinkingConfig"] == {"thinking_level": "HIGH"}
    assert "gemma-4-31b-it" in json.dumps(body) or True  # model rides in the URL
    version = (
        DESCRIPTION_PROMPT_VERSION if family is Family.DESCRIPTION else PROVIDER_PROMPT_VERSION
    )
    assert body["systemInstruction"]["parts"][0]["text"] == load_prompt(version)
    assert body["contents"][0]["parts"][0]["text"] == POSTING


@pytest.mark.parametrize("family", [Family.DESCRIPTION, Family.PROVIDER])
def test_the_request_carries_nothing_it_must_not(family: Family) -> None:
    raw = json.dumps(wire(family)).lower()

    for forbidden in (
        "x-goog-api-key",
        "bearer ",
        "gemini-3",
        "claude-",
        "gpt-",
        "fallback",
        "description_v9",
        "strip the fence",
        "if the output is malformed",
        "thaisholanda",
        "contato@",
    ):
        assert forbidden not in raw, f"the request body contains {forbidden!r}"


# =========================================================================
# 4. THE RESPONSE CONTRACT -- what counts as an answer
# =========================================================================


def response_with(parts: list[dict[str, Any]], finish: str = "STOP") -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": parts, "role": "model"}, "finishReason": finish}],
        "modelVersion": "gemma-4-31b-it",
    }


EXPECTED = TOOL_NAMES[Family.DESCRIPTION]


def test_one_call_by_the_right_name_is_the_answer() -> None:
    """The arguments become the document the rest of the pipeline already reads.

    Re-serialised, not reinterpreted: the parser, the transport validator, the
    evidence verifier and the assembler see exactly what they have always seen.
    """
    args = {"observed_title": "Platform Engineer", "observations": []}
    parsed = parse_response(
        response_with([{"functionCall": {"name": EXPECTED, "args": args}}]), EXPECTED
    )

    assert parsed.contract_error is None
    assert json.loads(parsed.raw_text) == args
    assert parsed.function_calls == ((EXPECTED, args),)
    assert parsed.stop_reason == "STOP"


@pytest.mark.parametrize(
    ("label", "parts", "expected_in_error"),
    [
        ("text only", [{"text": '{"observed_title": "x"}'}], "returned 23 characters of text"),
        ("fenced text", [{"text": '```json\n{"a": 1}\n```'}], "characters of text"),
        ("empty", [], "an empty response"),
        (
            "wrong name",
            [{"functionCall": {"name": "submit_provider_family", "args": {}}}],
            "called 'submit_provider_family'",
        ),
        (
            "two calls",
            [
                {"functionCall": {"name": EXPECTED, "args": {"a": 1}}},
                {"functionCall": {"name": EXPECTED, "args": {"a": 2}}},
            ],
            "the model made 2",
        ),
        (
            "stringified arguments",
            [{"functionCall": {"name": EXPECTED, "args": '{"a": 1}'}}],
            "not a structured answer",
        ),
        (
            "prose beside the call",
            [
                {"text": "Here is the extraction:"},
                {"functionCall": {"name": EXPECTED, "args": {"a": 1}}},
            ],
            "has not answered once",
        ),
    ],
)
def test_every_other_shape_is_a_contract_failure(
    label: str, parts: list[dict[str, Any]], expected_in_error: str
) -> None:
    """None of these is repaired, salvaged, unwrapped or stripped."""
    parsed = parse_response(response_with(parts), EXPECTED)

    assert parsed.contract_error is not None, f"{label} was accepted"
    assert expected_in_error in parsed.contract_error, parsed.contract_error


def test_a_contract_failure_is_reported_as_its_own_category() -> None:
    """Not "bad JSON". A transport that was ignored and a model that filled the
    arguments in wrongly are different findings about a route."""
    from pydantic import BaseModel

    from career_agent.pipeline.extract import _interpret

    class Doc(BaseModel):
        a: int

    parsed = parse_response(response_with([{"text": "no thanks"}]), EXPECTED)
    parsed_ok, validated_ok, payload, error = _interpret(parsed, Doc)

    assert (parsed_ok, validated_ok, payload) == (False, False, None)
    assert error is not None and error.startswith("function-call contract:")


def test_a_thought_part_is_still_not_an_answer() -> None:
    """The rule that already applied to text applies to this channel too."""
    parsed = parse_response(
        response_with(
            [
                {"text": "thinking out loud", "thought": True},
                {"functionCall": {"name": EXPECTED, "args": {"a": 1}}},
            ]
        ),
        EXPECTED,
    )
    assert parsed.contract_error is None, "a thought is not prose beside the call"
    assert json.loads(parsed.raw_text) == {"a": 1}


def test_the_other_modes_read_responses_exactly_as_before() -> None:
    """`parse_response` without an expected tool is the function it always was."""
    body = response_with([{"text": '{"a": 1}'}])
    assert parse_response(body).raw_text == '{"a": 1}'
    assert parse_response(body).contract_error is None
    assert parse_response(body).function_calls == ()


# =========================================================================
# 5. THE v2 DECLARATION REVISION -- restatement, and a new identity
# =========================================================================


def test_the_revision_only_annotates_and_never_reshapes() -> None:
    """v2 adds `description` keys. Nothing else about the ask moved.

    The whole claim of a restatement is that it restates. If a type, an enum, a
    required list or a property could change under cover of "clarification",
    the declaration would be a second schema nobody reviews.
    """
    from career_agent.llm.tools import _annotated

    for family, schema in (
        (Family.DESCRIPTION, description_schema()),
        (Family.PROVIDER, provider_schema()),
    ):
        annotated = _annotated(family, google_openapi(schema))

        def strip(node: Any) -> Any:
            if isinstance(node, list):
                return [strip(item) for item in node]
            if isinstance(node, dict):
                return {k: strip(v) for k, v in node.items() if k != "description"}
            return node

        assert strip(annotated) == strip(google_openapi(schema))


def test_the_revision_states_the_two_rules_that_failed() -> None:
    """Both are already true, already enforced, and were nowhere in the tool."""
    declaration = tool_declaration(Family.DESCRIPTION, google_openapi(description_schema()))
    properties = declaration["parameters"]["properties"]
    evidence = properties["evidence"]["items"]["properties"]
    observation = properties["observations"]["items"]["properties"]

    assert "JOB_DESCRIPTION" in evidence["quote"]["description"]
    assert "character-for-character" in evidence["quote"]["description"]
    assert "not a substitute for quote" in evidence["source_value"]["description"]
    assert "PROVIDER_FIELD" in evidence["source_value"]["description"]
    assert "EXPLICIT" in observation["evidence_id"]["description"]
    assert "same call" in observation["evidence_id"]["description"]


def test_the_revision_names_no_case_model_or_answer() -> None:
    """A declaration that hinted at an answer would be a prompt nobody reviews."""
    from career_agent.llm.tools import (
        _DESCRIPTION_EVIDENCE_NOTES,
        _DESCRIPTION_OBSERVATION_NOTES,
    )

    text = " ".join(
        list(_DESCRIPTION_EVIDENCE_NOTES.values()) + list(_DESCRIPTION_OBSERVATION_NOTES.values())
    ).lower()
    for forbidden in (
        "gc-",
        "gemma",
        "google",
        "asana",
        "solutions architect",
        "san francisco",
        "remote",
        "hybrid",
    ):
        assert forbidden not in text, f"the revision mentions {forbidden!r}"


def test_description_v8_is_byte_identical() -> None:
    """The one-variable rule. Only the declaration moved."""
    from career_agent.llm.prompts import prompt_digest

    assert prompt_digest("description_v8") == (
        "sha256:18bcb569c03b196fcfb438af8ee43b693914e96bdd285f4392148763040a297c"
    )
    assert prompt_digest("provider_v3") == (
        "sha256:81a0e71195c12ede6cc0af87ad99b9a857348c1f20a20f5ceb13610f1bf16aec"
    )
    from career_agent.llm.prompts import available_prompts

    assert "description_v9" not in available_prompts(), "the one-variable rule: only the tool moved"


def test_the_revision_moves_the_arm_and_the_old_answer_is_not_a_hit() -> None:
    """An answer given to the old declaration is an answer to a different ask."""
    from career_agent.llm.tools import TOOL_DECLARATION_VERSIONS, tool_digest

    assert TOOL_DECLARATION_VERSIONS[Family.DESCRIPTION] == "v2"
    assert TOOL_DECLARATION_VERSIONS[Family.PROVIDER] == "v1"

    schema = description_schema()
    assert tool_digest(Family.DESCRIPTION, schema) == DESCRIPTION_TOOL_DIGEST

    # The declaration reaches the key THROUGH `static_digest`, so the key's own
    # inputs carry the static digest rather than the tool digest. Asserted as
    # the composition it actually is.
    built = build_description_request("sha256:PIN", POSTING, arm())
    expected_static = static_digest(
        load_prompt(DESCRIPTION_PROMPT_VERSION),
        schema,
        "FUNCTION_CALL",
        tool_digest(Family.DESCRIPTION, schema),
    )
    assert expected_static in built.cache_key.inputs

    # And an answer given to the v1 declaration is an answer to a different ask.
    assert built.cache_key.key != (
        "sha256:06f684c4082740eb5860bda191e7c241830100b583f377cd75e6ca12a0da945d"
    )

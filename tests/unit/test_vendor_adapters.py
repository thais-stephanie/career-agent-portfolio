"""Three vendors, one port, and no live call anywhere in this file.

M2 Phase A authorises adapters that are *structural only*. What that means
concretely is tested here: each adapter can turn a vendor-neutral `LLMRequest`
into its vendor's request shape, and turn a recorded vendor response back into
an `LLMResponse` -- with no SDK installed, no credentials, and no network.

The last assertion is the one that would fail silently otherwise: no vendor
type escapes an adapter. If an SDK object reached `pipeline/` the port would
have leaked, and nothing would break until the second vendor was added.
"""

import ast
import json
import os
from pathlib import Path

import pytest

from career_agent.llm.client import Family, LLMRequest, LLMResponse, ModelConfig, Runner
from career_agent.llm.vendors import (
    UnknownVendorError,
    anthropic,
    available_vendors,
    get_client,
    google,
    openai,
)
from career_agent.llm.vendors.schema_dialect import (
    count_optional_and_union,
    google_openapi,
    inline_refs,
    openai_strict,
)

SRC = Path(__file__).resolve().parents[2] / "src" / "career_agent"

SCHEMA = {
    "$defs": {
        "Row": {
            "type": "object",
            "properties": {
                "dimension": {"type": "string"},
                "value": {"anyOf": [{"type": "string"}, {"type": "null"}]},
            },
            "required": ["dimension"],
        }
    },
    "type": "object",
    "title": "TDescriptionFamily",
    "properties": {
        "observed_title": {"type": "string"},
        "observations": {"type": "array", "items": {"$ref": "#/$defs/Row"}},
    },
    "required": ["observations"],
    "additionalProperties": False,
}

REQUEST = LLMRequest(
    family=Family.DESCRIPTION,
    system="You read one job posting and report what it says.",
    user="This role is fully remote.",
    schema=SCHEMA,
    schema_name="job_description_extraction",
    cache_key="sha256:abc",
)


def config(vendor: str, **kwargs) -> ModelConfig:
    defaults = {"vendor": vendor, "identifier": f"{vendor}-model"}
    defaults.update(kwargs)
    return ModelConfig(**defaults)


# --- the registry -----------------------------------------------------------


def test_a_fourth_vendor_would_cost_one_file_and_one_entry() -> None:
    """The same test M1B applied to the ATS abstraction, applied to this port."""
    assert available_vendors() == [
        "anthropic",
        "cerebras",
        "deepseek",
        "google",
        "openai",
        "openrouter",
    ]

    for vendor in available_vendors():
        client = get_client(vendor)
        assert client.vendor == vendor
        assert client.runner is Runner.PRODUCTION_API


def test_an_unknown_vendor_names_the_ones_that_exist() -> None:
    with pytest.raises(UnknownVendorError, match="anthropic"):
        get_client("mistral")


def test_constructing_a_client_costs_nothing_and_reaches_nothing() -> None:
    """No SDK is installed here, and this still passes.

    That is the property M2 Phase A needs: the adapters are real code that can
    be reviewed and tested, and merely having them cannot produce a charge.
    """
    for vendor in available_vendors():
        assert get_client(vendor) is not None


# --- request shape, per vendor ----------------------------------------------


def test_anthropic_forces_the_schema_as_a_tool_call() -> None:
    """Structured output on this vendor is a forced tool, not a response format.

    Without `tool_choice` the model may answer in prose instead, which would
    parse as a failed extraction and be retried -- paying twice for a
    misconfiguration rather than for a hard posting.
    """
    payload = anthropic.build_payload(REQUEST, config("anthropic"))

    assert payload["system"] == REQUEST.system
    assert payload["messages"] == [{"role": "user", "content": REQUEST.user}]
    assert payload["tools"][0]["name"] == "job_description_extraction"
    assert payload["tool_choice"] == {"type": "tool", "name": "job_description_extraction"}


def test_anthropic_thinking_forces_temperature_one() -> None:
    """The vendor's rule, encoded where it cannot be forgotten.

    An arm that set both thinking and temperature 0 would be rejected, and the
    reason would not appear in any config file we wrote.
    """
    payload = anthropic.build_payload(REQUEST, config("anthropic", reasoning="high"))

    assert payload["thinking"] == {"type": "enabled", "budget_tokens": 16_000}
    assert payload["temperature"] == 1


def test_openai_rewrites_optional_properties_into_required_nullable_ones() -> None:
    """Strict mode has no optional properties, so ours cannot be forwarded.

    Sending our schema unchanged would fail on the first call -- at whatever
    moment we first paid for one.
    """
    payload = openai.build_payload(REQUEST, config("openai"))
    schema = payload["text"]["format"]["schema"]

    assert payload["text"]["format"]["strict"] is True
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["observations", "observed_title"]
    assert "$defs" not in schema


def test_google_strips_the_keywords_its_subset_rejects() -> None:
    """Gemini rejects unrecognised keywords rather than ignoring them."""
    payload = google.build_payload(REQUEST, config("google"))
    schema = payload["generationConfig"]["responseSchema"]

    flattened = json.dumps(schema)
    for rejected in ("$defs", "$ref", "additionalProperties"):
        assert rejected not in flattened

    assert payload["systemInstruction"]["parts"][0]["text"] == REQUEST.system


def test_every_vendor_keeps_the_system_prompt_and_the_source_apart() -> None:
    """One cache key has to mean one model-visible input on all three.

    If a vendor concatenated them, two calls with the same key would be sending
    different things, and a cached answer would be served for a question that
    was never asked.
    """
    payloads = {
        "anthropic": json.dumps(anthropic.build_payload(REQUEST, config("anthropic"))),
        "openai": json.dumps(openai.build_payload(REQUEST, config("openai"))),
        "google": json.dumps(google.build_payload(REQUEST, config("google"))),
    }

    for vendor, body in payloads.items():
        assert REQUEST.system in body, vendor
        assert REQUEST.user in body, vendor
        assert REQUEST.system + REQUEST.user not in body, f"{vendor} concatenated them"


def test_an_unknown_reasoning_label_is_refused_rather_than_dropped() -> None:
    """Silently ignoring it would benchmark an arm that never ran as configured."""
    for module in (anthropic, openai, google):
        with pytest.raises(ValueError, match="unknown reasoning"):
            module.build_payload(REQUEST, config(module.VENDOR, reasoning="ludicrous"))


# --- response shape, per vendor ---------------------------------------------


def test_anthropic_normalises_a_tool_call_into_text() -> None:
    """Everything downstream is defined on the text, including `llm_call.raw_output`."""
    response = anthropic.parse_response(
        {
            "model": "claude-sonnet-5",
            "stop_reason": "tool_use",
            "content": [{"type": "tool_use", "name": "x", "input": {"observed_title": "Analyst"}}],
            "usage": {"input_tokens": 5_800, "output_tokens": 900},
        }
    )

    assert json.loads(response.raw_text) == {"observed_title": "Analyst"}
    assert (response.input_tokens, response.output_tokens) == (5_800, 900)
    assert response.refusal is None


@pytest.mark.parametrize(
    ("module", "body", "expected_tokens"),
    [
        (
            openai,
            {
                "model": "gpt-x",
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": '{"a":1}'}]}],
                "usage": {"input_tokens": 10, "output_tokens": 2},
            },
            (10, 2),
        ),
        (
            google,
            {
                "modelVersion": "gemini-x",
                "candidates": [
                    {"finishReason": "STOP", "content": {"parts": [{"text": '{"a":1}'}]}}
                ],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
            },
            (10, 2),
        ),
    ],
)
def test_a_recorded_response_replays_without_the_sdk(module, body, expected_tokens) -> None:
    """A dict in, an LLMResponse out. No SDK object anywhere in between."""
    response = module.parse_response(body)

    assert isinstance(response, LLMResponse)
    assert json.loads(response.raw_text) == {"a": 1}
    assert (response.input_tokens, response.output_tokens) == expected_tokens


def test_a_refusal_is_never_recorded_as_an_empty_extraction() -> None:
    """The failure mode that would be invisible.

    A refusal yields no text. Recorded as a success it would read, forever
    after, as a posting that was examined and said nothing -- a manufactured
    fact rather than a missing one.
    """
    refusals = [
        anthropic.parse_response({"stop_reason": "refusal", "content": []}),
        openai.parse_response(
            {"output": [{"content": [{"type": "refusal", "refusal": "no thanks"}]}]}
        ),
        google.parse_response({"candidates": [{"finishReason": "SAFETY", "content": {}}]}),
    ]

    for response in refusals:
        assert response.refusal
        assert response.raw_text == ""


# --- the SDK dump, which does not speak the wire format ---------------------
#
# M2 Stage 0 made 55 live calls and recorded zero tokens. The adapter was
# reading `usageMetadata`; `GenerateContentResponse.model_dump()` from the
# declared SDK hands it `usage_metadata`. The same mismatch silently blinded
# `finishReason` and `modelVersion` beside it, and nothing failed -- the text
# came through, because `content.parts[].text` happens to be spelled the same
# either way.
#
# Every test here constructs an SDK response object in memory. None makes a
# network call.


def _sdk_response(**kwargs: object) -> dict:
    from google.genai import types

    return types.GenerateContentResponse(**kwargs).model_dump()  # type: ignore[arg-type]


def test_google_reads_usage_from_the_sdk_dump_not_only_from_the_wire_format() -> None:
    from google.genai import types

    body = _sdk_response(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[types.Part(text='{"a":1}')]),
                finish_reason=types.FinishReason.STOP,
            )
        ],
        model_version="gemini-3.1-flash-lite",
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=8_953, candidates_token_count=2_672
        ),
    )

    response = google.parse_response(body)

    assert (response.input_tokens, response.output_tokens) == (8_953, 2_672)
    assert response.stop_reason == "STOP"
    assert response.model == "gemini-3.1-flash-lite"
    assert json.loads(response.raw_text) == {"a": 1}
    assert response.refusal is None


def test_google_detects_a_refusal_in_the_sdk_dump() -> None:
    """The finish reason arrives as an enum member, not as the wire string.

    So `finish_reason` being read under the right key is not enough on its own:
    comparing `FinishReason.SAFETY` against a set of names never matches, and a
    refusal would have gone on being invisible.
    """
    from google.genai import types

    body = _sdk_response(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=[]),
                finish_reason=types.FinishReason.SAFETY,
            )
        ]
    )

    response = google.parse_response(body)

    assert response.refusal
    assert response.raw_text == ""
    assert response.stop_reason == "SAFETY"


def test_google_reports_unknown_tokens_as_unknown_when_usage_is_absent() -> None:
    """None, not zero. "We were not told" and "it cost nothing" differ.

    Take 1 of Stage 0 stored zero for 54 attempts because of the defect above.
    Those rows stay as they are -- the instrumentation was wrong when they were
    captured, and inventing counts afterwards would be manufacturing a
    measurement.
    """
    from google.genai import types

    body = _sdk_response(
        candidates=[
            types.Candidate(content=types.Content(role="model", parts=[types.Part(text="{}")]))
        ]
    )

    response = google.parse_response(body)

    assert response.input_tokens is None
    assert response.output_tokens is None
    assert response.raw_text == "{}"


def test_google_still_reads_the_rest_wire_format() -> None:
    """Both spellings, because a body legitimately arrives either way.

    Recorded fixtures and the replay path carry the camelCase REST shape;
    the live adapter passes an SDK dump. Neither is "the" format.
    """
    response = google.parse_response(
        {
            "modelVersion": "gemini-x",
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": '{"a":1}'}]}}],
            "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 2},
        }
    )

    assert (response.input_tokens, response.output_tokens) == (10, 2)
    assert response.stop_reason == "STOP"
    assert response.model == "gemini-x"


# --- the boundary -----------------------------------------------------------


def test_no_module_outside_an_adapter_imports_a_vendor_sdk() -> None:
    """The port leaking is the failure that stays invisible until vendor two.

    Checked by AST rather than by grep so that a name inside a docstring or a
    prompt cannot trip it, and an actual import cannot hide from it.
    """
    sdk_roots = {"anthropic", "openai", "google", "google.genai"}
    allowed = {
        Path("llm/vendors/anthropic.py"),
        Path("llm/vendors/openai.py"),
        Path("llm/vendors/google.py"),
        # Cerebras and OpenRouter are reached through the OpenAI SDK pointed at
        # their own base URLs. One dialect, two routes, one file that may import.
        Path("llm/vendors/openai_compatible.py"),
    }
    offenders: list[str] = []

    for path in SRC.rglob("*.py"):
        relative = path.relative_to(SRC)
        if relative in allowed:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom):
                names = {(node.module or "").split(".")[0]}
            else:
                continue
            if names & sdk_roots:
                offenders.append(f"{relative}: {sorted(names & sdk_roots)}")

    assert offenders == [], f"vendor SDK imported outside an adapter: {offenders}"


def test_each_adapter_imports_its_sdk_lazily() -> None:
    """Module scope must stay import-free so the shape is testable without the SDK.

    This whole file is the proof -- it runs with none of the three installed --
    and this asserts the property directly so the reason survives.
    """
    for module in (anthropic, openai, google):
        source = Path(module.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in tree.body:  # module scope only
            assert not isinstance(node, ast.Import | ast.ImportFrom) or not (
                {getattr(node, "module", "") or ""} | {a.name for a in getattr(node, "names", [])}
            ) & {"anthropic", "openai", "google"}


# --- the schema dialects ----------------------------------------------------


def test_inlining_terminates_on_a_recursive_schema() -> None:
    """A naive walk hangs here. The transport is not recursive today; this is
    what stops that from becoming a hang the first time one is."""
    recursive = {
        "$defs": {"Node": {"type": "object", "properties": {"child": {"$ref": "#/$defs/Node"}}}},
        "$ref": "#/$defs/Node",
    }

    assert inline_refs(recursive)  # returns rather than recursing forever


def test_inlining_keeps_keywords_written_beside_a_ref() -> None:
    """A description on the use site belongs to the use site."""
    schema = {
        "$defs": {"X": {"type": "string"}},
        "type": "object",
        "properties": {"a": {"$ref": "#/$defs/X", "description": "the a"}},
    }

    inlined = inline_refs(schema)

    assert inlined["properties"]["a"] == {"type": "string", "description": "the a"}


def test_the_limit_counter_charges_per_usage_not_per_definition() -> None:
    """Vendors bill a shared definition once per reference, so the count does too.

    Counting definitions instead would have made the M2.0 topology measurement
    look comfortably inside a limit it was actually sitting on.
    """
    optional, union = count_optional_and_union(SCHEMA)

    assert optional >= 1  # observed_title is not required
    assert union >= 1  # Row.value is a union, reached through the $ref


def test_strict_and_openapi_forms_agree_on_the_data_they_describe() -> None:
    """The dialects differ in what they forbid, not in what they mean.

    If one of them dropped a property the two vendors would be answering
    different questions, and their benchmark rows would not be comparable.
    """
    strict = openai_strict(SCHEMA)
    openapi = google_openapi(SCHEMA)

    assert (
        set(strict["properties"])
        == set(openapi["properties"])
        == {
            "observed_title",
            "observations",
        }
    )


def test_anthropic_asks_for_the_guarantee_and_not_just_the_shape() -> None:
    """`strict` is the difference between enforcement and a strong suggestion.

    A tool without it returns schema-*shaped* output that the vendor never
    constrained. The failure would look like an unusually unreliable model
    rather than like a missing flag.
    """
    payload = anthropic.build_payload(REQUEST, config("anthropic"))

    assert payload["tools"][0]["strict"] is True


def test_the_documented_limits_the_topology_was_measured_against_are_recorded() -> None:
    """24 optional and 16 union parameters, per request, combined across schemas.

    Verified against the vendor's structured-output documentation on
    2026-09-02. The compact transport exists because the durable fingerprint
    measured 191 and 107 against these two numbers.
    """
    assert anthropic.MAX_OPTIONAL_PARAMETERS == 24
    assert anthropic.MAX_UNION_PARAMETERS == 16


def test_the_transport_schema_fits_inside_the_documented_limits() -> None:
    """The tripwire, checked against the real schema rather than an estimate.

    If a future dimension pushes either count past its limit, the request stops
    being accepted -- and this fails first, offline, instead of at whatever
    moment we first paid for a call.
    """
    from career_agent.llm.requests import description_schema, provider_schema

    for name, schema in (("description", description_schema()), ("provider", provider_schema())):
        optional, union = count_optional_and_union(schema)
        assert optional <= anthropic.MAX_OPTIONAL_PARAMETERS, f"{name}: {optional} optional"
        assert union <= anthropic.MAX_UNION_PARAMETERS, f"{name}: {union} union"


def test_the_description_schema_sits_exactly_on_the_optional_limit() -> None:
    """**Zero headroom.** 24 of 24. One more optional parameter breaks every call.

    Pinned rather than bounded, because `<= 24` passes just as quietly at 24 as
    at 12 and this is the number that decides whether requests are accepted at
    all. Measured at 24/24 before the worksite change and still 24/24 after --
    worksite rides the existing string-grammar row rather than adding a
    transport field, which is why it cost nothing here.

    If this fails because the count ROSE, the request will be rejected by the
    vendor and the fix is to move a dimension onto a compact grammar, not to
    raise the constant. If it fails because the count FELL, headroom was
    recovered and the new number should be recorded here deliberately.
    """
    from career_agent.llm.requests import description_schema

    optional, union = count_optional_and_union(description_schema())

    assert optional == 24, f"optional parameter count moved to {optional}; see the docstring"
    assert union == 13, f"union parameter count moved to {union}; 16 is the limit"


def test_the_vendor_does_not_enforce_our_confidence_range() -> None:
    """A concrete reason structured output is still untrusted input.

    pydantic emits `minimum`/`maximum` for a bounded float and `pattern` for a
    formatted id. The SDK strips both before sending, so the guarantee stops
    exactly where our own validators start.
    """
    assert "pattern" in anthropic.UNENFORCED_KEYWORDS
    assert "minimum" in anthropic.UNENFORCED_KEYWORDS


# --- the OpenAI adapter against real SDK objects ----------------------------
#
# The Google adapter made 55 live calls and recorded zero tokens because it read
# `usageMetadata` while the SDK handed it `usage_metadata`. Before Stage 1 spends
# real money, the same question is asked of OpenAI: not "does the documented wire
# format match" but "does what the installed SDK actually produces match".
#
# Every response below is a real `openai.types.responses.Response`, constructed
# in memory. None makes a network call.


def _luna_response(output, status="completed", usage=None, incomplete=None):
    from openai.types.responses import Response

    fields = {
        "id": "resp_test",
        "object": "response",
        "created_at": 0.0,
        "model": "gpt-5.6-luna",
        "status": status,
        "output": output,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
    }
    if usage is not None:
        fields["usage"] = usage
    if incomplete is not None:
        fields["incomplete_details"] = incomplete
    return Response(**fields).model_dump()


def _text_message(text):
    from openai.types.responses import ResponseOutputMessage, ResponseOutputText

    return ResponseOutputMessage(
        id="msg",
        type="message",
        role="assistant",
        status="completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )


def test_openai_reads_usage_from_the_sdk_dump_not_only_from_the_wire_format() -> None:
    """The Google defect, asked of OpenAI before any money is spent.

    It passes -- the Responses API is snake_case in the SDK dump and on the wire,
    so there is nothing to reconcile. That is worth a test precisely because it
    is the assumption that cost 55 calls last time it went unchecked.
    """
    from openai.types.responses import ResponseUsage
    from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails

    usage = ResponseUsage(
        input_tokens=8_953,
        output_tokens=2_672,
        total_tokens=11_625,
        input_tokens_details=InputTokensDetails(cached_tokens=1_024, cache_write_tokens=0),
        output_tokens_details=OutputTokensDetails(reasoning_tokens=800),
    )
    parsed = openai.parse_response(_luna_response([_text_message('{"ok":1}')], usage=usage))

    assert parsed.raw_text == '{"ok":1}'
    assert parsed.model == "gpt-5.6-luna"
    assert parsed.input_tokens == 8_953
    assert parsed.output_tokens == 2_672
    assert parsed.refusal is None


def test_openai_reports_unknown_tokens_as_unknown_rather_than_zero() -> None:
    """A call the vendor did not meter must not be priced as a free one."""
    parsed = openai.parse_response(_luna_response([_text_message("x")]))

    assert parsed.input_tokens is None
    assert parsed.output_tokens is None


def test_openai_lifts_a_truncated_answer_out_of_the_json_parser() -> None:
    """A cut-off answer is a failed attempt with a cause, not a syntax error.

    Reasoning tokens count against `max_output_tokens`, so an effort setting
    raises truncation risk -- and on a paid arm every uninformative retry is
    bought twice. Without this the operator sees `Expecting ',' delimiter`
    thousands of tokens in, which does not say to raise the output ceiling.
    """
    parsed = openai.parse_response(
        _luna_response(
            [_text_message('{"observ')],
            status="incomplete",
            incomplete={"reason": "max_output_tokens"},
        )
    )

    assert parsed.refusal is not None
    assert "max_output_tokens" in parsed.refusal
    assert parsed.stop_reason == "incomplete"


def test_openai_survives_a_reasoning_item_in_the_output() -> None:
    """A reasoning item carries a summary and no content at all.

    Indexing rather than iterating `or ()` would raise here, and it would raise
    only on the arm we intend to run with an effort setting.
    """
    from openai.types.responses import ResponseReasoningItem

    parsed = openai.parse_response(
        _luna_response(
            [
                ResponseReasoningItem(id="rs", type="reasoning", summary=[]),
                _text_message("answer"),
            ]
        )
    )

    assert parsed.raw_text == "answer"


def test_the_luna_reasoning_setting_reaches_the_payload_and_the_arm() -> None:
    """Reasoning is part of the benchmark identity, not a runtime preference.

    `ModelConfig.arm` is what the cache keys on and what `llm_call.model`
    records, so two efforts are two arms and can never serve each other.
    """
    from career_agent.llm.client import ModelConfig

    arms = set()
    for effort in ("low", "medium", "high"):
        config = ModelConfig(vendor="openai", identifier="gpt-5.6-luna", reasoning=effort)
        payload = openai.build_payload(REQUEST, config)

        assert payload["reasoning"] == {"effort": effort}
        assert "temperature" not in payload, "a reasoning model rejects an explicit temperature"
        arms.add(config.arm)

    assert arms == {"gpt-5.6-luna@low", "gpt-5.6-luna@medium", "gpt-5.6-luna@high"}


# --- the chat-completions dialect, and the two routes that speak it ---------


def _chat_body(content=None, finish="stop", refusal=None, usage=True, model="gpt-oss-120b"):
    """A `ChatCompletion.model_dump()` as the OpenAI SDK produces one."""
    from openai.types.chat import ChatCompletion, ChatCompletionMessage
    from openai.types.chat.chat_completion import Choice
    from openai.types.completion_usage import CompletionUsage

    message = ChatCompletionMessage(role="assistant", content=content, refusal=refusal)
    completion = ChatCompletion(
        id="chatcmpl-test",
        object="chat.completion",
        created=0,
        model=model,
        choices=[Choice(index=0, finish_reason=finish, message=message)],
        usage=CompletionUsage(prompt_tokens=9_963, completion_tokens=2_672, total_tokens=12_635)
        if usage
        else None,
    )
    return completion.model_dump()


def test_the_chat_dialect_reads_the_sdk_dump_not_a_documented_shape() -> None:
    """Asked of every adapter before it runs, since the Google one was not.

    That adapter read `usageMetadata` while the SDK handed it `usage_metadata`,
    and 55 live calls recorded zero tokens. Here the body is a real
    `ChatCompletion`, dumped the way the client dumps it.
    """
    from career_agent.llm.vendors import openai_compatible

    parsed = openai_compatible.parse_response(_chat_body(content='{"observations":[]}'))

    assert parsed.raw_text == '{"observations":[]}'
    assert parsed.model == "gpt-oss-120b"
    assert parsed.input_tokens == 9_963
    assert parsed.output_tokens == 2_672
    assert parsed.refusal is None
    assert parsed.stop_reason == "stop"


def test_the_chat_dialect_reports_unknown_usage_as_unknown() -> None:
    from career_agent.llm.vendors import openai_compatible

    parsed = openai_compatible.parse_response(_chat_body(content="x", usage=False))

    assert parsed.input_tokens is None
    assert parsed.output_tokens is None


def test_the_chat_dialect_lifts_truncation_and_refusal_out_of_the_parser() -> None:
    """Three ways to come back with nothing usable, none of which raises."""
    from career_agent.llm.vendors import openai_compatible

    cut_off = openai_compatible.parse_response(_chat_body(content='{"obs', finish="length"))
    filtered = openai_compatible.parse_response(_chat_body(content="", finish="content_filter"))
    declined = openai_compatible.parse_response(_chat_body(refusal="I cannot help with that"))

    assert cut_off.refusal and "length" in cut_off.refusal
    assert filtered.refusal and "content_filter" in filtered.refusal
    assert declined.refusal == "I cannot help with that"


def test_cerebras_enforces_the_provider_schema_and_asks_for_the_description_one() -> None:
    """The arm's defining asymmetry, in the two payloads it actually sends.

    Cerebras caps a strict schema at 5,000 characters. Our provider schema is
    4,038 and our description schema 8,536, so the honest configuration of this
    route enforces one family and not the other -- and weakening the provider
    family for symmetry would benchmark a setup nobody would ship.
    """
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.requests import build_description_request, build_provider_request
    from career_agent.llm.vendors import openai_compatible

    config = ModelConfig(
        vendor="cerebras",
        identifier="gpt-oss-120b",
        per_family=openai_compatible.CEREBRAS_PER_FAMILY,
    )

    description = build_description_request("sha256:a", "A posting.", config)
    from career_agent.domain.enums import MetadataDimension
    from career_agent.providers.base import ProviderMetadataObservation

    observation = ProviderMetadataObservation(
        dimension=MetadataDimension.HIRING_LOCATION_HINT,
        provider="greenhouse",
        source_field="location.name",
        source_value="Remote - United States",
    )
    provider = build_provider_request("greenhouse", (observation,), "sha256:map", config)
    assert provider is not None

    sent_description = openai_compatible.build_payload(description.request, config)
    sent_provider = openai_compatible.build_payload(provider.request, config)

    assert "response_format" not in sent_description, "the description schema does not fit strict"
    assert sent_provider["response_format"]["json_schema"]["strict"] is True
    assert sent_provider["response_format"]["json_schema"]["name"]

    # And the strict one is genuinely inside the documented limit, while the
    # other genuinely is not. If either ever stopped being true the arm's whole
    # rationale would have changed.
    strict_schema = json.dumps(
        sent_provider["response_format"]["json_schema"]["schema"], separators=(",", ":")
    )
    assert len(strict_schema) < 5_000
    assert len(json.dumps(openai_strict(description.request.schema), separators=(",", ":"))) > 5_000

    assert sent_description["messages"][0]["role"] == "system"
    assert sent_description["messages"][1]["content"] == "A posting."


def test_openrouter_pins_the_endpoint_it_recorded_and_filters_for_nothing() -> None:
    """Enforcement varies by routed provider, so an unpinned arm measures a lottery.

    The pin used to be `require_parameters: true` -- ask the router for
    providers supporting every parameter sent. On a single-endpoint free slug
    that is not a pin, it is a way to lose the route, and this project has the
    404 to prove it: "No endpoints found that can handle the requested
    parameters", on the only free endpoint there is.

    `allow_fallbacks: false` says the narrower and more useful thing -- do not
    serve this from somewhere else -- and cannot empty the candidate list. It
    goes on every mode, because which machine answered is not a property of the
    output format. A pair nobody recorded gets no `provider` block at all.
    """
    from career_agent.llm.client import ModelConfig, StructuredOutput
    from career_agent.llm.vendors import openai_compatible

    for mode in (StructuredOutput.STRICT_SCHEMA, StructuredOutput.PLAIN_JSON):
        pinned = ModelConfig(
            vendor="openrouter", identifier="z-ai/glm-5.2:free", structured_output=mode
        )
        payload = openai_compatible.build_payload(REQUEST, pinned)
        assert payload["extra_body"]["provider"] == {"allow_fallbacks": False}
        assert "require_parameters" not in json.dumps(payload), "the 404, never again"

    unrecorded = ModelConfig(vendor="openrouter", identifier="some/other-model:free")
    assert "provider" not in openai_compatible.build_payload(REQUEST, unrecorded).get(
        "extra_body", {}
    ), "a pin nobody observed would be a claim about an endpoint nobody looked at"

    # Cerebras serves its own models and has nothing to route around.
    cerebras = ModelConfig(vendor="cerebras", identifier="gpt-oss-120b")
    assert "extra_body" not in openai_compatible.build_payload(REQUEST, cerebras)


def test_both_new_routes_keep_the_system_prompt_and_the_source_apart() -> None:
    """The property every adapter is held to, so one cache key means one question."""
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.vendors import openai_compatible

    for vendor, model in (("cerebras", "gpt-oss-120b"), ("openrouter", "z-ai/glm-5.2:free")):
        payload = openai_compatible.build_payload(
            REQUEST, ModelConfig(vendor=vendor, identifier=model)
        )
        roles = [message["role"] for message in payload["messages"]]

        assert roles == ["system", "user"]
        assert payload["messages"][0]["content"] == REQUEST.system
        assert payload["messages"][1]["content"] == REQUEST.user


def test_each_route_reads_its_own_credential_variable() -> None:
    """The defect that stopped the first challenger screen at call zero.

    Every other adapter lets its SDK find the key, because each SDK's default
    variable happens to be the right one. That breaks here: the OpenAI SDK looks
    for `OPENAI_API_KEY` whatever base URL it has been pointed at. A Cerebras run
    with `CEREBRAS_API_KEY` set and `OPENAI_API_KEY` unset failed with a message
    naming the wrong variable -- and one with BOTH set would have sent an OpenAI
    key to Cerebras, which is the version of this bug that does not announce
    itself.

    Presence and selection only. No test reads or asserts a key's value.
    """
    from career_agent.llm.vendors import openai_compatible

    assert openai_compatible.ROUTES["cerebras"].credential_variable == "CEREBRAS_API_KEY"
    assert openai_compatible.ROUTES["openrouter"].credential_variable == "OPENROUTER_API_KEY"

    for vendor, variable in (
        ("cerebras", "CEREBRAS_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
    ):
        client = get_client(vendor)
        assert client.route.credential_variable == variable

        os.environ[variable] = "sentinel-not-a-real-key"
        os.environ["OPENAI_API_KEY"] = "a-different-vendors-key"
        try:
            assert client._credential() == "sentinel-not-a-real-key"
        finally:
            del os.environ[variable]
            del os.environ["OPENAI_API_KEY"]

        assert client._credential() is None, "an absent variable must not fall back"


def test_a_client_that_cannot_be_built_reports_that_nothing_was_sent() -> None:
    """Construction belonged inside the guard, and was beside it.

    `openai.OpenAI(...)` raises the SDK's own error type when it has no
    credential. Outside the try that is not an `LLMError`, so it escaped `_ask`,
    aborted the run and lost every finished posting -- which is what happened on
    call zero of the first challenger screen.

    It raises `LLMNotSent` rather than `LLMUnavailable`, and the difference is
    the vendor's reputation and a free tier's arithmetic: nothing was asked, so
    nothing was refused and no quota moved.
    """
    from career_agent.llm.client import LLMNotSent
    from career_agent.llm.vendors import openai_compatible

    client = openai_compatible.ChatCompletionsClient(
        route=openai_compatible.ROUTES["cerebras"], api_key=""
    )
    saved = os.environ.pop("OPENAI_API_KEY", None)
    try:
        with pytest.raises(LLMNotSent, match="never sent"):
            client.complete(REQUEST, ModelConfig(vendor="cerebras", identifier="gpt-oss-120b"))
    finally:
        if saved is not None:
            os.environ["OPENAI_API_KEY"] = saved


def test_openrouter_sends_a_reasoning_object_and_cerebras_a_flat_effort() -> None:
    """One frozen setting, two spellings, and only one of them is a no-op.

    OpenRouter unifies OpenAI-style efforts and Anthropic-style token budgets
    under a `reasoning` object; Cerebras takes chat-completions' flat
    `reasoning_effort`. Sending the wrong one is not an error -- it is silently
    ignored, which would benchmark an arm at a reasoning setting it never
    received and report the result as if it had.
    """
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.vendors import openai_compatible

    openrouter = openai_compatible.build_payload(
        REQUEST,
        ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high"),
    )
    cerebras = openai_compatible.build_payload(
        REQUEST,
        ModelConfig(vendor="cerebras", identifier="gpt-oss-120b", reasoning="medium"),
    )

    assert openrouter["extra_body"]["reasoning"] == {"effort": "high"}
    assert "reasoning_effort" not in openrouter

    assert cerebras["reasoning_effort"] == "medium"
    assert "extra_body" not in cerebras


def test_the_serialised_wire_body_is_the_request_we_intended() -> None:
    """The test that would have caught it, and the one the last two did not.

    A dict's CONTENTS say nothing about whether the SDK will accept it, and a
    method SIGNATURE says nothing about what ends up on the wire. Both of those
    passed while the adapter sent an unknown keyword argument and raised
    `TypeError` before any request existed -- eighteen recorded failures, zero
    HTTP.

    So this drives the REAL installed OpenAI client through a mocked httpx
    transport and reads the bytes it was about to send. No network, no
    credential, no vendor.
    """
    import httpx
    import openai

    from career_agent.llm.client import ModelConfig
    from career_agent.llm.requests import build_description_request
    from career_agent.llm.vendors import openai_compatible

    seen: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        seen["headers"] = {key.lower() for key in request.headers}
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "z-ai/glm-5.2",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    config = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high")
    built = build_description_request("sha256:a", "A posting.", config)
    client = openai.OpenAI(
        api_key="sentinel-not-a-real-key",
        base_url=openai_compatible.ROUTES["openrouter"].base_url,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    client.chat.completions.create(**openai_compatible.build_payload(built.request, config))

    body = seen["body"]
    assert isinstance(body, dict)

    # The frozen arm, on the wire.
    assert body["model"] == "z-ai/glm-5.2:free", "the :free slug is part of the arm identity"
    assert body["reasoning"] == {"effort": "high"}, "reasoning was silently dropped"
    assert body["provider"] == {"allow_fallbacks": False}, "routing pin was silently dropped"
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["response_format"]["json_schema"]["schema"]
    assert [message["role"] for message in body["messages"]] == ["system", "user"]
    assert body["messages"][1]["content"] == "A posting."
    # The output cap under the name this endpoint advertises. Sending OpenAI's
    # newer `max_completion_tokens` filtered the only free endpoint to zero.
    assert "max_tokens" in body and "max_completion_tokens" not in body

    # No paid fallback, by absence. `models` and `route` are how OpenRouter is
    # asked to try something else, and neither may appear.
    assert "models" not in body and "route" not in body
    assert "extra_body" not in body, "extra_body is a transport mechanism, not a wire field"

    # The credential travels in a header and never in the body.
    assert "authorization" in seen["headers"]
    assert "sentinel-not-a-real-key" not in json.dumps(body)


def test_cerebras_does_not_inherit_openrouter_routing_on_the_wire() -> None:
    """Two routes, one module, and no dialect bleeding between them.

    Cerebras serves its own models: `provider` routing is meaningless there, and
    `reasoning_effort` is a parameter the SDK declares, so it belongs at the top
    level rather than in `extra_body`.
    """
    import httpx
    import openai

    from career_agent.llm.client import ModelConfig
    from career_agent.llm.vendors import openai_compatible

    seen: dict[str, object] = {}

    def transport(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": "x",
                "object": "chat.completion",
                "created": 0,
                "model": "gpt-oss-120b",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "{}"},
                    }
                ],
            },
        )

    config = ModelConfig(vendor="cerebras", identifier="gpt-oss-120b", reasoning="medium")
    client = openai.OpenAI(
        api_key="sentinel",
        base_url=openai_compatible.ROUTES["cerebras"].base_url,
        http_client=httpx.Client(transport=httpx.MockTransport(transport)),
    )
    client.chat.completions.create(**openai_compatible.build_payload(REQUEST, config))

    body = seen["body"]
    assert isinstance(body, dict)
    assert body["reasoning_effort"] == "medium"
    assert "reasoning" not in body
    assert "provider" not in body, "OpenRouter routing leaked into the Cerebras dialect"


def test_every_payload_key_is_one_the_installed_sdk_will_accept() -> None:
    """The test whose absence made a screen report 18 failures and send nothing.

    `client.chat.completions.create()` is a TYPED method. A key it does not
    declare raises `TypeError` in Python, before an HTTP request exists -- so
    OpenRouter's `provider` and `reasoning` produced eighteen recorded failures
    and zero requests.

    Asserting the CONTENTS of a payload dict says nothing about whether it can
    be passed. This asserts the second thing, against the SDK actually
    installed, and it is why `extra_body` exists.
    """
    import inspect

    import openai

    from career_agent.llm.client import ModelConfig, StructuredOutput
    from career_agent.llm.vendors import openai_compatible

    accepted = set(
        inspect.signature(openai.resources.chat.completions.Completions.create).parameters
    )

    arms = [
        ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high"),
        ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free"),
        ModelConfig(vendor="cerebras", identifier="gpt-oss-120b", reasoning="medium"),
        ModelConfig(
            vendor="cerebras",
            identifier="gpt-oss-120b",
            structured_output=StructuredOutput.PLAIN_JSON,
        ),
    ]
    for config in arms:
        payload = openai_compatible.build_payload(REQUEST, config)
        unknown = set(payload) - accepted
        assert not unknown, f"{config.vendor} sends {sorted(unknown)}, which the SDK rejects"


#: Exactly what the one free GLM endpoint advertises, read from
#: /api/v1/models/z-ai/glm-5.2:free/endpoints on 2026-09-03. One endpoint,
#: provider Decart, $0 in and out, 256,000 context.
GLM_FREE_SUPPORTED_PARAMETERS = frozenset(
    {
        "reasoning",
        "include_reasoning",
        "temperature",
        "top_p",
        "top_k",
        "min_p",
        "frequency_penalty",
        "presence_penalty",
        "repetition_penalty",
        "stop",
        "seed",
        "max_tokens",
        "response_format",
        "structured_outputs",
        "tools",
        "tool_choice",
        "reasoning_effort",
    }
)


def test_the_glm_arm_sends_only_parameters_its_free_endpoint_advertises() -> None:
    """The defect that made a frozen arm look unroutable.

    `require_parameters: true` filters out any endpoint that does not support
    EVERY parameter in the request. The free GLM endpoint advertises
    `max_tokens`; we were sending `max_completion_tokens`, OpenAI's newer name
    for the same cap. One endpoint, filtered to zero, HTTP 404 -- and both
    diagnostic probes we were about to run would have failed identically and
    blamed structured output or reasoning.

    `response_format` and `reasoning` are both advertised. Neither was ever the
    problem.
    """
    from career_agent.llm.client import ModelConfig
    from career_agent.llm.vendors import openai_compatible

    config = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high")
    payload = openai_compatible.build_payload(REQUEST, config)

    # What actually lands on the wire: top level plus the merged extensions.
    wire = {key: value for key, value in payload.items() if key != "extra_body"}
    wire.update(payload.get("extra_body", {}))

    # `model`, `messages` and `provider` are the request envelope and the
    # routing directive, not model parameters the filter considers.
    parameters = set(wire) - {"model", "messages", "provider"}
    unadvertised = parameters - GLM_FREE_SUPPORTED_PARAMETERS

    assert not unadvertised, (
        f"the free endpoint does not advertise {sorted(unadvertised)}, and "
        "require_parameters will filter it out"
    )
    assert "max_tokens" in wire and "max_completion_tokens" not in wire
    assert "response_format" in wire, "strict schema is advertised and must still be sent"
    assert wire["reasoning"] == {"effort": "high"}

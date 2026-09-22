"""The OpenAI adapter. The only module that may import the OpenAI SDK.

The interesting difference from Anthropic is not the field names. It is that
strict structured output will not accept a schema with optional properties at
all: optionality has to be rewritten as nullability first. `openai_strict`
does that, and it is why the adapter cannot simply forward `request.schema`.
"""

from typing import Any

from career_agent.llm.client import (
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ModelConfig,
    Runner,
    StructuredOutput,
)
from career_agent.llm.vendors.schema_dialect import openai_strict

VENDOR = "openai"

#: The effort labels the Responses API accepts, by our own reasoning label.
_EFFORT = {"low": "low", "medium": "medium", "high": "high"}


def build_payload(request: LLMRequest, config: ModelConfig) -> dict[str, Any]:
    """The Responses API request this call becomes.

    `instructions` carries the system prompt and `input` the source text, which
    keeps the two apart the way the Messages API does -- so the same cache key
    describes the same model-visible input on both vendors.

    The schema is rewritten by `openai_strict` rather than forwarded. Strict
    mode requires every object to forbid extra properties and to list every
    property as required; a schema that merely omits an optional field is
    rejected. Forwarding ours unchanged would fail on the first call, at
    whatever moment we first paid for one.
    """
    payload: dict[str, Any] = {
        "model": config.identifier,
        "instructions": request.system,
        "input": request.user,
        "max_output_tokens": config.max_output_tokens,
    }

    mode = config.output_mode(request.family)
    if mode is StructuredOutput.STRICT_SCHEMA:
        payload["text"] = {
            "format": {
                "type": "json_schema",
                "name": request.schema_name,
                "strict": True,
                "schema": openai_strict(request.schema),
            }
        }
    elif mode is StructuredOutput.JSON_OBJECT:
        payload["text"] = {"format": {"type": "json_object"}}

    if config.reasoning:
        effort = _EFFORT.get(config.reasoning)
        if effort is None:
            raise ValueError(
                f"unknown reasoning setting {config.reasoning!r} for {VENDOR}; "
                f"expected one of {sorted(_EFFORT)}"
            )
        payload["reasoning"] = {"effort": effort}
    else:
        # Reasoning models reject an explicit temperature; non-reasoning ones
        # need it pinned for the run to be reproducible.
        payload["temperature"] = config.temperature

    return payload


def parse_response(body: dict[str, Any]) -> LLMResponse:
    """Turn a decoded Responses body into the project's own type.

    Verified against `Response.model_dump()` from the declared SDK rather than
    against the documented wire format. The Responses API is snake_case in both,
    so this adapter has none of the spelling mismatch that made the Google
    adapter record zero tokens across 55 live calls -- but that was established
    by running SDK objects through this function, not by assuming it.

    Two outcomes produce no usable answer and neither raises:

    **A refusal** is a content block with its own type. An unwary reader gets an
    empty string and records an extraction of nothing.

    **A truncation** is `status: "incomplete"`, with the cause in
    `incomplete_details`. The text that arrives is real and cut off mid-token,
    so it fails to parse and the attempt is retried either way -- but as a JSON
    syntax error thousands of tokens in, which says nothing about the cause. It
    matters more here than on the free arm: reasoning tokens count against
    `max_output_tokens`, so a reasoning effort raises truncation risk, and on a
    paid arm every uninformative retry is bought twice.

    Both are lifted into `refusal` because that is the pipeline's channel for
    *this attempt produced no answer, and here is why* -- the same choice the
    Google adapter makes for a SAFETY finish reason.
    """
    chunks: list[str] = []
    refusal: str | None = None

    for item in body.get("output") or ():
        # A reasoning item carries a summary and no content. Iterating `or ()`
        # rather than indexing is what keeps an effort setting from crashing
        # the parser.
        for block in item.get("content") or ():
            kind = block.get("type")
            if kind in {"output_text", "text"}:
                chunks.append(block.get("text", ""))
            elif kind == "refusal":
                refusal = block.get("refusal") or "the model declined to answer"

    status = body.get("status")
    if refusal is None and status == "incomplete":
        reason = (body.get("incomplete_details") or {}).get("reason") or "unspecified"
        refusal = f"the answer was cut off before it finished ({reason})"

    usage = body.get("usage") or {}
    return LLMResponse(
        raw_text="".join(chunks),
        model=str(body.get("model", "")),
        # `.get` rather than `.get(..., 0)`: a vendor that reported no usage has
        # told us nothing, and a zero here would be priced as a free call.
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        stop_reason=status,
        refusal=refusal,
    )


class OpenAIClient:
    vendor = VENDOR
    runner = Runner.PRODUCTION_API

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - not installed at M2
            raise LLMUnavailable(
                "the openai SDK is not installed. M2 Phase A is structural: no live "
                "call is authorised, and the request shape is tested through build_payload."
            ) from exc

        client = openai.OpenAI(api_key=self._api_key)
        try:
            response = client.responses.create(**build_payload(request, config))
        except Exception as exc:  # pragma: no cover - requires the network
            raise LLMUnavailable(f"{VENDOR} could not be reached: {exc}") from exc
        return parse_response(response.model_dump())

"""The Anthropic adapter. The only module that may import the Anthropic SDK.

Structural at M2: `build_payload` and `parse_response` are pure dict-to-dict
functions with no network and no credentials, and they are what the tests
exercise. `complete` is the thin remainder that actually spends money, and it
is not authorised to run yet.

The SDK import lives inside `complete` rather than at module scope. That is
what lets the request shape be tested on a machine with no SDK installed --
and it means importing this module can never, by itself, pull a vendor client
into the process.
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

VENDOR = "anthropic"

#: Thinking budgets, by the reasoning label a benchmark arm carries.
_THINKING_BUDGET = {"low": 2_000, "medium": 8_000, "high": 16_000}

#: Documented structured-output limits, checked 2026-09-02.
#:
#: These are the numbers the M2.0 topology decision was measured against, and
#: they are counted as combined totals across every schema in one request --
#: which is why a shared `$defs` entry referenced thirty times costs thirty,
#: not one. The compact row-based transport exists because the durable
#: fingerprint measured 191 optional and 107 union parameters against them.
#:
#: Source: platform.claude.com/docs/en/build-with-claude/structured-outputs
MAX_OPTIONAL_PARAMETERS = 24
MAX_UNION_PARAMETERS = 16

#: Keywords the SDK strips before the request is sent.
#:
#: Consequence worth stating plainly: the vendor does NOT enforce our
#: confidence range or our evidence-id format, because pydantic emits those as
#: `minimum`/`maximum` and `pattern`. Our own validators do, and this is a
#: concrete reason the architecture treats structured output as untrusted input
#: rather than as a guarantee.
UNENFORCED_KEYWORDS = ("minimum", "maximum", "minLength", "maxLength", "pattern")


def build_payload(request: LLMRequest, config: ModelConfig) -> dict[str, Any]:
    """The Messages API request this call becomes.

    Structured output is a forced tool call rather than a response format: the
    schema becomes a tool's `input_schema`, and `tool_choice` names it so the
    model cannot answer in prose instead. The result arrives as an already
    parsed object in `content[].input`, which is why `parse_response` has two
    shapes to handle.

    Thinking forces `temperature: 1`. That is the vendor's rule, not a
    preference of ours, and encoding it here rather than in a benchmark config
    stops an arm being silently rejected for a reason nobody wrote down.
    """
    payload: dict[str, Any] = {
        "model": config.identifier,
        "max_tokens": config.max_output_tokens,
        "system": request.system,
        "messages": [{"role": "user", "content": request.user}],
        "temperature": config.temperature,
    }

    mode = config.output_mode(request.family)
    if mode is StructuredOutput.JSON_OBJECT:
        # Anthropic has no JSON mode. Its structured output IS the forced tool
        # call, which enforces the whole schema or nothing -- there is no
        # "valid JSON, any shape" in between. Refusing is the honest answer:
        # quietly sending the plain request instead would record a row claiming
        # an envelope guarantee that was never in force.
        raise ValueError(
            f"{VENDOR} has no JSON_OBJECT mode: structured output here is a forced tool call, "
            "which enforces the whole schema or nothing. Use STRICT_SCHEMA or PLAIN_JSON."
        )

    if mode is StructuredOutput.STRICT_SCHEMA:
        payload["tools"] = [
            {
                "name": request.schema_name,
                "description": f"Record the extraction for the {request.family} family.",
                "input_schema": request.schema,
                # Without this the tool is merely schema-SHAPED. `strict` is what
                # buys grammar-constrained sampling, and it is the difference
                # between a guarantee and a strong suggestion.
                "strict": True,
            }
        ]
        payload["tool_choice"] = {"type": "tool", "name": request.schema_name}

    if config.reasoning:
        budget = _THINKING_BUDGET.get(config.reasoning)
        if budget is None:
            raise ValueError(
                f"unknown reasoning setting {config.reasoning!r} for {VENDOR}; "
                f"expected one of {sorted(_THINKING_BUDGET)}"
            )
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["temperature"] = 1  # required whenever thinking is enabled

    return payload


def parse_response(body: dict[str, Any]) -> LLMResponse:
    """Turn a decoded Messages response into the project's own type.

    Takes a dict rather than an SDK object so a recorded response replays
    without the SDK present -- and so no vendor type can escape this module.

    A forced tool call returns a parsed object; a plain completion returns
    text. Both are normalised to `raw_text`, because everything downstream --
    the parser, the `llm_call` row, the question "what did the model actually
    say?" -- is defined on the text.
    """
    import json

    chunks: list[str] = []
    refusal: str | None = None
    for block in body.get("content") or ():
        kind = block.get("type")
        if kind == "tool_use":
            chunks.append(json.dumps(block.get("input", {}), ensure_ascii=False))
        elif kind == "text":
            chunks.append(block.get("text", ""))

    if body.get("stop_reason") == "refusal":
        refusal = "the model declined to answer"

    usage = body.get("usage") or {}
    return LLMResponse(
        raw_text="".join(chunks),
        model=str(body.get("model", "")),
        input_tokens=usage.get("input_tokens"),
        output_tokens=usage.get("output_tokens"),
        stop_reason=body.get("stop_reason"),
        refusal=refusal,
    )


class AnthropicClient:
    """A billable client. Constructing one is free; calling it is not."""

    vendor = VENDOR
    runner = Runner.PRODUCTION_API

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - not installed at M2
            raise LLMUnavailable(
                "the anthropic SDK is not installed. M2 Phase A is structural: no live "
                "call is authorised, and the request shape is tested through build_payload."
            ) from exc

        client = anthropic.Anthropic(api_key=self._api_key)
        try:
            message = client.messages.create(**build_payload(request, config))
        except Exception as exc:  # pragma: no cover - requires the network
            raise LLMUnavailable(f"{VENDOR} could not be reached: {exc}") from exc
        return parse_response(message.model_dump())

"""The Google adapter. The only module that may import the Google GenAI SDK.

Gemini's `responseSchema` is an OpenAPI 3.0 subset, and it *rejects* keywords
it does not recognise rather than ignoring them. `$defs` and `$ref` are among
them, so the schema must be inlined before it is sent -- a transformation the
other two vendors accept but do not require.

ONE VENDOR, TWO MODEL FAMILIES, TWO WAYS TO ASK FOR THINKING
------------------------------------------------------------
`google` is a vendor name, not a request dialect. Gemini takes a thinking
**budget** in tokens; Gemma 4 takes a thinking **level**, and supports exactly
two of them -- HIGH and MINIMAL. Sending a budget to Gemma is not an error the
vendor reports: an unknown generation field is ignored, and the arm would be
benchmarked at a reasoning setting it never received while the report claimed
otherwise. That is the failure this file already carries a scar from -- the
adapter asked for `usageMetadata` while the SDK handed it `usage_metadata`, and
55 live calls recorded zero tokens.

So the mapping is per FAMILY, and a label the family does not support is
refused rather than translated into the nearest thing that would be accepted.
"""

import json
from typing import Any

from career_agent.llm.client import (
    LLMErrorResponse,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ModelConfig,
    Runner,
    StructuredOutput,
)
from career_agent.llm.tools import TOOL_NAMES, tool_declaration
from career_agent.llm.vendors.schema_dialect import google_openapi

VENDOR = "google"

#: Thinking budgets in tokens, by the reasoning label a benchmark arm carries.
#: Zero is meaningful here and is not "unset": it disables thinking outright.
#: **Gemini family only.**
_THINKING_BUDGET = {"off": 0, "low": 2_000, "medium": 8_000, "high": 16_000}

#: Gemma 4's thinking levels, by the same arm label. Two, and only two:
#: "HIGH = enabled, MINIMAL = disabled" (Gemma-on-Gemini-API documentation,
#: read 2026-09-03).
#:
#: The installed SDK's `ThinkingLevel` enum also offers LOW and MEDIUM, and
#: `types.ThinkingConfig(thinking_level="MEDIUM")` constructs without complaint
#: -- so the SDK will not stop us sending Gemma a level it does not support.
#: This table is the refusal, and it is ours.
_GEMMA_THINKING_LEVEL = {"high": "HIGH", "minimal": "MINIMAL"}

#: How a Gemma model announces itself. The Gemini API also accepts the
#: `models/`-prefixed form, so the prefix is stripped before the comparison
#: rather than being matched twice.
GEMMA_PREFIX = "gemma-"


def is_gemma(identifier: str) -> bool:
    """Whether this model id belongs to the Gemma family.

    A prefix test rather than a list, deliberately: a list would silently treat
    the next Gemma release as a Gemini model, which is the direction that fails
    quietly. An unknown `gemma-*` is refused loudly by the level table instead.
    """
    return identifier.removeprefix("models/").startswith(GEMMA_PREFIX)


def thinking_config(config: ModelConfig) -> dict[str, Any]:
    """The thinking block for this arm, in its own family's grammar.

    Raises `ValueError` on a label the family does not support. That is the
    whole point: a Gemma arm asked for `medium` has been mis-specified, and the
    only safe answers are "refuse" and "silently benchmark something else".
    """
    if is_gemma(config.identifier):
        level = _GEMMA_THINKING_LEVEL.get(config.reasoning or "")
        if level is None:
            raise ValueError(
                f"unknown reasoning setting {config.reasoning!r} for the Gemma family; "
                f"expected one of {sorted(_GEMMA_THINKING_LEVEL)}. Gemma 4 documents two "
                "thinking levels -- HIGH is enabled and MINIMAL is disabled -- and the "
                "Gemini token budgets do not apply to it."
            )
        return {"thinkingLevel": level}

    budget = _THINKING_BUDGET.get(config.reasoning or "")
    if budget is None:
        raise ValueError(
            f"unknown reasoning setting {config.reasoning!r} for {VENDOR}; "
            f"expected one of {sorted(_THINKING_BUDGET)}"
        )
    return {"thinkingBudget": budget}


def build_payload(request: LLMRequest, config: ModelConfig) -> dict[str, Any]:
    """The generateContent request this call becomes.

    `systemInstruction` and `contents` keep the system prompt and the source
    text apart, matching the other two adapters so one cache key means one
    model-visible input everywhere.
    """
    schema = google_openapi(request.schema)
    payload: dict[str, Any] = {
        "model": config.identifier,
        "systemInstruction": {"parts": [{"text": request.system}]},
        "contents": [{"role": "user", "parts": [{"text": request.user}]}],
        "generationConfig": {
            "temperature": config.temperature,
            "maxOutputTokens": config.max_output_tokens,
        },
    }

    mode = config.output_mode(request.family)
    if mode is StructuredOutput.FUNCTION_CALL:
        # A DIFFERENT CHANNEL, AND ONLY ONE OF THEM AT A TIME.
        #
        # No `responseSchema` and no `responseMimeType`: those shape the text,
        # and under this mode the text is not where the answer goes. Sending
        # both would ask for the same document twice, through two channels with
        # different guarantees, and make a malformed answer unattributable.
        #
        # `mode: ANY` with a single allowed name is how "you must call this
        # function" is spelled. It is NOT documented for this route -- Google's
        # Gemma page shows tools and never shows a tool config -- so it is sent
        # as the correct expression of the intent and trusted for nothing. The
        # response contract below assumes the model was free to answer in text
        # and checks that it did not.
        payload["tools"] = [{"functionDeclarations": [tool_declaration(request.family, schema)]}]
        payload["toolConfig"] = {
            "functionCallingConfig": {
                "mode": "ANY",
                "allowedFunctionNames": [TOOL_NAMES[request.family]],
            }
        }
    elif mode is StructuredOutput.STRICT_SCHEMA:
        payload["generationConfig"]["responseMimeType"] = "application/json"
        payload["generationConfig"]["responseSchema"] = schema
    elif mode is StructuredOutput.JSON_OBJECT:
        # Gemini spells the envelope guarantee as a mime type. The schema is
        # deliberately absent: that is the whole difference between the modes.
        payload["generationConfig"]["responseMimeType"] = "application/json"

    if config.reasoning:
        payload["generationConfig"]["thinkingConfig"] = thinking_config(config)

    return payload


#: Finish reasons that mean the model produced no answer on purpose.
REFUSAL_FINISH_REASONS = frozenset({"SAFETY", "RECITATION", "PROHIBITED_CONTENT", "BLOCKLIST"})


def _either(body: dict[str, Any], snake: str, camel: str) -> Any:
    """Read a field under either spelling this body may arrive in.

    A `generateContent` body reaches this function two ways, and they do not
    agree. The REST wire format is camelCase; `GenerateContentResponse
    .model_dump()` from the declared SDK is snake_case. Reading only one is how
    M2 Stage 0 ran 55 live calls and recorded zero tokens: the adapter asked for
    `usageMetadata` while the SDK was handing it `usage_metadata`, and the same
    mismatch silently blinded `finishReason` and `modelVersion` beside it.

    Neither spelling is "the" format, so neither is guessed at.
    """
    value = body.get(snake)
    return body.get(camel) if value is None else value


def _as_text(value: Any) -> str | None:
    """A finish reason as a plain string, whether it arrived as one or as an enum.

    The SDK dumps `finish_reason` as a `FinishReason` member, not as the wire
    string, so comparing it against a set of names fails even once the key is
    right. `.name` rather than `str()`, because `str(FinishReason.SAFETY)` is
    `'FinishReason.SAFETY'`.
    """
    if value is None:
        return None
    return str(getattr(value, "name", value))


def parse_response(body: dict[str, Any], expected_tool: str | None = None) -> LLMResponse:
    """Turn a decoded generateContent body into the project's own type.

    Accepts both the REST camelCase body and the SDK's snake_case
    `model_dump()`; see `_either`.

    `SAFETY` and `RECITATION` finish reasons produce no text. They are lifted
    into `refusal` for the same reason OpenAI's refusal block is: an empty
    answer recorded as a successful extraction would read, forever after, as a
    posting that said nothing.
    """
    chunks: list[str] = []
    calls: list[tuple[str, dict[str, Any]]] = []
    stop_reason: str | None = None

    for candidate in body.get("candidates") or ():
        stop_reason = _as_text(_either(candidate, "finish_reason", "finishReason")) or stop_reason
        for part in (candidate.get("content") or {}).get("parts") or ():
            # A THOUGHT IS NOT AN ANSWER, and Gemini says which is which.
            #
            # `generateContent` returns thinking as ordinary parts flagged
            # `thought: true`, in the same list and before the answer. Appending
            # them concatenates a monologue onto the front of the JSON -- which
            # would not parse, and on a lenient day would parse into something
            # nobody said. Gemini 3.1 Flash Lite never exposed one, because
            # thoughts are only returned when `include_thoughts` is set; a Gemma
            # arm at thinking level HIGH is exactly where that would stop being
            # true, and the failure would look like a prompt regression.
            #
            # The same distinction the chat-completions adapter draws between
            # `message.content` and `message.reasoning`. Presence and length of
            # the thought survive in the envelope; the text is never read as
            # output.
            if part.get("thought"):
                continue
            call = _either(part, "function_call", "functionCall")
            if call:
                calls.append((str(call.get("name") or ""), call.get("args") or {}))
                continue
            if part.get("text") is not None:
                chunks.append(part["text"])

    refusal = None
    if stop_reason in REFUSAL_FINISH_REASONS:
        refusal = f"the model declined to answer ({stop_reason})"

    text = "".join(chunks)
    contract_error = None
    if expected_tool is not None:
        text, contract_error = _function_call_answer(calls, text, expected_tool)

    usage = _either(body, "usage_metadata", "usageMetadata") or {}
    return LLMResponse(
        raw_text=text,
        model=str(_either(body, "model_version", "modelVersion") or ""),
        input_tokens=_either(usage, "prompt_token_count", "promptTokenCount"),
        output_tokens=_either(usage, "candidates_token_count", "candidatesTokenCount"),
        stop_reason=stop_reason,
        refusal=refusal,
        envelope=_envelope(body),
        function_calls=tuple(calls),
        contract_error=contract_error,
    )


def _function_call_answer(
    calls: list[tuple[str, dict[str, Any]]], text: str, expected: str
) -> tuple[str, str | None]:
    """The FUNCTION_CALL contract, checked before anything reads the answer.

    Every rejection here is a MODEL OUTPUT failure, recorded and never repaired.
    The point of moving the answer out of the text was to stop guessing which
    part of a response was meant to be the document; salvaging one from a
    half-honoured call would put the guessing straight back.

    Returns `(answer_text, error)`. The answer is the arguments re-serialised so
    that the parser, the transport validator, the evidence verifier and the
    assembler downstream see exactly what they have always seen -- a JSON
    document. Nothing about what is INSIDE it is decided here.
    """
    if not calls:
        got = "an empty response" if not text.strip() else f"{len(text)} characters of text"
        return text, f"expected a call to {expected!r} and the model returned {got}"
    if len(calls) > 1:
        names = ", ".join(sorted(name for name, _ in calls))
        return text, f"expected one call to {expected!r} and the model made {len(calls)}: {names}"

    name, arguments = calls[0]
    if name != expected:
        return text, f"expected a call to {expected!r} and the model called {name!r}"
    if not isinstance(arguments, dict):
        # A model that stringifies its own arguments has produced something that
        # would need unwrapping, and unwrapping is repair.
        return text, (
            f"{expected!r} was called with {type(arguments).__name__} arguments rather than an "
            "object; a stringified payload is not a structured answer"
        )
    if text.strip():
        return text, (
            f"{expected!r} was called and the model also returned {len(text.strip())} characters "
            "of text; a response that answers twice has not answered once"
        )
    return json.dumps(arguments, sort_keys=True), None


#: The two kinds of response evidence, named as the chat-completions adapter
#: names them so one reader can read both columns.
MODEL_RESPONSE_ENVELOPE = "MODEL_RESPONSE"
HTTP_ERROR_ENVELOPE = "HTTP_ERROR"

#: Longest vendor prose kept in an error envelope. The full text stays in
#: `llm_call.error`.
_MAX_ENVELOPE_TEXT = 400

#: Top-level `generateContent` fields this envelope already reports by name.
#: Anything else is listed in `extra_keys`, so a new field announces itself
#: instead of disappearing.
_KNOWN_BODY_KEYS = frozenset(
    {
        "candidates",
        "usage_metadata",
        "usageMetadata",
        "model_version",
        "modelVersion",
        "prompt_feedback",
        "promptFeedback",
        "response_id",
        "responseId",
        "sdk_http_response",
        "automatic_function_calling_history",
        "parsed",
        "create_time",
        "createTime",
    }
)


def _envelope(body: dict[str, Any]) -> dict[str, Any]:
    """What the vendor actually returned, before anyone decided what it meant.

    The same guarantee the chat-completions route already gives, for the same
    reason: `raw_text` is this adapter's *interpretation* of a response, and an
    interpretation cannot explain itself. The Cerebras run billed 62,794 output
    tokens against content read as empty, and only a stored shape could say
    where the answer had gone.

    Gemini and Gemma answer in PARTS, and a part is either an answer or a
    thought. So the shape is recorded per candidate: how many parts, which keys
    they carried, how many characters were answer and how many were thought.
    The thought TEXT is never copied -- its presence and its length are the
    diagnosis, and the text itself is the model's private reasoning.

    Built from a response body only. No request, no headers, no client, so no
    credential can appear here by construction.
    """
    candidates = []
    for candidate in body.get("candidates") or ():
        parts = ((candidate.get("content") or {}).get("parts")) or ()
        answer_chars = thought_chars = 0
        part_keys: set[str] = set()
        for part in parts:
            if not isinstance(part, dict):
                continue
            part_keys.update(key for key, value in part.items() if value is not None)
            text = part.get("text")
            if not isinstance(text, str):
                continue
            if part.get("thought"):
                thought_chars += len(text)
            else:
                answer_chars += len(text)
        candidates.append(
            {
                "finish_reason": _as_text(_either(candidate, "finish_reason", "finishReason")),
                "part_count": len(parts),
                "part_keys": sorted(part_keys),
                # Where an answer would be if it were not where we looked.
                "answer_text_chars": answer_chars,
                "thought_text_chars": thought_chars,
                "safety_ratings": _either(candidate, "safety_ratings", "safetyRatings"),
            }
        )

    return {
        "envelope_kind": MODEL_RESPONSE_ENVELOPE,
        "model_version": _either(body, "model_version", "modelVersion"),
        "response_id": _either(body, "response_id", "responseId"),
        # The whole usage block, `thoughts_token_count` included: thinking is
        # counted, and a report that cannot separate it cannot explain a large
        # output beside a small answer.
        "usage": _either(body, "usage_metadata", "usageMetadata"),
        "prompt_feedback": _either(body, "prompt_feedback", "promptFeedback"),
        "extra_keys": sorted(
            key for key, value in body.items() if value is not None and key not in _KNOWN_BODY_KEYS
        ),
        "candidates": candidates,
    }


def _clip(value: object) -> str | None:
    """Vendor prose, bounded. Never a body dumped whole into a report."""
    if not isinstance(value, str):
        return None
    return value if len(value) <= _MAX_ENVELOPE_TEXT else value[:_MAX_ENVELOPE_TEXT] + "..."


def _error_envelope(exc: Any) -> dict[str, Any]:
    """What Google said when it refused, as durable audit evidence.

    A rejected request is a response. The one this round is most likely to meet
    is a 400 saying `response_schema` is not supported on this model -- a
    transport FACT worth storing, and worthless as the string "google could not
    be reached".

    Allow-listed fields from the error body only. `google.rpc` status details
    are kept by their `@type`, so a RetryInfo or a QuotaFailure can be read
    afterwards without copying whatever else a body carries.
    """
    payload = getattr(exc, "response_json", None)
    body = payload if isinstance(payload, dict) else {}
    nested = body.get("error")
    error = nested if isinstance(nested, dict) else body

    envelope: dict[str, Any] = {
        "envelope_kind": HTTP_ERROR_ENVELOPE,
        "http_status": getattr(exc, "code", None),
        "status": error.get("status") or getattr(exc, "status", None),
        "message": _clip(error.get("message")) or _clip(getattr(exc, "message", None)),
    }
    details = error.get("details")
    if isinstance(details, list):
        envelope["detail_types"] = [
            detail.get("@type") for detail in details if isinstance(detail, dict)
        ]
    return envelope


def _retry_after_seconds(exc: Any) -> float | None:
    """The cooldown Google asked for, from `google.rpc.RetryInfo` and nowhere else.

    A documented structured field, parsed as documented: `retryDelay` is a
    protobuf Duration rendered as seconds with an `s` suffix. Nothing here
    reads a number out of prose -- see `failures.py` for what that costs.
    """
    payload = getattr(exc, "response_json", None)
    body = payload if isinstance(payload, dict) else {}
    nested = body.get("error")
    error = nested if isinstance(nested, dict) else body
    for detail in error.get("details") or ():
        if not isinstance(detail, dict) or not str(detail.get("@type", "")).endswith("RetryInfo"):
            continue
        delay = detail.get("retryDelay") or detail.get("retry_delay")
        if isinstance(delay, int | float) and not isinstance(delay, bool):
            return float(delay)
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


class GoogleClient:
    vendor = VENDOR
    runner = Runner.PRODUCTION_API

    def __init__(self, api_key: str | None = None) -> None:
        self._api_key = api_key

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover - not installed at M2
            raise LLMUnavailable(
                "the google-genai SDK is not installed. M2 Phase A is structural: no live "
                "call is authorised, and the request shape is tested through build_payload."
            ) from exc

        client = genai.Client(api_key=self._api_key)
        payload = build_payload(request, config)
        # Built from the payload rather than beside it, so the request the wire
        # test captures is the request this line sends. `tools` and `toolConfig`
        # are present only under FUNCTION_CALL.
        sdk_config: dict[str, Any] = {
            "system_instruction": request.system,
            **payload["generationConfig"],
            **{key: payload[key] for key in ("tools", "toolConfig") if key in payload},
        }
        try:
            response = client.models.generate_content(
                model=payload["model"],
                contents=payload["contents"],
                config=sdk_config,  # type: ignore[arg-type]
            )
        except genai.errors.APIError as exc:
            # GOOGLE ANSWERED, and the answer was an error. A 400 rejecting a
            # schema, a 429, a 503 -- all of them are HTTP responses, and the
            # one thing they are not is "could not be reached".
            raise LLMErrorResponse(
                f"{VENDOR} could not be reached: {exc}",
                status=getattr(exc, "code", None),
                retry_after_seconds=_retry_after_seconds(exc),
                envelope=_error_envelope(exc),
            ) from exc
        except Exception as exc:  # pragma: no cover - requires the network
            raise LLMUnavailable(f"{VENDOR} could not be reached: {exc}") from exc
        # The expected tool is passed only under FUNCTION_CALL, so the contract
        # is checked exactly where it was established and nowhere else. Under
        # the other three modes `parse_response` behaves as it always has.
        expected = (
            TOOL_NAMES[request.family]
            if config.output_mode(request.family) is StructuredOutput.FUNCTION_CALL
            else None
        )
        return parse_response(response.model_dump(), expected)

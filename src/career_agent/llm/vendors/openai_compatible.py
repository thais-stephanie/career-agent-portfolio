"""The chat-completions dialect, and the two routes that speak it.

Cerebras and OpenRouter both expose OpenAI's `/v1/chat/completions`. Our
`vendors/openai.py` does not: it speaks the **Responses** API; `instructions`,
`input`, `responses.create`, which neither route implements. One dialect, two
vendors, one file; the registry has promised since M1B that a new vendor costs
one file and one entry, and this is the entry being cashed.

WHY THIS IS NOT A SECOND EXTRACTION PATH
----------------------------------------
It is an `LLMClient` and nothing more. `extract_job` cannot tell it from the
Google adapter, and every answer it returns goes through the same parse,
transport validation, evidence verification, assembly and persistence as every
other. The only thing that varies is the shape of one HTTP body.

STRICTNESS IS PER FAMILY, AND THAT IS THE POINT
-----------------------------------------------
Cerebras enforces a strict JSON schema of at most **5,000 characters**. Our
provider schema is 4,038 and our description schema is 8,536. So on that route
the provider family runs under a vendor guarantee and the description family
does not, which is the best production-usable configuration of the route, and
therefore the honest arm to benchmark. Weakening the provider family to match
would measure a configuration nobody would ship.

`config.output_mode(family)` decides, `static_digest` carries it into the cache
key, and `llm_call.structured_output` records it per row. An answer produced
under `PLAIN_JSON` can never be served as one produced under `STRICT_SCHEMA`.

ROUTING IS PART OF THE ARM, NOT AN ACCIDENT
-------------------------------------------
OpenRouter serves one model from many providers, and its own documentation says
enforcement "varies by provider: some guarantee schema-conforming output, while
others translate your schema into their own structured-output format or treat
it as a strong hint". For a benchmark that measures first-attempt schema pass
rate, that is not a detail: an unpinned route would make the metric a
measurement of the routing lottery. `require_parameters` restricts routing to
providers that actually support what the request asks for, and this adapter
sends it whenever a family is asking for enforcement.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Any

from career_agent.llm.client import (
    Family,
    LLMErrorResponse,
    LLMNotSent,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ModelConfig,
    Runner,
    StructuredOutput,
)
from career_agent.llm.routing import pinned_endpoint
from career_agent.llm.vendors.schema_dialect import openai_strict

CEREBRAS = "cerebras"
OPENROUTER = "openrouter"


@dataclass(frozen=True)
class Route:
    """One deployment of the chat-completions dialect.

    A dated observation like `quotas.py` and `pricing.py`, for the same reason:
    a base URL and a model catalogue are facts about a service on a day, not
    properties of the protocol.
    """

    vendor: str
    base_url: str
    credential_variable: str
    #: OpenRouter routes one model id to many providers and lets a caller
    #: restrict that. Cerebras serves its own models and has nothing to pin.
    pins_provider_routing: bool = False
    #: How this route spells a reasoning effort. OpenAI's chat-completions API
    #: takes a flat `reasoning_effort`; OpenRouter takes a `reasoning` object,
    #: because it unifies OpenAI-style efforts and Anthropic-style token budgets
    #: under one parameter. Sending the wrong one is not an error -- it is
    #: silently ignored, which would have benchmarked an arm at a reasoning
    #: setting it never received.
    reasoning_object: bool = False
    #: What this route calls the output cap. OpenAI's newer name is
    #: `max_completion_tokens`; OpenRouter's free GLM endpoint advertises
    #: `max_tokens` and nothing else.
    #:
    #: Not cosmetic. With `require_parameters: true` a router filters out any
    #: endpoint that does not support EVERY parameter in the request, so sending
    #: a name the endpoint never advertised removes the only candidate -- which
    #: is exactly what happened: one endpoint, filtered to zero at "Filter by
    #: Parameters", HTTP 404, and a frozen arm that looked unroutable when the
    #: blocker was the name of a field.
    max_output_field: str = "max_completion_tokens"
    source: str = "unrecorded"
    observed_at: str = "unknown"


ROUTES: dict[str, Route] = {
    CEREBRAS: Route(
        vendor=CEREBRAS,
        base_url="https://api.cerebras.ai/v1",
        credential_variable="CEREBRAS_API_KEY",
        source="Cerebras inference documentation, chat-completions reference",
        observed_at="2026-09-03",
    ),
    OPENROUTER: Route(
        vendor=OPENROUTER,
        base_url="https://openrouter.ai/api/v1",
        credential_variable="OPENROUTER_API_KEY",
        pins_provider_routing=True,
        reasoning_object=True,
        # The free GLM endpoint advertises `max_tokens` and not
        # `max_completion_tokens`. Read from
        # /api/v1/models/z-ai/glm-5.2:free/endpoints on 2026-09-03.
        max_output_field="max_tokens",
        source="OpenRouter API reference and provider-routing documentation",
        observed_at="2026-09-03",
    ),
}


def build_payload(request: LLMRequest, config: ModelConfig) -> dict[str, Any]:
    """The chat-completions body this call becomes.

    The system prompt and the source text stay in separate messages rather than
    being concatenated, so the same cache key describes the same model-visible
    input here as on the Responses API and on Gemini. A route that glued them
    together would be asking a different question under an identical key.

    Under `PLAIN_JSON` the schema is **not** sent at all, and the mode is not a
    quiet downgrade of `STRICT_SCHEMA`: `response_format` is omitted entirely,
    so what is measured is the model's own ability to produce our shape from the
    instructions it was given. Sending the schema as an unenforced "hint" would
    be a third mode that neither name describes.

    VENDOR EXTENSIONS GO IN `extra_body`, NOT AT THE TOP LEVEL
    ----------------------------------------------------------
    `client.chat.completions.create()` is a TYPED method. A key it does not
    declare is a `TypeError` raised in Python, before any HTTP request exists --
    which is how a screen made zero requests and reported eighteen failures.
    `provider` and `reasoning` are OpenRouter's own parameters and the SDK has
    never heard of them; `extra_body` is the documented way to send exactly
    that. `reasoning_effort` IS declared, so Cerebras keeps it at the top level.

    A test asserts every top-level key here is a parameter the installed SDK
    accepts, because the shape of a dict says nothing about whether it can be
    passed.
    """
    mode = config.output_mode(request.family)
    route = ROUTES.get(config.vendor)
    payload: dict[str, Any] = {
        "model": config.identifier,
        "messages": [
            {"role": "system", "content": request.system},
            {"role": "user", "content": request.user},
        ],
        (route.max_output_field if route else "max_completion_tokens"): (config.max_output_tokens),
        "temperature": config.temperature,
    }

    if mode is StructuredOutput.STRICT_SCHEMA:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": request.schema_name,
                "strict": True,
                "schema": openai_strict(request.schema),
            },
        }
    elif mode is StructuredOutput.JSON_OBJECT:
        # The envelope guarantee without the shape. Sent whenever a route
        # advertises `response_format` but not `structured_outputs` -- reading
        # that as PLAIN_JSON would give up a guarantee the endpoint offers.
        payload["response_format"] = {"type": "json_object"}

    extra: dict[str, Any] = {}

    # Only meaningful when a SHAPE is being required. Sent unconditionally it
    # would refuse routes perfectly able to answer a plain-JSON request, which
    # narrows a free tier for nothing.
    #
    # Deliberately NOT sent for JSON_OBJECT. The router filters on parameter
    # NAMES, and `response_format` is advertised by every route we would send
    # this to -- so the pin would pass and buy nothing, while adding one more
    # way for a single-endpoint free slug to be filtered to zero. That is the
    # exact failure the GLM 404 was: one endpoint, one unadvertised parameter
    # name, no candidates left.
    # PIN THE ENDPOINT; DO NOT FILTER FOR IT.
    #
    # `require_parameters: true` asks the router for providers that support
    # every parameter sent, and on a single-endpoint free slug that is a way to
    # lose the route rather than to pin it. This project has the 404 to prove
    # it: "No endpoints found that can handle the requested parameters", on the
    # only free endpoint there is.
    #
    # `allow_fallbacks: false` says the narrower and more useful thing -- do not
    # silently serve this from somewhere else -- and cannot empty the candidate
    # list. Sent whenever the pair has a recorded endpoint, under every mode,
    # because "which machine answered" is not a property of the output format.
    if pinned_endpoint(config.vendor, config.identifier) is not None:
        extra["provider"] = {"allow_fallbacks": False}

    if config.reasoning:
        if route is not None and route.reasoning_object:
            extra["reasoning"] = {"effort": config.reasoning}
        else:
            payload["reasoning_effort"] = config.reasoning

    if extra:
        payload["extra_body"] = extra

    return payload


def parse_response(body: dict[str, Any], http_status: int | None = None) -> LLMResponse:
    """Turn a decoded chat-completions body into the project's own type.

    Three ways to come back with no usable answer, and none of them raises:

    * a **refusal** arrives as `message.refusal` beside a null content;
    * a **truncation** arrives as `finish_reason: "length"`, with real text cut
      off mid-token. It would fail to parse anyway, but as a JSON syntax error
      thousands of tokens in, which does not say "raise the output ceiling";
    * a **content filter** arrives as `finish_reason: "content_filter"`.

    All three are lifted into `refusal`, which is the pipeline's channel for
    *this attempt produced no answer, and here is why*: the same choice the
    Google adapter makes for a SAFETY finish reason and the OpenAI one makes for
    an incomplete response.
    """
    chunks: list[str] = []
    refusal: str | None = None
    finish: str | None = None
    envelope = _envelope(body, http_status)

    for choice in body.get("choices") or ():
        finish = choice.get("finish_reason") or finish
        message = choice.get("message") or {}
        if message.get("refusal"):
            refusal = str(message["refusal"])
        content = message.get("content")
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            # Some routes return content as parts rather than as a string.
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    chunks.append(part["text"])

    if refusal is None and finish == "length":
        refusal = "the answer was cut off before it finished (length)"
    if refusal is None and finish == "content_filter":
        refusal = "the model declined to answer (content_filter)"

    usage = body.get("usage") or {}
    return LLMResponse(
        raw_text="".join(chunks),
        model=str(body.get("model", "")),
        # `.get` rather than `.get(..., 0)`. A route that reported no usage has
        # told us nothing, and a zero would be priced as a free call.
        input_tokens=usage.get("prompt_tokens"),
        output_tokens=usage.get("completion_tokens"),
        stop_reason=finish,
        refusal=refusal,
        envelope=envelope,
    )


def _envelope(body: dict[str, Any], http_status: int | None = None) -> dict[str, Any]:
    """What the vendor actually returned, before anyone decided what it meant.

    The Cerebras screen billed for 62,794 output tokens and produced empty
    content, and nothing survived that could say where the answer had gone --
    `raw_text` is this adapter's *interpretation* of a response, and an
    interpretation cannot explain itself.

    So the shape is preserved: which keys each message carried, the finish
    reason, the model the vendor says answered, and the full usage block. The
    message CONTENT is deliberately summarised to its length and its keys rather
    than copied -- the content we could read is already in `raw_output`, and the
    content we could not read is what its absence proves.

    Built from a response body only. No request, no headers, no client, so no
    credential can appear here by construction.
    """
    choices = []
    for choice in body.get("choices") or ():
        message = choice.get("message") or {}
        choices.append(
            {
                "finish_reason": choice.get("finish_reason"),
                "native_finish_reason": choice.get("native_finish_reason"),
                # What the vendor actually POPULATED, and what it merely
                # declared. A dumped SDK object carries every field the schema
                # knows -- `audio`, `function_call`, `refusal`, `tool_calls` --
                # all null, and listing those as "keys present" buries the one
                # fact this envelope exists to show: which channel had content.
                "message_keys": sorted(k for k, v in message.items() if v is not None),
                "null_message_keys": sorted(k for k, v in message.items() if v is None),
                # Where an answer would be if it were not where we looked.
                "field_lengths": {
                    key: len(value) if isinstance(value, str | list) else None
                    for key, value in message.items()
                    if value is not None
                },
            }
        )
    return {
        # Which of the two envelopes this is. A successful ChatCompletion body
        # and an HTTP error body are both response evidence and neither is the
        # other: one says what the model produced, the other says why nothing
        # was produced. Naming the kind is what stops a reader from asking a
        # 429 for its `choices`.
        "envelope_kind": MODEL_RESPONSE_ENVELOPE,
        # A 2xx is a fact about the response, not an assumption from the fact
        # that the SDK returned rather than raised. Read from the raw response.
        "http_status": http_status,
        "model": body.get("model"),
        "object": body.get("object"),
        "usage": body.get("usage"),
        "provider": body.get("provider"),
        "error": body.get("error"),
        # Vendor fields nobody here has heard of, BY NAME. Cerebras adds
        # `time_info`; a route that starts putting an answer somewhere new
        # would otherwise be invisible to a reader of this envelope, which is
        # the exact position the lost GPT-OSS run left us in.
        "extra_keys": sorted(
            key for key, value in body.items() if value is not None and key not in _KNOWN_BODY_KEYS
        ),
        "choices": choices,
    }


#: Everything `_envelope` already reports by name. Anything else is listed in
#: `extra_keys` so a new vendor field announces itself.
_KNOWN_BODY_KEYS = frozenset(
    {"id", "object", "created", "model", "choices", "usage", "provider", "error"}
)


#: The two kinds of response evidence, named so a stored envelope says which it
#: is without anyone having to infer it from which keys are present.
MODEL_RESPONSE_ENVELOPE = "MODEL_RESPONSE"
HTTP_ERROR_ENVELOPE = "HTTP_ERROR"

#: Longest vendor prose kept in an error envelope. A remedy hint is useful and a
#: paragraph of it is not; the full text is still in `llm_call.error`.
_MAX_ENVELOPE_TEXT = 400

#: Response headers worth keeping, lowercased. An allow-list rather than a
#: filter, because "copy the headers except the dangerous ones" is a policy that
#: is one vendor away from being wrong. Request headers are never read at all,
#: which is where an Authorization value would be.
_KEPT_HEADERS = ("retry-after",)

#: Fields an OpenAI-compatible error body may carry, and nothing else. The
#: OpenRouter 429 also carried `user_id`; an account identifier is not response
#: evidence about a request, so it is not copied.
_KEPT_ERROR_FIELDS = ("code", "type", "param")
_KEPT_METADATA_FIELDS = (
    "provider_name",
    "provider_error_code",
    "limit_source",
    "is_byok",
    "retry_after_seconds",
    "failed_routing_step",
    "routing_funnel",
)


def _clip(value: object) -> str | None:
    """Vendor prose, bounded. Never a body dumped whole into a report."""
    if not isinstance(value, str):
        return None
    return value if len(value) <= _MAX_ENVELOPE_TEXT else value[:_MAX_ENVELOPE_TEXT] + "..."


def _error_body(exc: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """The `error` object and its `metadata`, whatever nesting the route used.

    OpenRouter nests: `{"error": {"message", "code", "metadata": {...}}}`.
    Cerebras is flat: `{"message", "type", "param", "code"}`. Both are handled
    by looking for the nested object and falling back to the body itself.
    """
    body = exc.body if isinstance(getattr(exc, "body", None), dict) else {}
    nested = body.get("error")
    error = nested if isinstance(nested, dict) else body
    metadata = error.get("metadata")
    return error, metadata if isinstance(metadata, dict) else {}


def _response_headers(exc: Any) -> dict[str, str]:
    """The allow-listed response headers, lowercased."""
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    kept = {}
    for name in _KEPT_HEADERS:
        value = headers.get(name)
        if value is not None:
            kept[name] = str(value)
    return kept


def _error_envelope(exc: Any) -> dict[str, Any]:
    """What the vendor said when it refused, as durable audit evidence.

    A successful body is not a precondition for evidence. The 429 that stopped
    the last canary named its provider (Decart), its own error code
    (`upstream_429`), where the limit came from (`upstream_provider_shared_pool`)
    and how long to wait (5s) -- every one of which answers a question the
    string "could not be reached" cannot.

    Built from the RESPONSE only: its status, its allow-listed headers and its
    body. No request, no request headers, no client, so no credential can appear
    here by construction.
    """
    error, metadata = _error_body(exc)
    envelope: dict[str, Any] = {
        "envelope_kind": HTTP_ERROR_ENVELOPE,
        "http_status": getattr(exc, "status_code", None),
        "message": _clip(error.get("message")),
    }
    for field_name in _KEPT_ERROR_FIELDS:
        if field_name in error:
            envelope[field_name] = error[field_name]
    for field_name in _KEPT_METADATA_FIELDS:
        if field_name in metadata:
            value = metadata[field_name]
            envelope[field_name] = _clip(value) if isinstance(value, str) else value
    if "raw" in metadata:
        envelope["raw"] = _clip(metadata["raw"])
    headers = _response_headers(exc)
    if headers:
        envelope["response_headers"] = headers
    return envelope


def _retry_after_seconds(exc: Any) -> float | None:
    """The cooldown the PROVIDER asked for, from documented fields only.

    Two sources, both structured, and nothing else:

    * `error.metadata.retry_after_seconds`, which OpenRouter documents and sent
      as `5` alongside the 429 from Decart;
    * the standard `Retry-After` response header, in its delay-seconds form.

    Deliberately NOT a number scraped out of prose. "retry in a few minutes" and
    "429 of 500 today" both contain digits, and a cooldown guessed from a
    sentence is the same class of mistake as retrying a payment error because
    its parameter was called `quota`.

    The HTTP-date form of `Retry-After` is not read: interpreting it needs a
    clock and a timezone, and this project injects its clock rather than
    reaching for one in an adapter. A route that only sends a date gets the
    local cooldown, which is the safe direction to be wrong in.
    """
    _, metadata = _error_body(exc)
    hinted = metadata.get("retry_after_seconds")
    if isinstance(hinted, int | float) and not isinstance(hinted, bool) and hinted >= 0:
        return float(hinted)

    header = _response_headers(exc).get("retry-after")
    if header is not None:
        try:
            seconds = float(header.strip())
        except ValueError:
            return None
        if seconds >= 0:
            return seconds
    return None


@dataclass
class ChatCompletionsClient:
    """One route's client. Constructing it reaches nothing and reads no key."""

    route: Route
    #: An explicit key, as on every other adapter. `None` -- which is what
    #: `get_client` always passes -- means read the route's own variable.
    api_key: str | None = None

    #: Plain fields rather than properties, because `LLMClient` declares them as
    #: attributes and a read-only property does not satisfy that. Derived from
    #: the route in `__post_init__` so the two can never disagree.
    vendor: str = field(init=False, default="")
    runner: Runner = field(init=False, default=Runner.PRODUCTION_API)

    def __post_init__(self) -> None:
        self.vendor = self.route.vendor

    def _credential(self) -> str | None:
        """This route's key, read from this route's own variable.

        The other adapters let their SDK find the key, because each SDK's
        default variable happens to be the right one. That breaks here and
        breaks silently in the worst place: the OpenAI SDK looks for
        `OPENAI_API_KEY` no matter whose base URL it has been pointed at, so a
        Cerebras run with `CEREBRAS_API_KEY` set and `OPENAI_API_KEY` unset
        fails with a message naming the wrong variable -- and one with BOTH set
        would have sent an OpenAI key to Cerebras.

        Read and handed to the SDK, never printed, logged or persisted. The
        preflight still reports presence only.
        """
        if self.api_key is not None:
            return self.api_key
        return os.environ.get(self.route.credential_variable, "").strip() or None

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        try:
            import openai
        except ImportError as exc:  # pragma: no cover - the SDK is declared
            raise LLMUnavailable(
                f"the openai SDK is not installed; {self.route.vendor} is reached through its "
                "OpenAI-compatible client."
            ) from exc

        # Construction inside the guard, not beside it. A client that cannot be
        # built raises the SDK's own error type, which is not an `LLMError` --
        # so it escapes `_ask`, aborts the whole run, and loses the postings
        # already finished. One unreachable vendor is one failed attempt.
        try:
            client = openai.OpenAI(
                api_key=self._credential() or None,
                base_url=self.route.base_url,
                # The SDK retries 408/409/429/5xx TWICE by default, sleeping on
                # the response's own Retry-After. That is a second, invisible
                # retry policy underneath ours: one `complete()` on a 429 sends
                # up to three HTTP requests, and a run authorised for exactly
                # one request would quietly make three of them against a free
                # tier's daily allowance -- while `live_calls` counted one.
                #
                # Retrying is this project's decision, taken in `_ask` and
                # bounded by `--max-attempts` and `--max-live-calls`. Zero here
                # is what makes those numbers true.
                max_retries=0,
            )
            payload = build_payload(request, config)
        except (TypeError, ValueError, openai.OpenAIError) as exc:
            # Construction failed. Nothing has been sent, so nothing has been
            # used -- and it will fail identically on the next posting, because
            # the cause is here rather than at the vendor.
            raise LLMNotSent(f"{self.route.vendor} request was never sent: {exc}") from exc

        try:
            # `with_raw_response` rather than `create`, for one field: the HTTP
            # STATUS of a success. Everything else is identical -- the same
            # typed method, the same exceptions, the same parsed object from
            # `.parse()`. Without it a 2xx is inferred from the SDK having
            # returned rather than raised, and the success envelope reports a
            # status nobody read.
            raw = client.chat.completions.with_raw_response.create(**payload)
        except TypeError as exc:
            # A keyword the typed SDK method does not declare raises in Python,
            # before any HTTP request exists. This is the failure that produced
            # 18 recorded attempts and zero requests.
            raise LLMNotSent(f"{self.route.vendor} request was never sent: {exc}") from exc
        except openai.APIStatusError as exc:
            # THE VENDOR ANSWERED. A 402, a 404, a 429 and a 503 all arrive
            # here, and all of them are HTTP responses: a status line, usually a
            # structured body, sometimes a `Retry-After`. What none of them
            # carries is a completion -- which is a different fact, and used to
            # be recorded as the same one.
            raise LLMErrorResponse(
                f"{self.route.vendor} could not be reached: {exc}",
                status=exc.status_code,
                retry_after_seconds=_retry_after_seconds(exc),
                envelope=_error_envelope(exc),
            ) from exc
        except openai.APIConnectionError as exc:
            # The request went out and nothing came back: a reset, a timeout, a
            # socket that closed mid-flight. `APITimeoutError` is a subclass, so
            # it lands here too. This -- and only this -- is SENT_NO_RESPONSE.
            raise LLMUnavailable(f"{self.route.vendor} could not be reached: {exc}") from exc
        except Exception as exc:  # pragma: no cover - requires the network
            raise LLMUnavailable(f"{self.route.vendor} could not be reached: {exc}") from exc
        return parse_response(json.loads(raw.parse().model_dump_json()), raw.status_code)


def CerebrasClient(api_key: str | None = None) -> ChatCompletionsClient:
    return ChatCompletionsClient(route=ROUTES[CEREBRAS], api_key=api_key)


def OpenRouterClient(api_key: str | None = None) -> ChatCompletionsClient:
    return ChatCompletionsClient(route=ROUTES[OPENROUTER], api_key=api_key)


#: The families each route can hold to a vendor-enforced schema, given the
#: schema sizes we actually send. A dated observation, not a capability claim
#: about the vendor in general.
#:
#: Cerebras: strict schemas are capped at 5,000 characters (documentation read
#: 2026-09-03). Our provider schema is 4,038 and our description schema 8,536.
CEREBRAS_PER_FAMILY: tuple[tuple[Family, StructuredOutput], ...] = (
    (Family.PROVIDER, StructuredOutput.STRICT_SCHEMA),
    (Family.DESCRIPTION, StructuredOutput.PLAIN_JSON),
)

"""What "we heard back" means, and the 429 that proved we were saying it wrong.

THE FACT THAT WAS BEING CONFLATED
---------------------------------
The last OpenRouter canary was stored as `SENT_NO_RESPONSE`. What actually
happened was:

    HTTP 429
    provider_name        Decart
    provider_error_code  upstream_429
    retry_after_seconds  5
    limit_source         upstream_provider_shared_pool

That is a response. A named provider read the request, decided, and said so
within a few milliseconds -- and the row recorded it as an attempt nobody had
heard back from, which is what a dropped connection looks like. Two opposite
events, one state, and the difference between them is exactly the difference
between "the arm is unroutable" and "ask again in five seconds".

The confusion is linguistic and it is worth naming: no MODEL RESPONSE was
generated, so the code said no HTTP RESPONSE was received. Those are different
sentences about different layers.

Every test here is offline. The status-code tests drive the REAL installed
OpenAI SDK through a mocked httpx transport, because the thing under test is
which SDK exception a status produces and what our adapter does with it -- and a
hand-written fake exception would only prove that we can write one.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest

from career_agent.llm.client import (
    LLMErrorResponse,
    LLMNotSent,
    LLMUnavailable,
    ModelConfig,
)
from career_agent.llm.failures import (
    Transport,
    http_status_in,
    observed_transport,
    transport_from_error,
)
from career_agent.llm.requests import build_description_request
from career_agent.llm.transport import TDescriptionFamily
from career_agent.llm.vendors import openai_compatible
from career_agent.pipeline.extract import ExtractionOutcome, FamilyAttempt, _ask
from career_agent.pipeline.live import _cooldown_for

ARM = ModelConfig(vendor="openrouter", identifier="z-ai/glm-5.2:free", reasoning="high")

#: Captured once, at import. Reading `openai.OpenAI` inside the helper would
#: read whatever a previous `monkeypatch.setattr` left there, and a test that
#: mocks the wire twice would wrap the wrapper.
REAL_OPENAI = openai.OpenAI

#: The exact body OpenRouter returned on 2026-09-03, byte for byte from the
#: stored row. Every assertion about the error envelope reads this and not a
#: tidied-up version of it.
UPSTREAM_429 = {
    "error": {
        "message": "Provider returned error",
        "code": 429,
        "metadata": {
            "raw": "z-ai/glm-5.2:free is temporarily rate-limited upstream. Please retry "
            "shortly, or add your own key to accumulate your rate limits: "
            "https://openrouter.ai/settings/integrations",
            "provider_name": "Decart",
            "is_byok": False,
            "provider_error_code": "upstream_429",
            "limit_source": "upstream_provider_shared_pool",
            "remedy_hint": "Retry shortly, add your own provider key",
            "retry_after_seconds": 5,
            "retry_after_seconds_raw": 5,
            "headers": {"Retry-After": "5"},
        },
    },
    "user_id": "user_2w981qIYhEBqF8UhrxjFeefF5bC",
}

#: The 404 the routing fix was diagnosed from. Also a response.
ROUTER_404 = {
    "error": {
        "message": "No endpoints found that can handle the requested parameters.",
        "code": 404,
        "metadata": {
            "routing_funnel": [{"step": "Initial Endpoints", "endpoint_count": 1}],
            "failed_routing_step": "Filter by Parameters",
        },
    }
}

OK_BODY = {
    "id": "x",
    "object": "chat.completion",
    "created": 0,
    "model": "z-ai/glm-5.2",
    "choices": [
        {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}
    ],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


# =========================================================================
# THE HARNESS
# =========================================================================


class Wire:
    """One mocked HTTP endpoint, and a count of what actually reached it.

    The count is not decoration. The OpenAI SDK retries 429 and 5xx twice by
    default, so "one call to `complete()`" and "one HTTP request" are not the
    same number unless somebody makes them so.
    """

    def __init__(self, handler) -> None:
        self.handler = handler
        self.requests = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        return self.handler(request)


def responding(status: int, body: dict[str, Any], headers: dict[str, str] | None = None) -> Wire:
    return Wire(lambda request: httpx.Response(status, json=body, headers=headers or {}))


def refusing_to_connect() -> Wire:
    """A request that goes out and finds nothing on the other end."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection reset by peer", request=request)

    return Wire(handler)


def call(wire: Wire, monkeypatch: pytest.MonkeyPatch) -> FamilyAttempt:
    """Run one attempt of the real frozen arm against a mocked wire.

    Goes through `_ask`, not through the adapter alone, because the question is
    what gets STORED -- and the classification that was wrong lived in `_ask`.
    """
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: REAL_OPENAI(
            **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(wire))
        ),
    )
    client = openai_compatible.ChatCompletionsClient(
        route=openai_compatible.ROUTES["openrouter"], api_key="sentinel-not-a-real-key"
    )
    built = build_description_request("sha256:a", "A posting.", ARM)
    outcome = ExtractionOutcome(job_id="gc-09")
    _ask(client, ARM, built, TDescriptionFamily, outcome, max_attempts=1)
    assert len(outcome.attempts) == 1, "an attempt was observed and not recorded"
    return outcome.attempts[0]


# =========================================================================
# 1. ANY HTTP RESPONSE IS A RESPONSE
# =========================================================================


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, OK_BODY),
        (400, {"error": {"message": "invalid request", "code": 400}}),
        (402, {"message": "Payment required", "type": "payment_required_error", "param": "quota"}),
        (404, ROUTER_404),
        (429, UPSTREAM_429),
        (503, {"error": {"message": "upstream is overloaded", "code": 503}}),
    ],
)
def test_every_http_status_is_recorded_as_a_response(
    status: int, body: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """2xx, 4xx and 5xx alike. The status is not the question.

    A 402 is a permanent refusal, a 404 condemns the arm, a 429 buys a wait and
    a 200 may still be unparseable -- four different downstream decisions, all
    made from evidence that only exists because the vendor answered.
    """
    attempt = call(responding(status, body), monkeypatch)
    assert attempt.transport == Transport.RESPONSE_RECEIVED.value, (
        f"HTTP {status} is a response; only a missing response is SENT_NO_RESPONSE"
    )


def test_a_connection_that_dies_before_a_response_is_the_only_sent_no_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The state's actual meaning: the request went out, nothing came back.

    Quota may well have been consumed -- the far end may have read it -- and
    that is exactly why this is not NOT_SENT either.
    """
    attempt = call(refusing_to_connect(), monkeypatch)
    assert attempt.transport == Transport.SENT_NO_RESPONSE.value
    assert attempt.response_envelope is None, "there was no response to build an envelope from"


def test_a_local_sdk_failure_never_reached_http_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    """No credential, no client, no request. The eighteen-failures case."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    wire = responding(200, OK_BODY)
    client = openai_compatible.ChatCompletionsClient(
        route=openai_compatible.ROUTES["openrouter"], api_key=""
    )
    built = build_description_request("sha256:a", "A posting.", ARM)
    outcome = ExtractionOutcome(job_id="gc-09")

    with pytest.raises(openai.OpenAIError):
        openai.OpenAI(api_key=None, base_url="https://openrouter.ai/api/v1")

    _ask(client, ARM, built, TDescriptionFamily, outcome, max_attempts=1)

    assert outcome.attempts[0].transport == Transport.NOT_SENT.value
    assert wire.requests == 0
    assert outcome.arm_unusable, "a defect of ours repeats identically on every posting"


# =========================================================================
# 2. THE ERROR ENVELOPE IS EVIDENCE
# =========================================================================


def test_a_429_keeps_the_provider_evidence_it_arrived_with(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`could not be reached` is not a diagnosis. This is.

    Which provider refused, under which of its own error codes, from which
    pool, and for how long -- four answers that were previously thrown away
    because no ChatCompletion body existed to hang them on.
    """
    attempt = call(responding(429, UPSTREAM_429, {"Retry-After": "5"}), monkeypatch)

    assert attempt.response_envelope is not None, "the provider's own answer was discarded"
    envelope = json.loads(attempt.response_envelope)
    assert envelope["envelope_kind"] == "HTTP_ERROR"
    assert envelope["http_status"] == 429
    assert envelope["provider_name"] == "Decart"
    assert envelope["provider_error_code"] == "upstream_429"
    assert envelope["limit_source"] == "upstream_provider_shared_pool"
    assert envelope["retry_after_seconds"] == 5
    assert envelope["response_headers"]["retry-after"] == "5"


def test_a_404_keeps_the_routing_funnel_that_explains_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The evidence the max_tokens fix was found from, now stored by default."""
    envelope = json.loads(str(call(responding(404, ROUTER_404), monkeypatch).response_envelope))
    assert envelope["failed_routing_step"] == "Filter by Parameters"
    assert envelope["routing_funnel"] == [{"step": "Initial Endpoints", "endpoint_count": 1}]


def test_an_error_envelope_carries_no_credential_and_no_account_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An allow-list, not a filter.

    "Copy the body except the dangerous parts" is a policy that is one vendor
    away from being wrong. The 429 body carried a `user_id`; an account
    identifier is not evidence about a request, so it is not copied -- and a
    request header, which is where an Authorization value would be, is never
    read at all.
    """
    attempt = call(responding(429, UPSTREAM_429, {"Retry-After": "5"}), monkeypatch)
    serialised = str(attempt.response_envelope)

    assert "sentinel-not-a-real-key" not in serialised
    assert "authorization" not in serialised.lower()
    assert "user_id" not in serialised
    assert "user_2w981" not in serialised


def test_a_successful_envelope_and_an_error_envelope_say_which_they_are(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both are response evidence, and neither is the other."""
    ok = json.loads(str(call(responding(200, OK_BODY), monkeypatch).response_envelope))
    bad = json.loads(str(call(responding(429, UPSTREAM_429), monkeypatch).response_envelope))

    assert ok["envelope_kind"] == "MODEL_RESPONSE"
    assert ok["usage"]["prompt_tokens"] == 1
    assert bad["envelope_kind"] == "HTTP_ERROR"
    assert "choices" not in bad, "a refusal has no choices, and must not pretend to"


def test_an_error_envelope_does_not_carry_a_whole_body_into_a_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Vendor prose is bounded. The full text stays in `llm_call.error`."""
    huge = {"error": {"message": "x" * 5_000, "code": 429, "metadata": {"raw": "y" * 5_000}}}
    envelope = json.loads(str(call(responding(429, huge), monkeypatch).response_envelope))

    assert len(envelope["message"]) < 500
    assert envelope["message"].endswith("...")
    assert len(envelope["raw"]) < 500


# =========================================================================
# 3. EXACTLY ONE HTTP REQUEST MEANS EXACTLY ONE
# =========================================================================


def test_one_attempt_is_one_http_request_even_on_a_429(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SDK had a retry policy of its own, and it was invisible.

    `openai.DEFAULT_MAX_RETRIES` is 2, and 429/5xx are retried -- sleeping on
    the response's own Retry-After. So a canary authorised for ONE request
    against a 50-per-day free tier would have sent three, while `live_calls`
    counted one and the report said one. Retrying is this project's decision,
    taken in `_ask` under `--max-attempts`.
    """
    assert openai.DEFAULT_MAX_RETRIES == 2, "the SDK default this guard exists for"

    wire = responding(429, UPSTREAM_429, {"Retry-After": "0"})
    call(wire, monkeypatch)
    assert wire.requests == 1, "the SDK retried underneath our own budget"


# =========================================================================
# 4. RETRY-AFTER IS A FLOOR
# =========================================================================


def test_the_provider_hint_is_read_from_structured_fields_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A documented field or a header. Never a number scraped out of prose."""
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: REAL_OPENAI(
            **kwargs,
            http_client=httpx.Client(
                transport=httpx.MockTransport(responding(429, UPSTREAM_429, {"Retry-After": "5"}))
            ),
        ),
    )
    client = openai_compatible.ChatCompletionsClient(
        route=openai_compatible.ROUTES["openrouter"], api_key="sentinel-not-a-real-key"
    )
    built = build_description_request("sha256:a", "A posting.", ARM)

    with pytest.raises(LLMErrorResponse) as raised:
        client.complete(built.request, ARM)

    assert raised.value.status == 429
    assert raised.value.retry_after_seconds == 5.0


def test_a_number_in_vendor_prose_is_never_a_cooldown() -> None:
    """The `param: 'quota'` mistake, in its pacing form.

    "429 of 500 requests today" and "retry in a few minutes" both contain
    digits and neither is an instruction. Only a structured field counts.
    """

    class Prose:
        status_code = 429
        body = {"error": {"message": "rate limited; try again in 30 minutes", "code": 429}}
        response = None

    assert openai_compatible._retry_after_seconds(Prose()) is None


def test_a_retry_after_header_alone_is_enough() -> None:
    """Some routes send the standard header and no structured field."""

    class Headered:
        status_code = 503
        body: dict[str, Any] = {}
        response = httpx.Response(503, headers={"Retry-After": "12"})

    assert openai_compatible._retry_after_seconds(Headered()) == 12.0


def test_an_http_date_retry_after_is_declined_rather_than_guessed() -> None:
    """Reading it needs a clock, and this project injects its clock."""

    class Dated:
        status_code = 503
        body: dict[str, Any] = {}
        response = httpx.Response(503, headers={"Retry-After": "Wed, 03 Sep 2026 18:22:43 GMT"})

    assert openai_compatible._retry_after_seconds(Dated()) is None


@pytest.mark.parametrize(
    ("hint", "local", "expected"),
    [
        (5.0, 20.0, 20.0),  # the observed 429: local policy is stricter, and wins
        (30.0, 20.0, 30.0),  # the provider asks for longer, and wins
        (None, 20.0, 20.0),  # no hint is not a shorter wait
        (0.0, 20.0, 20.0),
    ],
)
def test_the_effective_cooldown_is_never_shorter_than_the_provider_asked(
    hint: float | None, local: float, expected: float
) -> None:
    """One-directional: a hint is a floor, never a schedule.

    A local policy may be longer -- a safety margin is ours to set. It may
    never be shorter, because that is the pace the provider has just refused.
    """
    error = LLMErrorResponse("429", status=429, retry_after_seconds=hint)
    assert _cooldown_for(error, local) == expected


def test_a_failure_with_no_hint_at_all_still_gets_the_local_cooldown() -> None:
    """Every other `LLMError` keeps exactly the behaviour it had."""
    assert _cooldown_for(LLMUnavailable("503 unavailable"), 20.0) == 20.0
    assert _cooldown_for(LLMNotSent("never sent"), 20.0) == 20.0


# =========================================================================
# 5. READING A ROW THAT WAS WRITTEN UNDER THE OLD MEANING
# =========================================================================

#: The stored `error` of `llm_call` 01M1M84CDT9WRHQHF4H9TV3SG5, verbatim.
STORED_429 = (
    "openrouter could not be reached: Error code: 429 - {'error': {'message': 'Provider "
    "returned error', 'code': 429, 'metadata': {'provider_name': 'Decart', "
    "'provider_error_code': 'upstream_429', 'retry_after_seconds': 5}}}"
)

STORED_404 = (
    "openrouter could not be reached: Error code: 404 - {'error': {'message': 'No endpoints "
    "found that can handle the requested parameters.', 'code': 404}}"
)


def test_a_stored_row_proves_its_own_status_from_the_sdk_marker() -> None:
    """`Error code: NNN` is an SDK format, produced only from a real response.

    Which is why it may be read and a bare three-digit number may not: the
    prefix is a structured fact that was flattened into a string on its way to
    storage, not a number found in a sentence.
    """
    assert http_status_in(STORED_429) == 429
    assert http_status_in(STORED_404) == 404
    assert observed_transport("SENT_NO_RESPONSE", STORED_429) is Transport.RESPONSE_RECEIVED
    assert observed_transport("SENT_NO_RESPONSE", STORED_404) is Transport.RESPONSE_RECEIVED


def test_the_derivation_only_ever_upgrades_and_never_guesses() -> None:
    """Absence of proof is not proof of absence, in either direction."""
    # Nothing provable: left exactly as recorded.
    assert (
        observed_transport("SENT_NO_RESPONSE", "openrouter could not be reached: timeout") is None
    )
    assert (
        observed_transport("UNRECORDED", "Completions.create() got an unexpected keyword") is None
    )
    assert observed_transport("NOT_SENT", None) is None
    # Never downgraded.
    assert observed_transport("RESPONSE_RECEIVED", "anything at all") is None


def test_a_bare_status_number_in_prose_proves_nothing() -> None:
    """The half of the rule that keeps it honest.

    `param: 'quota'` cost eighteen requests once. A rule that read any 429 in
    any sentence as an HTTP response would be the same mistake with a different
    field.
    """
    assert http_status_in("google could not be reached: 503 UNAVAILABLE") is None
    assert http_status_in("429 of 500 daily requests used") is None
    assert transport_from_error("google could not be reached: 503 UNAVAILABLE") is (
        Transport.SENT_NO_RESPONSE
    )

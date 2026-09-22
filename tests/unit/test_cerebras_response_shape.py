"""Where a GPT-OSS answer lives, and where its thinking lives. Not the same place.

THE RUN THIS EXISTS TO EXPLAIN
------------------------------
The first Cerebras screen was SERVED: 18 requests, 137,878 input tokens, 62,794
output tokens, US$ 0.0954 on promotional credit. Our adapter observed empty
content on every one of them, and the attempt rows were then discarded by the
ledger defect -- so the only surviving description of what the vendor said is
that it said nothing useful, which is our interpretation and not evidence.

WHAT THE DOCUMENTATION SAYS, READ 2026-09-03
--------------------------------------------
    message.content     "The contents of the message" (nullable)
    message.reasoning   "The model's reasoning content when using reasoning
                         models"

    reasoning_format    parsed | raw | hidden | none
                        GPT OSS supports parsed, raw and hidden.
                        parsed -> reasoning in `message.reasoning`, final answer
                                  in `message.content`
                        raw    -> reasoning prepended to content, concatenated
                                  without separators
                        hidden -> reasoning dropped; final answer in content;
                                  the tokens are still generated and billed
                        Sent through `extra_body`: the OpenAI SDK does not
                        declare it.

    "Reasoning tokens count toward max_completion_tokens and the completion-token
     usage reported by the API." No separate reasoning-token field is documented.

THE INVARIANT
-------------
Content and reasoning are two channels and the final answer is contractually in
`content`. This repository will not implement "if content is empty, use
reasoning": under `parsed` that would feed the assembler a monologue, and under
`raw` the answer is already in content. Both are preserved STRUCTURALLY -- the
envelope reports presence and length -- so a diagnosis is possible without ever
guessing which field held the answer.

Everything here is offline. The response bodies are shaped from the documented
schema; the wire is a mocked httpx transport driving the real installed SDK.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import openai
import pytest

from career_agent.llm.client import Family, ModelConfig, StructuredOutput
from career_agent.llm.failures import Transport
from career_agent.llm.requests import build_description_request
from career_agent.llm.transport import TDescriptionFamily
from career_agent.llm.vendors import openai_compatible
from career_agent.pipeline.extract import ExtractionOutcome, FamilyAttempt, _ask

REAL_OPENAI = openai.OpenAI

#: The historical arm, exactly. Description PLAIN_JSON because our description
#: schema is 8,536 characters against a documented 5,000-character strict cap.
ARM = ModelConfig(
    vendor="cerebras",
    identifier="gpt-oss-120b",
    reasoning="medium",
    per_family=openai_compatible.CEREBRAS_PER_FAMILY,
)

#: A plausible answer, kept tiny. What matters is which field it is in.
ANSWER = json.dumps(
    {
        "observed_title": "Business Technology Analyst",
        "function_signals": [],
        "observations": [],
        "responsibilities": [],
        "software": [],
        "languages": [],
        "evidence": [],
    }
)

THINKING = "The posting says " * 875  # ~14,000 characters of monologue


def body(
    *,
    content: str | None,
    reasoning: str | None = None,
    finish_reason: str = "stop",
    completion_tokens: int = 3_488,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A chat-completions body in the shape Cerebras documents."""
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    return {
        "id": "chatcmpl-x",
        "object": "chat.completion",
        "created": 0,
        "model": "gpt-oss-120b",
        "choices": [{"index": 0, "finish_reason": finish_reason, "message": message}],
        "usage": {
            "prompt_tokens": 7_660,
            "completion_tokens": completion_tokens,
            "total_tokens": 7_660 + completion_tokens,
        },
        **(extra or {}),
    }


def call(payload: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> FamilyAttempt:
    """One description attempt against a mocked wire, stored the production way."""
    monkeypatch.setattr(
        openai,
        "OpenAI",
        lambda **kwargs: REAL_OPENAI(
            **kwargs,
            http_client=httpx.Client(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
            ),
        ),
    )
    client = openai_compatible.ChatCompletionsClient(
        route=openai_compatible.ROUTES["cerebras"], api_key="sentinel-not-a-real-key"
    )
    built = build_description_request("sha256:a", "A posting.", ARM)
    outcome = ExtractionOutcome(job_id="gc-09")
    _ask(client, ARM, built, TDescriptionFamily, outcome, max_attempts=1)
    return outcome.attempts[0]


# =========================================================================
# 1. TWO CHANNELS, AND ONLY ONE OF THEM IS AN ANSWER
# =========================================================================


def test_reasoning_is_never_promoted_into_the_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact shape of the lost run, and the fix we must NOT make.

    Empty content beside 14,000 characters of reasoning is the signature the
    Cerebras screen produced. Reading the monologue as the answer would hand the
    assembler prose to validate, turn a transport problem into a fabricated
    extraction, and do it under a cache key that claims to be an answer.
    """
    attempt = call(body(content="", reasoning=THINKING), monkeypatch)

    assert attempt.raw_output == "", "reasoning was promoted into the answer channel"
    assert attempt.parsed_ok is False
    assert attempt.transport == Transport.RESPONSE_RECEIVED.value


def test_an_answer_in_content_is_not_contaminated_by_reasoning_beside_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The other half: a real answer must arrive intact, monologue or not."""
    attempt = call(body(content=ANSWER, reasoning=THINKING), monkeypatch)

    assert attempt.raw_output == ANSWER
    assert "The posting says" not in attempt.raw_output
    assert attempt.parsed_ok and attempt.validated_ok


# =========================================================================
# 2. THE ENVELOPE IS THE DIAGNOSIS THE LOST RUN DID NOT HAVE
# =========================================================================


def test_the_envelope_says_where_the_tokens_went(monkeypatch: pytest.MonkeyPatch) -> None:
    """Presence and length of both channels, before anyone reads either.

    This is the row the 18 served requests should have left behind. It answers
    "the vendor billed 3,488 output tokens and we saw nothing -- where did they
    go" without anyone re-running anything.
    """
    attempt = call(
        body(content="", reasoning=THINKING, extra={"time_info": {"queue_time": 0.01}}),
        monkeypatch,
    )
    envelope = json.loads(str(attempt.response_envelope))
    choice = envelope["choices"][0]

    assert envelope["envelope_kind"] == "MODEL_RESPONSE"
    assert envelope["http_status"] == 200, "a success has a status too"
    assert envelope["model"] == "gpt-oss-120b"
    assert choice["finish_reason"] == "stop"
    assert choice["message_keys"] == ["content", "reasoning", "role"]
    assert choice["field_lengths"]["content"] == 0
    assert choice["field_lengths"]["reasoning"] == len(THINKING)
    assert envelope["usage"]["completion_tokens"] == 3_488
    # A vendor field nobody here has heard of, named rather than dropped.
    assert envelope["extra_keys"] == ["time_info"]


def test_the_envelope_carries_no_reasoning_text_only_its_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Diagnosis needs the shape. It does not need the monologue.

    `raw_output` already holds whatever was in the answer channel; duplicating
    a 14,000-character reasoning trace into a second column would bloat every
    report for nothing.
    """
    serialised = str(call(body(content="", reasoning=THINKING), monkeypatch).response_envelope)

    assert "The posting says" not in serialised
    assert str(len(THINKING)) in serialised


def test_an_absent_reasoning_field_is_absent_rather_than_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`hidden`, or a non-reasoning model. Length 0 and "no such field" differ."""
    envelope = json.loads(str(call(body(content=ANSWER), monkeypatch).response_envelope))

    assert envelope["choices"][0]["message_keys"] == ["content", "role"]
    assert "reasoning" not in envelope["choices"][0]["field_lengths"]


# =========================================================================
# 3. A BUDGET SPENT ON THINKING IS A TRUNCATION, AND SAYS SO
# =========================================================================


def test_reasoning_that_eats_the_output_budget_is_reported_as_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Documented: reasoning tokens count toward the completion-token ceiling.

    So a model can think its way past `max_tokens` and return an empty answer
    with `finish_reason: "length"`. That is a ceiling problem, and it must not
    be recorded as a posting that said nothing.
    """
    attempt = call(
        body(content="", reasoning=THINKING, finish_reason="length", completion_tokens=8_000),
        monkeypatch,
    )

    assert attempt.error is not None and "cut off" in attempt.error
    assert attempt.parsed_ok is False, "a truncation is not a parse failure to be tuned"
    envelope = json.loads(str(attempt.response_envelope))
    assert envelope["choices"][0]["finish_reason"] == "length"


# =========================================================================
# 4. THE HISTORICAL ARM, ON THE WIRE, UNCHANGED
# =========================================================================


def test_the_diagnostic_request_reproduces_the_historical_transport() -> None:
    """Diagnosis, not improvement: the same body the lost run sent.

    `reasoning_format` is deliberately ABSENT. The historical screen never sent
    it, so the vendor's default applied, and a diagnostic that changes the
    variable it is trying to observe measures a different request.
    """
    built = build_description_request("sha256:a", "A posting.", ARM)
    payload = openai_compatible.build_payload(built.request, ARM)

    assert ARM.output_mode(Family.DESCRIPTION) is StructuredOutput.PLAIN_JSON
    assert ARM.output_mode(Family.PROVIDER) is StructuredOutput.STRICT_SCHEMA
    assert "response_format" not in payload, "PLAIN_JSON sends no schema at all"
    assert "extra_body" not in payload, "no reasoning_format, no provider routing"
    assert payload["reasoning_effort"] == "medium", "Cerebras declares this one; it stays top-level"
    assert payload["model"] == "gpt-oss-120b"
    assert payload["max_completion_tokens"] == 8_000
    assert "max_tokens" not in payload, "the OpenRouter spelling must not leak into this route"


def test_the_documented_json_object_candidate_is_not_what_this_round_sends() -> None:
    """The recommendation is a DIFFERENT arm, and the code must not pre-empt it.

    `json_object` and `reasoning_format: "hidden"` are both documented and both
    plausible fixes. Either would change `static_digest`, and therefore the
    benchmark identity -- so neither may arrive quietly inside a diagnostic.
    """
    payload = openai_compatible.build_payload(
        build_description_request("sha256:a", "A posting.", ARM).request, ARM
    )

    assert "response_format" not in payload
    assert "reasoning_format" not in json.dumps(payload)

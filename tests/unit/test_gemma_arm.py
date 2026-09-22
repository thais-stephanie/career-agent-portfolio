"""One vendor, two model families, and the setting that would have been ignored.

`google` is a vendor name, not a request dialect. Gemini takes a thinking
BUDGET in tokens; Gemma 4 takes a thinking LEVEL, and documents exactly two --
"HIGH is enabled, MINIMAL is disabled" (Gemma-on-Gemini-API, read 2026-09-03).

Sending a budget to Gemma is not an error anyone reports. An unrecognised
generation field is ignored, the request succeeds, and the benchmark row claims
a reasoning setting the model never received. This adapter already carries the
scar: it asked for `usageMetadata` while the SDK handed it `usage_metadata`, and
55 live calls recorded zero tokens while every gate stayed green.

The installed SDK will not save us either. `types.ThinkingConfig(
thinking_level="MEDIUM")` constructs happily, so the refusal has to be ours.

Everything here is offline. Nothing constructs a client or reads a credential.
"""

from __future__ import annotations

import json

import pytest

from career_agent.llm.cache import description_key, static_digest
from career_agent.llm.client import Family, ModelConfig, StructuredOutput
from career_agent.llm.pricing import Funding, observed_price
from career_agent.llm.prompts import DESCRIPTION_PROMPT_VERSION
from career_agent.llm.quotas import observed_quota
from career_agent.llm.requests import build_description_request
from career_agent.llm.vendors import google as google_adapter
from career_agent.llm.vendors.schema_dialect import google_openapi

GEMMA = "gemma-4-31b-it"
GEMMA_FALLBACK = "gemma-4-26b-a4b-it"
GEMINI = "gemini-3.1-flash-lite"

POSTING = "We are hiring a Business Technology Analyst in Amsterdam."


def arm(identifier: str, reasoning: str | None) -> ModelConfig:
    return ModelConfig(vendor="google", identifier=identifier, reasoning=reasoning)


def payload(identifier: str, reasoning: str | None) -> dict:
    config = arm(identifier, reasoning)
    built = build_description_request("sha256:a", POSTING, config)
    return google_adapter.build_payload(built.request, config)


# =========================================================================
# 1. THINKING, IN EACH FAMILY'S OWN GRAMMAR
# =========================================================================


@pytest.mark.parametrize("model", [GEMMA, GEMMA_FALLBACK, "models/gemma-4-31b-it"])
def test_gemma_asks_for_a_thinking_level_and_never_a_budget(model: str) -> None:
    """The defect this file was written for, stated in one assertion.

    `thinkingBudget` on a Gemma request is silently dropped, and the run then
    reports an arm at a reasoning setting it never had.
    """
    high = payload(model, "high")["generationConfig"]["thinkingConfig"]
    minimal = payload(model, "minimal")["generationConfig"]["thinkingConfig"]

    assert high == {"thinkingLevel": "HIGH"}
    assert minimal == {"thinkingLevel": "MINIMAL"}
    assert "thinkingBudget" not in json.dumps(payload(model, "high"))


def test_gemini_keeps_the_budget_it_was_benchmarked_on() -> None:
    """The half that must not move. Stage 0 ran on these numbers."""
    assert payload(GEMINI, "high")["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 16_000
    }
    assert payload(GEMINI, "medium")["generationConfig"]["thinkingConfig"] == {
        "thinkingBudget": 8_000
    }
    assert payload(GEMINI, "off")["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}
    assert "thinkingLevel" not in json.dumps(payload(GEMINI, "high"))


@pytest.mark.parametrize("label", ["low", "medium", "off", "xhigh", "HIGH ", ""])
def test_a_level_gemma_does_not_document_is_refused_rather_than_translated(label: str) -> None:
    """Refusal, not the nearest thing that would be accepted.

    A Gemma arm asked for `medium` has been mis-specified, and there are only
    two possible answers: say so, or quietly benchmark something else.
    """
    with pytest.raises(ValueError, match="Gemma"):
        google_adapter.thinking_config(arm(GEMMA, label))


@pytest.mark.parametrize("label", ["minimal", "xhigh", ""])
def test_gemini_still_refuses_a_label_it_does_not_document(label: str) -> None:
    """`minimal` is a Gemma word. It must not silently become a Gemini budget."""
    with pytest.raises(ValueError, match="google"):
        google_adapter.thinking_config(arm(GEMINI, label))


def test_the_family_is_decided_by_the_model_id_not_by_a_list() -> None:
    """A list would read the NEXT Gemma release as a Gemini model, silently."""
    assert google_adapter.is_gemma(GEMMA)
    assert google_adapter.is_gemma("models/gemma-4-26b-a4b-it")
    assert google_adapter.is_gemma("gemma-5-whatever-it")
    assert not google_adapter.is_gemma(GEMINI)
    assert not google_adapter.is_gemma("models/gemini-3.1-flash-lite")


# =========================================================================
# 2. RECURRING FREE, PER EXACT MODEL ID
# =========================================================================


@pytest.mark.parametrize("model", [GEMMA, GEMMA_FALLBACK])
def test_gemma_is_recorded_as_recurring_free_with_its_provenance(model: str) -> None:
    """Free tier, not a trial, and the difference is a whole class of bill.

    Google's pricing table lists Gemma 4 as "Free of charge" for input and
    output AND the paid tier as "Not available" -- there is no rate to fall
    into. The Cerebras row two entries above is the contrast: real rates, real
    economic cost, absorbed by a grant that runs out.
    """
    price = observed_price("google", model)
    assert price is not None, "an unpriced arm cannot be run at all"
    assert price.funding is Funding.FREE_TIER
    assert price.free
    assert price.input_per_mtok == 0.0
    assert price.output_per_mtok == 0.0
    assert price.promotional_credit_usd is None, "a grant would make this promotional, not free"
    assert price.observed_at == "2026-09-03"
    assert "Not available" in price.source


def test_being_free_is_not_generalised_from_one_gemma_to_the_family() -> None:
    """A price borrowed from a neighbour is a price nobody read."""
    assert observed_price("google", "gemma-4-12b-it") is None
    assert observed_price("google", "gemma-4-e4b-it") is None


@pytest.mark.parametrize("model", [GEMMA, GEMMA_FALLBACK])
def test_the_gemma_quota_is_this_project_read_on_one_day(model: str) -> None:
    """Gemma limits are in neither the metadata API nor the public page.

    So they were read off this maintainer's console, and they are recorded as
    exactly that. Both ids showed the same three numbers and both are recorded
    separately anyway: "the console showed the same values" is an observation
    about two models, and copying one row to the other would quietly turn it
    into an assumption about a family.
    """
    quota = observed_quota("google", model)
    assert quota is not None
    assert quota.requests_per_minute == 30
    assert quota.tokens_per_minute == 16_000
    assert quota.requests_per_day == 14_400
    assert quota.free
    assert quota.observed_at == "2026-09-03"
    assert "AI Studio" in quota.source


def test_the_absent_daily_token_limit_cannot_be_read_as_a_number() -> None:
    """The console exposes no TPD, and absent is neither zero nor unlimited.

    `QuotaLimits` has no daily-token field at all, so there is nothing for a
    caller to read and nothing for a report to round. The gap is stated in the
    `source` string, where a person will see it.
    """
    quota = observed_quota("google", GEMMA)
    assert quota is not None
    assert "tokens_per_day" not in quota.__dataclass_fields__
    assert "TPD not exposed" in quota.source


def test_tpm_binds_long_before_rpm_on_this_project() -> None:
    """The number that will shape every future screen, asserted once here.

    30 requests a minute sounds generous and is not the ceiling that binds:
    one description call is ~9,400 input tokens against a 16,000-token minute,
    so the second call in any minute crosses TPM while RPM is still at 2 of 30.
    A run paced on requests alone would overrun the limit it was watching --
    which is precisely why `pacing.RateLimiter` enforces both.
    """
    quota = observed_quota("google", GEMMA)
    assert quota is not None
    description_input = 9_425  # the measured gc-09 upper bound
    assert description_input < quota.tokens_per_minute, "one call must at least fit"
    calls_allowed_by_tokens = quota.tokens_per_minute // description_input
    assert calls_allowed_by_tokens == 1
    assert calls_allowed_by_tokens < quota.requests_per_minute


def test_a_gemma_nobody_observed_is_still_refused() -> None:
    """Two rows are two observations, not a family-wide fact."""
    assert observed_quota("google", "gemma-4-12b-it") is None
    assert observed_quota("google", "gemma-4-e4b-it") is None
    flash = observed_quota("google", GEMINI)
    assert flash is not None and flash.requests_per_minute == 15
    assert flash.tokens_per_minute == 250_000, "Flash Lite's ceiling is not Gemma's"


# =========================================================================
# 3. CACHE IDENTITY: FOUR ARMS, FOUR KEYS
# =========================================================================


def keys() -> dict[str, str]:
    """One description request per arm, keyed the production way."""
    arms = {
        "gemma@high+strict": arm(GEMMA, "high"),
        "gemma@minimal+strict": ModelConfig(vendor="google", identifier=GEMMA, reasoning="minimal"),
        "gemma@high+json": ModelConfig(
            vendor="google",
            identifier=GEMMA,
            reasoning="high",
            structured_output=StructuredOutput.PLAIN_JSON,
        ),
        "gemini@high+strict": arm(GEMINI, "high"),
        "gemma-26b@high+strict": arm(GEMMA_FALLBACK, "high"),
    }
    return {
        name: build_description_request("sha256:a", POSTING, config).cache_key.key
        for name, config in arms.items()
    }


def test_every_transport_difference_is_a_different_semantic_arm() -> None:
    """A Gemma answer must never be served for a Gemini question, or the reverse.

    Four things vary here and each one changes what was asked: the model, the
    thinking level, the enforcement mode, and the model again. If any two
    collided, a re-run would serve a stored answer to a question nobody asked.
    """
    computed = keys()
    assert len(set(computed.values())) == len(computed), computed


def test_the_enforcement_mode_reaches_the_key_through_the_static_digest() -> None:
    """STRICT_SCHEMA and PLAIN_JSON ask different questions of the same model.

    The arm LABEL is deliberately identical -- `gemma-4-31b-it@high` names who
    was asked, and a per-family mode would make one run report two arms. The
    mode rides in `static_digest`, which is where a change to the request
    itself belongs.
    """
    strict = arm(GEMMA, "high")
    plain = ModelConfig(
        vendor="google",
        identifier=GEMMA,
        reasoning="high",
        structured_output=StructuredOutput.PLAIN_JSON,
    )
    assert strict.arm == plain.arm == "gemma-4-31b-it@high"

    a = build_description_request("sha256:a", POSTING, strict)
    b = build_description_request("sha256:a", POSTING, plain)
    assert a.cache_key.key != b.cache_key.key
    assert a.cache_key.inputs != b.cache_key.inputs

    # And the difference is the static digest specifically -- the prompt and
    # the schema are identical, so only the enforcement mode can have moved it.
    assert static_digest(a.request.system, a.request.schema, "STRICT_SCHEMA") != static_digest(
        a.request.system, a.request.schema, "PLAIN_JSON"
    )


def test_the_arm_label_carries_the_thinking_level() -> None:
    """A benchmark row that cannot name its reasoning setting is unreproducible."""
    assert arm(GEMMA, "high").arm == "gemma-4-31b-it@high"
    assert arm(GEMMA, "minimal").arm == "gemma-4-31b-it@minimal"
    assert description_key is not None  # imported for the module it belongs to


# =========================================================================
# 4. THE SERIALISED REQUEST
# =========================================================================


def test_the_gemma_request_is_the_one_the_arm_was_frozen_as() -> None:
    """The production adapter path, not a Gemma-shaped test double."""
    config = arm(GEMMA, "high")
    built = build_description_request("sha256:a", POSTING, config)
    body = google_adapter.build_payload(built.request, config)
    generation = body["generationConfig"]

    assert body["model"] == GEMMA
    # The CURRENT description prompt, whatever it is: this test is about the
    # arm's transport, and pinning a version here would make a deliberate bump
    # look like a transport regression. `test_prompts.py` owns the version.
    assert body["systemInstruction"]["parts"][0]["text"].startswith(
        f"# {DESCRIPTION_PROMPT_VERSION}"
    )
    assert built.prompt_version == DESCRIPTION_PROMPT_VERSION
    assert body["contents"] == [{"role": "user", "parts": [{"text": POSTING}]}]
    assert generation["thinkingConfig"] == {"thinkingLevel": "HIGH"}
    assert generation["responseMimeType"] == "application/json"
    assert generation["maxOutputTokens"] == 8_000
    assert generation["temperature"] == 0.0
    assert generation["responseSchema"] == google_openapi(built.request.schema)
    assert config.output_mode(Family.DESCRIPTION) is StructuredOutput.STRICT_SCHEMA


def test_the_gemma_request_carries_no_fallback_and_no_private_data() -> None:
    """Two absences, both load-bearing.

    M2 is candidate-independent by design: a fingerprint describes the work, and
    a later layer decides whether it is wanted. A request that carried career
    intent would produce a document that could never be reused for anyone else.
    """
    config = arm(GEMMA, "high")
    built = build_description_request("sha256:a", POSTING, config)
    body = json.dumps(google_adapter.build_payload(built.request, config)).lower()

    for private in ("thais", "holanda", "contato@", "profile.local", "career_intent"):
        assert private not in body

    for fallback in (GEMMA_FALLBACK, GEMINI, "gemini-3", "models/", '"fallback"'):
        assert fallback.lower() not in body.replace(f'"{GEMMA}"', "")


def test_the_schema_that_reaches_google_has_lost_every_rejected_keyword() -> None:
    """Re-confirmed on the FINAL request object, not on the transport model.

    Gemini's subset rejects what it does not recognise rather than ignoring it,
    so a survivor is a 400. The schema itself is untouched: this is translation
    at the boundary, which is where a vendor dialect belongs.
    """
    config = arm(GEMMA, "high")
    built = build_description_request("sha256:a", POSTING, config)
    sent = google_adapter.build_payload(built.request, config)["generationConfig"]["responseSchema"]

    text = json.dumps(sent)
    for rejected in ('"$defs"', '"$ref"', '"$schema"', '"additionalProperties"', '"const"'):
        assert rejected not in text, f"{rejected} survives into the Gemini request"
    # The union form the transport needs for "a value or nothing" is supported
    # and must survive, or every nullable dimension would be mis-stated.
    assert '"anyOf"' in text


# =========================================================================
# 5. EVIDENCE SURVIVES WHATEVER THE ROUTE DOES
# =========================================================================


def test_a_google_answer_now_carries_the_envelope_the_other_route_has() -> None:
    """Parts, not one content field -- and a part is an answer or a thought.

    The thought TEXT is never copied. Its presence and length are the
    diagnosis; the text is the model's private reasoning.
    """
    response = google_adapter.parse_response(
        {
            "model_version": "gemma-4-31b-it",
            "response_id": "abc",
            "candidates": [
                {
                    "finish_reason": "STOP",
                    "content": {
                        "parts": [
                            {"text": "thinking out loud" * 100, "thought": True},
                            {"text": '{"observed_title": "Analyst"}'},
                        ]
                    },
                }
            ],
            "usage_metadata": {
                "prompt_token_count": 9_400,
                "candidates_token_count": 2_100,
                "thoughts_token_count": 1_700,
            },
            "time_info": {"queue": 1},
        }
    )

    assert response.raw_text == '{"observed_title": "Analyst"}', "a thought became the answer"
    envelope = response.envelope
    assert envelope is not None
    assert envelope["envelope_kind"] == "MODEL_RESPONSE"
    assert envelope["model_version"] == "gemma-4-31b-it"
    assert envelope["usage"]["thoughts_token_count"] == 1_700
    assert envelope["extra_keys"] == ["time_info"]

    candidate = envelope["candidates"][0]
    assert candidate["finish_reason"] == "STOP"
    assert candidate["part_count"] == 2
    assert candidate["answer_text_chars"] == len('{"observed_title": "Analyst"}')
    assert candidate["thought_text_chars"] == len("thinking out loud" * 100)
    assert "thinking out loud" not in json.dumps(envelope)


def test_a_rejected_request_keeps_the_reason_google_gave() -> None:
    """The likeliest outcome of this canary, and the one worth storing.

    Whether Gemma accepts `response_schema` is not settled by Google's own
    documentation. A 400 that says so is a transport FACT; "google could not be
    reached" is not.
    """

    class Rejected:
        code = 400
        status = "INVALID_ARGUMENT"
        message = "Json schema is not supported for this model."
        response_json = {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "Json schema is not supported for this model.",
                "details": [{"@type": "type.googleapis.com/google.rpc.BadRequest"}],
            }
        }

    envelope = google_adapter._error_envelope(Rejected())
    assert envelope["envelope_kind"] == "HTTP_ERROR"
    assert envelope["http_status"] == 400
    assert envelope["status"] == "INVALID_ARGUMENT"
    assert "not supported" in str(envelope["message"])
    assert envelope["detail_types"] == ["type.googleapis.com/google.rpc.BadRequest"]


def test_google_retry_after_is_read_from_retryinfo_and_nowhere_else() -> None:
    """A documented Duration, parsed as documented. Never a number in prose."""

    class Throttled:
        code = 429
        status = "RESOURCE_EXHAUSTED"
        message = "Quota exceeded, try again in about 32 seconds"
        response_json = {
            "error": {
                "code": 429,
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "32s"}
                ],
            }
        }

    class Prose:
        code = 429
        status = "RESOURCE_EXHAUSTED"
        message = "Quota exceeded, try again in about 32 seconds"
        response_json = {"error": {"code": 429, "message": "try again in 32 seconds"}}

    assert google_adapter._retry_after_seconds(Throttled()) == 32.0
    assert google_adapter._retry_after_seconds(Prose()) is None

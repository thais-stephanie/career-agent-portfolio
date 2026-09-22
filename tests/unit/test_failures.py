"""Precedence, and the eighteen requests that proved it was missing.

The first Cerebras screen was refused with HTTP 402 on every call. The body
named its offending parameter `quota`, the retry classifier matched that word,
and a permanent billing refusal was retried -- turning nine useful access
failures into eighteen requests, none of which could have succeeded.
"""

from career_agent.llm.failures import (
    FailureKind,
    classify,
    is_retryable,
    refused_before_inference,
)

#: The exact body Cerebras returned, 2026-09-03.
CEREBRAS_402 = (
    "cerebras could not be reached: Error code: 402 - {'message': 'Payment required to "
    "access this resource. Visit your billing tab.', 'type': 'payment_required_error', "
    "'param': 'quota', 'code': 'payment_required'}"
)


def test_a_payment_error_that_mentions_quota_is_still_a_payment_error() -> None:
    """The regression, in the exact words that caused it.

    Two readings are available in this one string and only one is right: an
    account that cannot pay is not an account going too fast. Waiting does not
    help, and retrying only buys a second copy of the message.
    """
    assert classify(CEREBRAS_402) is FailureKind.ACCOUNT_ACCESS
    assert not is_retryable(CEREBRAS_402)
    assert refused_before_inference(CEREBRAS_402)


def test_account_markers_take_precedence_over_transient_ones() -> None:
    """Stated directly, so precedence cannot be lost to a reordering.

    Every string below contains a transient marker AND an account-level one.
    `insufficient_quota` is the sharpest: it is OpenAI's word for an empty
    wallet and it contains `quota`.
    """
    both = [
        CEREBRAS_402,
        "Error code: 429 - insufficient_quota: you exceeded your current quota",
        "402 payment required; rate limit also applies",
        "401 unauthorized (service temporarily degraded)",
    ]
    for message in both:
        assert classify(message) is FailureKind.ACCOUNT_ACCESS, message
        assert not is_retryable(message)


def test_genuine_transient_failures_are_still_retried() -> None:
    """The half that must not be broken by fixing the other half.

    Stage 0 Take 2 bought four cooldowns from 503s and recovered every one. A
    fix that made those permanent would trade a doubled bill for a failed run.
    """
    transient = [
        "google could not be reached: 503 UNAVAILABLE. This model is currently experiencing "
        "high demand",
        "Error code: 429 - rate limit exceeded, please try again",
        "RESOURCE_EXHAUSTED",
        "the request timed out",
        "server overloaded",
    ]
    for message in transient:
        assert classify(message) is FailureKind.TRANSIENT, message
        assert is_retryable(message)
        assert not refused_before_inference(message)


def test_an_ordinary_failure_is_neither_retried_nor_fatal() -> None:
    """A bad answer is not a bad account, and not a reason to stop the run."""
    ordinary = [
        "output is not JSON: Expecting ',' delimiter",
        "model refused: I cannot help with that",
        "output does not match the TDescriptionFamily schema",
    ]
    for message in ordinary:
        assert classify(message) is FailureKind.OTHER, message
        assert not is_retryable(message)
        assert not refused_before_inference(message)


def test_a_refusal_is_provably_unbilled_and_an_unknown_is_not() -> None:
    """What separates $0.00 from UNKNOWN in the spend audit.

    A request rejected at the gate ran no inference. A request whose usage
    merely went unreported may have run all of it.
    """
    assert refused_before_inference(CEREBRAS_402)
    assert not refused_before_inference("google could not be reached: 503 UNAVAILABLE")
    assert not refused_before_inference("")


#: The exact body OpenRouter returned for the frozen GLM arm, 2026-09-03.
OPENROUTER_ROUTING_404 = (
    "openrouter could not be reached: Error code: 404 - {'error': {'message': 'No "
    "endpoints found that can handle the requested parameters. To learn more about "
    "provider routing, visit: https://openrouter.ai/docs/guides/routing/provider-selection', "
    "'code': 404, 'metadata': {'routing_funnel': [{'step': 'Initial Endpoints', "
    "'endpoint_count': 1}], 'failed_routing_step': 'Filter by Parameters'}}}"
)


def test_a_router_that_ran_out_of_endpoints_condemns_the_arm_not_the_posting() -> None:
    """One endpoint, filtered to zero, and the same answer waiting for all nine.

    A router refusing to place a request is arm-wide: every remaining posting
    would send the identical parameter set and be filtered identically. The
    Cerebras screen bought nine copies of one billing refusal; this would have
    bought nine copies of one routing refusal.

    Unlike an account failure it is fixed by changing the REQUEST, not the
    account -- which is why it is its own kind rather than folded into
    ACCOUNT_ACCESS.
    """
    from career_agent.llm.failures import is_arm_wide

    assert classify(OPENROUTER_ROUTING_404) is FailureKind.ROUTING_INCOMPATIBLE
    assert is_arm_wide(OPENROUTER_ROUTING_404)
    assert not is_retryable(OPENROUTER_ROUTING_404)
    assert refused_before_inference(OPENROUTER_ROUTING_404), "no provider ran a model"


def test_an_ordinary_404_is_not_a_routing_failure() -> None:
    """The evidence has to be the routing language, never the status code.

    A bare 404 is an ordinary "no such thing" -- a mistyped model id, a wrong
    path -- and stopping an arm on one would turn a typo into a benchmark
    verdict.
    """
    from career_agent.llm.failures import is_arm_wide

    ordinary = [
        "could not be reached: Error code: 404 - {'error': 'model not found'}",
        "404 page not found",
        "Error code: 404 - the requested resource does not exist",
    ]
    for message in ordinary:
        assert classify(message) is FailureKind.OTHER, message
        assert not is_arm_wide(message)

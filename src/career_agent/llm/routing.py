"""Which endpoint a routed model id is allowed to reach, as observed on a date.

WHY A ROUTER NEEDS PINNING AND A DIRECT VENDOR DOES NOT
------------------------------------------------------
`google/gemma-4-31b-it` names one machine. `openrouter/z-ai/glm-5.2:free` names
a market: OpenRouter serves one model id from many upstream providers, at
different quantizations, with different parameter support, and it picks. Two
answers to the same question can come from two different companies running two
different builds, and nothing in the model id would say so.

So for a routed vendor the endpoint is part of the arm, and it belongs in the
cache identity for the same reason the model does: an answer from one endpoint
must never be served as an answer from another.

WHY NOT `require_parameters`
----------------------------
The obvious way to pin routing is to ask the router for providers that support
every parameter sent. That is what this project did, and it produced

    404 No endpoints found that can handle the requested parameters

on the only free endpoint there is. A filter that can empty the candidate list
is not a pin; it is a way to lose the route. `allow_fallbacks: false` says the
narrower and more useful thing -- do not silently serve this from somewhere else
-- and cannot filter the primary endpoint out.

DATED OBSERVATIONS, NOT FACTS
-----------------------------
Read off the endpoints API on one day, like `quotas.py` and `pricing.py`. A pair
nobody recorded is refused rather than guessed at: routing changes without
notice, and a pin invented from a model-family page would be a claim about an
endpoint nobody looked at.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PinnedEndpoint:
    """The one upstream endpoint an arm is allowed to be served from."""

    #: As the endpoints API reports it, e.g. `Decart`.
    provider_name: str
    #: The router's own endpoint tag, e.g. `decart/fp4`. Finer than the name:
    #: one provider can serve several builds of one model.
    tag: str
    quantization: str
    #: Prices as observed, per token. Both must be exactly zero for a free arm.
    input_price: float
    output_price: float
    context_length: int
    max_completion_tokens: int
    #: What the endpoint advertised it accepts, on the observed date.
    supported_parameters: tuple[str, ...]
    source: str
    observed_at: str

    @property
    def free(self) -> bool:
        return self.input_price == 0.0 and self.output_price == 0.0

    def identity(self) -> str:
        """What the cache key folds in. The endpoint, not the observation."""
        return f"{self.provider_name}/{self.tag}/{self.quantization}"


#: One entry per (vendor, model) somebody actually inspected.
#:
#: `z-ai/glm-5.2:free` read from https://openrouter.ai/api/v1/models/
#: z-ai/glm-5.2:free/endpoints on 2026-09-04: exactly ONE endpoint, Decart at
#: fp4, prompt and completion priced at zero, 256,000 context, 230,400 maximum
#: completion, 99.53% uptime over the previous 30 minutes, status 0.
#:
#: The paid slug `z-ai/glm-5.2` has 34 endpoints and every one of them bills.
#: They are a different model id and cannot be reached from this one; the pin
#: and `allow_fallbacks: false` are belt and braces over that.
PINNED_ENDPOINTS: dict[tuple[str, str], PinnedEndpoint] = {
    ("openrouter", "z-ai/glm-5.2:free"): PinnedEndpoint(
        provider_name="Decart",
        tag="decart/fp4",
        quantization="fp4",
        input_price=0.0,
        output_price=0.0,
        context_length=256_000,
        max_completion_tokens=230_400,
        supported_parameters=(
            "frequency_penalty",
            "include_reasoning",
            "max_tokens",
            "min_p",
            "presence_penalty",
            "reasoning",
            "reasoning_effort",
            "repetition_penalty",
            "response_format",
            "seed",
            "stop",
            "structured_outputs",
            "temperature",
            "tool_choice",
            "tools",
            "top_k",
            "top_p",
        ),
        source="OpenRouter /models/z-ai/glm-5.2:free/endpoints",
        observed_at="2026-09-04",
    ),
}


def pinned_endpoint(vendor: str, model: str) -> PinnedEndpoint | None:
    """The recorded endpoint for this exact pair, or nothing.

    Deliberately does not fall back to the vendor, to the paid slug, or to a
    plausible default -- the whole point is that nobody guessed.
    """
    return PINNED_ENDPOINTS.get((vendor, model))


def routing_identity(vendor: str, model: str) -> str | None:
    """The endpoint component of a routed arm's cache identity, if it has one.

    `None` for a direct vendor, so not one existing key moves: a digest that
    appended an empty component would invalidate every answer this project has
    stored to record the absence of a router.
    """
    endpoint = pinned_endpoint(vendor, model)
    return None if endpoint is None else f"endpoint:{endpoint.identity()}"

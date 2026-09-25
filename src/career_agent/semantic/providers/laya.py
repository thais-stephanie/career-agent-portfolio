"""Laya, an optional local decision model. Detected, never required.

Laya answers typed questions (a choice, a yes or no, a score) with calibrated
probabilities in one forward pass. It does not return text spans, so it cannot
quote a posting, and the publication gate publishes no positive finding without
a verbatim quote. It therefore cannot produce semantic findings here, and this
adapter says so rather than pretending.

Whether it earns a place ELSEWHERE, as a local prefilter, is a measured
question recorded in docs/SEMANTIC_MATCHING.md. It is not wired into any
production path unless that measurement says it should be.
"""

from __future__ import annotations

import importlib.util

from career_agent.semantic.intent import SearchIntent
from career_agent.semantic.providers.base import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)

UNSUPPORTED_REASON = (
    "Laya returns typed decisions without quoting the posting, so it cannot "
    "publish evidence-backed semantic findings."
)


def installed() -> bool:
    try:
        return importlib.util.find_spec("laya") is not None
    except (ImportError, ValueError):
        return False


class LayaProvider:
    id = "laya"
    display_name = "Laya (local)"

    def availability(self) -> ProviderStatus:
        if not installed():
            return ProviderStatus(Availability.NOT_INSTALLED, "Laya is not installed.")
        return ProviderStatus(Availability.UNSUPPORTED, UNSUPPORTED_REASON)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.LOCAL,
            quotes=False,
            max_concurrency=1,
            sends="Nothing leaves this computer.",
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return 0.0

    def evaluate(self, intent: SearchIntent, title: str, posting: str) -> ProviderAnswer:
        raise ProviderFailed(UNSUPPORTED_REASON, state=Availability.UNSUPPORTED)

    def healthcheck(self) -> ProviderStatus:
        return self.availability()

"""Which provider a semantic run uses. A policy, not a brand preference.

AUTO, as measured (docs/SEMANTIC_MATCHING.md, 2026-09-25): on 45 hand-labelled
postings DeepSeek Flash, Claude Code and Codex agreed with the labels equally
often (39 of 45 each), made the same two arguable promotions and the same one
miss, and quoted the postings verbatim. DeepSeek did that in about 1.7 s for
under a tenth of a cent per posting; the subscription CLIs took 9 to 12 s and
spend the person's plan limits. Escalating DeepSeek's cases to a subscription
provider fixed nothing the benchmark could see, so Auto has no escalation:

    DeepSeek, when a key is configured
    else Claude Code, when signed in with a subscription
    else Codex, when signed in with ChatGPT
    else deterministic Search Fit, which always works.

Laya is never in the chain: it cannot quote, so it cannot publish evidence.

A provider the PERSON selected is respected. When it is unavailable the run
falls back to deterministic scoring, never to a different vendor: choosing
Claude Code must not quietly send postings to DeepSeek.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from career_agent.semantic.providers import ProviderStatus, SemanticProvider, get_provider
from career_agent.semantic.settings import Mode, SemanticSettings

AUTO_ORDER: tuple[str, ...] = ("deepseek", "claude_code", "codex")

#: Local availability checks start processes for the CLIs; a settings page
#: asking twice a second must not spawn two per provider each time.
_STATUS_TTL_SECONDS = 60.0
_status_cache: dict[str, tuple[float, ProviderStatus]] = {}


def provider_status(provider_id: str, *, fresh: bool = False) -> ProviderStatus:
    now = time.monotonic()
    hit = _status_cache.get(provider_id)
    if hit is not None and not fresh and now - hit[0] < _STATUS_TTL_SECONDS:
        return hit[1]
    status = get_provider(provider_id).availability()
    _status_cache[provider_id] = (now, status)
    return status


def forget_status(provider_id: str | None = None) -> None:
    if provider_id is None:
        _status_cache.clear()
    else:
        _status_cache.pop(provider_id, None)


@dataclass
class Route:
    provider: SemanticProvider | None
    #: What was wanted and why it was not used, in order.
    fallbacks: list[dict[str, str]] = field(default_factory=list)
    #: Why no provider is used, when none is.
    reason: str = ""


def resolve(settings: SemanticSettings) -> Route:
    if not settings.enabled:
        return Route(None, reason="Semantic matching is off.")
    if settings.mode is Mode.DETERMINISTIC:
        return Route(None, reason="Deterministic only was selected.")
    if settings.mode is not Mode.AUTO:
        chosen = settings.mode.value
        status = provider_status(chosen)
        if status.state.usable:
            return Route(get_provider(chosen))
        return Route(
            None,
            fallbacks=[{"preferred": chosen, "reason": status.detail, "used": "deterministic"}],
            reason=status.detail,
        )
    fallbacks: list[dict[str, str]] = []
    for candidate in AUTO_ORDER:
        status = provider_status(candidate)
        if status.state.usable:
            for row in fallbacks:
                row["used"] = candidate
            return Route(get_provider(candidate), fallbacks=fallbacks)
        fallbacks.append({"preferred": candidate, "reason": status.detail, "used": ""})
    for row in fallbacks:
        row["used"] = "deterministic"
    return Route(None, fallbacks=fallbacks, reason="No semantic provider is available.")

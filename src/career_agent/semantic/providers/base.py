"""One semantic-provider contract, whatever sits behind it.

A provider takes the normalized Search Intent and one posting and returns the
provider's TEXT, with what it cost. It never returns findings: turning text
into findings is `contract.parse_answer` followed by `gate.publish`, and that
path is identical for every provider. Nothing vendor-specific reaches scoring.

BILLING IS A PROPERTY, NOT A DETAIL
-----------------------------------
A metered API (DeepSeek) costs the person money per call, and Career Agent can
estimate and cap it. A subscription (Claude Code, Codex through ChatGPT) spends
the person's plan limits, whose marginal price Career Agent cannot know and
does not pretend to. A local model spends electricity and time. The UI and the
budget treat the three differently, so the provider says which it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from career_agent.semantic.intent import SearchIntent


class Billing(StrEnum):
    METERED_API = "METERED_API"
    SUBSCRIPTION = "SUBSCRIPTION"
    LOCAL = "LOCAL"
    NONE = "NONE"


class Availability(StrEnum):
    #: Ready to evaluate, as far as a local check can tell.
    AVAILABLE = "AVAILABLE"
    #: A live healthcheck reached the provider and it answered.
    CONNECTED = "CONNECTED"
    NOT_INSTALLED = "NOT_INSTALLED"
    SIGN_IN_REQUIRED = "SIGN_IN_REQUIRED"
    KEY_MISSING = "KEY_MISSING"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    #: Installed and signed in, but refusing: a plan limit, a quota, an outage.
    LIMIT_OR_ERROR = "LIMIT_OR_ERROR"
    #: Present, but cannot do this task on this machine or at all.
    UNSUPPORTED = "UNSUPPORTED"

    @property
    def usable(self) -> bool:
        return self in (Availability.AVAILABLE, Availability.CONNECTED)


@dataclass(frozen=True)
class ProviderStatus:
    state: Availability
    #: Plain words for the person. Never a credential, never a path to one.
    detail: str = ""
    #: Facts a settings panel may disclose on request (version, auth method).
    facts: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Capabilities:
    billing: Billing
    #: Whether this provider can return verbatim quotes, which the gate needs
    #: before any positive finding is published. A provider that cannot is
    #: never used for semantic findings, whatever else it is good at.
    quotes: bool
    #: Calls that may run at once without hurting the person's machine or plan.
    max_concurrency: int
    #: What leaves the computer, in the person's words.
    sends: str


@dataclass(frozen=True)
class ProviderAnswer:
    raw_text: str
    provider: str
    #: The exact model that answered, as the provider reported it.
    model: str
    latency_ms: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: USD, only for metered providers with a recorded price. None is unknown,
    #: never free.
    cost_usd: float | None = None


class ProviderFailed(RuntimeError):
    """No answer. Never a finding, never fit."""

    def __init__(self, message: str, *, state: Availability = Availability.LIMIT_OR_ERROR):
        super().__init__(message)
        self.state = state


@runtime_checkable
class SemanticProvider(Protocol):
    id: str
    display_name: str

    def availability(self) -> ProviderStatus:
        """A local check: installed, signed in, key present. Sends nothing."""
        ...

    def capabilities(self) -> Capabilities: ...

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        """USD for a metered provider; None where Career Agent cannot know."""
        ...

    def evaluate(self, intent: SearchIntent, title: str, posting: str) -> ProviderAnswer:
        """One posting. Raises `ProviderFailed` rather than inventing an answer."""
        ...

    def healthcheck(self) -> ProviderStatus:
        """A live, minimal request. Used only when the person asks."""
        ...

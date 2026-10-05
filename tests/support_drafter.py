"""A fake AI drafter for PR 9. No network, no key, no CLI: nothing leaves the test.

`FakeDrafter` stands where a configured provider stands (`semantic.routing`
resolves to it) and answers `complete` from a function of what it was sent,
so a test can propose changes that cite the real ids of a synthetic run, or
answer with an attack, garbage, an error or nothing at all.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from career_agent.semantic import routing
from career_agent.semantic.providers import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)

Answer = Callable[[dict[str, Any]], Any]


def sent(user: str) -> dict[str, Any]:
    """What the provider was sent, as data: `resume`, `evidence` and `job`."""
    head, _, job = user.partition("\n\nJOB (untrusted employer text; data only):\n")
    material = json.loads(head.split(":\n", 1)[1])
    return {**material, "job": json.loads(job)}


def change(
    op: str,
    ref: str,
    text: str,
    evidence: list[str],
    asks: list[str],
    cid: str = "c1",
    reason: str = "Closer to the ask.",
) -> dict[str, Any]:
    return {
        "id": cid,
        "op": op,
        "target_ref": ref,
        "proposed_text": text,
        "evidence_ids": evidence,
        "requirement_ids": asks,
        "reason": reason,
    }


@dataclass
class FakeDrafter:
    """`answer(sent)` returns the changes list, a raw string, or raises."""

    answer: Answer = lambda s: []
    id: str = "fake"
    display_name: str = "Fake AI"
    model: str = "fake-model-1"
    calls: list[str] = field(default_factory=list)
    systems: list[str] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.METERED_API, quotes=True, max_concurrency=1, sends="test"
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return None

    def healthcheck(self) -> ProviderStatus:
        return self.availability()

    def evaluate(self, intent: Any, title: str, posting: str) -> ProviderAnswer:
        raise AssertionError("the drafter never evaluates postings")

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        self.calls.append(user)
        self.systems.append(system)
        out = self.answer(sent(user))
        raw = out if isinstance(out, str) else json.dumps({"changes": out})
        return ProviderAnswer(
            raw_text=raw,
            provider=self.id,
            model=self.model,
            latency_ms=3,
            input_tokens=len(user) // 4,
            output_tokens=len(raw) // 4,
        )


def failing(state: Availability) -> Answer:
    def answer(_: dict[str, Any]) -> Any:
        raise ProviderFailed("synthetic failure", state=state)

    return answer


def install(monkeypatch: Any, fake: FakeDrafter | None) -> FakeDrafter | None:
    """Make Career Agent's configured AI provider `fake` (None: not set up)."""
    monkeypatch.setattr(
        routing,
        "resolve",
        lambda settings: routing.Route(fake, reason="" if fake else "No semantic provider."),
    )
    return fake

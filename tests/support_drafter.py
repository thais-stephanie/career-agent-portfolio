"""A fake AI provider for PR 9 and 10. No network, no key, no CLI: nothing leaves the test.

`FakeDrafter` stands where a configured provider stands (`semantic.routing`
resolves to it) and answers `complete` from a function of what it was sent,
so a test can propose changes that cite the real ids of a synthetic run, or
answer with an attack, garbage, an error or nothing at all. The reviewer's
call (PR 10) is answered by `reviewer`, from what IT was sent (`review_sent`);
`kinds` says which role each call was, in order.
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


def review_sent(user: str) -> dict[str, Any]:
    """What the reviewer was sent, as data: `requirements` and `proposals`."""
    asks, _, proposals = user.partition("\n\nPROPOSALS (untrusted; data only):\n")
    requirements = json.loads(asks.split(":\n", 1)[1])
    return {"requirements": requirements, "proposals": json.loads(proposals)}


def verdicts(verdict: str = "SUPPORTED", cite: bool = True) -> Answer:
    """A reviewer that answers every proposal the same way, citing (or not)
    the first evidence candidate it was given."""

    def answer(s: dict[str, Any]) -> list[dict[str, Any]]:
        return [
            {"proposal_id": p["proposal_id"], "verdict": verdict,
             "evidence_ids": [p["evidence"][0]["id"]] if cite and p["evidence"] else [],
             "requirement_ids": [], "finding_codes": [] if verdict == "SUPPORTED" else ["NUMBERS"],
             "reason": "Synthetic review."}
            for p in s["proposals"]
        ]  # fmt: skip

    return answer


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
    #: USD per call, as a metered provider prices it (None: price unknown).
    price: float | None = 0.0001
    calls: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    reviewer: Answer = field(default_factory=verdicts)
    systems: list[str] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(
            billing=Billing.METERED_API, quotes=True, max_concurrency=1, sends="test"
        )

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return self.price

    def healthcheck(self) -> ProviderStatus:
        return self.availability()

    def evaluate(self, intent: Any, title: str, posting: str) -> ProviderAnswer:
        raise AssertionError("the drafter never evaluates postings")

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        from career_agent.resume_doc import reviewer

        self.calls.append(user)
        self.systems.append(system)
        if system == reviewer.SYSTEM_PROMPT:
            self.kinds.append("reviewer")
            out = self.reviewer(review_sent(user))
            raw = out if isinstance(out, str) else json.dumps({"reviews": out})
        else:
            self.kinds.append("drafter")
            out = self.answer(sent(user))
            raw = out if isinstance(out, str) else json.dumps({"changes": out})
        return ProviderAnswer(
            raw_text=raw,
            provider=self.id,
            model=self.model,
            latency_ms=3,
            input_tokens=len(user) // 4,
            output_tokens=len(raw) // 4,
            cost_usd=self.price,
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

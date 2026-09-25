"""Published findings, in the one shape scoring consumes."""

from __future__ import annotations

from career_agent.domain.matching import SemanticEvidence, SemanticMatch
from career_agent.semantic.gate import Published
from career_agent.semantic.intent import COMPONENT_OF, SearchIntent


def to_evidence(
    published: Published,
    intent: SearchIntent,
    *,
    evaluation_id: str,
    provider: str,
    model: str,
    contract: str,
    requested_provider: str | None = None,
    fallback_reason: str | None = None,
) -> SemanticEvidence:
    matches = tuple(
        SemanticMatch(
            component_id=COMPONENT_OF[m.aspect],
            signal_id=m.signal_id,
            intent_id=m.intent_id,
            strength=m.strength.value,
            quote=m.quotes[0],
            sentence=m.sentence,
        )
        for m in published.matches
    )
    verdicts = tuple((COMPONENT_OF[a.aspect], a.verdict.value) for a in published.aspects)
    return SemanticEvidence(
        evaluation_id=evaluation_id,
        provider=provider,
        model=model,
        contract=contract,
        intent_digest=intent.digest,
        matches=matches,
        verdicts=verdicts,
        requested_provider=requested_provider,
        fallback_reason=fallback_reason,
    )

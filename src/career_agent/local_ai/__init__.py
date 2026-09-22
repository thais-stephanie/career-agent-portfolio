"""Local, on-machine inference. Deliberately NOT part of `career_agent.llm`.

`career_agent.llm` is the HOSTED inference stack: vendor adapters, a budget
ledger with a US$ 10,00 Personal Alpha ceiling, a pacing governor built around
per-account RPM/TPM quotas, an acceptance layer, and a cache whose identity
encodes which paid prompt bytes produced which answer. Every one of those
mechanisms exists because a hosted call costs money, is rate limited, leaves the
machine, and must be provable after the fact.

A call to a 4B model running on this laptop has none of those properties. It
costs nothing, is limited only by the GPU, never leaves loopback, and can be
re-run at will. Routing it through the hosted stack would mean either polluting
the ledger with rows that are structurally free -- destroying the one number
that says how much of the ceiling is left -- or teaching every hosted mechanism
a "local" special case. Both are worse than a second, much smaller package.

So the boundary is stated as a rule and enforced by a test:

    **This package imports nothing from `career_agent.llm`, and a local call
    never touches the hosted inference ledger, cache, pacing or routing.**

What it does share is the domain layer, and it shares exactly one thing:
`domain.verify.verify_quote`. Evidence is a quote that exists (ADR-0002), and
that rule cannot have two implementations -- a second normaliser would be a
second definition of proof.

What this package deliberately does not do:

* it does not produce a `JobFingerprint` -- 33 dimensions is a question for a
  frontier model, not for 4B of local weights;
* it does not produce a score, a percentage or a match. `recommended_action` is
  a reading suggestion. Scoring is deterministic Python elsewhere (ADR-0001);
* it does not read a credential. There is no credential; loopback is the only
  address it will speak to.
"""

from career_agent.local_ai.cache import (
    DEFAULT_CACHE_ROOT,
    LocalEnrichmentCache,
    local_cache_key,
    settings_digest,
    text_digest,
)
from career_agent.local_ai.contract import (
    LOCAL_ENRICHMENT_SCHEMA_VERSION,
    EvidencedItem,
    LocalEnrichment,
    RejectedItem,
    VerifiedEnrichment,
    json_schema,
    verify,
)
from career_agent.local_ai.ollama import (
    OllamaClient,
    OllamaInvalidOutput,
    OllamaRefused,
    OllamaSettings,
    OllamaUnavailable,
    assert_loopback,
)
from career_agent.local_ai.prompt import (
    JOB_DESCRIPTION_BEGIN,
    JOB_DESCRIPTION_END,
    LOCAL_PROMPT_VERSION,
    PROMPT_DIGEST,
    build_messages,
    neutralise_fences,
    verify_prompt_digest,
)

__all__ = [
    "DEFAULT_CACHE_ROOT",
    "JOB_DESCRIPTION_BEGIN",
    "JOB_DESCRIPTION_END",
    "LOCAL_ENRICHMENT_SCHEMA_VERSION",
    "LOCAL_PROMPT_VERSION",
    "PROMPT_DIGEST",
    "EvidencedItem",
    "LocalEnrichment",
    "LocalEnrichmentCache",
    "OllamaClient",
    "OllamaInvalidOutput",
    "OllamaRefused",
    "OllamaSettings",
    "OllamaUnavailable",
    "RejectedItem",
    "VerifiedEnrichment",
    "assert_loopback",
    "build_messages",
    "json_schema",
    "local_cache_key",
    "neutralise_fences",
    "settings_digest",
    "text_digest",
    "verify",
    "verify_prompt_digest",
]

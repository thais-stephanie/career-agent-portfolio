"""Row shapes for the collection tables.

These are plain frozen dataclasses rather than domain models, and that is a
deliberate boundary. A company or a job posting is *material the system
collects*; it is not something the domain layer reasons about. The domain works
on fingerprints, gates and preferences, and it stays free of anything that
knows what a database row looks like.

Identity, timestamps and status transitions are added by the repositories.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CompanyRecord:
    slug: str
    name: str
    website: str | None = None
    hq_country: str | None = None  # target-market signal only, never eligibility
    size_estimate: str | None = None
    stage: str | None = None
    industry: str | None = None
    notes: str | None = None
    #: M1D identity anchor: normalised, unique where present. A slug is a label
    #: a human chose; this is the thing two entries can be compared on.
    canonical_domain: str | None = None
    #: Why this company is monitored at all, and why it was prioritised. Signal
    #: names only -- never the candidate's profile data (privacy boundary).
    discovery_source: str | None = None
    priority_reason: str | None = None


@dataclass(frozen=True)
class SourceBoardRecord:
    company_id: str
    provider: str
    board_identifier: str
    board_url: str | None = None
    active: bool = True
    #: How this identifier was arrived at. A guessed slug that happens to answer
    #: is not the same evidence as one a careers page pointed at.
    discovery_method: str | None = None
    verified_at: str | None = None


@dataclass(frozen=True)
class JobRecord:
    company_id: str
    source_board_id: str
    provider: str
    external_id: str
    url: str
    title: str
    department: str | None = None
    location_raw: str | None = None
    posted_at: str | None = None
    content_hash: str | None = None


@dataclass(frozen=True)
class ProviderPayloadRecord:
    job_id: str
    provider: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LLMCallRecord:
    """One attempt at one extraction family, as it will be stored.

    A record rather than a repository signature with twenty parameters, and a
    storage type rather than the pipeline's own `FamilyAttempt`: the repository
    must not import from `pipeline/`, or the dependency runs backwards and
    storage starts to know how extraction is orchestrated.

    `raw_output` is whatever came back, byte for byte, including output that
    failed to parse. The row is written either way -- an attempt that failed is
    the most useful row in the table when a prompt regresses, and it is exactly
    the row a system that only stored successes would not have.
    """

    job_id: str
    cache_key: str
    #: Which RUN made this physical request. `cache_key` says which question was
    #: asked; this says who asked it and when, and the two together are what
    #: make an attempt row an event rather than a slot. NULL only on rows
    #: written before executions were identified.
    execution_id: str | None
    family: str
    provider: str
    model: str
    #: A `career_agent.llm.client.Runner` value, as a string. Recorded so a
    #: cost report can separate populations that must never be added together:
    #: a billable vendor call and a free one are not the same event. The
    #: members live on the enum, not here -- storage records the label, it does
    #: not know what any particular runner means.
    runner: str
    prompt_version: str
    schema_version: int
    #: A `career_agent.llm.client.StructuredOutput` value, as a string. What the
    #: vendor was asked to guarantee for THIS family -- the same arm can enforce
    #: one family's schema and not the other's, so it cannot be read off the
    #: model name. Storage records the label; it does not know what one means.
    structured_output: str
    raw_output: str
    #: The vendor envelope as JSON, or None when there was no response at all.
    response_envelope: str | None
    #: A `career_agent.llm.failures.Transport` value. How far this attempt got.
    transport: str
    parsed_ok: bool
    validated_ok: bool
    #: A `career_agent.llm.acceptance.CacheDisposition` value, as a string.
    #: Whether this answer may be SERVED again, which is a different question
    #: from whether it validated -- the row that proved they differ was
    #: schema-perfect and cited nothing. Defaults to UNVERIFIED so an unjudged
    #: answer is never a hit.
    cache_disposition: str = "UNVERIFIED"
    attempt: int = 1
    purpose: str = "fingerprint"
    reasoning_config: str | None = None
    error: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    #: None means "not yet priced", which is the honest state of a production
    #: call here. A free runner records 0.0, because costing nothing is a fact.
    cost_usd: float | None = None


@dataclass(frozen=True)
class CachedAnswer:
    """A stored answer that may be served again instead of being re-asked.

    Only ever built from a row whose `validated_ok` is true. A recorded failure
    is the most useful row in the table when a prompt regresses, and the least
    useful thing to replay: serving it back would spend a retry reproducing a
    failure we already have on disk.
    """

    cache_key: str
    raw_output: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None

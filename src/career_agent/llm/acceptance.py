"""Whether one stored answer may be served again as a successful one.

WHY THIS IS NOT `validated_ok`
------------------------------
`validated_ok` means the transport document parsed and matched its schema. That
is a real and useful fact, and it was doing a job it was never asked to do: the
semantic cache read it as "this answer was accepted", and served it.

The two came apart the moment a model produced a perfectly shaped document whose
evidence cited nothing. Every field was the right type, every dimension was
present exactly once, and all eighteen evidence entries carried a real sentence
from the posting in the wrong field, with `quote` null and no observation
referencing any of them. The row was schema-valid, unusable, and a cache hit.

So admission gets its own question, its own answer, and its own column.

WHY IT IS STRICTER THAN `ExtractionOutcome.ok`
----------------------------------------------
An extraction whose citations do not resolve is not a failure -- the claim stays
visible and marked, and "the model cited a sentence that does not exist" is a
measurement the evaluation is built to make. That is right for a run.

It is wrong for a cache. A cached answer is one a later run will treat as
finished work, so admitting a partially-verified one freezes an unresolved
citation into every future run and every metric computed from them, with no
request made and nothing to notice. The corpus-level target of >= 0.95 is a
statement about a population of answers; it is not a licence for any single
stored answer to carry a citation that does not exist.

Measured before it was chosen: of 150 schema-valid rows, 125 are admitted, 20
are refused for unsupported EXPLICIT observations and 5 for unresolved evidence.
Only one row separates "every entry verifies" from ">= 0.95", so the strict
reading costs one row and removes an entire class of silent error.

WHAT IS NOT DECIDED HERE
------------------------
Nothing is repaired, copied, inferred or normalised. A refused answer keeps its
row, its bytes and its envelope; it simply stops being an answer anything will
be served. `REJECTED_*` is a durable finding about a response, not a deletion of
one.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from career_agent.domain.verify import verify_all


class CacheDisposition(StrEnum):
    """Whether this stored answer may be served as a successful result.

    Recorded per attempt, alongside `parsed_ok` and `validated_ok` rather than
    instead of them: "it parsed", "it matched the schema" and "it would be
    accepted again" are three different facts, and a row that collapses them
    cannot answer why an answer was refused.
    """

    #: Every family-level acceptance gate passed. The only value the cache reads.
    ACCEPTED = "ACCEPTED"
    #: Schema-valid, and at least one evidence entry does not resolve against
    #: the artefact it names.
    REJECTED_EVIDENCE = "REJECTED_EVIDENCE"
    #: Schema-valid, and at least one EXPLICIT observation cites nothing, or
    #: cites an evidence id the response did not return.
    REJECTED_UNSUPPORTED = "REJECTED_UNSUPPORTED"
    #: The response never got as far as being judged: no transport, no parse,
    #: no schema match, or a contract violation.
    REJECTED_OUTPUT = "REJECTED_OUTPUT"
    #: Nobody has judged this row.
    #:
    #: The value every historical row carries until it is reverified, and the
    #: value any row keeps when its source material is no longer available. It
    #: is NOT a hit: an answer nobody has checked is exactly the answer that
    #: should not be served, and defaulting the other way is how the defect this
    #: module exists for got in.
    UNVERIFIED = "UNVERIFIED"


class _HasStatus(Protocol):
    status: Any
    evidence_id: str | None


class _Family(Protocol):
    observations: Any
    evidence: Any


def disposition_for(
    payload: _Family, description_text: str, provider_payload: Any
) -> tuple[CacheDisposition, str | None]:
    """Judge one family answer against every gate a cache hit must clear.

    Returns the disposition and, when refused, a short reason for the row. The
    order is deliberate: an answer that cites nothing is refused for citing
    nothing, not for failing a verification it never invited.

    Both families are judged the same way, and that is not an oversight. Their
    PROVENANCE rules differ -- a description citation resolves against the
    archived posting text, a provider citation against the archived payload --
    and `verify_all` already routes each entry by its own `source_kind`. What
    does not differ is the rule that an EXPLICIT claim names evidence that
    exists. All 56 stored provider answers already satisfy it.
    """
    observations = list(payload.observations)
    evidence = list(payload.evidence)
    known = {entry.id for entry in evidence}

    uncited = [
        observation.dimension
        for observation in observations
        if str(observation.status) == "EXPLICIT" and not observation.evidence_id
    ]
    if uncited:
        return (
            CacheDisposition.REJECTED_UNSUPPORTED,
            f"{len(uncited)} EXPLICIT observation(s) cite no evidence: {', '.join(uncited[:4])}",
        )

    dangling = [
        observation.evidence_id
        for observation in observations
        if observation.evidence_id and observation.evidence_id not in known
    ]
    if dangling:
        return (
            CacheDisposition.REJECTED_UNSUPPORTED,
            f"{len(dangling)} citation(s) name no returned evidence: {', '.join(dangling[:4])}",
        )

    report = verify_all(evidence, description_text, provider_payload)
    if report.rate < 1.0:
        failed = [result.evidence_id for result in report.failed]
        return (
            CacheDisposition.REJECTED_EVIDENCE,
            f"{len(failed)} of {len(evidence)} evidence entries did not resolve: "
            f"{', '.join(failed[:4])}",
        )

    return CacheDisposition.ACCEPTED, None

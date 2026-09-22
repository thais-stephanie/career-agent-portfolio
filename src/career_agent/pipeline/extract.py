"""One posting in, one fingerprint out -- through whichever runner is supplied.

This is the only extraction path. A vendor adapter, the replay client and the
Cowork harness all reach the same code here, because they are all the same
thing: something that turns an `LLMRequest` into an `LLMResponse`. Nothing in
this module can tell them apart, and nothing in it is allowed to try.

That is the property the Cowork harness rests on. If extraction had a second
route for development output, the two would drift and the development results
would stop being evidence about the production path. There is one route.

THE ORDER OF OPERATIONS, AND WHY IT IS THIS ORDER
-------------------------------------------------
    1  ask          per family, independently, with bounded retries
    2  parse        JSON text -> object, still untrusted
    3  transport    validate against the family schema, still untrusted
    4  assemble     code merges the families; the model never does
    5  verify       every quote must exist in the source; every field must resolve
    6  validate     cross-field plausibility
    7  remediate    apply what the validator decided, towards uncertainty

Verification runs *after* assembly because assembly is what namespaces evidence
ids, and *before* validation because a rule reading an unverifiable quote is
reasoning about a sentence that may not exist.

RETRY IS PER FAMILY
-------------------
A description failure never re-runs the provider call and vice versa. Under a
single-call topology one bad field costs the whole extraction twice; here it
costs one family. This is the operational half of the reason the topology was
split, and it is the reason `llm_call.family` exists.
"""

import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from career_agent.domain.fingerprint import FingerprintMeta, JobFingerprint
from career_agent.domain.remediate import apply_corrections
from career_agent.domain.validate import ValidationOutcome, validate_fingerprint
from career_agent.domain.verify import VerificationReport, verify_all
from career_agent.llm.acceptance import CacheDisposition, disposition_for
from career_agent.llm.assemble import (
    AssemblyError,
    AssemblyReport,
    assemble,
    index_observations,
)
from career_agent.llm.cache import field_map_version
from career_agent.llm.client import (
    Family,
    LLMClient,
    LLMError,
    LLMErrorResponse,
    LLMNotSent,
    LLMResponse,
    ModelConfig,
    Runner,
)
from career_agent.llm.failures import (
    RunHalted,
    Transport,
    is_arm_wide,
    transport_from_error,
)
from career_agent.llm.requests import (
    BuiltRequest,
    build_description_request,
    build_provider_request,
)
from career_agent.llm.transport import (
    ALL_DIMENSIONS,
    TRANSPORT_SCHEMA_VERSION,
    TDescriptionFamily,
    TProviderFamily,
)
from career_agent.providers.base import resolve_metadata
from career_agent.providers.registry import field_map_for
from career_agent.storage.fingerprint_repo import FingerprintRepo, LLMCallRepo
from career_agent.storage.records import LLMCallRecord

#: One family's transport payload, whichever family it is. The orchestration
#: below is identical for both, and saying so in the type is what stops a
#: future third family from arriving with its own copy of the retry loop.
TFamily = TypeVar("TFamily", bound=BaseModel)

#: How many times one family may be asked before the extraction gives up on it.
#:
#: Two, not five. A model that returned unparseable output twice against a
#: schema it was given is not going to succeed on the fifth attempt, and each
#: attempt is a real charge under a production runner. Failures are visible and
#: countable rather than retried into an invoice.
MAX_ATTEMPTS = 2


@dataclass(frozen=True)
class JobSource:
    """Everything one extraction is allowed to see.

    Deliberately candidate-free. There is no profile here, no residence, no
    compensation requirement and no career intent -- not because they are
    unavailable, but because a fingerprint that saw them would be an
    observation about a candidate rather than about a posting, and could never
    be reused for anyone else or compared against a golden label.
    """

    job_id: str
    content_hash: str
    description_text: str
    provider: str
    payload: dict[str, Any] = field(default_factory=dict)
    payload_hash: str | None = None
    observations: tuple[Any, ...] = ()
    field_map_digest: str = ""


@dataclass(frozen=True)
class FamilyAttempt:
    """One request, one answer, and what happened to it -- recorded either way.

    Written whether or not the answer was usable. An attempt that failed to
    parse is the most valuable row in the table when a prompt regresses, and it
    is precisely the row a system that only stored successes would not have.
    """

    family: Family
    cache_key: str
    attempt: int
    raw_output: str
    parsed_ok: bool
    validated_ok: bool
    model: str
    vendor: str
    runner: Runner
    prompt_version: str
    transport_version: int
    #: What the vendor was asked to guarantee for this family, as a string.
    structured_output: str
    #: Whether this answer may be SERVED again. Judged at the moment of
    #: persistence, because that is the only moment where the answer, the
    #: archived source material and the write are all in the same place -- and
    #: the write must not wait for a judgement made later.
    cache_disposition: str = CacheDisposition.UNVERIFIED.value
    error: str | None = None
    #: The vendor's response structure as JSON, captured before parsing. None
    #: when no response arrived at all.
    response_envelope: str | None = None
    #: How far this attempt got: NOT_SENT / SENT_NO_RESPONSE / RESPONSE_RECEIVED.
    transport: str = Transport.SENT_NO_RESPONSE.value
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass
class ExtractionOutcome:
    """What one job's extraction produced, including its own failure modes."""

    job_id: str
    attempts: list[FamilyAttempt] = field(default_factory=list)
    fingerprint: JobFingerprint | None = None
    verification: VerificationReport = field(default_factory=lambda: VerificationReport(()))
    assembly: AssemblyReport | None = None
    validation: ValidationOutcome | None = None
    failure: str | None = None

    #: True when this posting failed for a reason that has nothing to do with
    #: this posting: no credit, no valid credential, no permission. The caller
    #: is expected to stop rather than reproduce it once per remaining job.
    arm_unusable: bool = False

    #: Set when a ceiling ended the run mid-posting. The caller stores this
    #: outcome -- its earlier attempts are real and were paid for -- and then
    #: stops.
    halted: str | None = None
    #: Families whose answer was usable here and refused admission to the cache,
    #: with the reason. Reported rather than raised: the extraction is not
    #: wrong, and the operator needs to know a later run will re-ask.
    refused_cache: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.fingerprint is not None and self.failure is None

    @property
    def paid_calls(self) -> int:
        """Attempts that cost money. Zero under replay and under Cowork.

        Counted from the runner rather than from the attempt count, because the
        whole point of the three-bucket cost report is that "we ran 150
        extractions" and "we paid for 150 extractions" are different sentences.
        """
        return sum(1 for a in self.attempts if a.runner is Runner.PRODUCTION_API)

    @property
    def unverified_evidence(self) -> tuple[str, ...]:
        """Citations that could not be resolved against the source they name.

        Kept rather than removed. The claim they support stays visible and
        marked, and "the model cited a sentence that does not exist" is exactly
        the measurement the evaluation plan is built around.
        """
        return tuple(r.evidence_id for r in self.verification.failed)


# =========================================================================
# ONE FAMILY
# =========================================================================


def _ask(
    client: LLMClient,
    config: ModelConfig,
    built: BuiltRequest,
    model_cls: type[TFamily],
    outcome: ExtractionOutcome,
    max_attempts: int,
    check: Callable[[TFamily], None] | None = None,
    on_attempt: Callable[[FamilyAttempt], None] | None = None,
    judge: Callable[[TFamily], tuple[CacheDisposition, str | None]] | None = None,
) -> TFamily | None:
    """Ask one family until it answers usably, or until the budget is spent.

    Every attempt is appended to `outcome.attempts` before it is judged, which
    is the storage ordering the architecture requires: the raw text is a record
    of what the model said, and a record written only when the answer was good
    is a record of our opinion instead.

    `check` is a family-specific test that the vendor schema cannot express.
    Completeness is the one that matters: every transport field has a default,
    so `{"observed_title": "..."}` is a *valid* payload that answered nothing.
    Running that check here rather than at assembly is what makes a forgotten
    dimension a retry instead of a crash -- the model omitted something it was
    asked for, which is exactly the case a second attempt exists for.
    """

    def observe(
        attempt: int,
        raw: str,
        parsed_ok: bool,
        validated_ok: bool,
        error: str | None,
        response: LLMResponse | None = None,
        transport: Transport = Transport.SENT_NO_RESPONSE,
        envelope: dict[str, Any] | None = None,
        disposition: str = CacheDisposition.UNVERIFIED.value,
    ) -> None:
        """Record one attempt, and persist it NOW rather than at end of posting.

        THE INVARIANT THIS FUNCTION EXISTS FOR
        --------------------------------------
        If diagnostic information exists for an attempt, it is durable before
        control leaves this frame.

        Four separate rounds lost evidence and every one had the same shape: an
        attempt was observed, held in memory, and discarded by whatever ended
        the run before the end-of-posting write -- a conflicting insert, an
        arm-wide refusal, a budget stop, and an interrupt. Patching each branch
        produced four fixes and a fifth hole.

        There is one place an attempt is observed, and saving happens there. No
        later exception -- from a retry, a ceiling, an interrupt, or the
        persistence of something else -- can reach backwards and unmake it.
        """
        row = _attempt_row(
            built,
            client,
            config,
            attempt,
            raw,
            parsed_ok,
            validated_ok,
            error,
            response,
            transport,
            envelope,
            disposition,
        )
        outcome.attempts.append(row)
        if on_attempt is not None:
            on_attempt(row)

    last_error: str | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            response = client.complete(built.request, config)
        except LLMNotSent as exc:
            # Never reached the wire. Always our defect, never the vendor's, and
            # it will fail identically on the next posting.
            observe(attempt, "", False, False, str(exc), transport=Transport.NOT_SENT)
            outcome.arm_unusable = True
            outcome.failure = f"{built.request.family}: {exc}"
            return None
        except RunHalted as exc:
            # The run is over -- budget spent, money spent, or the arm unusable.
            # It is NOT a failed attempt: nothing was asked, so there is nothing
            # to record about this call. What matters is that the outcome is
            # RETURNED rather than thrown, so the caller can store the attempts
            # already made before it stops.
            #
            # These used to propagate out of `extract_job` uncaught, and took
            # the outcome with them. The OpenRouter canary lost its entire
            # diagnosis that way: one real request, one real failure, and no
            # record of either.
            outcome.halted = str(exc)
            outcome.failure = outcome.failure or f"{built.request.family}: {exc}"
            return None
        except LLMError as exc:
            # AN HTTP RESPONSE IS NOT A MODEL ANSWER, AND NOT ITS ABSENCE EITHER
            # -------------------------------------------------------------
            # Every failure here used to be recorded as SENT_NO_RESPONSE,
            # because no completion had been produced. A 429 from a named
            # provider, carrying its own error code and a Retry-After, is a
            # response by any reading of HTTP -- and storing it as one nobody
            # heard back from threw away the evidence that the request had
            # reached the far end at all.
            #
            # An adapter that carried the response through structurally says so
            # by type; one that flattened it into a string is read for the SDK's
            # own status marker, and left alone when it proves nothing.
            observe(
                attempt,
                "",
                False,
                False,
                str(exc),
                transport=(
                    Transport.RESPONSE_RECEIVED
                    if isinstance(exc, LLMErrorResponse)
                    else transport_from_error(str(exc))
                ),
                envelope=getattr(exc, "envelope", None),
            )
            last_error = str(exc)
            if is_arm_wide(str(exc)):
                # Recorded, then abandoned. Neither cause improves with
                # repetition: an account that could not make the first request
                # cannot make the second, and a request our own code could not
                # construct will not construct on the next posting either. The
                # GLM screen repeated one `TypeError` nine times.
                outcome.arm_unusable = True
                outcome.failure = f"{built.request.family}: {last_error}"
                return None
            continue

        parsed_ok, validated_ok, payload, error = _interpret(response, model_cls, check)
        # Judged BEFORE the row is written, so an answer is never durable
        # without the verdict that decides whether it may be served again. The
        # judgement never blocks the write and never changes the raw text: a
        # refusal is a finding recorded beside the answer, not an edit to it.
        disposition = CacheDisposition.REJECTED_OUTPUT
        reason: str | None = error
        if payload is not None:
            disposition, reason = (
                judge(payload) if judge is not None else (CacheDisposition.UNVERIFIED, None)
            )
        observe(
            attempt,
            response.raw_text,
            parsed_ok,
            validated_ok,
            error,
            response,
            Transport.RESPONSE_RECEIVED,
            disposition=disposition.value,
        )
        if payload is not None and disposition is not CacheDisposition.ACCEPTED:
            # Usable for THIS run, and never served to another. The distinction
            # the cache was missing: an answer can be complete enough to
            # assemble from and still be one nobody should be handed later.
            outcome.refused_cache.append(f"{built.request.family}: {reason}")
        if payload is not None:
            return payload
        last_error = error

    outcome.failure = f"{built.request.family}: {last_error}"
    return None


def _interpret(
    response: LLMResponse,
    model_cls: type[TFamily],
    check: Callable[[TFamily], None] | None = None,
) -> tuple[bool, bool, TFamily | None, str | None]:
    """JSON text -> validated transport object, reporting where it broke.

    Parsing and schema validation are separated because they fail for different
    reasons and want different fixes: unparseable output is usually a wrapper
    problem (prose around the JSON, a truncated response), while a parse that
    fails validation is a prompt problem. Collapsing them into one boolean
    would hide which of the two is happening across a benchmark run.

    A refusal is treated as a failed attempt rather than an empty extraction.
    A model declining to answer has not told us the posting said nothing.
    """
    if response.refusal:
        return False, False, None, f"model refused: {response.refusal}"
    if response.contract_error:
        # The answer did not arrive through the channel the request established.
        # Reported before parsing, and separately from it: "the model wrote
        # prose instead of calling the function" and "the model called the
        # function and its arguments were the wrong shape" are different
        # findings about a route, and a run that collapsed them into "bad JSON"
        # could not tell a transport that does not work from a model that
        # cannot fill it in.
        return False, False, None, f"function-call contract: {response.contract_error}"
    try:
        raw = json.loads(response.raw_text)
    except (json.JSONDecodeError, TypeError) as exc:
        return False, False, None, f"output is not JSON: {exc}"
    try:
        payload = model_cls.model_validate(raw)
    except Exception as exc:  # pydantic ValidationError, and anything it wraps
        return True, False, None, f"output does not match the {model_cls.__name__} schema: {exc}"

    if check is not None:
        try:
            check(payload)
        except (AssemblyError, ValueError) as exc:
            return True, False, None, str(exc)
    return True, True, payload, None


def _attempt_row(
    built: BuiltRequest,
    client: LLMClient,
    config: ModelConfig,
    attempt: int,
    raw_output: str,
    parsed_ok: bool,
    validated_ok: bool,
    error: str | None,
    response: LLMResponse | None = None,
    transport: Transport = Transport.SENT_NO_RESPONSE,
    error_envelope: dict[str, Any] | None = None,
    cache_disposition: str = CacheDisposition.UNVERIFIED.value,
) -> FamilyAttempt:
    return FamilyAttempt(
        family=built.request.family,
        cache_key=built.cache_key.key,
        structured_output=config.output_mode(built.request.family).value,
        attempt=attempt,
        raw_output=raw_output,
        parsed_ok=parsed_ok,
        validated_ok=validated_ok,
        cache_disposition=cache_disposition,
        model=config.arm,
        vendor=client.vendor,
        runner=client.runner,
        prompt_version=built.prompt_version,
        transport_version=built.transport_version,
        error=error,
        input_tokens=response.input_tokens if response else None,
        output_tokens=response.output_tokens if response else None,
        # Serialised here rather than in the adapter so that every vendor's
        # envelope reaches storage the same way, and so a vendor that returns
        # something unserialisable degrades to a note instead of killing a run
        # whose answer we already have.
        response_envelope=_envelope_json(response, error_envelope),
        transport=transport.value,
    )


def _envelope_json(
    response: LLMResponse | None,
    error_envelope: dict[str, Any] | None = None,
) -> str | None:
    """The vendor's own response, as JSON, or an honest note about why not.

    Two envelopes reach this column and both are response evidence: a
    ChatCompletion body that produced an answer, and an HTTP error body that
    explained why there is none. Requiring the first before the column could be
    written is what left the 429 with nothing but a sentence.

    Credential-free by construction: an envelope is built from a response body,
    never from a request, its headers or a client. Nothing here has ever seen a
    key.
    """
    envelope = response.envelope if response is not None else None
    if envelope is None:
        envelope = error_envelope
    if envelope is None:
        return None
    try:
        return json.dumps(envelope, default=str)
    except (TypeError, ValueError) as exc:  # pragma: no cover - defensive
        return json.dumps({"unserialisable": str(exc)})


def _description_is_complete(payload: TDescriptionFamily) -> None:
    """Every dimension answered, even when the answer is 'the posting is silent'.

    The compact transport solved a vendor schema limit by turning dimensions
    into rows, and gave up one thing in exchange: a forgotten dimension no
    longer looks different from a stated one. `index_observations` is the check
    that buys it back, and running it here makes an incomplete answer a retry
    rather than a failure discovered halfway through assembly.
    """
    index_observations(payload.observations, ALL_DIMENSIONS, "description")


# =========================================================================
# THE WHOLE JOB
# =========================================================================


def extract_job(
    client: LLMClient,
    config: ModelConfig,
    source: JobSource,
    max_attempts: int = MAX_ATTEMPTS,
    on_attempt: Callable[[FamilyAttempt], None] | None = None,
) -> ExtractionOutcome:
    """Run both families, assemble, verify, validate and remediate one posting.

    The provider family is skipped entirely when nothing in the ATS record
    needs interpreting, and skipping is not a degraded outcome: an empty
    `TProviderFamily` is the honest representation of "the payload said nothing
    a model was needed for", and the deterministic values it carries were never
    the model's to produce.
    """
    outcome = ExtractionOutcome(job_id=source.job_id)

    description_request = build_description_request(
        source.content_hash, source.description_text, config
    )

    def judge(payload: Any) -> tuple[CacheDisposition, Any]:
        """Would this answer be accepted if it arrived again tomorrow?

        Closed over `source`, because that is what an evidence citation is
        checked against and the only place both are in scope. Both families use
        it; `verify_all` routes each entry to the artefact its own `source_kind`
        names, which is where their provenance rules differ.
        """
        return disposition_for(payload, source.description_text, source.payload)

    description = _ask(
        client,
        config,
        description_request,
        TDescriptionFamily,
        outcome,
        max_attempts,
        check=_description_is_complete,
        on_attempt=on_attempt,
        judge=judge,
    )
    if description is None:
        return outcome

    provider_request = build_provider_request(
        source.provider, source.observations, source.field_map_digest, config
    )
    provider = TProviderFamily()
    prompt_version = description_request.prompt_version
    if provider_request is not None:
        answered = _ask(
            client,
            config,
            provider_request,
            TProviderFamily,
            outcome,
            max_attempts,
            on_attempt=on_attempt,
            judge=judge,
        )
        if answered is None:
            return outcome
        provider = answered
        prompt_version = f"{description_request.prompt_version}+{provider_request.prompt_version}"

    meta = FingerprintMeta(
        prompt_version=prompt_version,
        transport_version=TRANSPORT_SCHEMA_VERSION,
        model=config.arm,
        content_hash=source.content_hash,
        payload_hash=source.payload_hash,
        truncated=description_request.truncated,
    )

    # Assembly can still refuse a payload that passed both the schema and the
    # completeness check -- evidence citing the wrong channel, a hiring scope
    # that will not parse, a provider claim with no citation. Those are model
    # errors rather than crashes, and one bad posting must not end a batch that
    # under a paid runner would then be re-paid for from the start.
    try:
        fingerprint, report = assemble(description, provider, meta, source_provider=source.provider)
    except (AssemblyError, ValueError) as exc:
        outcome.failure = f"assembly: {exc}"
        return outcome
    outcome.assembly = report

    # Each channel is checked against its own artefact and only its own: a
    # quote against the archived description text, a provider field against the
    # archived payload. A description citation can never be rescued by a
    # payload that happens to contain the same string, which is the one place
    # the two provenance channels could quietly merge.
    outcome.verification = verify_all(
        list(fingerprint.evidence), source.description_text, source.payload
    )

    validation = validate_fingerprint(fingerprint, source.description_text)
    outcome.validation = validation
    if not validation.ok:
        outcome.failure = "; ".join(validation.fatal)
        return outcome

    outcome.fingerprint = apply_corrections(fingerprint, validation.corrections)
    return outcome


def extract_many(
    client: LLMClient,
    config: ModelConfig,
    sources: Sequence[JobSource],
    max_attempts: int = MAX_ATTEMPTS,
) -> list[ExtractionOutcome]:
    """Extract a batch, and never let one posting's failure end the run.

    A malformed posting in position 12 must not cost the other 149 extractions,
    which under a paid runner would mean paying twice for work that already
    succeeded.
    """
    return [extract_job(client, config, source, max_attempts) for source in sources]


# =========================================================================
# READING A JOB OUT OF THE DATABASE, AND WRITING THE RESULT BACK
#
# These two functions are the only place extraction touches storage. Keeping
# them here rather than in the repositories is what lets `extract_job` stay a
# pure function of (client, config, source) -- which is why it can be tested
# offline, replayed deterministically, and driven by the Cowork harness without
# a database existing at all.
# =========================================================================


def load_source(conn: sqlite3.Connection, job_id: str) -> JobSource:
    """Assemble one job's extraction input from what collection archived.

    The provider's field map is resolved against the archived payload here, so
    the observations the model sees are produced by exactly the code that
    produced them at collection time, and the evidence they carry resolves
    against exactly the payload stored beside them.

    Raises when the job has no description text. That is a collection failure,
    not an extraction result, and letting it through would produce a
    fingerprint asserting that a posting we never read said nothing.
    """
    row = conn.execute(
        "SELECT j.id, j.provider, j.content_hash, r.description_text"
        " FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    if row is None:
        raise LookupError(f"job {job_id!r} has no archived description text")

    provider = str(row["provider"])
    field_map = field_map_for(provider)
    payload_row = conn.execute(
        "SELECT payload_json, payload_hash FROM job_provider_payload WHERE job_id = ?"
        " ORDER BY captured_at DESC, id DESC LIMIT 1",
        (job_id,),
    ).fetchone()

    payload: dict[str, Any] = {}
    payload_hash: str | None = None
    if payload_row is not None:
        payload = json.loads(payload_row["payload_json"])
        payload_hash = str(payload_row["payload_hash"])

    return JobSource(
        job_id=job_id,
        content_hash=str(row["content_hash"]),
        description_text=str(row["description_text"]),
        provider=provider,
        payload=payload,
        payload_hash=payload_hash,
        observations=resolve_metadata(provider, field_map, payload),
        field_map_digest=field_map_version(
            provider, field_map.paths(), tuple(m.dimension.value for m in field_map.mappings)
        ),
    )


def store_outcome(
    conn: sqlite3.Connection,
    outcome: ExtractionOutcome,
    execution_id: str | None = None,
    attempts_already_stored: bool = False,
) -> str | None:
    """Persist one extraction, successful or not. Returns the fingerprint id.

    A failed extraction still writes its `llm_call` rows. That is the whole
    reason the two repositories are separate: the record of what a model said
    has to survive the failure of what it said to be usable.

    `execution_id` names the run that made these physical requests. Without it,
    two runs asking the same question at the same retry ordinal collide -- which
    is how 18 real Cerebras attempts were silently discarded.

    `attempts_already_stored` is how a caller says it persisted each attempt as
    it happened, which is what the live runner does: writing them again would be
    a duplicate physical event, and the ledger now refuses those loudly rather
    than swallowing them.
    """
    fingerprint_id: str | None = None
    if outcome.fingerprint is not None:
        fingerprint_id = FingerprintRepo(conn).store(
            outcome.job_id, outcome.fingerprint, outcome.verification
        )

    calls = LLMCallRepo(conn)
    if attempts_already_stored:
        return fingerprint_id

    for attempt in outcome.attempts:
        calls.record(_as_record(attempt, outcome.job_id, execution_id), fingerprint_id)
    return fingerprint_id


def _as_record(
    attempt: FamilyAttempt, job_id: str, execution_id: str | None = None
) -> LLMCallRecord:
    return LLMCallRecord(
        job_id=job_id,
        cache_key=attempt.cache_key,
        execution_id=execution_id,
        family=attempt.family.value,
        provider=attempt.vendor,
        model=attempt.model,
        runner=attempt.runner.value,
        prompt_version=attempt.prompt_version,
        schema_version=attempt.transport_version,
        structured_output=attempt.structured_output,
        raw_output=attempt.raw_output,
        response_envelope=attempt.response_envelope,
        transport=attempt.transport,
        parsed_ok=attempt.parsed_ok,
        validated_ok=attempt.validated_ok,
        cache_disposition=attempt.cache_disposition,
        attempt=attempt.attempt,
        error=attempt.error,
        input_tokens=attempt.input_tokens,
        output_tokens=attempt.output_tokens,
        # Zero, not NULL, for a free runner: costing nothing is a fact about
        # the call. A production call stays NULL until the cost model prices
        # it, because "not yet priced" and "free" are different statements.
        cost_usd=None if attempt.runner is Runner.PRODUCTION_API else 0.0,
    )

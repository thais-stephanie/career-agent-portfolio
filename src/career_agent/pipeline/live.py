"""The live runner: the first code in this repository that can spend money.

It is deliberately thin, and the list of what it does *not* do is the design:

    selection      which postings, resolved explicitly, never "all of them"
    preflight      what this would cost, computed before anything is sent
    pacing         requests and tokens kept under the caller's chosen ceilings
    budget         a hard stop the run cannot exceed, retries included
    persistence    one transaction per posting, so an interruption keeps work

Extraction itself is not here. `extract_job` does that, exactly as it does for
the replay client and the assisted development harness, and this module hands it
a client like any other. There is no live-only extraction path, no vendor-only
prompt, no benchmark-only assembly. If there were, a benchmark would be
measuring a pipeline the product does not run.

WHY THE CLIENT IS WRAPPED RATHER THAN THE PIPELINE CHANGED
----------------------------------------------------------
Caching, pacing and the call budget are all properties of *how a request
reaches a vendor*, which is precisely what the `LLMClient` port describes. A
wrapper is therefore the whole implementation: `extract_job` cannot tell it from
an adapter, and does not have to be told that a benchmark is running.

WHAT THE CACHE IS, AND WHY IT IS THE PRODUCTION ONE
---------------------------------------------------
`llm_call` already stores every attempt under its cache key, and the key already
carries the model identity, the prompt version, the transport version and the
whole model-visible input. So the cache exists; nothing consulted it before a
call. Now something does, and three properties fall out for free:

  * a re-run costs only what it did not already answer;
  * an interrupted run resumes rather than re-paying from the start;
  * a development answer can never be served to a benchmark arm, because the
    model identity is part of the key and `cowork-development` is not the name
    of any production model.

The third is checked rather than assumed: the lookup also matches `model` and
`provider` columns, which are redundant with the key by construction and would
have to disagree for a cross-arm hit to happen.
"""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from career_agent.llm.budget import (
    BudgetLedger,
    LedgerEvent,
    Reservation,
    outcome_for,
)
from career_agent.llm.client import (
    Family,
    LLMClient,
    LLMError,
    LLMNotSent,
    LLMRequest,
    LLMResponse,
    ModelConfig,
    Runner,
)
from career_agent.llm.failures import (
    ArmUnusable,
    CallBudgetExhausted,
    SpendBudgetExhausted,
    is_retryable,
)
from career_agent.llm.pacing import RateLimiter
from career_agent.llm.pricing import ModelPrice, estimate_cost, observed_price
from career_agent.llm.requests import (
    BuiltRequest,
    build_description_request,
    build_provider_request,
)
from career_agent.llm.tokens import (
    MEASURED_OUTPUT_CEILING,
    TokenEstimate,
    estimate_request,
    from_characters,
)
from career_agent.pipeline.extract import (
    MAX_ATTEMPTS,
    ExtractionOutcome,
    FamilyAttempt,
    JobSource,
    _as_record,
    extract_job,
    store_outcome,
)
from career_agent.storage.db import transaction
from career_agent.storage.fingerprint_repo import LLMCallRepo
from career_agent.storage.repositories import new_id

#: Substrings that mark a vendor failure as worth waiting out rather than
#: retrying immediately. Matched case-insensitively against the error text,
#: because the adapters deliberately flatten vendor exceptions into one type and
#: the only thing that survives is the message.

#: How long to hold back after a vendor says it is being asked too often. The
#: ordinary interval is the right pace when nothing is wrong; it is not the
#: right answer to a refusal that says the pace itself was the problem.
RETRY_COOLDOWN_SECONDS = 20.0


class LiveRunError(RuntimeError):
    """The live runner refused to do something, or had to stop."""


class LiveArmUnusable(ArmUnusable, LiveRunError):
    """The account cannot use this arm at all, so the run is over.

    Distinct from both budgets, because the operator's next action is different:
    a spent budget means authorise more, a spent quota means wait, and this
    means fix the account. Nothing about running fewer postings would help.
    """


class LiveSpendBudgetExhausted(SpendBudgetExhausted, LiveRunError):
    """The authorised money is spent. Distinct from the call budget on purpose.

    A run can stop for two unrelated reasons and the operator needs to know
    which: too many requests is a pacing or retry problem, too much money is a
    pricing or volume one. Collapsing them into one stop would report a $0.14
    benchmark and a runaway as the same event.
    """


class LiveCallBudgetExhausted(CallBudgetExhausted, LiveRunError):
    """The caller's live-call ceiling was reached.

    Deliberately **not** an `LLMError`. `_ask` catches `LLMError` and records it
    as a failed attempt, which is the correct treatment for a model that
    answered badly and exactly the wrong treatment for a budget: a budget stop
    is not something the model said, and swallowing it would let a run continue
    past the ceiling one recorded failure at a time.
    """


# =========================================================================
# PLANNING -- everything that happens before a client exists
# =========================================================================


@dataclass(frozen=True)
class PlannedCall:
    """One request the run would make, and whether it is already answered."""

    family: Family
    cache_key: str
    #: Every posting this call serves. More than one is the normal case for the
    #: provider family: two postings whose observation blocks render identically
    #: are asking one question, and paying twice for it is waste that only shows
    #: up if someone counts.
    job_ids: tuple[str, ...]
    estimate: TokenEstimate
    cached: bool

    @property
    def live(self) -> bool:
        return not self.cached


@dataclass(frozen=True)
class LivePlan:
    """What a run would do, computed without constructing a vendor client.

    Everything a preflight prints comes from here, and so does everything the
    run then enforces. One object rather than two means the summary a person
    approved is the summary the runner obeys.
    """

    database: Path
    config: ModelConfig
    sources: tuple[JobSource, ...]
    calls: tuple[PlannedCall, ...]
    requests_per_minute: int
    tokens_per_minute: int
    max_live_calls: int
    #: The durable, cumulative authorisation this run spends from. Required, and
    #: carried on the PLAN rather than passed to the runner, because the thing a
    #: person approves is a plan: "does this fit the remaining budget" is a
    #: preflight question, and a preflight that could not see the budget would be
    #: answering a different one.
    ledger: BudgetLedger = field(kw_only=True)
    max_attempts: int = MAX_ATTEMPTS
    #: The dated price observation for this arm, or None when nobody recorded
    #: one. None on a paid vendor is a refusal, not a default of zero -- see
    #: `estimated_cost`.
    price: ModelPrice | None = None
    #: The caller's hard money ceiling in USD. None means no money may be spent,
    #: which is the only safe default: a paid arm with no stated ceiling has not
    #: been authorised to spend anything.
    max_cost_usd: float | None = None

    @property
    def job_ids(self) -> tuple[str, ...]:
        return tuple(source.job_id for source in self.sources)

    @property
    def effective_max_live_calls(self) -> int:
        """What this invocation may actually send: the lower of the two ceilings.

        `--max-live-calls` is a per-invocation cap a person types; the programme
        budget is a standing authorisation nobody may raise from a command line.
        So the flag can only ever narrow, never widen, and this is the number a
        preflight should print.
        """
        return min(self.max_live_calls, self.ledger.remaining)

    @property
    def description_calls(self) -> int:
        return sum(1 for call in self.calls if call.family is Family.DESCRIPTION)

    @property
    def provider_calls(self) -> int:
        return sum(1 for call in self.calls if call.family is Family.PROVIDER)

    @property
    def jobs_needing_no_provider_call(self) -> int:
        served = {
            job for call in self.calls if call.family is Family.PROVIDER for job in call.job_ids
        }
        return len(self.sources) - len(served)

    @property
    def cache_hits(self) -> int:
        return sum(1 for call in self.calls if call.cached)

    @property
    def nominal_live_calls(self) -> int:
        """One live call per distinct unanswered question. No retries."""
        return sum(1 for call in self.calls if call.live)

    @property
    def worst_case_live_calls(self) -> int:
        """Every live call failing and being retried to the attempt limit.

        Reported, never used as the authorisation test. Requiring the worst case
        to fit would mean a 27-posting benchmark could not be authorised under
        104 calls, which is not the number anyone is being asked to approve --
        the run stops at the ceiling either way.
        """
        return self.nominal_live_calls * self.max_attempts

    @property
    def input_tokens(self) -> int:
        """Estimated input tokens for the calls that would actually be sent."""
        return sum(call.estimate.input for call in self.calls if call.live)

    @property
    def output_tokens(self) -> int:
        return sum(call.estimate.output for call in self.calls if call.live)

    @property
    def peak_tokens_per_minute(self) -> int:
        """The largest input volume one minute of pacing could draw.

        The configured request rate multiplied by the largest single call,
        because that is the burst a run of unusually long postings produces and
        it is the one a request-rate-only guard would not see coming.
        """
        largest = max((call.estimate.input for call in self.calls if call.live), default=0)
        return largest * self.requests_per_minute

    @property
    def free(self) -> bool:
        """True only when a dated observation says this arm bills nothing."""
        return self.price is not None and self.price.free

    @property
    def estimated_cost(self) -> float | None:
        """USD the nominal calls would cost, or None when it cannot be known.

        None is not zero and must never be rendered as one. It means either
        that nobody recorded a price for this exact (vendor, model) pair, or
        that the token estimate is missing -- and in both cases the honest
        report is "unknown", with the run refused rather than approved against
        a number nobody produced.
        """
        if self.price is None:
            return None
        return estimate_cost(self.price, self.input_tokens, self.output_tokens)

    @property
    def worst_case_cost(self) -> float | None:
        """Every call retried to the attempt limit, priced at the same rate.

        Retries are bought, so they belong in the ceiling the caller states even
        though the *authorisation* test is the nominal figure -- the same
        asymmetry `worst_case_live_calls` documents.
        """
        nominal = self.estimated_cost
        return None if nominal is None else nominal * self.max_attempts

    @property
    def fully_estimated(self) -> bool:
        """True when every static half came from a real measurement."""
        return all(call.estimate.static_measured for call in self.calls)

    def token_estimates(self) -> dict[str, int]:
        """Input tokens by cache key, for the rate limiter."""
        return {call.cache_key: call.estimate.input for call in self.calls}


def build_requests(source: JobSource, config: ModelConfig) -> list[BuiltRequest]:
    """Exactly the requests extracting this posting would make, in order.

    The same two builders `extract_job` calls, with the same arguments. A
    preflight that constructed its requests any other way would be predicting a
    run that does not exist -- and the provider family's `None` return, which is
    a real posting costing one call instead of two, is precisely the case a
    hand-rolled estimate gets wrong.
    """
    built = [build_description_request(source.content_hash, source.description_text, config)]
    provider = build_provider_request(
        source.provider, source.observations, source.field_map_digest, config
    )
    if provider is not None:
        built.append(provider)
    return built


def plan_run(
    conn: sqlite3.Connection,
    sources: Sequence[JobSource],
    config: ModelConfig,
    *,
    database: Path,
    requests_per_minute: int,
    tokens_per_minute: int,
    max_live_calls: int,
    ledger: BudgetLedger,
    max_attempts: int = MAX_ATTEMPTS,
    price: ModelPrice | None = None,
    max_cost_usd: float | None = None,
) -> LivePlan:
    """Work out what this run would send, and what is already answered.

    Makes no network call and constructs no vendor client. Building a request
    and pricing it are both offline operations, which is what allows the
    expensive decision to be reviewed before anything can act on it.
    """
    # Resolved here rather than demanded from the caller, for the reason
    # `_resolve_pace` resolves quotas: the plan already knows the exact
    # (vendor, model) pair a price is keyed by, and a guard that only engages
    # when somebody remembers to pass an argument is not a guard.
    price = price if price is not None else observed_price(config.vendor, config.identifier)

    grouped: dict[str, PlannedCall] = {}
    order: list[str] = []

    for source in sources:
        for built in build_requests(source, config):
            key = built.cache_key.key
            existing = grouped.get(key)
            if existing is None:
                order.append(key)
                grouped[key] = PlannedCall(
                    family=built.request.family,
                    cache_key=key,
                    job_ids=(source.job_id,),
                    estimate=estimate_request(built),
                    cached=False,
                )
            else:
                grouped[key] = PlannedCall(
                    family=existing.family,
                    cache_key=key,
                    job_ids=(*existing.job_ids, source.job_id),
                    estimate=existing.estimate,
                    cached=False,
                )

    answered = LLMCallRepo(conn).validated_answers(order, model=config.arm, vendor=config.vendor)
    calls = tuple(
        PlannedCall(
            family=grouped[key].family,
            cache_key=key,
            job_ids=grouped[key].job_ids,
            estimate=grouped[key].estimate,
            cached=key in answered,
        )
        for key in order
    )

    return LivePlan(
        database=database,
        config=config,
        sources=tuple(sources),
        calls=calls,
        requests_per_minute=requests_per_minute,
        tokens_per_minute=tokens_per_minute,
        max_live_calls=max_live_calls,
        ledger=ledger,
        price=price,
        max_cost_usd=max_cost_usd,
        max_attempts=max_attempts,
    )


# =========================================================================
# THE CLIENT WRAPPER -- cache, budget, pace
# =========================================================================


@dataclass
class PacedLiveClient:
    """An `LLMClient` that answers from storage first and paces what it cannot.

    ``runner`` changes per call, and that is not a bug to be tidied away. The
    enum documents itself as "how a completion was obtained", and a completion
    served from `llm_call` was obtained by replay -- free, deterministic, no
    vendor involved. Reporting it as a production call would put a free answer
    in the paid bucket, which is the one accounting error this milestone cannot
    afford. Extraction is strictly sequential and builds its attempt row
    immediately after `complete` returns, so the value it reads is always the
    one this call set.
    """

    inner: LLMClient
    calls: LLMCallRepo
    limiter: RateLimiter
    max_live_calls: int
    #: The durable authorisation. No default, deliberately: a client that could
    #: be built without one would be a live client nobody had authorised, and
    #: "remember to pass the ledger" is not an invariant. Both construction
    #: sites -- `run_plan` and the one in the runner tests -- name it.
    ledger: BudgetLedger = field(kw_only=True)
    #: The money half of the guard. `price=None` on a billing vendor means no
    #: call may be made at all; `max_cost_usd=None` means no money is
    #: authorised. Neither defaults to permission.
    price: ModelPrice | None = None
    max_cost_usd: float | None = None
    #: Input tokens by cache key, from the plan. A key the plan did not
    #: anticipate is estimated from its text rather than paced as if free.
    token_estimates: dict[str, int] = field(default_factory=dict)
    retry_cooldown_seconds: float = RETRY_COOLDOWN_SECONDS

    #: The run this client belongs to, stamped on every reservation so a ledger
    #: line and an `llm_call` row can be joined afterwards.
    execution_id: str = ""
    #: Which phase of the programme is spending. Recorded on every reservation
    #: so a ledger read years later can say what a request was for.
    phase: str = "UNSPECIFIED"
    #: The posting currently being extracted, set by `run_plan` before each one.
    #: Mutated per posting for the same reason `runner` is mutated per call:
    #: extraction is strictly sequential, and the alternative is threading a job
    #: id through the `LLMClient` protocol that every other client would ignore.
    job_id: str = ""

    vendor: str = field(init=False, default="")
    runner: Runner = field(init=False, default=Runner.PRODUCTION_API)
    #: The last reservation this client wrote, so a caller that discovers a
    #: persistence failure afterwards can attribute it to the request it belongs
    #: to rather than to the run in general.
    last_reservation: Reservation | None = field(init=False, default=None)
    #: Requests that reached the HTTP transport. This is the number a quota is
    #: measured in, and the number the hard budget bounds.
    live_calls: int = field(init=False, default=0)
    #: Invocations that failed in our own process before any request existed.
    #: Recorded and reported, but they consume no quota and no budget: calling
    #: a `TypeError` an API call would misstate a free tier's daily allowance.
    local_failures: int = field(init=False, default=0)
    cache_hits: int = field(init=False, default=0)
    #: What the vendor's own reported usage says has been spent so far. Built
    #: from returned token counts, never from estimates, because this is the
    #: number a report must be able to defend.
    spent_usd: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.vendor = self.inner.vendor
        self.runner = self.inner.runner

    @property
    def remaining_budget(self) -> int:
        """What may still be sent: the lower of the two ceilings, always.

        The per-invocation cap and the programme budget are checked separately
        in `complete`, which is the same thing as this minimum and fails with
        the more specific message. This is the number to report.
        """
        return min(max(0, self.max_live_calls - self.live_calls), self.ledger.remaining)

    @property
    def remaining_usd(self) -> float:
        if self.max_cost_usd is None:
            return 0.0
        return max(0.0, self.max_cost_usd - self.spent_usd)

    def _charge(self, response: LLMResponse) -> None:
        """Add one completed call to the running spend, before the next is made.

        The estimate authorised the run; this is what the vendor says it
        actually charged, and it is the figure the next call is checked
        against. A call whose usage came back unknown is charged at the
        estimate the limiter used rather than at zero -- an unpriceable call is
        the one case where continuing on a guess is worse than continuing on a
        conservative over-charge.
        """
        if self.price is None or self.price.free:
            return
        try:
            self.spent_usd += estimate_cost(
                self.price, response.input_tokens, response.output_tokens
            )
        except ValueError:
            self.spent_usd += self._unpriceable_call_estimate()

    def _unpriceable_call_estimate(self) -> float:
        """A deliberately generous stand-in for a call the vendor did not meter.

        The largest input the plan anticipated, at the full uncached rate, with
        the measured output median. It will overstate. That is the point: the
        alternative is treating an unmetered call as free, which is how a spend
        guard passes a run it should have stopped.
        """
        assert self.price is not None
        largest_input = max(self.token_estimates.values(), default=0)
        return estimate_cost(self.price, largest_input, MEASURED_OUTPUT_CEILING)

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        cached = (
            self.calls.validated_answer(
                request.cache_key, model=config.arm, vendor=self.inner.vendor
            )
            if request.cache_key
            else None
        )
        if cached is not None:
            self.cache_hits += 1
            self.runner = Runner.REPLAY
            return LLMResponse(
                raw_text=cached.raw_output,
                model=cached.model,
                input_tokens=cached.input_tokens,
                output_tokens=cached.output_tokens,
            )

        # Is this arm the one that was authorised at all? Asked first, and
        # before anything is written: a wrong vendor or model is not a budget
        # question, and refusing it must cost nothing.
        self.ledger.refuse_unauthorized(config, self.phase)

        # Checked before the call, not after. A budget enforced afterwards is a
        # report, not a ceiling. Two ceilings, checked separately so the message
        # says which one stopped the run: the flag a person typed for this
        # invocation, and the standing authorisation no flag can raise.
        if self.live_calls >= self.max_live_calls:
            raise LiveCallBudgetExhausted(
                f"the authorised live-call budget of {self.max_live_calls} is spent; "
                f"{request.family} call for cache key {request.cache_key[:24]}... was not sent"
            )

        self._refuse_if_unaffordable(request)

        self.runner = self.inner.runner
        # Paced BEFORE the reservation is written. The limiter may sleep, and a
        # reservation written before a wait would be spent by a process that died
        # waiting -- so the line goes down as late as possible while still going
        # down before the request.
        self.limiter.acquire(self._input_tokens(request))
        # Durable, fsynced, and the last thing that happens before the POST.
        # `reserve` raises `ProgramBudgetExhausted` here rather than writing,
        # so an exhausted authorisation stops before a request exists.
        reservation = self.ledger.reserve(
            config=config,
            family=request.family,
            cache_key=request.cache_key,
            execution_id=self.execution_id,
            phase=self.phase,
            job_id=self.job_id,
        )
        self.last_reservation = reservation
        self.live_calls += 1
        try:
            response = self.inner.complete(request, config)
        except LLMNotSent:
            # Never reached the wire: give the budget back and count it where it
            # belongs. The quota was not touched and neither was the vendor. The
            # only refund in either counter, and the two agree by construction.
            self.live_calls -= 1
            self.local_failures += 1
            self.ledger.settle(reservation, LedgerEvent.NOT_SENT)
            raise
        except LLMError as exc:
            # Asked, and no usable answer. The request happened, so it stays
            # spent -- a timeout is recorded as one because the vendor may well
            # have served it, and the quota does not care that we never read it.
            self.ledger.settle(reservation, outcome_for(str(exc)), detail=str(exc))
            if _is_retryable(str(exc)):
                self.limiter.penalise(_cooldown_for(exc, self.retry_cooldown_seconds))
            raise
        self.ledger.settle(reservation, LedgerEvent.SENT)
        self._charge(response)
        return response

    def _refuse_if_unaffordable(self, request: LLMRequest) -> None:
        """The money half of the guard, checked before the call like the other half.

        Three refusals, and none of them defaults to permission:

        * a **billing arm with no recorded price** cannot be run at all. We
          would have no way to say afterwards what it cost.
        * **no stated ceiling** on a billing arm is not "unlimited", it is "no
          money authorised". `--max-cost-usd` is how a caller says otherwise.
        * a call whose own conservative estimate would carry the running total
          past the ceiling stops the run, rather than being the one that
          crosses it.

        A free arm passes all three untouched and records no spend, so this
        cannot manufacture a cost for Gemini.
        """
        if self.price is not None and self.price.free:
            return
        if self.price is None:
            raise LiveSpendBudgetExhausted(
                f"no recorded price for this arm, so {self.vendor} cannot be billed against a "
                "ceiling. Record a dated observation in llm/pricing.py before spending on it: "
                "a run nobody can price afterwards is a run nobody can defend."
            )
        if self.max_cost_usd is None:
            raise LiveSpendBudgetExhausted(
                "this arm bills, and no spend was authorised. Pass --max-cost-usd to state a "
                "ceiling; the absence of one means no money, never unlimited money."
            )

        next_call = estimate_cost(self.price, self._input_tokens(request), MEASURED_OUTPUT_CEILING)
        if self.spent_usd + next_call > self.max_cost_usd:
            raise LiveSpendBudgetExhausted(
                f"the authorised spend of ${self.max_cost_usd:.2f} would be exceeded: "
                f"${self.spent_usd:.4f} already reported by the vendor, and this "
                f"{request.family} call is estimated at ${next_call:.4f}. Not sent."
            )

    def _input_tokens(self, request: LLMRequest) -> int:
        known = self.token_estimates.get(request.cache_key)
        if known is not None:
            return known
        return from_characters(request.system) + from_characters(request.user)


def _cooldown_for(exc: Exception, local_policy: float) -> float:
    """How long to hold back, when the provider has an opinion and we have one.

    The rule is one-directional: **never sooner than the provider asked**. A
    hint is a floor, not a schedule. Our own cooldown is a safety policy and may
    be longer -- the 429 from Decart asked for 5 seconds and 20 is what this
    project waits -- but a provider asking for 30 must not be answered in 20
    because a constant in this file says so.

    Only a structured field or a `Retry-After` header reaches this; the adapter
    refuses to read a number out of prose, and an absent hint is 0.0 rather than
    a guess.
    """
    hint = getattr(exc, "retry_after_seconds", None)
    if not isinstance(hint, int | float) or isinstance(hint, bool):
        hint = 0.0
    return max(local_policy, float(hint))


def _is_retryable(message: str) -> bool:
    """Delegated, so three modules cannot disagree about what a 402 means.

    The markers used to live here, and one of them was the bare word `quota` --
    which Cerebras puts in the `param` field of a *payment* error. See
    `llm/failures.py` for what that cost.
    """
    return is_retryable(message)


# =========================================================================
# THE RUN
# =========================================================================


@dataclass
class LiveRunReport:
    """What the run actually did, in the terms the accounting needs.

    Live calls and cache hits are counted separately and never added together.
    A benchmark has to be able to say which of its outputs genuinely exercised
    the model, and a single "calls" number cannot.
    """

    jobs_planned: int
    max_live_calls: int
    #: Which run this was. Stamped on every attempt row it wrote.
    execution_id: str = ""
    outcomes: list[ExtractionOutcome] = field(default_factory=list)
    #: HTTP requests that reached the vendor. What a quota counts.
    live_calls: int = 0
    #: Failures in our own process, before any request existed. Zero quota.
    local_failures: int = 0
    cache_hits: int = 0
    #: What the vendor's reported usage says this run cost. 0.0 on a free arm
    #: because nothing was billed -- a fact, not a missing measurement.
    spent_usd: float = 0.0
    waited_seconds: float = 0.0
    #: Why the run ended before every posting was attempted, if it did.
    stopped_early: str | None = None

    @property
    def jobs_run(self) -> int:
        return len(self.outcomes)

    @property
    def fingerprints(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def failures(self) -> int:
        return sum(1 for outcome in self.outcomes if not outcome.ok)

    @property
    def input_tokens(self) -> int:
        """Reported input tokens, counting only attempts that reached a vendor."""
        return sum(
            attempt.input_tokens or 0
            for outcome in self.outcomes
            for attempt in outcome.attempts
            if attempt.runner is Runner.PRODUCTION_API
        )

    @property
    def output_tokens(self) -> int:
        return sum(
            attempt.output_tokens or 0
            for outcome in self.outcomes
            for attempt in outcome.attempts
            if attempt.runner is Runner.PRODUCTION_API
        )


def run_plan(
    conn: sqlite3.Connection,
    plan: LivePlan,
    client: LLMClient,
    limiter: RateLimiter,
    *,
    phase: str = "UNSPECIFIED",
) -> LiveRunReport:
    """Extract every planned posting, one committed transaction at a time.

    **One transaction per posting, deliberately.** A single transaction around
    the whole benchmark would mean an interruption at posting 24 discarding 23
    successful extractions and the record of every model call that produced
    them -- and under a paid runner, re-paying for all of them. The unit of
    durability is the unit of work.

    A failed extraction is stored too. Its `llm_call` rows are the record of
    what the model said, and a run that kept only its successes could not
    explain a regression afterwards.
    """
    # One identity per invocation. Every physical request this run makes is
    # stamped with it, which is what lets the same semantic question be asked
    # again by a later run without the earlier attempts being overwritten -- or,
    # as happened to the Cerebras screen, the later ones silently dropped.
    # Minted before the client, because the client stamps every reservation with
    # it and a reservation is written before the first request.
    execution_id = new_id()
    paced = PacedLiveClient(
        inner=client,
        price=plan.price,
        max_cost_usd=plan.max_cost_usd,
        calls=LLMCallRepo(conn),
        limiter=limiter,
        max_live_calls=plan.max_live_calls,
        ledger=plan.ledger,
        token_estimates=plan.token_estimates(),
        execution_id=execution_id,
        phase=phase,
    )
    report = LiveRunReport(
        jobs_planned=len(plan.sources),
        max_live_calls=plan.max_live_calls,
        execution_id=execution_id,
    )

    def save_now(attempt: FamilyAttempt) -> None:
        """Write one attempt the instant it is observed.

        This is the whole reliability invariant, in one callback. An attempt
        held in memory until the end of a posting can be lost by anything that
        ends the run first -- and across four rounds, four different things did.
        Here nothing later can reach backwards and unmake it.

        Its own transaction, so a failure persisting one attempt cannot roll
        back another, and a fingerprint written afterwards cannot roll back the
        evidence that produced it.
        """
        with transaction(conn):
            LLMCallRepo(conn).record(_as_record(attempt, source.job_id, execution_id))

    for source in plan.sources:
        # Recorded on every reservation this posting makes. Set here rather than
        # threaded through `LLMClient.complete`, whose signature belongs to every
        # client and not to this one.
        paced.job_id = source.job_id
        try:
            outcome = extract_job(paced, plan.config, source, plan.max_attempts, save_now)
        except KeyboardInterrupt:
            # Everything already committed stays committed. Reported rather
            # than re-raised so the caller can print what survived, which is
            # the question anyone interrupting a paid run immediately has.
            # Every attempt this posting made is already durable -- `save_now`
            # wrote each one as it happened -- so an interrupt costs the
            # fingerprint, never the evidence.
            report.stopped_early = "interrupted; every attempt made so far is stored"
            break

        if outcome.halted and not outcome.attempts:
            # The ceiling was reached before this posting made any request. It
            # was not run, and counting it would report a failure that never
            # happened.
            report.stopped_early = outcome.halted
            break

        try:
            with transaction(conn):
                store_outcome(conn, outcome, execution_id, attempts_already_stored=True)
        except sqlite3.Error as exc:
            # The attempts are already durable. Losing the fingerprint is bad;
            # losing the run and every posting after it because one document
            # would not store is worse, and it would also discard the record of
            # calls that were already paid for.
            #
            # Noted in the ledger against the request it belongs to. It changes
            # no count -- a persist failure is not a refund, and the request was
            # made -- but a budget that spent a request whose record went missing
            # should say so where the spending is recorded.
            if paced.last_reservation is not None:
                plan.ledger.settle(
                    paced.last_reservation, LedgerEvent.PERSIST_FAILED, detail=str(exc)
                )
            report.stopped_early = f"persistence failed after the attempts were stored: {exc}"
            report.outcomes.append(outcome)
            break
        report.outcomes.append(outcome)

        if outcome.halted:
            # Stored first, then stopped. A ceiling reached PART WAY THROUGH a
            # posting leaves real attempts behind it, and those are the ones
            # that were paid for. Reported distinctly from an arm-wide failure because the
            # operator's next move differs: authorise more, versus fix the
            # account.
            report.stopped_early = outcome.halted
            break

        if outcome.arm_unusable:
            # The attempt is stored first, then the run ends. A vendor-wide
            # refusal is not a posting-level failure: repeating it across the
            # remaining postings buys one identical error message per job and
            # spends a request for each. The first Cerebras screen did exactly
            # that -- nine refusals, eighteen requests, no information after the
            # first.
            report.stopped_early = (
                f"the arm is unusable and the run stopped after one attempt: {outcome.failure}"
            )
            break

    report.live_calls = paced.live_calls
    report.local_failures = paced.local_failures
    report.cache_hits = paced.cache_hits
    report.spent_usd = paced.spent_usd
    report.waited_seconds = limiter.waited_seconds
    return report

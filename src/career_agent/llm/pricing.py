"""Prices we have READ, from a vendor's documentation, on a particular day.

Not a property of an adapter. The reasoning is `llm/quotas.py`'s, applied to
the other number a run has to know:

  * a price is not a fact about a model, it is a fact about a **price list**;
  * price lists change, without any notification reaching this repository;
  * the same model costs different amounts on batch, on cache reads, and above
    a long-context threshold;
  * and a number embedded in `vendors/openai.py` would read, forever after, as
    something the SDK told us. Nothing here was told to us by an SDK.

So a price carries its provenance and its date, and a pair nobody recorded is
**refused rather than guessed at** -- because the alternative is a run that
prices itself against a neighbouring model's rate and reports the result with
the same confidence as a real one.

WHAT THIS IS FOR, AND WHAT IT IS NOT
------------------------------------
It exists to answer one question before a paid run starts: *what is the most
this can cost?* It is not accounting. The authoritative record of what was
actually spent is the vendor's billing console, and this module's estimate must
never be presented as a substitute for it.

Two directions of error, and only one is acceptable. Over-estimating stops a
run that would have been affordable, which costs a re-authorisation. Under-
estimating spends money nobody approved. Every default here therefore rounds
against us: the standard rate rather than the batch rate, the uncached input
rate for every token, and an unknown token count priced as a refusal to
continue rather than as zero.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from career_agent.llm.client import identifier_from_arm
from career_agent.llm.failures import refused_before_inference

#: A run whose usage the vendor did not report cannot be priced. This is the
#: value that says so, and it is not zero -- see `estimate_cost`.
UNKNOWN = None


class Funding(StrEnum):
    """Who actually pays for a call. Not the same question as what it costs.

    The prototype has a hard ceiling on the maintainer's OWN money and no
    ceiling at all on arithmetic, so "what did this cost" and "what did this
    cost *us*" have to be two numbers. A promotional trial makes them differ by
    the whole bill.
    """

    #: The vendor bills nothing at all. Economic cost is genuinely zero.
    FREE_TIER = "FREE_TIER"
    #: The vendor bills normally and a promotional grant absorbs it. Economic
    #: cost is real and personal cash exposure is zero -- until the grant runs
    #: out, which is why the grant is recorded with an amount.
    PROMOTIONAL_CREDIT = "PROMOTIONAL_CREDIT"
    #: The maintainer's own money. The only funding the $10 ceiling governs.
    PERSONAL_CASH = "PERSONAL_CASH"


@dataclass(frozen=True)
class ModelPrice:
    """A price list entry, as read on one day, in USD per million tokens.

    `cached_input` is recorded but deliberately unused by the estimator. We do
    not know before a call how much of an input a vendor will serve from its
    own cache, and assuming any of it would understate the bill.
    """

    input_per_mtok: float
    output_per_mtok: float
    cached_input_per_mtok: float | None = None
    source: str = "unrecorded"
    observed_at: str = "unknown"
    funding: Funding = Funding.PERSONAL_CASH
    #: The promotional grant believed to cover this arm, in USD, as observed on
    #: a date. Informational: nothing decrements it, because the vendor's
    #: console is the only authority on a balance and this repository must not
    #: pretend otherwise.
    promotional_credit_usd: float | None = None

    @property
    def free(self) -> bool:
        """True only when the vendor bills nothing. Promotional is NOT free.

        The distinction the first Cerebras arm got wrong: it was recorded as a
        free tier when it was a paid model with a trial grant attached. Those
        produce the same personal-cash figure and completely different economic
        ones, and only one of them stops working when a balance runs out.
        """
        return self.funding is Funding.FREE_TIER

    def describe(self) -> str:
        if self.free:
            return f"free tier, {self.source}, read {self.observed_at}"
        rates = f"${self.input_per_mtok:.2f} in / ${self.output_per_mtok:.2f} out per 1M"
        if self.funding is Funding.PROMOTIONAL_CREDIT:
            grant = (
                f", ${self.promotional_credit_usd:.2f} promotional credit"
                if self.promotional_credit_usd is not None
                else ""
            )
            return f"{rates} on PROMOTIONAL CREDIT{grant}, {self.source}, read {self.observed_at}"
        return f"{rates}, {self.source}, read {self.observed_at}"


#: Keyed by the exact (vendor, model identifier) pair the price was read for.
#: A reasoning setting does not change the rate; it changes how many tokens are
#: billed, which the token counts already carry.
OBSERVED_PRICES: dict[tuple[str, str], ModelPrice] = {
    ("openai", "gpt-5.6-luna"): ModelPrice(
        input_per_mtok=0.20,
        output_per_mtok=1.20,
        cached_input_per_mtok=0.02,
        source="OpenAI model documentation (M2.5 §2; supersedes three inconsistent "
        "reads of the pricing table)",
        observed_at="2026-09-03",
    ),
    # NOT a free tier. The first screen recorded it as one and was refused with
    # HTTP 402 on every request: this is a billed model, and what makes it cost
    # the maintainer nothing is a promotional grant, which is a different fact
    # with a different failure mode -- a grant runs out.
    #
    # Rates could not be read from Cerebras's own pricing table, which does not
    # render its figures to a fetch. Two independent secondary sources agree and
    # corroborate each other arithmetically: $0.35/$0.75 reproduces the
    # published $0.39 blended rate exactly at the documented 7:2:1 cache:input:
    # output ratio. Recorded with that provenance rather than as a vendor fact,
    # and worth confirming in the billing console before any large run.
    ("cerebras", "gpt-oss-120b"): ModelPrice(
        input_per_mtok=0.35,
        output_per_mtok=0.75,
        source="secondary sources, corroborated against the published blended rate; "
        "Cerebras's own table was unreachable. Trial state from this maintainer's "
        "dashboard: Team org, Free Trial ACTIVE",
        observed_at="2026-09-03",
        funding=Funding.PROMOTIONAL_CREDIT,
        promotional_credit_usd=5.00,
    ),
    # `:free` is part of the model id, not a tier flag: the paid GLM-5.2 is a
    # different id at $1.40 / $4.40, and Z.ai's own platform has no free
    # GLM-5.2 at all -- only the Flash variants, which are different models.
    ("openrouter", "z-ai/glm-5.2:free"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="OpenRouter free model variant",
        observed_at="2026-09-03",
    ),
    # Read from the endpoint on 2026-09-03: one endpoint, provider GMICloud,
    # prompt 0 and completion 0. The `:free` suffix is the model id; the paid
    # MiniMax M3 is a different id and would need its own row.
    ("openrouter", "minimax/minimax-m3:free"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="OpenRouter endpoint metadata, /models/minimax/minimax-m3:free/endpoints: "
        "prompt 0, completion 0, provider GMICloud",
        observed_at="2026-09-03",
    ),
    # Read from the endpoint itself rather than from a model page: `pricing.
    # prompt` and `pricing.completion` are both 0 on the one endpoint OpenRouter
    # routes this slug to (provider Nvidia), on 2026-09-03. The `:free` suffix
    # is part of the model id and not a tier flag -- the paid Nemotron is a
    # different id and would need its own row.
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="OpenRouter endpoint metadata, /models/nvidia/"
        "nemotron-3-super-120b-a12b:free/endpoints: prompt 0, completion 0, provider Nvidia",
        observed_at="2026-09-03",
    ),
    # The arm Stage 0 ran on. `free` is the whole entry: the rates are zero
    # because nothing was billed, not because Gemini is free in general -- the
    # paid tier is $0.25 / $1.50, and a project on it needs its own row.
    ("google", "gemini-3.1-flash-lite"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="this maintainer's Google AI Studio project",
        observed_at="2026-09-03",
    ),
    # GEMMA 4 ON THE GEMINI API. The strongest free classification available,
    # and the reason it is not read as a trial: Google's own pricing table
    # lists Gemma 4 as "Free of charge" for input and output AND lists the PAID
    # tier as "Not available". There is no paid rate to fall into, no grant to
    # exhaust and no card to attach -- which is exactly what separates this
    # from the Cerebras row above, where the rates are real and a promotional
    # balance absorbs them.
    #
    # Keyed per exact model id, not per family. "Gemma is free" is not a fact
    # this file is allowed to hold; "these two ids were free on this date" is.
    ("google", "gemma-4-31b-it"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="Google Gemini API pricing, Gemma 4 row: free tier 'Free of charge' "
        "for input and output, paid tier 'Not available'",
        observed_at="2026-09-03",
    ),
    ("google", "gemma-4-26b-a4b-it"): ModelPrice(
        input_per_mtok=0.0,
        output_per_mtok=0.0,
        funding=Funding.FREE_TIER,
        source="Google Gemini API pricing, Gemma 4 row: free tier 'Free of charge' "
        "for input and output, paid tier 'Not available'",
        observed_at="2026-09-03",
    ),
    ("anthropic", "claude-sonnet-5"): ModelPrice(
        input_per_mtok=2.00,
        output_per_mtok=10.00,
        cached_input_per_mtok=0.20,
        source="Anthropic pricing documentation (M2.5 §2)",
        observed_at="2026-09-02",
    ),
    ("anthropic", "claude-haiku-4-5-20251001"): ModelPrice(
        input_per_mtok=1.00,
        output_per_mtok=5.00,
        cached_input_per_mtok=0.10,
        source="Anthropic pricing documentation (M2.5 §2)",
        observed_at="2026-09-02",
    ),
    # The PEAK rate. Off-peak is half, and which one applies depends on the UTC
    # hour the vendor receives the call; estimating at peak means a budget can
    # only ever be under-spent, never over-spent.
    ("deepseek", "deepseek-flash"): ModelPrice(
        input_per_mtok=0.30,
        output_per_mtok=1.20,
        cached_input_per_mtok=0.006,
        source="DeepSeek API pricing, deepseek-flash (DeepSeek-V4.1-Flash), peak rate",
        observed_at="2026-09-25",
    ),
}


def observed_price(vendor: str, model: str) -> ModelPrice | None:
    """What we recorded for this exact pair, or nothing.

    No fallback to the vendor and none to a similar model, for the reason
    `observed_quota` gives: a rate borrowed from a neighbour is a rate nobody
    read, reported with the confidence of one that was.
    """
    return OBSERVED_PRICES.get((vendor, model))


def estimate_cost(price: ModelPrice, input_tokens: int | None, output_tokens: int | None) -> float:
    """USD for a known number of tokens at a known rate.

    Raises on an unknown count rather than returning 0.0. A vendor that
    reported no usage has told us nothing about what it charged, and the one
    reading that must never produce is "this call was free" -- which is exactly
    what a `None or 0` would produce, silently, on the arm where it costs money.
    """
    if price.free:
        return 0.0
    if input_tokens is None or output_tokens is None:
        raise ValueError(
            "cannot price a call whose token usage the vendor did not report. "
            "Unknown usage is not zero usage, and treating it as zero is how a "
            "spend guard passes a run it should have stopped."
        )
    return (input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok) / 1_000_000


#: The whole paid-inference budget for Personal Alpha, across every arm and
#: every run, for the life of the prototype.
#:
#: A product decision, not a vendor fact, and deliberately a single number: the
#: point of a prototype ceiling is that it is small enough to be obviously
#: affordable and hard enough to be worth arguing with. Raising it is a decision
#: somebody makes on purpose, in a diff.
PERSONAL_ALPHA_PAID_CEILING_USD = 10.00


@dataclass(frozen=True)
class LostRun:
    """Inference that provably happened and whose attempt rows do not exist.

    Exactly one entry, and it should stay that way: this is a scar, not a
    feature. The ledger defect that produced it is fixed, so nothing new can
    land here without the fix having regressed.

    The alternative was fabricating `llm_call` rows to make the audit balance,
    which would put invented provenance in the one table whose entire purpose is
    to record what actually happened. So the cost is carried here, plainly
    labelled, and the missing evidence is named as missing.
    """

    vendor: str
    arm: str
    calls: int
    input_tokens: int
    output_tokens: int
    economic_usd: float
    funding: "Funding"
    what_happened: str
    observed_at: str


#: Runs whose economics are known and whose per-attempt records are not.
#:
#: The Cerebras screen of 2026-09-03 made 18 real HTTP requests after billing
#: was activated. Every attempt row collided with an earlier HTTP 402 row on
#: `UNIQUE (cache_key, attempt)` and was dropped by `ON CONFLICT DO NOTHING`.
#: The run report survived in memory and is the source of these figures; what
#: the model actually said did not survive at all.
LOST_RUNS: tuple[LostRun, ...] = (
    LostRun(
        vendor="cerebras",
        arm="gpt-oss-120b@medium",
        calls=18,
        input_tokens=137_878,
        output_tokens=62_794,
        economic_usd=0.0954,
        funding=Funding.PROMOTIONAL_CREDIT,
        what_happened="18 real requests served; every attempt row discarded by the "
        "ON CONFLICT ledger defect (fixed in migration 0006). Per-attempt raw "
        "responses are LOST. Capability result: UNEVALUATED, not failed.",
        observed_at="2026-09-03",
    ),
)


@dataclass(frozen=True)
class SpendAudit:
    """What every production call this database holds has cost, and to whom.

    THREE NUMBERS, BECAUSE THERE ARE THREE QUESTIONS
    ------------------------------------------------
    * `economic_usd` -- what the inference was worth at the recorded rates.
      Real even when nobody paid it, and the figure that says what this work
      would cost without a grant.
    * `promotional_usd` -- the part a vendor's trial credit absorbed. Counted
      because a grant runs out, and a plan that assumed it was free would
      discover that mid-run.
    * `cash_usd` -- the maintainer's own money. **The only one the $10 ceiling
      governs**, and the only one an authorisation is about.

    UNKNOWN IS STILL NOT ZERO
    -------------------------
    A billing arm with no recorded price, or one whose usage never came back,
    is counted in `unknown_calls` and enters no total. Every figure is then a
    declared LOWER BOUND. That rule is what made the eighteen HTTP 402 attempts
    visible instead of silently free.

    REFUSED IS NOT UNKNOWN
    ----------------------
    A request the vendor rejected at the gate ran no inference: no tokens read,
    none generated, nothing billed. `failures.refused_before_inference` proves
    it from the stored error, so those attempts are `refused_calls` at $0.00 --
    provably unbilled rather than merely unmeasured.
    """

    economic_usd: float
    promotional_usd: float
    cash_usd: float
    #: Inference that happened and left no attempt rows. Counted so the audit
    #: is not silently short by a run, and named so it is never mistaken for
    #: evidence that exists.
    lost_run_usd: float
    lost_run_calls: int
    proven_free_calls: int
    promotional_calls: int
    cash_calls: int
    #: Attempts a vendor rejected before running the model. Cost nothing, and
    #: are not benchmark outputs.
    refused_calls: int
    unknown_calls: int
    unknown_arms: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """True when every production call could be priced, proven free or proven refused."""
        return self.unknown_calls == 0

    @property
    def remaining_cash_usd(self) -> float:
        """Personal-cash headroom. Promotional spending does not consume it."""
        return max(0.0, PERSONAL_ALPHA_PAID_CEILING_USD - self.cash_usd)

    def describe(self) -> str:
        total = (
            self.proven_free_calls
            + self.promotional_calls
            + self.cash_calls
            + self.refused_calls
            + self.unknown_calls
        )
        line = (
            f"cash ${self.cash_usd:.4f} of ${PERSONAL_ALPHA_PAID_CEILING_USD:.2f} | "
            f"economic ${self.economic_usd:.4f} (${self.promotional_usd:.4f} on credit) | "
            f"{total} calls: {self.proven_free_calls} free, {self.promotional_calls} promotional, "
            f"{self.cash_calls} cash, {self.refused_calls} refused before inference"
        )
        if self.lost_run_calls:
            line += (
                f" | + {self.lost_run_calls} calls / ${self.lost_run_usd:.4f} from a run whose "
                "attempt rows were lost"
            )
        if self.unknown_calls:
            return line + f", {self.unknown_calls} UNPRICEABLE -- totals are LOWER BOUNDS"
        return line


def audit_spend(
    rows: Sequence[tuple[str, str, int | None, int | None, str | None]],
    lost_runs: Sequence[LostRun] = LOST_RUNS,
) -> SpendAudit:
    """Price every production call from its own recorded provenance.

    `rows` are `(provider, arm, input_tokens, output_tokens, error)`.

    DERIVED, NOT BACKFILLED, AND THAT IS THE SMALLER MECHANISM
    ----------------------------------------------------------
    Every historical row already carries what a price is keyed by. Writing a
    computed cost back into `llm_call.cost_usd` would bake today's observation
    into a historical row -- and if that observation were later corrected,
    nothing would say which rows had believed it. Deriving on read costs one
    query and stays correct.

    `lost_runs` defaults to the real ledger, so the production path is complete
    by default and a caller has to opt out on purpose. Tests that are about row
    arithmetic pass `()`.

    The arm is resolved to the provider's model id first. `llm_call.model`
    stores `gpt-oss-120b@medium` because a benchmark row must name its reasoning
    setting; a price is keyed on `gpt-oss-120b`, because reasoning changes the
    token count and never the rate. Reading the arm directly is what made
    eighteen rows UNPRICEABLE and blocked every paid run.
    """
    economic = promotional = cash = 0.0
    free = promo_calls = cash_calls = refused = unknown = 0
    unknown_arms: dict[str, None] = {}

    # Inference that happened without leaving rows. Added to the economic totals
    # because it was really spent, and counted separately because the evidence
    # for it is a run report rather than a ledger.
    lost_usd = sum(run.economic_usd for run in lost_runs)
    lost_calls = sum(run.calls for run in lost_runs)
    economic += lost_usd
    promotional += sum(
        run.economic_usd for run in lost_runs if run.funding is Funding.PROMOTIONAL_CREDIT
    )
    cash += sum(run.economic_usd for run in lost_runs if run.funding is Funding.PERSONAL_CASH)

    for provider, arm, input_tokens, output_tokens, error in rows:
        if error and refused_before_inference(error):
            # Rejected at the gate: no inference ran, so there is nothing to
            # price. Proven from the refusal itself, not assumed from the
            # absence of a token count.
            refused += 1
            continue

        price = observed_price(provider, identifier_from_arm(arm))
        if price is None:
            unknown += 1
            unknown_arms[f"{provider}/{arm} (no recorded price)"] = None
            continue
        if price.free:
            free += 1
            continue

        try:
            amount = estimate_cost(price, input_tokens, output_tokens)
        except ValueError:
            unknown += 1
            unknown_arms[f"{provider}/{arm} (usage not reported)"] = None
            continue

        economic += amount
        if price.funding is Funding.PROMOTIONAL_CREDIT:
            promotional += amount
            promo_calls += 1
        else:
            cash += amount
            cash_calls += 1

    return SpendAudit(
        economic_usd=economic,
        promotional_usd=promotional,
        cash_usd=cash,
        lost_run_usd=lost_usd,
        lost_run_calls=lost_calls,
        proven_free_calls=free,
        promotional_calls=promo_calls,
        cash_calls=cash_calls,
        refused_calls=refused,
        unknown_calls=unknown,
        unknown_arms=tuple(unknown_arms),
    )

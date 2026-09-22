"""Money, and the one direction its error may point.

Over-estimating stops a run that would have been affordable, which costs a
re-authorisation. Under-estimating spends money nobody approved. Every test here
is about keeping the second impossible.
"""

import pytest

from career_agent.llm.pricing import (
    PERSONAL_ALPHA_PAID_CEILING_USD,
    audit_spend,
    estimate_cost,
    observed_price,
)


def test_an_unpriced_pair_is_refused_rather_than_borrowed_from_a_neighbour() -> None:
    """A rate taken from a similar model is a rate nobody read."""
    assert observed_price("openai", "gpt-5.6-luna") is not None
    assert observed_price("openai", "gpt-5.7-luna") is None
    assert observed_price("cerebras", "some-other-model") is None


def test_unknown_usage_raises_instead_of_costing_nothing() -> None:
    """The failure that would make the guard decorative.

    A vendor that reported no usage has told us nothing about what it charged.
    `None or 0` would turn that into "this call was free", silently, on the arm
    where it costs money.
    """
    price = observed_price("openai", "gpt-5.6-luna")
    assert price is not None

    with pytest.raises(ValueError, match="Unknown usage is not zero usage"):
        estimate_cost(price, None, 1_000)
    with pytest.raises(ValueError):
        estimate_cost(price, 1_000, None)


def test_a_free_tier_costs_nothing_even_with_unknown_usage() -> None:
    """Free is a recorded fact, so it does not depend on a token count."""
    price = observed_price("google", "gemini-3.1-flash-lite")
    assert price is not None and price.free
    assert estimate_cost(price, None, None) == 0.0


def test_proven_free_and_unknown_are_never_blended() -> None:
    """The invariant the whole audit exists for: UNKNOWN != $0.

    A row on a free arm contributes zero and says so. A row on a billing arm
    whose usage never came back contributes nothing to any total and makes them
    all declared lower bounds. Collapsing the two would let an unpriceable run
    read as a free one.
    """
    audit = audit_spend(
        [
            ("google", "gemini-3.1-flash-lite", 9_000, 2_600, None),
            ("google", "gemini-3.1-flash-lite", None, None, None),
            ("openai", "gpt-5.6-luna", 10_000, 1_000, None),
            ("openai", "gpt-5.6-luna", None, 1_000, None),
            ("openai", "a-model-nobody-priced", 10_000, 1_000, None),
        ],
        (),
    )

    assert audit.proven_free_calls == 2
    assert audit.cash_calls == 1
    assert audit.unknown_calls == 2
    assert not audit.complete
    assert audit.cash_usd == pytest.approx((10_000 * 0.20 + 1_000 * 1.20) / 1_000_000)
    assert "LOWER BOUND" in audit.describe()
    assert len(audit.unknown_arms) == 2


def test_the_gemini_history_is_proven_free_from_its_own_provenance() -> None:
    """Derived, not backfilled, and that is the smaller mechanism.

    Every Stage 0 row carries `provider` and `model`, which is exactly what a
    price is keyed by. Writing a computed cost back into `cost_usd` would bake
    today's observation into a historical row with nothing to say which rows had
    believed it; deriving on read stays correct when an observation is
    corrected.
    """
    audit = audit_spend([("google", "gemini-3.1-flash-lite", 8_000, 2_000, None)] * 83, ())

    assert audit.cash_usd == 0.0
    assert audit.economic_usd == 0.0
    assert audit.proven_free_calls == 83
    assert audit.complete
    assert audit.remaining_cash_usd == PERSONAL_ALPHA_PAID_CEILING_USD


# --- the three questions a prototype budget has to keep apart ---------------


def test_a_reasoning_arm_is_priced_as_its_provider_model() -> None:
    """The lookup defect the first Cerebras screen exposed.

    `llm_call.model` stores the ARM, because a benchmark row must name its
    reasoning setting. A price is keyed on the provider's model id: reasoning
    changes how many tokens are billed, never the rate per token. Reading the
    arm directly made eighteen rows UNPRICEABLE and blocked every paid run.
    """
    medium = audit_spend([("cerebras", "gpt-oss-120b@medium", 1_000_000, 1_000_000, None)], ())
    high = audit_spend([("cerebras", "gpt-oss-120b@high", 1_000_000, 1_000_000, None)], ())
    bare = audit_spend([("cerebras", "gpt-oss-120b", 1_000_000, 1_000_000, None)], ())

    assert medium.complete and high.complete and bare.complete
    assert medium.economic_usd == pytest.approx(0.35 + 0.75)
    assert medium.economic_usd == high.economic_usd == bare.economic_usd

    # A genuinely different model is a genuinely different price identity.
    other = audit_spend([("cerebras", "some-other-model@medium", 1_000, 1_000, None)], ())
    assert not other.complete
    assert other.unknown_calls == 1


def test_promotional_credit_is_an_economic_cost_and_not_a_cash_cost() -> None:
    """Two numbers, because the $10 ceiling governs only one of them.

    A trial grant makes the bill real and the maintainer's exposure zero. A
    report that collapsed them would either claim the work was free -- it is
    not, and the grant runs out -- or spend the personal ceiling on money
    nobody paid.
    """
    audit = audit_spend([("cerebras", "gpt-oss-120b@medium", 1_000_000, 1_000_000, None)], ())

    assert audit.economic_usd == pytest.approx(1.10)
    assert audit.promotional_usd == pytest.approx(1.10)
    assert audit.cash_usd == 0.0
    assert audit.promotional_calls == 1
    assert audit.cash_calls == 0
    assert audit.remaining_cash_usd == PERSONAL_ALPHA_PAID_CEILING_USD


def test_a_request_refused_before_inference_is_proven_unbilled() -> None:
    """The eighteen HTTP 402 attempts, reconciled without pretending.

    They reached Cerebras and were rejected at the gate: no tokens read, none
    generated, nothing billed. That is provable from the refusal itself, which
    is what separates them from a call whose usage merely went unreported --
    the second is UNKNOWN and blocks a paid run, the first is $0.00 and does
    not. Neither is a benchmark output.
    """
    refusal = (
        "cerebras could not be reached: Error code: 402 - {'message': 'Payment required "
        "to access this resource. Visit your billing tab.', 'type': "
        "'payment_required_error', 'param': 'quota', 'code': 'payment_required'}"
    )
    audit = audit_spend([("cerebras", "gpt-oss-120b@medium", None, None, refusal)] * 18, ())

    assert audit.refused_calls == 18
    assert audit.economic_usd == 0.0
    assert audit.cash_usd == 0.0
    assert audit.unknown_calls == 0
    assert audit.complete, "a proven refusal must not block a later authorisation"
    assert "18 refused before inference" in audit.describe()


def test_the_prototype_ceiling_is_one_number_for_the_whole_alpha() -> None:
    """Not per run, not per arm. Raising it is a diff somebody has to write."""
    assert PERSONAL_ALPHA_PAID_CEILING_USD == 10.00


def test_the_lost_cerebras_run_is_carried_without_inventing_its_rows() -> None:
    """Inference that provably happened and whose evidence does not exist.

    18 real requests, 137,878 input and 62,794 output tokens, $0.0954 of
    promotional credit -- and every attempt row discarded by the ledger defect
    migration 0006 repairs.

    Two wrong answers were available. Fabricating `llm_call` rows would put
    invented provenance in the one table whose whole purpose is recording what
    happened. Ignoring it would leave the audit silently short by a run. So the
    cost is carried, labelled, and the missing evidence is named as missing.
    """
    from career_agent.llm.pricing import LOST_RUNS, Funding

    assert len(LOST_RUNS) == 1, "a second entry means the ledger fix regressed"
    lost = LOST_RUNS[0]
    assert (lost.vendor, lost.arm, lost.calls) == ("cerebras", "gpt-oss-120b@medium", 18)
    assert (lost.input_tokens, lost.output_tokens) == (137_878, 62_794)
    assert lost.funding is Funding.PROMOTIONAL_CREDIT
    assert "UNEVALUATED" in lost.what_happened

    audit = audit_spend([])
    assert audit.lost_run_calls == 18
    assert audit.economic_usd == pytest.approx(0.0954)
    assert audit.promotional_usd == pytest.approx(0.0954)
    assert audit.cash_usd == 0.0, "promotional credit is not the maintainer's money"
    assert audit.remaining_cash_usd == PERSONAL_ALPHA_PAID_CEILING_USD
    assert "attempt rows were lost" in audit.describe()


def test_the_nemotron_free_endpoint_is_recorded_from_the_endpoint_not_the_model_page() -> None:
    """Recurring free, keyed to the exact `:free` slug and to nothing else.

    The suffix is part of the model id, not a tier flag: the paid Nemotron is a
    different id, and a row that covered both would price a paid call at zero.
    Read from `/models/<slug>/endpoints`, where the provider and the two rates
    are stated per endpoint, rather than from a model page that describes a
    family.
    """
    from career_agent.llm.pricing import Funding
    from career_agent.llm.quotas import observed_quota

    free = "nvidia/nemotron-3-super-120b-a12b:free"
    price = observed_price("openrouter", free)
    assert price is not None
    assert price.funding is Funding.FREE_TIER
    assert price.free
    assert price.input_per_mtok == 0.0
    assert price.output_per_mtok == 0.0
    assert price.promotional_credit_usd is None, "a grant would make this promotional, not free"
    assert price.observed_at == "2026-09-03"
    assert "endpoint metadata" in price.source

    assert observed_price("openrouter", "nvidia/nemotron-3-super-120b-a12b") is None

    # One account-level budget, seen from two models. The row exists per model
    # because the table is keyed that way; the numbers are shared, and the
    # source says so rather than leaving a reader to assume 50 requests a day
    # each.
    quota = observed_quota("openrouter", free)
    glm = observed_quota("openrouter", "z-ai/glm-5.2:free")
    assert quota is not None and glm is not None
    assert quota.requests_per_day == glm.requests_per_day == 50
    assert "account-level" in quota.source

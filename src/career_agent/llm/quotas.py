"""Rate limits we have OBSERVED, on a particular account, on a particular day.

Not a table of vendor facts. That distinction is the whole reason this module
exists apart from the adapters.

WHY THIS IS NOT IN `vendors/google.py`
--------------------------------------
It was, briefly, as `FREE_TIER_QUOTA` -- a module constant beside `VENDOR` and
the thinking-budget table, reached through a `published_quota(vendor)` lookup
keyed by vendor name, and reported to the user as "the published ceiling for
google". Every part of that framing was wrong in the same way:

  * Gemini's free-tier limits are **per model**, not per vendor.
  * They differ **per project**: an AI Studio key, a Cloud project and a paid
    tier are three different sets of numbers behind one vendor name.
  * They **change**, without any notification reaching this repository.
  * And they were never *published* to us as a contract -- they were read off
    one account's console on one day.

A hardcoded vendor-level constant states none of that and implies all of its
opposites. The failure mode is specific and quiet: someone with a different
project runs this on limits that are not theirs, the guard passes, and the
vendor refuses mid-run -- or worse, does not, and the run silently overruns a
quota the tool was confident about.

WHAT REPLACES IT
----------------
An observation carries its own provenance: what it applies to, where it came
from, and when it was read. The runner's ceilings are **caller-supplied**; an
observation recorded here is only the default for exactly the (vendor, model)
pair it was observed on, and the preflight says so on the line where it is used.
A pair with no observation is not guessed at -- the caller is asked for their
own numbers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class QuotaLimits:
    """Rate limits believed to apply to one account, for one model, on one date.

    `source` and `observed_at` are not documentation. They are the fields that
    stop this being read as a vendor fact, and they are printed wherever the
    numbers are used, so nobody has to open this file to find out whose limits
    a run is pacing against.
    """

    requests_per_minute: int
    tokens_per_minute: int
    #: Informational. Nothing paces against a daily figure -- a run either fits
    #: in a day's allowance or it is planned across two.
    requests_per_day: int | None = None
    #: True when this tier costs nothing. Recorded because "we ran it for free"
    #: is a claim a report should be able to make from data rather than memory.
    free: bool = False
    source: str = "unrecorded"
    observed_at: str = "unknown"

    def describe(self) -> str:
        """One line naming whose limits these are and when they were read."""
        tier = "free tier" if self.free else "tier not recorded as free"
        return f"{self.source}, {tier}, observed {self.observed_at}"


#: Limits read from a console, keyed by the (vendor, model) pair they were read
#: for. Every entry is a dated observation about one account. Adding a row is
#: recording what someone saw; it is not discovering a vendor's policy.
#:
#: **These may be stale.** They default the runner's ceilings and nothing else,
#: and `--ceiling-rpm` / `--ceiling-tpm` override them without touching this
#: file -- which is what a different project, a different tier or a changed
#: limit is supposed to do.
OBSERVED_QUOTAS: dict[tuple[str, str], QuotaLimits] = {
    # Read off this maintainer's Cerebras dashboard, not off documentation.
    # The account publishes FOUR token limits and only the tightest pair can be
    # paced against: total 90,000/min and 3,000,000/day, uncached 30,000/min and
    # 1,000,000/day. Everything this benchmark sends is uncached -- 27 distinct
    # postings, each seen once -- so the uncached pair is the real ceiling and
    # pacing against the total one would overrun it threefold.
    #
    # Also observed: 131,000 context, 40,000 max completion tokens, 2,400
    # requests/day. The daily request figure is informational here; at 5 RPM a
    # 52-call benchmark cannot approach it.
    ("cerebras", "gpt-oss-120b"): QuotaLimits(
        requests_per_minute=5,
        tokens_per_minute=30_000,
        requests_per_day=2_400,
        # `free` is about the QUOTA, not the bill. These limits cost nothing to
        # be subject to; the inference under them is billed and absorbed by a
        # promotional grant. `pricing.py` holds that half -- recording it here
        # too is how the first screen came to believe a paid model was free.
        free=True,
        source="this maintainer's Cerebras dashboard, uncached limits "
        "(Team org, Free Trial ACTIVE; inference itself is billed -- see pricing.py)",
        observed_at="2026-09-03",
    ),
    # OpenRouter publishes request limits for `:free` variants and no token
    # limit. 50 requests/day is the figure for an account that has never
    # purchased credits; it rises to 1,000 only after $10 of credits has been
    # bought all-time, which is the entire Personal Alpha paid budget.
    #
    # `tokens_per_minute` is this repository's own conservative stand-in, not an
    # observation: 20 requests/min against our largest call would draw ~215,000
    # tokens/min from providers whose limits we cannot see. Recorded as a
    # ceiling we impose on ourselves.
    ("openrouter", "z-ai/glm-5.2:free"): QuotaLimits(
        requests_per_minute=20,
        tokens_per_minute=200_000,
        requests_per_day=50,
        free=True,
        source="OpenRouter free-tier limits documentation; TPM is our own "
        "self-imposed ceiling, not an observation",
        observed_at="2026-09-03",
    ),
    # The third view of the SAME account-level OpenRouter budget. See the note
    # on the Nemotron row below: 50 requests a day is shared across every
    # `:free` slug this account calls, not granted per model.
    ("openrouter", "minimax/minimax-m3:free"): QuotaLimits(
        requests_per_minute=20,
        tokens_per_minute=200_000,
        requests_per_day=50,
        free=True,
        source="OpenRouter free-tier limits documentation (account-level, shared "
        "across every :free slug); TPM is our own self-imposed ceiling, not an observation",
        observed_at="2026-09-03",
    ),
    # The same OpenRouter free-tier policy the GLM row records, keyed to a
    # second model because this table is keyed by (vendor, model) and a row is
    # an observation rather than a rule. The numbers are account-level and the
    # `source` says so: 50 requests/day is what an account that has never
    # purchased credits gets, and it is shared across every `:free` slug that
    # account calls -- so this row and the GLM row above describe ONE budget
    # seen from two models, not two budgets.
    #
    # `tokens_per_minute` is again our own conservative ceiling and not an
    # observation. OpenRouter publishes no token limit for `:free` variants.
    ("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"): QuotaLimits(
        requests_per_minute=20,
        tokens_per_minute=200_000,
        requests_per_day=50,
        free=True,
        source="OpenRouter free-tier limits documentation (account-level, shared "
        "across every :free slug); TPM is our own self-imposed ceiling, not an observation",
        observed_at="2026-09-03",
    ),
    # GEMMA 4 ON THE GEMINI API, read off this maintainer's AI Studio project on
    # 2026-09-03. Both ids showed the same three numbers; both are recorded
    # separately anyway, because "the console showed the same values" is an
    # observation about two models and copying one row to the other would make
    # it an assumption about a family.
    #
    # TPM is the binding limit here, and it is not obvious from the request
    # count: 30 requests/minute against a 16,000-token ceiling means barely one
    # description call per minute, since one is ~9,400 input tokens. A run
    # paced on RPM alone would cross TPM on its second call. That is exactly
    # the asymmetry `pacing.RateLimiter` enforces both ceilings for.
    #
    # There is no TPD here because the console does not expose one. Absent is
    # not zero and not unlimited: `QuotaLimits` has no daily-token field, so
    # nothing can read a number that nobody saw.
    ("google", "gemma-4-31b-it"): QuotaLimits(
        requests_per_minute=30,
        tokens_per_minute=16_000,
        requests_per_day=14_400,
        free=True,
        source="this maintainer's Google AI Studio project -> Rate Limits "
        "(TPD not exposed in the console)",
        observed_at="2026-09-03",
    ),
    ("google", "gemma-4-26b-a4b-it"): QuotaLimits(
        requests_per_minute=30,
        tokens_per_minute=16_000,
        requests_per_day=14_400,
        free=True,
        source="this maintainer's Google AI Studio project -> Rate Limits "
        "(TPD not exposed in the console)",
        observed_at="2026-09-03",
    ),
    ("google", "gemini-3.1-flash-lite"): QuotaLimits(
        requests_per_minute=15,
        tokens_per_minute=250_000,
        requests_per_day=500,
        free=True,
        source="this maintainer's Google AI Studio project",
        observed_at="2026-09-03",
    ),
}


def observed_quota(vendor: str, model: str) -> QuotaLimits | None:
    """What we recorded for this exact pair, or nothing.

    Deliberately does not fall back to the vendor, to a similar model, or to a
    plausible default. A limit guessed from a neighbouring model is a limit
    nobody observed, and it would be reported with the same confidence as one
    that was.
    """
    return OBSERVED_QUOTAS.get((vendor, model))

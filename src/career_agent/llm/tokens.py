"""How many tokens a planned run will consume, from measurements that exist.

This is a *planning* estimator, not a tokeniser. It answers one question -- "may
this run be paced at N requests per minute without crossing a tokens-per-minute
ceiling?" -- and it answers it before the first call, which is the only moment
the answer is useful.

WHY THERE IS NO TOKENISER HERE
------------------------------
`tiktoken` cannot reach its encoding host from this machine, which is why the
audit in `scripts/` is split in two. Adding it as a runtime dependency to pace a
rate limiter would trade a hard dependency and a network fetch for precision the
guard does not need: a limiter is safe when it over-estimates and unsafe when it
under-estimates, and nothing in between matters.

WHAT IS MEASURED AND WHAT IS ESTIMATED
--------------------------------------
The static half of every call -- the prompt and the schema -- is *measured*,
with `tiktoken o200k_base`, and recorded in M2.2. It is also the overwhelming
majority of the input: 87.8% of a description call and 98.8% of a provider call.
Those figures are constants below, keyed by prompt version, because a version
whose token count nobody measured must not be silently guessed at.

The dynamic half -- the posting text, the observation block -- is estimated from
characters at `CHARS_PER_TOKEN`.

WHY THE RATIO IS 4.0 WHEN THE MEASUREMENT SAYS 5.34
---------------------------------------------------
Three measured (characters, tokens) pairs exist in this repository:

    description_v6 prompt      25,665 chars   6,484 tokens   3.96
    description schema (JSON)   8,768 chars   2,042 tokens   4.29
    corpus median posting       5,811 chars   1,088 tokens   5.34

Real posting text is the *least* dense of the three, and it is the only text
this ratio is ever applied to. Using 4.0 therefore over-estimates a description
body by about a third, deliberately: this number guards a quota ceiling, and a
guard that under-estimates is not a guard. Every figure it produces is an upper
bound and is reported as one.
"""

import math
from dataclasses import dataclass

from career_agent.llm.client import Family
from career_agent.llm.requests import BuiltRequest

#: Characters per token. Deliberately below every ratio measured on this
#: corpus, so an estimate is an upper bound. See the module docstring.
CHARS_PER_TOKEN = 4.0

#: Measured with `tiktoken o200k_base` -- `scripts/token_audit_count.py`,
#: recorded in `docs/architecture/milestone-2-2-token-economics.md`. A prompt
#: version absent here has never been measured, and is estimated from its
#: characters rather than assumed to match a neighbour.
MEASURED_PROMPT_TOKENS: dict[str, int] = {
    "description_v1": 2_717,
    "description_v2": 3_299,
    "description_v3": 3_987,
    "description_v4": 4_528,
    "description_v5": 5_823,
    "description_v6": 6_484,
    "description_v7": 7_660,
    "description_v8": 7_740,
    "provider_v2": 822,
    "provider_v3": 921,
}

#: Likewise measured. The description schema has been byte-identical at 8,768
#: characters since v1, across five prompt versions and two new dimensions --
#: which is the row-based transport paying for itself.
MEASURED_SCHEMA_TOKENS: dict[Family, int] = {
    Family.DESCRIPTION: 2_042,
    Family.PROVIDER: 968,
}

#: Measured from the 53 real answers the assisted development run produced, not
#: assumed. Output is not charged against a tokens-per-minute input ceiling, but
#: it is two thirds of the eventual bill at every paid candidate, so a run
#: report that omitted it would understate what the run cost.
MEASURED_OUTPUT_TOKENS: dict[Family, int] = {
    Family.DESCRIPTION: 2_672,
    Family.PROVIDER: 150,
}

#: What one call's output is assumed to cost when it has to be priced before it
#: exists, or after a vendor declined to meter it. `max_output_tokens` on the
#: request, not a median: a spend guard that used the average would authorise a
#: call the vendor is free to bill at three times that. Nothing paces on this;
#: it only ever makes a ceiling stricter.
MEASURED_OUTPUT_CEILING = 8_000


def from_characters(text: str) -> int:
    """An upper-bound token count for a string, rounded up."""
    return math.ceil(len(text) / CHARS_PER_TOKEN)


@dataclass(frozen=True)
class TokenEstimate:
    """What one call is expected to consume, and how much of that was measured."""

    family: Family
    static: int
    dynamic: int
    output: int
    #: False when the prompt version has no measured figure and the static half
    #: had to be estimated from characters too. Reported rather than hidden: a
    #: planning number nobody measured should say so.
    static_measured: bool

    @property
    def input(self) -> int:
        return self.static + self.dynamic

    @property
    def total(self) -> int:
        return self.input + self.output


def estimate_request(built: BuiltRequest) -> TokenEstimate:
    """Estimate one built request, measured where a measurement exists.

    The schema is counted whether or not this arm asks the vendor to enforce it.
    An arm running without structured output sends less than this predicts,
    which is the safe direction for a ceiling guard to be wrong in.
    """
    family = built.request.family
    prompt_tokens = MEASURED_PROMPT_TOKENS.get(built.prompt_version)
    schema_tokens = MEASURED_SCHEMA_TOKENS.get(family)

    if prompt_tokens is None or schema_tokens is None:
        static = from_characters(built.request.system) + from_characters(str(built.request.schema))
        measured = False
    else:
        static = prompt_tokens + schema_tokens
        measured = True

    return TokenEstimate(
        family=family,
        static=static,
        dynamic=from_characters(built.request.user),
        output=MEASURED_OUTPUT_TOKENS.get(family, 0),
        static_measured=measured,
    )

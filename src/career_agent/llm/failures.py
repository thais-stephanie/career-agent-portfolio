"""What kind of failure this is, and therefore what may be done about it.

One classifier, three callers, one precedence rule. It exists because the first
Cerebras screen got the answer wrong in the most expensive available way.

WHAT WENT WRONG
---------------
Cerebras refused every request with HTTP 402:

    {'message': 'Payment required to access this resource.',
     'type': 'payment_required_error', 'param': 'quota', ...}

The retry classifier matched the substring `quota` -- there because the vendor
named the *parameter*, not because anything was rate limited -- and retried a
permanent billing refusal. Nine useful access failures became eighteen requests,
and every one of them was guaranteed to fail before it was sent.

PRECEDENCE IS THE WHOLE DESIGN
------------------------------
Account-level markers are checked FIRST and win outright. A body may contain
both `payment_required` and `quota`, and only one of those readings can be
right: an account that cannot pay is not an account that is going too fast.
Waiting will not fix it, and retrying only doubles the evidence.

Transient markers are unchanged. A 429, a 503 and a capacity error still buy a
cooldown and a second attempt, because they still should.

WHY A THIRD MODULE
------------------
Three callers need the same answer and none may import the others: `live.py`
decides whether to retry, `extract.py` decides whether to abandon the arm, and
`pricing.py` decides whether a failed attempt could have been billed. A copy in
each is three chances to disagree about what a 402 means.
"""

import re
from enum import StrEnum


class Transport(StrEnum):
    """How far one attempt got, measured in HTTP and nothing else.

    Four rounds of evidence loss taught the same lesson twice over: an attempt
    is an EVENT with stages, and collapsing them loses the answer to the two
    questions anyone asks afterwards -- did this consume quota, and did the
    vendor see it?

    THE ONE DISTINCTION THAT KEEPS BEING COLLAPSED
    ----------------------------------------------
    "an HTTP response was received" and "a model produced an answer" are
    different facts, and this enum records the first one only. The OpenRouter
    canary stored a 429 -- a real HTTP response, from a named provider, with a
    provider error code and a Retry-After -- as `SENT_NO_RESPONSE`, because the
    adapter raised on it and no completion body existed. Two rows then said the
    same thing about two opposite events: a connection that died with nothing
    on the other end, and a provider that answered clearly and quickly.

    A 2xx, a 4xx and a 5xx are all responses. What they contain is somebody
    else's question.
    """

    #: The request never reached HTTP transport. Our own process failed while
    #: building or dispatching it. No quota, no vendor, and always our defect
    #: rather than theirs.
    NOT_SENT = "NOT_SENT"
    #: The HTTP request was sent and NO HTTP response was received: a connection
    #: reset, a timeout, a socket that closed with nothing on it. Quota may well
    #: be consumed. Nothing is known about what the vendor decided.
    SENT_NO_RESPONSE = "SENT_NO_RESPONSE"
    #: ANY HTTP response was received -- 2xx, 4xx or 5xx alike. It says the
    #: vendor answered, never that the answer was useful.
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"


#: How the OpenAI SDK renders a status error: `Error code: 429 - {body}`. The
#: only status marker read from prose anywhere in this repository, and read
#: narrowly on purpose.
#:
#: A three-digit number in a vendor's message proves nothing -- "quota 429 of
#: 500" is a sentence, and `param: 'quota'` already cost this project eighteen
#: requests once. This prefix is a documented SDK format that is only produced
#: when an `APIStatusError` was constructed FROM AN HTTP RESPONSE, so matching
#: it is reading a structured field that happens to have been flattened into a
#: string on the way here.
_SDK_STATUS_MARKER = re.compile(r"\berror code:\s*(\d{3})\b", re.IGNORECASE)


def http_status_in(message: str) -> int | None:
    """The HTTP status a stored error message PROVES, or None.

    None means "not provable from this text", never "no response arrived".
    """
    found = _SDK_STATUS_MARKER.search(message)
    return int(found.group(1)) if found else None


def transport_from_error(message: str) -> Transport:
    """How far an attempt got, from its message alone.

    The conservative half of the rule: a message that proves a status proves a
    response, and one that does not leaves the recorded state alone. This is
    the fallback for adapters that still flatten their vendor's exception into
    a string; an adapter that carries the response through structurally does
    not need it.
    """
    if http_status_in(message) is not None:
        return Transport.RESPONSE_RECEIVED
    return Transport.SENT_NO_RESPONSE


def observed_transport(recorded: str | None, error: str | None) -> Transport | None:
    """What a stored row's own evidence says about it, or None to leave it be.

    Written for the rows that predate the corrected semantics, and deliberately
    unable to do anything except upgrade a row whose own error text proves an
    HTTP status. It never downgrades, never guesses at `UNRECORDED`, and never
    reads anything but the row it was handed.
    """
    if recorded == Transport.RESPONSE_RECEIVED.value:
        return None
    if error and http_status_in(error) is not None:
        return Transport.RESPONSE_RECEIVED
    return None


class FailureKind(StrEnum):
    """Why a request produced no answer."""

    #: The account cannot make this request at all: no credit, no valid
    #: credential, no permission. Every remaining request would fail the same
    #: way, so retrying is waste and continuing is worse.
    ACCOUNT_ACCESS = "ACCOUNT_ACCESS"
    #: The vendor could not answer *now*. A cooldown and one retry are correct.
    TRANSIENT = "TRANSIENT"
    #: No endpoint can serve this exact combination of parameters. Arm-wide and
    #: permanent for the arm as configured -- and, unlike ACCOUNT_ACCESS, fixed
    #: by changing the REQUEST rather than the account.
    ROUTING_INCOMPATIBLE = "ROUTING_INCOMPATIBLE"
    #: Anything else: a bad request, an unparseable answer, a model refusal.
    #: Not retried, not fatal to the arm.
    OTHER = "OTHER"


#: Checked FIRST, and deliberately narrow. Every entry here means "this account
#: cannot make this request", never "not right now".
#:
#: `insufficient_quota` is the entry that proves the precedence matters: it is
#: OpenAI's *billing* error, it contains the word `quota`, and reading it as a
#: rate limit would retry a wallet that is empty.
ACCOUNT_ACCESS_MARKERS: tuple[str, ...] = (
    "402",
    "payment required",
    "payment_required",
    "payment_required_error",
    "billing_required",
    "billing required",
    "insufficient credit",
    "insufficient_credit",
    "insufficient credits",
    "insufficient_quota",
    "401",
    "unauthorized",
    "unauthorised",
    "invalid api key",
    "invalid_api_key",
    "invalid authentication",
    "account suspended",
    "account_suspended",
    "permission_denied",
)

#: Checked only when nothing above matched. Unchanged from the behaviour the
#: Gemini runs relied on: Stage 0 Take 2 bought four cooldowns from 503s and
#: recovered every one of them.
TRANSIENT_MARKERS: tuple[str, ...] = (
    "429",
    "rate limit",
    "ratelimit",
    "resource_exhausted",
    "resource exhausted",
    "quota",
    "too many requests",
    "unavailable",
    "503",
    "overloaded",
    "timeout",
    "timed out",
    "temporarily",
)


#: Evidence that a router could not place a request at all, as opposed to a
#: vendor answering badly. Deliberately the ROUTING language and not the status
#: code: a bare 404 is an ordinary "no such thing" and must stay OTHER, while
#: these strings only appear when a router has filtered its candidates to zero.
#:
#: Observed 2026-09-03 from OpenRouter, with `failed_routing_step: "Filter by
#: Parameters"` and a funnel showing one endpoint reduced to none.
ROUTING_MARKERS: tuple[str, ...] = (
    "no endpoints found that can handle the requested parameters",
    "failed_routing_step",
    "no allowed providers are available",
)


def classify(message: str) -> FailureKind:
    """Read one error message. Account-level first, always.

    Substring matching on a vendor's prose is not elegant, and it is what is
    available: the adapters flatten every vendor's exception into one
    `LLMUnavailable` string precisely so that nothing downstream has to import a
    vendor SDK to understand a failure. The precedence rule is what makes the
    imprecision safe -- a false TRANSIENT costs a wasted retry, and this
    guarantees the one case that mattered cannot produce one.
    """
    lowered = message.lower()
    if any(marker in lowered for marker in ROUTING_MARKERS):
        return FailureKind.ROUTING_INCOMPATIBLE
    if any(marker in lowered for marker in ACCOUNT_ACCESS_MARKERS):
        return FailureKind.ACCOUNT_ACCESS
    if any(marker in lowered for marker in TRANSIENT_MARKERS):
        return FailureKind.TRANSIENT
    return FailureKind.OTHER


def is_retryable(message: str) -> bool:
    """Only a transient failure is worth a second attempt."""
    return classify(message) is FailureKind.TRANSIENT


def refused_before_inference(message: str) -> bool:
    """Whether this failure proves the vendor never ran the model.

    An account-access refusal is rejected at the gate, and a routing refusal
    never reaches a provider at all: no tokens are read, none are generated, and
    nothing is billed. That is what makes the eighteen 402 attempts provably
    free rather than merely unmeasured -- and the distinction matters, because
    `pricing.audit_spend` treats unmeasured as UNKNOWN and refuses to authorise
    a paid run against it.
    """
    return classify(message) in {
        FailureKind.ACCOUNT_ACCESS,
        FailureKind.ROUTING_INCOMPATIBLE,
    }


#: Failures that will repeat identically on every remaining posting. Retrying is
#: waste and continuing is worse: the first Cerebras screen bought nine copies
#: of one billing refusal, and the first GLM screen nine copies of one TypeError.
ARM_WIDE = frozenset({FailureKind.ACCOUNT_ACCESS, FailureKind.ROUTING_INCOMPATIBLE})


def is_arm_wide(message: str) -> bool:
    """Whether this failure condemns the arm rather than the posting."""
    return classify(message) in ARM_WIDE


class RunHalted(RuntimeError):
    """A run must stop, for a reason that is not about this posting.

    THESE LIVE HERE SO THE ATTEMPT CAN BE SAVED BEFORE THE RUN ENDS
    ---------------------------------------------------------------
    They were defined in `pipeline/live.py`, which `pipeline/extract.py` cannot
    import. So they propagated out of `extract_job` uncaught, taking the
    outcome -- and every attempt row it had already recorded -- with them.

    That cost the OpenRouter canary its entire diagnosis: one real HTTP request
    was made, it failed, the retry hit the budget, and the record of what the
    vendor said was discarded on the way out. It was the third time a "stop the
    run" path lost evidence, each in a different place, and each written to stop
    without being written to save first.

    Now `_ask` catches them, marks the outcome and returns normally. The caller
    stores what happened and then stops.
    """


class CallBudgetExhausted(RunHalted):
    """The authorised number of live calls is spent."""


class SpendBudgetExhausted(RunHalted):
    """The authorised money is spent."""


class ArmUnusable(RunHalted):
    """The account cannot use this arm at all: no credit, no credential, no permission."""

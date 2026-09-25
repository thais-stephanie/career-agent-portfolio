"""The vendor-neutral LLM port.

Everything above this line speaks in project types. Everything below it -- one
adapter per vendor -- speaks the vendor's dialect and is the only place a vendor
SDK may be imported.

The test of whether this boundary is real is the same one M1B applied to the
provider abstraction: adding a second vendor should cost one new file and one
registry entry, and touch nothing else. If a future adapter forces a change to
`domain/`, `pipeline/` or the transport models, the boundary leaked.

WHY REASONING CONFIGURATION LIVES HERE
--------------------------------------
`ModelConfig` carries the reasoning/thinking setting alongside the identifier,
because a model benchmarked at maximum reasoning and the same model at minimum
are two different candidates wearing one name. Recording only "we used Sonnet 5"
would make a benchmark irreproducible and its cost comparison meaningless.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class Family(StrEnum):
    """Which extraction call this is.

    The two production families are deliberately not interchangeable: they see
    different sources, cite different evidence kinds, and cache independently.
    ROLE and CONDITIONS exist for the benchmark arm that tests whether splitting
    the description improves late-posting extraction.
    """

    DESCRIPTION = "description"
    PROVIDER = "provider"
    ROLE = "role"
    CONDITIONS = "conditions"
    #: Semantic Search Fit interpretation (`career_agent.semantic`). Not an
    #: extraction family: it never produces a fingerprint.
    SEMANTIC = "semantic"


class Runner(StrEnum):
    """How a completion was obtained, as distinct from who produced it.

    `vendor` and `model` say who answered. This says how the answer was
    reached, and the two are independent -- the same identifier can arrive
    through a paid API, a recorded fixture, or an assisting agent working from
    the exact production prompt during development.

    It is data rather than a branch. Nothing in the pipeline may read this to
    decide what to do; it exists so that a cost report can separate three
    populations that must never blend, and so that an accuracy claim can never
    be made about a model that never ran.
    """

    #: A real, billable vendor call.
    PRODUCTION_API = "PRODUCTION_API"
    #: A recorded response replayed offline. Free, deterministic.
    REPLAY = "REPLAY"
    #: Development extraction produced through the Cowork harness. Free.
    COWORK_ASSISTED = "COWORK_ASSISTED"
    #: A test double. Never reaches a real database.
    FAKE = "FAKE"


class StructuredOutput(StrEnum):
    """How hard a vendor is asked to hold this request to the schema.

    Not a preference. It is a property of the route, and it changes the request
    the model receives, so it belongs to the benchmark arm's identity rather
    than to a runtime toggle.

    `PLAIN_JSON` is not a degraded mode we invented to be lenient. Our parser,
    transport validator, evidence verifier and assembler run identically in all
    three -- they were always the layer that decides whether an answer is
    usable, and a vendor guarantee only ever saved them a retry. What changes is
    who catches a malformed answer first, which is exactly what a benchmark
    should be able to measure.

    Three modes, three guarantees, and the middle one is not a rounding of
    either neighbour:

        STRICT_SCHEMA   this shape, enforced
        JSON_OBJECT     one valid JSON document, shape unenforced
        PLAIN_JSON      nothing enforced; the instructions are the only ask

    Not every vendor offers all three. An adapter that cannot express one
    refuses rather than sending the nearest thing it can -- silently sending a
    weaker request would label a benchmark row with a guarantee it never had.
    """

    #: The vendor enforces the schema and cannot return anything else.
    STRICT_SCHEMA = "STRICT_SCHEMA"
    #: The vendor guarantees ONE valid JSON document and nothing about its
    #: shape. Our schema is not sent.
    #:
    #: A third mode rather than a softer STRICT_SCHEMA, because it answers a
    #: different question: "can this model produce our shape unaided, given a
    #: guarantee that the envelope is JSON". Two routes have now shown why the
    #: envelope alone is worth buying -- Gemma appended a markdown fence after a
    #: complete document, and Nemotron opened with two braces. Neither is a
    #: schema failure; both are envelope failures, and a JSON-mode guarantee is
    #: aimed exactly at them.
    #:
    #: It exists because some free endpoints advertise `response_format` and NOT
    #: `structured_outputs`. Reading that as "use PLAIN_JSON" would give up a
    #: guarantee the route actually offers.
    JSON_OBJECT = "JSON_OBJECT"
    #: The vendor is asked for JSON in the prompt and nowhere else. No
    #: `response_format` is sent at all.
    PLAIN_JSON = "PLAIN_JSON"
    #: The family is declared as a callable tool and the answer arrives as the
    #: arguments of a function call, in its own response part.
    #:
    #: A DIFFERENT CHANNEL, NOT A STRONGER HINT
    #: ----------------------------------------
    #: The three modes above all shape one thing: the final text. On a route
    #: that accepts `responseSchema` without enforcing it, that distinction
    #: collapses -- Gemma ended a complete document with a markdown fence under
    #: four different prompts and both families, which constrained decoding
    #: cannot do. Weakening the ask to JSON_OBJECT would have removed the only
    #: field that might have helped, so it was never tried.
    #:
    #: A function call moves the answer out of the text entirely. Arguments come
    #: back as a structured object in a `functionCall` part, and a model that
    #: also wants to write prose has somewhere to put it that is not our
    #: document. That is why this is a fourth mode and not a fourth strength:
    #: the failure it addresses was never about how hard we asked.
    #:
    #: It guarantees nothing by itself. Whether a given route honours the
    #: channel is a measurement, and a text-only answer under this mode is a
    #: contract failure like any other.
    FUNCTION_CALL = "FUNCTION_CALL"


@dataclass(frozen=True)
class ModelConfig:
    """One benchmark arm: a model, at a setting, from a vendor.

    ``structured_output`` is the arm's default enforcement mode; ``per_family``
    overrides it for one family.

    THE OVERRIDE IS NOT A CONVENIENCE
    ---------------------------------
    Cerebras enforces a strict schema of at most 5,000 characters. Our provider
    schema is 4,038 and our description schema is 8,536, so on that route one
    family can be enforced and the other cannot -- and the honest arm is the
    best production-usable configuration of the route, not the weaker of the two
    applied to both for symmetry. Weakening the provider family to match would
    measure a configuration nobody would ship.

    A tuple of pairs rather than a dict because the config is frozen and hashed.
    """

    vendor: str
    identifier: str
    reasoning: str | None = None
    structured_output: StructuredOutput = StructuredOutput.STRICT_SCHEMA
    #: `((family, mode), ...)`. Empty means every family uses the default.
    per_family: tuple[tuple["Family", StructuredOutput], ...] = ()
    max_output_tokens: int = 8000
    temperature: float = 0.0

    def output_mode(self, family: "Family") -> StructuredOutput:
        """The enforcement this arm applies to one family."""
        for candidate, mode in self.per_family:
            if candidate is family:
                return mode
        return self.structured_output

    @property
    def arm(self) -> str:
        """Stable label for a benchmark row and for `llm_call.model`.

        Deliberately does NOT carry the output mode. The arm names *who* was
        asked; the mode is per family, so folding it in here would give one run
        two arm labels and make every per-model report ambiguous. The mode
        reaches the cache through `static_digest`, which is where a change to
        the request itself belongs -- see `llm/cache.py`.
        """
        return f"{self.identifier}@{self.reasoning}" if self.reasoning else self.identifier


#: How `ModelConfig.arm` joins an identifier to a reasoning setting. One
#: character, defined once, so the parser below and the builder above can never
#: drift apart.
ARM_SEPARATOR = "@"


def identifier_from_arm(arm: str) -> str:
    """The provider's own model id, recovered from a stored arm label.

    `llm_call.model` records the ARM, because a benchmark row has to say which
    reasoning setting produced it. A *price* is keyed on the provider's model id
    alone: reasoning changes how many tokens are billed, never the rate per
    token, so `gpt-oss-120b@medium` and `gpt-oss-120b@high` are one price and
    two arms.

    Deliberately not a normaliser. It splits on the one separator this
    repository writes and returns everything before it -- an identifier
    containing no separator is returned unchanged. Nothing here lowercases,
    strips whitespace, or tries to recognise a model it has not been given.
    """
    return arm.split(ARM_SEPARATOR, 1)[0]


@dataclass(frozen=True)
class LLMRequest:
    """What we ask for, in terms no vendor invented.

    ``schema`` is a plain JSON Schema dict produced from a transport model.
    Adapters translate it into whatever their vendor calls the same idea.
    """

    family: Family
    system: str
    user: str
    schema: dict[str, Any]
    schema_name: str
    #: This request's cache identity, carried so the request is self-describing.
    #:
    #: A replay or Cowork runner needs to know *which* recorded answer belongs
    #: to the request in its hand. Passing that out of band -- setting a field
    #: on the client before each call -- works until two calls interleave, and
    #: then serves one job's answer to another with nothing to show for it.
    cache_key: str = ""


@dataclass(frozen=True)
class LLMResponse:
    """What came back, before anyone believed any of it.

    ``raw_text`` is preserved exactly as received and is written to `llm_call`
    *before* parsing. That ordering is the whole point: without it, the only
    record of a bad response is our cleaned-up interpretation of it, and the
    question "what did the model actually say?" becomes unanswerable.
    """

    raw_text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    refusal: str | None = None
    #: The vendor's own response structure, captured BEFORE content extraction.
    #:
    #: `raw_text` is the adapter's *interpretation* of a response, and the
    #: Cerebras screen proved that is not enough to debug one: it returned empty
    #: content while billing for 62,794 output tokens, and nothing survived that
    #: could say where the answer had gone. This is the layer below that
    #: judgement.
    #:
    #: Built from the response body alone -- never from the request, its headers
    #: or a client -- so no credential can reach it by construction.
    envelope: dict[str, Any] | None = None
    #: Every function call the model made, as `(name, arguments)`. Recorded for
    #: every mode, because "it called nothing" and "it called the wrong thing"
    #: are different diagnoses and only this can tell them apart.
    function_calls: tuple[tuple[str, dict[str, Any]], ...] = ()
    #: Why this response violates the transport's own contract, if it does.
    #:
    #: Distinct from `refusal`, which says the model declined to answer, and
    #: from a parse failure, which says the answer was not the shape we asked
    #: for. This says the answer did not arrive through the channel the request
    #: established -- a text answer where a function call was required, two
    #: calls where one was, a call by another name. The model produced content;
    #: it just did not honour the transport, and a run has to be able to report
    #: that as its own category rather than as bad JSON.
    contract_error: str | None = None


class LLMError(RuntimeError):
    """A call failed in a way the caller may need to distinguish."""


class LLMUnavailable(LLMError):
    """The vendor could not be reached, or refused for reasons unrelated to us.

    Kept distinct from a bad response for the same reason a provider timeout is
    kept distinct from a 404 in collection: a transport failure says nothing
    about the posting, and must never be recorded as though the model had an
    opinion about it.
    """


class LLMErrorResponse(LLMUnavailable):
    """The vendor ANSWERED, and the answer was an HTTP error.

    A 429, a 402, a 404 and a 503 are responses. They carry a status line, they
    often carry a structured body naming the provider that refused and how long
    to wait, and they prove the request reached the far end -- which is exactly
    what `SENT_NO_RESPONSE` denies.

    It stayed invisible for as long as it did because the two facts sound alike
    in English: no *model answer* was produced, so the row said no *response*
    was received. The OpenRouter canary recorded a 429 from provider Decart,
    with `retry_after_seconds: 5`, as an attempt nobody had heard back from.

    A subclass of `LLMUnavailable` rather than a sibling, so every existing
    caller keeps treating it as "the vendor could not answer" and only the code
    that cares about the difference has to know there is one. The retry
    classifier is unchanged: it reads the message, and the message is the same.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        retry_after_seconds: float | None = None,
        envelope: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        #: The HTTP status line. Present whenever the SDK gave us one.
        self.status = status
        #: The minimum cooldown the PROVIDER asked for, in seconds, read from a
        #: structured field or a `Retry-After` header and from nowhere else. A
        #: floor, never a ceiling: local policy may wait longer, never less.
        self.retry_after_seconds = retry_after_seconds
        #: The credential-safe error envelope. Built from the response body and
        #: an allow-listed header, so no key can reach it by construction.
        self.envelope = envelope


class LLMNotSent(LLMError):
    """The request never reached HTTP. Nothing was asked, so nothing was used.

    The distinction quota and cost accounting rest on. A GLM-5.2 screen recorded
    18 failures and consumed zero of a 50-request daily allowance, because the
    SDK raised `TypeError` on an unknown keyword argument before building a
    request -- and a report that called those "18 API calls" would have been
    wrong about the one number a free tier is measured in.

    It is also always OUR defect rather than the vendor's: a request that could
    not be constructed will not construct on the next posting either. Treated as
    arm-fatal for that reason.
    """


class ReplayMiss(LLMError):
    """A recorded response was expected and none exists.

    Deliberately an error rather than a fallback. A replay client that quietly
    reached the network would turn a free, deterministic, offline test suite
    into an intermittent bill, and nobody would notice until the invoice.
    """


@runtime_checkable
class LLMClient(Protocol):
    """The whole port. One method, because one method is all the pipeline needs.

    Implementations: `AnthropicClient`, `OpenAIClient`, `GoogleClient`,
    `FakeClient`, `ReplayClient`. Only the first three import a vendor SDK, and
    a purity test asserts that no other module does.
    """

    #: For `llm_call.provider`. Not read by any branch -- nothing in the
    #: pipeline may behave differently because of it.
    vendor: str

    #: For `llm_call.runner`. Likewise recorded, never branched on. A client
    #: that lied here would put development output into the paid-benchmark
    #: bucket, which is the one accounting error this milestone cannot afford.
    runner: Runner

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        """Send one request. Raise `LLMUnavailable` if the vendor could not answer."""
        ...


@dataclass
class FakeClient:
    """Returns whatever a test told it to, and counts what it was asked.

    ``calls`` is the assertion surface for the cache tests: proving that an
    unchanged input costs zero client invocations means counting invocations,
    and a client that records them is simpler than a mock that guesses.
    """

    vendor: str = "fake"
    runner: Runner = Runner.FAKE
    responder: Callable[[LLMRequest, ModelConfig], LLMResponse] | None = None
    calls: list[tuple[Family, str]] = field(default_factory=list)

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        self.calls.append((request.family, config.identifier))
        if self.responder is None:
            return LLMResponse(raw_text="{}", model=config.identifier)
        return self.responder(request, config)

    @property
    def call_count(self) -> int:
        return len(self.calls)


@dataclass
class ReplayClient:
    """Serves recorded responses by cache key. Raises on anything unrecorded.

    This is what lets the full pipeline -- collect from recorded HTTP,
    fingerprint from recorded completions, verify, assemble -- run in CI
    offline, deterministically and free.

    `vendor` and `runner` are constructor arguments rather than constants
    because replay is a *mechanism*, not a source. The Cowork harness is
    exactly this client pointed at responses an assisting agent produced from
    the production prompt, and it labels itself `COWORK_ASSISTED` so that its
    output is never counted as a benchmark result. Reusing this class rather
    than writing a second one is deliberate: a parallel fake-model subsystem
    would be a second extraction path, and two paths drift.
    """

    responses: dict[str, LLMResponse] = field(default_factory=dict)
    vendor: str = "replay"
    runner: Runner = Runner.REPLAY
    calls: list[str] = field(default_factory=list)
    #: Fallback for callers that build a request without one. The request's own
    #: `cache_key` wins whenever it is set, which is always in the pipeline.
    next_key: str = ""

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        key = request.cache_key or self.next_key
        self.calls.append(key)
        if key not in self.responses:
            raise ReplayMiss(
                f"no recorded response for {key!r} ({request.family}, {config.identifier}). "
                "Record the fixture rather than allowing a live call: a replay client that "
                "falls back to the network turns a free test suite into an invoice."
            )
        return self.responses[key]

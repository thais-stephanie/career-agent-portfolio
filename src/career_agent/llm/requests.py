"""Turning one job into the requests that extract it.

This is the production path. Every runner -- a vendor adapter, the replay
client, the Cowork harness -- receives exactly what this module builds, which is
what makes those runners interchangeable rather than three implementations of
the same idea.

Two families, and the asymmetry between them is the topology working:

    description   the normalised posting text, and nothing else
    provider      the mapped location values, and nothing else

Neither can borrow from the other's source, because neither is given it.

WHY THE REQUEST AND ITS CACHE KEY ARE BUILT TOGETHER
----------------------------------------------------
A key computed somewhere other than where the request is assembled is a key
that will eventually describe a request nobody sends. Returning both from one
function makes the pair impossible to separate: whatever `BuiltRequest.request`
contains is what `BuiltRequest.cache_key` identifies, by construction.

THE ZERO-CALL RULE
------------------
`build_provider_request` returns `None` when nothing in the payload needs
interpreting. That is a real outcome and not an error -- a posting whose ATS
record holds only an employment type and a work model is fully resolved by
`domain/provider_values.py`, and paying ~1,800 tokens to have a model agree is
the waste that measurement exists to catch.
"""

import copy
from collections.abc import Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from career_agent.domain.provider_values import needs_a_model
from career_agent.llm.cache import (
    FamilyKey,
    description_key,
    observation_block,
    provider_key,
    static_digest,
)
from career_agent.llm.client import Family, LLMRequest, ModelConfig, StructuredOutput
from career_agent.llm.prompts import (
    DESCRIPTION_PROMPT_VERSION,
    PROVIDER_PROMPT_VERSION,
    load_prompt,
)
from career_agent.llm.routing import routing_identity
from career_agent.llm.tools import tool_digest
from career_agent.llm.transport import (
    PROVIDER_TRANSPORT_VERSION,
    TRANSPORT_SCHEMA_VERSION,
    TDescriptionFamily,
    TProviderFamily,
)

#: Names the vendor sees for each schema. Adapters pass them through to
#: whatever the vendor calls a tool or a response-format name.
DESCRIPTION_SCHEMA_NAME = "job_description_extraction"
PROVIDER_SCHEMA_NAME = "provider_geography_extraction"

#: The most posting text one description call will carry.
#:
#: Chosen against the corpus rather than guessed: the longest normalised
#: description among 18,549 open postings is 18,509 characters, so this is
#: 2.2x the observed maximum and fires on nothing we hold today. It exists so
#: that a pathological posting -- an employer pasting an entire handbook --
#: fails predictably instead of producing a surprise bill or a vendor error.
#:
#: **Changing this number is a prompt-version change.** It alters what the
#: model is shown, which is exactly the condition `prompts.py` writes down for
#: bumping `DESCRIPTION_PROMPT_VERSION`. The cache key cannot see the budget,
#: so lowering it without a bump would serve answers read from text the new
#: budget would have withheld. A test pins the value so the change is
#: deliberate.
MAX_DESCRIPTION_CHARS = 40_000

#: Fraction of the budget kept from the head when a posting overflows.
#: The remainder comes from the tail, because that is where eligibility,
#: work-authorisation and compensation language lives -- a head-only truncation
#: would reliably discard the highest-stakes third of a long posting.
HEAD_SHARE = 0.7

#: Marks the seam. Deliberately conspicuous: a quote the model composes across
#: the join will not appear in the stored full text and so will fail
#: verification, which is the safe direction for this failure to fall.
ELISION = "\n\n[... omitted: posting exceeds the extraction length budget ...]\n\n"


@dataclass(frozen=True)
class BuiltRequest:
    """One request, its cache identity, and the versions that produced both.

    The versions travel with the request because everything downstream needs
    them and none of it should re-derive them: `llm_call` stores them, the
    export harness writes them into its manifest, and the importer refuses a
    result whose versions do not match what it asked for.
    """

    request: LLMRequest
    cache_key: FamilyKey
    prompt_version: str
    transport_version: int
    #: True when the posting overflowed `MAX_DESCRIPTION_CHARS`. Recorded
    #: rather than silent: an extraction that read part of a posting must be
    #: distinguishable from one that read all of it.
    truncated: bool = False

    @property
    def model_visible_input(self) -> str:
        """Everything the model is given, as one string.

        The token estimator and the export manifest both need this, and both
        need it to be *the same* string the runner sends. Deriving it here
        rather than reassembling it at each call site is what keeps a cost
        estimate honest.
        """
        return f"{self.request.system}\n\n{self.request.user}"


# =========================================================================
# SCHEMAS
# =========================================================================


@lru_cache(maxsize=1)
def _description_schema_template() -> dict[str, Any]:
    return TDescriptionFamily.model_json_schema()


@lru_cache(maxsize=1)
def _provider_schema_template() -> dict[str, Any]:
    return TProviderFamily.model_json_schema()


def description_schema() -> dict[str, Any]:
    """A fresh copy of the description family's JSON Schema.

    Generated once and copied per call. Adapters legitimately rewrite the
    schema they are handed -- inlining `$defs`, adding a vendor's required
    `additionalProperties: false`, renaming the root -- and a shared mutable
    dict would let one vendor's rewrite leak into the next request.
    """
    return copy.deepcopy(_description_schema_template())


def provider_schema() -> dict[str, Any]:
    """A fresh copy of the provider family's JSON Schema."""
    return copy.deepcopy(_provider_schema_template())


# =========================================================================
# DESCRIPTION FAMILY
# =========================================================================


def truncate_description(text: str, limit: int = MAX_DESCRIPTION_CHARS) -> tuple[str, bool]:
    """Fit a posting into the budget, keeping both ends.

    Returns the text to send and whether anything was dropped.

    Head and tail rather than head alone because the two halves of a posting
    answer different questions. The opening describes the work; the closing
    carries the visa language, the residence restriction and the pay range --
    the dimensions that later become hard gates. Discarding the tail to save
    tokens would quietly bias the system toward "the posting did not say" on
    precisely the facts that decide eligibility.

    Verification is unaffected for what survives: quotes are always checked
    against the full stored description, so a quote from the retained text
    still resolves. A quote invented across the seam does not exist in the
    stored text and fails, which is the correct outcome.
    """
    if len(text) <= limit:
        return text, False
    budget = limit - len(ELISION)
    head = int(budget * HEAD_SHARE)
    tail = budget - head
    return text[:head] + ELISION + text[-tail:], True


def build_description_request(
    content_hash: str,
    description_text: str,
    config: ModelConfig,
) -> BuiltRequest:
    """The one call that reads the posting.

    The user message is the posting text and nothing else: no wrapper tags, no
    preamble, no restatement of the task. Anything added here is text the model
    could quote, and a quote containing our own scaffolding would be evidence
    the posting never contained.

    Raises on an empty posting rather than sending one. A model given no text
    will still return a well-formed object full of NOT_STATED, and that object
    would validate, store, and read for all time as "we examined this posting
    and it said nothing" -- which is a manufactured fact, not a missing one.
    """
    if not description_text.strip():
        raise ValueError(
            "refusing to build a description request for an empty posting: an extraction "
            "of nothing validates cleanly and is indistinguishable afterwards from an "
            "extraction of a posting that genuinely stated nothing"
        )
    body, truncated = truncate_description(description_text)
    system = load_prompt(DESCRIPTION_PROMPT_VERSION)
    schema = description_schema()
    key = description_key(
        config,
        DESCRIPTION_PROMPT_VERSION,
        TRANSPORT_SCHEMA_VERSION,
        content_hash,
        _static_identity(system, schema, Family.DESCRIPTION, config),
    )
    request = LLMRequest(
        family=Family.DESCRIPTION,
        system=system,
        user=body,
        schema=schema,
        schema_name=DESCRIPTION_SCHEMA_NAME,
        cache_key=key.key,
    )
    return BuiltRequest(
        request=request,
        cache_key=key,
        prompt_version=DESCRIPTION_PROMPT_VERSION,
        transport_version=TRANSPORT_SCHEMA_VERSION,
        truncated=truncated,
    )


def _static_identity(
    system: str, schema: dict[str, Any], family: Family, config: ModelConfig
) -> str:
    """The unchanging half of a request, digested, including anything a mode adds.

    FUNCTION_CALL is the first mode that puts model-visible bytes somewhere the
    prompt and the schema do not reach: the tool's name and its description. Two
    arms could otherwise share a key while asking the model to call two
    differently-named functions, and the second answer would be served as the
    first.

    Every other mode passes nothing extra -- deliberately, so that not one
    existing cache key moves. `static_digest` omits the component rather than
    appending an empty one, which is the difference between a no-op and
    invalidating every answer this project has stored.
    """
    mode = config.output_mode(family)
    parts = [
        tool_digest(family, schema) if mode is StructuredOutput.FUNCTION_CALL else None,
        # A ROUTED VENDOR SERVES ONE MODEL ID FROM MANY MACHINES.
        #
        # `openrouter/z-ai/glm-5.2:free` names a market, not a machine. Two
        # answers to the same question can come from two companies running two
        # builds at two quantizations, and the model id would say nothing. So
        # the pinned endpoint is part of the arm, exactly as the model is.
        routing_identity(config.vendor, config.identifier),
    ]
    present = [part for part in parts if part is not None]
    return static_digest(system, schema, mode, "".join(present) if present else None)


# =========================================================================
# PROVIDER FAMILY
# =========================================================================


def build_provider_request(
    provider: str,
    observations: Sequence[Any],
    field_map_digest: str,
    config: ModelConfig,
) -> BuiltRequest | None:
    """The call that reads the ATS record -- when there is anything to read.

    Only the observations `domain/provider_values.py` could not resolve on its
    own are sent, and the same filtered list produces both the prompt input and
    the cache key. That identity is the point: `observation_block` renders
    exactly what the model receives, so two postings sharing a block are
    provably asking one question and may share one answer.

    `field_map_digest` comes from `cache.field_map_version` and is supplied by
    the caller, which holds the provider adapter. Passing the digest rather
    than the field map keeps this module free of any import from `providers/`,
    so no vendor path can reach the request layer.

    Returns `None` when nothing needs a model. Callers must treat that as
    "assemble the provider family from deterministic values" -- never as a
    failure, and never as an empty provider family.
    """
    interpretable = needs_a_model(observations)
    if not interpretable:
        return None
    block = observation_block(provider, interpretable)
    system = load_prompt(PROVIDER_PROMPT_VERSION)
    schema = provider_schema()
    key = provider_key(
        config,
        PROVIDER_PROMPT_VERSION,
        PROVIDER_TRANSPORT_VERSION,
        block,
        field_map_digest,
        _static_identity(system, schema, Family.PROVIDER, config),
    )
    request = LLMRequest(
        family=Family.PROVIDER,
        system=system,
        user=block,
        schema=schema,
        schema_name=PROVIDER_SCHEMA_NAME,
        cache_key=key.key,
    )
    return BuiltRequest(
        request=request,
        cache_key=key,
        prompt_version=PROVIDER_PROMPT_VERSION,
        transport_version=PROVIDER_TRANSPORT_VERSION,
    )

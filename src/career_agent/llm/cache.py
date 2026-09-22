"""What makes two extractions the same extraction.

A cache key is not an optimisation here, it is a definition: two inputs share a
key exactly when re-running would be guaranteed to ask the same question. Get
the definition wrong in one direction and we pay twice for identical work; wrong
in the other and a stale answer survives a change that should have invalidated
it, which is far worse because nothing looks broken.

WHY THE FAMILIES KEY DIFFERENTLY
--------------------------------
The description call never receives provider metadata, so a payload change
cannot alter its answer -- and including `payload_hash` in its key would re-run
(and re-pay for) a description extraction every time an employer edited an ATS
location field. The provider call never receives the description, so
`content_hash` is equally irrelevant to it.

That asymmetry is the practical dividend of the two-call topology, and it only
works if the keys respect it.

THE PROVIDER KEY IS THE BLOCK, NOT THE PAYLOAD
----------------------------------------------
The provider call does not see the payload. It sees the *field-mapped
observations* drawn from it, and nothing else. Two postings whose blocks are
identical are asking the model the same question, word for word.

This started as a correctness observation and turned out to be a large one.
Keying on `payload_hash` -- unique per posting -- made every job its own cache
entry. Keyed on the block the corpus collapses:

    18,551 jobs  ->  5,609 distinct provider inputs   (3.3x, 69.8% avoided)

which is 26.1M fewer input tokens across a full M4.5 backfill. The reason is
visible in the values: `United States` appears 3,142 times as a location hint,
`US` 1,712 times, `FullTime` 5,765 times. Paying ~2,015 static tokens to
re-derive an identical answer thousands of times is waste that only shows up if
someone counts.

Evidence still verifies per job. The cached answer cites `source_field` and
`source_value`, and those are identical across the sharing jobs by construction
-- that is precisely what made the blocks identical -- so each job's own
archived payload still resolves them.

The field map and metadata vocabulary versions remain in the key. A change to
either alters what the block *means* even when its text is unchanged, and a
stale extraction must not survive a mapping-contract change invisibly.

BOTH HALVES OF THE INPUT ARE HASHED, NOT ONLY THE ONE THAT VARIES
-----------------------------------------------------------------
A call has a dynamic half -- the posting, the observation block -- and a static
half: the system prompt and the JSON schema. The dynamic half was always
content-addressed. The static half entered by *label*: a prompt version, which
is a filename, and a transport version, which is an integer someone remembers
to increment.

That made the key's central claim -- two inputs share a key exactly when
re-running would ask the same question -- true only as long as nobody edited a
prompt file. Editing one without bumping its version produced no new key and no
error: every answer to the old instructions was served as an answer to the new
ones, silently, which is the failure this module's opening paragraph names as
the worse of the two directions.

`static_digest` closes it. It is a digest of the exact system text and the exact
schema JSON the request carries, computed in `requests.py` from the bytes that
are about to be sent, so an edit anywhere in either produces a different key and
therefore a miss rather than a wrong hit.

Two mechanisms now guard the same property from different sides, and the
division is deliberate:

* **this digest** answers *"which bytes is this the answer to?"* -- it is
  self-maintaining, and cannot drift because nothing has to be kept in sync;
* **`prompts.PROMPT_DIGESTS`** answers *"has this named prompt mutated?"* -- it
  turns an edit to a version already used live into a refusal at load time,
  rather than a cache miss that quietly re-spends the quota.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from career_agent.domain.enums import METADATA_DIMENSION_VOCABULARY_VERSION
from career_agent.llm.client import Family, ModelConfig


def _digest(*parts: str | int | None) -> str:
    """Join with a separator that cannot appear in any part.

    Concatenating without one lets ("ab", "c") and ("a", "bc") collide, which is
    the sort of bug that produces one wrong cached answer in ten thousand and is
    never found.
    """
    joined = "\x1f".join("" if part is None else str(part) for part in parts)
    return "sha256:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FamilyKey:
    """One family's cache identity, and the inputs that produced it."""

    family: Family
    key: str
    inputs: tuple[str, ...]


def description_key(
    config: ModelConfig,
    prompt_version: str,
    transport_version: int,
    content_hash: str,
    static_digest: str,
) -> FamilyKey:
    """Identity for the description call.

    Deliberately excludes `payload_hash`. The description call is never given
    the provider payload, so a payload change cannot change its answer, and
    invalidating on one would re-pay for work that could not have differed.

    `static_digest` covers the system prompt and the schema as sent. The version
    labels stay beside it: they are what a human reads in `llm_call`, and a
    digest alone would make every stored row's provenance unreadable.
    """
    inputs = (
        config.arm,
        prompt_version,
        str(transport_version),
        content_hash,
        static_digest,
    )
    return FamilyKey(Family.DESCRIPTION, _digest(Family.DESCRIPTION, *inputs), inputs)


def observation_block(provider: str, observations: Sequence[Any]) -> str:
    """Exactly the text the provider call receives, and nothing else.

    One line per observation, in field-map declaration order. This is both the
    prompt input and the cache identity, which is the point: if it renders
    identically it *is* the same question.

    Provider is included because two vendors can hold the same string at
    different paths, and the resulting evidence is not interchangeable.
    """
    body = "\n".join(f"{o.dimension}\t{o.source_field}\t{o.source_value}" for o in observations)
    return f"{provider}\n{body}"


def provider_key(
    config: ModelConfig,
    prompt_version: str,
    transport_version: int,
    block: str,
    field_map_version: str,
    static_digest: str,
    metadata_vocabulary_version: int = METADATA_DIMENSION_VOCABULARY_VERSION,
) -> FamilyKey:
    """Identity for the provider call.

    Keyed on the rendered observation block, not on `payload_hash`. The block is
    the call's whole input; `payload_hash` is unique per posting and would make
    every job its own cache entry for a question thousands of jobs share.

    Excludes `content_hash` -- the call never sees the description.
    """
    inputs = (
        config.arm,
        prompt_version,
        str(transport_version),
        _digest(block),
        field_map_version,
        str(metadata_vocabulary_version),
        static_digest,
    )
    return FamilyKey(Family.PROVIDER, _digest(Family.PROVIDER, *inputs), inputs)


def field_map_version(provider: str, paths: tuple[str, ...], dimensions: tuple[str, ...]) -> str:
    """A digest of what the field map actually maps, not a hand-maintained number.

    A version someone has to remember to bump is a version that will not be
    bumped. Deriving it from the (path, dimension) pairs means the identity
    changes exactly when the mapping changes -- including when a path moves to a
    different dimension, which leaves the path count identical and is precisely
    the case a counter would miss.
    """
    pairs = sorted(zip(paths, dimensions, strict=True))
    return _digest(provider, *[f"{path}={dimension}" for path, dimension in pairs])


def static_digest(system: str, schema: dict[str, Any], mode: str, extra: str | None = None) -> str:
    """Identity of the unchanging half of a request: prompt, schema, enforcement.

    The schema is serialised with compact separators and sorted keys, so the
    digest depends on its *content* rather than on dictionary ordering, which
    pydantic is free to change between versions without changing what the model
    is asked for.

    `mode` is `StructuredOutput`, and it belongs here rather than in the arm
    label. Asking a vendor to *guarantee* a schema and asking it to *aim at* one
    are two different requests -- the payload differs, and so does what a
    malformed answer means. An answer given under `PLAIN_JSON` must never be
    served as an answer given under `STRICT_SCHEMA`: the second would be
    claiming a guarantee that was never in force, on the exact metric
    (first-attempt schema pass rate) the difference shows up in.

    Per family, because the mode is per family: Cerebras enforces our provider
    schema and cannot enforce our description one.

    `extra` carries anything model-visible that a mode adds and the three
    arguments above do not describe -- today, the FUNCTION_CALL tool
    declaration, whose name and description are bytes the model reads and the
    schema digest does not cover.

    OMITTED rather than empty when there is nothing to add. `_digest` joins with
    a separator, so passing "" would append one and move every digest this
    project has ever computed -- invalidating every stored answer to buy
    nothing.
    """
    parts: list[str | int | None] = [
        system,
        json.dumps(schema, separators=(",", ":"), sort_keys=True),
        mode,
    ]
    if extra is not None:
        parts.append(extra)
    return _digest(*parts)

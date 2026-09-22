"""The extraction families, expressed as callable tools.

WHY A TOOL AND NOT A RESPONSE SCHEMA
------------------------------------
`responseSchema` asks a model to shape its final text. On the route this
milestone is running, that request is accepted and not enforced: five of
eighteen Gemma answers ended a complete JSON document with a Markdown fence,
under four different prompts and both families, which a constrained decoder
cannot do. The schema was a hint.

A function call is a different channel, not a stronger hint. The arguments come
back in their own response part, as a structured object, beside the text rather
than inside it -- so a model that also wants to write prose has somewhere to put
it that is not our document. Whether THIS model honours the channel is the
question one canary exists to answer; nothing here assumes it does.

WHAT DOES NOT CHANGE
--------------------
The declaration carries the authoritative transport schema and nothing else.
Same fields, same enums, same cardinality, same evidence rules. The tool is an
envelope; `TDescriptionFamily` and `TProviderFamily` remain the authority on
what is inside it, and local validation still runs on the arguments exactly as
it ran on the parsed text.

The descriptions below are mechanical on purpose. A tool description is prompt
text the model reads, and a helpful sentence about what a good answer looks like
would be an instruction that lives outside the versioned prompt, invisible to
`PROMPT_DIGESTS` and to review. So they say what the function is for and
nothing about how to answer it -- no case, no candidate, no desired value.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from career_agent.llm.client import Family

#: One deterministic, family-specific name. The model is asked for this exact
#: string, and a call to any other name is a contract failure rather than
#: something to interpret -- so the names must never collide or drift.
TOOL_NAMES: dict[Family, str] = {
    Family.DESCRIPTION: "submit_description_family",
    Family.PROVIDER: "submit_provider_family",
}

#: Mechanical, candidate-independent, and deliberately uninformative about how
#: to answer. Everything about WHAT to report lives in the versioned prompt.
TOOL_DESCRIPTIONS: dict[Family, str] = {
    Family.DESCRIPTION: (
        "Submit the completed reading of the job posting text. Call this exactly once, "
        "with every field the parameters define."
    ),
    Family.PROVIDER: (
        "Submit the completed reading of the provider's structured record. Call this "
        "exactly once, with every field the parameters define."
    ),
}


#: Which revision of each family's declaration is in force.
#:
#: Part of the cache identity, so an answer given to one revision is never
#: served as an answer to another. Bumped for exactly the reason a prompt
#: version is: the bytes the model reads changed.
#:
#: description v2 -- the first canary returned a flawless envelope around
#: evidence that cited nothing. `description_v8` states both rules plainly:
#: an EXPLICIT observation without an `evidence_id` fails the extraction, and
#: every evidence entry carries a quote copied character-for-character. The
#: DECLARATION said neither. It offered `quote`, `provider`, `source_field` and
#: `source_value` as four equally optional strings with no descriptions at all,
#: required only `id` and `source_kind`, and gave `evidence_id` no description
#: either. The model filled `source_value` with eighteen real posting sentences
#: -- a field `description_v8` never mentions once, and which it could only have
#: learned from this declaration.
#:
#: Under STRICT_SCHEMA the same schema travelled as `responseSchema` and the
#: model followed the prompt. Under FUNCTION_CALL the declaration IS the
#: contract for the call's arguments, and it under-specified precisely the two
#: rules that failed.
#:
#: provider v1 -- unchanged. Its contract did not fail; all 56 stored provider
#: answers satisfy it. Revising it would invalidate every one of them to fix a
#: problem it does not have.
TOOL_DECLARATION_VERSIONS: dict[Family, str] = {
    Family.DESCRIPTION: "v2",
    Family.PROVIDER: "v1",
}

#: Annotations added to the DescriptionFamily declaration. RESTATEMENTS ONLY.
#:
#: Every line here is already true, already enforced, and already written in
#: `description_v8` or in the domain model's own validator. Nothing new is
#: asked; nothing is relaxed. No type, no enum, no required list and no
#: property changes -- a test asserts the parameter SHAPE is byte-identical to
#: the unannotated schema and that only `description` keys were added.
#:
#: Generic on purpose: no case, no posting, no vendor, no model, no expected
#: value. A declaration that hinted at an answer would be a prompt living
#: outside `PROMPT_DIGESTS`, invisible to prompt review.
_DESCRIPTION_EVIDENCE_NOTES: dict[str, str] = {
    "quote": (
        "REQUIRED when source_kind is JOB_DESCRIPTION: the exact text, copied "
        "character-for-character from the posting as one contiguous run. It is checked "
        "mechanically against the posting. An entry whose source_kind is JOB_DESCRIPTION "
        "and whose quote is absent is rejected."
    ),
    "source_value": (
        "The value read from a provider's structured record. Used only when source_kind is "
        "PROVIDER_FIELD. It is not a place to put posting text and is not a substitute for "
        "quote."
    ),
    "source_field": (
        "The field name in a provider's structured record. Used only when source_kind is "
        "PROVIDER_FIELD."
    ),
    "provider": "The provider whose record supplied this entry. Used only with PROVIDER_FIELD.",
}

_DESCRIPTION_OBSERVATION_NOTES: dict[str, str] = {
    "evidence_id": (
        "REQUIRED when status is EXPLICIT: the id of an evidence entry returned in this same "
        "call. It must match that entry's id exactly. An EXPLICIT observation carrying no "
        "evidence_id, or one naming an entry this call did not return, fails the extraction. "
        "If no valid evidence can be given, the observation is not EXPLICIT."
    ),
}


def _annotated(family: Family, schema: dict[str, Any]) -> dict[str, Any]:
    """Add the cross-field notes to a copy of the schema, changing nothing else.

    Descriptions only. The walk finds the evidence and observation item objects
    by the properties they carry rather than by a path, so it works on the raw
    schema and on the vendor dialect alike -- both callers of `tool_declaration`
    pass a different one, and they must agree about what was asked.
    """
    if family is not Family.DESCRIPTION:
        return schema

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        result = {key: walk(value) for key, value in node.items()}
        properties = result.get("properties")
        if isinstance(properties, dict):
            notes = (
                _DESCRIPTION_EVIDENCE_NOTES
                if "source_kind" in properties
                else _DESCRIPTION_OBSERVATION_NOTES
                if "dimension" in properties and "status" in properties
                else {}
            )
            for name, note in notes.items():
                if name in properties and isinstance(properties[name], dict):
                    properties[name] = {**properties[name], "description": note}
        return result

    return walk(schema)  # type: ignore[no-any-return]


def tool_declaration(family: Family, schema: dict[str, Any]) -> dict[str, Any]:
    """One function declaration for one family, in vendor-neutral form.

    Takes the schema already translated into the vendor's dialect, so this
    module imports no adapter and no SDK: the caller owns the dialect, this owns
    the envelope. That separation is what lets the request layer digest a
    declaration without depending on the vendor that will send it.
    """
    return {
        "name": TOOL_NAMES[family],
        "description": TOOL_DESCRIPTIONS[family],
        "parameters": _annotated(family, schema),
    }


def tool_digest(family: Family, schema: dict[str, Any]) -> str:
    """Identity of the declaration this family would be asked to call.

    Part of the cache key, and separately from the schema, because the name and
    the description are model-visible bytes that the schema digest does not
    cover. Renaming the function or rewording its description changes what the
    model was asked, and an answer to the old wording must not be served as an
    answer to the new one.
    """
    body = json.dumps(tool_declaration(family, schema), sort_keys=True, separators=(",", ":"))
    version = TOOL_DECLARATION_VERSIONS[family]
    return "sha256:" + hashlib.sha256(f"{version}{body}".encode()).hexdigest()

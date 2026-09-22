"""Every vendor wants a different dialect of JSON Schema. This speaks all three.

The port hands an adapter a plain JSON Schema dict produced from a transport
model. No vendor accepts it unchanged, and the ways they refuse differ enough
that a single "translate" function would be three functions wearing a coat:

    Anthropic   takes ordinary JSON Schema as a tool's `input_schema`, but
                `$ref` resolution is charged per usage rather than per
                definition, so a shared `$defs` entry referenced thirty times
                is billed thirty times.

    OpenAI      strict mode requires `additionalProperties: false` on every
                object AND every property listed in `required`. Optionality is
                expressed by unioning the type with `null` instead -- so a
                schema with genuinely optional fields has to be rewritten, not
                merely annotated.

    Google      `responseSchema` is an OpenAPI 3.0 subset: no `$defs`, no
                `$ref`, no `additionalProperties`, no `const`. Anything it does
                not recognise is rejected rather than ignored.

WHY THIS IS ONE MODULE AND NOT THREE
------------------------------------
Because the transformations compose, and two of the three vendors need the same
first step. Inlining is where the subtle bug lives -- a naive walk over a
recursive schema does not terminate -- so it is written once, tested once, and
shared, rather than reimplemented slightly differently in each adapter.

Nothing here imports a vendor SDK. These are dict-to-dict functions, which is
what makes an adapter's request shape testable with no network and no
credentials.
"""

from copy import deepcopy
from typing import Any

#: Keywords Google's OpenAPI subset rejects outright.
_GOOGLE_REJECTS = frozenset(
    {"$defs", "$ref", "$schema", "additionalProperties", "const", "definitions", "examples"}
)


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Resolve every local `$ref` against `$defs`, then drop `$defs`.

    Returns a new dict; the input is not touched.

    Recursion is the trap. A model that refers to itself produces a schema
    whose expansion never terminates, so a definition already being expanded is
    left as a `$ref` rather than followed. The transport models are not
    recursive today, and this is what stops that from becoming a hang the first
    time one is.
    """
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node: Any, active: frozenset[str]) -> Any:
        if isinstance(node, list):
            return [walk(item, active) for item in node]
        if not isinstance(node, dict):
            return node

        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/"):
            name = ref.rsplit("/", 1)[-1]
            if name in active or name not in defs:
                return dict(node)
            merged = walk(defs[name], active | {name})
            # Keywords sitting beside a `$ref` -- a description, a default --
            # belong to the use site and survive the substitution.
            beside = {k: walk(v, active) for k, v in node.items() if k != "$ref"}
            return {**merged, **beside} if beside else merged

        return {key: walk(value, active) for key, value in node.items() if key != "$defs"}

    inlined = walk(deepcopy(schema), frozenset())
    if isinstance(inlined, dict):
        inlined.pop("$defs", None)
        inlined.pop("definitions", None)
    return inlined  # type: ignore[no-any-return]


def openai_strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Rewrite a schema so OpenAI's strict structured output will accept it.

    Two rules, applied to every object in the tree: `additionalProperties`
    becomes false, and `required` lists every property.

    That second rule reads like a mistake and is not. Strict mode has no
    concept of an optional property -- it expresses "may be absent" as "may be
    null". A field that was optional therefore becomes required-and-nullable,
    which is the same statement in a different grammar, and the validators on
    our side already reject a null where a value is mandatory.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node

        result = {key: walk(value) for key, value in node.items()}
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["additionalProperties"] = False
            result["required"] = sorted(properties)
        return result

    return walk(inline_refs(schema))  # type: ignore[no-any-return]


def google_openapi(schema: dict[str, Any]) -> dict[str, Any]:
    """Strip a schema down to the OpenAPI 3.0 subset Gemini accepts.

    Unrecognised keywords are rejected rather than ignored, so this removes
    them rather than hoping. `anyOf` survives -- it is the one union form the
    subset keeps -- which matters because the transport expresses "a value or
    nothing" that way.
    """

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        return {key: walk(value) for key, value in node.items() if key not in _GOOGLE_REJECTS}

    return walk(inline_refs(schema))  # type: ignore[no-any-return]


def count_optional_and_union(schema: dict[str, Any]) -> tuple[int, int]:
    """How many optional and union-typed parameters this schema presents.

    Vendors publish limits on both, and the M2.0 topology decision was made
    against measurements from this function rather than against an estimate.
    Counted on the inlined form on purpose: a limit is charged per usage, so a
    shared definition referenced thirty times counts thirty times.
    """
    inlined = inline_refs(schema)
    optional = union = 0

    def walk(node: Any) -> None:
        nonlocal optional, union
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        properties = node.get("properties")
        if isinstance(properties, dict):
            required = set(node.get("required") or ())
            optional += sum(1 for name in properties if name not in required)
            for value in properties.values():
                if isinstance(value, dict) and ("anyOf" in value or "oneOf" in value):
                    union += 1
        for value in node.values():
            walk(value)

    walk(inlined)
    return optional, union

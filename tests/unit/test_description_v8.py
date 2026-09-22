"""What v8 corrects, and the three characters that made it necessary.

v7 wrote the output contract down for the first time -- one JSON document, `{`
first, `}` last, no fence -- and in the same section demonstrated the opposite.
The canonical skeleton it added was the only fenced block in the file carrying a
language tag, and the only one holding a JSON document, so the prompt ended up
with exactly one place where a document's outer closing brace is followed by a
closing Markdown fence.

The first live v7 request completed a valid 14,117-character document, emitted a
newline, and then emitted that fence. Its final 17 characters are a verbatim copy
of the skeleton block's final 17. The parser rejected the response as `Extra
data`; the reading inside it -- 33 dimensions, one row each, zero unsupported
EXPLICIT claims -- did not survive.

So v8 removes the demonstration and makes one terminal rule the last instruction
in the file. Nothing else moves, and these tests are the proof of the "nothing
else". Above all they prove the fix is a PROMPT fix: the stored v7 artifact still
fails the unchanged production parser, because nothing anywhere learned to strip
a fence.
"""

from __future__ import annotations

import difflib
import hashlib
import re

from pydantic import BaseModel

from career_agent.llm.cache import static_digest
from career_agent.llm.client import Family, LLMResponse, ModelConfig
from career_agent.llm.prompts import (
    DESCRIPTION_PROMPT_VERSION,
    PROMPT_DIGESTS,
    PROMPT_DIR,
    PROVIDER_PROMPT_VERSION,
    available_prompts,
    load_prompt,
)
from career_agent.llm.requests import (
    build_description_request,
    build_provider_request,
    description_schema,
    provider_schema,
)
from career_agent.llm.tokens import MEASURED_PROMPT_TOKENS, estimate_request
from career_agent.pipeline.extract import _interpret

#: Written out rather than read from PROMPT_DIGESTS. A test comparing the file to
#: whatever the registry currently claims would pass just as happily after both
#: were edited together, which is the one failure it exists to catch.
V6_DIGEST = "sha256:c1e558dbbf3c2828eb4f13d56fea2ab7fb2209089e1988f7e75fee8af5a43c18"
V7_DIGEST = "sha256:f1871d8d064a46706f64177aed3143bbf82f01dca2fab68d0655be05d775306b"
V8_DIGEST = "sha256:18bcb569c03b196fcfb438af8ee43b693914e96bdd285f4392148763040a297c"

#: The exact tail the model emitted after a complete document, from the stored
#: `llm_call` row of the failed v7 canary: the evidence array closing, the outer
#: brace, a newline, and three characters that should not exist.
V7_ORPHAN_TAIL = "\n  ]\n}\n```"

POSTING = "We are hiring a platform engineer. Remote within Brazil."


class Doc(BaseModel):
    """A stand-in transport model, so `_interpret` runs its real two stages.

    The parser question -- is this one JSON document and nothing else -- is
    independent of which family the document belongs to, and a real family model
    would make these tests about 33 dimensions instead of about a boundary.
    """

    a: int


#: The number of lines the terminal rule appended, including its separator and
#: blank lines. Pinned so that growing it is a decision, not a drift.
TERMINAL_RULE_LINES = 11

#: The provider family's static identity, unchanged by this bump. Pinned by value
#: because "did not change" is the claim, and a computed comparison against the
#: current file would agree with any change made to both halves at once.
PROVIDER_STATIC_DIGEST = "sha256:dd51426a9b49c3f3a58b8b4d63532e3a1dab4aff5e48fdb87ee8522ab446a510"


def digest_of(version: str) -> str:
    raw = (PROMPT_DIR / f"{version}.md").read_bytes()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def arm() -> ModelConfig:
    return ModelConfig(vendor="google", identifier="gemma-4-31b-it@high")


def v7_text() -> str:
    return load_prompt("description_v7")


def v8_text() -> str:
    return load_prompt("description_v8")


# =========================================================================
# 1. THE MEASURED VERSIONS DID NOT MOVE
# =========================================================================


def test_description_v6_still_matches_its_frozen_digest() -> None:
    assert digest_of("description_v6") == V6_DIGEST
    assert PROMPT_DIGESTS["description_v6"] == V6_DIGEST


def test_description_v7_still_matches_its_frozen_digest() -> None:
    """v7 ran live. Its bytes are now evidence, and evidence does not get edited.

    Everything recorded against v7 -- one attempt, one envelope, one 14,120-char
    `raw_output`, one parser error -- is attributable only while these bytes are
    the bytes that produced it.
    """
    assert digest_of("description_v7") == V7_DIGEST
    assert PROMPT_DIGESTS["description_v7"] == V7_DIGEST


# =========================================================================
# 2. v8 EXISTS, IS PINNED, AND IS THE VERSION A REQUEST IS BUILT FROM
# =========================================================================


def test_description_v8_is_registered_loadable_and_pinned() -> None:
    assert v8_text().startswith("# description_v8\n")
    assert "description_v8" in available_prompts()
    assert digest_of("description_v8") == V8_DIGEST
    assert PROMPT_DIGESTS["description_v8"] == V8_DIGEST
    assert DESCRIPTION_PROMPT_VERSION == "description_v8"
    assert "description_v8" in MEASURED_PROMPT_TOKENS, (
        "a prompt version nobody measured would be estimated from characters"
    )


def test_v8_travels_through_the_existing_request_builder() -> None:
    """No new code path and no new selection mechanism. One constant moved."""
    built = build_description_request("sha256:PIN", POSTING, arm())

    assert built.request.family is Family.DESCRIPTION
    assert built.prompt_version == "description_v8"
    assert built.request.system == v8_text()
    assert built.request.user == POSTING, "the user message is still the posting and nothing else"
    assert estimate_request(built).static_measured


# =========================================================================
# 3. THE EXACT DELTA, AND NOTHING BESIDE IT
# =========================================================================


def test_v8_differs_from_v7_only_in_the_three_declared_changes() -> None:
    """A line-level diff, enumerated.

    Three changes were authorised: the version header, the removal of the
    skeleton's fence pair, and one appended terminal rule. This asserts the diff
    contains those and nothing else -- a fourth changed line is a change nobody
    declared, whatever it looks like.
    """
    old = v7_text().split("\n")
    new = v8_text().split("\n")

    removed: list[str] = []
    added: list[str] = []
    for line in difflib.unified_diff(old, new, n=0, lineterm=""):
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            continue
        if line.startswith("-"):
            removed.append(line[1:])
        elif line.startswith("+"):
            added.append(line[1:])

    assert removed == ["# description_v7", "```json", "```"], removed
    assert added[0] == "# description_v8"
    assert len(added) == 1 + TERMINAL_RULE_LINES, added
    assert "## Where your output ends" in added


def test_no_semantic_schema_instruction_was_lost() -> None:
    """Every rule-bearing line of v7 is still in v8, verbatim.

    The permitted changes are a header, two delimiters and an append. None of
    them may take a sentence about what to report along with it.
    """
    carried = [
        line
        for line in v7_text().split("\n")
        if line.strip() not in ("", "```", "```json", "# description_v7")
    ]
    v8 = v8_text()
    missing = [line for line in carried if line not in v8]
    assert missing == [], f"v8 dropped {len(missing)} line(s) of v7: {missing[:5]}"


def test_the_v7_skeleton_was_fenced_and_the_v8_skeleton_is_not() -> None:
    """The audit finding, pinned as a test.

    v7's skeleton is the only `json`-tagged block in the file and the only one
    whose content is a JSON document, which is why it is the only place a closing
    brace is followed by a delimiter. v6, which never had it, has no such place.
    """
    assert v7_text().count("```") == 26
    assert v7_text().count("```json") == 1
    assert "```json\n{\n" in v7_text()
    assert re.search(r"\}\n```", v7_text()), "v7 ends a document with a fence"

    assert v8_text().count("```") == 24, "exactly one fence pair was removed"
    assert "```json" not in v8_text()
    assert not re.search(r"\}\n```", v8_text()), (
        "no continuation in v8 ends a JSON document with a delimiter"
    )
    assert not re.search(r"\}\n```", load_prompt("description_v6"))

    # The skeleton itself is intact, merely unwrapped.
    assert '"observed_title": "<the title as stated, or an empty string>"' in v8_text()


def test_the_terminal_boundary_rule_is_the_final_instruction() -> None:
    text = v8_text()
    heading = "## Where your output ends"
    assert heading in text
    after = text.split(heading, 1)[1]
    assert "#" not in after, "nothing follows the terminal rule"

    # It says all four things, and demonstrates none of them.
    assert "exactly one JSON document" in after
    assert "stop generating" in after
    assert "last non-whitespace" in after
    assert "no commentary" in after
    assert "```" not in after, "a rule about delimiters may not contain one"


def test_the_terminal_rule_comes_after_the_canonical_skeleton() -> None:
    """Ordering is the correction.

    v7 stated the boundary rule at the top of its section and demonstrated the
    violation at the bottom. Whichever of the two a model weighted more heavily,
    the last thing it read was the wrong one.
    """
    text = v8_text()
    assert text.index('"observed_title": "<the title as stated') < text.index(
        "## Where your output ends"
    )


def test_v8_names_no_vendor_no_model_and_no_benchmark_case() -> None:
    """A prompt written about one incident stops being a prompt about the task."""
    text = v8_text().lower()
    for forbidden in ("gemma", "google", "gemini", "gc-17", "orphan", "openrouter", "cerebras"):
        assert forbidden not in text, f"v8 mentions {forbidden!r}"


# =========================================================================
# 4. THE FIX IS IN THE PROMPT, AND NOWHERE ELSE
# =========================================================================


def test_the_stored_v7_failure_mode_is_still_invalid() -> None:
    """The load-bearing test of the whole change.

    A complete JSON document followed by a fence must still be rejected, by the
    unchanged parser, with the same error the canary got. If this ever passes,
    something learned to repair output, and every acceptance rate measured
    afterwards is measuring the repair rather than the model.
    """
    reply = LLMResponse(raw_text='{"a": 1}' + V7_ORPHAN_TAIL[-4:], model="m")
    parsed_ok, validated_ok, payload, error = _interpret(reply, Doc)

    assert parsed_ok is False
    assert validated_ok is False
    assert payload is None
    assert error is not None
    assert "output is not JSON" in error
    assert "Extra data" in error, "the same rejection the canary got"


def test_trailing_json_whitespace_is_accepted() -> None:
    """Whitespace is not a delimiter, which is why the rule says non-whitespace."""
    reply = LLMResponse(raw_text='{"a": 1}\n\n  \t\n', model="m")
    parsed_ok, validated_ok, payload, error = _interpret(reply, Doc)

    assert (parsed_ok, validated_ok, error) == (True, True, None)
    assert payload == Doc(a=1)


def test_a_document_followed_by_a_formatting_delimiter_is_rejected() -> None:
    for suffix in ("```", "\n```", "\n```json", "\nHope this helps.", '\n{"a": 2}'):
        reply = LLMResponse(raw_text='{"a": 1}' + suffix, model="m")
        parsed_ok, _, payload, error = _interpret(reply, Doc)

        assert parsed_ok is False, f"{suffix!r} was accepted"
        assert payload is None
        assert error is not None


# =========================================================================
# 5. CACHE ISOLATION
# =========================================================================


def test_v6_v7_and_v8_have_three_distinct_cache_identities() -> None:
    schema = description_schema()
    digests = {
        version: static_digest(load_prompt(version), schema, "STRICT_SCHEMA")
        for version in ("description_v6", "description_v7", "description_v8")
    }
    assert len(set(digests.values())) == 3, digests

    built = build_description_request("sha256:PIN", POSTING, arm())
    assert digests["description_v8"] in built.cache_key.inputs
    assert digests["description_v7"] not in built.cache_key.inputs
    assert digests["description_v6"] not in built.cache_key.inputs


def test_transport_mode_remains_an_independent_cache_dimension() -> None:
    """v8 must not have collapsed a dimension the screen depends on."""
    schema = description_schema()
    strict = static_digest(v8_text(), schema, "STRICT_SCHEMA")
    plain = static_digest(v8_text(), schema, "PLAIN_JSON")
    assert strict != plain, "an answer given without enforcement is not one given with it"


def test_no_provider_family_identity_changed() -> None:
    """One family moved. The other must not have, or a re-run pays twice."""
    assert PROVIDER_PROMPT_VERSION == "provider_v3"
    assert PROMPT_DIGESTS["provider_v3"] == digest_of("provider_v3")

    assert (
        PROMPT_DIGESTS["provider_v3"]
        == "sha256:" + hashlib.sha256((PROMPT_DIR / "provider_v3.md").read_bytes()).hexdigest()
    )
    assert (
        static_digest(load_prompt(PROVIDER_PROMPT_VERSION), provider_schema(), "STRICT_SCHEMA")
        == PROVIDER_STATIC_DIGEST
    ), "a moved provider identity is 25 live calls nobody authorised"

    # And a posting whose provider values all resolve deterministically still
    # asks no model at all, which is the cheapest identity of the three.
    assert build_provider_request("greenhouse", (), "sha256:map", arm()) is None

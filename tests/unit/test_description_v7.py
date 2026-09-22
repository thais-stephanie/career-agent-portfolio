"""What v7 adds, what it must not have touched, and the v6 it cannot disturb.

Every prompt version through v6 described WHAT to report and left the SHAPE of
the response to the vendor's schema enforcement. That held while every arm ran
under STRICT_SCHEMA and stopped holding the moment one did not: v6 never uses
the words `observations`, `function_signals`, `source_kind`, `JOB_DESCRIPTION`,
`raw_mention`, `language_code` or `not_applicable_because`. It says "return one
observation row per dimension" and never names the collection those rows go in.

A model given the schema is told. A model in JSON mode is guessing.

So v7 restates rules that were already true and already enforced -- the document
boundary, the topology, the cardinality, four semantic invariants -- and changes
nothing else. These tests are the proof of the "nothing else".
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from career_agent.llm.client import Family, ModelConfig, StructuredOutput
from career_agent.llm.prompts import (
    DESCRIPTION_PROMPT_VERSION,
    PROMPT_DIGESTS,
    PROMPT_DIR,
    PromptChanged,
    available_prompts,
    canonical_bytes,
    load_prompt,
)
from career_agent.llm.requests import build_description_request
from career_agent.llm.tokens import MEASURED_PROMPT_TOKENS, estimate_request
from career_agent.llm.transport import ALL_DIMENSIONS, TDescriptionFamily

#: The digest v6 has carried since the day it was pinned, before its first live
#: call. Written out rather than read from PROMPT_DIGESTS: a test that compared
#: the file to whatever the registry currently claims would pass just as happily
#: if someone edited both.
V6_DIGEST = "sha256:c1e558dbbf3c2828eb4f13d56fea2ab7fb2209089e1988f7e75fee8af5a43c18"
V6_BYTES = 25_737

POSTING = "We are hiring a Business Technology Analyst in Amsterdam."


def digest_of(version: str) -> str:
    """The digest of the CANONICAL bytes, as `load_prompt` computes it. A test
    that hashed the raw working-copy bytes passed on one machine and failed
    on a fresh clone, over files that had never changed (see PROMPT_DIGESTS)."""
    return (
        "sha256:"
        + hashlib.sha256(canonical_bytes((PROMPT_DIR / f"{version}.md").read_bytes())).hexdigest()
    )


# =========================================================================
# 1. v6 IS UNTOUCHED
# =========================================================================


def test_description_v6_is_byte_for_byte_what_it_always_was() -> None:
    """v6 has been used for live calls. It is history, and history is read-only.

    Every answer stored under the name `description_v6` was given to these
    exact bytes. Editing them would not produce a bad cache hit -- `load_prompt`
    refuses -- but it would make every stored row's provenance a lie.
    """
    raw = canonical_bytes((PROMPT_DIR / "description_v6.md").read_bytes())
    assert len(raw) == V6_BYTES
    assert digest_of("description_v6") == V6_DIGEST
    assert PROMPT_DIGESTS["description_v6"] == V6_DIGEST
    assert raw.decode("utf-8").startswith("# description_v6\n")


def test_every_earlier_prompt_version_still_loads() -> None:
    """v7 is an addition, not a replacement. Nothing was renamed or redirected."""
    for version in (
        "description_v1",
        "description_v2",
        "description_v3",
        "description_v4",
        "description_v5",
        "description_v6",
        "provider_v1",
        "provider_v2",
        "provider_v3",
    ):
        assert version in available_prompts()
        assert load_prompt(version)
        assert digest_of(version) == PROMPT_DIGESTS[version]


# =========================================================================
# 2. v7 EXISTS, IS PINNED, AND IS THE CURRENT VERSION
# =========================================================================


def test_description_v7_is_registered_loadable_and_pinned() -> None:
    text = load_prompt("description_v7")
    assert text.startswith("# description_v7\n")
    assert PROMPT_DIGESTS["description_v7"] == digest_of("description_v7")
    assert "description_v7" in MEASURED_PROMPT_TOKENS, (
        "a prompt version nobody measured would be estimated from characters"
    )
    # v7 ran live once and failed on its terminal boundary, so v8 succeeded it.
    # v7 stays pinned, loadable and measured -- a measured version whose bytes
    # could still move would make every number recorded against it worthless --
    # but it is no longer what a new request is built from.
    assert DESCRIPTION_PROMPT_VERSION == "description_v8"


def test_the_two_versions_cannot_be_confused_for_one_another() -> None:
    assert digest_of("description_v6") != digest_of("description_v7")
    assert PROMPT_DIGESTS["description_v6"] != PROMPT_DIGESTS["description_v7"]


def test_a_drifted_prompt_is_refused_rather_than_served(tmp_path, monkeypatch) -> None:
    """The mechanism that makes a version name a claim about bytes.

    Asserted for v7 specifically, because v7 is the version about to be
    measured, and a measured version that could be edited afterwards would make
    the measurement unattributable.
    """
    load_prompt.cache_clear()
    monkeypatch.setitem(PROMPT_DIGESTS, "description_v7", "sha256:" + "0" * 64)
    with pytest.raises(PromptChanged, match="does not match its pinned digest"):
        load_prompt("description_v7")
    load_prompt.cache_clear()


# =========================================================================
# 3. CACHE ISOLATION
# =========================================================================


def arm(mode: StructuredOutput = StructuredOutput.STRICT_SCHEMA) -> ModelConfig:
    return ModelConfig(
        vendor="google", identifier="gemma-4-31b-it", reasoning="high", structured_output=mode
    )


def test_no_v6_answer_can_satisfy_a_v7_request() -> None:
    """The whole reason a prompt revision is a version bump.

    The key moved because `static_digest` covers the system prompt BYTES, and
    v7 holds bytes v6 does not. A stored v6 answer is an answer to a question
    that was asked differently.
    """
    from career_agent.llm.cache import static_digest

    built = build_description_request("sha256:PIN", POSTING, arm())
    schema = built.request.schema
    v6_static = static_digest(load_prompt("description_v6"), schema, "STRICT_SCHEMA")
    v7_static = static_digest(load_prompt("description_v7"), schema, "STRICT_SCHEMA")

    assert v6_static != v7_static
    # Neither historical version can reach a request built today, which is the
    # same guarantee one bump further along.
    assert v6_static not in built.cache_key.inputs
    assert v7_static not in built.cache_key.inputs


def test_transport_mode_model_and_reasoning_remain_separate_cache_dimensions() -> None:
    """v7 adds a dimension to the identity; it does not collapse the others."""
    keys = {
        "strict": build_description_request("sha256:PIN", POSTING, arm()).cache_key.key,
        "json_object": build_description_request(
            "sha256:PIN", POSTING, arm(StructuredOutput.JSON_OBJECT)
        ).cache_key.key,
        "plain": build_description_request(
            "sha256:PIN", POSTING, arm(StructuredOutput.PLAIN_JSON)
        ).cache_key.key,
        "other_model": build_description_request(
            "sha256:PIN",
            POSTING,
            ModelConfig(vendor="google", identifier="gemma-4-26b-a4b-it", reasoning="high"),
        ).cache_key.key,
        "other_reasoning": build_description_request(
            "sha256:PIN",
            POSTING,
            ModelConfig(vendor="google", identifier="gemma-4-31b-it", reasoning="minimal"),
        ).cache_key.key,
    }
    assert len(set(keys.values())) == len(keys), keys


# =========================================================================
# 4. v7 SERIALISES THROUGH THE UNCHANGED REQUEST PATH
# =========================================================================


def test_the_builder_no_longer_serves_the_retired_version() -> None:
    """v7 is history, and history is not what the next request is built from.

    The builder reads one module-level constant, so a retired version stops
    being reachable the moment that constant moves. Asserted here rather than
    left implicit: v7's bytes are still on disk and still loadable, and "on
    disk" is exactly the state a stale request would be built from.
    """
    built = build_description_request("sha256:PIN", POSTING, arm())

    assert built.request.family is Family.DESCRIPTION
    assert built.request.system != load_prompt("description_v7")
    assert built.prompt_version != "description_v7"
    assert built.request.user == POSTING, "the user message is still the posting and nothing else"
    assert built.transport_version == 3
    estimate = estimate_request(built)
    assert estimate.static_measured, "the active version is measured, not estimated"


# =========================================================================
# 5. THE CONTRACT v7 STATES IS THE CONTRACT THE CODE ENFORCES
# =========================================================================


V7 = None


def v7_text() -> str:
    global V7
    if V7 is None:
        V7 = load_prompt("description_v7")
    return V7


def test_v7_names_the_authorised_top_level_properties_and_no_others() -> None:
    """Derived from `TDescriptionFamily`, not from any model's output.

    The schema forbids extra properties. Until v7 the prompt never said which
    ones existed, so a model without vendor enforcement had to guess -- and the
    guess that 33 dimensions were 33 top-level keys is one the instructions
    supported.
    """
    text = v7_text()
    authorised = set(TDescriptionFamily.model_fields)
    assert authorised == {
        "observed_title",
        "function_signals",
        "observations",
        "responsibilities",
        "software",
        "languages",
        "evidence",
    }
    for name in authorised:
        assert f"`{name}`" in text, f"v7 must name the top-level property {name}"

    assert TDescriptionFamily.model_config["extra"] == "forbid"


def test_v7_states_the_document_boundary() -> None:
    """The failure three separate routes produced: something around the JSON."""
    text = v7_text()
    for phrase in (
        "one JSON document",
        "first non-whitespace character is `{`",
        "last non-whitespace character is `}`",
        "no Markdown fence",
    ):
        assert phrase in text, phrase


def test_v7_states_the_cardinality_the_assembler_enforces() -> None:
    """One row per dimension -- the rule `index_observations` has always applied.

    Asserted against the real dimension count rather than a literal, so a future
    dimension makes this fail rather than letting the prompt go quietly stale.
    """
    text = v7_text()
    assert len(ALL_DIMENSIONS) == 33
    assert f"exactly one row for each of the {len(ALL_DIMENSIONS)} dimensions" in text


def test_v7_uses_the_authoritative_field_and_enum_names() -> None:
    """Names copied from the schema, not invented for the prose."""
    text = v7_text()
    for field in (
        "dimension",
        "status",
        "value",
        "confidence",
        "evidence_id",
        "not_applicable_because",
        "reasoning",
        "raw_mention",
        "canonical_suggestion",
        "alternative_group",
        "centrality",
        "language_code",
        "requirement_level",
        "category",
        "prominence",
        "raw_phrase",
        "source_kind",
    ):
        assert f"`{field}`" in text or f'"{field}"' in text, field
    assert "JOB_DESCRIPTION" in text


def test_the_skeleton_carries_no_answers() -> None:
    """A prompt that hands the benchmark its answer key measures nothing.

    Every leaf in the example is an angle-bracket descriptor or a neutral
    default, so nothing in it can be copied out as a finding.
    """
    text = v7_text()
    block = re.search(r"```json\n(.*?)\n```", text, re.S)
    assert block, "v7 carries one JSON skeleton"
    skeleton = block.group(1)

    parsed = json.loads(re.sub(r'"<[^"]*>"', '"PLACEHOLDER"', skeleton))
    assert set(parsed) == set(TDescriptionFamily.model_fields), (
        "the skeleton's top level is the schema's top level"
    )
    for value in re.findall(r':\s*"([^"]*)"', skeleton):
        assert value == "" or value.startswith("<") or value in {"ev_01", "JOB_DESCRIPTION"}, (
            f"{value!r} is a literal a model could copy as an answer"
        )


def test_v7_names_no_vendor_no_model_and_no_benchmark_case() -> None:
    """A production prompt that knows which benchmark it is being scored on is
    not measuring the model any more."""
    lowered = v7_text().lower()
    for forbidden in (
        "gemini",
        "gemma",
        "glm",
        "nemotron",
        "minimax",
        "openrouter",
        "cerebras",
        "gc-0",
        "gc-1",
        "gc-2",
        "anthropic",
        "openai",
    ):
        assert forbidden not in lowered, forbidden


# =========================================================================
# 6. NOTHING DOWNSTREAM MOVED
# =========================================================================


def test_v7_is_v6_plus_one_section_and_a_heading() -> None:
    """The delta, asserted as a delta.

    v7 must be reviewable as "v6, plus the output contract". Anything else is a
    prompt revision hiding inside a prompt revision.
    """
    v6 = load_prompt("description_v6")
    v7 = v7_text()

    shared = v6.replace("# description_v6\n", "", 1).rstrip("\n")
    assert shared in v7, "every byte of v6's guidance survives unedited in v7"
    added = v7.split(shared, 1)[1]
    assert added.count("\n## ") == 1, "exactly one new top-level section"
    assert "## The response document" in added


def test_no_repair_or_normalisation_was_added_anywhere() -> None:
    """v7 improves instructions. It must not have loosened enforcement.

    A fence-stripper, a JSON extractor or a flat-to-rows converter would each
    turn an invalid answer into a stored fingerprint, which is the one outcome
    every layer here exists to prevent.
    """
    import inspect

    from career_agent.llm import assemble as assemble_module
    from career_agent.pipeline import extract as extract_module

    sources = inspect.getsource(extract_module) + inspect.getsource(assemble_module)
    for smell in (
        "```",
        "strip_fence",
        "extract_json",
        "find_json",
        "json_repair",
        "lstrip('`",
        'lstrip("`',
        "removeprefix('```",
        'removeprefix("```',
    ):
        assert smell not in sources, f"output repair appeared: {smell}"

    # The parser is still a plain `json.loads` of the whole answer.
    interpret = inspect.getsource(extract_module._interpret)
    assert "json.loads(response.raw_text)" in interpret


def test_a_historically_invalid_answer_is_still_invalid() -> None:
    """The MiniMax topology, replayed against today's validator.

    Dimensions as top-level properties, no `observations` array. It failed
    before v7 and it fails after: v7 is an instruction, not an amnesty.
    """
    flat = {
        "observed_title": "Analyst",
        "seniority_signal": "JUNIOR",
        "team_context": "a team",
        "confidence": 0.82,
        "evidence": [],
    }
    with pytest.raises(Exception) as exc:
        TDescriptionFamily.model_validate(flat)
    assert "extra" in str(exc.value).lower() or "observations" in str(exc.value).lower()

    fenced = "```json\n{}\n```"
    with pytest.raises(json.JSONDecodeError):
        json.loads(fenced)


# =========================================================================
# 4. A PROMPT'S IDENTITY IS THE SAME ON EVERY MACHINE
# =========================================================================


def test_every_pinned_digest_is_the_digest_of_the_lf_bytes() -> None:
    """The repository stores LF (`.gitattributes`), so every clone holds these
    bytes; a pin over anything else is a pin over one machine's working copy.
    Two entries were exactly that until 2026-09-18, and a fresh clone refused
    `description_v2` and `description_v3` at load."""
    for version, pinned in PROMPT_DIGESTS.items():
        raw = (PROMPT_DIR / f"{version}.md").read_bytes()
        lf = raw.replace(b"\r\n", b"\n")
        assert "sha256:" + hashlib.sha256(lf).hexdigest() == pinned, version


def test_line_endings_do_not_change_a_prompts_identity_or_its_text() -> None:
    """The same instructions, checked out with CRLF or LF, are one version:
    one digest and one decoded text. What a model is sent does not depend on
    the operating system that checked the file out."""
    lf = b"# description_vX\n\nRead the posting.\n"
    crlf = lf.replace(b"\n", b"\r\n")
    assert canonical_bytes(crlf) == canonical_bytes(lf) == lf
    assert hashlib.sha256(canonical_bytes(crlf)).digest() == hashlib.sha256(lf).digest()
    assert canonical_bytes(crlf).decode("utf-8") == lf.decode("utf-8")
    # A lone carriage return is content, not a line ending, and is kept.
    assert canonical_bytes(b"a\rb\n") == b"a\rb\n"


def test_a_crlf_working_copy_loads_every_version_identically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's checkout once held two prompt files with CRLF endings. Such
    a working copy must load every version, verify every pin, and hand the
    model the same text an LF checkout does."""
    from career_agent.llm import prompts as module

    crlf_dir = tmp_path / "prompts"
    crlf_dir.mkdir()
    for path in PROMPT_DIR.glob("*.md"):
        lf = canonical_bytes(path.read_bytes())
        (crlf_dir / path.name).write_bytes(lf.replace(b"\n", b"\r\n"))
    expected = {version: load_prompt(version) for version in PROMPT_DIGESTS}

    monkeypatch.setattr(module, "PROMPT_DIR", crlf_dir)
    module.load_prompt.cache_clear()
    try:
        for version in PROMPT_DIGESTS:
            assert module.load_prompt(version) == expected[version], version
            assert "\r" not in module.load_prompt(version)
    finally:
        module.load_prompt.cache_clear()

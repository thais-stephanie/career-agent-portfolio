"""Cache identity is a definition, not an optimisation.

Two inputs share a key exactly when re-running is guaranteed to ask the same
question. Wrong in one direction we pay twice; wrong in the other a stale answer
survives a change that should have invalidated it -- and nothing looks broken.

The asymmetry tests are the ones that matter. Independent invalidation is a
concrete benefit the two-call topology was chosen for, so if these ever start
passing vacuously the topology has quietly lost one of its reasons to exist.
"""

from career_agent.llm.cache import (
    description_key,
    field_map_version,
    provider_key,
    static_digest,
)
from career_agent.llm.client import Family, ModelConfig

SONNET = ModelConfig(vendor="anthropic", identifier="claude-sonnet-5")
HAIKU = ModelConfig(vendor="anthropic", identifier="claude-haiku-4-5-20251001")

#: The digest of one arbitrary (prompt, schema) pair. These tests are about
#: what else moves a key, so the static half is held still except where a test
#: is specifically about moving it.
STATIC = static_digest("some prompt text", {"type": "object"}, "STRICT_SCHEMA")

MAP_V1 = field_map_version(
    "greenhouse",
    ("location.name", "metadata.remote"),
    ("hiring_location_hint", "work_model_hint"),
)


def desc(**kwargs) -> str:
    defaults = {
        "config": SONNET,
        "prompt_version": "description_v1",
        "transport_version": 1,
        "content_hash": "sha256:aaa",
        "static_digest": STATIC,
    }
    defaults.update(kwargs)
    return description_key(**defaults).key


BLOCK = "greenhouse\nhiring_location_hint\tlocation.name\tRemote - United States"


def prov(**kwargs) -> str:
    defaults = {
        "config": SONNET,
        "prompt_version": "provider_v1",
        "transport_version": 1,
        "block": BLOCK,
        "field_map_version": MAP_V1,
        "static_digest": STATIC,
    }
    defaults.update(kwargs)
    return provider_key(**defaults).key


# --- stability --------------------------------------------------------------


def test_identical_input_produces_an_identical_key() -> None:
    assert desc() == desc()
    assert prov() == prov()


def test_the_two_families_never_share_a_key() -> None:
    """Even given identical inputs, which they never have. A shared key would
    let one family's answer be served for the other's question."""
    assert desc() != prov()


# --- what must invalidate ---------------------------------------------------


def test_description_invalidates_on_content_change() -> None:
    assert desc() != desc(content_hash="sha256:zzz")


def test_description_invalidates_on_prompt_version() -> None:
    """Semantic prompt changes get a new version, and a new version is a new
    question. Whitespace edits do not, by the documented rule."""
    assert desc() != desc(prompt_version="description_v2")


def test_description_invalidates_on_transport_version() -> None:
    assert desc() != desc(transport_version=2)


def test_description_invalidates_on_model() -> None:
    assert desc() != desc(config=HAIKU)


def test_description_invalidates_on_reasoning_configuration() -> None:
    """The same model at two reasoning settings is two candidates.

    Sharing a key between them would silently serve one arm's answers to the
    other, which would make a benchmark comparing them meaningless.
    """
    low = ModelConfig(vendor="anthropic", identifier="claude-sonnet-5", reasoning="low")
    high = ModelConfig(vendor="anthropic", identifier="claude-sonnet-5", reasoning="high")

    assert desc(config=low) != desc(config=high)
    assert desc(config=low) != desc(config=SONNET)


def test_provider_invalidates_when_the_mapped_values_change() -> None:
    changed = "greenhouse\nhiring_location_hint\tlocation.name\tRemote - Brazil"
    assert prov() != prov(block=changed)


# --- the field-map trap -----------------------------------------------------


def test_provider_invalidates_when_the_field_map_changes() -> None:
    """The provider call sees mapped observations, not the payload.

    So a mapping change alters its input while `payload_hash` stays
    byte-identical. Without the field-map version in the key, a stale
    extraction survives a mapping-contract change invisibly.
    """
    with_extra_path = field_map_version(
        "greenhouse",
        ("location.name", "metadata.remote", "offices"),
        ("hiring_location_hint", "work_model_hint", "hiring_location_hint"),
    )

    assert prov() != prov(field_map_version=with_extra_path)


def test_moving_a_path_to_another_dimension_invalidates() -> None:
    """The case a path counter would miss.

    Same provider, same number of paths, same payload -- but one path now means
    something else, so the observations handed to the model mean something else.
    """
    remapped = field_map_version(
        "greenhouse",
        ("location.name", "metadata.remote"),
        ("work_model_hint", "hiring_location_hint"),  # swapped
    )

    assert remapped != MAP_V1
    assert prov() != prov(field_map_version=remapped)


def test_provider_invalidates_on_metadata_vocabulary_version() -> None:
    """A re-scoped MetadataDimension changes what an observation means, not
    merely how many there are."""
    assert prov() != prov(metadata_vocabulary_version=2)


# --- independent invalidation: the topology's practical dividend ------------


def test_a_metadata_change_does_not_invalidate_the_description() -> None:
    """An employer editing an ATS location field must not re-run, and re-pay
    for: a description extraction that could not possibly have differed.

    Under a single-call topology every payload edit invalidates everything.
    This is the concrete saving the split buys.
    """
    changed = "greenhouse\nhiring_location_hint\tlocation.name\tRemote - Brazil"
    before = desc()
    assert prov() != prov(block=changed)
    assert desc() == before


# --- keying on the block rather than the payload ----------------------------


def test_two_jobs_with_identical_metadata_share_one_provider_call() -> None:
    """The measurement that changed this key.

    The provider call's entire input is the mapped block, so two postings whose
    blocks render identically are asking the model the same question word for
    word. Keyed on `payload_hash`: unique per posting, each would have been
    its own cache entry.

    On the real corpus this collapses 18,551 jobs to 5,609 distinct provider
    inputs: 69.8% of calls avoided, 26.1M fewer input tokens across a full
    backfill. `United States` alone appears 3,142 times as a location hint.
    """
    same_block_different_jobs = prov(), prov()

    assert same_block_different_jobs[0] == same_block_different_jobs[1]


def test_the_same_value_from_two_vendors_does_not_share_a_call() -> None:
    """Identical text, different provenance.

    The cached answer carries evidence naming a provider and a payload path,
    and one vendor's evidence cannot verify against another's payload.
    """
    lever_block = "lever\nhiring_location_hint\tcategories.location\tRemote - United States"

    assert prov() != prov(block=lever_block)


def test_the_same_value_at_a_different_path_does_not_share_a_call() -> None:
    """Same reason: the evidence cites the path, and the path is part of what
    must later resolve against the archived payload."""
    other_path = "greenhouse\nhiring_location_hint\toffices.0.name\tRemote - United States"

    assert prov() != prov(block=other_path)


def test_a_description_change_does_not_invalidate_the_provider_family() -> None:
    """And the reverse: an employer rewording the posting body has said nothing
    new about the ATS record."""
    before = prov()
    assert desc() != desc(content_hash="sha256:changed")
    assert prov() == before


def test_a_description_prompt_change_does_not_invalidate_the_provider_family() -> None:
    """Versioning one prompt must not throw away unrelated cached work."""
    before = prov()
    assert desc() != desc(prompt_version="description_v2")
    assert prov() == before


# --- key construction -------------------------------------------------------


def test_parts_cannot_collide_across_a_boundary() -> None:
    """('ab','c') and ('a','bc') must not digest identically.

    A separator-free join makes that collision possible, and it would produce
    one wrong cached answer in many thousands: the kind that is never found.
    """
    assert desc(prompt_version="ab", content_hash="c") != desc(
        prompt_version="a", content_hash="bc"
    )


def test_the_key_records_the_inputs_that_produced_it() -> None:
    """A key nobody can explain is a key nobody can debug."""
    result = description_key(SONNET, "description_v1", 1, "sha256:aaa", STATIC)

    assert result.family is Family.DESCRIPTION
    assert "claude-sonnet-5" in result.inputs
    assert "sha256:aaa" in result.inputs
    assert STATIC in result.inputs


# --- the static half is part of the question, not a label on it -------------


def test_editing_the_prompt_text_moves_the_key() -> None:
    """The defect this closes, stated as the smallest possible case.

    Before `static_digest`, the system prompt entered the key as a filename.
    Rewriting the instructions under an unchanged version name produced an
    unchanged key, so every answer to the old instructions was served as an
    answer to the new ones -- silently, which the module docstring names as the
    worse of the two ways to be wrong.

    `description_v6` was compressed by 685 tokens while its cache identity did
    not move by a byte. That was harmless only because it had never been sent
    to a vendor, and nothing in the code knew that.
    """
    one = static_digest("read the posting", {"type": "object"}, "STRICT_SCHEMA")
    two = static_digest("read the posting carefully", {"type": "object"}, "STRICT_SCHEMA")

    assert one != two
    assert desc(static_digest=one) != desc(static_digest=two)
    assert prov(static_digest=one) != prov(static_digest=two)


def test_editing_the_schema_moves_the_key() -> None:
    """A transport integer is a label too, and schemas drift the same way."""
    one = static_digest("prompt", {"type": "object", "properties": {"a": {}}}, "S")
    two = static_digest("prompt", {"type": "object", "properties": {"b": {}}}, "S")

    assert desc(static_digest=one) != desc(static_digest=two)


def test_the_static_digest_ignores_key_order_but_not_content() -> None:
    """Pydantic may reorder a schema between releases; that is not a new question.

    Serialised with sorted keys for exactly that reason. A reordering that moved
    every key would re-ask the whole corpus for nothing.
    """
    assert static_digest("p", {"a": 1, "b": 2}, "STRICT_SCHEMA") == static_digest(
        "p", {"b": 2, "a": 1}, "STRICT_SCHEMA"
    )
    assert static_digest("p", {"a": 1, "b": 2}, "STRICT_SCHEMA") != static_digest(
        "p", {"a": 1, "b": 3}, "STRICT_SCHEMA"
    )


# --- one semantic request, many arms ----------------------------------------


def test_the_same_question_to_a_different_model_is_a_different_key() -> None:
    """No cross-model reuse, ever, and it must not depend on prompts differing.

    Every arm below is handed byte-identical instructions, an identical schema
    and an identical posting. The only thing that varies is who is being asked,
    and that alone has to separate them -- otherwise a benchmark could serve one
    model's answer as another's and report the result as a comparison.

    This is what makes the funnel meaningful: Gemini's 27 stored answers cannot
    satisfy a challenger, and no challenger can satisfy another.
    """
    from career_agent.llm.client import ModelConfig

    arms = [
        ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite"),
        ModelConfig(vendor="openai", identifier="gpt-5.6-luna"),
        ModelConfig(vendor="openai", identifier="gpt-5.6-luna", reasoning="low"),
        ModelConfig(vendor="openai", identifier="gpt-5.6-luna", reasoning="high"),
        ModelConfig(vendor="cowork", identifier="cowork-development"),
    ]

    keys = {
        description_key(arm, "description_v6", 3, "sha256:one-posting", STATIC).key for arm in arms
    }

    assert len(keys) == len(arms), "two arms share a cache key"


def test_a_guaranteed_schema_and_a_requested_one_are_different_questions() -> None:
    """The Cerebras arm's whole shape, protected as a cache property.

    That route enforces a strict schema of at most 5,000 characters. Our provider
    schema is 4,038 and our description schema 8,536, so the honest arm runs one
    family under a vendor guarantee and the other without one.

    Which means both modes will exist in the same database, for the same model,
    on the same postings. An answer given under PLAIN_JSON must never be served
    as one given under STRICT_SCHEMA: the second claims a guarantee that was not
    in force, and it does so on the exact metric the difference shows up in --
    first-attempt schema pass rate, where a failure means a vendor defect in one
    mode and the model's own formatting in the other.
    """
    strict = static_digest("prompt", {"type": "object"}, "STRICT_SCHEMA")
    plain = static_digest("prompt", {"type": "object"}, "PLAIN_JSON")

    assert strict != plain
    assert desc(static_digest=strict) != desc(static_digest=plain)
    assert prov(static_digest=strict) != prov(static_digest=plain)


def test_one_arm_may_hold_two_modes_at_once_without_the_families_colliding() -> None:
    """Per family, not per model -- which is why the arm label does not carry it.

    `ModelConfig.arm` stays `gpt-oss-120b` under both modes, so every per-model
    report reads cleanly; the separation happens in the key, where a change to
    the request itself belongs.
    """
    from career_agent.llm.client import Family, ModelConfig, StructuredOutput
    from career_agent.llm.vendors.openai_compatible import CEREBRAS_PER_FAMILY

    config = ModelConfig(
        vendor="cerebras",
        identifier="gpt-oss-120b",
        per_family=CEREBRAS_PER_FAMILY,
    )

    assert config.output_mode(Family.PROVIDER) is StructuredOutput.STRICT_SCHEMA
    assert config.output_mode(Family.DESCRIPTION) is StructuredOutput.PLAIN_JSON
    assert config.arm == "gpt-oss-120b"

    description = desc(
        config=config,
        static_digest=static_digest("p", {}, config.output_mode(Family.DESCRIPTION)),
    )
    provider = prov(
        config=config,
        static_digest=static_digest("p", {}, config.output_mode(Family.PROVIDER)),
    )
    assert description != provider

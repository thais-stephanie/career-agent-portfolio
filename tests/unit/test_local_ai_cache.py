"""The local cache key, and the answers it is allowed to hold.

Two lessons, both already paid for elsewhere in this repository:

**Editing a prompt in place must never serve a stale answer.** The hosted cache
keyed its static half by filename and version integer, so a prompt edited
without a rename produced no new key. `local_cache_key` hashes the prompt BYTES
as well as the version name, and the tests below check each of the six inputs
independently -- a key that changes when "something" changes is not the same
guarantee as a key that changes when *each* thing changes.

**An invalid answer is neither accepted nor cached.** `put` refuses rather than
warns, because a cache entry is a claim that this work is done: written once, it
is served forever and nothing looks at it again. That is proof 19.
"""

import json
from pathlib import Path

import pytest

from career_agent.local_ai.cache import (
    LocalEnrichmentCache,
    local_cache_key,
    settings_digest,
    text_digest,
)
from career_agent.local_ai.contract import (
    EvidencedItem,
    LocalEnrichment,
    VerifiedEnrichment,
    verify,
)
from career_agent.local_ai.ollama import OllamaSettings
from career_agent.local_ai.prompt import LOCAL_PROMPT_VERSION, PROMPT_DIGEST

SOURCE = "We build integrations in Python and expect strong SQL."

BASE_KEY_ARGS = {
    "description_digest": text_digest(SOURCE),
    "profile_digest": text_digest("systems and automation, Python and SQL"),
    "model": "qwen3:4b",
    "prompt_version": LOCAL_PROMPT_VERSION,
    "prompt_digest": PROMPT_DIGEST,
    "settings_digest": settings_digest(OllamaSettings().digest_material()),
}


def _acceptable() -> VerifiedEnrichment:
    return verify(
        LocalEnrichment(
            summary="Integration work in Python with SQL.",
            technologies=[EvidencedItem(text="Python", quote="integrations in Python")],
            recommended_action="SKIM",
            confidence="MEDIUM",
        ),
        SOURCE,
    )


def _unacceptable() -> VerifiedEnrichment:
    return verify(
        LocalEnrichment(
            summary="Integration work.",
            technologies=[
                EvidencedItem(text="Kubernetes", quote="you will run our Kubernetes clusters"),
                EvidencedItem(text="Kafka", quote="an event bus built on Kafka"),
            ],
        ),
        SOURCE,
    )


# --- the key --------------------------------------------------------------


def test_key_is_stable_for_identical_inputs() -> None:
    assert local_cache_key(**BASE_KEY_ARGS) == local_cache_key(**BASE_KEY_ARGS)
    assert len(local_cache_key(**BASE_KEY_ARGS)) == 64


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("description_digest", text_digest(SOURCE + " Occasional travel.")),
        ("profile_digest", text_digest("something else entirely")),
        ("model", "llama3.2:3b"),
        ("prompt_version", "local_enrichment_v2"),
        ("prompt_digest", "sha256:" + "0" * 64),
    ],
)
def test_every_input_moves_the_key(field: str, changed: str) -> None:
    """Each input is checked on its own. A key that moves when 'something'
    changes is a weaker promise than one that moves when *each* thing does."""
    other = dict(BASE_KEY_ARGS) | {field: changed}
    assert local_cache_key(**other) != local_cache_key(**BASE_KEY_ARGS)


def test_changing_the_prompt_bytes_changes_the_key() -> None:
    """The whole reason `prompt_digest` is in the key alongside `prompt_version`:
    the name can stay identical while the question changes underneath it."""
    edited = dict(BASE_KEY_ARGS) | {"prompt_digest": "sha256:" + "a" * 64}
    assert local_cache_key(**edited) != local_cache_key(**BASE_KEY_ARGS)


def test_changing_temperature_changes_the_key() -> None:
    hotter = settings_digest(OllamaSettings(temperature=0.7).digest_material())
    other = dict(BASE_KEY_ARGS) | {"settings_digest": hotter}
    assert local_cache_key(**other) != local_cache_key(**BASE_KEY_ARGS)


@pytest.mark.parametrize(
    "settings",
    [
        OllamaSettings(model="llama3.2:3b"),
        OllamaSettings(seed=7),
        OllamaSettings(think=True),
        OllamaSettings(num_ctx=16384),
    ],
)
def test_generation_settings_are_part_of_the_identity(settings: OllamaSettings) -> None:
    baseline = settings_digest(OllamaSettings().digest_material())
    assert settings_digest(settings.digest_material()) != baseline


def test_where_it_runs_is_not_part_of_the_identity() -> None:
    """A different port or a longer timeout does not change what the model says,
    so it must not invalidate every stored answer."""
    baseline = settings_digest(OllamaSettings().digest_material())
    moved = OllamaSettings(base_url="http://127.0.0.1:9999", timeout_ms=60_000)
    assert settings_digest(moved.digest_material()) == baseline


# --- the store ------------------------------------------------------------


def test_round_trip_returns_an_equal_document(tmp_path: Path) -> None:
    cache = LocalEnrichmentCache(tmp_path)
    key = local_cache_key(**BASE_KEY_ARGS)
    stored = _acceptable()

    cache.put(key, stored, {"eval_count": 42})
    assert cache.get(key) == stored


def test_a_miss_is_none_not_an_error(tmp_path: Path) -> None:
    assert LocalEnrichmentCache(tmp_path).get("0" * 64) is None


def test_invalid_output_is_neither_accepted_nor_cached(tmp_path: Path) -> None:
    """Proof 19. Every quote fabricated -> not acceptable -> put refuses ->
    get still misses, so the next run re-asks a free local model."""
    cache = LocalEnrichmentCache(tmp_path)
    key = local_cache_key(**BASE_KEY_ARGS)
    rubbish = _unacceptable()

    assert rubbish.is_acceptable is False
    with pytest.raises(ValueError):
        cache.put(key, rubbish, {})
    assert cache.get(key) is None
    assert cache.stats()["entries"] == 0


def test_a_corrupt_entry_is_a_miss_not_a_crash(tmp_path: Path) -> None:
    """The cache is an optimisation and must never be able to break a run."""
    cache = LocalEnrichmentCache(tmp_path)
    key = local_cache_key(**BASE_KEY_ARGS)
    cache.put(key, _acceptable(), {})

    path = tmp_path / key[:2] / f"{key}.json"
    path.write_text("{ truncated", encoding="utf-8")
    assert cache.get(key) is None


def test_an_entry_from_another_schema_version_is_a_miss(tmp_path: Path) -> None:
    cache = LocalEnrichmentCache(tmp_path)
    key = local_cache_key(**BASE_KEY_ARGS)
    cache.put(key, _acceptable(), {})

    path = tmp_path / key[:2] / f"{key}.json"
    envelope = json.loads(path.read_text(encoding="utf-8"))
    envelope["schema_version"] = 99
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cache.get(key) is None


def test_the_envelope_records_how_the_answer_was_produced(tmp_path: Path) -> None:
    cache = LocalEnrichmentCache(tmp_path)
    key = local_cache_key(**BASE_KEY_ARGS)
    cache.put(key, _acceptable(), {"model": "qwen3:4b", "eval_count": 42})

    envelope = json.loads((tmp_path / key[:2] / f"{key}.json").read_text(encoding="utf-8"))
    assert envelope["key"] == key
    assert envelope["stats"]["model"] == "qwen3:4b"
    assert envelope["stored_at"].endswith("Z")


def test_stats_counts_entries_and_bytes(tmp_path: Path) -> None:
    cache = LocalEnrichmentCache(tmp_path)
    assert cache.stats() == {"entries": 0, "bytes": 0}

    cache.put(local_cache_key(**BASE_KEY_ARGS), _acceptable(), {})
    other = dict(BASE_KEY_ARGS) | {"model": "llama3.2:3b"}
    cache.put(local_cache_key(**other), _acceptable(), {})

    stats = cache.stats()
    assert stats["entries"] == 2
    assert stats["bytes"] > 0

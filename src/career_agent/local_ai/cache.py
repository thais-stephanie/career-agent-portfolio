"""The local cache, and the six things that identify an answer.

THE KEY IS THE LESSON
---------------------
CLAUDE.md records what the hosted cache got wrong and what it cost: the static
half of the key entered by *filename and integer*, so editing a prompt in place
produced no new key and stale answers were served silently to a changed
question. The fix there was `cache.static_digest` -- hash the bytes, not the
name.

This cache starts there. `local_cache_key` hashes six values and nothing is
implicit in any of them:

    description_digest   which posting text was actually sent
    profile_digest       which capability line was actually sent
    model                which weights answered
    prompt_version       which named question was asked
    prompt_digest        which BYTES that name referred to
    settings_digest      temperature, seed, think, num_ctx

`prompt_version` and `prompt_digest` are both present on purpose. They ask
different questions -- *which question is this* and *has that question mutated*
-- and either alone can be satisfied while the other is violated.

WHAT MAY BE STORED
------------------
Only an acceptable answer. `put` raises on anything else rather than writing it,
because a cache entry is a promise that this work is done: an unacceptable
enrichment written once would be served forever, and re-running a free local
model is the cheapest recovery available anywhere in this system.

Reads are forgiving in the other direction: a corrupt or stale-schema entry is a
miss, never an error. The cache is an optimisation and must not be able to break
a run -- the same rule `net/fetcher.ResponseCache` follows.
"""

import contextlib
import hashlib
import json
from pathlib import Path
from typing import Any

from career_agent.clock import now_utc
from career_agent.local_ai.contract import LOCAL_ENRICHMENT_SCHEMA_VERSION, VerifiedEnrichment

#: `out/` is already gitignored. Local enrichments are derived, reproducible and
#: contain posting text, so they are not corpus and not committed.
DEFAULT_CACHE_ROOT = Path("out") / "local_ai"


def text_digest(text: str) -> str:
    """sha256 of the exact bytes. Used for the description and the profile line.

    Deliberately not normalised: this identifies *what was sent*, which is a
    different question from whether two texts mean the same thing. Normalisation
    belongs to verification, where forgiving typography is the point.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def local_cache_key(
    *,
    description_digest: str,
    profile_digest: str,
    model: str,
    prompt_version: str,
    prompt_digest: str,
    settings_digest: str,
) -> str:
    """The identity of one local answer. Changing any input changes the key."""
    material = {
        "description_digest": description_digest,
        "profile_digest": profile_digest,
        "model": model,
        "prompt_version": prompt_version,
        "prompt_digest": prompt_digest,
        "settings_digest": settings_digest,
        "schema_version": LOCAL_ENRICHMENT_SCHEMA_VERSION,
    }
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def settings_digest(material: dict[str, Any]) -> str:
    """Hash `OllamaSettings.digest_material()` into one stable string.

    Takes the plain dict rather than the settings object so `cache.py` does not
    import `ollama.py`: the cache stores answers, and needs to know nothing
    about how they were obtained.
    """
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class LocalEnrichmentCache:
    """Plain JSON files under a root directory. One file per key.

    Sharded two characters deep, like `net.fetcher.ResponseCache`, because a
    single directory holding thousands of entries is slow to list on Windows and
    unpleasant to inspect by hand.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> VerifiedEnrichment | None:
        """The stored answer, or None. Never raises on a damaged entry."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if envelope["schema_version"] != LOCAL_ENRICHMENT_SCHEMA_VERSION:
                return None
            return VerifiedEnrichment.model_validate(envelope["enrichment"])
        except Exception:
            # Corrupt, truncated, or written by an older shape. All of them are
            # a miss: re-running a free local model costs nothing, and raising
            # here would let a bad file break every future run.
            return None

    def put(self, key: str, verified: VerifiedEnrichment, stats: dict[str, Any]) -> None:
        """Store an answer, or refuse.

        Raises `ValueError` when the answer is not acceptable. This is the one
        place the rule is enforced, and it is enforced by refusing rather than
        by warning: an unacceptable answer that reaches disk is served as a hit
        forever, and no later check would ever look at it again.
        """
        if not verified.is_acceptable:
            raise ValueError(
                "refusing to cache an unacceptable enrichment "
                f"({verified.verified_count} verified, {verified.rejected_count} rejected, "
                f"summary {'present' if verified.summary.strip() else 'empty'}). "
                "A cached answer is a claim that this work is done; an invalid one "
                "would be served as a hit forever, and re-running a local model is free."
            )

        envelope = {
            "schema_version": LOCAL_ENRICHMENT_SCHEMA_VERSION,
            "key": key,
            "stored_at": now_utc(),
            "stats": stats,
            "enrichment": verified.model_dump(mode="json"),
        }
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")

    def stats(self) -> dict[str, int]:
        """How many entries there are and how much disk they hold."""
        entries = 0
        total_bytes = 0
        if self.root.exists():
            for path in self.root.rglob("*.json"):
                entries += 1
                with contextlib.suppress(OSError):
                    total_bytes += path.stat().st_size
        return {"entries": entries, "bytes": total_bytes}

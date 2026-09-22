"""The enrichment orchestrator, which is where the local-model boundaries live.

`career_agent.local_ai` has ninety tests over its parts. This file covers the
thing that *uses* them, which had none: `pipeline.enrich.enrich_one` is the only
module in the product path that opens a socket, and it alone enforces the triage
threshold, the cache-hit path, the two-attempt cap, and the rule that an
unverifiable answer is never stored.

Every test here runs against a fake client and a fake cache. Nothing in this
file reaches Ollama, and `_RefusingClient` exists so that "the model was never
contacted" is an assertion rather than an assumption.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from career_agent.domain.enums import CollectionStatus
from career_agent.local_ai.cache import LocalEnrichmentCache
from career_agent.local_ai.contract import LocalEnrichment
from career_agent.local_ai.ollama import OllamaInvalidOutput, OllamaSettings, OllamaUnavailable
from career_agent.pipeline.enrich import (
    EnrichmentRejected,
    EnrichmentUnavailable,
    enrich_one,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import MatchRepo
from career_agent.storage.records import CompanyRecord, JobRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, JobRawRepo, JobRepo, SourceBoardRepo

CONFIG_ID = "personal-alpha"
CONFIG_VERSION = 1
DIGEST = "b" * 64

POSTING = (
    "We are hiring a Business Systems Engineer. You will own our CRM architecture, "
    "build workflow automation across our business systems, and maintain REST API "
    "integrations between them. We hire globally and work from anywhere."
)


# =========================================================================
# fakes
# =========================================================================


class _FakeClient:
    """Answers whatever the test hands it, and records that it was asked."""

    def __init__(self, answers: list[Any], models: list[str] | None = None) -> None:
        self.answers = list(answers)
        self.models = models if models is not None else ["qwen3:4b"]
        self.calls = 0
        self.health_calls = 0

    def health(self) -> list[str]:
        self.health_calls += 1
        return self.models

    def is_model_available(self) -> bool:
        return bool(self.models)

    def enrich(self, messages: list[dict[str, str]], *, schema: dict) -> tuple[Any, dict]:
        self.calls += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer, {"eval_count": 10, "model": "qwen3:4b"}


class _RefusingClient:
    """Fails the test if anything touches it.

    Used where the assertion is "the model was never contacted": a gate that
    silently passed would otherwise look identical to one that held.
    """

    def health(self) -> list[str]:
        raise AssertionError("the local model was contacted when it should not have been")

    def is_model_available(self) -> bool:
        raise AssertionError("the local model was contacted when it should not have been")

    def enrich(self, messages: list[dict[str, str]], *, schema: dict) -> tuple[Any, dict]:
        raise AssertionError("the local model was contacted when it should not have been")


class _UnreachableClient(_FakeClient):
    def health(self) -> list[str]:
        raise OllamaUnavailable("connection refused")


def _enrichment(*, quote: str, summary: str = "A business systems role.") -> LocalEnrichment:
    return LocalEnrichment(
        summary=summary,
        technologies=[{"text": "CRM", "quote": quote}],
        strengths=[{"text": "CRM architecture", "quote": quote}],
        gaps=[],
        risk_flags=[],
        recommended_action="READ_IN_FULL",
        confidence="HIGH",
    )


# =========================================================================
# fixtures
# =========================================================================


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    conn = connect(tmp_path / "enrich.db")
    migrate(conn)
    return conn


@pytest.fixture
def cache(tmp_path: Path) -> LocalEnrichmentCache:
    return LocalEnrichmentCache(tmp_path / "cache")


@pytest.fixture
def settings() -> OllamaSettings:
    return OllamaSettings(base_url="http://127.0.0.1:11434", model="qwen3:4b")


def _seed(conn: sqlite3.Connection, *, score: int) -> str:
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company_id,
                provider="manual_import",
                board_identifier="acme",
                board_url="manual://acme",
                active=False,
            )
        )
        digest = JobRawRepo(conn).put(POSTING)
        job_id = JobRepo(conn).upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider="manual_import",
                external_id="seed-1",
                url="manual://seed-1",
                title="Business Systems Engineer",
                content_hash=digest,
            ),
            status=CollectionStatus.NORMALISED,
        )

    from career_agent.domain.matching import MatchResult

    with transaction(conn):
        MatchRepo(conn).store(
            job_id,
            digest,
            MatchResult(
                config_id=CONFIG_ID,
                config_version=CONFIG_VERSION,
                match_score=score,
                data_confidence=50,
                computed_at="2026-09-04T00:00:00Z",
            ),
            config_digest=DIGEST,
        )
    return job_id


def _stored(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM job_enrichment").fetchall()


def _run(conn, job_id, *, client, cache, settings, **kwargs):
    return enrich_one(
        conn,
        job_id,
        config_id=CONFIG_ID,
        config_version=CONFIG_VERSION,
        client=client,
        cache=cache,
        settings=settings,
        **kwargs,
    )


# =========================================================================
# the triage gate
# =========================================================================


def test_a_job_below_the_threshold_never_reaches_the_model(db, cache, settings) -> None:
    """Deterministic triage runs first. That is the whole ordering claim."""
    job_id = _seed(db, score=20)
    with pytest.raises(EnrichmentRejected, match="threshold"):
        _run(db, job_id, client=_RefusingClient(), cache=cache, settings=settings, min_score=60)
    assert _stored(db) == []


def test_an_unscored_job_is_refused_rather_than_enriched_anyway(db, cache, settings) -> None:
    """No score is not a low score; it is no basis for spending the laptop."""
    job_id = _seed(db, score=90)
    db.execute("DELETE FROM job_match")
    with pytest.raises(EnrichmentRejected):
        _run(db, job_id, client=_RefusingClient(), cache=cache, settings=settings, min_score=60)


def test_a_job_at_the_threshold_is_allowed(db, cache, settings) -> None:
    job_id = _seed(db, score=60)
    client = _FakeClient([_enrichment(quote="CRM architecture")])
    _run(db, job_id, client=client, cache=cache, settings=settings, min_score=60)
    assert client.calls == 1
    assert len(_stored(db)) == 1


def test_no_threshold_means_no_gate(db, cache, settings) -> None:
    """`--ignore-threshold` exists, and it must actually be the only way past."""
    job_id = _seed(db, score=5)
    client = _FakeClient([_enrichment(quote="CRM architecture")])
    _run(db, job_id, client=client, cache=cache, settings=settings, min_score=None)
    assert client.calls == 1


# =========================================================================
# availability
# =========================================================================


def test_an_unreachable_ollama_is_reported_and_writes_nothing(db, cache, settings) -> None:
    job_id = _seed(db, score=90)
    with pytest.raises(EnrichmentUnavailable, match="not running"):
        _run(db, job_id, client=_UnreachableClient([]), cache=cache, settings=settings)
    assert _stored(db) == []


def test_a_missing_model_names_the_pull_command(db, cache, settings) -> None:
    job_id = _seed(db, score=90)
    client = _FakeClient([], models=[])
    with pytest.raises(EnrichmentUnavailable, match="ollama pull"):
        _run(db, job_id, client=client, cache=cache, settings=settings)
    assert _stored(db) == []


def test_a_non_loopback_endpoint_is_refused_before_any_request(db, cache) -> None:
    """The refusal is a refusal, not an outage, and the message says so.

    `client=None` forces the real constructor, which is where `assert_loopback`
    runs -- so this exercises the boundary rather than a fake standing in for it.
    """
    job_id = _seed(db, score=90)
    remote = OllamaSettings(base_url="http://ollama.example.com:11434", model="qwen3:4b")
    with pytest.raises(EnrichmentUnavailable, match="non-local"):
        enrich_one(
            db,
            job_id,
            config_id=CONFIG_ID,
            config_version=CONFIG_VERSION,
            client=None,
            cache=cache,
            settings=remote,
        )
    assert _stored(db) == []


# =========================================================================
# verification -- the reason the module exists
# =========================================================================


def test_an_answer_whose_quotes_are_fabricated_is_never_stored(db, cache, settings) -> None:
    """Proof 19, at the orchestrator rather than at the cache.

    Both attempts cite text that is not in the posting. Nothing is written, the
    cache stays empty, and the failure names what went wrong.
    """
    job_id = _seed(db, score=90)
    fabricated = _enrichment(quote="a sentence that does not appear anywhere")
    client = _FakeClient([fabricated, fabricated])

    with pytest.raises(EnrichmentRejected, match="not in the posting"):
        _run(db, job_id, client=client, cache=cache, settings=settings)

    assert client.calls == 2, "one attempt and exactly one retry"
    assert _stored(db) == []
    assert cache.stats()["entries"] == 0


def test_unverifiable_items_are_dropped_and_the_rest_is_kept(db, cache, settings) -> None:
    """A partly-hallucinated answer is salvaged, not discarded, and the drop counted."""
    job_id = _seed(db, score=90)
    mixed = LocalEnrichment(
        summary="A business systems role.",
        technologies=[
            {"text": "CRM", "quote": "CRM architecture"},
            {"text": "Kubernetes", "quote": "Kubernetes and service meshes"},
        ],
        strengths=[{"text": "automation", "quote": "workflow automation"}],
        gaps=[],
        risk_flags=[],
        recommended_action="READ_IN_FULL",
        confidence="MEDIUM",
    )
    _run(db, job_id, client=_FakeClient([mixed]), cache=cache, settings=settings)

    row = _stored(db)[0]
    assert row["verified_count"] == 2
    assert row["rejected_count"] == 1, "the invented Kubernetes quote must be dropped"


def test_a_retry_recovers_from_one_unusable_answer(db, cache, settings) -> None:
    job_id = _seed(db, score=90)
    client = _FakeClient([OllamaInvalidOutput("not JSON"), _enrichment(quote="CRM architecture")])
    _run(db, job_id, client=client, cache=cache, settings=settings)
    assert client.calls == 2
    assert len(_stored(db)) == 1


def test_the_attempt_cap_is_two(db, cache, settings) -> None:
    """Never a retry loop. A model that cannot answer twice is telling us something."""
    job_id = _seed(db, score=90)
    client = _FakeClient([OllamaInvalidOutput("bad")] * 5)
    with pytest.raises(EnrichmentRejected):
        _run(db, job_id, client=client, cache=cache, settings=settings)
    assert client.calls == 2


# =========================================================================
# caching and persistence
# =========================================================================


def test_an_accepted_answer_is_cached_and_the_second_run_asks_nobody(db, cache, settings) -> None:
    job_id = _seed(db, score=90)
    client = _FakeClient([_enrichment(quote="CRM architecture")])
    _run(db, job_id, client=client, cache=cache, settings=settings)
    assert client.calls == 1

    state = _run(db, job_id, client=_RefusingClient(), cache=cache, settings=settings)
    assert "cache" in state["note"]
    assert len(_stored(db)) == 1, "the cache hit refreshes the row, it does not add one"


def test_the_stored_row_records_where_it_ran(db, cache, settings) -> None:
    """A row whose endpoint is not loopback is a bug worth being able to query for."""
    job_id = _seed(db, score=90)
    _run(
        db,
        job_id,
        client=_FakeClient([_enrichment(quote="CRM architecture")]),
        cache=cache,
        settings=settings,
    )
    row = _stored(db)[0]
    assert row["endpoint"] == "http://127.0.0.1:11434"
    assert row["model"] == "qwen3:4b"
    assert row["prompt_version"] == "local_enrichment_v1"


def test_enrichment_never_touches_the_hosted_ledger(db, cache, settings) -> None:
    """ADR-0010's pause, asserted rather than assumed."""
    job_id = _seed(db, score=90)
    _run(
        db,
        job_id,
        client=_FakeClient([_enrichment(quote="CRM architecture")]),
        cache=cache,
        settings=settings,
    )
    assert db.execute("SELECT COUNT(*) FROM llm_call").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM fingerprint").fetchone()[0] == 0


def test_enrichment_never_changes_the_score(db, cache, settings) -> None:
    """The observation sits beside the score. There is no path from one to the other."""
    job_id = _seed(db, score=90)
    before = db.execute("SELECT match_score, data_confidence FROM job_match").fetchone()
    _run(
        db,
        job_id,
        client=_FakeClient([_enrichment(quote="CRM architecture")]),
        cache=cache,
        settings=settings,
    )
    after = db.execute("SELECT match_score, data_confidence FROM job_match").fetchone()
    assert (before["match_score"], before["data_confidence"]) == (
        after["match_score"],
        after["data_confidence"],
    )


def test_a_missing_job_is_a_rejection_not_a_crash(db, cache, settings) -> None:
    with pytest.raises(EnrichmentRejected, match="no such job"):
        _run(db, "01NOPE", client=_RefusingClient(), cache=cache, settings=settings)


def test_no_environment_value_reaches_an_error_message(db, cache, settings) -> None:
    """A misconfiguration must be diagnosable without printing a secret."""
    job_id = _seed(db, score=10)
    with pytest.raises(EnrichmentRejected) as exc:
        _run(db, job_id, client=_RefusingClient(), cache=cache, settings=settings, min_score=60)
    message = str(exc.value)
    for forbidden in ("API_KEY", "nvapi", "sk-", "AIza"):
        assert forbidden not in message

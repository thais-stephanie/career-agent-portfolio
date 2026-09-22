"""A schema-valid answer is not an accepted answer.

`validated_ok` says the transport document parsed and matched its schema. The
semantic cache read it as "this answer was accepted" and served it, and for a
long time those were the same thing by accident.

They came apart on the first FUNCTION_CALL canary: 33 observations, 33 unique
dimensions, every field the right type -- and eighteen evidence entries carrying
real posting sentences in `source_value` with `quote` null, cited by none of the
twelve EXPLICIT observations. Evidence verification 0 of 18. `assemble` refused
it. `validated_answer` returned it.

So admission has its own column and its own gate. These tests are the proof that
the gate is real, that it cannot be bypassed, and that refusing an answer does
not destroy it.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from career_agent.llm.acceptance import CacheDisposition, disposition_for
from career_agent.llm.transport import TDescriptionFamily, TProviderFamily
from career_agent.storage.db import (
    applied_versions,
    connect,
    migrate,
    transaction,
)
from career_agent.storage.fingerprint_repo import LLMCallRepo
from career_agent.storage.records import LLMCallRecord

POSTING = (
    "We are hiring a platform engineer. The role is fully remote within Brazil. "
    "You will own our deployment pipeline and mentor two junior engineers."
)

KEY = "sha256:admission-key"
ARM = "gemma-4-31b-it@high"
VENDOR = "google"


def family(
    *,
    quote: str | None = "fully remote within Brazil",
    evidence_id: str | None = "ev_01",
    status: str = "EXPLICIT",
) -> dict[str, Any]:
    """One minimal DescriptionFamily answer, varied where the gate looks."""
    evidence = [{"id": "ev_01", "source_kind": "JOB_DESCRIPTION", "quote": quote}]
    return {
        "observed_title": "Platform Engineer",
        "observations": [
            {
                "dimension": "work_environment.work_model",
                "status": status,
                "value": "REMOTE",
                "confidence": 0.9,
                "evidence_id": evidence_id,
            }
        ],
        "evidence": evidence,
    }


def judge(document: dict[str, Any]) -> tuple[CacheDisposition, str | None]:
    return disposition_for(TDescriptionFamily.model_validate(document), POSTING, {})


@pytest.fixture
def repo(tmp_path: Path) -> LLMCallRepo:
    conn = connect(tmp_path / "c.db")
    migrate(conn)
    now = "2026-01-01T00:00:00Z"
    conn.execute(
        "INSERT INTO company (id, slug, name, created_at, updated_at) VALUES ('c','c','C',?,?)",
        (now, now),
    )
    conn.execute(
        "INSERT INTO source_board (id, company_id, provider, board_identifier)"
        " VALUES ('b','c','greenhouse','tok')"
    )
    # The archived posting the evidence is checked against. `job.content_hash`
    # references it, which is the schema saying the same thing: a citation has
    # nothing to resolve against without the text that was read.
    conn.execute(
        "INSERT INTO job_raw (content_hash, description_text, byte_length, created_at)"
        " VALUES ('h', ?, ?, ?)",
        (POSTING, len(POSTING), now),
    )
    conn.execute(
        "INSERT INTO job (id, company_id, source_board_id, provider, external_id, url, title,"
        " content_hash, first_seen_at, last_seen_at, collection_status, created_at, updated_at)"
        " VALUES ('j','c','b','greenhouse','x','u','t','h',?,?,'NORMALISED',?,?)",
        (now, now, now, now),
    )
    return LLMCallRepo(conn)


def store(repo: LLMCallRepo, document: dict[str, Any], **overrides: Any) -> None:
    """Persist one answer through the production record path.

    The disposition is judged the way the pipeline judges it, unless a test
    overrides it to stand in for a row nobody has judged yet.
    """
    disposition, _ = judge(document)
    overrides.setdefault("cache_disposition", disposition.value)
    record = LLMCallRecord(
        job_id="j",
        cache_key=KEY,
        execution_id="e",
        family="description",
        provider=VENDOR,
        model=ARM,
        runner="PRODUCTION_API",
        prompt_version="description_v8",
        schema_version=3,
        structured_output="FUNCTION_CALL",
        raw_output=json.dumps(document),
        response_envelope=None,
        transport="RESPONSE_RECEIVED",
        parsed_ok=True,
        validated_ok=True,
        attempt=1,
        error=None,
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.0,
        **overrides,
    )
    with transaction(repo.conn):
        repo.record(record)


def lookup(repo: LLMCallRepo) -> Any:
    return repo.validated_answer(KEY, model=ARM, vendor=VENDOR)


# =========================================================================
# 1-3. THE GATE ITSELF
# =========================================================================


def test_schema_valid_but_evidence_invalid_is_not_a_hit(repo: LLMCallRepo) -> None:
    """A citation to a sentence the posting does not contain."""
    document = family(quote="a sentence that appears nowhere in this posting")
    disposition, reason = judge(document)
    assert disposition is CacheDisposition.REJECTED_EVIDENCE
    assert reason is not None and "did not resolve" in reason

    store(repo, document)
    assert lookup(repo) is None


def test_schema_valid_but_unsupported_explicit_is_not_a_hit(repo: LLMCallRepo) -> None:
    """The exact failure the FUNCTION_CALL canary produced: EXPLICIT, citing nothing."""
    document = family(evidence_id=None)
    disposition, reason = judge(document)
    assert disposition is CacheDisposition.REJECTED_UNSUPPORTED
    assert reason is not None and "cite no evidence" in reason

    store(repo, document)
    assert lookup(repo) is None


def test_a_citation_naming_evidence_that_was_not_returned_is_not_a_hit(
    repo: LLMCallRepo,
) -> None:
    document = family(evidence_id="ev_99")
    disposition, reason = judge(document)
    assert disposition is CacheDisposition.REJECTED_UNSUPPORTED
    assert reason is not None and "name no returned evidence" in reason

    store(repo, document)
    assert lookup(repo) is None


def test_a_fully_accepted_response_is_a_hit(repo: LLMCallRepo) -> None:
    """The gate must still let a good answer through, or it is not a gate."""
    document = family()
    assert judge(document)[0] is CacheDisposition.ACCEPTED

    store(repo, document)
    answer = lookup(repo)
    assert answer is not None
    assert json.loads(answer.raw_output) == document


def test_a_not_stated_observation_needs_no_evidence(repo: LLMCallRepo) -> None:
    """Only EXPLICIT claims must cite. Silence never had to."""
    document = family(status="NOT_STATED", evidence_id=None)
    assert judge(document)[0] is CacheDisposition.ACCEPTED


# =========================================================================
# 4-5. A REFUSED ANSWER IS STILL EVIDENCE
# =========================================================================


def test_the_raw_failed_response_remains_durable(repo: LLMCallRepo) -> None:
    """Refusing to serve an answer is not deleting it.

    The row keeps its bytes, its parse result and its schema result. Only the
    question "may this be served again" is answered differently, and it is
    answered in its own column.
    """
    document = family(evidence_id=None)
    store(repo, document)

    row = repo.conn.execute("SELECT * FROM llm_call WHERE cache_key = ?", (KEY,)).fetchone()
    assert json.loads(row["raw_output"]) == document, "the bytes are untouched"
    assert row["parsed_ok"] == 1, "it did parse, and the row still says so"
    assert row["validated_ok"] == 1, "it did validate, and the row still says so"
    assert row["error"] is None
    assert row["cache_disposition"] == "REJECTED_UNSUPPORTED"
    assert lookup(repo) is None


def test_a_refused_answer_cannot_assemble_a_fingerprint() -> None:
    """The gate agrees with the assembler rather than replacing it.

    A COMPLETE document -- all 33 dimensions -- so that assembly refuses it for
    the evidence, not for a missing row. That is the whole shape of the canary
    failure: nothing was incomplete, and nothing could be built from it.
    """
    from career_agent.domain.fingerprint import FingerprintMeta
    from career_agent.llm.assemble import assemble
    from career_agent.llm.transport import ALL_DIMENSIONS

    document = family(quote=None)
    document["observations"] = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.5}
        for name in ALL_DIMENSIONS
        if name != "work_environment.work_model"
    ] + document["observations"]
    assert judge(document)[0] is not CacheDisposition.ACCEPTED

    meta = FingerprintMeta(
        prompt_version="description_v8",
        transport_version=3,
        model=ARM,
        content_hash="h",
        payload_hash="p",
        truncated=False,
    )
    with pytest.raises(Exception, match="quote"):
        assemble(
            TDescriptionFamily.model_validate(document),
            TProviderFamily(),
            meta,
            source_provider="greenhouse",
        )


# =========================================================================
# 6-8. THE GATE CANNOT BE BYPASSED, AND DOES NOT BLOCK NEW WORK
# =========================================================================


def test_a_refused_row_does_not_block_a_different_arm(repo: LLMCallRepo) -> None:
    """A failure under one identity must not look like an answer under another.

    The refused row and the new arm share nothing: different key, and the
    lookup is keyed on the arm as well.
    """
    store(repo, family(evidence_id=None))

    assert repo.validated_answer("sha256:a-different-arm", model=ARM, vendor=VENDOR) is None
    assert repo.validated_answer(KEY, model="gemma-4-31b-it@low", vendor=VENDOR) is None
    assert lookup(repo) is None, "and it still does not answer for its own key"


def test_the_lookup_cannot_return_an_unjudged_row(repo: LLMCallRepo) -> None:
    """UNVERIFIED is the default, and the default is not a hit.

    Every historical row arrives unjudged. Defaulting the other way is exactly
    how a schema-perfect answer that cited nothing became a cache hit.
    """
    store(repo, family(), cache_disposition="UNVERIFIED")
    assert lookup(repo) is None

    with transaction(repo.conn):
        repo.conn.execute(
            "UPDATE llm_call SET cache_disposition = 'ACCEPTED' WHERE cache_key = ?", (KEY,)
        )
    assert lookup(repo) is not None, "and judging it admits it"


def test_the_batch_lookup_applies_the_same_gate(repo: LLMCallRepo) -> None:
    """`validated_answers` plans a whole run. A gate on one path only is no gate."""
    store(repo, family(evidence_id=None))
    assert repo.validated_answers([KEY], model=ARM, vendor=VENDOR) == {}

    with transaction(repo.conn):
        repo.conn.execute(
            "UPDATE llm_call SET cache_disposition = 'ACCEPTED' WHERE cache_key = ?", (KEY,)
        )
    assert set(repo.validated_answers([KEY], model=ARM, vendor=VENDOR)) == {KEY}


def test_an_accepted_hit_costs_no_transport_call(repo: LLMCallRepo) -> None:
    """The whole point of admitting one: it is served instead of asked."""
    from career_agent.llm.budget import Authorization, BudgetLedger
    from career_agent.llm.client import Family, LLMRequest, ModelConfig, Runner
    from career_agent.llm.pacing import RateLimiter
    from career_agent.pipeline.live import PacedLiveClient

    store(repo, family())

    class Exploding:
        vendor = VENDOR
        runner = Runner.PRODUCTION_API

        def complete(self, request: Any, config: Any) -> Any:
            raise AssertionError("a cache hit reached the transport")

    directory = Path(repo.conn.execute("PRAGMA database_list").fetchone()[2]).parent
    ledger = BudgetLedger.for_authorization(
        Authorization("admission", 10, VENDOR, "gemma-4-31b-it"), directory=directory / "budget"
    )
    client = PacedLiveClient(
        inner=Exploding(),  # type: ignore[arg-type]
        calls=repo,
        limiter=RateLimiter(requests_per_minute=1000, tokens_per_minute=10**7),
        max_live_calls=1,
        ledger=ledger,
    )
    request = LLMRequest(
        family=Family.DESCRIPTION, system="s", user="u", schema={}, schema_name="n", cache_key=KEY
    )
    client.complete(
        request, ModelConfig(vendor=VENDOR, identifier="gemma-4-31b-it", reasoning="high")
    )

    assert client.cache_hits == 1
    assert client.live_calls == 0
    assert ledger.consumed == 0, "and it reserved nothing"


# =========================================================================
# 9-10. THE MIGRATION, AND THE KEYS IT MUST NOT MOVE
# =========================================================================


def test_the_migration_adds_the_column_and_keeps_every_row(tmp_path: Path) -> None:
    """Run against a database built by every earlier migration.

    The assertion is MEMBERSHIP, not the head. It used to read
    `schema_version(conn) == 10`, which pinned this test to whatever the newest
    migration happened to be and broke the day 0011 landed -- exactly the
    failure mode commit 83b7fbd named: a test pinned to a count is a test
    people learn to edit. What this test is actually about is that migration
    0010 applied and its column survived, and that is what it now says.
    """
    conn = connect(tmp_path / "m.db")
    migrate(conn)
    assert 10 in applied_versions(conn), "migration 0010 did not apply"
    columns = {row[1] for row in conn.execute("PRAGMA table_info(llm_call)")}
    assert "cache_disposition" in columns
    for preserved in ("raw_output", "response_envelope", "parsed_ok", "validated_ok", "error"):
        assert preserved in columns, f"{preserved} must survive the rebuild"

    default = conn.execute(
        "SELECT dflt_value FROM pragma_table_info('llm_call') WHERE name = 'cache_disposition'"
    ).fetchone()[0]
    assert "UNVERIFIED" in default, "an unjudged row must not default to a hit"

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO llm_call (id, job_id, cache_key, family, purpose, provider, model,"
            " prompt_version, schema_version, input_hash, raw_output, parsed_ok,"
            " cache_disposition, created_at)"
            " VALUES ('x','j','k','description','p','google','m','v',3,'h','{}',1,"
            " 'DEFINITELY_NOT_A_DISPOSITION','2026-01-01')"
        )


def test_admission_moved_no_cache_key(tmp_path: Path) -> None:
    """Admission changed. Identity did not.

    A key that moved would re-ask every stored question, which is a bill and a
    quota, and nothing about cache ADMISSION touches what was asked. The
    FUNCTION_CALL arm's key did move afterwards -- because the tool declaration
    was revised to v2, which is a change to what the model is asked and is
    supposed to move it. Pinned here on the arm that revision did not touch.
    """
    from career_agent.llm.client import Family, ModelConfig, StructuredOutput
    from career_agent.llm.requests import build_description_request

    gemini = ModelConfig(vendor="google", identifier="gemini-3.1-flash-lite")
    assert build_description_request("sha256:PIN", "a posting", gemini).cache_key.key == (
        "sha256:c5511a8ab5d57e0c612af622217e1a4a3dfe15d488cd8fdd8237c5bfa77c75d8"
    )

    # Every mode still has its own identity, and none of them is the other.
    keys = {
        mode: build_description_request(
            "sha256:PIN",
            "a posting",
            ModelConfig(
                vendor="google",
                identifier="gemma-4-31b-it",
                reasoning="high",
                per_family=((Family.DESCRIPTION, mode),),
            ),
        ).cache_key.key
        for mode in StructuredOutput
    }
    assert len(set(keys.values())) == len(StructuredOutput), keys

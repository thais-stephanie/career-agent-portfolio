"""Fault injection at every boundary an attempt can die at.

FOUR ROUNDS, FOUR HOLES, ONE SHAPE
----------------------------------
Evidence was lost four times, each in a different place and each fixed on its
own: a conflicting insert discarded a re-run's attempts; an arm-wide refusal was
repeated instead of stopping; a budget stop threw the outcome away; an interrupt
would have done the same. Patching branches produced four fixes and a fifth
hole.

The invariant is one thing now, enforced in one place -- `_ask.observe` writes
each attempt the moment it is observed:

    if diagnostic information exists for an attempt, it is durable before
    control leaves the frame that observed it

These tests attack that from every direction the enumeration named. All offline;
not one makes a network call.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from tests.integration.test_live_runner import (
    ScriptedClient,
    description_answer,
    instant_limiter,
    plan_for,
    provider_answer,
    working_client,
)

from career_agent.llm.client import (
    Family,
    LLMNotSent,
    LLMResponse,
    LLMUnavailable,
    Runner,
)
from career_agent.pipeline.live import run_plan


def rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT family, attempt, transport, error, raw_output, response_envelope,"
        " parsed_ok, validated_ok FROM llm_call ORDER BY created_at, attempt"
    ).fetchall()


# --- A. local failure, before anything reached the wire ---------------------


def test_a_local_failure_is_persisted_costs_no_http_and_stops_the_arm(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The GLM screen: 18 recorded attempts, 0 of a 50-request allowance used."""
    client = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMNotSent("request was never sent: TypeError")] * 9}
    )
    report = run_plan(conn, plan_for(conn, db_path, job_ids), client, instant_limiter())

    stored = rows(conn)
    assert len(stored) == 1, "the diagnostic was lost"
    assert stored[0]["transport"] == "NOT_SENT"
    assert report.live_calls == 0, "a request that never existed consumed HTTP budget"
    assert report.local_failures == 1
    assert report.jobs_run == 1, "one local defect was repeated across postings"


# --- B. the budget refuses the first request --------------------------------


def test_a_budget_refusal_before_any_request_invents_nothing(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """No fake HTTP attempt, and no fake posting failure."""
    report = run_plan(
        conn,
        plan_for(conn, db_path, job_ids, max_live_calls=2),
        working_client(),
        instant_limiter(),
    )

    assert report.live_calls == 2
    assert report.jobs_run == 1
    assert report.failures == 0
    assert len(rows(conn)) == 2


# --- C. a response arrived, and then the ceiling ----------------------------


def test_a_response_is_persisted_before_the_ceiling_stops_the_run(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The canary's exact shape: one real answer, then a stop that ate it."""
    client = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMResponse(raw_text="not json at all", model="m")] * 4}
    )
    report = run_plan(
        conn,
        plan_for(conn, db_path, job_ids[:1], max_live_calls=1),
        client,
        instant_limiter(),
    )

    stored = rows(conn)
    assert len(stored) == 1, "the response that was paid for was discarded by the stop"
    assert stored[0]["transport"] == "RESPONSE_RECEIVED"
    assert stored[0]["raw_output"] == "not json at all"
    assert report.stopped_early and "budget" in report.stopped_early


# --- D. a permanent account failure -----------------------------------------


def test_a_payment_refusal_is_persisted_never_retried_and_stops_the_arm(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    refusal = LLMUnavailable(
        "could not be reached: Error code: 402 - {'type': 'payment_required_error', "
        "'param': 'quota'}"
    )
    client = ScriptedClient(answers={Family.DESCRIPTION: [refusal] * 9})
    report = run_plan(conn, plan_for(conn, db_path, job_ids), client, instant_limiter())

    stored = rows(conn)
    assert len(stored) == 1
    # A 402 is an HTTP RESPONSE. Cerebras answered, clearly and permanently,
    # and the answer was "this account cannot pay". That the answer contained
    # no completion is a different fact and lives in `parsed_ok`.
    assert stored[0]["transport"] == "RESPONSE_RECEIVED"
    assert "402" in str(stored[0]["error"])
    assert report.live_calls == 1, "a permanent billing refusal was retried"
    assert report.jobs_run == 1


# --- E. a transient failure still buys a retry ------------------------------


def test_a_transient_failure_persists_the_first_error_and_retries(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: [
                LLMUnavailable("could not be reached: 503 UNAVAILABLE"),
                description_answer(),
            ],
            Family.PROVIDER: [provider_answer()],
        }
    )
    report = run_plan(conn, plan_for(conn, db_path, job_ids[:1]), client, instant_limiter())

    stored = rows(conn)
    assert report.fingerprints == 1
    # The first row is the conservative default and not a claim: this scripted
    # failure carries no status line and no response object, so nothing about
    # it PROVES a response arrived. Absence of proof stays SENT_NO_RESPONSE --
    # see `test_transport_semantics.py` for a 503 that really did answer.
    assert [r["transport"] for r in stored] == [
        "SENT_NO_RESPONSE",
        "RESPONSE_RECEIVED",
        "RESPONSE_RECEIVED",
    ]
    assert "503" in str(stored[0]["error"]), "the failed first attempt was not kept"


# --- F and G. an answer that cannot be used is still evidence ---------------


def test_an_unusable_response_keeps_its_raw_text_and_its_envelope(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The case the Cerebras run needed and did not have.

    Content that could not be read, with the vendor's own shape beside it
    saying where the answer went. A parse failure is benchmark data.
    """
    unusable = LLMResponse(
        raw_text="",
        model="z-ai/glm-5.2",
        envelope={
            "model": "z-ai/glm-5.2",
            "usage": {"prompt_tokens": 9_963, "completion_tokens": 3_488},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message_keys": ["content", "reasoning", "role"],
                    "field_lengths": {"content": 0, "reasoning": 14_000},
                }
            ],
        },
    )
    client = ScriptedClient(answers={Family.DESCRIPTION: [unusable] * 4})
    run_plan(conn, plan_for(conn, db_path, job_ids[:1]), client, instant_limiter())

    stored = rows(conn)
    assert stored, "an unreadable answer is still evidence, and was discarded"
    assert stored[0]["transport"] == "RESPONSE_RECEIVED"
    assert stored[0]["parsed_ok"] == 0
    envelope = json.loads(str(stored[0]["response_envelope"]))
    assert envelope["usage"]["completion_tokens"] == 3_488
    assert envelope["choices"][0]["field_lengths"]["content"] == 0
    assert envelope["choices"][0]["field_lengths"]["reasoning"] == 14_000


# --- H. a good answer stays reusable ----------------------------------------


def test_a_validated_answer_is_served_from_storage_on_the_next_run(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    first = run_plan(
        conn, plan_for(conn, db_path, job_ids[:1]), working_client(), instant_limiter()
    )
    assert first.fingerprints == 1

    client = working_client()
    second = run_plan(conn, plan_for(conn, db_path, job_ids[:1]), client, instant_limiter())

    assert second.live_calls == 0
    assert client.call_count == 0


# --- I. the same question, a later execution --------------------------------


def test_a_failed_attempt_does_not_block_the_same_question_later(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    failing = ScriptedClient(answers={Family.DESCRIPTION: [LLMUnavailable("500 internal")] * 4})
    run_plan(conn, plan_for(conn, db_path, job_ids[:1]), failing, instant_limiter())
    before = len(rows(conn))

    second = run_plan(
        conn, plan_for(conn, db_path, job_ids[:1]), working_client(), instant_limiter()
    )

    assert second.fingerprints == 1, "a failed attempt was replayed as an answer"
    assert len(rows(conn)) > before, "the second execution's attempts were discarded"


# --- J. two postings, one provider question ---------------------------------


def test_two_postings_sharing_one_provider_answer_do_not_collide(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    report = run_plan(conn, plan_for(conn, db_path, job_ids), working_client(), instant_limiter())

    assert report.fingerprints == 3
    counts = {
        row["runner"]: row["n"]
        for row in conn.execute("SELECT runner, count(*) AS n FROM llm_call GROUP BY 1")
    }
    assert counts[Runner.PRODUCTION_API.value] == 4
    assert counts[Runner.REPLAY.value] == 1, "the shared answer's replay row was swallowed"


# --- the two controls are two controls --------------------------------------


def test_max_attempts_and_max_live_calls_are_separate_controls(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The canary used a budget of 1 to mean "no retries". It does not mean that.

    A budget bounds provider HTTP requests for a whole run; attempts bound
    retries for one semantic request. Using the first to achieve the second
    worked by accident, and reported a ceiling stop where a policy had been
    asked for.
    """
    client = ScriptedClient(answers={Family.DESCRIPTION: [LLMUnavailable("500 internal")] * 6})
    report = run_plan(
        conn,
        plan_for(conn, db_path, job_ids[:1], max_live_calls=10, max_attempts=1),
        client,
        instant_limiter(),
    )

    assert report.live_calls == 1, "max_attempts=1 still permitted a retry"
    assert report.stopped_early is None, "a retry policy was reported as a ceiling stop"
    assert len(rows(conn)) == 1

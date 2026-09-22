"""The live runner, driven entirely by fake clients on a throwaway database.

**No test in this file can reach a vendor.** The clients are test doubles, the
clock is fake, and the one test that sets a credential variable sets it to a
string that is not a key and then proves nothing was constructed to use it.

What is being asserted here is mostly refusal. The runner is the first code in
this repository capable of spending money, so the interesting behaviour is the
set of conditions under which it declines to: no explicit database, no explicit
selection, no credential, a plan larger than the authorised ceiling, a budget
reached mid-run. The happy path is one test; the guards are a dozen.
"""

import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from typer.testing import CliRunner

from career_agent.cli import app
from career_agent.llm.budget import Authorization, BudgetLedger
from career_agent.llm.cache import description_key, static_digest
from career_agent.llm.client import (
    Family,
    LLMRequest,
    LLMResponse,
    LLMUnavailable,
    ModelConfig,
    Runner,
)
from career_agent.llm.pacing import RateLimiter
from career_agent.llm.prompts import DESCRIPTION_PROMPT_VERSION, load_prompt
from career_agent.llm.requests import description_schema
from career_agent.llm.transport import ALL_DIMENSIONS, TRANSPORT_SCHEMA_VERSION
from career_agent.pipeline.cowork import cowork_config
from career_agent.pipeline.extract import load_source
from career_agent.pipeline.live import (
    RETRY_COOLDOWN_SECONDS,
    LiveCallBudgetExhausted,
    PacedLiveClient,
    plan_run,
    run_plan,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.fingerprint_repo import LLMCallRepo
from career_agent.storage.records import (
    CompanyRecord,
    JobRecord,
    LLMCallRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

runner = CliRunner()

VENDOR = "google"
MODEL = "gemini-3.1-flash-lite"
CONFIG = ModelConfig(vendor=VENDOR, identifier=MODEL)

#: A key-shaped string that is not a key. Set only where a test needs to prove
#: that presence alone changes nothing.
NOT_A_KEY = "not-a-real-key-and-never-sent-anywhere"

WORLDWIDE = "We hire anywhere in the world, except Cuba and Iran."

#: Two postings that share an ATS location value, and one that carries no
#: location field at all. That is the whole provider-family economics in three
#: rows: the first two ask one question between them, the third asks none.
POSTINGS = (
    (
        "alpha",
        f"Alpha Analyst\n\nYou will own our internal business systems.\n{WORLDWIDE}\n",
        {"id": 1, "location": {"name": "Remote - United States"}},
    ),
    (
        "bravo",
        f"Bravo Analyst\n\nYou will own our internal business systems.\n{WORLDWIDE}\n",
        {"id": 2, "location": {"name": "Remote - United States"}},
    ),
    (
        "charlie",
        f"Charlie Analyst\n\nYou will own our internal business systems.\n{WORLDWIDE}\n",
        {"id": 3},
    ),
)


# =========================================================================
# FIXTURES
# =========================================================================


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "corpus.db"


@pytest.fixture
def conn(db_path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(db_path)
    migrate(connection)
    try:
        yield connection
    finally:
        connection.close()


@pytest.fixture
def job_ids(conn: sqlite3.Connection) -> list[str]:
    identifiers: list[str] = []
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="exampleco", name="Example Co"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(
                company_id=company, provider="greenhouse", board_identifier="exampleco"
            )
        )
        for external_id, description, payload in POSTINGS:
            content_hash = JobRawRepo(conn).put(description)
            identifier = JobRepo(conn).upsert_seen(
                JobRecord(
                    company_id=company,
                    source_board_id=board,
                    provider="greenhouse",
                    external_id=external_id,
                    url=f"https://boards.greenhouse.io/exampleco/jobs/{external_id}",
                    title=f"{external_id.title()} Analyst",
                    content_hash=content_hash,
                )
            )
            ProviderPayloadRepo(conn).put(
                ProviderPayloadRecord(job_id=identifier, provider="greenhouse", payload=payload)
            )
            identifiers.append(identifier)
    return identifiers


@pytest.fixture(autouse=True)
def no_ambient_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test here may depend on what this machine has configured.

    `load_dotenv` searches upward from the *calling module's* directory, so it
    finds the repository's real `.env` however a test is invoked. A suite whose
    result depends on the developer's local configuration is not a suite -- and
    for a command that can spend money, it is worse than that.
    """
    for name in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("career_agent.cli_extract.load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr("career_agent.cli.load_dotenv", lambda *a, **k: False)


# =========================================================================
# TEST DOUBLES
# =========================================================================


@dataclass
class ScriptedClient:
    """A vendor that answers from a script, and counts what it was asked.

    It labels itself as a production API client on purpose: the point of most
    of these tests is what the *accounting* says, and a double that called
    itself FAKE would make every runner assertion vacuous.
    """

    answers: dict[Family, list[object]] = field(default_factory=dict)
    vendor: str = VENDOR
    runner: Runner = Runner.PRODUCTION_API
    calls: list[tuple[Family, str]] = field(default_factory=list)

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        self.calls.append((request.family, request.cache_key))
        queue = self.answers.get(request.family)
        if not queue:
            raise AssertionError(f"the script has no answer left for {request.family}")
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, LLMResponse):
            # A test that cares about the envelope, the finish reason or an
            # empty body supplies a whole response rather than a body string.
            return answer
        return LLMResponse(raw_text=str(answer), model=config.identifier)

    @property
    def call_count(self) -> int:
        return len(self.calls)


class ExplodingClient:
    """A client that fails the test if it is ever asked for anything."""

    vendor = VENDOR
    runner = Runner.PRODUCTION_API

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        raise AssertionError("a live call was made when none should have been")


class InterruptingClient:
    """Answers the first posting, then behaves like someone pressing Ctrl-C.

    Counted in postings rather than in calls: the interruption has to land on
    the *second* posting for the test to say anything about durability, and how
    many calls the first posting took is an implementation detail of the
    provider family's zero-call rule.
    """

    vendor = VENDOR
    runner = Runner.PRODUCTION_API

    def __init__(self) -> None:
        self.descriptions = 0

    def complete(self, request: LLMRequest, config: ModelConfig) -> LLMResponse:
        if request.family is Family.PROVIDER:
            return LLMResponse(raw_text=provider_answer(), model=config.identifier)
        self.descriptions += 1
        if self.descriptions > 1:
            raise KeyboardInterrupt
        return LLMResponse(raw_text=description_answer(), model=config.identifier)


class FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def instant_limiter(rpm: int = 10, tpm: int = 250_000) -> RateLimiter:
    """A limiter that computes every delay and waits none of them."""
    clock = FakeClock()
    return RateLimiter(
        requests_per_minute=rpm,
        tokens_per_minute=tpm,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )


# =========================================================================
# THE ANSWERS
# =========================================================================


def description_answer(quote: str = WORLDWIDE) -> str:
    """A complete, well-formed description-family answer.

    Every dimension appears. A forgotten dimension and a dimension the posting
    was silent about are different facts, and the completeness check makes the
    first a retry rather than a crash halfway through assembly.
    """
    observations = [
        {"dimension": name, "status": "NOT_STATED", "value": None, "confidence": 0.0}
        for name in ALL_DIMENSIONS
        if name != "hiring_scope"
    ]
    observations.append(
        {
            "dimension": "hiring_scope",
            "status": "EXPLICIT",
            "value": "WORLDWIDE:!CU,IR",
            "confidence": 0.95,
            "evidence_id": "ev_01",
        }
    )
    return json.dumps(
        {
            "observed_title": "Analyst",
            "function_signals": ["business_systems"],
            "observations": observations,
            "responsibilities": [
                {
                    "category": "business_systems_administration",
                    "prominence": "PRIMARY",
                    "confidence": 0.9,
                    "evidence_id": "ev_02",
                }
            ],
            "software": [],
            "languages": [],
            "evidence": [
                {"id": "ev_01", "source_kind": "JOB_DESCRIPTION", "quote": quote},
                {
                    "id": "ev_02",
                    "source_kind": "JOB_DESCRIPTION",
                    "quote": "You will own our internal business systems.",
                },
            ],
        }
    )


def provider_answer() -> str:
    return json.dumps(
        {
            "observations": [
                {
                    "dimension": "hiring_location_hint",
                    "status": "EXPLICIT",
                    "value": "COUNTRY_LIST:US",
                    "confidence": 0.9,
                    "evidence_id": "ev_01",
                }
            ],
            "evidence": [
                {
                    "id": "ev_01",
                    "source_kind": "PROVIDER_FIELD",
                    "provider": "greenhouse",
                    "source_field": "location.name",
                    "source_value": "Remote - United States",
                }
            ],
        }
    )


def working_client() -> ScriptedClient:
    return ScriptedClient(
        answers={
            Family.DESCRIPTION: [description_answer()],
            Family.PROVIDER: [provider_answer()],
        }
    )


def runner_authorization(
    config: ModelConfig = CONFIG, *, max_requests: int = 1_000
) -> Authorization:
    """An authorisation covering the arm the runner tests use.

    Deliberately not the programme authorisation: these tests are about pacing,
    persistence and resume, and pinning them to one real model would make every
    one of them fail the day the programme moves to the next.
    """
    return Authorization(
        authorization_id="test-runner",
        max_requests=max_requests,
        vendor=config.vendor,
        model=config.identifier,
    )


def ledger_for(
    db_path: Path, config: ModelConfig = CONFIG, *, max_requests: int = 1_000
) -> BudgetLedger:
    """A ledger beside the test database, so each test gets its own file."""
    return BudgetLedger.for_authorization(
        runner_authorization(config, max_requests=max_requests),
        directory=db_path.parent / "budget",
    )


def plan_for(
    conn: sqlite3.Connection,
    db_path: Path,
    job_ids: list[str],
    *,
    max_live_calls: int = 100,
    max_attempts: int = 2,
    config: ModelConfig = CONFIG,
    ledger: BudgetLedger | None = None,
) -> object:
    sources = [load_source(conn, identifier) for identifier in job_ids]
    return plan_run(
        conn,
        sources,
        config,
        database=db_path,
        requests_per_minute=10,
        tokens_per_minute=250_000,
        max_live_calls=max_live_calls,
        ledger=ledger if ledger is not None else ledger_for(db_path, config),
        max_attempts=max_attempts,
    )


# =========================================================================
# PLANNING
# =========================================================================


def test_the_plan_counts_distinct_questions_not_postings(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Three postings, three description calls, one provider call.

    Alpha and bravo carry the same ATS location value, so their observation
    blocks render identically and they are asking the model one question between
    them. Charlie carries no location field, so it asks none. A planner that
    counted two calls per posting would predict six and a runner would then
    "discover" it needed four.
    """
    plan = plan_for(conn, db_path, job_ids)

    assert plan.description_calls == 3
    assert plan.provider_calls == 1
    assert plan.jobs_needing_no_provider_call == 1
    assert plan.nominal_live_calls == 4
    assert plan.worst_case_live_calls == 8


def test_the_plan_prices_the_run_before_a_client_exists(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    plan = plan_for(conn, db_path, job_ids)

    assert plan.fully_estimated
    assert plan.input_tokens > 0
    assert plan.output_tokens > 0
    # The pacing guard's worst case: the largest single call, at the full rate.
    assert plan.peak_tokens_per_minute >= plan.requests_per_minute


def test_the_planned_requests_are_the_ones_extraction_would_make(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The plan and the run must not be two opinions about the same question."""
    plan = plan_for(conn, db_path, job_ids)
    planned = {call.cache_key for call in plan.calls}

    client = working_client()
    report = run_plan(conn, plan, client, instant_limiter())

    assert {key for _, key in client.calls} <= planned
    assert report.live_calls == plan.nominal_live_calls


# =========================================================================
# THE HAPPY PATH, AND THE ACCOUNTING
# =========================================================================


def test_a_successful_run_stores_a_fingerprint_for_every_posting(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Five requests, four live calls -- and the difference is the whole point.

    Bravo asks the provider family the same question alpha already asked. It is
    a request the pipeline genuinely makes, and it is served from alpha's stored
    answer because storage happens per posting. That is how 27 golden postings
    cost 52 calls rather than 54, and it is why the two counts are reported
    separately rather than added together.
    """
    plan = plan_for(conn, db_path, job_ids)
    report = run_plan(conn, plan, working_client(), instant_limiter())

    assert report.jobs_run == 3
    assert report.fingerprints == 3
    assert report.failures == 0
    assert report.live_calls == 4
    assert report.cache_hits == 1
    assert report.stopped_early is None

    stored = conn.execute("SELECT count(*) AS n FROM fingerprint").fetchone()["n"]
    assert stored == 3


def test_every_live_attempt_is_recorded_as_a_production_call(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The accounting a benchmark rests on: which outputs actually ran a model.

    Five rows for three postings, and the fifth is the point. Two of these jobs
    render one observation block, so the second is served from the first's
    answer -- a REPLAY, free, no vendor involved. That row used to vanish:
    `ON CONFLICT (cache_key, attempt) DO NOTHING` swallowed it because the key
    and the ordinal matched a row already written moments earlier.

    Losing it looked harmless here and was the same defect that discarded 18
    real Cerebras attempts and the $0.0954 of inference behind them. An event
    that happened gets a row, and the two populations stay separable.
    """
    plan = plan_for(conn, db_path, job_ids)
    run_plan(conn, plan, working_client(), instant_limiter())

    rows = conn.execute(
        "SELECT runner, provider, model, count(*) AS n FROM llm_call GROUP BY 1, 2, 3"
    ).fetchall()
    assert [(r["runner"], r["provider"], r["model"], r["n"]) for r in rows] == [
        (Runner.PRODUCTION_API.value, VENDOR, MODEL, 4),
        (Runner.REPLAY.value, VENDOR, MODEL, 1),
    ]

    # Every row this run wrote carries its execution, and they all agree.
    executions = {row["execution_id"] for row in conn.execute("SELECT execution_id FROM llm_call")}
    assert len(executions) == 1 and None not in executions


# =========================================================================
# CACHE
# =========================================================================


def test_a_second_run_answers_from_storage_and_calls_nothing(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The production cache, consulted before the call rather than after it.

    The plan counts distinct *questions* already answered (four); the report
    counts *requests* served from storage (five, because two postings share
    one provider question). They are different quantities and neither is the
    other rounded off.
    """
    run_plan(conn, plan_for(conn, db_path, job_ids), working_client(), instant_limiter())

    second = plan_for(conn, db_path, job_ids)
    assert second.cache_hits == 4
    assert second.nominal_live_calls == 0

    report = run_plan(conn, second, ExplodingClient(), instant_limiter())
    assert report.live_calls == 0
    assert report.cache_hits == 5
    assert report.fingerprints == 3


def test_a_cache_hit_is_recorded_as_a_replay_not_as_a_paid_call(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """A free answer in the paid bucket is the one accounting error M2 cannot afford."""
    run_plan(conn, plan_for(conn, db_path, job_ids), working_client(), instant_limiter())
    report = run_plan(conn, plan_for(conn, db_path, job_ids), ExplodingClient(), instant_limiter())

    runners = {attempt.runner for outcome in report.outcomes for attempt in outcome.attempts}
    assert runners == {Runner.REPLAY}
    assert all(outcome.paid_calls == 0 for outcome in report.outcomes)


def test_a_development_answer_is_not_served_to_a_vendor_arm(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Structural, and then checked a second way.

    The model identity is part of the cache key, so an assisted-development
    answer and a vendor answer are different questions about the same posting.
    The lookup also matches the `model` and `provider` columns, which are
    redundant with the key by construction -- so a row would have to be
    deliberately mislabelled *and* the key derivation changed for a cross-arm
    hit to happen.
    """
    # The static half is held identical on purpose: the arm alone must separate
    # these, or the separation is an accident of the two running different
    # prompts today.
    same_bytes = static_digest(
        load_prompt(DESCRIPTION_PROMPT_VERSION), description_schema(), "STRICT_SCHEMA"
    )
    development = description_key(
        cowork_config(),
        DESCRIPTION_PROMPT_VERSION,
        TRANSPORT_SCHEMA_VERSION,
        "sha256:aaa",
        same_bytes,
    )
    production = description_key(
        CONFIG, DESCRIPTION_PROMPT_VERSION, TRANSPORT_SCHEMA_VERSION, "sha256:aaa", same_bytes
    )
    assert development.key != production.key

    calls = LLMCallRepo(conn)
    with transaction(conn):
        calls.record(
            LLMCallRecord(
                job_id=job_ids[0],
                cache_key=production.key,
                family=Family.DESCRIPTION.value,
                provider="cowork",
                model="cowork-development",
                runner=Runner.REPLAY.value,
                prompt_version=DESCRIPTION_PROMPT_VERSION,
                schema_version=TRANSPORT_SCHEMA_VERSION,
                structured_output="STRICT_SCHEMA",
                execution_id="test-execution",
                response_envelope=None,
                transport="RESPONSE_RECEIVED",
                raw_output=description_answer(),
                parsed_ok=True,
                validated_ok=True,
            )
        )

    assert calls.validated_answer(production.key, model=MODEL, vendor=VENDOR) is None


def test_a_failed_attempt_is_never_replayed_as_an_answer(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Serving a recorded failure back would spend a retry reproducing it."""
    plan = plan_for(conn, db_path, job_ids[:1])
    key = plan.calls[0].cache_key

    calls = LLMCallRepo(conn)
    with transaction(conn):
        calls.record(
            LLMCallRecord(
                job_id=job_ids[0],
                cache_key=key,
                family=Family.DESCRIPTION.value,
                provider=VENDOR,
                model=MODEL,
                runner=Runner.PRODUCTION_API.value,
                prompt_version=DESCRIPTION_PROMPT_VERSION,
                schema_version=TRANSPORT_SCHEMA_VERSION,
                structured_output="STRICT_SCHEMA",
                execution_id="test-execution",
                response_envelope=None,
                transport="RESPONSE_RECEIVED",
                raw_output="not json at all",
                parsed_ok=False,
                validated_ok=False,
            )
        )

    assert calls.validated_answer(key, model=MODEL, vendor=VENDOR) is None
    assert plan_for(conn, db_path, job_ids[:1]).cache_hits == 0


# =========================================================================
# FAILURE MODES
# =========================================================================


def test_a_schema_failure_is_retried_and_the_retry_costs_a_call(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: ['{"observed_title": "Analyst"}', description_answer()],
            Family.PROVIDER: [provider_answer()],
        }
    )
    plan = plan_for(conn, db_path, job_ids[:1])
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.fingerprints == 1
    assert report.live_calls == 3  # one failed description, one retry, one provider
    attempts = conn.execute(
        "SELECT validated_ok FROM llm_call WHERE family = 'description' ORDER BY attempt"
    ).fetchall()
    assert [row["validated_ok"] for row in attempts] == [0, 1]


def test_a_permanent_provider_error_fails_the_posting_and_keeps_the_record(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    client = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMUnavailable("google returned 500 internal")]}
    )
    plan = plan_for(conn, db_path, job_ids[:1])
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.fingerprints == 0
    assert report.failures == 1
    assert report.live_calls == 2  # both attempts spent
    rows = conn.execute("SELECT error FROM llm_call").fetchall()
    assert len(rows) == 2
    assert all("500" in str(row["error"]) for row in rows)


def test_a_rate_limit_refusal_buys_a_cooldown_before_the_retry(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The ordinary interval is not the right answer to "you are asking too often"."""
    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: [
                LLMUnavailable("google could not be reached: 429 RESOURCE_EXHAUSTED"),
                description_answer(),
            ],
            Family.PROVIDER: [provider_answer()],
        }
    )
    limiter = instant_limiter()
    plan = plan_for(conn, db_path, job_ids[:1])
    run_plan(conn, plan, client, limiter)

    assert limiter.waited_seconds >= RETRY_COOLDOWN_SECONDS


def test_an_unverifiable_quote_is_kept_and_marked_rather_than_dropped(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """ "The model cited a sentence that does not exist" is the measurement."""
    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: [description_answer(quote="A sentence this posting never had.")],
            Family.PROVIDER: [provider_answer()],
        }
    )
    plan = plan_for(conn, db_path, job_ids[:1])
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.outcomes[0].unverified_evidence
    unverified = conn.execute("SELECT count(*) AS n FROM evidence WHERE verified = 0").fetchone()[
        "n"
    ]
    assert unverified >= 1


# =========================================================================
# THE BUDGET
# =========================================================================


def test_the_run_stops_at_the_authorised_ceiling_and_never_past_it(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    plan = plan_for(conn, db_path, job_ids, max_live_calls=2)
    client = working_client()
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.live_calls == 2
    assert client.call_count == 2
    assert report.stopped_early is not None
    # The first posting needed two calls and completed; the second stopped
    # before its description call was sent.
    assert report.jobs_run == 1


def test_retries_count_against_the_budget(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """A ceiling that only counted first attempts would not be a ceiling."""
    client = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMUnavailable("google returned 500 internal")]}
    )
    plan = plan_for(conn, db_path, job_ids, max_live_calls=2)
    report = run_plan(conn, plan, client, instant_limiter())

    assert client.call_count == 2
    assert report.live_calls == 2


def test_the_budget_stop_is_not_recorded_as_something_the_model_said(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """It is not an `LLMError`, so the retry loop cannot swallow it.

    If it were, a run would continue past its ceiling one recorded failure at a
    time, and every one of those failures would read afterwards as a model that
    answered badly.
    """
    paced = PacedLiveClient(
        inner=working_client(),
        calls=LLMCallRepo(conn),
        limiter=instant_limiter(),
        max_live_calls=0,
        ledger=ledger_for(db_path),
    )
    request = LLMRequest(
        family=Family.DESCRIPTION, system="s", user="u", schema={}, schema_name="n", cache_key="k"
    )
    with pytest.raises(LiveCallBudgetExhausted):
        paced.complete(request, CONFIG)


# =========================================================================
# INTERRUPTION AND RESUME
# =========================================================================


def test_an_interrupted_run_keeps_what_it_finished_and_resumes_free(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """One transaction per posting, which is what makes this true.

    A single transaction around the whole run would discard every completed
    extraction here -- and under a paid runner, re-pay for all of them.
    """
    interrupted = run_plan(
        conn,
        plan_for(conn, db_path, job_ids),
        InterruptingClient(),
        instant_limiter(),
    )
    assert interrupted.stopped_early is not None
    assert interrupted.jobs_run == 1
    assert conn.execute("SELECT count(*) AS n FROM fingerprint").fetchone()["n"] == 1

    resumed = run_plan(conn, plan_for(conn, db_path, job_ids), working_client(), instant_limiter())
    assert resumed.cache_hits >= 2  # the first posting's two answers came back free
    assert resumed.fingerprints == 3


# =========================================================================
# THE COMMAND: DRY RUN IS THE DEFAULT
# =========================================================================


def cli(*args: str) -> object:
    return runner.invoke(app, ["run-extraction", *args])


def authorize_the_test_arm(monkeypatch: pytest.MonkeyPatch, tmp: Path) -> None:
    """Let the CLI tests exercise CLI mechanics under their own authorisation.

    The command is pinned to ONE standing authorisation on purpose -- that is the
    guarantee, and a flag that chose one would undo it. These tests are about
    ceilings, credentials and reporting, none of which is about which model was
    authorised, so they patch the authorisation the same way they already patch
    `get_client`. Production reads `PROGRAM_AUTHORIZATION` and nothing else.
    """
    monkeypatch.setattr("career_agent.cli_extract.PROGRAM_AUTHORIZATION", runner_authorization())
    monkeypatch.setattr("career_agent.llm.budget.LEDGER_DIR", tmp / "budget")


def base_args(db_path: Path, job_ids: list[str], *extra: str) -> list[str]:
    selection: list[str] = []
    for identifier in job_ids:
        selection += ["--job-id", identifier]
    return [
        "--db",
        str(db_path),
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
        "--max-live-calls",
        "55",
        *selection,
        *extra,
    ]


def test_a_plain_invocation_is_a_preflight_and_calls_nothing(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed during a dry run"),
    )
    result = cli(*base_args(db_path, job_ids))

    assert result.exit_code == 0, result.output
    assert "execute / live" in result.output
    assert "false" in result.output
    assert "DRY RUN" in result.output
    assert conn.execute("SELECT count(*) AS n FROM llm_call").fetchone()["n"] == 0


def test_a_configured_credential_is_not_an_instruction_to_use_it(
    conn: sqlite3.Connection,
    db_path: Path,
    job_ids: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The safety property the whole command is shaped around.

    A key on the machine says a key exists. It does not say a run was
    authorised, and nothing in this command may read it as though it did.
    """
    monkeypatch.setenv("GOOGLE_API_KEY", NOT_A_KEY)
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed while a credential was merely set"),
    )

    result = cli(*base_args(db_path, job_ids))

    assert result.exit_code == 0, result.output
    assert "PRESENT (GOOGLE_API_KEY)" in result.output
    assert NOT_A_KEY not in result.output
    assert conn.execute("SELECT count(*) AS n FROM llm_call").fetchone()["n"] == 0


def test_the_preflight_names_the_database_and_the_selection(
    db_path: Path, job_ids: list[str]
) -> None:
    result = cli(*base_args(db_path, job_ids))

    assert str(db_path) in result.output
    assert "jobs selected" in result.output
    assert "3" in result.output


# =========================================================================
# THE COMMAND: REFUSALS
# =========================================================================


def test_live_execution_without_an_explicit_database_is_refused(
    job_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is the empty database. Inheriting it to spend money is a trap."""
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed after a refused database"),
    )
    result = cli(
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
        "--max-live-calls",
        "55",
        "--job-id",
        job_ids[0],
        "--execute",
    )

    assert result.exit_code != 0
    assert "explicit --db" in result.output


def test_an_omitted_selection_is_never_read_as_the_whole_corpus(
    db_path: Path, job_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression this command exists under.

    18,550 postings is one forgotten flag away from a full-corpus run under any
    design where "no selection" means "everything". It does not mean that here,
    and it is refused in preflight as well as in execute mode.
    """
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed with no selection"),
    )
    for extra in ([], ["--execute"]):
        result = cli(
            "--db",
            str(db_path),
            "--vendor",
            VENDOR,
            "--model",
            MODEL,
            "--max-live-calls",
            "55",
            *extra,
        )
        assert result.exit_code != 0
        assert "no postings selected" in result.output


def test_the_empty_default_database_names_itself_rather_than_the_job(
    tmp_path: Path, job_ids: list[str]
) -> None:
    empty = tmp_path / "empty.db"
    result = cli(
        "--db",
        str(empty),
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
        "--max-live-calls",
        "55",
        "--job-id",
        job_ids[0],
    )

    assert result.exit_code != 0
    assert "holds 0 job(s)" in result.output
    assert "data/m1d2/career.db" in result.output


def test_a_job_id_that_is_not_in_the_database_is_named(db_path: Path, job_ids: list[str]) -> None:
    result = cli(
        "--db",
        str(db_path),
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
        "--max-live-calls",
        "55",
        "--job-id",
        "01M0NOTAREALJOBIDENTIFIER0",
    )

    assert result.exit_code != 0
    assert "not in this database" in result.output


def test_a_plan_larger_than_the_authorised_ceiling_is_refused_before_any_call(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    authorize_the_test_arm(monkeypatch, db_path.parent)
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed for an unauthorised plan"),
    )
    monkeypatch.setenv("GOOGLE_API_KEY", NOT_A_KEY)
    result = cli(
        "--db",
        str(db_path),
        "--vendor",
        VENDOR,
        "--model",
        MODEL,
        "--max-live-calls",
        "2",
        "--execute",
        *[arg for identifier in job_ids for arg in ("--job-id", identifier)],
    )

    assert result.exit_code != 0
    assert "REFUSED" in result.output
    assert "4 live call(s)" in result.output
    assert conn.execute("SELECT count(*) AS n FROM llm_call").fetchone()["n"] == 0


def test_live_execution_without_a_credential_is_refused(
    db_path: Path, job_ids: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    authorize_the_test_arm(monkeypatch, db_path.parent)
    monkeypatch.setattr(
        "career_agent.cli_extract.get_client",
        lambda vendor: pytest.fail("a client was constructed with no credential"),
    )
    result = cli(*base_args(db_path, job_ids, "--execute"))

    assert result.exit_code != 0
    assert "no credential is set" in result.output
    assert "GOOGLE_API_KEY" in result.output


def test_pacing_at_or_above_the_ceiling_in_force_is_refused(
    db_path: Path, job_ids: list[str]
) -> None:
    """At the ceiling, one retry is an overage."""
    result = cli(*base_args(db_path, job_ids, "--rpm", "15"))

    assert result.exit_code != 0
    assert "at or above the ceiling" in result.output


def test_a_model_with_no_recorded_limits_asks_rather_than_guessing(
    db_path: Path, job_ids: list[str]
) -> None:
    """The correction, as a test.

    Rate limits are a property of an account, not of a vendor: they differ by
    model, by project and by tier, and they change. A model nobody has recorded
    limits for must therefore produce a question, not a borrowed number from a
    neighbouring model that happens to share a vendor name.
    """
    result = cli(
        "--db",
        str(db_path),
        "--vendor",
        VENDOR,
        "--model",
        "gemini-9.9-unrecorded",
        "--max-live-calls",
        "55",
        "--job-id",
        job_ids[0],
    )

    assert result.exit_code != 0
    assert "no rate limits are recorded" in result.output


def test_a_caller_may_state_their_own_ceilings_without_editing_source(
    db_path: Path, job_ids: list[str]
) -> None:
    """A different project has different limits, and must not have to patch code."""
    result = cli(
        "--db",
        str(db_path),
        "--vendor",
        VENDOR,
        "--model",
        "gemini-9.9-unrecorded",
        "--max-live-calls",
        "55",
        "--job-id",
        job_ids[0],
        "--rpm",
        "2",
        "--ceiling-rpm",
        "5",
        "--ceiling-tpm",
        "40000",
    )

    assert result.exit_code == 0, result.output
    assert "supplied by --ceiling-rpm / --ceiling-tpm" in result.output
    assert "2 (ceiling 5)" in result.output


def test_a_recorded_ceiling_is_reported_with_the_account_and_the_date(
    db_path: Path, job_ids: list[str]
) -> None:
    """Nobody should have to open a source file to learn whose quota they paced against."""
    result = cli(*base_args(db_path, job_ids))

    assert result.exit_code == 0, result.output
    assert "rate ceilings are" in result.output
    assert "Google AI Studio project" in result.output
    assert "2026-09-03" in result.output


def test_no_vendor_adapter_asserts_a_rate_limit_as_its_own_fact() -> None:
    """The representation defect this test exists to keep closed.

    `FREE_TIER_QUOTA` lived in `vendors/google.py` beside `VENDOR`, reached
    through a lookup keyed by vendor name and reported as "the published ceiling
    for google". Every part of that framing claimed a universal vendor policy
    for three numbers read off one account's console on one day.
    """
    import career_agent.llm.vendors as vendors

    root = Path(vendors.__file__).parent
    for module in sorted(root.glob("*.py")):
        source = module.read_text(encoding="utf-8")
        for forbidden in ("QuotaLimits", "FREE_TIER", "PUBLISHED_QUOTA", "requests_per_day"):
            assert forbidden not in source, f"{module.name} asserts a rate limit ({forbidden})"


def test_an_observed_quota_carries_the_account_and_the_date_it_was_read() -> None:
    from career_agent.llm.quotas import OBSERVED_QUOTAS, observed_quota

    assert observed_quota("google", "a-model-nobody-measured") is None
    # Not a vendor-level fallback: an unrecorded model borrows nothing.
    assert observed_quota("google", "") is None

    for (vendor, model), quota in OBSERVED_QUOTAS.items():
        assert vendor and model
        assert quota.source != "unrecorded", f"{vendor}/{model} records no source"
        assert quota.observed_at != "unknown", f"{vendor}/{model} records no date"


def test_an_unknown_vendor_is_refused(db_path: Path, job_ids: list[str]) -> None:
    result = cli(
        "--db",
        str(db_path),
        "--vendor",
        "acme",
        "--model",
        MODEL,
        "--max-live-calls",
        "55",
        "--job-id",
        job_ids[0],
    )

    assert result.exit_code != 0
    assert "no adapter for vendor" in result.output


# =========================================================================
# THE COMMAND: EXECUTING
# =========================================================================


def test_execute_runs_the_production_path_and_reports_both_populations(
    conn: sqlite3.Connection,
    db_path: Path,
    job_ids: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The only test that takes the execute branch, and it still calls no vendor."""
    authorize_the_test_arm(monkeypatch, db_path.parent)
    monkeypatch.setenv("GOOGLE_API_KEY", NOT_A_KEY)
    monkeypatch.setattr("career_agent.cli_extract.get_client", lambda vendor: working_client())
    monkeypatch.setattr(
        "career_agent.cli_extract.RateLimiter",
        lambda **kwargs: instant_limiter(
            rpm=kwargs["requests_per_minute"], tpm=kwargs["tokens_per_minute"]
        ),
    )

    result = cli(*base_args(db_path, job_ids, "--execute"))

    assert result.exit_code == 0, result.output
    assert "LIVE MODEL CALLS" in result.output
    assert "CACHE HITS / REUSE" in result.output
    assert NOT_A_KEY not in result.output

    fingerprints = conn.execute("SELECT count(*) AS n FROM fingerprint").fetchone()["n"]
    assert fingerprints == 3


def test_the_golden_set_selector_resolves_the_cases_rather_than_a_hardcoded_list(
    tmp_path: Path,
) -> None:
    """A twenty-eighth case must change the count without changing any code."""
    from career_agent.evaluation.golden import golden_job_ids

    for number in range(2):
        case = tmp_path / f"synthetic-{number}"
        case.mkdir()
        (case / "meta.yaml").write_text(f"job_id: synthetic-job-{number}\n")
        (case / "expected.yaml").write_text("expectations: []\n")
        (case / "payload.json").write_text("{}")
        (case / "raw.txt").write_text("Synthetic posting for manifest resolution.")
    assert golden_job_ids(tmp_path) == ["synthetic-job-0", "synthetic-job-1"]


def test_a_billing_refusal_stops_the_arm_after_one_request(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Nine refusals, eighteen requests, and nothing learned after the first.

    That is what the first Cerebras screen cost. A vendor-wide billing refusal
    is not a posting-level failure: every remaining request is guaranteed to
    fail identically, so retrying buys a second copy of the message and
    continuing buys one per job.

    The attempt is still stored. The record of what the vendor said is the
    entire value of the run.
    """
    refusal = LLMUnavailable(
        "cerebras could not be reached: Error code: 402 - {'message': 'Payment required "
        "to access this resource.', 'type': 'payment_required_error', 'param': 'quota'}"
    )
    client = ScriptedClient(answers={Family.DESCRIPTION: [refusal] * 10})
    plan = plan_for(conn, db_path, job_ids)
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.live_calls == 1, "the refusal was retried or repeated across postings"
    assert report.jobs_run == 1, "the run continued past an arm-wide failure"
    assert report.fingerprints == 0
    assert report.stopped_early and "unusable" in report.stopped_early

    rows = conn.execute("SELECT error FROM llm_call").fetchall()
    assert len(rows) == 1, "the attempt must still be recorded"
    assert "402" in str(rows[0]["error"])


def test_a_transient_failure_still_costs_a_retry_and_not_the_run(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The half the billing fix must not break.

    Stage 0 Take 2 recovered four postings from 503s. A classifier that made
    those fatal would trade a doubled bill for a lost run.
    """
    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: [
                LLMUnavailable("google could not be reached: 503 UNAVAILABLE"),
                description_answer(),
            ],
            Family.PROVIDER: [provider_answer()],
        }
    )
    plan = plan_for(conn, db_path, job_ids[:1])
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.fingerprints == 1
    assert report.stopped_early is None
    assert not any(outcome.arm_unusable for outcome in report.outcomes)


def test_the_same_question_asked_by_two_runs_leaves_two_attempt_rows(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The defect that cost a benchmark run, stated as the smallest case.

    A first run fails. A second run asks the same question at the same retry
    ordinal and succeeds. Those are two physical events, and the ledger used to
    keep only the first: `ON CONFLICT (cache_key, attempt) DO NOTHING` matched
    on semantic identity, so the later, real attempt disappeared.

    That is how 18 Cerebras requests -- 137,878 input tokens, 62,794 output,
    $0.0954 of billed inference -- left no record of what the model said.
    """
    failing = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMUnavailable("google returned 500 internal")] * 4}
    )
    first = run_plan(conn, plan_for(conn, db_path, job_ids[:1]), failing, instant_limiter())
    assert first.fingerprints == 0

    before = conn.execute("SELECT count(*) AS n FROM llm_call").fetchone()["n"]
    second = run_plan(
        conn, plan_for(conn, db_path, job_ids[:1]), working_client(), instant_limiter()
    )
    after = conn.execute("SELECT count(*) AS n FROM llm_call").fetchone()["n"]

    assert second.fingerprints == 1
    assert after > before, "the second run's attempts were silently discarded"

    # Same question, same ordinal, two executions, two rows.
    rows = conn.execute(
        "SELECT cache_key, execution_id, attempt FROM llm_call WHERE family = 'description'"
    ).fetchall()
    first_attempts = [r for r in rows if r["attempt"] == 1]
    assert len({r["execution_id"] for r in first_attempts}) == 2
    assert len({r["cache_key"] for r in first_attempts}) == 1
    assert first.execution_id != second.execution_id


def test_a_vendor_response_is_captured_before_anyone_interprets_it(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """`raw_output` is an adapter's reading of a response. That was not enough.

    Cerebras billed for 62,794 output tokens and returned empty content, and
    nothing survived that could say where the answer had gone -- an
    interpretation cannot explain itself. The envelope is the layer below: the
    vendor's own structure, captured before content extraction, parsing or
    validation.

    It must never carry a credential, and by construction it cannot: it is built
    from a response body, never from a request, its headers or a client.
    """
    import json

    from career_agent.llm.vendors import openai_compatible

    body = {
        "model": "z-ai/glm-5.2",
        "object": "chat.completion",
        "usage": {"prompt_tokens": 100, "completion_tokens": 0},
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "", "reasoning": "thought hard"},
            }
        ],
    }
    parsed = openai_compatible.parse_response(body)

    assert parsed.envelope is not None
    assert parsed.envelope["usage"] == {"prompt_tokens": 100, "completion_tokens": 0}
    assert parsed.envelope["choices"][0]["finish_reason"] == "stop"
    # The diagnosis the Cerebras run could not make: the answer was somewhere,
    # and the envelope says which fields had length.
    assert parsed.envelope["choices"][0]["message_keys"] == ["content", "reasoning", "role"]
    assert parsed.envelope["choices"][0]["field_lengths"]["reasoning"] == len("thought hard")

    serialised = json.dumps(parsed.envelope)
    for secret in ("api_key", "authorization", "bearer", "sk-"):
        assert secret not in serialised.lower()


def test_a_request_that_never_reached_http_costs_no_quota_and_stops_the_arm(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """Eighteen recorded failures and zero requests, told apart properly.

    A `TypeError` on an unknown SDK keyword raises before any HTTP request
    exists. Counting those as API calls would misstate the one number a free
    tier is measured in -- the GLM screen consumed 0 of a 50-request daily
    allowance while reporting 18 live calls.

    It is also always our defect rather than the vendor's: a request our own
    code could not construct will not construct on the next posting either. So
    it stops the arm instead of being repeated nine times.
    """
    from career_agent.llm.client import LLMNotSent

    client = ScriptedClient(
        answers={
            Family.DESCRIPTION: [LLMNotSent("openrouter request was never sent: TypeError")] * 9
        }
    )
    plan = plan_for(conn, db_path, job_ids)
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.live_calls == 0, "a request that never reached HTTP consumed quota"
    assert report.local_failures == 1
    assert report.jobs_run == 1, "the same local defect was repeated across postings"
    assert report.stopped_early and "unusable" in report.stopped_early

    # And it is still recorded. The diagnostic is the whole value of the run.
    rows = conn.execute("SELECT error FROM llm_call").fetchall()
    assert len(rows) == 1
    assert "never sent" in str(rows[0]["error"])


def test_a_ceiling_reached_mid_posting_keeps_the_attempts_already_made(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The third place evidence was lost, and the one that cost a canary.

    A budget stop used to propagate out of `extract_job` uncaught, taking the
    outcome -- and every attempt row already recorded on it -- with it. The
    OpenRouter canary made one real HTTP request, it failed, the retry hit the
    ceiling, and the record of what the vendor said went out with the exception.

    A ceiling ends a run. It does not un-make the requests that preceded it, and
    those are exactly the ones that were paid for.
    """
    client = ScriptedClient(
        answers={Family.DESCRIPTION: [LLMUnavailable("openrouter returned 400 bad request")] * 4}
    )
    plan = plan_for(conn, db_path, job_ids[:1], max_live_calls=1)
    report = run_plan(conn, plan, client, instant_limiter())

    assert report.live_calls == 1
    assert report.stopped_early and "budget" in report.stopped_early

    rows = conn.execute("SELECT error FROM llm_call").fetchall()
    assert len(rows) == 1, "the attempt made before the ceiling was discarded"
    assert "400 bad request" in str(rows[0]["error"])


def test_a_posting_the_ceiling_never_let_start_is_not_reported_as_run(
    conn: sqlite3.Connection, db_path: Path, job_ids: list[str]
) -> None:
    """The other half: do not invent a failure that never happened.

    A posting whose first request was refused by the budget made no attempt at
    all. Counting it would report one more failed extraction than occurred.
    """
    plan = plan_for(conn, db_path, job_ids, max_live_calls=2)
    report = run_plan(conn, plan, working_client(), instant_limiter())

    assert report.live_calls == 2
    assert report.jobs_run == 1
    assert report.failures == 0

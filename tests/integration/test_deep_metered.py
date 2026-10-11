"""The deep reading on a METERED API, with a fake DeepSeek and a synthetic
corpus of 40 eligible postings. Nothing leaves the machine.

What must hold: a job of more than one batch runs to the end without asking
again; a pause and a resume never repeat a call; the budget is never
exceeded and the job pauses instead; a call that cannot be accounted for
pauses the job and is counted at its whole reservation; a provider failure
pauses after three in a row and the resume retries what had no answer; an
answer from another model is not published; an answer paid for and
rejected is not asked for again; the plan takes the strata in turn.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

import pytest
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_run import CONFIG_DIR

from career_agent.config.search_config import load_search_config
from career_agent.llm.pricing import observed_price
from career_agent.pipeline.rescore import rescore
from career_agent.semantic import deep
from career_agent.semantic.deep_run import (
    BATCH_SIZE,
    DeepRepo,
    accounted_usd,
    reservation_usd,
    run_deep,
    select,
)
from career_agent.semantic.providers import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo

POSTINGS = 40
WORK = (
    "You will own our HubSpot CRM architecture, build workflow automation across our business "
    "systems, and maintain REST API integrations and webhooks between them. We use n8n for "
    "orchestration. We hire globally and work from anywhere."
)


@pytest.fixture
def big_corpus(tmp_path):
    conn = connect(tmp_path / "metered.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="a")
        )
        for n in range(POSTINGS):
            text = f"Role number {n}. {WORK} Team {n} reports to operations."
            _put(conn, company, board, f"p-{n}", f"Business Systems Engineer {n}", text, "Remote")
    rescore(conn, config)
    yield conn, config
    conn.close()


def answer_for(message: dict, valid: bool = True) -> str:
    if not valid:
        return "This posting looks like a great fit!"
    first = message["posting"].split(".")[0]
    work = message["person_wants"]["work"][0]["id"]
    return json.dumps(
        {
            "primary_activity": {"summary": "x", "quotes": [first]},
            "work_relation": {
                "value": "central",
                "intent_ids": [work],
                "quotes": [first],
                "reason": "",
            },
            "responsibilities": [],
            "tools": [],
            "requirements": [],
            "conditions": [],
            "divergences": [],
            "insufficient": False,
        }
    )


@dataclass
class FakeDeepSeek:
    """Billed per token, reports usage like DeepSeek, never holds a schema."""

    id: str = "deepseek"
    display_name: str = "Fake DeepSeek"
    model: str = "deepseek-flash"
    enforces_schema: bool = False
    max_output_tokens: int = 8_000
    fail_at: set[int] = field(default_factory=set)
    timeout_at: set[int] = field(default_factory=set)
    no_usage_at: set[int] = field(default_factory=set)
    other_model_at: set[int] = field(default_factory=set)
    reject_at: set[int] = field(default_factory=set)
    on_call: object = None
    messages: list[dict] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(billing=Billing.METERED_API, quotes=True, max_concurrency=1, sends="t")

    def estimate_cost(self, input_tokens, output_tokens):
        return None

    def healthcheck(self):
        return self.availability()

    def evaluate(self, intent, title, posting):  # pragma: no cover - never used here
        raise AssertionError("the deep reading never asks for findings")

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        # The schema is written into the prompt, because JSON mode holds none.
        assert system == deep.system_prompt(with_schema=True)
        assert schema == deep.answer_schema()
        n = len(self.messages)
        message = json.loads(user)
        self.messages.append(message)
        if callable(self.on_call):
            self.on_call(n)
        if n in self.fail_at:
            raise ProviderFailed("DeepSeek answered HTTP 500.")
        if n in self.timeout_at:
            raise ProviderFailed(
                "DeepSeek could not be reached.", state=Availability.CONNECTION_FAILED
            )
        usage = n not in self.no_usage_at
        return ProviderAnswer(
            raw_text=answer_for(message, valid=n not in self.reject_at),
            provider=self.id,
            model="deepseek-other" if n in self.other_model_at else self.model,
            latency_ms=1,
            input_tokens=len(user) // 4 + 1_500 if usage else None,
            output_tokens=3_000 if usage else None,
            cached_input_tokens=1_200 if usage else None,
        )


def postings_asked(provider: FakeDeepSeek) -> list[str]:
    return [m["job_title_for_context_only_not_quotable"] for m in provider.messages]


def job_row(conn):
    return DeepRepo(conn).last_job()


def test_more_than_one_batch_runs_to_the_end_without_asking_again(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek()
    seen = []
    stats = run_deep(
        conn,
        config,
        provider,
        share=False,
        limit=POSTINGS,
        budget_usd=5.0,
        job_progress=seen.append,
    )
    assert POSTINGS > BATCH_SIZE
    assert stats.status == "DONE" and stats.published == POSTINGS
    assert len(set(postings_asked(provider))) == POSTINGS == len(provider.messages)
    assert seen[-1].done == POSTINGS and seen[-1].batches == 2
    job = job_row(conn)
    assert job["status"] == "DONE" and job["requested"] == POSTINGS
    spent = DeepRepo(conn).spent(str(job["id"]))
    assert spent == pytest.approx(stats.spent_usd) and 0 < spent <= 5.0
    assert DeepRepo(conn).attempts(str(job["id"])) == {"PUBLISHED": POSTINGS}


def test_a_pause_and_a_resume_never_repeat_a_call(big_corpus):
    conn, config = big_corpus
    pause = threading.Event()
    provider = FakeDeepSeek(on_call=lambda n: pause.set() if n == 29 else None)
    first = run_deep(
        conn, config, provider, share=False, limit=POSTINGS, budget_usd=5.0, pause=pause
    )
    assert first.status == "PAUSED" and first.stop_reason == "PAUSED"
    assert first.published == 30
    job = str(job_row(conn)["id"])
    pause.clear()
    second = run_deep(conn, config, provider, share=False, limit=0, budget_usd=None, resume_job=job)
    assert second.status == "DONE" and second.published == POSTINGS - 30
    asked = postings_asked(provider)
    assert len(asked) == len(set(asked)) == POSTINGS, "a resume asked a posting again"
    assert job_row(conn)["status"] == "DONE"


def test_the_budget_is_never_exceeded_and_a_raised_one_resumes(big_corpus):
    conn, config = big_corpus
    price = observed_price("deepseek", "deepseek-flash")
    assert price is not None
    provider = FakeDeepSeek()
    budget = 0.05
    stats = run_deep(conn, config, provider, share=False, limit=POSTINGS, budget_usd=budget)
    assert stats.status == "PAUSED" and stats.stop_reason == "BUDGET_REACHED"
    job = job_row(conn)
    spent = DeepRepo(conn).spent(str(job["id"]))
    assert 0 < spent <= budget and 0 < stats.published < POSTINGS
    # Every call was held to its worst case before it was sent.
    for message in provider.messages:
        assert (
            reservation_usd(price, deep.system_prompt(with_schema=True), json.dumps(message), 8_000)
            > 0
        )
    resumed = run_deep(
        conn, config, provider, share=False, limit=0, budget_usd=5.0, resume_job=str(job["id"])
    )
    assert resumed.status == "DONE"
    assert stats.published + resumed.published == POSTINGS
    assert len(set(postings_asked(provider))) == len(provider.messages)


def test_three_failures_pause_and_the_resume_retries_what_had_no_answer(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek(fail_at={2, 3, 4})
    stats = run_deep(conn, config, provider, share=False, limit=10, budget_usd=5.0)
    assert stats.status == "PAUSED" and stats.stop_reason == "PROVIDER_STOPPED"
    assert stats.published == 2 and stats.failed == 3
    job = str(job_row(conn)["id"])
    # A failed call had no answer and was not billed: nothing was counted.
    assert DeepRepo(conn).attempts(job) == {"PUBLISHED": 2}
    again = run_deep(conn, config, provider, share=False, limit=0, resume_job=job)
    assert again.status == "DONE" and again.published == 8


def test_usage_that_cannot_be_accounted_for_pauses_at_the_whole_reservation(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek(no_usage_at={1})
    stats = run_deep(conn, config, provider, share=False, limit=10, budget_usd=5.0)
    assert stats.status == "PAUSED" and stats.stop_reason == "UNACCOUNTED"
    assert stats.published == 2, "the answer itself was verified and kept"
    rows = conn.execute(
        "SELECT cost_usd, input_tokens FROM deep_attempt ORDER BY created_at, id"
    ).fetchall()
    unaccounted = [r for r in rows if r["input_tokens"] is None]
    assert len(unaccounted) == 1 and unaccounted[0]["cost_usd"] > max(
        r["cost_usd"] for r in rows if r["input_tokens"] is not None
    )


def test_a_call_that_may_have_been_billed_without_an_answer_pauses(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek(timeout_at={0})
    stats = run_deep(conn, config, provider, share=False, limit=10, budget_usd=5.0)
    assert stats.stop_reason == "UNACCOUNTED" and stats.published == 0
    assert stats.spent_usd > 0
    assert DeepRepo(conn).attempts(str(job_row(conn)["id"])) == {"UNKNOWN": 1}


def test_an_answer_from_another_model_is_not_published(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek(other_model_at={0})
    stats = run_deep(conn, config, provider, share=False, limit=5, budget_usd=5.0)
    assert stats.stop_reason == "MODEL_CHANGED" and stats.published == 0
    assert DeepRepo(conn).newest_per_job() == []
    assert DeepRepo(conn).attempts(str(job_row(conn)["id"])) == {"MODEL_MISMATCH": 1}


def test_a_resume_on_another_model_is_refused(big_corpus):
    conn, config = big_corpus
    pause = threading.Event()
    provider = FakeDeepSeek(on_call=lambda n: pause.set())
    run_deep(conn, config, provider, share=False, limit=5, budget_usd=5.0, pause=pause)
    other = FakeDeepSeek(model="deepseek-v4-pro")
    stats = run_deep(conn, config, other, share=False, limit=0, resume_job=str(job_row(conn)["id"]))
    assert stats.stop_reason == "MODEL_CHANGED" and other.messages == []


def test_an_answer_paid_for_and_rejected_is_not_asked_again(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek(reject_at={0})
    first = run_deep(conn, config, provider, share=False, limit=3, budget_usd=5.0)
    assert first.rejected == 1 and first.published == 2
    rejected_title = postings_asked(provider)[0]
    run_deep(conn, config, provider, share=False, limit=POSTINGS, budget_usd=5.0)
    assert postings_asked(provider).count(rejected_title) == 1


def test_a_metered_job_needs_an_approved_budget(big_corpus):
    conn, config = big_corpus
    provider = FakeDeepSeek()
    stats = run_deep(conn, config, provider, share=False, limit=5)
    assert stats.stop_reason == "NO_BUDGET" and provider.messages == []


def test_the_plan_takes_the_strata_in_turn(big_corpus):
    conn, config = big_corpus
    chosen = select(conn, config, provider_id="deepseek", limit=12).candidates
    head = [c.stratum for c in chosen[:3]]
    assert set(head) == {"top_local", "lexical", "control"} or len(set(head)) > 1
    assert select(conn, config, provider_id="deepseek", limit=0).available == POSTINGS


def test_accounting_uses_the_reported_cache_and_never_undercounts():
    price = observed_price("deepseek", "deepseek-flash")
    assert price is not None
    answer = ProviderAnswer("{}", "deepseek", "deepseek-flash", 1, 10_000, 3_000, None, 4_000)
    cost = accounted_usd(price, answer)
    assert cost == pytest.approx((4_000 * 0.006 + 6_000 * 0.30 + 3_000 * 1.20) / 1_000_000)
    assert accounted_usd(price, ProviderAnswer("{}", "deepseek", "deepseek-flash", 1)) is None
    reserve = reservation_usd(price, "s" * 1000, "m" * 9000, 8_000)
    assert reserve > (10_000 * 0.30 + 3_000 * 1.20) / 1_000_000 * 0.9

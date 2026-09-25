"""A semantic run end to end, with a fake provider and a synthetic corpus.

Candidates are chosen, the provider is asked, the answer passes the gate, the
evaluation is stored, the posting is marked, and a rescore uses the finding.
And every way that can go wrong ends in NO finding, never in fit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import pytest
from tests.integration.test_score_replay import POSTINGS, _put
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.rescore import RescoreMode, rescore
from career_agent.semantic.intent import search_intent
from career_agent.semantic.providers import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)
from career_agent.semantic.routing import Route
from career_agent.semantic.runner import run_semantic, select_candidates
from career_agent.semantic.settings import SemanticSettings
from career_agent.semantic.store import SemanticRepo
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import deserialise_match
from career_agent.storage.records import (
    CompanyRecord,
    ProviderPayloadRecord,
    SourceBoardRecord,
)
from career_agent.storage.repositories import (
    CompanyRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

CONFIG_DIR = committed_config_dir()


@dataclass
class FakeProvider:
    """Answers by quoting the first sentence of the posting for one work item."""

    id: str = "fake"
    display_name: str = "Fake"
    billing: Billing = Billing.METERED_API
    cost: float = 0.001
    mode: str = "quote"
    calls: list[str] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(billing=self.billing, quotes=True, max_concurrency=2, sends="test")

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return self.cost if self.billing is Billing.METERED_API else None

    def healthcheck(self) -> ProviderStatus:
        return self.availability()

    def evaluate(self, intent, title, posting) -> ProviderAnswer:
        self.calls.append(title)
        if self.mode == "fail":
            raise ProviderFailed("down", state=Availability.CONNECTION_FAILED)
        if self.mode == "garbage":
            raw = "I think this is a great fit!"
        else:
            quote = posting.split(".")[0] if self.mode == "quote" else "Invented sentence here"
            work = intent.items[0].intent_id
            raw = json.dumps(
                {
                    "role_core": "x",
                    "work": {
                        "verdict": "strong",
                        "matches": [{"intent_id": work, "strength": "strong", "quotes": [quote]}],
                    },
                    "tools": {"verdict": "none", "matches": []},
                    "other": {"verdict": "unresolved", "matches": []},
                }
            )
        return ProviderAnswer(
            raw_text=raw,
            provider=self.id,
            model="fake-1",
            latency_ms=1,
            input_tokens=100,
            output_tokens=20,
            cost_usd=self.cost if self.billing is Billing.METERED_API else None,
        )


@pytest.fixture
def corpus(tmp_path):
    conn = connect(tmp_path / "semantic.db")
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    with transaction(conn):
        company_id = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board_id = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company_id, provider="greenhouse", board_identifier="a")
        )
        for external, (title, text, location) in POSTINGS.items():
            job_id = _put(conn, company_id, board_id, external, title, text, location)
            ProviderPayloadRepo(conn).put(
                ProviderPayloadRecord(
                    job_id=job_id,
                    provider="greenhouse",
                    payload={"id": external, "location": {"name": location or ""}},
                )
            )
    rescore(conn, config)
    yield conn, config
    conn.close()


def run(conn, config, provider, **settings):
    return run_semantic(
        conn,
        config,
        SemanticSettings(**settings),
        Route(provider),
        requested="auto",
    )


def rows(conn):
    return {
        str(r["job_id"]): deserialise_match(r["result_json"])
        for r in conn.execute("SELECT job_id, result_json FROM job_match").fetchall()
    }


def test_candidates_are_selected_from_the_intent_not_the_whole_corpus(corpus) -> None:
    conn, config = corpus
    selection = select_candidates(conn, config, search_intent(config), limit=100)
    titles = {c.title for c in selection.candidates}
    assert "Business Systems Engineer" in titles
    assert "Operations Coordinator" not in titles, "a posting with no intent words is not sent"
    assert selection.eligible == len(selection.candidates)


def test_a_run_publishes_marks_and_a_rescore_uses_the_findings(corpus) -> None:
    conn, config = corpus
    before = rows(conn)
    provider = FakeProvider()
    stats = run(conn, config, provider)
    assert stats.published == len(provider.calls) > 0
    assert stats.failed == 0 and stats.spent_usd == pytest.approx(0.001 * stats.calls)
    marked = {str(r[0]) for r in conn.execute("SELECT job_id FROM job_dirty").fetchall()}
    assert marked == set(SemanticRepo(conn).jobs_with_evaluations())

    rescore(conn, config, mode=RescoreMode.DIRTY, semantic=True)
    after = rows(conn)
    used = [job for job, result in after.items() if result.semantic is not None]
    assert used, "no score used the published findings"
    for job_id in used:
        result = after[job_id]
        assert result.semantic.provider == "fake" and result.semantic.model == "fake-1"
        semantic = [
            c for comp in result.components for c in comp.contributions if c.source == "semantic"
        ]
        for contribution in semantic:
            assert contribution.quote in _posting(conn, job_id)
        assert result.match_score >= before[job_id].match_score


def test_semantic_off_scores_deterministically_even_with_findings_stored(corpus) -> None:
    conn, config = corpus
    run(conn, config, FakeProvider())
    rescore(conn, config, mode=RescoreMode.DIRTY, semantic=False)
    assert all(result.semantic is None for result in rows(conn).values())


def test_a_second_run_asks_nothing_already_answered(corpus) -> None:
    conn, config = corpus
    first = FakeProvider()
    run(conn, config, first)
    second = FakeProvider()
    stats = run(conn, config, second)
    assert second.calls == [] and stats.candidates == 0


def test_the_budget_is_a_hard_stop(corpus) -> None:
    conn, config = corpus
    provider = FakeProvider(cost=0.4)
    stats = run(conn, config, provider, budget_per_run_usd=0.5)
    assert stats.candidates >= 2, "the budget, not the candidates, must end this run"
    assert stats.spent_usd <= 0.5
    assert stats.stop_reason == "BUDGET"
    assert len(provider.calls) == 1


def test_a_failing_provider_produces_no_finding_and_stops(corpus) -> None:
    conn, config = corpus
    before = rows(conn)
    stats = run(conn, config, FakeProvider(mode="fail"))
    assert stats.published == 0 and stats.failed >= 1
    assert conn.execute("SELECT count(*) FROM semantic_evaluation").fetchone()[0] == 0
    rescore(conn, config, semantic=True)
    assert {j: r.match_score for j, r in rows(conn).items()} == {
        j: r.match_score for j, r in before.items()
    }


def test_a_malformed_answer_is_not_stored_and_is_counted(corpus) -> None:
    conn, config = corpus
    stats = run(conn, config, FakeProvider(mode="garbage"))
    assert stats.rejected == stats.calls > 0 and stats.published == 0
    assert conn.execute("SELECT count(*) FROM semantic_evaluation").fetchone()[0] == 0


def test_an_invented_quote_is_stored_as_no_evidence_and_moves_nothing(corpus) -> None:
    conn, config = corpus
    before = rows(conn)
    run(conn, config, FakeProvider(mode="invent"))
    stored = conn.execute("SELECT evidence_json, gate_json FROM semantic_evaluation").fetchall()
    assert stored
    for evidence_json, gate_json in stored:
        assert json.loads(evidence_json)["matches"] == []
        assert json.loads(gate_json)["match_without_verifiable_quote"] >= 1
    rescore(conn, config, mode=RescoreMode.DIRTY, semantic=True)
    for job_id, result in rows(conn).items():
        assert result.match_score == before[job_id].match_score


def test_a_subscription_provider_is_capped_and_never_priced(corpus) -> None:
    conn, config = corpus
    provider = FakeProvider(billing=Billing.SUBSCRIPTION)
    stats = run(conn, config, provider, max_jobs_per_subscription_run=1)
    assert len(provider.calls) == 1
    assert stats.budget_usd is None and stats.spent_usd == 0


def test_no_provider_means_a_skipped_run_and_no_call(corpus) -> None:
    conn, config = corpus
    stats = run_semantic(
        conn, config, SemanticSettings(), Route(None, reason="nothing"), requested="auto"
    )
    assert stats.status == "SKIPPED" and stats.stop_reason == "NO_PROVIDER"


def _posting(conn, job_id: str) -> str:
    row = conn.execute(
        "SELECT r.description_text FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.id = ?",
        (job_id,),
    ).fetchone()
    return str(row[0])


def test_every_job_sharing_a_text_is_marked_with_its_evaluation(corpus) -> None:
    """Marked in the same transaction as the save: an evaluation can never be
    stored without the rescore request that makes it reach a score."""
    conn, config = corpus
    run(conn, config, FakeProvider())
    stored = {str(r[0]) for r in conn.execute("SELECT content_hash FROM semantic_evaluation")}
    sharing = {
        str(r[0])
        for r in conn.execute(
            f"SELECT id FROM job WHERE content_hash IN ({','.join('?' * len(stored))})",
            tuple(stored),
        )
    }
    marked = {str(r[0]) for r in conn.execute("SELECT job_id FROM job_dirty")}
    assert sharing and sharing <= marked


def test_a_run_that_dies_is_still_closed(corpus) -> None:
    conn, config = corpus

    class Exploding(FakeProvider):
        def evaluate(self, intent, title, posting):
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run(conn, config, Exploding())
    status, reason = conn.execute("SELECT status, stop_reason FROM semantic_run").fetchone()
    assert (status, reason) == ("FAILED", "ERROR")


def test_a_metered_provider_without_a_price_is_never_run(corpus) -> None:
    conn, config = corpus

    class Unpriced(FakeProvider):
        def estimate_cost(self, input_tokens, output_tokens):
            return None

    provider = Unpriced()
    stats = run(conn, config, provider)
    assert provider.calls == [] and stats.stop_reason == "PRICE_UNKNOWN"


def test_the_reservation_is_an_upper_bound_for_any_script() -> None:
    from career_agent.semantic.runner import ceiling_tokens, estimate_tokens

    greek = "Διαχείριση και ανάπτυξη υφιστάμενου πελατολογίου " * 50
    assert ceiling_tokens(greek) >= 2 * estimate_tokens(greek)

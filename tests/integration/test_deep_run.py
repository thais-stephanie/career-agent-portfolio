"""A deep-reading run end to end, with a fake subscription provider and a
synthetic corpus. Nothing leaves the machine.

What must hold: the local score and the evidence are untouched, nothing is
read twice for one provider, a plan limit pauses and the next run resumes,
a cancel stops, a metered provider is refused, and evidence leaves only when
this profile named this provider, without contact details.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

import pytest
from tests.integration.test_semantic_run import corpus  # noqa: F401 - fixture

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.semantic import deep
from career_agent.semantic.deep_run import DeepRepo, context, run_deep, select
from career_agent.semantic.providers import (
    Availability,
    Billing,
    Capabilities,
    ProviderAnswer,
    ProviderFailed,
    ProviderStatus,
)
from career_agent.storage.db import transaction
from career_agent.storage.repositories import ClaimRepo


@dataclass
class FakeCli:
    id: str = "fake_cli"
    display_name: str = "Fake CLI"
    billing: Billing = Billing.SUBSCRIPTION
    #: What the run is configured with (an alias like `sonnet`, or a full
    #: name), and the exact model the answer reports, as a real CLI does.
    model: str = ""
    answers_with: str = "fake-model"
    #: What the reading reports, so a test can steer what depends on it.
    relation: str = "central"
    support: str = "supported"
    limit_after: int | None = None
    on_call: object = None
    messages: list[dict] = field(default_factory=list)

    def availability(self) -> ProviderStatus:
        return ProviderStatus(Availability.AVAILABLE)

    def capabilities(self) -> Capabilities:
        return Capabilities(billing=self.billing, quotes=True, max_concurrency=1, sends="test")

    def estimate_cost(self, input_tokens: int, output_tokens: int) -> float | None:
        return None

    def healthcheck(self) -> ProviderStatus:
        return self.availability()

    def evaluate(self, intent, title, posting):  # pragma: no cover - not used here
        raise AssertionError("the deep reading never asks for findings")

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        assert system == deep.SYSTEM_PROMPT and schema == deep.answer_schema()
        if self.limit_after is not None and len(self.messages) >= self.limit_after:
            raise ProviderFailed("usage limit reached", limit=True)
        message = json.loads(user)
        self.messages.append(message)
        if callable(self.on_call):
            self.on_call()
        first = message["posting"].split(".")[0]
        work = message["person_wants"]["work"][0]["id"]
        evidence = [item["id"] for item in message["evidence"]][:1]
        raw = json.dumps(
            {
                "primary_activity": {"summary": "x", "quotes": [first]},
                "work_relation": {
                    "value": self.relation,
                    "intent_ids": [work],
                    "quotes": [first],
                    "reason": "",
                },
                "responsibilities": [],
                "tools": [],
                "requirements": [
                    {
                        "quote": first,
                        "kind": "mandatory",
                        "support": self.support,
                        "evidence_ids": evidence + ["E999"],
                        "reason": "",
                    }
                ],
                "conditions": [],
                "divergences": [],
                "insufficient": False,
            }
        )
        return ProviderAnswer(raw, self.id, self.answers_with, 1, 100, 20, None)


def snapshot(conn, table: str) -> list[tuple]:
    return [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()]


def add_claims(conn) -> None:
    with transaction(conn):
        conn.execute(
            "INSERT INTO candidate (id, candidate_key, display_name, created_at)"
            " VALUES ('c1', 'me', 'Fulana Exemplo', '2026-10-10T00:00:00Z')"
        )
        repo = ClaimRepo(conn)
        for key, kind, text, verified in (
            ("s1", ClaimType.SKILL, "CRM integrations", True),
            ("s2", ClaimType.SKILL, "Never confirmed skill", False),
            ("e1", ClaimType.EMPLOYMENT, "Fulana built CRM integrations, fulana@ex.io", True),
        ):
            repo.add(
                "c1",
                VerifiedClaim(
                    claim_key=key,
                    claim_type=kind,
                    text=text,
                    source=ClaimSource.SELF_ATTESTED,
                    verified=verified,
                ),
            )


def test_a_run_stores_reviews_and_moves_no_score_and_no_evidence(corpus):  # noqa: F811
    conn, config = corpus
    add_claims(conn)
    before = snapshot(conn, "job_match"), snapshot(conn, "verified_claim")
    provider = FakeCli()
    stats = run_deep(conn, config, provider, share=True, limit=10, include_unresolved=True)
    assert stats.status == "DONE" and stats.published == stats.candidates > 0
    assert (snapshot(conn, "job_match"), snapshot(conn, "verified_claim")) == before
    rows = [row for _job, row in DeepRepo(conn).newest_per_job()]
    assert {r["stratum"] for r in rows} <= {"top_local", "lexical", "control"}
    review = json.loads(rows[0]["review_json"])
    assert review["requirements"][0]["evidence_ids"] in ([], ["E1"])
    assert json.loads(rows[0]["gate_json"]).get("unknown_evidence_id") == 1


def test_only_confirmed_evidence_leaves_and_without_contact_or_name(corpus):  # noqa: F811
    conn, config = corpus
    add_claims(conn)
    provider = FakeCli()
    run_deep(conn, config, provider, share=True, limit=2, include_unresolved=True)
    sent = json.dumps([m["evidence"] for m in provider.messages])
    assert "Never confirmed" not in sent
    assert "fulana@" not in sent and "Fulana" not in sent


def test_without_consent_no_evidence_leaves(corpus):  # noqa: F811
    conn, config = corpus
    add_claims(conn)
    provider = FakeCli()
    run_deep(conn, config, provider, share=False, limit=2, include_unresolved=True)
    assert all(m["evidence"] == [] and not m["evidence_shared"] for m in provider.messages)


def test_nothing_is_read_twice_for_one_provider_and_another_can_compare(corpus):  # noqa: F811
    conn, config = corpus
    first = run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True)
    again = run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True)
    assert first.published > 0 and again.candidates == 0
    other = run_deep(
        conn, config, FakeCli(id="other_cli"), share=False, limit=10, include_unresolved=True
    )
    assert other.published == first.published


def test_a_plan_limit_pauses_and_the_next_run_resumes(corpus):  # noqa: F811
    conn, config = corpus
    paused = run_deep(
        conn, config, FakeCli(limit_after=1), share=False, limit=10, include_unresolved=True
    )
    assert paused.status == "PAUSED" and paused.stop_reason == "LIMIT_REACHED"
    assert paused.published == 1
    resumed = run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True)
    assert resumed.published == paused.candidates - 1


def test_a_cancel_stops_before_the_next_posting(corpus, monkeypatch):  # noqa: F811
    """One call at a time; with more in flight see the concurrency tests."""
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "1")
    conn, config = corpus
    cancel = threading.Event()
    provider = FakeCli(on_call=cancel.set)
    stats = run_deep(
        conn, config, provider, share=False, limit=10, include_unresolved=True, cancel=cancel
    )
    assert stats.stop_reason == "CANCELLED" and len(provider.messages) == 1


def test_a_metered_provider_without_a_price_or_a_budget_sends_nothing(corpus):  # noqa: F811
    conn, config = corpus
    unpriced = FakeCli(billing=Billing.METERED_API)
    stats = run_deep(
        conn, config, unpriced, share=True, limit=10, include_unresolved=True, budget_usd=1.0
    )
    assert stats.stop_reason == "NO_PRICE" and unpriced.messages == []


def test_a_local_model_is_not_offered_for_the_deep_reading(corpus):  # noqa: F811
    conn, config = corpus
    local = FakeCli(billing=Billing.LOCAL)
    stats = run_deep(conn, config, local, share=False, limit=10, include_unresolved=True)
    assert stats.stop_reason == "DEEP_UNSUPPORTED" and local.messages == []


def test_by_default_only_the_main_list_is_read(corpus):  # noqa: F811
    conn, config = corpus
    chosen = select(conn, config, share=False, provider_id="x", limit=10).candidates
    statuses = {
        str(r[0])
        for r in conn.execute(
            "SELECT eligibility_status FROM job_match WHERE job_id IN"
            f" ({','.join('?' for _ in chosen)})",
            [c.job_id for c in chosen],
        )
    }
    assert chosen, "the synthetic corpus must hold an eligible posting"
    assert statuses <= {"VERIFIED_ELIGIBLE", "LIKELY_ELIGIBLE"}


@pytest.mark.parametrize("limit", [1, 3])
def test_a_run_never_exceeds_its_limit(corpus, limit):  # noqa: F811
    conn, config = corpus
    stats = run_deep(conn, config, FakeCli(), share=False, limit=limit, include_unresolved=True)
    assert stats.candidates <= limit


def test_a_named_set_narrows_the_main_list_and_never_widens_it(corpus):  # noqa: F811
    conn, config = corpus
    everything = select(
        conn, config, share=False, provider_id="x", limit=25, include_unresolved=True
    )
    first = everything.candidates[0].job_id
    outside = "not-in-the-main-list"
    named = select(
        conn,
        config,
        share=False,
        provider_id="x",
        limit=25,
        include_unresolved=True,
        only={first, outside},
    )
    assert [c.job_id for c in named.candidates] == [first]


class _App:
    def __init__(self, config):
        self._config = config

    def search_config(self):
        return self._config


def _review_rows(config, conn):
    from career_agent.web.deep_api import _review_rows as rows

    return {r["job_id"]: r for r in rows(_App(config), conn)}


def test_a_changed_posting_makes_its_reading_out_of_date_not_current(corpus):  # noqa: F811
    conn, config = corpus
    run_deep(conn, config, FakeCli(), share=False, limit=3, include_unresolved=True)
    before = _review_rows(config, conn)
    assert before and not any(r["stale"] for r in before.values())
    job = next(iter(before))
    with transaction(conn):
        # The reading was made for an earlier text of this posting.
        conn.execute(
            "UPDATE deep_review SET content_hash = 'earlier-text' WHERE job_id = ?", (job,)
        )
    after = _review_rows(config, conn)[job]
    assert after["stale"] and after["stale_reasons"] == ["posting"]
    assert after["level"] == "STALE" and after["level_then"] == before[job]["level"]
    assert after["comparison"] == "stale"


def test_changed_evidence_is_named_as_evidence_not_as_the_search(corpus):  # noqa: F811
    """Only the evidence moved: the work fit stands (ADR-0035, addendum 5);
    whether the level stands is `holds_without_evidence_change`'s call."""
    conn, config = corpus
    add_claims(conn)
    run_deep(conn, config, FakeCli(), share=True, limit=2, include_unresolved=True)
    assert not any(r["stale"] for r in _review_rows(config, conn).values())
    with transaction(conn):
        ClaimRepo(conn).supersede(
            "c1",
            VerifiedClaim(
                claim_key="s1",
                revision=2,
                claim_type=ClaimType.SKILL,
                text="CRM and billing integrations",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )
    rows = _review_rows(config, conn).values()
    assert rows and all(r["stale_reasons"] == ["evidence"] for r in rows)
    assert all(r["state"] == "evidence" for r in rows)


def test_a_reading_without_evidence_ignores_evidence_edits(corpus):  # noqa: F811
    conn, config = corpus
    add_claims(conn)
    run_deep(conn, config, FakeCli(), share=False, limit=2, include_unresolved=True)
    with transaction(conn):
        ClaimRepo(conn).supersede(
            "c1",
            VerifiedClaim(
                claim_key="s1",
                revision=2,
                claim_type=ClaimType.SKILL,
                text="Edited",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )
    assert not any(r["stale"] for r in _review_rows(config, conn).values())


def test_coverage_counts_the_main_list_and_what_is_too_short(corpus):  # noqa: F811
    from career_agent.semantic.deep_run import coverage

    conn, config = corpus
    reach, readable = coverage(conn, config, include_unresolved=True)
    assert reach.readable == len(readable) and reach.roles >= reach.readable
    assert reach.too_short == reach.roles - reach.readable


def test_a_sign_in_problem_pauses_at_once(corpus):  # noqa: F811
    conn, config = corpus

    class Refreshing(FakeCli):
        def complete(self, system, user, schema):
            self.messages.append({})
            raise ProviderFailed(
                "Failed to refresh OAuth token: sign in again",
                state=Availability.SIGN_IN_REQUIRED,
            )

    provider = Refreshing()
    stats = run_deep(conn, config, provider, share=False, limit=5, include_unresolved=True)
    assert stats.status == "PAUSED" and stats.stop_reason == "SIGN_IN"
    assert len(provider.messages) == 1


# -- an alias and the model it resolved to (2026-10-19) -----------------------
#
# Claude Code is configured with the alias `sonnet` and every answer reports
# the model that actually answered, `claude-sonnet-5-5`. Asking "is there a
# reading by `sonnet`?" found none, so a new job planned all 706 postings the
# owner had already read as unread.


def _claude(model: str, answers_with: str, **kwargs) -> FakeCli:
    return FakeCli(id="claude_code", model=model, answers_with=answers_with, **kwargs)


def _already_read(conn, config, provider_id: str, model: str) -> int:
    digest = context(conn, config, False).digest
    return select(
        conn,
        config,
        share=False,
        provider_id=provider_id,
        limit=0,
        current_digest=digest,
        model=model,
        include_unresolved=True,
    ).reviewed


def test_a_new_job_counts_what_its_alias_already_read(corpus):  # noqa: F811
    conn, config = corpus
    first = run_deep(
        conn,
        config,
        _claude("sonnet", "claude-sonnet-5-5"),
        share=False,
        limit=10,
        include_unresolved=True,
    )
    assert first.published > 0
    again = run_deep(
        conn,
        config,
        _claude("sonnet", "claude-sonnet-5-5"),
        share=False,
        limit=10,
        include_unresolved=True,
    )
    assert again.candidates == 0, "nothing already read is planned again"


def test_a_full_name_still_matches_itself_only(corpus):  # noqa: F811
    conn, config = corpus
    first = run_deep(
        conn,
        config,
        _claude("claude-sonnet-5-5", "claude-sonnet-5-5"),
        share=False,
        limit=10,
        include_unresolved=True,
    )
    assert _already_read(conn, config, "claude_code", "claude-sonnet-5-5") >= first.published
    assert _already_read(conn, config, "claude_code", "claude-sonnet-4-5") == 0


def test_another_alias_is_another_model(corpus):  # noqa: F811
    conn, config = corpus
    run_deep(
        conn,
        config,
        _claude("sonnet", "claude-sonnet-5-5"),
        share=False,
        limit=10,
        include_unresolved=True,
    )
    assert _already_read(conn, config, "claude_code", "opus") == 0
    assert _already_read(conn, config, "claude_code", "haiku") == 0


def test_an_alias_means_the_newest_model_it_answered_with(corpus):  # noqa: F811
    """`sonnet` once answered as 4.5 and now answers as 5.5: what 4.5 read is a
    reading by a different model and is not counted as done for 5.5."""
    conn, config = corpus
    old = run_deep(
        conn,
        config,
        _claude("sonnet", "claude-sonnet-4-5"),
        share=False,
        limit=1,
        include_unresolved=True,
    )
    assert _already_read(conn, config, "claude_code", "sonnet") >= old.published
    new = run_deep(
        conn,
        config,
        _claude("sonnet", "claude-sonnet-5-5"),
        share=False,
        limit=1,
        include_unresolved=True,
    )
    assert new.published == 1, "the posting 4.5 read is not planned again"
    assert _already_read(conn, config, "claude_code", "claude-sonnet-4-5") > 0
    assert _already_read(conn, config, "claude_code", "sonnet") == _already_read(
        conn, config, "claude_code", "claude-sonnet-5-5"
    ), "only what the model the alias answers with NOW read counts as done"


def test_a_metered_model_name_is_never_an_alias(corpus):  # noqa: F811
    conn, config = corpus
    run_deep(
        conn,
        config,
        FakeCli(id="deepseek", model="deepseek-flash", answers_with="deepseek-flash"),
        share=False,
        limit=3,
        include_unresolved=True,
    )
    assert _already_read(conn, config, "deepseek", "deepseek-flash") > 0
    assert _already_read(conn, config, "deepseek", "deepseek-pro") == 0
    assert _already_read(conn, config, "deepseek", "flash") == 0
    # The aliases belong to Claude Code: the same word means nothing elsewhere.
    assert _already_read(conn, config, "deepseek", "sonnet") == 0


def test_a_job_spends_its_calls_in_the_plan_order_highest_band_first(corpus):  # noqa: F811
    """The order is the real order of the calls, not a sort on the screen."""
    conn, config = corpus
    stats = run_deep(conn, config, FakeCli(), share=False, limit=10, include_unresolved=True)
    assert stats.published > 1
    plan = json.loads(DeepRepo(conn).last_job()["plan_json"])
    asked = [r[0] for r in conn.execute("SELECT job_id FROM deep_attempt ORDER BY rowid")]
    assert asked == [job for job, _hash, _stratum in plan][: len(asked)]
    bands = ["STRONG", "GOOD", "MODERATE", "WEAK"]
    marks = ",".join("?" for _ in asked)
    band = dict(
        conn.execute(f"SELECT job_id, fit_band FROM job_match WHERE job_id IN ({marks})", asked)
    )
    seen = [bands.index(band[job]) for job in asked]
    assert seen == sorted(seen)


# --- Calls in flight at once: the answers are handled as one at a time ------


@dataclass
class SlowCli(FakeCli):
    """Holds each call a moment and records how many were in flight at once."""

    in_flight: int = 0
    most: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def complete(self, system: str, user: str, schema: dict) -> ProviderAnswer:
        import time

        with self.lock:
            self.in_flight += 1
            self.most = max(self.most, self.in_flight)
        try:
            time.sleep(0.05)
            with self.lock:
                return super().complete(system, user, schema)
        finally:
            with self.lock:
                self.in_flight -= 1


def _stored(conn) -> list[tuple]:
    return [
        tuple(r)
        for r in conn.execute(
            "SELECT job_id, content_hash, title, review_json FROM deep_review ORDER BY rowid"
        )
    ]


def test_calls_in_flight_store_what_one_at_a_time_stores_in_plan_order(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "3")
    provider = SlowCli()
    stats = run_deep(conn, config, provider, share=False, limit=10, include_unresolved=True)
    assert stats.published == stats.candidates >= 2 and stats.failed == stats.rejected == 0
    assert provider.most == min(3, stats.candidates), "the calls were really in flight together"
    plan = json.loads(DeepRepo(conn).last_job()["plan_json"])
    asked = [r[0] for r in conn.execute("SELECT job_id FROM deep_attempt ORDER BY rowid")]
    assert asked == [job for job, _hash, _stratum in plan][: len(asked)], "stored in plan order"
    concurrent = _stored(conn)
    with transaction(conn):
        conn.execute("DELETE FROM deep_attempt")
        conn.execute("DELETE FROM deep_review")
        conn.execute("DELETE FROM deep_job")
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "1")
    run_deep(conn, config, SlowCli(), share=False, limit=10, include_unresolved=True)
    assert _stored(conn) == concurrent, "the same readings, in the same order"


def test_a_pause_with_calls_in_flight_resumes_without_reading_twice(corpus, monkeypatch):  # noqa: F811
    conn, config = corpus
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "3")
    pause = threading.Event()
    first = SlowCli()
    first.on_call = pause.set
    paused = run_deep(
        conn, config, first, share=False, limit=10, include_unresolved=True, pause=pause
    )
    assert paused.status == "PAUSED" and paused.stop_reason == "PAUSED"
    assert len(first.messages) <= paused.published + 3, "at most the calls already in flight"
    job = DeepRepo(conn).last_job()["id"]
    resumed = run_deep(
        conn, config, SlowCli(), share=False, limit=10, include_unresolved=True, resume_job=job
    )
    assert resumed.status == "DONE"
    rows = _stored(conn)
    keys = [(job_id, h, t) for job_id, h, t, _ in rows]
    assert len(keys) == len(set(keys)) == paused.candidates, "every posting read once"


def test_a_metered_provider_is_never_asked_more_than_once_at_a_time(monkeypatch):
    from career_agent.semantic.deep_run import deep_concurrency

    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "4")
    assert deep_concurrency(Billing.METERED_API) == 1
    assert deep_concurrency(Billing.SUBSCRIPTION) == 4
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "50")
    assert deep_concurrency(Billing.SUBSCRIPTION) == 4
    monkeypatch.setenv("CAREER_AGENT_DEEP_CONCURRENCY", "nonsense")
    assert deep_concurrency(Billing.SUBSCRIPTION) == 2

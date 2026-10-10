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
from career_agent.semantic.deep_run import DeepRepo, run_deep, select
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
                    "value": "central",
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
                        "support": "supported",
                        "evidence_ids": evidence + ["E999"],
                        "reason": "",
                    }
                ],
                "conditions": [],
                "divergences": [],
                "insufficient": False,
            }
        )
        return ProviderAnswer(raw, self.id, "fake-model", 1, 100, 20, None)


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
    rows = DeepRepo(conn).newest_per_job()
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


def test_a_cancel_stops_before_the_next_posting(corpus):  # noqa: F811
    conn, config = corpus
    cancel = threading.Event()
    provider = FakeCli(on_call=cancel.set)
    stats = run_deep(
        conn, config, provider, share=False, limit=10, include_unresolved=True, cancel=cancel
    )
    assert stats.stop_reason == "CANCELLED" and len(provider.messages) == 1


def test_a_metered_provider_is_refused_before_anything_is_sent(corpus):  # noqa: F811
    conn, config = corpus
    provider = FakeCli(billing=Billing.METERED_API)
    stats = run_deep(conn, config, provider, share=True, limit=10, include_unresolved=True)
    assert stats.stop_reason == "DEEP_NEEDS_SUBSCRIPTION" and provider.messages == []


def test_by_default_only_the_main_list_is_read(corpus):  # noqa: F811
    conn, config = corpus
    chosen = select(conn, config, provider_id="x", limit=10).candidates
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
    everything = select(conn, config, provider_id="x", limit=25, include_unresolved=True)
    first = everything.candidates[0].job_id
    outside = "not-in-the-main-list"
    named = select(
        conn, config, provider_id="x", limit=25, include_unresolved=True, only={first, outside}
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


def test_changed_evidence_makes_a_reading_that_used_it_out_of_date(corpus):  # noqa: F811
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
    assert rows and all(r["stale_reasons"] == ["profile"] for r in rows)


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

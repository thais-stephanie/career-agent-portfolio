"""The Deep reading's routes with DeepSeek chosen: provider, consent, budget,
count. No provider is reached: availability is a local check and no run is
started. What must hold: the choice is kept per profile; consent given to one
provider is never read as consent for another; a configured key authorises
nothing; a metered preview needs a budget; the count is not capped at 25;
no response carries the key.
"""

from __future__ import annotations

import json

import pytest
from tests.integration.test_deep_metered import WORK
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_api import KEY, build

from career_agent.pipeline.rescore import rescore
from career_agent.runtime.mode import RuntimeMode
from career_agent.storage.db import connect, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo
from career_agent.web.server import ApiError

POSTINGS = 30


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    app = build(tmp_path, RuntimeMode.PERSONAL)
    config_dir = app.config.config_dir
    (config_dir / "search.local.yaml").write_bytes(
        (config_dir / "search.worked-example.yaml").read_bytes()
    )
    conn = connect(app.config.db_path)
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="a")
        )
        for n in range(POSTINGS):
            text = f"Role number {n}. {WORK} Team {n}."
            _put(conn, company, board, f"p-{n}", f"Business Systems Engineer {n}", text, "Remote")
    rescore(conn, app.search_config())
    conn.close()
    return app


def call(api, method, path, body=None):
    return api.handle_api(method, path, {}, body or {})


def test_deepseek_can_be_chosen_and_stays_chosen(api) -> None:
    status = call(api, "GET", "/api/deep")
    assert [p["id"] for p in status["providers"]] == ["claude_code", "codex", "deepseek"]
    deepseek = next(p for p in status["providers"] if p["id"] == "deepseek")
    assert deepseek["model"] == "deepseek-flash" and deepseek["connection"] == "api"
    assert deepseek["billing"] == "METERED_API" and deepseek["state"] == "KEY_MISSING"
    assert deepseek["price"]["output_per_mtok"] == 1.20 and deepseek["price"]["observed_at"]
    assert call(api, "POST", "/api/deep/provider", {"provider": "deepseek"}) == {
        "provider": "deepseek"
    }
    again = call(api, "GET", "/api/deep")
    assert again["provider"] == "deepseek"
    assert [p["id"] for p in again["providers"] if p["chosen"]] == ["deepseek"]
    with pytest.raises(ApiError):
        call(api, "POST", "/api/deep/provider", {"provider": "laya"})


def test_consent_for_claude_is_not_consent_for_deepseek(api) -> None:
    call(api, "POST", "/api/deep/provider", {"provider": "claude_code"})
    assert call(api, "POST", "/api/deep/consent", {"share_evidence": True}) == {
        "share_evidence": True
    }
    call(api, "POST", "/api/deep/provider", {"provider": "deepseek"})
    status = call(api, "GET", "/api/deep")
    assert status["share_evidence"] is False
    consent = {p["id"]: p["consent"] for p in status["providers"]}
    assert consent == {"claude_code": True, "codex": False, "deepseek": False}


def test_a_key_authorises_nothing_and_is_never_returned(api, monkeypatch) -> None:
    call(api, "POST", "/api/deep/provider", {"provider": "deepseek"})
    call(api, "POST", "/api/semantic/deepseek-key", {"key": KEY})
    status = call(api, "GET", "/api/deep")
    assert status["share_evidence"] is False, "a key is not consent"
    preview = call(api, "POST", "/api/deep/preview", {"limit": 3, "budget_usd": 0.5})
    assert all(m == 0 for m in preview["evidence_per_posting"])
    for payload in (status, preview):
        assert KEY not in json.dumps(payload) and KEY[-6:] not in json.dumps(payload)


def test_a_metered_preview_needs_a_budget_and_says_what_it_estimates(api) -> None:
    call(api, "POST", "/api/deep/provider", {"provider": "deepseek"})
    with pytest.raises(ApiError):
        call(api, "POST", "/api/deep/preview", {"limit": 3})
    with pytest.raises(ApiError):
        call(api, "POST", "/api/deep/preview", {"limit": 3, "budget_usd": 0})
    preview = call(api, "POST", "/api/deep/preview", {"limit": POSTINGS, "budget_usd": 1.0})
    assert preview["requested"] == POSTINGS > 25, "no total cap of 25"
    assert preview["available"] == POSTINGS and preview["batch_size"] == 25
    estimate = preview["estimate"]
    assert estimate["output_tokens_typical"] == 3_300 * POSTINGS
    assert estimate["off_peak_usd"] == pytest.approx(estimate["typical_usd"] / 2, abs=1e-4)
    assert estimate["worst_usd"] > estimate["typical_usd"] > 0
    assert estimate["covers_worst"] <= estimate["covers_typical"]
    assert preview["packet"]["schema_in_prompt"] is True
    with pytest.raises(ApiError):
        call(api, "POST", "/api/deep/preview", {"limit": POSTINGS + 1, "budget_usd": 1.0})


def test_a_run_without_a_key_is_refused_before_anything_starts(api) -> None:
    call(api, "POST", "/api/deep/provider", {"provider": "deepseek"})
    with pytest.raises(ApiError) as refused:
        call(
            api,
            "POST",
            "/api/deep/run",
            {"limit": 3, "budget_usd": 0.5, "confirm": True},
        )
    assert "PROVIDER_NOT_READY" in str(refused.value)
    assert call(api, "GET", "/api/deep")["job"] is None


def test_the_deep_provider_is_never_derived_from_the_smart_matching_choice(api) -> None:
    """Each function has its own provider; configuring one moves nothing in the other."""
    assert call(api, "GET", "/api/deep")["provider"] is None
    call(api, "PATCH", "/api/semantic", {"enabled": True, "mode": "claude_code"})
    assert call(api, "GET", "/api/deep")["provider"] is None
    call(api, "POST", "/api/deep/provider", {"provider": "codex"})
    call(api, "PATCH", "/api/semantic", {"mode": "deterministic"})
    assert call(api, "GET", "/api/deep")["provider"] == "codex"
    assert call(api, "GET", "/api/semantic")["settings"]["mode"] == "deterministic"

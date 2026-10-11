"""What an edit of the profile changes is decided by what it really changed.

Skills and evidence reach the Deep reading and resume preparation, never the
local score; matcher preferences move the stored scores; search words and
markets decide the next search; an edit that changes none of them says so.
Nothing here collects, recalculates or calls a model.
"""

from __future__ import annotations

import json

import pytest
from tests.integration.test_deep_metered import WORK
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_api import build

from career_agent.pipeline.rescore import rescore
from career_agent.runtime.mode import RuntimeMode
from career_agent.semantic.deep_run import DeepRepo, context
from career_agent.storage.db import connect, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo
from career_agent.web.server import ApiError


@pytest.fixture
def app(tmp_path):
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
        _put(conn, company, board, "p-1", "Systems Engineer", f"{WORK} Team.", "Remote")
    rescore(conn, app.search_config())
    conn.close()
    return app


def call(app, method, path, body=None):
    return app.handle_api(method, path, {}, body or {})


def store_deep_reading(app, *, shared: bool) -> str:
    """A stored Deep reading that answers the profile as it is now."""
    conn = connect(app.config.db_path)
    job = conn.execute("SELECT id, content_hash FROM job").fetchone()
    digest = context(conn, app.search_config(), shared).digest
    with transaction(conn):
        DeepRepo(conn).save(
            {
                "id": "deep_test",
                "job_id": job["id"],
                "content_hash": job["content_hash"],
                "profile_digest": digest,
                "contract": "deep-test",
                "provider": "claude_code",
                "model": "m",
                "stratum": "top_local",
                "review_json": "{}",
                "gate_json": "{}",
                "sent_json": json.dumps({"shared": shared, "evidence": {}}),
                "raw_response": "{}",
                "input_tokens": 1,
                "output_tokens": 1,
                "latency_ms": 1,
                "run_id": "r",
                "created_at": "2026-10-11T00:00:00Z",
            }
        )
    conn.close()
    return str(job["id"])


def test_a_skill_makes_a_shared_deep_reading_out_of_date_and_nothing_else(app):
    store_deep_reading(app, shared=True)
    made = call(
        app,
        "POST",
        "/api/evidence",
        {"claim_type": "SKILL", "text": "Synthetic skill", "tools": []},
    )
    assert made["effects"] == {"needs_rescore": False, "offer_discovery": False, "deep_stale": 1}


def test_a_skill_leaves_a_reading_that_never_saw_evidence_current(app):
    store_deep_reading(app, shared=False)
    made = call(
        app, "POST", "/api/evidence", {"claim_type": "SKILL", "text": "Another skill", "tools": []}
    )
    assert made["effects"] == {"needs_rescore": False, "offer_discovery": False, "deep_stale": 0}


def test_a_matcher_preference_needs_a_recalculation_and_a_phrase_offers_a_search(app):
    store_deep_reading(app, shared=False)
    changed = call(app, "PATCH", "/api/profile", {"changes": {"seniority_excluded": ["LEAD"]}})
    assert changed["effects"]["needs_rescore"] is True
    assert changed["effects"]["offer_discovery"] is False
    # A preferred level is part of what a Deep reading was asked.
    assert changed["effects"]["deep_stale"] == 0, (
        "an excluded level is not read by the deep reading"
    )


def test_role_anchors_offer_a_search_and_change_no_score(app):
    saved = call(app, "PATCH", "/api/role-anchors", {"anchors": [{"text": "Revenue Analyst"}]})
    assert saved["effects"] == {"needs_rescore": False, "offer_discovery": True, "deep_stale": 0}
    again = call(app, "PATCH", "/api/role-anchors", {"anchors": [{"text": "Revenue Analyst"}]})
    assert again["effects"]["offer_discovery"] is False, "the same roles change nothing"


def test_a_refused_edit_reports_nothing_and_a_read_carries_no_effects(app):
    with pytest.raises(ApiError):
        call(app, "PATCH", "/api/role-anchors", {"anchors": "not a list"})
    assert "effects" not in call(app, "GET", "/api/evidence")

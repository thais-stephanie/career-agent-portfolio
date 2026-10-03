"""Functional recovery A: Find jobs totals and the saved heart on the board.
Driven through `handle_api`, as the page does."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.storage import mvp_repo
from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = committed_config_dir()
DEMO_FILE = REPO_ROOT / "evaluation" / "demo" / "demo_postings.yaml"
TRACKED = [
    "SHORTLISTED",
    "APPLIED",
    "INTERVIEW",
    "OFFER",
    "HIRED",
    "REJECTED",
    "WITHDRAWN",
    "ARCHIVED",
]
#: The narrowings the Find jobs page applies by default.
NARROWED = {
    "include_ineligible": ["0"],
    "include_unresolved": ["0"],
    "include_off_target": ["0"],
    "include_user_hidden": ["0"],
}


def _api(tmp: Path) -> JobsApi:
    db_path = tmp / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    return JobsApi(ServerConfig(db_path=db_path, config_dir=CONFIG_DIR), quiet=True)


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return _api(tmp_path)


def _get(api: JobsApi, path: str, **params) -> dict:
    query = {k: (v if isinstance(v, list) else [str(v)]) for k, v in params.items()}
    return api.handle_api("GET", path, query, {})


# =========================================================================
# Find jobs: the list first, the "N more" counts after, both remembered
# =========================================================================


def test_without_asking_for_later_the_list_still_carries_its_counts(api: JobsApi) -> None:
    """Every caller but the page keeps the old contract: the counts are in."""
    payload = _get(api, "/api/jobs", **NARROWED)
    assert payload["narrowings_pending"] is False
    assert isinstance(payload["hidden_by_eligibility"], int)
    assert payload == {**payload, **_get(api, "/api/jobs/narrowings", **NARROWED)}


def test_hidden_counts_arrive_after_the_list_and_equal_the_old_reading(api: JobsApi) -> None:
    first = _get(api, "/api/jobs", **NARROWED, narrowings="later")
    assert first["narrowings_pending"] is True
    assert first["hidden_by_eligibility"] is None
    counts = _get(api, "/api/jobs/narrowings", **NARROWED)

    # The same numbers the per-box methods give, read directly.
    filter_ = api._filter_from({k: v for k, v in NARROWED.items()})
    config_id, version = api._identity()
    conn = connect(api.config.db_path)
    try:
        repo = mvp_repo.ScoredJobQuery(conn)
        total = repo.count(config_id, version, filter_)
        assert counts["hidden_by_eligibility"] == repo.hidden_by_eligibility(
            config_id, version, filter_, narrow_total=total
        )
        assert counts["hidden_unresolved"] == repo.hidden_unresolved(
            config_id, version, filter_, narrow_total=total
        )
    finally:
        conn.close()
    assert first["total"] == total
    # Asked again, the list carries them.
    again = _get(api, "/api/jobs", **NARROWED, narrowings="later")
    assert again["narrowings_pending"] is False
    assert again["hidden_by_eligibility"] == counts["hidden_by_eligibility"]


def test_a_second_page_or_another_order_does_not_count_the_corpus_again(
    api: JobsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    _get(api, "/api/jobs", **NARROWED, limit=5)
    calls: list[str] = []
    for name in ("count", "facets"):
        original = getattr(mvp_repo.ScoredJobQuery, name)

        def spy(self, *a, _name=name, _original=original, **k):
            calls.append(_name)
            return _original(self, *a, **k)

        monkeypatch.setattr(mvp_repo.ScoredJobQuery, name, spy)
    page_two = _get(api, "/api/jobs", **NARROWED, limit=5, offset=5)
    reordered = _get(api, "/api/jobs", **NARROWED, limit=5, sort="posted", direction="asc")
    assert calls == []
    assert page_two["total"] == reordered["total"]


def test_freshness_moves_on_a_write_and_the_counts_follow_it(api: JobsApi) -> None:
    before = _get(api, "/api/jobs/freshness")["freshness"]
    counts = _get(api, "/api/jobs/narrowings", **NARROWED)
    listed = _get(api, "/api/jobs", **NARROWED)
    job_id = listed["items"][0]["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/hidden", {}, {"hidden": True})
    assert _get(api, "/api/jobs/freshness")["freshness"] != before
    after = _get(api, "/api/jobs/narrowings", **NARROWED)
    assert after["hidden_by_you"] == counts["hidden_by_you"] + 1
    assert _get(api, "/api/jobs", **NARROWED)["total"] == listed["total"] - 1


# =========================================================================
# The heart puts a job on My applications; it never changes a status
# =========================================================================


def _board(api: JobsApi) -> list[dict]:
    return _get(api, "/api/jobs", status=TRACKED, with_saved=1, limit=60)["items"]


def test_a_saved_job_is_on_the_board_and_leaves_it_when_unsaved(api: JobsApi) -> None:
    job_id = _get(api, "/api/jobs")["items"][0]["job_id"]
    assert job_id not in {job["job_id"] for job in _board(api)}

    api.handle_api("PATCH", f"/api/jobs/{job_id}/saved", {}, {"saved": True})
    on_board = [job for job in _board(api) if job["job_id"] == job_id]
    assert len(on_board) == 1
    # Saved, and still DISCOVERED: the heart wrote no status.
    assert on_board[0]["saved"] is True
    assert on_board[0]["application_status"] in (None, "DISCOVERED")
    # Without the board's own ask, a status list is still only statuses.
    plain = _get(api, "/api/jobs", status=TRACKED, limit=60)["items"]
    assert job_id not in {job["job_id"] for job in plain}

    api.handle_api("PATCH", f"/api/jobs/{job_id}/saved", {}, {"saved": False})
    assert job_id not in {job["job_id"] for job in _board(api)}


def test_save_then_apply_is_one_card_in_applied(api: JobsApi) -> None:
    job_id = _get(api, "/api/jobs")["items"][1]["job_id"]
    api.handle_api("PATCH", f"/api/jobs/{job_id}/saved", {}, {"saved": True})
    api.handle_api("PATCH", f"/api/jobs/{job_id}/status", {}, {"status": "APPLIED"})
    rows = [job for job in _board(api) if job["job_id"] == job_id]
    assert [row["application_status"] for row in rows] == ["APPLIED"]
    # Unsaving an applied job keeps it where its status puts it.
    api.handle_api("PATCH", f"/api/jobs/{job_id}/saved", {}, {"saved": False})
    assert [job["application_status"] for job in _board(api) if job["job_id"] == job_id] == [
        "APPLIED"
    ]

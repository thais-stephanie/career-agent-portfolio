"""The first run of a new installation, after the collection (2026-10-10).

Scoring 124,233 postings ended, and the screen sat on "124,233 of 124,233"
while the same pass rebuilt the search index for minutes; a scoring failure
was never reported at all; and nothing said whether the scores on screen were
keywords or an AI evaluation. Each is a field the poll and the list now carry.
"""

from __future__ import annotations

from tests.integration.test_source_freshness import api  # noqa: F401 -- fixture

from career_agent.web.api import _scoring_view


def test_the_step_after_the_last_posting_is_named() -> None:
    running = {"boards_done": 10, "boards_total": 124233, "status": "running", "run_id": "r"}
    assert _scoring_view(True, running)["step"] == "scoring"
    indexing = {**running, "boards_done": 124233}
    view = _scoring_view(True, indexing)
    assert view["step"] == "preparing" and not view["failed"]


def test_a_scoring_failure_is_reported_with_its_words() -> None:
    failed = {
        "boards_done": 3,
        "boards_total": 9,
        "status": "failed",
        "error": "database is locked",
        "run_id": "r",
    }
    view = _scoring_view(False, failed)
    assert view["failed"] is True and view["error"] == "database is locked"
    assert _scoring_view(False, {**failed, "status": "done"})["error"] is None
    assert _scoring_view(False, None) == {
        "running": False,
        "done": 0,
        "total": 0,
        "step": "scoring",
        "run_id": None,
        "failed": False,
        "error": None,
    }


def test_the_list_says_its_scores_are_keywords_until_an_ai_evaluation_ran(api) -> None:  # noqa: F811
    basis = api.handle_api("GET", "/api/jobs", {}, {})["scoring_basis"]
    assert basis == {"ai": False, "evaluated": 0, "mode": "deterministic"}
    # Choosing a provider evaluates nothing: still keywords only.
    api.handle_api("PATCH", "/api/semantic", {}, {"mode": "deepseek"})
    basis = api.handle_api("GET", "/api/jobs", {}, {})["scoring_basis"]
    assert basis["ai"] is False and basis["mode"] == "deepseek"

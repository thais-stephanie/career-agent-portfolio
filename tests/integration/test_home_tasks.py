"""Home's "Small things to do": scores to recalculate and right-to-work
questions nobody answered, per profile. Synthetic corpus, no network, no model.
"""

from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest
import yaml
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import plan, rescore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import ApiError, JobsApi
from career_agent.web.server import ServerConfig

DEMO_FILE = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


def _app(root: Path, **eligibility) -> JobsApi:
    config_dir = root / "config"
    shutil.copytree(committed_config_dir(), config_dir)
    if eligibility:
        path = config_dir / "search.local.yaml"
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        document["eligibility"].update(eligibility)
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    db = root / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, root.name)
    config, _ = load_search_config(config_dir)
    seed_demo(conn, config, source=DEMO_FILE)
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config_dir), quiet=True)


def _home(app: JobsApi) -> dict:
    return app.handle_api("GET", "/api/home", {}, {})


def _bump_version(app: JobsApi) -> None:
    """The configuration moves on without a recalculation: every score is now
    an answer to an older question."""
    path = app.config.config_dir / "search.local.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["config_version"] = int(document.get("config_version", 1)) + 1
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    app._search_config = None


@pytest.fixture
def app(tmp_path: Path) -> JobsApi:
    return _app(tmp_path / "one", authorized_countries=[], not_authorized_countries=[],
                sponsorship_needed=[], contractor=None, contractor_abroad=None)  # fmt: skip


def test_unanswered_questions_are_listed_and_i_dont_know_closes_one(app: JobsApi) -> None:
    assert _home(app)["declarations_pending"] == [
        "authorization", "contractor", "contractor_abroad",
    ]  # fmt: skip
    version = app.search_config().config_version

    answer = app.handle_api(
        "PATCH", "/api/profile/unsure", {}, {"question": "contractor_abroad", "unsure": True}
    )

    assert answer["unsure"] == ["contractor_abroad"]
    assert _home(app)["declarations_pending"] == ["authorization", "contractor"]
    # Not knowing changes no configuration and no score.
    app._search_config = None
    assert app.search_config().config_version == version
    rows = {row["field"]: row for row in app.handle_api("GET", "/api/profile", {}, {})["editable"]}
    assert rows["contractor_abroad"]["question"] == "contractor_abroad"
    assert rows["contractor_abroad"]["unsure"] is True
    assert rows["authorized_countries"]["question"] == "authorization"
    assert "question" not in rows["not_authorized_countries"]

    app.handle_api(
        "PATCH", "/api/profile/unsure", {}, {"question": "contractor_abroad", "unsure": False}
    )
    assert "contractor_abroad" in _home(app)["declarations_pending"]


def test_an_answer_closes_its_question(app: JobsApi) -> None:
    app.handle_api(
        "PATCH",
        "/api/profile",
        {},
        {"changes": {"contractor": False, "authorized_countries": ["BR"]}},
    )
    assert _home(app)["declarations_pending"] == ["contractor_abroad"]


def test_only_known_questions_are_accepted(app: JobsApi) -> None:
    for body in ({"question": "nationality", "unsure": True}, {"question": "contractor"}):
        with pytest.raises(ApiError) as caught:
            app.handle_api("PATCH", "/api/profile/unsure", {}, body)
        assert caught.value.status == 400


def test_one_profiles_i_dont_know_does_not_reach_another(tmp_path: Path) -> None:
    neutral = {"authorized_countries": [], "contractor": None, "contractor_abroad": None}
    first, second = _app(tmp_path / "first", **neutral), _app(tmp_path / "second", **neutral)
    first.handle_api("PATCH", "/api/profile/unsure", {}, {"question": "contractor", "unsure": True})
    assert "contractor" not in _home(first)["declarations_pending"]
    assert "contractor" in _home(second)["declarations_pending"]


def test_markets_are_optional_and_never_a_task(app: JobsApi) -> None:
    app.handle_api("PATCH", "/api/profile", {}, {"changes": {"target_markets": []}})
    payload = _home(app)
    assert "target_markets" not in payload["declarations_pending"]
    assert "markets" not in " ".join(payload["profile_gaps"])


def test_outdated_scores_are_what_recalculate_would_score(app: JobsApi) -> None:
    # A seeded posting is work until a real pass acknowledges it (by design:
    # `MatchRepo.settle_dirty`), so the count starts at what a pass would do.
    with connect(app.config.db_path) as conn:
        seeded = len(plan(conn, app.search_config()).targets)
        assert _home(app)["scores"] == {"outdated": seeded, "running": False}
        rescore(conn, app.search_config())
    assert _home(app)["scores"] == {"outdated": 0, "running": False}

    _bump_version(app)

    outdated = _home(app)["scores"]["outdated"]
    with connect(app.config.db_path) as conn:
        expected = len(plan(conn, app.search_config()).targets)
    assert outdated == expected > 0


def test_a_running_recalculation_is_reported_and_a_second_is_refused(app: JobsApi) -> None:
    release = threading.Event()
    app.rescore.start(lambda state, cancel: release.wait(10), "held-run")
    try:
        assert _home(app)["scores"]["running"] is True
        with pytest.raises(ApiError) as caught:
            app.handle_api("POST", "/api/rescore", {}, {})
        assert caught.value.status == 409
    finally:
        release.set()
        app.rescore.join(10)
    assert _home(app)["scores"]["running"] is False

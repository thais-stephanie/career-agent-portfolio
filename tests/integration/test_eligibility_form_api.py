"""The eligibility form over the API, two invented profiles side by side.

What a save changes (a recalculation only when an answer can move a verdict),
what "I don't know" closes, what happens to the retired question's answer,
and that one profile's answers never reach another's.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from career_agent.config.candidate_writer import current_candidate_fields, set_candidate_fields
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO = Path(__file__).resolve().parents[2]


def _profile(tmp_path: Path, name: str) -> JobsApi:
    config_dir = tmp_path / name / "config"
    shutil.copytree(REPO / "config", config_dir, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / name / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, name)
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=0), quiet=True)


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return _profile(tmp_path, "one")


def _patch(api: JobsApi, changes: dict) -> dict:
    return api.handle_api("PATCH", "/api/profile", {}, {"changes": changes})


def _fields(api: JobsApi) -> dict[str, dict]:
    payload = api.handle_api("GET", "/api/profile", {}, {})
    return {row["field"]: row for row in payload["editable"]}


def _where(api: JobsApi) -> dict:
    payload = api.handle_api("GET", "/api/firstrun", {}, {})
    return next(step for step in payload["steps"] if step["key"] == "where")


def test_an_authorization_asks_for_a_recalculation_and_a_market_does_not(api: JobsApi) -> None:
    version = api.search_config().config_version
    answer = _patch(api, {"target_markets": ["NORTH_AMERICA", "EUROPE"]})
    assert answer["rescore_required"] is False and answer["config_version"] == version
    answer = _patch(api, {"authorized_countries": ["BR"]})
    assert answer["rescore_required"] is True and answer["config_version"] == version + 1


def test_the_retired_question_is_flagged_and_its_answer_kept_as_given(api: JobsApi) -> None:
    set_candidate_fields(api.config.config_dir, {"eligible_countries": ["BR", "PT"]})
    api._search_config = None
    fields = _fields(api)
    assert fields["eligible_countries"]["legacy"] is True
    assert fields["eligible_countries"]["value"] == ["BR", "PT"]
    assert fields["authorized_countries"]["legacy"] is False
    # Never copied into the new question.
    assert not fields["authorized_countries"]["value"]
    # Answering the new question leaves the old answer exactly as it was...
    _patch(api, {"authorized_countries": ["BR"]})
    assert current_candidate_fields(api.config.config_dir)["eligible_countries"] == ["BR", "PT"]
    # ...and removing it is her explicit act.
    _patch(api, {"eligible_countries": []})
    assert current_candidate_fields(api.config.config_dir)["eligible_countries"] == []


def test_where_you_can_work_is_answered_by_an_authorization_or_by_i_dont_know(api) -> None:
    _patch(api, {"candidate_country": "BR"})
    assert _where(api)["done"] is False
    api.handle_api(
        "PATCH", "/api/profile/unsure", {}, {"question": "authorization", "unsure": True}
    )
    assert _where(api)["done"] is True
    api.handle_api(
        "PATCH", "/api/profile/unsure", {}, {"question": "authorization", "unsure": False}
    )
    assert _where(api)["done"] is False
    _patch(api, {"authorized_countries": ["BR"]})
    assert _where(api)["done"] is True


def test_a_setup_finished_with_the_retired_question_stays_finished(api: JobsApi) -> None:
    set_candidate_fields(
        api.config.config_dir, {"candidate_country": "BR", "eligible_countries": ["BR"]}
    )
    api._search_config = None
    assert _where(api)["done"] is True


def test_home_asks_once_and_i_dont_know_closes_it(api: JobsApi) -> None:
    def pending() -> list[str]:
        payload = api.handle_api("GET", "/api/home", {}, {})
        return list(payload.get("declarations_pending") or [])

    def gaps() -> list[str]:
        payload = api.handle_api("GET", "/api/home", {}, {})
        return list(payload.get("profile_gaps") or [])

    _patch(api, {"candidate_country": "BR"})
    assert "authorization" in pending()
    assert "hiring_scopes" not in gaps()
    api.handle_api(
        "PATCH", "/api/profile/unsure", {}, {"question": "authorization", "unsure": True}
    )
    assert "authorization" not in pending()


def test_one_profiles_answers_never_reach_another(tmp_path: Path) -> None:
    first, second = _profile(tmp_path, "first"), _profile(tmp_path, "second")
    _patch(first, {"candidate_country": "BR", "authorized_countries": ["BR"],
                   "target_markets": ["NORTH_AMERICA", "EUROPE"]})  # fmt: skip
    first.handle_api("PATCH", "/api/profile/unsure", {}, {"question": "contractor", "unsure": True})
    _patch(second, {"candidate_country": "PT", "authorized_countries": ["PT", "ES"]})

    one, two = _fields(first), _fields(second)
    assert one["authorized_countries"]["value"] == ["BR"]
    assert two["authorized_countries"]["value"] == ["PT", "ES"]
    assert not two["target_markets"]["value"]
    assert two["contractor"].get("unsure") is False
    assert one["contractor"].get("unsure") is True

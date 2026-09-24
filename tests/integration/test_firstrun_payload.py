"""What `GET /api/firstrun` tells the setup, and what it no longer asks.

The setup shows back the words that were typed, offers only the hiring regions
that contain where the person lives, and does not ask career stage, which
nothing reads (docs/ONBOARDING.md).
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from career_agent.config.candidate_writer import set_candidate_fields
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    config_dir = tmp_path / "config"
    shutil.copytree(REPO / "config", config_dir, ignore=shutil.ignore_patterns("*.local.*"))
    db = tmp_path / "personal.db"
    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "firstrun payload")
    finally:
        conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=config_dir, port=0), quiet=True)


def steps(api: JobsApi) -> dict[str, dict]:
    payload = api.handle_api("GET", "/api/firstrun", {}, {})
    return {step["key"]: step for step in payload["steps"]}


def test_career_stage_is_not_a_step_but_a_given_answer_is_kept(api: JobsApi) -> None:
    assert "career_stage" not in steps(api)
    api.handle_api("POST", "/api/firstrun/stage", {}, {"stage": "CHANGING_CAREERS"})
    payload = api.handle_api("GET", "/api/firstrun", {}, {})
    assert "career_stage" not in {step["key"] for step in payload["steps"]}
    assert payload["career_stage"] == "CHANGING_CAREERS"


def test_the_words_given_to_the_setup_come_back(api: JobsApi) -> None:
    api.handle_api(
        "POST",
        "/api/first-search",
        {},
        {"role_examples": ["Customer onboarding", "Implementation"], "skills": ["HubSpot"]},
    )
    work = steps(api)["work"]
    assert work["done"]
    assert sorted(work["roles"]) == ["Customer onboarding", "Implementation"]
    assert work["skills"] == ["HubSpot"]


@pytest.mark.parametrize(
    ("country", "regions"),
    [("BR", ["WORLDWIDE", "AMERICAS", "LATAM"]), ("PT", ["WORLDWIDE", "EMEA"]), ("", [])],
)
def test_only_regions_containing_where_she_lives_are_offered(
    api: JobsApi, country: str, regions: list[str]
) -> None:
    if country:
        set_candidate_fields(api.config.config_dir, {"candidate_country": country})
        api._search_config = None
    assert steps(api)["where"]["regions"] == regions

"""The preview contract is enforced by the real dispatcher, including fresh installs."""

import pytest
from tests.support import committed_config_dir

from career_agent.storage.db import connect, migrate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


@pytest.fixture
def api(tmp_path):
    db = tmp_path / "api.db"
    conn = connect(db)
    migrate(conn)
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir()), quiet=True)


def test_fresh_candidate_metadata_preview_and_linked_text_edit(api):
    command = {"action": "create", "metadata": {"company": "Example", "title": "Coordinator"}}
    preview = api.handle_api("POST", "/api/career/preview", {}, command)
    created = api.handle_api(
        "POST",
        "/api/career/changes",
        {},
        {
            "command": command,
            "preview_hash": preview["preview_hash"],
        },
    )
    api.handle_api(
        "POST",
        "/api/evidence",
        {},
        {
            "text": "Owned the handover process",
            "claim_type": "EMPLOYMENT",
            "employer": "Example Ltd",
            "period_start": "2020-01",
            "period_end": "2022-12",
            "experience_id": created["experience_id"],
        },
    )
    page = api.handle_api("GET", "/api/career/evidence", {}, {})
    key = page["keys"][0]
    api.handle_api(
        "PATCH", f"/api/evidence/{key}", {}, {"text": "Owned the weekly handover process"}
    )
    item = api.handle_api("GET", "/api/career/evidence", {}, {})["items"][0]
    assert item["employer"] == "Example Ltd"
    assert item["period_start"] == "2020-01" and item["period_end"] == "2022-12"
    assert item["revision"] == 2 and item["experience_id"] == created["experience_id"]


def test_stale_and_malformed_requests_fail_before_writing(api):
    with pytest.raises(ApiError):
        api.handle_api("POST", "/api/career/preview", {}, {"action": []})
    with pytest.raises(ApiError):
        api.handle_api(
            "POST",
            "/api/career/changes",
            {},
            {
                "command": {"action": "create", "metadata": {"title": "Project"}},
                "preview_hash": "stale",
            },
        )
    assert api.handle_api("GET", "/api/career", {}, {})["experiences"] == []

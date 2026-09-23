"""A past collection and unfinished receipt must render without claiming liveness."""

from pathlib import Path

from tests.browser.home_helpers import open_home_past_setup
from tests.browser.test_release_personas import Workspace

from career_agent.storage.db import connect, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


def test_settings_renders_history_without_claiming_a_live_refresh(page, tmp_path):
    ws = Workspace(tmp_path)
    try:
        with connect(ws.db) as conn, transaction(conn):
            company = CompanyRepo(conn).upsert(CompanyRecord(slug="invented", name="Invented"))
            SourceBoardRepo(conn).upsert(
                SourceBoardRecord(
                    company_id=company, provider="greenhouse", board_identifier="invented"
                )
            )
            conn.execute("UPDATE source_board SET last_collected_at='2026-09-21T00:00:00Z'")
            conn.execute(
                "INSERT INTO pipeline_run (id,stage,started_at,status,stats_json)"
                " VALUES ('fixture','source-maintenance','2026-09-21T01:00:00Z','RUNNING','{}')"
            )
        status = ws.app.handle_api("GET", "/api/source-maintenance", {}, {})
        assert status["last_successful_check"] == "2026-09-21T00:00:00Z"
        assert not status["maintenance_running"] and not status["app_refresh_running"]
        open_home_past_setup(page, ws.base)
        page.evaluate("document.querySelector('.topnav__link[data-page=\"settings\"]').click()")
        page.wait_for("document.querySelector('.maintenance')")
        text = str(page.evaluate("document.querySelector('.maintenance').innerText"))
        assert "2026-09-21 00:00" in text
        assert "No refresh is running" in text
        assert "no completion record" in text
        assert "pending; completed jobs are kept" in text
        page.screenshot(Path("out/rc-maintenance-history.png"))
    finally:
        ws.stop()

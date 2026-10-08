"""After an update that reads the same configuration differently, the notice
offers the recalculation, a partial pass resumes, and nothing is silent.

Synthetic demo corpus in a temporary database. A score written under another
reading of the SAME configuration version (here: an older digest, which is what
an update adding a declaration with its default produces) is not current: the
targeted plan takes it, and `/api/health` counts it for the notice.
"""

from __future__ import annotations

from pathlib import Path

from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.pipeline.rescore import RescoreMode, plan, rescore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ServerConfig

DEMO = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


def test_an_older_reading_of_the_same_settings_is_offered_for_recalculation(
    tmp_path: Path,
) -> None:
    db = tmp_path / "personal.db"
    config, _ = load_search_config(committed_config_dir())
    with connect(db) as conn:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
        seed_demo(conn, config, source=DEMO)
        total = conn.execute("SELECT COUNT(*) FROM job_match").fetchone()[0]
        with transaction(conn):
            # As an update leaves them: same version, worked out the old way.
            conn.execute("UPDATE job_match SET config_digest = 'an-older-reading'")
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    assert api.handle_api("GET", "/api/health", {}, {})["stale_scores"] == total

    with connect(db) as conn:
        assert len(plan(conn, config, mode=RescoreMode.TARGETED).targets) == total
        rescore(conn, config, mode=RescoreMode.TARGETED, limit=5)  # interrupted
        assert len(plan(conn, config, mode=RescoreMode.TARGETED).targets) == total - 5
        rescore(conn, config, mode=RescoreMode.TARGETED)  # resumed: the rest only
        assert plan(conn, config, mode=RescoreMode.TARGETED).targets == []
    assert api.handle_api("GET", "/api/health", {}, {})["stale_scores"] == 0


def test_a_row_without_a_recorded_digest_is_not_counted(tmp_path: Path) -> None:
    db = tmp_path / "personal.db"
    config, _ = load_search_config(committed_config_dir())
    with connect(db) as conn:
        migrate(conn)
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
        seed_demo(conn, config, source=DEMO)
        with transaction(conn):
            conn.execute("UPDATE job_match SET config_digest = 'UNRECORDED'")
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    assert api.handle_api("GET", "/api/health", {}, {})["stale_scores"] == 0

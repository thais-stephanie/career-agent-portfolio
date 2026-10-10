"""The repost a candidate can take stands for its group (2026-10-10).

Pavago posted one Full Stack Engineer four times: India 68, Pakistan 66,
LATAM-including-Brazil 57 and US 42. The card showed the India copy, "to
confirm where they hire", and hid the one that confirms her. Built here on the
demo corpus: four demo postings are made one company and one title, with the
four statuses and scores of that case. Nothing moves between the copies.
"""

from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Iterator

import pytest
from tests.support import committed_config_dir

from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.mvp_repo import JobFilter, ScoredJobQuery

CONFIG_DIR = committed_config_dir()
DEMO_FILE = pathlib.Path("evaluation") / "demo" / "demo_postings.yaml"
TITLE = "Full Stack Engineer (Pavago regression)"

#: (where it was posted, eligibility, score), as on the owner's copy.
COPIES = [
    ("IN", "UNRESOLVED", 68),
    ("PK", "UNRESOLVED", 66),
    ("MX / CO / BR / CR", "VERIFIED_ELIGIBLE", 57),
    ("US", "UNRESOLVED", 42),
]


@pytest.fixture(scope="module")
def pavago() -> Iterator[tuple[ScoredJobQuery, str, int, dict[str, str]]]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="pavago")) / "demo.db"
    conn = connect(db_path)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.DEMO, "demo")
    config, _ = load_search_config(CONFIG_DIR)
    seed_demo(conn, config, source=DEMO_FILE)
    config_id, version = str(config.config_id), int(config.config_version)
    ids = [row[0] for row in conn.execute("SELECT id FROM job ORDER BY id LIMIT 4")]
    company = conn.execute("SELECT company_id FROM job WHERE id = ?", (ids[0],)).fetchone()[0]
    by_location: dict[str, str] = {}
    with transaction(conn):
        for job_id, (location, status, score) in zip(ids, COPIES, strict=True):
            conn.execute(
                "UPDATE job SET company_id = ?, title = ?, location_raw = ? WHERE id = ?",
                (company, TITLE, location, job_id),
            )
            conn.execute(
                "UPDATE job_match SET eligibility_status = ?, match_score = ?,"
                " screening_state = 'NOT_BLOCKED' WHERE job_id = ? AND config_id = ?"
                " AND config_version = ?",
                (status, score, job_id, config_id, version),
            )
            by_location[location] = job_id
    try:
        yield ScoredJobQuery(conn), config_id, version, by_location
    finally:
        conn.close()


def _group(query: ScoredJobQuery, config_id: str, version: int, **kwargs) -> list:
    rows = query.page(config_id, version, JobFilter(group_duplicates=True, limit=500, **kwargs))
    return [row for row in rows if row.title == TITLE]


def test_the_copy_she_can_take_stands_for_the_group(pavago) -> None:
    query, config_id, version, by_location = pavago
    shown = _group(query, config_id, version, include_unresolved=True)
    assert len(shown) == 1
    assert shown[0].job_id == by_location["MX / CO / BR / CR"]


def test_with_uncertain_territory_hidden_it_is_still_that_copy(pavago) -> None:
    query, config_id, version, by_location = pavago
    shown = _group(query, config_id, version, include_unresolved=False)
    assert [row.job_id for row in shown] == [by_location["MX / CO / BR / CR"]]


def test_no_copy_borrows_another_copys_eligibility(pavago) -> None:
    query, config_id, version, by_location = pavago
    statuses = dict(
        query.conn.execute(
            "SELECT job_id, eligibility_status FROM job_match WHERE config_id = ?"
            " AND config_version = ? AND job_id IN (?, ?, ?, ?)",
            (config_id, version, *by_location.values()),
        ).fetchall()
    )
    for location, status, _ in COPIES:
        assert statuses[by_location[location]] == status

"""Opening a posting whose stored score answers OLDER preferences.

A preference edit raises `config_version`; until the next recalculation there
is no score at the new version. The list serves the previous answer, labelled.
The detail used to ask for the new version only, and a posting without one
answered 404 "no such job": the description, the link, the application and the
resume preparation were all unreachable until a recalculation the person had
not been told to run.
"""

from __future__ import annotations

import re

import pytest
from tests.integration.test_deep_metered import WORK
from tests.integration.test_score_replay import _put
from tests.integration.test_semantic_api import build

from career_agent.pipeline.rescore import rescore
from career_agent.runtime.mode import RuntimeMode
from career_agent.storage.db import connect, transaction
from career_agent.storage.records import CompanyRecord, SourceBoardRecord
from career_agent.storage.repositories import CompanyRepo, SourceBoardRepo


@pytest.fixture
def app(tmp_path):
    app = build(tmp_path, RuntimeMode.PERSONAL)
    config_dir = app.config.config_dir
    local = config_dir / "search.local.yaml"
    local.write_bytes((config_dir / "search.worked-example.yaml").read_bytes())
    conn = connect(app.config.db_path)
    with transaction(conn):
        company = CompanyRepo(conn).upsert(CompanyRecord(slug="acme", name="Acme"))
        board = SourceBoardRepo(conn).upsert(
            SourceBoardRecord(company_id=company, provider="greenhouse", board_identifier="a")
        )
        for n in range(3):
            _put(
                conn,
                company,
                board,
                f"p-{n}",
                f"Systems Engineer {n}",
                f"{WORK} Team {n}.",
                "Remote",
            )
    # The default age limit hides undated postings; these are recent.
    conn.execute("UPDATE job SET posted_at = date('now', '-3 days')")
    conn.commit()
    rescore(conn, app.search_config())
    conn.close()
    return app


def bump(app) -> int:
    local = app.config.config_dir / "search.local.yaml"
    text = local.read_text(encoding="utf-8")
    version = int(re.search(r"^config_version:\s*(\d+)", text, re.M).group(1))
    local.write_text(
        re.sub(r"^config_version:\s*\d+", f"config_version: {version + 1}", text, flags=re.M),
        encoding="utf-8",
    )
    app._search_config = None  # the running app reloads after a save
    return version + 1


def call(app, method, path, body=None, query=None):
    return app.handle_api(method, path, query or {}, body or {})


def first_id(app) -> str:
    return call(app, "GET", "/api/jobs", query={"limit": ["5"], "include_unresolved": ["true"]})[
        "items"
    ][0]["job_id"]


def test_the_detail_opens_with_an_old_score_and_says_so(app):
    job_id = first_id(app)
    before = call(app, "GET", f"/api/jobs/{job_id}")
    assert before["assessment"]["state"] == "current"
    new_version = bump(app)
    # The list still serves the previous answer; the detail used to answer
    # 404 "no such job" for a posting that was on screen a moment before.
    assert call(app, "GET", "/api/jobs", query={"limit": ["5"], "include_unresolved": ["true"]})[
        "items"
    ]
    detail = call(app, "GET", f"/api/jobs/{job_id}")
    assert detail["description"] and detail["title"] == before["title"]
    assert detail["assessment"]["state"] == "stale"
    assert detail["assessment"]["reasons"] == ["preferences"]
    assert detail["assessment"]["current_version"] == new_version
    assert detail["assessment"]["shown_version"] == new_version - 1
    assert detail["match_score"] == before["match_score"], "the older answer, labelled"
    assert detail["fit_feedback"] is None


def test_the_application_and_the_preparation_work_without_a_recalculation(app):
    job_id = first_id(app)
    bump(app)
    saved = call(app, "PATCH", f"/api/jobs/{job_id}/status", {"status": "SHORTLISTED"})
    assert saved["assessment"]["state"] == "stale" and saved["application_status"] == "SHORTLISTED"
    prep = call(app, "GET", f"/api/jobs/{job_id}/preparation")
    assert prep["assessment"]["state"] == "stale" and prep["total"] >= 0


def test_a_posting_never_scored_still_opens_and_is_not_given_a_score(app):
    conn = connect(app.config.db_path)
    with transaction(conn):
        company = conn.execute("SELECT id FROM company LIMIT 1").fetchone()[0]
        board = conn.execute("SELECT id FROM source_board LIMIT 1").fetchone()[0]
        _put(conn, company, board, "fresh", "Brand New Role", f"{WORK} New.", "Remote")
    fresh = conn.execute("SELECT id FROM job WHERE external_id = 'fresh'").fetchone()[0]
    conn.execute("DELETE FROM job_match WHERE job_id = ?", (fresh,))
    conn.commit()
    conn.close()
    detail = call(app, "GET", f"/api/jobs/{fresh}")
    assert detail["assessment"]["state"] == "missing"
    assert detail["scored"] is False and detail["match_score"] is None
    assert detail["description"] and detail["components"] == []
    prep = call(app, "GET", f"/api/jobs/{fresh}/preparation")
    assert prep["assessment"]["state"] == "missing" and prep["requirements"] == []


def test_a_changed_text_is_marked_out_of_date_even_under_the_same_preferences(app):
    job_id = first_id(app)
    conn = connect(app.config.db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # only the text moved, not its stored copy
    with transaction(conn):
        conn.execute(
            "UPDATE job SET content_hash = 'changed-after-scoring' WHERE id = ?", (job_id,)
        )
    conn.close()
    detail = call(app, "GET", f"/api/jobs/{job_id}")
    assert detail["assessment"]["state"] == "stale"
    assert detail["assessment"]["reasons"] == ["posting"]

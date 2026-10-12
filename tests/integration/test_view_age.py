# ruff: noqa: F811
"""How old a posting may be and still appear: one limit per profile.

Synthetic corpus: postings published 10 days ago, 130 days ago, 200 days ago and
never dated, a role posted twice (one recent copy, one old), and old postings
that this machine collected again today.

* The limit is on the date the SOURCE published the posting. Collecting a
  posting again does not make it young.
* 120 days by default; 150 or no limit on request; kept per profile.
* Undated postings are hidden unless included, and the notice counts both.
* Saved and applied postings are never hidden by it.
* A grouped role is represented by a copy that passes the limit.
* The limit changes no match % and writes no score.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.integration.test_deep_api_provider import POSTINGS, api, call  # noqa: F401 - fixture
from tests.integration.test_local_profiles import app as app_of
from tests.integration.test_local_profiles import install  # noqa: F401 - fixture

from career_agent.storage.db import connect
from career_agent.web.server import ApiError

DEMO_FILE = Path(__file__).resolve().parents[2] / "evaluation" / "demo" / "demo_postings.yaml"


def days_ago(n: int) -> str:
    return (datetime.now(UTC) - timedelta(days=n)).date().isoformat() + "T00:00:00+00:00"


@pytest.fixture
def aged(api):  # noqa: F811
    """30 postings: 0-9 recent, 10-19 at 130 days, 20-24 at 200 days, 25-29 undated.
    Every old one was collected again today."""
    conn = connect(api.config.db_path)
    try:
        for n in range(POSTINGS):
            posted = (
                days_ago(10)
                if n < 10
                else days_ago(130)
                if n < 20
                else days_ago(200)
                if n < 25
                else None
            )
            conn.execute(
                "UPDATE job SET posted_at = ?, last_seen_at = ?, first_seen_at = ?"
                " WHERE external_id = ?",
                (posted, days_ago(0), days_ago(0), f"p-{n}"),
            )
    finally:
        conn.close()
    return api


def jobs(api, **query):
    flat = {k: [str(v)] for k, v in {"limit": 100, "include_unresolved": 1, **query}.items()}
    return api.handle_api("GET", "/api/jobs", flat, {})


def scores(api):
    conn = connect(api.config.db_path)
    try:
        return conn.execute("SELECT job_id, match_score FROM job_match ORDER BY job_id").fetchall()
    finally:
        conn.close()


def test_the_default_hides_postings_published_more_than_120_days_ago(aged) -> None:
    answer = jobs(aged)
    assert answer["view"] == {
        "max_age_days": 120,
        "include_undated": False,
        "choices": [120, 150, None],
        "applies": True,
    }
    assert answer["total"] == 10
    narrowings = aged.handle_api("GET", "/api/jobs/narrowings", {"include_unresolved": ["1"]}, {})
    assert narrowings["hidden_by_age"] == 15 and narrowings["hidden_undated"] == 5


def test_collecting_again_does_not_make_an_old_posting_young(aged) -> None:
    # Every old posting was seen again TODAY (see the fixture) and is still hidden.
    titles = {item["job_id"]: item["posted_at"] for item in jobs(aged)["items"]}
    assert len(titles) == 10 and all(
        value.startswith(days_ago(10)[:10]) for value in titles.values()
    )


def test_the_limit_can_be_150_days_or_none_and_undated_can_be_included(aged) -> None:
    assert call(aged, "PATCH", "/api/view-prefs", {"max_age_days": 150})["max_age_days"] == 150
    assert jobs(aged)["total"] == 20
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": 120, "include_undated": True})
    assert jobs(aged)["total"] == 15, "the 10 recent ones and the 5 with no date"
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": None})
    assert jobs(aged)["total"] == 30, "no limit: nothing is set aside for its age"
    with pytest.raises(ApiError) as refused:
        call(aged, "PATCH", "/api/view-prefs", {"max_age_days": 90})
    assert refused.value.status == 400


def test_the_limit_changes_no_score_and_writes_none(aged) -> None:
    before = scores(aged)
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": 150})
    jobs(aged)
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": None, "include_undated": True})
    assert scores(aged) == before


def test_a_saved_posting_stays_reachable_whatever_its_age(aged) -> None:
    conn = connect(aged.config.db_path)
    try:
        old = conn.execute("SELECT id FROM job WHERE external_id = 'p-22'").fetchone()[0]
    finally:
        conn.close()
    assert old not in {item["job_id"] for item in jobs(aged)["items"]}
    call(aged, "PATCH", f"/api/jobs/{old}/saved", {"saved": True})
    saved = jobs(aged, saved_only=1)
    assert old in {item["job_id"] for item in saved["items"]}
    # The posting still says how old it is.
    assert next(i for i in saved["items"] if i["job_id"] == old)["posted_at"].startswith(
        days_ago(200)[:10]
    )


def clone(conn, source_external: str, external: str, posted: str, score: int) -> str:
    """A second posting of the same role: same company and title, its own date and score."""
    job = dict(
        conn.execute("SELECT * FROM job WHERE external_id = ?", (source_external,)).fetchone()
    )
    new_id = "01MCLONE" + external.upper().replace("-", "")[:8].ljust(8, "0") + "0" * 10
    old_id = job["id"]
    job.update(
        id=new_id, external_id=external, posted_at=posted, url=f"https://example.test/{external}"
    )
    conn.execute(
        f"INSERT INTO job ({', '.join(job)}) VALUES ({', '.join('?' for _ in job)})",
        list(job.values()),
    )
    match = dict(conn.execute("SELECT * FROM job_match WHERE job_id = ?", (old_id,)).fetchone())
    match.update(id=new_id + "m", job_id=new_id, match_score=score)
    conn.execute(
        f"INSERT INTO job_match ({', '.join(match)}) VALUES ({', '.join('?' for _ in match)})",
        list(match.values()),
    )
    return new_id


def test_a_grouped_role_is_represented_by_a_copy_that_passes_the_limit(aged) -> None:
    conn = connect(aged.config.db_path)
    try:
        # The OLD copy scores highest, so without the limit it is the one shown.
        old = clone(conn, "p-0", "dup-old", days_ago(300), 99)
        new = clone(conn, "p-0", "dup-new", days_ago(5), 50)
    finally:
        conn.close()
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": None})
    everything = jobs(aged, group_duplicates=1)
    shown = {item["job_id"]: item for item in everything["items"]}
    assert old in shown and shown[old]["duplicate_count"] == 3

    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": 120})
    limited = {item["job_id"]: item for item in jobs(aged, group_duplicates=1)["items"]}
    assert old not in limited, "the 300-day-old copy is not presented as the role"
    role = (
        limited.get(new)
        or limited[next(i for i in limited if limited[i]["title"] == shown[old]["title"])]
    )
    assert role["duplicate_count"] == 2, "only the copies that pass the limit are counted"
    assert role["posted_at"] >= days_ago(120), "and the date shown is one of theirs"


def test_the_choice_belongs_to_the_profile_that_made_it(install) -> None:  # noqa: F811
    host = install
    first = app_of(host)
    first_id = call(first, "GET", "/api/profiles")["active"]["id"]
    created = call(first, "POST", "/api/profiles", {"label": "Other"})["created"]["id"]
    call(first, "PATCH", "/api/view-prefs", {"max_age_days": 150, "include_undated": True})
    call(first, "POST", "/api/profiles/switch", {"profile_id": created})
    second = app_of(host)
    theirs = call(second, "GET", "/api/view-prefs")
    assert theirs["max_age_days"] == 120 and theirs["include_undated"] is False
    call(second, "POST", "/api/profiles/switch", {"profile_id": first_id})
    assert call(app_of(host), "GET", "/api/view-prefs")["max_age_days"] == 150


def test_the_demo_corpus_meets_the_same_limit_with_relative_dates(tmp_path) -> None:
    """Nothing is exempted: the invented postings keep their AGE, so the limit
    behaves as in a real profile. Only the one invented undated posting is set
    aside, and it is counted."""
    from tests.integration.test_semantic_api import build

    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.demo_seed import seed_demo
    from career_agent.runtime.mode import RuntimeMode

    demo = build(tmp_path, RuntimeMode.DEMO)
    conn = connect(demo.config.db_path)
    try:
        config, _ = load_search_config(demo.config.config_dir)
        seed_demo(conn, config, source=DEMO_FILE)
    finally:
        conn.close()
    assert call(demo, "GET", "/api/view-prefs")["applies"] is True
    answer = jobs(demo, include_ineligible=True, include_off_target=True)
    assert answer["hidden_by_age"] == 0, "relative dates keep every dated demo posting young"
    assert answer["hidden_undated"] == 1


def test_a_posting_the_person_imported_is_never_hidden_for_having_no_date(aged) -> None:
    conn = connect(aged.config.db_path)
    try:
        conn.execute("UPDATE job SET provider = 'manual_import' WHERE external_id = 'p-27'")
        imported = conn.execute("SELECT id FROM job WHERE external_id = 'p-27'").fetchone()[0]
        other = conn.execute("SELECT id FROM job WHERE external_id = 'p-26'").fetchone()[0]
    finally:
        conn.close()
    answer = jobs(aged)
    shown = {item["job_id"]: item for item in answer["items"]}
    assert imported in shown and other not in shown
    assert shown[imported]["posted_at"] is None, "kept in view, never presented as recent"
    assert shown[imported]["provider"] == "manual_import"
    assert answer["hidden_undated"] == 4


def test_the_deep_selection_follows_the_same_limit_as_the_list(aged) -> None:
    """One criterion in two places: whatever Find jobs hides for age, the deep
    reading neither counts nor selects, and its preview names the limit."""
    call(aged, "POST", "/api/deep/provider", {"provider": "deepseek"})
    narrow = call(aged, "GET", "/api/deep")
    assert narrow["age_limit"] == {"days": 120, "include_undated": False}
    listed = jobs(aged)["total"]
    call(aged, "PATCH", "/api/view-prefs", {"max_age_days": None, "include_undated": True})
    wide = call(aged, "GET", "/api/deep")
    assert wide["age_limit"] == {"days": None, "include_undated": True}
    assert wide["available"] > narrow["available"]
    assert narrow["excluded_by_age"] == wide["available"] - narrow["available"]
    assert jobs(aged)["total"] > listed, "the list widened by the same choice"

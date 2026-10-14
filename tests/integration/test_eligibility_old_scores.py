"""Mattermost and ElevenLabs before and after the readers-12 recalculation.

The default search and the deep selection read the eligibility stored WITH
the score. A score written by readers-11 still says "eligible" for both
shapes until a recalculation re-reads them; these tests pin that dependency
(so it is never assumed away) and what holds once the posting is re-read:
out of both selections, the stored score unchanged, and still reachable from
Saved and the applications board. Synthetic adverts, no network, no model.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.integration.test_home_tasks import _app

from career_agent.semantic.deep_run import population_rows
from career_agent.storage.db import connect, transaction

PERKS = (
    "Why join us\n\n- Remote-first culture: Work from anywhere with a globally distributed team\n"
)
SECURITY = (
    "U.S. Eligibility & Compliance\n\n"
    "This role may require obtaining and maintaining a U.S. government security clearance. "
    "Applicants must meet eligibility requirements for access to export-controlled information "
    "as defined by U.S. export control laws, including EAR and ITAR.\n"
)
SHAPES = {
    # A role published in the United States whose only "worldwide" is a perk,
    # with a clearance it may require (the Mattermost shape).
    "security": {
        "title": "Senior Software Engineer",
        "company": "Example Collaboration Co (fictitious)",
        "location_raw": "United States",
        "description": "Build integrations and APIs for our platform with the operations team.\n\n"
        + PERKS
        + "\n"
        + SECURITY,
    },
    # Two explicit statements that disagree (the ElevenLabs shape).
    "conflict": {
        "title": "Solutions Engineer",
        "company": "Example Voice Co (fictitious)",
        "location_raw": "Colombia",
        "description": "This role is remote but the successful candidate should be based in "
        "Brazil.\n\nLocation\n\nThis role is remote-first, so it can be executed from anywhere "
        "in Colombia, with a preference of Bogota. Build integrations and APIs for our "
        "platform with the operations team.",
    },
    # A restriction that excludes the candidate, read only by readers-12
    # ("can be executed from anywhere in ..."): decisive, not to confirm.
    "elsewhere": {
        "title": "Integration Engineer",
        "company": "Example Logistics Co (fictitious)",
        "location_raw": "Mexico",
        "description": "Location\n\nThis role can be executed from anywhere in Mexico. "
        "Build integrations and APIs for our platform with the operations team.",
    },
    # The control: eligible under both readers.
    "control": {
        "title": "Integration Engineer",
        "company": "Example Payments Co (fictitious)",
        "location_raw": "Brazil",
        "description": "Candidates must be based in Brazil. Build integrations and APIs "
        "for our payments platform with the operations team.",
    },
}


@pytest.fixture
def app(tmp_path: Path):
    # The committed configuration: a candidate in Brazil, plus the demo corpus.
    api = _app(tmp_path / "old-scores")
    ids = {}
    for key, body in SHAPES.items():
        ids[key] = api.handle_api("POST", "/api/import", {}, body)["job_id"]
    api.ids = ids
    return api


def default_ids(app) -> set[str]:
    return {
        i["job_id"] for i in app.handle_api("GET", "/api/jobs", {"limit": ["200"]}, {})["items"]
    }


def deep_ids(app) -> set[str]:
    """The deep selection's default population (`include_unresolved` off)."""
    conn = connect(app.config.db_path)
    try:
        rows = population_rows(conn, app.search_config())
    finally:
        conn.close()
    return {
        r["id"] for r in rows if r["eligibility_status"] in {"VERIFIED_ELIGIBLE", "LIKELY_ELIGIBLE"}
    }


def stored(app, job_id: str) -> tuple[str, int]:
    conn = connect(app.config.db_path)
    try:
        row = conn.execute(
            "SELECT eligibility_status, match_score FROM job_match WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    return row["eligibility_status"], row["match_score"]


def as_readers_11(app, *job_ids: str) -> None:
    """What a score written before readers-12 holds for these shapes: eligible,
    under an older reading identity. The score itself is not touched."""
    conn = connect(app.config.db_path)
    try:
        with transaction(conn):
            conn.executemany(
                "UPDATE job_match SET eligibility_status = 'VERIFIED_ELIGIBLE',"
                " result_json = json_set(result_json, '$.eligibility_status', 'VERIFIED_ELIGIBLE'),"
                " input_digest = 'readers-11-synthetic' WHERE job_id = ?",
                [(j,) for j in job_ids],
            )
    finally:
        conn.close()


def test_re_read_by_readers_12_both_leave_the_default_search_and_the_deep_selection(app) -> None:
    security, conflict, elsewhere, control = (
        app.ids[k] for k in ("security", "conflict", "elsewhere", "control")
    )
    assert stored(app, security)[0] == "UNRESOLVED"
    assert stored(app, conflict)[0] == "UNRESOLVED"
    assert stored(app, elsewhere)[0] == "VERIFIED_NOT_ELIGIBLE"
    assert stored(app, control)[0] == "VERIFIED_ELIGIBLE"
    shown, deep = default_ids(app), deep_ids(app)
    assert control in shown and control in deep, "the control proves the selections are not empty"
    for job_id in (security, conflict, elsewhere):
        assert job_id not in shown
        assert job_id not in deep


def test_the_stored_score_is_the_same_under_either_reading(app) -> None:
    """Eligibility is a separate measurement: re-reading it moves no score."""
    for key in ("security", "conflict", "elsewhere"):
        job_id = app.ids[key]
        _, score_12 = stored(app, job_id)
        as_readers_11(app, job_id)
        _, score_11 = stored(app, job_id)
        assert score_11 == score_12


def test_saved_and_applied_stay_reachable_after_the_re_read(app) -> None:
    security, conflict, elsewhere = (app.ids[k] for k in ("security", "conflict", "elsewhere"))
    app.handle_api("PATCH", f"/api/jobs/{security}/saved", {}, {"saved": True})
    app.handle_api("PATCH", f"/api/jobs/{elsewhere}/saved", {}, {"saved": True})
    app.handle_api("PATCH", f"/api/jobs/{conflict}/status", {}, {"status": "APPLIED"})
    saved = app.handle_api("GET", "/api/jobs", {"saved_only": ["true"]}, {})["items"]
    applied = app.handle_api("GET", "/api/jobs", {"status": ["APPLIED"]}, {})["items"]
    assert {security, elsewhere} <= {i["job_id"] for i in saved}
    assert conflict in {i["job_id"] for i in applied}
    assert security not in default_ids(app) and conflict not in default_ids(app)


def test_until_recalculated_a_readers_11_score_keeps_them_in_both_selections(app) -> None:
    """THE DEPENDENCY, pinned. The selections read the stored eligibility, so a
    readers-11 score keeps these shapes "eligible" until the readers-12
    recalculation (or a guard, not built) re-reads them. When a guard exists,
    this test is the one to change."""
    old = [app.ids[k] for k in ("security", "conflict", "elsewhere")]
    as_readers_11(app, *old)
    shown, deep = default_ids(app), deep_ids(app)
    for job_id in old:
        assert job_id in shown and job_id in deep

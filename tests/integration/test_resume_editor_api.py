"""The editor's routes: create, autosave, version points, findings (PR 4).

Synthetic documents in temporary profile databases. Nothing here writes
Career Evidence, the candidate row or a score, and a test proves it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support import committed_config_dir
from tests.support_resume import confirm_cited, long, rich, sparse

from career_agent.clock import new_id
from career_agent.resume_doc.check import findings
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.workspace_repo import ensure_candidate
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig


def make_api(tmp_path: Path, name: str = "p") -> JobsApi:
    db = tmp_path / name / "personal.db"
    conn = connect(db)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic")
        candidate = ensure_candidate(conn)
        conn.execute("UPDATE candidate SET display_name = 'You' WHERE id = ?", (candidate,))
        confirm_cited(conn, rich(), sparse(), long())
    conn.close()
    return JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    return make_api(tmp_path)


def call(api: JobsApi, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    return api.handle_api(method, f"/api/resume{path}", {}, body or {})


def test_a_blank_resume_has_no_name_until_one_is_typed(api: JobsApi) -> None:
    made = call(api, "POST", "/documents", {"title": "Mine"})
    assert made["kind"] == "SCRATCH" and made["document"]["identity"]["full_name"] == ""
    rendered = call(api, "POST", "/render", {"document": made["document"]})
    assert "NAME_MISSING" in {f["kind"] for f in rendered["findings"]}


def test_autosave_writes_the_working_copy_and_refuses_a_stale_one(api: JobsApi) -> None:
    made = call(api, "POST", "/documents", {"title": "Mine"})
    doc = {**made["document"], "title": "Renamed"}
    saved = call(
        api,
        "PATCH",
        f"/documents/{made['id']}/working",
        {"document": doc, "expected_sha256": made["sha256"]},
    )
    assert saved["sha256"] != made["sha256"]
    assert call(api, "GET", f"/documents/{made['id']}")["title"] == "Renamed"
    older = {**doc, "title": "From an older window"}
    with pytest.raises(ApiError) as stale:
        call(
            api,
            "PATCH",
            f"/documents/{made['id']}/working",
            {"document": older, "expected_sha256": made["sha256"]},
        )
    assert stale.value.status == 409
    bad = {**doc, "identity": {**doc["identity"], "email": "not an email"}}
    with pytest.raises(ApiError) as invalid:
        call(
            api,
            "PATCH",
            f"/documents/{made['id']}/working",
            {"document": bad, "expected_sha256": saved["sha256"]},
        )
    assert invalid.value.status == 400
    other_kind = {**doc, "kind": "MASTER"}
    with pytest.raises(ApiError) as changed:
        call(
            api,
            "PATCH",
            f"/documents/{made['id']}/working",
            {"document": other_kind, "expected_sha256": saved["sha256"]},
        )
    assert changed.value.status == 400


def test_a_retried_save_of_the_same_copy_is_not_a_conflict(api: JobsApi) -> None:
    """The first answer was lost: the same copy again, with the old hash, is
    already saved, never "changed in another window"."""
    made = call(api, "POST", "/documents", {"title": "Mine"})
    body = {"document": {**made["document"], "title": "Once"}, "expected_sha256": made["sha256"]}
    first = call(api, "PATCH", f"/documents/{made['id']}/working", body)
    assert call(api, "PATCH", f"/documents/{made['id']}/working", body) == first


def test_version_points_are_asked_for_and_typing_writes_none(api: JobsApi) -> None:
    made = call(api, "POST", "/documents", {"title": "Mine"})
    sha = made["sha256"]
    for n in range(5):
        doc = {**made["document"], "title": f"Typing {n}"}
        sha = call(
            api,
            "PATCH",
            f"/documents/{made['id']}/working",
            {"document": doc, "expected_sha256": sha},
        )["sha256"]
    with connect(api.config.db_path) as conn:
        assert len(ResumeStore(conn).list_revisions(made["id"])) == 1, "no revision per keystroke"
    assert (
        call(api, "POST", f"/documents/{made['id']}/checkpoint", {"reason": "TEMPLATE_CHANGED"})[
            "revision"
        ]
        == 2
    )
    with pytest.raises(ApiError):
        call(api, "POST", f"/documents/{made['id']}/checkpoint", {"reason": "GENERATED"})


def test_a_copy_is_a_new_draft_with_the_same_words(api: JobsApi) -> None:
    source = rich().model_dump(mode="json")
    copy = call(api, "POST", "/documents", {"from": source})
    assert copy["id"] != source["id"] and copy["kind"] == "SCRATCH"
    assert copy["document"]["provenance"]["created_from"] == "DUPLICATE"
    assert copy["document"]["experience"] == source["experience"]


def test_editing_writes_nothing_but_the_resume(api: JobsApi) -> None:
    made = call(api, "POST", "/documents", {"from": rich().model_dump(mode="json")})

    def snapshot() -> list[Any]:
        with connect(api.config.db_path) as conn:
            return [
                conn.execute(f"SELECT * FROM {table}").fetchall()
                for table in ("verified_claim", "candidate", "job_match", "search_profile_version")
            ]

    before = snapshot()
    doc = made["document"]
    doc["experience"][0]["bullets"][0]["text"] = "Reworded by the person, with 99 teams."
    call(
        api,
        "PATCH",
        f"/documents/{made['id']}/working",
        {"document": doc, "expected_sha256": made["sha256"]},
    )
    call(api, "POST", f"/documents/{made['id']}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    assert [list(map(tuple, rows)) for rows in snapshot()] == [
        list(map(tuple, rows)) for rows in before
    ]


def test_each_profile_sees_only_its_own_resumes(tmp_path: Path) -> None:
    a, b = make_api(tmp_path, "a"), make_api(tmp_path, "b")
    made = call(a, "POST", "/documents", {"title": "A only"})
    assert call(b, "GET", "/documents") == {"master": None, "others": [], "jobs": []}
    with pytest.raises(ApiError) as missing:
        call(b, "GET", f"/documents/{made['id']}")
    assert missing.value.status == 404


def test_findings_are_named_facts_never_a_score() -> None:
    doc = rich().model_dump(mode="json")
    first = doc["experience"][0]["bullets"][0]
    first.update(override="EDITED", original_text=first["text"], text="Joined 77 teams together.")
    second = doc["experience"][0]["bullets"][1]
    second.update(
        override="EDITED", original_text=second["text"], text=second["text"] + " Proudly."
    )
    doc["experience"][1]["bullets"].append(
        {"id": new_id(), "text": "x" * 320, "origin": "USER_AUTHORED"}
    )
    doc["identity"]["email"] = None
    found = {(f["kind"], f["ref"].split("/")[-1]) for f in findings(upgrade_resume_document(doc))}
    kinds = {k for k, _ in found}
    assert ("NUMBER_NOT_IN_EVIDENCE", first["id"]) in found
    assert ("EDITED_EVIDENCE", second["id"]) in found
    assert {"LONG_BULLET", "NOT_FROM_EVIDENCE", "EMAIL_MISSING"} <= kinds
    assert all(
        set(f) == {"key", "kind", "ref", "category", "severity", "nature", "action"}
        for f in findings(upgrade_resume_document(doc))
    )
    assert findings(sparse()) and not any("score" in f["kind"].lower() for f in findings(sparse()))

"""Resume Workspace V2 PR 7: My resumes, versions, and the old helper's resumes.

Synthetic profiles, documents and job ads only (the demo corpus for jobs, the
PR 2 synthetic Resume helper workspace for the move). Nothing here reads or
migrates anybody's real resumes.
"""

from __future__ import annotations

import json
import shutil
import time
import types
from pathlib import Path
from typing import Any

import pytest
from tests.integration.test_resume_editor_api import call, make_api
from tests.integration.test_resume_evidence_trust import _retire
from tests.integration.test_resume_legacy import LABEL, legacy, profile  # noqa: F401
from tests.support import committed_config_dir
from tests.support_resume import rich, sparse

from career_agent.clock import new_id
from career_agent.config.search_config import load_search_config
from career_agent.pipeline.demo_seed import seed_demo
from career_agent.resume_doc.legacy import _manifest
from career_agent.resume_doc.models import upgrade_resume_document
from career_agent.resume_doc.store import ResumeStore, resume_row_counts
from career_agent.storage.db import connect, transaction
from career_agent.web.api import JobsApi
from career_agent.web.server import ApiError, ServerConfig

REPO = Path(__file__).resolve().parents[2]
DEMO = REPO / "evaluation" / "demo" / "demo_postings.yaml"


@pytest.fixture
def api(tmp_path: Path) -> JobsApi:
    api = make_api(tmp_path, "a")
    with connect(api.config.db_path) as conn:
        seed_demo(conn, load_search_config(committed_config_dir())[0], source=DEMO)
    return api


def jobs(api: JobsApi) -> list[str]:
    with connect(api.config.db_path) as conn:
        return [str(r[0]) for r in conn.execute("SELECT id FROM job ORDER BY id")]


def store_doc(api: JobsApi, doc: Any) -> dict[str, Any]:
    with connect(api.config.db_path) as conn:
        ResumeStore(conn).create_document(doc)
    return call(api, "GET", f"/documents/{doc.id}")


def master(api: JobsApi) -> dict[str, Any]:
    return store_doc(api, rich())


def imported(api: JobsApi, title: str = "Consulting CV") -> dict[str, Any]:
    data = sparse().model_dump(mode="json")
    data.update(id=new_id(), kind="IMPORTED", title=title, provenance={"created_from": "IMPORT"})
    return store_doc(api, upgrade_resume_document(data))


def library(api: JobsApi, archived: bool = False) -> dict[str, Any]:
    return call_q(api, "GET", "/documents", {"archived": ["1"]} if archived else {})


def call_q(api: JobsApi, method: str, path: str, query: dict[str, list[str]]) -> Any:
    return api.handle_api(method, f"/api/resume{path}", query, {})


def manage(api: JobsApi, doc_id: str, action: str, **extra: Any) -> dict[str, Any]:
    return call(api, "PATCH", f"/documents/{doc_id}", {"action": action, **extra})


def active_masters(api: JobsApi) -> int:
    with connect(api.config.db_path) as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM resume_document WHERE kind = 'MASTER' AND archived_at IS NULL"
        ).fetchone()[0]


def revisions(api: JobsApi, doc_id: str) -> list[tuple[int, str, str]]:
    with connect(api.config.db_path) as conn:
        return [
            (r[0], r[1], r[2])
            for r in conn.execute(
                "SELECT seq, reason, content_sha256 FROM resume_revision WHERE document_id = ?"
                " ORDER BY seq",
                (doc_id,),
            )
        ]


# ----------------------------------------------------------------- the list


def test_my_resumes_groups_the_master_standalone_and_each_jobs_versions(api: JobsApi) -> None:
    mine = master(api)
    other = imported(api)
    job = jobs(api)[0]
    v1 = call(api, "POST", f"/jobs/{job}/versions")
    v2 = call(api, "POST", f"/jobs/{job}/versions")
    listed = library(api)
    assert listed["master"]["id"] == mine["id"]
    assert [d["id"] for d in listed["others"]] == [other["id"]]
    (group,) = listed["jobs"]
    assert group["job_id"] == job and group["title"] and group["key"] == f"job:{job}"
    assert [(v["id"], v["version_number"]) for v in group["versions"]] == [
        (v2["id"], 2),
        (v1["id"], 1),
    ]
    # A list is summaries: no document body crosses, no path, no internal id.
    text = json.dumps(listed)
    assert '"document"' not in text and "working" not in text and "sha256" not in text


def test_the_list_reads_no_document_and_no_export_one_by_one(
    api: JobsApi, monkeypatch: pytest.MonkeyPatch
) -> None:
    master(api)
    for n in range(5):
        imported(api, f"CV {n}")

    def refuse(*_: Any, **__: Any) -> None:
        raise AssertionError("the list loaded one document or export at a time")

    monkeypatch.setattr(ResumeStore, "get_document", refuse)
    monkeypatch.setattr(ResumeStore, "list_exports", refuse)
    monkeypatch.setattr(ResumeStore, "list_documents", refuse)
    assert len(library(api)["others"]) == 5


@pytest.mark.parametrize("count", [1, 20, 100])
def test_the_list_stays_fast_with_many_resumes(api: JobsApi, count: int) -> None:
    master(api)
    job = jobs(api)[0]
    for n in range(count - 1):
        if n % 3 == 0:
            call(api, "POST", f"/jobs/{job}/versions")
        else:
            imported(api, f"CV {n}")
    started = time.perf_counter()
    listed = library(api)
    elapsed = time.perf_counter() - started
    total = 1 + len(listed["others"]) + sum(len(g["versions"]) for g in listed["jobs"])
    assert total == count
    print(f"\nMY RESUMES {count} documents: {elapsed * 1000:.1f} ms, one request")
    assert elapsed < 2.0


# ---------------------------------------------------------- name, copy, archive


def test_renaming_changes_the_name_only(api: JobsApi) -> None:
    master(api)
    job = jobs(api)[0]
    made = call(api, "POST", f"/jobs/{job}/versions")
    before = made["document"]
    out = manage(api, made["id"], "rename", title="  V1 -  shorter ")
    assert out["title"] == "V1 - shorter"
    after = call(api, "GET", f"/documents/{made['id']}")["document"]
    assert after["target"] == before["target"]
    assert after["experience"] == before["experience"]
    assert {**after, "title": before["title"]} == before
    with pytest.raises(ApiError) as empty:
        manage(api, made["id"], "rename", title="   ")
    assert empty.value.status == 400


def test_a_duplicate_is_a_new_document_with_its_own_first_revision(api: JobsApi) -> None:
    source = imported(api)
    copy = call(api, "POST", f"/documents/{source['id']}/copy", {"title": "Consulting CV (copy)"})
    assert copy["id"] != source["id"] and copy["kind"] == "IMPORTED"
    assert copy["title"] == "Consulting CV (copy)"
    assert [r[1] for r in revisions(api, copy["id"])] == ["CREATED"]
    assert copy["document"]["experience"] == source["document"]["experience"]
    assert copy["document"]["provenance"]["created_from"] == "DUPLICATE"
    with connect(api.config.db_path) as conn:
        ids = {
            r[0]
            for r in conn.execute(
                "SELECT id FROM resume_revision WHERE document_id IN (?, ?)",
                (source["id"], copy["id"]),
            )
        }
    assert len(ids) == 2  # never a reused revision id
    # A copy of the Master is a draft: there is one Master.
    mine = master(api)
    assert call(api, "POST", f"/documents/{mine['id']}/copy", {})["kind"] == "SCRATCH"
    assert active_masters(api) == 1


def test_archiving_hides_and_keeps_everything_and_comes_back(api: JobsApi) -> None:
    source = imported(api)
    with connect(api.config.db_path) as conn:
        before = resume_row_counts(conn)
    manage(api, source["id"], "archive")
    assert library(api)["others"] == []
    assert [d["id"] for d in library(api, archived=True)["others"]] == [source["id"]]
    with connect(api.config.db_path) as conn:
        assert resume_row_counts(conn) == before
    manage(api, source["id"], "unarchive")
    assert [d["id"] for d in library(api)["others"]] == [source["id"]]


def test_the_current_master_is_never_archived_from_the_list(api: JobsApi) -> None:
    mine = master(api)
    with pytest.raises(ApiError) as kept:
        manage(api, mine["id"], "archive")
    assert kept.value.status == 409 and kept.value.code == "master_kept"
    assert active_masters(api) == 1


# ------------------------------------------------------------------ Master


def test_make_master_archives_the_old_one_and_keeps_both_histories(api: JobsApi) -> None:
    old = master(api)
    source = imported(api)
    old_history, source_history = revisions(api, old["id"]), revisions(api, source["id"])
    made = manage(api, source["id"], "make_master")
    assert made["kind"] == "MASTER" and made["id"] not in (old["id"], source["id"])
    assert active_masters(api) == 1
    archived = {d["id"] for d in library(api, archived=True)["others"]}
    assert archived == {old["id"], source["id"]}
    assert revisions(api, old["id"]) == old_history
    assert revisions(api, source["id"]) == source_history
    new = call(api, "GET", f"/documents/{made['id']}")["document"]
    assert new["experience"] == source["document"]["experience"]
    # An old Master does not come back beside the current one...
    with pytest.raises(ApiError) as twice:
        manage(api, old["id"], "unarchive")
    assert twice.value.code == "master_exists"
    # ...it comes back by being made the Master again.
    manage(api, old["id"], "make_master")
    assert library(api)["master"]["id"] == old["id"] and active_masters(api) == 1


def test_make_master_is_all_or_nothing(api: JobsApi, monkeypatch: pytest.MonkeyPatch) -> None:
    old = master(api)
    source = imported(api)

    def fail(*_: Any, **__: Any) -> None:
        raise RuntimeError("disk full")

    monkeypatch.setattr(ResumeStore, "create_document", fail)
    with pytest.raises(RuntimeError):
        manage(api, source["id"], "make_master")
    monkeypatch.undo()
    listed = library(api)
    assert listed["master"]["id"] == old["id"]
    assert [d["id"] for d in listed["others"]] == [source["id"]]
    assert library(api, archived=True)["others"] == []


def test_a_version_for_a_job_is_never_made_the_master(api: JobsApi) -> None:
    master(api)
    made = call(api, "POST", f"/jobs/{jobs(api)[0]}/versions")
    with pytest.raises(ApiError) as refused:
        manage(api, made["id"], "make_master")
    assert refused.value.status == 409 and active_masters(api) == 1


# --------------------------------------------------------- job versions


def test_a_version_for_a_job_copies_the_exact_master_revision(api: JobsApi) -> None:
    mine = master(api)
    job = jobs(api)[0]
    made = call(api, "POST", f"/jobs/{job}/versions")
    doc = made["document"]
    assert made["kind"] == "TAILORED" and made["version_number"] == 1
    (latest,) = revisions(api, mine["id"])[-1:]
    with connect(api.config.db_path) as conn:
        rev = conn.execute(
            "SELECT id, content_json FROM resume_revision WHERE document_id = ? AND seq = ?",
            (mine["id"], latest[0]),
        ).fetchone()
        snap = conn.execute(
            "SELECT job_id, title FROM jd_snapshot WHERE id = ?",
            (doc["target"]["jd_snapshot_id"],),
        ).fetchone()
        job_title = conn.execute("SELECT title FROM job WHERE id = ?", (job,)).fetchone()[0]
    assert doc["provenance"] == {
        "created_from": "MASTER_COPY",
        "master_document_id": mine["id"],
        "master_revision_id": rev[0],
        "import_id": None,
        "tailoring_run_id": None,
    }
    assert (snap[0], snap[1]) == (job, job_title)
    # Nothing rewritten, chosen or added: every section is the Master's.
    source = json.loads(rev[1])
    for key in ("identity", "headline", "summary", "experience", "skills", "education"):
        assert doc[key] == source[key], key
    with connect(api.config.db_path) as conn, pytest.raises(Exception, match="immutable"):
        conn.execute("UPDATE jd_snapshot SET title = 'x'")


def test_more_versions_number_on_without_collision(api: JobsApi) -> None:
    master(api)
    job, other = jobs(api)[:2]
    v1 = call(api, "POST", f"/jobs/{job}/versions")
    v2 = call(api, "POST", f"/jobs/{job}/versions")
    v3 = call(api, "POST", f"/jobs/{job}/versions", {"from": v1["id"]})
    w1 = call(api, "POST", f"/jobs/{other}/versions")
    assert [v["version_number"] for v in (v1, v2, v3, w1)] == [1, 2, 3, 1]
    assert v3["document"]["experience"] == v1["document"]["experience"]
    with pytest.raises(ApiError):
        call(api, "POST", f"/jobs/{other}/versions", {"from": v1["id"]})


def test_a_job_version_needs_a_master_and_a_real_job(api: JobsApi) -> None:
    with pytest.raises(ApiError) as none:
        call(api, "POST", f"/jobs/{jobs(api)[0]}/versions")
    assert none.value.code == "no_master"
    master(api)
    with pytest.raises(ApiError) as unknown:
        call(api, "POST", "/jobs/no-such-job/versions")
    assert unknown.value.status == 404


def test_one_preferred_version_per_job(api: JobsApi) -> None:
    master(api)
    job = jobs(api)[0]
    v1 = call(api, "POST", f"/jobs/{job}/versions")
    v2 = call(api, "POST", f"/jobs/{job}/versions")
    manage(api, v1["id"], "prefer")
    manage(api, v2["id"], "prefer")
    versions = call(api, "GET", f"/jobs/{job}")["versions"]
    assert [(v["version_number"], v["preferred"]) for v in versions] == [(2, True), (1, False)]
    manage(api, v2["id"], "archive")
    assert not any(v["preferred"] for v in library(api, archived=True)["jobs"][0]["versions"])
    with pytest.raises(ApiError):
        manage(api, v2["id"], "prefer")  # an archived version is not preferred


def test_marking_the_resume_used_for_an_application(api: JobsApi) -> None:
    master(api)
    job = jobs(api)[0]
    v1 = call(api, "POST", f"/jobs/{job}/versions")
    v2 = call(api, "POST", f"/jobs/{job}/versions")
    assert call(api, "GET", f"/jobs/{job}")["used"] is None  # never inferred
    call(api, "POST", f"/jobs/{job}/used", {"document_id": v1["id"]})
    assert call(api, "GET", f"/jobs/{job}")["used"]["document_id"] == v1["id"]
    call(api, "POST", f"/jobs/{job}/used", {"document_id": v2["id"]})
    used = call(api, "GET", f"/jobs/{job}")["used"]
    assert used["document_id"] == v2["id"] and used["revision_seq"] == 1
    manage(api, v2["id"], "archive")  # an archived resume is still the one sent
    assert call(api, "GET", f"/jobs/{job}")["used"]["document_id"] == v2["id"]
    call(api, "POST", f"/jobs/{job}/used", {"document_id": None})
    assert call(api, "GET", f"/jobs/{job}")["used"] is None


# --------------------------------------------------------- history, restore


def test_restoring_writes_a_new_revision_and_rewrites_none(api: JobsApi) -> None:
    source = imported(api)
    doc = source["document"]
    edited = {**doc, "title": "Edited"}
    saved = call(
        api,
        "PATCH",
        f"/documents/{source['id']}/working",
        {"document": edited, "expected_sha256": source["sha256"]},
    )
    call(api, "POST", f"/documents/{source['id']}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    before = revisions(api, source["id"])
    log = call(api, "GET", f"/documents/{source['id']}/history")
    assert [r["reason"] for r in log["revisions"]] == ["MANUAL_CHECKPOINT", "CREATED"]
    assert log["revisions"][0]["current"] and log["sha256"] == saved["sha256"]
    first = log["revisions"][-1]["id"]
    out = call(
        api,
        "POST",
        f"/documents/{source['id']}/restore",
        {"revision_id": first, "expected_sha256": saved["sha256"]},
    )
    assert out["title"] == "Consulting CV" and out["unconfirmed"] == []
    after = revisions(api, source["id"])
    assert after[: len(before)] == before  # history untouched
    assert after[-1][1] == "RESTORED" and after[-1][2] == before[0][2]
    with pytest.raises(ApiError) as stale:
        call(
            api,
            "POST",
            f"/documents/{source['id']}/restore",
            {"revision_id": first, "expected_sha256": saved["sha256"]},
        )
    assert stale.value.status == 409


def test_restored_evidence_that_was_retired_is_not_trusted_again(api: JobsApi) -> None:
    mine = master(api)
    doc = mine["document"]
    key = doc["experience"][0]["bullets"][0]["evidence_ids"][0]
    first, *rest = doc["experience"]
    edited = {**doc, "experience": [{**first, "bullets": []}, *rest]}
    saved = call(
        api,
        "PATCH",
        f"/documents/{mine['id']}/working",
        {"document": edited, "expected_sha256": mine["sha256"]},
    )
    with connect(api.config.db_path) as conn:
        _retire(conn, key)
    first = call(api, "GET", f"/documents/{mine['id']}/history")["revisions"][-1]["id"]
    out = call(
        api,
        "POST",
        f"/documents/{mine['id']}/restore",
        {"revision_id": first, "expected_sha256": saved["sha256"]},
    )
    assert doc["experience"][0]["bullets"][0]["id"] in out["unconfirmed"]
    with pytest.raises(ApiError) as refused:
        call(api, "POST", f"/documents/{mine['id']}/checkpoint", {"reason": "MANUAL_CHECKPOINT"})
    assert refused.value.code == "evidence_not_confirmed"


# ----------------------------------------------------------------- compare


def test_compare_names_the_differences_and_changes_nothing(api: JobsApi) -> None:
    master(api)
    job = jobs(api)[0]
    v1 = call(api, "POST", f"/jobs/{job}/versions")
    v2 = call(api, "POST", f"/jobs/{job}/versions")
    doc = v2["document"]
    role = doc["experience"][0]
    bullets = [
        {
            **role["bullets"][0],
            "text": "Shorter line.",
            "origin": "USER_AUTHORED",
            "evidence_ids": [],
            "override": "NONE",
            "original_text": None,
        },
        {**role["bullets"][1], "hidden": True},
        *role["bullets"][2:],
    ]
    group = doc["skills"][0]
    new_skill = {"id": new_id(), "label": "Negotiation", "origin": "USER_AUTHORED"}
    doc = {
        **doc,
        "headline": {**doc["headline"], "text": "A different headline"},
        "experience": [
            {**role, "display_title": "Lead", "bullets": bullets},
            *doc["experience"][1:],
        ],
        "skills": [
            {**group, "items": [*group["items"], new_skill]},
            *doc["skills"][1:],
        ],
        "layout": {**doc["layout"], "hidden_sections": ["summary"]},
    }
    call(
        api,
        "PATCH",
        f"/documents/{v2['id']}/working",
        {"document": doc, "expected_sha256": v2["sha256"]},
    )
    with connect(api.config.db_path) as conn:
        before = (
            resume_row_counts(conn),
            conn.execute(
                "SELECT id, working_sha256, preferred FROM resume_document ORDER BY id"
            ).fetchall(),
        )
    out = call_q(api, "GET", "/compare", {"a": [v1["id"]], "b": [v2["id"]]})
    seen = {(c["area"], c["change"]) for c in out["changes"]}
    assert {
        ("headline", "CHANGED"),
        ("title", "CHANGED"),
        ("line", "CHANGED"),
        ("line", "HIDDEN"),
        ("skill", "ADDED"),
        ("section", "HIDDEN"),
    } <= seen
    changed = next(c for c in out["changes"] if c["area"] == "line" and c["change"] == "CHANGED")
    assert changed["after"] == "Shorter line." and changed["where"] == "Lead"
    with connect(api.config.db_path) as conn:
        after = (
            resume_row_counts(conn),
            conn.execute(
                "SELECT id, working_sha256, preferred FROM resume_document ORDER BY id"
            ).fetchall(),
        )
    assert [tuple(r) for r in after[1]] == [tuple(r) for r in before[1]]
    assert after[0] == before[0]


def test_only_versions_of_one_job_are_compared(api: JobsApi) -> None:
    master(api)
    a = call(api, "POST", f"/jobs/{jobs(api)[0]}/versions")
    b = call(api, "POST", f"/jobs/{jobs(api)[1]}/versions")
    with pytest.raises(ApiError) as other_job:
        call_q(api, "GET", "/compare", {"a": [a["id"]], "b": [b["id"]]})
    assert other_job.value.status == 400


# ------------------------------------------------------------ exports


def test_export_history_says_when_a_file_is_gone(api: JobsApi) -> None:
    from tests.support_resume import exportable

    with connect(api.config.db_path) as conn:
        from tests.support_resume import confirm_cited

        with transaction(conn):
            confirm_cited(conn, exportable())
        ResumeStore(conn).create_document(exportable())
    made = call(api, "GET", f"/documents/{exportable().id}")
    out = call(
        api,
        "POST",
        f"/documents/{made['id']}/exports",
        {"format": "JSON", "expected_sha256": made["sha256"]},
    )
    (listed,) = call(api, "GET", f"/documents/{made['id']}/exports")
    assert listed["available"] is True and "file_path" not in listed
    with connect(api.config.db_path) as conn:
        from career_agent.resume_doc.export import stored_file

        stored_file(conn, ResumeStore(conn).get_export(out["id"])).unlink()
    (gone,) = call(api, "GET", f"/documents/{made['id']}/exports")
    assert gone["available"] is False
    assert library(api)["master"]["last_export"]["format"] == "JSON"


# ---------------------------------------------------------- profile walls


def test_another_profile_sees_none_of_it(api: JobsApi, tmp_path: Path) -> None:
    mine = master(api)
    v1 = call(api, "POST", f"/jobs/{jobs(api)[0]}/versions")
    other = make_api(tmp_path, "b")
    assert library(other) == {"master": None, "others": [], "jobs": []}
    for path in (f"/documents/{mine['id']}", f"/documents/{v1['id']}/history"):
        with pytest.raises(ApiError) as hidden:
            call(other, "GET", path)
        assert hidden.value.status == 404
    with pytest.raises(ApiError):
        call_q(other, "GET", "/compare", {"a": [mine["id"]], "b": [v1["id"]]})
    with pytest.raises(ApiError):
        manage(other, mine["id"], "archive")


# ------------------------------------------------- the old helper's resumes

PID = "prof-01syntheticmoveaaaaaaaaaa"


@pytest.fixture
def moving(tmp_path: Path, legacy: Path) -> tuple[JobsApi, Path]:  # noqa: F811
    """A profile with the synthetic old workspace where the engine keeps it."""
    conn = profile(tmp_path)
    db = Path(conn.execute("PRAGMA database_list").fetchone()[2])
    conn.close()
    root = tmp_path / "install"
    workspace = root / "tailor" / "candidates" / PID
    shutil.copytree(legacy, workspace)
    api = JobsApi(ServerConfig(db_path=db, config_dir=committed_config_dir(), port=0), quiet=True)
    active = types.SimpleNamespace(id=PID, label=LABEL, tailor_home="tailor")
    api.profile_host = types.SimpleNamespace(root=root, active=active)  # type: ignore[attr-defined]
    return api, workspace


def test_without_old_resumes_there_is_nothing_to_move(api: JobsApi) -> None:
    assert call(api, "GET", "/legacy") == {"state": "NONE"}


def test_old_resumes_are_found_and_moved_only_when_asked(moving: tuple[JobsApi, Path]) -> None:
    api, workspace = moving
    before = _manifest(workspace)
    found = call(api, "GET", "/legacy")
    assert found["state"] == "FOUND" and found["base_resumes"] == 1
    assert found["job_versions"] == 4 and found["drafts"] == 1 and found["exports"] == 2
    assert found["unfinished"] == 1 and found["contact"] is True
    # Looking moved nothing.
    assert library(api) == {"master": None, "others": [], "jobs": []}
    out = call(api, "POST", "/legacy/migrate")
    assert out["masters"] == 1 and out["job_versions"] == 3 and out["exports"] == 1
    assert out["failed"] == ["run", "export"] and out["already"] == 0
    assert _manifest(workspace) == before  # the old files are never changed
    backups = list((api.config.db_path.parent / "resume_helper_backups").glob("*.zip"))
    assert len(backups) == 1
    after = call(api, "GET", "/legacy")
    assert after["state"] == "MOVED" and after["remaining"] == 1  # the corrupt run
    with connect(api.config.db_path) as conn:
        counts = resume_row_counts(conn)
        assert ResumeStore(conn).has_legacy_documents()
    again = call(api, "POST", "/legacy/migrate")
    assert again["masters"] == again["job_versions"] == 0 and again["already"] >= 4
    with connect(api.config.db_path) as conn:
        assert resume_row_counts(conn) == counts  # nothing duplicated


def test_no_backup_no_move(moving: tuple[JobsApi, Path], monkeypatch: pytest.MonkeyPatch) -> None:
    import career_agent.resume_doc.legacy as legacy_module

    api, workspace = moving
    before = _manifest(workspace)

    def broken(*_: Any, **__: Any) -> None:
        raise legacy_module.LegacyMigrationError("the backup archive is missing")

    monkeypatch.setattr(legacy_module, "backup_legacy_workspace", broken)
    with pytest.raises(ApiError) as refused:
        call(api, "POST", "/legacy/migrate")
    assert refused.value.code == "backup_failed"
    assert library(api) == {"master": None, "others": [], "jobs": []}
    assert _manifest(workspace) == before
    assert call(api, "GET", "/legacy")["state"] == "FOUND"

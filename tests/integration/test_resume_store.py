"""The Resume Workspace storage (migration 0047) through its one repository.

Synthetic documents and job ads only; every database is a temporary file.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from career_agent.clock import new_id
from career_agent.resume_doc.models import (
    Origin,
    ResumeDocument,
    TextBlock,
    upgrade_resume_document,
)
from career_agent.resume_doc.store import (
    NotFound,
    ResumeStore,
    ResumeStoreError,
    StaleDocument,
)
from career_agent.storage.db import MIGRATIONS_DIR, connect, migrate, table_names, transaction

AD = "Data Analyst at Exemplo Digital. Requisitos: SQL; Python; dashboards."


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "profile.db"
    conn = connect(path)
    migrate(conn)
    conn.close()
    return path


@pytest.fixture
def store(db: Path):  # noqa: ANN201
    conn = connect(db)
    yield ResumeStore(conn)
    conn.close()


def document(kind: str = "MASTER", **extra: Any) -> ResumeDocument:
    return upgrade_resume_document(
        {
            "schema_version": "1.0",
            "id": new_id(),
            "kind": kind,
            "title": f"{kind.title()} resume",
            "language": "en",
            "identity": {"full_name": "Riley Synthetic"},
            "experience": [
                {
                    "id": new_id(),
                    "employer": "Northwind Synthetic",
                    "display_title": "Analyst",
                    "source_title": "Analyst",
                    "start": {"year": 2020},
                    "bullets": [
                        {
                            "id": new_id(),
                            "text": "Built weekly reports.",
                            "origin": "EVIDENCE_VERBATIM",
                            "evidence_ids": ["claim-1@1"],
                        }
                    ],
                }
            ],
            "provenance": {"created_from": "SCRATCH"},
            **extra,
        }
    )


def tailored(store: ResumeStore, snapshot_id: str, master: Any = None, **target: Any) -> Any:
    """A tailored version whose target is copied from its snapshot (overridable)."""
    rev = store.list_revisions(master.id)[-1] if master else None
    snap = store.get_jd_snapshot(snapshot_id)
    doc = document(
        "TAILORED",
        target={
            "jd_snapshot_id": snapshot_id,
            "job_id": snap.job_id,
            "title": snap.title,
            "company": snap.company,
            **target,
        },
        provenance={
            "created_from": "TAILOR",
            "master_document_id": master.id if master else None,
            "master_revision_id": rev.id if rev else None,
        },
    )
    return store.create_document(doc, reason="GENERATED")


def edited(doc: ResumeDocument, title: str) -> ResumeDocument:
    return doc.model_copy(update={"title": title})


# ------------------------------------------------------------- documents


def test_create_read_and_list(store: ResumeStore) -> None:
    master = store.create_document(document())
    assert master.working.identity.full_name == "Riley Synthetic"
    assert store.get_document(master.id) == master
    assert [d.id for d in store.list_documents()] == [master.id]
    [first] = store.list_revisions(master.id)
    assert (first.seq, first.reason, first.content_sha256) == (1, "CREATED", master.working_sha256)
    with pytest.raises(NotFound):
        store.get_document(new_id())
    with pytest.raises(sqlite3.IntegrityError):
        store.create_document(master.working)  # one id, one document


def test_autosave_needs_the_hash_last_read_and_never_writes_history(store: ResumeStore) -> None:
    master = store.create_document(document())
    new_sha = store.save_working_copy(
        master.id, edited(master.working, "Renamed"), expected_sha256=master.working_sha256
    )
    after = store.get_document(master.id)
    assert (after.title, after.working_sha256) == ("Renamed", new_sha)
    assert len(store.list_revisions(master.id)) == 1
    # A second window still holding the first copy cannot overwrite the edit.
    with pytest.raises(StaleDocument) as stale:
        store.save_working_copy(
            master.id, edited(master.working, "Older window"), expected_sha256=master.working_sha256
        )
    assert stale.value.current_sha256 == new_sha
    assert store.get_document(master.id).title == "Renamed"


def test_a_document_keeps_its_id_kind_target_and_provenance(store: ResumeStore) -> None:
    master = store.create_document(document())
    other = document().model_copy(update={"title": "x"})
    with pytest.raises(ResumeStoreError, match="do not change"):
        store.save_working_copy(master.id, other, expected_sha256=master.working_sha256)
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst", job_id="job-1")
    version = tailored(store, ad.id, master)
    assert version.working.target is not None
    for change in (
        {"target": version.working.target.model_copy(update={"job_id": "job-999"})},
        {"provenance": version.working.provenance.model_copy(update={"master_revision_id": None})},
    ):
        with pytest.raises(ResumeStoreError, match="do not change"):
            store.save_working_copy(
                version.id,
                version.working.model_copy(update=change),
                expected_sha256=version.working_sha256,
            )


def test_an_unvalidated_model_is_validated_before_it_is_stored(store: ResumeStore) -> None:
    master = store.create_document(document())
    bad = TextBlock.model_construct(
        id=new_id(), text="Led everything.", origin=Origin.AI_REWRITE, evidence_ids=[]
    )
    sneaky = master.working.model_copy(update={"summary": bad})
    with pytest.raises(ValidationError, match="evidence"):
        store.save_working_copy(master.id, sneaky, expected_sha256=master.working_sha256)
    with pytest.raises(ValidationError):
        store.create_document(sneaky.model_copy(update={"id": new_id()}))
    assert store.list_documents()[0].working == master.working


def test_checkpoints_append_and_history_cannot_be_rewritten(store: ResumeStore, db: Path) -> None:
    master = store.create_document(document())
    sha = store.save_working_copy(
        master.id, edited(master.working, "v2"), expected_sha256=master.working_sha256
    )
    second = store.checkpoint_revision(master.id, "MANUAL_CHECKPOINT")
    assert (second.seq, second.content.title, second.content_sha256) == (2, "v2", sha)
    assert second.base_revision_id == store.list_revisions(master.id)[0].id
    # Nothing changed since: the same milestone, not a duplicate row.
    assert store.checkpoint_revision(master.id, "EXPORTED").id == second.id
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("UPDATE resume_revision SET reason = 'IMPORTED'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.conn.execute("DELETE FROM resume_revision")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute(
            "INSERT INTO resume_revision (id, document_id, seq, content_json, content_sha256,"
            " reason, created_at) VALUES (?, ?, 1, '{}', 'x', 'CREATED', 'now')",
            (new_id(), master.id),
        )


def test_restore_appends_a_new_revision_and_keeps_the_old_ones(store: ResumeStore) -> None:
    master = store.create_document(document())
    original = store.list_revisions(master.id)[0]
    sha = store.save_working_copy(
        master.id, edited(master.working, "Changed"), expected_sha256=master.working_sha256
    )
    store.checkpoint_revision(master.id, "MANUAL_CHECKPOINT")
    with pytest.raises(StaleDocument):
        store.restore_revision(master.id, original.id, expected_sha256=master.working_sha256)
    restored = store.restore_revision(master.id, original.id, expected_sha256=sha)
    assert (restored.seq, restored.reason) == (3, "RESTORED")
    assert restored.base_revision_id == original.id
    assert store.get_document(master.id).working == original.content
    assert [r.content.title for r in store.list_revisions(master.id)] == [
        "Master resume",
        "Changed",
        "Master resume",
    ]


def test_archiving_is_soft(store: ResumeStore) -> None:
    master = store.create_document(document())
    store.archive_document(master.id)
    assert store.list_documents() == []
    [kept] = store.list_documents(include_archived=True)
    assert kept.archived_at is not None and len(store.list_revisions(master.id)) == 1


# --------------------------------------------------------------- job ads


def test_a_snapshot_is_immutable_and_deduplicated_by_content(store: ResumeStore) -> None:
    first = store.create_jd_snapshot(text=AD, title="Data Analyst", company="Exemplo", job_id="j1")
    import hashlib

    assert first.text_sha256 == hashlib.sha256(AD.encode("utf-8")).hexdigest()
    again = store.create_jd_snapshot(text=AD, title="Data Analyst", company="Exemplo", job_id="j1")
    assert again.id == first.id, "the same ad captured twice is one snapshot"
    changed = store.create_jd_snapshot(
        text=AD + " Remote.", title="Data Analyst", company="Exemplo", job_id="j1"
    )
    assert changed.id != first.id and store.get_jd_snapshot(first.id).text == AD
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute("UPDATE jd_snapshot SET text = 'edited' WHERE id = ?", (first.id,))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        store.conn.execute("DELETE FROM jd_snapshot")
    for text, title in (("   ", "Empty"), (AD, "  "), (AD, "")):
        with pytest.raises(ResumeStoreError):
            store.create_jd_snapshot(text=text, title=title)
    assert store.create_jd_snapshot(text=AD, title="Pasted", job_id="").job_id is None


# -------------------------------------------------------------- versions


def test_versions_of_one_job_are_numbered_and_jobs_are_independent(store: ResumeStore) -> None:
    master = store.create_document(document())
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst", job_id="job-1")
    # A new capture of the same job's (changed) ad stays in that job's group.
    ad2 = store.create_jd_snapshot(text=AD + " Hybrid.", title="Data Analyst", job_id="job-1")
    other = store.create_jd_snapshot(text="Another ad entirely.", title="Writer", job_id="job-2")
    numbers = [
        tailored(store, ad.id, master).version_number,
        tailored(store, ad2.id, master).version_number,
        tailored(store, other.id, master).version_number,
        tailored(store, ad.id, master).version_number,
    ]
    assert numbers == [1, 2, 1, 3]


def test_a_pasted_ad_groups_by_its_text(store: ResumeStore) -> None:
    pasted = store.create_jd_snapshot(text=AD, title="Data Analyst")
    retitled = store.create_jd_snapshot(text=AD, title="Analyst (pasted again)")
    assert pasted.id != retitled.id
    assert tailored(store, pasted.id).version_number == 1
    assert tailored(store, retitled.id).version_number == 2
    elsewhere = store.create_jd_snapshot(text="Other.", title="X")
    assert tailored(store, elsewhere.id).version_number == 1


def test_an_archived_version_keeps_its_number(store: ResumeStore) -> None:
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst")
    first = tailored(store, ad.id)
    store.archive_document(first.id)
    assert tailored(store, ad.id).version_number == 2


def test_two_writers_never_take_the_same_version_number(db: Path) -> None:
    setup = ResumeStore(connect(db))
    snapshot = setup.create_jd_snapshot(text=AD, title="Data Analyst", job_id="race")
    setup.conn.close()
    errors: list[BaseException] = []

    def write() -> None:
        conn = connect(db)
        try:
            for _ in range(5):
                tailored(ResumeStore(conn), snapshot.id)
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    threads = [threading.Thread(target=write) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    check = ResumeStore(connect(db))
    numbers = sorted(d.version_number or 0 for d in check.list_documents())
    assert numbers == list(range(1, 16))
    # And the database itself refuses a duplicate, whatever wrote it.
    with pytest.raises(sqlite3.IntegrityError):
        check.conn.execute("UPDATE resume_document SET version_number = 1 WHERE version_number = 2")
    check.conn.close()


def test_one_preferred_version_per_job(store: ResumeStore) -> None:
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst", job_id="job-1")
    v1, v2 = tailored(store, ad.id), tailored(store, ad.id)
    ad2 = store.create_jd_snapshot(text="B", title="B", job_id="job-2")
    other = tailored(store, ad2.id)
    store.set_preferred(v1.id)
    store.set_preferred(other.id)
    store.set_preferred(v2.id)
    preferred = {d.id for d in store.list_documents() if d.preferred}
    assert preferred == {v2.id, other.id}
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE resume_document SET preferred = 1 WHERE id = ?", (v1.id,))
    store.archive_document(v2.id)
    assert not store.get_document(v2.id).preferred
    with pytest.raises(ResumeStoreError):
        store.set_preferred(v2.id)
    master = store.create_document(document())
    with pytest.raises(ResumeStoreError):
        store.set_preferred(master.id)


# ---------------------------------------------------------------- master


def test_tailoring_never_writes_the_master(store: ResumeStore) -> None:
    master = store.create_document(document())
    before = (store.get_document(master.id), store.list_revisions(master.id))
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst")
    version = tailored(store, ad.id, master)
    assert version.master_document_id == master.id
    assert version.working.provenance.master_revision_id == before[1][-1].id
    first = store.list_revisions(version.id)[0]
    sha = store.save_working_copy(
        version.id, edited(version.working, "Edited"), expected_sha256=version.working_sha256
    )
    store.checkpoint_revision(version.id, "MANUAL_CHECKPOINT")
    store.restore_revision(version.id, first.id, expected_sha256=sha)
    run = store.create_tailoring_run(version.id, mode="EVIDENCE_ONLY")
    store.decide_tailoring_change(
        store.record_tailoring_change(run.id, op={"op": "replace"}, source="RULE").id, "ACCEPTED"
    )
    assert (store.get_document(master.id), store.list_revisions(master.id)) == before


def test_a_master_reference_must_be_a_master_and_its_revision(store: ResumeStore) -> None:
    scratch = store.create_document(document("SCRATCH"))
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst")
    with pytest.raises(ResumeStoreError, match="MASTER"):
        tailored(store, ad.id, scratch)
    master = store.create_document(document())
    foreign = store.list_revisions(scratch.id)[0].id
    doc = document(
        "TAILORED",
        target={"jd_snapshot_id": ad.id, "title": "Data Analyst"},
        provenance={
            "created_from": "TAILOR",
            "master_document_id": master.id,
            "master_revision_id": foreign,
        },
    )
    with pytest.raises(ResumeStoreError, match="revision"):
        store.create_document(doc)
    for wrong in ({"job_id": "not-the-snapshots-job"}, {"title": "Invented Senior Title"}):
        with pytest.raises(ResumeStoreError, match="snapshot"):
            tailored(store, ad.id, **wrong)
    orphan = document(
        provenance={"created_from": "MASTER_COPY", "master_revision_id": foreign},
    )
    with pytest.raises(ResumeStoreError, match="needs its master"):
        store.create_document(orphan)


# ------------------------------------------------- runs, exports, findings


def test_a_tailoring_run_and_its_changes_round_trip(store: ResumeStore) -> None:
    master = store.create_document(document())
    ad = store.create_jd_snapshot(text=AD, title="Data Analyst")
    version = tailored(store, ad.id, master)
    with pytest.raises(ResumeStoreError):
        store.create_tailoring_run(master.id, mode="EVIDENCE_ONLY")
    run = store.create_tailoring_run(version.id, mode="EVIDENCE_ONLY", options={"two_pages": True})
    assert (run.status, run.jd_snapshot_id, run.master_document_id) == ("PENDING", ad.id, master.id)
    run = store.update_tailoring_run_stage(
        run.id, status="DONE", analysis={"requirements": [{"id": "r1", "text": "SQL"}]}
    )
    assert run.stages["analysis"] == {"requirements": [{"id": "r1", "text": "SQL"}]}
    assert run.stages["options"] == {"two_pages": True}
    assert run.finished_at is not None and run.stages["review"] is None
    with pytest.raises(ResumeStoreError):
        store.update_tailoring_run_stage(run.id, prompt={"x": 1})
    change = store.record_tailoring_change(
        run.id,
        op={"op": "replace", "path": "/experience/0/bullets/0/text", "value": "Built reports."},
        source="DRAFTER",
        evidence_ids=["claim-1@1"],
        requirement_ids=["r1"],
        reason="Names the reporting the ad asks for.",
    )
    assert (change.decision, change.decided_at) == ("PENDING", None)
    decided = store.decide_tailoring_change(change.id, "REJECTED")
    assert decided.decision == "REJECTED" and decided.evidence_ids == ["claim-1@1"]
    assert store.list_tailoring_changes(run.id) == [decided]
    with pytest.raises(ResumeStoreError, match="already decided"):
        store.decide_tailoring_change(change.id, "ACCEPTED")


def test_export_metadata_and_dismissals_round_trip(store: ResumeStore) -> None:
    master = store.create_document(document())
    other = store.create_document(document())
    revision = store.checkpoint_revision(master.id, "EXPORTED")
    export = store.record_export(
        master.id,
        revision.id,
        format="PDF",
        template="clean",
        file_path="exports/synthetic.pdf",
        file_sha256="0" * 64,
        engine="synthetic",
        page_count=1,
        ats_check={"ascii_dates": True},
    )
    assert store.list_exports(master.id) == [export]
    assert export.ats_check == {"ascii_dates": True}
    with pytest.raises(ResumeStoreError):
        store.record_export(
            other.id, revision.id, format="PDF", template="clean", file_path="x",
            file_sha256="x", engine="x",
        )  # fmt: skip
    store.dismiss_finding(master.id, "NAME_PLACEHOLDER", reason="Pen name on purpose.")
    store.dismiss_finding(master.id, "NAME_PLACEHOLDER")
    assert store.dismissed_findings(master.id) == {"NAME_PLACEHOLDER"}
    assert store.dismissed_findings(other.id) == set(), "a dismissal belongs to one document"


# ------------------------------------------------------ isolation, migration


def test_two_profiles_never_see_each_others_resumes(tmp_path: Path) -> None:
    stores = []
    for name in ("a", "b"):
        conn = connect(tmp_path / name / "personal.db")
        migrate(conn)
        stores.append(ResumeStore(conn))
    a, b = stores
    a.create_document(document())
    a.create_jd_snapshot(text=AD, title="Data Analyst")
    assert len(a.list_documents()) == 1
    assert b.list_documents() == []
    assert b.conn.execute("SELECT COUNT(*) FROM jd_snapshot").fetchone()[0] == 0
    for s in stores:
        s.conn.close()


def test_a_pre_0047_database_migrates_additively_and_once(tmp_path: Path) -> None:
    old = tmp_path / "migrations"
    old.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) < 47:
            shutil.copy(path, old / path.name)
    conn = connect(tmp_path / "old.db")
    migrate(conn, old)
    with transaction(conn):
        conn.execute(
            "INSERT INTO search_fit_feedback (job_id, config_id, config_version, schema_version,"
            " match_score, fit_band, verdict, note, created_at, updated_at)"
            " VALUES ('j', 'c', 1, 1, 50, 'GOOD', 'ACCURATE', 'kept', 'now', 'now')"
        )
    before = set(table_names(conn))
    assert "resume_document" not in before
    applied = migrate(conn)
    assert [(m.version, m.name) for m in applied] == [(47, "resume_workspace")]
    assert set(table_names(conn)) - before == {
        "resume_document",
        "resume_revision",
        "jd_snapshot",
        "tailoring_run",
        "tailoring_change",
        "resume_export",
        "resume_finding_dismissal",
    }
    assert conn.execute("SELECT note FROM search_fit_feedback").fetchone()[0] == "kept"
    assert migrate(conn) == []
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.close()

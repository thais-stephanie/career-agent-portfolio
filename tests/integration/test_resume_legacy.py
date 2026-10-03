"""Migrating a Resume helper workspace into the Resume Workspace (PR 2).

The old workspace is built with the old engine itself, from a synthetic
profile, so its files have exactly the shapes a real one has. Nothing here
is anybody's real career, job ad or resume.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from resume_tailor.core.models import TailorOptions, TailorRequest
from resume_tailor.core.pipeline import TailorService
from resume_tailor.export.exporters import export_filename
from resume_tailor.integration.career import base_resume_from_profile
from resume_tailor.providers.llm.vendors import NoneProvider
from resume_tailor.storage.runs import RunStore
from resume_tailor.workspace import WorkspaceStore
from resume_tailor.workspace import drafts as dr
from tests.integration.test_resume_pr0 import PT_JD, PT_TITLE, THIN_PROFILE

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.resume_doc.legacy import (
    NOT_CHECKED,
    LegacyMigrationError,
    _manifest,
    backup_legacy_workspace,
    migrate_legacy_workspace,
    stable_id,
)
from career_agent.resume_doc.master import get_or_create_master
from career_agent.resume_doc.store import ResumeStore, resume_row_counts
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate

LABEL = "Synthetic Work"


def profile(
    tmp_path: Path, name: str = "p", display_name: str = "Riley Synthetic", *, claims: bool = True
) -> Any:
    """A profile whose Career Evidence still holds the confirmed statements the
    old workspace cites (`claims=False`: none of them)."""
    conn = connect(tmp_path / name / "personal.db")
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, LABEL)
        candidate = ensure_candidate(conn)
        conn.execute(
            "UPDATE candidate SET display_name = ? WHERE id = ?", (display_name, candidate)
        )
        for experience in THIN_PROFILE["experiences"] if claims else []:
            for highlight in experience["highlights"]:  # type: ignore[index]
                ClaimRepo(conn).add(
                    candidate,
                    VerifiedClaim(
                        claim_key=highlight["key"],
                        claim_type=ClaimType.EMPLOYMENT,
                        text=highlight["text"],
                        source=ClaimSource.SELF_ATTESTED,
                        verified=True,
                    ),
                )
    return conn


@pytest.fixture(scope="module")
def legacy(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One base resume, two versions for one job, one pasted ad, a draft,
    two exports, a run that never finished and one that is corrupt."""
    home = tmp_path_factory.mktemp("legacy")
    ws = WorkspaceStore(home).create(LABEL)  # the old helper named it after the profile
    ws.save_meta(
        {
            **ws.meta(),
            "email": "riley@example.invalid",
            "location": "Curitiba, Brazil",
            "linkedin": "linkedin.com/in/riley-synthetic",
        }
    )
    base = base_resume_from_profile(ws, THIN_PROFILE)
    service = TailorService(ws.load_index(), ws.load_resumes(), ws.load_profiles(), NoneProvider())
    store = RunStore(ws.root / "applications")

    def run(run_id: str, **extra: Any) -> Any:
        made = service.run(
            TailorRequest(
                jd_text=PT_JD, resume_id=base.id, options=TailorOptions(use_llm=False), **extra
            ),
            run_id,
        )
        store.save(made)
        return made

    job = {"career_job_id": "job-1", "kind": "career_agent"}
    first = run("20260101T000000-aaaaaa", target_title=PT_TITLE, source=job)
    run("20260102T000000-bbbbbb", target_title=PT_TITLE, source=job)
    pasted = run(
        "20260103T000000-cccccc", target_title="Synthetic Analyst", source={"kind": "pasted"}
    )
    # A draft on the first version: one line reworded, one hidden.
    one, two = (e.bullets[0] for e in first.generated_resume.experience[:2])
    doc = dr.load_doc(ws, first.run_id)
    dr.push_state(
        doc,
        {
            **dr.EMPTY_STATE,
            "bullets": {
                one.id: {"text": "Reworded by the person."},
                two.id: {"hidden": True},
            },
        },
    )
    dr.save_doc(ws, first.run_id, doc)
    # Exports: one names only the pasted version; one names both job versions.
    exports = ws.root / "exports"
    for made in (pasted, first):
        name = export_filename(
            "", made.job_analysis.role_title, made.generated_resume.headline, "docx"
        )
        (exports / name).write_bytes(b"PK synthetic docx " + made.run_id.encode())
    (ws.root / "applications" / "20260104T000000-dddddd").mkdir()
    (ws.root / "applications" / "20260104T000000-dddddd" / "status.json").write_text("{}")
    broken = ws.root / "applications" / "20260105T000000-eeeeee"
    broken.mkdir()
    (broken / "run.json").write_text("{not json", encoding="utf-8")
    return Path(ws.root)


def migrated(tmp_path: Path, legacy: Path, conn: Any = None) -> tuple[Any, Any]:
    conn = conn or profile(tmp_path)
    backup = backup_legacy_workspace(legacy, tmp_path / "backups")
    return conn, migrate_legacy_workspace(conn, backup, labels=[LABEL])


def test_the_whole_workspace_migrates_without_touching_a_file(tmp_path: Path, legacy: Path) -> None:
    before = _manifest(legacy)
    conn, report = migrated(tmp_path, legacy)
    assert _manifest(legacy) == before, "a legacy file was changed"
    assert [f["unit"] for f in report.failures] == [
        "run 20260105T000000-eeeeee",
        f"export {_job_export(legacy)}",
    ]
    assert "matches 2" in report.failures[1]["reason"]
    assert any("dddddd" in n for n in report.notes), "an unfinished run is named, not dropped"
    store = ResumeStore(conn)
    docs = {d.id: d for d in store.list_documents()}
    master = store.current_master()
    assert master is not None and master.id == stable_id("legacy-base", "career_agent_profile")
    identity = master.working.identity
    assert identity.full_name == "Riley Synthetic", "the profile label is not a name"
    assert identity.email == "riley@example.invalid" and identity.city == "Curitiba, Brazil"
    assert [(link.kind, link.url) for link in identity.links] == [
        ("LINKEDIN", "https://linkedin.com/in/riley-synthetic")
    ]
    [revision] = store.list_revisions(master.id)
    assert revision.reason == "PRE_MIGRATION"
    bullet = master.working.experience[0].bullets[0]
    assert bullet.origin == "EVIDENCE_VERBATIM" and bullet.evidence_ids == ["k0a"]
    tailored = sorted(
        (d for d in docs.values() if d.kind == "TAILORED"), key=lambda d: d.created_at
    )
    assert [(d.version_group, d.version_number) for d in tailored][:2] == [
        ("job:job-1", 1),
        ("job:job-1", 2),
    ]
    assert tailored[2].version_group.startswith("jd:") and tailored[2].version_number == 1
    for doc in tailored:
        assert doc.master_document_id == master.id
        assert doc.working.experience[0].source_title in {
            e["title"]  # type: ignore[index]
            for e in THIN_PROFILE["experiences"]
        }


def test_runs_keep_their_history_as_legacy_and_untrusted(tmp_path: Path, legacy: Path) -> None:
    conn, _ = migrated(tmp_path, legacy)
    store = ResumeStore(conn)
    run = store.get_tailoring_run(stable_id("legacy-tailoring", "20260102T000000-bbbbbb"))
    assert (run.mode, run.status, run.provider) == ("DETERMINISTIC_V1", "DONE", "none")
    assert run.stages["analysis"]["trusted"] is False
    assert run.stages["analysis"]["job_analysis"]["requirements"]
    assert run.stages["options"]["use_llm"] is False
    snapshot = store.get_jd_snapshot(run.jd_snapshot_id)
    assert snapshot.text == PT_JD and snapshot.title == PT_TITLE and snapshot.job_id == "job-1"


def test_a_draft_becomes_the_working_copy_and_nothing_is_lost(tmp_path: Path, legacy: Path) -> None:
    conn, _ = migrated(tmp_path, legacy)
    store = ResumeStore(conn)
    doc_id = stable_id("legacy-run", "20260101T000000-aaaaaa")
    generated, edited = store.list_revisions(doc_id)
    assert (generated.reason, edited.reason) == ("GENERATED", "MANUAL_CHECKPOINT")
    working = store.get_document(doc_id)
    assert working.working_sha256 == edited.content_sha256
    first = working.working.experience[0].bullets[0]
    assert first.text == "Reworded by the person."
    assert (first.origin, first.override) == ("USER_AUTHORED", "EDITED")
    old = generated.content.experience[0].bullets
    assert first.original_text == old[0].text and first.id == old[0].id
    hidden = generated.content.experience[1].bullets[0]
    kept = working.working.experience[1].bullets
    assert [b.id for b in kept] == [b.id for b in generated.content.experience[1].bullets]
    assert kept[0].hidden and kept[0].text == hidden.text, "a hidden line stays, hidden"
    # ...which the generated revision still holds.
    no_draft = stable_id("legacy-run", "20260102T000000-bbbbbb")
    assert [r.reason for r in store.list_revisions(no_draft)] == ["GENERATED"]


def test_an_export_points_at_its_file_and_claims_no_check(tmp_path: Path, legacy: Path) -> None:
    conn, _ = migrated(tmp_path, legacy)
    store = ResumeStore(conn)
    [export] = store.list_exports(stable_id("legacy-run", "20260103T000000-cccccc"))
    assert export.ats_check == NOT_CHECKED and export.page_count is None
    assert (
        export.format == "DOCX" and Path(export.file_path).parent == (legacy / "exports").resolve()
    )
    assert export.file_sha256 and not list(tmp_path.glob("**/*.docx")), "no bytes were copied"


def test_a_second_migration_writes_nothing(tmp_path: Path, legacy: Path) -> None:
    conn, first = migrated(tmp_path, legacy)
    counts = resume_row_counts(conn)
    _, second = migrated(tmp_path / "again", legacy, conn)
    assert resume_row_counts(conn) == counts
    assert second.created == [] and len(second.already) == len(first.created)


def test_no_migration_without_a_backup_that_still_matches(tmp_path: Path, legacy: Path) -> None:
    conn = profile(tmp_path)
    backup = backup_legacy_workspace(legacy, tmp_path / "backups")
    extra = legacy / "settings.json"
    original = extra.read_bytes()
    try:
        extra.write_bytes(original + b" ")
        with pytest.raises(LegacyMigrationError, match="changed since its backup"):
            migrate_legacy_workspace(conn, backup)
    finally:
        extra.write_bytes(original)
    assert set(resume_row_counts(conn).values()) == {0}


def test_an_existing_master_is_never_overwritten(tmp_path: Path, legacy: Path) -> None:
    conn = profile(tmp_path)
    master, _ = get_or_create_master(conn)
    _, report = migrated(tmp_path, legacy, conn)
    store = ResumeStore(conn)
    assert store.current_master().id == master.id  # type: ignore[union-attr]
    base = store.get_document(stable_id("legacy-base", "career_agent_profile"))
    assert base.kind == "IMPORTED"
    assert all(d.master_document_id is None for d in store.list_documents() if d.kind == "TAILORED")
    assert not report.failures[2:]


def test_each_profile_migrates_on_its_own(tmp_path: Path, legacy: Path) -> None:
    a, b = profile(tmp_path, "a"), profile(tmp_path, "b", display_name="You")
    migrated(tmp_path / "a", legacy, a)
    assert set(resume_row_counts(b).values()) == {0}
    migrated(tmp_path / "b", legacy, b)
    master_b = ResumeStore(b).current_master()
    assert master_b is not None and master_b.working.identity.full_name == "", "You is not a name"
    assert ResumeStore(a).current_master().working.identity.full_name == "Riley Synthetic"  # type: ignore[union-attr]


def test_a_corrupt_run_is_rolled_back_whole(tmp_path: Path, legacy: Path) -> None:
    """A run whose content cannot be read leaves no snapshot, document or run."""
    conn = profile(tmp_path)
    target = legacy / "applications" / "20260102T000000-bbbbbb" / "run.json"
    original = target.read_text("utf-8")
    data = json.loads(original)
    data["generated_resume"]["experience"][0]["company"] = ""  # an employer is required
    try:
        target.write_text(json.dumps(data), encoding="utf-8")
        _, report = migrated(tmp_path, legacy, conn)
    finally:
        target.write_text(original, encoding="utf-8")
    assert "run 20260102T000000-bbbbbb" in [f["unit"] for f in report.failures]
    store = ResumeStore(conn)
    with pytest.raises(Exception):  # noqa: B017
        store.get_document(stable_id("legacy-run", "20260102T000000-bbbbbb"))
    with pytest.raises(Exception):  # noqa: B017
        store.get_tailoring_run(stable_id("legacy-tailoring", "20260102T000000-bbbbbb"))
    assert isinstance(conn, sqlite3.Connection)


def _job_export(legacy: Path) -> str:
    names = sorted(p.name for p in (legacy / "exports").iterdir())
    return next(n for n in names if "Synthetic Analyst" not in n)


def test_evidence_is_cited_only_while_this_profile_still_confirms_it(
    tmp_path: Path, legacy: Path
) -> None:
    conn = profile(tmp_path, claims=False)
    migrated(tmp_path, legacy, conn)
    master = ResumeStore(conn).current_master()
    assert master is not None
    bullets = [b for e in master.working.experience for b in e.bullets]
    assert bullets and all(b.evidence_ids == [] and b.origin == "IMPORTED" for b in bullets)


def test_a_renamed_workspace_or_old_label_is_never_the_name(tmp_path: Path, legacy: Path) -> None:
    conn = profile(tmp_path, display_name="You")
    meta_file = legacy / "candidate.json"
    original = meta_file.read_text("utf-8")
    try:
        meta_file.write_text(
            json.dumps({**json.loads(original), "name": "Old Label"}), encoding="utf-8"
        )
        migrated(tmp_path, legacy, conn)
    finally:
        meta_file.write_text(original, encoding="utf-8")
    master = ResumeStore(conn).current_master()
    assert master is not None and master.working.identity.full_name == ""


def test_a_workspace_of_another_profile_is_refused(tmp_path: Path, legacy: Path) -> None:
    conn = profile(tmp_path)
    with transaction(conn):
        conn.execute("UPDATE database_identity SET profile_id = 'prof-01SYNTHETICTHIS'")
    meta_file = legacy / "candidate.json"
    original = meta_file.read_text("utf-8")
    try:
        meta = {**json.loads(original), "career_agent_profile_id": "prof-01SYNTHETICOTHER"}
        meta_file.write_text(json.dumps(meta), encoding="utf-8")
        _, report = migrated(tmp_path, legacy, conn)
    finally:
        meta_file.write_text(original, encoding="utf-8")
    assert [f["unit"] for f in report.failures] == ["identity"]
    assert "another profile" in report.failures[0]["reason"]
    assert set(resume_row_counts(conn).values()) == {0}, "nothing migrates after a refusal"


def test_a_draft_edited_after_migration_is_reported_not_applied(
    tmp_path: Path, legacy: Path
) -> None:
    conn, _ = migrated(tmp_path, legacy)
    draft = legacy / "drafts" / "20260101T000000-aaaaaa.json"
    original = draft.read_text("utf-8")
    doc_id = stable_id("legacy-run", "20260101T000000-aaaaaa")
    before = ResumeStore(conn).get_document(doc_id).working_sha256
    try:
        doc = json.loads(original)
        doc["history"][doc["cursor"]]["note"] = "Added later in the old helper."
        draft.write_text(json.dumps(doc), encoding="utf-8")
        _, report = migrated(tmp_path / "again", legacy, conn)
    finally:
        draft.write_text(original, encoding="utf-8")
    assert {"unit": "run 20260101T000000-aaaaaa", "reason": (
        "its draft changed after it was migrated; the change is not applied"
    )} in report.failures  # fmt: skip
    assert ResumeStore(conn).get_document(doc_id).working_sha256 == before

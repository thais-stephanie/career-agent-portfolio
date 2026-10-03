"""The Master resume and its identity (Resume Workspace V2, PR 2).

A synthetic Career Profile written straight into a profile database: no CV
read, no network, no model. Nothing here is anybody's real career.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from career_agent.clock import new_id, now_utc
from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.resume_doc.master import (
    evidence_changes,
    get_or_create_master,
    resolve_identity,
    update_resume_identity,
)
from career_agent.resume_doc.models import Identity, Origin
from career_agent.resume_doc.store import ResumeStore, StaleDocument, resume_row_counts
from career_agent.runtime import RuntimeMode, stamp_identity
from career_agent.storage.db import connect, migrate, transaction
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate

LABEL = "Synthetic Work"


def profile(tmp_path: Path, name: str = "p") -> sqlite3.Connection:
    conn = connect(tmp_path / name / "personal.db")
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, LABEL)
    return conn


def claim(
    conn: sqlite3.Connection,
    key: str,
    kind: ClaimType,
    text: str,
    *,
    experience: str | None = None,
    verified: bool = True,
    tools: list[str] | None = None,
) -> None:
    candidate = ensure_candidate(conn)
    ClaimRepo(conn).add(
        candidate,
        VerifiedClaim(
            claim_key=key,
            claim_type=kind,
            text=text,
            source=ClaimSource.SELF_ATTESTED,
            verified=verified,
            tools=tools or [],
        ),
    )
    if experience:
        conn.execute(
            "INSERT INTO career_evidence_link (candidate_id, claim_key, experience_id)"
            " VALUES (?, ?, ?)",
            (candidate, key, experience),
        )


def experience(
    conn: sqlite3.Connection,
    company: str,
    title: str,
    start: str,
    end: str | None,
    *,
    current: bool = False,
    archived: bool = False,
) -> str:
    candidate = ensure_candidate(conn)
    company_id = new_id()
    conn.execute(
        "INSERT INTO career_company (id, candidate_id, label) VALUES (?, ?, ?)",
        (company_id, candidate, company),
    )
    exp_id = new_id()
    conn.execute(
        "INSERT INTO career_experience (id, candidate_id, company_id, title, period_start,"
        " period_end, current_role, archived, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (exp_id, candidate, company_id, title, start, end, int(current), int(archived), now_utc()),
    )
    return exp_id


def rich(conn: sqlite3.Connection) -> dict[str, str]:
    with transaction(conn):
        now = experience(conn, "Northwind Synthetic", "Data Analyst", "2022-03", None, current=True)
        before = experience(conn, "Exemplo Digital", "Reporting Assistant", "2019", "2022-02")
        gone = experience(conn, "Archived Co", "Old Role", "2010-01", "2011-01", archived=True)
        claim(conn, "k-built", ClaimType.EMPLOYMENT, "Built weekly reports.", experience=now,
              tools=["python", "SQL"])  # fmt: skip
        claim(conn, "k-cut", ClaimType.ACHIEVEMENT, "Cut report time in half.", experience=now)
        claim(conn, "k-old", ClaimType.EMPLOYMENT, "Kept the archive tidy.", experience=before)
        claim(conn, "k-draft", ClaimType.EMPLOYMENT, "Waiting for review.", experience=now,
              verified=False)  # fmt: skip
        claim(conn, "k-gone", ClaimType.EMPLOYMENT, "In an archived role.", experience=gone)
        claim(conn, "k-python", ClaimType.SKILL, "Python")
        claim(conn, "k-cert", ClaimType.CERTIFICATION, "Synthetic Data Certificate")
        claim(conn, "k-edu", ClaimType.EDUCATION, "BSc Synthetic Studies, Example University")
        claim(conn, "k-loose", ClaimType.ACHIEVEMENT, "Won a synthetic award.")
    return {"now": now, "before": before, "gone": gone}


# ------------------------------------------------------------------ master


def test_an_empty_profile_gets_an_empty_master_with_a_blank_name(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    master, notes = get_or_create_master(conn)
    assert master.kind == "MASTER" and master.working.experience == []
    assert master.working.identity.full_name == ""
    assert master.working.identity.name_finding() == "NAME_MISSING"
    assert any("full_name" in n for n in notes)


def test_the_master_holds_confirmed_evidence_verbatim_and_nothing_else(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    ids = rich(conn)
    master, notes = get_or_create_master(conn)
    doc = master.working
    assert [e.employer for e in doc.experience] == ["Northwind Synthetic", "Exemplo Digital"]
    current, older = doc.experience
    assert current.experience_id == ids["now"] and current.current and current.end is None
    assert (current.source_title, current.display_title) == ("Data Analyst", "Data Analyst")
    assert (older.start.year, older.start.month) == (2019, None)  # type: ignore[union-attr]
    texts = {b.text: b for e in doc.experience for b in e.bullets}
    assert set(texts) == {
        "Built weekly reports.",
        "Cut report time in half.",
        "Kept the archive tidy.",
    }
    assert texts["Built weekly reports."].evidence_ids == ["k-built"]
    assert {b.origin for b in texts.values()} == {Origin.EVIDENCE_VERBATIM}
    assert all(b.original_text is None for b in texts.values())
    [skills] = doc.skills
    by_label = {i.label.casefold(): i for i in skills.items}
    assert set(by_label) == {"python", "sql"}, "one skill per name, whatever its case"
    assert sorted(by_label["python"].evidence_ids) == ["k-built", "k-python"]
    assert [c.name for c in doc.certifications] == ["Synthetic Data Certificate"]
    assert any("k-edu" in n for n in notes) and any("k-loose" in n for n in notes)
    assert doc.education == [], "no education invented from a sentence"


def test_one_master_per_profile(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    rich(conn)
    first, _ = get_or_create_master(conn)
    again, notes = get_or_create_master(conn)
    assert again.id == first.id and notes == []
    store = ResumeStore(conn)
    [revision] = store.list_revisions(first.id)
    assert revision.reason == "CREATED" and revision.content_sha256 == first.working_sha256
    another = first.working.model_copy(update={"id": new_id()})
    with pytest.raises(sqlite3.IntegrityError):
        store.create_document(another)
    assert len(store.list_documents()) == 1


def test_the_display_title_is_editable_and_evidence_changes_are_only_reported(
    tmp_path: Path,
) -> None:
    conn = profile(tmp_path)
    ids = rich(conn)
    master, _ = get_or_create_master(conn)
    store = ResumeStore(conn)
    entry = master.working.experience[0]
    renamed = master.working.model_copy(
        update={
            "experience": [
                entry.model_copy(update={"display_title": "Analytics Lead"}),
                *master.working.experience[1:],
            ]
        }
    )
    sha = store.save_working_copy(master.id, renamed, expected_sha256=master.working_sha256)
    assert evidence_changes(conn, renamed) == {
        "added": [],
        "removed": [],
        "changed": [],
        "experiences_added": [],
        "experiences_removed": [],
    }
    candidate = ensure_candidate(conn)
    with transaction(conn):
        old = ClaimRepo(conn).current_row(candidate, "k-cut")
        assert old is not None
        ClaimRepo(conn).supersede(
            candidate,
            VerifiedClaim(
                claim_key="k-cut",
                revision=2,
                claim_type=ClaimType.ACHIEVEMENT,
                text="Cut report time by 40%.",
                source=ClaimSource.SELF_ATTESTED,
                verified=True,
            ),
        )
        claim(conn, "k-new", ClaimType.EMPLOYMENT, "Ran the data guild.", experience=ids["now"])
    changes = evidence_changes(conn, store.get_document(master.id).working)
    assert changes["changed"] == ["k-cut"] and changes["added"] == ["k-new"]
    after = store.get_document(master.id)
    assert after.working_sha256 == sha, "nothing was applied behind the person's back"
    assert after.working.experience[0].source_title == "Data Analyst"


# ---------------------------------------------------------------- identity


def test_a_placeholder_or_a_profile_label_is_never_the_name(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    with transaction(conn):
        candidate = ensure_candidate(conn)
    for stored, expected in (("You", ""), (LABEL, ""), ("Riley Synthetic", "Riley Synthetic")):
        with transaction(conn):
            conn.execute("UPDATE candidate SET display_name = ? WHERE id = ?", (stored, candidate))
        identity, _ = resolve_identity(conn)
        assert identity.full_name == expected, stored
    identity, _ = resolve_identity(conn, contact={"name": "My profile"}, labels=["Other Label"])
    assert identity.full_name == "Riley Synthetic", "a placeholder contact name falls through"
    identity, _ = resolve_identity(conn, contact={"name": "Other Label"}, labels=["Other Label"])
    assert identity.full_name == "Riley Synthetic"


def test_contact_values_are_kept_valid_and_never_inferred(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    identity, notes = resolve_identity(
        conn,
        contact={
            "name": "Riley Synthetic",
            "email": "riley@example.invalid",
            "phone": "+55 41 0000-0000",
            "location": "Curitiba, PR, Brazil",
            "linkedin": "linkedin.com/in/riley-synthetic",
            "portfolio": "https://example.invalid/riley",
        },
    )
    assert identity.email == "riley@example.invalid" and identity.city == "Curitiba, PR, Brazil"
    assert [(link.kind, link.url) for link in identity.links] == [
        ("LINKEDIN", "https://linkedin.com/in/riley-synthetic"),
        ("PORTFOLIO", "https://example.invalid/riley"),
    ]
    assert any("linkedin" in n for n in notes)
    bad, notes = resolve_identity(conn, contact={"email": "not an email"})
    assert bad.email is None and any("email" in n for n in notes)
    with pytest.raises(ValueError):
        Identity.model_validate({"links": [{"id": new_id(), "kind": "WEBSITE", "url": "ftp://x"}]})


def test_editing_identity_is_a_revision_and_touches_nothing_else(tmp_path: Path) -> None:
    conn = profile(tmp_path)
    rich(conn)
    master, _ = get_or_create_master(conn)
    claims_before = conn.execute("SELECT * FROM verified_claim ORDER BY id").fetchall()
    candidate_before = conn.execute("SELECT * FROM candidate").fetchall()
    identity = Identity.model_validate(
        {
            "full_name": "Riley Synthetic",
            "email": "riley@example.invalid",
            "country": "BR",
            "links": [{"id": new_id(), "kind": "GITHUB", "url": "https://example.invalid/r"}],
        }
    )
    updated = update_resume_identity(conn, identity, expected_sha256=master.working_sha256)
    assert updated.working.identity == identity
    reasons = [r.reason for r in ResumeStore(conn).list_revisions(master.id)]
    assert reasons == ["CREATED", "MANUAL_CHECKPOINT"]
    with pytest.raises(StaleDocument):
        update_resume_identity(conn, Identity(), expected_sha256=master.working_sha256)
    assert conn.execute("SELECT * FROM verified_claim ORDER BY id").fetchall() == claims_before
    assert conn.execute("SELECT * FROM candidate").fetchall() == candidate_before


def test_the_identity_api(tmp_path: Path) -> None:
    from tests.integration.test_tailor_bridge import _profile

    from career_agent.web.server import ApiError

    _, api, _ = _profile(tmp_path, "prof-01SYNTHETICRESUMEAPIAAAAAA", LABEL)
    config = api.config.config_dir / "search.local.yaml"
    config_before = config.read_bytes()
    matches_before = _scores(api)

    def call(method: str, body: dict[str, Any] | None = None, path: str = "") -> Any:
        return api.handle_api(method, f"/api/resume/master{path}", {}, body or {})

    assert call("GET") == {"master": None}, "reading never creates a Master"
    made = call("POST")["master"]
    assert call("POST")["master"]["id"] == made["id"]
    body = {"identity": {"full_name": "Riley Synthetic"}, "expected_sha256": made["sha256"]}
    after = call("PATCH", body, "/identity")["master"]
    assert after["identity"]["full_name"] == "Riley Synthetic" and after["name_finding"] is None
    older = {"identity": {"full_name": "Riley Older Window"}, "expected_sha256": made["sha256"]}
    with pytest.raises(ApiError) as stale:
        call("PATCH", older, "/identity")
    assert stale.value.status == 409
    with pytest.raises(ApiError) as bad:
        call(
            "PATCH",
            {"identity": {"email": "nope"}, "expected_sha256": after["sha256"]},
            "/identity",
        )
    assert bad.value.status == 400
    assert config.read_bytes() == config_before and _scores(api) == matches_before


def _scores(api: Any) -> list[tuple[Any, ...]]:
    with connect(api.config.db_path) as conn:
        return [tuple(r) for r in conn.execute("SELECT job_id, match_score FROM job_match")]


# ------------------------------------------------- forget, two profiles


def test_forget_everything_clears_this_profiles_resumes_only(tmp_path: Path) -> None:
    from tests.integration.test_tailor_bridge import _profile

    from career_agent.cli import app

    _, api, _ = _profile(tmp_path, "prof-01SYNTHETICFORGETAAAAAAAAA", LABEL)
    _, other, _ = _profile(tmp_path, "prof-01SYNTHETICKEEPAAAAAAAAAA", "Other")
    for each in (api, other):
        with connect(each.config.db_path) as conn:
            master, _ = get_or_create_master(conn)
            store = ResumeStore(conn)
            snap = store.create_jd_snapshot(
                text="A synthetic ad for a synthetic role.", title="Role"
            )
            store.dismiss_finding(master.id, "NAME_MISSING")
            store.record_export(
                master.id, store.list_revisions(master.id)[0].id, format="PDF", template="clean",
                file_path="x.pdf", file_sha256="0" * 64, engine="synthetic",
            )  # fmt: skip
            assert snap.id
    with connect(api.config.db_path) as conn:
        jobs = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
    result = CliRunner().invoke(
        app,
        ["forget", "everything", "--yes", "--db", str(api.config.db_path),
         "--config-dir", str(api.config.config_dir)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "1 resumes" in result.output
    with connect(api.config.db_path) as conn:
        assert set(resume_row_counts(conn).values()) == {0}
        assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == jobs
        with pytest.raises(sqlite3.IntegrityError):
            ResumeStore(conn).create_jd_snapshot(text="Another synthetic ad text.", title="R")
            conn.execute("DELETE FROM jd_snapshot")  # the guard is back
    with connect(other.config.db_path) as conn:
        assert resume_row_counts(conn)["resume_document"] == 1, "another profile was touched"


def test_two_profiles_have_their_own_master_and_identity(tmp_path: Path) -> None:
    a, b = profile(tmp_path, "a"), profile(tmp_path, "b")
    rich(a)
    master_a, _ = get_or_create_master(a)
    update_resume_identity(
        a, Identity(full_name="Riley Synthetic"), expected_sha256=master_a.working_sha256
    )
    master_b, _ = get_or_create_master(b)
    assert master_b.id != master_a.id
    assert master_b.working.experience == [] and master_b.working.identity.full_name == ""
    assert ResumeStore(b).list_documents()[0].id == master_b.id

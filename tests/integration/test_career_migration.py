"""Migration adds only organization tables; historical evidence bytes survive."""

import shutil

from tests.integration.test_career_import_review import package

from career_agent.domain.claims import VerifiedClaim
from career_agent.domain.enums import ClaimSource, ClaimType
from career_agent.intake.store import import_package
from career_agent.storage.career_repo import CareerRepo
from career_agent.storage.db import MIGRATIONS_DIR, connect, migrate, transaction
from career_agent.storage.integrity import check_career_organization
from career_agent.storage.repositories import ClaimRepo
from career_agent.storage.workspace_repo import ensure_candidate


def test_upgrade_preserves_claim_keys_revisions_sources_and_imports(tmp_path):
    old = tmp_path / "old-migrations"
    old.mkdir()
    for path in MIGRATIONS_DIR.glob("*.sql"):
        if int(path.name[:4]) <= 29:
            shutil.copyfile(path, old / path.name)
    conn = connect(tmp_path / "upgrade.db")
    migrate(conn, old)
    import_package(conn, package())
    with transaction(conn):
        candidate = ensure_candidate(conn)
        claims = ClaimRepo(conn)
        initial = VerifiedClaim(
            claim_key="stable-key",
            claim_type=ClaimType.PROJECT,
            text="Built a shared checklist.",
            employer="Original label",
            source=ClaimSource.RESUME,
            evidence_ref="Original CV, page 2",
            verified=True,
        )
        claims.add(candidate, initial)
        claims.supersede(
            candidate, initial.next_revision(text="Built a shared handover checklist.")
        )
    tables = ("verified_claim", "intake_package", "intake_claim", "candidate_state")
    # The columns each table had BEFORE the upgrade. A later migration may add
    # nullable columns (0039 adds `intake_package.deleted_at`); what must not
    # change is a single byte of what was already there.
    columns = {
        table: ", ".join(row[1] for row in conn.execute(f"PRAGMA table_info({table})"))
        for table in tables
    }
    before = {
        table: [tuple(r) for r in conn.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1")]
        for table in tables
    }
    proposed_before = CareerRepo(conn, candidate).overview()["proposals"]
    assert [m.version for m in migrate(conn)] == [
        int(path.name[:4])
        for path in sorted(MIGRATIONS_DIR.glob("*.sql"))
        if int(path.name[:4]) >= 30
    ]
    assert migrate(conn) == []
    assert before == {
        table: [tuple(r) for r in conn.execute(f"SELECT {columns[table]} FROM {table} ORDER BY 1")]
        for table in tables
    }
    assert CareerRepo(conn, candidate).overview()["proposals"] == proposed_before
    assert CareerRepo(conn, candidate).overview()["experiences"] == []
    assert check_career_organization(conn) is None
    conn.close()


def test_integrity_detects_alias_cycles_without_changing_them(tmp_path):
    conn = connect(tmp_path / "cycle.db")
    migrate(conn)
    with transaction(conn):
        candidate = ensure_candidate(conn)
        conn.execute(
            "INSERT INTO career_company(id, candidate_id, label) VALUES ('a', ?, 'A')", (candidate,)
        )
        conn.execute(
            "INSERT INTO career_company(id, candidate_id, label, merged_into)"
            " VALUES ('b', ?, 'B', 'a')",
            (candidate,),
        )
        conn.execute("UPDATE career_company SET merged_into = 'b' WHERE id = 'a'")
    finding = check_career_organization(conn)
    assert finding is not None and finding.check == "career_alias_cycle" and finding.count == 2
    assert (
        conn.execute("SELECT merged_into FROM career_company WHERE id = 'a'").fetchone()[0] == "b"
    )
    conn.close()

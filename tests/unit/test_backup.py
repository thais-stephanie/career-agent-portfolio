"""A backup that keeps the work and refuses the secrets.

The asymmetry runs through every test here. Omitting a preference file is an
inconvenience somebody notices and fixes; including a credential is a key loose
in a Downloads folder, in a file people email to themselves and forget.
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from career_agent.storage.backup import NEVER_COPIED, contents, create_backup


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A database with real rows, a private config, and a credential beside it."""
    config = tmp_path / "config"
    config.mkdir()
    (config / "search.local.yaml").write_text("config_version: 4\n", encoding="utf-8")
    (config / "search.local.yaml.backup").write_text("config_version: 3\n", encoding="utf-8")
    (config / "search.worked-example.yaml").write_text("config_version: 2\n", encoding="utf-8")
    (config / ".env").write_text("JOOBLE_API_KEY=not-a-real-key-abc123\n", encoding="utf-8")

    db = tmp_path / "career.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE job (id TEXT PRIMARY KEY, title TEXT);"
        "CREATE TABLE job_application (id TEXT PRIMARY KEY, job_id TEXT, applied_at TEXT);"
    )
    conn.execute("INSERT INTO job VALUES ('j1', 'Data Engineer')")
    conn.execute("INSERT INTO job_application VALUES ('a1', 'j1', '2026-09-01T00:00:00Z')")
    conn.commit()
    conn.close()
    return tmp_path


def make(workspace: Path) -> tuple[Path, object]:
    destination = workspace / "out" / "backup.zip"
    result = create_backup(
        db=workspace / "career.db",
        config_dir=workspace / "config",
        destination=destination,
        staging=workspace / "staging",
    )
    return destination, result


# =========================================================================
# 1. NO SECRET, EVER
# =========================================================================


def test_no_credential_reaches_the_archive(workspace: Path) -> None:
    destination, _ = make(workspace)
    names = contents(destination)
    assert not [name for name in names if ".env" in name.lower()]


def test_the_key_itself_appears_nowhere_in_the_bytes(workspace: Path) -> None:
    """The strongest form. Not "no file named .env" -- no occurrence of the
    value, anywhere in the archive, however it might have got there."""
    destination, _ = make(workspace)
    raw = destination.read_bytes()
    assert b"not-a-real-key-abc123" not in raw
    assert b"JOOBLE_API_KEY" not in raw


def test_the_manifest_says_what_is_missing_and_why(workspace: Path) -> None:
    """A restore that fails on an absent key, with nothing saying it was never
    there, is an hour spent looking for a bug that does not exist."""
    destination, _ = make(workspace)
    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("MANIFEST.json"))
    assert sorted(manifest["excluded"]["credentials"]) == sorted(NEVER_COPIED)
    assert "why" in manifest["excluded"]
    assert manifest["restore"]


# =========================================================================
# 2. THE WORK IS ACTUALLY IN THERE
# =========================================================================


def test_the_database_is_a_working_database_not_a_file_copy(workspace: Path) -> None:
    """Restored and queried, because under WAL a `.db` file on its own is not
    the database and a copy taken with `shutil` can be missing the newest
    commits -- which is exactly the work somebody wants back."""
    destination, _ = make(workspace)
    restored = workspace / "restored"
    with zipfile.ZipFile(destination) as archive:
        archive.extractall(restored)

    conn = sqlite3.connect(restored / "career.db")
    try:
        assert conn.execute("SELECT COUNT(*) FROM job").fetchone()[0] == 1
        assert conn.execute("SELECT applied_at FROM job_application").fetchone()[0] == (
            "2026-09-01T00:00:00Z"
        )
    finally:
        conn.close()


def test_the_private_configuration_is_kept(workspace: Path) -> None:
    destination, result = make(workspace)
    names = contents(destination)
    assert "config/search.local.yaml" in names
    assert "config/search.local.yaml.backup" in names
    assert "search.local.yaml" in result.config_files


def test_the_committed_example_is_not_kept(workspace: Path) -> None:
    """It is already in git, and a backup twice the size for a file `git
    checkout` restores is a backup somebody stops taking."""
    destination, _ = make(workspace)
    assert "config/search.worked-example.yaml" not in contents(destination)


def test_the_counts_are_read_from_the_database_not_guessed(workspace: Path) -> None:
    _, result = make(workspace)
    assert result.jobs == 1
    assert result.applications == 1


def test_a_secret_beside_the_config_is_reported_as_skipped(workspace: Path) -> None:
    """Silence would be indistinguishable from not having looked."""
    _, result = make(workspace)
    assert ".env" in result.skipped_secrets


# =========================================================================
# 3. THE MODULE OPENS NO SOCKET
# =========================================================================


def test_nothing_here_can_reach_the_network() -> None:
    """A backup that could upload is a backup that might.

    Walked as an import GRAPH, not searched as text. The first version of this
    test grepped the source for "socket" and failed on the docstring sentence
    saying the module opens none -- a check that cannot tell a promise from a
    violation is not a check.
    """
    import ast

    from career_agent.storage import backup

    forbidden = {"httpx", "requests", "urllib", "socket", "http", "ftplib", "smtplib"}
    tree = ast.parse(Path(backup.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])

    assert not (imported & forbidden), f"the backup module imports {sorted(imported & forbidden)}"


# =========================================================================
# 4. WHAT SHE WROTE, COUNTED APART FROM WHAT WAS COLLECTED
# =========================================================================


def _full_schema(workspace: Path) -> None:
    """Add the V1.3 candidate tables to the fixture's database.

    Postings can be collected again. A confirmed career fact cannot: it came
    from an evening spent reviewing a CV, and there is nowhere to re-collect it
    from. A backup summary reporting only postings would let somebody check an
    archive, see a large number and never notice the half that is
    irreplaceable.
    """
    conn = sqlite3.connect(workspace / "career.db")
    conn.executescript(
        "ALTER TABLE job_application ADD COLUMN notes TEXT;"
        "CREATE TABLE verified_claim ("
        "  id TEXT PRIMARY KEY, verified INTEGER, superseded_by_id TEXT);"
        "CREATE TABLE cv_proposal (id TEXT PRIMARY KEY, decision TEXT);"
    )
    conn.executescript(
        "INSERT INTO verified_claim VALUES ('c1', 1, NULL);"
        "INSERT INTO verified_claim VALUES ('c2', 1, NULL);"
        "INSERT INTO verified_claim VALUES ('c3', 0, NULL);"
        "INSERT INTO verified_claim VALUES ('c4', 1, 'c1');"
        "INSERT INTO cv_proposal VALUES ('p1', 'PENDING');"
        "INSERT INTO cv_proposal VALUES ('p2', 'ACCEPTED');"
        "UPDATE job_application SET notes = 'recruiter said mid-October';"
    )
    conn.commit()
    conn.close()


def test_the_candidate_side_is_counted_and_reported(workspace: Path) -> None:
    _full_schema(workspace)
    destination, result = make(workspace)

    # Two confirmed and current. The retired one and the superseded one are
    # neither, which is what "what you can draw on" means everywhere else.
    assert result.claims == 2
    assert result.pending_proposals == 1
    assert result.notes == 1

    manifest = json.loads(zipfile.ZipFile(destination).read("MANIFEST.json"))
    assert manifest["confirmed_claims"] == 2
    assert manifest["cv_proposals_awaiting_review"] == 1
    assert manifest["application_notes"] == 1


def test_the_candidate_data_is_readable_after_a_restore(workspace: Path) -> None:
    """The point of a backup. Extracted and opened, not merely present."""
    _full_schema(workspace)
    destination, _ = make(workspace)
    out = workspace / "restored"
    zipfile.ZipFile(destination).extractall(out)

    conn = sqlite3.connect(out / "career.db")
    try:
        confirmed = conn.execute(
            "SELECT COUNT(*) FROM verified_claim WHERE verified = 1 AND superseded_by_id IS NULL"
        ).fetchone()[0]
        proposals = conn.execute("SELECT COUNT(*) FROM cv_proposal").fetchone()[0]
    finally:
        conn.close()
    assert confirmed == 2
    assert proposals == 2


def test_a_database_older_than_the_candidate_tables_still_backs_up(
    workspace: Path,
) -> None:
    """The person most in need of a backup is the one who has not migrated. A
    count that raised on a missing table would refuse exactly her."""
    destination, result = make(workspace)
    assert result.jobs == 1
    assert result.claims == 0
    assert result.pending_proposals == 0
    assert result.notes == 0
    assert destination.exists()

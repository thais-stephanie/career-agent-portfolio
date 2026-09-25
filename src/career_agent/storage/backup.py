"""A copy of everything that would hurt to lose, and nothing that would hurt to keep.

This is a local-first product with no server behind it. The corpus, the tracked
applications, the dates somebody applied and the search they spent an evening
tuning all live in two places on one machine, and nothing else has a copy.

Three rules, and the third is the one worth arguing about.

**The database is copied through SQLite, not through the filesystem.** Under
WAL a `.db` file on its own is not the database: recent commits live in
`-wal` until a checkpoint moves them. Copying the file while anything is
writing produces a backup missing the most recent work, which is exactly the
work somebody would want back. `sqlite3.Connection.backup` takes a consistent
snapshot of a live database and is the supported way to do this.

**No secret is ever copied.** `.env` is excluded by name and by rule: a backup
is a file people email to themselves, put on a USB stick, and forget about, and
a credential in one outlives every intention. The manifest says so in words, so
somebody restoring does not spend an hour wondering why the key is missing.

**Nothing is committed and nothing is uploaded.** The archive is written where
it is asked for and this module opens no socket.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

#: Filenames that must never enter a backup, whatever directory they sit in.
#:
#: Matched on the NAME, so a copy of `.env` in a subdirectory is caught too. A
#: prefix rule rather than a whitelist because the failure is asymmetric:
#: omitting a preference file is an inconvenience, and including a key is a
#: credential loose in somebody's Downloads folder.
NEVER_COPIED: frozenset[str] = frozenset({".env", ".env.local", ".env.production"})

#: Config files worth keeping. The private ones by definition -- they are the
#: only ones not already in git.
CONFIG_GLOBS: tuple[str, ...] = ("*.local.yaml", "*.local.yaml.backup")


@dataclass
class BackupResult:
    """What went in, so the caller can report it without reopening the archive."""

    path: Path
    database_bytes: int = 0
    config_files: list[str] = field(default_factory=list)
    skipped_secrets: list[str] = field(default_factory=list)
    jobs: int = 0
    applications: int = 0
    #: What the person put in herself, counted separately from what was
    #: collected. Losing 21,000 postings costs an afternoon of re-collection;
    #: losing the facts she confirmed about her own career costs an evening of
    #: reviewing a CV again, and there is nowhere to re-collect it FROM. A
    #: summary that reported only postings would let somebody check a backup,
    #: see a large number, and never notice the half that is irreplaceable.
    claims: int = 0
    pending_proposals: int = 0
    notes: int = 0

    @property
    def total_bytes(self) -> int:
        return self.path.stat().st_size if self.path.exists() else 0


def _count(live: sqlite3.Connection, sql: str) -> int:
    """One count, or zero when the table is not there yet.

    A database migrated before 0019 has no `cv_proposal`, and a backup that
    raised on an older file would refuse exactly the person most in need of
    one.
    """
    try:
        row = live.execute(sql).fetchone()
    except sqlite3.OperationalError:
        return 0
    return int(row["n"]) if row is not None else 0


def _snapshot(source: Path, destination: Path) -> dict[str, int]:
    """A consistent copy of a live SQLite database, and what is in it.

    `Connection.backup` rather than `shutil.copy`: under WAL the `.db` file
    alone is missing whatever sits in the write-ahead log, and that is the most
    recent work rather than the oldest.

    The counts are taken from the LIVE database rather than the copy, which is
    the same data by construction and one fewer connection to open.
    """
    live = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(destination)
        try:
            live.backup(target)
            target.commit()
        finally:
            target.close()
        live.row_factory = sqlite3.Row
        return {
            "jobs": _count(live, "SELECT COUNT(*) AS n FROM job"),
            "applications": _count(live, "SELECT COUNT(*) AS n FROM job_application"),
            "claims": _count(
                live,
                "SELECT COUNT(*) AS n FROM verified_claim"
                " WHERE superseded_by_id IS NULL AND verified = 1",
            ),
            "pending_proposals": _count(
                live, "SELECT COUNT(*) AS n FROM cv_proposal WHERE decision = 'PENDING'"
            ),
            "notes": _count(
                live,
                "SELECT COUNT(*) AS n FROM job_application"
                " WHERE notes IS NOT NULL AND TRIM(notes) <> ''",
            ),
        }
    finally:
        live.close()


def _manifest(
    result: BackupResult, database_name: str, profile: dict[str, str] | None = None
) -> str:
    """What this archive is, for whoever opens it in a year.

    Including what is deliberately ABSENT. A restore that fails because a key
    is missing, with nothing saying the key was never there, is an hour spent
    looking for a bug that does not exist.
    """
    return json.dumps(
        {
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            # ONE PERSON'S DATA. A backup holds one local profile: its
            # database and its private settings. It never mixes profiles.
            "scope": "one local profile",
            "profile": profile or {"id": None, "label": None},
            "database": database_name,
            "jobs": result.jobs,
            "applications": result.applications,
            # What she wrote, counted apart from what was collected. Postings
            # can be collected again; a confirmed career fact cannot.
            "confirmed_claims": result.claims,
            "cv_proposals_awaiting_review": result.pending_proposals,
            "application_notes": result.notes,
            "config_files": sorted(result.config_files),
            "not_included": {
                "other_profiles": "Each local profile is backed up on its own.",
                "resume_tailor": (
                    "Resume Tailor keeps its own per-candidate backup; its workspace "
                    "is not in this archive."
                ),
            },
            "contains_public_jobs": (
                "Yes: until the shared catalogue exists, a profile's database also holds "
                "the job postings it collected. See docs/MULTI_PROFILE.md."
            ),
            "excluded": {
                "credentials": sorted(NEVER_COPIED),
                "why": (
                    "A backup gets emailed, copied to a stick and forgotten. A "
                    "credential inside one outlives every intention about it. "
                    "Re-create .env by hand after restoring; nothing else here "
                    "depends on it."
                ),
            },
            "restore": (
                "Unzip, put the .db back where your --db flag points, and copy "
                "the config files into config/. Then run `career-agent doctor`."
            ),
        },
        indent=2,
    )


def create_backup(
    *,
    db: Path,
    config_dir: Path,
    destination: Path,
    staging: Path,
    profile: dict[str, str] | None = None,
) -> BackupResult:
    """Write one archive holding the corpus and the private configuration.

    `staging` is a caller-supplied scratch directory, so this function creates
    no temporary state of its own and a test can see everything it did.
    """
    result = BackupResult(path=destination)
    staging.mkdir(parents=True, exist_ok=True)

    snapshot = staging / db.name
    counted = _snapshot(db, snapshot)
    result.jobs = counted["jobs"]
    result.applications = counted["applications"]
    result.claims = counted["claims"]
    result.pending_proposals = counted["pending_proposals"]
    result.notes = counted["notes"]
    result.database_bytes = snapshot.stat().st_size

    copied: list[Path] = []
    for pattern in CONFIG_GLOBS:
        for source in sorted(config_dir.glob(pattern)):
            if source.name in NEVER_COPIED:
                result.skipped_secrets.append(source.name)
                continue
            target = staging / "config" / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            copied.append(target)
            result.config_files.append(source.name)

    # Belt and braces: whatever the globs above matched, a file named like a
    # credential does not enter the archive. The globs are `*.local.yaml`
    # today; a later edit that widens them must not be able to widen this.
    for stray in sorted(config_dir.iterdir()):
        if stray.name in NEVER_COPIED:
            result.skipped_secrets.append(stray.name)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.write(snapshot, snapshot.name)
        for target in copied:
            archive.write(target, f"config/{target.name}")
        archive.writestr("MANIFEST.json", _manifest(result, snapshot.name, profile))

    result.skipped_secrets = sorted(set(result.skipped_secrets))
    return result


def contents(archive_path: Path) -> list[str]:
    """Every name inside an archive. Used by the command to report, and by the
    tests to assert that a secret is not among them."""
    with zipfile.ZipFile(archive_path) as archive:
        return sorted(archive.namelist())

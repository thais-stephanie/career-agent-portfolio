"""The one automatic backup: a profile's database, before a migration changes it.

WHO MAKES BACKUPS, AND WHICH ARE THESE
--------------------------------------
Career Agent itself makes very few copies, and this module is the only one that
copies a DATABASE on its own:

* saving a setting leaves a `<file>.backup` beside the small configuration file
  it rewrote (one generation, a few kilobytes, never a database);
* collecting jobs, editing skills or preferences, and opening the app copy no
  database;
* `career-agent backup` writes an archive when the person asks for one, and
  moving the retired Resume helper's files makes its own verified backup;
* this module: before an upgrade applies migrations to an existing profile, so a
  schema change that goes wrong can be undone.

The folders `data/backups/pre-*.zip` and the like come from operator procedures,
not from the app. This module never creates, lists as its own, prunes or touches
anything outside `data/backups/auto/`.

THE POLICY
----------
* WHEN: only when migrations are pending on a profile database that already
  holds data. An ordinary opening, a collection, a preference or a skill edit
  never copies anything.
* WHAT: the profile database and its private settings (the same archive as
  `career-agent backup`; a credential file is never included). NOT the shared job
  catalogue: it holds public, re-collectable data, a copy is hundreds of
  megabytes, and `career-agent backup --catalogue` makes one on request.
* NO EQUIVALENT COPIES: one backup per profile per schema step (from version X
  to version Y). Opening again, or a failed upgrade retried, makes no second.
* HOW MANY: the newest 3 per profile. How big: the total of automatic backups
  stays under 2 GiB; if a new one would not fit after pruning, none is written
  and the manifest says why. The upgrade itself is never blocked by this.
* PROOF: the archive is read back (zip test) and the snapshot inside is
  integrity-checked BEFORE the manifest lists it; older ones are pruned only
  after the new one is verified, and the newest verified copy is never deleted.
* ONLY WHAT IT MADE: pruning touches files listed in `auto/manifest.json` and
  nothing else. Manual backups, exports, recovery copies and every older file
  are protected by construction.
* VISIBLE: Settings, Backup lists the count, total size and date of each.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

AUTO_DIR = "auto"
MANIFEST = "manifest.json"
KEEP_PER_PROFILE = 3
MAX_TOTAL_BYTES = 2 * 1024**3
KIND = "before-upgrade"


def folder(data_dir: Path) -> Path:
    return data_dir / "backups" / AUTO_DIR


def read_manifest(data_dir: Path) -> dict[str, Any]:
    path = folder(data_dir) / MANIFEST
    try:
        found = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(found, dict) and isinstance(found.get("entries"), list):
            return found
    except (OSError, ValueError):
        pass
    return {"version": 1, "entries": [], "skipped": []}


def _write_manifest(data_dir: Path, manifest: dict[str, Any]) -> None:
    target = folder(data_dir)
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / (MANIFEST + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(temporary, target / MANIFEST)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _verified(archive: Path, staged: Path) -> bool:
    """The archive reads back whole, and the snapshot inside is a sound database."""
    try:
        with zipfile.ZipFile(archive) as opened:
            if opened.testzip() is not None:
                return False
        conn = sqlite3.connect(f"file:{staged}?mode=ro", uri=True)
        try:
            return conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            conn.close()
    except (OSError, zipfile.BadZipFile, sqlite3.Error):
        return False


def listing(data_dir: Path) -> dict[str, Any]:
    """What is there, for Settings: only entries whose file still exists."""
    root = folder(data_dir)
    rows = []
    for entry in read_manifest(data_dir)["entries"]:
        path = root / str(entry.get("file", ""))
        if path.is_file():
            rows.append({**entry, "bytes": path.stat().st_size})
    rows.sort(key=lambda e: str(e.get("created_at", "")), reverse=True)
    manifest = read_manifest(data_dir)
    return {
        "count": len(rows),
        "total_bytes": sum(int(r["bytes"]) for r in rows),
        "entries": rows,
        "skipped": manifest.get("skipped", [])[-3:],
        "policy": {
            "keep_per_profile": KEEP_PER_PROFILE,
            "max_total_bytes": MAX_TOTAL_BYTES,
            "when": KIND,
        },
    }


def backup_before_upgrade(
    data_dir: Path,
    *,
    profile_id: str,
    profile_label: str,
    db: Path,
    config_dir: Path,
    pending: list[str],
    schema_now: int,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Back the profile database up before `pending` migrations are applied.

    Returns the manifest entry written, or None when nothing was needed or none
    could be made (the reason is recorded in the manifest). Never raises: the
    upgrade must not depend on this."""
    from career_agent.storage.backup import create_backup

    if not pending or not db.exists() or schema_now <= 0:
        return None
    moment = now or datetime.now(UTC)
    key = f"{profile_id}:{schema_now}->{pending[-1]}"
    manifest = read_manifest(data_dir)
    root = folder(data_dir)
    try:
        for entry in manifest["entries"]:
            if entry.get("key") == key and (root / str(entry.get("file", ""))).is_file():
                return None  # an equivalent, verified copy already exists
        root.mkdir(parents=True, exist_ok=True)
        stamp = moment.strftime("%Y%m%dT%H%M%SZ")
        name = f"auto-{KIND}-{profile_id}-{schema_now}-to-{pending[-1]}-{stamp}.zip"
        final = root / name
        with tempfile.TemporaryDirectory(prefix="auto-backup-") as scratch:
            staging = Path(scratch)
            partial = root / (name + ".partial")
            make = create_backup(
                db=db,
                config_dir=config_dir,
                destination=partial,
                staging=staging,
                profile={"id": profile_id, "label": profile_label},
            )
            size = make.total_bytes
            if not _verified(partial, staging / db.name):
                partial.unlink(missing_ok=True)
                _skip(data_dir, manifest, key, "NOT_VERIFIED", moment)
                return None
            kept = _after_pruning(data_dir, manifest, profile_id, size, dry=True)
            if kept is None:
                partial.unlink(missing_ok=True)
                _skip(data_dir, manifest, key, "TOO_LARGE", moment)
                return None
            os.replace(partial, final)
        entry = {
            "file": name,
            "key": key,
            "kind": KIND,
            "profile_id": profile_id,
            "profile_label": profile_label,
            "schema_before": schema_now,
            "schema_after": pending[-1],
            "created_at": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bytes": size,
            "sha256": _sha256(final),
            "verified": True,
        }
        manifest["entries"].append(entry)
        # The new copy is verified and listed; only now may older ones go.
        _after_pruning(data_dir, manifest, profile_id, 0, dry=False)
        _write_manifest(data_dir, manifest)
        return entry
    except Exception as exc:  # noqa: BLE001 - an upgrade does not depend on its backup
        _skip(data_dir, manifest, key, f"ERROR:{type(exc).__name__}", moment)
        return None


def _skip(data_dir: Path, manifest: dict[str, Any], key: str, why: str, moment: datetime) -> None:
    manifest.setdefault("skipped", []).append(
        {"key": key, "why": why, "at": moment.strftime("%Y-%m-%dT%H:%M:%SZ")}
    )
    manifest["skipped"] = manifest["skipped"][-20:]
    with contextlib.suppress(OSError):
        _write_manifest(data_dir, manifest)


def _after_pruning(
    data_dir: Path, manifest: dict[str, Any], profile_id: str, incoming: int, *, dry: bool
) -> list[dict[str, Any]] | None:
    """Which entries would remain (and with `dry=False`, delete the rest).

    Only files named in the manifest and sitting in `auto/` are ever removed,
    oldest first, never the newest verified copy of any profile. Returns None
    when `incoming` bytes cannot fit even then."""
    root = folder(data_dir)
    entries = sorted(
        (e for e in manifest["entries"] if (root / str(e.get("file", ""))).is_file()),
        key=lambda e: str(e.get("created_at", "")),
    )
    mine = [e for e in entries if e.get("profile_id") == profile_id]
    newest = {e["file"] for e in (mine[-1:] if mine else [])}
    doomed: list[dict[str, Any]] = [
        e
        for e in mine[
            : max(0, len(mine) - (KEEP_PER_PROFILE if dry is False else KEEP_PER_PROFILE - 1))
        ]
        if e["file"] not in newest
    ]
    remaining = [e for e in entries if e not in doomed]
    total = sum((root / e["file"]).stat().st_size for e in remaining) + incoming
    # Still over the cap: older copies of ANY profile go first, but the newest
    # verified copy of each profile is always kept.
    protected = {
        max((e for e in entries if e.get("profile_id") == p), key=lambda e: str(e["created_at"]))[
            "file"
        ]
        for p in {e.get("profile_id") for e in entries}
    }
    for entry in remaining[:]:
        if total <= MAX_TOTAL_BYTES:
            break
        if entry["file"] in protected:
            continue
        doomed.append(entry)
        remaining.remove(entry)
        total -= (root / entry["file"]).stat().st_size
    if total > MAX_TOTAL_BYTES:
        return None
    if not dry:
        for entry in doomed:
            (root / entry["file"]).unlink(missing_ok=True)
        manifest["entries"] = [e for e in manifest["entries"] if e not in doomed]
    return remaining

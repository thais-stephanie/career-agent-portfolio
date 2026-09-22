"""Plan or explicitly prune superseded scores. Never migrates or runs VACUUM.

Execution requires a reviewed token, an existing matching backup and a new
receipt path. Production execution additionally requires owner authorization
outside this command. Payload retention is deliberately unsupported.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from career_agent.config.search_config import load_search_config
from career_agent.storage.mvp_repo import MatchRepo
from career_agent.storage.retention import RetentionRefused, execute_history, plan_history


def connection(path: Path, mode: str = "ro") -> sqlite3.Connection:
    conn = sqlite3.connect(
        path.resolve().as_uri() + f"?mode={mode}", uri=True, isolation_level=None
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    # Offline maintenance scans benefit from a bounded index cache. This is
    # connection-local; it does not change production/application settings.
    conn.execute("PRAGMA cache_size=-65536")
    if mode == "ro":
        conn.execute("PRAGMA query_only=ON")
    return conn


def verified_file_backup(source: Path, backup: Path) -> None:
    """Require a quiescent, byte-identical restore point, not just equal counts."""
    before = [p.stat() for p in (source, backup)]
    digests = []
    for path in (source, backup):
        wal = Path(str(path) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise RetentionRefused("stop writers and make a checkpointed backup first")
        with path.open("rb") as stream:
            digests.append(hashlib.file_digest(stream, "sha256").hexdigest())
    for path, stat in zip((source, backup), before, strict=True):
        after = path.stat()
        wal = Path(str(path) + "-wal")
        if (stat.st_size, stat.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or (
            wal.exists() and wal.stat().st_size
        ):
            raise RetentionRefused("database changed during backup verification")
    if digests[0] != digests[1]:
        raise RetentionRefused("backup is not byte-identical to the checkpointed database")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, default=Path("config"))
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--plan", action="store_true", help="default: read only")
    action.add_argument("--execute", action="store_true")
    parser.add_argument("--expect-token")
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--receipt", type=Path)
    args = parser.parse_args(argv)
    config, _ = load_search_config(args.config_dir)
    conn = connection(args.db)
    try:
        if MatchRepo(conn).digests_for(config.config_id, config.config_version) != {config.digest}:
            raise RetentionRefused("current scores do not match the loaded configuration")
        plan = plan_history(conn, config.config_id, config.config_version)
    finally:
        conn.close()
    report = {
        "database": str(args.db.resolve()),
        **asdict(plan),
        "token": plan.token,
        "bytes_note": (
            "Exact result_json bytes only; excludes other columns/indexes. "
            "Physical reclamation requires separate VACUUM."
        ),
        "payload_policy": "P0: keep all",
        "executed": False,
    }
    print(json.dumps(report, indent=2), flush=True)
    if not args.execute:
        return 0
    if args.expect_token != plan.token or not args.backup or not args.receipt:
        raise RetentionRefused("execution requires matching --expect-token, --backup and --receipt")
    if args.db.resolve() == args.backup.resolve() or os.path.samefile(args.db, args.backup):
        raise RetentionRefused("backup must be a separate file")
    verified_file_backup(args.db, args.backup)
    backup = connection(args.backup)
    try:
        if plan_history(backup, config.config_id, config.config_version) != plan:
            raise RetentionRefused("backup does not match the reviewed population")
        if backup.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise RetentionRefused("backup quick_check failed")
    finally:
        backup.close()
    # Exclusive create: never overwrite evidence of an earlier attempt.
    with args.receipt.open("x", encoding="utf-8") as receipt:
        receipt.write(
            json.dumps(
                {
                    **report,
                    "state": "prepared",
                    "at": datetime.now(UTC).isoformat(),
                    "backup": str(args.backup.resolve()),
                }
            )
            + "\n"
        )
        receipt.flush()
        os.fsync(receipt.fileno())
        conn = connection(args.db, "rw")
        try:
            removed = execute_history(conn, plan)
        except BaseException:
            receipt.write(
                json.dumps({"state": "failed_or_interrupted", "at": datetime.now(UTC).isoformat()})
                + "\n"
            )
            receipt.flush()
            os.fsync(receipt.fileno())
            raise
        finally:
            conn.close()
        receipt.write(
            json.dumps(
                {
                    "state": "committed",
                    "at": datetime.now(UTC).isoformat(),
                    "removed": removed,
                    "token": plan.token,
                }
            )
            + "\n"
        )
        receipt.flush()
        os.fsync(receipt.fileno())
    print(f"Deleted {removed} score rows; no payload deletion, no VACUUM.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

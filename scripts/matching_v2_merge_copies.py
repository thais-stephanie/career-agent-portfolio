"""Join bounded copies into one file a blind package is drawn from.

    uv run python scripts/matching_v2_merge_copies.py --out MERGED.db A.db B.db ...

Each input is a copy written by `matching_v2_sample.py` or
`matching_v2_sample_legacy.py`. Rows are copied table by table over the
columns the tables share (a legacy copy's `job_match` is a stub), and a
posting present in two inputs is refused rather than silently doubled.
Writes MERGED.db only; prints counts. Opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

TABLES = ("job", "job_raw", "job_provider_payload", "job_discovery_source", "company", "job_match")


def _columns(conn: sqlite3.Connection, schema: str, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA {schema}.table_info({table})")]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("inputs", nargs="+", type=Path)
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; give a new file")
    out = sqlite3.connect(f"file:{a.out.as_posix()}", uri=True)
    first = a.inputs[0]
    out.execute(f"ATTACH DATABASE 'file:{first.as_posix()}?mode=ro' AS src")
    for table in TABLES:
        out.execute(f"CREATE TABLE {table} AS SELECT * FROM src.{table}")
    out.commit()
    out.execute("DETACH DATABASE src")
    for other in a.inputs[1:]:
        out.execute(f"ATTACH DATABASE 'file:{other.as_posix()}?mode=ro' AS src")
        clash = out.execute("SELECT count(*) FROM src.job WHERE id IN (SELECT id FROM job)")
        if clash.fetchone()[0]:
            raise SystemExit(f"{other} shares postings with an earlier input")
        for table in TABLES:
            shared = [c for c in _columns(out, "main", table) if c in _columns(out, "src", table)]
            cols = ", ".join(shared)
            if table == "job_raw":
                where = " WHERE content_hash NOT IN (SELECT content_hash FROM job_raw)"
            elif table == "company":
                where = " WHERE id NOT IN (SELECT id FROM company)"
            else:
                where = ""
            out.execute(f"INSERT INTO {table} ({cols}) SELECT {cols} FROM src.{table}{where}")
        out.commit()
        out.execute("DETACH DATABASE src")
    for table in TABLES:
        print(table, out.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    out.close()


if __name__ == "__main__":
    main()

"""Print what the Workable shadow observed. Opens the catalogue read-only.

Usage: python scripts/workable_shadow_report.py data/shared/catalogue.db
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

from career_agent.pipeline.workable_presence import shadow_report


def main(path: str) -> None:
    conn = sqlite3.connect(f"{Path(path).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        print(json.dumps(shadow_report(conn), indent=2))
    finally:
        conn.close()


if __name__ == "__main__":
    main(sys.argv[1])

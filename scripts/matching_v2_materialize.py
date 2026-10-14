"""Materialise the shared facts Phase C reads, in priority order, bounded.

    uv run python scripts/matching_v2_materialize.py --db DB --config-dir DIR [--max N]
        [--budget-seconds S] [--job-id ID ...]

Fills `job_features` for open postings: the ones the profile's stored score
says are eligible first, then by stored score, then the rest
(`phase_c.priority_order`), or exactly the `--job-id`s given (on demand, a
posting about to be shown). Stops at `--max` postings or after
`--budget-seconds`, whichever comes first; a fresh row is skipped, so running
it again resumes where it stopped. Each posting is its own write; the legacy
state stays usable throughout and nothing reads these rows unless
CAREER_AGENT_ELIGIBILITY_SOURCE=v2. Writes only the catalogue's
`job_features`; opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.matching_v2 import phase_c  # noqa: E402
from career_agent.pipeline.facts import job_facts  # noqa: E402
from career_agent.pipeline.rescore import _rows_for  # noqa: E402
from career_agent.storage.db import connect  # noqa: E402
from career_agent.storage.repositories import (  # noqa: E402
    DiscoverySourceRepo,
    ProviderPayloadRepo,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    p.add_argument("--max", type=int)
    p.add_argument("--budget-seconds", type=float)
    p.add_argument("--job-id", nargs="*", default=[])
    a = p.parse_args()
    conn = connect(a.db)
    config, _ = load_search_config(a.config_dir)
    ids = list(a.job_id) or phase_c.priority_order(conn, config)
    if a.max is not None:
        ids = ids[: a.max]
    deadline = None if a.budget_seconds is None else time.monotonic() + a.budget_seconds
    started = time.perf_counter()
    total = {"written": 0, "fresh": 0, "stopped": 0}
    for i in range(0, len(ids), 200):
        page = ids[i : i + 200]
        rows = _rows_for(conn, page)
        payloads = ProviderPayloadRepo(conn).latest_for_jobs(page)
        sightings = DiscoverySourceRepo(conn).sightings_for_jobs(page)
        jobs = [
            (
                job_facts(
                    job_id=str(r["id"]),
                    title=r["title"] or "",
                    description=r["description_text"] or "",
                    location_raw=r["location_raw"],
                    posted_at=r["posted_at"],
                    provider=r["provider"],
                    payload=payloads.get(str(r["id"])),
                    sightings=sightings.get(str(r["id"]), ()),
                    company_country=r["hq_country"],
                ),  # fmt: skip
                str(r["content_hash"]),
            )
            for r in rows
            if r["content_hash"] is not None
        ]
        done = phase_c.materialize(
            conn,
            config,
            jobs,
            should_stop=lambda: deadline is not None and time.monotonic() > deadline,
        )
        for k, v in done.items():
            total[k] = total.get(k, 0) + v
        if done.get("stopped"):
            break
    print(f"postings {len(ids)}: {total} in {time.perf_counter() - started:.1f} s")


if __name__ == "__main__":
    main()

"""The current reader and v2 on the same real postings, from a bounded copy.

    uv run python scripts/matching_v2_parity.py --source CATALOGUE --config-dir DIR \
        --scratch OUT.db --limit 300

Copies `limit` open postings (their rows, adverts, payloads, sightings and
companies) from `--source`, opened READ-ONLY, into `--scratch`, a new file;
then scores each with `match_job` and with v2 under the configuration in
`--config-dir`, and prints how the two agree. Prints counts and posting ids
only, never an employer's text. Opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import random
import sqlite3
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.match.engine import match_job  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402
from career_agent.pipeline.facts import job_facts  # noqa: E402
from career_agent.storage.repositories import (  # noqa: E402
    DiscoverySourceRepo,
    ProviderPayloadRepo,
)


def bounded_copy(source: Path, scratch: Path, limit: int, seed: int) -> list[str]:
    if scratch.exists():
        raise SystemExit(f"{scratch} exists; give a new file")
    out = sqlite3.connect(f"file:{scratch.as_posix()}", uri=True)  # URI, so ATTACH can be read-only
    out.execute(f"ATTACH DATABASE 'file:{source.as_posix()}?mode=ro' AS src")
    ids = [
        r[0]
        for r in out.execute(
            "SELECT j.id FROM src.job j JOIN src.job_raw r ON r.content_hash = j.content_hash"
            " WHERE j.closed_at IS NULL AND length(r.description_text) > 400 ORDER BY j.id"
        )
    ]
    picked = sorted(random.Random(seed).sample(ids, min(limit, len(ids))))
    out.execute("CREATE TEMP TABLE picked (id TEXT PRIMARY KEY)")
    out.executemany("INSERT INTO picked VALUES (?)", [(i,) for i in picked])
    out.execute("CREATE TABLE job AS SELECT * FROM src.job WHERE id IN (SELECT id FROM picked)")
    out.execute(
        "CREATE TABLE job_raw AS SELECT * FROM src.job_raw WHERE content_hash IN"
        " (SELECT content_hash FROM job)"
    )
    for table in ("job_provider_payload", "job_discovery_source"):
        out.execute(
            f"CREATE TABLE {table} AS SELECT * FROM src.{table}"
            " WHERE job_id IN (SELECT id FROM picked)"
        )
    out.execute(
        "CREATE TABLE company AS SELECT * FROM src.company WHERE id IN (SELECT company_id FROM job)"
    )
    out.commit()
    out.execute("DETACH DATABASE src")
    out.close()
    return picked


def main() -> None:
    args = argparse.ArgumentParser()
    args.add_argument("--source", type=Path, required=True)
    args.add_argument("--config-dir", type=Path, required=True)
    args.add_argument("--scratch", type=Path, required=True)
    args.add_argument("--limit", type=int, default=300)
    args.add_argument("--seed", type=int, default=7)
    a = args.parse_args()
    started = time.perf_counter()
    ids = bounded_copy(a.source, a.scratch, a.limit, a.seed)
    print(f"bounded copy: {len(ids)} postings in {time.perf_counter() - started:.1f} s")
    config, _ = load_search_config(a.config_dir)
    conn = sqlite3.connect(a.scratch)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT j.id, j.title, j.location_raw, j.posted_at, j.provider, j.content_hash,"
        " r.description_text, c.hq_country FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " LEFT JOIN company c ON c.id = j.company_id ORDER BY j.id"
    ).fetchall()
    payloads = ProviderPayloadRepo(conn).latest_for_jobs(ids)
    sightings = DiscoverySourceRepo(conn).sightings_for_jobs(ids)
    headings = (tuple(config.prominence.primary_headings),
                tuple(config.prominence.secondary_headings))  # fmt: skip
    same_status = 0
    moved: Counter = Counter()
    diffs: list[int] = []
    band_moves = 0
    t_cur = t_v2 = 0.0
    biggest: list[tuple[int, str]] = []
    for row in rows:
        facts = job_facts(
            job_id=row["id"], title=row["title"] or "", description=row["description_text"],
            location_raw=row["location_raw"], posted_at=row["posted_at"],
            provider=row["provider"], payload=payloads.get(row["id"]),
            sightings=sightings.get(row["id"], ()), company_country=row["hq_country"],
        )  # fmt: skip
        s = time.perf_counter()
        current = match_job(config, facts, computed_at="parity")
        t_cur += time.perf_counter() - s
        s = time.perf_counter()
        features = feat.extract(facts, row["content_hash"], headings)
        v2 = pm.match(config, facts, features).result
        t_v2 += time.perf_counter() - s
        if current.eligibility_status == v2.eligibility_status:
            same_status += 1
        else:
            moved[(str(current.eligibility_status), str(v2.eligibility_status))] += 1
        diffs.append(v2.match_score - current.match_score)
        band_moves += current.fit_band != v2.fit_band
        biggest.append((v2.match_score - current.match_score, row["id"]))
    n = len(rows)
    print(f"postings scored: {n}")
    print(f"eligibility identical: {same_status} of {n}; moved: {dict(moved)}")
    print(f"score v2 - current: mean {statistics.mean(diffs):+.2f}, median"
          f" {statistics.median(diffs):+.1f}, same {sum(d == 0 for d in diffs)},"
          f" higher {sum(d > 0 for d in diffs)}, lower {sum(d < 0 for d in diffs)}")  # fmt: skip
    print(f"fit band changed: {band_moves} of {n}")
    biggest.sort()
    print("largest drops (ids):", [f"{d:+d} {i}" for d, i in biggest[:5]])
    print("largest rises (ids):", [f"{d:+d} {i}" for d, i in biggest[-5:]])
    print(f"ms per posting, one profile: current {1000 * t_cur / n:.1f}, v2 {1000 * t_v2 / n:.1f}")


if __name__ == "__main__":
    main()

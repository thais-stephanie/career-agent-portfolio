"""Build a SYNTHETIC corpus at the volume of a real installation, for timing.

Every posting, employer and description here is invented: the 21 committed
demo postings are cloned under new ids, with "Synthetic employer NNNNN" as
the employer and the demo advert repeated to a realistic length. Nothing is
read from anybody's data. The proportions are the ones measured on a real
installation (2026-10-13): about 172,000 open postings, 10,000 employers,
133,000 distinct roles, adverts of about 6,300 characters, 4% eligible, 75%
not saying where the employer hires, 21% ruled out, and a second, older
scoring revision of 124,000 rows beside the current one.

The result has the real layout, a profile database with the shared
catalogue beside it (`<out>/data/personal.db` + `<out>/data/shared/
catalogue.db`), and a `<out>/config` the server reads.

    python scripts/synthetic_volume.py --out <empty folder> [--postings 172000]
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))


def _say(message: str, started: float) -> None:
    print(f"[{time.monotonic() - started:7.1f}s] {message}", flush=True)


def build(out: Path, postings: int, employers: int, old_rows: int) -> None:
    from career_agent.config.search_config import load_search_config
    from career_agent.pipeline.demo_seed import seed_demo
    from career_agent.runtime import RuntimeMode, stamp_identity
    from career_agent.storage import catalogue_split, search_index
    from career_agent.storage.db import connect, migrate, transaction

    started = time.monotonic()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty")
    config = out / "config"
    shutil.copytree(REPO / "config", config, ignore=shutil.ignore_patterns("*.local.*", "*.backup"))
    shutil.copyfile(config / "search.worked-example.yaml", config / "search.local.yaml")
    single = out / "staging" / "single.db"
    single.parent.mkdir(parents=True)
    conn = connect(single)
    migrate(conn)
    with transaction(conn):
        stamp_identity(conn, RuntimeMode.PERSONAL, "Synthetic volume")
    search, _ = load_search_config(config)
    seed_demo(conn, search, source=REPO / "evaluation" / "demo" / "demo_postings.yaml")
    _say("demo seeded", started)

    conn.row_factory = sqlite3.Row
    jobs = [dict(r) for r in conn.execute("SELECT * FROM job ORDER BY id")]
    raws = {r["content_hash"]: dict(r) for r in conn.execute("SELECT * FROM job_raw")}
    matches = {r["job_id"]: dict(r) for r in conn.execute("SELECT * FROM job_match")}
    company = dict(conn.execute("SELECT * FROM company ORDER BY id LIMIT 1").fetchone())
    today = datetime.now(UTC).date()
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    def insert(table: str, rows: list[dict]) -> None:
        if not rows:
            return
        cols = list(rows[0])
        conn.executemany(
            f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
            [tuple(r[c] for c in cols) for r in rows],
        )

    with transaction(conn):
        insert(
            "company",
            [
                {
                    **company,
                    "id": f"syn-co-{n:05d}",
                    "slug": f"synthetic-employer-{n:05d}",
                    "name": f"Synthetic employer {n:05d}",
                    "website": None,
                    "canonical_domain": None,
                    # About 1% carry a curated headquarters country, as measured.
                    "hq_country": "US" if n % 100 == 0 else None,
                }
                for n in range(employers)
            ],
        )
    _say(f"{employers} employers", started)

    statuses = ("VERIFIED_ELIGIBLE",) * 4 + ("VERIFIED_NOT_ELIGIBLE",) * 21
    statuses += ("UNRESOLVED",) * 75
    batch: dict[str, list[dict]] = {"job_raw": [], "job": [], "job_match": []}

    def flush() -> None:
        with transaction(conn):
            for table in ("job_raw", "job", "job_match"):
                insert(table, batch[table])
                batch[table].clear()

    for i in range(postings):
        template = jobs[i % len(jobs)]
        raw = raws[template["content_hash"]]
        text = str(raw["description_text"] or "")
        body = (text + "\n\n") * max(1, 6300 // max(len(text), 1)) + f"Reference {i}."
        content_hash = hashlib.sha256(body.encode()).hexdigest()
        job_id = f"syn-{i:06d}"
        posted = (today - timedelta(days=i % 200)).isoformat() if i % 9 else None
        batch["job_raw"].append(
            {
                **raw,
                "content_hash": content_hash,
                "description_text": body,
                "description_html": None,
                "byte_length": len(body.encode()),
            }
        )
        batch["job"].append(
            {
                **template,
                "id": job_id,
                "company_id": f"syn-co-{i % employers:05d}",
                "external_id": job_id,
                "url": f"https://example.invalid/synthetic/{job_id}",
                "title": f"{template['title']} {(i // 4) % 40000}",
                "posted_at": posted,
                "content_hash": content_hash,
                "first_seen_at": now,
                "last_seen_at": now,
                "closed_at": None,
            }
        )
        match = matches.get(template["id"])
        if match is not None:
            score = (i * 37) % 100
            band = "STRONG" if score >= 75 else "GOOD" if score >= 55 else "MODERATE"
            row = {
                **match,
                "id": f"syn-m-{i:06d}",
                "job_id": job_id,
                "content_hash": content_hash,
                "match_score": score,
                "fit_band": band if score >= 35 else "WEAK",
                "screening_state": "NOT_BLOCKED",
                "eligibility_status": statuses[i % len(statuses)],
            }
            batch["job_match"].append(row)
            if i < old_rows:
                batch["job_match"].append(
                    {
                        **row,
                        "id": f"syn-o-{i:06d}",
                        "config_version": int(row["config_version"]) - 2,
                    }
                )
        if len(batch["job"]) >= 5000:
            flush()
            _say(f"{i + 1} postings", started)
    flush()
    with transaction(conn):
        conn.execute("DELETE FROM job_dirty")
    _say("postings written; rebuilding the search index", started)
    search_index.rebuild(conn)
    conn.close()
    _say("splitting into profile + catalogue", started)
    profile, catalogue, _identity = catalogue_split.build(single, out / "staging" / "split")
    data = out / "data"
    (data / "shared").mkdir(parents=True)
    shutil.move(str(profile), data / "personal.db")
    shutil.move(str(catalogue), data / "shared" / "catalogue.db")
    shutil.rmtree(out / "staging")
    _say(f"done: {data / 'personal.db'}", started)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--postings", type=int, default=172_000)
    parser.add_argument("--employers", type=int, default=10_000)
    parser.add_argument("--old-rows", type=int, default=124_000)
    args = parser.parse_args()
    build(args.out, args.postings, args.employers, args.old_rows)


if __name__ == "__main__":
    main()

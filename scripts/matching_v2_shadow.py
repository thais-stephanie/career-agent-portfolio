"""Shadow mode over a bounded copy: v2 beside the stored production answers.

    uv run python scripts/matching_v2_shadow.py --sample SAMPLE.db \
        --config-dir DIR --out REPORT.json

`--sample` is a copy made by `scripts/matching_v2_sample.py` (postings and
the profile's stored `job_match` rows, readers-N). For every posting:

1. the posting's shared facts are read once (v2);
2. this build's production reader scores it again and is compared with the
   STORED answer: the refactors behind v2 must leave production identical;
3. `matching_v2.shadow.compare` explains, component by component, where v2
   and the stored production answer differ.

Writes REPORT.json (every diverging posting with its reasons and the
evidence quotes; a local file, never committed) and prints a summary of
counts only, never an employer's text. Opens the sample read-only, writes
nothing else, opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.match.engine import match_job  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import shadow  # noqa: E402
from career_agent.pipeline.facts import job_facts  # noqa: E402
from career_agent.storage.mvp_repo import deserialise_match  # noqa: E402
from career_agent.storage.repositories import (  # noqa: E402
    DiscoverySourceRepo,
    ProviderPayloadRepo,
)


def postings(conn: sqlite3.Connection, limit: int | None):
    rows = conn.execute(
        "SELECT j.id, j.title, j.location_raw, j.posted_at, j.provider, j.content_hash,"
        " r.description_text, c.hq_country, m.result_json FROM job j"
        " JOIN job_raw r ON r.content_hash = j.content_hash"
        " LEFT JOIN company c ON c.id = j.company_id"
        " JOIN job_match m ON m.job_id = j.id ORDER BY j.id"
    ).fetchall()
    rows = rows[:limit] if limit else rows
    ids = [r["id"] for r in rows]
    payloads = ProviderPayloadRepo(conn).latest_for_jobs(ids)
    sightings = DiscoverySourceRepo(conn).sightings_for_jobs(ids)
    for r in rows:
        job = job_facts(
            job_id=r["id"], title=r["title"] or "", description=r["description_text"],
            location_raw=r["location_raw"], posted_at=r["posted_at"], provider=r["provider"],
            payload=payloads.get(r["id"]), sightings=sightings.get(r["id"], ()),
            company_country=r["hq_country"],
        )  # fmt: skip
        yield job, r["content_hash"], r["provider"], r["result_json"]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--limit", type=int)
    a = p.parse_args()
    config, _ = load_search_config(a.config_dir)
    headings = (
        tuple(config.prominence.primary_headings),
        tuple(config.prominence.secondary_headings),
    )
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    started = time.perf_counter()
    n = 0
    production = Counter()  # this build's production reader vs the stored answer
    moved = Counter()
    by_component: Counter = Counter()
    by_reason: Counter = Counter()
    uncertain: Counter = Counter()
    by_lang: dict[str, Counter] = defaultdict(Counter)
    by_provider: dict[str, Counter] = defaultdict(Counter)
    deltas: list[int] = []
    report = []
    for job, content_hash, provider, stored_json in postings(conn, a.limit):
        n += 1
        stored = deserialise_match(stored_json)
        now = match_job(config, job, computed_at="shadow")
        production["eligibility"] += now.eligibility_status == stored.eligibility_status
        production["gates"] += [(g.gate, g.result) for g in now.gates] == [
            (g.gate, g.result) for g in stored.gates
        ]
        production["score"] += now.match_score == stored.match_score
        facts = feat.extract(job, content_hash, headings, negation=config.negation)
        row = shadow.compare(config, job, facts, current=stored)
        lang = facts.language
        by_lang[lang]["n"] += 1
        by_provider[provider]["n"] += 1
        moved["eligibility_same"] += row.current_eligibility == row.v2_eligibility
        moved["score_same"] += row.current_score == row.v2_score
        moved["band_same"] += row.current_band == row.v2_band
        by_lang[lang]["score_same"] += row.current_score == row.v2_score
        by_provider[provider]["score_same"] += row.current_score == row.v2_score
        deltas.append(row.v2_score - row.current_score)
        uncertain.update(row.uncertain)
        for c in row.components:
            by_component[c.component_id] += 1
            for s in c.signals:
                by_reason[f"{s.change}:{s.reason}"] += 1
        if not row.same:
            report.append(row.as_dict())
    elapsed = time.perf_counter() - started
    a.out.write_text(
        json.dumps(
            {
                "postings": n,
                "production_vs_stored": dict(production),
                "v2_vs_production": dict(moved),
                "components": dict(by_component),
                "reasons": dict(by_reason),
                "uncertain": dict(uncertain),
                "languages": {k: dict(v) for k, v in by_lang.items()},
                "providers": {k: dict(v) for k, v in by_provider.items()},
                "deltas": deltas,
                "rows": report,
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(f"postings: {n} in {elapsed:.0f} s")
    print(f"this build's production reader vs the stored answer: {dict(production)}")
    print(f"v2 vs production: {dict(moved)}")
    higher = sum(d > 0 for d in deltas)
    lower = sum(d < 0 for d in deltas)
    print(f"score: higher {higher}, lower {lower}, mean {sum(deltas) / max(n, 1):+.2f}")
    print(f"components that moved: {dict(by_component.most_common())}")
    print("reasons:")
    for reason, count in by_reason.most_common():
        print(f"  {count:5d}  {reason}")
    print(f"uncertain evidence shown, by cause: {dict(uncertain.most_common())}")
    for lang, c in sorted(by_lang.items()):
        print(f"  language {lang}: {c['score_same']} of {c['n']} scores identical")
    print(f"report: {a.out}")


if __name__ == "__main__":
    main()

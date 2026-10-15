"""A bounded copy of postings from an OLDER corpus that the catalogue lacks.

    uv run python scripts/matching_v2_sample_legacy.py --legacy OLD.db \
        --catalogue CATALOGUE --out OUT.db --size 600 --language pt --id-class 2

Portuguese ran out in the catalogue's id classes once its Brazilian source
closed (2026-10-02). An older corpus kept on this machine still holds
postings that source served then. A posting is eligible only when the
catalogue has none with its id, its content hash or its URL, so no posting
ever sampled from the catalogue (and none ever labelled) can be drawn again;
`--exclude` keeps earlier legacy copies out too. Whether the posting is
still open is not asked: an evaluation of reading, not of availability.

Opens the legacy corpus IMMUTABLE and the catalogue read-only, writes a NEW
file (`job`, `job_raw`, `job_provider_payload`, `company` and a `job_match`
stub without a stored answer, which the readers do not need). `--must-match`
keeps postings whose description matches a regular expression (a targeted
development set). Prints counts only. Opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import random
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from matching_v2_sample import id_class  # noqa: E402

from career_agent.matching_v2.language import detect_language  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--legacy", type=Path, required=True)
    p.add_argument("--catalogue", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--size", type=int, default=600)
    p.add_argument("--pool", type=int, default=20000)
    p.add_argument("--seed", type=int, default=15)
    p.add_argument("--language", default="pt")
    p.add_argument("--id-class", type=int, nargs="*", choices=(0, 1, 2, 3))
    p.add_argument("--must-match", help="a regular expression the description must match")
    p.add_argument("--exclude", nargs="*", type=Path, default=[])
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; give a new file")
    legacy = sqlite3.connect(f"file:{a.legacy.as_posix()}?mode=ro&immutable=1", uri=True)
    cat = sqlite3.connect(f"file:{a.catalogue.as_posix()}?mode=ro", uri=True)
    known_ids = {r[0] for r in cat.execute("SELECT id FROM job")}
    known_hashes = {r[0] for r in cat.execute("SELECT content_hash FROM job")}
    known_urls = {r[0] for r in cat.execute("SELECT url FROM job")}
    used: set[str] = set()
    for earlier in a.exclude:
        used |= {r[0] for r in sqlite3.connect(f"file:{earlier.as_posix()}?mode=ro", uri=True)
                 .execute("SELECT id FROM job")}  # fmt: skip
    ids = [
        jid
        for jid, h, url in legacy.execute(
            "SELECT j.id, j.content_hash, j.url FROM job j JOIN job_raw r"
            " ON r.content_hash = j.content_hash WHERE length(r.description_text) > 300"
            " ORDER BY j.id"
        )
        if jid not in known_ids and h not in known_hashes and url not in known_urls
        and jid not in used
    ]  # fmt: skip
    if a.id_class:
        ids = [i for i in ids if id_class(i) in a.id_class]
    rng = random.Random(a.seed)
    pool = rng.sample(ids, min(a.pool, len(ids)))
    pattern = re.compile(a.must_match, re.IGNORECASE) if a.must_match else None
    picked: list[str] = []
    for chunk in range(0, len(pool), 900):
        part = pool[chunk : chunk + 900]
        marks = ",".join("?" * len(part))
        for job_id, text in legacy.execute(
            f"SELECT j.id, r.description_text FROM job j JOIN job_raw r"
            f" ON r.content_hash = j.content_hash WHERE j.id IN ({marks})",
            part,
        ):
            if detect_language(text[:3000]) != a.language:
                continue
            if pattern is not None and not pattern.search(text):
                continue
            picked.append(job_id)
        if len(picked) >= a.size:
            break
    picked = sorted(picked[: a.size])
    out = sqlite3.connect(f"file:{a.out.as_posix()}", uri=True)
    out.execute(f"ATTACH DATABASE 'file:{a.legacy.as_posix()}?mode=ro&immutable=1' AS src")
    out.execute("CREATE TEMP TABLE picked (id TEXT PRIMARY KEY)")
    out.executemany("INSERT INTO picked VALUES (?)", [(i,) for i in picked])
    out.execute("CREATE TABLE job AS SELECT * FROM src.job WHERE id IN (SELECT id FROM picked)")
    out.execute(
        "CREATE TABLE job_raw AS SELECT * FROM src.job_raw WHERE content_hash IN"
        " (SELECT content_hash FROM job)"
    )
    out.execute(
        "CREATE TABLE job_provider_payload AS SELECT * FROM src.job_provider_payload"
        " WHERE job_id IN (SELECT id FROM picked)"
    )
    out.execute(
        "CREATE TABLE job_discovery_source AS SELECT * FROM src.job_discovery_source"
        " WHERE job_id IN (SELECT id FROM picked)"
    )
    out.execute(
        "CREATE TABLE company AS SELECT * FROM src.company WHERE id IN (SELECT company_id FROM job)"
    )
    out.execute("CREATE TABLE job_match AS SELECT id AS job_id, NULL AS result_json FROM job")
    out.commit()
    out.execute("DETACH DATABASE src")
    print(f"eligible {len(ids)}; picked {len(picked)} in {a.language}")
    print("providers:", dict(Counter(r[0] for r in out.execute("SELECT provider FROM job"))))
    out.close()


if __name__ == "__main__":
    main()

"""A stratified, bounded copy of real postings for Matching Architecture v2.

    uv run python scripts/matching_v2_sample.py --catalogue CATALOGUE \
        --personal PERSONAL_DB --config-version 9 --out OUT.db --size 1500

Opens both sources READ-ONLY and writes a NEW file holding, for the chosen
postings only: `job`, `job_raw`, `job_provider_payload`,
`job_discovery_source`, `company`, and the profile's stored `job_match` rows
at `--config-version` (the production answer, readers-N, kept as the
comparison side; never as a label).

The sample is stratified so no single slice of the corpus decides a result:
a random pool of open postings with a body is read for its language (v2's
function-word count), then filled per language x stored-score band, with
providers left to fall where they fall. `--include` adds named posting ids
(the readers-12 regression cases). Prints counts only, never employer text.
Opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import hashlib
import random
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from career_agent.matching_v2.language import detect_language  # noqa: E402

BANDS = ((0, 20), (20, 40), (40, 60), (60, 101))
#: Share of the sample per language. Portuguese is over-weighted on purpose:
#: it is the minority the PT/EN work exists for.
LANG_SHARE = {"en": 0.5, "pt": 0.35, "other": 0.15}


def _band(score: int | None) -> int:
    for i, (lo, hi) in enumerate(BANDS):
        if score is not None and lo <= score < hi:
            return i
    return 0


def id_class(job_id: str) -> int:
    """A posting's class by its id, 0-3. The first evaluation (2026-10-14)
    used the ODD classes (1, 3) as its held-out half, so a new development
    set (class 0) and a new blind set (class 2) can never contain one of its
    postings."""
    return int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % 4


def write_copy(
    catalogue: Path, personal: Path, config_version: int, out_path: Path, picked: list[str]
) -> sqlite3.Connection:
    """A NEW file holding the chosen postings' rows and the profile's stored
    answers at `config_version`; both sources are attached READ-ONLY."""
    out = sqlite3.connect(f"file:{out_path.as_posix()}", uri=True)
    out.execute(f"ATTACH DATABASE 'file:{catalogue.as_posix()}?mode=ro' AS src")
    out.execute(f"ATTACH DATABASE 'file:{personal.as_posix()}?mode=ro' AS per")
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
    out.execute(
        "CREATE TABLE job_match AS SELECT * FROM per.job_match"
        f" WHERE config_version = {int(config_version)} AND job_id IN (SELECT id FROM picked)"
    )
    out.commit()
    out.execute("DETACH DATABASE src")
    out.execute("DETACH DATABASE per")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--catalogue", type=Path, required=True)
    p.add_argument("--personal", type=Path, required=True)
    p.add_argument("--config-version", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--size", type=int, default=1500)
    p.add_argument("--pool", type=int, default=24000)
    p.add_argument("--seed", type=int, default=14)
    p.add_argument("--include", nargs="*", default=[])
    p.add_argument(
        "--exclude",
        nargs="*",
        type=Path,
        default=[],
        help="earlier copies whose postings this sample must not contain",
    )
    p.add_argument(
        "--id-class",
        type=int,
        choices=(0, 1, 2, 3),
        help="keep only postings whose id hashes to this class mod 4 (`id_class`)",
    )
    a = p.parse_args()
    if a.out.exists():
        raise SystemExit(f"{a.out} exists; give a new file")
    rng = random.Random(a.seed)
    cat = sqlite3.connect(f"file:{a.catalogue.as_posix()}?mode=ro", uri=True)
    per = sqlite3.connect(f"file:{a.personal.as_posix()}?mode=ro", uri=True)
    ids = [
        r[0]
        for r in cat.execute(
            "SELECT j.id FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
            " WHERE j.closed_at IS NULL AND length(r.description_text) > 300 ORDER BY j.id"
        )
    ]
    if a.id_class is not None:
        ids = [i for i in ids if id_class(i) == a.id_class]
    used: set[str] = set()
    for earlier in a.exclude:
        used |= {r[0] for r in sqlite3.connect(f"file:{earlier.as_posix()}?mode=ro", uri=True)
                 .execute("SELECT id FROM job")}  # fmt: skip
    ids = [i for i in ids if i not in used]
    pool = rng.sample(ids, min(a.pool, len(ids)))
    scores = dict(
        per.execute(
            "SELECT job_id, match_score FROM job_match WHERE config_version = ?",
            (a.config_version,),
        )
    )
    strata: dict[tuple[str, int], list[str]] = defaultdict(list)
    for chunk in range(0, len(pool), 900):
        part = pool[chunk : chunk + 900]
        marks = ",".join("?" * len(part))
        for job_id, text in cat.execute(
            f"SELECT j.id, substr(r.description_text, 1, 3000) FROM job j"
            f" JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.id IN ({marks})",
            part,
        ):
            lang = detect_language(text)
            lang = lang if lang in ("en", "pt") else "other"
            strata[(lang, _band(scores.get(job_id)))].append(job_id)
    picked: list[str] = list(dict.fromkeys(a.include))
    for lang, share in LANG_SHARE.items():
        quota = int(a.size * share)
        bands = [b for b in range(len(BANDS)) if strata[(lang, b)]]
        # Equal per band where the band can fill it; the rest from the others.
        left = quota
        for i, b in enumerate(sorted(bands, key=lambda b: len(strata[(lang, b)]))):
            want = left // (len(bands) - i)
            have = strata[(lang, b)]
            take = rng.sample(have, min(want, len(have)))
            picked += take
            left -= len(take)
    picked = list(dict.fromkeys(picked))
    cat.close()
    per.close()

    out = write_copy(a.catalogue, a.personal, a.config_version, a.out, picked)
    counts = Counter()
    for (lang, band), members in strata.items():
        counts[(lang, band)] = sum(1 for m in members if m in set(picked))
    print(f"picked {len(picked)} postings")
    for key in sorted(counts):
        print(f"  {key[0]:>5} band {BANDS[key[1]]}: {counts[key]}")
    print("providers:", dict(Counter(r[0] for r in out.execute("SELECT provider FROM job"))))
    print("stored answers:", out.execute("SELECT count(*) FROM job_match").fetchone()[0])
    out.close()


if __name__ == "__main__":
    main()

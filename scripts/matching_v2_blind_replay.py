"""Recompute every item of a blind package with THIS build, and compare.

    uv run python scripts/matching_v2_blind_replay.py --package DIR --sample COPY.db \
        --config-dir DIR [--digest OUT.txt]

Rebuilds the items exactly as `matching_v2_blind_package.py build` made them
(`matching_v2_evaluate.build_items`, half `blind`, every profile), finds each
key item again by profile, posting, signal and sentence, and reports how many
decisions of the current reader and of v2 this build reproduces. With
`--digest`, also writes one SHA-256 per (profile, posting) over v2's whole
answer (score, components, gates, evidence): the equivalence oracle for a
change that must not move any output. Reads the copy only; writes OUT.txt.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts import matching_v2_evaluate as evaluate  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402
from career_agent.matching_v2.store import compact_result  # noqa: E402


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()[:300]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    p.add_argument("--digest", type=Path)
    p.add_argument("--half", choices=("blind", "any"), default="blind")
    a = p.parse_args()
    key = [json.loads(x) for x in (a.package / "key.jsonl").read_text("utf-8").splitlines()]
    original = a.package / "REVIEW.blind.original.tsv"
    review = (original if original.exists() else a.package / "REVIEW.tsv").read_text("utf-8")
    review = review.splitlines()
    header = review[0].split("\t")
    sentence_of = {
        r.split("\t")[0]: _norm(dict(zip(header, r.split("\t"), strict=False))["sentence"])
        for r in review[1:]
    }
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "items.jsonl"
        evaluate.build_items(
            argparse.Namespace(
                sample=a.sample, config_dir=a.config_dir, out=out,
                to_label=Path(tmp) / "x.tsv", half=a.half, profiles="all",
                near_misses=10**6, both=10**6, held=10**6,
            )
        )  # fmt: skip
        items = [json.loads(x) for x in out.read_text("utf-8").splitlines()]
    index: dict[tuple, list[dict]] = {}
    for it in items:
        k = (it["profile"], it["job_id"], it["signal"], _norm(it["sentence"]))
        index.setdefault(k, []).append(it)
    same_cur = same_v2 = found = 0
    missing = []
    for k in key:
        hits = index.get((k["profile"], k["job_id"], k["signal"], sentence_of[k["id"]]))
        if not hits:
            missing.append(k["id"])
            continue
        found += 1
        it = hits[0]
        same_cur += bool(it["current"]) == bool(k["current"])
        same_v2 += (it["v2"], it["cause"], it["context"]) == (k["v2"], k["cause"], k["context"])
    print(f"key items found again: {found}/{len(key)}; current decision reproduced {same_cur};"
          f" v2 state+cause+context reproduced {same_v2}; missing {missing[:10]}")  # fmt: skip
    if a.digest:
        owner, _ = load_search_config(a.config_dir)
        profiles = {"owner": owner}
        profiles.update({k: evaluate.wizard(v) for k, v in evaluate.PROFESSIONS.items()})
        profiles.update({k: evaluate.wizard(v) for k, v in evaluate.ONE_WORD.items()})
        headings = (tuple(owner.prominence.primary_headings),
                    tuple(owner.prominence.secondary_headings))  # fmt: skip
        conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        lines = []
        for job, h, _p, _s in postings(conn, None):
            f = feat.extract(job, h, headings, negation=owner.negation)
            facts = json.dumps(f.payloads, sort_keys=True, default=str)
            lines.append(f"facts {job.job_id} {hashlib.sha256(facts.encode()).hexdigest()}")
            for name, config in profiles.items():
                m = pm.match(config, job, f)
                body = json.dumps(
                    [m.result.match_score, str(m.result.fit_band), compact_result(m)],
                    sort_keys=True, default=str,
                )  # fmt: skip
                lines.append(f"{name} {job.job_id} {hashlib.sha256(body.encode()).hexdigest()}")
        a.digest.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"digest: {len(lines)} lines -> {a.digest}")


if __name__ == "__main__":
    main()

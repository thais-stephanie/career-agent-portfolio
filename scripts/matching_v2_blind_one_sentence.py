"""What a human-labelled sentence is WORTH in the score, under A, B and C.

    uv run python scripts/matching_v2_blind_one_sentence.py --package DIR \
        --labels REVIEWED.tsv --sample COPY.db --config-dir DIR

Recognising a sentence as work (what the labels judge) and the share of a
component that sentence then pays (score arithmetic, ADR-0036) are two
questions. For every labelled item v2 MATCHED, this finds the sentence's
contribution in v2's answer for that profile and posting and reports:

* N items (not work) that still pay 50%+ of a component;
* Y items that fill a component to 100% on their own;
* components held at their ceiling by two or more DISTINCT sentences,
  and how many of those sentences were labelled (and how);

then the share each sentence pays under A (production), B (no sentence pays
over 50%) and C (one sentence stops at 75% of a component), split by label.
Simulation only; reads the copy, writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts import matching_v2_evaluate as evaluate  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402

PHRASE_COMPONENTS = ("responsibilities", "technologies", "automation_integration")


def _norm(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", text.lower())


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    a = p.parse_args()
    key = {
        k["id"]: k
        for k in map(json.loads, (a.package / "key.jsonl").read_text("utf-8").splitlines())
    }
    lines = a.labels.read_text("utf-8-sig").splitlines()
    header = lines[0].split("\t")
    rows = {r.split("\t")[0]: dict(zip(header, r.split("\t"), strict=False)) for r in lines[1:]}
    orig = (a.package / "REVIEW.blind.original.tsv").read_text("utf-8").splitlines()
    oh = orig[0].split("\t")
    sentence = {
        r.split("\t")[0]: dict(zip(oh, r.split("\t"), strict=False))["sentence"] for r in orig[1:]
    }
    labelled = [
        (i, k, rows[i]["label"].strip().upper())
        for i, k in key.items()
        if k["v2"] == "MATCH" and rows[i]["label"].strip().upper() in ("Y", "N")
    ]
    wanted = defaultdict(list)
    for i, k, lab in labelled:
        wanted[(k["profile"], k["job_id"])].append((i, k, lab))
    owner, _ = load_search_config(a.config_dir)
    profiles = {"owner": owner}
    profiles.update({k: evaluate.wizard(v) for k, v in evaluate.PROFESSIONS.items()})
    profiles.update({k: evaluate.wizard(v) for k, v in evaluate.ONE_WORD.items()})
    headings = (
        tuple(owner.prominence.primary_headings),
        tuple(owner.prominence.secondary_headings),
    )
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    share: dict[str, list[float]] = defaultdict(list)
    counts: Counter = Counter()
    ceiling_by_many: Counter = Counter()
    examples: dict[str, list[str]] = defaultdict(list)
    for job, h, _p, _s in postings(conn, None):
        for name, config in profiles.items():
            items = wanted.get((name, str(job.job_id)))
            if not items:
                continue
            f = feat.extract(job, h, headings, negation=owner.negation)
            r = pm.match(config, job, f).result
            for c in r.components:
                if c.component_id not in PHRASE_COMPONENTS or c.max_points <= 0:
                    continue
                paid = defaultdict(float)
                for x in c.contributions:
                    if x.points > 0:
                        paid[_norm(x.quote or "")] += x.points
                if c.points >= c.max_points - 1e-9 and len(paid) >= 2:
                    labs = [
                        lab
                        for i, k, lab in items
                        if any(
                            _norm(sentence[i])[:60] in q or q[:60] in _norm(sentence[i])
                            for q in paid
                        )
                    ]
                    tag = "".join(sorted(labs)) or "-"
                    ceiling_by_many[f"{len(paid)} sentences, labelled {tag}"] += 1
                for i, _k, lab in items:
                    s = _norm(sentence[i])
                    pts = sum(v for q, v in paid.items() if q and (s[:60] in q or q[:60] in s))
                    if pts <= 0:
                        continue
                    frac = pts / c.max_points
                    alone = len(paid) == 1 and c.points >= c.max_points - 1e-9
                    share[f"A:{lab}"].append(frac)
                    share[f"B:{lab}"].append(min(frac, 0.5))
                    share[f"C:{lab}"].append(min(frac, 0.75) if len(paid) == 1 else frac)
                    counts[f"{lab} paid"] += 1
                    if lab == "N" and frac >= 0.5:
                        counts["N paying >=50%"] += 1
                        line = f"{name} {frac:.0%} | {sentence[i][:110]}"
                        examples["N paying >=50%"].append(line)
                    if lab == "Y" and alone:
                        counts["Y alone filling 100%"] += 1
                        examples["Y alone filling 100%"].append(f"{name} | {sentence[i][:110]}")
                    if lab == "N" and alone:
                        counts["N alone filling 100%"] += 1
    print("labelled v2 matches:", len(labelled), dict(Counter(lab for _, _, lab in labelled)))
    print("counts:", dict(counts))
    for k in sorted(share):
        v = share[k]
        print(f"  {k}: n={len(v)} mean share of the component {sum(v) / len(v):.3f}")
    print("components at the ceiling held by 2+ sentences:", dict(ceiling_by_many))
    for k, v in examples.items():
        print(f"{k} (first 8):")
        for e in v[:8]:
            print("   ", e)


if __name__ == "__main__":
    main()

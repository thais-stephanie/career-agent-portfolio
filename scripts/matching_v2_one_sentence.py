"""How often ONE piece of evidence fills a work component, and what two
alternative rules would do. A simulation only: nothing here changes a score.

    uv run python scripts/matching_v2_one_sentence.py --sample S.db \
        --config-dir DIR --out CASES.jsonl

Over a bounded copy, for the owner's configuration and ten wizard-built
professions, every posting is matched by v2 (`profile_match.match`; the score
arithmetic is production's, ADR-0036). For each phrase component
(responsibilities, technologies, automation_integration) it counts how many
DISTINCT sentences paid, and reports the components that reach 50%, 75% and
100% of their maximum from one sentence.

Then, without adopting either, it recomputes every score under:

* A, current: one central duty counts `CENTRAL_PIECES` pieces (fills it);
* B, a ceiling per piece of evidence: no single sentence pays more than
  `--cap-b` of the component;
* C, two independent sentences for the full ceiling: a component paid by
  one sentence stops at `--cap-c` of its maximum; two or more, unchanged;

and reports the score, the fit band and the overlap of the top 10 / 20 / 50
(all postings, and the eligible ones) against A. CASES.jsonl holds the owner's
one-sentence components with their sentence, for reading (keep it local).
Opens the copy read-only, opens no socket, calls no model.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.matching_v2_evaluate import PROFESSIONS, wizard  # noqa: E402
from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.domain.enums import EligibilityStatus  # noqa: E402
from career_agent.match.score import fit_band_for, match_score_from  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402

PHRASE_COMPONENTS = ("responsibilities", "technologies", "automation_integration")


def _sentences(component) -> list[str]:
    return sorted({c.quote or "" for c in component.contributions if c.points > 0})


def _variant(component, rule: str, cap_b: float, cap_c: float):
    if component.component_id not in PHRASE_COMPONENTS or component.max_points <= 0:
        return component
    if rule == "B":
        per: dict[str, float] = defaultdict(float)
        for c in component.contributions:
            if c.points > 0:
                per[c.quote or ""] += c.points
        points = sum(min(p, cap_b * component.max_points) for p in per.values())
        return replace(component, points=min(points, component.points))
    if rule == "C" and len(_sentences(component)) == 1:
        return replace(component, points=min(component.points, cap_c * component.max_points))
    return component


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=Path, required=True)
    p.add_argument("--config-dir", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--cap-b", type=float, default=0.5)
    p.add_argument("--cap-c", type=float, default=0.75)
    a = p.parse_args()
    owner, _ = load_search_config(a.config_dir)
    profiles = {"owner": owner, **{k: wizard(v) for k, v in PROFESSIONS.items()}}
    headings = (
        tuple(owner.prominence.primary_headings),
        tuple(owner.prominence.secondary_headings),
    )
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    reach: dict[str, Counter] = defaultdict(Counter)
    scores: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    band_moves: dict[str, Counter] = defaultdict(Counter)
    cases = []
    for job, content_hash, _provider, _stored in postings(conn, None):
        facts = feat.extract(job, content_hash, headings, negation=owner.negation)
        for name, config in profiles.items():
            r = pm.match(config, job, facts).result
            for c in r.components:
                if c.component_id not in PHRASE_COMPONENTS or c.max_points <= 0:
                    continue
                said = _sentences(c)
                share = c.points / c.max_points
                if said:
                    reach[name]["components_paid"] += 1
                if len(said) == 1:
                    for edge in (0.5, 0.75, 1.0):
                        reach[name][f"one_sentence_>={edge:.0%}"] += share >= edge - 1e-9
                    if name == "owner" and share >= 0.5 - 1e-9:
                        cases.append(
                            {
                                "job_id": job.job_id,
                                "component": c.component_id,
                                "share": round(share, 3),
                                "score": r.match_score,
                                "sentence": said[0],
                                "signals": [x.label for x in c.contributions if x.points > 0],
                            }
                        )
            eligible = r.eligibility_status is EligibilityStatus.VERIFIED_ELIGIBLE
            row = {"A": r.match_score}
            for rule in ("B", "C"):
                comps = tuple(_variant(c, rule, a.cap_b, a.cap_c) for c in r.components)
                score = match_score_from(comps, r.penalties)
                row[rule] = score
                band_moves[f"{name}:{rule}"][
                    f"{fit_band_for(r.match_score, config)}->{fit_band_for(score, config)}"
                ] += 1
            scores[name]["rows"].append((str(job.job_id), eligible, row))
    with a.out.open("w", encoding="utf-8") as out:
        for case in cases:
            out.write(json.dumps(case, ensure_ascii=False) + "\n")
    print(f"postings: {len(scores['owner']['rows'])}; profiles: {len(profiles)}")
    total = Counter()
    for name in profiles:
        total.update(reach[name])
    print("phrase components paid, and those paid by ONE sentence reaching a share:")
    for key in ("components_paid", "one_sentence_>=50%", "one_sentence_>=75%",
                "one_sentence_>=100%"):  # fmt: skip
        print(f"  {key:22s} owner {reach['owner'][key]:6d}   all profiles {total[key]:7d}")
    for rule in ("B", "C"):
        print(f"\nvariant {rule}:")
        for name in ("owner", "all"):
            rows = (
                scores[name]["rows"]
                if name != "all"
                else [x for n in profiles for x in scores[n]["rows"]]
            )
            deltas = [row[rule] - row["A"] for _, _, row in rows]
            moved = [d for d in deltas if d]
            print(
                f"  {name:6s} scores moved {len(moved)}/{len(rows)}, mean move"
                f" {statistics.mean(moved) if moved else 0:+.1f}, largest {min(deltas, default=0)}"
            )
        moves = Counter()
        for name in profiles:
            moves.update({k: v for k, v in band_moves[f"{name}:{rule}"].items()
                          if k.split("->")[0] != k.split("->")[1]})  # fmt: skip
        print(f"  fit band changes (all profiles): {dict(moves.most_common())}")
        owner_moves = {
            k: v
            for k, v in band_moves[f"owner:{rule}"].items()
            if k.split("->")[0] != k.split("->")[1]
        }
        print(f"  owner band changes: {owner_moves}")
        for scope in ("all postings", "eligible"):
            line = []
            for k in (10, 20, 50):
                overlaps = []
                for name in profiles:
                    rows = scores[name]["rows"]
                    if scope == "eligible":
                        rows = [x for x in rows if x[1]]
                    if len(rows) < k:
                        continue
                    top_a = {j for j, _, s in sorted(rows, key=lambda x: (-x[2]["A"], x[0]))[:k]}
                    top_v = {j for j, _, s in sorted(rows, key=lambda x: (-x[2][rule], x[0]))[:k]}
                    overlaps.append(len(top_a & top_v) / k)
                    if name == "owner":
                        line.append(f"owner top{k} {len(top_a & top_v)}/{k}")
                if overlaps:
                    line.append(f"mean top{k} overlap {statistics.mean(overlaps):.2f}")
            print(f"  {scope}: " + "; ".join(line))
    print(f"\ncases: {a.out} ({len(cases)} owner components paid by one sentence at >= 50%)")


if __name__ == "__main__":
    main()

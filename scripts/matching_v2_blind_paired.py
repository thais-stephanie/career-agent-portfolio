"""Paired analysis of a labelled blind package: current and v2 on the SAME items.

    uv run python scripts/matching_v2_blind_paired.py --package DIR --labels REVIEWED.tsv

Complements `matching_v2_blind_package.py score` (whose numbers are the
official ones and are not replaced). For every group (all, language, one-word
or not, profile) it reports:

* the paired matrix: both right, both wrong, current right and v2 wrong,
  current wrong and v2 right ("right" = said MATCH and labelled Y, or did
  not and labelled N);
* what v2 changes: true positives recovered (Y, v2 matched, current did
  not), lost (Y, current matched, v2 did not), false positives removed (N,
  current matched, v2 did not) and introduced (N, v2 matched, current did
  not). Raw item counts and sampling-weighted counts;
* a STRATIFIED PAIRED BOOTSTRAP of the deltas v2 - current in precision,
  recall and F1: the package was drawn per cell (profile, language, kind of
  item), so each replicate resamples items with replacement WITHIN each cell,
  keeps their sampling weights, and recomputes both readers on the same
  resampled items. Seed 20261014, 2,000 replicates, 95% percentile interval.

THE GATE IS READ AS FOLLOWS, written before any delta was computed
(2026-10-14), from the gate fixed in `docs/checkpoints/matching-architecture-v2.md`
("precision not lower than the current reader's at 95% confidence, recall
higher, ... per language and per profession with at least 30 items each"):

* precision not lower: PASS when the 95% interval of the precision delta
  lies at or above zero; FAIL when it lies wholly below zero; otherwise
  INSUFFICIENT_DATA (no margin was ever set, so none is invented here);
* recall higher: PASS when the 95% interval of the recall delta lies wholly
  above zero; FAIL when it lies at or below zero; otherwise
  INSUFFICIENT_DATA;
* a group with fewer than 30 labelled Y/N items is INSUFFICIENT_FOR_GATE.

`?` labels are counted and left out of every metric. Writes nothing.

PACKAGE 3 (`--gate package3`), written 2026-10-15 BEFORE package 3 was drawn
and before any of its labels existed, by the owner's specification; not to
be changed after:

* primary, on ALL: precision non-inferiority with margin -0.03 (PASS when
  the lower bound of the 95% paired interval of precision v2 - current is
  above -0.03), recall higher (lower bound of recall v2 - current above 0)
  and F1 v2 >= F1 current at the point estimate (a confirmation, never a
  substitute). Every one must hold.
* safety, for each group with 30+ labelled Y/N items among one-word, 2+
  words, EN, PT, ES, each profile and each preference of two words or more:
  significance is not required per group. A group BLOCKS when a regression
  is material AND consistent: precision v2 - current at or below -0.10
  with the interval's upper bound below 0 and at least 3 false positives
  introduced (raw); or recall v2 - current at or below -0.10 with the upper
  bound below 0, at least 3 true positives lost (raw) and F1 v2 below F1
  current (no compensation). A structural defect the analyst names in the
  qualitative review (3+ v2-only errors in one group with one structural
  cause) is reported as STRUCTURAL and blocks too. A delta of 0.001 or an
  interval made degenerate by cells of one item blocks nothing.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

SEED = 20261014
REPLICATES = 2000
MIN_ITEMS = 30


def load(package: Path, labels_path: Path) -> tuple[list[dict], Counter]:
    key = {
        k["id"]: k for k in map(json.loads, (package / "key.jsonl").read_text("utf-8").splitlines())
    }
    lines = labels_path.read_text(encoding="utf-8-sig").splitlines()
    header = lines[0].split("\t")
    col = header.index("label")
    counts: Counter = Counter()
    rows = []
    for line in lines[1:]:
        parts = line.split("\t")
        label = parts[col].strip().upper() if len(parts) > col else ""
        counts[label or "(blank)"] += 1
        if label in ("Y", "N"):
            k = key[parts[0]]
            rows.append({**k, "label": label, "cur": bool(k["current"]), "new": k["v2"] == "MATCH"})
    return rows, counts


def metrics(rows: list[dict], said: str) -> tuple[float, float, float]:
    positives = sum(r["weight"] for r in rows if r["label"] == "Y")
    yes = [r for r in rows if r[said]]
    tp = sum(r["weight"] for r in yes if r["label"] == "Y")
    p = tp / sum(r["weight"] for r in yes) if yes else math.nan
    rc = tp / positives if positives else math.nan
    f1 = 2 * p * rc / (p + rc) if p + rc and not math.isnan(p + rc) else math.nan
    return p, rc, f1


def paired(rows: list[dict]) -> dict:
    m: Counter = Counter()
    w: Counter = Counter()
    for r in rows:
        y = r["label"] == "Y"
        cur_ok, new_ok = r["cur"] == y, r["new"] == y
        cell = {
            (True, True): "both_right",
            (False, False): "both_wrong",
            (True, False): "current_right_v2_wrong",
            (False, True): "current_wrong_v2_right",
        }[(cur_ok, new_ok)]
        m[cell] += 1
        w[cell] += r["weight"]
        if r["new"] != r["cur"]:
            change = (
                ("tp_recovered" if y else "fp_introduced")
                if r["new"]
                else ("tp_lost" if y else "fp_removed")
            )
            m[change] += 1
            w[change] += r["weight"]
    return {"raw": dict(m), "weighted": {k: round(v, 1) for k, v in w.items()}}


def bootstrap(rows: list[dict]) -> dict[str, tuple[float, float, float]]:
    cells: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        cells[(r["profile"], r["lang"], r["stratum"])].append(r)
    rng = random.Random(SEED)
    deltas: dict[str, list[float]] = {"precision": [], "recall": [], "f1": []}
    for _ in range(REPLICATES):
        sample = [rng.choice(members) for members in cells.values() for _ in members]
        a, b = metrics(sample, "cur"), metrics(sample, "new")
        for name, x, y in zip(("precision", "recall", "f1"), a, b, strict=True):
            if not (math.isnan(x) or math.isnan(y)):
                deltas[name].append(y - x)
    out = {}
    for name, values in deltas.items():
        values.sort()
        if len(values) < REPLICATES * 0.9:
            out[name] = (math.nan, math.nan, math.nan)
            continue
        lo = values[int(0.025 * len(values))]
        hi = values[min(len(values) - 1, int(0.975 * len(values)))]
        out[name] = (sum(values) / len(values), lo, hi)
    return out


def verdicts(delta: dict, n: int) -> dict[str, str]:
    if n < MIN_ITEMS:
        return {
            "precision_not_lower": "INSUFFICIENT_FOR_GATE",
            "recall_higher": "INSUFFICIENT_FOR_GATE",
        }
    _, plo, phi = delta["precision"]
    _, rlo, rhi = delta["recall"]
    p = "PASS" if plo >= 0 else ("FAIL" if phi < 0 else "INSUFFICIENT_DATA")
    r = "PASS" if rlo > 0 else ("FAIL" if rhi <= 0 else "INSUFFICIENT_DATA")
    return {"precision_not_lower": p, "recall_higher": r}


MARGIN = -0.03
MATERIAL = -0.10
MIN_CHANGED = 3


def primary_package3(res: dict) -> dict[str, str]:
    """The package 3 primary gate on one group (ALL), fixed before drawing."""
    if "delta" not in res:
        return {"precision_noninferior": "INSUFFICIENT_FOR_GATE"}
    _, plo, _ = res["delta"]["precision"]
    _, rlo, _ = res["delta"]["recall"]
    f1_cur, f1_new = res["current"][2], res["v2"][2]
    out = {
        "precision_noninferior": "PASS" if plo > MARGIN else "FAIL",
        "recall_higher": "PASS" if rlo > 0 else "FAIL",
        "f1_not_lower": "PASS" if f1_new >= f1_cur else "FAIL",
    }
    out["verdict"] = "PASS" if all(v == "PASS" for v in out.values()) else "FAIL"
    return out


def safety_package3(res: dict) -> str:
    """OK, BLOCK_PRECISION or BLOCK_RECALL for one subgroup (see the module
    docstring); a STRUCTURAL block is the analyst's, from the error review."""
    if "delta" not in res:
        return "INSUFFICIENT_FOR_GATE"
    raw = res["paired"]["raw"]
    p, _, phi = res["delta"]["precision"]
    r, _, rhi = res["delta"]["recall"]
    if p <= MATERIAL and phi < 0 and raw.get("fp_introduced", 0) >= MIN_CHANGED:
        return "BLOCK_PRECISION"
    if (
        r <= MATERIAL
        and rhi < 0
        and raw.get("tp_lost", 0) >= MIN_CHANGED
        and res["v2"][2] < res["current"][2]
    ):
        return "BLOCK_RECALL"
    return "OK"


def report(name: str, rows: list[dict]) -> dict:
    n = len(rows)
    out: dict = {"group": name, "n": n, "Y": sum(r["label"] == "Y" for r in rows)}
    out["current"] = metrics(rows, "cur")
    out["v2"] = metrics(rows, "new")
    out["paired"] = paired(rows)
    if n >= MIN_ITEMS:
        out["delta"] = bootstrap(rows)
        out["gate"] = verdicts(out["delta"], n)
    else:
        out["gate"] = verdicts({}, n)
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--package", type=Path, required=True)
    p.add_argument("--labels", type=Path, required=True)
    p.add_argument("--json", type=Path)
    p.add_argument("--gate", choices=("package1", "package3"), default="package1")
    a = p.parse_args()
    rows, counts = load(a.package, a.labels)
    print(f"labels: {dict(counts)}; used in metrics: {len(rows)}")
    groups = [
        ("ALL", rows),
        ("one-word", [r for r in rows if r["one_word"]]),
        ("2+ words", [r for r in rows if not r["one_word"]]),
    ]
    for lang in ("en", "pt", "es", "und"):
        groups.append((f"lang={lang}", [r for r in rows if r["lang"] == lang]))
    # Recognised without grammar since 2026-10-14 (fr, it, de, nl, pl, id).
    others = [r for r in rows if r["lang"] not in ("en", "pt", "es", "und")]
    if others:
        groups.append(("lang=other", others))
    for profile in sorted({r["profile"] for r in rows}):
        groups.append((f"profile={profile}", [r for r in rows if r["profile"] == profile]))
    if a.gate == "package3":
        for signal in sorted({r["signal"] for r in rows if not r["one_word"]}):
            groups.append((f"phrase={signal}", [r for r in rows if r["signal"] == signal]))
    results = [report(name, subset) for name, subset in groups]
    for res in results:
        c, v = res["current"], res["v2"]
        line = (
            f"{res['group']:24s} n={res['n']:3d} Y={res['Y']:3d} | current P {c[0]:.3f}"
            f" R {c[1]:.3f} F1 {c[2]:.3f} | v2 P {v[0]:.3f} R {v[1]:.3f} F1 {v[2]:.3f}"
        )
        print(line)
        print(f"    paired raw {res['paired']['raw']}")
        if "delta" in res:
            d = res["delta"]
            print(
                "    delta v2-current: "
                + ", ".join(f"{k} {m:+.3f} [{lo:+.3f}, {hi:+.3f}]" for k, (m, lo, hi) in d.items())
            )
        if a.gate == "package3":
            if res["group"] == "ALL":
                res["gate"] = primary_package3(res)
            elif not res["group"].startswith("lang=und") and res["group"] != "lang=other":
                res["gate"] = {"safety": safety_package3(res)}
        print(f"    gate: {res['gate']}")
    if a.json:
        a.json.write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()

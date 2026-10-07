"""Measure the AI rewrite reader against the synthetic rewrite corpus.

    uv run python scripts/rewrite_corpus.py            # both splits, a table
    uv run python scripts/rewrite_corpus.py --json     # outcomes, for a record
    uv run python scripts/rewrite_corpus.py --split dev  # while writing rules

Each case is one rewrite of confirmed evidence, read exactly as the drafter
reads it: `tailor.grounding(text, statements + tools, tools, ai=True)`.
Distortions (expect FLAG) and faithful controls (expect PASS) are counted
apart: a reader that flags everything catches every distortion and is useless.
Synthetic text only; no network, no model.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "rewrite_corpus"
SPLITS = ("dev", "eval")
#: A neutral title: rank words in it would lend the rewrite their rank.
TITLE = "Operations Analyst"


def load(split: str) -> list[dict[str, Any]]:
    from career_agent.yaml_io import safe_load

    return list(safe_load((CORPUS / f"{split}.yaml").read_text(encoding="utf-8"))["cases"])


def outcome(case: dict[str, Any]) -> tuple[str, list[str]]:
    """FLAG with the reader's check codes, or PASS."""
    from career_agent.resume_doc import jd
    from career_agent.resume_doc.tailor import grounding

    tools = list(case.get("tools") or [])
    source = " ".join([*case["sources"], " ".join(tools)])
    found = grounding(
        case["text"],
        source,
        {jd.folded(t) for t in tools},
        ai=True,
        titles=case.get("title", TITLE),
    )
    codes = sorted({code for code, _ in found})
    return ("FLAG" if codes else "PASS"), codes


def measure(split: str) -> dict[str, Any]:
    rows = []
    for case in load(split):
        got, codes = outcome(case)
        rows.append({"id": case["id"], "dimension": case["dimension"], "lang": case["lang"],
                     "expect": case["expect"], "got": got, "codes": codes})  # fmt: skip
    distortions = [r for r in rows if r["expect"] == "FLAG"]
    controls = [r for r in rows if r["expect"] == "PASS"]
    return {
        "split": split,
        "distortions_flagged": sum(r["got"] == "FLAG" for r in distortions),
        "distortions": len(distortions),
        "controls_passed": sum(r["got"] == "PASS" for r in controls),
        "controls": len(controls),
        "missed_by_dimension": dict(
            Counter(r["dimension"] for r in distortions if r["got"] == "PASS")
        ),
        "rows": rows,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print outcomes as JSON")
    parser.add_argument("--split", choices=SPLITS, help="one split only (dev while writing rules)")
    args = parser.parse_args(argv)
    results = [measure(split) for split in ([args.split] if args.split else SPLITS)]
    if args.json:
        print(json.dumps(results, indent=2, ensure_ascii=False))
        return 0
    for r in results:
        print(f"\n{r['split'].upper()}")
        for row in r["rows"]:
            mark = "  " if row["got"] == row["expect"] else "XX"
            codes = ",".join(row["codes"])
            print(f" {mark} {row['id']:16} {row['dimension']:15} {row['lang']}  "
                  f"expect {row['expect']}  got {row['got']}  {codes}")  # fmt: skip
        print(f" distortions flagged {r['distortions_flagged']}/{r['distortions']}, "
              f"controls passed {r['controls_passed']}/{r['controls']}, "
              f"missed by dimension {r['missed_by_dimension']}")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())

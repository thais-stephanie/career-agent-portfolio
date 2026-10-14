"""Precision and recall of the work matching, from labels that are not scores.

    uv run python scripts/matching_v2_evaluate.py items --sample S.db \
        --config-dir DIR --out ITEMS.jsonl --to-label TO_LABEL.tsv
    uv run python scripts/matching_v2_evaluate.py score --items ITEMS.jsonl \
        --labels LABELS.tsv

ITEMS. The EVALUATION half of a bounded copy (`--half`: postings whose id
hashes odd; the even half was used to develop the rules and is never read
here) is matched by production (readers-N, `match_job`) and by v2, under the
configuration in `--config-dir` and ten wizard-built profiles in different
professions. One item is a (profile, posting, phrase, sentence): every
sentence either reader found the phrase in, every sentence v2 set aside or
held uncertain, and a sample of NEAR MISSES (every word of the phrase in the
sentence, neither reader matching) so a miss both readers share is counted.

TO_LABEL is the items in a shuffled order with only the phrase and the
sentence: no reader's decision, no score. A label is Y (the sentence says the
role does or needs the work the phrase names), N (it does not: another
sense, the company, a benefit, words that merely meet) or ? (cannot tell).

SCORE joins the labels and reports precision and recall for each reader,
overall and by language and profession, with Wilson 95% intervals. Recall is
over the pool of positives either reader found plus those in the near-miss
sample (weighted by its sampling rate). ITEMS holds the sentences; keep it
local. LABELS holds ids and letters only. Opens no socket and calls no model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from scripts.matching_v2_shadow import postings  # noqa: E402

from career_agent.config.search_config import SearchConfig, load_search_config  # noqa: E402
from career_agent.config.setup import Answers, apply_answers  # noqa: E402
from career_agent.match.engine import match_job  # noqa: E402
from career_agent.matching_v2 import features as feat  # noqa: E402
from career_agent.matching_v2 import profile_match as pm  # noqa: E402
from career_agent.matching_v2.language import concept_readings  # noqa: E402

#: Ten professions, written the way people write them, in two languages.
PROFESSIONS = {
    "automation": Answers(role_examples=("workflow automation", "systems integration"),
                          residence_country="BR"),
    "nursing_pt": Answers(role_examples=("assistência ao paciente",
                                         "administração de medicamentos"), residence_country="BR"),
    "teaching": Answers(role_examples=("lesson plans", "student support"), residence_country="PT"),
    "sales": Answers(role_examples=("lead generation", "vendas consultivas"),
                     residence_country="MX"),
    "finance": Answers(role_examples=("accounts payable", "budget analysis"),
                       residence_country="AR"),
    "people": Answers(role_examples=("recruitment", "payroll"), residence_country="CO"),
    "design": Answers(role_examples=("user interface design", "user research"),
                      residence_country="BR"),
    "service_pt": Answers(role_examples=("atendimento ao cliente", "suporte técnico"),
                          residence_country="BR"),
    "data": Answers(role_examples=("data analysis", "data migration"), residence_country="US"),
    "operations": Answers(role_examples=("inventory management", "supplier negotiation"),
                          residence_country="ES"),
}  # fmt: skip


def wizard(answers: Answers) -> SearchConfig:
    starter = yaml.safe_load((ROOT / "config" / "search.starter.yaml").read_text("utf-8"))
    mapping, _ = apply_answers(starter, answers)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "search.local.yaml").write_text(
            yaml.safe_dump(mapping, sort_keys=False, allow_unicode=True), encoding="utf-8"
        )
        config, _ = load_search_config(Path(tmp))
    return config


def in_half(job_id: str, half: str) -> bool:
    odd = int(hashlib.sha256(job_id.encode()).hexdigest(), 16) % 2 == 1
    return odd if half == "eval" else not odd


def _sentence_span(cut: list, start: int, end: int) -> tuple[int, int]:
    """The whole sentence (as v2 cut the advert) holding `start:end`."""
    for s in cut:
        if s.start <= start < s.end:
            return s.start, max(s.end, end)
    return start, end


def _item(
    items: dict, base: dict, config: SearchConfig, cut: list, signal: str, start: int, end: int
) -> dict:
    """The item for this profile, posting, phrase and sentence, made once."""
    s, e = _sentence_span(cut, start, end)
    key = (base["profile"], base["job_id"], signal, s)
    if key not in items:
        items[key] = {**base, "signal": signal, "phrase": config.lexicon[signal].label,
                      "start": s, "end": e, "current": False, "v2": "NONE", "cause": None,
                      "context": None}  # fmt: skip
    return items[key]


def build_items(a: argparse.Namespace) -> None:
    owner, _ = load_search_config(a.config_dir)
    profiles = {"owner": owner, **{k: wizard(v) for k, v in PROFESSIONS.items()}}
    headings = (
        tuple(owner.prominence.primary_headings),
        tuple(owner.prominence.secondary_headings),
    )
    conn = sqlite3.connect(f"file:{a.sample.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    items: dict[tuple, dict] = {}
    near: list[dict] = []
    for job, content_hash, provider, _stored in postings(conn, None):
        if not in_half(str(job.job_id), a.half):
            continue
        facts = feat.extract(job, content_hash, headings, negation=owner.negation)
        cut = facts.sentences
        units = feat.work_units(job, facts)
        for name, config in profiles.items():
            current = match_job(config, job, computed_at="eval")
            v2 = pm.match(config, job, facts, computed_at="eval")
            work = set(config.scoring.components.responsibilities.weights) | set(
                config.scoring.components.automation_integration.weights
            )
            base = {"profile": name, "job_id": job.job_id, "lang": facts.language,
                    "provider": provider}  # fmt: skip

            for sig in current.signals:
                if sig.signal_id not in work:
                    continue
                for h in sig.hits:
                    if h.field_name == "description":
                        _item(items, base, config, cut, sig.signal_id, h.char_start, h.char_end)[
                            "current"
                        ] = True
            rank = {"MATCH": 3, "UNCERTAIN": 2, "EXCLUDED_CONTEXT": 1, "NONE": 0}
            for e in v2.evidence:
                if e.signal_id not in work or e.context == "TITLE":
                    continue
                it = _item(items, base, config, cut, e.signal_id, e.start, e.end)
                if rank[e.state] > rank[it["v2"]]:
                    it["v2"], it["cause"], it["context"] = e.state, e.reason, e.context
            # Near misses: every root of a reading in one sentence, neither
            # reader matching there.
            found = {(k[2], k[3]) for k in items if k[0] == name and k[1] == job.job_id}
            for signal in work:
                readings = [set(r) for p in config.lexicon[signal].patterns
                            for r in concept_readings(p)]  # fmt: skip
                for field_name, _ctx, _start, toks in units:
                    if field_name != "description" or not toks:
                        continue
                    roots = {t.root for t in toks}
                    if any(r and r <= roots for r in readings):
                        s, e = _sentence_span(cut, toks[0].start, toks[-1].end)
                        if (signal, s) not in found:
                            near.append({**base, "signal": signal,
                                         "phrase": config.lexicon[signal].label,
                                         "start": s, "end": e, "current": False,
                                         "v2": "NONE", "cause": None, "context": None,
                                         "near_miss": True})  # fmt: skip
    rng = random.Random(14)
    # Strata: every disagreement; samples of each kind of agreement, weighted
    # by their sampling rate so the estimates are of the whole population.
    strata: dict[str, list[dict]] = defaultdict(list)
    for it in items.values():
        v2_yes = it["v2"] == "MATCH"
        stratum = (
            "disagree" if it["current"] != v2_yes
            else "both_match" if v2_yes
            else "v2_held"  # neither matched; v2 showed it uncertain or set it aside
        )  # fmt: skip
        strata[stratum].append(it)
    strata["near_miss"] = near
    quota = {"disagree": None, "both_match": a.both, "v2_held": a.held,
             "near_miss": a.near_misses}  # fmt: skip
    everything = []
    for stratum, members in strata.items():
        want = quota[stratum]
        chosen = members if want is None else rng.sample(members, min(want, len(members)))
        weight = len(members) / max(1, len(chosen))
        for it in chosen:
            it["stratum"], it["weight"] = stratum, weight
            it.setdefault("near_miss", stratum == "near_miss")
        everything += chosen
        print(f"  {stratum}: {len(members)} items, {len(chosen)} to label, weight {weight:.2f}")
    texts: dict[str, str] = {}
    for job, _h, _p, _s in postings(conn, None):
        if in_half(str(job.job_id), a.half):
            texts[str(job.job_id)] = job.description
    with a.out.open("w", encoding="utf-8") as out:
        for i, it in enumerate(everything):
            it["id"] = f"i{i:05d}"
            it["sentence"] = texts[str(it["job_id"])][it["start"] : it["end"]]
            out.write(json.dumps(it, ensure_ascii=False) + "\n")
    order = list(everything)
    rng.shuffle(order)
    with a.to_label.open("w", encoding="utf-8") as out:
        for it in order:
            sentence = " ".join(it["sentence"].split())[:400]
            out.write(f"{it['id']}\t{it['phrase']}\t{sentence}\n")
    print(f"profiles: {len(profiles)}; to label: {len(everything)}; by profile: "
          f"{dict(Counter(i['profile'] for i in everything))}")  # fmt: skip


def wilson(k: float, n: float) -> tuple[float, float, float]:
    if n <= 0:
        return (math.nan, math.nan, math.nan)
    p = k / n
    z = 1.96
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return p, max(0.0, centre - half), min(1.0, centre + half)


def score(a: argparse.Namespace) -> None:
    items = [json.loads(line) for line in a.items.read_text(encoding="utf-8").splitlines()]
    labels = {}
    for line in a.labels.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#"):
            key, value = line.split("\t")[:2]
            labels[key.strip()] = value.strip().upper()
    rows = [dict(i, label=labels.get(i["id"])) for i in items]
    rows = [r for r in rows if r["label"] in ("Y", "N")]
    print(f"labelled: {len(rows)} of {len(items)} ({len(items) - len(rows)} unlabelled or ?)")

    def report(name: str, subset: list[dict]) -> None:
        out = []
        positives = sum(r["weight"] for r in subset if r["label"] == "Y")
        for reader, said in (
            ("current", lambda r: r["current"]),
            ("v2", lambda r: r["v2"] == "MATCH"),
        ):
            said_yes = [r for r in subset if said(r)]
            tp = sum(r["weight"] for r in said_yes if r["label"] == "Y")
            p = wilson(tp, sum(r["weight"] for r in said_yes))
            rc = wilson(tp, positives)
            f1 = 2 * p[0] * rc[0] / (p[0] + rc[0]) if p[0] + rc[0] else math.nan
            out.append(
                f"{reader}: P {p[0]:.3f} [{p[1]:.3f}-{p[2]:.3f}] n={len(said_yes)}  "
                f"R {rc[0]:.3f} [{rc[1]:.3f}-{rc[2]:.3f}]  F1 {f1:.3f}"
            )
        print(f"{name} (positives {positives:.0f}):\n    " + "\n    ".join(out))

    report("ALL", rows)
    for key in ("lang", "profile"):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in rows:
            groups[r[key]].append(r)
        for value, subset in sorted(groups.items()):
            if len(subset) >= 10:
                report(f"{key}={value}", subset)
    flips = Counter()
    for r in rows:
        if r["current"] != (r["v2"] == "MATCH"):
            who = "current_only" if r["current"] else "v2_only"
            flips[(who, r["v2"], r["cause"] or r["context"], r["label"])] += 1
    print("disagreements (who matched, v2 state, cause, label):")
    for k, v in flips.most_common():
        print(f"  {v:4d}  {k}")


def main() -> None:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("items")
    i.add_argument("--sample", type=Path, required=True)
    i.add_argument("--config-dir", type=Path, required=True)
    i.add_argument("--out", type=Path, required=True)
    i.add_argument("--to-label", type=Path, required=True)
    i.add_argument("--half", choices=("eval", "dev"), default="eval")
    i.add_argument("--near-misses", type=int, default=120)
    i.add_argument("--both", type=int, default=220)
    i.add_argument("--held", type=int, default=100)
    s = sub.add_parser("score")
    s.add_argument("--items", type=Path, required=True)
    s.add_argument("--labels", type=Path, required=True)
    a = p.parse_args()
    build_items(a) if a.cmd == "items" else score(a)


if __name__ == "__main__":
    main()

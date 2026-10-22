"""Ranking quality against the person's own decisions, and the E2 recall panel.

    python scripts/ranking_eval.py import DECISIONS.csv
    python scripts/ranking_eval.py golden [--seed-surfaced CSV --seed-kept CSV]
    python scripts/ranking_eval.py evaluate [--save-baseline] [--open-lockbox]
    python scripts/ranking_eval.py panel-draw [--per-stratum 25]
    python scripts/ranking_eval.py panel-report PANEL_DIR

IMPORT writes decisions from a CSV into `review_decision` (migration 0060;
the database is migrated on open, like any other command). Columns: `Job id`
or, for a panel, `Panel #`; `Decision` (Keep / Maybe / Reject, or Manter /
Talvez / Descartar); optionally `Primary reason`, `Other reasons` (separated
by `;`) and `Note`. A reason is a code (WRONG_PRIMARY_WORK...) or its words in
English or Portuguese.

GOLDEN rebuilds `data/evaluation/golden/ranking-golden.json` (read-only).
`--seed-surfaced` / `--seed-kept` freeze the 2026-10-21 review: every row of
the surfaced CSV is labelled (KEEP if its id is in the kept CSV), its row
order is kept as the order it was served in, and groups are split into
`development` and `regression`. Later decisions join their group's split when
the group is already there, and the `prospective_lockbox` otherwise.

EVALUATE ranks the labelled postings under every policy, read-only, prints
the metrics and applies the release gate against `baseline.json` (exit 1 when
it fails). It never reads the lockbox unless `--open-lockbox`, which is logged
and fails the gate. `--save-baseline` makes this run the baseline.

PANEL-DRAW samples postings the review list never showed, blind, into a new
folder: `panel.csv` to label and `key.json` (strata, sizes, scores: do not open
while labelling). PANEL-REPORT estimates what each stratum hides.

Everything stays under `data/`, which Git ignores. No model call, no socket.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from career_agent.config.search_config import load_search_config  # noqa: E402
from career_agent.evaluation import ranking  # noqa: E402
from career_agent.storage.catalogue import open_read_only  # noqa: E402
from career_agent.storage.review_decision import (  # noqa: E402
    DECISIONS,
    REASON_WORDS,
    REASONS,
    ReviewDecisionRepo,
)

GOLDEN_DIR = ROOT / "data" / "evaluation" / "golden"
DECISION_WORDS_IN = {
    **{d.lower(): d for d in DECISIONS},
    "manter": "KEEP",
    "talvez": "MAYBE",
    "descartar": "REJECT",
    "rejeitar": "REJECT",
}
#: The screen's Portuguese words (i18n.js `review.reason.*`), accepted on import.
REASON_WORDS_PT = {
    "WRONG_PRIMARY_WORK": "O trabalho principal não é o que eu quero",
    "MANDATORY_EXPERIENCE_GAP": "Falta experiência obrigatória",
    "MANDATORY_TOOL_PLATFORM_GAP": "Falta uma ferramenta ou plataforma obrigatória",
    "MANDATORY_CREDENTIAL_GAP": "Falta uma certificação ou diploma obrigatório",
    "LOCATION_OR_WORK_AUTH": "Local ou autorização de trabalho",
    "COMPENSATION": "Remuneração",
    "SENIORITY_OR_SCOPE": "Senioridade ou escopo",
    "WORK_MODEL_OR_SCHEDULE": "Modelo de trabalho ou horário",
    "COMPANY_OR_OPPORTUNITY_QUALITY": "A empresa ou a oportunidade",
    "NOT_INTERESTED_DESPITE_FIT": "Não tenho interesse, mesmo combinando",
    "DUPLICATE": "Repetida de outra vaga",
    "STALE_OR_CLOSED": "Antiga ou encerrada",
    "INSUFFICIENT_INFORMATION": "Informação insuficiente para decidir",
}
REASON_WORDS_IN = {
    **{r.lower(): r for r in REASONS},
    **{w.lower(): c for c, w in REASON_WORDS.items()},
    **{w.lower(): c for c, w in REASON_WORDS_PT.items()},
}
DEEP_WORDS_IN = {
    "apply": "STRONG",
    "apply, preparing": "PREPARE",
    "check first": "CHECK_FIRST",
    "aligned, history not compared": "NOT_COMPARED",
    "to confirm": "TO_CONFIRM",
    "low priority": "LOW",
    "different main activity": "OTHER_WORK",
}
#: Below this many characters a posting's local text is an excerpt (the
#: 1200-character completeness bar the score itself uses).
FULL_TEXT = 1200
INCOMPLETE = "Descrição local incompleta: consulte a URL antes de decidir."


def _read(db: Path) -> sqlite3.Connection:
    conn = open_read_only(db)
    conn.row_factory = sqlite3.Row
    return conn


def _config(config_dir: Path):
    config, _ = load_search_config(config_dir)
    return config


def _rows(path: Path) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as handle:
        return [
            {(k or "").strip().lower(): (v or "").strip() for k, v in r.items()}
            for r in csv.DictReader(handle)
        ]


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _reason(raw: str, where: str) -> str:
    code = REASON_WORDS_IN.get(raw.strip().lower())
    if code is None:
        raise SystemExit(f"{where}: unknown reason {raw!r}; codes: {', '.join(REASONS)}")
    return code


def _label(row: dict[str, str], where: str) -> tuple | None:
    """(decision, primary, secondary, note) from one CSV row, None without a decision."""
    raw = row.get("decision", "")
    if not raw:
        return None
    decision = DECISION_WORDS_IN.get(raw.lower())
    if decision is None:
        raise SystemExit(f"{where}: unknown decision {raw!r} (Keep, Maybe or Reject)")
    primary_raw = row.get("primary reason") or row.get("reason") or ""
    primary = _reason(primary_raw, where) if primary_raw else None
    secondary = [
        _reason(r, where) for r in (row.get("other reasons") or "").split(";") if r.strip()
    ]
    if decision == "KEEP" and primary:
        # A KEEP's concern is kept as a secondary reason, never dropped.
        secondary, primary = [primary, *secondary], None
    return decision, primary, secondary, row.get("note") or None


# -- import ---------------------------------------------------------------------


def cmd_import(args: argparse.Namespace) -> None:
    from career_agent.storage.db import connect, transaction

    rows = _rows(args.csv)
    panel = None
    if rows and "job id" not in rows[0]:
        if "panel #" not in rows[0]:
            raise SystemExit("the CSV needs a 'Job id' or a 'Panel #' column")
        key = json.loads((args.csv.parent / "key.json").read_text(encoding="utf-8"))
        panel = {str(item["panel"]): item for item in key["items"]}
    labelled = []
    for i, row in enumerate(rows, start=2):
        found = _label(row, f"{args.csv.name} line {i}")
        if found:
            job_id = panel[row["panel #"]]["job_id"] if panel else row["job id"]
            labelled.append((job_id, row, *found))
    print(
        f"{len(labelled)} decisions in {len(rows)} rows: "
        f"{dict(Counter(d for _, _, d, _, _, _ in labelled))}"
    )
    if args.dry_run or not labelled:
        return
    conn = connect(args.db)
    try:
        repo = ReviewDecisionRepo(conn)
        with transaction(conn):
            for job_id, row, decision, primary, secondary, note in labelled:
                if panel:
                    item = next(v for v in panel.values() if v["job_id"] == job_id)
                    exposure = {
                        "review_lane": f"panel:{args.csv.parent.name}",
                        "match_score": item.get("match_score"),
                        "fit_band": item.get("fit_band"),
                        "eligibility_status": item.get("eligibility_status"),
                    }
                else:
                    score = row.get("search fit")
                    exposure = {
                        "match_score": float(score) if score else None,
                        "fit_band": row.get("search fit band") or None,
                        "deep_level": DEEP_WORDS_IN.get(row.get("deep reading level", "").lower()),
                    }
                repo.save(
                    job_id,
                    decision=decision,
                    primary_reason=primary,
                    secondary_reasons=secondary,
                    note=note,
                    origin="PANEL" if panel else "IMPORT",
                    exposure=exposure,
                )
    finally:
        conn.close()
    print(f"saved to {args.db}")


# -- golden ---------------------------------------------------------------------


def _decisions(db: Path) -> list[dict]:
    conn = _read(db)
    try:
        return ReviewDecisionRepo(conn).all()
    except sqlite3.OperationalError:  # a database from before migration 0060
        return []
    finally:
        conn.close()


def cmd_golden(args: argparse.Namespace) -> None:
    path = args.dir / "ranking-golden.json"
    if args.seed_surfaced:
        if path.exists():
            raise SystemExit(f"{path} exists: the frozen seed is written once")
        surfaced = [r["job id"] for r in _rows(args.seed_surfaced)]
        kept = {r["job id"] for r in _rows(args.seed_kept)}
        if not kept <= set(surfaced):
            raise SystemExit("the kept CSV holds ids the surfaced CSV does not")
        conn = _read(args.db)
        try:
            facts = ranking.posting_facts(conn, surfaced)
        finally:
            conn.close()
        group_of = ranking.groups(facts)
        decision = {j: "KEEP" if j in kept else "REJECT" for j in surfaced}
        split = ranking.assign_frozen_splits(group_of, decision, args.seed)
        items = [
            ranking.GoldenItem(
                job_id=j,
                decision=decision[j],
                split=split[group_of[j]],
                group_key=group_of[j],
                source="audit-2026-10-21",
                served_sort="deep:desc",
                served_rank=rank,
            )
            for rank, j in enumerate(surfaced, start=1)
        ]
        golden = ranking.Golden(
            items,
            frozen_at=datetime.now(UTC).isoformat(timespec="seconds"),
            seed=args.seed,
            note="Private: the owner's review decisions. Never commit.",
            extra={
                "seed_files": {
                    str(args.seed_surfaced.name): _sha256(args.seed_surfaced),
                    str(args.seed_kept.name): _sha256(args.seed_kept),
                }
            },
        )
    else:
        golden = ranking.load(path)
    # Later decisions: a frozen posting decided again changes the frozen set
    # (the gate then asks for a deliberate new baseline); a new posting joins
    # its group's split, or the lockbox in the order it was decided. A recall
    # panel's decisions are never taken: they judge postings the review list
    # never showed, another population (E2_PROTOCOL §9).
    later = [
        d
        for d in _decisions(args.db)
        if d["origin"] != "PANEL"
        and (d["created_at"] > golden.frozen_at or d["job_id"] in {i.job_id for i in golden.items})
    ]
    by_id = {i.job_id: i for i in golden.items}
    conn = _read(args.db)
    try:
        new_ids = [d["job_id"] for d in later if d["job_id"] not in by_id]
        facts = ranking.posting_facts(conn, [*by_id, *new_ids]) if new_ids else []
    finally:
        conn.close()
    group_of = ranking.groups(facts) if facts else {}
    frozen_group = {}
    for f in facts:
        if f["job_id"] in by_id:
            frozen_group[group_of[f["job_id"]]] = by_id[f["job_id"]]
    for d in sorted(later, key=lambda d: (d["created_at"], d["job_id"])):
        old = by_id.get(d["job_id"])
        if old is None:
            anchor = frozen_group.get(group_of.get(d["job_id"]))
            group = anchor.group_key if anchor else group_of.get(d["job_id"], d["job_id"])
            split = anchor.split if anchor else ranking.LOCKBOX
        else:
            group, split = old.group_key, old.split
        by_id[d["job_id"]] = ranking.GoldenItem(
            job_id=d["job_id"],
            decision=d["decision"],
            split=split,
            group_key=group,
            primary_reason=d["primary_reason"],
            secondary_reasons=tuple(d["secondary_reasons"]),
            decided_at=d["created_at"],
            source="app" if old is None else old.source,
            served_sort=d["served_sort"] or (old.served_sort if old else None),
            served_rank=d["served_rank"] or (old.served_rank if old else None),
        )
    golden.items = list(by_id.values())
    leaks = ranking.leakage(golden.items)
    if leaks:
        raise SystemExit(f"groups in more than one split: {leaks[:5]}")
    ranking.save(path, golden)
    print(
        f"{path}: {len(golden.items)} postings, "
        f"{dict(sorted(Counter((i.split, i.decision) for i in golden.items).items()))}, "
        f"{len({i.group_key for i in golden.items})} groups"
    )


# -- evaluate -------------------------------------------------------------------


def cmd_evaluate(args: argparse.Namespace) -> int:
    golden = ranking.load(args.dir / "ranking-golden.json")
    if golden.frozen_digest != ranking.frozen_digest(golden.items):
        print("GATE FAILED: the golden file was edited by hand (its digest does not match)")
        return 1
    if args.open_lockbox:
        with open(args.dir / "lockbox-openings.log", "a", encoding="utf-8") as log:
            log.write(f"{datetime.now(UTC).isoformat(timespec='seconds')} opened\n")
    config = _config(args.config_dir)
    wanted = [
        i.job_id for i in golden.items if i.split in ranking.FROZEN_SPLITS or args.open_lockbox
    ]
    conn = _read(args.db)
    try:
        seen = ranking.shown(conn, config, wanted)
    finally:
        conn.close()
    report = ranking.evaluate(golden, seen, open_lockbox=args.open_lockbox)
    report["config"] = f"{config.config_id} v{config.config_version}"
    report["at"] = datetime.now(UTC).isoformat(timespec="seconds")
    print(ranking.table(report))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (args.dir / "reports").mkdir(parents=True, exist_ok=True)
    (args.dir / "reports" / f"{stamp}.json").write_text(json.dumps(report, indent=1), "utf-8")
    baseline = args.dir / "baseline.json"
    if args.save_baseline:
        if args.open_lockbox:
            raise SystemExit("a baseline is never made with the lockbox open")
        body = {
            "report": report,
            "golden": {"frozen_digest": golden.frozen_digest, "labelled": len(golden.frozen())},
        }
        baseline.write_text(json.dumps(body, indent=1), encoding="utf-8")
        print(f"baseline saved: {baseline}")
        return 0
    if not baseline.exists():
        print("no baseline yet: run again with --save-baseline")
        return 0
    saved = json.loads(baseline.read_text(encoding="utf-8"))
    failures = ranking.gate(report, saved["report"], golden, saved["golden"])
    for failure in failures:
        print(f"GATE FAILED: {failure}")
    if not failures:
        print("gate passed")
    return 1 if failures else 0


# -- recall panel (E2) ---------------------------------------------------------------

#: The six strata of E2. Every one is open, at a level and work model the
#: person accepts, never in the review list, and disjoint from the others.
STRATA = {
    "S1_good_strong_to_confirm": (
        "m.fit_band IN ('STRONG','GOOD') AND m.eligibility_status = 'UNRESOLVED'"
        " AND j.posted_at >= :cutoff AND j.last_seen_at >= :live"
    ),
    "S2_good_strong_eligible_older": (
        "m.fit_band IN ('STRONG','GOOD')"
        " AND m.eligibility_status IN ('VERIFIED_ELIGIBLE','LIKELY_ELIGIBLE')"
        " AND j.posted_at < :cutoff AND j.last_seen_at >= :live"
    ),
    "S3_moderate_eligible_full_text": (
        "m.fit_band = 'MODERATE'"
        " AND m.eligibility_status IN ('VERIFIED_ELIGIBLE','LIKELY_ELIGIBLE')"
        " AND j.posted_at >= :cutoff AND r.byte_length >= :full"
    ),
    "S4_weak_eligible_full_text": (
        "m.fit_band = 'WEAK' AND m.eligibility_status IN ('VERIFIED_ELIGIBLE','LIKELY_ELIGIBLE')"
        " AND j.posted_at >= :cutoff AND r.byte_length >= :full"
    ),
    "S5_eligible_excerpt_only": (
        "m.fit_band IN ('MODERATE','WEAK')"
        " AND m.eligibility_status IN ('VERIFIED_ELIGIBLE','LIKELY_ELIGIBLE')"
        " AND j.posted_at >= :cutoff AND COALESCE(r.byte_length, 0) < :full"
    ),
    "S6_strong_not_eligible_control": (
        "m.fit_band = 'STRONG' AND m.eligibility_status = 'VERIFIED_NOT_ELIGIBLE'"
        " AND j.posted_at >= :cutoff"
    ),
}


def cmd_panel_draw(args: argparse.Namespace) -> None:
    from career_agent.storage import revisions
    from career_agent.web import view_prefs

    config = _config(args.config_dir)
    golden_path = args.dir / "ranking-golden.json"
    labelled = ranking.load(golden_path).items if golden_path.exists() else []
    excluded = tuple(level.value for level in config.preferences.seniority.excluded)
    models = tuple(m.upper() for m in config.preferences.remote.excluded_work_models)
    conn = _read(args.db)
    try:
        serving = revisions.resolve(conn, config.config_id, int(config.config_version)).serving
        age = view_prefs.read(conn)["max_age_days"] or 120
        today = datetime.now(UTC).date()
        params = {
            "cid": serving.config_id,
            "ver": serving.config_version,
            "cutoff": (today - timedelta(days=age)).isoformat(),
            "live": (today - timedelta(days=args.live_days)).isoformat(),
            "full": FULL_TEXT,
        }
        levels = ",".join(f"'{level}'" for level in excluded) or "''"
        model_list = ",".join(f"'{m}'" for m in models) or "''"
        base = (
            "SELECT m.job_id, j.company_id, j.content_hash, j.title, c.name AS company,"
            " j.location_raw, j.posted_at, j.url, j.last_seen_at, m.match_score, m.fit_band,"
            " m.eligibility_status, r.byte_length, r.description_text FROM job_match m"
            " JOIN job j ON j.id = m.job_id LEFT JOIN company c ON c.id = j.company_id"
            " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
            " WHERE m.config_id = :cid AND m.config_version = :ver AND j.closed_at IS NULL"
            f" AND m.seniority NOT IN ({levels})"
            f" AND COALESCE(m.work_model, '') NOT IN ({model_list}) AND "
        )
        # A posting whose opportunity is already labelled is not drawn again.
        taken = {tuple(i.group_key.split("|", 1)) for i in labelled}
        labelled_ids = {i.job_id for i in labelled}
        rng = random.Random(args.seed)
        drawn, sizes = [], {}
        for name, where in STRATA.items():
            # One posting per opportunity group (same company and title, or
            # same body): a repost is one decision. Sizes count groups.
            rows = [dict(r) for r in conn.execute(base + where, params)]
            group_of = ranking.groups(rows)
            pool: dict[str, dict] = {}
            for r in rows:
                g = group_of[r["job_id"]]
                if r["job_id"] in labelled_ids or tuple(g.split("|", 1)) in taken:
                    continue
                if g not in pool or r["job_id"] < pool[g]["job_id"]:
                    pool[g] = {**r, "group_key": g}
            ordered = sorted(pool.values(), key=lambda r: r["job_id"])
            sizes[name] = len(ordered)
            picked = rng.sample(ordered, min(args.per_stratum, len(ordered)))
            for row in picked:
                drawn.append({**row, "stratum": name})
                taken.add(tuple(row["group_key"].split("|", 1)))
            print(f"{name}: {len(ordered)} opportunity groups, drew {len(picked)}")
    finally:
        conn.close()
    rng.shuffle(drawn)
    out = args.out or ROOT / "data" / "evaluation" / f"recall-panel-{datetime.now(UTC):%Y%m%d}"
    out.mkdir(parents=True, exist_ok=False)
    with open(out / "panel.csv", "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\r\n")
        writer.writerow(
            [
                "Panel #",
                "Title",
                "Company",
                "Location",
                "Posted",
                "URL",
                "Description note",
                "Decision",
                "Primary reason",
                "Other reasons",
                "Note",
                "Job description",
            ]
        )
        for i, row in enumerate(drawn, start=1):
            thin = (row["byte_length"] or 0) < FULL_TEXT
            writer.writerow(
                [
                    i,
                    row["title"],
                    row["company"],
                    row["location_raw"],
                    (row["posted_at"] or "")[:10],
                    row["url"],
                    INCOMPLETE if thin else "",
                    "",
                    "",
                    "",
                    "",
                    row["description_text"] or "",
                ]
            )
    key = {
        "drawn_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "seed": args.seed,
        "params": params,
        "sizes": sizes,
        "per_stratum": args.per_stratum,
        "items": [
            {
                "panel": i,
                "job_id": r["job_id"],
                "group_key": r["group_key"],
                "stratum": r["stratum"],
                "inclusion_probability": min(1.0, args.per_stratum / sizes[r["stratum"]]),
                "match_score": r["match_score"],
                "fit_band": r["fit_band"],
                "eligibility_status": r["eligibility_status"],
                "byte_length": r["byte_length"],
                "last_seen_at": r["last_seen_at"],
            }
            for i, r in enumerate(drawn, start=1)
        ],
    }
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(
        f"{len(drawn)} postings to label in {out / 'panel.csv'}"
        " (key.json holds the strata and scores: do not open it while labelling)"
    )


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return (0.0, 1.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (max(0.0, centre - half), min(1.0, centre + half))


def panel_summary(key: dict, labels: dict[str, tuple]) -> dict:
    """Per stratum: counts, rates, Wilson 95% intervals, estimated hidden
    KEEP and review-worthy (KEEP + MAYBE) postings, and reasons."""
    out = {}
    for name, size in key["sizes"].items():
        got = [
            labels[str(i["panel"])]
            for i in key["items"]
            if i["stratum"] == name and str(i["panel"]) in labels
        ]
        n = len(got)
        counts = Counter(d for d, *_ in got)
        entry: dict = {
            "pool": size,
            "drawn": sum(1 for i in key["items"] if i["stratum"] == name),
            "labelled": n,
        }
        for label, positives in (
            ("keep", ("KEEP",)),
            ("maybe", ("MAYBE",)),
            ("reject", ("REJECT",)),
            ("keep_or_maybe", ("KEEP", "MAYBE")),
        ):
            k = sum(counts[p] for p in positives)
            low, high = _wilson(k, n)
            entry[label] = {"count": k, "rate": k / n if n else None, "ci95": [low, high]}
        # The same, without the MAYBEs that only say the posting could not be
        # judged (E2_PROTOCOL §8, S5): unreadable is not the same as relevant.
        k = counts["KEEP"] + sum(
            1 for d, p, _, _ in got if d == "MAYBE" and p != "INSUFFICIENT_INFORMATION"
        )
        low, high = _wilson(k, n)
        entry["keep_or_maybe_excluding_insufficient"] = {
            "count": k,
            "rate": k / n if n else None,
            "ci95": [low, high],
        }
        for label, src in (
            ("estimated_hidden_keep", "keep"),
            ("estimated_hidden_review_worthy", "keep_or_maybe"),
            (
                "estimated_hidden_review_worthy_excluding_insufficient",
                "keep_or_maybe_excluding_insufficient",
            ),
        ):
            rate, (low, high) = entry[src]["rate"], entry[src]["ci95"]
            entry[label] = {
                "estimate": None if rate is None else rate * size,
                "ci95": [low * size, high * size],
            }
        entry["primary_reasons"] = dict(
            Counter(p or "NO_REASON" for d, p, _, _ in got if d != "KEEP").most_common()
        )
        entry["secondary_reasons"] = dict(Counter(r for _, _, s, _ in got for r in s).most_common())
        out[name] = entry
    return out


def cmd_panel_report(args: argparse.Namespace) -> None:
    key = json.loads((args.panel / "key.json").read_text(encoding="utf-8"))
    labels = {}
    for i, row in enumerate(_rows(args.panel / "panel.csv"), start=2):
        found = _label(row, f"panel.csv line {i}")
        if found:
            labels[row["panel #"]] = found
    summary = panel_summary(key, labels)
    (args.panel / "report.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"{len(labels)} of {len(key['items'])} labelled")
    pct = lambda x: "-" if x is None else f"{x:5.1%}"  # noqa: E731
    for name, e in summary.items():
        print(f"\n{name}: pool {e['pool']} groups, labelled {e['labelled']} of {e['drawn']}")
        for label in (
            "keep",
            "maybe",
            "reject",
            "keep_or_maybe",
            "keep_or_maybe_excluding_insufficient",
        ):
            v = e[label]
            print(
                f"  {label:38} {v['count']:3}  {pct(v['rate'])}"
                f"  (95% CI {v['ci95'][0]:.1%}-{v['ci95'][1]:.1%})"
            )
        for label in (
            "estimated_hidden_keep",
            "estimated_hidden_review_worthy",
            "estimated_hidden_review_worthy_excluding_insufficient",
        ):
            v = e[label]
            est = "-" if v["estimate"] is None else f"{v['estimate']:.0f}"
            print(f"  {label:54} {est} (95% CI {v['ci95'][0]:.0f}-{v['ci95'][1]:.0f})")
        print(f"  primary reasons: {e['primary_reasons']}")
        print(f"  other reasons:   {e['secondary_reasons']}")
    print(f"\nsaved {args.panel / 'report.json'}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "personal.db")
    parser.add_argument("--config-dir", type=Path, default=ROOT / "config")
    parser.add_argument("--dir", type=Path, default=GOLDEN_DIR, help="golden set folder")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("import")
    p.add_argument("csv", type=Path)
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("golden")
    p.add_argument("--seed-surfaced", type=Path)
    p.add_argument("--seed-kept", type=Path)
    p.add_argument("--seed", type=int, default=20261021)
    p = sub.add_parser("evaluate")
    p.add_argument("--save-baseline", action="store_true")
    p.add_argument("--open-lockbox", action="store_true")
    p = sub.add_parser("panel-draw")
    p.add_argument("--per-stratum", type=int, default=25)
    p.add_argument("--live-days", type=int, default=10)
    p.add_argument("--seed", type=int, default=20261021)
    p.add_argument("--out", type=Path)
    p = sub.add_parser("panel-report")
    p.add_argument("panel", type=Path)
    args = parser.parse_args(argv)
    if args.command == "golden" and bool(args.seed_surfaced) != bool(args.seed_kept):
        parser.error("--seed-surfaced and --seed-kept go together")
    handler = {
        "import": cmd_import,
        "golden": cmd_golden,
        "evaluate": cmd_evaluate,
        "panel-draw": cmd_panel_draw,
        "panel-report": cmd_panel_report,
    }[args.command]
    return int(handler(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())

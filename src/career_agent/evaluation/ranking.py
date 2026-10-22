"""How good the list's ORDER is, measured against the person's own decisions.

THE LABELS. The golden set holds the person's review decisions
(`review_decision`, migration 0060), seeded once from the 2026-10-21 audit
(181 postings: 17 KEEP, 164 REJECT). KEEP means "worth keeping for
consideration or application"; it includes stretches and middling postings
and is never read as "strong fit". MAYBE is kept apart from REJECT.

THE GROUPS. Variants of one opportunity must never sit on both sides of a
split. A posting's group is the connected component of:

1. a canonical vacancy identity -- none exists in the schema today;
2. the canonical posting identity -- `job.id`, which already folds the same
   advert seen through several sources (`job_discovery_source`), plus the
   same body (`content_hash`) at the same company;
3. the fallback: the same company and the same normalised title.

Limits: agency reposts under reworded titles, and the same vacancy at two
employer names, escape the grouping; two real requisitions with one title at
one company are merged (conservative for leakage).

THE SPLITS. `development` may be looked at and tuned on. `regression` is a
fixed set the release gate reads; it is NOT a blind holdout (it was inspected
in the audits). `prospective_lockbox` starts empty and receives decisions made
after the freeze, in time order, for postings whose group is not already in
the other two; nothing in CI or the gate reads it.

THE ORDERS. Each policy orders the labelled postings among themselves:
`score` (the Jobs list default), `deep_current` (`sort=deep` as shipped), two
offline alternatives that are hypotheses, not choices (`deep_unread_middle`,
`deep_exploration`), and `served_history` (the order the postings were
actually served in, when recorded). Ranking labelled postings among
themselves measures ORDER: its recall is `recall_within_labeled_surface`,
never market or corpus recall.

Nothing here writes to a database, calls a model or opens a socket.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

SCHEMA = 2
FROZEN_SPLITS = ("development", "regression")
LOCKBOX = "prospective_lockbox"
#: Share of groups (stratified by "has a KEEP") placed in `regression`.
REGRESSION_SHARE = 0.3
#: Positive decisions per perspective. They are reported apart, never mixed.
PERSPECTIVES = {"strict_pursuit": ("KEEP",), "review_worthy": ("KEEP", "MAYBE")}
CUTS = (10, 20, 50)
RANK_BUCKETS = ((1, 10), (11, 20), (21, 50), (51, 100), (101, 10**9))
POLICIES = ("score", "deep_current", "deep_unread_middle", "deep_exploration", "served_history")
#: The orders the product ships; the gate guards these.
SHIPPED = ("score", "deep_current")
#: `deep_exploration`: every Nth slot goes to the best unread posting left.
#: A fixed, untuned illustration of reserved exploration slots.
EXPLORATION_EVERY = 4
#: Gate: a material NDCG@20 fall on `regression` (strict pursuit), or this
#: many fewer regression KEEPs in a top 20. With ~5 KEEPs in `regression`,
#: one KEEP moving a few places moves NDCG@20 by about 0.05-0.10; one leaving
#: the top 20 moves it by 0.15-0.35. The threshold catches the second only.
GATE_NDCG_DROP = 0.15
GATE_KEEP_LOSS = 2
#: Gate: more labelled postings than this share without a score now.
GATE_MAX_MISSING = 0.10
DECISION_RANK = {"KEEP": 2, "MAYBE": 1, "REJECT": 0}


@dataclass(frozen=True, slots=True)
class GoldenItem:
    job_id: str
    decision: str
    split: str
    group_key: str
    primary_reason: str | None = None
    secondary_reasons: tuple[str, ...] = ()
    decided_at: str | None = None
    source: str = "app"
    #: The order it was actually served in when decided, when recorded.
    served_sort: str | None = None
    served_rank: int | None = None


@dataclass(slots=True)
class Golden:
    items: list[GoldenItem]
    frozen_at: str
    seed: int
    note: str = ""
    frozen_digest: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def frozen(self) -> list[GoldenItem]:
        return [i for i in self.items if i.split in FROZEN_SPLITS]


def frozen_digest(items: list[GoldenItem]) -> str:
    """What may never change silently: every frozen posting, label and split."""
    rows = sorted(
        (i.job_id, i.decision, i.split, i.group_key) for i in items if i.split in FROZEN_SPLITS
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def load(path: Path) -> Golden:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError(f"{path}: golden set schema {data.get('schema')!r}, expected {SCHEMA}")
    items = [
        GoldenItem(**{**raw, "secondary_reasons": tuple(raw.get("secondary_reasons") or ())})
        for raw in data["items"]
    ]
    return Golden(
        items,
        data["frozen_at"],
        data["seed"],
        data.get("note", ""),
        data.get("frozen_digest", ""),
        data.get("extra", {}),
    )


def save(path: Path, golden: Golden) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    golden.frozen_digest = frozen_digest(golden.items)
    body = {
        "schema": SCHEMA,
        "frozen_at": golden.frozen_at,
        "seed": golden.seed,
        "note": golden.note,
        "frozen_digest": golden.frozen_digest,
        "extra": golden.extra,
        "items": [
            {**asdict(i), "secondary_reasons": list(i.secondary_reasons)} for i in golden.items
        ],
    }
    path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")


# -- groups ------------------------------------------------------------------------

_NOISE = re.compile(r"\b(remote|remoto|job|vaga)\b")
#: The shortest body that identifies a vacancy (the Deep Reading floor).
SAME_BODY_MIN = 200


def normalise_title(title: str) -> str:
    folded = re.sub(r"[^0-9a-z]+", " ", (title or "").casefold())
    return " ".join(_NOISE.sub(" ", folded).split())


def groups(postings: list[dict[str, Any]]) -> dict[str, str]:
    """`job_id -> group_key` for postings with `job_id`, `company_id`, `title`,
    `content_hash` and `byte_length`: same company AND (same normalised title
    OR same body). An empty or excerpt-sized body is no evidence of the same
    vacancy (every bodiless posting at one company would share it), so the
    body joins postings only when it is at least `SAME_BODY_MIN` long."""
    parent = {p["job_id"]: p["job_id"] for p in postings}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    by: dict[tuple, list[str]] = defaultdict(list)
    for p in postings:
        company = p.get("company_id") or f"none:{p['job_id']}"
        by[("t", company, normalise_title(p.get("title") or ""))].append(p["job_id"])
        if p.get("content_hash") and (p.get("byte_length") or 0) >= SAME_BODY_MIN:
            by[("h", company, p["content_hash"])].append(p["job_id"])
    for members in by.values():
        for other in members[1:]:
            a, b = find(members[0]), find(other)
            if a != b:
                parent[max(a, b)] = min(a, b)
    info = {p["job_id"]: p for p in postings}
    key = {}
    for job_id in parent:
        root = info[find(job_id)]
        key[job_id] = (
            f"{root.get('company_id') or 'none'}|{normalise_title(root.get('title') or '')}"
        )
    return key


def posting_facts(conn: sqlite3.Connection, job_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for start in range(0, len(job_ids), 500):
        chunk = job_ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        out += [
            dict(
                zip(
                    ("job_id", "company_id", "title", "content_hash", "byte_length"), r, strict=True
                )
            )
            for r in conn.execute(
                "SELECT j.id, j.company_id, j.title, j.content_hash, r.byte_length FROM job j"
                f" LEFT JOIN job_raw r ON r.content_hash = j.content_hash WHERE j.id IN ({marks})",
                chunk,
            )
        ]
    return out


def assign_frozen_splits(group_of: dict[str, str], decision: dict[str, str], seed: int) -> dict:
    """`group_key -> development | regression`, stratified by "the group holds
    a KEEP", deterministic for a seed: groups ordered by a seeded hash."""
    keep_groups = {g for j, g in group_of.items() if decision[j] == "KEEP"}
    out = {}
    for stratum in (sorted(keep_groups), sorted(set(group_of.values()) - keep_groups)):
        ordered = sorted(stratum, key=lambda g: hashlib.sha1(f"{seed}:{g}".encode()).hexdigest())
        cut = round(len(ordered) * REGRESSION_SHARE)
        for i, g in enumerate(ordered):
            out[g] = "regression" if i < cut else "development"
    return out


def leakage(items: list[GoldenItem]) -> list[str]:
    """Groups that sit in more than one split."""
    splits: dict[str, set[str]] = defaultdict(set)
    for i in items:
        splits[i.group_key].add(i.split)
    return sorted(g for g, s in splits.items() if len(s) > 1)


# -- metrics -------------------------------------------------------------------------


def metrics(decisions: list[str], positive: tuple[str, ...], cuts: tuple[int, ...] = CUTS) -> dict:
    """Precision, recall within the labelled surface and binary NDCG at each cut,
    for decisions in ranked order. A cut longer than the list is the whole list."""
    hits = [d in positive for d in decisions]
    total = sum(hits)
    out: dict[str, Any] = {
        "n": len(decisions),
        "positives": total,
        "prevalence": total / len(decisions) if decisions else None,
    }
    for k in cuts:
        top = min(k, len(decisions))
        out[f"P@{k}"] = sum(hits[:top]) / top if top else None
        out[f"recall_within_labeled_surface@{k}"] = sum(hits[:top]) / total if total else None
        dcg = sum(1 / math.log2(i + 2) for i, h in enumerate(hits[:top]) if h)
        idcg = sum(1 / math.log2(i + 2) for i in range(min(total, top)))
        out[f"NDCG@{k}"] = dcg / idcg if idcg else None
    ranks = [i + 1 for i, h in enumerate(hits) if h]
    out["positive_ranks"] = ranks
    out["rank_distribution"] = {
        (f"{lo}-{hi}" if hi < 10**9 else f"{lo}+"): sum(1 for r in ranks if lo <= r <= hi)
        for lo, hi in RANK_BUCKETS
    }
    return out


# -- what the product shows -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Shown:
    """One labelled posting as the product reads it now."""

    job_id: str
    title: str
    company: str
    score: int
    band: str
    eligibility: str
    deep_level: str | None
    deep_stale: bool


def shown(conn: sqlite3.Connection, config: Any, job_ids: list[str]) -> dict[str, Shown]:
    """The served score and deep level of each posting, as the Jobs list reads them:
    the served revision (`storage.revisions`) and `deep_api.deep_levels`."""
    from career_agent.storage import revisions
    from career_agent.web.deep_api import deep_levels

    serving = revisions.resolve(conn, config.config_id, int(config.config_version)).serving
    if serving is None:
        return {}
    levels = deep_levels(SimpleNamespace(search_config=lambda: config), conn)
    out: dict[str, Shown] = {}
    for start in range(0, len(job_ids), 500):
        chunk = job_ids[start : start + 500]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            "SELECT m.job_id, j.title, c.name, m.match_score, m.fit_band, m.eligibility_status"
            " FROM job_match m JOIN job j ON j.id = m.job_id"
            " LEFT JOIN company c ON c.id = j.company_id"
            f" WHERE m.config_id = ? AND m.config_version = ? AND m.job_id IN ({marks})",
            (serving.config_id, serving.config_version, *chunk),
        ).fetchall()
        for job_id, title, company, score, band, eligibility in rows:
            level, stale, _state = levels.get(job_id, (None, False, None))
            out[job_id] = Shown(
                job_id, title or "", company or "", int(score), band, eligibility, level, stale
            )
    return out


def order(items: list[Shown], policy: str, served: dict[str, int] | None = None) -> list[Shown]:
    """One policy's order. `score` and `deep_current` mirror the Jobs list
    (`mvp_repo._order_by`, `_deep_order`); the others are offline hypotheses."""
    from career_agent.semantic.deep_assess import RECOMMENDATIONS

    rank = {level: i for i, level in enumerate(RECOMMENDATIONS)}
    by_score = sorted(items, key=lambda s: (-s.score, s.job_id))
    if policy == "score":
        return by_score
    if policy == "deep_current":

        def key(s: Shown) -> tuple:
            tier = 2 if s.deep_level is None else (1 if s.deep_stale else 0)
            return (tier, rank.get(s.deep_level, len(rank)), -s.score, s.job_id)

        return sorted(items, key=key)
    if policy == "deep_unread_middle":
        # Apply, preparing, check first (and the other aligned levels), then
        # UNREAD, then low and different main activity. An out-of-date reading
        # is placed by the level it had.
        unread_at = rank["LOW"] - 0.5

        def key(s: Shown) -> tuple:
            place = unread_at if s.deep_level is None else rank.get(s.deep_level, len(rank))
            return (place, -s.score, s.job_id)

        return sorted(items, key=key)
    if policy == "deep_exploration":
        main = [s for s in order(items, "deep_current") if s.deep_level is not None]
        unread = [s for s in by_score if s.deep_level is None]
        out: list[Shown] = []
        while main or unread:
            reserved = (len(out) + 1) % EXPLORATION_EVERY == 0
            source = unread if (reserved and unread) or not main else main
            out.append(source.pop(0))
        return out
    if policy == "served_history":
        known = [s for s in items if served and s.job_id in served]
        return sorted(known, key=lambda s: (served[s.job_id], s.job_id))
    raise ValueError(f"unknown policy: {policy!r}")


def collapse(ranked: list[Shown], label: dict[str, GoldenItem]) -> list[str]:
    """Duplicate-adjusted decisions: one slot per group, at its first member's
    place, carrying the group's best decision (KEEP > MAYBE > REJECT)."""
    best: dict[str, str] = {}
    for s in ranked:
        g, d = label[s.job_id].group_key, label[s.job_id].decision
        if g not in best or DECISION_RANK[d] > DECISION_RANK[best[g]]:
            best[g] = d
    seen, out = set(), []
    for s in ranked:
        g = label[s.job_id].group_key
        if g not in seen:
            seen.add(g)
            out.append(best[g])
    return out


def _bootstrap(pool: list[Shown], label: dict, policy: str, served: dict, seed: int) -> dict:
    """Group-cluster bootstrap 95% intervals of strict P@20 and NDCG@20."""
    by_group: dict[str, list[Shown]] = defaultdict(list)
    for s in pool:
        by_group[label[s.job_id].group_key].append(s)
    keys = sorted(by_group)
    rng = random.Random(seed)
    draws: dict[str, list[float]] = {"P@20": [], "NDCG@20": []}
    for b in range(1000):
        sample = []
        for n, g in enumerate(rng.choice(keys) for _ in keys):
            sample += [
                Shown(f"{s.job_id}#{b}.{n}", *[getattr(s, f) for f in Shown.__slots__[1:]])
                for s in by_group[g]
            ]
        names = {x.job_id: label[x.job_id.split("#")[0]] for x in sample}
        hist = {
            x.job_id: served[x.job_id.split("#")[0]]
            for x in sample
            if x.job_id.split("#")[0] in served
        }
        ranked = order(sample, policy, hist)
        m = metrics([names[x.job_id].decision for x in ranked], PERSPECTIVES["strict_pursuit"])
        for k in draws:
            if m[k] is not None:
                draws[k].append(m[k])
    return {
        k: [round(sorted(v)[int(0.025 * len(v))], 3), round(sorted(v)[int(0.975 * len(v)) - 1], 3)]
        if v
        else None
        for k, v in draws.items()
    }


def evaluate(
    golden: Golden, seen: dict[str, Shown], *, open_lockbox: bool = False, bootstrap: bool = True
) -> dict[str, Any]:
    """Every policy x population x perspective, raw and duplicate-adjusted."""
    label = {i.job_id: i for i in golden.items}
    served = {i.job_id: i.served_rank for i in golden.items if i.served_rank is not None}
    splits = list(FROZEN_SPLITS) + ([LOCKBOX] if open_lockbox else [])
    populations = {"labeled_surface": set(FROZEN_SPLITS)} | {s: {s} for s in splits}
    report: dict[str, Any] = {
        "labelled": len(golden.frozen()),
        "lockbox_size": sum(1 for i in golden.items if i.split == LOCKBOX),
        "lockbox_opened": open_lockbox,
        "scored_now": sum(1 for j in seen if label[j].split in splits),
        "missing": sorted(
            i.job_id for i in golden.items if i.split in splits and i.job_id not in seen
        ),
        "label_conflicts": sorted(
            g for g, ds in _decisions_by_group(golden.frozen()).items() if len(ds) > 1
        ),
        "leakage": leakage(golden.items),
        "populations": {},
    }
    for name, members in populations.items():
        pool = [s for s in seen.values() if label[s.job_id].split in members]
        per: dict[str, Any] = {}
        for policy in POLICIES:
            ranked = order(pool, policy, served)
            if not ranked:
                continue
            entry: dict[str, Any] = {"n_ranked": len(ranked)}
            for perspective, positive in PERSPECTIVES.items():
                entry[perspective] = metrics([label[s.job_id].decision for s in ranked], positive)
                entry[f"{perspective}_duplicate_adjusted"] = metrics(
                    collapse(ranked, label), positive
                )
            top = ranked[:20]
            entry["top20_not_eligible"] = sum(
                1 for s in top if s.eligibility == "VERIFIED_NOT_ELIGIBLE"
            )
            entry["top20_reasons"] = dict(
                Counter(
                    label[s.job_id].primary_reason or "NO_REASON"
                    for s in top
                    if label[s.job_id].decision != "KEEP"
                )
            )
            if bootstrap and name == "labeled_surface":
                entry["strict_pursuit_ci95"] = _bootstrap(pool, label, policy, served, golden.seed)
            per[policy] = entry
        report["populations"][name] = per
    return report


def _decisions_by_group(items: list[GoldenItem]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = defaultdict(set)
    for i in items:
        out[i.group_key].add(i.decision)
    return out


def gate(
    report: dict[str, Any],
    baseline: dict[str, Any],
    golden: Golden,
    baseline_golden: dict[str, Any],
) -> list[str]:
    """Why this run fails the release gate. Large regressions only: with ~5
    KEEPs in `regression` nothing finer is measurable."""
    failures = []
    if golden.frozen_digest != baseline_golden["frozen_digest"]:
        failures.append("the frozen golden labels or splits changed since the baseline")
    if len(golden.frozen()) < baseline_golden["labelled"]:
        failures.append("frozen labelled postings were lost since the baseline")
    if report["leakage"]:
        failures.append(f"groups in more than one split: {report['leakage'][:5]}")
    if report["lockbox_opened"]:
        failures.append("the prospective lockbox was read; the gate never reads it")
    if len(report["missing"]) > GATE_MAX_MISSING * max(1, report["labelled"]):
        failures.append(f"{len(report['missing'])} labelled postings have no score now")
    for policy in SHIPPED:
        now = report["populations"]["regression"][policy]["strict_pursuit"]
        then = baseline["populations"]["regression"][policy]["strict_pursuit"]
        ndcg_now, ndcg_then = now["NDCG@20"], then["NDCG@20"]
        if ndcg_now is not None and ndcg_then is not None and ndcg_now < ndcg_then - GATE_NDCG_DROP:
            failures.append(
                f"{policy}: regression NDCG@20 {ndcg_now:.3f} fell more"
                f" than {GATE_NDCG_DROP} below {ndcg_then:.3f}"
            )
        kept_now = sum(1 for r in now["positive_ranks"] if r <= 20)
        kept_then = sum(1 for r in then["positive_ranks"] if r <= 20)
        if kept_then - kept_now >= GATE_KEEP_LOSS:
            failures.append(
                f"{policy}: {kept_then - kept_now} fewer regression KEEPs in the top 20"
            )
    for name, per in report["populations"].items():
        for policy, entry in per.items():
            if policy in SHIPPED and entry["top20_not_eligible"]:
                failures.append(f"{policy}: a posting that rules her out is in the {name} top 20")
    return failures


def table(report: dict[str, Any]) -> str:
    """The report as lines a person reads (strict pursuit, then review-worthy)."""
    lines = [
        f"labelled {report['labelled']} (lockbox {report['lockbox_size']}, "
        f"{'OPENED' if report['lockbox_opened'] else 'closed'}), scored now {report['scored_now']},"
        f" missing {len(report['missing'])}, label conflicts {len(report['label_conflicts'])},"
        f" leakage {len(report['leakage'])}"
    ]
    for perspective in PERSPECTIVES:
        lines.append(f"-- {perspective} (positive = {' + '.join(PERSPECTIVES[perspective])})")
        for name, per in report["populations"].items():
            for policy, entry in per.items():
                m = entry[perspective]
                d = entry[f"{perspective}_duplicate_adjusted"]
                cells = " ".join(
                    f"{k.replace('recall_within_labeled_surface', 'R')}={m[k]:.2f}"
                    if m[k] is not None
                    else f"{k}=-"
                    for k in (
                        "P@10",
                        "P@20",
                        "P@50",
                        "recall_within_labeled_surface@20",
                        "recall_within_labeled_surface@50",
                        "NDCG@20",
                        "NDCG@50",
                    )
                )
                ci = entry.get("strict_pursuit_ci95") if perspective == "strict_pursuit" else None
                dedup = "-" if d["NDCG@20"] is None else f"{d['NDCG@20']:.2f}"
                lines.append(
                    f"{name:16} {policy:19} n={m['n']:3} pos={m['positives']:2} {cells}"
                    f" | dedup NDCG@20={dedup}"
                    + (f" | CI95 P@20={ci['P@20']} NDCG@20={ci['NDCG@20']}" if ci else "")
                    + f" | ranks {m['rank_distribution']}"
                )
    return "\n".join(lines)

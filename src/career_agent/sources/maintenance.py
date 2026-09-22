"""Candidate-independent admission planning from persisted collection evidence.

No network, mutations or configuration migration. Every inventory item has an
answer, including items this version cannot execute. Estimates are allowances,
not promises. See ADR-0031.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from career_agent.providers.registry import board_providers, poll_interval_seconds
from career_agent.sources.catalogue import resolve

FEED_EXECUTORS = frozenset({"avlis", "remotive", "jobicy", "workingnomads"})
OWNER_RUN = frozenset({"remoteok", "getonbrd"})


def timestamp(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


@contextmanager
def read_only(path: Path) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    try:
        yield conn
    finally:
        conn.close()


@dataclass(frozen=True)
class Sample:
    key: str
    provider: str
    seconds: float
    postings: int
    evidence: str
    at: str


@dataclass(frozen=True)
class Item:
    key: str
    provider: str
    board_id: str | None
    last_success: str | None
    seconds: float
    evidence: str
    postings: int | None = None
    blocked: str | None = None
    last_attempt: str | None = None
    eligible_after: float | None = None

    @property
    def lane(self) -> str:
        if self.evidence.startswith("UNKNOWN"):
            return "UNKNOWN"
        return "HEAVY" if self.seconds > 120 else "FAST"


def history(conn: sqlite3.Connection) -> list[Sample]:
    """Read direct timers, or conservatively reconstruct the latest stamp chain.

    Only use an entire successful board prefix whose current stamps lie inside
    its original run interval. A later collection invalidates reconstruction;
    failed boards break the chain. No run-average masquerades as board timing.
    """
    stamps = {
        f"{r['provider']}:{r['board_identifier']}": r["last_collected_at"]
        for r in conn.execute(
            "SELECT provider, board_identifier, last_collected_at FROM source_board"
        )
    }
    runs = conn.execute(
        "SELECT * FROM pipeline_run WHERE stage = 'collect' OR stage LIKE 'collect-%'"
        " ORDER BY started_at, id"
    ).fetchall()
    samples: list[Sample] = []
    for index, run in enumerate(runs):
        stats = json.loads(run["stats_json"] or "{}")
        start = timestamp(run["started_at"])
        # An interrupted run's next collection is a conservative upper bound.
        upper = (
            timestamp(run["finished_at"])
            if run["finished_at"]
            else (timestamp(runs[index + 1]["started_at"]) if index + 1 < len(runs) else start)
        )
        if run["stage"] != "collect":
            if run["finished_at"] and run["status"] == "OK" and not stats.get("deferred_reason"):
                duration = stats.get("elapsed_ms", 0) / 1000
                if duration > 0:
                    provider = run["stage"].removeprefix("collect-")
                    samples.append(
                        Sample(
                            f"feed:{provider}",
                            provider,
                            duration,
                            stats.get("postings_seen", stats.get("postings_observed", 0)),
                            "MEASURED_FEED_PASS",
                            run["finished_at"],
                        )
                    )
            continue
        previous: float | None = start
        for board in stats.get("by_board", []):
            key = f"{board['provider']}:{board['board_identifier']}"
            stamp = stamps.get(key)
            end = timestamp(stamp) if stamp else None
            seconds = board.get("elapsed_ms", 0) / 1000
            evidence = "MEASURED_BOARD"
            if (
                not seconds
                and previous is not None
                and end is not None
                and previous <= end <= upper
                and board.get("boards_succeeded") == 1
            ):
                seconds = max(1.0, end - previous)
                evidence = "RECONSTRUCTED_INTERVAL"
            if seconds > 0:
                if board.get("deferred_reason"):
                    evidence = "INTERRUPTED_LOWER_BOUND"
                samples.append(
                    Sample(
                        key,
                        board["provider"],
                        seconds,
                        board.get("postings_observed", 0),
                        evidence,
                        board.get("finished_at") or run["started_at"],
                    )
                )
            previous = end if end is not None and start <= end <= upper else None
    return samples


def inventory(conn: sqlite3.Connection, catalogue: Path | None = None) -> list[Item]:
    samples = history(conn)
    by_key: dict[str, list[Sample]] = defaultdict(list)
    by_provider: dict[str, list[Sample]] = defaultdict(list)
    for sample in samples:
        by_key[sample.key].append(sample)
        by_provider[sample.provider].append(sample)
    policy = {s.provider: s for s in resolve(path=catalogue) if s.provider}
    families = set(board_providers())
    feed_attempts = {
        r["stage"].removeprefix("collect-"): timestamp(r["attempt"])
        for r in conn.execute(
            "SELECT stage, MAX(started_at) AS attempt FROM pipeline_run"
            " WHERE stage LIKE 'collect-%' GROUP BY stage"
        )
    }
    attempts: dict[str, str] = {}
    for row in conn.execute(
        "SELECT started_at, stats_json FROM pipeline_run WHERE stage = 'source-maintenance'"
        " ORDER BY started_at"
    ):
        receipt = json.loads(row["stats_json"] or "{}")
        for result in receipt.get("results", []):
            if result.get("attempted"):
                attempts[result["key"]] = row["started_at"]
        if receipt.get("active"):
            attempts[receipt["active"]] = row["started_at"]

    def item(key: str, provider: str, board_id: str | None, stamp: str | None) -> Item:
        own = by_key[key][-5:]
        family = by_provider[provider]
        if own:
            seconds = (
                max(s.seconds * (2 if s.evidence == "INTERRUPTED_LOWER_BOUND" else 1) for s in own)
                * 1.25
                + 2
            )
            evidence = own[-1].evidence
        elif family:
            seconds = max(s.seconds for s in family) * 1.25 + 2
            evidence = "UNKNOWN_FAMILY_ALLOWANCE"
        else:
            seconds, evidence = 120.0, "UNKNOWN_EXPLORATION_ALLOWANCE"
        source = policy.get(provider)
        blocked = None
        if provider in OWNER_RUN:
            blocked = "OWNER_RUN_ONLY"
        elif source is None or source.permission.value == "FORBIDDEN" or source.collection_blocker:
            blocked = "SOURCE_POLICY_REQUIRES_ACTION"
        elif board_id is None and provider not in FEED_EXECUTORS:
            blocked = "FEED_EXECUTOR_NOT_BUDGETED_YET"
        return Item(
            key,
            provider,
            board_id,
            stamp,
            math.ceil(seconds),
            evidence,
            own[-1].postings if own else None,
            blocked,
            attempts.get(key),
            feed_attempts[provider] + poll_interval_seconds(provider)
            if provider in feed_attempts and poll_interval_seconds(provider)
            else None,
        )

    items = [
        item(
            f"{r['provider']}:{r['board_identifier']}",
            r["provider"],
            r["id"],
            r["last_collected_at"],
        )
        for r in conn.execute("SELECT * FROM source_board WHERE active = 1")
        if r["provider"] in families
    ]
    for provider in sorted(set(policy) - families):
        own = by_key[f"feed:{provider}"]
        items.append(item(f"feed:{provider}", provider, None, own[-1].at if own else None))
    return items


def plan(
    items: list[Item],
    budget_seconds: float,
    *,
    now: float | None = None,
    stale_hours: float = 24,
    max_items: int = 100,
    round_number: int = 0,
) -> dict[str, Any]:
    if not math.isfinite(budget_seconds) or budget_seconds <= 0 or max_items < 1:
        raise ValueError("Budget and item bound must be positive and finite.")
    if not math.isfinite(stale_hours) or stale_hours < 0:
        raise ValueError("Freshness age must be finite and nonnegative.")
    now = datetime.now(UTC).timestamp() if now is None else now
    decisions: dict[str, dict[str, Any]] = {}
    eligible: list[Item] = []
    for item in items:
        age = None if item.last_success is None else max(0, now - timestamp(item.last_success))
        reason = item.blocked
        if not reason and item.eligible_after is not None and now < item.eligible_after:
            reason = "PROVIDER_COOLDOWN"
        if not reason and age is not None and age < stale_hours * 3600:
            reason = "FRESH"
        # A failed/interrupted attempt gets a cooldown, then joins the tail of
        # the never-successful queue. One broken source cannot monopolize it.
        if not reason and item.last_attempt and now - timestamp(item.last_attempt) < 3600:
            reason = "ATTEMPT_COOLDOWN"
        decisions[item.key] = {
            **asdict(item),
            "lane": item.lane,
            "age_seconds": age,
            "selected": False,
            "reason": reason or "TIME_BUDGET",
        }
        if not reason:
            eligible.append(item)

    def order(item: Item) -> tuple[float, float, str]:
        oldest = max(
            timestamp(item.last_success) if item.last_success else 0,
            timestamp(item.last_attempt) if item.last_attempt else 0,
        )
        return oldest, -float(item.postings or 0), item.key

    eligible.sort(key=order)
    fast = [i for i in eligible if i.lane == "FAST"]
    heavy = [i for i in eligible if i.lane == "HEAVY"]
    unknown = [i for i in eligible if i.lane == "UNKNOWN"]
    selected: list[str] = []
    remaining = budget_seconds

    def admit(item: Item, reason: str) -> bool:
        nonlocal remaining
        if item.seconds > remaining or len(selected) >= max_items:
            return False
        selected.append(item.key)
        remaining -= item.seconds
        decisions[item.key].update(selected=True, reason=reason)
        return True

    # Rotate the first reserved slot using persisted execution count. Even a
    # heavy item that needs the entire budget gets a turn; cheap and unknown
    # work each have their own round and cannot starve behind it.
    first_lane = (fast, heavy, unknown)[round_number % 3]
    for item in first_lane:
        if admit(item, "ROTATING_FAIRNESS_TURN"):
            break
    if fast and fast[0].key not in selected:
        admit(fast[0], "STALE_CHEAP_RESERVE")
    for item in heavy:
        if any(by_item.lane == "HEAVY" and by_item.key in selected for by_item in heavy):
            break
        if admit(item, "OLDEST_FITTING_HEAVY_TURN"):
            break
    for item in unknown:
        if any(by_item.key in selected for by_item in unknown):
            break
        if admit(item, "ONE_BOUNDED_UNKNOWN_TURN"):
            break
    for item in eligible:
        if item.key not in selected and item.lane != "UNKNOWN":
            admit(item, "STALE_WITHIN_REMAINING_BUDGET")
    for item in eligible:
        if item.key not in selected:
            decisions[item.key]["reason"] = (
                "NEEDS_LARGER_BUDGET"
                if item.seconds > budget_seconds
                else "UNKNOWN_SLOT_LIMIT"
                if item.lane == "UNKNOWN"
                else "ITEM_BOUND"
                if len(selected) >= max_items
                else "TIME_BUDGET"
            )
    return {
        "budget_seconds": budget_seconds,
        "estimated_seconds": budget_seconds - remaining,
        "selected": selected,
        "items": list(decisions.values()),
        "inventory_count": len(items),
        "pending_count": len(eligible),
        "fresh_count": sum(
            d["age_seconds"] is not None and d["age_seconds"] < stale_hours * 3600
            for d in decisions.values()
        ),
        "note": "Admission allowances, not ETAs. Discover remains available.",
    }


def round_number(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM pipeline_run WHERE stage = 'source-maintenance'"
        ).fetchone()[0]
    )


def freshness(conn: sqlite3.Connection, catalogue: Path | None = None) -> dict[str, Any]:
    """Minimal read model; a ledger row alone never proves process liveness."""
    items = inventory(conn, catalogue)
    result = plan(items, 1800, round_number=round_number(conn))
    row = conn.execute(
        "SELECT * FROM pipeline_run WHERE stage = 'source-maintenance'"
        " ORDER BY started_at DESC, id DESC LIMIT 1"
    ).fetchone()
    receipt = json.loads(row["stats_json"]) if row else None
    return {
        "last_successful_check": max(
            (i.last_success for i in items if i.last_success), default=None
        ),
        "fresh": result["fresh_count"],
        "pending": result["pending_count"],
        "inventory": len(items),
        "items": result["items"],
        "last_session": receipt,
        "activity": "UNCONFIRMED_RUNNING_OR_INTERRUPTED"
        if row and not row["finished_at"]
        else "NO_ACTIVE_MAINTENANCE_RECEIPT",
        "message": "Discover is available. A recent check is not whole-market coverage.",
    }

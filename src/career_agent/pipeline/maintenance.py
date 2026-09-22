"""Execute an admitted plan using the existing collectors, with durable receipts."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from career_agent.domain.enums import PipelineRunStatus
from career_agent.net.deadline import Deadline
from career_agent.net.fetcher import FetchStats, HttpFetcher
from career_agent.pipeline.avlis_collect import AvlisCollector
from career_agent.pipeline.collect import Collector
from career_agent.pipeline.jobicy_collect import JobicyCollector
from career_agent.pipeline.remotive_collect import RemotiveCollector
from career_agent.pipeline.workingnomads_collect import WorkingNomadsCollector
from career_agent.sources.maintenance import inventory, plan, round_number
from career_agent.storage.db import transaction
from career_agent.storage.repositories import PipelineRunRepo

FEEDS: dict[str, Any] = {
    "avlis": AvlisCollector,
    "remotive": RemotiveCollector,
    "jobicy": JobicyCollector,
    "workingnomads": WorkingNomadsCollector,
}


def execute(
    conn,
    *,
    budget_seconds: float,
    stale_hours: float = 24,
    max_items: int = 100,
    provider: str | None = None,
    catalogue: Path | None = None,
    clock=time.monotonic,
    fetcher_factory=HttpFetcher,
    on_progress=None,
) -> dict[str, Any]:
    started = clock()
    deadline = Deadline(started + budget_seconds, clock)
    items = inventory(conn, catalogue)
    if provider:
        items = [i for i in items if i.provider == provider]
    admitted = plan(
        items,
        budget_seconds,
        stale_hours=stale_hours,
        max_items=max_items,
        round_number=round_number(conn),
    )
    by_key = {i.key: i for i in items}
    receipt: dict[str, Any] = {
        "budget_seconds": budget_seconds,
        "selected": admitted["selected"],
        "inventory_count": len(items),
        "active": None,
        "results": [],
        "status": "RUNNING",
        "elapsed_ms": 0,
    }
    runs = PipelineRunRepo(conn)
    refused_providers: set[str] = set()
    with transaction(conn):
        run_id = runs.start("source-maintenance")
        runs.progress(run_id, receipt)
    try:
        with fetcher_factory(deadline=deadline) as fetcher:
            for key in admitted["selected"]:
                item = by_key[key]
                if item.provider in refused_providers:
                    receipt["results"].append(
                        {
                            "key": key,
                            "outcome": "DEFERRED",
                            "reason": "PROVIDER_REFUSAL",
                            "attempted": False,
                        }
                    )
                    continue
                remaining = deadline.ends_at - clock()
                if item.seconds > remaining:
                    receipt["results"].append(
                        {
                            "key": key,
                            "outcome": "DEFERRED",
                            "reason": "TIME_BUDGET",
                            "attempted": False,
                        }
                    )
                    continue
                # Recheck successful stamps and policy; a plan is not permission.
                current = next((i for i in inventory(conn, catalogue) if i.key == key), None)
                if current is None or current.blocked or current.last_success != item.last_success:
                    receipt["results"].append(
                        {
                            "key": key,
                            "outcome": "DEFERRED",
                            "reason": "STATE_CHANGED",
                            "attempted": False,
                        }
                    )
                    continue
                receipt["active"] = key
                with transaction(conn):
                    runs.progress(run_id, receipt)
                if on_progress:
                    on_progress(receipt)
                unit_started = clock()
                fetcher.stats = FetchStats()
                # Unknown work gets only its reserved exploration allowance;
                # a surprising large board cannot consume the whole session.
                fetcher.deadline = Deadline(
                    min(deadline.ends_at, clock() + item.seconds)
                    if item.lane == "UNKNOWN"
                    else deadline.ends_at,
                    clock,
                )
                # HttpFetcher uses this deadline dynamically, including waits.
                if item.board_id:
                    stats: Any = Collector(conn, fetcher).collect_all(
                        use_cache=False, board_ids={item.board_id}
                    )
                else:
                    stats = FEEDS[item.provider](conn, fetcher).collect()
                data = stats.as_dict()
                if any(e.get("status_code") in {401, 403, 429} for e in fetcher.stats.failures):
                    refused_providers.add(item.provider)
                reason = data.get("deferred_reason")
                if data.get("boards_rejected"):
                    reason = "BOARD_NOT_ADDRESSABLE"
                if data.get("cooling_down_until"):
                    reason = "PROVIDER_COOLDOWN"
                failed = bool(data.get("boards_failed") or data.get("failures"))
                receipt["results"].append(
                    {
                        "key": key,
                        "outcome": "DEFERRED" if reason else "FAILED" if failed else "OK",
                        "reason": reason,
                        "attempted": True,
                        "elapsed_ms": int((clock() - unit_started) * 1000),
                        "postings": data.get("postings_observed", data.get("postings_seen", 0)),
                    }
                )
                receipt["active"] = None
                with transaction(conn):
                    runs.progress(run_id, receipt)
    except KeyboardInterrupt:
        receipt["status"] = "INTERRUPTED"
    except Exception:
        receipt["status"] = "ERROR"
        raise
    finally:
        receipt["elapsed_ms"] = int((clock() - started) * 1000)
        receipt["overrun_seconds"] = max(0, clock() - deadline.ends_at)
        if receipt["status"] == "RUNNING":
            receipt["status"] = "FINISHED"
        receipt["scoring"] = "Deferred to normal targeted rescore; serving corpus stays available."
        with transaction(conn):
            runs.finish(
                run_id,
                PipelineRunStatus.FAILED if receipt["status"] == "ERROR" else PipelineRunStatus.OK,
                stats=receipt,
            )
    return receipt

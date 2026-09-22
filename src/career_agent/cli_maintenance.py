"""Time-budgeted source maintenance, read-only by default."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from career_agent.pipeline.maintenance import execute as execute_plan
from career_agent.runtime.maintenance_lock import maintenance_lock, maintenance_running
from career_agent.runtime.mode import RuntimeMode, read_identity, resolve_database
from career_agent.sources.maintenance import freshness, inventory, plan, read_only, round_number
from career_agent.storage.db import connect, pending_migrations


def refresh(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    budget_minutes: Annotated[float, typer.Option("--budget-minutes", min=0.1, max=240)] = 30,
    execute: Annotated[bool, typer.Option("--execute", help="Run permitted collectors.")] = False,
    plan_only: Annotated[bool, typer.Option("--plan", help="Read-only, also the default.")] = False,
    provider: Annotated[str | None, typer.Option("--provider")] = None,
    max_items: Annotated[int, typer.Option("--max-items", min=1, max=1000)] = 100,
    stale_hours: Annotated[float, typer.Option("--stale-hours", min=0)] = 24,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Refresh useful stale sources within an admission budget. Plan by default.

    Estimates are not exact ETAs. An in-flight request or atomic commit can
    finish after the deadline. Source policy and cooldowns always apply.
    Scoring is a separate normal targeted rescore, never an implicit full pass.
    """
    if execute and plan_only:
        raise typer.BadParameter("Choose --plan or --execute, not both.")
    path = resolve_database(RuntimeMode.PERSONAL, db)
    with read_only(path) as conn:
        identity = read_identity(conn)
        if execute and (identity is None or identity.kind is not RuntimeMode.PERSONAL):
            raise typer.BadParameter("Live refresh requires a personal database identity.")
        if pending_migrations(conn):
            raise typer.BadParameter("Database migrations are pending; refresh never migrates.")
        items = inventory(conn)
        if provider:
            if provider not in {i.provider for i in items}:
                raise typer.BadParameter("Provider has no registered maintenance inventory.")
            items = [i for i in items if i.provider == provider]
        result = plan(
            items,
            budget_minutes * 60,
            stale_hours=stale_hours,
            max_items=max_items,
            round_number=round_number(conn),
        )
    if execute:
        with maintenance_lock(path):
            conn = connect(path)
            try:
                result = execute_plan(
                    conn,
                    budget_seconds=budget_minutes * 60,
                    stale_hours=stale_hours,
                    max_items=max_items,
                    provider=provider,
                    on_progress=None
                    if json_output
                    else lambda r: typer.echo(f"Running: {r['active']}"),
                )
            finally:
                conn.close()
    if json_output or execute:
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"Read-only plan: {path}")
        typer.echo(
            f"Budget {budget_minutes:g} min; allowance {result['estimated_seconds']:.0f}s; "
            f"selected {len(result['selected'])} / pending {result['pending_count']}; "
            f"fresh {result['fresh_count']}; inventory {result['inventory_count']}"
        )
        for item in result["items"]:
            state = "RUN" if item["selected"] else "DEFER"
            freshness = item["last_success"] or "never successfully collected"
            typer.echo(
                f"{state} {item['key']} | {freshness} | {item['lane']} "
                f"{item['seconds']:g}s ({item['evidence']}) | {item['reason']}"
            )
        typer.echo(result["note"])
        typer.echo(
            "Run this plan with --execute. Unfinished legacy ledger rows do not prove liveness."
        )


def register(app: typer.Typer) -> None:
    app.command(name="refresh")(refresh)
    app.command(name="refresh-status")(refresh_status)


def refresh_status(db: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Read freshness, pending work and the last maintenance receipt."""
    path = resolve_database(RuntimeMode.PERSONAL, db)
    with read_only(path) as conn:
        result = freshness(conn)
    result["maintenance_running"] = maintenance_running(path)
    typer.echo(json.dumps(result, indent=2))

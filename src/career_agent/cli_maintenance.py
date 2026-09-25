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
    app.command(name="repair-placement")(repair_placement)


def repair_placement(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    apply: Annotated[
        bool, typer.Option("--apply", help="Link the unambiguous ones. Dry run otherwise.")
    ] = False,
) -> None:
    """Report, and with --apply link, confirmed CV details outside their experience.

    A DRY RUN BY DEFAULT: it opens the database read-only and changes nothing.
    With --apply, each link is an Organization history event that can be
    undone. Ambiguous entries are only ever reported. See `storage/cv_placement`.
    """
    from career_agent.storage.cv_placement import apply_repair, plan_repair
    from career_agent.storage.workspace_repo import candidate_id_of

    path = resolve_database(RuntimeMode.PERSONAL, db)
    with read_only(path) as conn:
        if pending_migrations(conn):
            raise typer.BadParameter("Run `career-agent migrate` first.")
        candidate = candidate_id_of(conn)
        plans = plan_repair(conn, candidate) if candidate else []
    _print_placement(plans)
    if not apply:
        typer.echo("")
        typer.echo(
            "DRY RUN: nothing was changed. Run again with --apply to link the proposed ones."
        )
        return
    if candidate is None or not any(plan.proposed for plan in plans):
        typer.echo("")
        typer.echo("Nothing to link.")
        return
    conn = connect(path)
    try:
        # Planned again on the writable connection: never apply a stale plan.
        linked = apply_repair(conn, candidate, plan_repair(conn, candidate))
    finally:
        conn.close()
    typer.echo("")
    typer.echo(f"Linked {linked} confirmed details. Each move is in Organization history.")


def _print_placement(plans: list) -> None:
    for p in plans:
        typer.echo("")
        typer.echo(f"{p.company or '(no employer)'} / {p.role or '(no role)'}  [{p.import_name}]")
        typer.echo(f"  {p.imported} imported details, {p.confirmed} confirmed")
        typer.echo(f"  {p.linked_here} already linked to this experience")
        if p.linked_elsewhere:
            typer.echo(f"  {p.linked_elsewhere} linked to another experience (left as they are)")
        typer.echo(f"  {len(p.missing)} confirmed but not linked")
        if p.proposed:
            typer.echo(f"  -> propose linking {len(p.proposed)} to {p.experience_label}")
            typer.echo(f"     experience {p.experience_id}, because: {p.basis}")
        elif p.missing:
            typer.echo(f"  -> AMBIGUOUS, not moved: {p.ambiguous}")
    proposed = sum(len(p.proposed) for p in plans)
    ambiguous = sum(len(p.missing) for p in plans if not p.experience_id)
    typer.echo("")
    typer.echo(
        f"SUMMARY: {len(plans)} CV entries with confirmed details, "
        f"{proposed} links proposed, {ambiguous} confirmed details ambiguous."
    )


def refresh_status(db: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Read freshness, pending work and the last maintenance receipt."""
    path = resolve_database(RuntimeMode.PERSONAL, db)
    with read_only(path) as conn:
        result = freshness(conn)
    result["maintenance_running"] = maintenance_running(path)
    typer.echo(json.dumps(result, indent=2))

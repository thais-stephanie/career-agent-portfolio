# Modified for the Career Agent public edition (2026-09-22). See NOTICE.
"""Command line: run the pipeline headless, serve the UI, inspect the bank."""

from __future__ import annotations

import json
from pathlib import Path

import typer
from dotenv import load_dotenv

from resume_tailor.core.models import TailorOptions, TailorRequest
from resume_tailor.core.pipeline import TailorService
from resume_tailor.export.exporters import EXPORTERS
from resume_tailor.providers.llm.factory import build_provider
from resume_tailor.storage import paths
from resume_tailor.storage.runs import RunStore, new_run_id

app = typer.Typer(add_completion=False, help="Resume Tailor: evidence-grounded resume tailoring.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8765, reload: bool = False) -> None:
    """Start the local web UI."""
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise typer.BadParameter("Resume Tailor binds loopback only.")
    import uvicorn

    uvicorn.run("resume_tailor.api.app:app", host=host, port=port, reload=reload)


@app.command()
def migrate() -> None:
    """Move this repository's candidate data into a local candidate workspace (~/.resume-tailor)."""
    from resume_tailor.workspace import WorkspaceError, WorkspaceStore
    from resume_tailor.workspace.migrate import migrate_data_dir

    store = WorkspaceStore()
    try:
        ws = migrate_data_dir(store, paths.data_dir())
    except WorkspaceError as e:
        typer.echo(str(e))
        raise typer.Exit(1) from e
    settings = store.app_settings()
    settings["last_selected_candidate"] = ws.id
    store.save_app_settings(settings)
    typer.echo(f"Migrated '{ws.meta()['name']}' to {ws.root}")
    typer.echo("The original files were left in place; nothing was deleted.")


@app.command()
def demo() -> None:
    """Create the synthetic demo candidate (Alex Morgan) to explore the app."""
    from resume_tailor.workspace import WorkspaceStore
    from resume_tailor.workspace.demo import create_demo_candidate

    store = WorkspaceStore()
    ws = create_demo_candidate(store)
    settings = store.app_settings()
    settings.setdefault("last_selected_candidate", ws.id)
    store.save_app_settings(settings)
    typer.echo(
        f"Demo candidate '{ws.meta()['name']}' is ready. Start the app with: uv run resume-tailor serve"
    )


@app.command()
def candidates() -> None:
    """List local candidates."""
    from resume_tailor.workspace import WorkspaceStore

    store = WorkspaceStore()
    for meta in store.list_candidates(include_archived=True):
        flag = " (archived)" if meta.get("archived") else ""
        typer.echo(f"{meta['name']}{flag}  [{meta['id']}]")


@app.command()
def tailor(
    jd: Path = typer.Option(..., help="Path to a job description text file"),
    resume: str = typer.Option("ai_automation_data_engineer", help="Base resume id"),
    profile: str = typer.Option("auto", help="Target profile id or 'auto'"),
    no_llm: bool = typer.Option(False, help="Force deterministic mode"),
    export: str = typer.Option(
        "md", help="Export format written next to the run (md|html|docx|pdf)"
    ),
) -> None:
    """Run Analyze & Tailor headless and print where the artifacts are."""
    load_dotenv(paths.PACKAGE_ROOT / ".env")
    llm = build_provider(cache_dir=paths.cache_dir())
    svc = TailorService(
        paths.load_evidence_index(), paths.load_resumes(), paths.load_profiles(), llm
    )
    req = TailorRequest(
        jd_text=jd.read_text(encoding="utf-8"),
        resume_id=resume,
        target_profile=None if profile == "auto" else profile,
        options=TailorOptions(use_llm=not no_llm),
    )
    run_id = new_run_id()
    run = svc.run(req, run_id, progress=lambda s: typer.echo(f"  … {s}"))
    store = RunStore(paths.runs_dir())
    d = store.save(run)
    exp = EXPORTERS.get(export)
    if exp is None:
        raise typer.BadParameter(
            f"choose from {', '.join(sorted(EXPORTERS))}", param_hint="--export"
        )
    from resume_tailor.export.exporters import export_filename

    out_name = export_filename(
        run.generated_resume.candidate.name,
        run.job_analysis.role_title,
        run.generated_resume.headline,
        exp.extension,
    )
    try:
        (d / out_name).write_bytes(exp.render(run.generated_resume))
        typer.echo(f"export: {d / out_name}")
    except NotImplementedError as e:  # PDF without a local Word/LibreOffice: the run is still saved
        typer.echo(f"export skipped: {e}")
    v = run.validation_report
    typer.echo(f"\nrun {run_id}: provider={run.provider['provider']}/{run.provider['model']}")
    typer.echo(
        f"profile={run.resume_strategy.target_profile} title={run.resume_strategy.recommended_title!r}"
    )
    typer.echo(
        f"validation={v.status} supported={v.supported} replaced={v.replaced} rejected={v.rejected}"
    )
    typer.echo(f"coverage={json.dumps(run.evidence_matches.coverage)}")
    typer.echo(
        f"lint: {run.lint_report.metrics['errors']} errors, {run.lint_report.metrics['warnings']} warnings; est. pages={run.generated_resume.estimated_pages}"
    )
    for w in run.warnings:
        typer.echo(f"warning: {w}")
    typer.echo(f"artifacts: {d}")


@app.command()
def evidence(
    check: bool = typer.Option(True, help="Validate the bank and print a summary"),
) -> None:
    """Validate and summarise the evidence bank."""
    idx = paths.load_evidence_index()
    b = idx.bank
    typer.echo(
        f"{len(b.records)} records, {len(b.positions)} positions, {len(b.conflicts)} conflicts, {len(idx.all_terms)} distinct terms"
    )
    typer.echo(
        f"tenure: {idx.tenure_summary()} (total_relevant incl. junior enterprise / employment incl. internship / post-internship professional)"
    )
    typer.echo(f"user overrides applied: {[o.id for o in b.overrides]}")
    for c in b.conflicts:
        typer.echo(f"  conflict {c.id}: {c.topic} -> {c.resolved_by or 'unresolved'}")


@app.command()
def doctor() -> None:
    """Show which LLM provider would be used."""
    load_dotenv(paths.PACKAGE_ROOT / ".env")
    info = build_provider(cache_dir=paths.cache_dir()).info()
    typer.echo(
        f"provider={info.provider} model={info.model} available={info.available} ({info.note})"
    )


if __name__ == "__main__":
    app()

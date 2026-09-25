"""`career-agent catalogue`: the shared public job catalogue from the command line.

    catalogue status      which side a database is on, and what it links to
    catalogue split       turn a profile that holds its own postings into a
                          split profile plus the shared catalogue
    catalogue rollback    put the pre-split database back

`split` never changes the database it reads. Without `--execute` it builds and
verifies both new files in a staging folder and stops there: a rehearsal. With
`--execute` it does the same and then moves the verified files into place, the
original going to `data/legacy/` as the rollback. See docs/MULTI_PROFILE.md.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from career_agent.runtime import RuntimeMode, resolve_database

catalogue_app = typer.Typer(
    help="The shared public job catalogue: status, split, rollback.", no_args_is_help=True
)


def register(app: typer.Typer) -> None:
    app.add_typer(catalogue_app, name="catalogue")


@catalogue_app.command("status")
def status_command(db: Annotated[Path | None, typer.Option("--db")] = None) -> None:
    """Say whether this profile uses the shared catalogue. Read-only."""
    from career_agent.storage.catalogue import (
        catalogue_id,
        catalogue_path,
        linked_id,
        open_read_only,
        role,
    )

    path = resolve_database(RuntimeMode.PERSONAL, db)
    if not path.exists():
        typer.secho(f"no database at {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    conn = open_read_only(path)
    try:
        side = role(conn)
        typer.echo(f"database         : {path}")
        typer.echo(f"layout           : {side}")
        if side == "profile":
            typer.echo(f"linked catalogue : {linked_id(conn)}")
            typer.echo(f"catalogue file   : {catalogue_path(path)}")
            typer.echo(f"catalogue id     : {catalogue_id(conn, 'catalogue')}")
        jobs = conn.execute("SELECT COUNT(*) FROM job").fetchone()[0]
        typer.echo(f"postings visible : {jobs}")
    finally:
        conn.close()


@catalogue_app.command("split")
def split_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute/--rehearse",
            help="Move the verified files into place. Without it: build and verify only.",
        ),
    ] = False,
    staging: Annotated[
        Path | None,
        typer.Option("--staging", help="Where to build. Default: data/shared/.split-<time>"),
    ] = None,
    report: Annotated[
        Path | None, typer.Option("--report", help="Write the verification report here (JSON).")
    ] = None,
    keep_staging: Annotated[
        bool, typer.Option("--keep-staging", help="Keep the rehearsal's files to inspect them.")
    ] = False,
) -> None:
    """Split a profile database into private state plus the shared job catalogue.

    The database named by --db is only read. Both new files are built in a
    staging folder and compared with it: every table's rows, value digests of
    postings, scores, applications, semantic findings and retrieval
    provenance, integrity and foreign-key checks, and every private row that
    names a posting finding it in the catalogue. Nothing moves unless all of
    that passes AND --execute is given.
    """
    from career_agent.runtime.profiles import ProfileError, ProfileLock
    from career_agent.storage import catalogue_split
    from career_agent.storage.catalogue import CatalogueError, catalogue_path

    source = resolve_database(RuntimeMode.PERSONAL, db)
    if not source.exists():
        typer.secho(f"no database at {source}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if catalogue_path(source).exists():
        typer.secho(
            f"a shared catalogue already exists at {catalogue_path(source)}; this command "
            "only creates the first one.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    where = staging or catalogue_path(source).parent / f".split-{stamp}"
    guard = ProfileLock(source)
    try:
        guard.acquire()
    except ProfileError as exc:
        typer.secho(f"{exc} Close Career Agent first.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    try:
        started = time.monotonic()
        typer.echo(f"Reading {source} (not changed) and building in {where} ...")
        try:
            profile, shared, identity = catalogue_split.build(source, where)
        except CatalogueError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        built = time.monotonic() - started
        check = catalogue_split.verify(source, profile, shared)
        payload = {
            "source": str(source),
            "catalogue_id": identity,
            "build_seconds": round(built, 1),
            "profile_bytes": profile.stat().st_size,
            "catalogue_bytes": shared.stat().st_size,
            **check.as_dict(),
        }
        if report:
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text(json.dumps(payload, indent=1), encoding="utf-8")
        typer.echo(
            f"built in {built:.0f}s: profile {profile.stat().st_size:,} bytes, "
            f"catalogue {shared.stat().st_size:,} bytes"
        )
        typer.echo(f"integrity        : {check.integrity}")
        typer.echo(
            f"digests equal    : {all(check.digests.values())} ({len(check.digests)} checked)"
        )
        typer.echo(f"orphans          : {sum(check.orphans.values())}")
        if not check.ok:
            typer.secho("VERIFICATION FAILED; nothing was moved:", fg=typer.colors.RED, bold=True)
            for problem in check.problems:
                typer.echo(f"  {problem}")
            raise typer.Exit(code=1)
        typer.secho("verified: every check passed", fg=typer.colors.GREEN)
        if not execute:
            if not keep_staging:
                shutil.rmtree(where, ignore_errors=True)
            typer.echo("Rehearsal only. Run again with --execute to move the files into place.")
            return
        # Still holding the profile's lock: nothing can open it while it moves.
        installed = catalogue_split.install(source, profile, shared)
    finally:
        guard.release()
    shutil.rmtree(where, ignore_errors=True)
    typer.secho("Split done.", bold=True)
    typer.echo(f"profile          : {installed.profile}")
    typer.echo(f"shared catalogue : {installed.catalogue}")
    typer.echo(f"original kept at : {installed.legacy}")
    typer.echo(
        "The original is the rollback: `career-agent catalogue rollback --db "
        f"{installed.profile} --legacy {installed.legacy}`. Delete it only once you are sure."
    )


@catalogue_app.command("rollback")
def rollback_command(
    legacy: Annotated[
        Path, typer.Option("--legacy", help="The pre-split database in data/legacy/.")
    ],
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Put the pre-split database back. Nothing is deleted: the split profile
    is kept beside it, and the catalogue stays where it is."""
    from career_agent.runtime.profiles import ProfileError, ProfileLock
    from career_agent.storage import catalogue_split

    target = resolve_database(RuntimeMode.PERSONAL, db)
    if not legacy.exists():
        typer.secho(f"no database at {legacy}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    guard = ProfileLock(target)
    try:
        guard.acquire()
    except ProfileError as exc:
        typer.secho(f"{exc} Close Career Agent first.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    try:
        aside = catalogue_split.rollback(target, legacy)
    finally:
        guard.release()
    typer.echo(f"restored {target} from {legacy}; the split profile is kept at {aside}")

"""The local product's commands, registered onto the main Typer app.

Seven commands, and the division between them is the point: `rescore`,
`seed-demo`, `search-config` and `serve` cannot reach a network at all, and
`enrich` and `ollama-check` can reach exactly one address -- 127.0.0.1 -- and
only when a person types them.

`serve` is the only long-running command. It binds loopback, and refuses to
bind anything else.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from career_agent.domain.enums import DomesticContext, EligibilityStatus
from career_agent.runtime import (
    RuntimeMode,
    RuntimeModeError,
    database_ref,
    identity_of,
    resolve_database,
    stamp_identity,
)
from career_agent.storage.db import connect, migrate, schema_version, transaction

#: The historical default, kept only so a caller who names it explicitly
#: still works. Commands no longer FALL BACK to it: `resolve_database`
#: resolves the personal database, because a command that silently scored
#: an empty file and reported "0 jobs considered, 0 errors" looks exactly
#: like a command that worked.
DEFAULT_DB_PATH = Path("data/career.db")
DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_DEMO_FILE = Path("evaluation/demo/demo_postings.yaml")


def register(app: typer.Typer) -> None:
    app.command(name="search-config")(search_config_command)
    app.command(name="rescore")(rescore_command)
    app.command(name="semantic-match")(semantic_match_command)
    app.command(name="rebuild-bodies")(rebuild_bodies_command)
    app.command(name="intake-build")(intake_build_command)
    app.command(name="intake-validate")(intake_validate_command)
    app.command(name="intake-import")(intake_import_command)
    app.command(name="intake-review")(intake_review_command)
    app.command(name="intake-answer")(intake_answer_command)
    app.command(name="integrity")(integrity_command)
    app.command(name="source-health")(source_health_command)
    app.command(name="source-coverage")(source_coverage_command)
    app.command(name="discover-employer-boards")(discover_employer_boards_command)
    app.command(name="discover-boards")(discover_boards_command)
    app.command(name="ingestion-report")(ingestion_report_command)
    app.command(name="source-matrix")(source_matrix_command)
    app.command(name="scan-career-sites")(scan_career_sites_command)
    app.command(name="discover-remotesource")(discover_remotesource_command)
    app.command(name="backup")(backup_command)
    app.command(name="cv-import")(cv_import_command)
    app.command(name="evidence")(evidence_command)
    app.command(name="prepare")(prepare_command)
    app.command(name="init-personal")(init_personal_command)
    app.command(name="seed-demo")(seed_demo_command)
    app.command(name="serve")(serve_command)
    app.command(name="start")(start_command)
    app.command(name="import-job")(import_job_command)
    app.command(name="enrich")(enrich_command)
    app.command(name="ollama-check")(ollama_check_command)
    app.command(name="collect-speedrun")(collect_speedrun_command)
    app.command(name="jooble-probe")(jooble_probe_command)
    app.command(name="daily")(daily_command)
    app.command(name="collect-wwr")(collect_wwr_command)
    app.command(name="collect-himalayas")(collect_himalayas_command)
    app.command(name="collect-linkedin")(collect_linkedin_command)
    app.command(name="experimental-source")(experimental_source_command)
    app.command(name="collect-jobicy")(collect_jobicy_command)
    app.command(name="collect-remotive")(collect_remotive_command)
    app.command(name="collect-jobgether")(collect_jobgether_command)
    app.command(name="collect-fourdayweek")(collect_fourdayweek_command)
    app.command(name="enrich-leads")(enrich_leads_command)
    app.command(name="collect-dynamitejobs")(collect_dynamitejobs_command)
    app.command(name="collect-gupy")(collect_gupy_command)
    app.command(name="collect-programathor")(collect_programathor_command)
    app.command(name="collect-remoteok")(collect_remoteok_command)
    app.command(name="collect-arbeitnow")(collect_arbeitnow_command)
    app.command(name="collect-workable")(collect_workable_command)
    app.command(name="collect-avlis")(collect_avlis_command)
    app.command(name="collect-workingnomads")(collect_workingnomads_command)
    app.command(name="collect-getonbrd")(collect_getonbrd_command)
    app.command(name="setup")(setup_command)
    app.command(name="profile-export")(profile_export_command)
    app.command(name="profile-import")(profile_import_command)
    app.command(name="migrate-profile")(migrate_profile_command)
    app.command(name="forget")(forget_command)


def _table(rows: list[tuple[str, Any]]) -> None:
    width = max((len(label) for label, _ in rows), default=0)
    for label, value in rows:
        typer.echo(f"  {label:<{width}} : {value}")


def _who_owns_port(port: int) -> str:
    """The command that names the process holding a port, on THIS system.

    A message telling somebody to "check what is using the port" and leaving
    them to work out how is a message that gets ignored. The owner's terminal
    is PowerShell; `Get-NetTCPConnection` is the one that also gives the
    command line, which is what distinguishes an old Career Agent from
    something else entirely.
    """
    if sys.platform == "win32":
        return (
            f"Get-Process -Id (Get-NetTCPConnection -LocalPort {port} -State Listen)"
            ".OwningProcess | Select-Object Id, ProcessName, Path"
        )
    return f"lsof -nP -iTCP:{port} -sTCP:LISTEN"


def _load_config(config_dir: Path):
    from career_agent.config.search_config import SearchConfigError, load_search_config

    try:
        return load_search_config(config_dir)
    except SearchConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


def _semantic_on(config_dir: Path) -> bool:
    """Whether stored semantic findings take part in scoring. Reads a file."""
    from career_agent.semantic.settings import load_settings

    return load_settings(config_dir).uses_findings


# =====================================================================
# search-config
# =====================================================================
def search_config_command(
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Show which search configuration is in force, and summarise it.

    Always prints the path actually read. `search.local.yaml` overrides the
    example, and a fallback to the example is announced rather than silent --
    the whole reason the loader returns the path alongside the object.
    """
    config, path = _load_config(config_dir)
    lexicon = getattr(config, "lexicon", {}) or {}
    taxonomy = getattr(config, "taxonomy", None)

    typer.secho("Search configuration", bold=True)
    _table(
        [
            ("file", path),
            (
                "kind",
                "local override" if path.name.endswith(".local.yaml") else "committed example",
            ),
            ("config_id", config.config_id),
            ("config_version", config.config_version),
            ("digest", getattr(config, "digest", "n/a")),
            ("label", getattr(config, "label", "")),
            ("lexicon signals", len(lexicon)),
        ]
    )
    if taxonomy is not None:
        typer.secho("\nTitle taxonomy", bold=True)
        _table(
            [
                (name, len(getattr(taxonomy, name, []) or []))
                for name in ("primary", "strong_adjacent", "conditional", "excluded")
            ]
        )
    thresholds = getattr(config, "thresholds", None)
    if thresholds is not None:
        typer.secho("\nThresholds", bold=True)
        payload = thresholds if isinstance(thresholds, dict) else thresholds.model_dump()
        _table([(k, v) for k, v in payload.items() if not isinstance(v, dict)])

    typer.echo("\nno network calls and no inference calls were made by this command")


def _open_personal(db: Path) -> Any:
    """Open a personal database, refusing one that belongs to the other kind.

    `serve` had this check and the three commands that WRITE did not, which
    got the guarantee backwards: reading a demo database shows you invented
    postings, and `import-job` against one puts a REAL employer into the demo
    population. The committed screenshots are demo-mode precisely because that
    population contains no real employer name, so the missing check undermined
    the leak guard too.
    """
    conn = connect(db)
    try:
        migrate(conn)
        identity_of(conn, RuntimeMode.PERSONAL)
    except RuntimeModeError as exc:
        conn.close()
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    except Exception:
        conn.close()
        raise
    return conn


# =====================================================================
# scan-career-sites
# =====================================================================
def scan_career_sites_command(
    registry: Annotated[
        Path, typer.Option("--registry", help="The company registry to read")
    ] = Path("config/companies.yaml"),
    limit: Annotated[
        int, typer.Option("--limit", help="How many companies to read. 0 means every one.")
    ] = 25,
    company: Annotated[
        str | None, typer.Option("--company", help="One company slug, instead of a scan")
    ] = None,
    out: Annotated[
        Path | None, typer.Option("--out", help="Write every finding to this JSON file")
    ] = None,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute/--preflight",
            help="Make the requests. Without it, nothing is fetched and the plan is printed.",
        ),
    ] = False,
) -> None:
    """Read a registered company's own careers page and see which ATS it names.

    WHY THIS IS NOT `discover-boards`. That command derives plausible board
    identifiers from a domain and asks each provider whether one is a board.
    Two families cannot be found that way, and for two different reasons that
    are really the same reason: what an EMPTY ANSWER means.

    One addresses a board by a tenant, a site and a numbered data centre, none
    of which is derivable from a domain -- guessing them is a namespace walk in
    three dimensions. The other addresses a board by a slug, and its host
    ANSWERS FOR A SLUG NOBODY REGISTERED, so a probe returning no openings
    cannot be told from a real board with nothing open.

    So this reads what the company itself published: a link, a form action, a
    script, a reference to a vendor endpoint. A board is proposed because the
    employer pointed at it.

    PREFLIGHT BY DEFAULT. Without `--execute` nothing is fetched: it prints how
    many companies are in scope and how many requests that is at most. Reading
    280 strangers' websites is a decision, and it should cost typing a word.

    EVERY HOST IS ASKED FIRST. Its own `robots.txt` is read once per origin and
    obeyed, and a host that refuses is reported as REFUSED_BY_HOST rather than
    as a failure. Until now every source's permission was a reviewed human
    observation quoted into the catalogue -- the right answer for eighteen
    vendors, and no answer at all for the company websites this reads.

    IT WRITES NOTHING TO THE DATABASE. No board is promoted, no posting is
    touched, and nothing is closed -- a discovery pass that could close a
    posting would let a company changing its website retire jobs that are still
    open. The output is a report, and promoting a board is a separate,
    deliberate act.
    """
    import json as _json

    from career_agent.config.registry import load_registry_file
    from career_agent.pipeline.site_discovery import (
        CAREERS_PATHS,
        SiteOutcome,
        read_site,
    )
    from career_agent.providers.site_detect import families

    entries = [c for c in load_registry_file(registry).companies if c.website or c.canonical_domain]
    if company:
        entries = [c for c in entries if c.slug == company]
        if not entries:
            typer.secho(f"No registered company with slug {company!r}.", fg=typer.colors.RED)
            raise typer.Exit(code=2)
    if limit > 0:
        entries = entries[:limit]

    typer.secho("\nCareer-site scan", bold=True)
    _table(
        [
            ("companies in scope", len(entries)),
            ("families recognised", ", ".join(families())),
            ("pages tried per company", f"at most {len(CAREERS_PATHS)}"),
            ("requests at most", len(entries) * len(CAREERS_PATHS)),
        ]
    )

    if not execute:
        typer.secho(
            "\nPREFLIGHT. Nothing was fetched. Add --execute to read these sites.",
            fg=typer.colors.YELLOW,
        )
        return

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.net.robots import RobotsPolicy

    fetcher = HttpFetcher()
    # CONSTRUCTED HERE AND ALWAYS PASSED. `read_site` accepts `None` for the
    # tests, which hand in a fetcher that cannot fetch; the one caller that
    # touches the network cannot be run without a policy, which is what makes
    # ADR-0018's fourth limit a property of the command rather than a habit.
    robots = RobotsPolicy()
    findings = []
    for entry in entries:
        finding = read_site(
            fetcher,
            company=entry.slug,
            website=entry.website,
            domain=entry.canonical_domain,
            robots=robots,
        )
        findings.append(finding)
        if finding.outcome in (SiteOutcome.IDENTITY_RESOLVED, SiteOutcome.PROVIDER_DETECTED):
            typer.echo(f"  {entry.slug:<28} {finding.outcome.value}")
            for signal in finding.signals:
                typer.echo(
                    f"      {signal.provider:<12} {signal.kind.value:<6} "
                    f"{signal.identity or dict(signal.parts) or 'no identity'}"
                )
                typer.echo(f"          {signal.evidence[:100]}")

    typer.echo("")
    counts = {outcome.value: 0 for outcome in SiteOutcome}
    for finding in findings:
        counts[finding.outcome.value] += 1
    _table([(outcome.value, counts[outcome.value]) for outcome in SiteOutcome])

    typer.secho(
        "\nNothing was promoted and nothing was written. "
        "A resolved identity is a proposal for a person to accept.",
        fg=typer.colors.YELLOW,
    )

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            _json.dumps([f.as_dict() for f in findings], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        typer.secho(f"wrote {out}", bold=True)


# =====================================================================
# discover-remotesource
# =====================================================================
def discover_remotesource_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    max_employers: Annotated[
        int,
        typer.Option("--max-employers", help="How many employers to read this run."),
    ] = 100,
    retry: Annotated[
        bool,
        typer.Option(
            "--retry",
            help="Also revisit employers whose last page published no origin or could not be read.",
        ),
    ] = False,
    delay: Annotated[float, typer.Option("--delay", help="Seconds between requests.")] = 1.0,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute/--preflight",
            help="Make the requests. Without it, nothing is fetched and the plan is printed.",
        ),
    ] = False,
) -> None:
    """Find ATS boards through RemoteSource's index, and register the ones
    the employer's own ATS answers for.

    RemoteSource is not a source of postings here. It republishes remote
    roles and links the ORIGINAL applicant tracking system on every page
    sampled, so one page per employer names a board somebody published --
    all three parts of a Workday identity included, which no domain can
    yield and which `scan-career-sites` found twice in 280 careers pages.

    THE BOARD IS REGISTERED ONLY AFTER ITS OWN ATS ANSWERED FOR IT, through
    the family's own adapter under the family's own permission. Postings
    then arrive through the ordinary `collect`, with the employer's own
    words and an origin URL on the employer's own board; no RemoteSource
    row is ever stored, and its parsed scope and estimated salary never
    reach a fingerprint.

    BOUNDED AND RESUMABLE. One page per employer, `--max-employers` a run,
    and every employer answered is remembered in `board_discovery_lead`, so
    the next run starts after them. The walk is ordered by the index's own
    employer keys and nothing about a person orders it.

    PREFLIGHT BY DEFAULT. Without `--execute` nothing is fetched.
    """
    from career_agent.pipeline.board_discovery import (
        INDEX_SOURCE,
        BoardDiscovery,
        LeadOutcome,
        supported_families,
    )
    from career_agent.providers import remotesource
    from career_agent.runtime.mode import RuntimeMode, resolve_database

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = connect(db)
    try:
        migrate(conn)
        answered = conn.execute(
            "SELECT outcome, COUNT(*) AS n FROM board_discovery_lead WHERE index_source = ?"
            " GROUP BY outcome ORDER BY outcome",
            (INDEX_SOURCE,),
        ).fetchall()
        typer.secho("\nRemoteSource board discovery", bold=True)
        _table(
            [
                ("database", db),
                ("families that recognise their boards", ", ".join(supported_families())),
                ("employers answered so far", sum(int(r["n"]) for r in answered)),
                *[(f"  {r['outcome']}", int(r["n"])) for r in answered],
                ("employers this run, at most", max_employers),
                (
                    "requests at most",
                    f"{max_employers} pages + {max_employers} ATS probes + the sitemap shards",
                ),
            ]
        )
        if not execute:
            typer.secho(
                "\nPREFLIGHT. Nothing was fetched. Add --execute to walk the index.",
                fg=typer.colors.YELLOW,
            )
            return

        from career_agent.net.fetcher import HttpFetcher
        from career_agent.net.robots import RobotsPolicy

        with HttpFetcher(request_delay_seconds=delay) as fetcher:
            robots = RobotsPolicy()
            index = remotesource.fetch_index(fetcher)
            employers = remotesource.group_by_employer(index)
            typer.echo(f"  index: {len(index)} posting URLs, {len(employers)} employers named")

            def progress(lead: Any, stats: Any) -> None:
                mark = {
                    LeadOutcome.REGISTERED: "+",
                    LeadOutcome.ALREADY_REGISTERED: "=",
                    LeadOutcome.FEED_COVERED: "~",
                }.get(lead.outcome, " ")
                where = (
                    f"{lead.provider}:{lead.board_identifier}"
                    if lead.board_identifier
                    else (lead.detail or "")
                )
                typer.echo(f"  {mark} {lead.employer_key:<32} {lead.outcome.value:<20} {where}")

            stats = BoardDiscovery(conn, fetcher, robots=robots).run(
                employers, max_employers=max_employers, retry=retry, progress=progress
            )
    finally:
        conn.close()

    typer.echo("")
    summary = stats.as_dict()
    _table(
        [
            ("employers in the index", summary["employers_in_index"]),
            ("already answered before this run", summary["employers_already_walked"]),
            ("walked this run", summary["employers_walked"]),
            ("requests made", summary["requests_made"]),
            *[(f"  {k}", v) for k, v in summary["outcomes"].items()],
        ]
    )
    typer.secho("\nBy family:", bold=True)
    for family, counts in summary["by_family"].items():
        typer.echo(f"  {family:<14} " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    if summary["boards_registered"]:
        typer.secho(
            f"\n{len(summary['boards_registered'])} boards registered. Run `career-agent"
            f" collect --db {db}` to read them.",
            fg=typer.colors.GREEN,
        )


# =====================================================================
# source-matrix
# =====================================================================
def source_matrix_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    write: Annotated[
        Path | None,
        typer.Option("--write", help="Write the living document to this path"),
    ] = None,
) -> None:
    """The official source matrix: what works, what does not, why, what never ran.

    ONE TABLE, CROSSED FROM FIVE PLACES: the catalogue's quoted permissions, the
    provider registry, the corpus, the `pipeline_run` ledger, and the content
    completeness migration 0022 stores. Each of those already answered its own
    question correctly and none of them answered the one people ask, which is
    "can I get jobs out of this source today, and if not, whose move is it".

    `--write` regenerates the living document. It is GENERATED and dated rather
    than written by hand, because a status recorded in prose on one day and read
    as current on another is the most reliable way this project has misled
    itself. `docs/product/source-capability-matrix.md` stays: it is prose about
    WHY each row is what it is, which is worth keeping and is a different
    document from a measurement.

    Read-only against the database. It opens no socket and calls no vendor.
    """
    from career_agent.sources.matrix import MatrixState, build, summarise

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        rows = build(conn)
    finally:
        conn.close()

    typer.secho("\nSource matrix", bold=True)
    typer.echo(f"  measured against {db}")
    typer.echo("")

    counts = summarise(rows)
    for state in MatrixState:
        typer.echo(f"  {counts[state.value]:>3}  {state.value:<20} {state.label}")

    typer.echo("")
    for state in MatrixState:
        group = [row for row in rows if row.state is state]
        if not group:
            continue
        typer.secho(f"  {state.value}", bold=True)
        for row in group:
            held = "-" if row.postings is None else str(row.postings)
            typer.echo(f"    {row.name:<24} {row.region:<16} open={held}")
            if row.blocker:
                typer.echo(f"        blocker: {row.blocker[:120]}")
            typer.echo(f"        next:    {row.next_action[:120]}")
        typer.echo("")

    if write is not None:
        from career_agent.sources.matrix_doc import render

        write.parent.mkdir(parents=True, exist_ok=True)
        write.write_text(render(rows, database=db), encoding="utf-8")
        typer.secho(f"wrote {write}", bold=True)


# =====================================================================
# source-health
# =====================================================================
def ingestion_report_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    stage: Annotated[
        str | None, typer.Option("--stage", help="One stage, e.g. collect-gupy")
    ] = None,
) -> None:
    """What each collector's last run actually did, in one vocabulary.

    Eleven collectors count themselves carefully and each of them counts in its
    own words -- `feeds_failed`, `categories_failed`, `details_failed`,
    `slices_over_ceiling` -- so "which source is losing postings, and where"
    had no answer that did not involve reading eleven dataclasses. This reads
    `pipeline_run.stats_json`, which every run already stores, and arranges it
    as one funnel: discovered, fetched, parsed, accepted, rejected,
    deduplicated, failed.

    NOT MEASURED IS PRINTED, and it is not zero. Most sources never say how
    many postings they hold; reporting 0 for those would read as a catastrophe
    and reporting the fetched count would read as success.

    UNACCOUNTED is the line to look at. Records parsed that were neither
    stored, nor rejected for a named reason, nor recognised as one already
    held. It should be zero, and a collector dropping a posting without
    counting why is what makes it not be.

    Read-only. It opens no socket and writes nothing.
    """
    from career_agent.pipeline.funnel import KNOWN_STAGES, latest_funnels, missing_stages

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        funnels = latest_funnels(conn)
    finally:
        conn.close()

    if stage:
        funnels = tuple(f for f in funnels if f.stage == stage)
        if not funnels:
            typer.secho(f"No run recorded for stage {stage!r}.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)

    typer.secho(f"Last run of each collection stage in {db}", bold=True)
    typer.echo("")

    def _count(value: int | None) -> str:
        return "not measured" if value is None else f"{value:,}"

    for funnel in funnels:
        colour = {
            "OK": typer.colors.GREEN,
            "FAILED": typer.colors.RED,
            "RUNNING": typer.colors.YELLOW,
        }.get(funnel.status)
        typer.secho(f"  {funnel.stage:<24} {funnel.status:<8} {funnel.started_at}", fg=colour)
        _table(
            [
                ("discovered", _count(funnel.discovered)),
                ("fetched", _count(funnel.fetched)),
                ("parsed", _count(funnel.parsed)),
                ("accepted", _count(funnel.accepted)),
                ("of which new", _count(funnel.jobs_new)),
                ("rejected", _count(funnel.rejected)),
                ("deduplicated", _count(funnel.deduplicated)),
                ("failed", _count(funnel.failed)),
                ("unaccounted", _count(funnel.unaccounted)),
            ]
        )
        for provider, split in funnel.per_provider:
            # ONE STAGE, FOUR FAMILIES. The ATS runner drives Greenhouse,
            # Lever, Ashby and Recruiterflow in a single pass, and one set of
            # totals for all four cannot answer "which source is losing
            # postings".
            typer.echo(
                f"      {provider:<16} parsed {_count(split.parsed):>8}"
                f"  accepted {_count(split.accepted):>8}"
                f"  rejected {_count(split.rejected):>6}"
                f"  unaccounted {_count(split.unaccounted):>6}"
            )
        for rejection in funnel.rejections:
            typer.echo(f"      rejected {rejection.count:,}: {rejection.reason}")
        for lost in funnel.lost_before_parsing:
            typer.echo(f"      never read {lost.count:,}: {lost.reason}")
        if funnel.bounds:
            # NOT a failure. This product choosing to stop, or a vendor
            # refusing, and the collector's own field names say which.
            typer.echo("      did not read the whole source: " + ", ".join(funnel.bounds))
        if funnel.unaccounted:
            typer.secho(
                f"      {funnel.unaccounted:,} records are unaccounted for: parsed, and neither "
                "stored nor rejected for a named reason.",
                fg=typer.colors.RED,
            )
        for failure in funnel.failures[:5]:
            typer.secho(f"      failed: {failure}", fg=typer.colors.RED)
        if len(funnel.failures) > 5:
            typer.echo(f"      ... and {len(funnel.failures) - 5} more")
        if funnel.error:
            typer.secho(f"      error: {funnel.error}", fg=typer.colors.RED)
        typer.echo("")

    never = missing_stages(KNOWN_STAGES, funnels)
    if never and not stage:
        # NAMED RATHER THAN OMITTED. A report listing only what ran answers
        # "did the collections work" and silently drops "and which never
        # happened", which is what a corpus missing a whole market looks like.
        typer.secho("Stages with a command and no run recorded:", bold=True)
        for missing in never:
            typer.echo(f"  {missing.stage:<24} {missing.reason}")


def discover_boards_command(
    source: Annotated[
        Path,
        typer.Option(
            "--from",
            help="One company per line: `Name | domain`, or a board URL, or a bare domain.",
        ),
    ],
    out: Annotated[
        Path | None, typer.Option("--out", help="Write the candidate rows here as YAML.")
    ] = None,
    all_boards: Annotated[
        bool, typer.Option("--all-boards", help="Keep probing after the first live board.")
    ] = False,
) -> None:
    """Do these companies have a board this product can collect?

    THE REGISTRY IS THE PLACE A PROFILE HIDES BEST. All 234 companies in
    `config/companies.yaml` carry `discovery_source: curated_technology_sourcing`,
    which is not a bug in any of them and is a bug in the set: a corpus meant to
    serve somebody moving into an executive assistant role, somebody starting in
    sales, and a customer success manager in Utah cannot have been sourced
    entirely from one industry. Growing it was an afternoon of manual probing,
    which is why it had not happened; this makes it a command.

    Everything it does, `pipeline/discover.py` already did. What is new is that
    a person can now ask the question about sixty companies without writing a
    script, and that the answer comes back in the shape
    `config/company_candidates.yaml` already uses.

    IT DOES NOT WALK A NAMESPACE. Each company gets a bounded set of plausible
    identifiers derived from its domain and its name -- five, not two hundred
    permutations -- and `stop_on_first` ends the probing at the first live
    board, because every extra request is one a vendor pays to serve.

    It writes nothing to the corpus and opens no database.
    """
    from datetime import UTC, datetime

    import yaml

    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.discover import BoardOutcome, discover, read_sourcing_list

    if not source.exists():
        raise typer.BadParameter(f"{source} does not exist")

    queries = read_sourcing_list(source.read_text(encoding="utf-8"))
    if not queries:
        raise typer.BadParameter(f"{source} named no companies")

    typer.secho(f"Probing {len(queries)} companies", bold=True)
    rows: list[dict[str, str]] = []
    found = 0
    with HttpFetcher() as fetcher:
        for query in queries:
            result = discover(
                fetcher,
                domain=query.domain,
                name=query.name,
                board_url=query.board_url,
                stop_on_first=not all_boards,
            )
            best = result.best
            if best is not None:
                found += 1
                # A board that answered with NOTHING is real and collectable;
                # V1.2 measured three of 116 recording NOT_FOUND and that was a
                # company taking its board down, not a broken connector.
                status: str = "SUPPORTED_BOARD"
                boards: str = ";".join(f"{p.provider}:{p.board_identifier}" for p in result.boards)
            elif result.had_temporary_failure:
                # NOT the same as absence, and the vocabulary already says so.
                status, boards = "INCONCLUSIVE", ""
            else:
                status, boards = "NO_BOARD_FOUND", ""

            colour = {
                "SUPPORTED_BOARD": typer.colors.GREEN,
                "INCONCLUSIVE": typer.colors.YELLOW,
            }.get(status)
            label = query.label
            count = best.posting_count if best else 0
            typer.secho(f"  {label:<34} {status:<16} {boards} {count or ''}", fg=colour)

            row = {
                "name": query.name or (result.canonical_domain or query.label),
                "canonical_domain": result.canonical_domain or "",
                "discovery_source": "curated_non_technology_sourcing",
                "status": status,
                "boards_found": boards,
                "probed_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            }
            if best and best.outcome is BoardOutcome.VALID_EMPTY:
                row["note"] = "the board answered and is empty today"
            rows.append(row)

    typer.echo("")
    typer.secho(f"{found} of {len(queries)} have a collectable board", bold=True)

    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            yaml.safe_dump({"candidates": rows}, sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        typer.echo(f"\nWrote {len(rows)} candidate rows to {out}.")
        typer.echo(
            "Promoting one into config/companies.yaml is a REVIEWED step and stays manual: "
            "the registry is what drives collection."
        )


def source_coverage_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print JSON rows.")] = False,
) -> None:
    """Per source: boards, last attempt and success, status, requests, fetched,
    new, updated, deduplicated, failed, canonical and open jobs, and why a
    refresh was partial. Changes no posting and makes no network call."""
    from career_agent.providers.registry import board_providers
    from career_agent.sources.coverage import coverage

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        rows = coverage(conn, families=set(board_providers()))
    finally:
        conn.close()
    if as_json:
        typer.echo(json.dumps([r.as_dict() for r in rows], indent=1))
        return
    total_open = sum(r.open_jobs for r in rows) or 1
    header = (
        f"{'source':<16}{'status':<10}{'boards':>7}{'open':>8}{'share':>7}{'fetched':>9}"
        f"{'new':>7}{'upd':>7}{'dup':>6}{'fail':>5}  last success          why"
    )
    typer.echo(header)
    for r in rows:
        typer.echo(
            f"{r.provider:<16}{r.status:<10}{r.boards:>7}{r.open_jobs:>8}"
            f"{100 * r.open_jobs / total_open:>6.1f}%"
            f"{(r.raw_fetched if r.raw_fetched is not None else '-'):>9}"
            f"{(r.new if r.new is not None else '-'):>7}"
            f"{(r.updated if r.updated is not None else '-'):>7}"
            f"{(r.deduplicated if r.deduplicated is not None else '-'):>6}"
            f"{r.failed:>5}  {(r.last_success or '-'):<22}"
            f"{r.partial_reason or ''}{'; '.join(r.notes)}"
        )


def discover_employer_boards_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    limit: Annotated[
        int, typer.Option("--limit", help="At most this many employers. Default 40.")
    ] = 40,
) -> None:
    """Find the Ashby, Greenhouse or Lever boards of employers seen on aggregators.

    Most relevant employers first. A board is registered only when it lists a
    posting title the aggregator showed for that employer, and every employer's
    answer is recorded so it is never probed twice. Collect the new boards with
    `career-agent collect` (or Refresh all in the app).
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.employer_boards import discover_employer_boards

    if not 1 <= limit <= 400:
        raise typer.BadParameter("--limit is 1 to 400")
    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        with HttpFetcher() as fetcher:
            stats = discover_employer_boards(conn, fetcher, limit=limit)
    finally:
        conn.close()
    _table([(k, v) for k, v in stats.as_dict().items()])


def source_health_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    only_problems: Annotated[
        bool, typer.Option("--problems", help="Only sources not currently producing")
    ] = False,
) -> None:
    """Are the sources actually working, and when did each last run.

    Reads the corpus and makes no request of any kind. The catalogue says what
    a source IS; this says what it DID, and keeps the two apart -- a source
    that has never been asked is not failing, and reading one as the other is
    how a person learns to ignore a health report.
    """
    from career_agent.sources.health import health, summarise

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        entries = health(conn)
    finally:
        conn.close()

    working = [e for e in entries if e.postings > 0]
    typer.secho(f"{len(working)} of {len(entries)} sources have postings in {db}", bold=True)
    typer.echo("  " + "  ".join(f"{k}={v}" for k, v in summarise(entries).items()))
    typer.echo("")

    for entry in entries:
        if only_problems and entry.postings > 0 and not entry.boards_with_errors:
            continue
        colour = typer.colors.GREEN if entry.postings else None
        typer.secho(
            f"  {entry.source.name:24} {entry.source.coverage.value:13} {entry.postings:>6}",
            fg=colour,
        )
        typer.echo(f"      {entry.note}")
        if entry.source.contradicted_because:
            # Louder than a note, because this is the one line here that is a
            # defect in our own catalogue rather than a fact about a vendor.
            typer.secho(
                f"      CATALOGUE DISAGREES WITH THE CORPUS: {entry.source.contradicted_because}",
                fg=typer.colors.RED,
                bold=True,
            )

    typer.echo("")
    typer.echo("A source with an adapter is not a source with postings.")


# =====================================================================
# cv-import
# =====================================================================
def cv_import_command(
    path: Annotated[Path, typer.Argument(help="Your CV. PDF, DOCX, TXT or MD.")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    review: Annotated[
        bool, typer.Option("--review", help="Decide on each proposal one at a time")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run/--no-dry-run", help="Show the proposals and store nothing"),
    ] = True,
) -> None:
    """Read your CV on this machine, and propose what it appears to say.

    NOTHING IS CONFIRMED BY BEING READ. Every line becomes a PROPOSAL carrying
    the text it came from, and a proposal becomes a fact about you only when
    you accept it. That is the whole design: a CV is a document you wrote about
    yourself, often years ago, and reading a line off one is not the same as
    standing behind it today.

    Local and private. No network call, no model of either kind, and the text
    is never written anywhere except the claims you accept.

    A dry run by default. `--no-dry-run --review` goes through the proposals
    one at a time: accept, edit, reject or stop. There is no way to confirm
    them all at once, here or anywhere else in Career Agent: every confirmed
    statement is one somebody read (docs/CAREER_EVIDENCE.md).
    """
    from career_agent.cv.extract import CvError, extract
    from career_agent.cv.propose import read_cv, to_claim

    try:
        found = extract(path)
    except CvError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.secho(f"Read {found.source_name}", bold=True)
    _table(
        [
            ("kind", found.kind),
            ("pages", found.pages),
            ("characters", found.characters),
        ]
    )
    if found.looks_empty:
        typer.secho(
            "\n  Almost no text came out. If this is a scanned CV the pages are"
            " images, and there is nothing here to read.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    read = read_cv(found.text)
    typer.echo("")
    typer.secho(f"{len(read.proposals)} proposals, none of them confirmed", bold=True)
    typer.echo(f"  sections found : {', '.join(sorted(read.sections)) or 'none'}")
    if read.unread_lines:
        typer.echo(f"  before any heading : {len(read.unread_lines)} lines, not proposed")
    jobs = [entry for entry in read.entries if entry.section == "experience"]
    if jobs:
        typer.echo(f"  experiences found : {len(jobs)}")
        for entry in jobs:
            when = entry.span.text if entry.span is not None else "dates not stated"
            typer.echo(f"    {entry.title or 'not named'} ({when})")

    by_type: dict[str, list] = {}
    for proposal in read.proposals:
        by_type.setdefault(proposal.claim_type.value, []).append(proposal)

    for claim_type, group in by_type.items():
        typer.echo("")
        typer.secho(f"  {claim_type} ({len(group)})", bold=True)
        for proposal in group:
            figure = "  [carries a figure]" if proposal.has_measurement else ""
            typer.echo(f"    {proposal.text[:88]}{figure}")

    if dry_run:
        typer.echo("")
        typer.echo("  Nothing was stored. Add --no-dry-run --review to answer them one at a time,")
        typer.echo("  or review them by experience in Career Evidence.")
        typer.echo("  no network call and no inference call of either kind")
        return

    if not review:
        typer.secho(
            "\n  Refusing to store proposals nobody accepted."
            " Add --review to answer them one at a time.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    decisions = _review(read.proposals)
    if not decisions:
        typer.echo("\n  Nothing accepted. Nothing stored.")
        return

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        from career_agent.storage.db import transaction
        from career_agent.storage.repositories import ClaimRepo

        repo = ClaimRepo(conn)
        candidate_id = _sole_candidate(conn)
        stored = 0
        with transaction(conn):
            for proposal, text in decisions:
                # The company and months of the job the line sits under, as
                # the listing above showed them. Headings are never claims.
                job = read.entry(proposal.entry_key)
                span = job.span if job is not None else None
                claim = to_claim(
                    proposal,
                    text=text,
                    employer=job.company if job is not None else None,
                    period_start=span.start if span is not None else None,
                    period_end=span.end if span is not None else None,
                )
                repo.supersede(candidate_id, claim)
                stored += 1
    finally:
        conn.close()

    typer.secho(f"\n  {stored} claims confirmed and stored", fg=typer.colors.GREEN)
    typer.echo("  `career-agent evidence` lists what you can now safely draw on.")


def _review(proposals: list) -> list:
    """Each proposal, one at a time, with three real answers.

    ACCEPT, EDIT, REJECT -- and edit is the one that matters most. A CV line is
    often nearly right, and a review offering only accept-or-reject pushes
    somebody into accepting something they would have corrected, which is how a
    stale sentence ends up being repeated in an interview.

    Returns the accepted proposals paired with the text to store. Anything
    rejected is simply absent: nothing records that somebody said no, because a
    list of rejections is a list of things about themselves they did not want
    kept.
    """
    kept: list = []
    total = len(proposals)
    typer.echo("")
    typer.secho("Reviewing. Nothing is stored until the end.", bold=True)
    typer.echo("  [a] accept   [e] edit   [r] reject   [q] stop here")

    for index, proposal in enumerate(proposals, start=1):
        typer.echo("")
        typer.secho(f"  {index}/{total}  {proposal.claim_type.value}", bold=True)
        typer.echo(f"    {proposal.text}")
        if proposal.evidence.strip() != proposal.text.strip():
            typer.echo(f"    from: {proposal.evidence[:88]}")
        if proposal.has_measurement:
            typer.echo("    this carries a figure. Check it says what you remember saying.")

        # NO DEFAULT. An Enter that meant "accept" made holding the key down
        # (or piping blank lines in) a way to confirm every proposal unread.
        answer = ""
        while answer not in {"a", "e", "r", "q"}:
            answer = typer.prompt("    [a/e/r/q]").strip().lower()[:1]
        if answer == "q":
            typer.echo("    stopping. Everything accepted so far is kept.")
            break
        if answer == "r":
            continue
        if answer == "e":
            edited = typer.prompt("    corrected text", default=proposal.text).strip()
            if edited:
                kept.append((proposal, edited))
            continue
        kept.append((proposal, proposal.text))

    typer.echo("")
    typer.secho(f"  {len(kept)} of {total} accepted", bold=True)
    return kept


def _sole_candidate(conn) -> str:
    """The one candidate row, created if this database has none.

    Personal Alpha is N=1 by design. A multi-candidate story is a migration and
    not something this command should invent on the way past.
    """
    from career_agent.clock import new_id

    row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
    if row is not None:
        return str(row["id"])
    candidate_id = new_id()
    from career_agent.clock import now_utc

    conn.execute(
        "INSERT INTO candidate (id, candidate_key, display_name, created_at)"
        " VALUES (?, 'owner', 'You', ?)",
        (candidate_id, now_utc()),
    )
    conn.commit()
    return candidate_id


# =====================================================================
# evidence
# =====================================================================
def _waiting_for_review(conn: object) -> int:
    """How many suggestions still have no answer, in imports she is working on.

    The SAME definition every screen uses (`storage/review_counts.py`): the
    intake package in force and every CV read not archived or deleted. A
    database predating those tables answers zero instead of raising, because
    this command is also how somebody finds out what state their workspace
    is in.
    """
    from career_agent.storage.review_counts import review_counts

    try:
        return review_counts(conn).waiting  # type: ignore[arg-type]
    except Exception:
        return 0


def evidence_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """What has been confirmed about you, and can safely be drawn on.

    One question, answered plainly: which facts has this system been told are
    true. Only confirmed claims appear. A proposal read off a CV and never
    accepted is a draft and is not here, which is what makes the review step
    mean something.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        from career_agent.storage.repositories import ClaimRepo

        row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
        claims = ClaimRepo(conn).current(str(row["id"])) if row is not None else []
        # What is ALREADY WAITING. An empty ledger beside a staged package of
        # three hundred proposals is not "nothing to do"; it is "nothing has
        # been answered yet", and a message that cannot tell those apart sends
        # somebody off to re-import a document the product already read.
        waiting = _waiting_for_review(conn)
    finally:
        conn.close()

    confirmed = [claim for claim in claims if claim.verified]
    typer.secho(f"{len(confirmed)} confirmed facts", bold=True)
    if not confirmed:
        typer.echo("  Nothing is confirmed about you yet. Nothing here is confirmed by being")
        typer.echo("  read: a proposal becomes a fact only when you say so.")
        if waiting:
            typer.echo("")
            typer.secho(
                f"  {waiting} proposals are staged and waiting for you.", fg=typer.colors.YELLOW
            )
            typer.echo(f"    uv run career-agent intake-review --db {db}")
            typer.echo(
                "    uv run career-agent intake-answer --help   # confirm, correct or reject"
            )
            # The count includes CV reads, which are reviewed in the interface
            # (Career Evidence), experience by experience.
            typer.echo("  CV suggestions are reviewed in Career Evidence:")
            typer.echo("    uv run career-agent serve")
        else:
            typer.echo("")
            typer.echo("  Build a package from your own documents, on this machine:")
            typer.echo("    uv run career-agent intake-build --cv <your-cv> --out import.json")
            typer.echo(f"    uv run career-agent intake-import import.json --db {db}")
        return

    by_type: dict[str, list] = {}
    for claim in confirmed:
        by_type.setdefault(claim.claim_type.value, []).append(claim)

    for claim_type, group in sorted(by_type.items()):
        typer.echo("")
        typer.secho(f"  {claim_type} ({len(group)})", bold=True)
        for claim in group:
            typer.echo(f"    {claim.text[:92]}")
            if claim.evidence_ref and claim.evidence_ref.strip() != claim.text.strip():
                typer.echo(f"        from: {claim.evidence_ref[:84]}")

    unconfirmed = len(claims) - len(confirmed)
    if unconfirmed:
        typer.echo(f"\n  {unconfirmed} more are drafts and are not counted above.")


# =====================================================================
# prepare
# =====================================================================
def prepare_command(
    job_id: Annotated[str, typer.Argument(help="The posting to prepare for")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    export: Annotated[
        Path | None,
        typer.Option("--export", help="Write the selection to a file you can work from."),
    ] = None,
) -> None:
    """What this posting asks for, beside what you have evidence of.

    The gaps are the half worth reading. Nothing is hidden because you lack it:
    a view listing only the matches would be a view that flatters, and what is
    missing is what to prepare, what to be honest about, and sometimes the
    reason not to apply.

    `--export` writes the same thing as a file: your own confirmed sentences,
    in the order this posting argues for, with the gaps beneath them. Every
    line in it is one you confirmed, word for word. Nothing in the file was
    written by this program about you, and there is no code path that would
    produce a cover letter.

    THE FILE HOLDS YOUR CAREER. Write it somewhere outside this repository:
    `data/` and `backups/` are gitignored, anywhere else is not, and this
    command will not guess where you meant.

    Reports. Recommends nothing, scores nothing, and applies to nothing.
    """
    from career_agent.match.preparation import Readiness, prepare
    from career_agent.match.resume import plan as resume_plan
    from career_agent.match.resume_export import as_text
    from career_agent.storage.mvp_repo import MatchRepo
    from career_agent.storage.repositories import ClaimRepo

    db = resolve_database(RuntimeMode.PERSONAL, db)
    config, _ = _load_config(config_dir)
    conn = _open_personal(db)
    try:
        result = MatchRepo(conn).get(job_id, config.config_id, config.config_version)
        if result is None:
            typer.secho(
                f"No score for {job_id} under {config.config_id} v{config.config_version}."
                " Run `career-agent rescore` first.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=2)
        row = conn.execute("SELECT id FROM candidate LIMIT 1").fetchone()
        claims = ClaimRepo(conn).current(str(row["id"])) if row is not None else []
        job = conn.execute(
            "SELECT j.title, c.name AS company FROM job j"
            " JOIN company c ON c.id = j.company_id WHERE j.id = ?",
            (job_id,),
        ).fetchone()
    finally:
        conn.close()

    typer.secho(f"{job['company']} -- {job['title']}" if job else job_id, bold=True)
    plan = prepare(config, result, claims)
    typer.echo("  " + "  ".join(f"{k}={v}" for k, v in plan.counts.items()))

    headings = {
        Readiness.MATCHED: "You have done this",
        Readiness.PARTIAL: "You have named this, without evidence of doing it",
        Readiness.GAP: "Nothing confirmed",
        Readiness.UNRESOLVED: "This system cannot tell",
    }
    for readiness, heading in headings.items():
        rows = plan.of(readiness)
        if not rows:
            continue
        typer.echo("")
        typer.secho(f"  {heading} ({len(rows)})", bold=True)
        for requirement in rows:
            typer.echo(f"    {requirement.label}")
            if requirement.posting_quote:
                typer.echo(f'        they say: "{requirement.posting_quote[:78]}"')
            if requirement.evidence_text:
                typer.echo(f'        you say:  "{requirement.evidence_text[:78]}"')

    if plan.concerns:
        typer.echo("")
        typer.secho(f"  To settle before applying ({len(plan.concerns)})", bold=True)
        for concern in plan.concerns:
            typer.echo(f"    [{concern.kind}] {concern.detail}")

    if not claims:
        typer.echo("")
        typer.echo("  Nothing is confirmed about you yet, so everything reads as a gap.")
        # Point at what is ALREADY WAITING before suggesting a fresh import.
        # Sending somebody to re-read a document the product already read is
        # how a review nobody finished becomes a review nobody starts.
        typer.echo("  `career-agent evidence` says what is waiting for you.")

    if export is not None:
        # SELECTION AND ORDER, WRITTEN OUT. Every line of the file is one of
        # her own confirmed sentences or a requirement label from her own
        # configuration; `match/resume_export.py` owns the layout and a test
        # walks its syntax tree to keep it from composing one.
        text = as_text(
            resume_plan(plan, claims),
            title=str(job["title"]) if job else job_id,
            company=str(job["company"]) if job else "",
        )
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text(text, encoding="utf-8")
        typer.echo("")
        typer.secho("Written", bold=True)
        _table([("to", export), ("size", f"{export.stat().st_size} bytes")])
        typer.echo("")
        typer.echo("  It holds sentences you confirmed about your own career.")
        typer.echo("  Nothing in it was written by this program about you.")


# =====================================================================
# backup
# =====================================================================
def backup_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    out: Annotated[Path | None, typer.Option("--out", help="Where to write the archive")] = None,
) -> None:
    """Copy everything that would hurt to lose, and nothing that would hurt to keep.

    This is a local-first product with no server behind it. The corpus, the
    applications, the dates you applied and the search you spent an evening
    tuning live in two places on one machine and nowhere else.

    NO CREDENTIAL IS EVER COPIED. A backup gets emailed, put on a stick and
    forgotten, and a key inside one outlives every intention about it. The
    manifest says so in words, so restoring does not become an hour spent
    looking for a key that was deliberately never there.

    Opens no socket and uploads nothing. The archive goes where you ask.
    """
    import tempfile

    from career_agent.storage.backup import contents, create_backup

    db = resolve_database(RuntimeMode.PERSONAL, db)
    if not db.exists():
        typer.secho(f"no database at {db}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    stamp = _utc_stamp()
    destination = out or Path("backups") / f"career-agent-{stamp}.zip"

    with tempfile.TemporaryDirectory() as scratch:
        result = create_backup(
            db=db,
            config_dir=config_dir,
            destination=destination,
            staging=Path(scratch) / "backup",
        )

    typer.secho(f"Wrote {result.path}", bold=True)
    _table(
        [
            ("postings", result.jobs),
            ("applications tracked", result.applications),
            ("facts you confirmed", result.claims),
            ("CV proposals still to answer", result.pending_proposals),
            ("jobs with your notes on them", result.notes),
            ("config files", len(result.config_files)),
            ("archive bytes", result.total_bytes),
        ]
    )
    for name in result.config_files:
        typer.echo(f"  config/{name}")
    if result.skipped_secrets:
        typer.echo("")
        typer.secho(
            f"  not copied, on purpose: {', '.join(result.skipped_secrets)}",
            fg=typer.colors.YELLOW,
        )
        typer.echo("  re-create it by hand after restoring. Nothing else here needs it.")

    typer.echo("")
    typer.echo(f"  {len(contents(result.path))} entries, including MANIFEST.json")
    typer.echo("  nothing was uploaded and no network call was made")


def _utc_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


# =====================================================================
# integrity
# =====================================================================
def integrity_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Check the database against invariants the schema cannot express.

    Reads only. Nothing here repairs anything, and that is deliberate: two of
    the tables it inspects hold RAW OBSERVATIONS, and an observation this
    system edits is no longer an observation. Derived rows are rebuilt by
    `rescore`, which is a separate command the owner runs deliberately.

    Exit code 1 if anything is BROKEN, so it can be used as a gate. STALE and
    NOTE do not fail: a score computed by an older build was correct when it
    was written, and history kept on purpose is not a defect.
    """
    from career_agent.storage.integrity import audit

    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        findings = list(audit(conn))
    finally:
        conn.close()

    typer.echo(f"checking {db}")
    if not findings:
        typer.secho("\nEvery invariant holds.", fg=typer.colors.GREEN, bold=True)
        return

    broken = [f for f in findings if f.is_broken]
    typer.echo("")
    for finding in findings:
        colour = {
            "BROKEN": typer.colors.RED,
            "STALE": typer.colors.YELLOW,
        }.get(finding.severity)
        typer.secho(f"  [{finding.severity}] {finding.check} -- {finding.count}", fg=colour)
        typer.echo(f"      {finding.detail}")
        typer.echo(f"      e.g. {', '.join(finding.examples)}")

    if broken:
        typer.secho(
            f"\n{len(broken)} check(s) found rows a reader would see as wrong.",
            fg=typer.colors.RED,
            bold=True,
        )
        raise typer.Exit(code=1)
    typer.secho("\nNothing broken. The findings above are history or staleness.", bold=True)


def _personal_databases() -> list[tuple[Path, int]]:
    """Every personal database under `data/`, with how many open jobs it holds.

    Read-only and best-effort. A file that is not a database, or is a demo,
    or cannot be opened, is simply not a candidate -- this is a convenience
    for finding the right file, never an authority on what a file is.
    """
    import sqlite3

    found: list[tuple[Path, int]] = []
    root = Path("data")
    if not root.is_dir():
        return found
    for candidate in sorted(root.rglob("*.db")):
        try:
            conn = sqlite3.connect(f"file:{candidate}?mode=ro", uri=True)
            try:
                kind = conn.execute("SELECT kind FROM database_identity LIMIT 1").fetchone()
                if kind is None or str(kind[0]).upper() != "PERSONAL":
                    continue
                total = int(
                    conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0]
                )
            finally:
                conn.close()
        except sqlite3.Error:
            continue
        found.append((candidate, total))
    return found


def start_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    port: Annotated[int, typer.Option("--port")] = 8765,
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Open your workspace. The one command.

    It resolves which database is yours the way every other command does --
    an explicit `--db`, then `CAREER_AGENT_DB`, then the default -- and then
    does the one thing `serve` cannot: it LOOKS before it serves.

    An empty `data/career.db` beside a corpus of twenty thousand postings is
    the single most common way to be confused by this product. Serving the
    empty one renders a working interface with nothing in it, which reads as
    "the setup failed" and is not. So when the resolved database is empty and
    another personal one is not, this stops and says which.

    Opens no socket to anything but your own machine, and asks no model of
    either kind.
    """
    resolved = resolve_database(RuntimeMode.PERSONAL, db)
    others = [(path, total) for path, total in _personal_databases() if total > 0]
    chosen = resolved

    # **The fallback fires only when NOTHING was stated.** An explicit `--db`,
    # or a `CAREER_AGENT_DB` somebody set on purpose, is a decision. A first
    # version of this overrode both, which is the same mistake as letting a
    # sentence of prose overrule a field the employer filled in: a stated
    # answer does not get second-guessed by a helpful default.
    from os import environ

    from career_agent.runtime.mode import PERSONAL_DB_ENV

    stated = db is not None or bool(environ.get(PERSONAL_DB_ENV))
    here = next((t for path_, t in others if path_.resolve() == resolved.resolve()), 0)

    if here == 0:
        if stated:
            # Open what was asked for, and say plainly that it is empty. An
            # empty database on a fresh install is not a fault, and refusing
            # to open one is how somebody concludes the setup failed.
            typer.secho(
                f"{resolved} holds no postings. Opening it anyway, because you asked for it.",
                fg=typer.colors.YELLOW,
            )
            for path_, total in others:
                typer.echo(f"    {path_} holds {total}")
        elif len(others) == 1:
            chosen = others[0][0]
            typer.secho(
                f"{resolved} holds no postings; using {chosen}, the only one that does.",
                fg=typer.colors.YELLOW,
            )
            typer.echo("  Set CAREER_AGENT_DB to say so once and stop the guessing.")
        elif others:
            typer.secho(
                f"{resolved} holds no postings, and more than one other does.",
                fg=typer.colors.RED,
            )
            for path_, total in others:
                typer.echo(f"    {path_}   {total} jobs")
            typer.echo("")
            typer.echo("Say which, rather than have this choose for you:")
            typer.echo(f"  uv run career-agent start --db {others[0][0]}")
            raise typer.Exit(code=1)

    conn = _open_personal(chosen)
    try:
        jobs = int(conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0])
        confirmed = int(
            conn.execute(
                "SELECT COUNT(*) FROM verified_claim"
                " WHERE superseded_by_id IS NULL AND verified = 1"
            ).fetchone()[0]
        )
        waiting = _waiting_for_review(conn)
        unscored = int(conn.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0])
    finally:
        conn.close()

    # **NO ADDRESS HERE.** This block runs before the socket is bound, and an
    # address printed by a process that has not got one is the whole failure
    # this session was called in for: on 2026-09-08 a second copy printed a
    # complete, truthful preamble ending in `http://127.0.0.1:8765` while an
    # older process from the previous day owned that port and answered every
    # request. `serve_command` prints the URL once it HAS the socket, and one
    # line saying where to go is better placed after the thing it points at
    # exists.
    typer.secho("\nCareer Agent", bold=True)
    _table(
        [
            ("your database", str(chosen)),
            ("jobs you can browse", jobs),
            ("facts you have confirmed", confirmed),
            ("proposals waiting for you", waiting),
            ("postings changed since last scored", unscored),
        ]
    )
    if unscored:
        typer.echo(
            f"  {unscored} postings were collected or changed after the last scoring pass;"
            " the interface offers to score them, or run `career-agent rescore`."
        )
    if confirmed == 0 and waiting:
        typer.secho(
            f"\n  {waiting} proposals are staged. Until you answer them, every requirement "
            "on every posting reads as a gap.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")

    serve_command(db=chosen, port=port, open_browser=open_browser)


# =====================================================================
# the Candidate Intake Package
# =====================================================================
def intake_build_command(
    cv: Annotated[Path | None, typer.Option("--cv", help="Your CV. PDF, DOCX, TXT or MD.")] = None,
    linkedin: Annotated[
        Path | None, typer.Option("--linkedin", help="A LinkedIn profile PDF export.")
    ] = None,
    document: Annotated[
        list[Path] | None, typer.Option("--document", help="Any other career document.")
    ] = None,
    out: Annotated[Path, typer.Option("--out", help="Where to write the package")] = Path(
        "career-agent-import.json"
    ),
) -> None:
    """Build an intake package from your documents, on this machine.

    NOTHING LEAVES THIS COMPUTER. The documents are read here, by the same
    extractor `cv-import` uses, and the result is a file on your disk.

    The other way to produce this file is to copy the prompt in
    `docs/product/intake-prompt-v1.md` into an AI you already use. That path
    SENDS YOUR DOCUMENTS to whichever provider you choose, which is a decision
    only you can make. This command exists so that it is a choice rather than
    the only way through.

    Whatever you build, nothing in it is confirmed. `intake-import` stages it
    and you answer each claim yourself.
    """
    from career_agent.intake.build import IntakeDocument, build_package, package_json

    chosen: list[tuple[Path, str, str]] = []
    if cv is not None:
        chosen.append((cv, "cv", "RESUME"))
    if linkedin is not None:
        chosen.append((linkedin, "linkedin", "LINKEDIN"))
    for index, extra in enumerate(document or [], start=1):
        chosen.append((extra, f"document-{index}", "DOCUMENT"))

    if not chosen:
        raise typer.BadParameter("give at least one of --cv, --linkedin or --document")
    for path, _, _ in chosen:
        if not path.is_file():
            raise typer.BadParameter(f"no such file: {path}")

    # The READING happens here, in the command that was given file names.
    # `build_package` takes bytes so that the browser -- where an upload
    # arrives as base64 inside a JSON body and must never be written to disk --
    # can call the same function.
    documents = [
        IntakeDocument(name=path.name, data=path.read_bytes(), ref=ref, kind=kind)
        for path, ref, kind in chosen
    ]
    package = build_package(documents)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(package_json(package), encoding="utf-8")

    typer.secho(f"\nwrote {out}", bold=True)
    _table(
        [
            ("documents read", len(documents)),
            ("claims proposed", len(package.claims)),
            ("confirmed by this", 0),
        ]
    )
    typer.secho(
        "\nThis file describes you. It is not gitignored by default -- keep it somewhere private.",
        fg=typer.colors.YELLOW,
    )
    typer.echo("\nNext:")
    typer.echo(f"  uv run career-agent intake-validate {out}")


def _read_package(path: Path):
    """One package file, parsed, or a BadParameter naming what is wrong."""
    from career_agent.intake import parse_package
    from career_agent.intake.models import IntakeParseError

    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise typer.BadParameter(f"cannot read {path}: {exc}") from exc
    try:
        return parse_package(raw)
    except IntakeParseError as exc:
        raise typer.BadParameter(f"{path.name} is not a valid intake package.\n  {exc}") from exc


def intake_validate_command(
    path: Annotated[Path, typer.Argument(help="career-agent-import.json")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Check an intake package and say what importing it would do.

    Writes nothing. This is the step to run on a file an assistant produced,
    because the useful answer is usually a refusal: a package that names an
    undeclared source, normalises a date the document did not state, or claims
    a fact is already verified is refused here rather than staged.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.intake.store import preview

    package = _read_package(path)
    conn = _open_personal(db)
    try:
        result = preview(conn, package, filename=path.name)
    finally:
        conn.close()

    typer.secho(f"\n{path.name} is a valid v{result.schema_version} package", bold=True)
    _table(
        [
            ("written by", result.generator),
            ("documents", ", ".join(result.sources)),
            ("claims in the file", result.claims_total),
            ("after merging duplicates", result.claims_total - result.duplicates_collapsed),
            ("you have not seen", result.claims_new),
            ("you already stand behind", result.claims_already_confirmed),
            ("disagreements found", result.conflict_groups),
            ("claims in a disagreement", result.conflicted_claims),
        ]
    )
    if result.missing_kinds:
        typer.echo("\nNothing in this package about: " + ", ".join(result.missing_kinds))
        typer.echo("  That is a fact about the file, not about you.")
    if result.existing_package_id:
        typer.secho(
            "\nThis exact file has been imported before. Importing it again reopens that "
            "review with your answers intact.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("\nNothing was written. `career-agent intake-import` stages it for review.")


def intake_import_command(
    path: Annotated[Path, typer.Argument(help="career-agent-import.json")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Stage an intake package for review. Nothing becomes a fact about you.

    Every claim lands UNREVIEWED, or CONFLICT where two documents disagree.
    Confirming is a separate act, one claim at a time, in `intake-review` or in
    the interface.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.intake.store import import_package, summary

    package = _read_package(path)
    conn = _open_personal(db)
    try:
        package_id = import_package(conn, package, filename=path.name)
        counts = summary(conn, package_id)
    finally:
        conn.close()

    typer.secho("\nStaged for review", bold=True)
    _table(
        [("package", package_id), *[(k.lower().replace("_", " "), v) for k, v in counts.items()]]
    )
    typer.echo("\nNothing here is confirmed. Review it with:")
    typer.echo(f"  uv run career-agent intake-review --db {db}")


def intake_review_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    package: Annotated[str | None, typer.Option("--package", help="Which staged package")] = None,
    show_all: Annotated[
        bool, typer.Option("--all", help="Include rows you have already answered")
    ] = False,
) -> None:
    """List what a staged package is waiting on. Read-only.

    Confirming and rejecting happen in the interface, one claim at a time,
    beside the evidence for each -- which is where a decision about your own
    career belongs. This command is how you see what is waiting without
    starting the server.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.intake.models import ReviewState
    from career_agent.intake.store import rows, summary

    conn = _open_personal(db)
    try:
        if package is None:
            found = conn.execute(
                "SELECT id, filename, created_at FROM intake_package"
                " WHERE status = 'ACTIVE' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if found is None:
                typer.echo(
                    "No intake package is in force. `career-agent intake-import` "
                    "stages one, and `intake-review` lists what is already staged."
                )
                return
            package = str(found["id"])
        counts = summary(conn, package)
        staged = rows(conn, package)
    finally:
        conn.close()

    typer.secho(f"\nPackage {package}", bold=True)
    _table([(k.lower().replace("_", " "), v) for k, v in counts.items()])
    typer.echo("")
    for row in staged:
        state = str(row["review_state"])
        if not show_all and state not in ReviewState.ANSWERABLE:
            continue
        import json as _json

        claim = _json.loads(str(row["payload_json"]))
        marker = "!" if row["conflict_group"] else " "
        typer.echo(f"  {marker} [{state:17}] {claim['type']:12} {claim['text'][:70]}")
        if row["conflict_group"]:
            typer.echo(f"        disagreement group: {row['conflict_group']}")
    typer.echo("\nConfirm or reject each one in the interface:")
    typer.echo(f"  uv run career-agent serve --db {db}")


def intake_answer_command(
    claim: Annotated[str, typer.Argument(help="The claim key, from intake-review")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    package: Annotated[str | None, typer.Option("--package")] = None,
    confirm: Annotated[
        bool, typer.Option("--confirm", help="You stand behind this, as written")
    ] = False,
    text: Annotated[
        str | None,
        typer.Option("--text", help="Your wording. Confirms YOUR sentence, not the file's."),
    ] = None,
    reject: Annotated[bool, typer.Option("--reject", help="No. Creates nothing.")] = False,
    unresolved: Annotated[
        bool, typer.Option("--unresolved", help="You looked and cannot answer yet")
    ] = False,
    reopen: Annotated[
        bool, typer.Option("--reopen", help="Undo an answer, returning it to the queue")
    ] = False,
) -> None:
    """Answer one staged claim. This is the only way a proposal becomes a fact.

    Four answers, and the third is the one that matters: accept-or-reject
    alone pushes somebody into keeping a sentence they would have corrected.
    `--text` confirms YOUR wording; the file's original stays beside it so
    the review can always show what arrived.

    A confirmation writes a `verified_claim`, which means this system will
    from then on say that YOU said it is true.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.intake import store

    chosen = [
        name
        for name, given in (
            ("--confirm", confirm or text is not None),
            ("--reject", reject),
            ("--unresolved", unresolved),
            ("--reopen", reopen),
        )
        if given
    ]
    if len(chosen) != 1:
        raise typer.BadParameter(
            "give exactly one of --confirm, --text, --reject, --unresolved or --reopen; "
            f"got {chosen or ['none']}"
        )

    conn = _open_personal(db)
    try:
        if package is None:
            found = conn.execute(
                "SELECT id FROM intake_package WHERE status = 'ACTIVE'"
                " ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if found is None:
                raise typer.BadParameter("no intake package is in force")
            package = str(found["id"])
        try:
            if reject:
                store.reject(conn, package, claim)
                answered = "rejected. Nothing was created."
            elif unresolved:
                store.mark_unresolved(conn, package, claim)
                answered = "left unresolved. It will not be shown as untouched."
            elif reopen:
                store.reopen(conn, package, claim)
                answered = "returned to the queue."
            else:
                store.confirm(conn, package, claim, text=text)
                answered = "confirmed in your own words." if text else "confirmed as written."
        except store.IntakeReviewError as exc:
            raise typer.BadParameter(str(exc)) from exc
        counts = store.summary(conn, package)
    finally:
        conn.close()

    typer.secho(f"\n{claim}: {answered}", bold=True)
    _table([(k.lower().replace("_", " "), v) for k, v in counts.items()])


# =====================================================================
# rebuild-bodies
# =====================================================================
def rebuild_bodies_command(
    provider: Annotated[str, typer.Option("--provider", help="Which source to re-read")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    execute: Annotated[
        bool, typer.Option("--execute", help="Write. Without it this is a dry run.")
    ] = False,
) -> None:
    """Re-derive stored posting bodies from payloads already on disk.

    For when an adapter is found to have read a source too conservatively.
    Get on Board is the case this was built for: 409 postings were stored with
    no description because the adapter declared the feed carried none, when in
    fact it splits each posting across five fields.

    **It opens no socket.** The input is `job_provider_payload`. Re-collecting
    would have been 409 requests for bytes already here.

    A dry run by default, because this rewrites rows in the personal corpus.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.pipeline.rebuild_bodies import UnsupportedProvider, rebuild_bodies

    conn = _open_personal(db)
    try:
        try:
            stats = rebuild_bodies(conn, provider=provider, dry_run=not execute)
        except UnsupportedProvider as exc:
            raise typer.BadParameter(str(exc)) from exc
    finally:
        conn.close()

    typer.secho(
        f"\nRebuild {'complete' if execute else 'PREFLIGHT (nothing written)'}: {provider}",
        bold=True,
    )
    _table(
        [
            ("jobs inspected", stats.jobs_inspected),
            ("gained a body", stats.jobs_given_a_body),
            ("body corrected", stats.jobs_body_changed),
            ("already current", stats.jobs_unchanged),
            ("payload yields nothing", stats.jobs_payload_yields_nothing),
            ("no archived payload", stats.jobs_without_payload),
            ("errors", stats.errors),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    if not execute:
        typer.echo("\nNothing was written. Add --execute to apply.")
    elif stats.jobs_given_a_body or stats.jobs_body_changed:
        typer.echo(
            f"\nThese rows now hold text they did not before. Run:"
            f"\n  uv run career-agent rescore --db {db}"
        )


# =====================================================================
# semantic-match
# =====================================================================
def semantic_match_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    run: Annotated[
        bool,
        typer.Option(
            "--run",
            help="Send the selected postings to the provider. Without it nothing is sent.",
        ),
    ] = False,
    limit: Annotated[
        int, typer.Option("--limit", help="At most N postings this run (0: the setting).")
    ] = 0,
    budget: Annotated[
        float, typer.Option("--budget", help="USD hard stop for a metered provider (0: setting).")
    ] = 0.0,
    order: Annotated[
        str,
        typer.Option(
            "--order",
            help="relevance (default), or priority: targeted-search postings first, then "
            "deterministic Search Fit.",
        ),
    ] = "relevance",
    since: Annotated[
        str,
        typer.Option(
            "--since",
            help="Only postings collected after this ISO time, or 'last-run' (the last semantic "
            "run's start); targeted-search postings are always included.",
        ),
    ] = "",
) -> None:
    """Semantic Search Fit: plan, or run, one bounded batch.

    Without `--run` this is a plan: which provider Auto (or your choice)
    would use, how many postings pass the prefilter and what it would cost.
    Nothing leaves the computer. With `--run`, each selected posting and your
    search intent go to that provider; answers pass the deterministic
    publication gate and the evaluated postings are scored again.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.pipeline.rescore import RescoreMode, rescore
    from career_agent.semantic.intent import search_intent
    from career_agent.semantic.routing import resolve
    from career_agent.semantic.runner import estimate, run_cap, run_semantic, select_candidates
    from career_agent.semantic.settings import load_settings

    config, _ = _load_config(config_dir)
    settings = load_settings(config_dir)
    overrides: dict[str, Any] = {}
    if limit > 0:
        overrides["max_jobs_per_run"] = limit
        overrides["max_jobs_per_subscription_run"] = min(limit, 500)
    if budget > 0:
        overrides["budget_per_run_usd"] = budget
    if overrides:
        settings = settings.model_copy(update=overrides)
    route = resolve(settings)
    if order not in ("relevance", "priority"):
        raise typer.BadParameter("--order is relevance or priority")
    conn = _open_personal(db)
    try:
        if since == "last-run":
            row = conn.execute("SELECT max(started_at) FROM semantic_run").fetchone()
            if not row or not row[0]:
                typer.secho(
                    "No earlier semantic run: --since last-run reads the whole pool.",
                    fg=typer.colors.YELLOW,
                )
            since = str(row[0]) if row and row[0] else ""
        elif since:
            # Compared as text against timestamps written as ...Z, so it is
            # parsed and written the same way; a typo is refused, never read
            # as a filter that silently drops everything.
            from datetime import UTC, datetime

            try:
                moment = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as exc:
                raise typer.BadParameter("--since is an ISO time or 'last-run'") from exc
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=UTC)
            since = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        intent = search_intent(config)
        selection = select_candidates(
            conn,
            config,
            intent,
            limit=run_cap(settings, route),
            order=order,
            since=since or None,
        )
        cost = estimate(route, selection, intent)
        _table(
            [
                ("provider", route.provider.id if route.provider else "deterministic only"),
                ("why", route.reason or "-"),
                ("fallbacks", route.fallbacks or "-"),
                ("postings passing the prefilter", selection.eligible),
                ("postings this run", cost.candidates),
                ("estimated input tokens", cost.input_tokens),
                ("expected USD", cost.expected_usd if cost.expected_usd is not None else "n/a"),
                ("worst case USD", cost.worst_usd if cost.worst_usd is not None else "n/a"),
                ("budget USD", settings.budget_per_run_usd),
            ]
        )
        if not run:
            typer.echo("Plan only. Nothing was sent. Add --run to evaluate.")
            return
        stats = run_semantic(
            conn,
            config,
            settings,
            route,
            requested=settings.mode.value,
            order=order,
            since=since or None,
        )
        typer.echo(json.dumps(stats.as_dict(), indent=1))
        if stats.published:
            rescored = rescore(
                conn, config, mode=RescoreMode.DIRTY, semantic=settings.uses_findings
            )
            typer.echo(f"rescored {rescored.jobs_scored} postings")
    finally:
        conn.close()


# =====================================================================
# rescore
# =====================================================================
def rescore_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    force: Annotated[
        bool,
        typer.Option("--force", help="Recompute EVERY open posting. The corpus-sized pass."),
    ] = False,
    dirty: Annotated[
        bool,
        typer.Option(
            "--dirty",
            help="Only the postings the ledger marks as changed. What a collector runs.",
        ),
    ] = False,
    job_id: Annotated[
        list[str] | None,
        typer.Option("--job-id", help="Recompute this posting whatever its score. Repeatable."),
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Recompute every open posting of this provider."),
    ] = None,
    board: Annotated[
        str | None,
        typer.Option("--board", help="Recompute every open posting of provider:identifier."),
    ] = None,
    plan_only: Annotated[
        bool,
        typer.Option("--plan", help="Say what would be scored, and score nothing."),
    ] = False,
    limit: Annotated[int, typer.Option("--limit", help="Stop after N jobs")] = 0,
    include_closed: Annotated[bool, typer.Option("--include-closed")] = False,
    no_replay: Annotated[
        bool,
        typer.Option(
            "--no-replay",
            help="Read every advert again instead of reusing stored readings.",
        ),
    ] = False,
) -> None:
    """Score the postings that need it against the search configuration.

    With no flags this is TARGETED: the postings the dirty ledger marks as
    changed, plus every open posting without a current score -- none at all
    when nothing moved, every posting when the configuration version or the
    result schema did. It reads exactly the postings it scores; it never
    walks the corpus to find them (ADR-0025).

    `--provider` and `--board` are for a READER that moved on a known subset,
    which is the pattern instead of a schema bump: `--provider workable` after
    a Workable field is read differently. `--force` is the whole corpus, for
    a reading whose meaning moved without its columns moving.

    WHEN ONLY THE ARITHMETIC MOVED, THE ADVERTS ARE NOT READ AGAIN. A targeted
    pass reuses the readings already stored beside each score -- the signals,
    the gates, the title and the four readings -- whenever the configuration
    sections a READING depends on are unchanged and the posting's own inputs
    have not moved. The report says how many postings were replayed, how many
    were read in full, and why any refusal happened. `--force`, `--provider`
    and `--board` never replay, which is what keeps them escape hatches;
    `--no-replay` is the same refusal for a targeted pass.

    Makes zero network calls and zero inference calls of either kind.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.pipeline.rescore import RescoreMode, plan, rescore

    config, config_path = _load_config(config_dir)
    conn = _open_personal(db)
    try:
        if force:
            mode = RescoreMode.ALL
        elif dirty:
            mode = RescoreMode.DIRTY
        elif job_id or provider or board:
            mode = RescoreMode.EXPLICIT
        else:
            mode = RescoreMode.TARGETED
        typer.echo(f"scoring {db} with {config_path} (v{config.config_version}), {mode.value}")
        if plan_only:
            chosen = plan(
                conn,
                config,
                mode=mode,
                job_ids=job_id or (),
                provider=provider,
                board=board,
                include_closed=include_closed,
            )
            typer.secho("\nRescore plan (nothing scored)", bold=True)
            _table(
                [
                    ("postings examined (index)", chosen.candidates_examined),
                    ("postings to score", len(chosen.targets)),
                    ("why", dict(chosen.breakdown) or "nothing to do"),
                    ("ledger marks", len(chosen.dirty)),
                    ("search rows to refresh", len(chosen.search_refresh)),
                    ("plan ms", chosen.plan_ms),
                ]
            )
            return
        stats = rescore(
            conn,
            config,
            mode=mode,
            job_ids=job_id or (),
            provider=provider,
            board=board,
            limit=limit or None,
            include_closed=include_closed,
            no_replay=no_replay,
            semantic=_semantic_on(config_dir),
        )
    finally:
        conn.close()

    typer.secho("\nRescore complete", bold=True)
    _table(
        [
            ("mode", stats.mode),
            ("postings examined (index)", stats.candidates_examined),
            ("targeted", stats.jobs_targeted),
            ("why", dict(stats.plan_breakdown) or "nothing to do"),
            ("scored", stats.jobs_scored),
            (
                "  from stored readings",
                f"{stats.jobs_replayed}"
                + (
                    f" (source v{stats.replay_source_version})"
                    if stats.replay_source_version is not None
                    else ""
                ),
            ),
            ("  by reading the advert", stats.jobs_read_in_full),
            ("  replay refused", dict(stats.replay_refused) or "nothing refused"),
            ("already current", stats.jobs_skipped_already_scored),
            ("no description", stats.jobs_skipped_no_description),
            ("closed meanwhile", stats.jobs_skipped_closed),
            ("blocked on screening", stats.blocked_screening),
            ("explicitly ineligible", stats.ineligible),
            ("eligibility unresolved", stats.unresolved),
            ("at or above shortlist", stats.shortlisted),
            ("errors", stats.errors),
            ("ledger cleared", f"{stats.dirty_cleared} of {stats.dirty_marked}"),
            ("search index", f"{stats.search_refresh}, {stats.search_rows_indexed} rows"),
            (
                "elapsed ms",
                f"{stats.elapsed_ms} (plan {stats.plan_ms}, score {stats.score_ms},"
                f" search {stats.search_ms})",
            ),
        ]
    )
    for sample in stats.error_samples:
        typer.secho(f"  ! {sample}", fg=typer.colors.YELLOW)
    typer.echo("\nzero network calls, zero hosted inference, zero local inference")


# =====================================================================
# seed-demo
# =====================================================================
def seed_demo_command(
    db: Annotated[Path, typer.Option("--db")] = Path("data/demo.db"),
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    source: Annotated[Path, typer.Option("--source")] = DEFAULT_DEMO_FILE,
) -> None:
    """Build a demo database from the sanitised corpus, and score it.

    Seventeen invented postings at invented companies. Nothing real, nothing
    personal, safe to screenshot. This is the database the browser acceptance
    tests and the portfolio screenshots both use.
    """
    from career_agent.pipeline.demo_seed import seed_demo

    config, config_path = _load_config(config_dir)
    conn = connect(db)
    try:
        migrate(conn)
        # Stamp BEFORE seeding, so a file that already belongs to the personal
        # population refuses the invented rows instead of receiving them and
        # being discovered later.
        with transaction(conn):
            stamp_identity(conn, RuntimeMode.DEMO, "demo")
        stats = seed_demo(conn, config, source=source)
    finally:
        conn.close()

    typer.secho("Demo database ready", bold=True)
    _table(
        [
            ("database", db),
            ("configuration", config_path),
            ("companies", stats["companies"]),
            ("postings", stats["postings"]),
            ("scored", stats["scored"]),
        ]
    )
    typer.echo(f"\nstart the interface with:\n  uv run career-agent serve --db {db}")


# =====================================================================
# serve
# =====================================================================
def serve_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    demo: Annotated[
        bool,
        typer.Option(
            "--demo/--personal",
            help="Serve the invented demo corpus. Must be asked for explicitly.",
        ),
    ] = False,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    port: Annotated[int, typer.Option("--port")] = 8765,
    host: Annotated[
        str, typer.Option("--host", help="Loopback only. Anything else is refused.")
    ] = "127.0.0.1",
    open_browser: Annotated[bool, typer.Option("--open/--no-open")] = True,
) -> None:
    """Start the local web interface. Loopback only; no authentication.

    The server has no authentication because it has no network exposure, and
    the second half of that sentence is enforced rather than assumed: a host
    that is not loopback is refused before the socket is created.
    """
    from career_agent.runtime.fingerprint import runtime_fingerprint
    from career_agent.web.api import JobsApi
    from career_agent.web.server import PortInUse, ServerConfig, build_server

    mode = RuntimeMode.DEMO if demo else RuntimeMode.PERSONAL
    db = resolve_database(mode, db)
    config, config_path = _load_config(config_dir)

    conn = connect(db)
    try:
        migrate(conn)
        try:
            identity = identity_of(conn, mode)
        except RuntimeModeError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        version = schema_version(conn)
        scored = conn.execute(
            "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?",
            (config.config_id, config.config_version),
        ).fetchone()[0]
        total = conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0]
    finally:
        conn.close()

    if mode.is_personal and not total:
        # The defect this replaces: the documented path served the demo
        # database, so an empty personal corpus looked like a working product
        # with 19 jobs in it. Onboarding, never invented rows.
        typer.secho(
            "This personal database has no postings yet.\n"
            f"Collect some:  uv run career-agent collect --db {db}\n"
            f"Or import one: uv run career-agent import-job --db {db} ...",
            fg=typer.colors.YELLOW,
        )

    if total and not scored:
        typer.secho(
            f"{total} jobs are present and none are scored under {config.config_id} "
            f"v{config.config_version}.\nRun:  uv run career-agent rescore --db {db}",
            fg=typer.colors.YELLOW,
        )

    # **BIND FIRST, ANNOUNCE SECOND.** Everything below this block describes a
    # running server, and none of it may be printed by a process that does not
    # have one. See `web.server.ExclusiveHTTPServer` for what used to happen
    # instead: on Windows a second process bound the same port in silence, the
    # FIRST one kept answering every request, and this banner truthfully
    # described a runtime the browser was not talking to.
    try:
        api = JobsApi(ServerConfig(db_path=db, config_dir=config_dir, host=host, port=port))
        httpd = build_server(api)
    except PortInUse as exc:
        typer.secho(f"{exc}\n", fg=typer.colors.RED, err=True)
        typer.secho(
            "Career Agent did NOT start. Something is already serving that address,\n"
            "and it may be an older copy of this program left running.\n\n"
            "Find out what it is:\n"
            f"  {_who_owns_port(exc.port)}\n\n"
            "Then stop that process, or start this one somewhere else:\n"
            f"  uv run career-agent start --port {exc.port + 1}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=2) from exc
    except ValueError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    url = f"http://{host}:{port}/"
    fingerprint = runtime_fingerprint()
    typer.secho("Career Agent -- local interface", bold=True)
    _table(
        [
            ("url", url),
            ("mode", identity.kind.banner),
            ("database", f"{db} (schema {version})"),
            ("configuration", f"{config_path} (v{config.config_version})"),
            ("jobs", total),
            ("scored", scored),
            ("local model", "on demand only -- never on page load"),
            # **THE SAME THREE VALUES `/api/health` REPORTS.** Printed so the
            # two can be compared without knowing anything about sockets: if
            # the browser's health panel names a different pid, the page is
            # being served by a different program than this one. That is not
            # hypothetical -- it is what happened on 2026-09-08, and every
            # line above was true of the wrong process.
            (
                "this process",
                f"pid {fingerprint['pid']} - started {fingerprint['started_at']} "
                f"- revision {fingerprint['revision']}",
            ),
        ]
    )
    typer.echo("\npress Ctrl+C to stop")

    if open_browser:
        import webbrowser

        try:
            opened = webbrowser.open(url)
        except webbrowser.Error:
            opened = False
        if not opened:
            typer.secho(
                f"No default browser could be opened. Open your browser and paste {url}",
                fg=typer.colors.YELLOW,
            )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        typer.echo("\nstopping")
    finally:
        httpd.server_close()


# =====================================================================
# import-job
# =====================================================================
def import_job_command(
    title: Annotated[str, typer.Option("--title")],
    company: Annotated[str, typer.Option("--company")],
    description_file: Annotated[
        Path, typer.Option("--description-file", help="A file holding the posting text")
    ],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    url: Annotated[str, typer.Option("--url")] = "",
    location: Annotated[str, typer.Option("--location")] = "",
) -> None:
    """Import one posting from a file. The path for every source we may not fetch.

    The description comes from a FILE rather than an argument on purpose: a
    job description pasted onto a command line ends up in PowerShell history,
    and it is often several kilobytes of text that would be mangled by quoting.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.pipeline.manual_import import ImportError_, import_posting
    from career_agent.web.api import _now

    if not description_file.is_file():
        typer.secho(f"no such file: {description_file}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1)

    config, _ = _load_config(config_dir)
    text = description_file.read_text(encoding="utf-8")

    conn = _open_personal(db)
    try:
        job_id = import_posting(
            conn,
            config,
            title=title,
            company=company,
            description=text,
            url=url or None,
            location=location or None,
            config_id=config.config_id,
            config_version=config.config_version,
            now=_now(),
        )
        row = conn.execute(
            "SELECT match_score, data_confidence, eligibility_status FROM job_match "
            "WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    except ImportError_ as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    typer.secho("Imported", bold=True)
    _table(
        [
            ("job id", job_id),
            ("title", title),
            ("company", company),
            ("match score", row["match_score"] if row else "not scored"),
            ("data confidence", row["data_confidence"] if row else "-"),
            ("eligibility", row["eligibility_status"] if row else "-"),
            ("provenance", "manual_import"),
        ]
    )


# =====================================================================
# enrich / ollama-check
# =====================================================================
def ollama_check_command() -> None:
    """One health request to the LOCAL Ollama endpoint. Nothing else.

    Reads `OLLAMA_BASE_URL` and `OLLAMA_MODEL` from the environment, refuses
    any endpoint that is not loopback, and never reads `OLLAMA_API_KEY` --
    local Ollama needs no key, and honouring one would be the first step
    towards a silent cloud fallback.
    """
    from career_agent.pipeline.enrich import probe

    state = probe()
    typer.secho("Local model", bold=True)
    _table(
        [
            ("endpoint", state.get("endpoint")),
            ("model", state.get("model")),
            ("reachable", state.get("reachable")),
            ("note", state.get("note")),
            ("installed", ", ".join(state.get("models", [])) or "-"),
        ]
    )
    if not state.get("reachable"):
        typer.secho(
            "\nThe application works fully without Ollama. Enrichment is the only "
            "feature that needs it.",
            fg=typer.colors.YELLOW,
        )


def enrich_command(
    job_id: Annotated[str, typer.Argument(help="The job to enrich")],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    ignore_threshold: Annotated[bool, typer.Option("--ignore-threshold")] = False,
) -> None:
    """Run the LOCAL model over one job that survived deterministic triage.

    One job, by id, typed by a person. There is no bulk mode, and the
    threshold is enforced unless it is explicitly waived.
    """
    db = resolve_database(RuntimeMode.PERSONAL, db)
    from career_agent.pipeline.enrich import EnrichmentRejected, EnrichmentUnavailable, enrich_one

    config, _ = _load_config(config_dir)
    thresholds = getattr(config, "thresholds", None)
    raw = (
        thresholds.get("local_ai_min_score")
        if isinstance(thresholds, dict)
        else getattr(thresholds, "local_ai_min_score", 60)
    )
    minimum = None if ignore_threshold else int(raw if raw is not None else 60)

    conn = _open_personal(db)
    try:
        state = enrich_one(
            conn,
            job_id,
            config_id=config.config_id,
            config_version=config.config_version,
            min_score=minimum,
        )
        row = conn.execute(
            "SELECT verified_count, rejected_count, model, endpoint, payload_json "
            "FROM job_enrichment WHERE job_id = ?",
            (job_id,),
        ).fetchone()
    except EnrichmentUnavailable as exc:
        typer.secho(str(exc), fg=typer.colors.YELLOW, err=True)
        raise typer.Exit(code=3) from exc
    except EnrichmentRejected as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    finally:
        conn.close()

    typer.secho("Local observation stored", bold=True)
    _table(
        [
            ("job", job_id),
            ("model", row["model"]),
            ("endpoint", row["endpoint"]),
            ("verified observations", row["verified_count"]),
            ("rejected (quote not in posting)", row["rejected_count"]),
            ("note", state.get("note")),
        ]
    )
    payload = json.loads(row["payload_json"])
    typer.echo(f"\n  summary: {payload.get('summary', '')}")
    typer.echo(f"  suggested action: {payload.get('recommended_action')}")
    typer.secho(
        "\nthis observation is displayed beside the score and is never part of it",
        fg=typer.colors.GREEN,
    )


# =====================================================================
# init-personal
# =====================================================================
def init_personal_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    label: Annotated[
        str,
        typer.Option("--label", help="A short name the interface shows instead of a path."),
    ] = "personal",
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Claim a database as YOUR personal one. Creates it if it does not exist.

    A database has to say which population it belongs to before anything will
    serve it, and this is how a personal one says so. The alternative -- infer
    it from the filename -- is a guess: copy `demo.db` to `personal.db` and the
    name lies while the contents do not.

    Refuses a database already stamped as demo. Demo rows are invented, so if
    that file really is meant to be personal, deleting it and collecting into a
    fresh one loses nothing and keeps the two populations apart.

    Makes no network call and contacts no model.
    """
    path = resolve_database(RuntimeMode.PERSONAL, db)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        migrate(conn)
        try:
            with transaction(conn):
                identity = stamp_identity(conn, RuntimeMode.PERSONAL, label)
        except RuntimeModeError as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=2) from exc
        total = conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0]
    finally:
        conn.close()

    typer.secho("Personal database ready", bold=True)
    _table(
        [
            ("database", path),
            ("reference", database_ref(path, identity.label)),
            ("kind", identity.kind.banner),
            ("postings", total),
            ("last retrieval", identity.last_retrieval_at or "never"),
        ]
    )
    if not total:
        typer.echo("\nNo postings yet. Collect some:")
        typer.echo(f"  uv run career-agent collect --db {path}")
    typer.echo("\nno network calls and no inference calls were made by this command")


# =====================================================================
# collect-speedrun
# =====================================================================
def collect_speedrun_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    scope: Annotated[
        str,
        typer.Option(
            "--scope",
            help="speedrun (the portfolio itself), portfolio (default), or everywhere.",
        ),
    ] = "portfolio",
    max_pages: Annotated[
        int,
        typer.Option(
            "--max-pages",
            help="Pages of 50 postings to read. 0 means as far as the API will serve.",
        ),
    ] = 2,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Read the a16z Speedrun Talent Network feed. Public, read-only, free.

    BOUNDED BY DEFAULT. Two pages, a hundred postings, because a run that read
    the whole feed would cost thousands of requests against someone else's
    service. Reading more is something you ask for with --max-pages.

    NO WRITE ACTION IS POSSIBLE FROM HERE. The published API has no write
    operation in its specification, this connector constructs none, and it
    sends nothing about you: the only query parameters it ever emits are page,
    scope, company and the attribution tag the API asks callers to send.

    The contract is checked against the live OpenAPI specification before the
    first feed request, and a mismatch refuses the run rather than collecting
    against a schema that moved.

    Postings already held from an employer's own board are recorded as a second
    sighting and are never overwritten with this source's copy.
    """
    from career_agent.pipeline.collect import new_fetcher
    from career_agent.pipeline.speedrun_collect import (
        ContractRefused,
        SpeedrunCollector,
    )
    from career_agent.providers.speedrun import SCOPES, SPEEDRUN_MAX_ATTEMPTS

    if scope not in SCOPES:
        typer.secho(
            f"unknown scope {scope!r}; choose one of {', '.join(SCOPES)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    path = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(path)
    # More attempts than the shared default: this API returns intermittent 500s,
    # and a lost page is a hole in the middle of the corpus rather than a
    # visible error. The politeness delay is the shared one, unchanged.
    fetcher = new_fetcher(max_attempts=SPEEDRUN_MAX_ATTEMPTS)
    try:
        collector = SpeedrunCollector(conn, fetcher)
        typer.echo(f"reading the Speedrun feed, scope={scope}, max_pages={max_pages or 'all'}")
        try:
            stats = collector.collect(scope=scope, max_pages=max_pages or None)
        except ContractRefused as exc:
            typer.secho(str(exc), fg=typer.colors.RED, err=True)
            raise typer.Exit(code=3) from exc
    finally:
        fetcher.close()
        conn.close()

    typer.secho("\nSpeedrun retrieval complete", bold=True)
    _table(
        [
            ("scope", stats.scope),
            ("pages read", stats.pages_read),
            ("postings seen", stats.postings_seen),
            ("already held (duplicates)", stats.duplicates_total),
            ("new postings", stats.jobs_new),
            ("new companies", stats.companies_new),
            ("detail requests", stats.details_fetched),
            ("detail failures", stats.details_failed),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    if stats.duplicates:
        typer.echo("\nmatched by:")
        for rule, count in sorted(stats.duplicates.items()):
            typer.echo(f"    {rule:<16} {count}")
    if stats.origin_hosts:
        typer.echo("\nwhere the apply links point:")
        for host, count in sorted(stats.origin_hosts.items(), key=lambda i: (-i[1], i[0]))[:10]:
            typer.echo(f"    {host:<32} {count}")

    if stats.claimed_total is not None:
        typer.echo(
            f"\nthe feed reports {stats.claimed_total} postings at this scope; "
            f"the API will serve at most {stats.servable_total}"
        )
        if stats.beyond_reach:
            typer.secho(
                f"  {stats.beyond_reach} are beyond the API's own page ceiling and "
                "were NOT retrieved",
                fg=typer.colors.YELLOW,
            )
    if stats.stopped_early:
        typer.echo("  this run stopped at its page limit; it did not read the whole feed")

    typer.echo("\nread-only: no write endpoint was called and no personal data was sent")
    typer.echo("run `career-agent rescore` to score the new postings offline")


# =====================================================================
# setup: the first run
# =====================================================================
def _ask_list(prompt: str, help_text: str) -> tuple[str, ...]:
    """One comma-separated answer, or nothing.

    Comma-separated rather than one-per-line with a sentinel, because a person
    who does not read the instructions still gets the common case right, and
    because an empty answer has to be the easy answer: every question here is
    optional and pressing Enter must never be a mistake.
    """
    typer.echo("")
    typer.secho(prompt, bold=True)
    typer.echo(f"  {help_text}")
    raw = typer.prompt(
        "  (comma separated, or press Enter to skip)", default="", show_default=False
    )
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _ask_choice(prompt: str, options: tuple[str, ...], help_text: str) -> tuple[str, ...]:
    typer.echo("")
    typer.secho(prompt, bold=True)
    typer.echo(f"  {help_text}")
    typer.echo(f"  choices: {', '.join(options)}")
    raw = typer.prompt(
        "  (comma separated, or press Enter to skip)", default="", show_default=False
    )
    return tuple(part.strip().upper() for part in raw.split(",") if part.strip())


def _ask_text(prompt: str, help_text: str) -> str | None:
    typer.echo("")
    typer.secho(prompt, bold=True)
    typer.echo(f"  {help_text}")
    value = typer.prompt("  (or press Enter to skip)", default="", show_default=False).strip()
    return value or None


def _ask_yes_no(prompt: str, help_text: str) -> bool | None:
    """Three answers, not two. Enter means "leave it as it is"."""
    typer.echo("")
    typer.secho(prompt, bold=True)
    typer.echo(f"  {help_text}")
    raw = typer.prompt("  yes / no (or press Enter to skip)", default="", show_default=False)
    answer = raw.strip().lower()
    if answer in ("y", "yes", "s", "sim"):
        return True
    if answer in ("n", "no", "nao"):
        return False
    return None


def _ask_int(prompt: str, help_text: str) -> int | None:
    typer.echo("")
    typer.secho(prompt, bold=True)
    typer.echo(f"  {help_text}")
    raw = typer.prompt("  (a whole number, or press Enter to skip)", default="", show_default=False)
    raw = raw.strip().replace(",", "").replace(".", "")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        typer.secho(f"  {raw!r} is not a whole number; skipping this one.", fg=typer.colors.YELLOW)
        return None


def _interactive_answers():
    """Ask the questions. This function does nothing but collect."""
    from career_agent.config.setup import (
        CONTRACT_TYPES,
        PERIODS,
        SENIORITIES,
        WORK_MODELS,
        Answers,
    )

    typer.secho("\nSetting up your job search", bold=True)
    typer.echo(
        "Every question is optional. Press Enter to skip one and the shipped default\n"
        "stays. You can run this again whenever you like, and you can edit the file\n"
        "it writes by hand."
    )

    role_examples = _ask_list(
        "What kind of work are you looking for?",
        # The two examples that were here named one industry. The load-bearing
        # half of this sentence is the second one, which is also the founding
        # idea of the product: these are matched against the WHOLE posting.
        "Describe the work itself, in your own words, one phrase per line. "
        "Enter - alone to clear setup work phrases. "
        "These are matched against the WHOLE posting, not just the job title, "
        "so a good job with an odd title still finds you.",
    )
    skills = _ask_list(
        "Which tools and systems do you work with?",
        # NO EXAMPLES OF A PARTICULAR TRADE. This line used to read "HubSpot, "
        # "Salesforce, Python, Zapier", which are four tools from one industry
        # printed to every person who runs this command. A nurse, a lawyer and
        # a chef each read it as a statement about who the product is for.
        #
        # The sentence says what the ANSWER does instead, which is the part
        # nobody can guess: a tool named here earns points wherever it appears
        # in a posting.
        "Enter - alone to clear setup tools. "
        "Whatever you actually use by name, one per line. A posting that names "
        "one gets credit for it.",
    )
    keywords = _ask_list(
        "Any other words that matter to you?",
        "Anything worth points wherever it appears in a posting.",
    )
    negative = _ask_list(
        "What makes a job LESS interesting, without ruling it out?",
        # Kept deliberately abstract. Two examples from one trade here read as
        # advice about what to dislike.
        "These lose points wherever they appear. They never hide a job.",
    )
    exclusions = _ask_list(
        "What would rule a job out completely?",
        "The posting has to SAY one of these for the job to be excluded. A posting "
        "that stays silent is never excluded by them, because saying nothing is not "
        "the same as saying yes.",
    )

    country = _ask_text(
        "Which country do you live in?",
        "Two letters, like BR or PT. Used to work out whether a job's stated "
        "requirements would rule you out.",
    )
    regions = _ask_list(
        "Which parts of the world would you work for?",
        "LATAM, AMERICAS, EMEA, EU, WORLDWIDE.",
    )
    models = _ask_choice(
        "Office, hybrid or remote?",
        WORK_MODELS,
        "Which arrangements you would accept.",
    )
    require_remote = _ask_yes_no(
        "Remote only?",
        "Yes hides anything that is not remote. No keeps everything you chose above.",
    )
    sponsorship = _ask_yes_no(
        "Would you need an employer to arrange your right to work?",
        "Answer no if you can already work where you live. Answering no removes the "
        "sponsorship rules, so postings that say they do not sponsor stop ruling you out.",
    )

    contracts = _ask_choice(
        "What kind of contract?", CONTRACT_TYPES, "The arrangements that work for you."
    )
    levels = _ask_choice("What level?", SENIORITIES, "The levels you are aiming at.")

    amount = _ask_int(
        "What are you aiming to earn?",
        "A number only. Pay is worth a few points at most and NEVER rules a job out.",
    )
    currency = None
    period = None
    if amount is not None:
        currency = _ask_text(
            "In which currency?",
            "Three letters, like BRL or USD. Required with an amount: nothing here "
            "converts between currencies, so a number on its own cannot be compared.",
        )
        period = _ask_text(
            f"Per month or per year? ({' or '.join(PERIODS)})",
            "Postings are converted between the two for you, within one currency.",
        )

    shortlist = _ask_int(
        "Above what score is a job worth your time?",
        "0 to 100. The default is 55. This only decides what counts as a shortlist; "
        "nothing is hidden by it.",
    )
    fresh = _ask_int(
        "After how many days is a posting old news?",
        "The default is 14. Older postings are still shown, just marked.",
    )
    label = _ask_text(
        "Give this search a name.", "Shown in the interface so you know which search is loaded."
    )

    return Answers(
        role_examples=(() if role_examples == ("-",) else role_examples or None),
        skills=(() if skills == ("-",) else skills or None),
        keywords=keywords,
        negative_keywords=negative,
        hard_exclusions=exclusions,
        residence_country=country,
        target_regions=regions,
        accepted_work_models=models,
        require_remote=require_remote,
        needs_visa_sponsorship=sponsorship,
        preferred_contracts=contracts,
        preferred_seniorities=levels,
        target_amount=amount,
        currency=currency,
        period=period,
        shortlist_min_score=shortlist,
        fresh_days=fresh,
        label=label,
    )


def _answers_from_file(path: Path):
    """Answers read from a YAML file. How the wizard is tested, and scripted."""
    from career_agent.config.setup import Answers
    from career_agent.yaml_io import safe_load

    raw = safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        typer.secho(f"{path} must contain a mapping", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    known = {field.name for field in dataclasses.fields(Answers)}
    unknown = sorted(set(raw) - known)
    if unknown:
        # Refused rather than ignored. A typo'd key that is silently dropped is
        # an answer the person believes they gave.
        typer.secho(
            f"unknown answer(s) in {path.name}: {', '.join(unknown)}.\n"
            f"accepted: {', '.join(sorted(known))}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    tuples = {
        "role_examples",
        "skills",
        "keywords",
        "negative_keywords",
        "hard_exclusions",
        "target_regions",
        "accepted_work_models",
        "preferred_contracts",
        "preferred_seniorities",
    }
    kwargs: dict[str, Any] = {}
    for key, value in raw.items():
        kwargs[key] = tuple(value) if key in tuples and value is not None else value
    return Answers(**kwargs)


def setup_command(
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    answers_file: Annotated[
        Path | None,
        typer.Option("--answers", help="Read answers from a YAML file instead of asking."),
    ] = None,
    worked_example: Annotated[
        bool,
        typer.Option(
            "--example",
            help="Start from the committed worked example instead of a blank search.",
        ),
    ] = False,
) -> None:
    """Set up your job search. Writes config/search.local.yaml, which stays here.

    Run this once when you start, and again whenever what you want changes.
    Re-running it EDITS your existing settings rather than replacing them: every
    question you skip leaves that setting exactly as it was.

    It starts from a BLANK search: the machinery with no phrases, no title
    rules, no country and no blockers, so what you end up with is yours.

    `config/search.worked-example.yaml` is a worked example, which means it is
    somebody's real job search -- forty-nine phrases about business systems and
    integration work, a country, and a list of work they will not do. If that
    is roughly your field, `--example` starts from it and editing it is often
    easier than starting from nothing. It is a choice rather than the default,
    because inheriting a stranger's search without being asked is not one.

    Neither form overrides settings you already have. They say where to START,
    and if you already have a `search.local.yaml` that file is what gets
    edited. `forget settings` is the command that discards.

    Nothing leaves this machine. `config/*.local.yaml` is gitignored, and this
    command makes no network call and contacts no model.
    """
    from career_agent.config.setup import SetupError, base_config, run_setup

    try:
        _, started_from = base_config(config_dir)
    except SetupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    if answers_file is not None:
        answers = _answers_from_file(answers_file)
    else:
        typer.echo(f"starting from {started_from}")
        answers = _interactive_answers()

    if answers.is_empty():
        typer.secho(
            "\nNothing was answered, so nothing was written. Your settings are unchanged.",
            fg=typer.colors.YELLOW,
        )
        typer.echo("The shipped example is a working configuration; you can start from it.")
        return

    try:
        result = run_setup(config_dir, answers, worked_example=worked_example)
    except SetupError as exc:
        typer.secho(f"\n{exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc

    typer.secho("\nYour search is set up", bold=True)
    _table(
        [
            ("written to", result.path),
            ("started from", result.started_from),
            ("version", result.config_version),
            ("things you look for", len(result.signals_added)),
            ("things that rule a job out", len(result.blockers_added)),
            ("rules removed", len(result.blockers_removed)),
        ]
    )
    if result.changed_sections:
        typer.echo("\n  changed: " + ", ".join(dict.fromkeys(result.changed_sections)))
    elif result.started_from.endswith("local.yaml"):
        typer.echo("\n  nothing changed, so your version number stayed where it was")

    # Phrases that were already covered. Said out loud, because "nothing
    # happened" and "that is already being looked for" are different answers,
    # and only one of them stops a person typing it again next time.
    if result.signals_already_present:
        typer.echo("\nAlready covered, so not added a second time:")
        for phrase, entry_id in dict(result.signals_already_present).items():
            typer.echo(f"  {phrase}  ->  {entry_id}")

    typer.echo("\nThis file stays on your computer. It is gitignored and never uploaded.")
    typer.echo("\nNext:")
    typer.echo("  uv run career-agent rescore    # score what you already have, offline and free")
    typer.echo("  uv run career-agent serve      # open the interface")


# =====================================================================
# profile-export / profile-import
# =====================================================================
def profile_export_command(
    out: Annotated[Path, typer.Option("--out", help="Where to write the copy.")],
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Copy your search settings to a file you can keep or move.

    The file contains your preferences and may contain a pay target. It is a
    plain copy of `config/search.local.yaml`, so anything that reads one reads
    the other.

    Makes no network call.
    """
    from career_agent.config.setup import SetupError, export_profile

    try:
        written = export_profile(config_dir, out)
    except SetupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho("Exported", bold=True)
    _table([("written to", written), ("size", f"{written.stat().st_size} bytes")])
    typer.echo("\nIt holds what you are looking for and what you want to be paid.")
    typer.echo("Treat it the way you would treat that.")


def profile_import_command(
    source: Annotated[Path, typer.Option("--in", help="The file to import.")],
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    overwrite: Annotated[
        bool, typer.Option("--overwrite", help="Replace the settings already here.")
    ] = False,
) -> None:
    """Install a settings file as your own.

    Checked before it is installed: a file that does not load is refused, and
    your existing settings are left alone. Importing a broken profile over a
    working one would break the product with a file you did not write.

    Makes no network call.
    """
    from career_agent.config.setup import SetupError, import_profile

    try:
        written = import_profile(config_dir, source, overwrite=overwrite)
    except SetupError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from exc
    typer.secho("Imported", bold=True)
    _table([("written to", written)])
    typer.echo("\nRun `career-agent rescore` to score against these settings.")


# =====================================================================
# migrate-profile: the candidate's own answers, versioned
# =====================================================================
def migrate_profile_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Record the current state of your own answers, and say what is left.

    Three version numbers exist in this product and they answer three
    questions: the CODE moved, the CONFIGURATION moved, and -- from V1.4 --
    YOUR ANSWERS moved. The third was missing, so "my scores are stale because
    the matcher changed" and "my scores are stale because I said where I live"
    were the same number.

    This writes a content-hashed snapshot of the candidate-owned half of your
    settings into `search_profile_version`, which has existed since the first
    migration and held nothing. Running it twice with nothing changed records
    nothing and says so.

    IT MOVES NO FILE AND CHANGES NO SCORE. `search.local.yaml` is untouched,
    the matcher keeps reading exactly what it read before, and no rescore is
    needed. Repointing the matcher at a profile file is a separate decision
    with a real cost, and `docs/architecture/candidate-ownership.md` states
    what that cost is.

    Makes no network call.
    """
    from career_agent import profile_history
    from career_agent.config.ownership import OWNERSHIP_RULE, displaced, summary
    from career_agent.storage.db import connect, migrate, transaction

    if not db.exists():
        typer.secho(f"No database at {db}.", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    conn = connect(db)
    try:
        migrate(conn)
        before = profile_history.latest(conn)
        with transaction(conn):
            revision = profile_history.record(conn, config_dir)
    finally:
        conn.close()

    typer.secho("Your answers", bold=True)
    if revision.recorded and before is None:
        typer.echo("  recorded for the first time.")
    elif revision.recorded and before is not None:
        typer.echo("  changed, and recorded.")
        for field, (was, now) in profile_history.changed_between(before, revision).items():
            typer.echo(f"    {field}: {was!r} -> {now!r}")
    else:
        typer.echo("  unchanged since the last recording. Nothing was written.")

    _table(
        [
            ("revision", revision.number),
            ("recorded at", revision.created_at),
            ("digest", revision.content_hash[:16]),
            ("answers held", len(revision.answers)),
        ]
    )

    counts = summary()
    typer.echo("")
    typer.secho("What is left", bold=True)
    typer.echo(f"  {OWNERSHIP_RULE}")
    _table(
        [
            ("facts you own", counts["candidate_facts"]),
            ("of those, still in the search file", counts["candidate_facts_in_the_search_file"]),
            ("editable without opening a file", counts["editable_without_a_file"]),
        ]
    )
    for fact in displaced():
        mark = " " if fact.editable else "*"
        typer.echo(f"   {mark} {fact.subject}")
    typer.echo("")
    typer.echo("  * not editable from a screen: it is a phrase group the matcher reads")
    typer.echo("    against every posting, so changing it rescores everything and it has")
    typer.echo("    its own editor that says so.")
    typer.echo("")
    typer.echo("  Moving these is an owner decision and would invalidate every stored")
    typer.echo("  score. See docs/architecture/candidate-ownership.md.")


# =====================================================================
# forget: the destructive one
# =====================================================================
def forget_command(
    what: Annotated[
        str,
        typer.Argument(help="settings, tracking, or everything."),
    ],
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
) -> None:
    """Delete something of yours, on purpose and with a confirmation.

    `settings`   removes config/search.local.yaml. The shipped example takes
                 over, so the product keeps working.
    `tracking`   clears saved jobs, application statuses, dates and notes. The
                 postings themselves stay.
    `everything` both of the above.

    It always says exactly what it is about to remove, and how many rows, before
    it removes anything. Nothing here touches the postings you collected: those
    are a corpus, not personal data, and deleting a database is a thing you do
    with your own file manager rather than through a flag.
    """
    from career_agent.config.search_config import local_search_path

    choices = ("settings", "tracking", "everything")
    if what not in choices:
        typer.secho(
            f"unknown target {what!r}; choose one of {', '.join(choices)}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    doing_settings = what in ("settings", "everything")
    doing_tracking = what in ("tracking", "everything")

    plan: list[str] = []
    local = local_search_path(config_dir)
    if doing_settings:
        plan.append(
            f"delete {local} (it exists)" if local.exists() else f"nothing at {local} to delete"
        )

    conn = None
    counts = {"saved": 0, "tracked": 0, "notes": 0, "events": 0}
    if doing_tracking:
        path = resolve_database(RuntimeMode.PERSONAL, db)
        conn = _open_personal(path)
        counts["saved"] = conn.execute(
            "SELECT COUNT(*) FROM job_application WHERE saved = 1"
        ).fetchone()[0]
        counts["tracked"] = conn.execute(
            "SELECT COUNT(*) FROM job_application"
            " WHERE status IS NOT NULL AND status != 'DISCOVERED'"
        ).fetchone()[0]
        counts["notes"] = conn.execute(
            "SELECT COUNT(*) FROM job_application WHERE notes IS NOT NULL AND notes != ''"
        ).fetchone()[0]
        counts["events"] = conn.execute("SELECT COUNT(*) FROM job_application_event").fetchone()[0]
        plan.append(
            f"clear tracking in {path}: {counts['saved']} saved, {counts['tracked']} tracked, "
            f"{counts['notes']} with notes, {counts['events']} history entries"
        )

    typer.secho("This will:", bold=True)
    for line in plan:
        typer.echo(f"  {line}")
    typer.echo("\nThe job postings themselves are not touched.")

    if not yes:
        typer.echo("")
        confirmed = typer.confirm("Go ahead?", default=False)
        if not confirmed:
            if conn is not None:
                conn.close()
            typer.secho("Nothing was removed.", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)

    removed: list[tuple[str, Any]] = []
    if doing_settings and local.exists():
        local.unlink()
        removed.append(("settings file", local))
    if conn is not None:
        with transaction(conn):
            conn.execute("DELETE FROM job_application_event")
            conn.execute("DELETE FROM job_application")
        removed.append(("tracking rows cleared", counts["saved"] + counts["tracked"]))
        conn.close()

    typer.secho("\nDone", bold=True)
    if removed:
        _table(removed)
    else:
        typer.echo("  there was nothing to remove")
    if doing_settings:
        typer.echo("\nThe shipped example is in force again. `career-agent setup` starts over.")


# =====================================================================
# jooble-probe
# =====================================================================
def jooble_probe_command(
    keywords: Annotated[str, typer.Option("--keywords", help="What to search for.")],
    location: Annotated[str, typer.Option("--location", help="Where. Required by Jooble.")],
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    budget: Annotated[
        int,
        typer.Option("--budget", help="Maximum live requests this run may spend."),
    ] = 1,
    execute: Annotated[
        bool,
        typer.Option("--execute", help="Actually send the request. Default is a dry run."),
    ] = False,
) -> None:
    """Ask Jooble one question and report what came back. Spends live quota.

    Jooble documents a LIFETIME limit of 500 requests per key -- not per month,
    not per day. So this command is a preflight by default: without `--execute`
    it resolves the configuration, prints the ledger and sends nothing, which is
    enough to discover that the domain is unset without paying to learn it.

    `--budget` is the ceiling for THIS run and defaults to one. One answered
    question proves the contract; a second proves nothing new at the same price.

    Nothing here prints the API key. The endpoint is reported with the key
    replaced by a placeholder, which is also the only form that reaches an
    error, a log line or a cache filename.
    """
    import os

    from dotenv import load_dotenv

    from career_agent.net.fetcher import FetchError, HttpFetcher
    from career_agent.providers.jooble import (
        DOMAIN_ENV,
        KEY_ENV,
        JoobleConfigurationError,
        JoobleProvider,
        Query,
        origin_hint,
    )
    from career_agent.providers.jooble_quota import QuotaExhausted, ledger_for

    load_dotenv()
    ledger = ledger_for(db)
    usage = ledger.read()

    typer.secho("Jooble probe", bold=True)
    _table(
        [
            ("query", f"{keywords!r} in {location!r}"),
            (KEY_ENV, "PRESENT" if os.environ.get(KEY_ENV) else "MISSING"),
            (DOMAIN_ENV, os.environ.get(DOMAIN_ENV) or "NOT SET"),
            ("requests made here", usage.known_local_requests),
            ("at most remaining", usage.known_remaining_upper_bound),
            ("this run may spend", budget),
        ]
    )
    typer.echo(f"  {usage.sentence}")

    try:
        with HttpFetcher() as fetcher:
            provider = JoobleProvider(
                fetcher,
                domain=os.environ.get(DOMAIN_ENV, ""),
                api_key=os.environ.get(KEY_ENV, ""),
                ledger=ledger,
            )
            typer.echo(f"  endpoint : {provider.safe_url}")

            if not execute:
                typer.secho(
                    "DRY RUN. Nothing was sent. Add --execute to spend one request.",
                    fg=typer.colors.YELLOW,
                )
                return

            page = provider.search(Query(keywords, location), budget=budget)
    except JoobleConfigurationError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except QuotaExhausted as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc
    except FetchError as exc:
        # The recorded URL is the redacted one. A 403 here most often means the
        # key belongs to a different Jooble domain than the one configured.
        typer.secho(f"request failed: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    stubs = [s for s in (provider.to_stub(row) for row in page.jobs) if s is not None]
    lengths = [len(str(row.get("snippet") or "")) for row in page.jobs]
    typer.secho("Answered", bold=True)
    _table(
        [
            ("total claimed", page.total_count),
            ("rows returned", len(page.jobs)),
            ("addressable", len(stubs)),
            ("truncated", page.truncated),
            ("description chars", f"min {min(lengths, default=0)} max {max(lengths, default=0)}"),
            ("origin boards named", ", ".join(sorted({origin_hint(r) or "-" for r in page.jobs}))),
            ("dates present", sum(1 for s in stubs if s.posted_at)),
            ("locations present", sum(1 for s in stubs if s.location_raw)),
        ]
    )
    after = ledger.read()
    typer.echo(f"  {after.sentence}")
    typer.secho(
        "  This is ONE query's answer. It is not coverage of a market.",
        fg=typer.colors.YELLOW,
    )


# =====================================================================
# daily
# =====================================================================
def daily_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    days: Annotated[int, typer.Option("--days", help="How far back 'recent' reaches.")] = 7,
    show: Annotated[int, typer.Option("--show", help="Rows per section.")] = 8,
) -> None:
    """What is actually worth looking at today.

    Every number here is one the product already computed. There is no daily
    ranking, no freshness weighting and no recommendation engine: the order is
    the same deterministic match score the cards use, and the sections are
    filters over it. A digest that invented its own ordering would be a second
    opinion nobody could trace, which is the whole thing ADR-0004 refuses.

    **Dates say what they mean.** A posting's date is `posted_at` when the
    board published one and `first_seen_at` otherwise, and the two are never
    mixed into one word: `first_seen_at` is when WE noticed it, which for a
    board that publishes no dates is the only thing anybody knows and is not a
    claim about when the employer wrote it. The header says which population
    each count came from.

    Makes no network call and no inference call of either kind.
    """
    from career_agent.digest import default_filter, sections
    from career_agent.storage.mvp_repo import ScoredJobQuery
    from career_agent.storage.workspace_repo import (
        LAST_REVIEWED_AT,
        CandidateStateRepo,
        candidate_id_of,
    )

    config, config_path = _load_config(config_dir)
    conn = connect(db)
    try:
        repo = ScoredJobQuery(conn)
        identity = (config.config_id, config.config_version)
        default = default_filter(show)
        total = repo.count(*identity, default)

        # Where the reader got to, if she has ever said. `daily` does not WRITE
        # the checkpoint: a digest that marked itself read every time it was
        # printed would make "since you last looked" mean "since the last time
        # this command ran", which is not a fact about anybody.
        candidate_id = candidate_id_of(conn)
        seen_at = (
            CandidateStateRepo(conn).get(candidate_id, LAST_REVIEWED_AT) if candidate_id else None
        )

        typer.secho(f"Today, from {db}", bold=True)
        _table(
            [
                ("settings", f"{config_path.name} v{config.config_version}"),
                ("roles worth looking at", total),
                (
                    "set aside: rules you out",
                    repo.hidden_by_eligibility(*identity, default, narrow_total=total),
                ),
                (
                    "set aside: different work",
                    repo.hidden_by_screening(*identity, default, narrow_total=total),
                ),
            ]
        )

        for section in sections(config, days=days, show=show, last_reviewed_at=seen_at):
            _daily_section(repo, identity, section.title, section.lead, section.job_filter, show)
    finally:
        conn.close()

    typer.echo("")
    typer.secho("no network calls and no inference of either kind", fg=typer.colors.GREEN)


def _daily_section(repo, identity, title: str, lead: str, job_filter, show: int) -> None:
    """One section, or a sentence saying it is empty.

    An empty section prints its heading and says so rather than vanishing. A
    digest that silently omits "nothing new today" is indistinguishable from a
    digest that failed to look.
    """
    rows = repo.page(*identity, job_filter)
    typer.echo("")
    typer.secho(f"{title} ({len(rows)})", bold=True)
    typer.echo(f"  {lead}")
    if not rows:
        typer.echo("  nothing here today")
        return

    for job in rows[:show]:
        result = job.result
        score = result.match_score if result else None
        level = result.seniority if result else None
        # The date, and which KIND of date it is. Never one word for both.
        when = (
            f"posted {job.posted_at[:10]}"
            if job.posted_at
            else f"first seen {job.first_seen_at[:10]}"
            if job.first_seen_at
            else "no date"
        )
        typer.echo(
            f"  {(score if score is not None else '--'):>3}  "
            f"{(job.company_name or '?')[:22]:<22} {job.title[:44]:<44} "
            f"{when}"
        )
        if level is not None and not level.is_evidence:
            typer.echo("       level not stated by the posting")
        # Only where nobody has resolved eligibility, which is where it changes
        # what you would ASK. Beside a posting whose scope the employer already
        # stated it would be noise, and beside one that rules her out it would
        # be a second, weaker reason on top of a real one.
        domestic = result.domestic if result else None
        if (
            domestic is not None
            and domestic.context is DomesticContext.LIKELY_US_DOMESTIC
            and result is not None
            and result.eligibility_status is EligibilityStatus.UNRESOLVED
        ):
            typer.echo(
                f"       probably a United States job: it offers {domestic.signal}"
                " and does not mention hiring elsewhere"
            )


# =====================================================================
# collect-getonbrd
# =====================================================================
def collect_getonbrd_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    category: Annotated[
        list[str] | None,
        typer.Option("--category", help="Which category to read. Repeatable."),
    ] = None,
    max_pages: Annotated[
        int | None,
        typer.Option("--max-pages", help="Pages per category. Default 3, cap 50."),
    ] = None,
) -> None:
    """Read the Get on Board category feeds and store what is new.

    LATAM technology hiring, and the first source here whose market is the one
    this product exists to serve. Makes no inference call of either kind.

    **Bounded by default**: three pages per category, and the walk says whether
    it reached the end of the feed or stopped at that budget. Those are
    different outcomes and this command prints which one happened, because a
    run that read three pages of forty must never report like a complete one.

    **The categories default to the WORK, not to `programming`.** This board
    splits leadership, data and machine-learning roles out of that category, so
    reading it alone misses most of what an operations and automation candidate
    does.

    **No description.** The feed carries a title, a company, a country list, a
    remote flag and a date, and no advert body. Every posting stored here is
    METADATA_ONLY and the interface says so; it is a lead to open, not a
    posting to score as though it had been read.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.getonbrd_collect import GetonbrdCollector
    from career_agent.providers.getonbrd import CategoryError, resolve_categories

    try:
        chosen = resolve_categories(list(category) if category else None)
    except CategoryError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = GetonbrdCollector(conn, fetcher, max_pages=max_pages).collect(categories=chosen)
    finally:
        conn.close()

    typer.secho("Get on Board", bold=True)
    _table(
        [
            ("categories asked", ", ".join(chosen)),
            ("categories read", stats.categories_read),
            ("categories empty", stats.categories_empty),
            ("categories failed", stats.categories_failed),
            ("stopped at the page budget", stats.categories_page_limited),
            ("feed stopped short", stats.categories_truncated),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the payload", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  {failure}", fg=typer.colors.YELLOW)

    if stats.complete:
        typer.secho(
            "  Every category was read to the end of its feed.",
            fg=typer.colors.GREEN,
        )
    else:
        typer.secho(
            "  This did NOT read every category to the end. Raise --max-pages, or read "
            "the counts above: a page budget and a feed that stopped short are "
            "different problems.",
            fg=typer.colors.YELLOW,
        )
    typer.secho(
        "  These postings carry no description. They are leads to open, not "
        "postings that have been read.",
        fg=typer.colors.YELLOW,
    )
    typer.secho(
        "  A category is not a market. This is a bounded read of one LATAM board.",
        fg=typer.colors.YELLOW,
    )
    typer.secho("  zero inference calls of either kind", fg=typer.colors.GREEN)


# =====================================================================
# collect-wwr
# =====================================================================
def collect_workingnomads_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
) -> None:
    """Read the Working Nomads window and store what is new.

    ONE request, because there is no pagination: the feed is a window of the
    most recent postings with no archive behind it. Collecting repeatedly
    accumulates over time and never completes, so nothing here reports
    coverage of remote hiring.

    Every posting is remote and arrives with its full description. Its
    `location` string is stored and SHOWN and is never read as a hiring scope
    -- see `providers/workingnomads.py` for why. Read-only, sends nothing
    about you, and makes no inference call of either kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.workingnomads_collect import WorkingNomadsCollector

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = WorkingNomadsCollector(conn, fetcher).collect()
    finally:
        conn.close()

    typer.secho("Working Nomads", bold=True)
    _table(
        [
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", stats.duplicates_total),
            ("which rules matched", stats.duplicates or "none"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    typer.echo("\nThis is a window of recent postings, never a whole board.")
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_himalayas_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Pages of 20 to read. Default 2.")
    ] = 2,
    search: Annotated[
        bool,
        typer.Option(
            "--search/--no-search",
            help="Also search by your role anchors and work intent in your markets.",
        ),
    ] = True,
    max_queries: Annotated[
        int, typer.Option("--max-queries", help="At most this many searches. Default 24.")
    ] = 24,
    search_pages: Annotated[
        int, typer.Option("--search-pages", help="Pages of 20 per search. Default 1.")
    ] = 1,
) -> None:
    """Read the Himalayas jobs feed and store what is new.

    Bounded by default: two pages of twenty. The feed states a total above a
    hundred thousand, so nothing this prints is coverage of remote hiring, and
    the stored run stats say so in words.

    Every posting arrives with its full description AND with
    `locationRestrictions` -- the employer's own answer to where it may hire,
    which is the reason this source is worth having. Read-only, sends nothing
    about you, and makes no inference call of either kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.himalayas_collect import HimalayasCollector

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")
    if not 0 <= max_queries <= 60 or not 1 <= search_pages <= 5:
        raise typer.BadParameter("--max-queries is 0 to 60 and --search-pages 1 to 5")

    searches: tuple[Any, ...] = ()
    if search and max_queries:
        # The TARGETED lane. What leaves the machine is the search words (a
        # role you named, an alias of it, or one of your work phrases) and a
        # country code: never a CV, evidence or anything else about you.
        from career_agent.config.search_config import SearchConfigError, load_search_config
        from career_agent.discovery.plan import targeted_plan

        try:
            config, _ = load_search_config(config_dir)
            searches = targeted_plan(
                config,
                config_dir,
                max_queries=max_queries,
                # Himalayas' country filter already includes worldwide-friendly
                # postings, so the country scopes cover the region ones here.
                scope_filter=lambda scope: scope.country is not None,
            )
        except SearchConfigError:
            searches = ()

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = HimalayasCollector(conn, fetcher, max_pages=max_pages).collect(
                searches=searches, search_pages=search_pages
            )
    finally:
        conn.close()

    typer.secho("Himalayas", bold=True)
    _table(
        [
            ("pages read", stats.pages_read),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            ("feed says it holds", stats.claimed_total if stats.claimed_total is not None else "?"),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("states where it may hire", stats.with_hiring_scope),
            ("states nothing about that", stats.without_hiring_scope),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            (
                "searches planned / ok / failed",
                f"{stats.queries_planned} / {stats.queries_succeeded} / {stats.queries_failed}",
            ),
            ("searches rate-limited", stats.queries_rate_limited),
            ("search results / unique", f"{stats.search_results} / {stats.search_unique}"),
            ("unique by scope", stats.unique_by_scope or "-"),
            ("unique by term origin", stats.unique_by_origin or "-"),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.stopped_early:
        typer.echo("\nThis was a bounded walk, not the whole feed. Raise --max-pages to read more.")
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


LINKEDIN_SOURCE_ID = "linkedin_br"


def experimental_source_command(
    source_id: Annotated[str, typer.Argument(help="The catalogue row, e.g. linkedin_br.")],
    on: Annotated[bool, typer.Option("--on/--off", help="Switch the override on or off.")],
    understood: Annotated[
        bool,
        typer.Option(
            "--i-understand",
            help="Required to switch on: you read what the source says about automated access.",
        ),
    ] = False,
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
) -> None:
    """Switch a forbidden source's LOCAL EXPERIMENTAL override on or off.

    The source's own permission does not change: LinkedIn still forbids
    automated access, and this records only that YOU chose to run the
    experimental adapter for this profile. Off by default; never shipped on.
    """
    from career_agent.sources.catalogue import resolve
    from career_agent.sources.experimental import set_opt_in
    from career_agent.storage.db import transaction

    rows = {r.id: r for r in resolve(path=config_dir / "source_catalogue.yaml")}
    row = rows.get(source_id)
    if row is None or not row.experimental_provider:
        raise typer.BadParameter(f"{source_id} has no experimental option")
    if on and not understood:
        typer.secho(
            f"{row.name}: {row.reason_plain or row.reason}\n"
            "This adapter is experimental and off by default. The site may rate-limit or block "
            "it, results can be partial, and it never logs in or asks for a password. "
            "Pass --i-understand to switch it on for this profile.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)
    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        with transaction(conn):
            value = set_opt_in(conn, source_id, on)
    finally:
        conn.close()
    typer.echo(f"{source_id}: experimental override {'ON' if value['opted_in'] else 'OFF'}")


def collect_linkedin_command(
    db: Annotated[Path | None, typer.Option("--db")] = None,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    max_queries: Annotated[
        int, typer.Option("--max-queries", help="At most this many searches. Default 24.")
    ] = 24,
    results_per_query: Annotated[
        int, typer.Option("--results", help="Result cards per search, 1 to 50. Default 25.")
    ] = 25,
    hours_old: Annotated[
        int, typer.Option("--hours-old", help="Only postings this recent. Default 336 (14 days).")
    ] = 24 * 14,
    max_enrich: Annotated[
        int, typer.Option("--max-enrich", help="Posting pages to read, 0 to 200. Default 60.")
    ] = 60,
    plan_only: Annotated[
        bool, typer.Option("--plan", help="Print how many searches would run, and stop.")
    ] = False,
) -> None:
    """EXPERIMENTAL: search LinkedIn through python-jobspy, for an opted-in profile.

    LinkedIn forbids automated access (see `career-agent sources`); this runs
    only if this profile switched the experimental override on
    (`career-agent experimental-source linkedin_br --on --i-understand`, or
    Settings & Sources). It never logs in. Bounded and paced; a refusal from
    LinkedIn stops it and is reported as such, never as "no jobs".
    """
    from career_agent.config.search_config import SearchConfigError, load_search_config
    from career_agent.discovery.plan import targeted_plan
    from career_agent.pipeline.linkedin_collect import LinkedInCollector
    from career_agent.providers.linkedin_jobspy import available
    from career_agent.sources.experimental import opted_in

    if not available():
        typer.secho(
            "python-jobspy is not installed for this Python, so the experimental LinkedIn "
            "source is unavailable here (it needs Python 3.12, the launcher's version).",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=2)

    if not 0 <= max_queries <= 60 or not 1 <= results_per_query <= 50:
        raise typer.BadParameter("--max-queries is 0 to 60 and --results 1 to 50")
    if not 1 <= hours_old <= 24 * 60 or not 0 <= max_enrich <= 200:
        raise typer.BadParameter("--hours-old is 1 to 1440 and --max-enrich 0 to 200")
    try:
        config, _ = load_search_config(config_dir)
    except SearchConfigError as exc:
        raise typer.BadParameter("describe the work you want first (Settings)") from exc
    from career_agent.pipeline.linkedin_collect import expressible

    queries = targeted_plan(config, config_dir, max_queries=max_queries, scope_filter=expressible)
    if plan_only:
        from collections import Counter

        typer.echo(f"{len(queries)} searches")
        typer.echo(f"by scope: {dict(Counter(q.scope.key for q in queries))}")
        typer.echo(f"by term origin: {dict(Counter(q.term.origin for q in queries))}")
        return
    db = resolve_database(RuntimeMode.PERSONAL, db)
    conn = _open_personal(db)
    try:
        if not opted_in(conn, LINKEDIN_SOURCE_ID):
            typer.secho(
                "LinkedIn is not switched on for this profile. It is an experimental, "
                "opt-in source: see Settings & Sources.",
                fg=typer.colors.YELLOW,
            )
            raise typer.Exit(code=2)
        stats = LinkedInCollector(conn).collect(
            queries,
            results_per_query=results_per_query,
            hours_old=hours_old,
            max_enrich=max_enrich,
            # Checked before every search and page: switching LinkedIn off in
            # Settings stops a run already in progress.
            should_stop=lambda: not opted_in(conn, LINKEDIN_SOURCE_ID),
        )
    finally:
        conn.close()
    typer.secho("LinkedIn (experimental)", bold=True)
    _table([(k.replace("_", " "), v) for k, v in stats.as_dict().items() if k != "failures"])
    for failure in stats.failures:
        typer.secho(f"  {failure}", fg=typer.colors.RED)
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_avlis_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
) -> None:
    """Read the Avlis Talent board. Brazilians at United States startups.

    One agency, one feed, one request. There is no pagination to bound and no
    per-posting page: every posting links to the board itself, measured rather
    than assumed.

    It was measured and DECLINED earlier the same day, on volume -- six
    postings against a corpus of a hundred thousand. That refusal was a
    judgement about whether the work is worth SHOWING, which ADR-0019 puts in
    the filter layer rather than in a collector.

    Read-only, sends nothing about you, and makes no inference call of either
    kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.avlis_collect import AvlisCollector

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = AvlisCollector(conn, fetcher).collect()
    finally:
        conn.close()

    typer.secho("Avlis Talent", bold=True)
    _table(
        [
            ("postings seen", stats.postings_seen),
            ("closed by the agency", stats.postings_closed_by_source),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("not addressable", stats.postings_unaddressable),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    typer.echo(
        "\nEvery posting here links to the board page: this board has no per-posting URL, "
        "and inventing one would send somebody to a page that does not read it."
    )
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_workable_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Deprecated: XML downloads the whole feed.")
    ] = 25,
    xml_file: Annotated[
        Path | None, typer.Option("--xml-file", help="Replay an archived public XML download.")
    ] = None,
    day_range: Annotated[
        int | None,
        typer.Option("--day-range", help="Only postings created in the last N days."),
    ] = None,
    query: Annotated[
        str | None,
        typer.Option("--query", help="Narrow by text. NOT the default, and recorded when used."),
    ] = None,
) -> None:
    """Download Jobs by Workable XML, then parse and filter locally.

    The whole feed is downloaded. Apply URLs retain vendor attribution.
    --max-pages is a deprecated compatibility option; XML has no pages.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.workable_collect import WorkableCollector

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")
    if day_range is not None and day_range < 1:
        raise typer.BadParameter("--day-range must be at least 1")

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = WorkableCollector(
                conn,
                fetcher,
                max_pages=max_pages,
                query=query,
                day_range=day_range,
                xml_path=xml_file,
            ).collect()
    finally:
        conn.close()

    typer.secho("Workable", bold=True)
    _table(
        [
            ("XML complete records", stats.xml_jobs),
            ("XML bytes", stats.xml_bytes),
            ("locally filtered", stats.filtered_out),
            ("repeated XML references", stats.feed_duplicates),
            ("employer identities repaired", stats.employer_identities_corrected),
            ("persistence batches", stats.pages_read),
            ("the index says it holds", stats.claimed_total or "not stated"),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            ("query", stats.query or "none, which is the default"),
            ("day range", stats.day_range or "none"),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("says where the work is", stats.with_location),
            ("says nothing about that", stats.without_location),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.repeated_itself:
        # NOT a bound and NOT a failure. The feed served a page holding
        # nothing new, which is what a page token that did not take looks like
        # from here: HTTP 200, twenty good postings, no progress.
        typer.secho(
            "\nThe feed stopped advancing: a page arrived holding no posting this walk did "
            "not already have. Nothing failed and nothing was lost; the walk stopped rather "
            "than spend its budget re-reading one page.",
            fg=typer.colors.YELLOW,
        )
    if stats.stopped_early:
        typer.echo(
            "\nThis was a bounded walk of a very large index. Raise --max-pages to read more, "
            "and run it again tomorrow with --day-range 1 for what is new."
        )
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_arbeitnow_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_pages: Annotated[int, typer.Option("--max-pages", help="Pages of 250. Default 3.")] = 3,
) -> None:
    """Read the Arbeitnow feed and store what is new. Europe, mostly on-site.

    Evaluated and deliberately not built in V1.5, for FIT rather than
    permission: 11 of 250 rows were remote and the rest were offices in London,
    Munich, Paris and Berlin. That reasoning belonged to a filter rather than a
    collector, and the corpus is not filtered to one person's preferences.

    The vendor states its terms in every response and asks for a link back.
    Apply opens the Arbeitnow posting URL and nothing else, and the terms
    served on the run are stored with it.

    Read-only, sends nothing about you, and makes no inference call of either
    kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.arbeitnow_collect import ArbeitnowCollector

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = ArbeitnowCollector(conn, fetcher, max_pages=max_pages).collect()
    finally:
        conn.close()

    typer.secho("Arbeitnow", bold=True)
    _table(
        [
            ("pages read", stats.pages_read),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("says where the work is", stats.with_location),
            ("says nothing about that", stats.without_location),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.stopped_early:
        typer.echo("\nThis was a bounded walk, not the whole feed. Raise --max-pages to read more.")
    typer.echo(
        "\nArbeitnow is credited on every posting and Apply opens its own URL, "
        "which is what its stated terms ask for."
    )
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_remoteok_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
) -> None:
    """Read the Remote OK window and store what is new. International, remote.

    One request returns what the board is showing now. There is no paging
    parameter, so this accumulates over time and never yields a complete board.

    THE FIRST ELEMENT OF THE RESPONSE IS THE LICENCE, not a job, and it is a
    grant with three conditions: link back to the Remote OK URL, credit Remote
    OK, do not use its logo. All three are structural here rather than
    promised: `job.url` is the only Apply target this adapter produces, the
    posting is stored under Remote OK as its source, and the logo fields are
    archived and read by nothing. The licence text served on the run is stored
    with it.

    Read-only, sends nothing about you, and makes no inference call of either
    kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.remoteok_collect import RemoteOkCollector

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = RemoteOkCollector(conn, fetcher).collect()
    finally:
        conn.close()

    typer.secho("Remote OK", bold=True)
    _table(
        [
            ("elements the feed served", stats.elements_served),
            ("postings in the window", stats.claimed_total or 0),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("says where the work is", stats.with_location),
            ("says nothing about that", stats.without_location),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    typer.echo(
        "\nA window of what the board shows now, not coverage of remote hiring: "
        "this feed has no paging and no archive."
    )
    typer.echo(
        "\nRemote OK is credited on every posting and Apply opens its own URL, "
        "which is what its API terms ask for."
    )
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_programathor_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Listing pages of 15. Default 2.")
    ] = 2,
) -> None:
    """Read the Programathor listing and store what is new. Brazil, developers.

    Bounded by default, and the bound matters more here than anywhere else:
    the listing carries no descriptions, so each posting costs its own request.
    Two pages is 2 listing requests plus up to 30 posting requests.

    Roughly half of this vendor's posting pages answer HTTP 500. That is
    measured and deterministic rather than a rate limit, so it is counted and
    reported rather than treated as a failure of the run.

    What buys the request cost back is what the postings carry: a salary with a
    currency AND a period, and an employment type that on this board
    distinguishes PJ from CLT-shaped work. Read-only, sends nothing about you,
    and makes no inference call of either kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.programathor_collect import ProgramathorCollector

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = ProgramathorCollector(conn, fetcher, max_pages=max_pages).collect()
    finally:
        conn.close()

    typer.secho("Programathor", bold=True)
    _table(
        [
            ("listing pages read", stats.pages_read),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            ("postings listed", stats.postings_listed),
            ("the vendor would not serve", stats.postings_unavailable),
            ("postings read", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("states where the work is", stats.with_location),
            ("states pay, with a period", stats.with_salary),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.postings_listed:
        served = stats.postings_seen
        typer.echo(
            f"\n{served} of {stats.postings_listed} listed postings were served. "
            "The rest answered 500, which is this board's ordinary rate and not a fault here."
        )
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_gupy_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Pages of 100, PER partition. Default 2.")
    ] = 2,
    workplace: Annotated[
        str,
        typer.Option(
            "--workplace",
            help="Comma-separated: remote, hybrid, on-site. Default is all three.",
        ),
    ] = "remote,hybrid,on-site",
) -> None:
    """Read the Gupy candidate portal and store what is new. Brazil.

    Bounded by default: two pages of a hundred, per partition. The feed holds
    more than eighty thousand postings and the vendor caps `offset` at 10,000,
    so nothing this prints is coverage of Brazilian hiring, and the stored run
    stats say so in words.

    The feed is cut into slices small enough for a walk to reach the end of:
    by the employer's own `workplaceType`, then by contract type, then by
    state, then by city. Each cut is MEASURED against the live feed rather than
    configured, and a slice the planner cannot cut small enough is reported by
    name rather than rounded away.

    All three workplace types by default, including on-site and including PJ
    contracts. What a candidate wants to see is a question for her filters; a
    corpus filtered to one person's preferences is useless to the next one.

    Every posting arrives with its full description already in the listing, so
    there is no per-posting request at all. Read-only, sends nothing about you,
    and makes no inference call of either kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.gupy_collect import GupyCollector
    from career_agent.providers.gupy import WORKPLACE_TYPES

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")

    chosen = tuple(part.strip() for part in workplace.split(",") if part.strip())
    unknown = [part for part in chosen if part not in WORKPLACE_TYPES]
    if not chosen or unknown:
        raise typer.BadParameter(
            f"--workplace takes any of {', '.join(WORKPLACE_TYPES)}; "
            f"did not recognise {', '.join(unknown) or 'an empty list'}"
        )

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = GupyCollector(
                conn, fetcher, max_pages=max_pages, workplace_types=chosen
            ).collect()
    finally:
        conn.close()

    typer.secho("Gupy", bold=True)
    rows: list[tuple[str, object]] = [
        ("partitions read", ", ".join(stats.partitions)),
        ("slices planned", stats.slices_planned),
        (
            "slices too big to finish",
            f"{len(stats.slices_over_ceiling)}"
            + (
                f" ({', '.join(stats.slices_over_ceiling[:3])}...)"
                if stats.slices_over_ceiling
                else ""
            ),
        ),
        ("postings seen in two slices", stats.slice_overlap),
    ]
    for partition in stats.partitions:
        held = stats.partition_totals.get(partition)
        rows.append(
            (
                f"  {partition}",
                f"{stats.partition_read.get(partition, 0)} read"
                f" of {held if held is not None else '?'} it says it holds",
            )
        )
    _table(
        rows
        + [
            ("pages read", stats.pages_read),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            (
                "stopped at the vendor's ceiling",
                ", ".join(stats.ceiling_hit) if stats.ceiling_hit else "no",
            ),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("boards new", stats.boards_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("states where the work is", stats.with_location),
            ("states nothing about that", stats.without_location),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.ceiling_hit:
        typer.echo(
            "\nThe vendor serves nothing past offset 10,000. Those partitions were cut "
            "off by Gupy, not by --max-pages."
        )
    elif stats.stopped_early:
        typer.echo("\nThis was a bounded walk, not the whole feed. Raise --max-pages to read more.")
    typer.echo("\nrun `career-agent rescore` to score the new postings offline")


def collect_jobicy_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    geo: Annotated[
        str,
        typer.Option("--geo", help="Which region to ask for. Default latam."),
    ] = "latam",
    count: Annotated[
        int, typer.Option("--count", help="Rows to ask for, 1-200. Default 200.")
    ] = 200,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Poll even if the last one was under an hour ago. Read the licence first.",
        ),
    ] = False,
) -> None:
    """Read one bounded window of the Jobicy jobs API and store what is new.

    ONE REQUEST, AT MOST ONCE AN HOUR. That cadence is a condition of the
    first-party grant this source is read under, not a politeness: Jobicy
    permits use of its listings in other products without asking, and asks for
    credit, a direct link, and no more than one poll per hour. The gate reads
    the last ATTEMPT out of `pipeline_run`, so it survives a restart, and a
    refusal is printed as a refusal rather than as a failure.

    There is no paging. The window is the latest 200 rows matching the query,
    nothing can reach past them, and a posting missing from it has NOT been
    shown to have closed.

    Every posting arrives with its full description AND with `jobGeo` -- the
    employer's own answer to where applicants may live, which is the reason
    this source is worth having. `Anywhere` is the vendor's DEFAULT and is
    stored as nothing stated, never as a worldwide invitation.

    Read-only, sends nothing about you, and makes no inference call of either
    kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.jobicy_collect import JobicyCollector, describe_cooldown

    if not 1 <= count <= 200:
        raise typer.BadParameter("--count must be between 1 and 200")

    conn = connect(db)
    try:
        migrate(conn)
        collector_geo = geo.strip() or None
        with HttpFetcher() as fetcher:
            collector = JobicyCollector(conn, fetcher, geo=collector_geo, count=count)
            stats = collector.collect(force=force)
    finally:
        conn.close()

    typer.secho("Jobicy", bold=True)
    if stats.cooling_down_until is not None:
        # Not an error, and the exit code says so. Nothing was fetched, nothing
        # failed, and the number is printed because a refusal with no number is
        # the kind people work around.
        typer.secho(describe_cooldown(stats.cooling_down_until), fg=typer.colors.YELLOW)
        return

    _table(
        [
            ("window filled", "yes" if stats.window_full else "no"),
            (
                "rows the envelope claimed",
                stats.claimed_count if stats.claimed_count is not None else "?",
            ),
            ("the question asked", stats.applied_filters or {}),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("states where it may hire", stats.with_hiring_scope),
            ("states nothing about that", stats.without_hiring_scope),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.window_full:
        typer.echo(
            "\nThe window filled, which means a LIMIT was reached and not a total. "
            "There is no next page: postings older than these are unreachable here."
        )
    typer.echo("\nJobicy is credited on every posting, and Apply opens its source URL.")
    typer.echo("run `career-agent rescore` to score the new postings offline")


def collect_remotive_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Poll even if the last one was under six hours ago. Read the licence first.",
        ),
    ] = False,
) -> None:
    """Read the Remotive public jobs window once and store what is new.

    ONE REQUEST, AT MOST FOUR TIMES A DAY. Remotive's own API page says access
    "is granted so that developers can share our jobs further", asks for a
    link back and a mention as the source, and advises at most four polls a
    day. All three are implemented: Apply opens the Remotive URL, the source is
    credited, and the gate reads the last ATTEMPT out of `pipeline_run`.

    The public window is small and is the whole public inventory: 18 postings
    when measured, every one with a whole advert and with
    `candidate_required_location`, the employer's answer to where applicants
    may live. Read-only, sends nothing about you, no inference call.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.remotive_collect import RemotiveCollector, describe_cooldown

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            collector = RemotiveCollector(conn, fetcher)
            stats = collector.collect(force=force)
    finally:
        conn.close()

    typer.secho("Remotive", bold=True)
    if stats.cooling_down_until is not None:
        typer.secho(describe_cooldown(stats.cooling_down_until), fg=typer.colors.YELLOW)
        return

    _table(
        [
            (
                "rows the envelope claimed",
                stats.claimed_count if stats.claimed_count is not None else "?",
            ),
            (
                "public inventory the vendor reports",
                stats.claimed_total if stats.claimed_total is not None else "?",
            ),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("states where it may hire", stats.with_hiring_scope),
            ("states nothing about that", stats.without_hiring_scope),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    typer.echo("")
    typer.echo("Remotive is credited on every posting, and Apply opens its source URL.")
    typer.echo("run `career-agent rescore` to score the new postings offline")


def collect_jobgether_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = DEFAULT_CONFIG_DIR,
    locations: Annotated[
        str | None,
        typer.Option(
            "--locations",
            help=(
                "Comma-separated vendor location slugs to put FIRST. The whole universe "
                "is always walked over successive runs; this only decides the order. "
                "Default: your own markets first, from your search's countries."
            ),
        ),
    ] = None,
    only: Annotated[
        str | None,
        typer.Option(
            "--only",
            help=(
                "Restrict the universe to these location slugs. A canary tool: a "
                "restricted universe is not coverage, and the slice ledger says so."
            ),
        ),
    ] = None,
    max_requests: Annotated[
        int,
        typer.Option("--max-requests", help="Requests this run may make. Default 400."),
    ] = 400,
    no_cuts: Annotated[
        bool,
        typer.Option(
            "--no-cuts",
            help="One slice per location, uncut by contract type and experience band.",
        ),
    ] = False,
) -> None:
    """Walk Jobgether's job-search API for agents, one bounded question at a time.

    THE GRANT IS SPECIFIC AND THIS STAYS INSIDE IT. Jobgether's terms forbid
    automated extraction from its pages; its robots.txt and its API docs
    publish one endpoint for AI agents answering job-search questions, capped
    at 250 rows a query, at a crawl delay of two seconds. This command asks
    that endpoint geographic questions -- a location, cut by the vendor's own
    contract-type and experience vocabularies -- newest first, and never
    opens an offer page. So every posting is METADATA ONLY: title, employer,
    a hiring scope, a work model, a contract type, a date, sometimes a salary
    range, and the Jobgether link. No advert.

    The hiring scope is why it is worth having: `location` is the employer's
    answer to where the applicant may be, and `Anywhere` means anywhere.

    Your markets go first (`eligible_countries` in your search decides the
    order, never the set), the budget is in requests, and progress is written
    after every page so a stopped run keeps what it read. Read-only, sends
    nothing about you, no inference call.
    """
    from career_agent.config.search_config import load_search_config
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.jobgether_collect import JobgetherCollector
    from career_agent.providers.jobgether import (
        LOCATION_SLUGS,
        MIN_SECONDS_BETWEEN_REQUESTS,
        Slice,
        default_slices,
    )
    from career_agent.sources.scheduling import slice_order

    if max_requests < 1:
        raise typer.BadParameter("--max-requests must be at least 1")

    universe: tuple[str, ...] = LOCATION_SLUGS
    if only:
        universe = tuple(part.strip() for part in only.split(",") if part.strip())
    if locations:
        chosen = tuple(part.strip() for part in locations.split(",") if part.strip())
    else:
        countries: tuple[str, ...] = ()
        try:
            search, _ = load_search_config(config_dir)
            countries = tuple(search.eligibility.eligible_countries)
            if search.eligibility.candidate_country:
                countries = (search.eligibility.candidate_country, *countries)
        except Exception:  # noqa: BLE001 - no search config is not a reason to refuse
            countries = ()
        chosen = slice_order(universe, countries=countries)
    # THE SET IS THE PROVIDER'S; THE ORDER IS YOURS. `slices` is the whole
    # neutral universe in the vendor's order and `priority` is your order over
    # it. The collector walks fewest-walked-first, so what you put first is
    # fresh first and nothing is left out over successive runs.
    slices = tuple(Slice(loc) for loc in universe) if no_cuts else default_slices(universe)
    priority = [
        s.key for s in (tuple(Slice(loc) for loc in chosen) if no_cuts else default_slices(chosen))
    ]

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher(request_delay_seconds=MIN_SECONDS_BETWEEN_REQUESTS) as fetcher:
            collector = JobgetherCollector(conn, fetcher)

            def progress(done: int, total: int) -> None:
                typer.echo(f"  slice {done}/{total}", err=True)

            stats = collector.collect(
                slices, priority=priority, max_requests=max_requests, on_progress=progress
            )
            ledger = collector.slice_state.summary("jobgether")
    finally:
        conn.close()

    typer.secho("Jobgether", bold=True)
    _table(
        [
            ("locations, fresh first", ", ".join(chosen)),
            ("slice ledger after this run", ledger),
            ("slices planned / walked", f"{stats.slices_planned} / {stats.slices_walked}"),
            ("requests made / allowed", f"{stats.requests} / {stats.max_requests}"),
            ("slices capped by the vendor", sum(1 for x in stats.slices if x.ended == "capped")),
            ("slices caught up", sum(1 for x in stats.slices if x.ended == "caught_up")),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("companies new", stats.companies_new),
            ("states where it may hire", stats.with_hiring_scope),
            ("states nothing about that", stats.without_hiring_scope),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.budget_exhausted:
        typer.secho(
            "The request budget ran out before every slice was walked. Raise --max-requests "
            "or run again: progress is kept.",
            fg=typer.colors.YELLOW,
        )
    typer.echo("")
    typer.echo("Every posting here is metadata only: the advert lives on a page this product")
    typer.echo("does not read. Jobgether is credited and Apply opens its listing.")
    typer.echo("run `career-agent rescore` to score the new postings offline")


def collect_fourdayweek_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_pages: Annotated[
        int, typer.Option("--max-pages", help="Pages of 100 to read. Default 50.")
    ] = 50,
) -> None:
    """Read the 4 Day Week public API v2 and store what is new.

    Bounded by default: fifty pages of a hundred, a fifth of the 23,690 the
    feed reported on 2026-09-11, at about one request a second under the
    vendor's sixty a minute. The API's own terms are one sentence -- "All we
    ask is that you link back to https://4dayweek.io" -- and Apply opens the
    4dayweek.io page, which is also the only URL the response carries.

    Every posting arrives with its full text, the employer's own work
    arrangement, a salary with a currency and a period when stated, and the
    countries it may be done remotely from, which is a hiring scope. Read-only,
    sends nothing about you, no inference call.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.fourdayweek_collect import FourDayWeekCollector

    if max_pages < 1:
        raise typer.BadParameter("--max-pages must be at least 1")

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher(request_delay_seconds=1.1) as fetcher:
            stats = FourDayWeekCollector(conn, fetcher, max_pages=max_pages).collect()
    finally:
        conn.close()

    typer.secho("4 Day Week", bold=True)
    _table(
        [
            ("pages read", stats.pages_read),
            ("stopped at the page budget", "yes" if stats.stopped_early else "no"),
            ("feed says it holds", stats.claimed_total if stats.claimed_total is not None else "?"),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("states where it may hire", stats.with_hiring_scope),
            ("states nothing about that", stats.without_hiring_scope),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the row", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.stopped_early:
        typer.echo("")
        typer.echo("This was a bounded walk, not the whole feed. Raise --max-pages to read more.")
    typer.echo("")
    typer.echo("4 Day Week is credited on every posting, and Apply opens its page.")
    typer.echo("run `career-agent rescore` to score the new postings offline")


def enrich_leads_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    provider: Annotated[str, typer.Option("--provider")] = "jobgether",
    max_companies: Annotated[
        int, typer.Option("--max-companies", help="Employers to probe this run. Default 100.")
    ] = 100,
    max_probes: Annotated[
        int, typer.Option("--max-probes", help="Board probes this run may make. Default 600.")
    ] = 600,
    no_probe: Annotated[
        bool, typer.Option("--no-probe", help="Fold against the corpus only; open no socket.")
    ] = False,
) -> None:
    """Fold metadata-only leads into their employer's own full posting.

    A Jobgether lead names an employer and a title and carries no advert. This
    finds the same title on the same employer's board -- already in the corpus,
    or found by a bounded probe of the supported ATS families and accepted
    only when the board carries that title -- records the lead as a sighting
    on that posting, and closes the lead as folded. The lead's hiring scope
    travels with the sighting. Nothing here reads your search or a keyword.

    Afterwards run `career-agent rescore`: only the postings touched are
    recomputed. Recording a sighting marks its posting in the dirty ledger
    (migration 0034), so nothing here deletes a score -- the previous answer
    stays on screen until the new one is written.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.enrich_leads import LeadEnricher

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = LeadEnricher(conn, fetcher, provider=provider).enrich(
                max_companies=max_companies, max_probes=max_probes, probe=not no_probe
            )
    finally:
        conn.close()

    typer.secho(f"Lead enrichment: {provider}", bold=True)
    _table(
        [
            ("leads examined", stats.leads_examined),
            ("origin URLs the vendor published", stats.origin_urls_available),
            ("employers examined", stats.companies_examined),
            ("employers probed", stats.companies_probed),
            ("probes made", stats.probes_made),
            ("boards registered", stats.boards_registered),
            ("families that answered", dict(stats.ats_families_detected)),
            ("outcomes", dict(stats.outcomes)),
            ("leads folded", len(stats.folded_job_ids)),
            ("canonical postings to rescore", len(stats.affected_canonical_ids)),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures[:10]:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    typer.echo("")
    typer.echo("run `career-agent rescore` to score the touched postings with their new scope")


def collect_dynamitejobs_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    max_postings: Annotated[
        int, typer.Option("--max-postings", help="Posting pages to read this run. Default 500.")
    ] = 500,
) -> None:
    """Read Dynamite Jobs through its public job sitemaps and store what is new.

    Every category sitemap is walked, so the listing is the whole inventory;
    then one page per posting not already held, up to the budget, persisted
    as it goes. Each page's schema.org JobPosting carries the whole advert
    and `applicantLocationRequirements`: the countries the employer will hire
    from, which the gate reads as the exhaustive allowlist it is. Read-only,
    sends nothing about you, no inference call.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.dynamitejobs_collect import DynamiteJobsCollector

    if max_postings < 1:
        raise typer.BadParameter("--max-postings must be at least 1")

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = DynamiteJobsCollector(conn, fetcher, max_postings=max_postings).collect()
    finally:
        conn.close()

    typer.secho("Dynamite Jobs", bold=True)
    _table(
        [
            ("category sitemaps read", stats.pages_read),
            ("postings listed", stats.postings_listed),
            ("already held, not requested", stats.postings_already_held),
            ("stopped at the posting budget", "yes" if stats.stopped_early else "no"),
            ("pages read", stats.postings_seen + stats.postings_unavailable),
            ("no JobPosting on the page", stats.postings_unavailable),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("states where it may hire", stats.with_location),
            ("states nothing about that", stats.without_location),
            ("with a salary", stats.with_salary),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  failed: {failure}", fg=typer.colors.RED)
    if stats.stopped_early:
        typer.echo("")
        typer.echo(
            "More postings are listed than this run read. Run again: held ones cost nothing."
        )
    typer.echo("")
    typer.echo("run `career-agent rescore` to score the new postings offline")


def collect_wwr_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    feed: Annotated[
        list[str] | None,
        typer.Option("--feed", help="Which feed to read. Repeatable. Default: all."),
    ] = None,
) -> None:
    """Read the We Work Remotely feeds and store what is new.

    One request per feed, because a feed has no pagination: it is a WINDOW of
    the most recent postings with no archive behind it. Collecting repeatedly
    accumulates over time and never completes, so nothing here reports coverage
    of remote hiring, and the stored run stats say so in words.

    Every posting arrives with its full description, so there is no second
    request per job and no partial record. Makes no inference call of either
    kind.
    """
    from career_agent.net.fetcher import HttpFetcher
    from career_agent.pipeline.wwr_collect import WwrCollector, known_feeds

    chosen = tuple(feed) if feed else ("all",)
    unknown = [name for name in chosen if name not in known_feeds()]
    if unknown:
        typer.secho(
            f"unknown feed(s): {', '.join(unknown)}. Known: {', '.join(known_feeds())}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    conn = connect(db)
    try:
        migrate(conn)
        with HttpFetcher() as fetcher:
            stats = WwrCollector(conn, fetcher).collect(feeds=chosen)
    finally:
        conn.close()

    typer.secho("We Work Remotely", bold=True)
    _table(
        [
            ("feeds asked", ", ".join(chosen)),
            ("feeds read", stats.feeds_read),
            ("feeds empty", stats.feeds_empty),
            ("feeds failed", stats.feeds_failed),
            ("postings seen", stats.postings_seen),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("description changed", stats.jobs_changed),
            ("already held elsewhere", f"{stats.duplicates_total} {stats.duplicates or ''}"),
            ("companies new", stats.companies_new),
            ("with a description", stats.descriptions_non_empty),
            ("without one", stats.descriptions_empty),
            ("not addressable", stats.postings_unaddressable),
            ("no company in the title", stats.postings_without_company),
            ("elapsed ms", stats.elapsed_ms),
        ]
    )
    for failure in stats.failures:
        typer.secho(f"  {failure}", fg=typer.colors.YELLOW)

    typer.secho(
        "  These are the postings the feeds held when asked. A feed is a window "
        "of the most recent ones, so this is not coverage of remote hiring.",
        fg=typer.colors.YELLOW,
    )
    typer.secho("  zero inference calls of either kind", fg=typer.colors.GREEN)

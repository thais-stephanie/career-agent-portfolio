"""Collection commands, registered onto the main Typer app.

Kept in a separate module so `cli.py` stays readable as the list of stages
rather than growing into a monolith.
"""

import json
from pathlib import Path
from typing import Annotated, Any

import typer
import yaml

from career_agent.config.loader import ConfigError, load_profile
from career_agent.config.registry import apply_registry, load_registry_file
from career_agent.net.fetcher import HttpFetcher, ResponseCache
from career_agent.pipeline.collect import CollectionStats, Collector
from career_agent.pipeline.coverage import build_report
from career_agent.pipeline.discover import discover
from career_agent.pipeline.renormalize import renormalize, shared_rows
from career_agent.runtime import RuntimeMode, read_identity
from career_agent.runtime.mode import record_retrieval
from career_agent.storage.db import connect, migrate, transaction

#: THE FILE THAT DRIVES COLLECTION, and it was not the default here.
#:
#: `config/companies.yaml` says so in its own first line: "This file DRIVES
#: COLLECTION. Editing it changes which companies and boards the next
#: `career-agent collect` run touches." `companies.seed.yaml` is the
#: ENGINEERING VALIDATION SET, six deliberately awkward boards kept small so
#: provider regression tests do not move every time the registry grows.
#:
#: The default pointed at the second one, so promoting a company into the
#: active registry did nothing until somebody typed a flag. Measured
#: 2026-09-09: 46 boards promoted that day sat in the YAML while `collect`
#: attempted 234 -- the number the database happened to hold from an earlier
#: load -- and none of the 46 were reached.
DEFAULT_REGISTRY = Path("config/companies.yaml")

#: The validation set, still loadable and no longer the default.
VALIDATION_REGISTRY = Path("config/companies.seed.yaml")
DEFAULT_DB_PATH = Path("data/career.db")
DEFAULT_CACHE_DIR = Path("data/cache/http")

collect_app = typer.Typer(help="Job collection from ATS providers.")


def _table(rows: list[tuple[str, Any]]) -> None:
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        typer.echo(f"  {label:<{width}} : {value}")


def register(app: typer.Typer) -> None:
    app.command(name="load-registry")(load_registry)
    app.command(name="collect")(collect)
    app.command(name="collect-report")(collect_report)
    app.command(name="renormalize")(renormalize_command)
    app.command(name="discover-board")(discover_board_command)
    app.command(name="coverage-report")(coverage_report_command)


def load_registry(
    registry: Annotated[Path, typer.Option("--registry")] = DEFAULT_REGISTRY,
    db: Annotated[Path | None, typer.Option("--db")] = None,
) -> None:
    """Load the company registry into the database, so `collect` can see it.

    THE STEP BETWEEN A PROMOTED BOARD AND A COLLECTED ONE, and the one that had
    no obvious owner. `config/companies.yaml` is the file a person edits and
    `source_board` is the table `collect` reads; nothing carries the first into
    the second except this command.

    Both defaults used to point elsewhere -- at the engineering validation set
    and at the empty database -- so the ordinary case needed two flags nobody
    would know to pass, and forgetting them looked exactly like success.
    """
    from career_agent.runtime.mode import RuntimeMode, resolve_database

    db = resolve_database(RuntimeMode.PERSONAL, db)
    try:
        parsed = load_registry_file(registry)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    conn = connect(db)
    try:
        migrate(conn)
        with transaction(conn):
            result = apply_registry(conn, parsed)
    finally:
        conn.close()

    typer.echo(f"loaded {result.companies} companies, {result.boards} boards into {db}")
    typer.echo("run `career-agent collect` to fetch from them.")
    if result.purpose == "engineering_validation_only":
        typer.secho(
            "note: this registry is an engineering validation set, not a curated "
            "target-company list (M1D builds that).",
            fg=typer.colors.YELLOW,
        )


def collect(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    no_cache: Annotated[bool, typer.Option("--no-cache")] = False,
    board: Annotated[
        list[str] | None, typer.Option("--board", help="Exact provider:identifier; repeatable.")
    ] = None,
    provider: Annotated[
        str | None,
        typer.Option("--provider", help="Only this board family, by its registry name."),
    ] = None,
    uncollected: Annotated[
        bool,
        typer.Option(
            "--uncollected",
            help="Only boards never collected. Registered from evidence, not yet read.",
        ),
    ] = False,
    max_boards: Annotated[
        int,
        typer.Option("--max-boards", help="Stop after this many boards. 0 means all."),
    ] = 0,
    score: Annotated[
        bool,
        typer.Option(
            "--score/--no-score",
            help="Score the postings this run inserted or changed, and only those.",
        ),
    ] = True,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = Path("config"),
    delay: Annotated[float, typer.Option("--delay", help="Seconds between requests.")] = 1.0,
) -> None:
    """Collect every active board. Makes real network requests.

    Collects COMPLETE boards: every public posting at each monitored company,
    regardless of whether it looks relevant. The raw corpus and the eventual
    candidate-analysis corpus are different populations, and narrowing happens
    later at the relevance prefilter, never here.

    `--provider`, `--uncollected` and `--max-boards` bound a run to a family,
    to the boards nobody has read yet, and to a number -- so a backlog of
    boards registered from evidence can be worked through in resumable
    batches. A board is one transaction and records `last_collected_at`, so
    the next batch starts where this one stopped. Nothing about a person
    chooses the boards: the order is the company slug.

    COLLECT, MARK, SCORE WHAT WAS MARKED. When the run ends, the postings it
    inserted or changed -- and only those, through the dirty ledger -- are
    scored against the search configuration (ADR-0025). `--no-score` leaves
    them for `career-agent rescore`.
    """
    conn = connect(db)
    rescore_stats = None
    try:
        migrate(conn)
        cache = None if no_cache else ResponseCache(DEFAULT_CACHE_DIR)
        with HttpFetcher(cache=cache, request_delay_seconds=delay) as fetcher:
            board_ids = _select_boards(conn, board, provider, uncollected, max_boards)
            stats = Collector(conn, fetcher).collect_all(
                use_cache=not no_cache, board_ids=board_ids
            )
        # WE LOOKED. Recorded whether or not anything was new, because "when
        # did we last check" and "when did an employer last publish" are
        # different questions and only the second one is derivable from the
        # postings. A run that found nothing still ran, and without this the
        # interface can only show a three-week-old publication date and imply
        # the collector is broken.
        #
        # Only when the database says it is personal. A demo database has no
        # retrieval history to record, and stamping one would be the mixing
        # `runtime/mode.py` exists to prevent.
        if (identity := read_identity(conn)) and identity.kind is RuntimeMode.PERSONAL:
            with transaction(conn):
                record_retrieval(conn)
        if score:
            rescore_stats = _score_marked(conn, config_dir)
        waiting = int(conn.execute("SELECT COUNT(*) FROM job_dirty").fetchone()[0])
    finally:
        conn.close()

    _print_stats(stats)
    if rescore_stats is not None:
        typer.echo("\nScoring (the postings this run marked, and only those)")
        _table(
            [
                ("marked", rescore_stats.dirty_marked),
                ("scored", rescore_stats.jobs_scored),
                ("no description", rescore_stats.jobs_skipped_no_description),
                (
                    "search index",
                    f"{rescore_stats.search_refresh}, {rescore_stats.search_rows_indexed} rows",
                ),
                ("elapsed ms", rescore_stats.elapsed_ms),
            ]
        )
    if waiting:
        typer.echo(f"\n{waiting} postings are marked and unscored. Run `career-agent rescore`.")
    if stats.boards_failed:
        raise typer.Exit(code=1)


def _select_boards(
    conn: Any,
    board: list[str] | None,
    provider: str | None,
    uncollected: bool,
    max_boards: int,
) -> set[str] | None:
    """Which `source_board` rows this run reads. None means every active one.

    Ordered by company slug, like `collect_all` itself, so `--max-boards`
    cuts a deterministic prefix and the next run's prefix starts after the
    boards this one stamped `last_collected_at` on.
    """
    if not (board or provider or uncollected or max_boards):
        return None
    rows = conn.execute(
        "SELECT sb.id, sb.provider, sb.board_identifier, sb.last_collected_at"
        " FROM source_board sb JOIN company c ON c.id = sb.company_id"
        " WHERE sb.active = 1 ORDER BY c.slug, sb.board_identifier"
    ).fetchall()
    if board:
        by_identity = {f"{r['provider']}:{r['board_identifier']}": str(r["id"]) for r in rows}
        unknown = set(board) - by_identity.keys()
        if unknown:
            raise typer.BadParameter("Unregistered boards: " + ", ".join(sorted(unknown)))
        wanted = {by_identity[value] for value in board}
        rows = [r for r in rows if str(r["id"]) in wanted]
    if provider:
        rows = [r for r in rows if str(r["provider"]) == provider]
    if uncollected:
        rows = [r for r in rows if r["last_collected_at"] is None]
    if max_boards:
        rows = rows[:max_boards]
    return {str(r["id"]) for r in rows}


def _score_marked(conn: Any, config_dir: Path) -> Any:
    """Score the dirty ledger against the configuration in force, or say why not.

    A missing or invalid configuration is reported and does not fail the
    collection: the postings are stored and marked, and `rescore` will find
    them. Opens no socket; `rescore` never does.
    """
    from career_agent.config.search_config import SearchConfigError, load_search_config
    from career_agent.pipeline.rescore import RescoreMode, rescore

    try:
        config, _ = load_search_config(config_dir)
    except SearchConfigError as exc:
        typer.secho(f"not scored: {exc}", fg=typer.colors.YELLOW)
        return None
    return rescore(conn, config, mode=RescoreMode.DIRTY)


def _print_stats(stats: CollectionStats) -> None:
    typer.echo("\nBoards")
    _table(
        [
            ("attempted", stats.boards_attempted),
            ("succeeded", stats.boards_succeeded),
            ("failed", stats.boards_failed),
        ]
    )
    typer.echo("\nPostings")
    _table(
        [
            ("observed", stats.postings_observed),
            ("new jobs", stats.jobs_new),
            ("seen again", stats.jobs_seen_again),
            ("changed", stats.jobs_changed),
            ("closed", stats.jobs_closed),
            ("skipped (malformed)", stats.postings_skipped_malformed),
            ("listed but not served", stats.postings_fetch_failed),
            ("description non-empty", stats.descriptions_non_empty),
            ("description empty", stats.descriptions_empty),
        ]
    )
    typer.echo("\nHTTP")
    _table([(k, v) for k, v in stats.http.items()])

    if stats.metadata:
        typer.echo("\nProvider metadata")
        _table(
            [
                ("observations", stats.metadata.get("observations", 0)),
                (
                    "declared paths resolved",
                    f"{stats.metadata.get('paths_resolved', 0)}"
                    f"/{stats.metadata.get('paths_declared', 0)}",
                ),
            ]
            + [(f"  {name}", count) for name, count in stats.metadata["by_dimension"].items()]
        )
        # A path nothing answered is either a quiet run or a renamed vendor
        # field. Either way it should be visible now, not discovered at M2.
        unresolved = stats.metadata.get("paths_unresolved") or []
        if unresolved:
            typer.secho(
                f"  declared but never resolved: {', '.join(unresolved)}",
                fg=typer.colors.YELLOW,
            )

    for sample in stats.fetch_failure_samples:
        typer.secho(f"  not served: {sample}", fg=typer.colors.YELLOW)

    for sample in stats.fetch_failure_samples:
        typer.secho(f"  not served: {sample}", fg=typer.colors.YELLOW)

    typer.echo(f"\nelapsed: {stats.elapsed_ms / 1000:.1f}s")

    if stats.failures:
        typer.secho("\nBoard failures", fg=typer.colors.RED)
        for failure in stats.failures:
            typer.echo(
                f"  {failure.company_slug:<14} {failure.provider:<12} "
                f"{failure.category:<16} attempts={failure.attempts}  {failure.message[:80]}"
            )


def collect_report(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    limit: Annotated[int, typer.Option("--limit")] = 5,
) -> None:
    """Summarise what is currently in the corpus. Makes no network request."""
    conn = connect(db)
    try:
        totals = conn.execute(
            "SELECT COUNT(*) AS jobs,"
            " SUM(CASE WHEN closed_at IS NULL THEN 1 ELSE 0 END) AS open_jobs,"
            " SUM(CASE WHEN content_hash IS NOT NULL THEN 1 ELSE 0 END) AS with_text"
            " FROM job"
        ).fetchone()
        typer.echo("Corpus")
        _table(
            [
                ("jobs total", totals["jobs"] or 0),
                ("open", totals["open_jobs"] or 0),
                ("closed", (totals["jobs"] or 0) - (totals["open_jobs"] or 0)),
                ("with description", totals["with_text"] or 0),
                (
                    "distinct descriptions",
                    conn.execute("SELECT COUNT(*) AS n FROM job_raw").fetchone()["n"],
                ),
                (
                    "archived payloads",
                    conn.execute("SELECT COUNT(*) AS n FROM job_provider_payload").fetchone()["n"],
                ),
            ]
        )

        typer.echo("\nPer company")
        rows = conn.execute(
            "SELECT c.slug, COUNT(j.id) AS n,"
            " SUM(CASE WHEN j.content_hash IS NOT NULL THEN 1 ELSE 0 END) AS with_text"
            " FROM company c LEFT JOIN job j ON j.company_id = c.id"
            " GROUP BY c.slug ORDER BY n DESC"
        ).fetchall()
        for row in rows:
            typer.echo(
                f"  {row['slug']:<16} {row['n']:>5} jobs, {row['with_text'] or 0:>5} with text"
            )

        typer.echo(f"\nSample of {limit} titles (illustrating corpus breadth)")
        for row in conn.execute(
            "SELECT title, location_raw FROM job ORDER BY id LIMIT ?", (limit,)
        ).fetchall():
            typer.echo(f"  {row['title'][:60]:<62} {row['location_raw'] or ''}")
        typer.echo(
            "\nnote: the corpus is every posting on each monitored board. Relevance"
            "\nfiltering happens later and never deletes from this corpus."
        )
    finally:
        conn.close()


def renormalize_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    show_shared: Annotated[bool, typer.Option("--show-shared")] = False,
) -> None:
    """Rebuild stored description text after a normalisation change.

    Offline: the archived html is the input and no board is contacted. This is a
    NORMALISATION CHANGE, never a source change -- no job is closed, reopened or
    made to look newly seen, and old raw rows are kept. Safe to run twice; the
    second run finds nothing to do.
    """
    conn = connect(db)
    try:
        migrate(conn)
        if show_shared:
            rows = shared_rows(conn)
            typer.echo(f"\nRaw rows shared by more than one job: {len(rows)}")
            for row in rows[:20]:
                typer.echo(
                    f"  {row['content_hash'][:12]}  jobs={row['job_count']:<4}"
                    f" providers={row['providers']}"
                )
        stats = renormalize(conn)
    finally:
        conn.close()

    typer.echo("\nRe-normalisation  (NORMALIZATION CHANGE, not a source change)")
    _table(
        [
            ("raw rows inspected", stats.raw_rows_inspected),
            ("unchanged", stats.raw_rows_unchanged),
            ("rewritten", stats.raw_rows_rewritten),
            ("  new rows created", stats.raw_rows_created),
            ("  converged onto existing", stats.raw_rows_converged),
            ("  skipped, no archived html", stats.raw_rows_without_html),
            ("jobs repointed", stats.jobs_repointed),
        ]
        + [(f"  {name}", count) for name, count in sorted(stats.jobs_by_provider.items())]
    )
    typer.echo(f"\nelapsed: {stats.elapsed_ms / 1000:.1f}s")
    if stats.is_noop:
        typer.secho(
            "nothing to do: stored text already matches the current rules.",
            fg=typer.colors.GREEN,
        )


def discover_board_command(
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    company_name: Annotated[str | None, typer.Option("--company-name")] = None,
    board_url: Annotated[str | None, typer.Option("--board-url")] = None,
    candidate_slug: Annotated[list[str] | None, typer.Option("--candidate-slug")] = None,
    all_boards: Annotated[bool, typer.Option("--all")] = False,
    delay: Annotated[float, typer.Option("--delay")] = 1.0,
) -> None:
    """Does this company have a Greenhouse, Lever or Ashby board we can collect?

    Makes real requests. Give it whatever you have -- a domain is usually
    enough; a board URL is the strongest evidence and is validated rather than
    trusted. `--all` keeps looking after the first hit, for companies that run
    boards on more than one provider.
    """
    if not any((domain, company_name, board_url, candidate_slug)):
        typer.secho(
            "give me something to work with: --domain, --company-name, "
            "--board-url or --candidate-slug",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    with HttpFetcher(request_delay_seconds=delay) as fetcher:
        result = discover(
            fetcher,
            domain=domain,
            name=company_name,
            board_url=board_url,
            identifiers=tuple(candidate_slug or ()),
            stop_on_first=not all_boards,
        )

    typer.echo(f"\nQuery      : {result.query}")
    typer.echo(f"Domain     : {result.canonical_domain or '-'}")
    typer.echo("\nProbes")
    for probe in result.probes:
        colour = typer.colors.GREEN if probe.outcome.is_board else None
        typer.secho(
            f"  {probe.provider:<11} {probe.board_identifier:<28} {probe.outcome.value:<18}"
            f" {probe.posting_count or '':>5}  via {probe.discovery_method}",
            fg=colour,
        )

    best = result.best
    typer.echo("")
    if best is not None:
        typer.secho(
            f"FOUND: {best.provider}/{best.board_identifier}"
            f" ({best.outcome.value}, {best.posting_count} postings)",
            fg=typer.colors.GREEN,
        )
    elif result.had_temporary_failure:
        # Not the same as "no board". Saying so is the whole point of the
        # TEMPORARY_FAILURE outcome existing.
        typer.secho(
            "INCONCLUSIVE: a probe failed for reasons that say nothing about "
            "whether the board exists. Try again before recording NO_BOARD_FOUND.",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)
    else:
        typer.secho("NO SUPPORTED BOARD FOUND", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)


def coverage_report_command(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    config_dir: Annotated[Path, typer.Option("--config-dir")] = Path("config"),
    as_json: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """What the registry and the corpus actually cover. Makes no network call."""
    priorities = None
    profile_note = None
    try:
        profile, path = load_profile(config_dir)
        priorities = {
            bucket: list(getattr(profile.target_company_markets, bucket.lower(), []) or [])
            for bucket in ("WANT", "INTERESTED", "AVOID", "NEVER")
        }
        profile_note = f"market priorities read from {path.name}"
    except (ConfigError, AttributeError):
        # No local profile, or one without market priorities. Reading the
        # example would produce a report that looks complete and describes
        # nobody, so the report says what it could not measure instead.
        profile_note = "no candidate profile found - market-prioritised view unavailable"

    # The review queue is curation, not runtime configuration, so a missing or
    # unreadable file degrades the report rather than failing the command.
    candidate_rows: list[dict] | None = None
    queue_path = config_dir / "company_candidates.yaml"
    if queue_path.exists():
        try:
            parsed = yaml.safe_load(queue_path.read_text(encoding="utf-8")) or {}
            rows = parsed.get("candidates") if isinstance(parsed, dict) else None
            candidate_rows = [row for row in rows or [] if isinstance(row, dict)] or None
        except yaml.YAMLError:
            candidate_rows = None

    conn = connect(db)
    try:
        migrate(conn)
        report = build_report(conn, priorities, candidate_rows)
    finally:
        conn.close()

    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
        return

    typer.echo("\nRegistry")
    _table(list(report.registry.items())[:6])
    typer.echo("  by discovery source:")
    for name, count in report.registry["by_discovery_source"].items():
        typer.echo(f"    {name:<28} {count:>5}")

    typer.echo("\nProvider coverage")
    for label, values in report.providers.items():
        typer.echo(f"  {label}:")
        for name, count in values.items():
            typer.echo(f"    {name:<28} {count:>5}")

    typer.echo(f"\nMarket coverage  ({profile_note})")
    typer.secho(f"  {report.markets['note']}", fg=typer.colors.YELLOW)
    if report.markets.get("by_priority"):
        _table(list(report.markets["by_priority"].items()))
    else:
        typer.echo(f"  {report.markets['priority_note']}")
    typer.echo(f"  distinct HQ markets: {report.markets['distinct_markets']}")
    for code, count in list(report.markets["active_companies_by_hq"].items())[:15]:
        typer.echo(f"    {code:<12} {count:>5}")

    typer.echo("\nJob coverage")
    _table(
        [
            ("open jobs", report.jobs["open_total"]),
            ("closed jobs", report.jobs["closed_total"]),
            ("with description", report.jobs["with_description"]),
            ("distinct descriptions", report.jobs["distinct_descriptions"]),
            ("archived payloads", report.jobs["archived_payloads"]),
            ("companies with open jobs", report.jobs["companies_with_open_jobs"]),
            (
                f"posted in last {report.jobs['fresh_window_days']} days",
                report.jobs["posted_last_7_days"],
            ),
        ]
        + [(f"  {k}", v) for k, v in report.jobs["open_by_provider"].items()]
    )
    typer.echo("  busiest companies:")
    for row in report.jobs["top_companies_by_open_jobs"][:10]:
        typer.echo(f"    {row['company']:<28} {row['open_jobs']:>5}")

    typer.echo("\nSourcing funnel and unsupported ATS")
    if not report.candidates.get("available"):
        typer.secho(f"  {report.candidates['note']}", fg=typer.colors.YELLOW)
    else:
        _table([("candidates reviewed", report.candidates["total"])])
        for status, count in report.candidates["by_status"].items():
            typer.echo(f"    {status:<28} {count:>5}")
        typer.echo("  unsupported ATS, confirmed by name against a live board:")
        for vendor, count in report.candidates["unsupported_ats"].items():
            typer.echo(f"    {vendor:<28} {count:>5}")
        if not report.candidates["unsupported_ats"]:
            typer.echo("    (none confirmed)")
        typer.secho(f"  {report.candidates['note']}", fg=typer.colors.YELLOW)

    typer.echo("\nHealth")
    _table(
        [
            ("boards never collected", report.health["boards_never_collected"]),
            ("boards with last error", report.health["boards_with_last_error"]),
            ("empty boards", report.health["empty_boards"]),
            ("suspicious holds", len(report.health["suspicious_holds"])),
            ("last collection", report.health["last_collection_started_at"] or "-"),
            ("last status", report.health["last_collection_status"] or "-"),
        ]
    )
    for hold in report.health["suspicious_holds"]:
        typer.secho(
            f"    HOLD {hold['company']}/{hold['provider']}:{hold['board']}"
            f" observed={hold['observed']} since={hold['since']}",
            fg=typer.colors.YELLOW,
        )
    for failure in report.health["failed_boards"][:10]:
        typer.secho(
            f"    FAIL {failure['company']}/{failure['provider']}:{failure['board']}"
            f" {str(failure['error'])[:60]}",
            fg=typer.colors.RED,
        )

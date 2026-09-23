"""The command line interface - the only entry point into the system.

Every stage of the pipeline is a subcommand here, and nothing else is ever the
interface. That is a deliberate architectural constraint rather than a
convenience: when a scheduler runs this daily, it calls these commands. It does
not contain any logic of its own, because logic outside the repository cannot
be run by pytest, checked against the golden set, or reviewed in a diff.

Milestone 0 provides three commands, none of which touch the network:

    career-agent migrate   apply pending database migrations
    career-agent init      migrate, then register the candidate and snapshot
                           the profile
    career-agent doctor    report what is configured and what is not
"""

import contextlib
import sqlite3
import sys
from pathlib import Path
from typing import Annotated

import typer
from dotenv import load_dotenv

from career_agent import __version__
from career_agent.config.loader import (
    ConfigError,
    LoadedConfig,
    load_config,
    local_path,
    profile_yaml_text,
)
from career_agent.config.ownership import OWNERSHIP_RULE
from career_agent.config.ownership import summary as ownership_summary
from career_agent.domain.profile import SearchProfile
from career_agent.llm.vendors import credential_variables_present
from career_agent.storage.db import connect, migrate, pending_migrations, schema_version
from career_agent.storage.db import table_names as list_tables
from career_agent.storage.repositories import (
    CandidateRepo,
    ClaimRepo,
    SearchProfileVersionRepo,
)

DEFAULT_CONFIG_DIR = Path("config")
DEFAULT_DB_PATH = Path("data/career.db")


def _force_utf8_output() -> None:
    """Print Portuguese job titles instead of question marks.

    A default Windows console runs on a legacy code page, and Python encodes
    stdout to match it. "Especialista em Integracoes" survives that; the real
    title, with its tilde and cedilla, comes out as replacement characters --
    in a product whose whole point is reading Brazilian job postings.

    The data was never wrong: it round-trips through SQLite as UTF-8 either
    way. Only the terminal rendering was, which makes this a display fix and
    not a data fix, and worth doing precisely because the failure looks like
    corruption and is not.

    Guarded, because a redirected stream or a stub may not be reconfigurable.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        # A redirected stream or a test stub may refuse; slightly wrong
        # output is never worth failing a command over.
        with contextlib.suppress(ValueError, OSError):  # pragma: no cover
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_output()


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Career Agent - Personal Alpha job discovery and eligibility agent.",
)

ConfigDirOption = Annotated[
    Path, typer.Option("--config-dir", help="Directory holding the YAML configuration.")
]
DbOption = Annotated[Path, typer.Option("--db", help="Path to the SQLite database file.")]


def _echo_section(title: str) -> None:
    typer.echo(f"\n{title}")
    typer.echo("-" * len(title))


def _load_or_exit(config_dir: Path) -> LoadedConfig:
    try:
        return load_config(config_dir)
    except ConfigError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc


@app.command(name="migrate")
def migrate_cmd(
    db: DbOption = DEFAULT_DB_PATH,
) -> None:
    """Apply any pending database migrations. Safe to run repeatedly."""
    conn = connect(db)
    try:
        applied = migrate(conn)
    finally:
        conn.close()

    if applied:
        for migration in applied:
            typer.echo(f"applied {migration.version:04d}_{migration.name}")
    else:
        typer.echo("database is already up to date")


@app.command()
def init(
    config_dir: ConfigDirOption = DEFAULT_CONFIG_DIR,
    db: DbOption = DEFAULT_DB_PATH,
) -> None:
    """Prepare the database and register the candidate from the local profile.

    Snapshots the profile YAML into search_profile_version. Re-running with an
    unchanged profile reuses the existing snapshot rather than creating a
    duplicate, so this is safe to run as often as you like.
    """
    config = _load_or_exit(config_dir)

    conn = connect(db)
    try:
        applied = migrate(conn)
        for migration in applied:
            typer.echo(f"applied {migration.version:04d}_{migration.name}")

        conn.execute("BEGIN")
        candidate_id = CandidateRepo(conn).upsert(
            config.profile.candidate_key,
            config.profile.display_name or config.profile.candidate_key,
        )
        version_id = SearchProfileVersionRepo(conn).record(
            candidate_id, profile_yaml_text(config.profile_path)
        )

        claim_count = 0
        if config.career_facts is not None:
            claims = ClaimRepo(conn)
            for claim in config.career_facts.verified_claims:
                if claims.current_row(candidate_id, claim.claim_key) is None:
                    claims.add(candidate_id, claim)
                    claim_count += 1
        conn.execute("COMMIT")
    finally:
        conn.close()

    typer.echo(f"candidate      : {config.profile.candidate_key} ({candidate_id})")
    typer.echo(f"profile version: {version_id}")
    typer.echo(f"claims added   : {claim_count}")


def _report_new_seniority_levels(config_dir: Path) -> None:
    """Levels the vocabulary has and this configuration does not mention.

    Reports; never edits. `points_for` already answers zero for an unpriced
    level so nothing is broken, and `preferences.seniority.excluded` is empty
    so nothing is hidden -- both of which are the right defaults and neither of
    which is a decision this command is entitled to make for her.
    """
    from career_agent.config.search_config import load_search_config
    from career_agent.domain.enums import Seniority

    try:
        config, path = load_search_config(config_dir)
    except Exception:  # pragma: no cover - doctor already reports a bad config
        return

    unpriced = config.scoring.components.seniority.unpriced_levels()
    named = set(config.preferences.seniority.preferred) | set(config.preferences.seniority.excluded)
    unmentioned = tuple(level for level in Seniority if level not in named)
    if not unpriced and not unmentioned:
        return

    typer.echo("")
    typer.secho("seniority    : levels your search does not mention", bold=True)
    if unpriced:
        typer.echo("  scores nothing: " + ", ".join(level.value for level in unpriced))
    if unmentioned:
        typer.echo(
            "  neither wanted nor set aside: " + ", ".join(level.value for level in unmentioned)
        )
    typer.echo(f"  Say which you want in {path.name}: `preferences.seniority.preferred`")
    typer.echo("  and which you would rather not see at all: `preferences.seniority.excluded`.")
    typer.echo("  Nothing is hidden until you name it there.")


def _report_scope_membership(config_dir: Path) -> None:
    """Accepted hiring scopes that cannot contain where the candidate lives.

    Found on 2026-09-11 in the owner's private search: `NORTH_AMERICA`, `EMEA`
    and `EUROPE` beside `eligible_countries: [BR]`. Every `Remote (United
    States | Canada)` and `London, UK` posting had become VERIFIED_ELIGIBLE --
    11,056 of 15,285 -- because a scope the settings accepted was read as a
    scope that included Brazil. The gate now requires a region to contain one
    of the candidate's countries before it opens, so these entries are inert;
    this says so, because an inert setting somebody typed on purpose is a
    question they deserve to have answered.

    Reports; never edits.
    """
    from career_agent.config.search_config import load_search_config
    from career_agent.match.places import REGIONS, region_contains

    try:
        config, path = load_search_config(config_dir)
    except Exception:  # pragma: no cover - doctor already reports a bad config
        return

    countries = {c.upper() for c in config.eligibility.eligible_countries}
    if config.eligibility.candidate_country:
        countries.add(config.eligibility.candidate_country.upper())
    if not countries:
        return
    inert: list[str] = []
    unknown: list[str] = []
    for scope in config.eligibility.eligible_scopes:
        code = str(scope).upper()
        if code not in REGIONS:
            unknown.append(code)
            continue
        if not any(region_contains(code, country) for country in countries):
            inert.append(code)
    if not inert and not unknown:
        return

    typer.echo("")
    typer.secho("hiring scopes: entries that cannot include you", bold=True)
    if inert:
        typer.secho(
            "  accepted but do not contain "
            + ", ".join(sorted(countries))
            + ": "
            + ", ".join(inert),
            fg=typer.colors.YELLOW,
        )
        typer.echo("  A posting hiring only there is not one you can take, so the gate ignores")
        typer.echo("  these. They change nothing until `eligible_countries` changes.")
    if unknown:
        typer.secho(
            "  not a region this product knows: " + ", ".join(unknown), fg=typer.colors.YELLOW
        )
    typer.echo(
        f"  Edit `eligibility.eligible_scopes` in {path.name} if this is not what you meant."
    )


def _report_candidate_consistency(profile: SearchProfile, config_dir: Path) -> None:
    """Whether the two files that describe the candidate agree with each other.

    They are allowed to disagree; nothing here refuses to run. What is not
    allowed is disagreeing SILENTLY, which is how somebody ends up with a
    matcher gating on Brazil while their profile says Portugal and no command
    ever mentions it.

    An unreadable search configuration is not this check's business and is
    reported by `search-config`, so it degrades to a note rather than turning a
    profile section red for a reason that is not about the profile.
    """
    from career_agent.config.consistency import OWNERSHIP_RULE, divergences
    from career_agent.config.search_config import SearchConfigError, load_search_config

    try:
        search, search_path = load_search_config(config_dir)
    except SearchConfigError:
        typer.echo("  consistency: not checked - the search configuration did not load")
        return

    found = divergences(profile, search)
    if not found:
        typer.secho(f"  consistency: OK  (agrees with {search_path.name})", fg=typer.colors.GREEN)
        return

    typer.secho(
        f"  consistency: {len(found)} candidate fact(s) stated differently in {search_path.name}",
        fg=typer.colors.YELLOW,
    )
    for divergence in found:
        typer.echo(f"    - {divergence.sentence}")
    typer.echo(f"    {OWNERSHIP_RULE}")
    typer.echo("    Nothing was chosen for you. Edit whichever file is wrong.")


def _report_corpus(db) -> bool:
    """What this database actually holds, and whether it is the one meant.

    Three questions a person asks when something looks wrong, in the order
    they ask them: is this the right database, are these scores current, and
    are the sources working. Each has been answerable for a while and none was
    answered here, so the answer was to read the code.
    """
    import sqlite3

    from career_agent.domain.matching import MATCH_SCHEMA_VERSION

    _echo_section("Corpus")
    if not db.exists():
        typer.secho("no database at that path yet", fg=typer.colors.YELLOW)
        typer.echo("  `career-agent init-personal` creates one.")
        return False

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        jobs = int(conn.execute("SELECT COUNT(*) AS n FROM job").fetchone()["n"])
        typer.echo(f"database        : {db}")
        typer.echo(f"postings        : {jobs}")

        if jobs == 0:
            # Largest first, not alphabetical. The real corpus is the big one,
            # and sorting by name put `demo.db` and two abandoned milestone
            # databases ahead of it.
            others = sorted(
                (
                    other
                    for other in db.parent.rglob("*.db")
                    if other != db and other.stat().st_size > db.stat().st_size
                ),
                key=lambda other: other.stat().st_size,
                reverse=True,
            )
            if not others:
                # A new install. An empty database moments after `init` is the
                # correct state and must not read as a fault -- doctor failing
                # on a fresh project is how somebody concludes the install went
                # wrong and starts over.
                typer.echo("  empty, which is expected before the first collection")
                return True

            # The single most common way to be confused by this product. Every
            # extraction command defaults to `data/career.db`, which is empty,
            # and the real corpus lives somewhere the flag has to name.
            typer.secho("  this database is empty", fg=typer.colors.YELLOW)
            typer.echo("  a larger database exists. Did you mean one of these?")
            for other in others[:3]:
                typer.echo(f"      --db {other}")
            return False

        current = conn.execute(
            "SELECT config_id, MAX(config_version) AS v FROM job_match GROUP BY config_id"
        ).fetchall()
        stale = 0
        for row in current:
            stale += int(
                conn.execute(
                    "SELECT COUNT(*) AS n FROM job_match"
                    " WHERE config_id = ? AND config_version = ? AND schema_version < ?",
                    (row["config_id"], row["v"], MATCH_SCHEMA_VERSION),
                ).fetchone()["n"]
            )
        typer.echo(f"analysis schema : {MATCH_SCHEMA_VERSION}")
        if stale:
            typer.secho(f"  {stale} scores were computed by an older build", fg=typer.colors.YELLOW)
            typer.echo(f"  `career-agent rescore --db {db}` brings them up to date.")

        from career_agent.sources.health import health, summarise

        entries = health(conn)
        producing = [entry for entry in entries if entry.postings > 0]
        typer.echo(f"sources         : {len(producing)} of {len(entries)} have postings here")
        typer.echo("  " + "  ".join(f"{k}={v}" for k, v in summarise(entries).items()))
        typer.echo(f"  `career-agent source-health --db {db}` says which, and when each ran.")

        from career_agent.storage.integrity import audit

        broken = [finding for finding in audit(conn) if finding.is_broken]
        if broken:
            typer.secho(
                f"integrity       : {len(broken)} checks found broken rows", fg=typer.colors.RED
            )
            for finding in broken[:3]:
                typer.echo(f"  {finding.check}: {finding.count} -- {finding.detail}")
            typer.echo(f"  `career-agent integrity --db {db}` lists them.")
            return False
        typer.echo("integrity       : every invariant holds")
        return True
    finally:
        conn.close()


def _report_jooble(db: Path) -> None:
    """Whether the job-source API is usable, and how much of it is left.

    Three separate facts, and reporting them together is the point: a key with
    no domain cannot be used, and a key with a spent quota cannot be used
    either, and neither failure is visible from the other.

    PRESENCE ONLY for the key, exactly as above. The domain is not a secret and
    is printed, because a person needs to see which country their key is
    pointed at -- pointing a Brazilian search at `jooble.org` returns United
    States postings perfectly successfully.
    """
    import os

    from career_agent.providers.jooble import DOMAIN_ENV, KEY_ENV
    from career_agent.providers.jooble_quota import ledger_for

    _echo_section("Jooble (job source API)")
    key_set = bool(os.environ.get(KEY_ENV))
    typer.echo(f"{KEY_ENV:<16} : {'PRESENT' if key_set else 'MISSING'}")

    domain = os.environ.get(DOMAIN_ENV, "")
    if domain:
        typer.secho(f"{DOMAIN_ENV:<16} : {domain}", fg=typer.colors.GREEN)
    else:
        typer.secho(f"{DOMAIN_ENV:<16} : NOT SET", fg=typer.colors.YELLOW)
        typer.echo("  A Jooble key is bound to ONE country and the key does not say which.")
        typer.echo("  Set it to the domain your key was issued on, for example:")
        typer.echo("    JOOBLE_DOMAIN=br.jooble.org")
        typer.echo("  There is no default: guessing would spend one of 500 lifetime requests.")

    usage = ledger_for(db).read()
    typer.echo(f"{'requests':<16} : {usage.known_local_requests} made from this machine")
    typer.echo(f"{'budget':<16} : at most {usage.known_remaining_upper_bound} remain")
    typer.echo("  an upper bound, not a balance: the key may have been used elsewhere")


def _report_workspace(db: Path, config_dir: Path) -> bool:
    """The conditions that make the product unusable without saying so.

    Section 43 of the V1.3 brief. Each of these is silent when it is wrong: a
    CV that cannot be parsed looks like a CV with nothing in it, a review left
    half-answered looks like a review nobody started, and a private config
    missing a pattern the shipped example carries looks like a posting that
    simply did not say.

    Reads the database and the configuration. Opens no socket and prints no
    value that is anybody's private content -- counts and names of files only.
    """
    ok = True
    _echo_section("Your side of the workspace")

    # -- can a CV be read at all -----------------------------------------
    #
    # `pypdf` and `python-docx` are imported inside the readers, so a missing
    # one is discovered at the moment somebody chooses a file rather than at
    # startup. Checked here so it is discovered before that.
    for label, module in (("PDF", "pypdf"), ("DOCX", "docx")):
        try:
            __import__(module)
        except ImportError:
            ok = False
            typer.secho(f"{label:<12} : reader MISSING - run `uv sync`", fg=typer.colors.YELLOW)
        else:
            typer.secho(f"{label:<12} : reader available", fg=typer.colors.GREEN)

    if db.exists():
        conn = connect(db)
        try:
            tables = set(list_tables(conn))
            if {"verified_claim", "cv_import", "cv_proposal"} <= tables:
                confirmed = conn.execute(
                    "SELECT COUNT(*) AS n FROM verified_claim"
                    " WHERE superseded_by_id IS NULL AND verified = 1"
                ).fetchone()["n"]
                retired = conn.execute(
                    "SELECT COUNT(*) AS n FROM verified_claim"
                    " WHERE superseded_by_id IS NULL AND verified = 0"
                ).fetchone()["n"]
                pending = conn.execute(
                    "SELECT COUNT(*) AS n FROM cv_proposal WHERE decision = 'PENDING'"
                ).fetchone()["n"]
                reviews = conn.execute("SELECT COUNT(*) AS n FROM requirement_review").fetchone()[
                    "n"
                ]

                typer.echo(f"confirmed    : {confirmed} facts about you")
                if retired:
                    typer.echo(f"retired      : {retired} kept, no longer drawn on")
                if pending:
                    typer.secho(
                        f"CV review    : {pending} proposals still unanswered",
                        fg=typer.colors.YELLOW,
                    )
                    typer.echo("  open Your career evidence to finish them")
                else:
                    typer.echo("CV review    : nothing waiting")
                if reviews:
                    typer.echo(f"your notes   : {reviews} requirement verdicts recorded")
                if not confirmed:
                    typer.echo("  nothing is confirmed about you, so every requirement is a gap")
            else:
                ok = False
                typer.secho(
                    "workspace    : tables MISSING - run `career-agent migrate`",
                    fg=typer.colors.YELLOW,
                )
        finally:
            conn.close()

    # -- a vocabulary that grew under an existing configuration -----------
    #
    # STAFF and PRINCIPAL joined `Seniority` on 2026-09-07, so every
    # configuration written before then prices neither and names neither. That
    # is not an error and it is not something this command may fix: which
    # levels somebody wants is a candidate fact. It is something she should be
    # TOLD, because the silent version of it is a level scoring zero for a
    # reason nobody can see.
    _report_new_seniority_levels(config_dir)
    _report_scope_membership(config_dir)

    # -- who owns what ----------------------------------------------------
    counts = ownership_summary()
    typer.echo("")
    typer.echo(f"ownership    : {OWNERSHIP_RULE}")
    typer.echo(
        f"  {counts['candidate_facts']} facts are yours, "
        f"{counts['machinery_facts']} are the product's machinery"
    )
    typer.echo(f"  {counts['editable_without_a_file']} can be changed without opening a file")
    typer.echo(
        f"  {counts['candidate_facts_in_the_search_file']} of yours still live in the "
        "search configuration"
    )

    if _report_pattern_drift(config_dir):
        ok = False
    return ok


def _report_pattern_drift(config_dir: Path) -> bool:
    """Phrases the shipped example carries and the private file does not.

    This is not a style check. `USA Only` is what We Work Remotely publishes in
    the field where an employer answers "where may we hire?", and a private
    configuration written before that source existed has no pattern for it --
    so the gate stays UNRESOLVED and a posting that rules the candidate out
    reads as one that did not say. Nothing on any screen could show that.

    Reports a COUNT and the missing phrases, which are ours: they are in the
    committed example that ships with this repository. It never prints anything
    the private file adds.
    """
    import yaml

    from career_agent.yaml_io import safe_load

    local = config_dir / "search.local.yaml"
    shipped = config_dir / "search.worked-example.yaml"
    if not local.exists() or not shipped.exists():
        return False

    def blockers(path: Path) -> dict[str, set[str]]:
        raw = safe_load(path.read_text(encoding="utf-8")) or {}
        found: dict[str, set[str]] = {}
        for entry in (raw.get("eligibility") or {}).get("blockers") or []:
            found[str(entry.get("id"))] = {str(p).lower() for p in entry.get("patterns") or []}
        return found

    try:
        mine, theirs = blockers(local), blockers(shipped)
    except yaml.YAMLError:  # pragma: no cover - the loader reports this properly
        return False

    drifted = False
    for blocker_id, patterns in theirs.items():
        missing = sorted(patterns - mine.get(blocker_id, set()))
        if not missing or blocker_id not in mine:
            continue
        drifted = True
        typer.echo("")
        typer.secho(
            f"drift        : your {blocker_id} is missing "
            f"{len(missing)} phrase(s) the shipped example carries",
            fg=typer.colors.YELLOW,
        )
        for phrase in missing[:6]:
            typer.echo(f'  "{phrase}"')
        typer.echo("  a phrase you do not have is a posting that reads as silent")
    return drifted


@app.command()
def doctor(
    config_dir: ConfigDirOption = DEFAULT_CONFIG_DIR,
    db: DbOption = DEFAULT_DB_PATH,
) -> None:
    """Report what is configured, what is missing, and what the database holds.

    Makes no network call and no LLM call. Run it first whenever something
    behaves unexpectedly.
    """
    load_dotenv()
    ok = True

    _echo_section("Career Agent")
    typer.echo(f"version    : {__version__}")
    typer.echo(f"config dir : {config_dir.resolve()}")
    typer.echo(f"database   : {db.resolve()}")

    # --- configuration ----------------------------------------------------
    _echo_section("Configuration")
    profile_file = local_path(config_dir, "profile")
    facts_file = local_path(config_dir, "career_facts")

    try:
        config = load_config(config_dir)
    except ConfigError as exc:
        ok = False
        typer.secho("profile      : INVALID or MISSING", fg=typer.colors.RED)
        for line in str(exc).splitlines():
            typer.echo(f"  {line}")
    else:
        # Always report which file was actually read: a system running on the
        # wrong configuration looks completely normal from the outside.
        typer.secho(f"profile      : OK  ({config.profile_path})", fg=typer.colors.GREEN)
        typer.echo(f"  candidate  : {config.profile.candidate_key}")
        typer.echo(f"  residence  : {config.profile.candidate_geography.residence_country}")
        typer.echo(
            "  hiring scopes accepted: "
            + ", ".join(
                str(s) for s in config.profile.job_hiring_geography.acceptable_hiring_scopes
            )
        )
        typer.echo(
            "  target markets WANT   : "
            + (", ".join(config.profile.target_company_markets.WANT) or "(none)")
        )
        typer.echo(
            "  compensation gate     : "
            + ("active" if config.profile.compensation.has_hard_floor else "skipped (no floor set)")
        )
        if config.career_facts is None:
            typer.echo(f"career facts : not present ({facts_file}) - optional until M2+")
        else:
            verified = sum(1 for c in config.career_facts.verified_claims if c.verified)
            typer.echo(
                f"career facts : OK  ({config.career_facts_path}) "
                f"{len(config.career_facts.verified_claims)} claims, {verified} verified"
            )

        _report_candidate_consistency(config.profile, config_dir)

    if not profile_file.exists():
        typer.echo(f"  hint       : copy {config_dir / 'profile.example.yaml'} to {profile_file}")

    # --- database ---------------------------------------------------------
    _echo_section("Database")
    if not db.exists():
        ok = False
        typer.secho("status     : NOT CREATED - run `career-agent init`", fg=typer.colors.YELLOW)
    else:
        conn = connect(db)
        try:
            version = schema_version(conn)
            pending = pending_migrations(conn)
            typer.echo(f"schema     : version {version}")
            if pending:
                ok = False
                names = ", ".join(f"{m.version:04d}_{m.name}" for m in pending)
                typer.secho(
                    f"pending    : {names} - run `career-agent migrate`", fg=typer.colors.YELLOW
                )
            else:
                typer.secho("pending    : none", fg=typer.colors.GREEN)

            typer.echo("row counts :")
            for table in list_tables(conn):
                try:
                    count = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]
                except sqlite3.Error:  # pragma: no cover - defensive
                    count = "?"
                typer.echo(f"  {table:<24} {count}")
        finally:
            conn.close()

    # --- credentials ------------------------------------------------------
    _echo_section("Credentials")
    # PRESENCE ONLY. The value is never printed, never logged, and nothing in
    # this command reads it. One vendor per line because the benchmark is
    # multi-vendor by design and "the key is set" was previously a question
    # only answerable for Anthropic -- which made "can we run the free Gemini
    # arm?" a question the tool could not answer at all.
    # Which variables belong to which vendor is `llm/vendors`' business, and it
    # is now consulted by two callers -- this report and the live runner, which
    # refuses to start without one. Two copies of a security-relevant list is
    # one copy too many, so only the display labels live here.
    for label, vendor in (
        ("ANTHROPIC", "anthropic"),
        ("OPENAI", "openai"),
        # The Google SDK accepts either name and prefers GOOGLE_API_KEY.
        ("GOOGLE / GEMINI", "google"),
    ):
        found = credential_variables_present(vendor)
        state = f"PRESENT ({', '.join(found)})" if found else "MISSING"
        typer.echo(f"{label:<16} : {state}")
    typer.echo("  presence only - no value is read, printed or logged")
    typer.echo("  (no key is required until a benchmark arm is authorised)")

    _report_jooble(db)

    if not _report_workspace(db, config_dir):
        ok = False

    if not _report_corpus(db):
        ok = False

    _echo_section("Result")
    typer.echo("no network calls and no LLM calls were made by this command")
    if ok:
        typer.secho("everything checks out", fg=typer.colors.GREEN)
    else:
        typer.secho("attention needed - see above", fg=typer.colors.YELLOW)
        raise typer.Exit(code=1)


from career_agent import (  # noqa: E402  (avoids a circular import)
    cli_collect,
    cli_extract,
    cli_local,
    cli_maintenance,
)

cli_collect.register(app)
cli_maintenance.register(app)
cli_extract.register(app)
cli_local.register(app)


if __name__ == "__main__":  # pragma: no cover
    app()

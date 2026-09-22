"""Extraction commands, including the Cowork development harness.

`export-extraction-batch` writes what the production pipeline would send;
`import-extraction-results` runs what came back through the production pipeline.
Neither can reach a vendor: the export writes files, the import reads files and
replays them, and that is all they are capable of.

`run-extraction` is the third command, and it is the only one that can reach a
vendor. It was built last, on purpose, and it is preflight-only unless it is
told otherwise: a resolved database, a resolved model, an explicit selection, a
computed call count and a stated ceiling all have to line up before `--execute`
will let a single request leave the machine. Everything it can do without that
flag is arithmetic over files and rows.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer
from dotenv import load_dotenv

from career_agent.clock import now_utc
from career_agent.evaluation.golden import (
    REVIEW_ARTIFACT,
    GoldenError,
    golden_job_ids,
    load_cases,
    validate_case,
)
from career_agent.evaluation.report import assignment_problems, review_markdown
from career_agent.evaluation.screen import load_screen, validate_screen
from career_agent.llm.acceptance import CacheDisposition, disposition_for
from career_agent.llm.budget import PROGRAM_AUTHORIZATION, BudgetLedger
from career_agent.llm.client import Family, ModelConfig, StructuredOutput
from career_agent.llm.pacing import RateLimiter
from career_agent.llm.pricing import (
    PERSONAL_ALPHA_PAID_CEILING_USD as CEILING,
)
from career_agent.llm.pricing import (
    SpendAudit,
    audit_spend,
)
from career_agent.llm.quotas import QuotaLimits, observed_quota
from career_agent.llm.transport import TDescriptionFamily, TProviderFamily
from career_agent.llm.vendors import (
    CREDENTIAL_VARIABLES,
    available_vendors,
    credential_variables_present,
    get_client,
)
from career_agent.pipeline.cowork import (
    CoworkBatchError,
    export_batch,
    load_batch,
    run_batch,
)
from career_agent.pipeline.extract import (
    MAX_ATTEMPTS,
    ExtractionOutcome,
    load_source,
    store_outcome,
)
from career_agent.pipeline.live import LivePlan, LiveRunReport, plan_run, run_plan
from career_agent.storage.db import connect, migrate, schema_version, transaction
from career_agent.storage.fingerprint_repo import LLMCallRepo

DEFAULT_DB_PATH = Path("data/career.db")

#: Gitignored, and deliberately so. A batch contains full job descriptions and
#: archived ATS records; it is working material, not something to commit.
DEFAULT_BATCH_DIR = Path("out/m2-cowork")

#: Committed, unlike the batches. The golden cases are the evaluation baseline
#: and have to be reviewable in a diff.
DEFAULT_GOLDEN_DIR = Path("evaluation/golden")
DEFAULT_REVIEW_ARTIFACT = REVIEW_ARTIFACT

#: Where the collected corpus actually is. Named in error messages rather than
#: used as a default: a live command that silently pointed itself at the right
#: database would also silently point itself at the wrong one.
CORPUS_HINT = Path("data/m1d2/career.db")

#: Requests per minute the live runner paces at unless told otherwise.
#: A pace, not a ceiling. The ceiling is a property of the caller's own
#: account and is resolved separately -- see `_resolve_pace`.
DEFAULT_REQUESTS_PER_MINUTE = 10


def register(app: typer.Typer) -> None:
    app.command(name="export-extraction-batch")(export_extraction_batch)
    app.command(name="import-extraction-results")(import_extraction_results)
    app.command(name="run-extraction")(run_extraction)
    app.command(name="golden-report")(golden_report)
    app.command(name="verify-cache-dispositions")(verify_cache_dispositions)


def golden_report(
    golden: Annotated[Path, typer.Option("--golden")] = DEFAULT_GOLDEN_DIR,
    out: Annotated[Path, typer.Option("--out")] = DEFAULT_REVIEW_ARTIFACT,
) -> None:
    """Write the compact review artifact for the golden set.

    Validates every case first. A label that quotes a sentence the posting does
    not contain is a defect in the label, and shipping it to a reviewer would
    waste the scarcest resource in this process -- their attention.
    """
    cases = load_cases(golden)
    broken = [(c.case_id, p) for c in cases for p in validate_case(c)]
    for case_id, problem in broken:
        typer.secho(f"{case_id}: {problem}", fg=typer.colors.RED, err=True)

    # A case in no theme is never reviewed, and nothing else would say so.
    for problem in assignment_problems(cases):
        typer.secho(f"theme assignment: {problem}", fg=typer.colors.RED, err=True)
        broken.append(("themes", problem))

    if broken:
        raise typer.Exit(code=1)

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(review_markdown(cases), encoding="utf-8")

    questions = sum(1 for c in cases for e in c.expectations if e.review_question)
    _table(
        [
            ("cases", len(cases)),
            ("assertions", sum(len(c.expectations) for c in cases)),
            ("open review questions", questions),
            ("awaiting review", sum(1 for c in cases if not c.reviewed)),
            ("written to", out),
        ]
    )


def verify_cache_dispositions(
    db: Annotated[Path, typer.Option("--db", help="The database to judge. Never inherited.")],
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Write the dispositions. Without it this only reports."),
    ] = False,
) -> None:
    """Decide, offline, which stored answers may be served again.

    MAKES NO REQUEST. Every judgement is reproduced from bytes already on disk:
    the stored answer, the archived posting text and the archived provider
    payload. A row whose source material is gone cannot be reproduced, so it
    stays UNVERIFIED and stays ineligible -- an answer nobody can check is
    exactly the answer that must not be served.

    Writes ONE column. `raw_output`, `response_envelope`, `parsed_ok`,
    `validated_ok` and `error` are never touched: the disposition is a finding
    recorded beside the evidence, not an edit to it.
    """
    conn = connect(db)
    try:
        migrate(conn)
        rows = conn.execute(
            "SELECT id, job_id, family, raw_output, parsed_ok, validated_ok FROM llm_call"
        ).fetchall()
        tally: dict[str, int] = {}
        reasons: list[tuple[str, str]] = []
        sources: dict[str, Any] = {}
        updates: list[tuple[str, str]] = []

        for row in rows:
            disposition, reason = _judge_row(conn, row, sources)
            tally[disposition] = tally.get(disposition, 0) + 1
            if reason:
                reasons.append((str(row["family"]), reason))
            updates.append((disposition, str(row["id"])))

        if apply:
            with transaction(conn):
                conn.executemany("UPDATE llm_call SET cache_disposition = ? WHERE id = ?", updates)

        _table(
            [("database", db), ("rows judged", len(rows))]
            + [(name, count) for name, count in sorted(tally.items())]
            + [("written", "yes" if apply else "NO -- pass --apply")]
        )
        if reasons:
            typer.echo("\n  why answers were refused (first 10):")
            for family, reason in reasons[:10]:
                typer.echo(f"    {family:11} {reason}")
        typer.echo("\nno network call and no model call was made by this command")
    finally:
        conn.close()


def _judge_row(conn: Any, row: Any, sources: dict[str, Any]) -> tuple[str, str | None]:
    """One row's disposition, reproduced from archived bytes alone."""
    if not row["parsed_ok"] or not row["validated_ok"]:
        return CacheDisposition.REJECTED_OUTPUT.value, None

    model = TDescriptionFamily if row["family"] == Family.DESCRIPTION.value else TProviderFamily
    try:
        payload = model.model_validate(json.loads(row["raw_output"]))
    except Exception:
        # Stored as valid and no longer parseable: a contradiction worth leaving
        # visible rather than resolving in either direction.
        return CacheDisposition.UNVERIFIED.value, "stored as valid but no longer parseable"

    job_id = str(row["job_id"])
    if job_id not in sources:
        try:
            sources[job_id] = load_source(conn, job_id)
        except Exception:
            sources[job_id] = None
    source = sources[job_id]
    if source is None:
        return CacheDisposition.UNVERIFIED.value, "no archived source material to check against"

    disposition, reason = disposition_for(payload, source.description_text, source.payload)
    return disposition.value, reason


def _audit_spend(conn: Any) -> SpendAudit:
    """Everything this database has ever spent, derived from its own rows."""
    return audit_spend(LLMCallRepo(conn).production_calls())


def _price_line(plan: LivePlan) -> str:
    """Whose price list this is, and when it was read.

    A rate printed without its date reads as a vendor fact. It is not one: it
    is what somebody saw on a documentation page on a particular day, and it is
    the caller who has to decide whether that is still true.
    """
    if plan.price is None:
        return "NONE RECORDED - a billing arm cannot run without one"
    return plan.price.describe()


def _cost(amount: float | None, free: bool) -> str:
    """A cost, or an honest refusal to state one.

    `None` prints as UNKNOWN and never as $0.00. The two are opposite claims,
    and the whole purpose of the money guard is that the second one is never
    made by accident.
    """
    if free:
        return "$0.00 (free tier, nothing billed)"
    if amount is None:
        return "UNKNOWN - no recorded price for this arm"
    return f"${amount:.4f}"


def _ceiling(plan: LivePlan) -> str:
    if plan.free:
        return "not required (free tier)"
    if plan.max_cost_usd is None:
        return "NONE - no money authorised; pass --max-cost-usd"
    return f"${plan.max_cost_usd:.2f}"


def _table(rows: list[tuple[str, Any]]) -> None:
    width = max(len(label) for label, _ in rows)
    for label, value in rows:
        typer.echo(f"  {label:<{width}} : {value}")


def _refuse_unknown_jobs(conn: Any, job_ids: list[str]) -> None:
    """Fail naming the DATABASE, not the job, when explicit ids are not there.

    `--db` defaults to `data/career.db`, which on a fresh checkout is an empty
    database at schema version 1. Point a benchmark at it by omitting one flag
    and the failure that follows is `job '01M0...' has no archived description
    text` -- which reads as a collection problem and sends someone looking in
    entirely the wrong place.

    The corpus lives in `data/m1d2/career.db`. This says so, with the row count,
    at the moment the mistake is made.
    """
    # Placeholders are generated from the count, never from the values, so the
    # ids stay parameters and this cannot become string-built SQL.
    placeholders = ",".join("?" * len(job_ids))
    known = {
        str(row["id"])
        for row in conn.execute(f"SELECT id FROM job WHERE id IN ({placeholders})", job_ids)
    }
    unknown = [identifier for identifier in job_ids if identifier not in known]
    if not unknown:
        return

    total = conn.execute("SELECT count(*) AS n FROM job").fetchone()["n"]
    raise typer.BadParameter(
        f"{len(unknown)} of {len(job_ids)} requested job(s) are not in this database, "
        f"which holds {total} job(s) in total. First missing: {unknown[0]}. "
        "The collected corpus is in data/m1d2/career.db -- pass it with --db."
    )


def _select_jobs(conn: Any, limit: int, job_ids: list[str]) -> list[str]:
    """Which postings this batch covers.

    Ordered by id rather than by recency so that the same `--limit` selects the
    same jobs on every run. A batch that quietly changed membership between two
    exports would make two development runs incomparable for no visible reason.

    Open means `closed_at IS NULL`, and the join requires archived description
    text. Both matter: a closed posting is not worth reading, and a job with no
    text would be exported as a request nobody can answer honestly.
    """
    if job_ids:
        _refuse_unknown_jobs(conn, job_ids)
        return job_ids
    rows = conn.execute(
        "SELECT j.id FROM job j JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.closed_at IS NULL AND TRIM(r.description_text) <> ''"
        " ORDER BY j.id LIMIT ?",
        (limit,),
    ).fetchall()
    return [str(row["id"]) for row in rows]


def export_extraction_batch(
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    out: Annotated[Path, typer.Option("--out")] = DEFAULT_BATCH_DIR,
    limit: Annotated[int, typer.Option("--limit", help="How many open jobs to export.")] = 5,
    job_id: Annotated[list[str] | None, typer.Option("--job-id")] = None,
    batch: Annotated[
        str | None, typer.Option("--batch", help="Batch id. Defaults to a UTC stamp.")
    ] = None,
) -> None:
    """Export production extraction requests to disk for a Cowork-assisted run.

    Makes no network request and no model call of any kind. What it writes is
    exactly what a paid runner would have been sent, prompts included.
    """
    batch_id = batch or f"batch-{now_utc().replace(':', '').replace('-', '')[:15]}"
    conn = connect(db)
    try:
        migrate(conn)
        chosen = _select_jobs(conn, limit, list(job_id or []))
        if not chosen:
            typer.secho("no open jobs to export", fg=typer.colors.YELLOW)
            raise typer.Exit(code=1)
        sources = [load_source(conn, identifier) for identifier in chosen]
    finally:
        conn.close()

    summary = export_batch(sources, out, batch_id)

    typer.echo(f"\nExported batch {summary.batch_id}")
    _table(
        [
            ("database", db),
            ("directory", summary.directory),
            ("jobs", summary.jobs),
            ("description requests", summary.description_requests),
            ("provider requests", summary.provider_requests),
            ("jobs needing no provider call", summary.jobs_needing_no_provider_call),
            ("total requests", summary.total_requests),
            ("paid calls", 0),
        ]
    )
    typer.echo(f"\nAnswer each file into {summary.directory / 'results'}; see its README.md.")


def import_extraction_results(
    batch: Annotated[Path, typer.Argument(help="The batch directory to import.")],
    db: Annotated[Path, typer.Option("--db")] = DEFAULT_DB_PATH,
    dry_run: Annotated[bool, typer.Option("--dry-run", help="Report without storing.")] = False,
) -> None:
    """Run answered requests through the production extraction path.

    Nothing here repairs a bad answer. Output that does not parse, or does not
    match the transport schema, is recorded as a failed attempt with its raw
    text preserved -- which is the result worth having before any money is
    spent on a benchmark.
    """
    try:
        imported = load_batch(batch)
    except CoworkBatchError as exc:
        typer.secho(str(exc), fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from exc

    answered = imported.answered_jobs()
    if not answered:
        typer.secho(
            f"no fully answered job in {batch}: {len(imported.unanswered)} request(s) "
            "still have no result file",
            fg=typer.colors.YELLOW,
        )
        raise typer.Exit(code=1)

    conn = connect(db)
    try:
        migrate(conn)
        sources = [load_source(conn, identifier) for identifier in answered]
        outcomes = run_batch(imported, sources)
        if not dry_run:
            with transaction(conn):
                for outcome in outcomes:
                    store_outcome(conn, outcome)
    finally:
        conn.close()

    _report(imported.batch_id, outcomes, len(imported.unanswered), dry_run)
    if any(not outcome.ok for outcome in outcomes):
        raise typer.Exit(code=1)


def _report(
    batch_id: str, outcomes: list[ExtractionOutcome], unanswered: int, dry_run: bool
) -> None:
    citations = sum(len(o.verification.results) for o in outcomes)
    unverified = sum(len(o.unverified_evidence) for o in outcomes)
    typer.echo(f"\nBatch {batch_id}{' (dry run, nothing stored)' if dry_run else ''}")
    _table(
        [
            ("jobs run", len(outcomes)),
            ("fingerprints produced", sum(1 for o in outcomes if o.ok)),
            ("failed", sum(1 for o in outcomes if not o.ok)),
            ("requests still unanswered", unanswered),
            ("model attempts recorded", sum(len(o.attempts) for o in outcomes)),
            ("citations checked", citations),
            ("citations unverified", unverified),
            (
                "corrections applied",
                sum(len(o.validation.corrections) for o in outcomes if o.validation),
            ),
            ("paid calls", sum(o.paid_calls for o in outcomes)),
        ]
    )

    for outcome in outcomes:
        if not outcome.ok:
            typer.secho(f"  FAILED {outcome.job_id}: {outcome.failure}", fg=typer.colors.RED)

    typer.echo(
        "\nThese are development fingerprints (runner COWORK_ASSISTED, model "
        "cowork-development). They are not benchmark results for any production model."
    )


# =========================================================================
# THE LIVE RUNNER
#
# The only command in this repository that can reach a vendor. Everything
# below is selection, arithmetic and refusal; the extraction itself is
# `pipeline/extract.extract_job`, unchanged and unaware that a benchmark is
# what is driving it.
# =========================================================================


def _resolve_output_modes(
    plain: list[str],
    json_object: list[str] | None = None,
    function_call: list[str] | None = None,
) -> tuple[tuple[Family, StructuredOutput], ...]:
    """Which families this arm sends without asking the vendor to enforce a schema.

    Per family because the constraint is per family. Cerebras caps a strict
    schema at 5,000 characters; our provider schema is 4,038 and our description
    schema 8,536, so the best production-usable configuration of that route
    enforces one and not the other. Weakening both for symmetry would benchmark
    a setup nobody would ship.

    The mode reaches the cache through `static_digest` and `llm_call` through
    its own column, so an answer given without enforcement can never be served
    as one given with it.
    """
    known = {family.value: family for family in Family}
    modes: list[tuple[Family, StructuredOutput]] = []
    chosen: dict[Family, str] = {}
    for flag, mode, names in (
        ("--plain-json", StructuredOutput.PLAIN_JSON, plain),
        ("--json-object", StructuredOutput.JSON_OBJECT, json_object or []),
        ("--function-call", StructuredOutput.FUNCTION_CALL, function_call or []),
    ):
        for name in names:
            family = known.get(name.strip().lower())
            if family is None:
                raise typer.BadParameter(
                    f"{flag} {name!r} is not a family; expected {sorted(known)}"
                )
            if family in chosen:
                raise typer.BadParameter(
                    f"{family.value} was given two output modes ({chosen[family]} and {flag}). "
                    "One family has one mode, and it is part of the arm's identity."
                )
            chosen[family] = flag
            modes.append((family, mode))
    return tuple(modes)


def _resolve_selection(
    golden: Path | None, job_ids: list[str], screen: bool = False
) -> tuple[list[str], str]:
    """Which postings this run covers, and how they were chosen.

    Refuses an empty selection outright, in preflight as well as in execute
    mode. There is no "everything" here and there is not going to be: this
    corpus holds 18,550 postings, and a command whose default is a full-corpus
    run is a command that will eventually make one by accident.
    """
    chosen: list[str] = []
    described: list[str] = []

    if screen:
        # The membership is read from the frozen artifact, never assembled here.
        # A screen whose cases a command could choose is a screen that can be
        # narrowed after a bad result, which is the one thing it exists to stop.
        definition = load_screen()
        problems = validate_screen(definition, DEFAULT_GOLDEN_DIR)
        if problems:
            raise typer.BadParameter("; ".join(problems))
        by_case = {
            case.case_id: str(case.meta["job_id"]) for case in load_cases(DEFAULT_GOLDEN_DIR)
        }
        chosen.extend(by_case[case_id] for case_id in definition.case_ids)
        described.append(
            f"free-challenger screen ({len(definition.cases)} cases, frozen {definition.frozen_at})"
        )

    if golden is not None:
        if not golden.is_dir():
            raise typer.BadParameter(f"{golden} is not a directory of golden cases")
        try:
            resolved = golden_job_ids(golden)
        except GoldenError as exc:
            raise typer.BadParameter(str(exc)) from exc
        chosen.extend(resolved)
        described.append(f"golden set ({golden}, {len(resolved)} cases)")

    for identifier in job_ids:
        if identifier not in chosen:
            chosen.append(identifier)
    if job_ids:
        described.append(f"{len(job_ids)} explicit --job-id")

    if not chosen:
        raise typer.BadParameter(
            "no postings selected. Pass --golden-set and/or --job-id. An omitted selection "
            "is never read as every job in the database: a full-corpus extraction is a "
            "separate, explicitly authorised decision and this command cannot make it."
        )
    return chosen, " + ".join(described)


def _resolve_database(db: Path | None, execute: bool) -> Path:
    """The database this run reads and writes, refusing to guess when it matters.

    `--db` defaults to `data/career.db`, which is empty at schema version 1.
    That is a fine default for a command that only writes files. For a command
    that can spend money it is a trap, so live execution has to name the
    database rather than inherit one.
    """
    if db is not None:
        return db
    if execute:
        raise typer.BadParameter(
            "live execution requires an explicit --db. The default is data/career.db, "
            f"which is empty; the collected corpus is in {CORPUS_HINT}."
        )
    return DEFAULT_DB_PATH


@dataclass(frozen=True)
class ResolvedPace:
    """The numbers the limiter will be given, and where each one came from."""

    requests_per_minute: int
    ceiling_rpm: int
    ceiling_tpm: int
    ceiling_rpd: int | None
    #: The dated observation these ceilings defaulted from, if they did.
    observation: QuotaLimits | None
    #: True when the caller stated the ceilings rather than inheriting them.
    supplied_by_caller: bool

    @property
    def provenance(self) -> str:
        if self.supplied_by_caller:
            return "supplied by --ceiling-rpm / --ceiling-tpm"
        assert self.observation is not None
        return self.observation.describe()


def _resolve_pace(
    vendor: str,
    model: str,
    rpm: int,
    ceiling_rpm: int | None,
    ceiling_tpm: int | None,
    ceiling_rpd: int | None,
) -> ResolvedPace:
    """The ceilings this run is held to, and the refusal when nobody stated them.

    **The ceilings belong to an account, not to a vendor.** Gemini's free-tier
    limits are per model, differ between an AI Studio key and a Cloud project,
    and change without telling this repository. So they are caller-supplied, and
    `llm/quotas.py` only defaults them for the exact (vendor, model) pair
    somebody once read them for -- with the date and the account attached, both
    of which the preflight prints.

    A pair with no recorded observation is not guessed at. Being asked for two
    numbers is a much better outcome than pacing against a stranger's quota.

    Refuses to pace *at* the request ceiling. There, one retry is an overage.
    """
    observed = observed_quota(vendor, model)
    supplied = ceiling_rpm is not None or ceiling_tpm is not None

    resolved_rpm = (
        ceiling_rpm
        if ceiling_rpm is not None
        else (observed.requests_per_minute if observed else None)
    )
    resolved_tpm = (
        ceiling_tpm
        if ceiling_tpm is not None
        else (observed.tokens_per_minute if observed else None)
    )
    resolved_rpd = (
        ceiling_rpd
        if ceiling_rpd is not None
        else (observed.requests_per_day if observed else None)
    )

    if resolved_rpm is None or resolved_tpm is None:
        raise typer.BadParameter(
            f"no rate limits are recorded for {vendor}/{model}, and none were supplied. "
            "Pass --ceiling-rpm and --ceiling-tpm with the limits YOUR project actually "
            "has. They are not a property of the vendor: they differ by model, by "
            "project and by tier, and a guessed ceiling is worse than being asked."
        )
    if resolved_rpm < 1 or resolved_tpm < 1:
        raise typer.BadParameter("a rate ceiling must be at least 1")
    if rpm >= resolved_rpm:
        raise typer.BadParameter(
            f"--rpm {rpm} is at or above the ceiling of {resolved_rpm} in force for this "
            f"run. Pace below it: at the ceiling one retry is an overage."
        )

    return ResolvedPace(
        requests_per_minute=rpm,
        ceiling_rpm=resolved_rpm,
        ceiling_tpm=resolved_tpm,
        ceiling_rpd=resolved_rpd,
        observation=observed,
        supplied_by_caller=supplied,
    )


def _preflight_table(
    plan: LivePlan,
    pace: ResolvedPace,
    *,
    selection: str,
    version: int,
    credential: str,
    execute: bool,
    audit: SpendAudit,
) -> None:
    minutes = plan.nominal_live_calls / plan.requests_per_minute if plan.requests_per_minute else 0
    daily = (
        f"{plan.nominal_live_calls} of {pace.ceiling_rpd} "
        f"({plan.nominal_live_calls / pace.ceiling_rpd:.0%})"
        if pace.ceiling_rpd
        else "no daily ceiling recorded or supplied"
    )

    typer.echo("\nPREFLIGHT - run-extraction")
    _table(
        [
            ("database", plan.database),
            ("schema version", version),
            ("vendor", plan.config.vendor),
            ("model", plan.config.identifier),
            ("model identity (cache arm)", plan.config.arm),
            ("reasoning", plan.config.reasoning or "not set"),
            ("schema enforcement, description", plan.config.output_mode(Family.DESCRIPTION).value),
            ("schema enforcement, provider", plan.config.output_mode(Family.PROVIDER).value),
            ("selection", selection),
            ("jobs selected", len(plan.sources)),
            ("description requests", plan.description_calls),
            ("provider requests", plan.provider_calls),
            ("jobs needing no provider call", plan.jobs_needing_no_provider_call),
            ("cache hits (already answered)", plan.cache_hits),
            ("expected live calls (nominal)", plan.nominal_live_calls),
            ("worst case, every retry used", plan.worst_case_live_calls),
            ("maximum authorised live calls", plan.max_live_calls),
            (
                "programme budget remaining",
                f"{plan.ledger.remaining} of {plan.ledger.authorization.max_requests} "
                f"({plan.ledger.authorization.authorization_id})",
            ),
            ("effective ceiling this run", plan.effective_max_live_calls),
            (
                "attempts per request",
                f"{plan.max_attempts} ({plan.max_attempts - 1} retr"
                f"{'y' if plan.max_attempts == 2 else 'ies'} permitted)",
            ),
            ("price in force", _price_line(plan)),
            ("estimated cost (nominal)", _cost(plan.estimated_cost, plan.free)),
            ("estimated cost, every retry used", _cost(plan.worst_case_cost, plan.free)),
            ("maximum authorised spend", _ceiling(plan)),
            ("prototype accounting", audit.describe()),
            ("personal cash remaining", f"${audit.remaining_cash_usd:.4f}"),
            ("estimated input tokens", f"{plan.input_tokens:,} (upper bound)"),
            ("estimated output tokens", f"{plan.output_tokens:,} (measured median)"),
            ("requests per minute", f"{plan.requests_per_minute} (ceiling {pace.ceiling_rpm})"),
            ("tokens per minute ceiling", f"{pace.ceiling_tpm:,}"),
            ("peak input tokens per minute", f"{plan.peak_tokens_per_minute:,} (upper bound)"),
            ("share of the daily ceiling", daily),
            # The ceilings above are somebody's, and the run says whose. They
            # are not a vendor's policy and must never be printed as though
            # they were: they differ by model, by project and by tier.
            ("rate ceilings are", pace.provenance),
            ("estimated pacing time", f"~{minutes:.1f} min"),
            ("credential", credential),
            ("execute / live", "true" if execute else "false"),
        ]
    )
    if not plan.fully_estimated:
        typer.secho(
            "  note: a prompt version in this run has no measured token count; its static "
            "half was estimated from characters",
            fg=typer.colors.YELLOW,
        )


def run_extraction(
    vendor: Annotated[str, typer.Option("--vendor", help="Which vendor adapter to use.")],
    model: Annotated[str, typer.Option("--model", help="The vendor's model identifier.")],
    max_live_calls: Annotated[
        int,
        typer.Option(
            "--max-live-calls",
            help="Hard ceiling on live vendor calls. Retries count against it.",
        ),
    ],
    db: Annotated[
        Path | None,
        typer.Option("--db", help="Required for --execute; never inherited."),
    ] = None,
    reasoning: Annotated[
        str | None,
        typer.Option("--reasoning", help="Reasoning setting; part of the model identity."),
    ] = None,
    phase: Annotated[
        str,
        typer.Option(
            "--phase",
            help="Which programme phase is spending. Recorded on every reservation.",
        ),
    ] = "UNSPECIFIED",
    job_id: Annotated[list[str] | None, typer.Option("--job-id")] = None,
    screen: Annotated[
        bool,
        typer.Option(
            "--screen",
            help="Run the frozen free-challenger screen instead of naming cases.",
        ),
    ] = False,
    plain_json: Annotated[
        list[str] | None,
        typer.Option(
            "--plain-json",
            help="Family to send WITHOUT vendor schema enforcement "
            "(description / provider). Repeatable; part of the cache identity.",
        ),
    ] = None,
    json_object: Annotated[
        list[str] | None,
        typer.Option(
            "--json-object",
            help="Family to send in JSON mode: one valid JSON document guaranteed, "
            "shape unenforced. For routes that advertise response_format and not "
            "structured_outputs. Repeatable; part of the cache identity.",
        ),
    ] = None,
    function_call: Annotated[
        list[str] | None,
        typer.Option(
            "--function-call",
            help=(
                "Family to send as a forced tool call (description / provider). The answer "
                "arrives as function arguments rather than as text. Repeatable; part of the "
                "cache identity."
            ),
        ),
    ] = None,
    golden_set: Annotated[
        Path | None,
        typer.Option("--golden-set", help="Resolve the postings from a golden case directory."),
    ] = None,
    rpm: Annotated[
        int,
        typer.Option("--rpm", help="Requests per minute to pace at. Must be below the ceiling."),
    ] = DEFAULT_REQUESTS_PER_MINUTE,
    ceiling_rpm: Annotated[
        int | None,
        typer.Option(
            "--ceiling-rpm",
            help="YOUR project's requests-per-minute limit. Defaults to a dated observation.",
        ),
    ] = None,
    ceiling_tpm: Annotated[
        int | None,
        typer.Option(
            "--ceiling-tpm",
            help="YOUR project's input-tokens-per-minute limit.",
        ),
    ] = None,
    ceiling_rpd: Annotated[
        int | None,
        typer.Option("--ceiling-rpd", help="YOUR project's daily limit. Reported, never paced."),
    ] = None,
    max_cost_usd: Annotated[
        float | None,
        typer.Option(
            "--max-cost-usd",
            help="Hard ceiling on money, in USD. Required to spend anything on a billing arm.",
        ),
    ] = None,
    max_attempts: Annotated[
        int,
        typer.Option(
            "--max-attempts",
            help="Attempts per semantic request. 1 means no retries. Distinct from "
            "--max-live-calls, which bounds provider HTTP requests for the whole run.",
        ),
    ] = MAX_ATTEMPTS,
    allow_partial_run: Annotated[
        bool,
        typer.Option(
            "--allow-partial-run",
            help="Permit a budget smaller than the plan. The run stops at the ceiling "
            "and what it finished is kept and reusable.",
        ),
    ] = False,
    execute: Annotated[
        bool,
        typer.Option(
            "--execute",
            help="Actually call the vendor. Without it this command is arithmetic.",
        ),
    ] = False,
) -> None:
    """Extract postings through a real vendor - or, by default, only price what that would cost.

    Without `--execute` nothing leaves the machine: no client is constructed, no
    credential value is read, and the summary is computed from the database and
    the prompt files alone. A configured API key is not an instruction to use it.

    The rate ceilings belong to the caller's own account. `--ceiling-rpm` and
    `--ceiling-tpm` state them; a dated observation in `llm/quotas.py` defaults
    them for the exact (vendor, model) pair somebody once read them for, and the
    preflight prints whose limits are in force either way.
    """
    load_dotenv()

    if vendor not in available_vendors():
        raise typer.BadParameter(
            f"no adapter for vendor {vendor!r}; available: {', '.join(available_vendors())}"
        )
    if max_live_calls < 1:
        raise typer.BadParameter("--max-live-calls must be at least 1")
    if max_attempts < 1:
        raise typer.BadParameter("--max-attempts must be at least 1")

    chosen, selection = _resolve_selection(golden_set, list(job_id or []), screen)
    database = _resolve_database(db, execute)
    pace = _resolve_pace(vendor, model, rpm, ceiling_rpm, ceiling_tpm, ceiling_rpd)
    config = ModelConfig(
        vendor=vendor,
        identifier=model,
        reasoning=reasoning,
        per_family=_resolve_output_modes(
            list(plain_json or []), list(json_object or []), list(function_call or [])
        ),
    )

    present = credential_variables_present(vendor)
    credential = f"PRESENT ({', '.join(present)})" if present else "MISSING"

    conn = connect(database)
    try:
        migrate(conn)
        version = schema_version(conn)
        _refuse_unknown_jobs(conn, chosen)
        try:
            sources = [load_source(conn, identifier) for identifier in chosen]
        except LookupError as exc:
            raise typer.BadParameter(str(exc)) from exc

        # The standing authorisation, read from disk. Not a flag: `--max-live-calls`
        # narrows this invocation and nothing widens the programme. Seeding it here,
        # before the preflight, is what lets the preflight print a budget that has
        # already spent what earlier phases spent.
        ledger = BudgetLedger.for_authorization(PROGRAM_AUTHORIZATION)
        plan = plan_run(
            conn,
            sources,
            config,
            database=database,
            requests_per_minute=pace.requests_per_minute,
            tokens_per_minute=pace.ceiling_tpm,
            max_live_calls=max_live_calls,
            ledger=ledger,
            max_attempts=max_attempts,
            max_cost_usd=max_cost_usd,
        )
        audit = _audit_spend(conn)
        _preflight_table(
            plan,
            pace,
            selection=selection,
            version=version,
            credential=credential,
            execute=execute,
            audit=audit,
        )

        # The authorisation test is the nominal count, not the worst case.
        # Requiring every possible retry to fit would mean 52 planned calls
        # could not be approved under 104 - a number nobody is being asked to
        # approve, and one the hard ceiling makes unnecessary to approve.
        if execute and not plan.free:
            # The cumulative ceiling, checked before the per-run one. A run that
            # fits its own budget and not the prototype's is still refused: the
            # $10 is the number that was actually agreed, and it spans every arm
            # and every run rather than resetting per command.
            nominal_cost = plan.estimated_cost
            if nominal_cost is not None and audit.cash_usd + nominal_cost > CEILING:
                raise typer.BadParameter(
                    f"the Personal Alpha paid ceiling of ${CEILING:.2f} would be exceeded: "
                    f"{audit.describe()}, and this run is estimated at ${nominal_cost:.4f}."
                )
            if not audit.complete:
                raise typer.BadParameter(
                    "the cumulative spend cannot be established, so a paid run cannot be "
                    f"authorised against a ceiling: {audit.describe()}. Unpriceable arms: "
                    f"{', '.join(audit.unknown_arms)}."
                )

            # The money gate, stated the way the four call gates are: a thing
            # the caller had to say out loud. A billing arm with no recorded
            # price cannot be run at all, and one with no stated ceiling has
            # been authorised to spend nothing -- never "unlimited".
            if plan.price is None:
                raise typer.BadParameter(
                    f"no recorded price for ({vendor}, {model}), so this run cannot be held to "
                    "a spend ceiling. Record a dated observation in llm/pricing.py first: a run "
                    "nobody can price afterwards is a run nobody can defend."
                )
            if plan.max_cost_usd is None:
                raise typer.BadParameter(
                    "this arm bills. --max-cost-usd is required for --execute, and its absence "
                    "means no money is authorised rather than unlimited money."
                )
            nominal = plan.estimated_cost
            if nominal is not None and nominal > plan.max_cost_usd:
                raise typer.BadParameter(
                    f"the plan is estimated at ${nominal:.4f} and ${plan.max_cost_usd:.2f} is "
                    "authorised. Raise --max-cost-usd deliberately, or narrow the selection."
                )

        refusal = PROGRAM_AUTHORIZATION.refusal_for(config, phase)
        if refusal is not None and execute:
            typer.secho(f"\nREFUSED: {refusal}", fg=typer.colors.RED, err=True)
            raise typer.Exit(code=1)

        if plan.nominal_live_calls > plan.ledger.remaining and not allow_partial_run:
            typer.secho(
                f"\nREFUSED: this run plans {plan.nominal_live_calls} live call(s) and "
                f"authorisation {PROGRAM_AUTHORIZATION.authorization_id} has "
                f"{plan.ledger.remaining} of {PROGRAM_AUTHORIZATION.max_requests} remaining. "
                "The programme budget cannot be raised from the command line.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

        if plan.nominal_live_calls > plan.max_live_calls and not allow_partial_run:
            typer.secho(
                f"\nREFUSED: this run plans {plan.nominal_live_calls} live call(s) and only "
                f"{plan.max_live_calls} are authorised. Raise --max-live-calls deliberately, "
                "narrow the selection, or pass --allow-partial-run to stop at the ceiling on "
                "purpose -- finished postings are stored and a later run resumes from them.",
                fg=typer.colors.RED,
                err=True,
            )
            raise typer.Exit(code=1)

        if not execute:
            typer.echo("\nDRY RUN. No client was constructed, no credential value was read,")
            typer.echo("no network call and no model call was made.")
            typer.echo("Add --execute to run it for real.")
            return

        if not present:
            raise _no_credential(vendor)

        typer.secho(
            f"\nLIVE. Up to {plan.effective_max_live_calls} call(s) to {vendor} may now be "
            f"made ({plan.ledger.remaining} left on "
            f"{PROGRAM_AUTHORIZATION.authorization_id}).",
            fg=typer.colors.YELLOW,
        )
        report = run_plan(
            conn,
            plan,
            get_client(vendor),
            RateLimiter(
                requests_per_minute=plan.requests_per_minute,
                tokens_per_minute=plan.tokens_per_minute,
            ),
            phase=phase,
        )
    finally:
        conn.close()

    _live_report(report, plan)
    if report.failures or report.stopped_early:
        raise typer.Exit(code=1)


def _no_credential(vendor: str) -> typer.Exit:
    """Refuse a live run with no credential, naming the variables and no value."""
    names = ", ".join(CREDENTIAL_VARIABLES.get(vendor, ())) or "(none recorded)"
    typer.secho(
        f"\nREFUSED: no credential is set for {vendor}. Expected one of: {names}. "
        "Presence only - no value is read, printed or logged by this command.",
        fg=typer.colors.RED,
        err=True,
    )
    return typer.Exit(code=1)


def _live_report(report: LiveRunReport, plan: LivePlan) -> None:
    typer.echo(f"\nRun complete against {plan.database}")
    _table(
        [
            ("jobs planned", report.jobs_planned),
            ("jobs run", report.jobs_run),
            ("fingerprints produced", report.fingerprints),
            ("failed", report.failures),
            ("LIVE MODEL CALLS", report.live_calls),
            ("CACHE HITS / REUSE", report.cache_hits),
            ("maximum authorised live calls", report.max_live_calls),
            (
                "programme budget remaining",
                f"{plan.ledger.remaining} of {plan.ledger.authorization.max_requests}",
            ),
            ("REPORTED SPEND", _cost(report.spent_usd, plan.free)),
            ("LOCAL FAILURES (no HTTP, no quota)", report.local_failures),
            ("reported input tokens", f"{report.input_tokens:,}"),
            ("reported output tokens", f"{report.output_tokens:,}"),
            ("seconds spent pacing", f"{report.waited_seconds:.1f}"),
        ]
    )
    for outcome in report.outcomes:
        if not outcome.ok:
            typer.secho(f"  FAILED {outcome.job_id}: {outcome.failure}", fg=typer.colors.RED)
    if report.stopped_early:
        typer.secho(f"\nSTOPPED EARLY: {report.stopped_early}", fg=typer.colors.YELLOW)

"""Find Jobs -> Deep reading: provider, consent, preview, run, results.

Nothing here runs because a setting changed. A job starts only from
`POST /api/deep/run` with `confirm: true`, after a preview the person saw, on
the provider this profile chose BY NAME in the panel (never Auto, never a
fallback to another provider or model). A subscription command line spends
plan limits; a metered API (DeepSeek) runs only with a budget the person
approved, enforced by `deep_run` call by call. Consent to share evidence is
per profile AND per provider; a configured key authorises nothing. Every
response carries codes the interface words; no credential is ever read
beyond "is one configured", and none is ever returned.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import closing
from typing import TYPE_CHECKING, Any

from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi

#: What the panel proposes when the person has not chosen a number: one
#: batch. Not a ceiling; any number up to what is available can be approved.
DEFAULT_LIMIT = 25
#: The providers a deep reading can run on, in the panel's order. Others
#: (a local model, Auto) are not offered: none of them has been measured on
#: this contract.
DEEP_PROVIDERS = ("claude_code", "codex", "deepseek")


def _bool(value: Any) -> bool:
    return value is True or value in ("1", "true")


def register_deep(app: JobsApi) -> None:
    from career_agent.pipeline.retrieval import RetrievalRunner
    from career_agent.web.semantic_api import _is_personal, profile_settings

    runner = RetrievalRunner()
    app.deep_runner = runner
    lock = threading.Lock()
    last: dict[str, Any] = {}
    live: dict[str, Any] = {}
    pause = threading.Event()
    #: The main list's rows (`deep_run.population_rows`), remembered with the
    #: key they were read under. The key is everything the rows depend on: the
    #: profile's database, the score revision the list serves, the
    #: configuration's digest (hidden levels) and `_jobs_freshness`, which
    #: moves with every posting or score written (by this process or another),
    #: every hide or status change, every write this server accepted and the
    #: day. A key that moved reads again; nothing else expires it.
    rows_memo: dict[str, Any] = {}
    rows_lock = threading.Lock()

    def chosen_id(settings: Any) -> str | None:
        """The provider this profile chose for the deep reading, by name, or
        none. Never derived from the Smart matching choice: each function has
        its own provider, and configuring one must not move the other."""
        return str(settings.deep_provider) if settings.deep_provider else None

    def chosen_provider(settings: Any) -> tuple[Any, str]:
        """The profile's NAMED provider, or why there is none to use."""
        provider_id = chosen_id(settings)
        if provider_id is None:
            return None, "CHOOSE_PROVIDER"
        return _instance(provider_id), ""

    def available_for(
        provider: Any, settings: Any, include_unresolved: bool, retry_rejected: bool = False
    ) -> int:
        return selection_for(provider, settings, include_unresolved, retry_rejected).available

    def scores_of(conn: Any) -> tuple[tuple[str, int] | None, dict[str, Any]]:
        """The revision of match scores the Find jobs list is built from, and
        how it relates to the one in force. The list serves the previous
        revision until a recalculation finishes; the deep reading reads the
        same list, so it reads the same scores."""
        decision = app._serving(conn)
        served = decision.serving
        info = {
            "state": "none" if served is None else "current" if decision.is_current else "older",
            "serving_version": served.config_version if served else None,
            "current_version": decision.current.config_version,
            "building": decision.is_building,
        }
        return ((served.config_id, served.config_version) if served else None), info

    def age_of(conn: Any) -> tuple[int | None, bool]:
        """The profile's age limit on postings, the one Find jobs applies."""
        from career_agent.web import view_prefs

        prefs = view_prefs.read(conn)
        return prefs["max_age_days"], bool(prefs["include_undated"])

    def main_rows(conn: Any, scores: tuple[str, int]) -> list[Any]:
        """The main list's rows for `scores`, read once per change of the data."""
        from career_agent.semantic.deep_run import population_rows

        config = app.search_config()
        key = (
            str(app.config.db_path),
            scores,
            str(getattr(config, "digest", "")),
            app._jobs_freshness(conn),
        )
        with rows_lock:
            if rows_memo.get("key") == key:
                return list(rows_memo["rows"])
        rows = population_rows(conn, config, scores)
        with rows_lock:
            rows_memo.update(key=key, rows=rows)
        return list(rows)

    def selection_for(
        provider: Any,
        settings: Any,
        include_unresolved: bool,
        retry_rejected: bool = False,
        conn: Any = None,
    ) -> Any:
        from career_agent.semantic.deep_run import Selection, context, select

        if conn is None:
            with closing(app.connect()) as own:
                return selection_for(provider, settings, include_unresolved, retry_rejected, own)
        share = provider.id in settings.share_evidence_with
        scores, _info = scores_of(conn)
        if scores is None:
            return Selection([], 0, 0, 0, 0)
        ctx = context(conn, app.search_config(), share)
        return select(
            conn,
            app.search_config(),
            provider_id=provider.id,
            limit=0,
            include_unresolved=include_unresolved,
            current_digest=ctx.digest,
            model=_model(provider),
            retry_rejected=retry_rejected,
            scores=scores,
            age=age_of(conn),
            rows=main_rows(conn, scores),
            share=share,
        )

    def limits(body: dict, provider: Any, settings: Any) -> tuple[int, bool, int, bool]:
        include_unresolved = _bool(body.get("include_unresolved", False))
        retry = _bool(body.get("retry_rejected", False))
        available = available_for(provider, settings, include_unresolved, retry)
        if available < 1:
            # Nothing to read: say so, never start an empty reading. The page
            # words the reason from `status`; nothing was sent.
            raise ApiError(409, "No readable posting is available.", code="NO_POSTINGS")
        limit = body.get("limit", min(DEFAULT_LIMIT, available))
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= available:
            raise ApiError(
                400, "limit out of range", code="LIMIT_OUT_OF_RANGE", data={"available": available}
            )
        return limit, include_unresolved, available, retry

    def budget_of(body: dict, provider: Any) -> float | None:
        """The approved most-to-spend for a metered provider, in USD."""
        from career_agent.semantic.providers import Billing

        if provider.capabilities().billing is not Billing.METERED_API:
            return None
        value = body.get("budget_usd")
        if (
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not 0 < float(value) <= 100
        ):
            raise ApiError(400, "budget_usd must be more than 0 and at most 100", for_reader=True)
        return round(float(value), 4)

    def status(*, query: dict, body: dict) -> dict:
        """The panel itself: provider, consent, the last job and run. Fast.

        The counts of the main list are `GET /api/deep/coverage`, asked for
        beside this one, so the panel and its controls are on screen at once
        and only the numbers wait. `coverage_ok` is None here: not counted
        YET, which the page says, never a zero.
        """
        from career_agent.semantic.store import SemanticRepo
        from career_agent.web.profiles import served_profile_id

        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        state = auth = None
        if provider is not None and _is_personal(app):
            checked = provider.availability()  # local: `claude auth status`, sends nothing
            state, auth = checked.state.value, checked.facts.get("auth")
        with closing(app.connect()) as conn:
            last_run = SemanticRepo(conn).last_run(deep=True)
            rows = _review_rows(app, conn)
            job = _job_summary(app, conn, chosen_id(settings))
            scores_info: dict[str, Any] = {}
            age: tuple[int | None, bool] = (120, False)
            try:
                _scores, scores_info = scores_of(conn)
                age = age_of(conn)
            except sqlite3.Error as exc:
                app.log(f"deep status failed: {type(exc).__name__}: {exc}")
        current = {r["job_id"] for r in rows if not r["stale"]}
        stale = sum(1 for r in rows if r["stale"])
        return {
            "profile": served_profile_id(app),
            "coverage_ok": None,
            "scores": scores_info,
            "age_limit": {"days": age[0], "include_undated": age[1]},
            "demo": not _is_personal(app),
            "provider": provider.id if provider else None,
            "provider_reason": reason,
            "provider_state": state,
            "auth": auth,
            "share_evidence": bool(provider and provider.id in settings.share_evidence_with),
            "cap": settings.max_jobs_per_subscription_run,
            "reviewed": len(current),
            "stale": stale,
            "last_run": last_run,
            "run": runner.snapshot(),
            "providers": _providers(settings, chosen_id(settings)) if _is_personal(app) else [],
            "budget_default": settings.deep_budget_usd,
            "job": job,
            "live": dict(live),
        }

    def coverage_status(*, query: dict, body: dict) -> dict:
        """How much of the MAIN LIST a reading can reach, never the catalogue.

        One read of the list (`main_rows`, remembered until the data moves)
        gives every number: roles with and without the age limit, readable
        and too short, and what the chosen provider could still read with and
        without unresolved postings. A failed count says so (`coverage_ok`
        false) and carries no number, so the page never shows a zero for it.
        """
        from career_agent.semantic.deep_run import Coverage, coverage
        from career_agent.web.profiles import served_profile_id

        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        settings = profile_settings(app)
        provider, _reason = chosen_provider(settings)
        profile = served_profile_id(app)
        try:
            with closing(app.connect()) as conn:
                scores, scores_info = scores_of(conn)
                age = age_of(conn)
                too_old = 0
                if scores is None:
                    reach, main_list = Coverage(0, 0), list[Any]()
                else:
                    rows = main_rows(conn, scores)
                    config = app.search_config()
                    reach, main_list = coverage(conn, config, False, scores, age, rows=rows)
                    if age[0] is not None:
                        everything, _ = coverage(
                            conn, config, False, scores, (None, False), rows=rows
                        )
                        too_old = max(0, everything.roles - reach.roles)
                reviews = _review_rows(app, conn)
                counts: dict[str, Any] = {}
                unavailable = None
                if provider is not None:
                    counts = _counts(selection_for(provider, settings, False, conn=conn))
                    counts["available_unresolved"] = selection_for(
                        provider, settings, True, conn=conn
                    ).available
                    if counts["available"] < 1:
                        unavailable = _why_none(scores_info, reach, counts["available_unresolved"])
        except sqlite3.Error as exc:
            # A failed count is not a count of zero: the page says the check
            # failed instead of showing an empty list as a result.
            app.log(f"deep coverage failed: {type(exc).__name__}: {exc}")
            return {"profile": profile, "coverage_ok": False}
        current = {r["job_id"] for r in reviews if not r["stale"]}
        return {
            "profile": profile,
            "coverage_ok": True,
            "scores": scores_info,
            # The age limit this reading respects (the same as Find jobs), and
            # how many roles of the main list it leaves out.
            "age_limit": {"days": age[0], "include_undated": age[1]},
            "excluded_by_age": too_old,
            "unavailable": unavailable,
            # The MAIN LIST only, never the catalogue: roles in it, roles with
            # enough text to read, and roles whose advert is too short.
            "main_list_roles": reach.roles,
            "readable": reach.readable,
            "too_short": reach.too_short,
            # Readable roles with no CURRENT reading: still in the list, in
            # the local order, never ranked below the ones that have one.
            "not_reviewed": sum(1 for r in main_list if str(r["id"]) not in current),
            **counts,
        }

    def choose(*, query: dict, body: dict) -> dict:
        """Name the provider this profile's deep reading uses. Sends nothing,
        shares nothing: consent stays per provider and starts off."""
        from career_agent.semantic.settings import SettingsError, save_settings

        if query or set(body) != {"provider"} or body["provider"] not in DEEP_PROVIDERS:
            raise ApiError(
                400, "provider must be one of " + ", ".join(DEEP_PROVIDERS), for_reader=True
            )
        if runner.running:
            raise ApiError(409, "A deep reading is running.", code="RUN_ACTIVE")
        try:
            with closing(app.connect()) as conn:
                saved = save_settings(
                    app.config.config_dir, {"deep_provider": body["provider"]}, conn
                )
        except SettingsError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        return {"provider": saved.deep_provider}

    def preview(*, query: dict, body: dict) -> dict:
        """What a run WOULD send, computed locally. Nothing leaves."""
        from career_agent.semantic import deep
        from career_agent.semantic.deep_run import (
            context,
            coverage,
            estimate_tokens,
            request_for,
            select,
        )

        if query or set(body) - {"limit", "include_unresolved", "budget_usd", "retry_rejected"}:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        if provider is None:
            return {"provider": None, "reason": reason}
        limit, include_unresolved, available, retry = limits(body, provider, settings)
        budget = budget_of(body, provider)
        share = provider.id in settings.share_evidence_with
        config = app.search_config()
        with closing(app.connect()) as conn:
            scores, scores_info = scores_of(conn)
            age = age_of(conn)
            ctx = context(conn, config, share)
            rows = main_rows(conn, scores) if scores is not None else None
            chosen = select(
                conn,
                config,
                provider_id=provider.id,
                limit=limit,
                include_unresolved=include_unresolved,
                current_digest=ctx.digest,
                model=_model(provider),
                retry_rejected=retry,
                scores=scores,
                age=age,
                rows=rows,
                share=share,
            )
            names = _companies(conn, [c.job_id for c in chosen.candidates])
            reach, _rows = coverage(conn, config, include_unresolved, scores, age, rows=rows)
        from career_agent.semantic.deep_run import (
            BATCH_SIZE,
            estimate,
            max_output_for,
            price_for,
            system_for,
        )

        system = system_for(provider)
        messages: list[str] = []
        example: list[dict[str, Any]] = []
        evidence_counts: list[int] = []
        notes = []
        for candidate in chosen.candidates:
            message, items, _sent, note = request_for(ctx, candidate)
            messages.append(message)
            evidence_counts.append(len(items))
            notes.append(note)
            if not example:
                example = items
        tokens = sum(estimate_tokens(system + m) for m in messages)
        cost = estimate(provider, system, messages, max_output_for(provider))
        price = price_for(provider)
        per_worst = (cost.worst_usd or 0.0) / max(len(messages), 1)
        per_typical = (cost.typical_usd or 0.0) / max(len(messages), 1)
        return {
            "provider": provider.id,
            "model": str(getattr(provider, "model", "") or ""),
            "billing": provider.capabilities().billing.value,
            "available": available,
            "scores": scores_info,
            "age_limit": {"days": age[0], "include_undated": age[1]},
            # Answers paid for and rejected: asked again only on request,
            # and each retry is paid again.
            "rejected": chosen.rejected,
            "retry_rejected": retry,
            "requested": limit,
            "batch_size": BATCH_SIZE,
            "budget_usd": budget,
            "estimate": {
                "input_tokens": cost.input_tokens,
                "input_tokens_ceiling": cost.input_tokens_ceiling,
                "output_tokens_typical": cost.output_tokens_typical,
                "output_tokens_ceiling": cost.output_tokens_ceiling,
                "typical_usd": cost.typical_usd,
                "off_peak_usd": cost.off_peak_usd,
                "worst_usd": cost.worst_usd,
                # How many postings the budget covers, at the typical cost and
                # at the reserved worst case. The job pauses, never overspends.
                "covers_typical": int((budget or 0) / per_typical)
                if budget and per_typical
                else None,
                "covers_worst": int((budget or 0) / per_worst) if budget and per_worst else None,
            },
            "price": (
                {
                    "input_per_mtok": price.input_per_mtok,
                    "cached_input_per_mtok": price.cached_input_per_mtok,
                    "output_per_mtok": price.output_per_mtok,
                    "observed_at": price.observed_at,
                }
                if price is not None
                else None
            ),
            "packet": {
                "postings_cut": sum(1 for n in notes if n.posting_chars_cut),
                "posting_chars_cut": sum(n.posting_chars_cut for n in notes),
                "max_posting_chars": deep.MAX_POSTING_CHARS,
                "skills_left_out": max((n.skills_left_out for n in notes), default=0),
                "experience_left_out_max": max((n.experience_left_out for n in notes), default=0),
                "experience_shortened_max": max((n.experience_shortened for n in notes), default=0),
                "schema_in_prompt": system != deep.SYSTEM_PROMPT,
            },
            "share_evidence": share,
            "intent": ctx.intent_payload,
            "levels": ctx.levels,
            "evidence_example": example,
            "evidence_per_posting": evidence_counts,
            "confirmed_evidence": len(ctx.claims),
            "population": chosen.population,
            "main_list_roles": reach.roles,
            "too_short": reach.too_short,
            "already_reviewed": chosen.reviewed,
            "estimated_input_tokens": tokens,
            "postings": [
                {
                    "job_id": c.job_id,
                    "title": c.title,
                    "company": names.get(c.job_id, ""),
                    "stratum": c.stratum,
                    "local_score": c.local_score,
                    "band": c.band,
                }
                for c in chosen.candidates
            ],
        }

    def start(*, query: dict, body: dict) -> dict:
        from career_agent.clock import new_id

        if query or set(body) - {
            "limit",
            "include_unresolved",
            "confirm",
            "budget_usd",
            "resume",
            "retry_rejected",
        }:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if body.get("confirm") is not True:
            raise ApiError(
                400, "A run starts only after the preview is confirmed.", for_reader=True
            )
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        if provider is None:
            raise ApiError(409, reason, for_reader=True)
        checked = provider.availability()
        if not checked.state.usable:
            raise ApiError(
                409,
                "PROVIDER_NOT_READY",
                code="PROVIDER_NOT_READY",
                data={"state": checked.state.value},
            )
        share = provider.id in settings.share_evidence_with
        resume = body.get("resume")
        if resume is not None:
            # Continue a paused job: same provider, same model, same plan.
            if not isinstance(resume, str):
                raise ApiError(400, "resume must be a job id", for_reader=True)
            budget = budget_of(body, provider) if "budget_usd" in body else None
            limit, include_unresolved, retry = 0, False, False
        else:
            limit, include_unresolved, _available, retry = limits(body, provider, settings)
            budget = budget_of(body, provider)
            if budget is not None:
                from career_agent.semantic.settings import save_settings

                with closing(app.connect()) as conn:
                    save_settings(app.config.config_dir, {"deep_budget_usd": budget}, conn)
        with lock:
            semantic = app.semantic_runner
            if runner.running or (semantic is not None and semantic.running):
                raise ApiError(409, "An AI run is already in progress.", code="RUN_ACTIVE")
            pause.clear()
            live.clear()
            return runner.start(
                _work(
                    app,
                    provider,
                    share,
                    limit,
                    include_unresolved,
                    retry,
                    budget,
                    resume,
                    last,
                    live,
                    pause,
                ),
                new_id(),
            )

    def pause_run(*, query: dict, body: dict) -> dict:
        """Stop before the next posting; the job resumes where it stopped."""
        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not runner.running:
            return {"paused": False}
        pause.set()
        return {"paused": True}

    def cancel(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.providers.local_cli import terminate_running

        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        cancelled = runner.cancel()
        if cancelled:
            terminate_running()
        return {"cancelled": cancelled}

    def run_status(*, query: dict, body: dict) -> dict:
        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        return {"run": runner.snapshot(), "result": dict(last), "live": dict(live)}

    def reviews(*, query: dict, body: dict) -> dict:
        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        with closing(app.connect()) as conn:
            rows = _review_rows(app, conn)
        return {"reviews": rows}

    def job_review(*, job_id: str, query: dict, body: dict) -> dict:
        from career_agent.web.api import validate_job_id

        validate_job_id(job_id)
        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        with closing(app.connect()) as conn:
            found = _review_rows(app, conn, [job_id], detail=True)
        return {"job_id": job_id, "review": found[0] if found else None}

    def consent(*, query: dict, body: dict) -> dict:
        """This profile's consent for THIS provider, on or off. Nothing runs."""
        from career_agent.semantic.settings import SettingsError, save_settings

        if query or set(body) != {"share_evidence"} or not isinstance(body["share_evidence"], bool):
            raise ApiError(400, "share_evidence must be true or false", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        if provider is None:
            raise ApiError(409, reason, for_reader=True)
        shared = [p for p in settings.share_evidence_with if p != provider.id]
        if body["share_evidence"]:
            shared.append(provider.id)
        try:
            with closing(app.connect()) as conn:
                saved = save_settings(app.config.config_dir, {"share_evidence_with": shared}, conn)
        except SettingsError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        return {"share_evidence": provider.id in saved.share_evidence_with}

    app.register("GET", r"/api/deep", status)
    app.register("GET", r"/api/deep/coverage", coverage_status)
    app.register("POST", r"/api/deep/consent", consent)
    app.register("POST", r"/api/deep/preview", preview)
    app.register("GET", r"/api/deep/run", run_status)
    app.register("POST", r"/api/deep/run", start)
    app.register("POST", r"/api/deep/run/cancel", cancel)
    app.register("POST", r"/api/deep/run/pause", pause_run)
    app.register("POST", r"/api/deep/provider", choose)
    app.register("GET", r"/api/deep/reviews", reviews)
    app.register("GET", r"/api/jobs/(?P<job_id>[^/]+)/deep", job_review)


def _companies(conn: Any, job_ids: list[str]) -> dict[str, str]:
    if not job_ids:
        return {}
    marks = ",".join("?" for _ in job_ids)
    return {
        str(r[0]): str(r[1] or "")
        for r in conn.execute(
            "SELECT j.id, c.name FROM job j LEFT JOIN company c ON c.id = j.company_id"
            f" WHERE j.id IN ({marks})",
            job_ids,
        )
    }


def _review_rows(
    app: Any, conn: Any, job_ids: list[str] | None = None, *, detail: bool = False
) -> list[dict[str, Any]]:
    """Each posting's newest review, read against what is CURRENT now.

    A review is out of date when the posting's text changed, when the reading
    instructions changed, or when the profile it was asked against changed
    (search phrases, preferred levels, or, if evidence was sent, the confirmed
    evidence). An out-of-date review keeps its findings for reference but no
    level: `level` is STALE and the level it had is `level_then`, so it is
    never presented as the current recommendation. A cited evidence id is
    resolved in THIS profile's ledger; one no longer confirmed as it was is
    "changed". The three answers (`deep_assess`) are derived here from the
    stored observations every time, so a rule change re-reads every stored
    review without asking anybody anything.
    """
    from career_agent.semantic import deep, deep_assess
    from career_agent.semantic.deep_run import DeepRepo, confirmed_claims, context, freshness

    rows = DeepRepo(conn).newest_per_job(job_ids)
    if not rows:
        return []
    config = app.search_config()
    contract_now = deep.contract_identity()
    contexts = {shared: context(conn, config, shared) for shared in (True, False)}
    digest_now = {shared: ctx.digest for shared, ctx in contexts.items()}
    intent_now = contexts[False].intent_digest
    ledger = {c.claim_key: c for c in confirmed_claims(conn)}
    revisions = {key: int(claim.revision) for key, claim in ledger.items()}
    jobs = [job_id for job_id, _row in rows]
    marks = ",".join("?" for _ in jobs)
    local: dict[str, tuple[Any, ...]] = {}
    for r in conn.execute(
        "SELECT j.id, jm.match_score, jm.fit_band, j.title, c.name, j.content_hash FROM job j"
        " LEFT JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ?"
        "  AND jm.config_version = ?"
        " LEFT JOIN company c ON c.id = j.company_id"
        f" WHERE j.id IN ({marks})",
        (str(config.config_id), int(config.config_version), *jobs),
    ):
        local[str(r[0])] = (r[1], r[2], str(r[3] or ""), str(r[4] or ""), r[5])
    out = []
    for job_id, row in rows:
        review = json.loads(row["review_json"])
        sent = json.loads(row["sent_json"])
        shared = bool(sent.get("shared"))
        evidence: dict[str, dict[str, Any]] = {}
        for alias, (key, revision) in (sent.get("evidence") or {}).items():
            claim = ledger.get(key)
            evidence[alias] = (
                {
                    "text": claim.text,
                    "type": claim.claim_type.value,
                    "tools": " ".join(claim.tools),
                    # The confirmed dates, for a requirement that asks years.
                    "start": claim.period_start,
                    "end": claim.period_end,
                    "changed": False,
                }
                if claim is not None and claim.revision == revision
                else {"text": "", "type": "", "tools": "", "changed": True}
            )
        current = {a: e for a, e in evidence.items() if not e["changed"]}
        assessment = deep_assess.assess(review, current if shared else None)
        recommendation = assessment.recommendation
        score, band, title, company, hash_now = local.get(job_id, (None, None, "", "", None))
        # One rule for the list, the panel, the drawer and the planner
        # (`deep_run.freshness`, ADR-0035 addendum 5). Another title is another
        # question to the reader (migration 0058).
        state, reasons, level_current = freshness(
            row,
            hash_now=hash_now,
            title_now=title,
            contract_now=contract_now,
            digest_now=digest_now,
            intent_now=intent_now,
            ledger=revisions,
        )
        stale = not level_current
        item: dict[str, Any] = {
            "job_id": job_id,
            "title": title,
            "company": company,
            "level": "STALE" if stale else recommendation,
            "level_then": recommendation,
            "stale": stale,
            # current | evidence (work fit current, evidence changed) | stale
            "state": state,
            "stale_reasons": reasons,
            "assessment": assessment.as_dict(),
            "comparison": "stale" if stale else deep_assess.comparison(recommendation, band),
            "local_score": int(score) if score is not None else 0,
            "band": band or "",
            "stratum": row["stratum"],
            "provider": row["provider"],
            "model": row["model"],
            "contract": row["contract"],
            "created_at": row["created_at"],
            "evidence_shared": shared,
        }
        if detail:
            item.update(
                review=review,
                evidence=evidence,
                dropped=json.loads(row["gate_json"]),
                profile_digest=row["profile_digest"],
            )
        out.append(item)
    order = {name: index for index, name in enumerate((*deep_assess.RECOMMENDATIONS, "STALE"))}
    out.sort(key=lambda r: (order[r["level"]], -r["local_score"], r["job_id"]))
    return out


def deep_levels(app: Any, conn: Any) -> dict[str, tuple[str, bool, str]]:
    """Each read posting's deep level as `_review_rows` shows it NOW:
    `job_id -> (level, stale, state)`, where `level` is the level the reading
    gives (for an out-of-date reading, the level it had then), `stale` that
    this level is not current, and `state` current, evidence (the work fit
    stands, the evidence changed) or stale (`deep_run.freshness`). Nothing is stored:
    the level is derived from the stored observations, as everywhere else.

    Remembered while nothing it depends on moved: a new or replaced reading,
    the profile digests (search intent, levels, evidence), the reading
    instructions, or any posting's text (the list's own freshness token, whose
    population part moves when the corpus does). Deriving all of them costs
    about half a second on 710 readings; checking costs a few milliseconds.
    A database without the table (a demo, an older build) has none.
    """
    from career_agent.semantic import deep
    from career_agent.semantic.deep_run import context
    from career_agent.storage.catalogue import population_token

    try:
        stamp = tuple(conn.execute("SELECT COUNT(*), MAX(created_at) FROM deep_review").fetchone())
    except sqlite3.Error:
        return {}
    if not stamp[0]:
        return {}
    config = app.search_config()
    key = (
        stamp,
        deep.contract_identity(),
        tuple(context(conn, config, shared).digest for shared in (True, False)),
        population_token(conn),
        str(config.config_id),
        int(config.config_version),
    )
    with _LEVELS_LOCK:
        cached = getattr(app, "_deep_levels_cache", None)
        if cached is not None and cached[0] == key:
            return dict(cached[1])
    levels = {
        str(item["job_id"]): (str(item["level_then"]), bool(item["stale"]), str(item["state"]))
        for item in _review_rows(app, conn)
    }
    with _LEVELS_LOCK:
        app._deep_levels_cache = (key, levels)
    return dict(levels)


_LEVELS_LOCK = threading.Lock()


def _work(
    app: Any,
    provider: Any,
    share: bool,
    limit: int,
    include_unresolved: bool,
    retry_rejected: bool,
    budget: float | None,
    resume: str | None,
    last: dict,
    live: dict,
    pause: threading.Event,
):
    def work(state: Any, cancel: Any) -> None:
        from career_agent.semantic.deep_run import run_deep

        config = app.search_config()
        with closing(app.connect()) as conn:
            served = app._serving(conn).serving
            scores = (served.config_id, served.config_version) if served else None
            age = age_of_conn(conn)

            def job_progress(job: Any) -> None:
                state.boards_total = int(job.requested)
                state.boards_done = int(job.done)
                live.update(
                    deep_job_id=job.deep_job_id,
                    requested=job.requested,
                    done=job.done,
                    spent_usd=job.spent_usd,
                    budget_usd=job.budget_usd,
                    batch=job.batch,
                    batches=job.batches,
                )

            stats = run_deep(
                conn,
                config,
                provider,
                share=share,
                limit=limit,
                include_unresolved=include_unresolved,
                retry_rejected=retry_rejected,
                budget_usd=budget,
                resume_job=resume,
                job_progress=job_progress,
                cancel=cancel,
                pause=pause,
                scores=scores,
                age=age,
            )
            last.clear()
            last.update(stats.as_dict())
        state.funnel = dict(last)

    return work


def age_of_conn(conn: Any) -> tuple[int | None, bool]:
    from career_agent.web import view_prefs

    prefs = view_prefs.read(conn)
    return prefs["max_age_days"], bool(prefs["include_undated"])


def _why_none(scores: dict[str, Any], reach: Any, with_unresolved: int) -> str:
    """Why no posting can be read, in the order a person can act on it."""
    if scores.get("state") == "none":
        return "NO_SCORES"
    if with_unresolved > 0:
        return "UNRESOLVED_ONLY"
    if reach.roles == 0:
        return "NO_ELIGIBLE"
    if reach.readable == 0:
        return "TOO_SHORT"
    return "ALL_READ"


def _counts(selection: Any) -> dict[str, int]:
    # `rejected`: answers paid for and rejected, offered for an explicit retry.
    return {"available": selection.available, "rejected": selection.rejected}


def _model(provider: Any) -> str:
    return str(getattr(provider, "model", "") or "")


def _instance(provider_id: str) -> Any:
    """The provider, configured for the deep reading: DeepSeek with the
    longer answer ceiling this contract needs. Constructing reads no key."""
    from career_agent.semantic.deep_run import DEEP_MAX_OUTPUT_TOKENS
    from career_agent.semantic.providers import get_provider
    from career_agent.semantic.providers.deepseek import DeepSeekProvider

    provider = get_provider(provider_id)
    if isinstance(provider, DeepSeekProvider):
        provider = DeepSeekProvider(max_output_tokens=DEEP_MAX_OUTPUT_TOKENS)
    return provider


def _providers(settings: Any, chosen: str | None) -> list[dict[str, Any]]:
    """Each provider the deep reading can use: model, connection, whether this
    computer is ready for it (a local check that sends nothing), what it
    costs, and whether THIS profile consented to share evidence with it."""
    from career_agent.semantic.deep_run import price_for
    from career_agent.semantic.providers import Billing

    out = []
    for provider_id in DEEP_PROVIDERS:
        provider = _instance(provider_id)
        billing = provider.capabilities().billing
        checked = provider.availability()
        price = price_for(provider) if billing is Billing.METERED_API else None
        out.append(
            {
                "id": provider_id,
                "model": str(getattr(provider, "model", "") or ""),
                "connection": "api" if billing is Billing.METERED_API else "subscription",
                "billing": billing.value,
                "state": checked.state.value,
                "usable": checked.state.usable,
                "auth": checked.facts.get("auth"),
                "price": (
                    {
                        "input_per_mtok": price.input_per_mtok,
                        "cached_input_per_mtok": price.cached_input_per_mtok,
                        "output_per_mtok": price.output_per_mtok,
                        "observed_at": price.observed_at,
                    }
                    if price is not None
                    else None
                ),
                "consent": provider_id in settings.share_evidence_with,
                "chosen": provider_id == chosen,
            }
        )
    return out


def _job_summary(app: Any, conn: Any, chosen: str | None) -> dict[str, Any] | None:
    """The newest job: where it stands and whether it can be resumed as it is."""
    from career_agent.semantic import deep
    from career_agent.semantic.deep_run import DeepRepo, context

    repo = DeepRepo(conn)
    job = repo.last_job()
    if job is None:
        return None
    attempts = repo.attempts(str(job["id"]))
    share = bool(job["share_evidence"])
    digest_now = context(conn, app.search_config(), share).digest
    plan = json.loads(str(job["plan_json"]))
    finished = repo.reviewed(
        str(job["provider"]), str(job["contract"]), str(job["profile_digest"]), str(job["model"])
    ) | repo.answered_in(str(job["id"]))
    done = sum(1 for _job, content_hash, _s in plan if content_hash in finished)
    same = (
        str(job["profile_digest"]) == digest_now
        and str(job["contract"]) == deep.contract_identity()
        and str(job["provider"]) == (chosen or "")
    )
    return {
        "id": str(job["id"]),
        "provider": str(job["provider"]),
        "model": str(job["model"]),
        "billing": str(job["billing"]),
        "status": str(job["status"]),
        "stop_reason": job["stop_reason"],
        "requested": int(job["requested"]),
        "done": done,
        "spent_usd": round(repo.spent(str(job["id"])), 6),
        "budget_usd": job["budget_usd"],
        "attempts": attempts,
        "resumable": str(job["status"]) == "PAUSED" and same and done < int(job["requested"]),
        "created_at": str(job["created_at"]),
    }

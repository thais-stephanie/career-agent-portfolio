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

    def chosen_id(settings: Any) -> str | None:
        """The provider this profile chose for the deep reading, by name. Until
        it chooses in the panel, its AI & Semantic Matching choice when that is
        a subscription command line (what this panel used before), else none."""
        if settings.deep_provider:
            return str(settings.deep_provider)
        mode = settings.mode.value
        return mode if settings.enabled and mode in ("claude_code", "codex") else None

    def chosen_provider(settings: Any) -> tuple[Any, str]:
        """The profile's NAMED provider, or why there is none to use."""
        provider_id = chosen_id(settings)
        if provider_id is None:
            return None, "CHOOSE_PROVIDER"
        return _instance(provider_id), ""

    def available_for(provider: Any, settings: Any, include_unresolved: bool) -> int:
        from career_agent.semantic.deep_run import context, select

        share = provider.id in settings.share_evidence_with
        with closing(app.connect()) as conn:
            ctx = context(conn, app.search_config(), share)
            return select(
                conn,
                app.search_config(),
                provider_id=provider.id,
                limit=0,
                include_unresolved=include_unresolved,
                current_digest=ctx.digest,
            ).available

    def limits(body: dict, provider: Any, settings: Any) -> tuple[int, bool, int]:
        include_unresolved = _bool(body.get("include_unresolved", False))
        available = available_for(provider, settings, include_unresolved)
        limit = body.get("limit", min(DEFAULT_LIMIT, max(available, 1)))
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= max(available, 1)
        ):
            raise ApiError(400, "limit out of range", for_reader=True)
        return limit, include_unresolved, available

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
        from career_agent.semantic.deep_run import coverage
        from career_agent.semantic.store import SemanticRepo

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
            reach, main_list = coverage(conn, app.search_config(), False)
            rows = _review_rows(app, conn)
            job = _job_summary(app, conn, chosen_id(settings))
        current = {r["job_id"] for r in rows if not r["stale"]}
        stale = sum(1 for r in rows if r["stale"])
        waiting = sum(1 for r in main_list if str(r["id"]) not in current)
        return {
            "demo": not _is_personal(app),
            "provider": provider.id if provider else None,
            "provider_reason": reason,
            "provider_state": state,
            "auth": auth,
            "share_evidence": bool(provider and provider.id in settings.share_evidence_with),
            "cap": settings.max_jobs_per_subscription_run,
            # The MAIN LIST only, never the catalogue: roles in it, roles with
            # enough text to read, and roles whose advert is too short.
            "main_list_roles": reach.roles,
            "readable": reach.readable,
            "too_short": reach.too_short,
            "reviewed": len(current),
            "stale": stale,
            # Readable roles with no CURRENT reading: still in the list, in
            # the local order, never ranked below the ones that have one.
            "not_reviewed": waiting,
            "last_run": last_run,
            "run": runner.snapshot(),
            "providers": _providers(settings, chosen_id(settings)) if _is_personal(app) else [],
            "budget_default": settings.deep_budget_usd,
            "available": (available_for(provider, settings, False) if provider is not None else 0),
            "job": job,
            "live": dict(live),
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
            raise ApiError(409, "A deep reading is running.", for_reader=True)
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

        if query or set(body) - {"limit", "include_unresolved", "budget_usd"}:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        if provider is None:
            return {"provider": None, "reason": reason}
        limit, include_unresolved, available = limits(body, provider, settings)
        budget = budget_of(body, provider)
        share = provider.id in settings.share_evidence_with
        config = app.search_config()
        with closing(app.connect()) as conn:
            ctx = context(conn, config, share)
            chosen = select(
                conn,
                config,
                provider_id=provider.id,
                limit=limit,
                include_unresolved=include_unresolved,
                current_digest=ctx.digest,
            )
            names = _companies(conn, [c.job_id for c in chosen.candidates])
            reach, _rows = coverage(conn, config, include_unresolved)
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

        if query or set(body) - {"limit", "include_unresolved", "confirm", "budget_usd", "resume"}:
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
        if not provider.availability().state.usable:
            raise ApiError(409, "PROVIDER_NOT_READY", for_reader=True)
        share = provider.id in settings.share_evidence_with
        resume = body.get("resume")
        if resume is not None:
            # Continue a paused job: same provider, same model, same plan.
            if not isinstance(resume, str):
                raise ApiError(400, "resume must be a job id", for_reader=True)
            budget = budget_of(body, provider) if "budget_usd" in body else None
            limit, include_unresolved = 0, False
        else:
            limit, include_unresolved, _available = limits(body, provider, settings)
            budget = budget_of(body, provider)
            if budget is not None:
                from career_agent.semantic.settings import save_settings

                with closing(app.connect()) as conn:
                    save_settings(app.config.config_dir, {"deep_budget_usd": budget}, conn)
        with lock:
            semantic = app.semantic_runner
            if runner.running or (semantic is not None and semantic.running):
                raise ApiError(409, "An AI run is already in progress.", for_reader=True)
            pause.clear()
            live.clear()
            return runner.start(
                _work(
                    app,
                    provider,
                    share,
                    limit,
                    include_unresolved,
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
    from career_agent.semantic.deep_run import DeepRepo, confirmed_claims, context

    rows = DeepRepo(conn).newest_per_job(job_ids)
    if not rows:
        return []
    config = app.search_config()
    contract_now = deep.contract_identity()
    digest_now = {shared: context(conn, config, shared).digest for shared in (True, False)}
    ledger = {c.claim_key: c for c in confirmed_claims(conn)}
    jobs = [str(r["job_id"]) for r in rows]
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
    for row in rows:
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
                    "changed": False,
                }
                if claim is not None and claim.revision == revision
                else {"text": "", "type": "", "tools": "", "changed": True}
            )
        current = {a: e for a, e in evidence.items() if not e["changed"]}
        assessment = deep_assess.assess(review, current if shared else None)
        recommendation = assessment.recommendation
        score, band, title, company, hash_now = local.get(
            str(row["job_id"]), (None, None, "", "", None)
        )
        reasons = []
        if row["content_hash"] != hash_now:
            reasons.append("posting")
        if row["contract"] != contract_now:
            reasons.append("instructions")
        if row["profile_digest"] != digest_now[shared]:
            reasons.append("profile")
        stale = bool(reasons)
        item: dict[str, Any] = {
            "job_id": row["job_id"],
            "title": title,
            "company": company,
            "level": "STALE" if stale else recommendation,
            "level_then": recommendation,
            "stale": stale,
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


def _work(
    app: Any,
    provider: Any,
    share: bool,
    limit: int,
    include_unresolved: bool,
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
                budget_usd=budget,
                resume_job=resume,
                job_progress=job_progress,
                cancel=cancel,
                pause=pause,
            )
            last.clear()
            last.update(stats.as_dict())
        state.funnel = dict(last)

    return work


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
    finished = repo.finished_hashes(
        str(job["provider"]), str(job["contract"]), str(job["profile_digest"])
    )
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

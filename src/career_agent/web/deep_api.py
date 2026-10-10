"""Find Jobs -> Deep reading: consent, preview, run, results.

Nothing here runs because a setting changed. A run starts only from
`POST /api/deep/run` with `confirm: true`, after a preview the person saw, on
the provider this profile chose BY NAME (never Auto, never a fallback), and
only a signed-in subscription command line can be used. Every response
carries codes the interface words; no credential is ever read or returned.
"""

from __future__ import annotations

import json
import threading
from contextlib import closing
from typing import TYPE_CHECKING, Any

from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi

#: Postings a preview shows the identity of; the run reads the same list.
DEFAULT_LIMIT = 25


def _bool(value: Any) -> bool:
    return value is True or value in ("1", "true")


def register_deep(app: JobsApi) -> None:
    from career_agent.pipeline.retrieval import RetrievalRunner
    from career_agent.web.semantic_api import _is_personal, profile_settings

    runner = RetrievalRunner()
    app.deep_runner = runner
    lock = threading.Lock()
    last: dict[str, Any] = {}

    def chosen_provider(settings: Any) -> tuple[Any, str]:
        """The profile's NAMED provider, or why there is none to use."""
        from career_agent.semantic.providers import Billing, get_provider

        mode = settings.mode.value
        if not settings.enabled or mode in ("deterministic", "auto", "laya"):
            return None, "CHOOSE_PROVIDER"
        provider = get_provider(mode)
        if provider.capabilities().billing is not Billing.SUBSCRIPTION:
            return None, "DEEP_NEEDS_SUBSCRIPTION"
        return provider, ""

    def limits(body: dict) -> tuple[int, bool]:
        settings = profile_settings(app)
        cap = settings.max_jobs_per_subscription_run
        limit = body.get("limit", min(DEFAULT_LIMIT, cap))
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= cap:
            raise ApiError(400, "limit out of range", for_reader=True)
        return limit, _bool(body.get("include_unresolved", False))

    def status(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.deep_run import DeepRepo, _population
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
            reviewed_hashes = {str(r["content_hash"]) for r in DeepRepo(conn).all_latest()}
            last_run = SemanticRepo(conn).last_run(deep=True)
            main_list = _population(conn, app.search_config(), False)
        waiting = sum(1 for r in main_list if str(r["content_hash"]) not in reviewed_hashes)
        return {
            "demo": not _is_personal(app),
            "provider": provider.id if provider else None,
            "provider_reason": reason,
            "provider_state": state,
            "auth": auth,
            "share_evidence": bool(provider and provider.id in settings.share_evidence_with),
            "cap": settings.max_jobs_per_subscription_run,
            "reviewed": len(reviewed_hashes),
            # Main-list postings with no deep reading yet: still in the list,
            # in the local order, never ranked below the ones that have one.
            "not_reviewed": waiting,
            "last_run": last_run,
            "run": runner.snapshot(),
        }

    def preview(*, query: dict, body: dict) -> dict:
        """What a run WOULD send, computed locally. Nothing leaves."""
        from career_agent.semantic import deep
        from career_agent.semantic.deep_run import context, estimate_tokens, request_for, select

        if query or set(body) - {"limit", "include_unresolved"}:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        settings = profile_settings(app)
        provider, reason = chosen_provider(settings)
        if provider is None:
            return {"provider": None, "reason": reason}
        limit, include_unresolved = limits(body)
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
        tokens = 0
        example: list[dict[str, Any]] = []
        evidence_counts: list[int] = []
        for candidate in chosen.candidates:
            message, items, _sent = request_for(ctx, candidate)
            tokens += estimate_tokens(deep.SYSTEM_PROMPT + message)
            evidence_counts.append(len(items))
            if not example:
                example = items
        return {
            "provider": provider.id,
            "share_evidence": share,
            "intent": ctx.intent_payload,
            "levels": ctx.levels,
            "evidence_example": example,
            "evidence_per_posting": evidence_counts,
            "confirmed_evidence": len(ctx.claims),
            "population": chosen.population,
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

        if query or set(body) - {"limit", "include_unresolved", "confirm"}:
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
            raise ApiError(409, "SIGN_IN_REQUIRED", for_reader=True)
        limit, include_unresolved = limits({k: v for k, v in body.items() if k != "confirm"})
        share = provider.id in settings.share_evidence_with
        with lock:
            semantic = app.semantic_runner
            if runner.running or (semantic is not None and semantic.running):
                raise ApiError(409, "An AI run is already in progress.", for_reader=True)
            return runner.start(
                _work(app, provider, share, limit, include_unresolved, last), new_id()
            )

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
        return {"run": runner.snapshot(), "result": dict(last)}

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
            row = conn.execute("SELECT content_hash FROM job WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise ApiError(404, "no such job", for_reader=True)
            found = _review_rows(app, conn, [str(row[0])], detail=True)
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
    app: Any, conn: Any, hashes: list[str] | None = None, *, detail: bool = False
) -> list[dict[str, Any]]:
    """Each review with its priority, read against the CURRENT profile.

    A review written for an earlier profile is `stale`, never silently
    current. A cited evidence id is resolved in THIS profile's ledger; one
    that is no longer confirmed here is reported as changed, not shown as
    support.
    """
    from career_agent.semantic import deep
    from career_agent.semantic.deep_run import DeepRepo, confirmed_claims, context

    repo = DeepRepo(conn)
    rows = repo.all_latest() if hashes is None else list(repo.latest(hashes).values())
    if not rows:
        return []
    config = app.search_config()
    digest_now = {shared: context(conn, config, shared).digest for shared in (True, False)}
    ledger = {c.claim_key: c for c in confirmed_claims(conn)} if detail else {}
    jobs = [str(r["job_id"]) for r in rows]
    marks = ",".join("?" for _ in jobs)
    local = {
        str(r[0]): (int(r[1]), str(r[2]), str(r[3] or ""), str(r[4] or ""))
        for r in conn.execute(
            "SELECT j.id, jm.match_score, jm.fit_band, j.title, c.name FROM job j"
            " JOIN job_match jm ON jm.job_id = j.id AND jm.config_id = ? AND jm.config_version = ?"
            " LEFT JOIN company c ON c.id = j.company_id"
            f" WHERE j.id IN ({marks})",
            (str(config.config_id), int(config.config_version), *jobs),
        )
    }
    out = []
    for row in rows:
        review = json.loads(row["review_json"])
        sent = json.loads(row["sent_json"])
        shared = bool(sent.get("shared"))
        level = deep.priority(review, shared)
        score, band, title, company = local.get(str(row["job_id"]), (0, "", "", ""))
        item: dict[str, Any] = {
            "job_id": row["job_id"],
            "title": title,
            "company": company,
            "level": level.level,
            "reasons": [{"code": code, **values} for code, values in level.reasons],
            "comparison": deep.comparison(level.level, band or None),
            "local_score": score,
            "band": band,
            "stratum": row["stratum"],
            "provider": row["provider"],
            "model": row["model"],
            "contract": row["contract"],
            "created_at": row["created_at"],
            "stale": row["profile_digest"] != digest_now[shared],
            "evidence_shared": shared,
        }
        if detail:
            evidence = {}
            for alias, (key, revision) in (sent.get("evidence") or {}).items():
                claim = ledger.get(key)
                evidence[alias] = (
                    {"text": claim.text, "type": claim.claim_type.value, "changed": False}
                    if claim is not None and claim.revision == revision
                    else {"text": "", "type": "", "changed": True}
                )
            item.update(
                review=review,
                evidence=evidence,
                dropped=json.loads(row["gate_json"]),
                profile_digest=row["profile_digest"],
            )
        out.append(item)
    order = {name: index for index, name in enumerate(deep.LEVELS)}
    out.sort(key=lambda r: (order[r["level"]], -r["local_score"], r["job_id"]))
    return out


def _work(app: Any, provider: Any, share: bool, limit: int, include_unresolved: bool, last: dict):
    def work(state: Any, cancel: Any) -> None:
        from career_agent.semantic.deep_run import run_deep

        config = app.search_config()
        with closing(app.connect()) as conn:

            def progress(stats: Any) -> None:
                state.boards_total = int(stats.candidates)
                state.boards_done = int(stats.published + stats.failed + stats.rejected)

            stats = run_deep(
                conn,
                config,
                provider,
                share=share,
                limit=limit,
                include_unresolved=include_unresolved,
                progress=progress,
                cancel=cancel,
            )
            last.clear()
            last.update(stats.as_dict())
        state.funnel = dict(last)

    return work

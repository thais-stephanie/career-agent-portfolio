"""Settings & Sources -> AI & Semantic Matching, and Search Fit readiness.

HOW Career Agent interprets postings. WHAT the person wants stays in Search
Preferences and is never edited here.

Nothing on these routes returns, logs or echoes a credential. The DeepSeek key
can be added, replaced or removed, and the only thing any response says about
it is whether one is configured.

Demo mode never sends anything to a provider: the demo corpus is invented,
and the runtime modes exist so nothing personal and nothing real mixes with it.
"""

from __future__ import annotations

import threading
from contextlib import closing
from typing import TYPE_CHECKING, Any

from career_agent.web.server import ApiError

if TYPE_CHECKING:
    from career_agent.web.api import JobsApi

SETTINGS_KEYS = frozenset(
    {"enabled", "mode", "budget_per_run_usd", "max_jobs_per_run", "max_jobs_per_subscription_run"}
)


def _is_personal(app: JobsApi) -> bool:
    from career_agent.runtime import RuntimeMode, read_identity

    with closing(app.connect()) as conn:
        identity = read_identity(conn)
    return identity is not None and identity.kind is RuntimeMode.PERSONAL


def register_semantic(app: JobsApi) -> None:
    from career_agent.pipeline.retrieval import RetrievalRunner
    from career_agent.semantic.settings import load_stored_key

    # A key saved from Settings lives in the root `.env`, which the launcher
    # does not load. Demo mode's configuration sits elsewhere and finds none.
    load_stored_key(app.config.config_dir)

    runner = RetrievalRunner()
    host = getattr(app, "profile_host", None)
    if host is not None:
        host.gate(app, runner)
    # Visible to `start_rescore`, which refuses while a semantic run (and the
    # rescore it ends with) is writing scores. Two passes on two connections
    # would contend for the same rows.
    app.semantic_runner = runner
    lock = threading.Lock()
    last: dict[str, Any] = {}

    def provider_rows(fresh: bool = False) -> list[dict[str, Any]]:
        from career_agent.semantic.providers import PROVIDER_IDS, get_provider
        from career_agent.semantic.routing import provider_status

        rows = []
        for provider_id in PROVIDER_IDS:
            provider = get_provider(provider_id)
            status = provider_status(provider_id, fresh=fresh)
            capabilities = provider.capabilities()
            rows.append(
                {
                    "id": provider_id,
                    "name": provider.display_name,
                    "state": status.state.value,
                    "auth": status.facts.get("auth"),
                    "usable": status.state.usable,
                    "detail": status.detail,
                    "billing": capabilities.billing.value,
                    "quotes": capabilities.quotes,
                    "sends": capabilities.sends,
                }
            )
        return rows

    def semantic_status(*, query: dict, body: dict) -> dict:
        from career_agent.match.readiness import search_fit_readiness
        from career_agent.semantic.routing import resolve
        from career_agent.semantic.settings import key_state, load_settings
        from career_agent.semantic.store import SemanticRepo

        fresh = query.get("refresh") in ("1", "true")
        if set(query) - {"refresh"}:
            raise ApiError(400, "unknown parameter", for_reader=True)
        config_dir = app.config.config_dir
        settings = load_settings(config_dir)
        personal = _is_personal(app)
        if fresh:
            from career_agent.semantic.routing import forget_status

            forget_status()
        route = resolve(settings) if personal else None
        with closing(app.connect()) as conn:
            try:
                last_run = SemanticRepo(conn).last_run()
            except Exception:  # noqa: BLE001 - an unmigrated demo database
                last_run = None
        key = key_state(config_dir)
        return {
            "settings": settings.model_dump(mode="json"),
            "demo": not personal,
            "active_provider": route.provider.id if route and route.provider else None,
            "active_reason": "DEMO"
            if not personal
            else ("" if route and route.provider else "NO_PROVIDER"),
            "fallbacks": route.fallbacks if route else [],
            # `resolve` above already refreshed each status when asked; asking
            # the CLIs a second time would double a slow GET for nothing.
            "providers": provider_rows(),
            "deepseek_key": {"configured": key.configured},
            "last_run": last_run,
            "run": runner.snapshot(),
            "readiness": search_fit_readiness(app.search_config()).as_dict(),
            "privacy": {
                "sends": [
                    "The work, tools and other signals you said you want.",
                    "The title and text of each posting evaluated.",
                ],
                "never_sends": [
                    "Your Career Profile or evidence",
                    "CV files",
                    "Application history",
                    "Other postings",
                    "Your database",
                ],
            },
        }

    def patch_settings(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.settings import SettingsError, load_settings, save_settings

        if query or not body or set(body) - SETTINGS_KEYS:
            raise ApiError(400, "unknown setting", for_reader=True)
        before = load_settings(app.config.config_dir)
        try:
            after = save_settings(app.config.config_dir, body)
        except SettingsError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        recalculation = before.uses_findings != after.uses_findings
        if recalculation:
            _mark_evaluated_for_rescore(app)
        return {"settings": after.model_dump(mode="json"), "recalculation_required": recalculation}

    def put_key(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.routing import forget_status
        from career_agent.semantic.settings import SettingsError, store_key

        if query or set(body) != {"key"} or not isinstance(body["key"], str):
            raise ApiError(400, "Provide the key.", for_reader=True)
        try:
            state = store_key(app.config.config_dir, body["key"])
        except SettingsError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        forget_status("deepseek")
        return {"configured": state.configured}

    def delete_key(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.routing import forget_status
        from career_agent.semantic.settings import remove_key

        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        state = remove_key(app.config.config_dir)
        forget_status("deepseek")
        return {"configured": state.configured}

    def healthcheck(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.providers import PROVIDER_IDS, get_provider
        from career_agent.semantic.routing import forget_status

        provider_id = body.get("provider")
        if query or set(body) != {"provider"} or provider_id not in PROVIDER_IDS:
            raise ApiError(400, "unknown provider", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        status = get_provider(str(provider_id)).healthcheck()
        forget_status(str(provider_id))
        return {"provider": provider_id, "state": status.state.value, "detail": status.detail}

    def plan(*, query: dict, body: dict) -> dict:
        from career_agent.semantic.intent import search_intent
        from career_agent.semantic.routing import resolve
        from career_agent.semantic.runner import estimate, run_cap, select_candidates
        from career_agent.semantic.settings import load_settings

        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        settings = load_settings(app.config.config_dir)
        route = resolve(settings)
        config = app.search_config()
        intent = search_intent(config)
        with closing(app.connect()) as conn:
            selection = select_candidates(conn, config, intent, limit=run_cap(settings, route))
        cost = estimate(route, selection, intent)
        return {
            "provider": route.provider.id if route.provider else None,
            "reason": selection.reason or ("" if route.provider else "NO_PROVIDER"),
            "fallbacks": route.fallbacks,
            "candidates": cost.candidates,
            "eligible": cost.eligible,
            "input_tokens": cost.input_tokens,
            "expected_usd": cost.expected_usd,
            "worst_usd": cost.worst_usd,
            "billing": cost.billing,
            "budget_usd": settings.budget_per_run_usd,
        }

    def start_run(*, query: dict, body: dict) -> dict:
        from career_agent.clock import new_id

        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        if not _is_personal(app):
            raise ApiError(409, "Demo mode never uses AI.", for_reader=True)
        with lock:
            if runner.running:
                raise ApiError(409, "A semantic run is already in progress.", for_reader=True)
            if app.rescore.running:
                raise ApiError(409, "Search Fit is being recalculated.", for_reader=True)
            return runner.start(_work(app, last), new_id())

    def cancel_run(*, query: dict, body: dict) -> dict:
        if query or body:
            raise ApiError(400, "unexpected parameters", for_reader=True)
        return {"cancelled": runner.cancel()}

    def run_status(*, query: dict, body: dict) -> dict:
        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        return {"run": runner.snapshot(), "result": dict(last)}

    def readiness(*, query: dict, body: dict) -> dict:
        from career_agent.match.readiness import search_fit_readiness

        if query:
            raise ApiError(400, "unknown parameter", for_reader=True)
        return search_fit_readiness(app.search_config()).as_dict()

    app.register("GET", r"/api/search-fit/readiness", readiness)
    app.register("GET", r"/api/semantic", semantic_status)
    app.register("PATCH", r"/api/semantic", patch_settings)
    app.register("POST", r"/api/semantic/deepseek-key", put_key)
    app.register("POST", r"/api/semantic/deepseek-key/remove", delete_key)
    app.register("POST", r"/api/semantic/healthcheck", healthcheck)
    app.register("POST", r"/api/semantic/plan", plan)
    app.register("GET", r"/api/semantic/run", run_status)
    app.register("POST", r"/api/semantic/run", start_run)
    app.register("POST", r"/api/semantic/run/cancel", cancel_run)


def _mark_evaluated_for_rescore(app: JobsApi) -> None:
    """Semantic evidence switched on or off: every posting that has any must
    be scored again, and only those."""
    from career_agent.storage import invalidation
    from career_agent.storage.db import transaction

    with closing(app.connect()) as conn:
        ids = [
            str(row[0])
            for row in conn.execute(
                "SELECT id FROM job WHERE content_hash IN"
                " (SELECT content_hash FROM semantic_evaluation)"
            ).fetchall()
        ]
        if ids:
            with transaction(conn):
                invalidation.request(conn, ids)


def _work(app: JobsApi, last: dict[str, Any]):
    def work(state, cancel) -> None:
        from career_agent.pipeline.rescore import RescoreMode, rescore
        from career_agent.semantic.routing import resolve
        from career_agent.semantic.runner import run_semantic
        from career_agent.semantic.settings import load_settings

        settings = load_settings(app.config.config_dir)
        route = resolve(settings)
        config = app.search_config()
        with closing(app.connect()) as conn:

            def progress(stats) -> None:
                state.boards_total = int(stats.candidates)
                state.boards_done = int(stats.published + stats.failed + stats.rejected)

            stats = run_semantic(
                conn,
                config,
                settings,
                route,
                requested=settings.mode.value,
                progress=progress,
                cancel=cancel,
            )
            last.clear()
            last.update(stats.as_dict())
            # The evaluated postings were marked; score them now, from the
            # stored readings, with the findings that just passed the gate.
            if stats.published:
                rescored = rescore(
                    conn, config, mode=RescoreMode.DIRTY, semantic=settings.uses_findings
                )
                last["rescored"] = rescored.jobs_scored
        state.funnel = dict(last)

    return work

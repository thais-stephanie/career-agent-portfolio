"""Tailor with AI (PR 9): the routes. The drafting itself is `resume_doc.drafter`.

AI is used ONLY on `POST /api/resume/jobs/<id>/drafts`, which the page sends
when the person presses Send after reading what will be sent. Opening a job,
a resume or a tab never reaches it. One request makes at most ONE provider
call and never retries: a failure is said, and trying again is the person's
next explicit action, a new run.

The provider is the one Career Agent already uses (Settings -> AI & Semantic
Matching, `semantic.routing.resolve`): there is no resume-specific key, model
or setting, and none of these routes returns, logs or echoes a credential.
Demo mode sends nothing anywhere.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING, Any, cast

from career_agent.resume_doc import drafter, reviewer
from career_agent.resume_doc.store import EvidenceNotConfirmed, NotFound
from career_agent.resume_doc.tailor import TailorFailed, job_ad
from career_agent.web.server import ApiError, closing

if TYPE_CHECKING:
    from career_agent.semantic.providers import SemanticProvider
    from career_agent.web.api import JobsApi
    from career_agent.web.server import LocalApp

#: A provider's failure, as one word the page translates. Never its own text.
_FAILURES = {
    "KEY_MISSING": "auth",
    "SIGN_IN_REQUIRED": "auth",
    "NOT_INSTALLED": "unavailable",
    "UNSUPPORTED": "unavailable",
    "CONNECTION_FAILED": "unreachable",
    "LIMIT_OR_ERROR": "limit",
}


def _provider(app: LocalApp) -> tuple[SemanticProvider | None, str]:
    """The configured provider, or None and why: `DEMO` or `NO_PROVIDER`."""
    from career_agent.semantic import routing
    from career_agent.semantic.settings import load_settings
    from career_agent.web.semantic_api import _is_personal

    if not _is_personal(cast("JobsApi", app)):
        return None, "DEMO"
    route = routing.resolve(load_settings(app.config.config_dir))
    return route.provider, "" if route.provider else "NO_PROVIDER"


def _over_budget(
    app: LocalApp, provider: SemanticProvider, system: str, message: str, spent: float = 0.0
) -> str:
    """Why a metered provider may not be asked: the person's AI budget per run
    (Settings) is the ceiling for this call too, with the answer at its longest
    and what this run already spent (`spent`) counted."""
    from career_agent.semantic.providers import Billing
    from career_agent.semantic.settings import load_settings

    if provider.capabilities().billing is not Billing.METERED_API:
        return ""
    budget = load_settings(app.config.config_dir).budget_per_run_usd
    # About four characters a token; the answer is priced at its ceiling.
    inputs = (len(system) + len(message)) // 3
    cost = provider.estimate_cost(inputs, int(getattr(provider, "max_output_tokens", 2500)))
    if cost is None:
        return "The price of this AI provider is not known, so nothing was sent."
    if spent + cost > budget:
        return "This would cost more than your AI budget per run, so nothing was sent."
    return ""


def register_resume_ai_routes(app: LocalApp) -> None:
    # A Cancel can arrive before its run is written (the page names the run as
    # it sends it). Remembered here, so the draft stops before the provider is
    # asked rather than going on unseen. The last few only: ids are random.
    early: deque[str] = deque(maxlen=64)

    def status(*, query: dict, body: dict) -> dict[str, Any]:
        """Which provider and model Tailor with AI would use, said before anything is sent."""
        provider, reason = _provider(app)
        if provider is None:
            return {"available": False, "reason": reason}
        return {
            "available": True,
            "provider": provider.id,
            "name": provider.display_name,
            "model": getattr(provider, "model", None),
            "billing": provider.capabilities().billing.value,
            "calls": 1,
        }

    def start(*, query: dict, body: dict, job_id: str) -> dict[str, Any]:
        run_id = body.get("run_id")
        if (
            set(body) != {"run_id"}
            or not isinstance(run_id, str)
            or not drafter.RUN_ID.match(run_id)
        ):
            raise ApiError(400, "Say which draft this is.")
        provider, _ = _provider(app)
        if provider is None:
            raise ApiError(409, "AI is not set up.", for_reader=True, code="ai_unavailable")
        with closing(app.connect()) as conn:
            ad = job_ad(conn, job_id)
            if ad is None:
                raise ApiError(404, "This job is not in this profile.")
            try:
                message = drafter.start(
                    conn,
                    ad=ad,
                    run_id=run_id,
                    provider=provider.id,
                    model=str(getattr(provider, "model", "") or ""),
                )
            except NotFound as exc:
                raise ApiError(
                    409, "Make your Master resume first.", for_reader=True, code="no_master"
                ) from exc
            except drafter.DrafterError as exc:
                raise ApiError(409, "This draft already exists.", code=exc.code) from exc
            over = _over_budget(app, provider, drafter.SYSTEM_PROMPT, message)
            if over:
                drafter.end(conn, run_id, "budget")
                raise ApiError(409, over, for_reader=True, code="ai_budget")
            # Asked last, right before the call: a Cancel that landed while
            # the run was being prepared stops it here, before any cost.
            if run_id in early or drafter.status(conn, run_id) != "RUNNING":
                drafter.end(conn, run_id, "cancelled")
                raise ApiError(409, "Cancelled.", for_reader=True, code="ai_cancelled")
        # The provider is asked with no connection open and no lock held, once.
        from career_agent.semantic.providers import ProviderFailed

        try:
            try:
                answer = provider.complete(drafter.SYSTEM_PROMPT, message, drafter.SCHEMA)
            except ProviderFailed as exc:
                code = _FAILURES.get(exc.state.value, "limit")
                with closing(app.connect()) as conn:
                    drafter.end(conn, run_id, code)
                raise ApiError(
                    502, "The AI provider did not answer.", for_reader=True, code=f"ai_{code}"
                ) from exc
            with closing(app.connect()) as conn:
                try:
                    drafter.receive(conn, run_id, answer)
                except drafter.DrafterError as exc:
                    raise ApiError(
                        409, "Nothing was applied.", for_reader=True, code=f"ai_{exc.code}"
                    ) from exc
        except ApiError:
            raise
        except Exception:
            # Never a run left RUNNING by a fault: it ends, and says so.
            with closing(app.connect()) as conn:
                drafter.end(conn, run_id, "failed")
            raise
        with closing(app.connect()) as conn:
            return drafter.view(conn, run_id)

    def one(*, query: dict, body: dict, run_id: str) -> dict[str, Any]:
        with closing(app.connect()) as conn:
            try:
                return drafter.view(conn, run_id)
            except NotFound as exc:
                raise ApiError(404, "No such draft.") from exc

    def decide(*, query: dict, body: dict, run_id: str, change_id: str) -> dict[str, Any]:
        decision, text = body.get("decision"), body.get("text")
        if (
            set(body) - {"decision", "text"}
            or decision not in ("ACCEPTED", "EDITED", "REJECTED", "PENDING")
            or (decision == "EDITED") != isinstance(text, str)
            or (isinstance(text, str) and not 1 <= len(text.strip()) <= 700)
        ):
            raise ApiError(400, "Accept, edit or reject this change.")
        with closing(app.connect()) as conn:
            try:
                drafter.decide(conn, run_id, change_id, decision, text)
            except NotFound as exc:
                raise ApiError(404, "No such change.") from exc
            except drafter.Refused as exc:
                raise ApiError(
                    422,
                    "Career Agent couldn't verify this wording from your confirmed experience.",
                    for_reader=True,
                    code="refused",
                    data={"checks": exc.checks},
                ) from exc
            except drafter.DrafterError as exc:
                said = (
                    "This change was already decided."
                    if exc.code == "decided"
                    else ("This draft is closed.")
                )
                raise ApiError(409, said, for_reader=True, code=exc.code) from exc
            return drafter.view(conn, run_id)

    def finalize(*, query: dict, body: dict, run_id: str) -> dict[str, Any]:
        from career_agent.web.resume_api import _detail, _unconfirmed

        if body:
            raise ApiError(400, "Nothing is sent to create the version.")
        with closing(app.connect()) as conn:
            try:
                return _detail(drafter.finalize(conn, run_id))
            except NotFound as exc:
                raise ApiError(404, "No such draft.") from exc
            except drafter.Refused as exc:
                raise ApiError(
                    422,
                    "A change no longer holds.",
                    for_reader=True,
                    code="refused",
                    data={"checks": exc.checks},
                ) from exc
            except TailorFailed as exc:
                raise ApiError(
                    422,
                    "This version could not be built safely, so nothing was saved.",
                    for_reader=True,
                    code="tailor_failed",
                    data={"checks": sorted({f["check"] for f in exc.findings})},
                ) from exc
            except EvidenceNotConfirmed as exc:
                raise _unconfirmed(exc.lines) from exc
            except drafter.DrafterError as exc:
                raise ApiError(409, "Nothing was saved.", for_reader=True, code=exc.code) from exc

    def cancel(*, query: dict, body: dict, run_id: str) -> dict[str, Any]:
        """Stop waiting, or discard the review. A late answer is then kept nowhere."""
        with closing(app.connect()) as conn:
            try:
                return {"ended": drafter.end(conn, run_id, "cancelled")}
            except NotFound:
                early.append(run_id)
                return {"ended": True}

    def review(*, query: dict, body: dict, run_id: str) -> dict[str, Any]:
        """The independent AI review: ONE call, only when asked, over the
        proposals Python already let through. Its failure loses nothing."""
        from career_agent.semantic.providers import ProviderFailed

        if body:
            raise ApiError(400, "Nothing is sent to review the suggestions.")
        provider, _ = _provider(app)
        if provider is None:
            raise ApiError(409, "AI is not set up.", for_reader=True, code="ai_unavailable")
        model = str(getattr(provider, "model", "") or "")
        with closing(app.connect()) as conn:
            try:
                message, held = reviewer.prepare(conn, run_id, provider=provider.id, model=model)
            except NotFound as exc:
                raise ApiError(404, "No such draft.") from exc
            except drafter.DrafterError as exc:
                raise ApiError(409, "Nothing to review.", for_reader=True, code=exc.code) from exc
            spent = drafter.spent(conn, run_id)
            over = _over_budget(app, provider, reviewer.SYSTEM_PROMPT, message, spent)
            if over:
                reviewer.end(conn, run_id, held["attempt"], "budget")
                raise ApiError(409, over, for_reader=True, code="ai_budget")
        try:
            answer = provider.complete(reviewer.SYSTEM_PROMPT, message, reviewer.SCHEMA)
        except ProviderFailed as exc:
            code = _FAILURES.get(exc.state.value, "limit")
            with closing(app.connect()) as conn:
                reviewer.end(conn, run_id, held["attempt"], code)
            raise ApiError(
                502, "The independent AI review couldn't finish.", for_reader=True,
                code=f"ai_{code}",
            ) from exc  # fmt: skip
        except Exception:
            with closing(app.connect()) as conn:
                reviewer.end(conn, run_id, held["attempt"], "failed")
            raise
        with closing(app.connect()) as conn:
            try:
                reviewer.receive(conn, run_id, held["attempt"], answer)
            except drafter.DrafterError as exc:
                raise ApiError(
                    409, "The independent AI review couldn't finish.", for_reader=True,
                    code=f"ai_{exc.code}",
                ) from exc  # fmt: skip
            return drafter.view(conn, run_id)

    def review_cancel(*, query: dict, body: dict, run_id: str) -> dict[str, Any]:
        """Stop waiting for the review; the suggestions stay as they are."""
        with closing(app.connect()) as conn:
            try:
                return {"ended": reviewer.cancel(conn, run_id)}
            except NotFound as exc:
                raise ApiError(404, "No such draft.") from exc

    run = r"/api/resume/drafts/(?P<run_id>[0-9A-HJKMNP-TV-Z]{26})"
    app.register("GET", r"/api/resume/ai", status)
    app.register("POST", r"/api/resume/jobs/(?P<job_id>[A-Za-z0-9_-]{1,64})/drafts", start)
    app.register("GET", run, one)
    app.register("POST", run + r"/changes/(?P<change_id>[0-9A-HJKMNP-TV-Z]{26})", decide)
    app.register("POST", run + "/finalize", finalize)
    app.register("POST", run + "/cancel", cancel)
    app.register("POST", run + "/review", review)
    app.register("POST", run + "/review/cancel", review_cancel)

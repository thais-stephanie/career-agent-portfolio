"""The API handlers. One place where HTTP meets the repositories.

The route table below is the whole surface. Two things about it are worth
saying out loud, because both are decisions rather than accidents:

**Cards and Table share `GET /api/jobs` exactly.** There is no card endpoint
and no table endpoint. They send the same query string to the same handler and
render the same rows differently, which is what makes "identical filters return
identical job ids" a property of the architecture instead of a bug class.

**There is no route that submits an application.** The person opens `url` in
their own browser and comes back to record what happened. Nothing here posts to
an employer, and there is no code path that could.
"""

from __future__ import annotations

import contextlib
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from career_agent.domain.application import ApplicationStatus
from career_agent.domain.enums import Seniority
from career_agent.match.places import REGIONS
from career_agent.runtime import database_ref, read_identity
from career_agent.runtime.fingerprint import runtime_fingerprint
from career_agent.runtime.mode import indicator
from career_agent.storage.mvp_repo import NOT_STATED
from career_agent.web.presenter import job_card, job_detail, utc_today
from career_agent.web.server import ApiError, LocalApp, validate_job_id
from career_agent.web.server import closing as _closing
from career_agent.web.workspace_api import WorkspaceRoutes

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _one(query: dict, key: str) -> str | None:
    values = query.get(key)
    return values[0] if values else None


def _many(query: dict, key: str) -> tuple[str, ...]:
    return tuple(query.get(key, ()))


def _int(query: dict, key: str) -> int | None:
    raw = _one(query, key)
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ApiError(400, f"{key} must be an integer") from exc


#: Everything `/api/jobs` accepts. An unknown key is a 400, not a silent
#: no-op -- see :func:`_reject_unknown` for why that mattered enough to name.
JOB_QUERY_PARAMS: frozenset[str] = frozenset(
    {
        "search",
        "company",
        "provider",
        "role_class",
        "status",
        "eligibility",
        "fit_band",
        "signal",
        "min_score",
        "max_score",
        "min_confidence",
        "saved_only",
        "enriched_only",
        "has_salary",
        "remote_only",
        "include_ineligible",
        "include_unresolved",
        "include_excluded_seniority",
        "include_excluded_work_model",
        "include_off_target",
        "include_user_hidden",
        "user_hidden_only",
        "group_duplicates",
        "posted_within_days",
        # -- the twelve section 16 listed as absent -----------------------
        "country",
        "region",
        "latam_only",
        "worldwide_only",
        "worksite",
        "seniority",
        "employment_type",
        "min_salary",
        "salary_currency",
        "salary_period",
        "technology",
        "employment_context",
        "content_completeness",
        "contract_regime",
        "keyword",
        "exclude_keyword",
        # THE SOFT PAIR. These sort; they never filter, so they are absent from
        # every count on the screen and present only in the order of the page.
        "prefer_keyword",
        "avoid_keyword",
        # -- what the posting asked for, and who it invited (migration 0027) --
        "experience_requirement",
        "experience_max_years",
        "entry_signal",
        "include_transferable",
        "sort",
        "direction",
        "limit",
        "offset",
    }
)

#: The longest free-text `search` accepted, in characters. The box in the
#: Discover toolbar cuts to the same length (`SEARCH_MAX` in `state.js`), so
#: this is only ever met by a hand-built URL, and it keeps a pasted page of
#: text from becoming a LIKE pattern scanned against every description.
SEARCH_MAX_CHARS = 200

#: The closed vocabularies, keyed by the parameter that carries them. Values
#: are compared case-insensitively and normalised to the stored spelling, so
#: `role_class=primary` works -- it used to return zero rows in silence,
#: because the column stores `PRIMARY`.
_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "role_class": (
        "PRIMARY",
        "STRONG_ADJACENT",
        "CONDITIONAL",
        "UNCLASSIFIED",
        "EXCLUDED",
    ),
    "eligibility": ("VERIFIED_ELIGIBLE", "VERIFIED_NOT_ELIGIBLE", "UNRESOLVED"),
    # HOW HARD the posting's ask for previous experience is. Five readings and
    # the honest sixth, and no two of them mean the same thing: a posting that
    # PREFERS three years and one that REQUIRES them are different jobs to
    # somebody deciding whether to apply.
    "experience_requirement": (
        "NONE_REQUIRED",
        "REQUIRED_MINIMUM",
        "REQUIRED_UNQUANTIFIED",
        "PREFERRED",
        "NICE_TO_HAVE",
        "NOT_STATED",
    ),
    # Invitations the employer WROTE. Never inferred from a low seniority band
    # or from the absence of a requirement.
    "entry_signal": (
        "NO_EXPERIENCE_REQUIRED",
        "ENTRY_LEVEL",
        "RECENT_GRADUATE",
        "TRAINING_PROVIDED",
        "CAREER_CHANGERS_WELCOME",
    ),
    # HOW A POSTING READS, and deliberately a separate vocabulary from the one
    # above it. Neither value here is a verdict about whether the candidate may
    # take the job; a query for one can never return the other.
    "employment_context": ("LIKELY_US_DOMESTIC", "INTERNATIONAL_STATED", "UNRESOLVED"),
    # PROVENANCE, never quality. `PARTIAL_CONTENT` says the SOURCE returns an
    # excerpt and offers no way to fetch the rest; it is a fact about an
    # aggregator, not a verdict on an employer.
    "content_completeness": (
        "FULL_CONTENT",
        "PARTIAL_CONTENT",
        "METADATA_ONLY",
        "UNKNOWN",
    ),
    # Two Brazilian statutes and the honest third answer.
    #
    # This read `("CLT", "PJ")` until V1.7, on the reasoning that "did not say"
    # is the absence of the filter rather than a value of it. That was
    # unarguable while the column was NULL on every row in the corpus, because
    # the filter could not do anything either way. It stopped being true the
    # day the column filled: the facet reports a `NOT_STATED` bucket holding
    # every posting outside Brazil, and a bucket a person can see and cannot
    # click is a broken control, not a principled refusal.
    #
    # `_where` maps it to `IS NULL`, exactly as it does for the five columns
    # beside it.
    "contract_regime": ("CLT", "PJ", NOT_STATED),
    "fit_band": ("STRONG", "GOOD", "MODERATE", "WEAK"),
    "status": tuple(str(s) for s in ApplicationStatus),
    "direction": ("asc", "desc"),
    "sort": ("score", "confidence", "company", "title", "posted", "status", "hidden"),
    # NOT_STATED is in the vocabulary because it is an ANSWER a person asks
    # for -- "show me the ones that never said" -- not an absence they should
    # have to notice. Stored as NULL and offered as a word; `_where` maps it
    # back to `IS NULL`.
    "worksite": ("REMOTE", "HYBRID", "ONSITE", NOT_STATED),
    "region": REGIONS,
    "seniority": tuple(str(s) for s in Seniority),
}


def _reject_unknown(query: dict, allowed: frozenset[str], where: str) -> None:
    """A query parameter we do not recognise is a 400, never a silent no-op.

    This is the fourth time this repository has been bitten by reading absence
    as permission, and it is the one the person actually felt. `_check_origin`
    once read a MISSING Host header as consent. `set_status` once read an
    omitted field as "clear the date you applied". And here, an unrecognised
    parameter name was dropped on the floor: `?companies=vanta` -- the plural,
    which is what `JobFilter` calls the field -- returned all 18,549 postings
    with no error anywhere, and looked exactly like "the filters do not work".

    A wrong VALUE was already rejected (`min_score=notanumber` was a 400). Only
    a wrong NAME was silently forgiven, which is the worse half: a typo in a
    value fails loudly, a typo in a name fails invisibly and returns the whole
    corpus dressed as a filtered result.
    """
    unknown = sorted(set(query) - allowed)
    if unknown:
        raise ApiError(
            400,
            f"unknown {where} parameter(s): {', '.join(unknown)}. "
            f"accepted: {', '.join(sorted(allowed))}",
        )


def _vocab(query: dict, key: str) -> tuple[str, ...]:
    """Values from a closed vocabulary, case-normalised, unknown ones rejected.

    "no results" and "that is not a value this system has" are different
    statements, and the person has to be able to tell them apart. Before this,
    `role_class=primary` produced an empty list that was indistinguishable from
    a real, correctly-filtered empty result.
    """
    values = _many(query, key)
    if not values:
        return ()
    accepted = _VOCABULARIES[key]
    folded = {value.casefold(): value for value in accepted}
    out: list[str] = []
    for raw in values:
        match = folded.get(raw.strip().casefold())
        if match is None:
            raise ApiError(
                400,
                f"{key} must be one of {', '.join(accepted)} -- got {raw!r}",
            )
        out.append(match)
    return tuple(out)


def _single(query: dict, key: str, default: str) -> str:
    """One value from a closed vocabulary, and exactly one.

    `?sort=score&sort=title` used to take the first and drop the second in
    silence -- the one place a repeated parameter is not multi-valued, so the
    person's second choice vanished with no signal. Two answers to a
    single-answer question is a 400.
    """
    values = _vocab(query, key)
    if not values:
        return default
    if len(values) > 1:
        raise ApiError(400, f"{key} was given more than once: {', '.join(values)}")
    return values[0]


def _bool(query: dict, key: str) -> bool:
    """A boolean flag. An unrecognised value is a 400, not a quiet False.

    `saved_only=yes` used to mean False, because anything outside `_TRUE` was
    read as "off". A filter the person switched on and the server ignored is
    the same defect as an unknown parameter name, one layer down.
    """
    raw = _one(query, key)
    if raw is None or raw == "":
        return False
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(400, f"{key} must be true or false -- got {raw!r}")


def _upper(query: dict, key: str) -> tuple[str, ...]:
    """Free-vocabulary values, upper-cased to match the stored spelling.

    Employment types, currencies and periods come from the boards rather than
    from us, so there is no closed list to validate against -- a new one
    appearing must not become a 400. Case is normalised because `full_time`
    and `FULL_TIME` are the same request and only one of them is stored.
    """
    return tuple(value.strip().upper() for value in _many(query, key) if value.strip())


def _codes(query: dict, key: str) -> tuple[str, ...]:
    """ISO 3166-1 alpha-2 country codes, with the aliases people type.

    `UK` is not a country code -- `GB` is -- and a person typing the first gets
    silence from a raw comparison. `normalise_country` already exists for
    exactly this failure and is reused rather than re-implemented; a code it
    does not recognise is a 400 naming it, never an empty result that looks
    like a correctly-filtered nothing.
    """
    from career_agent.domain.countries import UnknownCountryError, normalise_country

    out: list[str] = []
    for raw in _many(query, key):
        if not raw.strip():
            continue
        try:
            out.append(normalise_country(raw))
        except UnknownCountryError as exc:
            raise ApiError(400, f"{key}: {exc}") from exc
    return tuple(out)


def _phrases(query: dict, key: str) -> tuple[str, ...]:
    """Ad-hoc keyword phrases. Bounded, because each one is a search clause."""
    values = tuple(value.strip() for value in _many(query, key) if value.strip())
    if len(values) > 12:
        raise ApiError(400, f"{key}: at most 12 phrases")
    for value in values:
        if len(value) > 120:
            raise ApiError(400, f"{key}: a phrase longer than 120 characters is a mistake")
    return values


def _experience_years(query: dict) -> int | None:
    """The most previous experience a posting may DEMAND, in years.

    Bounded at 50 because a figure above it is a typing mistake rather than a
    search, and a negative one is not a question. Refusing names the problem;
    silently clamping would answer a question nobody asked.
    """
    value = _int(query, "experience_max_years")
    if value is None:
        return None
    if not 0 <= value <= 50:
        raise ApiError(400, "experience_max_years must be between 0 and 50")
    return value


def _technologies(query: dict, config: Any) -> tuple[str, ...]:
    """Lexicon signal ids, checked against the ones this configuration defines.

    Validated rather than passed through: an unknown signal id matches nothing,
    and "no results" and "that is not a signal this search has" are different
    statements a person has to be able to tell apart. Same rule as `_vocab`,
    against a vocabulary that lives in the configuration instead of in code.
    """
    values = tuple(value.strip() for value in _many(query, "technology") if value.strip())
    if not values:
        return ()
    known = set(_as_dict(config, "lexicon"))
    unknown = sorted(set(values) - known)
    if unknown:
        raise ApiError(
            400,
            f"technology: no such signal(s): {', '.join(unknown)}. "
            "The available ids are on /api/config.",
        )
    return values


def _tribool(query: dict, key: str) -> bool | None:
    """Three states, and the third is "the person did not ask"."""
    raw = _one(query, key)
    if raw is None or raw == "":
        return None
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise ApiError(400, f"{key} must be true or false -- got {raw!r}")


def _stage_for_source(source) -> str:
    """The `pipeline_run.stage` this source's collector writes.

    Asked of `sources/matrix.py`, which asks the provider registry, so this
    module still names no vendor.
    """
    from career_agent.sources.matrix import _stage_for

    return _stage_for(source.provider or "")


def _stage_for_source_provider(provider: str) -> str:
    from career_agent.sources.matrix import _stage_for

    return _stage_for(provider)


def _progress_dict(p, name: str = "") -> dict:
    """One source's refresh, as JSON a screen can draw without arithmetic.

    `percent` and `eta_seconds` are None far more often than not, and the
    interface must render "not measured" for them rather than a zero. Sending
    them as null rather than as 0 is what makes that possible on the other side.
    """
    return {
        "source_id": p.source_id,
        # THE NAME A PERSON READS, and the id beside it for a details view.
        #
        # `tests/browser/test_plain_language.py` failed on the first version of
        # this panel because it printed `a16z_speedrun`: a word the SYSTEM chose,
        # on a screen, which that test treats as a defect regardless of how
        # readable it looks to somebody who wrote it.
        "name": name or p.source_id,
        "state": p.state.value,
        "retrieved": p.retrieved,
        "persisted": p.persisted,
        "units_done": p.units_done,
        "expected_total": p.expected_total,
        "percent": p.percent,
        "measurable": p.measurable,
        "started_at": p.started_at,
        "last_success": p.last_success,
        "eta_seconds": p.eta_seconds,
        "blocker": p.blocker,
        "reason": p.reason,
    }


class JobsApi(WorkspaceRoutes, LocalApp):
    """`LocalApp` with the routes bound. Constructed by the `serve` command."""

    def __init__(self, config, *, quiet: bool = False) -> None:
        super().__init__(config, quiet=quiet)
        self._search_config = None
        self._search_config_path: Path | None = None
        self._bands: dict[str, dict[str, int]] = {}
        self._recency: dict[str, int] = {}
        # One runner per server, so two browser tabs cannot start two
        # collections against the same boards and double the request rate at
        # endpoints this project is deliberately polite to.
        from career_agent.pipeline.retrieval import RetrievalRunner

        self.retrieval = RetrievalRunner()
        # Its own runner, not the retrieval one. Two rescores would interleave
        # writes to `job_match`, so one at a time is right; refusing a rescore
        # because a COLLECTION is running would be a conflict that does not
        # exist. The class is generic -- it runs a callable on a thread and
        # reports on it -- so this costs one line and no new machinery.
        self.rescore = RetrievalRunner()
        #: Set by `register_semantic`; read by `start_rescore`.
        self.semantic_runner: RetrievalRunner | None = None

        self.register("GET", r"/api/health", self.health)
        self.register("GET", r"/api/config", self.config_summary)
        self.register("GET", r"/api/sources", self.sources)
        from career_agent.web.source_refresh import register_source_refresh

        register_source_refresh(self)
        from career_agent.web.first_search import register_first_search

        register_first_search(self)
        from career_agent.web.profiles import register_profiles

        #: Set by the launcher's ProfileHost; None under `serve` and the demo.
        self.profile_host = None
        register_profiles(self)
        from career_agent.web.role_anchors_api import register_role_anchors

        register_role_anchors(self)
        from career_agent.web.semantic_api import register_semantic

        register_semantic(self)
        self._active_source_refresh: str | None = None
        self.register("GET", r"/api/preferences", self.preferences)
        self.register("GET", r"/api/profile", self.profile)
        self.register("PATCH", r"/api/preferences", self.patch_preferences)
        self.register("GET", r"/api/search-review", self.search_review)
        self.register("PATCH", r"/api/search-review", self.patch_search_review)
        self.register("PATCH", r"/api/profile", self.patch_profile)
        self.register("GET", r"/api/profile/history", self.profile_history)
        self.register("POST", r"/api/profile/restore", self.restore_profile)
        self.register("GET", r"/api/retrieval", self.retrieval_status)
        self.register("POST", r"/api/retrieval", self.start_retrieval)
        self.register("POST", r"/api/retrieval/cancel", self.cancel_retrieval)
        self.register("GET", r"/api/rescore", self.rescore_status)
        self.register("POST", r"/api/rescore", self.start_rescore)
        self.register("GET", r"/api/jobs", self.list_jobs)
        self.register("GET", r"/api/jobs/(?P<job_id>[^/]+)", self.get_job)
        self.register("PATCH", r"/api/jobs/(?P<job_id>[^/]+)/status", self.patch_status)
        self.register("PATCH", r"/api/jobs/(?P<job_id>[^/]+)/applied-at", self.patch_applied_at)
        self.register("PATCH", r"/api/jobs/(?P<job_id>[^/]+)/saved", self.patch_saved)
        self.register("PATCH", r"/api/jobs/(?P<job_id>[^/]+)/hidden", self.patch_hidden)
        self.register("PATCH", r"/api/jobs/(?P<job_id>[^/]+)/notes", self.patch_notes)
        self.register("POST", r"/api/jobs/(?P<job_id>[^/]+)/enrich", self.enrich_job)
        self.register("POST", r"/api/import", self.import_job)
        # The candidate half of the product. Registered from its own module so
        # that the two sets of truth rules stay in two files.
        self.register_workspace_routes()

    def transferable_signals(self) -> tuple[str, ...]:
        """Signals this person has confirmed evidence for. Read, never stored.

        Only ever called when a request carried `include_transferable`, which
        is off by default and which a person switches on deliberately. It
        reaches exactly one SQL clause, where its effect is to STOP hiding a
        posting -- see `JobFilter.transferable_signals`.

        Empty when nothing has been confirmed, which is the state a fresh
        install is in, and which makes the control correctly do nothing rather
        than quietly do something unexplained.
        """
        from career_agent.match.preparation import transferable_signals
        from career_agent.storage.repositories import ClaimRepo
        from career_agent.storage.workspace_repo import candidate_id_of

        with _closing(self.connect()) as conn:
            candidate_id = candidate_id_of(conn)
            if not candidate_id:
                return ()
            claims = ClaimRepo(conn).current(candidate_id)
        return transferable_signals(self.search_config(), claims)

    # =================================================================
    # configuration
    # =================================================================
    def search_config(self):
        """Loaded once and cached. Reloading is a server restart, deliberately.

        A score stored under `config_version` N must keep meaning what it meant
        when it was computed; hot-reloading the file underneath a running
        server would make the displayed explanation drift from the stored
        number without anything saying so.
        """
        if self._search_config is None:
            from career_agent.config.search_config import load_search_config

            self._search_config, self._search_config_path = load_search_config(
                self.config.config_dir
            )
            thresholds = _as_dict(self._search_config, "thresholds")
            self._bands = {
                "fit": dict(
                    thresholds.get("fit_bands", {"STRONG": 75, "GOOD": 55, "MODERATE": 35})
                ),
                "confidence": dict(thresholds.get("confidence_bands", {"HIGH": 70, "MEDIUM": 45})),
            }
            self._recency = dict(_as_dict(self._search_config, "preferences").get("recency", {}))
        return self._search_config

    def _identity(self) -> tuple[str, int]:
        cfg = self.search_config()
        return _attr(cfg, "config_id"), int(_attr(cfg, "config_version"))

    def _serving(self, conn: Any):
        """Which scored population to READ FROM, which may not be the current one.

        `_identity` is the QUESTION -- the preferences in force. This is the
        ANSWER available to show, and for the minutes after a preference edit
        those are different revisions.

        The owner met that difference on 2026-09-08: she set her seniority
        preferences in the interface, her configuration went from revision 4
        to revision 6, and her Jobs list went empty because all 19,469 stored
        scores answer revision 4. Nothing was lost and the product was being
        precise, but "0 jobs" reads as "this has stopped working".

        See `storage.revisions`: it serves the previous answer, labelled, and
        never a half-built one. Read routes call this; write routes and the
        rescore itself still use `_identity`, because a new score belongs to
        the question being asked now.
        """
        from career_agent.storage import revisions

        config_id, config_version = self._identity()
        return revisions.resolve(conn, config_id, config_version)

    # =================================================================
    # routes
    # =================================================================
    def health(self, *, query: dict, body: dict) -> dict:
        _reject_unknown(query, frozenset(), "health")
        cfg = self.search_config()
        config_id, config_version = self._identity()
        with _closing(self.connect()) as conn:
            from career_agent.storage.db import schema_version
            from career_agent.storage.mvp_repo import MatchRepo

            jobs = conn.execute("SELECT COUNT(*) FROM job WHERE closed_at IS NULL").fetchone()[0]
            repo = MatchRepo(conn)
            scored = repo.count_for(config_id, config_version)
            # Rows written before `MATCH_SCHEMA_VERSION` last moved. They have
            # empty facet columns, so the country, region, worksite and salary
            # filters return nothing for them -- which looks exactly like a
            # correctly-filtered empty result unless something says otherwise.
            stale_scores = repo.stale_schema_count(config_id, config_version)
            version = schema_version(conn)
            # `config_digest` was being written and never read, which made the
            # version discipline advisory. If someone edits the configuration
            # without bumping `config_version`, `rescore` skips every row --
            # it keys on the version alone -- and the interface would show
            # scores computed from bytes that no longer exist, with nothing
            # saying so. Comparing the digests is what turns that from an
            # invisible drift into a visible one.
            digest = str(getattr(cfg, "digest", "UNRECORDED"))
            stored = repo.digests_for(config_id, config_version)
            drift = sorted(d for d in stored if d not in (digest, "UNRECORDED"))

            # Which population is this, and does it say so itself? An
            # unstamped database is reported as UNKNOWN rather than assumed
            # personal: the interface has to be able to say "this file does
            # not declare what it is", because guessing is how nineteen
            # invented postings got shown to someone who asked for their own.
            from career_agent.storage.search_index import is_current as _index_current

            search_indexed = _index_current(conn)
            identity = read_identity(conn)
            runtime = (
                indicator(conn, self.config.db_path, identity).as_dict()
                if identity is not None
                else {
                    "mode": "UNKNOWN",
                    "banner": "Unidentified database",
                    "database_ref": database_ref(self.config.db_path, "unstamped"),
                    "is_personal": False,
                    "total_active": jobs,
                    "last_retrieval_at": None,
                    "displayed": None,
                }
            )
        return {
            "ok": True,
            "runtime": runtime,
            # Whether free-text search is running on the index or on the slow
            # fallback. Built to be reported and then, for one commit, not
            # reported -- which is how a 21-second search looked like a
            # mystery instead of a stale index. The interface can now say so.
            "search_indexed": search_indexed,
            # Same discipline, one layer down: a degraded state is REPORTED,
            # never silent. `search_indexed` says the free-text index is
            # behind; this says the stored scores are.
            "stale_scores": stale_scores,
            # The file NAME, never the full path: an absolute path on Windows
            # carries the person's username, and this payload is rendered in a
            # page that gets screenshotted for a portfolio.
            "db_name": self.config.db_path.name,
            "schema_version": version,
            "config_id": config_id,
            "config_version": config_version,
            "config_path": str(self._search_config_path),
            "config_is_local": str(self._search_config_path or "").endswith(".local.yaml"),
            "job_count": jobs,
            "scored_count": scored,
            "thresholds": _as_dict(cfg, "thresholds"),
            "config_digest": digest,
            "config_drift": drift,
            # WHICH PROCESS IS ANSWERING THIS. See `runtime_fingerprint`.
            "process": runtime_fingerprint(),
            "ollama": self._ollama_status(),
        }

    def _ollama_status(self) -> dict[str, Any]:
        """What we know about the local model WITHOUT asking it.

        `configured` and `model` are read from the environment, because they
        are configuration and reading configuration is free. `reachable` stays
        **null** -- meaning "not checked" -- until a person presses Enrich.

        Null rather than false on purpose. A page load must never open a
        socket, not even to localhost, so the honest report before anyone asks
        is that we do not know. Saying `false` would be a claim we had checked,
        and the interface would then tell the user their model is down when
        nobody has looked.
        """
        if self._ollama_state.get("checked_at"):
            return self._ollama_state
        from career_agent.local_ai.ollama import OllamaSettings

        settings = OllamaSettings.from_env(dict(os.environ))
        return {
            "configured": True,
            "reachable": None,
            "model": settings.model,
            "endpoint": settings.base_url,
            "checked_at": None,
            "note": "not checked -- the local model is only contacted when you ask for it",
        }

    def config_summary(self, *, query: dict, body: dict) -> dict:
        _reject_unknown(query, frozenset(), "config")
        """Labels the interface needs so it never invents its own vocabulary."""
        cfg = self.search_config()
        lexicon = _as_dict(cfg, "lexicon")
        taxonomy = _as_dict(cfg, "taxonomy")
        config_id, config_version = self._identity()
        return {
            "config_id": config_id,
            "config_version": config_version,
            "config_path": str(self._search_config_path),
            "label": _attr(cfg, "label", ""),
            "thresholds": _as_dict(cfg, "thresholds"),
            "signals": [
                {"signal_id": key, "label": _get(value, "label", key)}
                for key, value in sorted(lexicon.items())
            ],
            "title_classes": [
                "PRIMARY",
                "STRONG_ADJACENT",
                "CONDITIONAL",
                "UNCLASSIFIED",
                "EXCLUDED",
            ],
            "statuses": [str(s) for s in ApplicationStatus],
            "taxonomy_labels": {
                cls: [_get(rule, "label", _get(rule, "id", "")) for rule in rules]
                for cls, rules in taxonomy.items()
                if cls != "fragments" and isinstance(rules, list)
            },
        }

    def sources(self, *, query: dict, body: dict) -> dict:
        """Which sources exist and what a person can actually do with each.

        In the product and not only in a document, because a capability matrix
        nobody opens is a claim rather than a feature. Every live integration
        is re-checked against the registry AND the corpus on the way out, so
        this endpoint cannot report a source as working because a YAML file
        said so.
        """
        _reject_unknown(query, frozenset(), "sources")
        from career_agent.sources.experimental import state as experimental_state
        from career_agent.sources.health import health
        from career_agent.web.source_refresh import (
            can_refresh,
            experimental_available,
            modes,
            opted_in,
        )

        with _closing(self.connect()) as conn:
            # The server's OWN config directory, not whatever folder the
            # process happens to be running in. `--config-dir` is a real flag
            # and the catalogue has to follow it like everything else.
            #
            # `health` resolves the catalogue itself and then adds what the
            # DATABASE observed: how many postings arrived, when the last one
            # did, how many boards are carrying an error. The panel used to
            # show only the declaration, so a connector that had stopped
            # returning anything looked exactly like one that was working.
            observed = health(conn, catalogue_path=self.config.config_dir / "source_catalogue.yaml")
            refresh_modes = modes(conn)
            opted = opted_in(conn, observed)
            experimental = {
                entry.source.id: experimental_state(conn, entry.source.id)
                for entry in observed
                if entry.source.experimental_provider
            }

        names = {entry.source.id: entry.source.name for entry in observed}
        counts: dict[str, int] = {}
        states: dict[str, int] = {}
        for entry in observed:
            counts[entry.source.status.value] = counts.get(entry.source.status.value, 0) + 1
            states[entry.state] = states.get(entry.state, 0) + 1
        return {
            "sources": [
                {
                    **entry.as_dict(),
                    "refresh_mode": refresh_modes.get(entry.source.id, "AUTO"),
                    "can_refresh": can_refresh(entry, opted),
                    # Only on a row that declares a local experimental
                    # override: whether THIS profile opted in, and when.
                    **(
                        {
                            "experimental": {
                                "provider": entry.source.experimental_provider,
                                "opted_in": entry.source.id in opted,
                                "available": experimental_available(
                                    entry.source.experimental_provider or ""
                                ),
                                "changed_at": experimental[entry.source.id].get("changed_at"),
                            }
                        }
                        if entry.source.id in experimental
                        else {}
                    ),
                }
                for entry in observed
            ],
            "counts": counts,
            # The five-word summary a person who is not debugging a connector
            # actually wants. `counts` above stays for the capability matrix.
            "states": states,
            # REWRITTEN 2026-09-09, because it described a policy that had
            # been reversed and was still on the screen. It said "we collect
            # from a site when the site itself says we may, and when we cannot
            # tell, we do not" -- which is the exact rule ADR-0018 replaced,
            # and by then Gupy and Programathor were both being collected under
            # the new one. A status a person reads must not describe a decision
            # the product no longer makes.
            #
            # The three facts a reader needs are the three the row now carries
            # separately: what the site said, how far the connector got, and
            # how much of a posting comes back.
            "note": (
                "A site is left alone when it says not to. When it has said "
                "nothing, jobs are collected carefully and in small amounts."
            ),
            # HOW FRESH EACH ONE IS, AND WHETHER ANYTHING IS MOVING RIGHT NOW.
            #
            # Beside the capability rows rather than instead of them, because
            # they answer different questions: `sources` says what a person can
            # do with a board at all, and this says what happened to it last
            # night and what is happening this minute. A board can be perfectly
            # capable and eight hours stale.
            "refresh": [
                _progress_dict(p, names.get(p.source_id, ""))
                for p in self._refresh_progress(observed)
            ],
        }

    def _refresh_progress(self, observed: list) -> list:
        """Where each source's retrieval stands, derived from the run ledger.

        Reads. It starts nothing, and it cannot: a screen that kicked off a
        three-hour collection because somebody opened a panel would be the
        opposite of the rule this whole area serves.
        """
        from career_agent.sources.progress import RefreshState, read_progress
        from career_agent.sources.scheduling import plan_refresh
        from career_agent.web.source_refresh import (
            effective_provider,
            experimental_available,
            modes,
            opted_in,
        )

        with _closing(self.connect()) as conn:
            opted = opted_in(conn, observed)
        running = {
            entry.source.id: provider
            for entry in observed
            if (provider := effective_provider(entry, opted)) and experimental_available(provider)
        }
        stage_for = {
            source_id: _stage_for_source_provider(provider)
            for source_id, provider in running.items()
        }
        providers = dict(running)
        if not stage_for:
            return []

        regions = {
            entry.source.id: entry.source.region for entry in observed if entry.source.id in running
        }
        # WHERE THE CANDIDATE MAY WORK, and it lives under `eligibility` rather
        # than `preferences`. The first version read `preferences.
        # eligible_countries`, which does not exist -- so every candidate got an
        # empty list, and a candidate in Brazil had the board holding 77% of
        # their own corpus paused as "a market you have not asked for".
        #
        # `candidate_country` is included because somebody who has named where
        # they live and nothing else has still told us their market. Refusing to
        # infer that would be a scheduler asking for a form to be filled in
        # twice.
        eligibility = getattr(self.search_config(), "eligibility", None)
        countries = list(getattr(eligibility, "eligible_countries", ()) or ())
        home = getattr(eligibility, "candidate_country", "") or ""
        if home:
            countries.append(home)
        with _closing(self.connect()) as conn:
            refresh_modes = modes(conn)
        paused = {
            plan.source_id: plan.reason
            for plan in plan_refresh(
                regions,
                countries=countries,
                enabled=[key for key, mode in refresh_modes.items() if mode == "ENABLED"],
            )
            if plan.paused
        }
        paused.update(
            {
                key: "Paused by your refresh preference."
                for key, mode in refresh_modes.items()
                if mode == "PAUSED"
            }
        )
        blocked = {
            entry.source.id: (
                entry.source.collection_blocker
                or entry.source.reason
                or "Source currently unavailable."
            )
            for entry in observed
            if entry.source.collection_blocker
            # An opted-in experimental override runs, so it is judged by its
            # runs (and can be paused) like any other source. Its permission
            # still reads FORBIDDEN on the row itself.
            or (entry.source.permission.value == "FORBIDDEN" and entry.source.id not in opted)
        }
        with _closing(self.connect()) as conn:
            progress = read_progress(
                conn,
                stage_for=stage_for,
                providers=providers,
                paused=paused,
                blocked=blocked,
            )
        if self._active_source_refresh and self.retrieval.running:
            return [
                replace(p, state=RefreshState.RUNNING, blocker=None)
                if p.source_id == self._active_source_refresh
                else p
                for p in progress
            ]
        snapshot = self.retrieval.snapshot()
        if self._active_source_refresh and snapshot and snapshot["status"] == "failed":
            return [
                replace(p, state=RefreshState.FAILED, blocker=snapshot.get("error"))
                if p.source_id == self._active_source_refresh
                and (
                    not p.started_at
                    or not snapshot.get("finished_at")
                    or p.started_at <= snapshot["finished_at"]
                )
                else p
                for p in progress
            ]
        return progress

    def preferences(self, *, query: dict, body: dict) -> dict:
        """The editable phrase groups, so a person never opens the YAML."""
        _reject_unknown(query, frozenset(), "preferences")
        from career_agent.config.preferences import CATEGORIES, read_signals
        from career_agent.storage.signals import reach_summary, signal_reach_details

        signals = read_signals(self.config.config_dir)
        # **HOW MANY POSTINGS EACH PHRASE GROUP ACTUALLY REACHES.**
        #
        # Without it this screen is a phrase editor operated blind: add a
        # phrase, remove a phrase, and find out what happened by rescoring
        # nineteen thousand postings and looking at the list.
        #
        # Measured on the owner's corpus 2026-09-08, the numbers it surfaces
        # are not decoration: `documentation_practice` matches 6,506 postings
        # and `selling_hubspot` matches none at all. Neither of those is a
        # verdict -- "no posting says this" and "this work does not exist" are
        # different statements, and only she can tell them apart -- but she
        # cannot tell them apart without the number.
        #
        # Counted against the SERVED population, so it describes the list she
        # is actually looking at rather than a revision nobody is being shown.
        with _closing(self.connect()) as conn:
            decision = self._serving(conn)
            served = decision.serving
            measured = (
                signal_reach_details(conn, served.config_id, served.config_version)
                if served is not None
                else None
            )
            population = measured.population if measured else 0
        return {
            "categories": [
                {
                    "id": key,
                    # The English AND the key, exactly as the profile sections
                    # do it: the key is what the catalogue renders, the English
                    # is what a caller that is not this browser gets, and
                    # without the key these three headings stayed in English
                    # under a Portuguese page.
                    "label_key": f"prefsCategory.{key}",
                    "help_key": f"prefsCategory.{key}Help",
                    **{k: v for k, v in meta.items() if k != "path"},
                }
                for key, meta in CATEGORIES.items()
            ],
            "signals": [signal.as_dict() for signal in signals],
            "reach": reach_summary(
                measured.lexical if measured else {},
                [(s.as_dict()["signal_id"], s.as_dict()["category"]) for s in signals],
                population,
                positive_body=measured.positive_body if measured else None,
            ),
            "reach_population": population,
            "reach_config_version": served.config_version if served else None,
            "config_version": self._identity()[1],
            "is_local": str(self._search_config_path or "").endswith(".local.yaml"),
        }

    def search_review(self, *, query: dict, body: dict) -> dict:
        from career_agent.config.search_review import review_search

        _reject_unknown(query, frozenset(), "search review")
        return review_search(self.config.config_dir)

    def patch_search_review(self, *, query: dict, body: dict) -> dict:
        from career_agent.config.preferences import PreferenceError
        from career_agent.config.search_review import apply_review, plan_review

        _reject_unknown(query, frozenset(), "search review")
        allowed = {"path", "action", "expected_hash", "value", "confirmation"}
        if set(body) - allowed:
            raise ApiError(400, "Unknown search-review fields")
        for key in ("path", "action", "expected_hash"):
            if not isinstance(body.get(key), str):
                raise ApiError(400, f"{key} is required")
        try:
            if "confirmation" not in body:
                _, plan = plan_review(self.config.config_dir, **body)
                return {"preview": True, **plan}
            if not isinstance(body["confirmation"], str):
                raise ApiError(400, "confirmation must identify the current preview")
            result = apply_review(self.config.config_dir, **body)
        except PreferenceError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc
        self._search_config = None
        return {"ok": True, **result}

    # =====================================================================
    # THE CAREER PROFILE
    # =====================================================================

    def profile(self, *, query: dict, body: dict) -> dict:
        """What Career Agent believes this person is looking for, in one place.

        Assembled from data that ALREADY EXISTS. No new schema, no new store
        and no new semantics: every row below is a value the matcher reads, and
        the point of the screen is that somebody should never have to open a
        YAML file to find out what the product thinks of them.

        Three rules shape it.

        **A section with no data is absent, not empty.** An interface that
        renders "Languages: --" is describing a feature nobody built, and this
        product has enough real unknowns to report without inventing display
        ones.

        **Editable where a safe writer exists, read-only where it does not.**
        The phrase groups already have `PATCH /api/preferences`, which writes
        to `search.local.yaml` and bumps `config_version`. Everything else is
        marked `editable: false` and says which file holds it, because a second
        write path is how two files start disagreeing.

        **A duplicated fact shows the disagreement rather than a winner.** The
        candidate profile and the search configuration both carry a country, a
        contract preference and a pay target. When both exist and differ, the
        response says so and names both sources. It does not choose; choosing
        is the ownership migration, and that is not this screen's decision.
        """
        _reject_unknown(query, frozenset(), "profile")
        from career_agent.config.preferences import CATEGORIES, read_signals

        config = self.search_config()
        path_name = (self._search_config_path or Path("search.yaml")).name
        signals = [signal.as_dict() for signal in read_signals(self.config.config_dir)]
        by_category: dict[str, list[dict]] = {}
        for signal in signals:
            by_category.setdefault(signal["category"], []).append(signal)

        sections: list[dict] = []

        def add(section_id: str, label: str, lead: str, rows: list[dict], **extra) -> None:
            """A section reaches the screen only when it has something to say.

            `label` and `lead` are sent as ENGLISH and as a KEY. The English is
            what a caller that is not this browser gets -- the CLI reads the
            same payload -- and the key is what the catalogue renders, so a
            Portuguese reader stops getting an English page inside a
            translated shell.

            The keys are derived from the section id rather than passed,
            because a heading and its lede belong to the section and a third
            argument nobody could forget is better than one they could.
            """
            if rows:
                key = section_id.replace("-", "_")
                sections.append(
                    {
                        "id": section_id,
                        "label": label,
                        "label_key": f"profileSection.{key}",
                        "lead": lead,
                        "lead_key": f"profileSection.{key}Lead",
                        "rows": rows,
                        **extra,
                    }
                )

        # -- what this search IS ------------------------------------------
        #
        # ONE ROW, AND IT IS HERS. There was a second one, "Reading settings
        # from: search.local.yaml", under a lede reading "The file these
        # answers come from" -- a filename, in a directory she has never
        # opened, on a page called "Your career profile". Which file holds the
        # answers is a fact about this program, and `career-agent
        # search-config` is where somebody who needs it asks. The payload
        # still carries `source`, so a caller that is not this browser can
        # still see the path; what changed is that the page stopped leading
        # with it.
        about = [
            {
                "label": "You call this search",
                "label_key": "profileRow.searchName",
                "value": config.label,
            },
        ]
        add("about", "About your search", "The name this search goes by.", about)

        # -- the three phrase groups, which ARE editable -------------------
        for category, meta in CATEGORIES.items():
            rows = [
                {
                    "label": signal["label"],
                    "value": ", ".join(signal["patterns"][:8]),
                    "count": len(signal["patterns"]),
                    "signal_id": signal["signal_id"],
                }
                for signal in by_category.get(category, [])
            ]
            add(
                f"signals-{category}",
                str(meta["label"]),
                str(meta["help"]),
                rows,
                editable=True,
                category=category,
            )

        # -- where you can work -------------------------------------------
        eligibility = config.eligibility
        place = []
        if eligibility.candidate_country:
            place.append(
                {
                    "label": "You are in",
                    "label_key": "profileRow.youAreIn",
                    "value": eligibility.candidate_country_label or eligibility.candidate_country,
                }
            )
        if eligibility.eligible_scopes or eligibility.eligible_countries:
            place.append(
                {
                    "label": "You can be hired from",
                    "label_key": "profileRow.hiredFrom",
                    "value": ", ".join(
                        [str(s) for s in eligibility.eligible_scopes]
                        + list(eligibility.eligible_countries)
                    ),
                }
            )
        add(
            "place",
            "Where you can work",
            "A posting whose stated hiring region includes none of these fails the "
            "geography gate. A posting "
            "that says nothing stays unresolved -- silence is not a rejection.",
            place,
            file=path_name,
        )

        # -- what would rule you out ---------------------------------------
        blockers = [
            {
                "label": blocker.label,
                "value": f"{len(blocker.patterns)} phrases",
                "gate": blocker.gate,
            }
            for blocker in eligibility.blockers
        ]
        add(
            "blockers",
            "What rules a job out",
            "Things an employer states that you cannot meet. Each one has to quote "
            "the sentence it fired on, so nothing is ruled out on a guess.",
            blockers,
            file=path_name,
        )

        # -- the shape of the work ----------------------------------------
        preferences = config.preferences
        shape = []
        if preferences.remote.accepted_work_models:
            shape.append(
                {
                    "label": "Ways of working you prefer",
                    "label_key": "profileRow.workModels",
                    "value": ", ".join(preferences.remote.accepted_work_models),
                }
            )
        if preferences.remote.avoided_work_models:
            shape.append(
                {
                    "label": "Ways of working you would rather avoid",
                    "label_key": "profileRow.workModelsAvoided",
                    "value": ", ".join(preferences.remote.avoided_work_models),
                }
            )
        if preferences.remote.excluded_work_models:
            shape.append(
                {
                    "label": "Ways of working never shown in Discover",
                    "label_key": "profileRow.workModelsExcluded",
                    "value": ", ".join(preferences.remote.excluded_work_models),
                }
            )
        if preferences.contract.preferred:
            shape.append(
                {
                    "label": "Contracts you prefer",
                    "label_key": "profileRow.contractsPreferred",
                    "value": ", ".join(preferences.contract.preferred),
                }
            )
        if preferences.contract.unwanted:
            shape.append(
                {
                    "label": "Contracts you do not want",
                    "label_key": "profileRow.contractsUnwanted",
                    "value": ", ".join(preferences.contract.unwanted),
                }
            )
        if preferences.seniority.preferred:
            shape.append(
                {
                    "label": "Levels you are looking for",
                    "label_key": "profileRow.levels",
                    "value": ", ".join(str(level) for level in preferences.seniority.preferred),
                }
            )
        shape.append(
            {
                "label": "Travel you will accept",
                "label_key": "profileRow.travel",
                # The VALUE is a sentence too, and the only one on this screen
                # that is. Its number rides as text inside the key's parameter
                # because the row's other values are configuration strings and
                # this list is typed `dict[str, str]` for that reason.
                "value_key": "profileValue.travelUpTo",
                "value_pct": str(preferences.travel.max_tolerated_pct),
                "value": f"up to {preferences.travel.max_tolerated_pct}% of the time",
            }
        )
        add(
            "shape",
            "The shape of the work",
            "Preferences, not gates. A posting that disagrees loses points and stays visible.",
            shape,
            file=path_name,
        )

        # -- pay ------------------------------------------------------------
        money = preferences.compensation
        pay = []
        if money.target_monthly_amount:
            pay.append(
                {
                    "label": "You are aiming for",
                    "label_key": "profileRow.aimingFor",
                    "value": f"{money.currency} {money.target_monthly_amount:,.0f} per "
                    f"{money.period.lower()}",
                }
            )
            pay.append(
                {
                    "label": "Cross-currency comparison",
                    "label_key": "profileRow.crossCurrency",
                    "value": (
                        f"rates recorded {money.conversion_rates_dated}"
                        if money.conversion_rates_dated
                        else "off, because no dated exchange rate is configured"
                    ),
                }
            )
        add(
            "pay",
            "Pay",
            "Worth at most five points, and never a reason to rule a job out. A "
            "posting that states no salary is not penalised for it.",
            pay,
            file=path_name,
        )

        return {
            "source": {
                "file": path_name,
                "is_local": path_name.endswith(".local.yaml"),
                "config_id": config.config_id,
                "config_version": config.config_version,
            },
            "sections": sections,
            # What a person may change here, described by the same table that
            # validates the write. A control the backend would refuse is worse
            # than no control at all.
            "editable": self._editable_fields(),
            # `BR -> Brazil`, `LATAM -> Latin America`. The profile stores ISO
            # codes, which is right, and used to SHOW them: the country field
            # was a seven-character box reading `BR` and the regions were chips
            # reading `LATAM` and `EMEA`. A person answering "where do you
            # live" should not have to know the code for their own country.
            #
            # From the gazetteer, the same file that decides which codes can
            # exist, because a second list here would drift from it the first
            # time a country was added.
            "place_names": {
                "countries": self._country_labels(),
                "regions": self._region_labels(),
            },
            **self._candidate_profile_state(config),
            **self._profile_revision(),
        }

    def _profile_revision(self) -> dict:
        """Which revision of HER OWN ANSWERS the product is reading.

        A third number beside `config_version` and `MATCH_SCHEMA_VERSION`, and
        the one that was missing: `config_version` moves when a weight changes
        AND when she says where she lives, so it could not tell those apart.

        Absent rather than zero when nothing has been recorded. A revision of
        `0` reads as "you have answered nothing", and she may have answered
        everything and simply never run `migrate-profile`.
        """
        from career_agent import profile_history

        with _closing(self.connect()) as conn:
            latest = profile_history.latest(conn)
        if latest is None:
            return {"profile_revision": None}
        return {
            "profile_revision": {
                "number": latest.number,
                "recorded_at": latest.created_at,
                "digest": latest.content_hash[:16],
            }
        }

    def _candidate_profile_state(self, config) -> dict:
        """Whether a candidate profile exists, and whether it agrees.

        `profile.local.yaml` is deliberately absent on the owner's machine, so
        the common answer is "there is none" -- and that is reported as a state
        rather than as an error, because M2 is candidate-independent by design.

        When one DOES exist, any fact both files carry is compared. A
        disagreement is shown with both values and both filenames; it is never
        resolved here. The Candidate Profile owns facts about the person and
        the search configuration owns the machinery, and moving a fact between
        them is a migration rather than a screen.
        """
        from career_agent.config.consistency import OWNERSHIP_RULE, divergences
        from career_agent.config.loader import ConfigError, load_profile

        try:
            profile, profile_path = load_profile(self.config.config_dir)
        except ConfigError:
            return {
                "candidate_profile": {
                    "present": False,
                    "note": (
                        "No candidate profile on this machine, which is the expected "
                        "state: everything above comes from your search settings."
                    ),
                    # The SEMANTIC key beside the English, as everything else
                    # on this screen now travels. The English is what a caller
                    # that is not this browser gets -- `doctor` prints it.
                    "note_key": "profile.noneOnThisMachine",
                },
                "divergences": [],
                "ownership_rule": OWNERSHIP_RULE,
                "ownership_rule_key": "profile.ownershipRule",
            }

        found = divergences(profile, config)
        return {
            "candidate_profile": {
                "present": True,
                "file": profile_path.name,
                "candidate_key": profile.candidate_key,
                "note": (
                    "Both files describe you. Where they disagree, the matcher reads "
                    "the search settings -- and the disagreement is shown rather than "
                    "resolved."
                ),
                "note_key": "profile.bothDescribeYou",
            },
            "divergences": [
                {
                    "subject": divergence.subject,
                    "profile_value": divergence.profile_value,
                    "profile_path": divergence.profile_path,
                    "config_value": divergence.config_value,
                    "config_path": divergence.config_path,
                }
                for divergence in found
            ],
            "ownership_rule": OWNERSHIP_RULE,
            "ownership_rule_key": "profile.ownershipRule",
        }

    def patch_preferences(self, *, query: dict, body: dict) -> dict:
        """Replace one signal's phrases, writing to the LOCAL file.

        Never to the committed example: that is the shipped baseline and the
        thing `git checkout` restores when an edit goes wrong. Every write
        bumps `config_version`, because a score is only true relative to the
        configuration that produced it -- and the response says so, so the
        interface can tell the person their existing scores are now stale.
        """
        _reject_unknown(query, frozenset(), "preferences")
        from career_agent.config.preferences import PreferenceError, set_patterns

        category = body.get("category")
        signal_id = body.get("signal_id")
        patterns = body.get("patterns")
        if not isinstance(category, str) or not isinstance(signal_id, str):
            raise ApiError(400, "category and signal_id are required")
        if not isinstance(patterns, list):
            raise ApiError(400, "patterns must be a list of phrases")
        if len(patterns) > 200:
            raise ApiError(400, "a signal with more than 200 phrases is a mistake")

        try:
            path, version = set_patterns(self.config.config_dir, category, signal_id, patterns)
        except PreferenceError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc

        # The cached config is now behind the file.
        self._search_config = None
        return {
            "ok": True,
            "written_to": path.name,
            "config_version": version,
            "rescore_required": True,
            "note": (
                "Saved. Your existing matches were worked out from your older "
                "preferences, so they need recalculating against these."
            ),
        }

    def _editable_fields(self) -> list[dict]:
        """Which candidate choices the panel may change, with their options.

        Sent by the SERVER rather than hard-coded in the browser, because a
        control the backend will refuse is worse than no control: it saves,
        fails, and teaches the reader that the panel is unreliable. The
        whitelist that validates a write is the same table that describes it.
        """
        from career_agent.config.candidate_writer import FIELDS, current_candidate_fields

        current = current_candidate_fields(self.config.config_dir)
        return [
            {
                "field": name,
                "label": spec["label"],
                "label_key": f"field.{name}",
                "kind": spec["kind"],
                "choices": list(spec.get("choices", ())),
                "value": current.get(name),
                "allow_empty": bool(spec.get("allow_empty")),
            }
            for name, spec in FIELDS.items()
        ]

    def patch_profile(self, *, query: dict, body: dict) -> dict:
        """Change the candidate's own choices, one save at a time.

        Everything the request may name is in `candidate_writer.FIELDS`, and
        that whitelist is the security property rather than a convenience: this
        body arrives from a browser, and a dotted path taken from it could
        otherwise name `scoring.components` and rewrite how matching works from
        a settings panel.

        The write is atomic and validated through the real loader first, so a
        rejected value leaves the previous search exactly as it was rather than
        producing a file every later command refuses to read.
        """
        _reject_unknown(query, frozenset(), "profile")
        from career_agent.config.candidate_writer import set_candidate_fields
        from career_agent.config.preferences import PreferenceError

        changes = body.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise ApiError(400, "changes must be an object with at least one field")
        from career_agent.config.candidate_writer import FIELDS

        if len(changes) > len(FIELDS):
            raise ApiError(400, "more fields than this panel has")

        try:
            path, version, applied = set_candidate_fields(self.config.config_dir, changes)
        except PreferenceError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc

        # The cached configuration is now behind the file.
        self._search_config = None

        # AND HER ANSWERS HAVE A NEW REVISION. Recorded here rather than inside
        # `candidate_writer`, which is a configuration module and has no
        # database: a config layer that opened a connection to record a version
        # would be a second thing that file-writing does, and the failure mode
        # -- a save that half-worked -- is worse than a history with a gap.
        #
        # Content-hashed, so a save that changed nothing records nothing.
        recorded: dict[str, object] | None = None
        try:
            from career_agent import profile_history
            from career_agent.storage.db import transaction

            with _closing(self.connect()) as conn, transaction(conn):
                revision = profile_history.record(conn, self.config.config_dir)
            recorded = {"number": revision.number, "new": revision.recorded}
        except Exception:  # noqa: BLE001
            # A history that could not be written must never fail a save. The
            # settings ARE written by this point -- atomically, validated -- and
            # refusing the request now would tell her the change did not happen
            # when it did.
            recorded = None
        return {
            "ok": True,
            "written_to": path.name,
            "config_version": version,
            "changed": applied,
            "profile_revision": recorded,
            "rescore_required": True,
            "note": (
                "Saved. Your existing matches were worked out from your older "
                "settings, so they need recalculating against these."
            ),
        }

    def profile_history(self, *, query: dict, body: dict) -> dict:
        """Every recorded state of her own answers, newest first.

        `search_profile_version` has held these since migration 0001 and
        `profile_history` has been filling it since V1.4; nothing had ever read
        them back. They are the only record of what her preferences USED to
        say, which is what a rollback needs and what a diff is made of.

        The answers themselves are not returned. A revision list is a
        navigation aid, and shipping every candidate answer in a payload the
        Jobs screen also polls would put her whole profile on the wire for a
        control that only needs dates and a count. `changed` says what moved,
        which is the part anybody can act on.
        """
        _reject_unknown(query, frozenset(), "profile/history")
        from career_agent.profile_history import changed_between, history

        with _closing(self.connect()) as conn:
            # `history` returns OLDEST FIRST and says so. The diff for a
            # revision is against the one BEFORE it in that order, and the
            # current one is the LAST. Reading it the other way round inverts
            # every `from`/`to` pair and points a rollback at the revision
            # already in force -- which is what the first version of this did,
            # silently, and what the tests caught.
            revisions = history(conn)

        out = []
        for index, revision in enumerate(revisions):
            previous = revisions[index - 1] if index else None
            # Named both ways round on purpose. "Your profile changed" is not
            # something anybody can act on; "levels to keep off the list:
            # none -> Lead, Staff, Principal" is.
            moved = changed_between(previous, revision) if previous else {}
            out.append(
                {
                    "number": revision.number,
                    "created_at": revision.created_at,
                    "content_hash": revision.content_hash[:16],
                    "changed": {
                        field: {
                            "from": before,
                            "to": after,
                            # **ABSENT AND NULL ARE DIFFERENT.**
                            # `changed_between` reads both through `.get`, so
                            # a field that did not EXIST in the older snapshot
                            # and one that existed holding nothing both arrive
                            # as None. On the owner's own history that renders
                            # `seniority_excluded: None -> [...]` for a field
                            # whose real previous value was an empty list --
                            # it was added to the writer between recordings.
                            #
                            # The screen needs to say "not recorded then"
                            # rather than "was empty", and only the snapshot's
                            # keys can tell it which.
                            "existed_before": previous is not None and field in previous.answers,
                        }
                        for field, (before, after) in moved.items()
                    },
                    # A revision the recorder wrote whose visible answers match
                    # the one before it. Content-addressed recording saw a real
                    # difference -- usually a field appearing for the first
                    # time -- that a field-by-field diff cannot show. Saying so
                    # beats rendering a revision with a blank space under it.
                    "changed_nothing_visible": bool(previous) and not moved,
                    "is_current": index == len(revisions) - 1,
                }
            )
        # Newest first for the reader. The chronological order above is what
        # the diffs are computed in; this is what a list on a screen wants.
        out.reverse()
        return {"revisions": out, "count": len(out)}

    def restore_profile(self, *, query: dict, body: dict) -> dict:
        """Put a previous set of answers back, as a NEW revision.

        **History is never rewritten.** Going back to revision 2 writes
        revision 5 carrying revision 2's answers; the intervening ones stay
        exactly where they are. A product that erased them would make "undo"
        the one action with no undo of its own.

        It goes through `set_candidate_fields` like every other edit, so the
        values are validated through the real loader before anything is
        written, the write is atomic, and a value that has since become
        invalid is refused rather than restored into a file every later
        command would fail to read.

        And it costs a recalculation, like any other preference change. The
        results on screen stay usable while that runs -- see ADR-0017 -- which
        is what makes going back a safe thing to try rather than a decision.
        """
        _reject_unknown(query, frozenset(), "profile/restore")
        from career_agent.config.candidate_writer import FIELDS, set_candidate_fields
        from career_agent.config.preferences import PreferenceError
        from career_agent.profile_history import history

        number = body.get("number")
        if not isinstance(number, int):
            raise ApiError(400, "number must be the revision to restore")

        with _closing(self.connect()) as conn:
            revisions = history(conn)
        wanted = next((r for r in revisions if r.number == number), None)
        if wanted is None:
            raise ApiError(404, f"no revision {number}")
        if revisions and revisions[-1].number == number:
            # Restoring what is already in force would bump the version and
            # ask for a rescore over a change nobody made.
            raise ApiError(400, "that revision is already the current one", for_reader=True)

        # Only what a screen may write. A recorded revision covers every
        # candidate-owned fact, and `ownership.FACTS` deliberately includes
        # facts that are hers but not editable from here; restoring one of
        # those would be this route reaching past the whitelist that exists
        # to stop exactly that.
        changes = {
            field: value
            for field, value in wanted.answers.items()
            if field in FIELDS and value is not None
        }
        if not changes:
            raise ApiError(400, "that revision holds nothing this panel may write", for_reader=True)

        try:
            path, version, applied = set_candidate_fields(self.config.config_dir, changes)
        except PreferenceError as exc:
            raise ApiError(400, str(exc), for_reader=True) from exc

        self._search_config = None
        return {
            "ok": True,
            "restored": number,
            "written_to": path.name,
            "config_version": version,
            "fields": applied,
            "rescore_required": True,
        }

    def retrieval_status(self, *, query: dict, body: dict) -> dict:
        """The funnel, and what the last run did. Safe to poll.

        The funnel is counted from the database on every call rather than
        cached from the end of a run, so it stays true after an import, a
        rescore, or a preference change -- none of which are retrievals.
        """
        _reject_unknown(query, frozenset({"funnel"}), "retrieval")
        from career_agent.pipeline.retrieval import build_funnel, now_iso

        # `funnel=false` is for the progress poll. The funnel is seven COUNT
        # queries over the whole corpus, and a poll every two seconds only
        # needs the run, which is held in memory.
        with_funnel = _one(query, "funnel") is None or _bool(query, "funnel")
        config_id, config_version = self._identity()
        cfg = self.search_config()
        shortlist = int(_as_dict(cfg, "thresholds").get("shortlist_min_score", 55))
        with _closing(self.connect()) as conn:
            funnel = (
                build_funnel(conn, config_id, config_version, shortlist) if with_funnel else None
            )
            from career_agent.runtime import read_identity

            identity = read_identity(conn)
        run = self.retrieval.snapshot()
        scoring = self.rescore.snapshot()
        return {
            "funnel": funnel,
            "shortlist_min_score": shortlist,
            "last_retrieval_at": identity.last_retrieval_at if identity else None,
            "running": self.retrieval.running,
            "run": run,
            # Scoring follows a collection on its own runner. Reported here so
            # one poll can say "reading sources" and then "scoring what came
            # in" without the page watching two endpoints.
            "scoring": {
                "running": self.rescore.running,
                "done": int(scoring["boards_done"]) if scoring else 0,
                "total": int(scoring["boards_total"]) if scoring else 0,
            },
            # The server's clock, so elapsed time is measured against the
            # clock that stamped `started_at` rather than the browser's.
            "now": now_iso(),
        }

    def start_retrieval(self, *, query: dict, body: dict) -> dict:
        """Begin a collection pass. Returns immediately; poll for progress.

        THIS IS THE ONLY ROUTE THAT TOUCHES THE NETWORK, and it only reaches
        the three documented, unauthenticated ATS APIs through the existing
        collector -- the same code path `career-agent collect` uses, with the
        same politeness delay. It sends nothing about the person, applies to
        nothing, and logs in nowhere.
        """
        _reject_unknown(query, frozenset(), "retrieval")
        from career_agent.clock import new_id

        if self.retrieval.running:
            raise ApiError(409, "a retrieval is already running", for_reader=True)
        # Demo mode collects nothing: the demo corpus is invented, and putting
        # real postings in it is the mixing the runtime modes exist to stop.
        from career_agent.runtime import RuntimeMode, read_identity

        with _closing(self.connect()) as conn:
            identity = read_identity(conn)
        if identity is None or identity.kind is not RuntimeMode.PERSONAL:
            raise ApiError(
                409,
                "retrieval only runs against a personal database; this one is "
                f"{identity.kind.value.lower() if identity else 'unidentified'}",
                # It names the database she is looking at and why the button
                # did nothing, which is more than a generic refusal could say.
                for_reader=True,
            )

        limit = _int(body, "board_limit") if isinstance(body.get("board_limit"), int) else None
        return self.retrieval.start(self._collect_work(limit), new_id())

    def rescore_status(self, *, query: dict, body: dict) -> dict:
        """What the current or last rescore did. Safe to poll.

        `scored` comes from the database rather than from the run, so the
        answer is right before any rescore has ever happened and stays right
        after one that was interrupted.
        """
        _reject_unknown(query, frozenset(), "rescore")
        config_id, config_version = self._identity()
        with _closing(self.connect()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM job_match WHERE config_id = ? AND config_version = ?",
                (config_id, config_version),
            ).fetchone()
        return {
            "running": self.rescore.running,
            "run": self.rescore.snapshot(),
            "scored": int(row[0]) if row else 0,
            "config_version": config_version,
        }

    def start_rescore(self, *, query: dict, body: dict) -> dict:
        """Score every open posting against the current preferences.

        MAKES NO NETWORK CALL AND NO INFERENCE CALL OF EITHER KIND. This is
        the same `pipeline.rescore` the CLI runs, on a background thread, and
        it is the reason the empty state can now offer an action rather than
        an instruction to open a terminal.

        Unlike retrieval this runs in demo mode too. A demo corpus is invented
        postings scored by the same deterministic code, and rescoring it
        touches nothing outside the file it was seeded into -- so the runtime
        check that keeps real postings out of a demo database has nothing to
        protect here.
        """
        _reject_unknown(query, frozenset(), "rescore")
        from career_agent.clock import new_id

        if self.rescore.running:
            raise ApiError(409, "a rescore is already running", for_reader=True)
        semantic = getattr(self, "semantic_runner", None)
        if semantic is not None and semantic.running:
            # A semantic run ends with its own rescore of what it evaluated.
            raise ApiError(409, "Semantic matching is still running.", for_reader=True)
        return self.rescore.start(self._rescore_work(), new_id())

    def _rescore_work(self):
        """The unit the runner executes on its thread.

        Its own connection, opened inside the thread: the sqlite3 objects this
        module hands around are not shared across threads, and a request
        handler's connection is closed the moment it returns.
        """

        def work(state, cancel) -> None:
            from career_agent.pipeline.rescore import rescore as run_rescore

            config = self.search_config()
            state.boards_total = 0
            with _closing(self.connect()) as conn:

                def progress(stats) -> None:
                    # `boards_done` is the runner's generic progress counter;
                    # here it counts postings. The field is named for the job
                    # the class was written for and the interface reads a
                    # fraction, so renaming it would be a schema change for a
                    # word nobody sees. The total is what the PLAN chose, not
                    # the corpus: after a preference change that is every
                    # posting, after a collection it is the new ones, and
                    # when nothing moved it is zero and the pass is seconds.
                    state.boards_total = int(stats.jobs_targeted)
                    state.boards_done = int(stats.jobs_scored)

                # TARGETED, not forced (ADR-0025). A preference edit bumps the
                # configuration version, so every posting is missing a current
                # score and all of them are targeted -- the same work `force`
                # did. Pressed with nothing to do, this now costs seconds.
                from career_agent.semantic.settings import load_settings

                semantic = load_settings(self.config.config_dir).uses_findings
                stats = run_rescore(conn, config, progress=progress, semantic=semantic)

            state.funnel = {
                "considered": stats.jobs_considered,
                "targeted": stats.jobs_targeted,
                "scored": stats.jobs_scored,
                "no_description": stats.jobs_skipped_no_description,
                "shortlisted": stats.shortlisted,
                "errors": stats.errors,
                "mode": stats.mode,
            }

        return work

    def cancel_retrieval(self, *, query: dict, body: dict) -> dict:
        """Stop after the board in flight. Never mid-board."""
        _reject_unknown(query, frozenset(), "retrieval")
        return {"cancelled": self.retrieval.cancel()}

    def _collect_work(self, board_limit: int | None, *, provider: str | None = None):
        """The unit the runner executes on its thread.

        Built here so the runner stays free of collection concerns and the
        tests can drive the lifecycle without a network.
        """

        def work(state, cancel) -> None:
            if provider is None:
                self._active_source_refresh = None
            from career_agent.net.fetcher import HttpFetcher
            from career_agent.pipeline.collect import Collector
            from career_agent.pipeline.retrieval import (
                build_funnel,
                provider_by_board,
                source_outcomes_from_db,
            )
            from career_agent.runtime.mode import record_retrieval
            from career_agent.storage.db import transaction

            config_id, config_version = self._identity()
            shortlist = int(
                _as_dict(self.search_config(), "thresholds").get("shortlist_min_score", 55)
            )
            conn = self.connect()
            try:
                providers = provider_by_board(conn)
                from career_agent.sources.health import health
                from career_agent.web.source_refresh import modes

                selected = None
                if provider:
                    selected = {key for key, name in providers.items() if name == provider}
                else:
                    preferences = modes(conn)
                    paused_providers = {
                        e.source.provider
                        for e in health(
                            conn, catalogue_path=self.config.config_dir / "source_catalogue.yaml"
                        )
                        if preferences.get(e.source.id) == "PAUSED"
                    }
                    if paused_providers:
                        selected = {
                            key for key, name in providers.items() if name not in paused_providers
                        }
                if selected is not None:
                    providers = {key: name for key, name in providers.items() if key in selected}
                state.boards_total = len(providers)

                def progress(done: int, total: int, board: object) -> None:
                    # Written straight onto the shared state the status route
                    # reads. Ints on a dataclass under CPython, so no lock is
                    # needed for the reader to see a coherent number.
                    state.boards_done = done
                    state.boards_total = total

                with HttpFetcher(request_delay_seconds=1.0) as fetcher:
                    stats = Collector(conn, fetcher).collect_all(
                        use_cache=True,
                        on_board=progress,
                        should_stop=cancel.is_set,
                        board_ids=selected,
                    )
                state.boards_done = int(getattr(stats, "boards_attempted", 0))
                state.sources = source_outcomes_from_db(conn, stats, providers, state.started_at)
                with transaction(conn):
                    record_retrieval(conn)
                state.funnel = build_funnel(conn, config_id, config_version, shortlist)
            finally:
                conn.close()
            # COLLECT, MARK, SCORE WHAT WAS MARKED. The postings this pass
            # inserted or changed sit in the dirty ledger; scoring them is
            # proportional to their number, so a collection no longer leaves
            # new postings unscored until somebody remembers a command. One
            # rescore at a time, through its own runner: if one is already
            # running it planned before these marks existed, and the marks
            # wait for the next pass, which the revision notice offers.
            if not self.rescore.running:
                from career_agent.clock import new_id

                # Somebody may press Recalculate between the check and the
                # start; that pass, or the next, reads the marks.
                with contextlib.suppress(RuntimeError):
                    self.rescore.start(self._rescore_work(), new_id())

        return work

    def list_jobs(self, *, query: dict, body: dict) -> dict:
        from career_agent.storage.mvp_repo import ScoredJobQuery

        job_filter = self._filter_from(query)
        with _closing(self.connect()) as conn:
            # **THE POPULATION TO READ, WHICH MAY ANSWER THE PREVIOUS
            # PREFERENCES.** See `_serving`. One revision, never a blend, and
            # never a half-built one; `revision` in the payload is how the
            # screen knows to say which question these jobs answer.
            decision = self._serving(conn)
            from career_agent.pipeline.rescore import rescore_in_progress

            # Partly scored is not being scored: a pass killed mid-run leaves
            # the same rows as one still working. Only a live runner here, or
            # a recent heartbeat from another process, is "recalculating".
            scoring_now = decision.is_building and (
                self.rescore.running or rescore_in_progress(conn)
            )
            config_id, config_version = self._identity()
            if decision.serving is not None:
                config_id = decision.serving.config_id
                config_version = decision.serving.config_version
            repo = ScoredJobQuery(conn)
            total = repo.count(config_id, config_version, job_filter)
            rows = repo.page(config_id, config_version, job_filter)
            facets = repo.facets(config_id, config_version, job_filter)
            # Counted against the same filter with one field flipped, so it is
            # exactly "how many more you would see if you asked to". `total`,
            # the page and the facets all already reflect the narrowing,
            # because it is applied in `_where` and every one of the three
            # reads goes through it.
            # `total` is passed in rather than recounted. It is the count of
            # exactly this filter, three lines up.
            hidden = repo.hidden_by_eligibility(
                config_id, config_version, job_filter, narrow_total=total
            )
            # And, separately, how many the person's own SEARCH set aside. Two
            # numbers because they are two sentences: an employer ruled you
            # out, and your search ruled the work out. One number covering both
            # is the conflation that once put a rejection notice on three
            # postings that had none.
            off_target = repo.hidden_by_screening(
                config_id, config_version, job_filter, narrow_total=total
            )
            # And the third: postings where NOTHING IN THE POSTING answered
            # whether she could take it. Three sentences, three counts, and
            # only the first of them is a rejection.
            unresolved = repo.hidden_unresolved(
                config_id, config_version, job_filter, narrow_total=total
            )
            # And the fourth: levels she said not to show her. The only one of
            # the four that is a preference rather than a fact about a posting.
            wrong_level = repo.hidden_by_seniority(
                config_id, config_version, job_filter, narrow_total=total
            )
            # And ways of working she said never to show. A preference too.
            wrong_work_model = repo.hidden_by_work_model(
                config_id, config_version, job_filter, narrow_total=total
            )
            # And, separately again, how many SHE hid. Three counts because
            # they are three sentences and three different ways back: a
            # control in the rail, a second control in the rail, and a restore
            # view listing exactly the postings she set aside herself.
            user_hidden = repo.hidden_by_the_candidate(
                config_id, config_version, job_filter, narrow_total=total
            )
            # How many rows grouping folded away, so the three numbers on the
            # screen reconcile. The header says "19 jobs", the notice says "3
            # hidden" and the list said "14 roles"; 19 minus 3 is 16, and
            # nothing accounted for the other two. They are the same role
            # posted in three cities.
            #
            # Computed HERE, inside the connection, and not in the returned
            # dictionary. That is where it was first written, and the
            # dictionary is built after `_closing` has shut the connection:
            # "Cannot operate on a closed database", from three tests at once.
            grouped_away = (
                repo.count(config_id, config_version, replace(job_filter, group_duplicates=False))
                - total
                if job_filter.group_duplicates
                else 0
            )
        today = utc_today()
        return {
            "total": total,
            "offset": job_filter.offset,
            "limit": job_filter.limit,
            "items": [
                job_card(row, bands=self._bands, today=today, recency=self._recency) for row in rows
            ],
            "facets": facets,
            # How many postings this query left out because the posting itself
            # states something that rules this person out. Reported so the
            # narrowing is VISIBLE: a filter that is on by default and says
            # nothing is a silent filter, and this codebase has fixed that
            # defect once already.
            "hidden_by_eligibility": hidden,
            # Not the work you asked for, which is a different
            # thing from an employer ruling you out and has its
            # own control.
            "hidden_off_target": off_target,
            # Not a rejection and never worded as one. The posting did not say
            # where it hires, which is a fact about the posting -- and a job
            # nobody can vouch for is not a recommendation, which is why it is
            # out of the default list and behind its own control.
            "hidden_unresolved": unresolved,
            # Levels she asked not to see. Zero unless she has said so: the
            # list is empty until a candidate fills it in, and "not preferred"
            # is deliberately not read as "prohibited".
            "hidden_by_seniority": wrong_level,
            # Ways of working she said never to show. Zero until she says so.
            "hidden_by_work_model": wrong_work_model,
            # Hidden by HER, one at a time. Never merged with either number
            # above: those are things that happened to her and this is a thing
            # she did, and only she can put it back.
            "hidden_by_you": user_hidden,
            #
            # Zero when grouping is off, because then nothing was folded and a
            # line saying "0 reposts folded in" is noise.
            "grouped_away": grouped_away,
            # **WHICH QUESTION THESE JOBS ANSWER.** Usually the one being
            # asked; for the minutes after a preference edit, the previous
            # one. The screen needs this to say so rather than presenting the
            # old answer as the new one -- and it is the difference between a
            # product that looks broken after an edit and one that explains
            # itself. See `storage.revisions`.
            "revision": {
                **decision.as_dict(),
                "is_building": scoring_now,
                # Stopped part way, nothing running: say so, and offer to go on.
                "is_interrupted": decision.is_building and not scoring_now,
            },
            # Display names for the facets whose buckets are IDENTIFIERS. A
            # signal facet counts `hubspot_platform`, and the configuration
            # already knows that is called "HubSpot platform ownership" -- the
            # cards render exactly that. Without this the filter panel
            # sentence-cased the id and offered "Hubspot platform" and "Ipaas"
            # beside cards saying something else, which reads as two different
            # vocabularies for one thing.
            #
            # Sent separately rather than by changing the facet shape, so
            # `{bucket: count}` stays the contract every test and the counter
            # rely on. A dimension with no entry here falls through to the
            # client's own labelling, as it did before.
            # Country and region buckets are ISO codes, which are exactly right
            # to store and are not a label. "BR", "GB" and "AE" were printed
            # into the filter rail as themselves, so a person filtering by
            # place read a code table. The names come from `places.yaml`, the
            # same file that decides which codes can exist, because a second
            # list here would drift from it the first time a country was added
            # -- and drift silently, since a missing name falls back to the
            # code, which is the state it was already in.
            "facet_labels": {
                "signal": self._signal_labels(),
                "technology": self._signal_labels(),
                "country": self._country_labels(),
                "region": self._region_labels(),
            },
        }

    def _signal_labels(self) -> dict[str, str]:
        """`signal_id -> label`, from the loaded configuration's lexicon."""
        lexicon = _as_dict(self.search_config(), "lexicon")
        return {key: str(_get(value, "label", key)) for key, value in lexicon.items()}

    def _country_labels(self) -> dict[str, str]:
        """`BR -> Brazil`, from the gazetteer."""
        from career_agent.match.places import display_names

        return display_names()[0]

    def _region_labels(self) -> dict[str, str]:
        """`LATAM -> Latin America`, from the gazetteer."""
        from career_agent.match.places import display_names

        return display_names()[1]

    def get_job(self, *, job_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.mvp_repo import ApplicationRepo, ScoredJobQuery

        validate_job_id(job_id)
        config_id, config_version = self._identity()
        with _closing(self.connect()) as conn:
            row = ScoredJobQuery(conn).get_one(job_id, config_id, config_version)
            if row is None:
                raise ApiError(404, "no such job")
            history = ApplicationRepo(conn).history(job_id)
        return job_detail(
            row, bands=self._bands, today=utc_today(), recency=self._recency, history=history
        )

    # -- mutations -------------------------------------------------------
    def patch_status(self, *, job_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.mvp_repo import ApplicationRepo

        validate_job_id(job_id)
        raw = body.get("status")
        if not isinstance(raw, str):
            raise ApiError(400, "status is required")
        try:
            status = ApplicationStatus(raw)
        except ValueError as exc:
            raise ApiError(400, f"unknown status: {raw!r}") from exc

        applied_at = body.get("applied_at")
        if applied_at is not None and not _is_iso_date(applied_at):
            raise ApiError(400, "applied_at must be YYYY-MM-DD")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise ApiError(400, "note must be a string")

        with _closing(self.connect()) as conn:
            ApplicationRepo(conn).set_status(
                job_id, status, applied_at=applied_at, note=note, now=_now()
            )
        return self.get_job(job_id=job_id, query={}, body={})

    def patch_applied_at(self, *, job_id: str, query: dict, body: dict) -> dict:
        """Set or clear the applied date on its own. The only route that clears it.

        A separate route from `/status` because the two requests mean different
        things about an absent value, and one handler cannot hold both readings
        without a flag that callers forget. On `/status`, `applied_at` is
        incidental and an omitted one means "leave the date alone" -- that
        reading is what stopped `APPLIED -> REJECTED` from erasing the day you
        applied. Here the date IS the request, so `null` is an instruction to
        clear it.

        Which is why this is the route the interface puts a confirmation in
        front of: it is the only way to destroy the record of having applied,
        and after ADR-0012 nothing else can do it by accident.

        The key must be PRESENT. `{}` is not a request to clear -- it is a
        request that forgot to say anything, and answering it by deleting the
        date would be the same defect this route exists to remove.
        """
        from career_agent.storage.mvp_repo import ApplicationDateRefused, ApplicationRepo

        validate_job_id(job_id)
        _reject_unknown(query, frozenset(), "applied-at")
        if "applied_at" not in body:
            raise ApiError(
                400,
                "applied_at is required; send null to clear the date and a "
                "YYYY-MM-DD string to set it",
            )
        applied_at = body["applied_at"]
        if applied_at is not None and not _is_iso_date(applied_at):
            raise ApiError(400, "applied_at must be YYYY-MM-DD or null")
        note = body.get("note")
        if note is not None and not isinstance(note, str):
            raise ApiError(400, "note must be a string")

        try:
            with _closing(self.connect()) as conn:
                ApplicationRepo(conn).set_applied_at(job_id, applied_at, note=note, now=_now())
        except ApplicationDateRefused as exc:
            # 409 and not 400: the request is well-formed and the rule is about
            # the state it met, which is something the person can resolve.
            raise ApiError(409, str(exc)) from exc
        return self.get_job(job_id=job_id, query={}, body={})

    def patch_saved(self, *, job_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.mvp_repo import ApplicationRepo

        validate_job_id(job_id)
        saved = body.get("saved")
        if not isinstance(saved, bool):
            raise ApiError(400, "saved must be true or false")
        with _closing(self.connect()) as conn:
            ApplicationRepo(conn).set_saved(job_id, saved, now=_now())
        return self.get_job(job_id=job_id, query={}, body={})

    def patch_hidden(self, *, job_id: str, query: dict, body: dict) -> dict:
        """Hide one posting from the discovery views, or put it back.

        A route of its own rather than a field on `/status`, because hiding is
        not a position in the application workflow and `/status` writes one.
        The nearest status is ARCHIVED, and using it here would record that an
        application ended when none was ever sent.

        Returns the job, like every other mutation on this resource, so the
        caller can offer an Undo against the row it actually wrote.
        """
        from career_agent.domain.enums import HiddenReason
        from career_agent.storage.mvp_repo import ApplicationRepo

        validate_job_id(job_id)
        hidden = body.get("hidden")
        if not isinstance(hidden, bool):
            raise ApiError(400, "hidden must be true or false")

        # WHAT THE CARD STOOD FOR. In a grouped view one card represents every
        # posting an employer published for one role, so hiding "posting"
        # there hid the representative and promoted a sibling into its place:
        # the card came back looking almost the same, and the control appeared
        # not to work.
        #
        # The caller says which, because only the caller knows which card was
        # pressed. Inferring it from `group_duplicates` on this request would
        # be the server guessing what was on screen.
        scope = body.get("scope", "posting")
        if scope not in {"posting", "role"}:
            raise ApiError(400, "scope must be 'posting' or 'role'")

        # **WHY, WHEN SHE SAYS, AND NEVER REQUIRED.**
        #
        # The audit on 2026-09-08 could measure that 92 of her top 100 sit in
        # the WEAK band; it could not tell whether a posting was wrong because
        # of where it is or because of what the work is, and those two want
        # completely different corrections. Only she knows, one posting at a
        # time, at the moment she decides not to look at it again.
        #
        # Optional on purpose. Hiding without a reason is the commonest case
        # and has to stay unremarkable: a control that interrogates somebody
        # for setting one job aside is a control they stop using.
        #
        # A closed vocabulary because counting is the point, and NOTHING reads
        # it back into a preference. `test_hidden_reason.py` asserts that a
        # reason changes no score and no configuration; if a pattern in these
        # is ever turned into a rule, it goes through the preference editor as
        # a proposal she confirms, with the count of postings it would affect.
        reason = body.get("reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise ApiError(400, "reason must be a string or null")
            allowed = {member.value for member in HiddenReason}
            if reason not in allowed:
                raise ApiError(400, f"reason must be one of {', '.join(sorted(allowed))}")
        if reason is not None and not hidden:
            # Un-hiding carries no reason. Accepting one would write a note
            # about a decision being reversed, which is not what it means.
            raise ApiError(400, "a reason belongs to hiding, not to restoring")

        stamp = _now()
        with _closing(self.connect()) as conn:
            repo = ApplicationRepo(conn)
            targets = repo.siblings_of(job_id) if scope == "role" else [job_id]
            for target in targets:
                repo.set_hidden(target, hidden, reason=reason, now=stamp)
        return self.get_job(job_id=job_id, query={}, body={})

    def patch_notes(self, *, job_id: str, query: dict, body: dict) -> dict:
        from career_agent.storage.mvp_repo import ApplicationRepo

        validate_job_id(job_id)
        notes = body.get("notes")
        if notes is not None and not isinstance(notes, str):
            raise ApiError(400, "notes must be a string or null")
        if isinstance(notes, str) and len(notes) > 20_000:
            raise ApiError(400, "notes are limited to 20000 characters")
        with _closing(self.connect()) as conn:
            ApplicationRepo(conn).set_notes(job_id, notes, now=_now())
        return self.get_job(job_id=job_id, query={}, body={})

    # -- the only route that may open a socket, and only to localhost ----
    def enrich_job(self, *, job_id: str, query: dict, body: dict) -> dict:
        """Run the local model over one job. Never called automatically.

        A 503 here is a normal, expected state, not a failure of the product:
        Ollama is not running. The interface says so and everything else keeps
        working.
        """
        validate_job_id(job_id)
        from career_agent.local_ai.ollama import OllamaInvalidOutput
        from career_agent.local_ai.prompt import LocalPromptChanged
        from career_agent.pipeline.enrich import (
            EnrichmentRejected,
            EnrichmentUnavailable,
            enrich_one,
        )

        config_id, config_version = self._identity()
        # Deterministic triage runs FIRST, here as well as in the CLI. Without
        # this the button was a way around the whole point of the threshold:
        # the model would see any posting a person happened to click, instead
        # of only the ones that already survived scoring.
        thresholds = _as_dict(self.search_config(), "thresholds")
        minimum = thresholds.get("local_ai_min_score")
        try:
            with _closing(self.connect()) as conn:
                self._ollama_state = enrich_one(
                    conn,
                    job_id,
                    config_id=config_id,
                    config_version=config_version,
                    state=self._ollama_state,
                    min_score=int(minimum) if minimum is not None else None,
                )
        except EnrichmentUnavailable as exc:
            # 503 and not 500: Ollama being down is an expected state, and the
            # interface says so calmly while everything else keeps working.
            raise ApiError(503, str(exc), for_reader=True) from exc
        except EnrichmentRejected as exc:
            raise ApiError(409, str(exc), for_reader=True) from exc
        except OllamaInvalidOutput as exc:
            # The model answered and the answer was unusable. 502: an upstream
            # gave us something we could not accept, which is not a bug in
            # this server and must not read as one.
            raise ApiError(
                502, f"the local model returned an unusable answer: {exc}", for_reader=True
            ) from exc
        except LocalPromptChanged as exc:
            # The prompt bytes no longer match their recorded digest. Refusing
            # is the point: an edited prompt must become a new version rather
            # than silently answering under the old one's name.
            raise ApiError(500, str(exc)) from exc
        return self.get_job(job_id=job_id, query={}, body={})

    def import_job(self, *, query: dict, body: dict) -> dict:
        """Manual import: the universal path for every source we may not fetch.

        Eleven of the eighteen sources in the capability matrix are
        manual-import-only. This is how they reach the product: the person is
        allowed to read those pages, and the agent is allowed to help with what
        they bring back.
        """
        from career_agent.pipeline.manual_import import ImportError_, import_posting

        fields: dict[str, str] = {}
        for name in ("title", "company", "description"):
            value = body.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ApiError(400, f"{name} is required")
            fields[name] = value
        title, company, description = fields["title"], fields["company"], fields["description"]
        url = body.get("url")
        if url is not None and not _is_http_url(url):
            raise ApiError(400, "url must be http or https")
        # A description is pasted by a person, so it is bounded by what a job
        # posting plausibly is. Without a cap, a 2 MB body makes the matcher
        # build a 2-million-entry offset map per request -- and `notes` was
        # already capped while the far larger field was not.
        if len(description) > 200_000:
            raise ApiError(
                400,
                "the description is longer than any job posting; is this the right text?",
                for_reader=True,
            )

        config_id, config_version = self._identity()
        try:
            with _closing(self.connect()) as conn:
                job_id = import_posting(
                    conn,
                    self.search_config(),
                    title=title.strip(),
                    company=company.strip(),
                    description=description,
                    url=url,
                    location=body.get("location_raw"),
                    config_id=config_id,
                    config_version=config_version,
                    now=_now(),
                )
        except ImportError_ as exc:
            # A domain rule the person can act on -- "the description is too
            # short to score meaningfully" -- was reaching the catch-all and
            # surfacing as "internal error, see the terminal". A user mistake
            # and a server bug looked identical, and the useful half was the
            # one being hidden.
            raise ApiError(400, str(exc)) from exc
        return self.get_job(job_id=job_id, query={}, body={})

    # =================================================================
    # filter parsing -- one place, so a parameter cannot mean two things
    # =================================================================
    def _filter_from(self, query: dict):
        from career_agent.storage.mvp_repo import JobFilter

        _reject_unknown(query, JOB_QUERY_PARAMS, "filter")
        limit = _int(query, "limit") or 60
        if not 1 <= limit <= 500:
            raise ApiError(400, "limit must be between 1 and 500")
        offset = _int(query, "offset") or 0
        if offset < 0:
            raise ApiError(400, "offset must not be negative")
        search = _one(query, "search")
        if search is not None and len(search) > SEARCH_MAX_CHARS:
            raise ApiError(400, f"search must be at most {SEARCH_MAX_CHARS} characters")

        # A minimum salary without a currency is not a question this system can
        # answer. Nothing here converts between currencies -- there is no
        # offline rate that is true on the day a posting was collected -- so
        # "at least 120,000" across a corpus quoting USD, EUR, BRL and INR
        # would compare numbers that are not comparable and would do it
        # silently. Refusing names the missing half instead.
        min_salary = _int(query, "min_salary")
        if min_salary is not None:
            if min_salary < 0:
                raise ApiError(400, "min_salary must not be negative")
            if len(_upper(query, "salary_currency")) != 1:
                raise ApiError(
                    400,
                    "min_salary needs exactly one salary_currency: this system "
                    "never converts between currencies, so a figure without one "
                    "would compare amounts that are not comparable",
                    for_reader=True,
                )
        # Is this request ABOUT her decisions, or is it asking what to look at?
        #
        # `saved_only`, an explicit `status` list and `user_hidden_only` are
        # the three ways the interface says "show me the postings I have
        # already done something with": Saved, the Applications board (which
        # narrows to the tracked statuses) and the restore view. Those are the
        # views that must never lose a posting to a later rescore, so those are
        # the views that ask for the tracking exemption.
        #
        # Everything else is discovery, and discovery is a RECOMMENDATION. See
        # `JobFilter.exempt_tracked`: shortlisting a posting an employer ruled
        # her out of must not put it back on the list of jobs to consider.
        statuses = _vocab(query, "status")
        saved_only = _bool(query, "saved_only")
        user_hidden_only = _bool(query, "user_hidden_only")
        return JobFilter(
            exempt_tracked=bool(statuses or saved_only or user_hidden_only),
            search=_one(query, "search"),
            companies=_many(query, "company"),
            providers=_many(query, "provider"),
            role_classes=_vocab(query, "role_class"),
            statuses=statuses,
            eligibility=_vocab(query, "eligibility"),
            fit_bands=_vocab(query, "fit_band"),
            signals=_many(query, "signal"),
            min_score=_int(query, "min_score"),
            max_score=_int(query, "max_score"),
            min_confidence=_int(query, "min_confidence"),
            saved_only=saved_only,
            enriched_only=_bool(query, "enriched_only"),
            has_salary=_tribool(query, "has_salary"),
            remote_only=_bool(query, "remote_only"),
            include_ineligible=_bool(query, "include_ineligible"),
            include_unresolved=_bool(query, "include_unresolved"),
            # HER OWN list, read from her configuration rather than from the
            # query. The query only says whether to override it.
            excluded_seniorities=tuple(
                level.value for level in self.search_config().preferences.seniority.excluded
            ),
            include_excluded_seniority=_bool(query, "include_excluded_seniority"),
            excluded_work_models=tuple(
                model.upper()
                for model in self.search_config().preferences.remote.excluded_work_models
            ),
            include_excluded_work_model=_bool(query, "include_excluded_work_model"),
            include_off_target=_bool(query, "include_off_target"),
            include_user_hidden=_bool(query, "include_user_hidden"),
            user_hidden_only=user_hidden_only,
            group_duplicates=_bool(query, "group_duplicates"),
            posted_within_days=_int(query, "posted_within_days"),
            # -- the twelve ---------------------------------------------
            countries=_codes(query, "country"),
            regions=_vocab(query, "region"),
            latam_only=_bool(query, "latam_only"),
            worldwide_only=_bool(query, "worldwide_only"),
            worksites=_vocab(query, "worksite"),
            seniorities=_vocab(query, "seniority"),
            employment_types=_upper(query, "employment_type"),
            min_salary=min_salary,
            salary_currencies=_upper(query, "salary_currency"),
            salary_periods=_upper(query, "salary_period"),
            technologies=_technologies(query, self.search_config()),
            # Migration 0020. Separate parameters, separate columns, and a
            # query for one can never return the other -- which is the
            # whole reason the reading did not simply join `eligibility`.
            employment_context=_vocab(query, "employment_context"),
            content_completeness=_vocab(query, "content_completeness"),
            contract_regime=_vocab(query, "contract_regime"),
            keywords=_phrases(query, "keyword"),
            excluded_keywords=_phrases(query, "exclude_keyword"),
            preferred_keywords=_phrases(query, "prefer_keyword"),
            avoided_keywords=_phrases(query, "avoid_keyword"),
            # Migration 0027. Three parameters because they are three
            # questions, and `experience_max_years` deliberately EXCLUDES the
            # postings that stated no minimum -- see the field's own note.
            experience_requirements=_vocab(query, "experience_requirement"),
            experience_max_years=_experience_years(query),
            entry_signals=_vocab(query, "entry_signal"),
            # OFF unless asked for, and the only place in this request that
            # reads what she has confirmed. It can only widen: see
            # `JobFilter.transferable_signals`.
            transferable_signals=(
                self.transferable_signals() if _bool(query, "include_transferable") else ()
            ),
            sort=_single(query, "sort", "score"),
            direction=_single(query, "direction", "desc"),
            limit=limit,
            offset=offset,
        )


# =========================================================================
# small helpers
# =========================================================================


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _is_http_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith(("http://", "https://"))


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from either a pydantic model or a plain mapping."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_dict(obj: Any, name: str) -> dict:
    value = _attr(obj, name, {})
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return dict(value)


def _get(obj: Any, name: str, default: Any = None) -> Any:
    return _attr(obj, name, default)

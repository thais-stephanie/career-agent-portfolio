"""The official source matrix: what works, what does not, why, and what has never run.

WHY A SIXTH THING THAT TALKS ABOUT SOURCES
------------------------------------------
There were five, and each of them answered a different question correctly:

    `config/source_catalogue.yaml`     what the vendor PERMITS, quoted
    `sources/catalogue.py::resolve`    the catalogue, refused where the corpus
                                       does not back it
    `sources/health.py`                did each BOARD's last run succeed
    `career-agent ingestion-report`    what each collector's last run did
    `docs/product/source-capability-matrix.md`   the prose, written by hand

None of them answers the question somebody actually asks, which is "can I get
jobs out of this source today, and if not, whose move is it". Answering it
needs all five at once, and the hand-written document is the one that goes
stale -- a status written in prose on one date and read as current on another
is the single most reliable way this project has misled itself.

So this module CROSSES THEM and derives one state per source, and
`career-agent source-matrix` writes the document. The document is generated,
dated and reproducible; nothing in it is a remembered claim.

THE STATES, AND WHY THESE ONES
------------------------------
The existing vocabularies are each right about their own question and wrong as
an answer to this one. `Coverage.PLANNED` famously covers two unrelated
situations -- permitted and unbuilt, and built but never run -- which is why a
word had to be added to its label. `Maturity` cannot see a quota. `Permission`
cannot see a run.

`MatrixState` is the cross product, collapsed to the outcomes that differ
in WHOSE MOVE IT IS:

    FORBIDDEN            the vendor said no. Nobody's move; do not fetch.
    BLOCKED_PROVIDER     access is blocked or unavailable, with measured reason.
    DISABLED_QUOTA       a budget we or they set is spent.
    READY_OWNER_RUN      built, permitted, and the first call is the OWNER'S --
                         a robots file naming the agent, or a key that belongs
                         to one person.
    PERMITTED_NOT_BUILT  nothing stops us; there is no adapter.
    NOTHING_PUBLISHED    no general job-board inventory is published.
    PRODUCTION_EMPTY     it runs and the corpus holds nothing from it.
    PRODUCTION           postings from it are in the corpus.
    NOT_STARTED          an implementation exists but has never run.
    FAILED               a production attempt failed and holds no inventory.

WHAT THIS MODULE REFUSES TO DO
------------------------------
**It does not upgrade a catalogue claim.** `resolve` already refuses a claim
the corpus cannot support, and it reports the opposite -- a catalogue that
UNDERSTATES -- rather than correcting it, because choosing between OPERATIONAL
and PARTIAL is a judgement. Nothing here overrides either.

**It states no permission the catalogue did not state.** Permission comes from
a first-party page read on a recorded date. A source whose state here is
`PERMITTED_NOT_BUILT` is permitted because the catalogue says so, not because
this code found nothing stopping it.

**Every number is measured.** Postings, full-content share, last successful
run, boards: all from the database handed in. With no database the counts are
`None` and the states say so, rather than reporting zero -- "we did not look"
and "there are none" must never render the same.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from career_agent.sources.catalogue import Coverage, Source, resolve

# =========================================================================
# the vocabulary
# =========================================================================


class MatrixState(StrEnum):
    """One source's real situation, and whose move it is.

    Ordered worst-to-best for reading, not for arithmetic: nothing compares two
    of these with `<`.
    """

    NOT_STARTED = "NOT_STARTED"
    FAILED = "FAILED"
    FORBIDDEN = "FORBIDDEN"
    BLOCKED_PROVIDER = "BLOCKED_PROVIDER"
    DISABLED_QUOTA = "DISABLED_QUOTA"
    PERMITTED_NOT_BUILT = "PERMITTED_NOT_BUILT"
    READY_OWNER_RUN = "READY_OWNER_RUN"
    NOTHING_PUBLISHED = "NOTHING_PUBLISHED"
    PRODUCTION_EMPTY = "PRODUCTION_EMPTY"
    PRODUCTION = "PRODUCTION"

    @property
    def label(self) -> str:
        return {
            MatrixState.NOT_STARTED: "Implemented; no production run",
            MatrixState.FAILED: "Attempted; no production inventory",
            MatrixState.FORBIDDEN: "Forbidden by the vendor",
            MatrixState.BLOCKED_PROVIDER: "Provider access blocked or unavailable",
            MatrixState.DISABLED_QUOTA: "Switched off: a budget is spent",
            MatrixState.PERMITTED_NOT_BUILT: "Permitted, no adapter",
            MatrixState.READY_OWNER_RUN: "Built; the first call is the owner's",
            MatrixState.NOTHING_PUBLISHED: "No general job-board inventory published",
            MatrixState.PRODUCTION_EMPTY: "Runs, and the corpus holds nothing",
            MatrixState.PRODUCTION: "In production",
        }[self]


#: Sources whose first live call belongs to the OWNER rather than to an agent,
#: and the reason in each case.
#:
#: NAMED HERE RATHER THAN DERIVED, because neither reason is visible in any
#: field: one is a robots file that names a specific user agent, the other is a
#: credential whose budget belongs to one person. A derivation would have to
#: guess, and guessing in the permissive direction is how a boundary gets
#: crossed by accident.
#:
#: It is a COST rather than a flag: moving a row out of here is a reviewable
#: commit, which is what an ethical boundary should cost. ADR-0018 and the
#: Torre precedent.
OWNER_RUN_ONLY: dict[str, str] = {
    "getonbrd": (
        "robots.txt allows a generic reader and names ClaudeBot with Disallow: /. "
        "The collector is not ClaudeBot; the agent is. So the agent must not run it."
    ),
    "remoteok": (
        "robots.txt names ClaudeBot with Disallow: /, so the agent must not run it. "
        "Its API terms are a grant with three structural conditions."
    ),
    "jooble": (
        "A key is 500 requests for the LIFETIME of the credential, not per day, and "
        "it belongs to one country that cannot be read from the key."
    ),
}

#: Sources switched off because a budget is spent, and what spent it.
#:
#: Separate from `OWNER_RUN_ONLY` because the remedy is different in kind: an
#: owner-run source needs a person to decide, and this needs a person to WAIT or
#: to ask the vendor for something.
QUOTA_DISABLED: dict[str, str] = {
    "jooble": (
        "Disabled: country-bound lifetime quota and excerpt-only inventory; "
        "owner decision required."
    ),
}


# =========================================================================
# one row
# =========================================================================


@dataclass(frozen=True, slots=True)
class MatrixRow:
    """One source, crossed against every fact this repository holds about it."""

    source_id: str
    name: str
    region: str
    provider: str | None
    state: MatrixState
    #: From the catalogue, and from a first-party page on a recorded date.
    permission: str
    #: How far the code got, asked of the registry and the corpus.
    maturity: str
    #: Whether the ordinary production path runs this source at all.
    production_enabled: bool
    #: Whether anything has EVER been collected from it into this database.
    ever_run: bool
    #: The last `pipeline_run` for this source that finished OK. `None` is not
    #: zero: it means no successful run is recorded in the database handed in.
    last_success: str | None
    #: Open postings held right now.
    postings: int | None
    #: How many have a stored score for their current content. Beside `full_content`
    #: because a provider collected after the last rescore has postings, no
    #: scores and therefore no measured content depth -- and "not measured yet"
    #: must not render as "none of these is complete".
    scored: int | None
    #: How many hold the whole advert, measured rather than declared.
    full_content: int | None
    #: The blocker, in one sentence, or `None`.
    blocker: str | None
    #: Whose move it is, and what the move is.
    next_action: str
    refresh_state: str = "NOT_STARTED"
    coverage_limitation: str = ""
    #: The adapter answers bounded questions rather than serving a board or a
    #: feed, so no run can reach its whole market. Asked of the registry.
    query_scoped: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "name": self.name,
            "region": self.region,
            "provider": self.provider,
            "state": self.state.value,
            "state_label": self.state.label,
            "permission": self.permission,
            "maturity": self.maturity,
            "production_enabled": self.production_enabled,
            "ever_run": self.ever_run,
            "last_success": self.last_success,
            "postings": self.postings,
            "scored": self.scored,
            "full_content": self.full_content,
            "blocker": self.blocker,
            "next_action": self.next_action,
            "refresh_state": self.refresh_state,
            "coverage_limitation": self.coverage_limitation,
        }


# =========================================================================
# the measurements
# =========================================================================


def _last_success_by_stage(conn: sqlite3.Connection) -> dict[str, str]:
    """The latest OK `pipeline_run` per stage.

    Stage names are what the commands wrote (`collect-gupy`, `collect`), so the
    mapping back to a provider is done by the caller, which knows both.
    """
    rows = conn.execute(
        "SELECT stage, MAX(finished_at) AS last FROM pipeline_run"
        " WHERE status = 'OK' AND finished_at IS NOT NULL GROUP BY stage"
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}


def _postings_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """OPEN postings per provider. Closed ones are history, not availability."""
    rows = conn.execute(
        "SELECT provider, COUNT(*) FROM job WHERE closed_at IS NULL GROUP BY provider"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _scored_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """How many open postings per provider have a score for their current content.

    Beside the full-content count rather than folded into it, because the two
    answer different questions and the first version conflated them: a provider
    whose postings arrived after the last rescore reports 0 full-content
    postings, which reads as "none of these holds a whole advert" and means "we
    have not looked yet". With both numbers on the row a reader can tell.
    """
    rows = conn.execute(
        "SELECT j.provider, COUNT(DISTINCT j.id) FROM job j"
        " JOIN job_match m ON m.job_id = j.id AND m.content_hash = j.content_hash"
        " WHERE j.closed_at IS NULL GROUP BY j.provider"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _textless_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """How many open postings per provider have NO description to score.

    THE THIRD COUNT, and it exists because the second one told a lie. With only
    `postings` and `scored`, this file reported "Rescore: 158 postings have no
    stored score yet" for Speedrun immediately after a forced rescore of the
    whole corpus -- because all 158 are textless, and no rescore will ever score
    a posting with nothing in it. An action a reader cannot carry out is worse
    than no action: it sends somebody to run a twenty-minute command twice.

    Measured 2026-09-10: 163 open postings in the corpus hold no text, and they
    are exactly the postings with no stored score.
    """
    rows = conn.execute(
        "SELECT j.provider, COUNT(*) FROM job j"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
        " WHERE j.closed_at IS NULL"
        "   AND (r.description_text IS NULL OR length(r.description_text) = 0)"
        " GROUP BY j.provider"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}


def _full_content_by_provider(conn: sqlite3.Connection) -> dict[str, int]:
    """How many open postings per provider hold the WHOLE advert.

    Read from `job_match.content_completeness`, which migration 0022 put there
    so a query could ask.

    **`COUNT(DISTINCT j.id)`, and the DISTINCT is a bug fix.** `job_match` holds
    one row per (posting, configuration version) and this corpus carries five
    versions, so a plain `COUNT(*)` reported 30,384 full-content Greenhouse
    postings against 11,134 open ones -- a number larger than its own
    population, which is the loudest possible way for a count to be wrong and
    was in the first version of this file.

    A row scored before migration 0022 has NULL here and is counted as not-full,
    which understates rather than overstates: the safe direction for a number
    that says how much of a posting somebody will actually see.
    """
    try:
        rows = conn.execute(
            "SELECT j.provider, COUNT(DISTINCT j.id) FROM job j"
            " JOIN job_match m ON m.job_id = j.id AND m.content_hash = j.content_hash"
            " WHERE j.closed_at IS NULL AND m.content_completeness = 'FULL_CONTENT'"
            " GROUP BY j.provider"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}
    return {str(row[0]): int(row[1]) for row in rows}


#: Which `pipeline_run` stage collects each provider.
#:
#: DERIVED FROM THE COMMAND NAMES, which is the only place the association
#: exists. Most collectors write `collect-<provider>`; the board families share
#: the one `collect` stage, because `collect` walks boards and a board belongs
#: to an employer rather than to a vendor.
_SHARED_STAGE = "collect"


def _board_families() -> frozenset[str]:
    """Providers `collect` walks, ASKED OF THE REGISTRY rather than listed.

    This was a literal set of four vendor names, and
    `tests/unit/test_provider_neutrality.py` was right to refuse it: generic
    code must never spell a vendor. The registry already answers the question
    -- `board_providers()` is exactly "providers that can be asked whether an
    employer has a board here" -- so a fifth board family added tomorrow lands
    in this stage mapping without anybody remembering to edit it.
    """
    from career_agent.providers.registry import board_providers

    return frozenset(board_providers())


def _stage_for(provider: str) -> str:
    return _SHARED_STAGE if provider in _board_families() else f"collect-{provider}"


def _state_for(
    source: Source,
    *,
    postings: int | None,
    ever_run: bool,
    successful_empty: bool = False,
    failed: bool = False,
) -> MatrixState:
    """One source's real situation. Every branch names the fact that decided it.

    ORDER IS THE WHOLE OF THIS FUNCTION, and two places in it were decided
    rather than fallen into.

    A forbidden source that also has a spent quota is FORBIDDEN: the quota is
    irrelevant to something we must not call at all.

    **A source with postings is in PRODUCTION, whoever pressed the button**, so
    the `postings` check comes BEFORE the owner-run one. Remote OK is the case:
    its robots file names the agent, the owner ran it herself, and 99 postings
    are in the corpus. Reporting that as "waiting for the owner" would hide work
    that is already done, and would tell her to do again the thing she has just
    done.
    """
    if source.coverage in (Coverage.BLOCKED, Coverage.UNSUPPORTED):
        return MatrixState.FORBIDDEN
    if postings:
        # It has produced. How the first call happened is history; the question
        # this column answers is whether jobs from here are in the list.
        return MatrixState.PRODUCTION
    if source.id in QUOTA_DISABLED:
        return MatrixState.DISABLED_QUOTA
    if source.collection_blocker:
        return MatrixState.BLOCKED_PROVIDER
    if source.coverage is Coverage.NOTHING_PUBLISHED:
        return MatrixState.NOTHING_PUBLISHED
    if not source.provider:
        return MatrixState.PERMITTED_NOT_BUILT
    if source.id in OWNER_RUN_ONLY:
        return MatrixState.READY_OWNER_RUN
    if successful_empty:
        return MatrixState.PRODUCTION_EMPTY
    return MatrixState.FAILED if failed or ever_run else MatrixState.NOT_STARTED


def _next_action(
    row_state: MatrixState,
    source: Source,
    *,
    postings: int | None = None,
    scored: int | None = None,
    textless: int = 0,
) -> str:
    """Whose move it is, in one sentence. Never "investigate".

    `postings` and `scored` are here for one case the state cannot express: a
    source that collected successfully AFTER the last rescore. Those postings are
    in the database and in no list, because every discovery query is keyed on a
    configuration version and they have no row at it. That is not a collection
    problem and the state is correctly `PRODUCTION`; the move is a rescore, and
    it is the single most common reason a screen in this product is
    inexplicably empty.
    """
    if row_state is MatrixState.FORBIDDEN:
        return "None. Do not fetch. Use the search link or a manual import."
    if row_state is MatrixState.DISABLED_QUOTA:
        return QUOTA_DISABLED.get(source.id, "Wait, or ask the vendor for more.")
    if row_state is MatrixState.BLOCKED_PROVIDER:
        return source.unblocked_by or "Wait for the source to restore public access."
    if row_state is MatrixState.READY_OWNER_RUN:
        return OWNER_RUN_ONLY.get(source.id, "The owner runs the first collection.")
    if row_state is MatrixState.PERMITTED_NOT_BUILT:
        return source.unblocked_by or "Write an adapter."
    if row_state is MatrixState.NOTHING_PUBLISHED:
        return "Nothing. Re-check when the source publishes openings."
    if row_state is MatrixState.PRODUCTION_EMPTY:
        return "Verified empty board. Refresh on its normal schedule."
    if row_state in (MatrixState.NOT_STARTED, MatrixState.FAILED):
        stage = _stage_for(source.provider or "")
        # A BOARD FAMILY WITH NO BOARDS HAS NOT FAILED. It has not been asked.
        #
        # `companies.yaml` is a file and `source_board` is the table `collect`
        # reads; only `load-registry` carries the first into the second, and
        # V1.7 lost 46 promoted boards for a whole session to exactly that gap.
        # Without this branch the newest family reads "run the collector and
        # find out why it stored nothing" -- an instruction to debug a collector
        # that is working, about boards it has never been handed.
        # `not source.boards` rather than `== 0`, because the count comes from a
        # GROUP BY: a provider with no rows in `source_board` is absent from the
        # result and arrives here as None rather than as zero.
        if source.provider in _board_families() and not source.boards:
            return (
                "Load the registry, then collect: load-registry, then "
                f"{stage}. This family has no board rows yet, so the collector "
                "has nothing to walk."
            )
        return f"Run the collector and find out why it stored nothing: {stage}."
    # A POSTING WITH NO TEXT IS NOT WAITING FOR A RESCORE. It can never be
    # scored by any configuration, so counting it as unscored work turns this
    # column into an instruction that does nothing.
    unscored = 0 if not postings or scored is None else max(postings - scored, 0)
    unscorable = min(textless, unscored)
    waiting = unscored - unscorable
    if postings and not scored and waiting:
        return (
            f"Rescore. All {waiting} scoreable postings arrived after the last one "
            "and have no stored score, so no list can show them."
        )
    if waiting:
        return f"Rescore: {waiting} postings have no stored score yet."
    if unscorable:
        return (
            f"Nothing. Keep collecting. {unscorable} postings hold no description "
            "at all, so no configuration can ever score them."
        )
    return "Nothing. Keep collecting."


def _blocker(row_state: MatrixState, source: Source) -> str | None:
    if row_state is MatrixState.FORBIDDEN:
        return source.reason or "A first-party page forbids automated access."
    if row_state is MatrixState.DISABLED_QUOTA:
        return QUOTA_DISABLED.get(source.id)
    if row_state is MatrixState.BLOCKED_PROVIDER:
        return source.collection_blocker
    if row_state is MatrixState.READY_OWNER_RUN:
        return OWNER_RUN_ONLY.get(source.id)
    if row_state is MatrixState.PERMITTED_NOT_BUILT:
        return "No adapter has been written."
    return None


def _run_evidence(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """A shared run proves only families with attempted boards in its stats."""
    result: dict[str, dict[str, Any]] = {}
    for row in conn.execute("SELECT * FROM pipeline_run ORDER BY started_at DESC, id DESC"):
        try:
            stats = json.loads(row["stats_json"] or "{}")
        except (ValueError, TypeError):
            stats = {}
        stage = str(row["stage"])
        if stage == "collect":
            slices = stats.get("by_provider", {})
            slices = {k: v for k, v in slices.items() if v.get("boards_attempted", 0) > 0}
        elif stage.startswith("collect-"):
            slices = {stage.removeprefix("collect-"): stats}
        else:
            continue
        for provider, counted in slices.items():
            succeeded = bool(row["finished_at"]) and (
                counted.get("boards_succeeded", 0) > 0
                if stage == "collect"
                else row["status"] == "OK"
            )
            evidence = result.setdefault(
                provider,
                {
                    "latest": dict(row),
                    "stats": counted,
                    "last_success": None,
                    "empty": False,
                },
            )
            if succeeded and evidence["last_success"] is None:
                evidence["last_success"] = row["finished_at"]
                evidence["empty"] = (
                    counted.get("postings_observed", counted.get("postings_seen")) == 0
                    and not counted.get("failures")
                    and not counted.get("boards_failed")
                    and not counted.get("stopped_early")
                )
    return result


def build(
    conn: sqlite3.Connection | None = None,
    *,
    catalogue_path: Path | None = None,
) -> list[MatrixRow]:
    """The whole matrix, measured against one database.

    `conn` is optional and a `None` is not a degraded mode to be hidden: with
    no database there is nothing to verify a claim against, so every count is
    `None` and `ever_run` is False for everything. A caller printing that must
    say which database it read, and `career-agent source-matrix` does.
    """
    from career_agent.providers.base import RetrievalMode
    from career_agent.providers.registry import available_providers, retrieval_mode

    sources = resolve(conn, path=catalogue_path)
    implementations = set(available_providers())
    postings = _postings_by_provider(conn) if conn is not None else {}
    scored = _scored_by_provider(conn) if conn is not None else {}
    textless = _textless_by_provider(conn) if conn is not None else {}
    full = _full_content_by_provider(conn) if conn is not None else {}
    evidence_by_provider = _run_evidence(conn) if conn is not None else {}

    rows: list[MatrixRow] = []
    for source in sources:
        provider = source.provider or (source.id if source.id in implementations else None)
        held = postings.get(provider, 0) if provider and conn is not None else None
        evidence = evidence_by_provider.get(provider or "", {})
        last = evidence.get("last_success")
        ever = bool(evidence) or bool(held)
        latest = evidence.get("latest", {})
        counted = evidence.get("stats", {})
        failed = bool(latest) and (
            bool(counted.get("boards_failed"))
            if latest.get("stage") == _SHARED_STAGE
            else latest.get("status") == "FAILED" or bool(counted.get("failures"))
        )
        state = _state_for(
            source,
            postings=held,
            ever_run=ever,
            successful_empty=evidence.get("empty", False),
            failed=failed,
        )
        if not latest:
            refresh = "NOT_STARTED"
        elif latest.get("finished_at") is None:
            refresh = "RUNNING_OR_INTERRUPTED"
        elif failed or counted.get("boards_failed") or counted.get("stopped_early"):
            refresh = "PARTIAL" if held else "FAILED"
        else:
            refresh = "COMPLETE"
        failure = latest.get("error") if latest.get("stage") != _SHARED_STAGE else None
        if not failure and counted.get("failures"):
            failure = str(counted["failures"])
        if not failure and counted.get("boards_failed"):
            failure = (
                f"{counted['boards_failed']} board(s) failed; see source-health and run ledger."
            )
        limitation = source.reason or ""
        attempted = counted.get("boards_attempted", 0)
        if (
            provider in _board_families()
            and attempted
            and source.boards
            and attempted < source.boards
        ):
            limitation += (
                f" Latest attempt refreshed {attempted} of {source.boards} registered boards;"
                " remaining inventory retains its own earlier freshness."
            )
            if refresh == "COMPLETE":
                refresh = "COMPLETE_TARGETED"
        action = _next_action(
            state,
            source,
            postings=held,
            scored=scored.get(provider, 0) if provider else None,
            textless=textless.get(provider, 0) if provider else 0,
        )
        if state is MatrixState.PRODUCTION and failure and not action.startswith("Rescore"):
            action = (
                "Resolve failed boards using source-health; keep existing inventory usable."
                if provider in _board_families()
                else source.unblocked_by or "Resolve the recorded refresh failure."
            )
        rows.append(
            MatrixRow(
                source_id=source.id,
                name=source.name,
                region=source.region,
                provider=provider,
                state=state,
                permission=source.permission.value,
                maturity=("COLLECTING" if held else "BUILT")
                if provider in implementations
                else source.maturity.value,
                # PRODUCTION ENABLED is about the PATH, not about permission: a
                # source with an adapter that no command reaches is not in the
                # production path. Every adapter here is reachable by a named
                # command, so this is True exactly when there is an adapter and
                # nothing is switched off.
                production_enabled=bool(source.provider)
                and source.id not in QUOTA_DISABLED
                and state not in (MatrixState.FORBIDDEN, MatrixState.BLOCKED_PROVIDER),
                ever_run=ever,
                last_success=last,
                postings=held if conn is not None else None,
                scored=scored.get(provider, 0) if (conn is not None and provider) else None,
                full_content=full.get(provider, 0) if (conn is not None and provider) else None,
                query_scoped=(
                    retrieval_mode(provider) is RetrievalMode.QUERY_DRIVEN if provider else False
                ),
                blocker=_blocker(state, source) or failure,
                refresh_state=refresh,
                coverage_limitation=limitation,
                next_action=action,
            )
        )
    return rows


def summarise(rows: list[MatrixRow]) -> dict[str, int]:
    """How many sources are in each state. Every state, including the zeroes.

    The zeroes are kept on purpose: "no source is blocked by a vendor today" is
    a fact worth reading, and a table that omitted it would leave somebody to
    infer the absence.
    """
    counts = {state.value: 0 for state in MatrixState}
    for row in rows:
        counts[row.state.value] += 1
    return counts

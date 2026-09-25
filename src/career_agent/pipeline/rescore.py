"""Offline scoring with indexed planning and revision-checked paged writes.

TARGETED planning scans the open covering index and probes the covering score
index, then checks persistent changed-input identities and dirty marks. This is
not O(changes): planning scales with the indexed population. Description/payload
reads and scoring scale with selected targets; rowid FTS work follows successful
marked targets. Full FTS rebuild is retained for an absent/untrusted map or a
large changed share. No socket is opened.

Each page reads facts, archived payloads and sightings in one WAL snapshot.
Under the write lock, a result is stored only if its durable input revision still
matches. The score and configuration-specific receipt commit together. Only a
successful matching mark may be acknowledged, atomically with the FTS refresh.
Crashes between these transactions leave replayable work. See ADR-0026.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from career_agent.clock import now_utc
from career_agent.domain.enums import PipelineRunStatus
from career_agent.domain.matching import REPLAY_MIN_SCHEMA
from career_agent.match.engine import match_job
from career_agent.match.identity import input_digest as reading_identity
from career_agent.match.replay import replay
from career_agent.pipeline.facts import job_facts
from career_agent.storage import invalidation, search_index
from career_agent.storage.db import transaction
from career_agent.storage.mvp_repo import (
    MATCH_SCHEMA_VERSION,
    MatchRepo,
    deserialise_match,
)
from career_agent.storage.repositories import DiscoverySourceRepo, ProviderPayloadRepo

#: Read in pages rather than all at once. 18,550 descriptions averaging 6 KB is
#: over 100 MB of text; holding it all is unnecessary and on a small machine
#: it is the difference between a scoring pass and a swap storm.
_PAGE = 500

#: Above this share of the corpus, refreshing the search index row by row
#: costs more than rebuilding it, and a rebuild is the path that is always
#: correct. Below it, the refresh is proportional to the dirty set.
_REBUILD_SHARE = 0.25


class RescoreMode(StrEnum):
    """Which postings a pass looks at. Every mode writes the same rows for
    the same posting; they differ only in how the postings are chosen."""

    #: The default: the dirty ledger plus every open posting without a
    #: current score. Safe to run after anything, cheap when nothing moved.
    TARGETED = "TARGETED"
    #: The ledger alone. What a collector runs when it finishes: the postings
    #: it just touched, and nothing else.
    DIRTY = "DIRTY"
    #: Postings named by the caller -- ids, a provider, a board -- recomputed
    #: whether or not their score is current. For a reader that moved on a
    #: known subset, which is the pattern instead of a schema bump.
    EXPLICIT = "EXPLICIT"
    #: Every open posting, recomputed. The corpus-sized pass. `force`.
    ALL = "ALL"


@dataclass
class RescorePlan:
    """What a pass will do, decided before it reads a single description."""

    mode: RescoreMode
    #: Open, scoreable postings this pass will (re)compute, ordered by id.
    targets: list[str]
    #: Every ledger row read, `job_id -> generation`. Cleared at the end by
    #: both columns, so a mark written while the pass ran survives it.
    dirty: dict[str, int]
    #: Postings whose search-index row is refreshed: the dirty set. A posting
    #: without a current score but with unchanged text needs no index work.
    search_refresh: list[str]
    #: How many rows the plan examined to choose the targets. For the
    #: anti-join that is the open scoreable population, read from an index;
    #: for the ledger it is the ledger.
    candidates_examined: int
    #: Why each target is here, by ledger reason or `MISSING_OR_STALE`.
    breakdown: dict[str, int] = field(default_factory=dict)
    #: Open postings among the candidates that hold no text at all (a NULL
    #: content hash: collected before their text arrived). Never scoreable,
    #: and reported rather than dropped -- an earlier inner join made such
    #: rows invisible to every counter and a short run read as a clean one.
    textless_open: int = 0
    #: Candidates that turned out to be closed. Only the ledger can name one:
    #: a mark on a posting that closed before the pass reached it.
    closed_candidates: int = 0
    plan_ms: int = 0


@dataclass
class RescoreStats:
    mode: str = RescoreMode.TARGETED.value
    jobs_considered: int = 0
    jobs_targeted: int = 0
    jobs_scored: int = 0
    jobs_skipped_changed: int = 0
    jobs_skipped_already_scored: int = 0
    jobs_skipped_no_description: int = 0
    jobs_skipped_closed: int = 0
    blocked_screening: int = 0
    ineligible: int = 0
    unresolved: int = 0
    shortlisted: int = 0
    errors: int = 0
    error_samples: list[str] = field(default_factory=list)
    #: Ledger rows read at plan time, and rows cleared at the end. They differ
    #: when a collector re-marked a posting while the pass ran.
    dirty_marked: int = 0
    dirty_cleared: int = 0
    candidates_examined: int = 0
    plan_breakdown: dict[str, int] = field(default_factory=dict)
    plan_ms: int = 0
    score_ms: int = 0
    input_read_ms: int = 0
    scoring_only_ms: int = 0
    write_ms: int = 0
    description_rows_read: int = 0
    payload_rows_read: int = 0
    sighting_rows_read: int = 0
    search_ms: int = 0
    #: How the pass answered. `jobs_replayed` recomputed the arithmetic over
    #: stored readings; `jobs_read_in_full` read the advert again. Together they
    #: are `jobs_scored`, and `replay_refused` says why the second group was not
    #: the first -- which is the only way a reader can tell a replay that was
    #: not possible from one that was not attempted.
    jobs_replayed: int = 0
    jobs_read_in_full: int = 0
    replay_source_version: int | None = None
    replay_refused: dict[str, int] = field(default_factory=dict)
    replay_ms: int = 0
    elapsed_ms: int = 0
    #: Rows written to the full-text index at the end of the pass, and how:
    #: `FULL` rebuilt it, `INCREMENTAL` replaced the dirty rows, `NONE` found
    #: nothing to touch.
    search_rows_indexed: int = 0
    search_refresh: str = "NONE"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = "DETERMINISTIC_RESCORE"
        return payload


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# =====================================================================
# PLANNING
# =====================================================================


def plan(
    conn: sqlite3.Connection,
    config: Any,
    *,
    mode: RescoreMode = RescoreMode.TARGETED,
    job_ids: Sequence[str] = (),
    provider: str | None = None,
    board: str | None = None,
    include_closed: bool = False,
) -> RescorePlan:
    """Decide which postings a pass will score. Reads indexes and the ledger.

    Nothing here reads a description or a payload, and nothing here reads a
    preference: which postings need a score is a fact about the corpus and
    its stored scores, never about the person.
    """
    started = datetime.now(UTC)
    config_id = str(config.config_id)
    config_version = int(config.config_version)
    config_digest = str(getattr(config, "digest", "UNRECORDED"))

    with invalidation.read_snapshot(conn):
        dirty = _read_ledger(conn)
        reasons = _ledger_reasons(conn)
    breakdown: dict[str, int] = {}
    examined = 0
    textless = 0
    closed = 0

    if mode is RescoreMode.ALL:
        closed_clause = "" if include_closed else " WHERE closed_at IS NULL"
        rows = conn.execute(f"SELECT id FROM job{closed_clause} ORDER BY id").fetchall()
        targets = [str(r["id"]) for r in rows]
        examined = len(targets)
        breakdown["ALL"] = len(targets)
    elif mode is RescoreMode.EXPLICIT:
        targets = _explicit_targets(conn, job_ids, provider, board, include_closed)
        examined = len(targets)
        breakdown["EXPLICIT"] = len(targets)
    else:
        chosen: dict[str, str] = {}
        if mode is RescoreMode.TARGETED:
            missing = _missing_or_stale(conn, config_id, config_version, config_digest)
            examined, textless = _open_counts(conn)
            for job_id in missing:
                chosen[job_id] = "MISSING_OR_STALE"
        else:
            examined = len(dirty)
        # A mark is unacknowledged work, including NEW. A stored score without
        # a revision-checked receipt cannot certify it.
        recompute = list(reasons)
        scoreable, dirty_textless, dirty_closed = _open_scoreable(conn, recompute)
        if mode is RescoreMode.DIRTY:
            textless = dirty_textless
            closed = dirty_closed
        for job_id in scoreable:
            chosen.setdefault(job_id, reasons.get(job_id, "DIRTY"))
        targets = sorted(chosen)
        for reason in chosen.values():
            breakdown[reason] = breakdown.get(reason, 0) + 1

    return RescorePlan(
        mode=mode,
        targets=targets,
        dirty=dirty,
        search_refresh=sorted(dirty),
        candidates_examined=examined,
        breakdown=breakdown,
        textless_open=textless,
        closed_candidates=closed,
        plan_ms=_elapsed(started),
    )


def _read_ledger(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute("SELECT job_id, generation FROM job_dirty").fetchall()
    return {str(r["job_id"]): int(r["generation"]) for r in rows}


def _ledger_reasons(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT job_id, reason FROM job_dirty").fetchall()
    return {str(r["job_id"]): str(r["reason"]) for r in rows}


def _missing_or_stale(
    conn: sqlite3.Connection, config_id: str, config_version: int, config_digest: str
) -> list[str]:
    """Open, scoreable postings with no CURRENT score.

    Current means: this configuration version, a result schema at least this
    build's, the same content hash as the posting holds now, and the same
    rule digest. Anything else is either absent or an answer to a different
    question. Both sides are covering indexes -- `idx_job_open_population`
    and the widened `idx_job_match_population` -- so this reads no row of
    either table; `tests/unit/test_rescore_plan.py` asserts the plan.

    `INDEXED BY` because the planner, on a database with no statistics,
    prefers the UNIQUE autoindex on `(job_id, config_id, config_version)`,
    which answers the first three predicates and then reads the row for the
    other three -- the 50-second shape this exists to remove. Naming the
    index makes a dropped or narrowed index a loud error rather than a
    quiet regression.
    """
    rows = conn.execute(
        "SELECT j.id FROM job j"
        " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL"
        " AND NOT EXISTS ("
        "   SELECT 1 FROM job_match m INDEXED BY idx_job_match_population"
        "   WHERE m.config_id = ? AND m.config_version = ? AND m.job_id = j.id"
        "     AND m.content_hash = j.content_hash"
        "     AND m.schema_version >= ? AND m.config_digest = ?)"
        " ORDER BY j.id",
        (config_id, config_version, MATCH_SCHEMA_VERSION, config_digest),
    ).fetchall()
    # Sparse input identities survive queue acknowledgement and are checked
    # for THIS configuration, so one candidate cannot certify another's work.
    changed = conn.execute(
        "SELECT i.job_id FROM job_input_revision i JOIN job j ON j.id=i.job_id"
        " WHERE j.closed_at IS NULL AND j.content_hash IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM job_score_revision r WHERE r.job_id=i.job_id"
        " AND r.config_id=? AND r.config_version=? AND r.revision=i.revision)",
        (config_id, config_version),
    ).fetchall()
    return sorted({str(r[0]) for r in rows} | {str(r[0]) for r in changed})


def _open_scoreable(conn: sqlite3.Connection, job_ids: Sequence[str]) -> tuple[list[str], int, int]:
    """Filter a list of ids down to the open postings a pass can score, and
    count the ones it cannot: `(scoreable, open without text, closed)`."""
    out: list[str] = []
    textless = 0
    closed = 0
    ids = list(job_ids)
    for start in range(0, len(ids), _PAGE):
        chunk = ids[start : start + _PAGE]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT id, content_hash, closed_at FROM job WHERE id IN ({marks})", chunk
        ).fetchall()
        for row in rows:
            if row["closed_at"] is not None:
                closed += 1
            elif row["content_hash"] is None:
                textless += 1
            else:
                out.append(str(row["id"]))
    return out, textless, closed


def _open_counts(conn: sqlite3.Connection) -> tuple[int, int]:
    """`(open postings, open postings with no text)`, from the open index."""
    row = conn.execute(
        "SELECT COUNT(*), SUM(content_hash IS NULL) FROM job WHERE closed_at IS NULL"
    ).fetchone()
    return int(row[0]), int(row[1] or 0)


def _explicit_targets(
    conn: sqlite3.Connection,
    job_ids: Sequence[str],
    provider: str | None,
    board: str | None,
    include_closed: bool,
) -> list[str]:
    closed = "" if include_closed else " AND j.closed_at IS NULL"
    chosen: set[str] = set()
    ids = list(job_ids)
    for start in range(0, len(ids), _PAGE):
        chunk = ids[start : start + _PAGE]
        marks = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT j.id FROM job j WHERE j.id IN ({marks}){closed}", chunk
        ).fetchall()
        chosen.update(str(r["id"]) for r in rows)
    if provider:
        rows = conn.execute(
            f"SELECT j.id FROM job j WHERE j.provider = ?{closed}", (provider,)
        ).fetchall()
        chosen.update(str(r["id"]) for r in rows)
    if board:
        family, _, identifier = board.partition(":")
        rows = conn.execute(
            "SELECT j.id FROM job j"
            " JOIN source_board sb ON sb.id = j.source_board_id"
            f" WHERE sb.provider = ? AND sb.board_identifier = ?{closed}",
            (family, identifier),
        ).fetchall()
        chosen.update(str(r["id"]) for r in rows)
    return sorted(chosen)


# =====================================================================
# THE PASS
# =====================================================================


def rescore(
    conn: sqlite3.Connection,
    config: Any,
    *,
    force: bool = False,
    limit: int | None = None,
    include_closed: bool = False,
    progress: Any = None,
    mode: RescoreMode | None = None,
    job_ids: Sequence[str] = (),
    provider: str | None = None,
    board: str | None = None,
    no_replay: bool = False,
    semantic: bool = False,
) -> RescoreStats:
    """Score the postings that need it under the loaded configuration.

    `semantic` lets validated semantic findings (`career_agent.semantic`) take
    part in each posting's Search Fit: the newest published evaluation of that
    posting's text, for this Search Intent and this semantic contract. Findings
    are READ here, never requested: a rescore makes no call of any kind. With
    it off, or with no evaluation stored, the score is the deterministic one.

    With no arguments this is TARGETED: the dirty ledger plus every open
    posting without a current score, and nothing else. `force` recomputes
    every open posting. `job_ids`, `provider` or `board` name postings to
    recompute regardless of their score. `mode` picks explicitly.
    `limit` is a non-negative, exact attempted-target budget (zero attempts
    nothing). Failures consume attempts, not acknowledgements; pages never
    round the budget upward. Explicit requests are persisted before processing.
    """
    started = datetime.now(UTC)
    if limit is not None and limit < 0:
        raise ValueError("limit must be non-negative")
    if mode is None:
        if force:
            mode = RescoreMode.ALL
        elif job_ids or provider or board:
            mode = RescoreMode.EXPLICIT
        else:
            mode = RescoreMode.TARGETED
    stats = RescoreStats(mode=mode.value)

    shortlist_min = _shortlist_threshold(config)
    # The bytes the score was computed from. Stored beside the version so an
    # in-place edit of the file under a fixed version is detectable rather
    # than invisible -- the lesson `cache.static_digest` taught the hosted side.
    config_digest = str(getattr(config, "digest", "UNRECORDED"))

    matches = MatchRepo(conn)
    # WHAT THE STORED READINGS WOULD HAVE TO BE AN ANSWER TO. One digest over
    # every configuration section a reading depends on, plus the reader
    # identity. See `match/identity.py`.
    identity = reading_identity(config)
    run_id = _start_run(conn)

    try:
        # Explicit/forced work must survive a crash even if the old score has
        # unchanged hashes. Persist the complete requested set before reading.
        if mode in (RescoreMode.EXPLICIT, RescoreMode.ALL):
            requested = plan(
                conn,
                config,
                mode=mode,
                job_ids=job_ids,
                provider=provider,
                board=board,
                include_closed=include_closed,
            )
            with transaction(conn):
                invalidation.request(conn, requested.targets)
        chosen = plan(
            conn,
            config,
            mode=mode,
            job_ids=job_ids,
            provider=provider,
            board=board,
            include_closed=include_closed,
        )
        stats.plan_ms = chosen.plan_ms
        stats.plan_breakdown = dict(chosen.breakdown)
        stats.candidates_examined = chosen.candidates_examined
        stats.dirty_marked = len(chosen.dirty)
        stats.jobs_targeted = len(chosen.targets)
        # In the targeted modes the population examined and found current is
        # reported as considered-and-skipped, which is what it was: every one
        # of those index entries was looked at and none was read.
        if mode in (RescoreMode.TARGETED, RescoreMode.DIRTY):
            stats.jobs_considered = chosen.candidates_examined
            stats.jobs_skipped_no_description = chosen.textless_open
            stats.jobs_skipped_closed = chosen.closed_candidates
            stats.jobs_skipped_already_scored = max(
                0,
                chosen.candidates_examined
                - chosen.textless_open
                - chosen.closed_candidates
                - len(chosen.targets),
            )
        if progress is not None:
            progress(stats)

        # THE ONE QUESTION ASKED PER PASS: is there a scored version whose
        # readings this build may reuse. Resolved before any posting is read,
        # because the answer cannot change while the pass runs.
        source_version = matches.replay_source_version(
            str(config.config_id),
            int(config.config_version),
            identity,
            REPLAY_MIN_SCHEMA,
            MATCH_SCHEMA_VERSION,
        )
        stats.replay_source_version = source_version

        intent_digest = contract = ""
        if semantic:
            from career_agent.semantic.contract import contract_identity
            from career_agent.semantic.intent import search_intent

            intent_digest = search_intent(config).digest
            contract = contract_identity()

        scoring_started = datetime.now(UTC)
        successful: dict[str, int] = {}
        targets = chosen.targets if limit is None else chosen.targets[:limit]
        for start in range(0, len(targets), _PAGE):
            chunk = targets[start : start + _PAGE]
            read_started = datetime.now(UTC)
            with invalidation.read_snapshot(conn):
                revisions = invalidation.input_revisions(conn, chunk)
                rows = _rows_for(conn, chunk)
                page_ids = [str(row["id"]) for row in rows]
                payloads = ProviderPayloadRepo(conn).latest_for_jobs(page_ids)
                sightings = DiscoverySourceRepo(conn).sightings_for_jobs(page_ids)
                findings = (
                    _semantic_findings(conn, rows, intent_digest, contract) if semantic else {}
                )
                reusable: dict[str, tuple[str, str]] = {}
                source_receipts: dict[str, int] = {}
                if source_version is not None:
                    reusable = matches.replay_candidates(
                        page_ids,
                        config_id=str(config.config_id),
                        source_version=source_version,
                        input_digest=identity,
                        min_schema=REPLAY_MIN_SCHEMA,
                        # The target version itself only lends rows an older
                        # schema wrote; a current row there is this pass's own.
                        below_schema=(
                            MATCH_SCHEMA_VERSION
                            if source_version == int(config.config_version)
                            else None
                        ),
                    )
                    source_receipts = invalidation.score_revisions(
                        conn, page_ids, str(config.config_id), source_version
                    )
            stats.input_read_ms += _elapsed(read_started)
            stats.description_rows_read += len(rows)
            stats.payload_rows_read += len(payloads)
            stats.sighting_rows_read += sum(len(values) for values in sightings.values())
            if mode in (RescoreMode.ALL, RescoreMode.EXPLICIT):
                stats.jobs_considered += len(rows)
            # ONE payload read for the page, shared by both readers, and one
            # sightings read: the aggregators that also SAW each posting, for
            # the hiring scope a canonical board never states (2026-09-11).

            compute_started = datetime.now(UTC)
            batch: list[tuple[str, str, Any]] = []
            for row in rows:
                job_id = row["id"]
                if row["closed_at"] is not None and not include_closed:
                    stats.jobs_skipped_closed += 1
                    continue
                # NULL from the LEFT JOIN (no raw row) and an empty string
                # (a raw row with no text) are the same fact here: nothing to
                # score. Both are counted rather than dropped.
                description = row["description_text"] or ""
                if not description.strip() and not _has_declared_scope(row):
                    # No text and no board-stated scope: nothing to read.
                    # A textless row WITH a declared hiring scope is scored,
                    # because the geography gate can answer from the field
                    # alone and a lead that says "open to Brazil" is worth
                    # more visible at a score of zero than invisible.
                    stats.jobs_skipped_no_description += 1
                    continue
                if row["content_hash"] is None:
                    stats.jobs_skipped_no_description += 1
                    continue

                facts = job_facts(
                    job_id=str(job_id),
                    title=row["title"] or "",
                    description=description,
                    location_raw=row["location_raw"],
                    posted_at=row["posted_at"],
                    provider=row["provider"],
                    payload=payloads.get(str(job_id)),
                    sightings=sightings.get(str(job_id), ()),
                )
                stored = _replay_source(
                    job_id=str(job_id),
                    content_hash=str(row["content_hash"]),
                    reusable=reusable,
                    source_receipts=source_receipts,
                    revisions=revisions,
                    stats=stats,
                    enabled=_replay_allowed(mode, source_version, no_replay),
                )
                evidence = findings.get(str(row["content_hash"]))
                try:
                    if stored is not None:
                        replay_started = datetime.now(UTC)
                        result = replay(
                            config, stored, facts, computed_at=_now(), semantic=evidence
                        )
                        stats.replay_ms += _elapsed(replay_started)
                        stats.jobs_replayed += 1
                    else:
                        result = match_job(config, facts, computed_at=_now(), semantic=evidence)
                        stats.jobs_read_in_full += 1
                except Exception as exc:  # noqa: BLE001
                    # One posting must never abort a corpus pass. The failure
                    # is counted and sampled so it is visible in the run stats
                    # rather than swallowed.
                    stats.errors += 1
                    if len(stats.error_samples) < 5:
                        stats.error_samples.append(f"{job_id}: {type(exc).__name__}: {exc}")
                    continue

                batch.append((job_id, row["content_hash"], result))
            stats.scoring_only_ms += _elapsed(compute_started)
            if batch:
                write_started = datetime.now(UTC)
                with transaction(conn):
                    current = invalidation.input_revisions(conn, chunk)
                    for job_id, content_hash, result in batch:
                        if current[job_id] != revisions[job_id]:
                            stats.jobs_skipped_changed += 1
                            continue
                        matches.store(
                            job_id,
                            content_hash,
                            result,
                            config_digest=config_digest,
                            input_digest=identity,
                        )
                        invalidation.receipt(conn, job_id, config, revisions[job_id])
                        successful[job_id] = revisions[job_id]
                        stats.jobs_scored += 1
                        _tally(stats, result, shortlist_min)
                stats.write_ms += _elapsed(write_started)

            if progress is not None:
                progress(stats)
            _heartbeat(conn, run_id, stats)
        stats.score_ms = _elapsed(scoring_started)

        # The search index and the ledger, together: a ledger row read at
        # plan time is cleared in the same transaction that brings the index
        # row of the posting it named up to date. Cleared by (job_id,
        # generation), so a mark a collector wrote while this ran is left for
        # the next pass.
        #
        # IN PAGES, NOT ONE TRANSACTION. The first version refreshed 11,825
        # index rows and cleared their marks under one write lock, and a
        # collector in another process, waiting its sixty seconds for that
        # lock, gave up with "database is locked" and lost the board it had
        # just fetched. A page of 500 is a few hundred milliseconds; a writer
        # waits that long, never minutes. The full rebuild stays atomic --
        # readers must never see half an index -- and is the one write in this
        # product that holds the lock for minutes, once after the migration.
        # Only successful, revision-checked score writes own an acknowledgement.
        # A newer plan-time mark is deliberately left for the next pass.
        chosen.dirty = {j: g for j, g in chosen.dirty.items() if successful.get(j) == g}
        chosen.search_refresh = sorted(chosen.dirty)
        search_started = datetime.now(UTC)
        stats.search_refresh, stats.search_rows_indexed, stats.dirty_cleared = _refresh_and_clear(
            conn, chosen, full=(mode is RescoreMode.ALL and limit is None)
        )
        stats.search_ms = _elapsed(search_started)

        stats.elapsed_ms = _elapsed(started)
        _finish_run(conn, run_id, PipelineRunStatus.OK, stats)
    except Exception as exc:  # noqa: BLE001
        stats.elapsed_ms = _elapsed(started)
        _finish_run(conn, run_id, PipelineRunStatus.FAILED, stats, error=str(exc))
        raise
    return stats


def _replay_allowed(mode: RescoreMode, source_version: int | None, no_replay: bool) -> bool:
    """Whether this pass may reuse stored readings at all.

    **`ALL` and `EXPLICIT` never replay, and that is the point of both.**
    `--force` exists for the case a reader's MEANING moved without any
    identity moving, and `--provider` / `--board` exist for the same thing on a
    known subset. A replaying `--force` would be a `--force` that does not
    re-read, which would remove the only escape hatch this product has -- and
    would remove the independent oracle that
    `tests/integration/test_incremental_rescore.py` uses, where a forced
    recomputation on a copy is what every targeted pass is compared against.

    `match.identity.READER_IDENTITY` is the principled replacement for reaching
    for `--force`, and it is why the flag can now stay what it always was.
    """
    if no_replay or source_version is None:
        return False
    return mode in (RescoreMode.TARGETED, RescoreMode.DIRTY)


def _replay_source(
    *,
    job_id: str,
    content_hash: str,
    reusable: dict[str, tuple[str, str]],
    source_receipts: dict[str, int],
    revisions: dict[str, int],
    stats: RescoreStats,
    enabled: bool,
) -> Any:
    """The stored result this posting may be replayed from, or None.

    THREE CHECKS, AND EACH ONE HAS TO BE ABLE TO REFUSE ON ITS OWN.

    ``NO_SOURCE``      no scored row under a version whose readings this build
                       may reuse. The digest and the schema floor were applied
                       in the query; this is what is left when they exclude
                       everything for this posting.
    ``CONTENT_MOVED``  the stored row was read from a different advert. The
                       content hash is the identity of the text, and a reading
                       of one advert is not a reading of another.
    ``INPUTS_MOVED``   the posting's durable input revision is not the one the
                       stored score was computed from. That covers everything
                       the six ledger triggers watch -- title, location, posted
                       date, provider, employer, a reopen, a newer archived
                       payload, a recorded or refreshed sighting -- so a payload
                       that changed the declared hiring scope refuses the
                       replay even though the advert text is untouched.

    The content hash and the input revision overlap, and both are kept. The
    revision is the complete guard and the hash is the cheap, independent one;
    a path that ever changes stored text without the trigger firing is caught
    by the second even though the first would have missed it.

    Refusals are counted by reason rather than summed, because "there was
    nothing to replay from" and "the posting changed" are different facts about
    a corpus and a reader who sees only a total cannot tell a first pass from a
    busy one.
    """
    if not enabled:
        return None
    found = reusable.get(job_id)
    if found is None:
        stats.replay_refused["NO_SOURCE"] = stats.replay_refused.get("NO_SOURCE", 0) + 1
        return None
    result_json, stored_hash = found
    if stored_hash != content_hash:
        stats.replay_refused["CONTENT_MOVED"] = stats.replay_refused.get("CONTENT_MOVED", 0) + 1
        return None
    if source_receipts.get(job_id, 0) != revisions.get(job_id, 0):
        stats.replay_refused["INPUTS_MOVED"] = stats.replay_refused.get("INPUTS_MOVED", 0) + 1
        return None
    return deserialise_match(result_json)


def _semantic_findings(
    conn: sqlite3.Connection, rows: Sequence[sqlite3.Row], intent_digest: str, contract: str
) -> dict[str, Any]:
    """Published semantic evidence for this page, keyed by content hash."""
    from career_agent.semantic.store import SemanticRepo

    hashes = [str(row["content_hash"]) for row in rows if row["content_hash"]]
    try:
        return dict(SemanticRepo(conn).evidence_for(hashes, intent_digest, contract))
    except sqlite3.OperationalError:
        # A database from before migration 0041 has no evaluations to read,
        # which is exactly "no semantic evidence", never a failed pass.
        return {}


def _rows_for(conn: sqlite3.Connection, job_ids: Sequence[str]) -> list[sqlite3.Row]:
    if not job_ids:
        return []
    marks = ",".join("?" for _ in job_ids)
    # LEFT JOIN, not JOIN. An inner join silently DROPPED any job whose
    # `content_hash` is NULL -- a posting collected before its text arrived --
    # so those rows were invisible to every counter including
    # `jobs_skipped_no_description`.
    return conn.execute(
        "SELECT j.id, j.title, j.location_raw, j.posted_at, j.provider, j.content_hash,"
        "       j.closed_at, r.description_text"
        " FROM job j"
        " LEFT JOIN job_raw r ON r.content_hash = j.content_hash"
        f" WHERE j.id IN ({marks})"
        " ORDER BY j.id",
        list(job_ids),
    ).fetchall()


def _refresh_and_clear(
    conn: sqlite3.Connection, chosen: RescorePlan, *, full: bool
) -> tuple[str, int, int]:
    """Bring the full-text index to the corpus as it stands and clear the
    ledger rows this pass read. Returns `(how, index rows, marks cleared)`.

    Incremental: one transaction per page of 500 postings, each refreshing
    those postings' index rows and clearing exactly their marks. Full: one
    transaction for the rebuild and every mark, because a half-built index
    must never be visible.
    """
    incremental = False
    if not full and search_index.can_refresh(conn):
        total, _ = search_index.current_state(conn)
        incremental = len(chosen.search_refresh) <= max(_PAGE, int(total * _REBUILD_SHARE))
    if not incremental:
        with transaction(conn):
            rows = search_index.rebuild(conn)
            cleared = _clear_ledger(conn, chosen.dirty)
        return "FULL", rows, cleared

    rows = 0
    cleared = 0
    ids = chosen.search_refresh
    for start in range(0, len(ids), _PAGE):
        page = ids[start : start + _PAGE]
        with transaction(conn):
            rows += search_index.refresh(conn, page)
            cleared += _clear_ledger(conn, {j: chosen.dirty[j] for j in page if j in chosen.dirty})
    # Marks on postings the index refresh did not name (none today: the
    # refresh set IS the ledger), and the staleness key when nothing was
    # dirty at all.
    refreshed = set(ids)
    rest = {j: g for j, g in chosen.dirty.items() if j not in refreshed}
    with transaction(conn):
        if rest:
            cleared += _clear_ledger(conn, rest)
        if not ids:
            rows += search_index.refresh(conn, [])
    return "INCREMENTAL", rows, cleared


def _clear_ledger(conn: sqlite3.Connection, dirty: dict[str, int]) -> int:
    cleared = 0
    items = list(dirty.items())
    for start in range(0, len(items), _PAGE):
        chunk = items[start : start + _PAGE]
        cursor = conn.executemany(
            "DELETE FROM job_dirty WHERE job_id = ? AND generation = ?", chunk
        )
        cleared += max(0, cursor.rowcount)
    return cleared


def _heartbeat(conn: sqlite3.Connection, run_id: str, stats: RescoreStats) -> None:
    """What the pass has done SO FAR, for a screen that asks mid-run. The
    pattern `PipelineRunRepo.progress` established for collectors."""
    from career_agent.storage.repositories import PipelineRunRepo

    with transaction(conn):
        PipelineRunRepo(conn).progress(run_id, stats.as_dict())


def _tally(stats: RescoreStats, result: Any, shortlist_min: int) -> None:
    if str(result.screening_state) == "BLOCKED":
        stats.blocked_screening += 1
    eligibility = str(result.eligibility_status)
    if eligibility == "VERIFIED_NOT_ELIGIBLE":
        stats.ineligible += 1
    elif eligibility == "UNRESOLVED":
        stats.unresolved += 1
    if result.match_score >= shortlist_min:
        stats.shortlisted += 1


def _shortlist_threshold(config: Any) -> int:
    thresholds = getattr(config, "thresholds", None)
    if thresholds is None:
        return 55
    value = (
        thresholds.get("shortlist_min_score")
        if isinstance(thresholds, dict)
        else getattr(thresholds, "shortlist_min_score", 55)
    )
    return int(value if value is not None else 55)


def _has_declared_scope(row: Any) -> bool:
    from career_agent.providers.registry import publishes_hiring_scope

    return bool(row["location_raw"]) and publishes_hiring_scope(row["provider"])


#: How long a RUNNING rescore may go without a heartbeat and still be taken
#: for alive. A heartbeat lands after every page of scores (seconds apart on
#: the real corpus); the plan before the first page is the long quiet part.
LIVE_WINDOW = timedelta(minutes=5)

#: What a run killed mid-pass is closed with, so no row says RUNNING for ever.
INTERRUPTED = "Interrupted: the process stopped before this recalculation finished."


def _last_sign_of_life(row: sqlite3.Row) -> datetime | None:
    try:
        stats = json.loads(row["stats_json"] or "{}")
    except (TypeError, ValueError):
        stats = {}
    stamp = stats.get("heartbeat_at") or row["started_at"]
    try:
        return datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return None


def rescore_in_progress(conn: sqlite3.Connection, *, now: datetime | None = None) -> bool:
    """Whether some process is scoring right now, judged by its heartbeat.

    **THE STALL THIS EXISTS FOR (2026-09-25).** A rescore of 122,876 postings
    was killed at 74,000 when the machine ran out of memory. The partial
    revision was still read as "being built", so the screen said
    "Recalculating: 74,000 of 122,876 (60%)" for ever, even after a restart,
    and offered no way to continue. Partly scored is not the same as being
    scored: a RUNNING row whose last heartbeat is older than `LIVE_WINDOW` is
    a pass that died.
    """
    moment = now or datetime.now(UTC)
    for row in conn.execute(
        "SELECT started_at, stats_json FROM pipeline_run"
        " WHERE stage = 'rescore' AND status = 'RUNNING' AND finished_at IS NULL"
    ):
        seen = _last_sign_of_life(row)
        if seen is not None and moment - seen <= LIVE_WINDOW:
            return True
    return False


def close_interrupted_runs(conn: sqlite3.Connection, *, now: datetime | None = None) -> int:
    """Close RUNNING rescore rows whose process is gone, as FAILED, and say why."""
    moment = now or datetime.now(UTC)
    stale = [
        str(row["id"])
        for row in conn.execute(
            "SELECT id, started_at, stats_json FROM pipeline_run"
            " WHERE stage = 'rescore' AND status = 'RUNNING' AND finished_at IS NULL"
        )
        if (seen := _last_sign_of_life(row)) is None or moment - seen > LIVE_WINDOW
    ]
    for run_id in stale:
        conn.execute(
            "UPDATE pipeline_run SET finished_at = ?, status = ?, error = ?"
            " WHERE id = ? AND finished_at IS NULL",
            (now_utc(), PipelineRunStatus.FAILED.value, INTERRUPTED, run_id),
        )
    return len(stale)


def _start_run(conn: sqlite3.Connection) -> str:
    from career_agent.storage.repositories import PipelineRunRepo

    with transaction(conn):
        close_interrupted_runs(conn)
    return PipelineRunRepo(conn).start("rescore")


def _finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    status: PipelineRunStatus,
    stats: RescoreStats,
    *,
    error: str | None = None,
) -> None:
    from career_agent.storage.repositories import PipelineRunRepo

    PipelineRunRepo(conn).finish(run_id, status, stats.as_dict(), error)


def _elapsed(started: datetime) -> int:
    return int((datetime.now(UTC) - started).total_seconds() * 1000)

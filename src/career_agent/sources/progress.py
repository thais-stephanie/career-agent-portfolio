"""How far each source has got, right now, and whether it is worth waiting for.

WHY THIS FILE EXISTS
--------------------
Gupy takes hours. For the whole of that time this product could say two things
about it -- that a run had started, and, much later, what it had found. In
between there was nothing to show a candidate except a spinner, and a spinner
that lasts three hours is indistinguishable from a broken product.

Worse, the shape of the older experience was *collect everything, rescore
everything, then show jobs*. That makes the freshest source the slowest part of
the product decide when ANY job is visible, including the 11,134 Greenhouse
postings that were scored yesterday and are sitting in the database.

**The rule this file serves: Career Agent stays useful while its market data is
improving.** A source that is slow, paused, rate-limited, half-collected or
failing is a fact about FRESHNESS. It is not a reason to withhold the corpus.

WHAT IT IS NOT
--------------
It is not a new table, and that is the design decision worth defending. Every
counter a progress display needs was already written into `pipeline_run.
stats_json` -- `postings_seen`, `jobs_new`, `pages_read`, `claimed_total`,
`stopped_early`, `failures`. The only thing missing was writing it more than
once per run, which `PipelineRunRepo.progress` now does at the point a collector
already commits a page.

So this module READS. It adds no schema, it cannot lose data, and a collector
that is killed halfway leaves behind exactly the last page it recorded.

**NO INVENTED PERCENTAGES.** A percentage requires a denominator the PROVIDER
published. Five sources publish one (`claimed_total`); Gupy publishes per-slice
totals; the rest publish nothing, and for those this reports counted work and
says the total is unknown rather than dividing by a guess. An ETA is offered
only where a rate and a remainder are both real.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

__all__ = [
    "RefreshState",
    "SourceProgress",
    "read_progress",
]


class RefreshState(StrEnum):
    """Where one source's retrieval stands, in the vocabulary a person needs.

    These answer "should I wait for this, and if not, why not" -- which is a
    different question from `MatrixState` in `sources/matrix.py`, which answers
    "whose move is it" about the source's whole life. A source can be
    `PRODUCTION` there and `FAILED` here: it works, and last night's run did not.
    """

    #: No run of this collector has ever been recorded here.
    NOT_STARTED = "NOT_STARTED"
    #: Scheduled for this profile and not yet begun.
    QUEUED = "QUEUED"
    #: A run is in flight now. Counters below are live and may be zero.
    RUNNING = "RUNNING"
    #: Finished, and did not reach the end of what the provider offers. This is
    #: an honest ceiling, not a failure -- a collector stops at its own page
    #: budget by design, and saying COMPLETE would claim coverage nobody has.
    PARTIAL = "PARTIAL"
    #: Finished, and walked to the end of the feed.
    COMPLETE = "COMPLETE"
    #: Deliberately not being refreshed for this profile. A choice, reversible
    #: from the screen, and never a statement about the source's quality.
    PAUSED = "PAUSED"
    #: The last run ended in an error.
    FAILED = "FAILED"
    #: The vendor or a quota is refusing us, so there is nothing to wait for.
    BLOCKED = "BLOCKED"
    #: The last successful refresh is older than `STALE_AFTER_HOURS`. Whatever
    #: that run achieved, the corpus from this source is no longer current,
    #: and saying "Refresh complete" about a three-day-old read is not true.
    STALE = "STALE"
    #: Fine, and old enough that the next refresh should include it
    #: (`DUE_AFTER_HOURS`). Not a problem yet; STALE is.
    DUE = "DUE"
    #: The provider refused us (429, 999, 403, a sign-in wall). A refusal is
    #: never "no jobs": nothing is retried until `REFUSAL_COOLDOWN_HOURS` pass.
    RATE_LIMITED = "RATE_LIMITED"


#: When a successful refresh stops counting as current, and needs attention.
#: Well inside the product rule that no enabled source goes a week unseen.
STALE_AFTER_HOURS = 72
#: When a successful refresh is old enough to be refreshed again.
DUE_AFTER_HOURS = 24
#: How long a failed attempt waits before it is due again.
FAILURE_COOLDOWN_HOURS = 1
#: How long a refusal is respected before the source is asked again.
REFUSAL_COOLDOWN_HOURS = 24

#: Why a refresh is PARTIAL, as codes a screen translates.
REASON_PAGE_LIMIT = "PAGE_LIMIT"
REASON_SOURCE_CEILING = "SOURCE_CEILING"
REASON_REQUEST_BUDGET = "REQUEST_BUDGET"
REASON_SOME_FAILED = "SOME_FAILED"
REASON_BOARDS_DEFERRED = "BOARDS_DEFERRED"


def partial_reason(stats: Mapping[str, Any]) -> str | None:
    """The first reason a run left part of the source unread, if any."""
    if stats.get("ceiling_hit") or stats.get("slices_over_ceiling"):
        return REASON_SOURCE_CEILING
    if stats.get("budget_exhausted") or stats.get("slices_capped"):
        return REASON_REQUEST_BUDGET
    if stats.get("stopped_early"):
        return REASON_PAGE_LIMIT
    if stats.get("boards_deferred"):
        return REASON_BOARDS_DEFERRED
    failures = stats.get("failures")
    if (isinstance(failures, (dict, list)) and failures) or (
        isinstance(failures, int) and failures
    ):
        return REASON_SOME_FAILED
    # A board family's own slice counts failed boards rather than listing them.
    failed = stats.get("boards_failed")
    if isinstance(failed, int) and not isinstance(failed, bool) and failed > 0:
        return REASON_SOME_FAILED
    return None


@dataclass(frozen=True)
class SourceProgress:
    """One source's refresh, as a screen can honestly draw it.

    Every optional field is `None` when the answer is unknown, and a display
    must render "not measured" rather than zero. The distinction is the whole
    point: Gupy having read 0 pages and Gupy not saying how many pages exist are
    different facts and lead to different sentences.
    """

    source_id: str
    provider: str
    state: RefreshState
    #: Postings the provider handed us in the run being described.
    retrieved: int | None = None
    #: Of those, how many are now rows -- new plus seen again.
    persisted: int | None = None
    #: Units of work finished. Pages for most, slices for a partitioned feed.
    units_done: int | None = None
    #: What the PROVIDER said the whole is, when it said anything.
    expected_total: int | None = None
    started_at: str | None = None
    #: The end of the last run that succeeded, which is what "fresh as of" means.
    last_success: str | None = None
    #: Seconds, and only when a rate and a remainder are both real.
    eta_seconds: int | None = None
    blocker: str | None = None
    #: Everything else the run recorded, for a details view. Never interpreted.
    detail: Mapping[str, Any] = field(default_factory=dict)
    #: Why the source is PARTIAL (or was, before it went STALE): a code from
    #: `partial_reason`, never prose.
    reason: str | None = None
    #: When a refresh of this source last began, successful or not.
    last_attempt: str | None = None
    #: When a refused or failed source may be asked again, if it is waiting.
    cooldown_until: str | None = None

    @property
    def needs_attention(self) -> bool:
        """One rule, read by Settings & Sources and by the sidebar alike."""
        return self.state in (RefreshState.STALE, RefreshState.FAILED, RefreshState.RATE_LIMITED)

    @property
    def due(self) -> bool:
        """Whether "Refresh due sources" should run it now.

        Never a paused, blocked or running source, and never one still
        cooling down after a refusal or a failure."""
        if self.cooldown_until is not None:
            return False
        # A partial pass that never succeeded (deferred before reading
        # anything) has no age to go stale by: it is due until it succeeds.
        if self.state is RefreshState.PARTIAL and self.last_success is None:
            return True
        return self.state in (
            RefreshState.DUE,
            RefreshState.STALE,
            RefreshState.NOT_STARTED,
            RefreshState.FAILED,
            RefreshState.RATE_LIMITED,
        )

    @property
    def measurable(self) -> bool:
        """Whether a real percentage exists. False means show counters only."""
        return bool(self.expected_total) and self.retrieved is not None

    @property
    def percent(self) -> int | None:
        """Never above 100, and never invented.

        Capped because a provider's own total is an estimate it revises: Gupy
        reported 79,000 and served more than once. A bar reading 104% is a bar
        nobody believes again.
        """
        if not self.measurable:
            return None
        assert self.expected_total and self.retrieved is not None
        return min(100, int(round(100 * self.retrieved / self.expected_total)))


#: Where a source's work total lives, per provider, when it publishes one.
#: Asked of the run's own stats rather than hardcoded per vendor, so a new
#: collector recording the same key is measurable with no change here.
#: The stage a run writes when it walks every employer board in one pass.
#: Named here rather than imported so this module keeps no dependency on the
#: matrix, which asks the provider registry for the same thing.
_SHARED_STAGE = "collect"

_TOTAL_KEYS = ("claimed_total", "servable_total", "claimed_count")

#: What the run counted. Same reasoning: these are the names the collectors
#: already agreed on in V1.7's one-vocabulary funnel.
_RETRIEVED_KEYS = ("postings_seen", "postings_observed", "postings_listed")
_UNIT_KEYS = ("pages_read", "partition_read", "feeds_read", "categories_read")


def _first(stats: Mapping[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = stats.get(key)
        if isinstance(value, int):
            return value
    return None


def _persisted(stats: Mapping[str, Any]) -> int | None:
    new = stats.get("jobs_new")
    again = stats.get("jobs_seen_again")
    if isinstance(new, int) and isinstance(again, int):
        return new + again
    if isinstance(new, int):
        return new
    return None


def _partitioned_total(stats: Mapping[str, Any]) -> int | None:
    """A partitioned feed publishes its total per slice, not once.

    Not folded into `_TOTAL_KEYS` because the shape is different: a feed that
    caps its own offset is cut into slices small enough to walk to the end of,
    and each cut is measured against the live feed. The whole is the sum, and a
    sum of measured parts is still a measurement.

    Named for the SHAPE rather than for the vendor that has it, which is the
    rule `tests/unit/test_provider_neutrality.py` protects: a second partitioned
    source would be read by this function with no edit, and a function called
    after one vendor invites a second one called after the next.
    """
    totals = stats.get("partition_totals")
    if isinstance(totals, dict):
        numbers = [v for v in totals.values() if isinstance(v, int)]
        if numbers:
            return sum(numbers)
    return None


def _parse(moment: str | None) -> datetime | None:
    if not moment:
        return None
    try:
        return datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return None


def _eta(
    *, started_at: str | None, retrieved: int | None, total: int | None, now: datetime
) -> int | None:
    """Seconds left, or None. Refuses far more often than it answers.

    Four things have to be true before this says anything: the run is in flight,
    the provider published a total, we have retrieved some of it, and enough
    time has passed for a rate to mean something. Thirty seconds is the floor
    because the first page of a feed is not representative of the next eight
    hundred, and an ETA that swings from four minutes to four hours in its first
    minute teaches somebody to ignore the number for the rest of the run.
    """
    begun = _parse(started_at)
    if begun is None or not retrieved or not total or retrieved >= total:
        return None
    elapsed = (now - begun).total_seconds()
    if elapsed < 30:
        return None
    rate = retrieved / elapsed
    if rate <= 0:
        return None
    return int((total - retrieved) / rate)


def _state_of(row: sqlite3.Row, stats: Mapping[str, Any]) -> RefreshState:
    """What the last recorded run means, in one word.

    `stopped_early` and `ceiling_hit` are why PARTIAL exists as its own value.
    V1.6 established that "we chose to stop" and "they refused to serve" are
    different facts, and both of them mean the corpus does not hold everything
    the provider has -- which is what a candidate needs to know, and is not a
    failure.
    """
    if row["finished_at"] is None:
        return RefreshState.RUNNING
    status = str(row["status"] or "").upper()
    from career_agent.sources.public_health import rate_limited

    if rate_limited(stats):
        return RefreshState.RATE_LIMITED
    if status not in {"OK", "SUCCESS", "COMPLETE"}:
        return RefreshState.FAILED
    # One rule with `partial_reason`, so the app and `source-coverage` can
    # never disagree about the same run.
    if partial_reason(stats) is not None:
        return RefreshState.PARTIAL
    return RefreshState.COMPLETE


def _state_of_slice(slice_stats: Mapping[str, Any]) -> RefreshState | None:
    """What one family did inside a pass that walked several.

    None when the slice records nothing to judge, in which case the caller keeps
    the whole run's verdict -- silence about a family is not evidence that the
    family succeeded.
    """
    if slice_stats.get("boards_deferred"):
        return RefreshState.PARTIAL
    attempted = slice_stats.get("boards_attempted")
    failed = slice_stats.get("boards_failed")
    if failed is None and slice_stats.get("boards_succeeded") == attempted:
        # A slice that succeeded on every board it tried need not also say
        # "0 failed"; reading silence there as unknown marked six fully
        # collected families PARTIAL for another family's 404s.
        failed = 0
    if not isinstance(attempted, int) or not isinstance(failed, int) or attempted <= 0:
        return None
    if failed >= attempted:
        return RefreshState.FAILED
    if failed:
        return RefreshState.PARTIAL
    return RefreshState.COMPLETE


def _latest_runs(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    """The newest run per stage, and the newest SUCCESSFUL one beside it."""
    rows = conn.execute(
        "SELECT * FROM pipeline_run WHERE stage LIKE 'collect%' ORDER BY started_at DESC, id DESC"
    ).fetchall()
    latest: dict[str, sqlite3.Row] = {}
    for row in rows:
        latest.setdefault(str(row["stage"]), row)
    return latest


def _last_success(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute(
        "SELECT stage, MAX(finished_at) FROM pipeline_run"
        " WHERE stage LIKE 'collect%' AND status = 'OK' AND finished_at IS NOT NULL"
        " AND json_extract(stats_json, '$.deferred_reason') IS NULL"
        " GROUP BY stage"
    ).fetchall()
    return {str(r[0]): str(r[1]) for r in rows}


def _for_provider(stats: Mapping[str, Any], provider: str) -> Mapping[str, Any] | None:
    """One family's own numbers out of a run that walked several at once.

    FOUR FAMILIES SHARE ONE STAGE. `collect` walks every employer board in a
    single pass, so its `postings_observed` is the total across all of them --
    and reporting that total against each family separately said four different
    sources had each retrieved 20,079 postings on the same night.

    That is the class of number this module exists to refuse. The run already
    records `by_provider`; when it is there, each family gets its own row, and
    when it is not there the counters are withheld rather than guessed at.
    """
    by_provider = stats.get("by_provider")
    if isinstance(by_provider, dict):
        mine = by_provider.get(provider)
        if isinstance(mine, dict):
            return mine
        return {}
    return None


def _family_history(conn: sqlite3.Connection) -> tuple[dict, dict]:
    """A targeted refresh must not erase another family's previous progress."""
    latest: dict = {}
    successes: dict = {}
    for row in conn.execute(
        "SELECT * FROM pipeline_run WHERE stage = 'collect' ORDER BY started_at DESC, id DESC"
    ):
        try:
            counted = json.loads(row["stats_json"] or "{}").get("by_provider", {})
        except (ValueError, TypeError):
            continue
        for provider, stats in counted.items():
            if not isinstance(stats, dict) or not (
                stats.get("boards_attempted", 0) > 0
                or "postings_observed" in stats
                or stats.get("boards_deferred", 0) > 0
            ):
                continue
            latest.setdefault(provider, row)
            if row["finished_at"] and stats.get("boards_succeeded", 0) > 0:
                successes.setdefault(provider, str(row["finished_at"]))
    return latest, successes


def _older_than(stamp: str | None, hours: int, now: datetime) -> bool:
    if not stamp:
        return False
    try:
        moment = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return (now - moment).total_seconds() > hours * 3600


def _later(first: str | None, second: str | None) -> str | None:
    if not first:
        return second
    if not second:
        return first
    return max(first, second)


def _plus_hours(stamp: str, hours: int) -> datetime | None:
    moment = _parse(stamp)
    if moment is None:
        return None
    from datetime import timedelta

    return moment + timedelta(hours=hours)


_OUTCOME_STATE = {
    "COMPLETE": RefreshState.COMPLETE,
    "PARTIAL": RefreshState.PARTIAL,
    "RATE_LIMITED": RefreshState.RATE_LIMITED,
    "FAILED": RefreshState.FAILED,
}


def read_progress(
    conn: sqlite3.Connection,
    *,
    stage_for: Mapping[str, str],
    providers: Mapping[str, str] | None = None,
    paused: Mapping[str, str] | None = None,
    blocked: Mapping[str, str] | None = None,
    now: datetime | None = None,
    public: Mapping[str, Any] | None = None,
) -> list[SourceProgress]:
    """One row per source the caller names, newest run first.

    `stage_for` maps a source id to the `pipeline_run.stage` its collector
    writes, and it comes from the caller because this module may not name a
    vendor -- `tests/unit/test_provider_neutrality.py` walks the syntax tree and
    fails on one that does.

    `paused` and `blocked` carry a REASON rather than a flag, because a screen
    that says "paused" without saying why is a screen somebody has to guess at.
    """
    moment = now or datetime.now(UTC)
    paused = paused or {}
    blocked = blocked or {}
    latest = _latest_runs(conn)
    successes = _last_success(conn)
    family_latest, family_successes = _family_history(conn)

    out: list[SourceProgress] = []
    for source_id, stage in stage_for.items():
        provider_name = (providers or {}).get(source_id, "")
        row = (
            family_latest.get(provider_name)
            if stage == _SHARED_STAGE and provider_name
            else latest.get(stage)
        )
        stats: Mapping[str, Any] = {}
        if row is not None and row["stats_json"]:
            try:
                loaded = json.loads(str(row["stats_json"]))
            except json.JSONDecodeError:
                loaded = None
            if isinstance(loaded, dict):
                stats = loaded

        # A SHARED STAGE IS NOT THIS SOURCE'S RUN. When several families are
        # walked in one pass, read this family's own slice of it or nothing.
        counted: Mapping[str, Any] = stats
        provider_slice: Mapping[str, Any] | None = None
        provider_name = (providers or {}).get(source_id, "")
        if stage == _SHARED_STAGE and provider_name:
            provider_slice = _for_provider(stats, provider_name)
            counted = provider_slice if provider_slice is not None else {}

        if source_id in blocked:
            state = RefreshState.BLOCKED
        elif source_id in paused:
            state = RefreshState.PAUSED
        elif row is None:
            state = RefreshState.NOT_STARTED
        else:
            state = _state_of(row, stats)
            # A SHARED RUN FAILS FOR THE WHOLE PASS, NOT FOR EACH FAMILY.
            #
            # Measured 2026-09-09: `collect` ended FAILED because four Ashby
            # boards out of 5,140 answered NOT_FOUND -- and that marked
            # Greenhouse "Did not finish" on a night it read 121 of its 122
            # boards and stored every one of 11,134 postings with a full body.
            #
            # So when a family has its own slice, that slice decides. A family
            # with failures of its own is still PARTIAL; a family with none is
            # judged on what it actually did.
            if stage == _SHARED_STAGE and provider_slice is not None:
                state = _state_of_slice(provider_slice) or state

        last_success = (
            family_successes.get(provider_name)
            if stage == _SHARED_STAGE and provider_name
            else successes.get(stage)
        )
        reason = partial_reason(counted) if state is RefreshState.PARTIAL else None
        last_attempt = str(row["started_at"]) if row is not None else None
        last_finished = str(row["finished_at"]) if row is not None and row["finished_at"] else None

        # THE SHARED CATALOGUE'S OWN RECORD (migration 0045). Another profile
        # may have refreshed this public source since this profile last did;
        # when its record is newer, it decides.
        key = f"{_SHARED_STAGE}:{provider_name}" if stage == _SHARED_STAGE else stage
        seen = (public or {}).get(key)
        # A source this profile has blocked or paused is not its business:
        # another profile's use of it is never shown here.
        if state in (RefreshState.BLOCKED, RefreshState.PAUSED):
            seen = None
        if seen is not None:
            last_success = _later(last_success, seen.last_success_at)
            newer = last_attempt is None or seen.last_attempt_at > last_attempt
            if newer and state not in (
                RefreshState.BLOCKED,
                RefreshState.PAUSED,
                RefreshState.RUNNING,
            ):
                state = _OUTCOME_STATE.get(seen.last_outcome, state)
                reason = seen.last_reason if state is RefreshState.PARTIAL else None
            last_attempt = _later(last_attempt, seen.last_attempt_at)
            if newer:
                last_finished = seen.last_finished_at or last_finished

        if state in (RefreshState.COMPLETE, RefreshState.PARTIAL):
            if _older_than(last_success, STALE_AFTER_HOURS, moment):
                state = RefreshState.STALE
            elif _older_than(last_success, DUE_AFTER_HOURS, moment):
                state = RefreshState.DUE

        cooldown = None
        # From when it FAILED, not when it began: a long run must not use up
        # its own cooldown while it runs.
        ended = last_finished or last_attempt
        if ended and state in (RefreshState.RATE_LIMITED, RefreshState.FAILED):
            wait = (
                REFUSAL_COOLDOWN_HOURS
                if state is RefreshState.RATE_LIMITED
                else FAILURE_COOLDOWN_HOURS
            )
            until = _plus_hours(ended, wait)
            if until is not None and until > moment:
                cooldown = until.isoformat(timespec="seconds").replace("+00:00", "Z")

        retrieved = _first(counted, _RETRIEVED_KEYS)
        total = _first(counted, _TOTAL_KEYS) or _partitioned_total(counted)
        started = str(row["started_at"]) if row is not None else None
        out.append(
            SourceProgress(
                source_id=source_id,
                provider=stage.removeprefix("collect-") if stage != "collect" else "collect",
                state=state,
                retrieved=retrieved,
                persisted=_persisted(counted),
                units_done=_first(counted, _UNIT_KEYS),
                expected_total=total,
                started_at=started,
                last_success=last_success,
                eta_seconds=(
                    _eta(started_at=started, retrieved=retrieved, total=total, now=moment)
                    if state is RefreshState.RUNNING
                    else None
                ),
                blocker=blocked.get(source_id) or paused.get(source_id),
                detail=counted,
                reason=reason,
                last_attempt=last_attempt,
                cooldown_until=cooldown,
            )
        )
    out.sort(key=lambda p: (p.state is not RefreshState.RUNNING, p.source_id))
    return out

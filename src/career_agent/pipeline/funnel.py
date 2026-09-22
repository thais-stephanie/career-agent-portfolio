"""One shape for what every collector did, read back out of `pipeline_run`.

WHY THIS EXISTS
---------------
Every collector already counts itself carefully, and each of them counts in its
own words. Get on Board reports `categories_failed`, We Work Remotely reports
`feeds_failed`, Speedrun reports `details_failed`, and Gupy reports
`slices_over_ceiling`. Each of those names is right for its own source and
none of them can be compared with the others, so the question "which source is
losing postings, and where" had no answer that did not involve reading eleven
dataclasses.

Worse, a posting that a collector declined to store left a COUNT and no
reason. `postings_unaddressable: 34` says thirty-four postings were dropped and
does not say what was wrong with them, which is the difference between a
diagnosis and a rumour.

WHAT IT IS NOT
--------------
It is not a new set of counters and it changes no collector. Every number here
is already in the database; this reads `pipeline_run.stats_json` and arranges
it. A funnel that required each collector to also maintain a second tally is a
funnel that goes stale the first time somebody edits one of them.

NOT MEASURED IS A FIRST-CLASS ANSWER
-------------------------------------
Most sources never say how many postings they hold. Reporting `discovered: 0`
for those would be a lie that reads as a catastrophe, and reporting
`discovered = fetched` would be a lie that reads as success. A stage a
collector does not measure is `None`, printed as `not measured`, exactly the
way a phrase group with no measurable membership reports `None` rather than a
zero.

THE VOCABULARY, AND WHAT EACH STAGE MEANS HERE
------------------------------------------------
    discovered      what the SOURCE says it holds for what was asked
    fetched         HTTP requests this run actually made
    parsed          records that arrived and could be read as postings
    accepted        rows written or confirmed: new + already held
    rejected        records read and NOT stored, itemised by reason
    deduplicated    postings recognised as one already held elsewhere, which
                    is a SIGHTING under ADR-0013 and never a second job
    failed          things that went wrong, named

`discovered` and `parsed` are deliberately not expected to agree. A bounded
walk stops on purpose, a vendor caps an offset, and a window shows what is
recent -- three different reasons the second number is smaller, and the
collector's own stats say which.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

#: Keys a collector may use to say WHAT THE SOURCE HOLDS, most specific first.
#:
#: Five spellings because five sources answer the question and each answers a
#: slightly different one: Himalayas and Remote OK state a total for the whole
#: board, Jobicy states the size of the window it served, Speedrun distinguishes
#: what exists from what it will serve, and Programathor states how many rows
#: the listing pages carried. A source absent from this list never says.
_DISCOVERED_KEYS = (
    "claimed_total",
    "servable_total",
    "claimed_count",
    "postings_listed",
)

#: Records that arrived and could be read. `collect.py` is the ATS runner and
#: predates the shared vocabulary, which is why it has its own spelling.
_PARSED_KEYS = ("postings_seen", "postings_observed")

#: Why a record was read and not stored. The VALUE is the sentence a person
#: reads; the KEY is the counter a collector already keeps.
#:
#: These are the whole reason this module exists. A count with no reason is a
#: rumour, and every one of these used to be exactly that.
_REJECTION_REASONS: tuple[tuple[str, str], ...] = (
    (
        "postings_unaddressable",
        "no stable id or no URL, so ADR-0013 identity has nothing to resolve",
    ),
    ("postings_without_company", "no employer named in the record"),
)

#: Rejections that happen BEFORE a record is counted as parsed, and are
#: therefore outside `parsed` rather than inside it.
#:
#: One entry, and finding out it was one rather than four is what this module
#: was for. Programathor's `postings_seen` only increments when the detail page
#: ANSWERS, so its six unavailable postings never became parsed ones -- while
#: `postings_unaddressable` and `postings_without_company` are decided about
#: records that already had. Treating all four alike made this report print
#: `unaccounted: -6`, a negative number of postings, on its first run.
_PRE_PARSE_REASONS: tuple[tuple[str, str], ...] = (
    ("postings_unavailable", "the detail page did not answer, so nothing was read"),
    #: An ATS adapter increments this where it would otherwise `continue` past
    #: an entry, INSIDE `list_postings` -- so the record never reaches the
    #: runner and never becomes a `postings_observed`. It sat with the
    #: post-parse reasons at first and produced `unaccounted: -1` on Ashby, one
    #: posting short, in the same shape as Programathor's `-6`.
    ("postings_skipped_malformed", "the record could not be read as a posting at all"),
)

#: Counters that report a SOURCE-LEVEL failure rather than a posting-level one.
#: Named individually because each names a different unit -- a board, a feed, a
#: category, a detail request -- and summing them would invent a number whose
#: unit is nothing.
_FAILURE_COUNTS: tuple[str, ...] = (
    "boards_failed",
    "feeds_failed",
    "categories_failed",
    "details_failed",
)

#: Counters that say THIS RUN DID NOT FINISH, which is not a failure and must
#: never be printed as one. `stopped_early` is this product's page budget;
#: `ceiling_hit` is the vendor refusing. V1.6 separated those two on purpose.
_BOUNDS: tuple[str, ...] = (
    "stopped_early",
    "truncated",
    "ceiling_hit",
    "slices_over_ceiling",
    "categories_page_limited",
    "categories_truncated",
    "beyond_reach",
    "window_full",
)


@dataclass(frozen=True)
class Rejection:
    """One reason records were not stored, and how many."""

    reason: str
    count: int


@dataclass(frozen=True)
class Funnel:
    """What one collector run did, in the shared vocabulary.

    Every integer field is `int | None`, and `None` means THIS COLLECTOR DOES
    NOT MEASURE THIS -- never zero. The distinction is the point: a source that
    publishes no total and a source that holds nothing are different facts and
    used to print identically.
    """

    stage: str
    status: str
    started_at: str
    finished_at: str | None = None
    discovered: int | None = None
    fetched: int | None = None
    parsed: int | None = None
    accepted: int | None = None
    rejected: int | None = None
    deduplicated: int | None = None
    failed: int | None = None
    jobs_new: int | None = None
    jobs_changed: int | None = None
    rejections: tuple[Rejection, ...] = ()
    #: Records the source offered that never became parsed postings, so they
    #: sit OUTSIDE `parsed` and outside the arithmetic below.
    lost_before_parsing: tuple[Rejection, ...] = ()
    failures: tuple[str, ...] = ()
    #: Bounds this run ran under, as `name=value`. A run that stopped at a page
    #: budget is not a run that lost postings, and the two must not read alike.
    bounds: tuple[str, ...] = ()
    #: The same funnel again, per PROVIDER, for a stage that runs several.
    #:
    #: Empty for every stage that is one source, which is all of them but the
    #: ATS runner. That one drives Greenhouse, Lever, Ashby and Recruiterflow
    #: in a single pass and reported one set of totals for all four, so the
    #: question this module exists to answer had no answer there.
    per_provider: tuple[tuple[str, Funnel], ...] = ()
    error: str | None = None

    @property
    def unaccounted(self) -> int | None:
        """Records parsed that were neither accepted nor rejected nor deduped.

        THE NUMBER WORTH LOOKING AT. It should be zero, and a collector that
        drops a posting without counting why is exactly what makes it not be.
        `None` when the run does not measure enough to ask.
        """
        if self.parsed is None or self.accepted is None:
            return None
        return self.parsed - self.accepted - (self.rejected or 0) - (self.deduplicated or 0)


def _int(stats: Mapping[str, Any], key: str) -> int | None:
    value = stats.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _first_int(stats: Mapping[str, Any], keys: Sequence[str]) -> int | None:
    for key in keys:
        found = _int(stats, key)
        if found is not None:
            return found
    return None


def _discovered(stats: Mapping[str, Any]) -> int | None:
    """What the source says it holds, however this collector spells it.

    Gupy is the one that cannot use a single key: it cuts the feed into slices
    and probes each one, so what it holds for what was asked is the sum of
    those probes. Summing them is arithmetic the collector already did in
    smaller pieces, not a new measurement.
    """
    totals = stats.get("partition_totals")
    if isinstance(totals, dict) and totals:
        counted = [v for v in totals.values() if isinstance(v, int) and not isinstance(v, bool)]
        if counted:
            return sum(counted)
    return _first_int(stats, _DISCOVERED_KEYS)


def _rejections(
    stats: Mapping[str, Any], reasons: Sequence[tuple[str, str]]
) -> tuple[Rejection, ...]:
    found: list[Rejection] = []
    for key, sentence in reasons:
        count = _int(stats, key)
        if count:
            found.append(Rejection(reason=sentence, count=count))
    return tuple(found)


def _accepted(stats: Mapping[str, Any]) -> int | None:
    """New rows plus rows confirmed still open.

    `jobs_changed` is deliberately NOT added: it is a subset of
    `jobs_seen_again` -- the ones whose description moved -- and adding it
    would count those postings twice and make the funnel stop summing.
    """
    new = _int(stats, "jobs_new")
    again = _int(stats, "jobs_seen_again")
    if new is None and again is None:
        return None
    return (new or 0) + (again or 0)


def _bounds(stats: Mapping[str, Any]) -> tuple[str, ...]:
    named: list[str] = []
    for key in _BOUNDS:
        value = stats.get(key)
        if isinstance(value, bool):
            if value:
                named.append(key)
        elif isinstance(value, list) and value:
            named.append(f"{key}={len(value)}")
        elif isinstance(value, int) and value:
            named.append(f"{key}={value}")
    return tuple(named)


def _per_provider(stats: Mapping[str, Any]) -> tuple[tuple[str, Funnel], ...]:
    """One funnel per provider, for a stage that ran several.

    The nested funnels carry the stage's own status and timestamps: they are
    the same run seen through one provider, not runs of their own, and giving
    them invented ones would be four rows claiming to be four runs.
    """
    split = stats.get("by_provider")
    if not isinstance(split, dict) or not split:
        return ()
    built: list[tuple[str, Funnel]] = []
    for provider, counters in sorted(split.items()):
        if not isinstance(counters, dict):
            continue
        built.append(
            (
                str(provider),
                funnel_from(
                    stage=str(provider),
                    status=str(stats.get("_status") or "OK"),
                    started_at="",
                    finished_at=None,
                    error=None,
                    stats=counters,
                ),
            )
        )
    return tuple(built)


def funnel_from(
    *,
    stage: str,
    status: str,
    started_at: str,
    finished_at: str | None,
    error: str | None,
    stats: Mapping[str, Any] | None,
) -> Funnel:
    """One stored run as a funnel. Reads nothing but what it is given."""
    stats = stats or {}
    http = stats.get("http")
    http = http if isinstance(http, dict) else {}
    rejections = _rejections(stats, _REJECTION_REASONS)
    lost = _rejections(stats, _PRE_PARSE_REASONS)

    collector_failures = stats.get("failures")
    failures = (
        tuple(str(f) for f in collector_failures) if isinstance(collector_failures, list) else ()
    )
    source_level = sum(_int(stats, key) or 0 for key in _FAILURE_COUNTS)
    transport = _int(http, "failure_count") or 0
    failed = len(failures) + source_level + transport

    return Funnel(
        stage=stage,
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        discovered=_discovered(stats),
        fetched=_int(http, "requests"),
        parsed=_first_int(stats, _PARSED_KEYS),
        accepted=_accepted(stats),
        rejected=sum(r.count for r in rejections) if rejections else 0,
        deduplicated=_int(stats, "duplicates_total"),
        failed=failed,
        jobs_new=_int(stats, "jobs_new"),
        jobs_changed=_int(stats, "jobs_changed"),
        rejections=rejections,
        lost_before_parsing=lost,
        failures=failures,
        bounds=_bounds(stats),
        per_provider=_per_provider(stats),
        error=error,
    )


def latest_funnels(conn: sqlite3.Connection) -> tuple[Funnel, ...]:
    """The most recent run of every collection stage, newest first.

    THE MOST RECENT, not every run. A stage that has been run nightly for a
    week holds seven rows and six of them answer a question nobody asked; what
    a person wants to know is whether the last one worked.

    Read-only, and deliberately so: this is a diagnostic and a diagnostic that
    writes is one nobody can safely run while worrying.
    """
    rows = conn.execute(
        "SELECT stage, status, started_at, finished_at, stats_json, error"
        "  FROM pipeline_run"
        " WHERE stage LIKE 'collect%'"
        " ORDER BY started_at DESC"
    ).fetchall()

    seen: set[str] = set()
    funnels: list[Funnel] = []
    for stage, status, started_at, finished_at, stats_json, error in rows:
        if stage in seen:
            continue
        seen.add(stage)
        stats: Mapping[str, Any] | None = None
        if stats_json:
            try:
                parsed = json.loads(stats_json)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, dict):
                stats = parsed
        funnels.append(
            funnel_from(
                stage=stage,
                status=status,
                started_at=started_at,
                finished_at=finished_at,
                error=error,
                stats=stats,
            )
        )
    return tuple(funnels)


@dataclass(frozen=True)
class NeverRun:
    """A stage with an adapter and no run at all.

    Named rather than omitted. A report that lists only what has run answers
    "did the collections work" and silently drops "and which ones never
    happened", which is the question a corpus missing a whole market is the
    answer to.
    """

    stage: str
    reason: str = "no run recorded"


def missing_stages(known: Sequence[str], funnels: Sequence[Funnel]) -> tuple[NeverRun, ...]:
    ran = {f.stage for f in funnels}
    return tuple(NeverRun(stage=stage) for stage in known if stage not in ran)


#: Stages with a command behind them. Kept beside the reader rather than in the
#: CLI so a report knows what SHOULD have run, not only what did.
KNOWN_STAGES: tuple[str, ...] = (
    "collect",
    "collect-gupy",
    "collect-programathor",
    "collect-getonbrd",
    "collect-himalayas",
    "collect-fourdayweek",
    "collect-dynamitejobs",
    "collect-jobgether",
    "collect-jobicy",
    "collect-remotive",
    "collect-workingnomads",
    "collect-wwr",
    "collect-speedrun",
    "collect-arbeitnow",
    "collect-remoteok",
    "collect-workable",
)

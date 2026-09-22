"""Collection: walk registered boards, persist what they return, track lifecycle.

This module is where the closing-safety rule actually lives, so it is worth
being explicit about the shape of a board pass:

1. Capture `pass_started_at` **before** asking the provider for anything.
2. Ask for the postings. If that raises, record the failure and return -- do not
   advance `last_collected_at`, do not close anything, do not touch the board's
   jobs at all.
3. On success, persist every posting. Each one gets `last_seen_at >=
   pass_started_at`.
4. Only now, close postings on this board whose `last_seen_at` is still older
   than `pass_started_at`. Those are the ones the board genuinely stopped
   returning.

Step 2 is the whole point. A board that times out tells us nothing about which
of its jobs still exist, and inferring "they all closed" from a network problem
would quietly destroy a company's posting history.

An HTTP 200 carrying an empty list is a different matter: the board answered,
and it said there are no openings. That IS grounds for closing, and it is
distinguishable precisely because the request succeeded.
"""

import time
from collections.abc import Sequence
from contextlib import suppress
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from career_agent.clock import now_utc
from career_agent.domain.enums import CollectionStatus, PipelineRunStatus
from career_agent.domain.normalize import content_hash
from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import FetchError, FetchStats, HttpFetcher
from career_agent.providers.base import BoardRef, JobProvider, RawPosting, resolve_metadata
from career_agent.providers.registry import board_providers, get_provider
from career_agent.storage.db import transaction
from career_agent.storage.records import JobRecord, ProviderPayloadRecord
from career_agent.storage.repositories import (
    CompanyRepo,
    JobRawRepo,
    JobRepo,
    PipelineRunRepo,
    ProviderPayloadRepo,
    SourceBoardRepo,
)

#: One source the plan refused, with the board row it came from and why.
#: A tuple rather than a class: it never leaves this module and a name for
#: each position is exactly what the unpacking at the call site gives it.
PlanRejection = tuple["BoardRef", str, str]


@dataclass(frozen=True)
class DisappearancePolicy:
    """When a SUCCESSFUL pass is too good at making postings vanish.

    Closing is already safe against every failure the pipeline can *see* -- a
    404, a timeout, a malformed body, a rate limit -- because those raise and
    this code is never reached. It is not safe against a pass that succeeds
    while returning wrong data: a truncated response, a board silently migrated,
    a vendor bug answering `200` with a partial list. A board of 300 that returns
    12 would close 288 postings, and every one of those closures would look
    perfectly legitimate.

    **PROVISIONAL -- revisit after real scheduled history.** These numbers are
    not empirically tuned and saying so is more useful than pretending: the
    corpus behind them is a handful of passes taken minutes apart in which no
    board declined at all. They are a typed object with an explicit default
    rather than a magic number, and rather than a field in `sources.yaml`, which
    the runtime still does not read.

    The bias is deliberate. A false alarm delays some closures by one pass. A
    false negative silently erases hundreds of jobs.
    """

    minimum_previous_open: int = 20
    suspicious_drop_ratio: float = 0.50

    def is_suspicious(self, previous_open: int, missing: int) -> bool:
        if previous_open < self.minimum_previous_open:
            return False
        return missing / previous_open >= self.suspicious_drop_ratio


@dataclass
class BoardHold:
    """A board whose closures were withheld because its response looked wrong."""

    company_slug: str
    provider: str
    board_identifier: str
    previously_open: int
    observed: int
    would_have_closed: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "company_slug": self.company_slug,
            "provider": self.provider,
            "board_identifier": self.board_identifier,
            "previously_open": self.previously_open,
            "observed": self.observed,
            "would_have_closed": self.would_have_closed,
        }


@dataclass
class BoardFailure:
    """Enough to diagnose a failed board, and no secrets."""

    company_slug: str
    provider: str
    board_identifier: str
    category: str
    message: str
    attempts: int
    occurred_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "company_slug": self.company_slug,
            "provider": self.provider,
            "board_identifier": self.board_identifier,
            "category": self.category,
            "message": self.message[:300],
            "attempts": self.attempts,
            "occurred_at": self.occurred_at,
        }


@dataclass
class CollectionStats:
    """What actually happened during a run. Reported, and stored on pipeline_run."""

    by_board: list[dict[str, Any]] = field(default_factory=list)
    deferred_reason: str | None = None
    boards_attempted: int = 0
    boards_succeeded: int = 0
    boards_failed: int = 0
    postings_observed: int = 0
    postings_skipped_malformed: int = 0
    jobs_new: int = 0
    jobs_seen_again: int = 0
    jobs_changed: int = 0
    jobs_closed: int = 0
    #: Mass-disappearance guard outcomes.
    boards_held: int = 0
    boards_confirmed_drop: int = 0
    boards_recovered: int = 0
    #: Sources this run could not ADDRESS, refused before any request: an
    #: identifier from another provider's vocabulary, or a provider with no
    #: adapter. Separate from `boards_failed`, and deliberately NOT a failure.
    #:
    #: On the owner's corpus this is 408 of 676, and every one of them is
    #: normal: an aggregator's `source_board` row records which employer a
    #: posting CAME FROM. It was never somewhere to fetch, and the dedicated
    #: `collect-himalayas`, `collect-getonbrd`, `collect-wwr` and
    #: `collect-workingnomads` commands are what actually read those feeds.
    #:
    #: "We asked and it failed" and "this is not a thing we can ask" are
    #: different sentences, and only the first one is about the network.
    boards_rejected: int = 0
    #: Rows belonging to a FEED rather than to a board family. Not failures and
    #: not rejections: a feed collector registers one `source_board` per
    #: employer so ADR-0008 can attribute a posting to whoever wrote it, and
    #: those rows are healthy. Counted so the number is visible rather than
    #: silently skipped.
    boards_not_this_runners: int = 0
    descriptions_non_empty: int = 0
    descriptions_empty: int = 0
    #: Postings a board LISTED and would not SERVE when asked for the detail:
    #: closed between the two requests, or a 404 for reasons of its own. Not
    #: stored, not closed, counted -- and no longer the end of the run.
    postings_fetch_failed: int = 0
    fetch_failure_samples: list[str] = field(default_factory=list)
    elapsed_ms: int = 0
    failures: list[BoardFailure] = field(default_factory=list)
    holds: list[BoardHold] = field(default_factory=list)
    http: dict[str, Any] = field(default_factory=dict)
    #: Provider-metadata transport coverage. Not decoration: a declared path
    #: that resolves zero times means a vendor renamed a field, and finding
    #: that out here is far cheaper than finding it out at M2.
    metadata: dict[str, Any] = field(default_factory=dict)
    #: The same counters again, split by PROVIDER.
    #:
    #: This stage runs four families at once -- Greenhouse, Lever, Ashby and
    #: Recruiterflow -- and reported one set of totals for all of them, so
    #: "which source is losing postings" had no answer here at all. Every other
    #: collector is one source and did not need this.
    #:
    #: Filled only through `tally` below, never assigned to directly.
    by_provider: dict[str, dict[str, int]] = field(default_factory=dict)

    def tally(self, provider: str, counter: str, amount: int = 1) -> None:
        """Add to a counter and to that provider's copy of it, in one call.

        ONE METHOD RATHER THAN TWO INCREMENTS, and that is the whole design.
        A per-provider breakdown maintained beside the totals is a breakdown
        that disagrees with them the first time somebody adds a counter and
        updates one of the two places -- silently, and in the direction that
        makes a report look fine.
        """
        setattr(self, counter, getattr(self, counter) + amount)
        bucket = self.by_provider.setdefault(provider, {})
        bucket[counter] = bucket.get(counter, 0) + amount

    def as_dict(self) -> dict[str, Any]:
        return {
            "by_board": self.by_board,
            "deferred_reason": self.deferred_reason,
            "stopped_early": self.deferred_reason is not None,
            "boards_attempted": self.boards_attempted,
            "boards_succeeded": self.boards_succeeded,
            "boards_failed": self.boards_failed,
            "boards_not_this_runners": self.boards_not_this_runners,
            "postings_observed": self.postings_observed,
            "postings_skipped_malformed": self.postings_skipped_malformed,
            "postings_fetch_failed": self.postings_fetch_failed,
            "fetch_failure_samples": list(self.fetch_failure_samples),
            "jobs_new": self.jobs_new,
            "jobs_seen_again": self.jobs_seen_again,
            "jobs_changed": self.jobs_changed,
            "jobs_closed": self.jobs_closed,
            "boards_held": self.boards_held,
            "boards_confirmed_drop": self.boards_confirmed_drop,
            "boards_recovered": self.boards_recovered,
            "boards_rejected": self.boards_rejected,
            "holds": [h.as_dict() for h in self.holds],
            "descriptions_non_empty": self.descriptions_non_empty,
            "descriptions_empty": self.descriptions_empty,
            "elapsed_ms": self.elapsed_ms,
            "http": self.http,
            "by_provider": {k: dict(v) for k, v in self.by_provider.items()},
            "metadata": self.metadata,
            "failures": [f.as_dict() for f in self.failures],
        }


class Collector:
    """Runs collection for a set of boards against one database connection."""

    def __init__(
        self,
        conn: Any,
        fetcher: HttpFetcher,
        policy: DisappearancePolicy | None = None,
    ) -> None:
        self.conn = conn
        self.fetcher = fetcher
        self.policy = policy or DisappearancePolicy()
        self.companies = CompanyRepo(conn)
        self.boards = SourceBoardRepo(conn)
        self.raw = JobRawRepo(conn)
        self.jobs = JobRepo(conn)
        self.payloads = ProviderPayloadRepo(conn)
        self.runs = PipelineRunRepo(conn)
        # (provider, path) pairs -- what the adapters said they could supply,
        # and what the payloads actually answered. Kept as provider+path pairs
        # rather than as anything typed, because this module must never learn
        # what any particular path is called.
        self._paths_declared: set[tuple[str, str]] = set()
        self._paths_resolved: set[tuple[str, str]] = set()
        self._dimension_counts: dict[str, int] = {}

    def collect_all(
        self,
        use_cache: bool = True,
        on_board: Any = None,
        should_stop: Any = None,
        board_ids: set[str] | None = None,
    ) -> CollectionStats:
        """Collect every active board. One board's failure never stops the rest.

        `on_board(done, total, board)` is called after each board, so a caller
        can report progress WHILE the run happens rather than after it. The
        interface showed 0 of 234 for four minutes and then jumped to 234
        without this: a progress indicator that only moves once is a spinner
        with extra steps.

        `should_stop()` is checked BETWEEN boards and never inside one. A board
        is a unit of work with its own transaction, so stopping mid-board is
        the only way this could leave the database inconsistent -- which makes
        it the one thing cancellation must not do.
        """
        started = time.monotonic()
        stats = CollectionStats()

        rows = self.conn.execute(
            "SELECT sb.*, c.slug AS company_slug FROM source_board sb"
            " JOIN company c ON c.id = sb.company_id"
            " WHERE sb.active = 1 ORDER BY c.slug"
        ).fetchall()

        if board_ids is not None:
            rows = [row for row in rows if row["id"] in board_ids]

        run_id = None
        with transaction(self.conn):
            run_id = self.runs.start("collect")

        # **THE WHOLE PLAN IS CHECKED BEFORE THE FIRST SOCKET OPENS.**
        # A `source_board` row can be structurally impossible -- an identifier
        # from another provider's vocabulary, or a provider with no adapter --
        # and that is knowable without asking anybody. Finding out mid-run is
        # how one bad row ended a whole collection with
        # `unknown WWR feed '6sense'`.
        planned, rejected = self._plan(rows)
        for board, board_id, reason in rejected:
            # **A ROW THIS RUNNER CANNOT ASK ABOUT IS NOT A BROKEN BOARD**, and
            # the difference is 4,860 rows wide. A feed's per-employer
            # attribution row is healthy and correct; marking it in error would
            # make `source-health` report "3,794 of 3,794 boards last recorded
            # an error" about a source that is working perfectly, which is how
            # a health report becomes something people learn to ignore.
            #
            # It is COUNTED, and the count is reported, so nothing is silent.
            if board.provider not in set(board_providers()):
                stats.tally(board.provider, "boards_not_this_runners")
                continue
            stats.boards_rejected += 1
            stats.failures.append(
                BoardFailure(
                    company_slug=board.company_slug,
                    provider=board.provider,
                    board_identifier=board.board_identifier,
                    category="NOT_ADDRESSABLE",
                    message=reason,
                    attempts=0,
                    occurred_at=now_utc(),
                )
            )
            with transaction(self.conn):
                self.boards.mark_error(board_id, f"NOT_ADDRESSABLE: {reason[:200]}")

        for row, board in planned:
            if should_stop is not None and should_stop():
                break
            saved = deepcopy(stats)
            board_started = time.monotonic()
            stats.tally(board.provider, "boards_attempted")
            counters = (
                "postings_observed",
                "jobs_new",
                "jobs_seen_again",
                "descriptions_non_empty",
                "descriptions_empty",
                "postings_skipped_malformed",
                "postings_fetch_failed",
                "boards_failed",
                "boards_succeeded",
            )
            before = {key: getattr(stats, key) for key in counters}
            try:
                self._collect_board(board, row["id"], row["company_id"], stats, use_cache)
            except (BudgetExpired, KeyboardInterrupt) as exc:
                # Fetching precedes the atomic write. No partial board has
                # committed, so partial counters cannot claim persisted jobs.
                stats = saved
                stats.deferred_reason = (
                    "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "TIME_BUDGET"
                )
                stats.by_provider.setdefault(board.provider, {})["boards_deferred"] = 1
                stats.by_board.append(
                    {
                        "provider": board.provider,
                        "board_identifier": board.board_identifier,
                        "elapsed_ms": max(1, int((time.monotonic() - board_started) * 1000)),
                        "deferred_reason": stats.deferred_reason,
                    }
                )
                break
            stats.by_board.append(
                {
                    "provider": board.provider,
                    "board_identifier": board.board_identifier,
                    "elapsed_ms": max(1, int((time.monotonic() - board_started) * 1000)),
                    "finished_at": now_utc(),
                    **{key: getattr(stats, key) - before[key] for key in counters},
                }
            )
            with transaction(self.conn):
                self.runs.progress(run_id, stats.as_dict())
            if on_board is not None:
                # Never let a reporting callback take down a collection run:
                # the postings are the point and the progress bar is not.
                with suppress(Exception):
                    on_board(stats.boards_attempted, len(rows), board)

        stats.elapsed_ms = int((time.monotonic() - started) * 1000)
        stats.http = self.fetcher.stats.as_dict()
        stats.metadata = self._metadata_summary()

        # **A REJECTION IS NOT A FAILURE.** 408 of the owner's 676 configured
        # sources are provenance rows written by a dedicated collector -- they
        # record which employer a posting came from and were never fetch
        # targets. Counting them as failures would mark every single run
        # FAILED and teach her to ignore the word.
        with transaction(self.conn):
            self.runs.finish(
                run_id,
                # **A FRACTION, NEVER A STATE.** This read
                # `OK if boards_failed == 0`, so 276 boards of 280 collecting
                # perfectly reported the whole run FAILED because four
                # companies had taken their boards down.
                #
                # V1.2 made exactly this correction to `source-health`: "names
                # a fraction, never a state. Three boards of 116 recording
                # NOT_FOUND is a company that took its board down, not a broken
                # connector." A run status that goes red for that is a red
                # light people learn to ignore.
                #
                # FAILED now means the run collected NOTHING while trying. The
                # per-board failures are in `stats.failures` either way, named
                # and reported, so nothing is hidden by this.
                PipelineRunStatus.OK
                if stats.boards_succeeded > 0 or stats.boards_attempted == 0
                else PipelineRunStatus.FAILED,
                stats=stats.as_dict(),
                error=None
                if stats.boards_failed == 0
                else f"{stats.boards_failed} board(s) failed",
            )
        if stats.deferred_reason == "INTERRUPTED":
            raise KeyboardInterrupt
        return stats

    def _plan(self, rows: Sequence[Any]) -> tuple[list[tuple[Any, BoardRef]], list[PlanRejection]]:
        """Split the configured sources into what can be asked and what cannot.

        Nothing here opens a socket. `get_provider` constructs an adapter and
        `validate_board` answers from the adapter's own vocabulary, so a
        structurally impossible source is known before the run begins rather
        than discovered as an exception in the middle of it.
        """
        planned: list[tuple[Any, BoardRef]] = []
        rejected: list[PlanRejection] = []
        addressable = set(board_providers())
        for row in rows:
            board = BoardRef(
                company_slug=row["company_slug"],
                provider=row["provider"],
                board_identifier=row["board_identifier"],
                board_url=row["board_url"],
            )
            # **THE ROW BELONGS TO A FEED, AND A FEED HAS NO BOARD PER
            # EMPLOYER.** Checked before the adapter is even constructed,
            # because it is a fact about the FAMILY rather than about this row.
            #
            # Every feed collector registers one `source_board` per employer,
            # and it is right to: ADR-0008 says a board belongs to one company,
            # and that row is what attributes a Gupy posting to the employer
            # who wrote it rather than to a stream. It is an ATTRIBUTION
            # record, not a collection plan, and this runner could not tell the
            # difference.
            #
            # Measured 2026-09-09, after the deep Gupy collection: 5,140 rows,
            # of which 3,794 are Gupy employers, 591 Himalayas, 142 Speedrun,
            # 134 Get on Board, 90 We Work Remotely, 73 Programathor and 71
            # Jobicy. 280 of the 5,140 are boards this runner can actually ask
            # about. A run left going for forty minutes reached EIGHTEEN, and
            # the forty-six ATS boards added that day were never reached at all.
            #
            # `addresses_boards_by_company` is the question, and it already
            # existed: `discover` has asked it since V1.4 to decide what may be
            # probed. The collector had never asked it.
            if board.provider not in addressable:
                rejected.append(
                    (
                        board,
                        row["id"],
                        f"{board.provider} is a feed, not a board per employer. This row "
                        "attributes a posting to its employer and is not something to collect "
                        f"from; use the `collect-{board.provider}` command instead.",
                    )
                )
                continue
            try:
                provider = get_provider(board.provider, self.fetcher)
            except Exception as exc:  # noqa: BLE001 -- an unregistered provider name
                rejected.append((board, row["id"], f"no adapter for provider: {exc}"))
                continue
            validate = getattr(provider, "validate_board", None)
            reason = validate(board) if callable(validate) else None
            if reason:
                rejected.append((board, row["id"], reason))
                continue
            planned.append((row, board))
        return planned, rejected

    def _collect_board(
        self,
        board: BoardRef,
        board_id: str,
        company_id: str,
        stats: CollectionStats,
        use_cache: bool,
    ) -> None:
        provider = get_provider(board.provider, self.fetcher)
        self._paths_declared.update((provider.name, path) for path in provider.field_map.paths())

        try:
            stubs = list(provider.list_postings(board))
        except BudgetExpired:
            raise
        except FetchError as exc:
            # The board told us nothing. Record it, leave last_collected_at
            # alone, and close NOTHING.
            stats.tally(board.provider, "boards_failed")
            stats.failures.append(
                BoardFailure(
                    company_slug=board.company_slug,
                    provider=board.provider,
                    board_identifier=board.board_identifier,
                    category=exc.category.value,
                    message=exc.message,
                    attempts=exc.attempts,
                    occurred_at=exc.occurred_at,
                )
            )
            with transaction(self.conn):
                self.boards.mark_error(board_id, f"{exc.category.value}: {exc.message[:200]}")
            return
        except Exception as exc:  # noqa: BLE001
            # **ONE BROKEN SOURCE MAY NOT END THE RUN.** `FetchError` above is
            # the failure an adapter is expected to raise; this is everything
            # it is not -- a parser meeting a shape nobody predicted, a
            # vocabulary mismatch the plan check did not catch, a bug.
            #
            # Until now those propagated out of `collect_all` and the whole
            # collection stopped. Measured 2026-09-08: one `source_board` row
            # out of 442 raised `unknown WWR feed '6sense'` and the owner's
            # retrieval ended there, with the other 441 sources never asked.
            #
            # Recorded with its type, so "this source is broken" and "this
            # source was unreachable" stay different sentences, and closing
            # NOTHING -- an exception is not a statement about what still
            # exists.
            stats.tally(board.provider, "boards_failed")
            stats.failures.append(
                BoardFailure(
                    company_slug=board.company_slug,
                    provider=board.provider,
                    board_identifier=board.board_identifier,
                    category="ADAPTER_ERROR",
                    message=f"{type(exc).__name__}: {exc}",
                    attempts=1,
                    occurred_at=now_utc(),
                )
            )
            with transaction(self.conn):
                self.boards.mark_error(board_id, f"ADAPTER_ERROR: {str(exc)[:200]}")
            return

        # ENTRIES THE ADAPTER READ AND COULD NOT USE, counted at last. Read
        # here because `get_provider` builds one adapter per board above, so
        # the instance counter is this board's. `postings_skipped_malformed`
        # was declared when this dataclass was written and never incremented,
        # under a Greenhouse docstring promising it surfaced in the run
        # statistics. It does now.
        # `getattr`, the way `validate_board` is asked for two lines below, and
        # for the same reason: a provider that predates a capability must not
        # end a run by not having it. `ScriptedProvider` in the retrieval-plan
        # tests is exactly that, and requiring the attribute took out three
        # tests whose whole subject is that one broken adapter cannot stop the
        # others.
        stats.tally(
            board.provider,
            "postings_skipped_malformed",
            getattr(provider, "postings_skipped", 0),
        )

        # FETCH FIRST, WRITE ONCE. The detail requests used to run INSIDE the
        # board's transaction, so a family whose listing carries no body --
        # Workday, one GET per posting at a one-second delay -- held the
        # database's single write lock for as long as the board took to read.
        # A board of two thousand postings is thirty-five minutes during which
        # the interface's own writes (a status, a note, a hide) fail after the
        # five-second wait. Nothing about the network belongs under a lock.
        #
        # And ONE posting the board lists and will not serve -- closed between
        # the listing and the detail, a 404 -- no longer ends the whole run. It
        # is counted and sampled, and it is left in the SEEN set: a posting we
        # could not read is not a posting the board stopped returning, so it is
        # never closed on that evidence.
        seen_external_ids: set[str] = set()
        fetched: list[tuple[Any, RawPosting]] = []
        for stub in stubs:
            self.fetcher.check_budget()
            stats.tally(board.provider, "postings_observed")
            seen_external_ids.add(stub.external_id)
            try:
                fetched.append((stub, provider.fetch_posting(board, stub)))
            except FetchError as exc:
                stats.tally(board.provider, "postings_fetch_failed")
                if len(stats.fetch_failure_samples) < 10:
                    stats.fetch_failure_samples.append(
                        f"{board.provider}:{board.board_identifier} {stub.external_id}"
                        f" {exc.category.value}: {exc.message[:120]}"
                    )

        self.fetcher.check_budget()
        with transaction(self.conn):
            for stub, posting in fetched:
                self._persist(provider, board, board_id, company_id, stub, posting, stats)

            # Reached ONLY on success, which is the whole safety property: the
            # board answered, so its silence about a posting is meaningful.
            #
            # This runs for an empty board too. A company that closed every role
            # is real, and it is distinguishable from a failure because we got a
            # valid response rather than an exception.
            self.boards.mark_collected(board_id)
            self._close_or_hold(board, board_id, seen_external_ids, stats)

        stats.tally(board.provider, "boards_succeeded")

    def _close_or_hold(
        self,
        board: BoardRef,
        board_id: str,
        seen_external_ids: set[str],
        stats: CollectionStats,
    ) -> None:
        """Close what the board stopped returning -- unless too much stopped.

        A successful response is normally proof: the board answered and did not
        mention these postings, so they are gone. The exception is a response so
        much smaller than the last one that "the board emptied" is a less likely
        explanation than "this response is wrong".

        Called only after a successful pass. Failures return before this, so a
        provider failure can never confirm a disappearance -- that is a property
        of where this sits, and a test pins it.
        """
        previously_open = self.jobs.open_external_ids(board_id)
        missing = previously_open - seen_external_ids
        held = self.boards.get(board_id)
        was_held = held is not None and held["suspicious_since"] is not None

        if not self.policy.is_suspicious(len(previously_open), len(missing)):
            if was_held:
                # The board came back. Recovery is defined by the absence of
                # suspicion rather than by an exact return to the old count, so
                # a board back to 95 of 100 clears just as one back to 100 does.
                self.boards.clear_suspicious(board_id)
                stats.boards_recovered += 1
            stats.tally(
                board.provider, "jobs_closed", self.jobs.close_absent(board_id, seen_external_ids)
            )
            return

        observed = len(seen_external_ids)
        previous_observation = held["suspicious_observed"] if held is not None else None
        confirmed = previous_observation is not None and observed <= previous_observation

        if confirmed:
            # Twice in a row, and no better the second time. The board really
            # did shrink, so the delayed closures happen now.
            self.boards.clear_suspicious(board_id)
            stats.tally(
                board.provider, "jobs_closed", self.jobs.close_absent(board_id, seen_external_ids)
            )
            stats.boards_confirmed_drop += 1
            return

        # First anomalous pass, or a partial recovery that is still suspicious.
        # Everything observed has already been persisted and the board is marked
        # collected -- it did answer. Only the closing is withheld.
        self.boards.hold_suspicious(board_id, observed)
        stats.boards_held += 1
        stats.holds.append(
            BoardHold(
                company_slug=board.company_slug,
                provider=board.provider,
                board_identifier=board.board_identifier,
                previously_open=len(previously_open),
                observed=observed,
                would_have_closed=len(missing),
            )
        )

    def _persist(
        self,
        provider: JobProvider,
        board: BoardRef,
        board_id: str,
        company_id: str,
        stub: Any,
        posting: RawPosting,
        stats: CollectionStats,
    ) -> None:
        # Ask the adapter what its own payload paths mean, then resolve them
        # with neutral code. Nothing here knows -- or could know -- what any of
        # those paths are called. The observations are not persisted: the
        # payload is already the immutable source of truth and they are
        # re-derivable from it at any time. What is kept is coverage, so a
        # vendor quietly renaming a field surfaces as a number.
        for observation in resolve_metadata(provider.name, provider.field_map, posting.payload):
            self._paths_resolved.add((observation.provider, observation.source_field))
            key = observation.dimension.value
            self._dimension_counts[key] = self._dimension_counts.get(key, 0) + 1

        if posting.has_description:
            stats.tally(board.provider, "descriptions_non_empty")
        else:
            stats.tally(board.provider, "descriptions_empty")

        # Content-addressed: an identical description, reposted or shared across
        # companies, is one row. A CHANGED description becomes a NEW job_raw row
        # while the job keeps its identity -- history without fragmentation.
        text_hash = (
            self.raw.put(posting.description_text, posting.description_html)
            if posting.description_text
            else None
        )

        existing = self.jobs.get_by_external(board.provider, stub.external_id)
        if existing is None:
            stats.tally(board.provider, "jobs_new")
        else:
            stats.tally(board.provider, "jobs_seen_again")
            if text_hash is not None and existing["content_hash"] != text_hash:
                stats.tally(board.provider, "jobs_changed")

        # The stub the DETAIL request refreshed, when the adapter refreshed one.
        # Every adapter hands back the stub it was given, except a family whose
        # list carries no date and whose detail does (Rippling, 2026-09-11):
        # there the detail's date and locations are the better fact, and the
        # identity fields are unchanged by construction.
        detailed = posting.stub if posting.stub.external_id == stub.external_id else stub
        job_id = self.jobs.upsert_seen(
            JobRecord(
                company_id=company_id,
                source_board_id=board_id,
                provider=board.provider,
                external_id=stub.external_id,
                url=stub.url,
                title=stub.title,
                department=stub.department,
                location_raw=detailed.location_raw or stub.location_raw,
                posted_at=detailed.posted_at or stub.posted_at,
                content_hash=text_hash,
            ),
            status=CollectionStatus.NORMALISED if text_hash else CollectionStatus.FETCHED,
        )

        # Provenance: exactly what the provider returned. Distinct payloads
        # accumulate; nothing is overwritten.
        self.payloads.put(
            ProviderPayloadRecord(
                job_id=job_id, provider=board.provider, payload=dict(posting.payload)
            )
        )

    def _metadata_summary(self) -> dict[str, Any]:
        """Coverage of the provider-metadata transport for this run.

        `paths_unresolved` is the one to read. A declared path that no payload
        answered means either that no posting in this run stated it, or that the
        vendor renamed the field -- and the second is the kind of silent rot
        that would otherwise be discovered at M2, months later, as a dimension
        that mysteriously stopped appearing.
        """
        unresolved = sorted(self._paths_declared - self._paths_resolved)
        return {
            "observations": sum(self._dimension_counts.values()),
            "by_dimension": dict(sorted(self._dimension_counts.items())),
            "paths_declared": len(self._paths_declared),
            "paths_resolved": len(self._paths_resolved & self._paths_declared),
            "paths_unresolved": [f"{provider}:{path}" for provider, path in unresolved],
        }


def verify_content_hash(description_text: str) -> str:
    """Exposed so tests and diagnostics use the same hash the pipeline does."""
    return content_hash(description_text)


def new_fetcher(*args: Any, **kwargs: Any) -> HttpFetcher:
    """Convenience for the CLI; keeps FetchStats construction in one place."""
    kwargs.setdefault("stats", FetchStats())
    return HttpFetcher(*args, **kwargs)

"""Where a query-scoped walk got to, slice by slice, across runs.

THE INVARIANT THIS TABLE EXISTS FOR
-----------------------------------
A candidate's market preference may decide which slice of a query-scoped
source becomes fresh FIRST. It must never decide which slices are permanently
never collected. The first Jobgether run (2026-09-11) walked 90 of 625 slices
before its request budget ran out, in the owner's order -- anywhere, South
America, LATAM, Brazil -- and a second run with the same order would have
walked the same 90. The shared corpus would then carry one person's markets
and nobody else's, which is ADR-0019's breach in its subtlest form: not a
keyword in a query, but an order that never reaches the end.

`plan_order` is the correction. Slices are ordered by how many times they
have been walked, FEWEST FIRST, and only within the same count does the
candidate's priority order break ties. A slice that has never been walked
therefore precedes every slice that has, whoever the candidate is, and the
walk reaches every slice before it repeats any. Once all have been walked
once, the same rule holds at each round: nothing can be walked a third time
while something else waits for its second. Priority decides WHEN inside a
round, never WHETHER.

The rows are provider-neutral. A slice key is the vendor's own geography and
closed vocabulary (`brazil/full-time/senior-5-10-years`), and nothing here
knows what a candidate wants; the candidate's order arrives as an argument to
`plan_order` and is not stored. Changing a candidate from Brazil to Germany,
or from one occupation to another, changes nothing in this table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from career_agent.clock import now_utc

NOT_STARTED = "NOT_STARTED"
RUNNING = "RUNNING"
PARTIAL = "PARTIAL"
COMPLETE = "COMPLETE"
FAILED = "FAILED"
PAUSED_PROVIDER_LIMIT = "PAUSED_PROVIDER_LIMIT"

#: How a collector's own `ended` word maps onto a slice state. `capped` is the
#: vendor's ceiling, not ours: the slice is as complete as the vendor allows
#: and is paused at the provider's limit rather than partial by our choice.
STATE_FOR_ENDING: dict[str, str] = {
    "exhausted": COMPLETE,
    "caught_up": COMPLETE,
    "capped": PAUSED_PROVIDER_LIMIT,
    "budget": PARTIAL,
    "stopped": PARTIAL,
    "failed": FAILED,
}


@dataclass(frozen=True)
class SliceRow:
    provider: str
    slice_key: str
    state: str
    times_walked: int
    last_started_at: str | None
    last_finished_at: str | None
    last_ended: str | None
    last_pages: int
    last_rows: int
    last_new: int
    last_error: str | None


class SliceStateRepo:
    """One row per (provider, slice). Additive; nothing here deletes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def ensure(self, provider: str, keys: Iterable[str]) -> None:
        """Every slice the provider offers has a row, NOT_STARTED until walked."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO source_slice_state (provider, slice_key, state)"
            " VALUES (?, ?, ?)",
            [(provider, key, NOT_STARTED) for key in keys],
        )

    def start(self, provider: str, key: str) -> None:
        self.conn.execute(
            "UPDATE source_slice_state SET state = ?, last_started_at = ?"
            " WHERE provider = ? AND slice_key = ?",
            (RUNNING, now_utc(), provider, key),
        )

    def finish(
        self,
        provider: str,
        key: str,
        *,
        ended: str,
        pages: int,
        rows: int,
        new: int,
        error: str | None = None,
    ) -> None:
        """Record how a walk of one slice ended. A slice that ended on our
        budget or on a stop is PARTIAL and keeps its walk count where it was:
        a walk that did not reach the slice's end has not earned a turn."""
        state = STATE_FOR_ENDING.get(ended, PARTIAL)
        counted = 1 if state in (COMPLETE, PAUSED_PROVIDER_LIMIT, FAILED) else 0
        self.conn.execute(
            "UPDATE source_slice_state SET state = ?, times_walked = times_walked + ?,"
            " last_finished_at = ?, last_ended = ?, last_pages = ?, last_rows = ?,"
            " last_new = ?, last_error = ? WHERE provider = ? AND slice_key = ?",
            (state, counted, now_utc(), ended, pages, rows, new, error, provider, key),
        )

    def rows_for(self, provider: str) -> dict[str, SliceRow]:
        cur = self.conn.execute(
            "SELECT provider, slice_key, state, times_walked, last_started_at, last_finished_at,"
            " last_ended, last_pages, last_rows, last_new, last_error"
            " FROM source_slice_state WHERE provider = ?",
            (provider,),
        )
        return {row[1]: SliceRow(*row) for row in cur.fetchall()}

    def summary(self, provider: str) -> dict[str, int]:
        cur = self.conn.execute(
            "SELECT state, COUNT(*) FROM source_slice_state WHERE provider = ? GROUP BY state",
            (provider,),
        )
        return {state: int(n) for state, n in cur.fetchall()}


def plan_order(
    keys: Sequence[str],
    walked: dict[str, SliceRow],
    *,
    priority: Sequence[str] = (),
) -> list[str]:
    """`keys`, ordered fewest-walks-first, with `priority` breaking ties.

    `keys` is the whole neutral universe in the provider's own order and every
    key comes back exactly once: nothing is added, nothing is dropped.
    `priority` is a candidate's preferred order over the same keys (or a
    prefix of them) and decides only among slices with the same walk count. A
    key in `priority` that the provider does not offer is ignored.

    So: run 1 walks the candidate's markets first, because everything is at
    zero walks. Run 2 walks what run 1 did not reach, whatever the candidate
    prefers, because those are still at zero. Nothing is walked a second time
    until everything has been walked once. That is the whole guarantee.
    """
    rank = {key: index for index, key in enumerate(priority)}
    neutral = {key: index for index, key in enumerate(keys)}

    def sort_key(key: str) -> tuple[int, int, int]:
        row = walked.get(key)
        walks = row.times_walked if row else 0
        return (walks, rank.get(key, len(rank)), neutral[key])

    return sorted(dict.fromkeys(keys), key=sort_key)

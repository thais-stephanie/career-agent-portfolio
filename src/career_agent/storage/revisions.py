"""Which scored population a reader should be shown, and whether it is current.

THE FAILURE THIS EXISTS FOR
---------------------------
The owner edited her seniority preferences in the interface on 2026-09-08. The
edit was correct, it was hers, and it did exactly what she asked. Her
configuration went from revision 4 to revision 6, and her Jobs list went empty
-- because all 19,469 stored scores answer revision 4, and the product asked
for revision 6 and found nothing.

Nothing was lost and nothing was broken. The product was being precise: a score
is only true relative to the preferences that produced it, and no score had yet
been computed for the new ones. But "0 jobs" is not what a person reads on that
screen. They read "this product has stopped working", and the remedy was a
seven-minute terminal command.

THE DISTINCTION THAT FIXES IT
-----------------------------
A configuration is a QUESTION. A scored population is an ANSWER to some
question. They are usually the same revision; for the minutes after an edit
they are not, and there is a third honest option between "show the new answer"
(which does not exist yet) and "show nothing":

    show the previous answer, and say plainly that it is the previous one.

WHY AN OLDER REVISION IS SAFE TO SERVE
--------------------------------------
Because a rescore only ever writes the CURRENT configuration version. Every
older version is therefore immutable: it was complete when it was current, and
nothing can be appending to it now. The only population that can be half-built
is the current one.

That single fact is what makes the rule below exact rather than a heuristic:

    Serve the current revision, unless an older one covers strictly more of
    the corpus.

During a rescore the current revision holds a thousand rows and the previous
one holds nineteen thousand, so the previous one is served and labelled. When
the rescore finishes, the current one covers everything and takes over -- in
one step, on the next read, with no partial population ever displayed. That is
the atomic activation, and it needs no lock, no flag and no second table.

After a collection adds thirty new postings, both revisions are short by the
same thirty, neither covers strictly more, and the current one keeps serving.
A tie goes to the current revision because it answers the question actually
being asked.

WHAT IT WILL NOT DO
-------------------
It will not mix two revisions into one population. The reader sees exactly one
answer to exactly one question, and is told which.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Revision:
    """One scored population, and how much of the corpus it covers."""

    config_id: str
    config_version: int
    scored: int
    scoreable: int

    @property
    def complete(self) -> bool:
        """Every posting that COULD be scored has been.

        `scoreable` excludes postings with no stored text -- 16 of the owner's
        19,485 -- because a scorer with nothing to read is not a gap in the
        population.
        """
        return self.scoreable > 0 and self.scored >= self.scoreable

    @property
    def coverage(self) -> float:
        return self.scored / self.scoreable if self.scoreable else 0.0


@dataclass(frozen=True)
class Serving:
    """What to show, what was asked for, and the difference between them."""

    #: The configuration in force. The question being asked.
    current: Revision
    #: The population to read from, or None when nothing has ever been scored.
    serving: Revision | None
    #: Older populations that exist, newest first. What rollback could reach.
    previous: tuple[Revision, ...]

    @property
    def is_current(self) -> bool:
        """Whether the answer on screen answers the question being asked."""
        return self.serving is not None and (
            self.serving.config_version == self.current.config_version
            and self.serving.config_id == self.current.config_id
        )

    @property
    def is_stale(self) -> bool:
        """Showing a real answer to a question that is no longer being asked."""
        return self.serving is not None and not self.is_current

    @property
    def is_building(self) -> bool:
        """The current revision is being scored and is not finished.

        True during a rescore and true after an interrupted one, which are the
        same situation from a reader's point of view: work is outstanding.
        """
        return not self.current.complete and self.current.scored > 0

    @property
    def has_nothing(self) -> bool:
        """No population anywhere. A fresh database, and honestly empty."""
        return self.serving is None

    def as_dict(self) -> dict[str, object]:
        """For `/api/health` and the Jobs response, so a screen can say it."""

        def one(revision: Revision | None) -> dict[str, object] | None:
            if revision is None:
                return None
            return {
                "config_id": revision.config_id,
                "config_version": revision.config_version,
                "scored": revision.scored,
                "scoreable": revision.scoreable,
                "complete": revision.complete,
            }

        return {
            "current": one(self.current),
            "serving": one(self.serving),
            "previous": [one(r) for r in self.previous],
            "is_current": self.is_current,
            "is_stale": self.is_stale,
            "is_building": self.is_building,
            "has_nothing": self.has_nothing,
        }


def scoreable_count(conn: sqlite3.Connection) -> int:
    """Postings a scorer could actually read: open, and with stored text.

    The denominator every completeness question here uses. Counting open
    postings alone would make a complete population look permanently short by
    the sixteen the corpus holds with no description.
    """
    row = conn.execute(
        # The enforced foreign key already guarantees that a non-null hash
        # references stored text. Avoid probing the large raw-text index for
        # every job on every Discover request while collection is writing it.
        "SELECT COUNT(*) FROM job WHERE closed_at IS NULL AND content_hash IS NOT NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def revisions(conn: sqlite3.Connection, config_id: str) -> tuple[Revision, ...]:
    """Every scored population for this configuration, newest first."""
    return _revisions(conn, config_id, scoreable_count(conn))


def _revisions(conn: sqlite3.Connection, config_id: str, total: int) -> tuple[Revision, ...]:
    rows = conn.execute(
        "SELECT m.config_version, COUNT(*) AS n FROM job_match m"
        " JOIN job j ON j.id = m.job_id"
        " WHERE m.config_id = ? AND j.closed_at IS NULL AND j.content_hash IS NOT NULL"
        " GROUP BY m.config_version ORDER BY m.config_version DESC",
        (config_id,),
    ).fetchall()
    return tuple(
        Revision(
            config_id=config_id,
            config_version=int(row[0]),
            scored=int(row[1]),
            scoreable=total,
        )
        for row in rows
    )


#: The last decision, keyed by everything that could change it. See `resolve`.
_LAST: dict[tuple[object, ...], Serving] = {}


def _decision_key(
    conn: sqlite3.Connection, config_id: str, config_version: int
) -> tuple[object, ...]:
    """Durable mutation identity, not a count that can hide membership swaps.

    Triggers advance the token on job and score writes in the same transaction.
    `resolve` reads token and population in one snapshot; a cached decision can
    never be stored under a token from a different committed population.
    """
    from career_agent.storage.catalogue import population_token

    paths = [str(row[2]) for row in conn.execute("PRAGMA database_list") if row[2]]
    revision = population_token(conn)
    # Requests open different connections to the same file: retaining the
    # connection in that key would disable cache reuse on every HTTP request.
    # File identity distinguishes a replaced database at the same path; the
    # durable token still identifies mutations within that database. A split
    # profile has two files (its own and the shared catalogue): both count.
    identity: object = conn
    if paths:
        identity = tuple(
            (path, stat.st_dev, stat.st_ino, stat.st_ctime_ns)
            for path, stat in ((p, Path(p).stat()) for p in paths)
        )
    return (identity, config_id, config_version, revision)


def resolve(conn: sqlite3.Connection, config_id: str, config_version: int) -> Serving:
    from career_agent.storage.invalidation import read_snapshot

    with read_snapshot(conn):
        return _resolve(conn, config_id, config_version)


def _resolve(conn: sqlite3.Connection, config_id: str, config_version: int) -> Serving:
    """Decide which population answers for this reader, right now.

    Serve the current revision, unless an older one covers strictly more of
    the corpus. See the module docstring for why that is exact rather than a
    guess: a rescore only ever writes the current version, so every older one
    is immutable and was complete when it was current.

    REMEMBERED BETWEEN READS. `_revisions` walks every open posting and
    probes its scores -- 245,871 probes, 3.1 s on the 2026-09-12 copy -- and
    the interface asked it on every list request, before a single posting
    was read. The answer cannot change unless a score was written or a
    posting opened or closed, and both are visible in `_decision_key` for a
    tenth of a second; so the walk runs only when the key moved.
    """
    # The durable population token advances on every job/score mutation.
    # Counting the corpus before testing it repeated a full covering-index
    # scan even on cache hits. Read token and, on a miss, counts in the same
    # snapshot; equal-count membership swaps still invalidate the decision.
    key = _decision_key(conn, config_id, config_version)
    remembered = _LAST.get(key)
    if remembered is not None:
        return remembered
    total = scoreable_count(conn)
    found = {r.config_version: r for r in _revisions(conn, config_id, total)}
    current = found.get(
        config_version,
        Revision(config_id=config_id, config_version=config_version, scored=0, scoreable=total),
    )
    older = tuple(
        r
        for r in sorted(found.values(), key=lambda r: -r.config_version)
        if r.config_version != config_version
    )

    # **THE NEWEST COMPLETE ONE, NOT THE BIGGEST ONE.** Row count is only a
    # proxy for "not half-built", and it picks the wrong revision the moment
    # the corpus has shrunk: measured on the real corpus, revision 2 holds
    # 21,293 rows from an era when more postings were open, and revision 4 --
    # her most recent complete answer -- holds 19,469. Serving 2 would have
    # answered a question she stopped asking two revisions ago, with postings
    # that have since closed.
    #
    # Completeness first, then recency. Falling back to row count at all only
    # matters when nothing older is complete, which is a corpus that has never
    # finished a rescore.
    best_older = max(older, key=lambda r: (r.complete, r.config_version, r.scored), default=None)
    if best_older is not None and best_older.scored > current.scored:
        # A real answer to the previous question beats a half-built answer to
        # this one. The tie goes the other way on purpose: equal coverage
        # means the current revision is as good and answers what was asked.
        serving: Revision | None = best_older
    elif current.scored > 0:
        serving = current
    else:
        serving = best_older

    decision = Serving(current=current, serving=serving, previous=older)
    _LAST.clear()
    _LAST[key] = decision
    return decision

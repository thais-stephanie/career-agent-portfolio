"""Whether a source is actually working, answered from the database.

The catalogue says what a source IS and `sources.resolve` already refuses to
let it overstate itself. This module answers the different question a person
asks on a Tuesday morning: did it run, did it work, and when.

**No new status vocabulary.** `SourceStatus` and `Coverage` already exist and
`resolve` already verifies them against the corpus, so a second enum here would
be a second thing to keep in agreement with the first. What this adds is
OBSERVED FACTS -- last attempt, last success, what failed, how much quota is
left -- and those are not statuses, they are readings with dates on them.

The distinction that matters most is between three things a single "it did not
work" would blur:

  never attempted    no run has ever named this source
  attempted, empty   it answered and had nothing new, which is normal for a
                     feed and suspicious for a board
  attempted, failed  it raised, and `last_error` says what
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from career_agent.sources.catalogue import Source, resolve
from career_agent.sources.matrix import QUOTA_DISABLED

#: Sources that FIND boards and store no posting of their own (RemoteSource
#: names an employer's ATS board; `discover-remotesource` registers it and
#: the family's own collector reads it). "Never collected" would be a lie in
#: either direction for one of these.
DISCOVERY_ONLY = frozenset({"remotesource"})

#: How far back from a source's latest run the "last refresh" reaches. One
#: refresh of a board family is many runs (one per board), and judging it by
#: its last board would report the whole family by its luck on one board.
REFRESH_WINDOW_HOURS = 6

#: A failure that is not the connector's: the employer took its board down.
#: Counted in the note, never a partial failure (2026-09 rule, kept).
_GONE = frozenset({"NOT_FOUND"})


@dataclass(frozen=True, slots=True)
class LastRefresh:
    """What the most recent refresh of one source did, summed over its runs."""

    runs: int = 0
    running: bool = False
    failed_runs: int = 0
    new: int = 0
    seen_again: int = 0
    failures: int = 0
    gone: int = 0


@dataclass(frozen=True, slots=True)
class SourceHealth:
    """One source, as the catalogue declares it and the database observed it."""

    source: Source
    #: When a collection last STARTED for this source, successful or not.
    last_attempt: str | None = None
    #: When one last finished without raising.
    last_success: str | None = None
    #: When one last finished having stored something new.
    last_new_posting: str | None = None
    #: The most recent error text any board of this provider recorded, and how
    #: many boards are carrying one.
    #:
    #: Both, because either alone misleads. Greenhouse reaches 96 boards and 3
    #: of them record `NOT_FOUND`; reporting the text alone made a source with
    #: 11,763 postings read as broken, which is how a health report teaches its
    #: reader to ignore it.
    last_error: str | None = None
    boards_with_errors: int = 0
    #: Boards configured, and boards that have produced at least one posting.
    boards: int = 0
    boards_producing: int = 0
    #: Postings in the corpus attributed to this provider.
    postings: int = 0
    #: Requests left, for a source with a metered key. `None` where the
    #: question does not apply, which is every source but one.
    quota_remaining: int | None = None
    #: The most recent refresh, summed over its runs (`LastRefresh`).
    last_refresh: LastRefresh = LastRefresh()

    @property
    def attempted(self) -> bool:
        return self.last_attempt is not None

    @property
    def note(self) -> str:
        """One sentence, chosen by what was actually observed.

        Ordered by what a reader most needs to know first. A source that has
        never been asked is not failing and must not read as though it were --
        that is the difference between "this is broken" and "you have not run
        it", and a person deciding what to do this morning needs it.
        """
        if self.source.coverage.value in {"BLOCKED", "UNSUPPORTED"}:
            if self.source.experimental_provider:
                return (
                    "Off by default: the source's own terms say no. An experimental "
                    "connector can be switched on for your own profile."
                )
            return "Not collected: the source's own terms or robots.txt say no."
        if self.source.id in QUOTA_DISABLED:
            return QUOTA_DISABLED[self.source.id]
        if self.source.collection_blocker:
            return self.source.collection_blocker
        if self.source.coverage.value == "UNDOCUMENTED":
            return "Reachable, and the vendor publishes no terms for it. Not collected."
        if self.source.coverage.value == "NOTHING_PUBLISHED":
            # A sentence of its own, because the two neighbours above are about
            # PERMISSION and this one is not. Without it a permitted source with
            # no openings fell through to "Never collected. Nothing has failed;
            # nothing has run", which reads as a job somebody forgot to do.
            return "They allow us, and they publish no open jobs. There is nothing to collect."
        if self.source.id in DISCOVERY_ONLY:
            return (
                "Used to find employers' own job boards, which are then collected "
                "directly. It stores no postings of its own."
            )
        if not self.source.provider:
            if self.source.coverage.value == "PLANNED":
                return "Planned: no connector is built for this source yet."
            return "No connector is built for this source."
        if self.quota_remaining == 0:
            return "Out of quota. No further requests will be made."
        if not self.attempted and self.postings == 0:
            return "Never collected. Nothing has failed; nothing has run."
        if self.postings == 0 and self.last_error:
            return f"It ran and stored nothing. The error recorded was: {self.last_error}"
        if self.postings == 0:
            return "It answered, and nothing from it is in the corpus."

        headline = f"{self.postings} postings"
        if self.last_new_posting:
            headline += f", newest {self.last_new_posting[:10]}"
        if self.boards_with_errors:
            # Named as a FRACTION, never as a state. Three boards out of 96
            # recording NOT_FOUND is a company that took its board down, not a
            # connector that stopped working.
            headline += (
                f". {self.boards_with_errors} of {self.boards} boards last recorded an error"
            )
        return headline + "."

    @property
    def state(self) -> str:
        """One of five words a person who is not an engineer can act on.

        `coverage` and the note below it are precise and are written for
        somebody debugging a connector. A person deciding what to do this
        morning needs a different question answered -- is anything wrong, and
        is it wrong in a way I can fix -- and answering it with `PARTIAL`,
        `UNDOCUMENTED` and `UNSUPPORTED` is answering a question nobody asked.

        The five are ordered by what most needs saying, and the order matters:
        a source that is out of quota is reported as needing a key rather than
        as healthy, even though it has postings and has never failed.

        NEEDS_SETUP, WAITING and NOT_RUN are three different answers and the
        difference is what a person should DO. NEEDS_SETUP is hers to fix.
        WAITING needs a permission somebody else has to give, and telling her
        to act on it would waste an evening. NOT_RUN is neither: the connector
        exists and nothing has asked it yet, which is not a fault and must not
        read as one.
        """
        coverage = self.source.coverage.value
        if coverage in {"BLOCKED", "UNSUPPORTED"}:
            return "EXPERIMENTAL" if self.source.experimental_provider else "DISABLED"
        if self.source.id in QUOTA_DISABLED:
            return "QUOTA_EXHAUSTED"
        if self.source.collection_blocker:
            return "BLOCKED_PROVIDER"
        if coverage == "NOTHING_PUBLISHED":
            return "NOTHING_PUBLISHED"
        # NOT_RUN only ever means "a connector exists and has not run"
        # (2026-10-16): a row with no connector said "Not run yet" for seven
        # sources nobody could run.
        if self.source.id in DISCOVERY_ONLY:
            return "DISCOVERY_ONLY"
        if not self.source.provider:
            return "PLANNED" if coverage == "PLANNED" else "NOT_IMPLEMENTED"
        if coverage == "UNDOCUMENTED":
            return "WAITING"
        if self.quota_remaining == 0:
            return "NEEDS_SETUP"
        last = self.last_refresh
        if last.running:
            return "RUNNING"
        if last.runs:
            if last.failed_runs >= last.runs:
                return "FAILED"
            if last.failed_runs or last.failures:
                return "PARTIAL_FAILURE"
            if last.new:
                return "SUCCESS_NEW"
            return "ALL_DUPLICATES" if last.seen_again else "SUCCESS_ZERO_NEW"
        # Postings in the shared corpus were collected by SOME profile's run: this
        # profile's own ledger (the only one `attempted` reads) being empty does
        # not make the source "never run" (sources/matrix.py holds the same rule).
        if not self.attempted and self.postings == 0:
            return "NOT_RUN"
        if self.postings == 0:
            return "ATTENTION"
        # A fraction of boards erroring is a company that took its board down,
        # never a broken connector. It is reported in the note and does not
        # change the state unless EVERY board is failing.
        if self.boards and self.boards_with_errors >= self.boards:
            return "ATTENTION"
        return "HEALTHY"

    def as_dict(self) -> dict[str, object]:
        """What the interface shows. The source's own row, plus the observation.

        `note`, `last_error` and the board counts ride along so a detail view
        can be opened without a second request -- but the interface keeps them
        behind a disclosure, because a person opening a job search should not
        meet a connector diagnosis first.
        """
        return {
            **self.source.as_dict(),
            "state": self.state,
            "note": self.note,
            "postings": self.postings,
            "boards": self.boards,
            "boards_producing": self.boards_producing,
            "boards_with_errors": self.boards_with_errors,
            "last_attempt": self.last_attempt,
            "last_success": self.last_success,
            "last_new_posting": self.last_new_posting,
            # The error TEXT is a third party's words about our request. It is
            # shown, and it is never interpreted.
            "last_error": self.last_error,
            "quota_remaining": self.quota_remaining,
            "collection_blocker": self.source.collection_blocker,
            "last_refresh": {
                "runs": self.last_refresh.runs,
                "new": self.last_refresh.new,
                "seen_again": self.last_refresh.seen_again,
                "failures": self.last_refresh.failures,
                "boards_gone": self.last_refresh.gone,
            },
        }


#: Which pipeline stages are a COLLECTION. A rescore is not one: it reads the
#: corpus and writes scores, so counting it as a source attempt would report
#: every source as freshly collected every time preferences changed.
_COLLECTION_STAGES = ("collect", "retrieval", "discover")


def _runs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    marks = " OR ".join("stage LIKE ?" for _ in _COLLECTION_STAGES)
    params = tuple(f"%{stage}%" for stage in _COLLECTION_STAGES)
    return list(
        conn.execute(
            f"SELECT stage, started_at, finished_at, status, stats_json, error"
            f" FROM pipeline_run WHERE {marks} ORDER BY started_at",
            params,
        )
    )


def _last_refresh(runs: list[sqlite3.Row], now: str | None = None) -> dict[str, LastRefresh]:
    """Per provider, the runs of its most recent refresh, summed.

    A run names its provider in its stage (`collect-gupy`), or, for the
    shared board collector (`collect`), in each `by_board` entry and each
    failure. A run with no `finished_at` that started within the window is
    RUNNING; an older one is a crashed run and counts as failed."""
    from datetime import UTC, datetime, timedelta

    entries: dict[str, list[tuple[str, dict[str, int]]]] = {}

    def add(provider: str, started: str, fact: dict[str, int]) -> None:
        entries.setdefault(provider, []).append((started, fact))

    current = datetime.now(UTC)
    if now is not None:
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    window = timedelta(hours=REFRESH_WINDOW_HOURS)
    for run in runs:
        started = str(run["started_at"])
        stats = json.loads(run["stats_json"] or "{}")
        try:
            begun = datetime.fromisoformat(started.replace("Z", "+00:00"))
        except ValueError:
            continue
        open_run = run["finished_at"] is None
        running = int(open_run and current - begun < window)
        failed = int(str(run["status"]) == "FAILED" or (open_run and not running))
        listed = stats.get("failures")
        failures = [f for f in listed if isinstance(f, dict)] if isinstance(listed, list) else []
        provider = _stage_provider(str(run["stage"]))
        if provider is not None:
            gone = sum(1 for f in failures if f.get("category") in _GONE)
            add(provider, started, {
                "running": running, "failed": int(failed and not (gone and gone == len(failures))),
                "new": int(stats.get("jobs_new") or 0),
                "seen": int(stats.get("jobs_seen_again") or 0),
                "failures": len(failures) - gone, "gone": gone, "feed": 1,
            })  # fmt: skip
            continue
        boards = [b for b in stats.get("by_board") or [] if isinstance(b, dict)]
        for name in {str(b.get("provider")) for b in boards if b.get("provider")}:
            mine = [b for b in boards if b.get("provider") == name]
            own = [f for f in failures if f.get("provider") == name]
            gone = sum(1 for f in own if f.get("category") in _GONE)
            add(name, started, {
                "running": running,
                # A run whose only failures are boards the employer took
                # down did not fail; it found them gone.
                "failed": int(failed and len(boards) == len(mine) and len(own) > gone),
                "new": sum(int(b.get("jobs_new") or 0) for b in mine),
                "seen": sum(int(b.get("jobs_seen_again") or 0) for b in mine),
                "failures": len(own) - gone, "gone": gone, "feed": 0,
            })  # fmt: skip
    out: dict[str, LastRefresh] = {}
    for provider, items in entries.items():
        latest = max(datetime.fromisoformat(s.replace("Z", "+00:00")) for s, _ in items)
        recent = [
            f for s, f in items
            if latest - datetime.fromisoformat(s.replace("Z", "+00:00")) <= window
        ]  # fmt: skip
        if all(f["feed"] for f in recent):
            # A feed is one run per refresh: an earlier attempt the same day
            # that a later one replaced is history, not a partial failure.
            recent = [max(items, key=lambda item: item[0])[1]]
        out[provider] = LastRefresh(
            runs=len(recent),
            running=any(f["running"] for f in recent),
            failed_runs=sum(f["failed"] for f in recent),
            new=sum(f["new"] for f in recent),
            seen_again=sum(f["seen"] for f in recent),
            failures=sum(f["failures"] for f in recent),
            gone=sum(f["gone"] for f in recent),
        )
    return out


def _board_facts(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, COUNT(*) AS boards, MAX(last_collected_at) AS last_collected,"
        " MAX(last_error) AS last_error,"
        " SUM(CASE WHEN last_error IS NOT NULL THEN 1 ELSE 0 END) AS erroring"
        " FROM source_board GROUP BY provider"
    ).fetchall()
    return {
        str(row["provider"]): {
            "boards": int(row["boards"]),
            "last_collected": row["last_collected"],
            "last_error": row["last_error"],
            "erroring": int(row["erroring"] or 0),
        }
        for row in rows
    }


def _posting_facts(conn: sqlite3.Connection) -> dict[str, dict[str, object]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT provider, COUNT(*) AS n, MAX(first_seen_at) AS newest FROM job GROUP BY provider"
    ).fetchall()
    return {str(row["provider"]): {"n": int(row["n"]), "newest": row["newest"]} for row in rows}


def _stage_provider(stage: str) -> str | None:
    """Which provider a run's stage name refers to, if it names one.

    Stages are written as `collect:wwr`, `collect-speedrun` and so on. A stage
    that names nothing returns None rather than being attributed to whichever
    provider sorts first, because a run credited to the wrong source is worse
    than a run credited to none.
    """
    for separator in (":", "-", "_"):
        if separator in stage:
            tail = stage.rsplit(separator, 1)[1].strip()
            if tail and tail not in {"collect", "retrieval", "discover"}:
                return tail
    return None


def health(
    conn: sqlite3.Connection,
    *,
    quota_remaining: dict[str, int] | None = None,
    catalogue_path: Path | None = None,
) -> list[SourceHealth]:
    """Every catalogued source, with what the database observed about it.

    `catalogue_path` follows `--config-dir` like everything else. The server
    passes its own, because a person who ran `serve --config-dir somewhere`
    and got the repository's catalogue would be reading a different product's
    answer to "which sources do I have".
    """
    boards = _board_facts(conn)
    postings = _posting_facts(conn)
    quotas = quota_remaining or {}

    attempts: dict[str, str] = {}
    successes: dict[str, str] = {}
    errors: dict[str, str] = {}
    runs = _runs(conn)
    refreshes = _last_refresh(runs)
    for run in runs:
        provider = _stage_provider(str(run["stage"]))
        if provider is None:
            continue
        attempts[provider] = str(run["started_at"])
        if str(run["status"]) == "OK" and not json.loads(run["stats_json"] or "{}").get(
            "deferred_reason"
        ):
            successes[provider] = str(run["finished_at"] or run["started_at"])
        elif run["error"]:
            errors[provider] = str(run["error"])[:160]

    resolved = resolve(conn, catalogue_path)
    out: list[SourceHealth] = []
    for source in resolved:
        provider = source.provider or ""
        board = boards.get(provider, {})
        posting = postings.get(provider, {})
        out.append(
            SourceHealth(
                source=source,
                last_attempt=attempts.get(provider) or (board.get("last_collected") or None),  # type: ignore[arg-type]
                last_success=successes.get(provider) or (board.get("last_collected") or None),  # type: ignore[arg-type]
                last_new_posting=posting.get("newest") or None,  # type: ignore[arg-type]
                last_error=errors.get(provider) or (board.get("last_error") or None),  # type: ignore[arg-type]
                boards=source.boards or 0,
                boards_producing=source.boards_with_postings or 0,
                postings=int(str(posting.get("n", 0) or 0)),
                boards_with_errors=int(str(board.get("erroring", 0) or 0)),
                quota_remaining=quotas.get(provider),
                last_refresh=refreshes.get(provider, LastRefresh()) if provider else LastRefresh(),
            )
        )
    return out


def summarise(entries: list[SourceHealth]) -> dict[str, int]:
    """How many sources are in each state, for a one-line answer.

    Deliberately counts the RESOLVED coverage rather than the declared one, so
    a source that names an adapter nobody has run is not summarised as working.
    """
    counts: dict[str, int] = {}
    for entry in entries:
        key = entry.source.coverage.value
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def read_quota(path: str) -> dict[str, int]:
    """Requests left for a metered source, read from its local ledger.

    Returns an empty mapping when the ledger is absent, which is the normal
    state: a ledger exists only once a key has been configured, and this must
    never be the thing that makes a health report fail.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {}
    remaining = data.get("known_remaining_upper_bound")
    return {"jooble": int(remaining)} if isinstance(remaining, int) else {}

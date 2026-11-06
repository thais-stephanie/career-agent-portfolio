"""Workable presence and shadow decisions (stages 0 and 1). CLOSES NOTHING.

Why it exists: the update skips unchanged postings, so nothing says which
postings each feed listed (docs/research/workable-lifecycle/). After a complete
read this records that, applies the proposed closing rules and stores what they
WOULD decide. The only tables written are the four of migration 0061: never
`job`, never a profile table, so no trigger fires and no score is invalidated.

Every switch is off unless its variable is exactly `on`:

    CAREER_AGENT_WORKABLE_PRESENCE  record presence and miss counters
    CAREER_AGENT_WORKABLE_SHADOW    also record hypothetical decisions
    CAREER_AGENT_WORKABLE_AUDIT     also check a seeded sample on Workable's site

They start no read: `observe` runs only at the end of a read the update was
doing anyway.

The first read evaluated is the BASELINE. The churn guard would hold every read
otherwise: a quarter of the open rows left long ago, and all of them would look
like first misses forever. They are counted and marked `baseline`.

Two populations, kept apart from the first event to the last:

    BACKLOG     already missing from the baseline read: long gone, or never seen
    NEW_MISSES  listed after the baseline, then gone: a departure actually watched

Each event's `reason` names its population, so the origin survives whatever the
posting does later. Every decision is hypothetical: WOULD_REOPEN is a posting that
came back after a WOULD_CLOSE, and its `closed_at` never changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
from collections.abc import Callable, Iterable
from datetime import UTC
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from career_agent.clock import new_id, now_utc
from career_agent.net.deadline import BudgetExpired
from career_agent.net.fetcher import USER_AGENT
from career_agent.providers.workable_feed import FeedInterrupted
from career_agent.storage.db import transaction

MISSES_TO_CLOSE = 2
MIN_SECONDS_FROM_FIRST_MISS = 24 * 3600
MIN_SIZE_RATIO = 0.97
MAX_NEW_MISS_RATIO = 0.15
AUDIT_SAMPLE = 50
#: Half the sample for each population. What one cannot use, the other may.
AUDIT_PER_STRATUM = 25
AUDIT_SPACING_SECONDS = 2.0
#: Two requests at most per posting, so a run of 50 never exceeds this.
AUDIT_MAX_REQUESTS = 100
AUDIT_MAX_ATTEMPTS = 3
SITE = "https://apply.workable.com"

GONE, PUBLISHED, UNKNOWN = "GONE", "PUBLISHED", "UNKNOWN"
BACKLOG, NEW = "backlog", "new"
#: The population each event reason belongs to, as the owner reads it.
POPULATION_OF_REASON = {
    "BASELINE_ABSENT": "BACKLOG",
    "FIRST_MISS": "NEW_MISSES",
    "BACKLOG": "WOULD_CLOSE_BACKLOG",
    "MISSED_REPEATEDLY": "WOULD_CLOSE_NEW",
    "REAPPEARED_FROM_BASELINE": "REAPPEARED_FROM_BACKLOG",
    "REAPPEARED": "REAPPEARED_FROM_NEW_MISSES",
    "EMPLOYER_STILL_LISTS": "EMPLOYER_STILL_LISTS",
}
POLICY = {
    "misses": MISSES_TO_CLOSE,
    "min_seconds": MIN_SECONDS_FROM_FIRST_MISS,
    "min_size_ratio": MIN_SIZE_RATIO,
    "max_new_miss_ratio": MAX_NEW_MISS_RATIO,
    "audit_sample": AUDIT_SAMPLE,
    "audit_per_stratum": AUDIT_PER_STRATUM,
}
_COUNTING = ("BASELINE", "COUNTED")
_CODE = re.compile(r"/j/([A-Za-z0-9]+)")
_ACCOUNT = re.compile(r"^/([^/]+)/j/[^/]+/?$")


def _on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() == "on"


def presence_enabled() -> bool:
    return _on("CAREER_AGENT_WORKABLE_PRESENCE")


def shadow_enabled() -> bool:
    return presence_enabled() and _on("CAREER_AGENT_WORKABLE_SHADOW")


def audit_enabled() -> bool:
    return shadow_enabled() and _on("CAREER_AGENT_WORKABLE_AUDIT")


def _version_time(version: str | None) -> str | None:
    """The feed's Last-Modified as UTC text, or None when it cannot be read."""
    try:
        moment = parsedate_to_datetime(version or "")
    except (ValueError, TypeError):
        return None
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _outcome(
    previous: Any, version_at: str, feed_unique: int, open_rows: int, new_misses: int
) -> str:
    if feed_unique == 0:
        return "HELD_FEED_EMPTY"
    if previous is None:
        return "BASELINE"
    if version_at <= previous["version_at"]:
        return "HELD_OLDER_VERSION"
    if feed_unique < MIN_SIZE_RATIO * previous["feed_unique"]:
        return "HELD_FEED_SHRANK"
    if new_misses > MAX_NEW_MISS_RATIO * open_rows:
        return "HELD_TOO_MANY_NEW_MISSES"
    return "COUNTED"


def observe(conn: Any, version: str | None, external_ids: Iterable[str]) -> dict[str, Any]:
    """Record one complete, distinct feed version: all of it, or none of it."""
    version_at = _version_time(version)
    if version_at is None:
        return {"outcome": "IGNORED_NO_VERSION"}
    shadow, audit = shadow_enabled(), audit_enabled()
    now = now_utc()
    with transaction(conn):
        if conn.execute(
            "SELECT 1 FROM workable_feed_read WHERE version = ?", (version,)
        ).fetchone():
            return {"outcome": "IGNORED_ALREADY_RECORDED"}
        previous = conn.execute(
            "SELECT version_at, feed_unique FROM workable_feed_read"
            " WHERE outcome IN ('BASELINE', 'COUNTED') ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        conn.execute("CREATE TEMP TABLE _wk_feed (external_id TEXT PRIMARY KEY)")
        conn.executemany("INSERT OR IGNORE INTO _wk_feed VALUES (?)", ((i,) for i in external_ids))
        conn.execute(
            "CREATE TEMP TABLE _wk_state AS"
            " SELECT j.id AS job_id, f.external_id IS NOT NULL AS present,"
            "        COALESCE(p.misses, 0) AS misses, p.last_decision AS last_decision,"
            "        COALESCE(p.baseline, 0) AS baseline"
            " FROM job j"
            " LEFT JOIN _wk_feed f ON f.external_id = j.external_id"
            " LEFT JOIN workable_presence p ON p.job_id = j.id"
            " WHERE j.provider = 'workable' AND j.closed_at IS NULL"
            "   AND j.external_id IS NOT NULL"
        )
        counts = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(present), 0),"
            "       COALESCE(SUM(NOT present AND misses = 0 AND NOT baseline), 0),"
            "       COALESCE(SUM(present AND misses > 0), 0),"
            "       COALESCE(SUM(NOT present AND baseline), 0),"
            "       COALESCE(SUM(present AND misses > 0 AND baseline), 0) FROM _wk_state"
        ).fetchone()
        open_rows, present_rows, new_misses, reappeared, absent_backlog, returned_backlog = (
            int(counts[i]) for i in range(6)
        )
        feed_unique = int(conn.execute("SELECT COUNT(*) FROM _wk_feed").fetchone()[0])
        outcome = _outcome(previous, version_at, feed_unique, open_rows, new_misses)
        absent = open_rows - present_rows
        baseline_absent = 0
        if outcome == "BASELINE":
            # Nothing on this read is a new departure: no earlier read to depart from.
            baseline_absent, absent_backlog, new_misses = absent, absent, 0
        if outcome == "HELD_OLDER_VERSION":
            returned_backlog = reappeared = 0
        populations: dict[str, Any] = {
            "baseline_absent": baseline_absent,
            "absent_backlog": absent_backlog,
            "absent_new": absent - absent_backlog,
            "reappeared_backlog": returned_backlog,
            "reappeared_new": reappeared - returned_backlog,
            "would_close_backlog": 0,
            "would_close_new": 0,
        }
        read_id = new_id()
        conn.execute(
            "INSERT INTO workable_feed_read (id, seq, version, version_at, observed_at,"
            " outcome, feed_unique, open_rows, present_rows, absent_rows, new_misses,"
            " reappeared, would_close) VALUES (?,"
            " (SELECT COALESCE(MAX(seq), 0) + 1 FROM workable_feed_read),"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                read_id,
                version,
                version_at,
                now,
                outcome,
                feed_unique,
                open_rows,
                present_rows,
                absent,
                new_misses,
                reappeared,
            ),
        )
        would_close = 0
        # An older snapshot says nothing about now. Any other read, even a held
        # one, really did list these postings, and a reset is the safe direction.
        if outcome != "HELD_OLDER_VERSION":
            if shadow:
                conn.execute(
                    "INSERT INTO workable_shadow_event"
                    " (read_id, job_id, decision, reason, misses, created_at)"
                    " SELECT ?, job_id,"
                    "  CASE WHEN last_decision = 'WOULD_CLOSE' THEN 'WOULD_REOPEN'"
                    "       ELSE 'WOULD_KEEP' END,"
                    "  CASE WHEN baseline THEN 'REAPPEARED_FROM_BASELINE' ELSE 'REAPPEARED' END,"
                    "  misses, ?"
                    " FROM _wk_state WHERE present AND misses > 0",
                    (read_id, now),
                )
            conn.execute(
                "INSERT INTO workable_presence (job_id, last_present_read)"
                " SELECT job_id, ? FROM _wk_state WHERE present"
                " ON CONFLICT (job_id) DO UPDATE SET"
                "   last_present_read = excluded.last_present_read, misses = 0,"
                "   first_miss_at = NULL, first_miss_read = NULL, baseline = 0,"
                "   last_decision = NULL, decided_read = NULL",
                (read_id,),
            )
        if outcome in _COUNTING:
            conn.execute(
                "INSERT INTO workable_presence"
                " (job_id, misses, first_miss_at, first_miss_read, baseline)"
                " SELECT job_id, 1, ?, ?, ? FROM _wk_state WHERE NOT present"
                " ON CONFLICT (job_id) DO UPDATE SET"
                # Once backlog, backlog until the feed lists it again: an employer's
                # answer restarts the count, not the origin.
                "   baseline = CASE WHEN excluded.baseline = 1 THEN 1 ELSE baseline END,"
                "   misses = misses + 1,"
                "   first_miss_at = COALESCE(first_miss_at, excluded.first_miss_at),"
                "   first_miss_read = COALESCE(first_miss_read, excluded.first_miss_read)",
                (version_at, read_id, int(outcome == "BASELINE")),
            )
            if shadow:
                populations["would_close_backlog"], populations["would_close_new"] = _decide(
                    conn, read_id, version_at, now
                )
                would_close = populations["would_close_backlog"] + populations["would_close_new"]
                if audit:
                    populations["sample"] = _sample(conn, read_id, version)
        conn.execute(
            "UPDATE workable_feed_read SET would_close = ?, policy_json = ? WHERE id = ?",
            (would_close, json.dumps({**POLICY, "populations": populations}), read_id),
        )
        conn.execute("DROP TABLE temp._wk_state")
        conn.execute("DROP TABLE temp._wk_feed")
    return {
        "outcome": outcome,
        "version": version,
        "feed_unique": feed_unique,
        "open_rows": open_rows,
        "absent_rows": absent,
        "new_misses": new_misses,
        "populations": populations,
        "reappeared": reappeared,
        "would_close": would_close,
    }


def _decide(conn: Any, read_id: str, version_at: str, now: str) -> tuple[int, int]:
    """Write the hypothetical decisions that changed on this read.

    Returns how many would close from the backlog and from new absences."""
    conn.execute(
        "INSERT INTO workable_shadow_event"
        " (read_id, job_id, decision, reason, misses, created_at)"
        " SELECT ?, s.job_id, 'WOULD_HOLD',"
        "  CASE WHEN p.baseline THEN 'BASELINE_ABSENT' ELSE 'FIRST_MISS' END, 1, ?"
        " FROM _wk_state s JOIN workable_presence p ON p.job_id = s.job_id"
        " WHERE NOT s.present AND s.misses = 0",
        (read_id, now),
    )
    conn.execute(
        "UPDATE workable_presence SET last_decision = 'WOULD_HOLD', decided_read = ?"
        " WHERE job_id IN (SELECT job_id FROM _wk_state WHERE NOT present AND misses = 0)",
        (read_id,),
    )
    due = (
        " FROM workable_presence p JOIN _wk_state s ON s.job_id = p.job_id"
        " WHERE NOT s.present AND p.misses >= ?"
        "   AND CAST(strftime('%s', ?) AS INTEGER)"
        "     - CAST(strftime('%s', p.first_miss_at) AS INTEGER) >= ?"
        "   AND p.last_decision IS NOT 'WOULD_CLOSE'"
    )
    rule = (MISSES_TO_CLOSE, version_at, MIN_SECONDS_FROM_FIRST_MISS)
    conn.execute(
        "INSERT INTO workable_shadow_event"
        " (read_id, job_id, decision, reason, misses, created_at)"
        " SELECT ?, p.job_id, 'WOULD_CLOSE',"
        "  CASE WHEN p.baseline THEN 'BACKLOG' ELSE 'MISSED_REPEATEDLY' END, p.misses, ?" + due,
        (read_id, now, *rule),
    )
    conn.execute(
        "UPDATE workable_presence SET last_decision = 'WOULD_CLOSE', decided_read = ?"
        " WHERE job_id IN (SELECT p.job_id" + due + ")",
        (read_id, *rule),
    )
    found = dict(
        tuple(r)
        for r in conn.execute(
            "SELECT reason, COUNT(*) FROM workable_shadow_event"
            " WHERE read_id = ? AND decision = 'WOULD_CLOSE' GROUP BY reason",
            (read_id,),
        )
    )
    return found.get("BACKLOG", 0), found.get("MISSED_REPEATEDLY", 0)


def allocate(backlog: int, new: int) -> tuple[int, int]:
    """How many to sample from each population: half each, the spare to the other."""
    from_backlog, from_new = min(backlog, AUDIT_PER_STRATUM), min(new, AUDIT_PER_STRATUM)
    spare = AUDIT_SAMPLE - from_backlog - from_new
    extra = min(spare, backlog - from_backlog)
    return from_backlog + extra, from_new + min(spare - extra, new - from_new)


def _sample(conn: Any, read_id: str, version: str | None) -> dict[str, Any]:
    """A sample seeded by the version, per population, of postings not sampled before.

    Fixed per population, so never a proportional picture of all absences."""
    due: dict[str, list[str]] = {BACKLOG: [], NEW: []}
    for job_id, baseline in conn.execute(
        "SELECT p.job_id, p.baseline FROM workable_presence p"
        " JOIN _wk_state s ON s.job_id = p.job_id"
        " WHERE NOT s.present AND p.last_decision = 'WOULD_CLOSE'"
        "   AND p.job_id NOT IN (SELECT job_id FROM workable_audit_sample)"
        " ORDER BY p.job_id"
    ):
        due[BACKLOG if baseline else NEW].append(job_id)
    counts = dict(zip((BACKLOG, NEW), allocate(len(due[BACKLOG]), len(due[NEW])), strict=True))
    for stratum, pool in due.items():
        for job_id in random.Random(f"{version}:{stratum}").sample(pool, counts[stratum]):
            conn.execute(
                "INSERT INTO workable_audit_sample (read_id, job_id, url)"
                " SELECT ?, id, url FROM job WHERE id = ?",
                (read_id, job_id),
            )
    return {
        "eligible": {stratum: len(pool) for stratum, pool in due.items()},
        "selected": counts,
    }


# -- the audit sample: asks the network, records answers, closes nothing -------


def ask_workable(
    client: httpx.Client, url: str, wait: Callable[[], None]
) -> tuple[str, str, int, bool]:
    """One posting: (answer, detail, requests, blocked). Only a measured answer counts."""
    match = _CODE.search(urlsplit(url).path)
    if not match:
        return UNKNOWN, "NO_POSTING_CODE", 0, False
    code = match.group(1)
    requests = 0

    def get(address: str) -> httpx.Response:
        nonlocal requests
        wait()
        requests += 1
        return client.get(address)

    try:
        first = get(f"{SITE}/j/{code}")
        if first.status_code in (403, 429):
            return UNKNOWN, f"HTTP_{first.status_code}", requests, True
        if first.status_code not in (301, 302):
            return UNKNOWN, f"HTTP_{first.status_code}", requests, False
        target = urlsplit(first.headers.get("location", "")).path
        if target.rstrip("/") == "/oops":
            return GONE, "SHORT_LINK_OOPS", requests, False
        account = _ACCOUNT.match(target)
        if not account:
            return UNKNOWN, "UNEXPECTED_REDIRECT", requests, False
        second = get(f"{SITE}/api/v2/accounts/{account.group(1)}/jobs/{code}")
    except httpx.HTTPError as exc:
        return UNKNOWN, type(exc).__name__, requests, False
    if second.status_code in (403, 429):
        return UNKNOWN, f"HTTP_{second.status_code}", requests, True
    if second.status_code == 404:
        return GONE, "ACCOUNT_API_404", requests, False
    if second.status_code != 200:
        return UNKNOWN, f"HTTP_{second.status_code}", requests, False
    try:
        state = second.json().get("state")
    except (ValueError, AttributeError):
        return UNKNOWN, "BAD_JSON", requests, False
    if state == "published":
        return PUBLISHED, "STATE_PUBLISHED", requests, False
    if isinstance(state, str) and state:
        return GONE, "STATE_OTHER", requests, False
    return UNKNOWN, "NO_STATE", requests, False


def run_audit(
    conn: Any,
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
    check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Ask about pending sample rows, within limits. Stopping leaves the rest pending."""
    # Hashed, not by id: ids sort by age, and a stop would then have asked only the
    # oldest postings, most of them from the backlog.
    pending = sorted(
        conn.execute(
            "SELECT read_id, job_id, url FROM workable_audit_sample"
            " WHERE answer IS NULL OR (answer = 'UNKNOWN' AND attempts < ?)",
            (AUDIT_MAX_ATTEMPTS,),
        ).fetchall(),
        key=lambda r: (r["read_id"], hashlib.sha256(r["job_id"].encode()).hexdigest()),
    )[:AUDIT_SAMPLE]
    summary: dict[str, Any] = {"asked": 0, "requests": 0, "stopped": None}
    if not pending:
        return summary
    own = client is None
    client = client or httpx.Client(
        timeout=20.0, follow_redirects=False, headers={"User-Agent": USER_AGENT}
    )

    def wait() -> None:
        if check is not None:
            check()
        sleep(AUDIT_SPACING_SECONDS)

    try:
        for row in pending:
            try:
                answer, detail, spent, blocked = ask_workable(client, row["url"], wait)
            except (BudgetExpired, FeedInterrupted) as exc:
                summary["stopped"] = str(exc) or type(exc).__name__
                break
            summary["asked"] += 1
            summary["requests"] += spent
            _record_answer(conn, row["read_id"], row["job_id"], answer, detail, spent)
            if blocked:
                summary["stopped"] = detail
                break
    finally:
        if own:
            client.close()
    _note_run(conn, summary)
    return summary


def _note_run(conn: Any, summary: dict[str, Any]) -> None:
    """Keep how the run ended beside the newest read: the catalogue's own record of it."""
    with transaction(conn):
        row = conn.execute(
            "SELECT id, policy_json FROM workable_feed_read ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return
        details = json.loads(row["policy_json"])
        details.setdefault("audit_runs", []).append({"at": now_utc(), **summary})
        conn.execute(
            "UPDATE workable_feed_read SET policy_json = ? WHERE id = ?",
            (json.dumps(details), row["id"]),
        )


def _record_answer(
    conn: Any, read_id: str, job_id: str, answer: str, detail: str, spent: int
) -> None:
    now = now_utc()
    with transaction(conn):
        conn.execute(
            "UPDATE workable_audit_sample SET answer = ?, detail = ?, attempts = attempts + 1,"
            " requests = requests + ?, asked_at = ? WHERE read_id = ? AND job_id = ?",
            (answer, detail, spent, now, read_id, job_id),
        )
        if answer != PUBLISHED:
            return
        # Still listed by the employer: the feed is not its board. Start again.
        row = conn.execute(
            "SELECT misses FROM workable_presence WHERE job_id = ?", (job_id,)
        ).fetchone()
        conn.execute(
            "INSERT INTO workable_shadow_event"
            " (read_id, job_id, decision, reason, misses, created_at)"
            " VALUES (?, ?, 'WOULD_KEEP', 'EMPLOYER_STILL_LISTS', ?, ?)",
            (read_id, job_id, row[0] if row else 0, now),
        )
        conn.execute(
            "UPDATE workable_presence SET misses = 0, first_miss_at = NULL,"
            " first_miss_read = NULL, last_decision = 'WOULD_KEEP',"
            " decided_read = ? WHERE job_id = ?",
            (read_id, job_id),
        )


# -- what the owner reads ------------------------------------------------------


def _tally() -> dict[str, Any]:
    return {
        "selected": 0,
        "consulted": 0,
        "not_asked": 0,
        "published": 0,
        "gone": 0,
        "unknown": 0,
        "requests": 0,
        "errors": {},
    }


def _count_answer(tally: dict[str, Any], row: Any) -> None:
    tally["selected"] += 1
    tally["consulted"] += int(row["attempts"] > 0)
    tally["not_asked"] += int(row["attempts"] == 0)
    tally["requests"] += row["requests"]
    if row["answer"]:
        tally[row["answer"].lower()] += 1
    if row["answer"] == UNKNOWN:
        tally["errors"][row["detail"]] = tally["errors"].get(row["detail"], 0) + 1


def _audit_by_read(conn: Any) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Answers per read and in all, by the population each posting was sampled from."""
    origins: dict[str, list[tuple[int, str]]] = {}
    for row in conn.execute(
        "SELECT e.job_id, r.seq, e.reason FROM workable_shadow_event e"
        " JOIN workable_feed_read r ON r.id = e.read_id"
        " WHERE e.decision = 'WOULD_CLOSE'"
        "   AND e.job_id IN (SELECT job_id FROM workable_audit_sample) ORDER BY r.seq"
    ):
        origins.setdefault(row["job_id"], []).append((row["seq"], row["reason"]))
    by_read: dict[str, dict[str, Any]] = {}
    overall = {BACKLOG: _tally(), NEW: _tally()}
    for row in conn.execute(
        "SELECT a.read_id, r.seq, a.job_id, a.answer, a.detail, a.attempts, a.requests"
        " FROM workable_audit_sample a JOIN workable_feed_read r ON r.id = a.read_id"
    ):
        reason = next(
            (why for seq, why in reversed(origins.get(row["job_id"], [])) if seq <= row["seq"]),
            None,
        )
        stratum = BACKLOG if reason == "BACKLOG" else NEW
        read = by_read.setdefault(row["read_id"], {BACKLOG: _tally(), NEW: _tally()})
        _count_answer(read[stratum], row)
        _count_answer(overall[stratum], row)
    return by_read, overall


def shadow_report(conn: Any) -> dict[str, Any]:
    """Everything stage 1 must show, by population. Reads only. Needs `sqlite3.Row` rows."""

    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql)]

    by_read, overall = _audit_by_read(conn)
    reads = []
    for row in rows(
        "SELECT id, seq, version, outcome, feed_unique, open_rows, absent_rows, new_misses,"
        " reappeared, would_close, policy_json FROM workable_feed_read ORDER BY seq"
    ):
        details = json.loads(row["policy_json"])
        kept = details.get("populations", {})
        audit = by_read.get(row["id"], {BACKLOG: _tally(), NEW: _tally()})
        sample = kept.get("sample", {})
        for stratum in (BACKLOG, NEW):
            audit[stratum]["eligible"] = sample.get("eligible", {}).get(stratum, 0)
        open_answers = [
            audit[s]["published"] + audit[s]["unknown"] + audit[s]["not_asked"]
            for s in (BACKLOG, NEW)
        ]
        reads.append(
            {
                "seq": row["seq"],
                "version": row["version"],
                "outcome": row["outcome"],
                "feed_unique": row["feed_unique"],
                "open_rows": row["open_rows"],
                "baseline_absent": kept.get("baseline_absent", 0),
                "new_misses": row["new_misses"],
                "absent_backlog": kept.get("absent_backlog", 0),
                "absent_new": kept.get("absent_new", 0),
                "would_close_backlog": kept.get("would_close_backlog", 0),
                "would_close_new": kept.get("would_close_new", 0),
                "reappeared_backlog": kept.get("reappeared_backlog", 0),
                "reappeared_new": kept.get("reappeared_new", 0),
                "sampled_backlog": audit[BACKLOG]["selected"],
                "sampled_new": audit[NEW]["selected"],
                "combined_absent": row["absent_rows"],
                "combined_would_close": row["would_close"],
                "combined_reappeared": row["reappeared"],
                # Any answer other than "gone" would close nothing on that read.
                "breaker_backlog": bool(open_answers[0]),
                "breaker_new": bool(open_answers[1]),
                "audit": audit,
                "audit_runs": details.get("audit_runs", []),
                "read_id": row["id"],
            }
        )
    # The backlog stays out of the rate: it would dilute it with postings long gone.
    first_misses = conn.execute(
        "SELECT COUNT(*) FROM workable_shadow_event WHERE reason = 'FIRST_MISS'"
    ).fetchone()[0]
    returned = conn.execute(
        "SELECT COUNT(*) FROM workable_shadow_event WHERE reason = 'REAPPEARED' AND misses = 1"
    ).fetchone()[0]
    return {
        "scope": (
            "Every decision here is hypothetical. None closed or reopened a posting:"
            " WOULD_REOPEN is a posting back after a WOULD_CLOSE, with closed_at never changed."
        ),
        "real_closed_workable_postings": conn.execute(
            "SELECT COUNT(*) FROM job WHERE provider = 'workable' AND closed_at IS NOT NULL"
        ).fetchone()[0],
        "reads": reads,
        "reads_counted": sum(r["outcome"] in _COUNTING for r in reads),
        "decisions": [
            {**row, "population": POPULATION_OF_REASON.get(row["reason"], "UNCLASSIFIED")}
            for row in rows(
                "SELECT decision, reason, COUNT(*) AS postings FROM workable_shadow_event"
                " GROUP BY decision, reason ORDER BY decision, reason"
            )
        ],
        "absent_now": [
            {**row, "population": "BACKLOG" if row["baseline"] else "NEW_MISSES"}
            for row in rows(
                "SELECT misses, baseline, COUNT(*) AS postings FROM workable_presence"
                " WHERE misses > 0 GROUP BY misses, baseline ORDER BY misses, baseline"
            )
        ],
        "new_misses_first": first_misses,
        "new_misses_returned_after_one_miss": returned,
        "new_misses_flap_rate": returned / first_misses if first_misses else None,
        "audit": {
            "note": (
                "A fixed number per population, not a proportional sample of all absences:"
                " read each population by itself and do not extrapolate across them."
            ),
            BACKLOG: overall[BACKLOG],
            NEW: overall[NEW],
        },
    }

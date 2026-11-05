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
like first misses forever. They are counted and marked `baseline`, so a report
can tell the backlog from departures actually watched.
"""

from __future__ import annotations

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
AUDIT_SPACING_SECONDS = 2.0
#: Two requests at most per posting, so a run of 50 never exceeds this.
AUDIT_MAX_REQUESTS = 100
AUDIT_MAX_ATTEMPTS = 3
SITE = "https://apply.workable.com"

GONE, PUBLISHED, UNKNOWN = "GONE", "PUBLISHED", "UNKNOWN"
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
            "       COALESCE(SUM(NOT present AND misses = 0), 0),"
            "       COALESCE(SUM(present AND misses > 0), 0) FROM _wk_state"
        ).fetchone()
        open_rows, present_rows, new_misses, reappeared = (int(counts[i]) for i in range(4))
        feed_unique = int(conn.execute("SELECT COUNT(*) FROM _wk_feed").fetchone()[0])
        outcome = _outcome(previous, version_at, feed_unique, open_rows, new_misses)
        read_id = new_id()
        conn.execute(
            "INSERT INTO workable_feed_read (id, seq, version, version_at, observed_at,"
            " outcome, feed_unique, open_rows, present_rows, absent_rows, new_misses,"
            " reappeared, would_close, policy_json) VALUES (?,"
            " (SELECT COALESCE(MAX(seq), 0) + 1 FROM workable_feed_read),"
            " ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
            (
                read_id,
                version,
                version_at,
                now,
                outcome,
                feed_unique,
                open_rows,
                present_rows,
                open_rows - present_rows,
                new_misses,
                reappeared,
                json.dumps(
                    {
                        "misses": MISSES_TO_CLOSE,
                        "min_seconds": MIN_SECONDS_FROM_FIRST_MISS,
                        "min_size_ratio": MIN_SIZE_RATIO,
                        "max_new_miss_ratio": MAX_NEW_MISS_RATIO,
                    }
                ),
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
                    "  CASE WHEN baseline AND last_decision IS NOT 'WOULD_CLOSE'"
                    "       THEN 'REAPPEARED_FROM_BASELINE' ELSE 'REAPPEARED' END, misses, ?"
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
            baseline = int(outcome == "BASELINE")
            conn.execute(
                "INSERT INTO workable_presence"
                " (job_id, misses, first_miss_at, first_miss_read, baseline)"
                " SELECT job_id, 1, ?, ?, ? FROM _wk_state WHERE NOT present"
                " ON CONFLICT (job_id) DO UPDATE SET"
                "   baseline = CASE WHEN misses = 0 THEN excluded.baseline ELSE baseline END,"
                "   misses = misses + 1,"
                "   first_miss_at = COALESCE(first_miss_at, excluded.first_miss_at),"
                "   first_miss_read = COALESCE(first_miss_read, excluded.first_miss_read)",
                (version_at, read_id, baseline),
            )
            if shadow:
                would_close = _decide(conn, read_id, version_at, baseline, now)
                conn.execute(
                    "UPDATE workable_feed_read SET would_close = ? WHERE id = ?",
                    (would_close, read_id),
                )
                if audit:
                    _sample(conn, read_id, version)
        conn.execute("DROP TABLE temp._wk_state")
        conn.execute("DROP TABLE temp._wk_feed")
    return {
        "outcome": outcome,
        "version": version,
        "feed_unique": feed_unique,
        "open_rows": open_rows,
        "absent_rows": open_rows - present_rows,
        "new_misses": new_misses,
        "reappeared": reappeared,
        "would_close": would_close,
    }


def _decide(conn: Any, read_id: str, version_at: str, baseline: int, now: str) -> int:
    """Write the hypothetical decisions that changed on this read."""
    reason = "BASELINE_ABSENT" if baseline else "FIRST_MISS"
    conn.execute(
        "INSERT INTO workable_shadow_event"
        " (read_id, job_id, decision, reason, misses, created_at)"
        " SELECT ?, job_id, 'WOULD_HOLD', ?, 1, ? FROM _wk_state"
        " WHERE NOT present AND misses = 0",
        (read_id, reason, now),
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
    would_close = conn.execute(
        "INSERT INTO workable_shadow_event"
        " (read_id, job_id, decision, reason, misses, created_at)"
        " SELECT ?, p.job_id, 'WOULD_CLOSE',"
        "  CASE WHEN p.baseline THEN 'BACKLOG' ELSE 'MISSED_REPEATEDLY' END, p.misses, ?" + due,
        (read_id, now, *rule),
    ).rowcount
    conn.execute(
        "UPDATE workable_presence SET last_decision = 'WOULD_CLOSE', decided_read = ?"
        " WHERE job_id IN (SELECT p.job_id" + due + ")",
        (read_id, *rule),
    )
    return would_close


def _sample(conn: Any, read_id: str, version: str | None) -> None:
    """A seeded sample of postings not sampled before, so reads add up."""
    due = [
        r[0]
        for r in conn.execute(
            "SELECT p.job_id FROM workable_presence p JOIN _wk_state s ON s.job_id = p.job_id"
            " WHERE NOT s.present AND p.last_decision = 'WOULD_CLOSE'"
            "   AND p.job_id NOT IN (SELECT job_id FROM workable_audit_sample)"
            " ORDER BY p.job_id"
        )
    ]
    for job_id in random.Random(version).sample(due, min(AUDIT_SAMPLE, len(due))):
        conn.execute(
            "INSERT INTO workable_audit_sample (read_id, job_id, url)"
            " SELECT ?, id, url FROM job WHERE id = ?",
            (read_id, job_id),
        )


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
    pending = conn.execute(
        "SELECT read_id, job_id, url FROM workable_audit_sample"
        " WHERE answer IS NULL OR (answer = 'UNKNOWN' AND attempts < ?)"
        " ORDER BY read_id, job_id LIMIT ?",
        (AUDIT_MAX_ATTEMPTS, AUDIT_SAMPLE),
    ).fetchall()
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
    return summary


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
            " first_miss_read = NULL, baseline = 0, last_decision = 'WOULD_KEEP',"
            " decided_read = ? WHERE job_id = ?",
            (read_id, job_id),
        )


# -- what the owner reads ------------------------------------------------------


def shadow_report(conn: Any) -> dict[str, Any]:
    """Everything stage 1 must show. Reads only. Needs `sqlite3.Row` rows."""

    def rows(sql: str) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(sql)]

    reads = rows(
        "SELECT r.id, r.version, r.outcome, r.feed_unique, r.open_rows, r.absent_rows,"
        " r.new_misses, r.reappeared, r.would_close,"
        " COUNT(a.job_id) AS sampled,"
        " COALESCE(SUM(a.answer = 'GONE'), 0) AS sampled_gone,"
        " COALESCE(SUM(a.answer = 'PUBLISHED'), 0) AS sampled_published,"
        " COALESCE(SUM(a.answer = 'UNKNOWN' OR a.answer IS NULL), 0) AS sampled_open"
        " FROM workable_feed_read r LEFT JOIN workable_audit_sample a ON a.read_id = r.id"
        " GROUP BY r.id ORDER BY r.seq"
    )
    for read in reads:
        # Any answer other than "gone" would close nothing on that read.
        read["breaker"] = bool(read["sampled_published"] or read["sampled_open"])
    # The backlog stays out of the rate: it would dilute it with postings long gone.
    first_misses = conn.execute(
        "SELECT COUNT(*) FROM workable_shadow_event WHERE reason = 'FIRST_MISS'"
    ).fetchone()[0]
    returned = conn.execute(
        "SELECT COUNT(*) FROM workable_shadow_event WHERE reason = 'REAPPEARED' AND misses = 1"
    ).fetchone()[0]
    return {
        "reads": reads,
        "reads_counted": sum(r["outcome"] in _COUNTING for r in reads),
        "decisions": rows(
            "SELECT decision, reason, COUNT(*) AS postings FROM workable_shadow_event"
            " GROUP BY decision, reason ORDER BY decision, reason"
        ),
        "absent_now": rows(
            "SELECT misses, baseline, COUNT(*) AS postings FROM workable_presence"
            " WHERE misses > 0 GROUP BY misses, baseline ORDER BY misses, baseline"
        ),
        "first_misses": first_misses,
        "returned_after_one_miss": returned,
        "flap_rate": returned / first_misses if first_misses else None,
        "audit": rows(
            "SELECT COALESCE(answer, 'NOT_ASKED') AS answer, COUNT(*) AS postings,"
            " SUM(requests) AS requests FROM workable_audit_sample GROUP BY 1"
        ),
    }

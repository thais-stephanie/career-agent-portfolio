"""Is the posting's own page still up? Asked politely, answered carefully.

A posting can stay in the catalogue after its advert was taken down (a Lever
page answering 404 "this job is no longer available" while the list still
offered it). This module asks the posting's own page, or its ATS's official
posting API, and files one of four answers, independent of matching and age:

* AVAILABLE    the page answered and still shows this posting;
* REMOVED      a 404 or 410, or the source's own unmistakable "closed" page;
* INCONCLUSIVE anything else: 403, 429, 5xx, a timeout, a captcha, a generic
               page, a redirect to a careers home. Never read as removed;
* EXCLUDED     LinkedIn, never asked (also when a redirect leads there).

No row is NOT CHECKED. AVAILABLE is what the page said at `checked_at`, never
a promise; each answer is asked again after its own interval (`RECHECK`).

SAFETY. Only http and https, to public addresses: a host resolving to a
loopback, private, link-local (cloud metadata), reserved or multicast address
is refused, before the first request and after every redirect, which are
followed by hand (at most `MAX_REDIRECTS`). No cookies, no credentials, no
script is run; at most `MAX_BYTES` of a page are read. One request per domain
at a time, `DOMAIN_DELAY` seconds apart; a 429 or 503 with Retry-After stops
asking that domain for the rest of the run.

Nothing here changes a score, deletes a posting or touches a saved one.
"""

from __future__ import annotations

import ipaddress
import re
import socket
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

#: At most this many postings per run: the saved ones, then the best matches.
RUN_LIMIT = 200
MAX_REDIRECTS = 5
MAX_BYTES = 400_000
TIMEOUT_SECONDS = 15.0
DOMAIN_DELAY = 2.0
WORKERS = 4
#: A whole run stops asking after this long; what was asked is kept.
RUN_BUDGET_SECONDS = 600.0
USER_AGENT = (
    "CareerAgent/1.0 (local job-search assistant; checks whether a posting is still online)"
)


class Availability(StrEnum):
    AVAILABLE = "AVAILABLE"
    REMOVED = "REMOVED"
    INCONCLUSIVE = "INCONCLUSIVE"
    EXCLUDED = "EXCLUDED"


#: How long each answer stands before the posting is asked again.
RECHECK: dict[str, timedelta] = {
    Availability.AVAILABLE: timedelta(days=3),
    Availability.REMOVED: timedelta(days=14),
    Availability.INCONCLUSIVE: timedelta(days=1),
    Availability.EXCLUDED: timedelta(days=30),
}

#: Hosts never asked: LinkedIn's terms forbid automated access.
EXCLUDED_HOSTS = ("linkedin.com",)

#: The source's own words for a closed posting, on a page that answers 200.
#: Each is a whole statement about THIS posting, never a word that a live
#: advert might also contain ("expired" alone is in many benefit policies).
CLOSED_MARKERS = re.compile(
    r"\b(?:this|the)\s+(?:job|position|posting|role|opening|vacancy)\s+"
    r"(?:(?:posting|listing|ad)\s+)?(?:is\s+no\s+longer\s+(?:available|open|accepting)|"
    r"has\s+(?:been\s+)?(?:closed|filled|expired|removed))"
    r"|\bno\s+longer\s+accepting\s+applications\b"
    r"|\bjob\s+not\s+found\b|\bposting\s+not\s+found\b"
    r"|\besta\s+vaga\s+(?:n[aã]o\s+est[aá]\s+mais\s+dispon[ií]vel|foi\s+encerrada)\b"
    r"|\bvaga\s+encerrada\b",
    re.IGNORECASE,
)

#: A captcha or bot wall: the page says nothing about the posting.
BOT_WALL = re.compile(
    r"captcha|are\s+you\s+a\s+robot|verify\s+you\s+are\s+human|cf-chl", re.IGNORECASE
)


@dataclass(frozen=True)
class Check:
    job_id: str
    state: Availability
    checked_at: str
    url: str | None
    final_url: str | None = None
    http_status: int | None = None
    evidence: str | None = None
    method: str | None = None


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def is_excluded(url: str | None) -> bool:
    host = _host(url or "")
    return any(host == h or host.endswith("." + h) for h in EXCLUDED_HOSTS)


Resolver = Callable[[str], Sequence[str]]


def system_resolver(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def public_destination(url: str, resolve: Resolver) -> str | None:
    """Why `url` may not be asked, or None when it may.

    # ponytail: checks the addresses before each request; httpx resolves again
    # when it connects, so a DNS answer that changes in between (rebinding) is
    # not caught. Pin the resolved address in the transport if that matters.
    """
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return "not an http(s) address"
    if parts.username or parts.password:
        return "credentials in the address"
    host = parts.hostname
    if not host:
        return "no host"
    try:
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            addresses = [ipaddress.ip_address(a.split("%")[0]) for a in resolve(host)]
        except OSError:
            return "the name did not resolve"
    if not addresses:
        return "the name did not resolve"
    for address in addresses:
        if not address.is_global or address.is_multicast:
            return "a local, private or reserved address"
    return None


def api_url(url: str) -> str | None:
    """The ATS's official posting endpoint for a posting page, when there is one."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    segments = [s for s in parts.path.split("/") if s]
    if host in {"job-boards.greenhouse.io", "boards.greenhouse.io"} and "jobs" in segments:
        at = segments.index("jobs")
        if at >= 1 and len(segments) > at + 1 and segments[at + 1].isdigit():
            board, job = segments[at - 1], segments[at + 1]
            return f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job}"
    if host == "jobs.lever.co" and len(segments) >= 2:
        return f"https://api.lever.co/v0/postings/{segments[0]}/{segments[1]}"
    return None


class _Domains:
    """One request per domain at a time, spaced; domains that asked us to wait."""

    def __init__(self, delay: float, sleep: Callable[[float], None], clock: Callable[[], float]):
        self.delay = delay
        self.sleep = sleep
        self.clock = clock
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self.paused: set[str] = set()
        self._guard = threading.Lock()

    def lock(self, host: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host: str) -> None:
        previous = self._last.get(host)
        if previous is not None:
            remaining = self.delay - (self.clock() - previous)
            if remaining > 0:
                self.sleep(remaining)
        self._last[host] = self.clock()


def _title_shown(text: str, title: str | None) -> bool:
    if not title:
        return False
    words = [w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 2]
    if not words:
        return False
    page = text.lower()
    return sum(w in page for w in words) >= max(1, (len(words) * 2 + 2) // 3)


def check_one(
    job_id: str,
    url: str | None,
    title: str | None,
    *,
    client: httpx.Client,
    resolve: Resolver,
    domains: _Domains,
) -> Check:
    """Ask one posting's page (or its ATS API) and classify the answer."""
    stamp = _now()
    if not url:
        return Check(job_id, Availability.INCONCLUSIVE, stamp, None, evidence="no address stored")
    if is_excluded(url):
        return Check(
            job_id, Availability.EXCLUDED, stamp, url, evidence="LinkedIn is never checked"
        )
    target = api_url(url)
    method = "api" if target else "page"
    current = target or url
    for _hop in range(MAX_REDIRECTS + 1):
        refused = public_destination(current, resolve)
        if refused:
            return Check(
                job_id,
                Availability.INCONCLUSIVE,
                stamp,
                url,
                current,
                evidence=refused,
                method=method,
            )
        if is_excluded(current):
            return Check(
                job_id,
                Availability.EXCLUDED,
                stamp,
                url,
                current,
                evidence="redirected to LinkedIn, which is never checked",
                method=method,
            )
        host = _host(current)
        if host in domains.paused:
            return Check(
                job_id,
                Availability.INCONCLUSIVE,
                stamp,
                url,
                current,
                evidence="the site asked to wait",
                method=method,
            )
        with domains.lock(host):
            domains.wait(host)
            try:
                with client.stream("GET", current) as response:
                    body = b""
                    for chunk in response.iter_bytes():
                        body += chunk
                        if len(body) >= MAX_BYTES:
                            break
                    status = response.status_code
                    location = response.headers.get("location")
                    retry_after = response.headers.get("retry-after")
                    encoding = response.encoding or "utf-8"
            except httpx.TimeoutException:
                return Check(
                    job_id,
                    Availability.INCONCLUSIVE,
                    stamp,
                    url,
                    current,
                    evidence="timed out",
                    method=method,
                )
            except httpx.HTTPError as exc:
                return Check(
                    job_id,
                    Availability.INCONCLUSIVE,
                    stamp,
                    url,
                    current,
                    evidence=f"could not connect ({type(exc).__name__})",
                    method=method,
                )
        if status in {301, 302, 303, 307, 308} and location:
            current = urljoin(current, location)
            continue
        text = body.decode(encoding, errors="replace")
        return _classify(
            job_id, stamp, url, current, status, text, title, method, retry_after, domains
        )
    return Check(
        job_id,
        Availability.INCONCLUSIVE,
        stamp,
        url,
        current,
        evidence="too many redirects",
        method=method,
    )


def _classify(
    job_id: str,
    stamp: str,
    url: str,
    final: str,
    status: int,
    text: str,
    title: str | None,
    method: str,
    retry_after: str | None,
    domains: _Domains,
) -> Check:
    def made(state: Availability, evidence: str) -> Check:
        return Check(job_id, state, stamp, url, final, status, evidence[:300], method)

    if status in {404, 410}:
        return made(Availability.REMOVED, f"HTTP {status}")
    if status in {429, 503} and retry_after:
        domains.paused.add(_host(final))
    if status != 200:
        return made(Availability.INCONCLUSIVE, f"HTTP {status}")
    if BOT_WALL.search(text[:20000]):
        return made(Availability.INCONCLUSIVE, "a bot check answered instead of the page")
    closed = CLOSED_MARKERS.search(text)
    if closed:
        return made(Availability.REMOVED, closed.group(0))
    if method == "api":
        # The ATS answered for this very posting id.
        return made(Availability.AVAILABLE, "the job board's API still lists this posting")
    if _title_shown(text, title):
        return made(Availability.AVAILABLE, "the page still shows this posting's title")
    # A 200 alone, or a redirect to a careers home, confirms nothing.
    return made(Availability.INCONCLUSIVE, "the page answered but does not show this posting")


# ---------------------------------------------------------------------------
# storage and selection
# ---------------------------------------------------------------------------


def store(conn: Any, checks: Sequence[Check]) -> None:
    conn.executemany(
        "INSERT INTO job_availability (job_id, state, checked_at, url, final_url, http_status,"
        " evidence, method) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT(job_id) DO UPDATE SET state = excluded.state,"
        " checked_at = excluded.checked_at, url = excluded.url, final_url = excluded.final_url,"
        " http_status = excluded.http_status, evidence = excluded.evidence,"
        " method = excluded.method",
        [
            (
                c.job_id,
                c.state.value,
                c.checked_at,
                c.url,
                c.final_url,
                c.http_status,
                c.evidence,
                c.method,
            )
            for c in checks
        ],
    )


def due(state: str | None, checked_at: str | None, now: datetime) -> bool:
    if state is None or checked_at is None:
        return True
    try:
        when = datetime.fromisoformat(checked_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    return now - when >= RECHECK.get(state, timedelta(days=1))


def counts(conn: Any, job_ids: Sequence[str]) -> dict[str, int]:
    """How many of these postings are in each state; never checked is its own."""
    tally: Counter[str] = Counter()
    for start in range(0, len(job_ids), 500):
        page = list(job_ids[start : start + 500])
        marks = ",".join("?" for _ in page)
        found = dict(
            conn.execute(
                f"SELECT job_id, state FROM job_availability WHERE job_id IN ({marks})", page
            )
        )
        for job_id in page:
            tally[found.get(job_id, "NOT_CHECKED")] += 1
    return dict(tally)


def run(
    conn: Any,
    candidates: Sequence[tuple[str, str | None, str | None]],
    *,
    client: httpx.Client | None = None,
    resolve: Resolver = system_resolver,
    stop: Callable[[], str | None] = lambda: None,
    progress: Callable[[int, int], None] = lambda done, total: None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    budget: float = RUN_BUDGET_SECONDS,
) -> dict[str, Any]:
    """Check `candidates` (job id, url, title), store each answer as it comes.

    Stops between requests when `stop` returns a reason (a collection or a
    recalculation started) or the budget is spent. Every answer received is
    stored when the run ends, on the caller's connection and thread.
    """
    from concurrent.futures import ThreadPoolExecutor

    owned = client is None
    client = client or httpx.Client(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        headers={"User-Agent": USER_AGENT},
        cookies=None,
        trust_env=False,
    )
    domains = _Domains(DOMAIN_DELAY, sleep, clock)
    started = clock()
    done: list[Check] = []
    stopped: str | None = None
    lock = threading.Lock()

    def one(item: tuple[str, str | None, str | None]) -> Check | None:
        nonlocal stopped
        reason = stop() or ("time budget" if clock() - started > budget else None)
        if reason:
            stopped = stopped or reason
            return None
        result = check_one(
            item[0], item[1], item[2], client=client, resolve=resolve, domains=domains
        )
        with lock:
            done.append(result)
            progress(len(done), len(candidates))
        return result

    try:
        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            results = [r for r in pool.map(one, candidates) if r is not None]
    finally:
        if owned:
            client.close()
    from career_agent.storage.db import transaction

    with transaction(conn):
        store(conn, results)
    tally = Counter(r.state.value for r in results)
    return {
        "checked": len(results),
        "planned": len(candidates),
        "stopped": stopped,
        "states": dict(tally),
    }

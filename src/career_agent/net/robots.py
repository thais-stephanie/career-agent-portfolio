"""Reading a host's own `robots.txt` before reading anything else on it.

WHY THIS DID NOT EXIST UNTIL NOW
--------------------------------
Every source this product reads was checked BY HAND: somebody opened the
vendor's `robots.txt` and its terms page, read them, and wrote the quote and
the date into `config/source_catalogue.yaml`. That is a better answer than a
runtime check for a set of eighteen vendors, because a person can read a terms
page and a parser cannot, and because the record is reviewable.

It does not scale to **two hundred and eighty company websites**. Career-site
discovery reads a registered company's own careers page, and those are 280
different hosts nobody has read a statement from.

ADR-0018 already decides what to do about that, in the fourth and fifth of the
five limits it binds every collector to: *an explicit restriction recorded and
obeyed when found*, and *a first-party statement about one host never read as
covering another*. This is the machinery those two sentences require.

THE THREE ANSWERS, AND WHY SILENCE IS NOT THE SAME AS UNREADABLE
-----------------------------------------------------------------
    ALLOWED     the file says nothing about this path, or allows it
    DISALLOWED  a rule matches our agent and this path
    UNREADABLE  the file could not be read at all

**Silence is permission** -- ADR-0018 says so in as many words, and a host with
no `robots.txt` has not objected to anything. A 404 is therefore ALLOWED.

**Unreadable is not silence.** A 500, a timeout or a body that is not a robots
file means there may be a statement we failed to read, and reading the site
anyway would be assuming the answer we prefer. It refuses.

WHAT THIS IS NOT
----------------
It is not a terms-of-service check, and it must never be presented as one. A
`robots.txt` that allows a path says nothing about what a vendor's terms
permit, which is exactly why the source catalogue quotes terms pages separately
and why `Coverage.UNSUPPORTED` exists for the ones that could not be read.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from enum import StrEnum
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

from career_agent.net.fetcher import CONNECT_TIMEOUT_SECONDS, USER_AGENT

#: How much of a `robots.txt` is read.
#:
#: A real one is a few kilobytes. A response longer than this is not a robots
#: file and reading all of it would be letting an arbitrary host decide how
#: much memory this process uses.
MAX_ROBOTS_BYTES = 512_000


class RobotsDecision(StrEnum):
    ALLOWED = "ALLOWED"
    DISALLOWED = "DISALLOWED"
    UNREADABLE = "UNREADABLE"

    @property
    def may_fetch(self) -> bool:
        return self is RobotsDecision.ALLOWED


def origin_of(url: str) -> str:
    """`https://host` for a URL, which is the scope a robots file covers.

    Its own sentence in the five limits: a statement about one host is never
    read as covering another. A subdomain is a different host and gets its own
    file.
    """
    parts = urlsplit(url)
    return urlunsplit((parts.scheme or "https", parts.netloc, "", "", ""))


class RobotsPolicy:
    """One `robots.txt` per origin, read once and remembered for this process.

    CACHED BY ORIGIN, because a scan reads up to five paths on each host and
    fetching the same file five times would be the impoliteness this class
    exists to prevent.

    The cache lives for the life of the object rather than on disk: a policy is
    a fact about a host right now, and a stale allow is the one kind of stale
    answer that matters here.
    """

    def __init__(self, *, agent: str = USER_AGENT, timeout: float = CONNECT_TIMEOUT_SECONDS):
        self.agent = agent
        self.timeout = timeout
        self._cache: dict[str, RobotsDecision | RobotFileParser] = {}

    def _policy_for(self, origin: str) -> RobotsDecision | RobotFileParser:
        cached = self._cache.get(origin)
        if cached is not None:
            return cached

        request = urllib.request.Request(  # noqa: S310 - scheme is checked below
            f"{origin}/robots.txt",
            headers={"User-Agent": self.agent, "Accept": "text/plain,*/*;q=0.5"},
        )
        if not origin.startswith(("http://", "https://")):
            self._cache[origin] = RobotsDecision.UNREADABLE
            return RobotsDecision.UNREADABLE

        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                body = response.read(MAX_ROBOTS_BYTES).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            # 404 and 410 are SILENCE, and ADR-0018 says silence is permission.
            # Every other status is a statement we failed to read.
            outcome = (
                RobotsDecision.ALLOWED if exc.code in (404, 410) else RobotsDecision.UNREADABLE
            )
            self._cache[origin] = outcome
            return outcome
        except (urllib.error.URLError, OSError, ValueError):
            self._cache[origin] = RobotsDecision.UNREADABLE
            return RobotsDecision.UNREADABLE

        parser = RobotFileParser()
        parser.parse(body.splitlines())
        self._cache[origin] = parser
        return parser

    def decide(self, url: str) -> RobotsDecision:
        """Whether this product may fetch one URL, per that host's own file."""
        policy = self._policy_for(origin_of(url))
        if isinstance(policy, RobotsDecision):
            return policy
        return (
            RobotsDecision.ALLOWED
            if policy.can_fetch(self.agent, url)
            else RobotsDecision.DISALLOWED
        )

    def may_fetch(self, url: str) -> bool:
        return self.decide(url).may_fetch

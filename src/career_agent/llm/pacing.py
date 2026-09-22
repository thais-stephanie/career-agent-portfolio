"""Keeping a sequential run under the ceilings its caller supplied.

Deliberately small, deliberately local, deliberately vendor-neutral. This is one
process making one call at a time; a distributed token bucket would be
infrastructure built for a problem this milestone does not have.

It knows nothing about vendors, tiers or quotas. It is given two numbers and
holds the run under them. Whose limits those numbers represent, where they came
from and when they were read is `llm/quotas.py`'s business, and keeping the two
apart is what stops one account's observed limits from being applied to another
account as though they were a vendor's policy.

TWO CEILINGS, BECAUSE THEY BIND AT DIFFERENT TIMES
--------------------------------------------------
Requests per minute is usually the constraint that binds first, but it is not
the only one, and the gap is smaller than it looks. At 15 requests per minute of
description calls the extraction pipeline would draw roughly 174,000 input
tokens per minute -- 70% of a 250,000 ceiling -- so a run of long postings paced
at the request ceiling could cross the token ceiling without ever touching the
one it was watching. Both are therefore enforced, and the caller paces below the
request ceiling rather than at it.

WHY A SLIDING WINDOW *AND* A MINIMUM INTERVAL
---------------------------------------------
A minimum interval alone admits one extra request at the window boundary: at
six-second spacing, eleven requests land in the sixty seconds from t=0 to t=60.
A sliding window alone admits a burst of the entire minute's allowance in a few
seconds, which is legal but rude and makes the token ceiling far easier to
cross. Enforcing both spaces the calls evenly *and* is provably within the
window at every instant.

THE CLOCK IS INJECTED
---------------------
Because a test that proves the pacing works must not take a minute to run. The
defaults are the real clock and the real sleep; the tests pass fakes and assert
on the intervals that were requested rather than on wall time.
"""

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic as _monotonic
from time import sleep as _sleep

#: The window both ceilings are expressed over.
WINDOW_SECONDS = 60.0


@dataclass
class RateLimiter:
    """Blocks until one more request of a given size is within both ceilings.

    Both numbers are the caller's, and this class has no opinion about where
    they should have come from. `requests_per_minute` is a *pace* and is
    expected to sit below whatever ceiling applies; `tokens_per_minute` is the
    hard backstop the run may not cross.
    """

    requests_per_minute: int
    tokens_per_minute: int
    monotonic: Callable[[], float] = _monotonic
    sleep: Callable[[float], None] = _sleep

    #: Instants of the requests already made, oldest first.
    _requests: deque[float] = field(default_factory=deque, init=False, repr=False)
    #: (instant, tokens) for the requests already made, oldest first.
    _tokens: deque[tuple[float, int]] = field(default_factory=deque, init=False, repr=False)
    #: Earliest instant the next request may be made. Moved forward by
    #: `penalise` when a vendor says it is being asked too often.
    _not_before: float = field(default=0.0, init=False, repr=False)

    #: Total seconds spent waiting. Reported, because a run that took twenty
    #: minutes because of pacing and one that took twenty minutes because the
    #: vendor was slow are different facts.
    waited_seconds: float = field(default=0.0, init=False)

    def __post_init__(self) -> None:
        if self.requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        if self.tokens_per_minute < 1:
            raise ValueError("tokens_per_minute must be at least 1")

    @property
    def min_interval(self) -> float:
        return WINDOW_SECONDS / self.requests_per_minute

    def penalise(self, seconds: float) -> None:
        """Hold the next request back by at least this long.

        Used for a retryable vendor refusal -- a 429, an exhausted quota. The
        ordinary interval is the right pace when nothing is wrong; it is not the
        right response to a vendor that has just said it is being asked too
        often.
        """
        if seconds <= 0:
            return
        self._not_before = max(self._not_before, self.monotonic() + seconds)

    def acquire(self, tokens: int) -> float:
        """Wait until a request of `tokens` input tokens may be made.

        Returns the seconds waited, so a caller can report pacing separately
        from vendor latency. Records the request as made -- the caller must
        actually make it.
        """
        waited = 0.0
        while True:
            now = self.monotonic()
            self._forget_before(now - WINDOW_SECONDS)
            delay = max(
                self._not_before - now,
                self._request_delay(now),
                self._token_delay(now, tokens),
            )
            if delay <= 0:
                break
            self.sleep(delay)
            waited += delay

        now = self.monotonic()
        self._requests.append(now)
        self._tokens.append((now, tokens))
        self._not_before = now + self.min_interval
        self.waited_seconds += waited
        return waited

    # -- the two ceilings ---------------------------------------------------

    def _request_delay(self, now: float) -> float:
        """How long until the request window has room for one more."""
        if len(self._requests) < self.requests_per_minute:
            return 0.0
        return self._requests[0] + WINDOW_SECONDS - now

    def _token_delay(self, now: float, tokens: int) -> float:
        """How long until the token window has room for `tokens` more.

        A single request larger than the whole ceiling can never fit. Waiting
        for it forever would be worse than sending it, so it is let through:
        the vendor will refuse it, which is a visible, recorded failure rather
        than a run that hangs with no explanation.
        """
        if tokens >= self.tokens_per_minute:
            return 0.0
        spent = sum(count for _, count in self._tokens)
        if spent + tokens <= self.tokens_per_minute:
            return 0.0
        # Retire the oldest entries until the request fits, and wait for the
        # last one retired to leave the window.
        remaining = spent
        for instant, count in self._tokens:
            remaining -= count
            if remaining + tokens <= self.tokens_per_minute:
                return instant + WINDOW_SECONDS - now
        return 0.0  # pragma: no cover - unreachable given the guard above

    def _forget_before(self, cutoff: float) -> None:
        while self._requests and self._requests[0] <= cutoff:
            self._requests.popleft()
        while self._tokens and self._tokens[0][0] <= cutoff:
            self._tokens.popleft()

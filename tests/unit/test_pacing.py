"""The rate limiter, on a fake clock.

A test that proved the pacing works by actually waiting would take a minute to
assert one thing, so the clock and the sleep are injected and the assertions are
about the intervals that were *requested*. That is also the honest thing to
measure: the limiter's job is to ask for the right delay, and whether the
operating system honours it precisely is not something this code decides.
"""

import pathlib

import pytest

from career_agent.llm.pacing import WINDOW_SECONDS, RateLimiter


class FakeClock:
    """A clock that only moves when something sleeps."""

    def __init__(self) -> None:
        self.now = 1_000.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def limiter(rpm: int = 10, tpm: int = 250_000) -> tuple[RateLimiter, FakeClock]:
    clock = FakeClock()
    return (
        RateLimiter(
            requests_per_minute=rpm,
            tokens_per_minute=tpm,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ),
        clock,
    )


def test_requests_are_spaced_at_the_configured_rate() -> None:
    paced, clock = limiter(rpm=10)

    for _ in range(4):
        paced.acquire(1_000)

    # Ten per minute is one every six seconds. The first call waits for nothing.
    assert clock.slept == [6.0, 6.0, 6.0]
    assert paced.waited_seconds == pytest.approx(18.0)


def test_no_more_than_the_configured_requests_land_in_any_window() -> None:
    """The property the whole limiter exists for, asserted directly.

    Not "the calls were six seconds apart" -- that is the mechanism. The claim
    is that no sixty-second window anywhere in the run holds more than the
    configured number, which is what a vendor actually counts.
    """
    paced, clock = limiter(rpm=10)
    instants: list[float] = []

    for _ in range(30):
        paced.acquire(1_000)
        instants.append(clock.now)

    for start in instants:
        inside = [t for t in instants if start <= t < start + WINDOW_SECONDS]
        assert len(inside) <= 10, f"{len(inside)} requests in the window from {start}"


def test_the_token_ceiling_binds_even_when_the_request_rate_does_not() -> None:
    """The failure the go/no-go warned about, made into a test.

    Paced at the request ceiling, long postings can draw 70% of the token
    ceiling in a minute -- so a run that watched only its request rate could
    cross the token rate without ever seeing it coming.
    """
    paced, clock = limiter(rpm=60, tpm=30_000)

    for _ in range(3):
        paced.acquire(10_000)
    # Three calls of 10,000 fill the window exactly; the fourth must wait for
    # the first to leave it, which the one-second request interval would not.
    paced.acquire(10_000)

    assert clock.slept[-1] > 1.0
    assert paced.waited_seconds > 1.0


def test_a_penalty_holds_the_next_request_back_beyond_the_normal_interval() -> None:
    paced, clock = limiter(rpm=10)
    paced.acquire(100)

    paced.penalise(45.0)
    paced.acquire(100)

    assert clock.slept[-1] == pytest.approx(45.0)


def test_a_penalty_of_zero_or_less_changes_nothing() -> None:
    paced, clock = limiter(rpm=60)
    paced.acquire(100)
    paced.penalise(0.0)
    paced.acquire(100)

    assert clock.slept == [1.0]


def test_a_request_larger_than_the_whole_ceiling_is_sent_rather_than_waited_on() -> None:
    """Waiting forever would be worse than letting the vendor refuse it.

    A refusal is a recorded, visible failure. A run that hangs with no output is
    a run nobody can diagnose.
    """
    paced, clock = limiter(rpm=60, tpm=1_000)
    paced.acquire(5_000)

    assert clock.slept == []


def test_a_pace_below_one_is_refused() -> None:
    with pytest.raises(ValueError):
        RateLimiter(requests_per_minute=0, tokens_per_minute=250_000)
    with pytest.raises(ValueError):
        RateLimiter(requests_per_minute=10, tokens_per_minute=0)


def test_the_limiter_knows_nothing_about_vendors_tiers_or_quotas() -> None:
    """The separation that stops one account's limits becoming a vendor fact.

    `RateLimiter` takes two numbers. Whose limits they represent, where they
    came from and when they were read lives in `llm/quotas.py`, which carries
    the provenance the limiter has no business holding.
    """
    import career_agent.llm.pacing as pacing

    source = pathlib.Path(pacing.__file__).read_text(encoding="utf-8")
    for forbidden in ("QuotaLimits", "FREE_TIER", "gemini", "google", "free_tier"):
        assert forbidden not in source, f"pacing.py names {forbidden!r}"

"""Cooperative collection budget, separate from source/network failures."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field


class BudgetExpired(Exception):
    """Pending work must resume later; the source did not fail."""


@dataclass
class Deadline:
    ends_at: float
    clock: Callable[[], float] = field(default=time.monotonic)

    def check(self) -> None:
        if self.clock() >= self.ends_at:
            raise BudgetExpired("TIME_BUDGET")

    def sleep(self, seconds: float, sleep: Callable[[float], None]) -> None:
        self.check()
        if seconds >= self.ends_at - self.clock():
            # Do not shorten a rate-limit wait and issue an early request.
            raise BudgetExpired("TIME_BUDGET")
        sleep(seconds)
        self.check()

"""A ledger of Jooble requests this machine is known to have made.

Jooble's free REST plan documents **a total lifetime limit of 500 requests per
key**. Not per month, not per day: five hundred, ever. That single fact decides
the shape of everything here, because the ordinary way this project develops a
connector -- run it, look at the result, adjust, run it again -- would spend a
meaningful fraction of a permanent budget on each iteration.

**What this counter is, exactly.** It records requests CAREER AGENT IS KNOWN TO
HAVE MADE FROM THIS MACHINE. It is not the provider's balance and must never be
printed as one. The key may have been used elsewhere, by another clone, by a
previous experiment, or by whoever generated it; a cache hit costs nothing here
and the vendor never saw it; and a request that failed in transit may or may not
have been counted upstream. So the number this file can honestly produce is an
UPPER BOUND on what remains:

    documented_limit          500, from the vendor's own documentation
    known_local_requests      what we have counted here
    known_remaining_upper_bound   the subtraction, and no more than that

`remaining` is deliberately not a field. A caller wanting "how many do I have
left" gets a bound with the word `upper` in its name, because the honest answer
to that question is "at most this many".

**It lives under `data/`.** That directory is gitignored, which is where a
runtime fact about one person's key belongs. Nothing in it is a secret: the key
is never read, never hashed into it and never named.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: The vendor's documented free-plan limit, read from Jooble's own REST API
#: documentation on 2026-09-05. A number we were TOLD, recorded with the date we
#: were told it, exactly like the rate limits in `llm/quotas.py`.
DOCUMENTED_LIFETIME_LIMIT = 500
DOCUMENTED_ON = "2026-09-05"

#: Where the ledger lives, relative to a database directory. Under `data/`,
#: which is gitignored.
LEDGER_FILENAME = "jooble-usage.json"


class QuotaExhausted(RuntimeError):
    """The configured budget for this run, or the documented lifetime limit, is spent.

    Raised BEFORE a request leaves the machine. A quota error discovered from a
    403 has already cost the thing it was protecting.
    """


@dataclass(frozen=True, slots=True)
class Usage:
    """What the ledger holds, as a reader should think about it."""

    known_local_requests: int
    first_recorded_at: str | None
    last_recorded_at: str | None

    @property
    def documented_limit(self) -> int:
        return DOCUMENTED_LIFETIME_LIMIT

    @property
    def known_remaining_upper_bound(self) -> int:
        """At most this many. Never 'remaining'."""
        return max(0, DOCUMENTED_LIFETIME_LIMIT - self.known_local_requests)

    def as_dict(self) -> dict[str, Any]:
        return {
            "documented_limit": self.documented_limit,
            "documented_on": DOCUMENTED_ON,
            "known_local_requests": self.known_local_requests,
            "known_remaining_upper_bound": self.known_remaining_upper_bound,
            "first_recorded_at": self.first_recorded_at,
            "last_recorded_at": self.last_recorded_at,
        }

    @property
    def sentence(self) -> str:
        return (
            f"Jooble documents a lifetime limit of {self.documented_limit} requests per key "
            f"({DOCUMENTED_ON}). This machine has made {self.known_local_requests}, so at most "
            f"{self.known_remaining_upper_bound} remain. That is an upper bound, not a balance: "
            f"the key may have been used elsewhere."
        )


class QuotaLedger:
    """Append-only counting, with the check before the spend.

    Deliberately not clever. One integer, two timestamps, a file. The
    interesting behaviour is entirely in WHEN it is consulted: `reserve` runs
    before the request rather than after, so a budget of zero costs nothing to
    discover.
    """

    def __init__(self, path: Path, *, limit: int = DOCUMENTED_LIFETIME_LIMIT) -> None:
        self.path = path
        self.limit = limit

    # -- reading -----------------------------------------------------------

    def read(self) -> Usage:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # An absent or unreadable ledger reads as zero KNOWN requests, which
            # is the truth: we know of none. It is not a claim that none were
            # made, which is why the bound it produces is an upper one.
            return Usage(0, None, None)
        return Usage(
            known_local_requests=int(raw.get("known_local_requests", 0)),
            first_recorded_at=raw.get("first_recorded_at"),
            last_recorded_at=raw.get("last_recorded_at"),
        )

    # -- spending ----------------------------------------------------------

    def reserve(self, *, budget: int | None = None) -> Usage:
        """Refuse before the request, or count it and return the new state.

        `budget` is the ceiling for THIS run, and it is separate from the
        lifetime limit on purpose: a live probe that may spend three requests
        and a collection that may spend twenty are different authorisations,
        and neither should be able to spend the other's.
        """
        usage = self.read()
        if usage.known_local_requests >= self.limit:
            raise QuotaExhausted(
                f"the documented lifetime limit of {self.limit} Jooble requests is spent "
                f"according to this machine's ledger ({self.path.name}). No request was sent."
            )
        if budget is not None and budget <= 0:
            raise QuotaExhausted("this run has no Jooble request budget left. No request was sent.")
        return self._record(usage)

    def _record(self, usage: Usage) -> Usage:
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        updated = Usage(
            known_local_requests=usage.known_local_requests + 1,
            first_recorded_at=usage.first_recorded_at or now,
            last_recorded_at=now,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(updated.as_dict(), indent=1, sort_keys=True), encoding="utf-8"
        )
        return updated


def ledger_for(db_path: Path) -> QuotaLedger:
    """The ledger beside the database it is being spent for.

    Beside the database rather than in one fixed place, because a demo database
    and the real corpus are different populations and the person may reasonably
    keep more than one. `data/` is gitignored either way.
    """
    # The key's quota is installation-wide: one ledger however many local
    # profiles use it, or each would spend the whole quota again.
    from career_agent.runtime.profiles import installation_data_dir

    return QuotaLedger(installation_data_dir(db_path) / LEDGER_FILENAME)

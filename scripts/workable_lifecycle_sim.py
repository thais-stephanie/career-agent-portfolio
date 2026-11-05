"""Reference model of a Workable closing policy. RESEARCH ONLY, not product code.

Nothing in `src/` imports this. It states the proposed rules as one pure
function so the scenarios in `tests/unit/test_workable_lifecycle_sim.py` can
check them before any product code exists. See
`docs/research/workable-lifecycle/`.

Policies:
    A  current behaviour: absence never closes.
    B  close after `misses` absences in distinct complete feeds spread over
       at least `min_hours`, unless the read trips a guard.
    CS as B, but a seeded sample of `confirm_cap` due postings is checked
       first; any answer other than "gone" closes nothing on that read.
    C  as B, but a closure also needs the employer's own posting to answer
       "gone". "Still published" keeps the row open. "Unknown" keeps it
       suspected and asks again on the next read.

A read counts only when it is complete (read to its end, not malformed, not
cut, not a 304) and is a feed version not counted before (its Last-Modified).
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field

OPEN, SUSPECTED, CLOSED = "OPEN", "SUSPECTED_STALE", "CLOSED"
GONE, PUBLISHED, UNKNOWN = "GONE", "PUBLISHED", "UNKNOWN"


@dataclass(frozen=True)
class Read:
    version: str  # the feed's Last-Modified
    at_hours: float
    present: frozenset[str]
    complete: bool = True
    not_modified: bool = False


@dataclass(frozen=True)
class Policy:
    name: str = "C"
    misses: int = 2
    min_hours: float = 24.0
    #: Hold when the feed is smaller than this share of the last counted one.
    min_size_ratio: float = 0.97
    #: Hold when more than this share of the postings open at the start of
    #: the read would gain a miss.
    max_new_miss_ratio: float = 0.15
    confirm_cap: int = 300


@dataclass
class Posting:
    status: str = OPEN
    misses: int = 0
    first_miss_at: float | None = None
    events: list[tuple[str, str, str, str]] = field(default_factory=list)

    def move(self, to: str, reason: str, version: str) -> None:
        self.events.append((self.status, to, reason, version))
        self.status = to


@dataclass
class Catalogue:
    postings: dict[str, Posting] = field(default_factory=dict)
    counted: set[str] = field(default_factory=set)
    last_size: int | None = None
    holds: list[tuple[str, str]] = field(default_factory=list)
    confirmations: int = 0


def apply_read(
    cat: Catalogue,
    read: Read,
    policy: Policy,
    confirm: Callable[[str], str] | None = None,
) -> str:
    """Apply one read and return what happened to it."""
    if not read.complete:
        return "IGNORED_INCOMPLETE"
    if read.not_modified or read.version in cat.counted:
        return "IGNORED_ALREADY_COUNTED"

    for pid in read.present:
        p = cat.postings.setdefault(pid, Posting())
        if p.status != OPEN:
            p.move(OPEN, "SEEN_AGAIN", read.version)
        p.misses, p.first_miss_at = 0, None

    if policy.name == "A":
        cat.counted.add(read.version)
        cat.last_size = len(read.present)
        return "COUNTED"

    live = [pid for pid, p in cat.postings.items() if p.status != CLOSED]
    absent = [pid for pid in live if pid not in read.present]
    newly = sum(1 for pid in absent if cat.postings[pid].misses == 0)
    if cat.last_size and len(read.present) < policy.min_size_ratio * cat.last_size:
        cat.holds.append((read.version, "FEED_SHRANK"))
        return "HELD_FEED_SHRANK"
    if live and newly > policy.max_new_miss_ratio * len(live):
        cat.holds.append((read.version, "TOO_MANY_NEW_MISSES"))
        return "HELD_TOO_MANY_NEW_MISSES"

    cat.counted.add(read.version)
    cat.last_size = len(read.present)
    due = []
    for pid in sorted(absent):
        p = cat.postings[pid]
        p.misses += 1
        if p.first_miss_at is None:
            p.first_miss_at = read.at_hours
        if p.status == OPEN:
            p.move(SUSPECTED, "MISSING_FROM_COMPLETE_FEED", read.version)
        if p.misses >= policy.misses and read.at_hours - p.first_miss_at >= policy.min_hours:
            due.append(pid)

    if policy.name == "B":
        for pid in due:
            cat.postings[pid].move(CLOSED, f"MISSED_{cat.postings[pid].misses}_FEEDS", read.version)
        return "COUNTED"

    if policy.name == "CS":
        # B decides; a seeded sample of the employer check audits the batch.
        # Anything but "gone" in the sample closes nothing on this read.
        sample = random.Random(read.version).sample(due, min(policy.confirm_cap, len(due)))
        answers = {pid: confirm(pid) if confirm else UNKNOWN for pid in sample}
        cat.confirmations += len(sample)
        for pid, answer in answers.items():
            if answer == PUBLISHED:
                cat.postings[pid].misses, cat.postings[pid].first_miss_at = 0, None
                cat.postings[pid].move(OPEN, "EMPLOYER_STILL_LISTS", read.version)
        if any(a != GONE for a in answers.values()):
            cat.holds.append((read.version, "AUDIT_SAMPLE_NOT_ALL_GONE"))
            return "HELD_AUDIT_SAMPLE"
        for pid in due:
            cat.postings[pid].move(CLOSED, f"MISSED_{cat.postings[pid].misses}_FEEDS", read.version)
        return "COUNTED"

    budget = policy.confirm_cap
    for pid in due:
        p = cat.postings[pid]
        if confirm is None or budget <= 0:
            continue
        budget -= 1
        cat.confirmations += 1
        answer = confirm(pid)
        if answer == GONE:
            p.move(CLOSED, "EMPLOYER_SAYS_GONE", read.version)
        elif answer == PUBLISHED:
            # The employer still publishes it: the feed is not its board.
            # Start counting again, so it is asked again only after another
            # `misses` complete feeds and `min_hours`.
            p.misses, p.first_miss_at = 0, None
            p.move(OPEN, "EMPLOYER_STILL_LISTS", read.version)
    return "COUNTED"

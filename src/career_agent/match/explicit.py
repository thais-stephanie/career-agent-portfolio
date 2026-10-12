"""Three explicit facts an ad states in words that nothing used to read.

Part-time work and the hours a week it takes, a talent pool (a list of people
to call when something opens, not an opening), and a time-zone requirement.
Each is a fact ABOUT THE POSTING with the employer's own sentence beside it.
None of them is a score, a gate or a verdict about the candidate:

* part-time is a schedule. It says nothing about whether the worker is an
  employee or a contractor, and no engagement is inferred from it;
* a talent pool says there may be no opening today. It is shown quietly and
  never excludes the posting;
* a time-zone requirement says WHEN someone must be reachable. It is not where
  they live (invariant 3: timezone is not geography), and it feeds no gate.

Pure, candidate independent, and cheap enough to run on every posting.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from career_agent.match.text import sentence_at

__all__ = [
    "PoolReading",
    "ScheduleReading",
    "TimezoneReading",
    "explicit_facts",
    "read_schedule",
    "read_talent_pool",
    "read_timezone",
]

_DASH = "-" + chr(0x2013) + chr(0x2014)
_PART_TIME = re.compile(r"\b(?:part[- ]?time|meio[- ]per[ií]odo|tempo parcial)\b", re.IGNORECASE)
_FULL_TIME = re.compile(r"\b(?:full[- ]?time|tempo integral)\b", re.IGNORECASE)
_WEEK = r"(?:per|a|each|every|/|por)\s*(?:week|wk|semana)"
_HOURS_RANGE = re.compile(
    rf"\b(\d{{1,2}})\s*(?:[{_DASH}]|to|a)\s*(\d{{1,2}})\s*(?:hours?|hrs?|horas?)\s*{_WEEK}",
    re.IGNORECASE,
)
_HOURS_ONE = re.compile(rf"\b(\d{{1,2}})\s*(?:hours?|hrs?|horas?)\s*{_WEEK}", re.IGNORECASE)


@dataclass(frozen=True)
class ScheduleReading:
    """What the ad says about the working week."""

    part_time: bool
    hours_min: int | None
    hours_max: int | None
    quote: str


def read_schedule(text: str) -> ScheduleReading | None:
    """Part-time and weekly hours, only as the ad wrote them. Never a guess:
    no hours are assumed for a full-time job, and `40 hours` is not part-time."""
    if not text:
        return None
    hours = _HOURS_RANGE.search(text)
    low = high = None
    where: re.Match[str] | None = None
    if hours:
        low, high = int(hours.group(1)), int(hours.group(2))
        if low > high or high > 80:
            hours = None
            low = high = None
    if hours:
        where = hours
    else:
        one = _HOURS_ONE.search(text)
        if one and int(one.group(1)) <= 80:
            low = high = int(one.group(1))
            where = one
    part = _PART_TIME.search(text)
    if part is not None and _FULL_TIME.search(text[max(0, part.start() - 60) : part.end() + 60]):
        part = None  # "full-time or part-time": the ad offers both, so it states neither
    anchor = part or where
    if anchor is None:
        return None
    return ScheduleReading(
        part_time=part is not None,
        hours_min=low,
        hours_max=high,
        quote=sentence_at(text, anchor.start(), anchor.end()),
    )


_POOL = re.compile(
    r"\b(?:talent[- ](?:pool|network|community|bench)|banco de talentos|cadastro reserva|"
    r"(?:there )?(?:may|might) not be an immediate opening|no immediate opening|"
    r"not (?:currently )?hiring for a specific|evergreen (?:role|position|requisition))\b",
    re.IGNORECASE,
)
_NO_OPENING = re.compile(
    r"(?:may|might) not be an immediate opening|no immediate opening", re.IGNORECASE
)


@dataclass(frozen=True)
class PoolReading:
    """The ad collects applications for later; there may be no opening today."""

    quote: str


def read_talent_pool(text: str) -> PoolReading | None:
    if not text:
        return None
    # The sentence that says there may be no opening is the one that matters;
    # the words "talent pool" alone can also name a pipeline of a live search.
    found = _NO_OPENING.search(text) or _POOL.search(text)
    if found is None:
        return None
    return PoolReading(quote=sentence_at(text, found.start(), found.end()))


_ZONE = re.compile(
    r"\b(?:time[- ]?zones?|timezones?|fusos? hor[aá]rios?|business hours)\b", re.IGNORECASE
)
_ZONE_REGION = re.compile(
    r"\b(european|europe|eu|cet|eet|gmt|utc|us|u\.s\.|american|latam|brazil|brasil|asia|apac|"
    r"emea|eastern|pacific|central)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class TimezoneReading:
    """WHEN the ad wants the person reachable. Not where they live."""

    quote: str


def read_timezone(text: str) -> TimezoneReading | None:
    if not text:
        return None
    for match in _ZONE.finditer(text):
        sentence = sentence_at(text, match.start(), match.end())
        if _ZONE_REGION.search(sentence):
            return TimezoneReading(quote=sentence)
    return None


def explicit_facts(text: str) -> dict[str, object]:
    """The three readings as plain data for `posting_facts`; absent stays absent."""
    out: dict[str, object] = {}
    for key, reading in (
        ("schedule", read_schedule(text)),
        ("talent_pool", read_talent_pool(text)),
        ("timezone", read_timezone(text)),
    ):
        if reading is not None:
            out[key] = asdict(reading)
    return out

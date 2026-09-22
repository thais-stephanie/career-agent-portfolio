"""Timestamp and identifier discipline (migration hazards A1, A3, A4).

These look like trivia. They are not: the format chosen here is the difference
between a mechanical PostgreSQL migration and a rewrite of every query.
"""

import re
from datetime import UTC, datetime

from career_agent.clock import ULID_LENGTH, is_valid_id, new_id, now_utc

ISO_UTC_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_now_utc_is_iso8601_with_a_z_suffix() -> None:
    assert ISO_UTC_Z.match(now_utc()), now_utc()


def test_now_utc_is_actually_utc() -> None:
    """A local timestamp that merely *looks* like UTC is the worst outcome, so
    compare against a real UTC reading rather than trusting the format."""
    parsed = datetime.fromisoformat(now_utc().replace("Z", "+00:00"))
    delta = abs((parsed - datetime.now(UTC)).total_seconds())
    assert delta < 60, f"now_utc() is {delta}s away from real UTC - wrong timezone?"


def test_timestamps_sort_lexicographically_in_time_order() -> None:
    """Why the format matters: string ORDER BY is correct date ordering, so no
    SQL date function is ever needed."""
    earlier = "2026-08-25T09:00:00Z"
    later = "2026-08-25T21:00:00Z"
    next_year = "2027-01-01T00:00:00Z"
    assert sorted([next_year, later, earlier]) == [earlier, later, next_year]


def test_new_id_is_a_valid_ulid() -> None:
    identifier = new_id()
    assert len(identifier) == ULID_LENGTH
    assert is_valid_id(identifier)


def test_ids_are_unique() -> None:
    assert len({new_id() for _ in range(1000)}) == 1000


def test_ids_sort_in_creation_order() -> None:
    """ULIDs are time-prefixed, so 'newest first' is a plain string sort."""
    batch = [new_id() for _ in range(50)]
    assert batch == sorted(batch)


def test_is_valid_id_rejects_look_alikes() -> None:
    assert not is_valid_id("")
    assert not is_valid_id("not-a-ulid")
    assert not is_valid_id("0" * 25)
    # Crockford base32 excludes I, L, O and U.
    assert not is_valid_id("I" * ULID_LENGTH)

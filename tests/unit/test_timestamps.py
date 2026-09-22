"""The `posted_at` contract: RFC 3339, offset-aware, UTC, second resolution."""

from datetime import datetime

import pytest

from career_agent.domain.timestamps import RFC3339_UTC_LENGTH, to_rfc3339_utc


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Greenhouse: the board's local offset, converted rather than preserved.
        ("2026-02-28T09:04:24-05:00", "2026-02-28T14:04:24+00:00"),
        ("2026-07-28T15:59:04-04:00", "2026-07-28T19:59:04+00:00"),
        ("2026-06-01T09:00:00Z", "2026-06-01T09:00:00+00:00"),
        ("2026-06-01T09:00:00+00:00", "2026-06-01T09:00:00+00:00"),
        # A positive offset, which crosses back over midnight.
        ("2026-06-01T01:00:00+05:30", "2026-05-31T19:30:00+00:00"),
        # Lever: epoch milliseconds.
        (1519801128216, "2018-02-28T06:58:48+00:00"),
        (1780000000000, "2026-05-28T20:26:40+00:00"),
    ],
    ids=[
        "gh-est",
        "gh-edt",
        "gh-zulu",
        "already-utc",
        "positive-offset",
        "lever-epoch",
        "lever-epoch-2",
    ],
)
def test_provider_encodings_converge_on_one_representation(raw: object, expected: str) -> None:
    assert to_rfc3339_utc(raw) == expected


def test_every_conforming_value_is_the_same_shape() -> None:
    """25 characters, offset-aware, UTC. A value that is not 25 characters long
    has either sub-second precision or a non-UTC offset -- both contract
    violations, and both silent ones if nobody checks."""
    for raw in ("2026-02-28T09:04:24-05:00", 1519801128216, "2026-06-01T09:00:00Z"):
        value = to_rfc3339_utc(raw)
        assert value is not None
        assert len(value) == RFC3339_UTC_LENGTH
        assert value.endswith("+00:00")
        parsed = datetime.fromisoformat(value)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() is not None and parsed.utcoffset().total_seconds() == 0


def test_sub_second_precision_is_dropped_not_rounded_up() -> None:
    """Greenhouse has no sub-second resolution, and a column of mixed precision
    invites comparisons that are only sometimes meaningful."""
    assert to_rfc3339_utc(1519801128999) == "2018-02-28T06:58:48+00:00"
    assert to_rfc3339_utc("2026-06-01T09:00:00.999999Z") == "2026-06-01T09:00:00+00:00"


@pytest.mark.parametrize(
    "raw",
    [None, True, False, "", "   ", "not a date", "2026-13-45", {"t": 1}, [], 10**20, object()],
    ids=[
        "none",
        "true",
        "false",
        "empty",
        "blank",
        "prose",
        "impossible-date",
        "object",
        "list",
        "out-of-range",
        "arbitrary",
    ],
)
def test_unusable_input_is_none_never_a_fabricated_date(raw: object) -> None:
    """Absence of a timestamp is a fact. Substituting "now" would make an
    unknown posting look brand new -- the freshness equivalent of treating
    silence as permission.

    `True` earns its place here: bool is an int subclass, so an unguarded epoch
    branch would quietly turn it into 1970.
    """
    assert to_rfc3339_utc(raw) is None


def test_a_naive_timestamp_is_read_as_utc_and_this_is_deliberate() -> None:
    """The one assumption in this module, pinned so it stays a decision.

    A string with no offset is ambiguous by up to +/-14 hours. Neither current
    provider produces one -- Greenhouse was offset-aware on 413/413 stored
    postings, Lever is an epoch -- so this is a defensive branch. It is read as
    UTC rather than discarded because for a freshness signal measured in days, a
    worst-case half-day skew is less harmful than losing the date entirely.
    """
    assert to_rfc3339_utc("2026-06-01T09:00:00") == "2026-06-01T09:00:00+00:00"


def test_conversion_is_idempotent() -> None:
    """Its own output must be a fixed point, or a second pass over stored values
    would quietly shift them."""
    once = to_rfc3339_utc("2026-02-28T09:04:24-05:00")
    assert once is not None
    assert to_rfc3339_utc(once) == once

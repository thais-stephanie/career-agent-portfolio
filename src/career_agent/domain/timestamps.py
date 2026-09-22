"""The one timestamp representation `posted_at` is allowed to have.

Every ATS states publication time differently. Greenhouse returns RFC 3339 with
the board's local offset (`2026-02-28T09:04:24-05:00`); Lever returns epoch
milliseconds as an integer (`1519801128216`). `PostingStub.posted_at` is a
single string field, so without a convention `job.posted_at` becomes a column
holding two incompatible encodings and every later freshness calculation is a
guess dressed as arithmetic.

**The contract (M1B.1 / A7.2):** `posted_at` is RFC 3339, offset-aware,
normalised to UTC, at second resolution -- `2026-02-28T14:04:24+00:00`. Always
25 characters, always parseable by a single `datetime.fromisoformat` call,
always directly comparable between providers.

Provider-native encodings are deliberately NOT preserved here. They remain in
the archived provider payload, where they are addressable by JSON path and
verifiable like any other field. This module converts representation; it never
decides what a date means.

Lives in the pure domain layer, alongside `normalize.py`, for the same reason:
one implementation, used by every adapter, that cannot drift per provider.
"""

from datetime import UTC, datetime

#: Length of a conforming value. Pinned because "always 25 characters" is a
#: property tests can assert, and a value that is not 25 characters long has
#: either sub-second precision or a non-UTC offset -- both contract violations.
RFC3339_UTC_LENGTH = 25


def to_rfc3339_utc(value: object) -> str | None:
    """Normalise a provider timestamp, or return None if it is unusable.

    Accepts an RFC 3339 / ISO 8601 string (with `Z` or a numeric offset) or an
    epoch expressed in milliseconds. Anything else -- None, a boolean, a dict, a
    string that is not a date, an epoch outside the representable range --
    returns None.

    Returning None matters as much as converting does. An unusable timestamp is
    a fact about the posting, and substituting "now" would make an unknown
    posting look brand new: the freshness equivalent of treating silence as
    permission.

    **The one assumption made here.** A string with no offset is ambiguous by up
    to +/-14 hours, and it is read as UTC rather than discarded. Neither current
    provider produces one -- Greenhouse was offset-aware on 413/413 stored
    postings and Lever is an epoch -- so this is a defensive branch rather than
    live behaviour. It is a stated assumption, pinned by a test: for a freshness
    signal measured in days, a worst-case half-day skew is less harmful than
    losing the date entirely.
    """
    moment = _parse(value)
    if moment is None:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat()


def _parse(value: object) -> datetime | None:
    # bool is an int subclass, and `True` would otherwise become 1970.
    if value is None or isinstance(value, bool):
        return None

    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    return None

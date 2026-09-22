"""The single source of "what time is it" and "what is this row's id".

Both live outside ``domain/`` on purpose. The domain layer is meant to be a set
of pure functions -- give it the same fingerprint and profile twice and it must
produce the same verdict twice. A clock and a random id generator are the two
things that would quietly break that property, so they sit here in shared
infrastructure and are passed *in* to anything that needs them.

Two migration hazards are closed by having exactly one implementation of each:

* **A3/A4 (timestamps).** Every timestamp in the database is an ISO-8601 UTC
  string ending in ``Z``. No SQL ever calls a date function, because SQLite's
  date functions do not exist in PostgreSQL. This format also sorts correctly
  as plain text, so ``ORDER BY created_at`` needs no special handling.
* **A1 (identifiers).** Every primary key is a ULID stored as TEXT, generated
  here in Python rather than by the database. That removes sequence migration,
  allows ids to be created before a row is written, and is byte-identical in
  SQLite and PostgreSQL.
"""

from datetime import UTC, datetime

from ulid import ULID

#: Length of the Crockford base32 text form of a ULID.
ULID_LENGTH = 26


def now_utc() -> str:
    """The current instant as an ISO-8601 UTC string, e.g. ``2026-08-25T20:14:07Z``.

    Use this everywhere. Never ``datetime.now()``: it returns local time with no
    timezone attached, and a database full of timezone-naive local timestamps is
    only discovered to be wrong long after the fact.
    """
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_id() -> str:
    """A fresh ULID as text: time-sortable, 26 characters, generated client-side."""
    return str(ULID())


def is_valid_id(candidate: str) -> bool:
    """Cheap shape check for a ULID, used by tests and by the doctor command."""
    if len(candidate) != ULID_LENGTH:
        return False
    # Crockford base32 excludes I, L, O and U to avoid transcription mistakes.
    allowed = set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
    return set(candidate) <= allowed
